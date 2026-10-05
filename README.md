# CEG — Cognitive Execution Graph Studio

**CEG (Cognitive Execution Graph)** est une plateforme complète et un framework d'orchestration de tâches cognitives LLM avec compilation vers **LangGraph**, moteur de décision dynamique, évaluation composite (LLM-as-judge + métriques auto) et interface de visualisation interactive Studio.

> ⚠️ **Exécution simulée.** Les nœuds sont exécutés par des exécuteurs Python déterministes (`MockExecutor` et ses sous-classes métier), pas par de vrais appels LLM. Le coût et la latence d'un nœud sont ceux du modèle simulé choisi par le Runtime Decision Engine, et la qualité est notée par `MockJudgeClient`, dont les scores ne dépendent pas du contenu des sorties. Voir [Limites connues](#-limites-connues).

---

## 🗂️ Structure du Projet (Monorepo Frontend / Backend)

```text
CEG/
├── backend/                  # API REST FastAPI & Moteur CEG Core
│   ├── api/                  # Routes REST, service d'exécution, modèles SQLAlchemy, schémas
│   ├── src/ceg/              # Framework CEG (Compilateur, Runtime, Evaluation)
│   ├── tests/                # Suite de 295 tests automatisés (pytest)
│   └── pyproject.toml        # Configuration Python, dépendances, linters
│
├── frontend/                 # Application Web React 19 + Vite (Dev-Tool Studio)
│   ├── src/                  # Composants Studio (FlowGraph React Flow, NodeInspector, etc.)
│   ├── package.json          # Dépendances (@xyflow/react, recharts, lucide-react)
│   └── vite.config.js        # Configuration Vite
│
└── README.md                 # Documentation globale
```

La base SQLite `backend/ceg.db` est créée au premier démarrage de l'API ; elle n'est pas versionnée.

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

Au premier démarrage, l'API crée les tables et ensemence la base avec les tâches de démonstration et une exécution par type de flux. Pour régénérer ces exécutions :

```bash
cd backend
python -m api.seed --reset
```

### 2. Démarrer le Frontend (CEG Studio UI)

