"""
FIRE Capital Tools - Site DD (beta).

A structured site walkthrough: a 32-item inspection checklist across six
categories, each recorded on the five-state condition scale
(Excellent/Good/Satisfactory/Repair/Replace), rolled up into counts by
state and a count of what needs work, with per-item notes,
photos, and a PDF report.

Supersedes Deal Dive's Condition tab, which was a single subjective rating
plus a notes blob plus a file list -- the same question this asks, at a
depth that cannot carry an inspection. Deal Dive keeps a summary card
linking here, the same way it does for Rent Comps.

Two modes, deal-linked primary:
  * Deal-linked -- arrived at with ?deal_id=N from Deal Dive's card. A site
    visit is nearly always tied to a property being pursued.
  * Standalone -- a walkthrough of something not (yet) in Deal Dive, which
    requires a property_label; an inspection record with no property
    identity is useless.

Unlike Rent Comps, nothing here is locked in deal-linked mode: the
inspection date and inspector belong to the visit, not to the deal
record, and follow the Deal Analyzer precedent of prefill-but-editable.

Scores are never stored -- they are computed on read from the item rows by
site_dd_conditions.summarize(), so what the screen shows, what the
summary card shows, and what the PDF prints cannot drift apart.
"""

from __future__ import annotations

import datetime
import secrets
import shutil
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import branding
from tools import deal_dive_db
from tools import site_dd_checklist as cl
from tools import site_dd_conditions as cond
from tools import site_dd_db as db
from tools import site_dd_report as report
from tools.form_utils import to_int

site_dd_bp = Blueprint("site_dd", __name__)

FEEDBACK_TOOL_NAME = "Site DD"

