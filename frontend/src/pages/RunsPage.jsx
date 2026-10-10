import React, { useMemo, useState } from 'react';
import { ListChecks, RefreshCw, Search } from 'lucide-react';
import StatusPill from '../components/StatusPill';
import { formatCost, formatDate, formatMs } from '../api';
import { href, navigate } from '../router';
import { STATUS_LABELS } from '../status';

// Every execution of every workflow, with filters (like Airflow's DAG runs).

const STATUS_FILTERS = ['completed', 'failed', 'awaiting_approval', 'running'];

export default function RunsPage({ executions, onRefresh }) {
  const [status, setStatus] = useState('all');
  const [workflow, setWorkflow] = useState('all');
  const [backend, setBackend] = useState('all');
  const [query, setQuery] = useState('');

  const workflows = useMemo(
    () => [...new Set(executions.map((e) => e.task_id).filter(Boolean))].sort(),
    [executions]
  );
  const backends = useMemo(
    () => [...new Set(executions.map((e) => e.backend).filter(Boolean))].sort(),
    [executions]
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return executions.filter(
      (e) =>
        (status === 'all' || e.status === status) &&
        (workflow === 'all' || e.task_id === workflow) &&
        (backend === 'all' || e.backend === backend) &&
        (!q || [e.id, e.scenario_name, e.task_id].some((v) => v && v.toLowerCase().includes(q)))
    );
  }, [executions, status, workflow, backend, query]);

  const stats = useMemo(() => {
    const finished = executions.filter((e) => e.status === 'completed' || e.status === 'failed');
    const ok = finished.filter((e) => e.status === 'completed').length;
    return {
      total: executions.length,
      successRate: finished.length ? Math.round((ok / finished.length) * 100) : null,
      waiting: executions.filter((e) => e.status === 'awaiting_approval').length,
      cost: executions.reduce((sum, e) => sum + (e.total_cost || 0), 0),
      latency: executions.length
        ? executions.reduce((sum, e) => sum + (e.total_latency_ms || 0), 0) / executions.length
        : 0,
    };
  }, [executions]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Exécutions</h1>
          <p className="page-subtitle">Toutes les exécutions, de tous les workflows.</p>
        </div>
        <div className="toolbar">
          <button className="btn" onClick={onRefresh}>
            <RefreshCw size={14} /> Actualiser
          </button>
        </div>
      </div>

      <div className="stats">
        <div className="stat">
          <div className="stat-label">Exécutions</div>
          <div className="stat-value">{stats.total}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Taux de succès</div>
          <div className="stat-value">{stats.successRate == null ? '—' : `${stats.successRate} %`}</div>
        </div>
        <div className="stat">
          <div className="stat-label">À approuver</div>
          <div className="stat-value" style={{ color: stats.waiting ? 'var(--accent-violet)' : undefined }}>
            {stats.waiting}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Coût cumulé</div>
          <div className="stat-value">{formatCost(stats.cost)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Latence moyenne</div>
          <div className="stat-value">{formatMs(stats.latency)}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header" style={{ flexWrap: 'wrap', gap: 10 }}>
          <ListChecks size={14} color="var(--accent-primary)" />
          <span>{visible.length} exécution{visible.length > 1 ? 's' : ''}</span>
          <span style={{ flex: 1 }} />
          <div className="toolbar">
            <button
              className={`btn btn-sm ${status === 'all' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setStatus('all')}
            >
              Toutes
            </button>
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                className={`btn btn-sm ${status === s ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setStatus(s)}
              >
                {STATUS_LABELS[s]}
              </button>
            ))}
          </div>
          <select className="input" style={{ minWidth: 0 }} value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
            <option value="all">Tous les workflows</option>
            {workflows.map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
          <select className="input" style={{ minWidth: 0 }} value={backend} onChange={(e) => setBackend(e.target.value)}>
            <option value="all">Tous les backends</option>
            {backends.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
          <div style={{ position: 'relative' }}>
            <Search size={13} style={{ position: 'absolute', left: 9, top: 9, color: 'var(--text-muted)' }} />
            <input
              className="input"
              style={{ paddingLeft: 28 }}
              placeholder="Rechercher…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>

        {visible.length === 0 ? (
          <div className="empty">Aucune exécution ne correspond à ces filtres.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Exécution</th>
                  <th>Workflow</th>
                  <th>Statut</th>
                  <th>Moteur</th>
                  <th>Début</th>
                  <th>Coût</th>
                  <th>Latence</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => navigate(`/runs/${e.id}`)}>
                    <td>
                      <div className="mono">{e.id}</div>
                      <div className="small muted">{e.scenario_name}</div>
                    </td>
                    <td>
                      {e.task_id ? (
                        <a href={href(`/workflows/${e.task_id}/grid`)} onClick={(ev) => ev.stopPropagation()}>
                          {e.task_id}
                        </a>
                      ) : '—'}
                    </td>
                    <td><StatusPill status={e.status} /></td>
                    <td className="mono small">
                      <div>{e.backend || '—'}</div>
                      <div className="muted">{e.optimizer || ''}</div>
                    </td>
                    <td className="small muted nowrap">{formatDate(e.started_at)}</td>
                    <td className="mono small">{formatCost(e.total_cost)}</td>
                    <td className="mono small">{formatMs(e.total_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
