# CEG — Cognitive Execution Graph Studio

**CEG (Cognitive Execution Graph)** est une plateforme et un framework **agnostiques** — vis-à-vis du framework agentique, de l'infrastructure et du fournisseur de LLM — qui ajoutent une **couche d'abstraction déclarative** au-dessus des workflows d'agents. On décrit l'**objectif métier**, les **contraintes** (budget, latence, qualité, outils autorisés) et le découpage en sous-tâches ; CEG se charge de **traduire** cette déclaration en workflow, de décider **comment l'exécuter** et de **choisir les modèles** adaptés à chaque étape.

C'est, à peu près, ce que fait **SQL** pour les bases de données relationnelles : on écrit *quoi* obtenir, le moteur décide *comment*. Aujourd'hui, CEG exécute le **même plan** avec **LangGraph**, avec **CrewAI** (Flows) et avec un **backend Python** sans framework, qui sert d'implémentation de référence — et donne le même résultat sur les trois ; d'autres moteurs peuvent s'ajouter sans changer les déclarations.

> ⚠️ **Exécution simulée.** Les nœuds sont exécutés par des exécuteurs Python déterministes (`MockExecutor` et ses sous-classes métier), pas par de vrais appels LLM. Le coût et la latence d'un nœud sont ceux du modèle simulé choisi par le Runtime Decision Engine, et la qualité est notée par `MockJudgeClient`, dont les scores ne dépendent pas du contenu des sorties. Voir [Limites connues](#-limites-connues).

---

## 🧭 Utiliser CEG : le parcours

Comme avec Airflow, un workflow est **un fichier Python déposé dans un dossier** ; la plateforme le découvre, le valide, l'exécute et le montre.

**1. Écrire le workflow** dans `workflows/` (exemple complet : [`workflows/tri_tickets_support.py`](workflows/tri_tickets_support.py)) :

```python
from ceg import CognitiveTask, SubTask, TaskConstraint, workflow

@workflow(executor=EtapesTickets, default_inputs={"tickets": [...]})
def tri_tickets_support() -> CognitiveTask:
    """Trier les tickets du support, escalader les urgences, résumer la journée."""
    return CognitiveTask(
        objective="Classer les tickets, escalader les urgences, rédiger une synthèse.",
        task_constraints=TaskConstraint(max_cost_usd=0.10, max_latency_seconds=5),
        tools_allowed=["ticketing_api", "pager"],
        subtasks=[
            SubTask(id="lire_tickets", objective="Lire les tickets", tools=["ticketing_api"]),
            SubTask(id="classer", objective="Classer", dependencies=["lire_tickets"]),
            SubTask(id="detecter_urgences", objective="Repérer les urgences",
                    dependencies=["lire_tickets"], model_tier_hint="quality"),
            SubTask(id="escalader", objective="Prévenir l'astreinte", tools=["pager"],
                    run_if="detecter_urgences.a_des_urgences"),
            SubTask(id="rediger_synthese", objective="Résumer",
                    dependencies=["classer", "detecter_urgences"]),
        ],
    )
```

Le fichier contient la **déclaration** (quoi faire, sous quelles contraintes) et l'**exécuteur** (`EtapesTickets`, une méthode `run` qui réalise chaque étape — c'est là que se branche un LLM).

**2. Le vérifier et l'essayer** dans le terminal (après `pip install -e "backend[dev]"`) :

```bash
ceg list                                 # workflows trouvés, fichiers en erreur
ceg validate tri_tickets_support         # plan, contraintes, backends acceptés — sans exécuter
ceg show tri_tickets_support             # le workflow sous forme de code
ceg run tri_tickets_support              # exécution + trace nœud par nœud
ceg run validation_budget_hitl --approve ask   # les approbations se font dans le terminal
```

**3. Le piloter dans le Studio** : `ceg studio` lance l'API et le Studio. Dans l'onglet **Workflows**, le nouveau fichier apparaît sans redémarrage, avec son graphe, son code, ses sources et le bouton **Run** ; un fichier qui ne se charge pas est signalé en rouge avec son erreur, comme un « Broken DAG ».