# Image-weighted: a site walkthrough produces photos, with the occasional
# third-party report attached. Mirrors Deal Dive's allowlist approach.
ALLOWED_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif", ".pdf"}
RASTER_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _upload_dir(assessment_id: int) -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "site-dd" / str(assessment_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _not_found():
    flash("That assessment could not be found — it may have been deleted.", "danger")
    return redirect(url_for("site_dd.index"))


def _load(assessment_id: int):
    with db.get_connection() as conn:
        return db.get_assessment(conn, assessment_id)


def _deal_for(deal_id):
    if deal_id is None:
        return None
    with deal_dive_db.get_connection() as conn:
        return deal_dive_db.get_deal(conn, deal_id)


# ── Index ────────────────────────────────────────────────────────────────

@site_dd_bp.route("/")
@login_required
def index():
    """All assessments, newest first, with live scores. Optionally scoped
    to one deal via ?deal_id=N so Deal Dive's card can link to just that
    property's history."""
    deal_id = to_int(request.args.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found — showing all assessments instead.", "warning")
        deal_id = None

    with db.get_connection() as conn:
        rows = db.list_assessments(conn, deal_id=deal_id, all_scopes=deal_id is None)
        for r in rows:
            r["summary"] = cond.summarize(db.get_conditions_map(conn, r["id"]), cl.CATEGORIES)

    return render_template(
        "tools/site_dd.html",
        assessments=rows,
        deal=deal,
        deal_id=deal_id,
        today=datetime.date.today().isoformat(),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@site_dd_bp.route("/new", methods=["POST"])
@login_required
def new_assessment():
    """Create and go straight to the checklist. Deal-linked assessments
    take their label from the deal so the two never disagree; standalone
    ones must supply their own."""
    deal_id = to_int(request.form.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found.", "danger")
        return redirect(url_for("site_dd.index"))

    if deal:
        label = f"{deal['address']}, {deal['city']} {deal['state']}"
    else:
        label = (request.form.get("property_label") or "").strip()
        if not label:
            flash("A property name or address is required for a standalone assessment.", "danger")
            return redirect(url_for("site_dd.index"))

    with db.get_connection() as conn:
        aid = db.create_assessment(conn, {
            "deal_id": deal_id,
            "property_label": label,
            "assessed_on": (request.form.get("assessed_on") or "").strip() or datetime.date.today().isoformat(),
            "inspector": (request.form.get("inspector") or "").strip() or None,
            "checklist_version": cl.CHECKLIST_VERSION,
            "status": db.STATUS_DRAFT,
        })
    flash("Assessment started.", "success")
    return redirect(url_for("site_dd.detail", assessment_id=aid))


# ── Detail / checklist ───────────────────────────────────────────────────

@site_dd_bp.route("/assessment/<int:assessment_id>")
@login_required
def detail(assessment_id):
    with db.get_connection() as conn:
        assessment = db.get_assessment(conn, assessment_id)
        if not assessment:
            abort(404)
        items = db.get_findings(conn, assessment_id)
        photos = db.list_media(conn, assessment_id, kind=db.MEDIA_PHOTO)
        summary = cond.summarize({k: v["condition"] for k, v in items.items()},
                                 cl.CATEGORIES)

    # Media is keyed to a finding from Branch 3 onward; until then it is
    # attached by item key, which is what the caption carries.
    photos_by_item = {}
    for p in photos:
        photos_by_item.setdefault(p.get("item_key") or "", []).append(p)

    return render_template(
        "tools/site_dd_detail.html",
        assessment=assessment,
        deal=_deal_for(assessment["deal_id"]),
        categories=cl.CATEGORIES,
        items=items,
        item_labels=cl.ITEM_LABELS,
        photos=photos,
        photos_by_item=photos_by_item,
        summary=summary,
        conditions=cond.CONDITIONS,
        condition_labels=cond.CONDITION_LABELS,
        condition_hints=cond.CONDITION_HINTS,
        condition_colours=cond.CONDITION_COLOURS,
        note_truncate_at=report.NOTE_TRUNCATE_AT,
        statuses=db.STATUSES,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@site_dd_bp.route("/assessment/<int:assessment_id>/save", methods=["POST"])
@login_required
def save(assessment_id):
    """Persist the whole checklist plus the assessment header in one go.

    Only keys in the checklist definition are accepted -- a hand-crafted
    POST cannot insert a response to an item that does not exist. Anything
    that is not one of the five conditions, including the empty string and
    a leftover numeric score, is stored as NULL (not assessed) rather than
    being coerced to something arbitrary."""
    if not _load(assessment_id):
        return _not_found()

    responses = []
    for key in cl.ITEM_KEYS:
        raw = (request.form.get(f"condition_{key}") or "").strip()
        responses.append({
            "scope": cl.SCOPE,
            "area_id": None,
            "room_id": None,
            "category_key": cl.ITEM_CATEGORY[key],
            "item_key": key,
            "condition": raw if cond.is_valid(raw) else None,
            "note": (request.form.get(f"note_{key}") or "").strip() or None,
        })

    status = (request.form.get("status") or "").strip()
    with db.get_connection() as conn:
        db.update_assessment(conn, assessment_id, {
            "property_label": (request.form.get("property_label") or "").strip() or "Untitled",
            "assessed_on": (request.form.get("assessed_on") or "").strip() or None,
            "inspector": (request.form.get("inspector") or "").strip() or None,
            "overall_notes": (request.form.get("overall_notes") or "").strip() or None,
            "status": status if status in db.STATUSES else db.STATUS_DRAFT,
        })
        db.upsert_findings(conn, assessment_id, responses)

    flash("Assessment saved.", "success")
    return redirect(url_for("site_dd.detail", assessment_id=assessment_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/delete", methods=["POST"])
@login_required
def delete(assessment_id):
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        db.delete_assessment(conn, assessment_id)
    shutil.rmtree(_upload_dir(assessment_id), ignore_errors=True)
    flash("Assessment deleted.", "success")
    return redirect(url_for("site_dd.index"))


# ── Photos ───────────────────────────────────────────────────────────────

@site_dd_bp.route("/assessment/<int:assessment_id>/photo", methods=["POST"])
@login_required
def upload_photo(assessment_id):
    if not _load(assessment_id):
        return _not_found()

    upload = request.files.get("photo")
    if not upload or not upload.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("site_dd.detail", assessment_id=assessment_id))

    original_name = secure_filename(upload.filename)
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_PHOTO_EXT:
        flash(f"Unsupported file type: {ext or 'unknown'}.", "danger")
        return redirect(url_for("site_dd.detail", assessment_id=assessment_id))

    item_key = (request.form.get("item_key") or "").strip() or None
    if item_key and item_key not in cl.ITEM_LABELS:
        item_key = None

    stored_name = f"{secrets.token_urlsafe(8)}_{original_name}"
    upload.save(str(_upload_dir(assessment_id) / stored_name))
    with db.get_connection() as conn:
        db.add_media(conn, assessment_id, item_key, original_name, stored_name,
                     (request.form.get("caption") or "").strip() or None)

    flash("Photo uploaded.", "success")
    return redirect(url_for("site_dd.detail", assessment_id=assessment_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/photo/<int:photo_id>")
@login_required
def download_photo(assessment_id, photo_id):
    with db.get_connection() as conn:
        record = db.get_media(conn, assessment_id, photo_id)
    if not record:
        abort(404)
    path = _upload_dir(assessment_id) / record["stored_name"]
    if not path.exists():
        abort(404)
    return send_file(str(path), download_name=record["original_name"])


@site_dd_bp.route("/assessment/<int:assessment_id>/photo/<int:photo_id>/delete", methods=["POST"])
@login_required
def delete_photo(assessment_id, photo_id):
    with db.get_connection() as conn:
        record = db.get_media(conn, assessment_id, photo_id)
        if record:
            db.delete_media(conn, assessment_id, photo_id)
    if record:
        (_upload_dir(assessment_id) / record["stored_name"]).unlink(missing_ok=True)
    flash("Photo removed.", "success")
    return redirect(url_for("site_dd.detail", assessment_id=assessment_id))


# ── Report ───────────────────────────────────────────────────────────────

@site_dd_bp.route("/assessment/<int:assessment_id>/report")
@login_required
def download_report(assessment_id):
    """Generate the PDF on demand rather than storing it -- it is derived
    entirely from the assessment, so a stored copy could only ever go
    stale. Written into the assessment's own upload directory, which is on
    the persistent volume in production, then streamed back."""
    with db.get_connection() as conn:
        assessment = db.get_assessment(conn, assessment_id)
        if not assessment:
            abort(404)
        items = db.get_findings(conn, assessment_id)
        photos = db.list_media(conn, assessment_id, kind=db.MEDIA_PHOTO)
        summary = cond.summarize({k: v["condition"] for k, v in items.items()},
                                 cl.CATEGORIES)

    upload_dir = _upload_dir(assessment_id)
    # Only raster images can be embedded as thumbnails; a PDF attachment
    # is still listed on the page but can't be previewed in the contact
    # sheet, so it is filtered out here rather than failing per-image.
    thumbable = [p for p in photos if Path(p["stored_name"]).suffix.lower() in RASTER_EXT]

    out_path = upload_dir / report.report_filename(assessment)
    report.build_report(
        out_path, assessment, items, summary, thumbable, photo_dir=upload_dir,
        logo_path=branding.logo_png_path(Path(current_app.root_path) / "static"),
    )
    return send_file(str(out_path), as_attachment=True,
                     download_name=report.report_filename(assessment),
                     mimetype="application/pdf")


# ── Cross-tool query ─────────────────────────────────────────────────────

def summary_for_deal(deal_id: int) -> dict | None:
    """Backs Deal Dive's Condition summary card. Returns the latest
    assessment for the deal with its live scores, or None. Called directly
    rather than over HTTP -- same process, one dict."""
    with db.get_connection() as conn:
        latest = db.latest_for_deal(conn, deal_id)
        if not latest:
            return None
        latest["summary"] = cond.summarize(
            db.get_conditions_map(conn, latest["id"]), cl.CATEGORIES)
        latest["total_count"] = db.count_for_deal(conn, deal_id)
        return latest


def purge_for_deal(deal_id: int, upload_root: Path) -> list[int]:
    """Called from Deal Dive's delete_deal. Removes every assessment tied
    to the deal along with its rows and its uploaded files -- the DB rows
    and the files on disk are separate concerns and both have to go, so
    the deleted ids come back to drive the directory removal."""
    with db.get_connection() as conn:
        ids = db.delete_assessments_for_deal(conn, deal_id)
    for aid in ids:
        shutil.rmtree(Path(upload_root) / "site-dd" / str(aid), ignore_errors=True)
    return ids
