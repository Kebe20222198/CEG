"""Workflow inputs prepared for real models.

A simulated executor reads a CSV file itself, from its path. A real model
only sees what is in its prompt: for it, the file's content replaces the
path, in a compact form (column names, then one list of values per row).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

MAX_ROWS = 500


def _value(text: str) -> Any:
    """A number when the cell is one, else the text."""
    text = text.strip()
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            continue
    return text


def csv_inputs(path: str | Path) -> dict[str, Any]:
    """The rows of a CSV file, to give a real model instead of its path.

    A row whose number of cells does not match the header is kept as raw
    text (``ligne_invalide``): detecting it is part of the model's job.
    """
    file = Path(path)
    with file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    header = [h.strip() for h in rows[0]] if rows else []
    data = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    lines: list[Any] = [
        [_value(cell) for cell in row]
        if len(row) == len(header)
        else {"ligne_invalide": ",".join(row)}
        for row in data[:MAX_ROWS]
    ]
    return {
        "fichier": file.name,
        "colonnes": header,
        "lignes": lines,
        "lignes_omises": max(0, len(data) - MAX_ROWS),
    }


def for_real_models(inputs: dict[str, Any]) -> dict[str, Any]:
    """``inputs`` with a ``csv_path`` replaced by the file's content."""
    if "csv_path" not in inputs:
        return inputs
    rest = {k: v for k, v in inputs.items() if k != "csv_path"}
    return {**rest, "donnees_csv": csv_inputs(inputs["csv_path"])}
