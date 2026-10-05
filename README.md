# CEG — Cognitive Execution Graph Studio

**CEG (Cognitive Execution Graph)** est une plateforme complète et un framework d'orchestration de tâches cognitives LLM avec compilation vers **LangGraph**, moteur de décision dynamique, évaluation composite (LLM-as-judge + métriques auto) et interface de visualisation interactive Studio.

---

## 🗂️ Structure du Projet (Monorepo Frontend / Backend)

```text
CEG/
├── backend/                  # API REST FastAPI & Moteur CEG Core
│   ├── api/                  # Routes REST, Modèles SQLAlchemy, Schémas Pydantic
│   ├── src/ceg/              # Framework CEG (Compilateur, Runtime, Evaluation)
│   ├── tests/                # Suite de 229 tests automatisés (pytest)
│   ├── ceg.db                # Base de données SQLite persistante
│   └── pyproject.toml        # Configuration Python, dépendances, linters
│
├── frontend/                 # Application Web React 18 + Vite (Dev-Tool Studio)
│   ├── src/                  # Composants Studio (FlowGraph React Flow, NodeInspector, etc.)
│   ├── package.json          # Dépendances (@xyflow/react, recharts, lucide-react)
│   └── vite.config.js        # Configuration Vite
│
└── README.md                 # Documentation globale
```

---

## 🚀 Démarrage Rapide

### 1. Démarrer le Backend (API REST FastAPI)

