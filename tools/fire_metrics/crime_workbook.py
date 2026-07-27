from __future__ import annotations

import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.fire_metrics.constants import (
    CRIME_WORKBOOK_HEADER_ROW,
    CRIME_WORKBOOK_REQUIRED_COLUMNS,
    _SCRIPTS_DIR,
)


def _normalize_crime_workbook_header(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _get_crime_workbook_path() -> Path:
    """fire_metrics/scripts isn't a real package -- it's a directory of
    standalone scripts imported via sys.path insertion, the same way
    orchestrator.py does it. Mirrored here (rather than importing
    orchestrator itself) since this web process deliberately doesn't
    import the pipeline/orchestration modules otherwise -- only the
    refresh subprocess does; see _start_refresh()'s docstring for why.
    """
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    from add_crime_index import get_fbi_crime_workbook_path
    return get_fbi_crime_workbook_path()


def _crime_workbook_status() -> dict:
    path = _get_crime_workbook_path()
    if not path.exists():
        return {"exists": False, "uploaded_at": None, "path": str(path)}
    uploaded_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return {"exists": True, "uploaded_at": uploaded_at, "path": str(path)}


def _validate_crime_workbook_bytes(data: bytes) -> str | None:
    """Return an error message if `data` doesn't look like a real FBI
    Table 8 workbook, or None if it looks valid enough to save.

    This is a structural sanity check, not a re-implementation of the
    crime pipeline's own matching/scoring logic -- just enough to reject
    an obviously-wrong file (wrong format, wrong sheet layout, wrong
    columns) with a clear reason up front, instead of silently accepting
    it and having the actual pipeline run fail confusingly later, or
    worse, "succeed" on garbage data.
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        return f"Could not read this file as an Excel workbook: {exc}"

    try:
        ws = wb.worksheets[0]
        header_cells = next(
            ws.iter_rows(min_row=CRIME_WORKBOOK_HEADER_ROW, max_row=CRIME_WORKBOOK_HEADER_ROW, values_only=True),
            None,
        )
        if header_cells is None:
            return (
                f"This workbook doesn't have a row {CRIME_WORKBOOK_HEADER_ROW} -- the FBI "
                f"Table 8 workbook has a few title rows before its real header row, which "
                f"is expected there."
            )

        normalized = {_normalize_crime_workbook_header(cell) for cell in header_cells if cell is not None}
        missing = CRIME_WORKBOOK_REQUIRED_COLUMNS - normalized
        if missing:
            found_preview = ", ".join(sorted(normalized)[:15]) or "(no column headers found)"
            return (
                f"This doesn't look like an FBI Table 8 workbook. Expected a header row at "
                f"row {CRIME_WORKBOOK_HEADER_ROW} including columns "
                f"{sorted(CRIME_WORKBOOK_REQUIRED_COLUMNS)}, but couldn't find: {sorted(missing)}. "
                f"Found instead: {found_preview}"
            )
    finally:
        wb.close()

    return None
