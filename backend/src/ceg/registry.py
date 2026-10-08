"""Workflows as Python files: the ``@workflow`` decorator and their discovery.

This is how a user brings a workflow to CEG, the way Airflow users drop a
DAG file into ``dags/``:

    # workflows/tri_tickets.py
    from ceg import CognitiveTask, SubTask, workflow

    @workflow(executor=MesEtapes)
    def tri_tickets() -> CognitiveTask:
        return CognitiveTask(objective="...", subtasks=[...])

The decorated function becomes a ``WorkflowDefinition``: the declaration
(what to do, under which constraints) together with what CEG needs to run
it — the executor implementing the steps, the quality criteria, default
inputs. ``WorkflowRegistry`` imports every ``.py`` file of a folder and
collects these definitions; a file that fails to import is reported with
its error instead of breaking the others (Airflow's "Broken DAG").
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, overload

from ceg.compiler.mock_executor import MockExecutor
from ceg.models.task import CognitiveTask

# Example workflows shipped with CEG (the demo pipelines).
BUILTIN_MODULES = (
    "ceg.use_cases.sales_pipeline",
    "ceg.use_cases.demo_pipelines",
    "ceg.use_cases.multi_agent_supervisor",
)


@dataclass(frozen=True)
class WorkflowDefinition:
    """A workflow: its declaration and what is needed to run it.

    Calling the definition returns the declaration (``CognitiveTask``).

    Attributes:
        id: Unique identifier (the function name unless given).
        declare: Returns the declaration.
        make_executor: Returns a fresh executor implementing the steps.
        criteria: Quality criteria for the Evaluation Engine.
        default_inputs: Graph inputs used when a run provides none.
        uses_csv: The workflow reads ``inputs["csv_path"]``.
        description: First line of the declaring function's docstring.
    """

    id: str
    declare: Callable[[], CognitiveTask]
    make_executor: Callable[[], MockExecutor] = MockExecutor
    criteria: list[Any] = field(default_factory=list)
    default_inputs: dict[str, Any] = field(default_factory=dict)
    uses_csv: bool = False
    description: str = ""

    def __call__(self) -> CognitiveTask:
        return self.declare()

    @property
    def source_file(self) -> str | None:
        """File where the workflow is declared."""
        try:
            return inspect.getsourcefile(self.declare)
        except TypeError:
            return None

    @property
    def executor_class(self) -> type:
        """Class of the executor (resolved by building one)."""
        return type(self.make_executor())


Declaration = Callable[[], CognitiveTask]


@overload
def workflow(fn: Declaration, /) -> WorkflowDefinition: ...


@overload
def workflow(
    *,
    id: str | None = None,
    executor: Callable[[], MockExecutor] = MockExecutor,
    criteria: list[Any] | None = None,
    default_inputs: dict[str, Any] | None = None,
    uses_csv: bool = False,
) -> Callable[[Declaration], WorkflowDefinition]: ...


def workflow(
    fn: Declaration | None = None,
    /,
    *,
    id: str | None = None,
    executor: Callable[[], MockExecutor] = MockExecutor,
    criteria: list[Any] | None = None,
    default_inputs: dict[str, Any] | None = None,
    uses_csv: bool = False,
) -> WorkflowDefinition | Callable[[Declaration], WorkflowDefinition]:
    """Turn a function returning a ``CognitiveTask`` into a workflow.

    Usable bare (``@workflow``) or with options
    (``@workflow(executor=MesEtapes, criteria=[...])``).

    Args:
        fn: The declaring function (bare use).
        id: Workflow identifier; defaults to the function name.
        executor: Executor class or factory implementing the steps.
        criteria: Quality criteria (``ceg.evaluation.models.Criterion``).
        default_inputs: Inputs used when a run provides none.
        uses_csv: The workflow reads ``inputs["csv_path"]``.
    """

    def wrap(declare: Callable[[], CognitiveTask]) -> WorkflowDefinition:
        doc = inspect.getdoc(declare) or ""
        return WorkflowDefinition(
            id=id or declare.__name__,
            declare=declare,
            make_executor=executor,
            criteria=list(criteria or []),
            default_inputs=dict(default_inputs or {}),
            uses_csv=uses_csv,
            description=doc.splitlines()[0] if doc else "",
        )

    return wrap(fn) if fn is not None else wrap


class WorkflowRegistry:
    """The workflows CEG knows: built-in examples plus a folder of files.

    Args:
        folder: Folder of user workflow files (``*.py``); None for none.
        include_builtins: Also register the example workflows of CEG.
    """

    def __init__(self, folder: Path | None = None, include_builtins: bool = True):
        self.folder = folder
        self.include_builtins = include_builtins
        self.workflows: dict[str, WorkflowDefinition] = {}
        self.errors: dict[str, str] = {}
        self._signature: tuple[tuple[str, float], ...] | None = None

    def load(self) -> WorkflowRegistry:
        """(Re)discover every workflow."""
        self.workflows, self.errors = {}, {}
        if self.include_builtins:
            for name in BUILTIN_MODULES:
                self._collect(importlib.import_module(name), origin=name)
        for path in self._files():
            self._load_file(path)
        self._signature = self._current_signature()
        return self

    def refresh_if_changed(self) -> bool:
        """Reload if a workflow file was added, removed or modified."""
        if self._signature == self._current_signature():
            return False
        self.load()
        return True

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        """The workflow ``workflow_id``, if known."""
        return self.workflows.get(workflow_id)

    # ── Internals ─────────────────────────────────────────────────────

    def _files(self) -> list[Path]:
        if self.folder is None or not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.glob("*.py") if not p.name.startswith("_"))

    def _current_signature(self) -> tuple[tuple[str, float], ...]:
        return tuple((str(p), p.stat().st_mtime) for p in self._files())

    def _load_file(self, path: Path) -> None:
        module_name = f"ceg_workflows.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            self.errors[str(path)] = traceback.format_exc(limit=3)
            return
        found = self._collect(module, origin=str(path))
        if not found and str(path) not in self.errors:
            self.errors[str(path)] = (
                "No workflow found: decorate a function returning a "
                "CognitiveTask with @workflow."
            )

    def _collect(self, module: Any, origin: str) -> int:
        found = 0
        for value in vars(module).values():
            if not isinstance(value, WorkflowDefinition):
                continue
            existing = self.workflows.get(value.id)
            if existing is not None and existing is not value:
                self.errors[origin] = (
                    f"Workflow id '{value.id}' is already defined in "
                    f"{existing.source_file}."
                )
                continue
            self.workflows[value.id] = value
            found += 1
        return found
