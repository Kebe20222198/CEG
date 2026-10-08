"""Code generation: a CognitiveTask written back as readable Python.

Every workflow can then be shown as code — including the ones created
through the API, which have no source file — the way Airflow shows the code
of a DAG. The generated code is exactly the declaration that runs:
executing it rebuilds an equal ``CognitiveTask`` (round trip).

Only fields that differ from their default are written, so the code reads
like what a person would have typed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

import ceg
from ceg.models.task import CognitiveTask

LINE_LENGTH = 88
INDENT = "    "


def task_to_python(task: CognitiveTask, variable: str = "task") -> str:
    """Python source that rebuilds ``task`` and plans it.

    Args:
        task: The declaration to write out.
        variable: Name of the variable holding the task.

    Returns:
        A self-contained Python module: imports, the declaration, and the
        ``plan()`` call producing its execution graph.
    """
    writer = _Writer()
    body = writer.expr(task, 0)
    writer.require("plan", "ceg")
    header = f"# Déclaration CEG — {task.name}" if task.name else "# Déclaration CEG"
    return (
        f"{header}\n"
        f"{writer.imports()}\n\n"
        f"{variable} = {body}\n\n"
        f"graph = plan({variable})  # plan d'exécution, indépendant du backend\n"
    )


class _Writer:
    """Turns pydantic models and plain values into Python expressions."""

    def __init__(self) -> None:
        self._classes: dict[str, str] = {}

    def require(self, name: str, module: str) -> None:
        """Import ``name`` from ``module`` in the generated code."""
        self._classes[name] = module

    def imports(self) -> str:
        by_module: dict[str, list[str]] = {}
        for name, module in self._classes.items():
            by_module.setdefault(module, []).append(name)
        return "\n".join(
            f"from {module} import {', '.join(sorted(names))}"
            for module, names in sorted(by_module.items())
        )

    def expr(self, value: Any, level: int) -> str:
        if isinstance(value, BaseModel):
            return self._model(value, level)
        if isinstance(value, list):
            return self._sequence(value, level)
        return repr(value)

    def _model(self, model: BaseModel, level: int) -> str:
        cls = type(model)
        # Public classes are imported from the ceg package itself.
        module = "ceg" if hasattr(ceg, cls.__name__) else cls.__module__
        self._classes[cls.__name__] = module

        explicit = model.model_dump(exclude_defaults=True)
        fields = [
            (name, getattr(model, name))
            for name in cls.model_fields
            if name in explicit
        ]
        if not fields:
            return f"{cls.__name__}()"

        inline = (
            f"{cls.__name__}("
            + ", ".join(f"{name}={self.expr(v, 0)}" for name, v in fields)
            + ")"
        )
        nested = any(
            isinstance(v, BaseModel) or (isinstance(v, list) and v) for _, v in fields
        )
        if not nested and len(INDENT * level) + len(inline) <= LINE_LENGTH:
            return inline

        pad = INDENT * (level + 1)
        lines = []
        for name, v in fields:
            line = f"{pad}{name}={self.expr(v, level + 1)},"
            if isinstance(v, str) and len(line) > LINE_LENGTH:
                line = f"{pad}{name}={_wrapped_string(v, level + 1)},"
            lines.append(line)
        return f"{cls.__name__}(\n" + "\n".join(lines) + f"\n{INDENT * level})"

    def _sequence(self, items: list[Any], level: int) -> str:
        if not items:
            return "[]"
        if not any(isinstance(i, BaseModel) for i in items):
            inline = repr(items)
            if len(INDENT * level) + len(inline) <= LINE_LENGTH:
                return inline
        pad = INDENT * (level + 1)
        lines = [f"{pad}{self.expr(i, level + 1)}," for i in items]
        return "[\n" + "\n".join(lines) + f"\n{INDENT * level}]"


def _wrapped_string(text: str, level: int) -> str:
    """A long string as implicitly concatenated chunks inside parentheses."""
    pad = INDENT * (level + 1)
    width = LINE_LENGTH - len(pad) - 2  # room for the quotes
    chunks: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and len(repr(candidate + " ")) > width:
            chunks.append(current + " ")
            current = word
        else:
            current = candidate
    chunks.append(current)
    lines = "\n".join(f"{pad}{chunk!r}" for chunk in chunks)
    return f"(\n{lines}\n{INDENT * level})"
