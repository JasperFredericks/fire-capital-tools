"""FIRE Capital Tools — FIRE Metrics package.

Split from the original single-file tools/fire_metrics.py. Re-exports the
Flask blueprint and the symbols the test suite imports so
`from tools.fire_metrics import ...` keeps working unchanged. `ai_summary`
is re-exported as a module so tests can patch tools.fire_metrics.ai_summary.
"""

from tools import fire_metrics_ai_summary as ai_summary
from tools.fire_metrics.routes import city_summary, fire_metrics_bp, top_cities
from tools.fire_metrics.services import _summary_unavailable_response

__all__ = [
    "ai_summary",
    "city_summary",
    "fire_metrics_bp",
    "top_cities",
    "_summary_unavailable_response",
]
