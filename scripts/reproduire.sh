#!/usr/bin/env bash
# Reproduire tous les résultats du rapport en une commande.
#
#   ./scripts/reproduire.sh              # environnement .venv (Python 3.13)
#   PYTHON=/chemin/python ./scripts/reproduire.sh
#
# Écrit dans resultats/ :
#   environnement.txt        versions (Python, CEG, LangGraph, CrewAI) et commit
#   tests.txt                vérifications qualité et suite de tests
#   optimiseur_appris.txt    expérience « optimiseur statique vs appris »
#   optimiseur_appris.json
#   benchmark.txt            benchmark « CEG vs workflows à modèle fixe »
#   benchmark.json
#
# Les expériences sont déterministes (simulateur à graine fixe) : deux
# exécutions donnent les mêmes fichiers, sur n'importe quelle machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
OUT="$ROOT/resultats"
RUNS="${RUNS:-40}"
SEED="${SEED:-0}"

if [ ! -x "$PYTHON" ]; then
  echo "✗ Python introuvable : $PYTHON"
  echo "  Créez l'environnement : uv venv --python 3.13 .venv && .venv/bin/pip install -e 'backend[dev]'"
  exit 1
fi

mkdir -p "$OUT"
cd "$ROOT/backend"

step() { printf '\n▶ %s\n' "$1"; }

step "Environnement"
{
  echo "date      : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "commit    : $(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet HEAD -- backend workflows || echo ' (modifications locales)')"
  "$PYTHON" - <<'EOF'
import platform
from importlib.metadata import version
print(f"python    : {platform.python_version()} ({platform.machine()})")
for package in ("ceg", "langgraph", "crewai", "pydantic", "fastapi"):
    try:
        print(f"{package:<10}: {version(package)}")
    except Exception:
        print(f"{package:<10}: non installé")
EOF
  echo "runs      : $RUNS, graine : $SEED"
} | tee "$OUT/environnement.txt"

step "Qualité et tests (ruff, mypy, pytest)"
{
  "$PYTHON" -m ruff check . && "$PYTHON" -m ruff format --check .
  "$PYTHON" -m mypy
  "$PYTHON" -m pytest -q -p no:cacheprovider
} 2>&1 | tee "$OUT/tests.txt" | tail -3

step "Expérience : optimiseur statique vs appris"
"$PYTHON" -m ceg.experiments.learned_optimizer --runs "$RUNS" --seed "$SEED" \
  --json "$OUT/optimiseur_appris.json" | tee "$OUT/optimiseur_appris.txt"

step "Benchmark : CEG vs workflows à modèle fixe"
"$PYTHON" -m ceg.experiments.benchmark --runs "$RUNS" --seed "$SEED" \
  --json "$OUT/benchmark.json" | tee "$OUT/benchmark.txt"

printf '\n✓ Résultats dans %s\n' "${OUT#"$ROOT"/}"
