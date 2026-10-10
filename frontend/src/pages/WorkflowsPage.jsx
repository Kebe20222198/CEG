import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Workflow, Play, RefreshCw, AlertTriangle, Info, Search } from 'lucide-react';
import { apiGet, apiPost, formatDate } from '../api';
import { href, navigate } from '../router';
import StatusPill from '../components/StatusPill';

// Home page: every workflow of the platform, like Airflow's DAG list.

function Constraints({ c }) {
  if (!c) return '—';
  return (
    <>
      <div className="nowrap">≤ ${c.max_cost_usd.toFixed(2)} · ≤ {c.max_latency_seconds} s</div>
      <div className="nowrap">qualité ≥ {c.min_quality_score}</div>
    </>
  );
}

export default function WorkflowsPage({ onRun }) {
  const [workflows, setWorkflows] = useState(null);
  const [importErrors, setImportErrors] = useState([]);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const load = useCallback(() => {
    apiGet('/workflows')
      .then((data) => {
        setWorkflows(data);
        setError(null);
      })
      .catch((err) => setError(err.message));
    apiGet('/workflows/errors')
      .then(setImportErrors)
      .catch(() => setImportErrors([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = () => {
    setRefreshing(true);
    apiPost('/workflows/refresh')
      .catch(() => {})
      .finally(() => {
        load();
        setRefreshing(false);
      });
  };

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!workflows) return [];
    if (!q) return workflows;
    return workflows.filter((w) =>
      [w.id, w.name, w.objective, w.source_file].some((v) => v && v.toLowerCase().includes(q))
    );
  }, [workflows, query]);

  const totals = useMemo(() => {
    const list = workflows || [];
    return {
      count: list.length,
      runs: list.reduce((sum, w) => sum + w.runs, 0),
      approval: list.filter((w) => w.requires_approval).length,
      failing: list.filter((w) => w.last_run?.status === 'failed').length,
    };
  }, [workflows]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Workflows</h1>
          <p className="page-subtitle">
            Chaque fichier du dossier <span className="mono">workflows/</span> est un workflow :
            sa déclaration, ses contraintes et l'implémentation de ses étapes.
          </p>
        </div>
        <div className="toolbar">
          <button className="btn btn-ghost" onClick={() => setShowHelp((v) => !v)}>
            <Info size={14} /> Ajouter un workflow
          </button>
          <button className="btn" onClick={refresh} disabled={refreshing}>
            <RefreshCw size={14} /> {refreshing ? 'Lecture…' : 'Rafraîchir'}
          </button>
        </div>
      </div>

      {showHelp && (
        <div className="card">
          <div className="card-header">
            <Info size={14} /> Ajouter un workflow, comme un DAG Airflow
          </div>
          <div className="card-body small" style={{ lineHeight: 1.7 }}>
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              <li>
                Créer un fichier dans <span className="mono">workflows/</span> : une fonction décorée par{' '}
                <span className="mono">@workflow</span> qui renvoie la déclaration (
                <span className="mono">CognitiveTask</span>), avec l'exécuteur de ses étapes. Exemple :{' '}
                <span className="mono">workflows/tri_tickets_support.py</span>.
              </li>
              <li>
                Le vérifier sans l'exécuter : <span className="mono">ceg validate &lt;workflow&gt;</span>, l'essayer :{' '}
                <span className="mono">ceg run &lt;workflow&gt;</span>.
              </li>
              <li>Il apparaît ici automatiquement (sinon « Rafraîchir ») : grille, graphe, code et bouton Run.</li>
            </ol>
          </div>
        </div>
      )}

      {importErrors.length > 0 && (
        <div
          className="card"
          style={{ borderColor: 'var(--status-failed-border)', background: 'var(--status-failed-bg)' }}
        >
          <div className="card-header" style={{ color: 'var(--status-failed)', borderColor: 'var(--status-failed-border)' }}>
            <AlertTriangle size={14} />
            {importErrors.length === 1
              ? '1 fichier de workflow en erreur'
              : `${importErrors.length} fichiers de workflow en erreur`}
          </div>
          <div className="card-body">
            {importErrors.map((e) => (
              <details key={e.file} style={{ marginBottom: 6 }}>
                <summary className="mono small" style={{ cursor: 'pointer', color: 'var(--status-failed)' }}>
                  {e.file} — {e.error.trim().split('\n').slice(-1)[0]}
                </summary>
                <pre className="mono small" style={{ whiteSpace: 'pre-wrap', margin: '6px 0 0' }}>
                  {e.error}
                </pre>
              </details>
            ))}
          </div>
        </div>
      )}

      <div className="stats">
        <div className="stat">
          <div className="stat-label">Workflows</div>
          <div className="stat-value">{totals.count}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Exécutions</div>
          <div className="stat-value">{totals.runs}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Avec approbation humaine</div>
          <div className="stat-value">{totals.approval}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Dernière exécution en échec</div>
          <div className="stat-value" style={{ color: totals.failing ? 'var(--status-failed)' : undefined }}>
            {totals.failing}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <Workflow size={14} color="var(--accent-primary)" /> Tous les workflows
          <span style={{ flex: 1 }} />
          <div style={{ position: 'relative' }}>
            <Search size={13} style={{ position: 'absolute', left: 9, top: 9, color: 'var(--text-muted)' }} />
            <input
              className="input"
              style={{ paddingLeft: 28 }}
              placeholder="Rechercher un workflow…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        </div>

        {error && <div className="empty" style={{ color: 'var(--status-failed)' }}>Impossible de charger les workflows : {error}</div>}
        {!error && workflows === null && <div className="empty">Chargement…</div>}
        {!error && workflows !== null && visible.length === 0 && (
          <div className="empty">Aucun workflow ne correspond à « {query} ».</div>
        )}

        {visible.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Étapes</th>
                  <th>Contraintes</th>
                  <th>Backends</th>
                  <th>Exécutions</th>
                  <th>Dernière exécution</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((w) => (
                  <tr key={w.id} className="clickable" onClick={() => navigate(`/workflows/${w.id}/grid`)}>
                    <td>
                      <a href={href(`/workflows/${w.id}/grid`)} style={{ color: 'var(--text-primary)', fontWeight: 600, textDecoration: 'none' }}>
                        {w.name || w.id}
                      </a>
                      <div className="mono small muted">{w.id}</div>
                      {w.source_file && <div className="mono small dim">{w.source_file}</div>}
                    </td>
                    <td className="mono">
                      {w.steps}
                      {w.requires_approval && <span className="chip violet" style={{ marginLeft: 6 }}>HITL</span>}
                    </td>
                    <td className="mono small muted"><Constraints c={w.constraints} /></td>
                    <td className="mono small">
                      {w.valid ? w.backends.map((b) => <div key={b}>{b}</div>) : (
                        <span style={{ color: 'var(--status-failed)' }} title={w.error}>invalide</span>
                      )}
                    </td>
                    <td className="mono small nowrap">
                      {w.runs}
                      {w.success_rate != null && <span className="muted"> · {Math.round(w.success_rate * 100)} % ok</span>}
                    </td>
                    <td>
                      {w.last_run ? (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                          <StatusPill status={w.last_run.status} />
                          <span className="small muted nowrap">{formatDate(w.last_run.started_at)}</span>
                        </div>
                      ) : (
                        <span className="small dim">jamais exécuté</span>
                      )}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={!w.valid}
                        onClick={(e) => {
                          e.stopPropagation();
                          onRun(w.id);
                        }}
                      >
                        <Play size={12} /> Run
                      </button>
                    </td>
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