```bash
cd frontend

# Installer les dépendances
npm install

# Démarrer le serveur de développement Vite
npm run dev
```
- Interface Studio : [http://localhost:5173](http://localhost:5173)

### Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `CEG_DB_PATH` | `backend/ceg.db` | Fichier SQLite |
| `CEG_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Origines autorisées à appeler l'API depuis un navigateur |
| `CEG_SEED` | `1` | `0` désactive l'ensemencement au démarrage |
| `CEG_DATA_DIR` | — | Dossier supplémentaire où l'API peut lire des CSV (`csv_path`) |
| `VITE_API_URL` | `http://localhost:8000` | URL de l'API pour le frontend (ex. dans `frontend/.env.local`) |

---

## 🧪 Tests & Qualité

```bash
cd backend
pytest                      # 295 tests (base SQLite temporaire, jamais ceg.db)
ruff check . && ruff format --check .
mypy                        # mode strict sur src/, api/ et tests/

cd ../frontend
npm run lint && npm run build
```

La CI GitHub Actions exécute ces mêmes vérifications (backend sur Python 3.10 à 3.12, frontend sur Node 22).

---

## ⚡ Runtime Decision Engine (Semaine 3)

Le **Runtime Decision Engine** intervient dynamiquement à chaque nœud du graphe pendant son exécution pour :

1. **Sélectionner le modèle optimal** (`select_model` / `rank_models`) à l'aide de l'algorithme de scoring :
   $$\text{Score} = w_1 \cdot \text{Quality} + w_2 \cdot \text{norm}\left(\frac{1}{\text{Cost}}\right) + w_3 \cdot \text{norm}\left(\frac{1}{\text{Latency}}\right)$$
   où $\text{norm}$ divise par le maximum parmi les modèles comparés, de sorte que les trois termes sont dans $[0, 1]$ et que les poids $w_1, w_2, w_3$ (somme = 1) fixent réellement le compromis. Les modèles qui ne satisfont pas les contraintes dures (budget disponible, latence max, capacités requises) sont exclus.
   - Le **`model_tier_hint`** du nœud (`fast` / `balanced` / `quality`) restreint le choix à ce tier dès qu'un modèle de ce tier est éligible ; sinon tous les modèles éligibles sont comparés.
   - Le modèle choisi est **transmis à l'exécuteur** : le coût et la latence enregistrés sont ceux de ce modèle.
   - La décision (modèle, score, candidats, budget restant) est enregistrée dans `execution_log[*].decision`.

2. **Suivre le budget transverse** (`budget_total`, `budget_used`, `budget_remaining`). Le budget est remis à zéro à chaque `invoke()` (une exécution = un budget) et conservé lors d'un `resume()`. Il n'est jamais dépassé : quand plus aucun modèle n'est finançable, seule une exécution dégradée qui tient dans le reste du budget est tentée, sinon le nœud est abandonné.

3. **Orchestrer les 5 stratégies de fallback** (`FallbackOrchestrator`) en cas d'échec d'exécution. Les stratégies réellement tentées sont enregistrées dans `execution_log[*].fallbacks_triggered` (ou dans `NodeAbortError.fallbacks_triggered`) :
   - **Retry** : Réessaie le même modèle jusqu'à $N$ tentatives.
   - **Escalation** : Exécute avec le meilleur modèle d'un tier supérieur (`fast` $\rightarrow$ `balanced` $\rightarrow$ `quality`).
   - **Degradation** : Version simplifiée et moins chère du nœud (simulation : objectif tronqué, coût et latence du modèle réduits de moitié).
   - **Skip** : Marque le nœud comme `skipped` sans faire crasher le graphe.
   - **Abort** : Interrompt immédiatement l'exécution globale (`NodeAbortError`).

---

## 🧭 Compilateur, entrées et Human-in-the-Loop

- **Entrées du graphe** : elles sont passées sous la clé `inputs` et transmises à chaque exécuteur, fusionnées avec les sorties des nœuds amont : `workflow.invoke({"inputs": {"csv_path": "data.csv"}})`. Un sous-graphe reçoit les entrées et les sorties amont de son parent.
- **Human-in-the-Loop** : un graphe qui contient des nœuds `interrupt_before` / `interrupt_after` (y compris dans un sous-graphe) **refuse de compiler sans checkpointer**, pour qu'une étape d'approbation ne soit jamais contournée silencieusement. Pour une exécution non surveillée (benchmark), il faut le demander explicitement avec `compile(graph, ignore_interrupts=True)`.

```python
from langgraph.checkpoint.memory import MemorySaver

workflow = CEGCompiler().compile(graph, checkpointer=MemorySaver())
state = workflow.invoke(thread_id="run-1")      # s'arrête avant le nœud à approuver
state = workflow.resume("run-1", value=True)    # False ou {"approved": False} pour rejeter
```

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

| Nœud | Capacités requises | Tier | Modèle choisi (registre par défaut) |
|---|---|---|---|
| `fetch_data` | `data_reading`, `validation` | fast | `fast-mini` |
| `aggregate_region` | `aggregation`, `computation` | fast | `fast-mini` |
| `compute_trend` | `trend_analysis`, `computation` | balanced | `balanced-standard` |
| `detect_anomaly` | `anomaly_detection`, `reasoning` | quality | `quality-pro` |
| `generate_alert` | `notification`, `summarization` | balanced | `balanced-standard` |

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
workflow = CEGCompiler(executor=SalesExecutor()).compile(graph)
result = workflow.invoke({"inputs": {"csv_path": "data/transactions.csv"}})
```

Ou en ligne de commande :

```bash
python -m ceg.use_cases.sales_pipeline
```

---

## 📊 Evaluation Engine (Semaine 5)

L'**Evaluation Engine** mesure chaque exécution sur 4 dimensions et calcule un score composite. C'est le socle du protocole de validation expérimentale (comparaison CEG vs baseline en S7).

### Les 4 dimensions (Table 7.4)

| Dimension | Type | Source |
|---|---|---|
| **Coût** | `float` USD | `CEGState.total_cost` (prix des modèles choisis) |
| **Latence** | `float` secondes | `CEGState.total_latency_ms / 1000` |
| **Qualité** | `float [0-1]` ou `None` | Score LLM-as-judge selon des `Criterion` ; `None` si aucun nœud n'a été jugé |
| **Robustesse** | `float [0-1]` ou `None` | Taux de succès sur N runs (`n_success / n_runs`) ; `None` si non mesurée |

### Score Composite (section 7.5.2)

$$\text{ScoreComposite} = w_c \cdot \left(1 - \frac{\text{cost}}{\text{budget}}\right) + w_l \cdot \left(1 - \frac{\text{latency}}{\text{max\_latency}}\right) + w_q \cdot \text{quality} + w_r \cdot \text{robustness}$$

Où $w_c + w_l + w_q + w_r = 1.0$ (défaut : 0.25 chacun). Chaque terme est clippé dans $[0, 1]$ pour éviter les scores négatifs en cas de dépassement de budget ou de latence. Une dimension **non mesurée** (`None`) n'est pas remplacée par une valeur arbitraire : elle est exclue et les poids restants sont renormalisés pour sommer à 1. La liste des dimensions exclues est dans `report.metadata["unmeasured"]`, et le juge utilisé dans `report.metadata["judge"]`.

### LLM-as-judge (Architecture)

```
JudgeClient (Protocol)          ← interface abstraite
    └── MockJudgeClient         ← scores déterministes (tests, pas d'API)
    └── OpenAIJudgeClient       ← (futur) branchement sans modifier l'engine
```

- **`Criterion`** : dimension d'évaluation avec `name`, `description`, `weight`, `evaluation_prompt_template` et `target_node_ids` (nœuds évalués ; vide = tous les nœuds terminés)
- **`JudgeVerdict`** : résultat structuré (un `CriterionScore` par critère + score agrégé)
- **`MockJudgeClient`** : scores fixes, hash-mode déterministe ou `fixed_scores` par critère. **Ses scores ne dépendent pas de la sortie évaluée** : il sert à faire tourner la chaîne d'évaluation, pas à mesurer la qualité.

### Mesure de robustesse

```python
engine.measure_robustness(
    build_fn=build_sales_graph,
    executor_factory=SalesExecutor,
    inputs={"csv_path": "..."},
    n_runs=20,          # N=20 en production, réductible en tests
) -> RobustnessReport
```

Avec des exécuteurs déterministes, les N runs donnent tous le même résultat (taux de 0 ou 1). La mesure devient informative avec des exécutions dont les échecs varient (vrais appels LLM, pannes injectées).

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

inputs = {"csv_path": "data/transactions.csv"}

# 1. Exécuter le pipeline
graph = build_sales_graph()
state = CEGCompiler(executor=SalesExecutor()).compile(graph).invoke({"inputs": inputs})

# 2. Mesurer la robustesse (N runs)
engine = EvaluationEngine(judge=MockJudgeClient(), criteria=ALL_SALES_CRITERIA)
rob = engine.measure_robustness(
    build_fn=build_sales_graph,
    executor_factory=SalesExecutor,
    inputs=inputs,
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
print(f"Qualité         : {report.quality_score}")
print(f"Robustesse      : {report.robustness_score}")
```

---

## 🌐 API REST FastAPI & Interface Web (Semaine 6)

CEG expose l'ensemble de ses fonctionnalités via une **API REST FastAPI 0.110+** documentée avec persistance SQLite et une **interface web de visualisation React 19 + React Flow**.

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
| `GET` | `/executions` | Lister les exécutions (filtres `status`, `task_id`) |
| `GET` | `/executions/{id}` | Détail d'une exécution |
| `POST` | `/executions/{id}/resume` | Approuver / rejeter une exécution en attente (HITL) |
| `GET` | `/executions/{id}/trace` | Trace complète d'exécution nœud par nœud |
| `GET` | `/executions/{id}/metrics` | Métriques d'évaluation |
| `POST` | `/benchmark` | Lancer un benchmark (Stub 202 Accepted) |
| `GET` | `/benchmark/{id}/results` | Résultats du benchmark (Stub) |
| `GET` | `/models` | Modèles (simulés) du registre du Runtime Decision Engine |
| `GET` | `/pipelines` | Modèles de pipeline utilisables par une tâche |
| `GET` | `/health` | État du système et statut SQLite |

### Exécution d'une tâche

- Une tâche nomme son **modèle de pipeline** dans le champ `pipeline` (`GET /pipelines`). Sans pipeline, son graphe est construit à partir de ses `subtasks` (une sous-tâche = un nœud, `dependencies` = arêtes).
- Le budget du moteur et les plafonds du score composite viennent des `task_constraints` de la tâche ; les critères de qualité viennent du pipeline ou des `evaluation_criteria` de la tâche.
- `POST /tasks/{id}/execute` accepte `scenario_name`, `inputs` (entrées du graphe), `csv_path` (pipelines CSV uniquement, limité aux données d'exemple et à `CEG_DATA_DIR`) et `robustness_runs` (0 par défaut : robustesse non mesurée).
- Statuts d'une exécution : `running`, `awaiting_approval` (pause HITL), `completed`, `failed`. En cas d'échec, la trace contient les nœuds réellement exécutés, puis le nœud en échec avec son erreur et les fallbacks tentés.
- Une exécution en `awaiting_approval` se poursuit avec `POST /executions/{id}/resume` (`{"approved": true|false, "comment": "...", "value": {...}}`) ; la reprise d'une exécution qui n'est pas en attente répond 409.

---

## 📦 Structure du Code

```
backend/
├── api/                  ← API REST FastAPI & SQLite (Semaine 6)
│   ├── main.py           ← App FastAPI + OpenAPI + CORS
│   ├── db.py             ← Modèles SQLAlchemy 2.0 (+ ajout des colonnes manquantes)
│   ├── schemas.py        ← Modèles Pydantic Request/Response
│   ├── pipelines.py      ← Registre des modèles de pipeline
│   ├── services.py       ← Exécution, reprise HITL, traces et métriques
│   ├── seed.py           ← Ensemencement des données de démonstration
│   └── routers/          ← Endpoints (tasks, executions, benchmark, models, health)
├── src/ceg/
│   ├── models/           ← CognitiveTask, CEGNode, CEGGraph
│   ├── compiler/         ← CEG → LangGraph, état, exécuteurs
│   ├── runtime/          ← Runtime Decision Engine, fallbacks
│   ├── evaluation/       ← Evaluation Engine, LLM-as-judge
│   ├── use_cases/        ← Pipelines de démonstration
│   └── examples/
└── tests/

frontend/
└── src/
    ├── components/       ← FlowGraph, NodeInspector, ExecutionList, ExecutionDetail, Compare, Trends
    ├── App.jsx
    └── index.css
```

---

## 🚧 Limites connues

- **Exécution et jugement simulés** : pas d'appel LLM réel (voir l'encadré en tête). Les scores de qualité du `MockJudgeClient` ne mesurent pas la qualité des sorties ; brancher un vrai `JudgeClient` est nécessaire pour toute conclusion sur la qualité.
- **Pauses HITL en mémoire** : le checkpointer de l'API est un `MemorySaver`. Une exécution en attente d'approbation ne survit pas à un redémarrage de l'API ; sa reprise échoue alors avec un message explicite.
- **Pas d'authentification** sur l'API : à ne pas exposer en dehors d'un poste de développement.
- **Migrations** : `init_db()` ajoute les colonnes manquantes, mais ne gère ni renommage ni suppression ; Alembic sera nécessaire pour des évolutions plus lourdes du schéma.
- **Benchmark** : `/benchmark` est encore un stub.

---

## 📄 Licence

Ce projet est sous licence [MIT](LICENSE).
