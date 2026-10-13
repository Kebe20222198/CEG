#!/usr/bin/env bash
# Pipeline des ventes sur modèles réels (Gemini, Groq, Ollama), puis rapport.
#
#   ./scripts/ventes_reelles.sh            # 3 exécutions par scénario
#   RUNS=2 ./scripts/ventes_reelles.sh
#
# Prérequis : clés dans .env, Docker lancé avec Ollama (docker compose up -d
# ollama). Durée : environ 25 minutes (Ollama est le plus lent). Les résultats
# sont enregistrés après chaque exécution dans resultats/ventes_reelles_*.json :
# une interruption ne perd que l'exécution en cours.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
RUNS="${RUNS:-3}"
cd "$ROOT/backend"

run() {  # fournisseur, pause entre deux exécutions (s)
  "$PYTHON" -m ceg.experiments.ventes_reelles --runs "$RUNS" --providers "$1" \
    --pause "$2" --json "$ROOT/resultats/ventes_reelles_$1.json" \
    > "$ROOT/resultats/ventes_reelles_$1.txt" 2>&1
  echo "✓ $1 terminé"
}

echo "▶ Lancement des trois fournisseurs en parallèle (suivi : resultats/ventes_reelles_*.txt)"
run groq 50 &      # Groq gratuit : 8 000 jetons par minute
run gemini 8 &
run ollama 0 &
wait

echo "▶ Tableaux du rapport"
"$PYTHON" "$ROOT/scripts/figures.py"
if command -v latexmk > /dev/null || [ -x /Library/TeX/texbin/latexmk ]; then
  echo "▶ Compilation du rapport"
  (cd "$ROOT/docs/rapport" && PATH="/Library/TeX/texbin:$PATH" \
    latexmk -pdf -interaction=nonstopmode rapport.tex > /dev/null 2>&1) \
    && echo "✓ docs/rapport/rapport.pdf"
fi
cat "$ROOT"/resultats/ventes_reelles_*.txt | grep -A6 "^Référence"
