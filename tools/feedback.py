"""
FIRE Capital Tools - Beta feedback capture.

One POST route, shared by every tool. A tool opts in by including
templates/_feedback.html with its own name:

    {% include "_feedback.html" with context %}

after setting `feedback_tool` in the render context (or passing it via
`{% set feedback_tool = "Deal Analyzer" %}`). Nothing else is required --
no per-tool route, no per-tool table, no per-tool template.

Deliberately tiny. This exists so beta notes land somewhere durable and
attributable instead of scattering across email; it is not meant to grow
into a ticketing system.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, request, url_for
from flask_login import login_required

from tools import feedback_db as db

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.route("/", methods=["POST"])
@login_required
def submit():
    """Record one note and return the user to where they wrote it.

    Redirects back to the referring page rather than a confirmation screen
    -- feedback is written mid-task, and bouncing someone out of a filled-in
    form to acknowledge their note would lose the work they were doing."""
    tool = (request.form.get("tool") or "Unknown").strip()[:100]
    message = (request.form.get("message") or "").strip()
    page_url = (request.form.get("page_url") or request.referrer or "").strip()[:500] or None

    # Fall back to the dashboard only if there is no referrer at all; a
    # direct POST with no context shouldn't 500.
    back = request.referrer or url_for("dashboard")

    if not message:
        flash("Feedback was empty — nothing sent.", "warning")
        return redirect(back)

    with db.get_connection() as conn:
        db.add_feedback(conn, tool, message, page_url)

    flash("Thanks — feedback sent.", "success")
    return redirect(back)
