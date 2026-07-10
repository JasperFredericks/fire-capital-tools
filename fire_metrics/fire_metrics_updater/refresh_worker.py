"""Standalone entry point for running a FIRE Metrics refresh as a real,
separate OS process -- not a thread.

Launched via subprocess.Popen from tools/fire_metrics.py's
_start_refresh(), specifically because the climate-risk step is CPU-heavy
enough (geopandas/GDAL geometry processing over a nationwide shapefile)
that running it as a thread inside the web process starves that process's
own ability to answer the request that triggered it -- and any other
concurrent request -- via ordinary GIL contention. Confirmed empirically:
threaded=True on the dev server does not fix this, because the problem is
CPU/GIL contention, not a connection-accept limit. A real subprocess has
its own interpreter and its own GIL, so it doesn't compete with the web
process for either.

Status lives in the refresh_metadata table (the same SQLite DB everything
else uses), not in any in-process variable, so it's readable regardless of
which process or request asks -- see tools/fire_metrics.py's
_refresh_status()/_is_refresh_running(). The parent (web) process writes
the "started" bookkeeping (refresh_running/refresh_started_at/refresh_pid)
itself, synchronously, right after spawning this process -- this script
only needs to write the *completion* state (success or error) when the
real work is done.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fire_metrics.fire_metrics_updater import db as db_module  # noqa: E402
from fire_metrics.fire_metrics_updater.orchestrator import run_full_refresh  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-climate", action="store_true")
    parser.add_argument("--skip-crime", action="store_true")
    args = parser.parse_args()

    try:
        result = run_full_refresh(skip_climate=args.skip_climate, skip_crime=args.skip_crime)
        with db_module.get_connection() as conn:
            db_module.set_metadata(
                conn,
                refresh_running="0",
                last_refresh_status="current" if not result["errors"] else "error",
                last_refresh_error="; ".join(f"{e['step']}: {e['error']}" for e in result["errors"]) or None,
                refresh_steps_json=json.dumps(result["steps"]),
            )
        return 0
    except Exception as exc:
        # run_full_refresh already isolates individual step failures (see
        # orchestrator._step) -- reaching here means something outside any
        # single step went wrong (e.g. the DB itself became unreachable).
        # Still must leave refresh_running=0, or this process's own crash
        # would look identical to "still running" until the staleness
        # timeout in _is_refresh_running() eventually kicks in.
        try:
            with db_module.get_connection() as conn:
                db_module.set_metadata(
                    conn,
                    refresh_running="0",
                    last_refresh_status="error",
                    last_refresh_error=str(exc),
                )
        except Exception:
            pass  # If even this fails, the staleness timeout is the backstop.
        return 1


if __name__ == "__main__":
    sys.exit(main())
