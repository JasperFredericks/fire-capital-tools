"""
FIRE Capital Tools - Investor Report notetaker.

Upload meeting transcripts, match each to a property, filter by date
range, and synthesise the matched set into a structured investor update.

WHY THE DATE IS TYPED IN AND NOT PARSED

Fathom and Otter both put a date in their exports, but not in a shape
this app can rely on: it varies by export format (txt, docx, pdf, and
Fathom's "copy transcript" clipboard form), by the user's locale, and by
whether the meeting title happens to contain a different date. A parser
that is right most of the time silently files a meeting under the wrong
quarter, which is exactly the error that would survive review -- an
investor update covering the wrong period looks completely normal.

So the date is entered once at upload, next to the file, where the
person who exported it is looking at it. If a reliable parse is
confirmed later it becomes a PREFILL for this field, never a
replacement.

WHAT THIS DELIBERATELY DOES NOT DO

Nothing here writes to Underwriting, Deal Dive, Site DD or Rent Comps.
A figure stated on a call is what somebody said; a figure in the model
is what was underwritten. The update shows both and flags a divergence,
and there is no code path that resolves one into the other.
"""

from __future__ import annotations

import datetime
import secrets
from pathlib import Path

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import deal_dive_db
from tools import investor_notes_db as notes_db
from tools import investor_notes_export as export
from tools import investor_notes_match as matching
from tools import investor_notes_properties as properties
from tools import investor_notes_synth as synth
from tools import openai_usage
from tools import site_dd_db
from tools import underwriting_db
from tools import upload_limits as ul
from tools.form_utils import to_int

investor_notes_bp = Blueprint("investor_notes", __name__)

FEEDBACK_TOOL_NAME = "Investor Report"

# Plain text is what both services export and what can be read without a
# parser. Anything else is refused with a message naming the fix rather
# than accepted and mangled.
ALLOWED_EXT = {".txt", ".md", ".vtt", ".srt", ".csv"}


def _upload_dir() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "investor-notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _model_name() -> str:
    import os
    return (os.environ.get("INVESTOR_NOTES_MODEL")
            or os.environ.get("FIRE_METRICS_SUMMARY_MODEL")
            or "gpt-4.1-mini")


def _api_key() -> str:
    return current_app.config.get("OPENAI_API_KEY") or ""


def _property_entries(conn) -> list[dict]:
    """Every property a transcript could belong to, from all three tools."""
    try:
        with deal_dive_db.get_connection() as dd:
            deals = [dict(r) for r in dd.execute(
                "SELECT id, address, city, state FROM deals ORDER BY id")]
    except Exception:
        deals = []
    try:
        with underwriting_db.get_connection() as uw:
            # (label, deal_id) pairs: a scenario that names its deal folds
            # into that deal's entry rather than spawning a rival one.
            uw_labels = [(r[0], r[1]) for r in uw.execute(
                "SELECT DISTINCT property_label, deal_id "
                "FROM underwriting_scenarios WHERE property_label IS NOT NULL")]
    except Exception:
        uw_labels = []
    try:
        with site_dd_db.get_connection() as sd:
            sd_labels = [(r[0], r[1]) for r in sd.execute(
                "SELECT DISTINCT property_label, deal_id "
                "FROM site_dd_assessments WHERE property_label IS NOT NULL")]
    except Exception:
        sd_labels = []
    return properties.build(deals, uw_labels, sd_labels,
                            notes_db.aliases_by_key(conn))


def _quarter_presets(today: datetime.date | None = None) -> list[dict]:
    today = today or datetime.date.today()
    out = []
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    start = datetime.date(today.year, q_start_month, 1)
    for back in range(4):
        month = start.month - 3 * back
        year = start.year
        while month < 1:
            month += 12
            year -= 1
        s = datetime.date(year, month, 1)
        end_month, end_year = month + 2, year
        if end_month > 12:
            end_month -= 12
            end_year += 1
        last = datetime.date(end_year, end_month, 1)
        last = (last.replace(day=28) + datetime.timedelta(days=4)
                ).replace(day=1) - datetime.timedelta(days=1)
        out.append({"label": f"Q{(month - 1) // 3 + 1} {year}",
                    "start": s.isoformat(), "end": last.isoformat()})
    return out


