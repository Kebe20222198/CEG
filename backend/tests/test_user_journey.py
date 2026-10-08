"""The user journey: write a workflow file, validate it, run it, see it.

Like Airflow: a workflow is a Python file in a folder; CEG discovers it, the
``ceg`` command validates and runs it, and the platform lists it without a
restart. Files that fail to load are reported, not fatal.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.pipelines import WORKFLOWS_DIR
from ceg import CognitiveTask, WorkflowDefinition, WorkflowRegistry, workflow
from ceg.backends import get_backend
from ceg.cli import main
from ceg.planner import plan

REPO_WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"

GOOD = textwrap.dedent(
    '''
    from ceg import CognitiveTask, SubTask, workflow

    @workflow(default_inputs={"sujet": "ventes"})
    def mon_rapport() -> CognitiveTask:
        """Rédiger un rapport en deux étapes."""
        return CognitiveTask(
            objective="Rédiger un rapport",
            subtasks=[
                SubTask(id="chercher", objective="Chercher"),
                SubTask(id="ecrire", objective="Écrire", dependencies=["chercher"]),
            ],
        )
    '''
)
BROKEN = "from ceg import workflow\ndef oups(:\n"
EMPTY = "VALUE = 1\n"


def _folder(tmp_path: Path, **files: str) -> Path:
    for name, content in files.items():
        (tmp_path / f"{name}.py").write_text(content, encoding="utf-8")
    return tmp_path


class TestDecorator:
    def test_bare_decorator(self) -> None:
        @workflow
        def simple() -> CognitiveTask:
            """Une seule étape."""
            return CognitiveTask(objective="o")

        assert isinstance(simple, WorkflowDefinition)
        assert simple.id == "simple"
        assert simple.description == "Une seule étape."
        assert simple().objective == "o"

    def test_options(self) -> None:
        @workflow(id="autre_nom", default_inputs={"a": 1}, uses_csv=True)
        def declared() -> CognitiveTask:
            return CognitiveTask(objective="o")

        assert declared.id == "autre_nom"
        assert declared.default_inputs == {"a": 1}
        assert declared.uses_csv


class TestDiscovery:
    def test_files_of_the_folder_are_discovered(self, tmp_path: Path) -> None:
        registry = WorkflowRegistry(folder=_folder(tmp_path, rapport=GOOD)).load()
        assert "mon_rapport" in registry.workflows
        assert "analyse_ventes_alertes" in registry.workflows  # built-in examples
        assert registry.errors == {}

    def test_broken_files_are_reported_not_fatal(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path, rapport=GOOD, casse=BROKEN, vide=EMPTY)
        registry = WorkflowRegistry(folder=folder).load()
        assert "mon_rapport" in registry.workflows
        errors = {Path(p).name: e for p, e in registry.errors.items()}
        assert "SyntaxError" in errors["casse.py"]
        assert "No workflow found" in errors["vide.py"]

    def test_duplicate_ids_are_reported(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path, a=GOOD, b=GOOD)
        registry = WorkflowRegistry(folder=folder).load()
        assert any("already defined" in e for e in registry.errors.values())

    def test_new_files_are_picked_up(self, tmp_path: Path) -> None:
        registry = WorkflowRegistry(folder=tmp_path).load()
        assert not registry.refresh_if_changed()
        _folder(tmp_path, rapport=GOOD)
        assert registry.refresh_if_changed()
        assert "mon_rapport" in registry.workflows

    def test_private_files_are_ignored(self, tmp_path: Path) -> None:
        registry = WorkflowRegistry(folder=_folder(tmp_path, _helpers=BROKEN)).load()
        assert registry.errors == {}


class TestExampleWorkflow:
    """The example shipped in workflows/ works as a user would expect."""

    @pytest.mark.parametrize("backend", ["langgraph", "python"])
    def test_ticket_triage_runs(self, backend: str) -> None:
        registry = WorkflowRegistry(folder=REPO_WORKFLOWS).load()
        definition = registry.get("tri_tickets_support")
        assert definition is not None
        state = (
            get_backend(backend)
            .compile(plan(definition()), executor=definition.make_executor())
            .invoke({"inputs": definition.default_inputs})
        )
        assert set(state["node_statuses"].values()) == {"completed"}
        assert state["node_outputs"]["escalader"]["escalades"] == ["T-102"]


class TestCommandLine:
    def _run(self, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
        code = main(list(argv))
        return code, capsys.readouterr().out

    def test_list(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        folder = str(_folder(tmp_path, rapport=GOOD))
        code, out = self._run(capsys, "list", "--workflows", folder)
        assert code == 0
        assert "mon_rapport" in out and "analyse_ventes_alertes" in out

    def test_list_reports_broken_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        folder = str(_folder(tmp_path, casse=BROKEN))
        code, out = self._run(capsys, "list", "--workflows", folder)
        assert code == 1
        assert "Fichier en erreur" in out

    def test_validate(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        folder = str(_folder(tmp_path, rapport=GOOD))
        code, out = self._run(capsys, "validate", "mon_rapport", "--workflows", folder)
        assert code == 0
        assert "déclaration valide" in out
        assert "✓ langgraph" in out and "✓ python" in out

    def test_validate_shows_refusing_backends(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, out = self._run(
            capsys, "validate", "validation_budget_hitl", "--workflows", str(tmp_path)
        )
        assert code == 0  # langgraph accepts it
        assert "✗ python" in out and "human approval" in out

    def test_show_prints_runnable_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        folder = str(_folder(tmp_path, rapport=GOOD))
        code, out = self._run(capsys, "show", "mon_rapport", "--workflows", folder)
        assert code == 0
        namespace: dict[str, Any] = {}
        exec(out, namespace)  # noqa: S102 — code generated by CEG
        assert namespace["task"].objective == "Rédiger un rapport"

    def test_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        folder = str(_folder(tmp_path, rapport=GOOD))
        code, out = self._run(
            capsys, "run", "mon_rapport", "--workflows", folder, "--backend", "python"
        )
        assert code == 0
        assert "chercher" in out and "ecrire" in out and "Total" in out

    def test_run_answers_approvals(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = self._run(
            capsys,
            "run",
            "validation_budget_hitl",
            "--workflows",
            str(tmp_path),
            "--approve",
            "no",
        )
        assert code == 0
        assert "refusé" in out
        assert "validation_manager  skipped" in out

    def test_run_needs_a_csv_for_csv_workflows(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = self._run(
            capsys, "run", "analyse_ventes_alertes", "--workflows", str(tmp_path)
        )
        assert code == 1
        assert "csv_path" in out

    def test_run_with_inputs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        csv = Path(__file__).parent / "fixtures" / "scenario_d_corrupted.csv"
        code, out = self._run(
            capsys,
            "run",
            "analyse_ventes_alertes",
            "--workflows",
            str(tmp_path),
            "--input",
            f"csv_path={csv}",
        )
        assert code == 1  # corrupted data: the run aborts, and says why
        assert "Abandon sur « fetch_data »" in out

    def test_unknown_workflow(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out = self._run(capsys, "run", "fantome", "--workflows", str(tmp_path))
        assert code == 1
        assert "Workflow inconnu" in out


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def workflow_file() -> Iterator[Path]:
    """A workflow file dropped into the platform's folder, removed afterwards."""
    path = WORKFLOWS_DIR / "rapport_utilisateur.py"
    path.write_text(GOOD, encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def broken_file() -> Iterator[Path]:
    path = WORKFLOWS_DIR / "casse.py"
    path.write_text(BROKEN, encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


class TestPlatform:
    def test_a_dropped_file_appears_without_restart(
        self, client: TestClient, workflow_file: Path
    ) -> None:
        workflows = {w["id"]: w for w in client.get("/workflows").json()}
        assert "mon_rapport" in workflows
        assert workflows["mon_rapport"]["source_file"].endswith(
            "rapport_utilisateur.py"
        )

    def test_it_runs_with_its_default_inputs(
        self, client: TestClient, workflow_file: Path
    ) -> None:
        client.get("/workflows")
        res = client.post("/tasks/mon_rapport/execute", json={"backend": "python"})
        assert res.status_code == 200, res.text
        state = res.json()["workflow_state"]
        assert state["inputs"] == {"sujet": "ventes"}
        assert res.json()["status"] == "completed"

    def test_its_code_shows_the_user_file(
        self, client: TestClient, workflow_file: Path
    ) -> None:
        client.get("/workflows")
        detail = client.get("/workflows/mon_rapport").json()
        declaration = next(s for s in detail["sources"] if s["title"] == "Déclaration")
        assert "def mon_rapport" in declaration["code"]

    def test_broken_files_are_listed(
        self, client: TestClient, broken_file: Path
    ) -> None:
        client.post("/workflows/refresh")
        errors = client.get("/workflows/errors").json()
        assert any(e["file"].endswith("casse.py") for e in errors)

    def test_refresh(self, client: TestClient, workflow_file: Path) -> None:
        result = client.post("/workflows/refresh").json()
        assert result["workflows"] >= 6
