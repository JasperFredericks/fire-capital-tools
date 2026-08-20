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
from tools import site_dd_bank as bank
from tools import site_dd_checklist as cl
from tools import site_dd_conditions as cond
from tools import site_dd_costs as costs
from tools import site_dd_reference_costs as refcosts
from tools import site_dd_capex_export as capex_export
from tools import site_dd_capture as cap
from tools import site_dd_unit_checklist as uc
from tools import upload_limits as ul
from tools import site_dd_db as db
from tools import site_dd_report as report
from tools.form_utils import to_float, to_int

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
        # The index is the one screen that shows an ASSESSMENT status,
        # which is a separate vocabulary from an area's.
        assessment_status_label=db.assessment_status_label,
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
        # Property scope only: area_id and room_id both NULL. Unit and
        # room findings live under their own pages and must not leak into
        # the property checklist's completion figure.
        items = db.get_findings(conn, assessment_id, None, None)
        photos = db.list_media(conn, assessment_id, kind=db.MEDIA_PHOTO)
        summary = cond.summarize({k: [r["condition"] for r in rows] for k, rows in items.items()},
                                 cl.CATEGORIES)
        areas = db.list_areas(conn, assessment_id)
        area_rollups = []
        for a in areas:
            rooms = db.list_rooms(conn, a["id"])
            by_room = {r["id"]: db.get_conditions_map(conn, assessment_id, a["id"], r["id"])
                       for r in rooms}
            unit_rows = db.get_conditions_map(conn, assessment_id, a["id"], None)
            area_rollups.append({
                "area": a, "room_count": len(rooms),
                "summary": uc.summarize_unit(by_room, rooms, unit_rows),
            })

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
        areas=areas, area_rollups=area_rollups,
        area_kinds=db.AREA_KINDS, area_statuses=db.AREA_STATUSES,
        # The ACCESSORS, not the raw maps.
        #
        # A template that subscripts a label dict does NOT raise on a
        # missing key -- this app runs Jinja's default Undefined, so
        # `area_status_labels[x]` renders as the EMPTY STRING. Verified,
        # after an earlier note here claimed it raised. Silent is the
        # worse half of the trade: a value from an older vocabulary
        # produces "Unit &middot;" with nothing after it, and the fact
        # that it was unrecognised leaves no trace at all.
        #
        # The accessor says "Not stated", which is a statement rather
        # than a gap. It also gives area_status_label() the caller it
        # never had -- it shipped in 5cde052 reached only by its test.
        area_status_label=db.area_status_label,
        assessment_status_label=db.assessment_status_label,
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

    with db.get_connection() as conn:
        existing = db.get_findings(conn, assessment_id, None, None)

    responses = []
    for key in cl.ITEM_KEYS:
        for n in _posted_instances(request.form, key, existing.get(key, [])):
            suffix = "" if n == 1 else f"__{n}"
            raw = (request.form.get(f"condition_{key}{suffix}") or "").strip()
            est_cost, est_source = _kept_cost(request.form, key, suffix,
                                              existing.get(key), n)
            responses.append({
                "scope": cl.SCOPE,
                "area_id": None,
                "room_id": None,
                "category_key": cl.ITEM_CATEGORY[key],
                "item_key": key,
                "instance_no": n,
                "instance_label": _kept_label(request.form, key, suffix,
                                              existing.get(key), n),
                "condition": raw if cond.is_valid(raw) else None,
                "est_unit_cost": est_cost,
                "est_cost_source": est_source,
                "measure": _kept_measure(request.form, key, suffix,
                                         existing.get(key), n),
                "note": (request.form.get(f"note_{key}{suffix}") or "").strip() or None,
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

    back = _capture_redirect(assessment_id)

    # One capture form serves every item on the page, so several empty
    # "photo" parts arrive alongside the one the inspector actually filled.
    # getlist and take the first with a filename; .get() would return the
    # first EMPTY part and report "no file selected" every time.
    upload = next((f for f in request.files.getlist("photo") if f and f.filename), None)
    if upload is None:
        flash("No file selected.", "danger")
        return redirect(back)

    try:
        ul.check(request.content_length, ul.VIDEO_BYTES, "file")
    except ul.UploadTooLarge as exc:
        flash(str(exc), "danger")
        return redirect(back)

    item_key = _arg("item_key")
    if item_key and not (item_key in cl.ITEM_LABELS or uc.is_known_item(item_key)
                         or bank.is_bank_item(item_key)
                         or bank.is_custom_key(item_key)):
        item_key = None
    area_id = to_int(request.args.get("area_id") or request.form.get("area_id"))
    room_id = to_int(request.args.get("room_id") or request.form.get("room_id"))
    finding_id = to_int(request.args.get("finding_id") or request.form.get("finding_id"))

    # Saved before it can be identified: the content has to be on disk to
    # be read, and the extension the client supplied is not trusted for
    # anything. A rejected file is removed in the same request.
    original_name = secure_filename(upload.filename) or "capture"
    tmp_name = f"{secrets.token_urlsafe(8)}_{original_name}"
    tmp_path = _upload_dir(assessment_id) / tmp_name
    upload.save(str(tmp_path))
    size_bytes = tmp_path.stat().st_size

    try:
        info = cap.sniff(tmp_path)
    except cap.UnsupportedMedia as exc:
        tmp_path.unlink(missing_ok=True)
        flash(str(exc), "danger")
        return redirect(back)

    # A capture usually comes BEFORE the judgement -- you photograph the
    # crack, then decide it is a Replace. So if no finding row exists yet
    # for this item, create the empty instance 1 now and attach to it.
    # Without this the photo lands with a NULL finding_id and does not
    # appear against the item at all.
    if finding_id is None and item_key:
        with db.get_connection() as conn:
            existing = db.get_findings(conn, assessment_id, area_id, room_id)
            rows_for_item = existing.get(item_key) or []
            if rows_for_item:
                finding_id = rows_for_item[0]["id"]
            else:
                finding_id = db.add_first_instance(
                    conn, assessment_id, item_key, area_id, room_id,
                    scope=(cond.SCOPE_ROOM if room_id else
                           cond.SCOPE_UNIT if area_id else cond.SCOPE_PROPERTY),
                    category_key=_category_for(item_key))

    kind = info["kind"]
    duration_s = None
    try:
        if kind == cap.KIND_VIDEO:
            # One video per finding, enforced before the limits so the
            # message is about the rule the user actually hit.
            if _video_count(assessment_id, finding_id, item_key, area_id, room_id):
                raise cap.MediaTooLarge(
                    "There is already a video on this item. Photos are the "
                    "default; video is for the one thing a still cannot show.")
            probe = cap.check_video(tmp_path, size_bytes)
            duration_s = probe.get("duration_s")
        else:
            cap.check_size(kind, size_bytes)
    except cap.MediaTooLarge as exc:
        tmp_path.unlink(missing_ok=True)
        flash(str(exc), "danger")
        return redirect(back)

    # Renamed to the extension the CONTENT says it is, so a .jpg that is
    # really a MOV is stored and served as a MOV.
    stem = Path(original_name).stem or "capture"
    stored_name = f"{secrets.token_urlsafe(8)}_{stem}{info['ext']}"
    final_path = _upload_dir(assessment_id) / stored_name
    tmp_path.rename(final_path)

    with db.get_connection() as conn:
        db.add_media(conn, assessment_id, item_key, original_name, stored_name,
                     (request.form.get("caption") or "").strip() or None,
                     kind=kind, finding_id=finding_id,
                     size_bytes=size_bytes, duration_s=duration_s,
                     area_id=area_id, room_id=room_id)

    flash(f"{kind.title()} uploaded ({cap.human_bytes(size_bytes)})."
          + (f" {duration_s:.0f}s." if duration_s else ""), "success")
    return redirect(back)


def _arg(name):
    """A field that may arrive in the query string or the body. The no-JS
    capture buttons put the scope in formaction, so both are read."""
    return ((request.args.get(name) or request.form.get(name) or "").strip() or None)


def _capture_redirect(assessment_id):
    """Back to wherever the capture was taken from -- a room, a unit, or
    the property checklist -- so an upload never bounces the inspector out
    of the walkthrough."""
    area_id = to_int(request.args.get("area_id") or request.form.get("area_id"))
    room_id = to_int(request.args.get("room_id") or request.form.get("room_id"))
    if area_id and room_id:
        return url_for("site_dd.room_detail", assessment_id=assessment_id,
                       area_id=area_id, room_id=room_id)
    if area_id:
        return url_for("site_dd.area_detail", assessment_id=assessment_id,
                       area_id=area_id)
    return url_for("site_dd.detail", assessment_id=assessment_id)


def _video_count(assessment_id, finding_id, item_key, area_id, room_id) -> int:
    """Videos already attached to the same item, in the same scope.

    Scoped by area and room as well as item_key, because `flooring` means
    a different thing in the kitchen and in bedroom 2 -- counting by item
    alone would let one kitchen video block every other room's.
    """
    with db.get_connection() as conn:
        rows = db.list_media(conn, assessment_id, kind=db.MEDIA_VIDEO)
    n = 0
    for r in rows:
        if finding_id and r.get("finding_id") == finding_id:
            n += 1
            continue
        if not finding_id and r.get("item_key") == item_key \
                and r.get("area_id") == area_id and r.get("room_id") == room_id:
            n += 1
    return n


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
    # Typed from the STORED name, which sniff() set from the content --
    # not from original_name, which is whatever the client called it.
    # Served inline rather than as an attachment so video plays in place.
    return send_file(str(path), download_name=record["original_name"],
                     mimetype=cap.mime_for_stored_name(record["stored_name"]))


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


# ── Units and rooms ──────────────────────────────────────────────────────
#
# The unit-by-unit walkthrough. Everything here is built for a phone held
# in one hand while standing in a room: tap-first, single column, and
# sequential -- the inspector moves room to room without going back to a
# menu between each one.

def _area_or_404(conn, assessment_id, area_id):
    area = db.get_area(conn, area_id)
    if not area or area["assessment_id"] != assessment_id:
        return None
    return area


@site_dd_bp.route("/assessment/<int:assessment_id>/areas", methods=["POST"])
@login_required
def create_area(assessment_id):
    if not _load(assessment_id):
        return _not_found()
    label = (request.form.get("label") or "").strip()
    if not label:
        flash("Give the unit a number or name.", "warning")
        return redirect(url_for("site_dd.detail", assessment_id=assessment_id) + "#units")
    with db.get_connection() as conn:
        area_id = db.create_area(conn, assessment_id, {
            "kind": request.form.get("kind") or db.AREA_UNIT,
            "label": label,
            "status": request.form.get("status"),
        })
    return redirect(url_for("site_dd.area_detail",
                            assessment_id=assessment_id, area_id=area_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>")
@login_required
def area_detail(assessment_id, area_id):
    """One unit: its rooms in walk order, the room-type pad, and the
    unit-wide items."""
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        area = _area_or_404(conn, assessment_id, area_id)
        if not area:
            return _not_found()
        rooms = db.list_rooms(conn, area_id)
        by_room = {r["id"]: db.get_conditions_map(conn, assessment_id, area_id, r["id"])
                   for r in rooms}
        unit_rows = db.get_findings(conn, assessment_id, area_id, None)
        unit_catalogue = list(uc.items_for_unit())
        unit_added = _added_items(conn, assessment_id, area_id, None, unit_catalogue)
        # Every room's added items, so the unit roll-up counts a fireplace
        # in the living room rather than quietly ignoring it.
        added_by_room = {}
        for r in rooms:
            room_catalogue = uc.items_for_room(r["room_type"])
            added_by_room[r["id"]] = _added_items(conn, assessment_id, area_id,
                                                  r["id"], room_catalogue)
        # Only units with no findings yet can be overwritten by a copy, so
        # the list offered is restricted to sources that actually have a
        # layout to give.
        others = [a for a in db.list_areas(conn, assessment_id)
                  if a["id"] != area_id and db.list_rooms(conn, a["id"])]
        finding_count = db.area_finding_count(conn, area_id)

    summary = uc.summarize_unit(
        by_room, rooms,
        {k: [r["condition"] for r in rows] for k, rows in unit_rows.items()},
        added_by_room=added_by_room, added_unit=unit_added)
    return render_template(
        "tools/site_dd_area.html",
        cost_units=COST_UNITS,
        assessment=_load(assessment_id), area=area, rooms=rooms,
        room_types=uc.ROOM_TYPES, room_type_labels=uc.ROOM_TYPE_LABELS,
        unit_items=unit_catalogue, added_items=unit_added, unit_rows=unit_rows,
        bank_groups=_bank_picker(bank.SCOPE_UNIT, None, unit_catalogue, unit_added),
        add_scope="unit",
        summary=summary, room_summaries={r["room"]["id"]: r for r in summary["rooms"]},
        conditions=cond.CONDITIONS, condition_labels=cond.CONDITION_LABELS,
        # The SAME predicate the capex filter uses, so the screen that
        # invites a cost and the export that spends it cannot disagree
        # about what counts as work. They did: the box stayed collapsed
        # on a missing smoke alarm, so even the manual override was
        # hidden behind the assumption that only conditions cost money.
        #
        # This replaced `work_conditions=cond.WORK_CONDITIONS`, which
        # both templates used to test `row.condition in work_conditions`
        # and which nothing reads now. Leaving it passed would be a dead
        # value handed to a template -- the shape of the bug this change
        # exists to fix.
        needs_work=uc.needs_work, catalogue=bank.every_item(),
        cost_describe=costs.describe,
        # Display only. See site_dd_costs.reference_hint(): it returns a
        # figure and words, never a provenance value, so the capture
        # screen can SHOW what the table would charge without becoming a
        # second place that can assign it.
        reference_hint=costs.reference_hint,
        manual_cost_label=costs.SOURCE_LABELS[costs.SOURCE_MANUAL],
        condition_colours=cond.CONDITION_COLOURS,
        statuses=db.AREA_STATUSES,
        # See detail() above for why this is the accessor, not the map.
        area_status_label=db.area_status_label, copy_sources=others,
        finding_count=finding_count,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/save", methods=["POST"])
@login_required
def save_area(assessment_id, area_id):
    """Unit header plus the unit-wide items, in one post."""
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        if not _area_or_404(conn, assessment_id, area_id):
            return _not_found()
        db.update_area(conn, area_id, {
            "label": request.form.get("label"),
            "status": request.form.get("status"),
            "notes": (request.form.get("notes") or "").strip() or None,
        })
        unit_catalogue = list(uc.items_for_unit())
        items = unit_catalogue + _added_items(conn, assessment_id, area_id,
                                              None, unit_catalogue)
        db.upsert_findings(conn, assessment_id,
                           _collect(request.form, items,
                                    scope=cond.SCOPE_UNIT, area_id=area_id, room_id=None,
                                    existing=db.get_findings(conn, assessment_id,
                                                             area_id, None)))
    flash("Unit saved.", "success")
    return redirect(url_for("site_dd.area_detail",
                            assessment_id=assessment_id, area_id=area_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/rooms", methods=["POST"])
@login_required
def add_room(assessment_id, area_id):
    """Append one room. The order rooms are added IS the walk order --
    tapping Kitchen first puts the kitchen first. One tap, one room, no
    form to fill in, because this happens while standing in a doorway."""
    if not _load(assessment_id):
        return _not_found()
    room_type = request.form.get("room_type")
    if room_type not in uc.ROOM_TYPE_LABELS:
        flash("Unknown room type.", "danger")
        return redirect(url_for("site_dd.area_detail",
                                assessment_id=assessment_id, area_id=area_id))
    with db.get_connection() as conn:
        if not _area_or_404(conn, assessment_id, area_id):
            return _not_found()
        # A second bathroom is "Bathroom 2" without anyone typing it.
        same = [r for r in db.list_rooms(conn, area_id) if r["room_type"] == room_type]
        label = None
        if same:
            label = f"{uc.ROOM_TYPE_LABELS[room_type]} {len(same) + 1}"
        db.create_room(conn, area_id, room_type, label)
    return redirect(url_for("site_dd.area_detail",
                            assessment_id=assessment_id, area_id=area_id) + "#rooms")


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/rooms/<int:room_id>/delete",
                  methods=["POST"])
@login_required
def delete_room(assessment_id, area_id, room_id):
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        if not _area_or_404(conn, assessment_id, area_id):
            return _not_found()
        db.delete_room(conn, room_id)
    flash("Room removed.", "success")
    return redirect(url_for("site_dd.area_detail",
                            assessment_id=assessment_id, area_id=area_id) + "#rooms")


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/copy-layout",
                  methods=["POST"])
@login_required
def copy_layout(assessment_id, area_id):
    """Copy another unit's room sequence onto this one.

    The layout copies; the findings do not. Two units can have identical
    rooms in identical order and be in completely different condition, and
    copying an inspection would be fabricating an observation nobody made.
    """
    if not _load(assessment_id):
        return _not_found()
    source_id = to_int(request.form.get("from_area_id"))
    with db.get_connection() as conn:
        target = _area_or_404(conn, assessment_id, area_id)
        source = _area_or_404(conn, assessment_id, source_id) if source_id else None
        if not target or not source:
            flash("Pick a unit to copy the layout from.", "warning")
            return redirect(url_for("site_dd.area_detail",
                                    assessment_id=assessment_id, area_id=area_id))
        if db.area_finding_count(conn, area_id):
            flash("This unit already has findings recorded — copying a layout would "
                  "discard them. Remove them first if you meant to start over.", "warning")
            return redirect(url_for("site_dd.area_detail",
                                    assessment_id=assessment_id, area_id=area_id))
        copied = db.copy_layout(conn, source["id"], area_id)
    flash(f"Copied {copied} room{'' if copied == 1 else 's'} from {source['label']} — "
          f"the layout only, no findings.", "success")
    return redirect(url_for("site_dd.area_detail",
                            assessment_id=assessment_id, area_id=area_id) + "#rooms")


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/delete", methods=["POST"])
@login_required
def delete_area(assessment_id, area_id):
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        if not _area_or_404(conn, assessment_id, area_id):
            return _not_found()
        db.delete_area(conn, area_id)
    flash("Unit removed.", "success")
    return redirect(url_for("site_dd.detail", assessment_id=assessment_id) + "#units")


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/rooms/<int:room_id>")
@login_required
def room_detail(assessment_id, area_id, room_id):
    """One room's checklist, with the next and previous room in the walk
    order carried into the template. The inspector never returns to a menu
    between rooms -- the walkthrough is sequential by design, so the page
    knows where it sits in the sequence."""
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        area = _area_or_404(conn, assessment_id, area_id)
        room = db.get_room(conn, room_id)
        if not area or not room or room["area_id"] != area_id:
            return _not_found()
        rooms = db.list_rooms(conn, area_id)
        rows = db.get_findings(conn, assessment_id, area_id, room_id)
        shots = db.list_media_for_scope(conn, assessment_id, area_id, room_id)
        catalogue = list(uc.items_for_room(room["room_type"]))
        added = _added_items(conn, assessment_id, area_id, room_id, catalogue)

    # Keyed by FINDING, not by item: with two sinks in a room, "which one
    # is this photo of" is only answerable through finding_id.
    media_by_finding = {}
    media_by_item = {}
    for m in shots:
        if m.get("finding_id"):
            media_by_finding.setdefault(m["finding_id"], []).append(m)
        media_by_item.setdefault(m.get("item_key") or "", []).append(m)

    order = [r["id"] for r in rooms]
    idx = order.index(room_id) if room_id in order else 0
    return render_template(
        "tools/site_dd_room.html",
        cost_units=COST_UNITS,
        assessment=_load(assessment_id), area=area, room=room, rooms=rooms,
        items=catalogue, added_items=added, rows=rows,
        bank_groups=_bank_picker(bank.SCOPE_ROOM, room["room_type"],
                                 catalogue, added),
        add_scope="room",
        room_type_labels=uc.ROOM_TYPE_LABELS,
        conditions=cond.CONDITIONS, condition_labels=cond.CONDITION_LABELS,
        # The SAME predicate the capex filter uses, so the screen that
        # invites a cost and the export that spends it cannot disagree
        # about what counts as work. They did: the box stayed collapsed
        # on a missing smoke alarm, so even the manual override was
        # hidden behind the assumption that only conditions cost money.
        #
        # This replaced `work_conditions=cond.WORK_CONDITIONS`, which
        # both templates used to test `row.condition in work_conditions`
        # and which nothing reads now. Leaving it passed would be a dead
        # value handed to a template -- the shape of the bug this change
        # exists to fix.
        needs_work=uc.needs_work, catalogue=bank.every_item(),
        cost_describe=costs.describe,
        # Display only. See site_dd_costs.reference_hint(): it returns a
        # figure and words, never a provenance value, so the capture
        # screen can SHOW what the table would charge without becoming a
        # second place that can assign it.
        reference_hint=costs.reference_hint,
        manual_cost_label=costs.SOURCE_LABELS[costs.SOURCE_MANUAL],
        condition_colours=cond.CONDITION_COLOURS,
        media_by_item=media_by_item,
        media_by_finding=media_by_finding,
        max_photo_mb=cap.MAX_PHOTO_BYTES // 1024 // 1024,
        max_video_mb=cap.MAX_VIDEO_BYTES // 1024 // 1024,
        max_video_seconds=int(cap.MAX_VIDEO_SECONDS),
        human_bytes=cap.human_bytes,
        position=idx + 1, room_count=len(rooms),
        prev_room=rooms[idx - 1] if idx > 0 else None,
        next_room=rooms[idx + 1] if idx + 1 < len(rooms) else None,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@site_dd_bp.route("/assessment/<int:assessment_id>/areas/<int:area_id>/rooms/<int:room_id>/save",
                  methods=["POST"])
@login_required
def save_room(assessment_id, area_id, room_id):
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        area = _area_or_404(conn, assessment_id, area_id)
        room = db.get_room(conn, room_id)
        if not area or not room or room["area_id"] != area_id:
            return _not_found()
        catalogue = list(uc.items_for_room(room["room_type"]))
        items = catalogue + _added_items(conn, assessment_id, area_id,
                                         room_id, catalogue)
        db.upsert_findings(conn, assessment_id,
                           _collect(request.form, items,
                                    scope=cond.SCOPE_ROOM, area_id=area_id, room_id=room_id,
                                    existing=db.get_findings(conn, assessment_id,
                                                             area_id, room_id)))
        rooms = db.list_rooms(conn, area_id)

    # "Save & next room" keeps the walkthrough moving without a detour
    # through the unit page, which is the whole point of a sequential flow.
    if request.form.get("go") == "next":
        order = [r["id"] for r in rooms]
        idx = order.index(room_id)
        if idx + 1 < len(order):
            return redirect(url_for("site_dd.room_detail", assessment_id=assessment_id,
                                    area_id=area_id, room_id=order[idx + 1]))
        flash("Last room saved — unit complete.", "success")
        return redirect(url_for("site_dd.area_detail",
                                assessment_id=assessment_id, area_id=area_id))

    flash("Room saved.", "success")
    return redirect(url_for("site_dd.room_detail", assessment_id=assessment_id,
                            area_id=area_id, room_id=room_id))


def _category_for(item_key):
    """The capex category for any item key, whatever scope defines it.

    The property checklist, the room/unit checklists and the item bank
    each own part of the key space and all three use the same category
    vocabulary. Callers that create a finding outside a form save -- a
    photo arriving before any judgement, an added instance -- go through
    here rather than knowing which catalogue an item came from.
    """
    return (cl.ITEM_CATEGORY.get(item_key)
            or uc.category_for(item_key)
            or (bank.get(item_key) or {}).get("category"))


def _added_items(conn, assessment_id, area_id, room_id, catalogue):
    """The bank picks and freeform items recorded on one scope.

    Shaped exactly like checklist items, and appended to the catalogue by
    the caller, so the form loop, _collect() and the roll-up treat them
    identically to a fixed item. That is the whole design: an added
    fireplace is not a special case anywhere downstream, it is the 33rd
    item on a 32-item list.
    """
    known = {i["key"] for i in catalogue}
    out = []
    for row in db.added_item_keys(conn, assessment_id, area_id, room_id, known):
        key = row["item_key"]
        # A bank pick is described by the catalogue; a freeform item is
        # described only by what somebody typed into instance_label.
        out.append(bank.as_item(row["bank_item_key"] or key, row["instance_label"]))
    return out


def _bank_picker(scope, room_type, catalogue, added=()):
    """What the picker offers here: the bank, minus anything already on
    the page.

    Two exclusions, one reason. An item the checklist already asks about
    would become a second question about one object; an item somebody
    already added would give two ways to reach the same row -- and the
    right way to record a SECOND fireplace is the "Add another" control
    under the first one, which knows it is making instance 2.
    """
    on_page = {i["label"] for i in catalogue} | {i["label"] for i in added}
    return bank.grouped_for_scope(scope, room_type, on_page)


def _collect(form, items, *, scope, area_id, room_id, existing=None):
    """Turn a posted room or unit form into finding rows.

    Only keys in the checklist are read, and a value that is not one of
    the item's own options is discarded rather than stored -- the same
    rule the property scope uses, so a hand-crafted POST cannot invent an
    answer to a question that was not asked.
    """
    existing = existing or {}
    out = []
    for item in items:
        key = item["key"]
        # Field names carry the instance number, so two sinks post two sets
        # of answers rather than the second overwriting the first. Instance
        # 1 keeps the unsuffixed names an older form would have sent.
        for n in _posted_instances(form, key, existing.get(key, [])):
            suffix = "" if n == 1 else f"__{n}"
            raw_condition = (form.get(f"condition_{key}{suffix}") or "").strip()
            raw_detail = (form.get(f"detail_{key}{suffix}") or "").strip()
            quantity = to_float(form.get(f"quantity_{key}{suffix}"))
            est_cost, est_source = _kept_cost(form, key, suffix,
                                              existing.get(key), n)
            out.append({
                "scope": scope, "area_id": area_id, "room_id": room_id,
                # The capex heading, NOT the input kind. This column used
                # to receive item["kind"], so every room and unit row
                # carried "condition"/"choice"/"number" and the capex
                # export emitted those as budget headings. The kind is
                # still on the item dict, where the form rendering reads
                # it -- the two were never the same fact.
                "category_key": item.get("category"),
                "item_key": key,
                "instance_no": n,
                # Set only for a curated pick. COALESCEd in the upsert, so
                # a form that does not know the link cannot break it.
                "bank_item_key": item.get("bank_item_key"),
                "instance_label": _kept_label(form, key, suffix,
                                              existing.get(key), n),
                "condition": raw_condition if cond.is_valid(raw_condition) else None,
                "detail": raw_detail if uc.is_valid_option(item, raw_detail) else None,
                "quantity": quantity if item["kind"] == uc.KIND_NUMBER else None,
                # A KIND_NUMBER item's measure describes the reading
                # itself (years, gallons); everything else carries the
                # unit of the typed COST, if one was chosen.
                "measure": (item["measure"] if item["kind"] == uc.KIND_NUMBER
                            else _kept_measure(form, key, suffix,
                                               existing.get(key), n)),
                "est_unit_cost": est_cost,
                "est_cost_source": est_source,
                "note": (form.get(f"note_{key}{suffix}") or "").strip() or None,
            })
    return out


def _kept_label(form, key, suffix, existing_rows, n):
    """The instance label after this save.

    A field that was NOT POSTED means "leave it alone"; a field posted
    empty means "clear it". The distinction is the whole fix: no template
    renders label_* for a checklist item, so treating absent as empty
    made every save null the label -- and for a freeform item, whose
    typed name is its only identity, the first save turned "Koi pond"
    into "Item".
    """
    field = f"label_{key}{suffix}"
    if field in form:
        return (form.get(field) or "").strip() or None
    for row in existing_rows or ():
        if int(row.get("instance_no") or 1) == n:
            return row.get("instance_label")
    return None


def _kept_cost(form, key, suffix, existing_rows, n):
    """The estimate and its provenance after this save.

    Absent means unchanged, exactly as for the instance label -- and for
    a sharper reason here. The cost field is only rendered where an
    estimate is plausible, so most saves do not mention most items; and
    when reference costs land, a save from a page that predates them
    must not silently downgrade a table figure to nothing.

    A typed number is always 'manual'. Typing over a reference figure
    makes the number the inspector's, and the provenance follows the
    number rather than lingering on the row it replaced.
    """
    field = f"cost_{key}{suffix}"
    if field not in form:
        for row in existing_rows or ():
            if int(row.get("instance_no") or 1) == n:
                return (row.get("est_unit_cost"),
                        costs.normalize_source(row.get("est_cost_source")))
        return (None, costs.SOURCE_NONE)
    value = costs.clean_cost(form.get(field))
    return (value, costs.source_for(value))


# The two states Michelle asked for: "yes, please add the toggle for 'per
# sq ft' or 'per job'. It's worth the extra click to ensure the data is
# accurate."
COST_UNITS = (("each", "per job"), ("sqft", "per sq ft"))
COST_UNIT_VALUES = {v for v, _ in COST_UNITS}


def _kept_measure(form, key, suffix, existing_rows, n):
    """Which unit the typed cost is in, after this save.

    UNSET IS A REAL STATE AND IS NOT QUIETLY RESOLVED

    She asked for an explicit choice, so an unanswered toggle must not
    silently become "per job" -- that is the assumption the toggle exists
    to remove. It stays None, and the export falls back to the provisional
    magnitude heuristic exactly as it does for rows stored before this
    existed.

    Absent from the form means unchanged, the same rule _kept_cost uses
    and for the same reason: most saves do not mention most items.
    """
    field = f"measure_{key}{suffix}"
    if field not in form:
        for row in existing_rows or ():
            if int(row.get("instance_no") or 1) == n:
                return row.get("measure")
        return None
    value = (form.get(field) or "").strip().lower()
    return value if value in COST_UNIT_VALUES else None


def _posted_instances(form, key, existing_rows):
    """Which instance numbers this form is answering for.

    Read from what was actually posted, unioned with what already exists,
    so a save never silently drops an instance the page did not happen to
    render -- and always includes 1, because every item has a first one.
    """
    numbers = {1}
    numbers.update(int(r["instance_no"] or 1) for r in existing_rows)
    prefix = f"condition_{key}__"
    for field in form:
        if field.startswith(prefix):
            tail = field[len(prefix):]
            if tail.isdigit():
                numbers.add(int(tail))
    return sorted(numbers)


@site_dd_bp.route("/assessment/<int:assessment_id>/instance", methods=["POST"])
@login_required
def add_instance(assessment_id):
    """Another one of the same item -- a second smoke alarm, a second sink.

    One POST, no form to fill in: the new instance appears empty and is
    filled in like any other. Works at every scope, so the property
    checklist, a unit and a room all use this same route.
    """
    if not _load(assessment_id):
        return _not_found()
    item_key = (request.form.get("item_key") or "").strip()
    area_id = to_int(request.form.get("area_id"))
    room_id = to_int(request.form.get("room_id"))
    scope = (request.form.get("scope") or cond.SCOPE_ROOM).strip()

    known = (item_key in cl.ITEM_LABELS or uc.is_known_item(item_key)
             or bank.is_bank_item(item_key) or bank.is_custom_key(item_key))
    if not known:
        flash("Unknown item.", "danger")
        return redirect(_capture_redirect(assessment_id))

    with db.get_connection() as conn:
        db.add_instance(conn, assessment_id, item_key, area_id, room_id,
                        scope=scope if scope in cond.SCOPES else cond.SCOPE_ROOM,
                        category_key=_category_for(item_key))
    flash("Added another.", "success")
    return redirect(_capture_redirect(assessment_id) + f"#item-{item_key}")


@site_dd_bp.route("/assessment/<int:assessment_id>/instance/<int:finding_id>/delete",
                  methods=["POST"])
@login_required
def delete_instance(assessment_id, finding_id):
    """Remove an extra instance. The first instance of an item is part of
    the checklist and is never removable -- only the extras are."""
    if not _load(assessment_id):
        return _not_found()
    with db.get_connection() as conn:
        row = db.get_finding(conn, finding_id)
        if not row or row["assessment_id"] != assessment_id:
            return _not_found()
        # The first instance of a CHECKLIST item is not removable -- the
        # question is asked of every unit whether or not it has one. An
        # added item is different: nothing obliges it to be here, so its
        # last instance takes the whole item away with it.
        fixed = (row["item_key"] in cl.ITEM_LABELS or uc.is_known_item(row["item_key"]))
        if int(row["instance_no"] or 1) <= 1 and fixed:
            flash("The first one is part of the checklist and can't be removed.",
                  "warning")
            return redirect(_capture_redirect(assessment_id))
        db.delete_instance(conn, finding_id)
    flash("Removed.", "success")
    return redirect(_capture_redirect(assessment_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/item", methods=["POST"])
@login_required
def add_item(assessment_id):
    """Put something on this room or unit that the checklist does not ask
    about -- from the bank, or typed.

    One route for both, because they produce the same record. A curated
    pick carries bank_item_key, which is what lets Branch 4 price it
    automatically; a typed one does not, and is otherwise identical. The
    freeform box is not a lesser path, it is the same path without a
    reference.
    """
    if not _load(assessment_id):
        return _not_found()
    area_id = to_int(request.form.get("area_id"))
    room_id = to_int(request.form.get("room_id"))
    scope = (request.form.get("scope") or cond.SCOPE_ROOM).strip()
    if scope not in cond.SCOPES:
        scope = cond.SCOPE_ROOM

    picked = (request.form.get("bank_item_key") or "").strip()
    typed = bank.clean_label(request.form.get("custom_label") or "")

    if picked:
        entry = bank.get(picked)
        if not entry:
            flash("That item is not in the bank.", "danger")
            return redirect(_capture_redirect(assessment_id))
        item_key, bank_key, label = entry["key"], entry["key"], entry["label"]
        category = entry["category"]
    elif typed:
        item_key, bank_key, label = bank.custom_key(typed), None, typed
        category = None
    else:
        flash("Pick an item or type what it is.", "warning")
        return redirect(_capture_redirect(assessment_id))

    with db.get_connection() as conn:
        db.add_item(conn, assessment_id, item_key, area_id, room_id,
                    scope=scope, bank_item_key=bank_key, category_key=category,
                    # A typed label is the only name the item will ever
                    # have, so it is stored on the row itself.
                    instance_label=typed if bank_key is None else None)
    flash(f"Added {label}.", "success")
    return redirect(_capture_redirect(assessment_id) + f"#item-{item_key}")


@site_dd_bp.route("/assessment/<int:assessment_id>/item/remove", methods=["POST"])
@login_required
def remove_item(assessment_id):
    """Take an added item off this scope, every instance of it.

    Refuses anything on the fixed checklist: those are not the
    inspector's to remove, and letting a stray POST delete "Smoke alarm"
    from a unit would turn a required question into an optional one.
    """
    if not _load(assessment_id):
        return _not_found()
    item_key = (request.form.get("item_key") or "").strip()
    area_id = to_int(request.form.get("area_id"))
    room_id = to_int(request.form.get("room_id"))
    if item_key in cl.ITEM_LABELS or uc.is_known_item(item_key):
        flash("That one is part of the checklist.", "warning")
        return redirect(_capture_redirect(assessment_id))
    with db.get_connection() as conn:
        removed = db.delete_item(conn, assessment_id, item_key, area_id, room_id)
    flash("Removed." if removed else "Nothing to remove.",
          "success" if removed else "warning")
    return redirect(_capture_redirect(assessment_id))


@site_dd_bp.route("/assessment/<int:assessment_id>/capex.<fmt>")
@login_required
def capex_budget(assessment_id, fmt):
    """The capital budget for an assessment.

    Reference costs are applied HERE, at export time, rather than being
    written into the findings table. A national average is not something
    the inspector recorded, and storing it beside their judgements would
    make the two indistinguishable a month later -- the whole point of
    est_cost_source. A manual estimate always wins: apply_reference
    leaves a priced row alone.
    """
    assessment = _load(assessment_id)
    if not assessment:
        return _not_found()
    if fmt not in ("pdf", "xlsx"):
        flash("Capex export is available as PDF or Excel.", "warning")
        return redirect(url_for("site_dd.detail", assessment_id=assessment_id))

    with db.get_connection(  ) as conn:
        findings = db.list_all_findings(conn, assessment_id)
        rooms = {}
        for area in db.list_areas(conn, assessment_id):
            for room in db.list_rooms(conn, area["id"]):
                rooms[room["id"]] = room

    # A flooring cost depends on the material, which is a separate item
    # on the same room -- so the type is looked up per room rather than
    # assumed.
    flooring_by_room = {f.get("room_id"): f.get("detail")
                        for f in findings if f.get("item_key") == "flooring_type"}

    labels = dict(cl.ITEM_LABELS)
    for room_type, _ in uc.ROOM_TYPES:
        labels.update({i["key"]: i["label"] for i in uc.items_for_room(room_type)})
    labels.update({i["key"]: i["label"] for i in uc.items_for_unit()})
    labels.update({b["key"]: b["label"] for b in bank.BANK_ITEMS})

    # The catalogue itself, not just its labels, because needs_work() has
    # to see the item's OPTION SET to know which of its values mean work.
    # Same three sources in the same order as the labels above, so an item
    # that can be labelled can also be judged.
    catalogue = bank.every_item()

    # Only findings that actually record a problem reach the budget. A
    # water heater in good order is not a capital line.
    #
    # This used to read `f["condition"] in WORK_CONDITIONS` and nothing
    # else, which meant it and this comment disagreed: a choice item
    # answers in `detail`, so a MISSING water heater -- the example the
    # comment reaches for, and a $1,725 item -- recorded a problem and was
    # dropped anyway. Alarms, GFCIs and every absent appliance went the
    # same way. uc.needs_work() is now the single definition of "records a
    # problem", and the capture screen's cost box opens on the same call.
    work = [f for f in findings
            if uc.needs_work(catalogue.get(f.get("item_key")),
                             f.get("condition"), f.get("detail"))]
    priced = [costs.apply_reference(f, flooring_by_room.get(f.get("room_id")))
              for f in work]
    lines = capex_export.build_lines(priced, labels,
                                     detail_labels=bank.detail_labels())
    summary = capex_export.summarize(lines)

    import tempfile
    out = (Path(tempfile.mkdtemp())
           / capex_export.suggested_filename(assessment, fmt))
    if fmt == "pdf":
        capex_export.build_pdf(out, assessment, lines, summary)
        mime = "application/pdf"
    else:
        capex_export.build_xlsx(out, assessment, lines, summary, labels)
        mime = ("application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet")
    return send_file(str(out), as_attachment=True, download_name=out.name,
                     mimetype=mime)


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
        # Property scope only: area_id and room_id both NULL. Unit and
        # room findings live under their own pages and must not leak into
        # the property checklist's completion figure.
        items = db.get_findings(conn, assessment_id, None, None)
        photos = db.list_media(conn, assessment_id, kind=db.MEDIA_PHOTO)
        summary = cond.summarize({k: [r["condition"] for r in rows] for k, rows in items.items()},
                                 cl.CATEGORIES)
        areas = db.list_areas(conn, assessment_id)
        area_rollups = []
        for a in areas:
            rooms = db.list_rooms(conn, a["id"])
            by_room = {r["id"]: db.get_conditions_map(conn, assessment_id, a["id"], r["id"])
                       for r in rooms}
            unit_rows = db.get_conditions_map(conn, assessment_id, a["id"], None)
            area_rollups.append({
                "area": a, "room_count": len(rooms),
                "summary": uc.summarize_unit(by_room, rooms, unit_rows),
            })

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
