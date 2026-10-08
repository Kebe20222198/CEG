"""Shared test configuration.

The API reads CEG_DB_PATH, CEG_STATS_PATH and CEG_WORKFLOWS_DIR when first
imported, so they are set here, before any test module imports the app:
tests never touch the developer's ``ceg.db``, learned statistics or
workflows folder.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ceg-tests-")
os.environ.setdefault("CEG_DB_PATH", os.path.join(_TMP, "ceg.db"))
os.environ.setdefault("CEG_STATS_PATH", os.path.join(_TMP, "model_statistics.json"))
# User workflow files: a temporary folder the tests write into.
os.environ.setdefault("CEG_WORKFLOWS_DIR", os.path.join(_TMP, "workflows"))
os.makedirs(os.environ["CEG_WORKFLOWS_DIR"], exist_ok=True)
