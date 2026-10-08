"""Shared test configuration.

The API reads CEG_DB_PATH and CEG_STATS_PATH when first imported, so they
are set here, before any test module imports the app: tests never touch the
developer's ``ceg.db`` nor the learned statistics.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ceg-tests-")
os.environ.setdefault("CEG_DB_PATH", os.path.join(_TMP, "ceg.db"))
os.environ.setdefault("CEG_STATS_PATH", os.path.join(_TMP, "model_statistics.json"))