# ── Pool ─────────────────────────────────────────────────────────────────

@investor_notes_bp.route("/notes")
@login_required
def index():
    with notes_db.get_connection() as conn:
        entries = _property_entries(conn)
        transcripts = notes_db.list_transcripts(conn)
        aliases = notes_db.list_aliases(conn)
        # Previously generated updates. list_updates() has existed since
        # the table did, but nothing ever called it -- so an update was
        # reachable only by the redirect right after generating it, or by
        # re-running the identical review query and hitting the cache.
        # Navigate away and the document, and its export buttons, were
        # gone. Same class of gap as the notetaker having no nav entry.
        updates = notes_db.list_updates(conn)
    for t in transcripts:
        t["evidence"] = notes_db.evidence_of(t)
    return render_template(
        "tools/investor_notes.html",
        transcripts=transcripts,
        entries=entries,
        aliases=aliases,
        updates=updates,
        sources=notes_db.SOURCES,
        source_labels=notes_db.SOURCE_LABELS,
        today=datetime.date.today().isoformat(),
        quarters=_quarter_presets(),
        storage=notes_db.storage_status(),
        max_mb=ul.TRANSCRIPT_BYTES // 1024 // 1024,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@investor_notes_bp.route("/notes/upload", methods=["POST"])
@login_required
def upload():
    """One transcript, with its meeting date typed in beside it."""
    upload_file = request.files.get("transcript")
    if not upload_file or not upload_file.filename:
        flash("Choose a transcript file to upload.", "danger")
        return redirect(url_for("investor_notes.index"))

    try:
        ul.check(request.content_length, ul.TRANSCRIPT_BYTES, "transcript")
    except ul.UploadTooLarge as exc:
        flash(str(exc), "danger")
        return redirect(url_for("investor_notes.index"))

    name = secure_filename(upload_file.filename) or "transcript.txt"
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        flash(f"{Path(name).suffix or 'That file type'} is not a transcript "
              f"format this reads. Export as plain text (.txt) from Fathom or "
              f"Otter and upload that.", "danger")
        return redirect(url_for("investor_notes.index"))

    transcript_date = (request.form.get("transcript_date") or "").strip()
    try:
        datetime.date.fromisoformat(transcript_date)
    except ValueError:
        flash("Enter the date the meeting took place — it cannot be read "
              "reliably from the file.", "danger")
        return redirect(url_for("investor_notes.index"))

    raw = upload_file.read()
    body = raw.decode("utf-8", "replace").strip()
    if not body:
        flash("That file contained no readable text.", "danger")
        return redirect(url_for("investor_notes.index"))

    stored = f"{secrets.token_urlsafe(8)}_{name}"
    (_upload_dir() / stored).write_bytes(raw)

    source = (request.form.get("source") or "").strip()
    title = (request.form.get("title") or "").strip() or Path(name).stem

    with notes_db.get_connection() as conn:
        tid = notes_db.add_transcript(
            conn, body=body, transcript_date=transcript_date,
            source=source, title=title, original_name=name,
            stored_name=stored, bytes_len=len(raw))
        # Match immediately, but only ever record what it found -- the
        # page shows the evidence and the result is overridable.
        entries = _property_entries(conn)
        result = matching.match(body, entries)
        if result["outcome"] == "matched":
            notes_db.set_match(conn, tid, property_key=result["key"],
                               property_label=result["label"],
                               method=notes_db.MATCH_AUTO, evidence=result)
        else:
            notes_db.set_match(
                conn, tid, property_key=None, property_label=None,
                method=(notes_db.MATCH_AMBIGUOUS
                        if result["outcome"] == "ambiguous"
                        else notes_db.MATCH_UNASSIGNED),
                evidence=result)

    if result["outcome"] == "matched":
        flash(f"Uploaded and matched to {result['label']}. {result['reason']} "
              f"Check it before generating an update.", "success")
    elif result["outcome"] == "ambiguous":
        flash(f"Uploaded, but not matched: {result['reason']} Pick a property "
              f"below.", "warning")
    else:
        flash(f"Uploaded, but not matched. {result['reason']} Assign it below.",
              "warning")
    return redirect(url_for("investor_notes.index"))


@investor_notes_bp.route("/notes/<int:tid>/assign", methods=["POST"])
@login_required
def assign(tid):
    key = (request.form.get("property_key") or "").strip()
    with notes_db.get_connection() as conn:
        row = notes_db.get_transcript(conn, tid)
        if not row:
            flash("That transcript could not be found.", "danger")
            return redirect(url_for("investor_notes.index"))
        if not key:
            notes_db.set_match(conn, tid, property_key=None, property_label=None,
                               method=notes_db.MATCH_UNASSIGNED, evidence=None)
            flash("Unassigned.", "success")
            return redirect(url_for("investor_notes.index"))
        entry = properties.find(_property_entries(conn), key)
        if not entry:
            flash("That property could not be found.", "danger")
            return redirect(url_for("investor_notes.index"))
        notes_db.set_match(conn, tid, property_key=entry["key"],
                           property_label=entry["label"],
                           method=notes_db.MATCH_MANUAL,
                           evidence={"outcome": "manual",
                                     "reason": "Assigned by hand."})
    flash(f"Assigned to {entry['label']}.", "success")
    return redirect(url_for("investor_notes.index"))


@investor_notes_bp.route("/notes/<int:tid>/delete", methods=["POST"])
@login_required
def delete_transcript(tid):
    with notes_db.get_connection() as conn:
        row = notes_db.delete_transcript(conn, tid)
    if row and row.get("stored_name"):
        (_upload_dir() / row["stored_name"]).unlink(missing_ok=True)
    flash("Transcript removed." if row else "That transcript was already gone.",
          "success" if row else "warning")
    return redirect(url_for("investor_notes.index"))


@investor_notes_bp.route("/notes/aliases", methods=["POST"])
@login_required
def add_alias():
    key = (request.form.get("property_key") or "").strip()
    alias = (request.form.get("alias") or "").strip()
    with notes_db.get_connection() as conn:
        valid = {e["key"] for e in _property_entries(conn)}
        try:
            ok = notes_db.add_alias(conn, key, alias, valid_keys=valid)
        except notes_db.UnknownProperty as exc:
            flash(str(exc), "danger")
            return redirect(url_for("investor_notes.index"))
    flash(f"Added “{alias}”." if ok else "That alias is already there.",
          "success" if ok else "warning")
    return redirect(url_for("investor_notes.index"))


@investor_notes_bp.route("/notes/properties", methods=["POST"])
@login_required
def add_property():
    """Create a property that exists only as a name, for now.

    THE GAP THIS CLOSES

    A property becomes visible to the notetaker by having a record in
    Deal Dive, Underwriting or Site DD -- those three are the only
    sources investor_notes_properties.build() reads. Michelle has
    meetings about properties before any of those exist, and until one
    does there is nothing to attach an alias to and every transcript
    naming the property comes back unassigned. That is not a matching
    problem and no amount of alias work fixes it.

    So this writes the smallest real record rather than inventing a
    fourth kind of property. An Underwriting scenario needs exactly one
    thing, property_label, and everything else is nullable -- so a stub
    is a scenario with a name and no numbers. It gets its key from
    absorb() like every other scenario, appears in the alias list
    immediately, and the day somebody underwrites it they open the
    scenario already sitting there and start typing.

    The alternative -- letting an alias exist on its own -- would create
    a property visible in one tool and unreachable from every other, and
    would then collide with the real record the moment one was made.
    """
    label = " ".join((request.form.get("property_label") or "").split())[:120]
    if not label:
        flash("A property needs a name.", "danger")
        return redirect(url_for("investor_notes.index") + "#properties")

    with notes_db.get_connection() as conn:
        existing = {matching.normalize(e["label"]): e
                    for e in _property_entries(conn)}
    already = existing.get(matching.normalize(label))
    if already:
        flash(f"“{already['label']}” is already here — "
              f"add an alias to it rather than a second copy.", "warning")
        return redirect(url_for("investor_notes.index") + "#properties")

    with underwriting_db.get_connection() as conn:
        scenario_id = underwriting_db.create_scenario(conn, {
            "property_label": label,
            # Named for what it is. Somebody opening Underwriting later
            # should see immediately that no assumptions were ever entered
            # here, rather than a "Base case" with silent defaults.
            "name": "Placeholder — no assumptions entered",
        })

    flash(f"Added “{label}”. It can be aliased now, and its Underwriting "
          f"scenario is ready whenever you want to put numbers in it.",
          "success")
    return redirect(url_for("investor_notes.index") + "#properties")


@investor_notes_bp.route("/notes/aliases/<int:alias_id>/delete", methods=["POST"])
@login_required
def delete_alias(alias_id):
    with notes_db.get_connection() as conn:
        notes_db.delete_alias(conn, alias_id)
    flash("Alias removed.", "success")
    return redirect(url_for("investor_notes.index"))


# ── Review, then synthesis ───────────────────────────────────────────────

@investor_notes_bp.route("/notes/review")
@login_required
def review():
    """The gate. Everything selected here is what gets sent, and nothing
    is sent until the button on this page is pressed."""
    key = (request.args.get("property_key") or "").strip()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()

    with notes_db.get_connection() as conn:
        entries = _property_entries(conn)
        entry = properties.find(entries, key)
        matched = notes_db.list_transcripts(
            conn, property_key=key, start=start or None,
            end=end or None) if entry else []
        unassigned = [t for t in notes_db.list_transcripts(conn)
                      if not t["property_key"]]
        cached = None
        if entry and matched:
            cached = notes_db.find_update(
                conn, synth.cache_key(key, start, end,
                                      [t["id"] for t in matched],
                                      model=_model_name()))
    for t in matched:
        t["evidence"] = notes_db.evidence_of(t)

    return render_template(
        "tools/investor_notes_review.html",
        entries=entries, entry=entry, start=start, end=end,
        matched=matched, unassigned=unassigned, cached=cached,
        quarters=_quarter_presets(),
        source_labels=notes_db.SOURCE_LABELS,
        sections=synth.SECTIONS,
        max_transcripts=synth.MAX_TRANSCRIPTS,
        usage=_usage_snapshot(),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


def _usage_snapshot():
    try:
        with openai_usage.get_connection() as conn:
            data = openai_usage.usage_for_month(conn)
        row = next((r for r in data["rows"]
                    if r["feature"] == openai_usage.FEATURE_INVESTOR_NOTETAKER),
                   None)
        return {"month": data["year_month"], "row": row,
                "total_calls": data["total_calls"]}
    except Exception:
        return None


@investor_notes_bp.route("/notes/generate", methods=["POST"])
@login_required
def generate():
    """The only place that spends. Reached from the review page, with an
    explicit confirmation, and short-circuited by the cache."""
    key = (request.form.get("property_key") or "").strip()
    start = (request.form.get("start") or "").strip()
    end = (request.form.get("end") or "").strip()
    chosen = [to_int(x) for x in request.form.getlist("transcript_ids")]
    chosen = sorted({c for c in chosen if c})
    force = bool(request.form.get("force"))

    back = url_for("investor_notes.review", property_key=key, start=start, end=end)

    with notes_db.get_connection() as conn:
        entry = properties.find(_property_entries(conn), key)
        if not entry:
            flash("Pick a property first.", "danger")
            return redirect(url_for("investor_notes.index"))
        pool = {t["id"]: t for t in notes_db.list_transcripts(
            conn, property_key=key, start=start or None, end=end or None)}
        selected = [pool[i] for i in chosen if i in pool]

        if not selected:
            flash("Select at least one transcript to include.", "warning")
            return redirect(back)

        ck = synth.cache_key(key, start, end, [t["id"] for t in selected],
                             model=_model_name())
        existing = notes_db.find_update(conn, ck)
        if existing and not force:
            flash("This exact update already exists — nothing was re-generated "
                  "and nothing was spent.", "success")
            return redirect(url_for("investor_notes.view_update",
                                    uid=existing["id"]))

        try:
            synth.check_size(selected)
        except synth.TooMuchInput as exc:
            flash(str(exc), "danger")
            return redirect(back)

        if not _api_key():
            flash("OPENAI_API_KEY is not configured, so no update can be "
                  "generated.", "danger")
            return redirect(back)

        try:
            result = synth.synthesize(
                api_key=_api_key(), model_name=_model_name(),
                transcripts=selected, property_label=entry["label"],
                start=start, end=end)
        except Exception as exc:
            flash(f"The update could not be generated: "
                  f"{type(exc).__name__}. Nothing was saved.", "danger")
            return redirect(back)

        uid = notes_db.save_update(
            conn, property_key=key, property_label=entry["label"],
            period_start=start, period_end=end, cache_key=ck,
            prompt_version=synth.PROMPT_VERSION, model=result["model"],
            sections=result["sections"],
            transcript_ids=[t["id"] for t in selected])

    if result["dropped"]:
        flash(f"Generated. {result['dropped']} point(s) were dropped because "
              f"they could not be traced to one of the selected meetings.",
              "warning")
    else:
        flash("Update generated.", "success")
    return redirect(url_for("investor_notes.view_update", uid=uid))


@investor_notes_bp.route("/notes/update/<int:uid>")
@login_required
def view_update(uid):
    with notes_db.get_connection() as conn:
        update = notes_db.get_update(conn, uid)
        if not update:
            flash("That update could not be found.", "danger")
            return redirect(url_for("investor_notes.index"))
        sections = notes_db.sections_of(update)
        ids = notes_db.transcript_ids_of(update)
        sources = [t for t in (notes_db.get_transcript(conn, i) for i in ids) if t]
    return render_template(
        "tools/investor_notes_update.html",
        update=update, sections=sections, sources=sources,
        figures=synth.figures_in(sections),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@investor_notes_bp.route("/notes/update/<int:uid>/export.<fmt>")
@login_required
def export_update(uid, fmt):
    if fmt not in ("pdf", "xlsx", "docx"):
        flash("Export is available as PDF, Word or Excel.", "warning")
        return redirect(url_for("investor_notes.view_update", uid=uid))
    with notes_db.get_connection() as conn:
        update = notes_db.get_update(conn, uid)
        if not update:
            flash("That update could not be found.", "danger")
            return redirect(url_for("investor_notes.index"))
        sections = notes_db.sections_of(update)
        sources = [t for t in (notes_db.get_transcript(conn, i)
                               for i in notes_db.transcript_ids_of(update)) if t]

    # Which sections to include, chosen per export. Absent means every
    # section this update has -- not a curated default, which is the fixed
    # list this design exists to avoid. Order always comes from the update.
    chosen = [k for k in request.args.getlist("section") if k]
    sections = export.select_sections(sections, chosen)
    if not sections:
        flash("Select at least one section to export.", "warning")
        return redirect(url_for("investor_notes.view_update", uid=uid))

    import tempfile
    out = Path(tempfile.mkdtemp()) / export.suggested_filename(update, fmt)
    if fmt == "pdf":
        export.build_pdf(out, update, sections, sources)
        mime = "application/pdf"
    elif fmt == "docx":
        export.build_docx(out, update, sections, sources)
        mime = ("application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document")
    else:
        export.build_xlsx(out, update, sections, sources)
        mime = ("application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet")
    return send_file(str(out), as_attachment=True,
                     download_name=out.name, mimetype=mime)


@investor_notes_bp.route("/notes/update/<int:uid>/delete", methods=["POST"])
@login_required
def delete_update(uid):
    with notes_db.get_connection() as conn:
        notes_db.delete_update(conn, uid)
    flash("Update deleted.", "success")
    return redirect(url_for("investor_notes.index"))