---

## 🧩 Architecture : déclarer → planifier → optimiser → exécuter

| SQL | CEG | Où |
|---|---|---|
| Requête déclarative | `CognitiveTask` : objectif, contraintes, sous-tâches | `ceg.models.task` |
| Planificateur | `plan(task)` → graphe d'exécution (`CEGGraph`), indépendant du moteur | `ceg.planner` |
| Optimiseur par coût | Choix du modèle par nœud sous contraintes (Runtime Decision Engine) | `ceg.runtime` |
| Moteur d'exécution | Backend : `langgraph`, `crewai`, `python`, … | `ceg.backends` |
| `EXPLAIN` | Traces, décisions et fallbacks enregistrés, CEG Studio | API + frontend |

```python
from ceg import CognitiveTask, SubTask, TaskConstraint, get_backend, plan

task = CognitiveTask(
    objective="Alerter si les ventes d'une région chutent de plus de 20 %",
    task_constraints=TaskConstraint(max_cost_usd=0.50, max_latency_seconds=15),
    tools_allowed=["send_alert"],
    subtasks=[
        SubTask(id="fetch", objective="Charger les ventes", model_tier_hint="fast"),
        SubTask(id="detect", objective="Détecter les chutes", dependencies=["fetch"],
                required_capabilities=["anomaly_detection"], model_tier_hint="quality"),
        SubTask(id="alert", objective="Envoyer l'alerte", tools=["send_alert"],
                run_if="detect.anomalies_found"),
    ],
)

graph = plan(task)                                   # quoi → plan
workflow = get_backend("langgraph").compile(graph)   # ou get_backend("python")
state = workflow.invoke({"inputs": {...}})           # le moteur choisit les modèles
```

Les nœuds sont exécutés par des **exécuteurs** (protocole `Executor`) : c'est là que se branche un fournisseur de LLM, sans toucher au reste.

---

## 🗂️ Structure du Projet (Monorepo Frontend / Backend)

```text
CEG/
├── workflows/                # Les workflows des utilisateurs (un fichier = un workflow)
├── backend/                  # API REST FastAPI & Moteur CEG Core
│   ├── api/                  # Routes REST, service d'exécution, modèles SQLAlchemy, schémas
│   ├── src/ceg/              # Framework CEG (planificateur, backends, runtime, évaluation)
│   ├── tests/                # Suite de 429 tests automatisés (pytest)
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

Un seul environnement Python pour tout le projet, en **Python 3.13** (version fixée par `.python-version` ; CrewAI ne supporte pas encore 3.14) :

```bash
# Créer l'environnement (une seule fois) — avec uv, ou python3.13 -m venv .venv
uv venv --python 3.13 .venv
source .venv/bin/activate

# Installer le backend en mode éditable : API, CLI, outils de test et les trois backends
pip install -e "backend[dev]"

# Lancer le serveur d'API REST sur le port 8000
cd backend
python -m uvicorn api.main:app --reload --port 8000
```
- Swagger UI : [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check : [http://localhost:8000/health](http://localhost:8000/health)

Ou, en une commande : `ceg studio` (API + Studio).

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
| `CEG_WORKFLOWS_DIR` | `workflows/` (racine du dépôt) | Dossier des fichiers de workflow |
| `CEG_STATS_PATH` | `backend/model_statistics.json` | Statistiques de l'optimiseur appris (conservées entre redémarrages) |
| `VITE_API_URL` | `http://localhost:8000` | URL de l'API pour le frontend (ex. dans `frontend/.env.local`) |

---

## 🧪 Tests & Qualité

```bash
cd backend
pytest                      # 454 tests (base SQLite temporaire, jamais ceg.db)
ruff check . && ruff format --check .
mypy                        # mode strict sur src/, api/ et tests/

cd ../frontend
npm run lint && npm run build
```

