"""FIRE Capital Tools — Scorecard Pro package.

Split from the original single-file tools/scorecard_pro.py; the public
entry point (the Flask blueprint) is re-exported here so
`from tools.scorecard_pro import scorecard_bp` keeps working unchanged.
"""

from tools.scorecard_pro.routes import scorecard_bp

__all__ = ["scorecard_bp"]
