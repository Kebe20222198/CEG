"""Shared test configuration.

The API reads CEG_DB_PATH when ``api.db`` is first imported, so it is set
here, before any test module imports the app: tests never touch the
developer's ``ceg.db``.
"""

import os
import tempfile

os.environ.setdefault(
    "CEG_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="ceg-tests-"), "ceg.db")
)