La CI GitHub Actions exécute ces mêmes vérifications (backend sur Python 3.10 à 3.13 avec les trois backends, frontend sur Node 22).

---

## ⚡ Runtime Decision Engine (Semaine 3)

Le **Runtime Decision Engine** intervient dynamiquement à chaque nœud du graphe pendant son exécution pour :

1. **Sélectionner le modèle optimal** (`select_model` / `rank_models`) à l'aide de l'algorithme de scoring :
   $$\text{Score} = w_1 \cdot \text{Quality} + w_2 \cdot \text{norm}\left(\frac{1}{\text{Cost}}\right) + w_3 \cdot \text{norm}\left(\frac{1}{\text{Latency}}\right)$$
   où $\text{norm}$ divise par le maximum parmi les modèles comparés, de sorte que les trois termes sont dans $[0, 1]$ et que les poids $w_1, w_2, w_3$ (somme = 1) fixent réellement le compromis. Les modèles qui ne satisfont pas les contraintes dures (budget disponible, latence max, capacités requises) sont exclus.
   - Le **`model_tier_hint`** du nœud (`fast` / `balanced` / `quality`) restreint le choix à ce tier dès qu'un modèle de ce tier est éligible ; sinon tous les modèles éligibles sont comparés.
   - Le modèle choisi est **transmis à l'exécuteur** : le coût et la latence enregistrés sont ceux de ce modèle.
   - La décision (modèle, score, candidats, budget restant) est enregistrée dans `execution_log[*].decision`.

2. **Suivre le budget et la latence transverses** (`budget_total`, `max_total_latency_ms`). Le budget est remis à zéro à chaque `invoke()` (une exécution = un budget) et conservé lors d'un `resume()`. Avant chaque appel, le coût et la latence estimés sont **réservés de façon atomique** : des branches qui s'exécutent en parallèle ne peuvent pas dépenser deux fois le même reste de budget, et un sous-graphe compte ce que son parent a déjà consommé. Ni le budget ni la latence cumulée ne sont jamais dépassés : quand plus aucun modèle ne tient, seule une exécution dégradée qui tient encore est tentée, sinon le nœud est abandonné. Si **aucun modèle ne possède les capacités** demandées, le nœud est refusé — jamais exécuté sur un modèle incapable.

Chaque appel, **réussi ou raté**, est facturé : un appel LLM qui échoue coûte quand même et prend du temps. Le coût et la latence d'un nœud incluent ses appels ratés (`execution_log[*].failed_calls`).

3. **Orchestrer les 5 stratégies de fallback** (`FallbackOrchestrator`) en cas d'échec d'exécution. Les stratégies réellement tentées sont enregistrées dans `execution_log[*].fallbacks_triggered` (ou dans `NodeAbortError.fallbacks_triggered`) :
   - **Retry** : Réessaie le même modèle jusqu'à $N$ tentatives.
   - **Escalation** : Exécute avec le meilleur modèle d'un tier supérieur (`fast` $\rightarrow$ `balanced` $\rightarrow$ `quality`).
   - **Degradation** : Version simplifiée et moins chère du nœud (simulation : objectif tronqué, coût et latence du modèle réduits de moitié).
   - **Skip** : Marque le nœud comme `skipped` sans faire crasher le graphe.
   - **Abort** : Interrompt immédiatement l'exécution globale (`NodeAbortError`).

---

## 📝 Langage déclaratif et planificateur

Une `SubTask` décrit une étape du travail ; le planificateur (`ceg.planner.plan`) en déduit le graphe :

| Champ de `SubTask` | Effet dans le plan |
|---|---|
| `dependencies` | Arêtes ; les sous-tâches qui n'attendent que le même travail sont **parallélisées automatiquement** |
| `run_if="detect.anomalies_found"` | Arête conditionnelle : la sous-tâche est sautée si la clé est fausse |
| `repeat=RepeatSpec(back_to, while_key, max_iterations)` | Boucle bornée (ex. rédiger ↔ critiquer, 3 fois au plus) |
| `requires_approval` / `review_output` | Point Human-in-the-Loop avant / après la sous-tâche |
| `required_capabilities`, `model_tier_hint` | Contraintes et préférences pour le choix du modèle |
| `tools` | Outils utilisés, vérifiés contre `tools_allowed` de la tâche |
| `subtasks` (imbriquées) | Équipe : planifiée comme un sous-graphe |