```bash
# Activer l'environnement virtuel
source .venv/bin/activate

# Installer le backend en mode éditable
pip install -e "backend[dev]"

# Lancer le serveur d'API REST sur le port 8000
cd backend
python -m uvicorn api.main:app --reload --port 8000
```
- Swagger UI : [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check : [http://localhost:8000/health](http://localhost:8000/health)

### 2. Démarrer le Frontend (CEG Studio UI)

```bash
cd frontend

# Installer les dépendances
npm install

# Démarrer le serveur de développement Vite
npm run dev
```
- Interface Studio : [http://localhost:5173](http://localhost:5173)

---

## 🧪 Tests Backend

```bash
# Exécuter les 229 tests
pytest backend/tests -v
```

---

## ⚡ Runtime Decision Engine (Semaine 3)

Le **Runtime Decision Engine** intervient dynamiquement à chaque nœud du graphe pendant son exécution pour :

1. **Sélectionner le modèle optimal** (`select_model`) à l'aide de l'algorithme de scoring :
   $$\text{Score} = w_1 \cdot \text{Quality} + w_2 \cdot \frac{1}{\text{Cost}} + w_3 \cdot \frac{1}{\text{Latency}}$$
   en filtrant les modèles qui ne satisfont pas les contraintes dures (budget disponible, latence max, capacités requises).

2. **Suivre le budget transverse** (`budget_total`, `budget_used`, `budget_remaining`) avant et après chaque exécution.

3. **Orchestrer les 5 stratégies de fallback** (`FallbackOrchestrator`) en cas d'échec d'exécution :
   - **Retry** : Réessaie le modèle jusqu'à $N$ tentatives.
   - **Escalation** : Passe au tier de modèle supérieur (`fast` $\rightarrow$ `balanced` $\rightarrow$ `quality`).
   - **Degradation** : Réduit la complexité (objectif tronqué, contraintes assouplies).
   - **Skip** : Marque le nœud comme `skipped` sans faire crasher le graphe.
   - **Abort** : Interrompt immédiatement l'exécution globale (`NodeAbortError`).

---

## 🎯 Cas d'usage Fil Rouge — Pipeline de Ventes (Semaine 4)

Le **pipeline d'agrégation des ventes avec alerte de chute de volume** est le cas d'usage officiel de référence du projet CEG.
Il sert de base à tous les tests de bout en bout, benchmarks (S7) et démos.

### Objectif Métier

> Générer un pipeline d'agrégation des ventes par région, avec alerte automatique en cas de chute de volume supérieure à 20 % par rapport au mois précédent.

**Données d'entrée** : fichier CSV de transactions avec colonnes `[date, region, montant, produit, quantite]`  
**Seuil d'alerte** : −20 % de chute de volume  
**Période** : 30 derniers jours vs mois précédent (30 jours)

### Graphe d'exécution

```
fetch_data → aggregate_region → compute_trend → detect_anomaly
                                                      │
                                         [CONDITIONAL: anomalies_found]
                                                      │
                                               generate_alert
                                         (ou SKIPPED si aucune anomalie)
```

### Les 5 sous-tâches (CEGNode)

| Nœud | Capacités requises | Tier |
|---|---|---|
| `fetch_data` | `data_reading`, `validation` | fast |
| `aggregate_region` | `aggregation`, `computation` | fast |
| `compute_trend` | `trend_analysis`, `computation` | balanced |
| `detect_anomaly` | `anomaly_detection`, `reasoning` | quality |
| `generate_alert` | `notification`, `summarization` | balanced |

### Contraintes globales

| Contrainte | Valeur |
|---|---|
| Budget max | 0.50 USD |
| Latence max | 15 secondes |
| Score qualité min | 0.85 |
| Outils autorisés | `sql_query`, `send_alert`, `data_aggregator` |

### Arête Conditionnelle (nouveauté S4)

L'arête `detect_anomaly → generate_alert` est de type **CONDITIONAL** avec la clé `anomalies_found`.
- Si `detect_anomaly` retourne `{"anomalies_found": True, ...}` → `generate_alert` s'exécute.
- Sinon → `generate_alert` est marqué `status="skipped"` et le graphe se termine proprement.

### 4 Scénarios de Test Officiels

| Scénario | Description | Résultat attendu |
|---|---|---|
| **A — Normal** | Aucune anomalie | `generate_alert` skipped, latence baseline |
| **B — Anomalie simple** | Nord : −26 % | Alerte générée avec Nord et son pourcentage |
| **C — Anomalies multiples** | Nord −30 %, Sud −25 %, Ouest −28 % | Alerte avec 3 régions |
| **D — Données corrompues** | CSV malformé | `NodeAbortError` sur `fetch_data` |

### Utilisation

```python
from ceg.use_cases.sales_pipeline import build_sales_graph, SalesExecutor
from ceg.compiler.compiler import CEGCompiler

graph = build_sales_graph()
executor = SalesExecutor(csv_path="data/transactions.csv")
compiler = CEGCompiler(executor=executor)
workflow = compiler.compile(graph)
result = workflow.invoke()
```

Ou en ligne de commande :

```bash
python -m ceg.use_cases.sales_pipeline
```

---

## 📊 Evaluation Engine (Semaine 5)

L'**Evaluation Engine** mesure systématiquement chaque exécution sur 4 dimensions et calcule un score composite. C'est le socle du protocole de validation expérimentale (comparaison CEG vs baseline en S7).

### Les 4 dimensions (Table 7.4)

| Dimension | Type | Source |
|---|---|---|
| **Coût** | `float` USD | `CEGState.total_cost` agrégé par le Runtime Decision Engine |
| **Latence** | `float` secondes | `CEGState.total_latency_ms / 1000` |
| **Qualité** | `float [0-1]` | Score LLM-as-judge selon des `Criterion` prédéfinis |
| **Robustesse** | `float [0-1]` | Taux de succès sur N runs (`n_success / n_runs`) |

### Score Composite (section 7.5.2)

$$\text{ScoreComposite} = w_c \cdot \left(1 - \frac{\text{cost}}{\text{budget}}\right) + w_l \cdot \left(1 - \frac{\text{latency}}{\text{max\_latency}}\right) + w_q \cdot \text{quality} + w_r \cdot \text{robustness}$$

Où $w_c + w_l + w_q + w_r = 1.0$ (défaut : 0.25 chacun). Chaque terme est clippé dans $[0, 1]$ pour éviter les scores négatifs en cas de dépassement de budget ou de latence.

### LLM-as-judge (Architecture)

```
JudgeClient (Protocol)          ← interface abstraite
    └── MockJudgeClient         ← scores déterministes (tests, pas d'API)
    └── OpenAIJudgeClient       ← (futur) branchement sans modifier l'engine
```

- **`Criterion`** : dimension d'évaluation avec `name`, `description`, `weight`, `evaluation_prompt_template`
- **`JudgeVerdict`** : résultat structuré (un `CriterionScore` par critère + score agrégé)
- **`MockJudgeClient`** : scores fixes, hash-mode déterministe ou `fixed_scores` par critère

### Mesure de robustesse

```python
engine.measure_robustness(
    build_fn=build_sales_graph,
    executor_factory=lambda: SalesExecutor(csv_path="..."),
    n_runs=20,          # N=20 en production, réductible en tests
) -> RobustnessReport
```

### Critères du cas d'usage fil rouge

| Critère | Nœud cible | Poids |
|---|---|---|
| `anomaly_precision` | `detect_anomaly` | 0.35 |
| `anomaly_completeness` | `detect_anomaly` | 0.35 |
| `threshold_accuracy` | `detect_anomaly` | 0.30 |
| `alert_relevance` | `generate_alert` | 0.40 |
| `alert_clarity` | `generate_alert` | 0.35 |
| `alert_accuracy` | `generate_alert` | 0.25 |

### Utilisation

```python
from ceg.evaluation import EvaluationEngine, MockJudgeClient
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA
from ceg.use_cases.sales_pipeline import build_sales_graph, SalesExecutor
from ceg.compiler.compiler import CEGCompiler

# 1. Exécuter le pipeline
graph = build_sales_graph()
executor = SalesExecutor(csv_path="data/transactions.csv")
state = CEGCompiler(executor=executor).compile(graph).invoke()

# 2. Mesurer la robustesse (N runs)
engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
rob = engine.measure_robustness(
    build_fn=build_sales_graph,
    executor_factory=lambda: SalesExecutor(csv_path="data/transactions.csv"),
    n_runs=20,
)

# 3. Rapport complet
report = engine.evaluate(
    state,
    scenario_name="production",
    max_budget_usd=0.50,
    max_latency_seconds=15.0,
    robustness_score=rob.success_rate,
)
print(f"Score composite : {report.composite_score:.3f}")
print(f"Qualité         : {report.quality_score:.3f}")
print(f"Robustesse      : {report.robustness_score:.3f}")
```

---

## 🌐 API REST FastAPI & Interface Web (Semaine 6)

CEG expose l'ensemble de ses fonctionnalités via une **API REST FastAPI 0.110+** documentée avec persistance SQLite et une **interface web de visualisation React 18 + Mermaid.js**.

### Endpoints REST (Table 7.5)

- **Swagger Documentation** : [`http://localhost:8000/docs`](http://localhost:8000/docs)
- **OpenAPI Schema** : [`http://localhost:8000/openapi.json`](http://localhost:8000/openapi.json)

| Méthode | Endpoint | Description |
|---|---|---|
| `POST` | `/tasks` | Créer une Cognitive Task |
| `GET` | `/tasks` | Lister les Cognitive Tasks |
| `GET` | `/tasks/{id}` | Récupérer une Cognitive Task |
| `PUT` | `/tasks/{id}` | Mettre à jour une Cognitive Task |
| `DELETE` | `/tasks/{id}` | Supprimer une Cognitive Task |
| `POST` | `/tasks/{id}/execute` | Exécuter une Cognitive Task |
| `GET` | `/executions` | Lister les exécutions |
| `GET` | `/executions/{id}` | Détail d'une exécution |
| `GET` | `/executions/{id}/trace` | Trace complète d'exécution nœud par nœud |
| `GET` | `/executions/{id}/metrics` | Métriques d'évaluation |
| `POST` | `/benchmark` | Lancer un benchmark (Stub 202 Accepted) |
| `GET` | `/benchmark/{id}/results` | Résultats du benchmark (Stub) |
| `GET` | `/models` | Modèles LLM disponibles |
| `GET` | `/health` | État du système et statut SQLite |

### 🚀 Lancement Rapide (Local)

#### 1. Démarrer l'API REST FastAPI

```bash
# Lancer le serveur FastAPI (ensemencement automatique SQLite au 1er démarrage)
uvicorn api.main:app --reload --port 8000
```

Accédez à la documentation Swagger interactive sur [http://localhost:8000/docs](http://localhost:8000/docs).

#### 2. Démarrer l'Interface Web React

```bash
cd frontend
npm install
npm run dev
```

Ouvrez [http://localhost:5173](http://localhost:5173) pour explorer l'interface de visualisation web (Tableau de bord, DAG Mermaid.js avec coloration par statut, traces détaillées, comparaison et graphiques de tendance).

---

## 📦 Structure du Code (`src layout`)

```
CEG/
├── api/                  ← Backend API REST FastAPI & SQLite (Semaine 6)
│   ├── main.py           ← App FastAPI + OpenAPI + CORS
│   ├── db.py             ← SQLAlchemy SQLite Engine & Modèles
│   ├── schemas.py        ← Modèles Pydantic Request/Response
│   ├── seed.py           ← Script d'ensemencement automatique
│   └── routers/          ← Endpoints (tasks, executions, benchmark, models, health)
├── frontend/             ← Application Web React 18 + Vite + Mermaid.js
│   ├── src/
│   │   ├── components/   ← MermaidGraph, ExecutionList, ExecutionDetail, Compare, Trends
│   │   ├── App.jsx
│   │   └── index.css
├── src/
│   └── ceg/
│       ├── models/
│       ├── compiler/
│       ├── runtime/
│       ├── evaluation/
│       └── use_cases/
├── tests/
│   ├── test_api.py       ← Tests FastAPI TestClient / httpx (Semaine 6)
│   └── ...
├── pyproject.toml
└── README.md
```

---

## 📄 Licence

Ce projet est sous licence [MIT](LICENSE).

