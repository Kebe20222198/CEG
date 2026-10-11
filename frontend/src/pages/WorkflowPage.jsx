import React, { useCallback, useEffect, useState } from 'react';
import {
  Play,
  RefreshCw,
  LayoutGrid,
  GitBranch,
  Code,
  FileCode2,
  ListChecks,
  Info,
} from 'lucide-react';
import FlowGraph from '../components/FlowGraph';
import CodeViewer from '../components/CodeViewer';
import RunGrid, { GridLegend } from '../components/RunGrid';
import { apiGet, formatCost, formatDate, formatMs } from '../api';
import { href, navigate } from '../router';
import StatusPill from '../components/StatusPill';

// One workflow, like an Airflow DAG page: Grid, Graph, Code, Source, Runs,
// Details — each tab has its own URL (#/workflows/<id>/<tab>).

const TABS = [
  { id: 'grid', label: 'Grille', icon: LayoutGrid },
  { id: 'graph', label: 'Graphe', icon: GitBranch },
  { id: 'code', label: 'Code', icon: Code },
  { id: 'source', label: 'Source', icon: FileCode2 },
  { id: 'runs', label: 'Exécutions', icon: ListChecks },
  { id: 'details', label: 'Détails', icon: Info },
];

export default function WorkflowPage({ workflowId, tab = 'grid', onRun }) {
  const [detail, setDetail] = useState(null);
  const [grid, setGrid] = useState(null);
  const [error, setError] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const load = useCallback(() => {
    apiGet(`/workflows/${workflowId}`)
      .then((data) => {
        setDetail(data);
        setError(null);
      })
      .catch((err) => setError(err.message));
    apiGet(`/workflows/${workflowId}/grid?limit=40`)
      .then(setGrid)
      .catch(() => setGrid(null));
  }, [workflowId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return <div className="empty" style={{ color: 'var(--status-failed)' }}>Workflow introuvable : {error}</div>;
  }
  if (!detail) return <div className="empty">Chargement…</div>;

  const c = detail.constraints;
  const runsNewestFirst = grid ? [...grid.runs].reverse() : [];

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">{detail.name || detail.id}</h1>
          <p className="page-subtitle">{detail.objective}</p>
          <div className="meta-row">
            <span className="chip">{detail.id}</span>
            {detail.source_file && <span className="chip">{detail.source_file}</span>}
            <span className="chip">{detail.steps} étapes</span>
            {detail.requires_approval && <span className="chip violet">approbation humaine</span>}
            {detail.backends.map((b) => (
              <span key={b} className="chip accent">⚙ {b}</span>
            ))}
            {detail.success_rate != null && (
              <span className="chip">{detail.runs} exécutions · {Math.round(detail.success_rate * 100)} % ok</span>
            )}
          </div>
        </div>
        <div className="toolbar">
          <button className="btn btn-ghost" onClick={load}>
            <RefreshCw size={14} /> Actualiser
          </button>
          <button className="btn btn-primary" disabled={!detail.valid} onClick={() => onRun(detail.id)}>
            <Play size={14} /> Exécuter
          </button>
        </div>
      </div>

      <nav className="tabs">
        {TABS.map(({ id, label, icon: Icon }) => (
          <a key={id} className={`tab ${tab === id ? 'active' : ''}`} href={href(`/workflows/${detail.id}/${id}`)}>
            <Icon size={14} /> {label}
          </a>
        ))}
      </nav>

      {tab === 'grid' && (
        <div className="card">
          <div className="card-header">
            Exécutions × étapes
            <span style={{ flex: 1 }} />
            <GridLegend />
          </div>
          <div className="card-body">
            <RunGrid grid={grid} />
          </div>
        </div>
      )}

      {tab === 'graph' && (
        detail.plan ? (
          <FlowGraph
            graphData={detail.plan}
            nodeStatuses={{}}
            selectedNodeId={selectedNodeId}
            onNodeSelect={setSelectedNodeId}
            title={`Plan // ${detail.id}`}
            height="560px"
          />
        ) : (
          <div className="empty" style={{ color: 'var(--status-failed)' }}>Déclaration invalide : {detail.error}</div>
        )
      )}

      {tab === 'code' && (
        <CodeViewer
          code={detail.python_code}
          title={`${detail.id}.py — déclaration générée depuis ce qui s'exécute`}
        />
      )}

      {tab === 'source' && (
        detail.sources.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {detail.sources.map((src) => (
              <CodeViewer
                key={`${src.path}:${src.start_line}`}
                code={src.code}
                startLine={src.start_line}
                title={`${src.title} — ${src.path}:${src.start_line}`}
              />
            ))}
          </div>
        ) : (
          <div className="empty">
            Ce workflow a été déclaré via l'API : il n'a pas de fichier source. Son code est dans l'onglet « Code ».
          </div>
        )
      )}

      {tab === 'runs' && (
        <div className="card">
          {runsNewestFirst.length === 0 ? (
            <div className="empty">Aucune exécution.</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Exécution</th>
                  <th>Statut</th>
                  <th>Début</th>
                  <th>Backend</th>
                  <th>Optimiseur</th>
                  <th>Coût</th>
                  <th>Latence</th>
                </tr>
              </thead>
              <tbody>
                {runsNewestFirst.map((run) => (
                  <tr key={run.id} className="clickable" onClick={() => navigate(`/runs/${run.id}`)}>
                    <td className="mono">{run.id}</td>
                    <td>
                      <StatusPill status={run.status} />
                    </td>
                    <td className="small muted">{formatDate(run.started_at)}</td>
                    <td className="mono small">{run.backend || '—'}</td>
                    <td className="mono small">{run.optimizer || '—'}</td>
                    <td className="mono small">{formatCost(run.total_cost)}</td>
                    <td className="mono small">{formatMs(run.total_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'details' && detail.estimate && <EstimateCard estimate={detail.estimate} />}

      {tab === 'details' && (
        <div className="stats">
          <div className="stat">
            <div className="stat-label">Budget maximal</div>
            <div className="stat-value">${c.max_cost_usd.toFixed(2)}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Latence maximale</div>
            <div className="stat-value">{c.max_latency_seconds} s</div>
          </div>
          <div className="stat">
            <div className="stat-label">Qualité minimale</div>
            <div className="stat-value">{c.min_quality_score}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Outils autorisés</div>
            <div className="mono small" style={{ marginTop: 8 }}>
              {detail.tools_allowed.length ? detail.tools_allowed.join(', ') : 'aucun'}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Dernière exécution</div>
            <div className="small" style={{ marginTop: 8 }}>
              {detail.last_run ? (
                <a href={href(`/runs/${detail.last_run.id}`)}>{formatDate(detail.last_run.started_at)} — {detail.last_run.status}</a>
              ) : 'jamais'}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Modèle de pipeline</div>
            <div className="mono small" style={{ marginTop: 8 }}>{detail.pipeline || 'déclaration API'}</div>
          </div>
        </div>
      )}
    </>
  );
}

// EXPLAIN: the cheapest run the engine can choose, against the declaration.
function EstimateCard({ estimate: e }) {
  const verdictColor = e.feasible ? 'var(--status-completed)' : 'var(--status-failed)';
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-header">
        Estimation avant exécution (EXPLAIN)
        <span style={{ flex: 1 }} />
        <span className="small" style={{ color: verdictColor, fontWeight: 600 }}>
          {e.feasible ? '✓ faisable' : '✗ refusé avant exécution'}
        </span>
      </div>
      <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="small">
          Minimum : <span className="mono">{formatCost(e.min_cost)}</span> pour un budget de{' '}
          <span className="mono">{formatCost(e.max_cost_usd)}</span>,{' '}
          <span className="mono">{formatMs(e.min_latency_ms)}</span>
          {e.max_latency_ms != null && (
            <> pour une limite de <span className="mono">{formatMs(e.max_latency_ms)}</span></>
          )}
          , qualité visée ≥ <span className="mono">{e.min_quality}</span>.
        </div>
        {e.errors.map((msg) => (
          <div key={msg} className="small" style={{ color: 'var(--status-failed)' }}>✗ {msg}</div>
        ))}
        {e.warnings.map((msg) => (
          <div key={msg} className="small" style={{ color: 'var(--status-skipped)' }}>⚠ {msg}</div>
        ))}
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Étape</th>
                <th>Modèle le moins cher</th>
                <th>Coût</th>
                <th>Latence</th>
                <th>Qualité estimée</th>
              </tr>
            </thead>
            <tbody>
              {e.steps.map((step) => (
                <tr key={step.node_id}>
                  <td className="mono small">
                    {step.node_id}
                    {step.optional && <span className="muted"> (si condition)</span>}
                  </td>
                  <td className="mono small">{step.team ? 'équipe' : step.model || '—'}</td>
                  <td className="mono small">{formatCost(step.cost)}</td>
                  <td className="mono small">{formatMs(step.latency_ms)}</td>
                  <td className="mono small">{step.quality == null ? '—' : step.quality.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
