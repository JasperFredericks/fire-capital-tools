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

from tools import market_data_service, service_costs

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

    return render_template(
        "admin/service_costs.html",
        rows=rows,
        tbd_count=service_costs.tbd_count(rows),
        reset_label=market_data_service.quota_reset_label(),
        last_reviewed=service_costs.LAST_REVIEWED,
        tbd_marker=service_costs.TBD,
        ai_summaries_enabled=bool(
            current_app.config.get("FIRE_METRICS_AI_SUMMARIES_ENABLED", False)
        ),
        summary_model=str(
            current_app.config.get("FIRE_METRICS_SUMMARY_MODEL") or ""
        ).strip(),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )
