# CEG - Cognitive Execution Graph

[![CI](https://github.com/your-org/CEG/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/CEG/actions/workflows/ci.yml)

**CEG (Cognitive Execution Graph)** est un framework déclaratif en Python permettant d'orchestrer des tâches cognitives basées sur des modèles de langage (LLM) sous la forme d'un graphe orienté acyclique (DAG).

---

## 🏗️ Architecture du Projet

CEG repose sur une architecture à 6 couches :

1. **SDK / Interface déclarative** : Classes Pydantic et décorateurs pour définir des `CognitiveTask`.
2. **CEG Core** : Structure en graphe orienté acyclique (`CEGGraph`) où chaque nœud (`CEGNode`) conserve son objectif, son historique d'exécution, son coût accumulé et son niveau de confiance.
3. **Compilateur CEG → LangGraph** : Traduction du graphe déclaratif vers le runtime LangGraph.
4. **Runtime Decision Engine** : Moteur d'exécution dynamique et de prise de décision.
5. **Evaluation Engine** : Évaluation continue de la qualité et du niveau de confiance des exécutions.
6. **Observabilité & Traçabilité** : Suivi des coûts et métriques d'exécution.

---

## 🚀 Installation & Développement

### Prerequisites

- Python 3.10 ou version ultérieure

### Installation en mode Éditable

```bash
# Cloner le dépôt
git clone https://github.com/your-org/CEG.git
cd CEG

# Créer et activer un environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# Installer le package et ses dépendances de développement
pip install -e ".[dev]"
```

---

## 🧪 Tests & Qualité de Code

```bash
# Lancer les tests unitaires avec couverture de code
pytest --cov=ceg --cov-report=term-missing tests/

# Vérification du style avec Ruff
ruff check .
ruff format --check .

# Vérification du typage statique avec MyPy
mypy src
```

---

## 📦 Structure du Code (`src layout`)

```
CEG/
├── .github/
│   └── workflows/
│       └── ci.yml
├── src/
│   └── ceg/
│       ├── __init__.py
│       ├── py.typed
│       └── models/
│           ├── __init__.py
│           ├── task.py
│           ├── node.py
│           └── graph.py
├── tests/
│   ├── __init__.py
│   ├── test_task.py
│   ├── test_node.py
│   └── test_graph.py
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 📄 Licence

Ce projet est sous licence [MIT](LICENSE).
