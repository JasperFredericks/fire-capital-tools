"""
FIRE Capital Tools - Admin.

Operational reference pages that aren't deal-workflow tools. Currently
just API & Service Costs; the blueprint exists as a section rather than a
one-off route so the next operational page has somewhere obvious to go
instead of being wedged into Acquisitions or Markets.

Read-only and stateless. No database, no writes, no forms beyond the
shared feedback component -- so no new storage path and no env var to
verify, unlike every tool built this cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, current_app, render_template
from flask_login import login_required

from tools import market_data_service
from tools import site_dd_capture as capture
from tools import site_dd_db as sdd_db, service_costs

admin_bp = Blueprint("admin", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent

FEEDBACK_TOOL_NAME = "API & Service Costs"

# Gitignored local key files, matching the fallbacks passed to get_secret()
# in market_data_service.py and the fire_metrics scripts.
_FALLBACK_KEY_FILES = {
    "RENTCAST_API_KEY": "rentcast_api_key.txt",
    "GOOGLE_PLACES_API_KEY": "google_places_api_key.txt",
    "CENSUS_API_KEY": "data/cache/census_api_key.txt",
    "BLS_API_KEY": "data/cache/bls_api_key.txt",
}


def _is_configured(env_var: str | None) -> bool | None:
    """Whether a service's key actually resolves right now.

    Returns None for a service that has no key at all (FEMA), which the
    template renders as "no key needed" rather than as a failure -- an
    absent key is only a problem when one is expected.

    Checks presence, never the value: the secret is not read into the
    page, logged, or compared against anything. Mirrors get_secret()'s
    env-first resolution, and also honours the gitignored local fallback
    files the fire_metrics scripts and market_data_service use, so a
    developer running locally off a key file doesn't see a false
    "not configured".
    """
    if not env_var:
        return None
    if os.environ.get(env_var, "").strip():
        return True
    # Flask config picks up a few of these at startup (Google Maps, OpenAI).
    if str(current_app.config.get(env_var) or "").strip():
        return True
    fallback = _FALLBACK_KEY_FILES.get(env_var)
    if fallback:
        path = BASE_DIR / fallback
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            # An unreadable key file is not a configured key.
            return False
    return False


# ── Storage ──────────────────────────────────────────────────────────────
#
# Every other line on this page is an API bill. This one is a disk, and it
# is here because Site DD video is the first feature in the app with a
# fast-growing storage footprint: at 40 MB a clip the production volume
# holds about 115 of them. A footprint nobody can see is one nobody checks
# until it is full.

# Warn well before the volume is actually in trouble, so there is time to
# do something other than delete in a hurry.
STORAGE_WARN_PCT = 60.0
STORAGE_CRITICAL_PCT = 85.0


def _media_storage():
    """Site DD media usage, and the volume it sits on. Never raises: this
    is a panel on an admin page, and a missing volume must not take the
    page down with it."""
    out = {"available": False, "reason": None}
    try:
        with sdd_db.get_connection() as conn:
            totals = sdd_db.media_totals(conn)
    except Exception:                       # noqa: BLE001 - display only
        out["reason"] = "Site DD media could not be read."
        return out

    out.update(totals)
    out["available"] = True
    out["human"] = capture.human_bytes(totals["bytes"])
    out["photo_human"] = capture.human_bytes(totals["photo_bytes"])
    out["video_human"] = capture.human_bytes(totals["video_bytes"])

    try:
        st = os.statvfs(sdd_db.get_db_path().parent)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        out["volume_total"] = total
        out["volume_free"] = free
        out["volume_total_human"] = capture.human_bytes(total)
        out["volume_free_human"] = capture.human_bytes(free)
        used_pct = ((total - free) / total * 100) if total else 0.0
        out["volume_used_pct"] = used_pct
        out["level"] = ("critical" if used_pct >= STORAGE_CRITICAL_PCT
                        else "warn" if used_pct >= STORAGE_WARN_PCT else "ok")
        # How many more videos fit, which is the figure that actually
        # answers "should I worry".
        out["videos_remaining"] = int(free // capture.MAX_VIDEO_BYTES)
    except (OSError, AttributeError):
        # statvfs does not exist on Windows; local development shows the
        # media totals without the volume figures rather than nothing.
        out["volume_total"] = None
        out["level"] = "ok"
    out["max_video_mb"] = capture.MAX_VIDEO_BYTES // 1024 // 1024
    return out


@admin_bp.route("/service-costs")
@login_required
def service_costs_page():
    """The cost inventory. Live counters for the two services that have
    them, static figures for everything else, and an explicit count of
    what still needs a human number."""
    live_usage = {
        "rentcast": market_data_service.rentcast_quota(),
        "google_places": market_data_service.google_places_quota(),
    }
    rows = service_costs.services_for(live_usage)
    for row in rows:
        row["configured"] = _is_configured(row["configured_key"])

    storage = _media_storage()

    return render_template(
        "admin/service_costs.html",
        rows=rows,
        tbd_count=service_costs.tbd_count(rows),
        reset_label=market_data_service.quota_reset_label(),
        last_reviewed=service_costs.LAST_REVIEWED,
        tbd_marker=service_costs.TBD,
        storage=storage,
        ai_summaries_enabled=bool(
            current_app.config.get("FIRE_METRICS_AI_SUMMARIES_ENABLED", False)
        ),
        summary_model=str(
            current_app.config.get("FIRE_METRICS_SUMMARY_MODEL") or ""
        ).strip(),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )
