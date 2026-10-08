"""The workflows the API can run, discovered like Airflow discovers DAGs.

Two sources, gathered by ``ceg.registry.WorkflowRegistry``:
  - the example workflows shipped with CEG (``ceg.use_cases``);
  - the user's workflow files: every ``*.py`` of ``CEG_WORKFLOWS_DIR``
    (default: the ``workflows/`` folder at the root of the repository).

The folder is re-scanned when a file is added, changed or removed, so a new
workflow appears on the platform without restarting the API. A file that
fails to import is reported (``REGISTRY.errors``) instead of breaking the
others.
"""

from __future__ import annotations

import os
from pathlib import Path

from ceg.registry import WorkflowDefinition, WorkflowRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]

WORKFLOWS_DIR = Path(os.getenv("CEG_WORKFLOWS_DIR", str(REPO_ROOT / "workflows")))

REGISTRY = WorkflowRegistry(folder=WORKFLOWS_DIR).load()

# Former name of a workflow definition in the API.
PipelineSpec = WorkflowDefinition


def pipelines() -> dict[str, WorkflowDefinition]:
    """Every known workflow, re-scanning the folder if it changed."""
    REGISTRY.refresh_if_changed()
    return REGISTRY.workflows


def display_path(path: str | None) -> str | None:
    """``path`` relative to the repository root when possible."""
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path
