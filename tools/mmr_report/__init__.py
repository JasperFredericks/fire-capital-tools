"""FIRE Capital Tools — MMR summary report package.

Split from the standalone mmr-summary/generate_summary.py. Re-exports the
names the Flask loader (tools/mmr_summary.py) and the CLI shim import.
"""

from tools.mmr_report.appfolio import (
    parse_appfolio,
)
from tools.mmr_report.builders import (
    build_summary,
    make_download_filename,
)
from tools.mmr_report.detection import (
    detect_source_system,
)
from tools.mmr_report.helpers import (
    fmt_month,
    fmt_pct,
)
from tools.mmr_report.parsers import (
    extract_appfolio_box_score,
    parse_available_units,
    parse_box_score,
    parse_delinquency,
    parse_expiring_leases,
    parse_prospect_sources,
    parse_rent_roll,
)
from tools.mmr_report.sheets import (
    default_box_score,
    parse_optional_sheet,
    sheet_by_name,
)
from tools.mmr_report.work_orders import (
    parse_work_orders,
)

__all__ = [
    "build_summary",
    "default_box_score",
    "detect_source_system",
    "extract_appfolio_box_score",
    "fmt_month",
    "fmt_pct",
    "make_download_filename",
    "parse_appfolio",
    "parse_available_units",
    "parse_box_score",
    "parse_delinquency",
    "parse_expiring_leases",
    "parse_optional_sheet",
    "parse_prospect_sources",
    "parse_rent_roll",
    "parse_work_orders",
    "sheet_by_name",
]