Une déclaration incohérente (dépendance inconnue, `run_if` mal formé, boucle vers une sous-tâche qui n'est pas en amont, outil non autorisé, tier inconnu) lève `PlanningError` : elle est **refusée avant toute exécution**. Tous les pipelines de démonstration sont écrits ainsi : leur `CognitiveTask` est la seule source de vérité, leur graphe vient du planificateur.

**Entrées** : elles sont passées sous la clé `inputs` et transmises à chaque exécuteur avec les sorties des nœuds amont : `workflow.invoke({"inputs": {"csv_path": "data.csv"}})`. Un sous-graphe reçoit les entrées et les sorties amont de son parent.

---

## 🛡️ Contraintes : garanties ou refusées

| Contrainte déclarée | Comment CEG la tient |
|---|---|
| `max_cost_usd` | Budget du moteur, **jamais dépassé** à l'exécution |
| `max_latency_seconds` | Budget de latence cumulée, **jamais dépassé** à l'exécution |
| `tools_allowed` | **Refus** au plan (et à la compilation d'un graphe écrit à la main) |
| `required_capabilities` | **Refus** à la compilation si aucun modèle disponible ne les offre |
| `min_quality_score` | **Vérifiée après coup** par l'Evaluation Engine : une violation fait échouer l'exécution ; non vérifiable si la qualité n'est pas mesurée |

Un moteur fourni par l'appelant peut être plus strict que la déclaration, jamais plus laxiste.

---

## 🔌 Backends d'exécution

| Backend | Exécution | Human-in-the-Loop |
|---|---|---|
| `langgraph` (défaut) | `StateGraph` LangGraph, branches parallèles par super-steps | ✅ via checkpointer |
| `crewai` (extra) | `Flow` CrewAI généré à partir du plan, branches parallèles concurrentes | ❌ refusé (sauf `ignore_interrupts=True`) |
| `python` | Interpréteur Python sans framework, branches exécutées l'une après l'autre | ❌ refusé (sauf `ignore_interrupts=True`) |

Les backends partagent la logique d'un nœud (sélection du modèle, fallbacks, sous-graphes) et les contrôles de contraintes (`ceg.backends.common`). Les tests (`tests/test_backends.py`) vérifient que **la même tâche donne exactement le même résultat** sur tous : statuts, sorties, coût, latence et modèle choisi pour chaque nœud, sur tous les pipelines de démonstration. Un backend qui ne sait pas faire une chose déclarée (ici, une approbation humaine) **refuse** le plan plutôt que de l'ignorer.

**CrewAI** : le plan est traduit en une sous-classe de `crewai.flow.Flow`, comme l'écrirait un développeur CrewAI — chaque nœud devient une méthode, une dépendance `@listen("noeud")`, une jointure `@listen(and_(...))`, une boucle un `@router` qui relance le corps de boucle ou laisse continuer ; une condition est vérifiée par le nœud qu'elle garde. CrewAI est installé avec `backend[dev]` ; un utilisateur qui n'en a pas besoin peut installer CEG sans lui (le backend `crewai` n'est alors simplement pas proposé, ou s'ajoute avec l'extra `ceg[crewai]`). Télémétrie et traces CrewAI sont désactivées.

```bash
ceg run analyse_ventes_parallele --backend crewai
```

```python
workflow = get_backend("crewai").compile(graph)
workflow.flow_class          # la sous-classe de crewai.flow.Flow générée
state = workflow.invoke()    # même état final qu'avec langgraph et python
```

**Human-in-the-Loop sur LangGraph** : un graphe avec points d'approbation refuse de compiler sans checkpointer, pour qu'une approbation ne soit jamais contournée silencieusement.

```python
from langgraph.checkpoint.memory import MemorySaver

workflow = get_backend("langgraph").compile(graph, checkpointer=MemorySaver())
state = workflow.invoke(thread_id="run-1")      # s'arrête avant le nœud à approuver
state = workflow.resume("run-1", value=True)    # False ou {"approved": False} pour rejeter
```

---

## 🖥️ CEG Studio

L'interface s'organise comme celle d'Airflow : une barre latérale (**Workflows**, **Exécutions**, **À approuver**, **Comparer**, **Tendances**, **Modèles & optimiseur**), un fil d'Ariane, et une URL par vue (`#/workflows/tri_tickets_support/grid`, `#/runs/exec_…/timeline`) que l’on peut partager ou mettre en favori. Thème clair ou sombre (par défaut, celui du système).

| Page | Contenu |
|---|---|
| **Workflows** | Tous les workflows : étapes, contraintes, backends capables de les exécuter, historique, fichiers en erreur, bouton Run |
| **Un workflow** | Onglets **Grille** (exécutions × étapes, une case colorée par statut), **Graphe**, **Code**, **Source**, **Exécutions**, **Détails** |
| **Une exécution** | Onglets **Graphe & trace** (modèle choisi et pourquoi, fallbacks, sorties), **Chronologie** (Gantt reconstruit à partir du plan et des latences : les branches parallèles se chevauchent, le chemin critique apparaît), **État (JSON)** |
| **À approuver** | Toutes les exécutions en pause sur une approbation humaine, avec la question posée et les boutons Approuver / Rejeter |
| **Modèles & optimiseur** | Registre des modèles, backends et leurs capacités, statistiques de l'optimiseur appris |

### Workflows comme code

Pour chaque workflow, les onglets Graphe, Code, Source et Déclaration montrent :

| Vue | Contenu |
|---|---|
| **Graphe** | Le plan produit par le planificateur, sans avoir à l'exécuter |
| **Code** | La déclaration écrite en Python (`ceg.codegen.task_to_python`), générée depuis ce qui s'exécute réellement ; elle existe donc aussi pour les workflows créés via l'API. Exécuter ce code reconstruit exactement la même déclaration |
| **Source** | Les fichiers Python du modèle de pipeline : la fonction de déclaration et la classe de l'exécuteur, avec chemin et numéros de ligne |
| **Déclaration (JSON)** | La déclaration stockée |

Chaque ligne a un bouton **Run** qui ouvre l'exécution avec ce workflow présélectionné.

---

## 🧠 Optimiseur appris

Les notes de qualité, coûts et latences du registre de modèles sont des **suppositions**. L'optimiseur appris (`ModelStatistics`, `ceg/runtime/statistics.py`) les remplace par ce que les exécutions ont réellement montré, comme un SGBD s'appuie sur les statistiques de ses tables :

- **qualité** observée par (modèle, capacité), un appel raté comptant pour 0 ;
- **taux de succès** : un modèle qui échoue souvent coûte et prend plus de temps qu'annoncé ; le coût et la latence attendus sont ceux d'un résultat *réussi* (`coût / taux de succès`) ;
- **moyenne bayésienne** entre la valeur statique (a priori, qui vaut `prior_weight` observations) et les observations : un modèle jamais utilisé garde sa note statique, les preuves prennent le relais progressivement ;
- **exploration déterministe** (bonus de type UCB) : un modèle peu observé reçoit un bonus qui décroît avec les observations, sans tirage aléatoire — une décision reste reproductible et explicable.

Le moteur apprend de **chaque appel** (succès, échecs, retries, escalades ; pas des exécutions dégradées), et la trace indique la source de la décision (`decision.quality_source` : `static` ou `learned`, avec `expected_quality` et `observations`).

```python
from ceg import RuntimeDecisionEngine, get_backend, plan
from ceg.runtime.statistics import ModelStatistics

stats = ModelStatistics(exploration=0.1)          # partagé entre les exécutions
engine = RuntimeDecisionEngine(statistics=stats)
get_backend("langgraph").compile(plan(task), engine=engine).invoke()
stats.to_dict()                                   # à sauvegarder (from_dict pour recharger)
```

### Expérience : optimiseur statique vs appris

```bash
cd backend/src
python -m ceg.experiments.learned_optimizer --runs 40
```

L'expérience exécute 40 fois une tâche d'analyse en 5 étapes, **sans indication de tier** (l'optimiseur décide seul), dans un environnement simulé (`ceg/simulation.py`) où la qualité réelle des modèles diffère des notes statiques. Les mesures viennent de la « vérité » du simulateur, que l'optimiseur ne voit jamais. Résultats (seed 0, identiques sur les backends `langgraph` et `python`) :

| Stratégie | Coût / exécution | Latence | Appels ratés | Qualité livrée |
|---|---|---|---|---|
| Statique | 0,0285 $ | 858 ms | 4,22 | 0,718 |
| Appris | 0,0207 $ (−27 %) | 549 ms (−36 %) | 0,38 (−91 %) | 0,716 |

À chaque exécution, l'optimiseur statique confie la détection d'anomalies à `fast-mini`, qui échoue quatre fois avant que l'escalade ne passe la main à `balanced-standard`. L'optimiseur appris l'apprend en une exécution et choisit directement `balanced-standard` : même qualité, moins cher et plus rapide.

> **Hypothèses de simulation.** Les qualités « réelles » (`SIMULATED_TRUTH`) sont inventées pour que l'a priori soit faux. L'expérience montre que l'optimiseur **corrige un a priori erroné** ; elle ne dit rien de la façon dont de vrais modèles se comparent. Avec un vrai LLM, le signal de qualité (`ExecutionResult.confidence`) devra venir d'un juge ou d'une vérité terrain.

Dans l'API, `POST /tasks/{id}/execute` accepte `optimizer: "learned"` ; les statistiques sont partagées par toutes les exécutions « learned », sauvegardées dans `CEG_STATS_PATH`, et consultables via `GET /optimizer/statistics`.

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

### Les 5 sous-tâches (déclarées dans `analyse_ventes_alertes()`)

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

### Condition `run_if`

`generate_alert` déclare `run_if="detect_anomaly.anomalies_found"` ; le planificateur en fait une arête **CONDITIONAL**.
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
from ceg import get_backend, plan
from ceg.use_cases.sales_pipeline import SalesExecutor, analyse_ventes_alertes

graph = plan(analyse_ventes_alertes())
workflow = get_backend("langgraph").compile(graph, executor=SalesExecutor())
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
from ceg import get_backend, plan
from ceg.evaluation import EvaluationEngine, MockJudgeClient
from ceg.use_cases.sales_criteria import ALL_SALES_CRITERIA
from ceg.use_cases.sales_pipeline import SalesExecutor, analyse_ventes_alertes, build_sales_graph

inputs = {"csv_path": "data/transactions.csv"}
task = analyse_ventes_alertes()

# 1. Exécuter le pipeline
workflow = get_backend("langgraph").compile(plan(task), executor=SalesExecutor())
state = workflow.invoke({"inputs": inputs})

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
    robustness_score=rob.success_rate,
    constraints=task.task_constraints,   # plafonds + vérification des contraintes
)
print(report.constraint_violations, report.constraints_unverified)
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
| `GET` | `/backends` | Backends d'exécution et leurs capacités |
| `GET` | `/optimizer/statistics` | Ce que l'optimiseur appris sait des modèles |
| `GET` | `/workflows` | Liste des workflows (contraintes, backends compatibles, historique) |
| `GET` | `/workflows/{id}` | Code d'un workflow : déclaration Python générée, sources, plan |
| `GET` | `/workflows/{id}/grid` | Statut de chaque étape dans les dernières exécutions (vue Grille) |
| `GET` | `/workflows/errors` | Fichiers de workflow qui n'ont pas pu être chargés |
| `POST` | `/workflows/refresh` | Relire le dossier des workflows |
| `GET` | `/pipelines` | Modèles de pipeline utilisables par une tâche |
| `GET` | `/health` | État du système et statut SQLite |

### Exécution d'une tâche

- Une tâche stockée est une **déclaration** : à chaque exécution elle est planifiée (`plan`), puis compilée sur le backend demandé. Une déclaration invalide est refusée dès `POST`/`PUT /tasks` (422).
- Une tâche peut s'appuyer sur un **modèle de pipeline** (champ `pipeline`, `GET /pipelines`) qui fournit les exécuteurs et les critères de qualité. Sans sous-tâches propres, elle reprend celles du modèle.
- `POST /tasks/{id}/execute` accepte `scenario_name`, `inputs` (entrées du graphe), `backend` (`langgraph` par défaut), `optimizer` (`static` par défaut, ou `learned`), `csv_path` (pipelines CSV uniquement, limité aux données d'exemple et à `CEG_DATA_DIR`) et `robustness_runs` (0 par défaut : robustesse non mesurée). Un backend qui ne peut pas honorer la tâche (ex. approbation humaine sur `python`) est refusé (400) avant toute exécution.
- Statuts d'une exécution : `running`, `awaiting_approval` (pause HITL), `completed`, `failed`. En cas d'échec, la trace contient les nœuds réellement exécutés, puis le nœud en échec avec son erreur et les fallbacks tentés. Une exécution qui se termine sans respecter ses contraintes déclarées (ex. qualité sous `min_quality_score`) est `failed`, avec la violation dans `error`.
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
│   ├── models/           ← CognitiveTask (déclaration), CEGNode, CEGGraph (plan)
│   ├── registry.py       ← @workflow et découverte du dossier workflows/
│   ├── cli.py            ← commande ceg (list, validate, show, run, studio)
│   ├── planner.py        ← CognitiveTask → CEGGraph
│   ├── codegen.py        ← CognitiveTask → code Python lisible (vue Code)
│   ├── backends/         ← interface Backend, backends langgraph et python, logique commune
│   ├── compiler/         ← CEG → LangGraph, état, exécuteurs
│   ├── runtime/          ← Runtime Decision Engine, fallbacks, statistiques apprises
│   ├── simulation.py     ← environnement LLM simulé (qualité réelle ≠ notes statiques)
│   ├── experiments/      ← expérience optimiseur statique vs appris
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
- **Backend `python`** : pas d'approbation humaine, et les branches indépendantes s'exécutent l'une après l'autre (pas de vrai parallélisme). La latence contrôlée est la **somme** des latences des nœuds, pas le temps réel écoulé.
- **Backend `crewai`** : pas d'approbation humaine (le *human feedback* de CrewAI ne correspond pas encore aux approbations CEG), Python 3.10–3.13 (sur Mac Intel, CrewAI ≤ 1.9 : les versions récentes dépendent de `lancedb`, absent de cette plateforme), et une boucle dont une étape attend aussi un nœud extérieur à la boucle est refusée (l'`and_` de CrewAI l'attendrait indéfiniment au second tour). Les nœuds restent des appels CEG simulés : ce ne sont pas (encore) des `Agent`/`Crew` CrewAI.
- **Pauses HITL en mémoire** : le checkpointer de l'API est un `MemorySaver`. Une exécution en attente d'approbation ne survit pas à un redémarrage de l'API ; sa reprise échoue alors avec un message explicite.
- **Pas d'authentification** sur l'API : à ne pas exposer en dehors d'un poste de développement.
- **Migrations** : `init_db()` ajoute les colonnes manquantes, mais ne gère ni renommage ni suppression ; Alembic sera nécessaire pour des évolutions plus lourdes du schéma.
- **Benchmark** : `/benchmark` est encore un stub.

---

## 📄 Licence

Ce projet est sous licence [MIT](LICENSE).
