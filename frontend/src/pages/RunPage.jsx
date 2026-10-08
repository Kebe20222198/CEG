import React, { useEffect, useState } from 'react';
import { Activity, GanttChart, Braces } from 'lucide-react';
import ExecutionDetail from '../components/ExecutionDetail';
import Timeline from '../components/Timeline';
import CodeViewer from '../components/CodeViewer';
import { API_BASE_URL, apiGet, formatCost, formatDate, formatMs } from '../api';
import { href, navigate } from '../router';

// One execution: graph and trace, timeline, raw state (#/runs/<id>/<tab>).

const TABS = [
  { id: 'trace', label: 'Graphe & trace', icon: Activity },
  { id: 'timeline', label: 'Chronologie', icon: GanttChart },
  { id: 'state', label: 'État (JSON)', icon: Braces },
];

export default function RunPage({ runId, tab = 'trace', onRunNew }) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    apiGet(`/executions/${runId}`)
      .then((data) => {
        setRun(data);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [runId, tab]);

  if (error) return <div className="empty" style={{ color: 'var(--status-failed)' }}>Exécution introuvable : {error}</div>;
  if (!run) return <div className="empty">Chargement…</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title mono" style={{ fontSize: '1.125rem' }}>{run.id}</h1>
          <div className="meta-row">
            <span className={`status-pill ${run.status}`}>
              <span className="status-pill-dot" />
              <span>{run.status}</span>
            </span>
            {run.task_id && (
              <a className="chip accent" href={href(`/workflows/${run.task_id}/grid`)} style={{ textDecoration: 'none' }}>
                {run.task_id}
              </a>
            )}
            <span className="chip">{run.scenario_name}</span>
            {run.backend && <span className="chip">⚙ {run.backend}</span>}
            {run.optimizer && <span className="chip">optimiseur {run.optimizer}</span>}
            <span className="chip">{formatDate(run.started_at)}</span>
            <span className="chip">{formatCost(run.total_cost)} · {formatMs(run.total_latency_ms)}</span>
          </div>
          {run.error && <p className="page-subtitle" style={{ color: 'var(--status-failed)' }}>{run.error}</p>}
        </div>
      </div>

      <nav className="tabs">
        {TABS.map(({ id, label, icon: Icon }) => (
          <a key={id} className={`tab ${tab === id ? 'active' : ''}`} href={href(`/runs/${run.id}/${id}`)}>
            <Icon size={14} /> {label}
          </a>
        ))}
      </nav>

      {tab === 'trace' && (
        <ExecutionDetail
          executionId={run.id}
          onBack={() => navigate('/runs')}
          onRunNew={onRunNew}
          apiBaseUrl={API_BASE_URL}
          embedded
        />
      )}

      {tab === 'timeline' && (
        <div className="card">
          <div className="card-header">Chronologie des étapes</div>
          <div className="card-body">
            <Timeline graph={run.graph} log={run.workflow_state?.execution_log} />
          </div>
        </div>
      )}

      {tab === 'state' && (
        <CodeViewer
          code={JSON.stringify(run.workflow_state, null, 2)}
          language="json"
          title="État final de l'exécution (CEGState)"
        />
      )}
    </>
  );
}
