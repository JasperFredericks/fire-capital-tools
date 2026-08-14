"""
FIRE Capital Tools - per-endpoint upload size limits.

WHY THIS FILE EXISTS

Until video, exactly one upload endpoint in this app had its own size
limit: FIRE Metrics' crime workbook, at 10 MB. Every other one -- the
Scorecard P&L, the MMR file, Deal Dive documents, Underwriting's rent
roll and T12, the Quick Analyzer's T12, Site DD photos -- was protected
only by Flask's app-wide MAX_CONTENT_LENGTH of 20 MB.

Site DD video needs more than 20 MB, so that global figure had to rise.
Raising it on its own would have silently removed the ONLY size guard
from eight endpoints at once: a 45 MB spreadsheet would have started
being accepted by tools that had been protected by 20 MB purely as a
side effect.

So the global limit rises to what video needs, and every other endpoint
gains an explicit limit at the value it effectively had before. Nothing
loses protection; one thing gains headroom. The limits are stated here,
in one file, rather than scattered as literals -- so the next person to
change MAX_CONTENT_LENGTH can see what depends on it.

Pure: no Flask import, so the numbers can be asserted in tests without a
request context.
"""

from __future__ import annotations

MB = 1024 * 1024

# The app-wide backstop, mirrored in config.py. Sized for the largest
# single thing the app now accepts -- a 40 MB video -- plus room for
# multipart framing and the other form fields travelling with it.
GLOBAL_MAX_CONTENT_BYTES = 48 * MB

# Spreadsheets and documents. 20 MB is what these endpoints were
# effectively limited to before the global cap moved, so this preserves
# their behaviour exactly rather than choosing a new number for them.
SPREADSHEET_BYTES = 20 * MB
DOCUMENT_BYTES = 20 * MB

# Site DD capture. Defined in site_dd_capture and restated here so the
# table below is complete; a test asserts the two agree.
PHOTO_BYTES = 12 * MB
VIDEO_BYTES = 40 * MB

# FIRE Metrics' crime workbook already had its own limit; kept as it was.
CRIME_WORKBOOK_BYTES = 10 * MB

# A meeting transcript is plain text. An hour of conversation is around
# 50 KB, so 5 MB is generous by two orders of magnitude while still being
# small enough that a spreadsheet or a video uploaded here by mistake is
# refused at the edge rather than read and found to be gibberish.
TRANSCRIPT_BYTES = 5 * MB

# Every upload endpoint in the app and the limit that applies to it.
# Exhaustive on purpose: a test walks the routes and fails if an endpoint
# accepting a file is missing from this table, so a new upload cannot
# quietly inherit the global cap as its only protection again.
ENDPOINT_LIMITS = {
    "scorecard_pro.upload": SPREADSHEET_BYTES,
    "scorecard_pro.upload_scorecard": SPREADSHEET_BYTES,
    "mmr.upload": SPREADSHEET_BYTES,
    "underwriting.upload_rentroll": SPREADSHEET_BYTES,
    "underwriting.upload_t12": SPREADSHEET_BYTES,
    "deal_analyzer.index": SPREADSHEET_BYTES,
    "deal_dive.upload_document": DOCUMENT_BYTES,
    "site_dd.upload_photo": VIDEO_BYTES,      # the route accepts both kinds
    "fire_metrics.upload_crime_workbook": CRIME_WORKBOOK_BYTES,
    "investor_notes.upload": TRANSCRIPT_BYTES,
}


class UploadTooLarge(Exception):
    """Raised with a message written for the person uploading."""


def limit_for(endpoint: str) -> int:
    return ENDPOINT_LIMITS.get(endpoint, SPREADSHEET_BYTES)


def check(size_bytes: int | None, limit_bytes: int, what: str = "file") -> None:
    """Refuse anything over the limit.

    A None size means the client sent no Content-Length. That is allowed
    through here rather than refused: Flask's MAX_CONTENT_LENGTH still
    caps the stream, and refusing an unknown length would break clients
    that stream legitimately.
    """
    if size_bytes is None:
        return
    if size_bytes > limit_bytes:
        raise UploadTooLarge(
            f"That {what} is {size_bytes / MB:.1f} MB — the limit here is "
            f"{limit_bytes // MB} MB.")
