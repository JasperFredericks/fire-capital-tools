"""Test-suite bootstrap.

Exists for one reason: to stop the suite writing to real databases.

tools/openai_usage.record() is called from inside openai_summary() and
openai_cre_research(), which is deliberate -- it is the only placement
that guarantees a cache hit cannot inflate the count. The consequence is
that any test exercising those functions with a mocked OpenAI client
records a real row, and tests/test_fire_metrics_improvements.py does
exactly that eighteen times.

That was not theoretical. Running the suite on production inflated the
live counter from 1 call to 37 -- thirty-six phantom calls at one token
each, because int(MagicMock()) is 1. The counter is meant to answer
"what is spending the OpenAI budget", and a number inflated by CI is
worse than no number.

Pointing the path at a temp file here fixes it for every test, including
ones not yet written, rather than relying on each author to remember. It
is set only when the variable is unset or points at a real deployment
path, so a developer who deliberately aims it somewhere is not overridden.
"""

import os
import tempfile

_path = os.environ.get("OPENAI_USAGE_DB_PATH", "")
if not _path or _path.startswith("/data/"):
    os.environ["OPENAI_USAGE_DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="fct-test-openai-usage-"), "openai_usage.db")
