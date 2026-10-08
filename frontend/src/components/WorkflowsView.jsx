import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Workflow,
  Play,
  FileCode2,
  GitBranch,
  Code,
  ShieldCheck,
  RefreshCw,
  AlertTriangle,
  Info,
} from 'lucide-react';
import FlowGraph from './FlowGraph';
import CodeViewer from './CodeViewer';

// Workflow list + per-workflow Graph / Code / Source / Declaration views,
// like Airflow's DAG list and its Graph and Code tabs.

const TABS = [
  { id: 'graph', label: 'Graphe', icon: GitBranch },
  { id: 'code', label: 'Code', icon: Code },
  { id: 'source', label: 'Source', icon: FileCode2 },
  { id: 'declaration', label: 'Déclaration (JSON)', icon: ShieldCheck },
];

const cell = { padding: '10px 14px', verticalAlign: 'middle' };
const mono = { fontFamily: 'var(--font-mono)', fontSize: '0.75rem' };

function formatConstraints(c) {
  if (!c) return '—';
  return `≤ $${c.max_cost_usd.toFixed(2)} · ≤ ${c.max_latency_seconds}s · q ≥ ${c.min_quality_score}`;
}

function formatRate(rate) {
  return rate == null ? '—' : `${Math.round(rate * 100)} %`;
}

export default function WorkflowsView({ apiBaseUrl, onRun }) {
  const [workflows, setWorkflows] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState('graph');
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [error, setError] = useState(null);
  // Workflow files that failed to load (Airflow's "Broken DAG").
  const [importErrors, setImportErrors] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  // The detail panel sits below the list: bring it into view when a
  // workflow is opened, otherwise clicking "Code" seems to do nothing.
  const detailRef = useRef(null);
  const [scrollRequested, setScrollRequested] = useState(false);

  const loadList = useCallback(() => {
    fetch(`${apiBaseUrl}/workflows`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setWorkflows(data);
        setSelectedId((prev) => prev ?? data[0]?.id ?? null);
      })
      .catch((err) => setError(`Impossible de charger les workflows : ${err.message}`));
    fetch(`${apiBaseUrl}/workflows/errors`)
      .then((res) => (res.ok ? res.json() : []))
      .then(setImportErrors)
      .catch(() => setImportErrors([]));
  }, [apiBaseUrl]);

  const refresh = () => {
    setRefreshing(true);
    fetch(`${apiBaseUrl}/workflows/refresh`, { method: 'POST' })
      .catch(() => {})
      .finally(() => {
        loadList();
        setRefreshing(false);
      });
  };

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selectedId) return;
    setDetail(null);
    fetch(`${apiBaseUrl}/workflows/${selectedId}`)
      .then((res) => res.ok && res.json())
      .then((data) => data && setDetail(data))
      .catch(() => setDetail(null));
  }, [selectedId, apiBaseUrl]);

  const openWorkflow = (id, nextTab) => {
    setSelectedId(id);
    if (nextTab) setTab(nextTab);
    setScrollRequested(true);
  };

  useEffect(() => {
    if (scrollRequested && detail && detailRef.current) {
      detailRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setScrollRequested(false);
    }
  }, [scrollRequested, detail]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* ── Workflow list ── */}
      <div className="ide-window" style={{ overflow: 'hidden' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-default)',
            ...mono,
            fontSize: '0.8125rem',
            fontWeight: 600,
          }}
        >
          <Workflow size={15} color="var(--accent-primary)" />
          <span>WORKFLOWS ({workflows.length})</span>
          <span style={{ flex: 1 }} />
          <button className="btn btn-ghost" onClick={() => setShowHelp((v) => !v)}>
            <Info size={13} /> Ajouter un workflow
          </button>
          <button className="btn btn-ghost" onClick={refresh} disabled={refreshing}>
            <RefreshCw size={13} /> {refreshing ? 'Lecture…' : 'Rafraîchir'}
          </button>
        </div>

        {showHelp && (
          <div
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid var(--border-default)',
              fontSize: '0.8125rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
            }}
          >
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
              Ajouter un workflow, comme un DAG Airflow
            </div>
            <ol style={{ margin: 0, paddingLeft: '18px' }}>
              <li>
                Créer un fichier Python dans le dossier <code>workflows/</code> : une
                fonction décorée par <code>@workflow</code> qui renvoie la déclaration
                (<code>CognitiveTask</code>), et l'exécuteur de ses étapes. Exemple :{' '}
                <code>workflows/tri_tickets_support.py</code>.
              </li>
              <li>
                Le vérifier sans l'exécuter : <code>ceg validate &lt;workflow&gt;</code>,
                ou l'essayer dans le terminal : <code>ceg run &lt;workflow&gt;</code>.
              </li>
              <li>
                Il apparaît ici automatiquement (sinon « Rafraîchir ») : graphe, code,
                exécution avec le bouton Run.
              </li>
            </ol>
          </div>
        )}

        {importErrors.length > 0 && (
          <div
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid var(--status-failed-border)',
              background: 'var(--status-failed-bg)',
              color: 'var(--status-failed)',
              fontSize: '0.8125rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
              <AlertTriangle size={14} />
              {importErrors.length === 1
                ? '1 fichier de workflow en erreur'
                : `${importErrors.length} fichiers de workflow en erreur`}
            </div>
            {importErrors.map((e) => (
              <details key={e.file} style={{ marginTop: '6px' }}>
                <summary style={{ cursor: 'pointer', ...mono }}>
                  {e.file} — {e.error.trim().split('\n').slice(-1)[0]}
                </summary>
                <pre style={{ margin: '6px 0 0', whiteSpace: 'pre-wrap', ...mono }}>{e.error}</pre>
              </details>
            ))}
          </div>
        )}

        {error && (
          <div style={{ padding: '12px 16px', color: 'var(--status-failed)', ...mono }}>
            {error}
          </div>
        )}

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-muted)', ...mono }}>
                <th style={cell}>Workflow</th>
                <th style={cell}>Modèle</th>
                <th style={cell}>Étapes</th>
                <th style={cell}>Contraintes</th>
                <th style={cell}>Backends</th>
                <th style={cell}>Exécutions</th>
                <th style={cell}>Dernière</th>
                <th style={cell}></th>
              </tr>
            </thead>
            <tbody>
              {workflows.map((w) => (
                <tr
                  key={w.id}
                  onClick={() => openWorkflow(w.id)}
                  style={{
                    cursor: 'pointer',
                    borderTop: '1px solid var(--border-subtle)',
                    background:
                      w.id === selectedId ? 'var(--bg-surface-elevated)' : 'transparent',
                  }}
                >
                  <td style={cell}>
                    <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                      {w.name || w.id}
                    </div>
                    <div style={{ ...mono, color: 'var(--text-muted)' }}>{w.id}</div>
                    {w.source_file && (
                      <div style={{ ...mono, color: 'var(--text-dim)', fontSize: '0.6875rem' }}>
                        {w.source_file}
                      </div>
                    )}
                  </td>
                  <td style={{ ...cell, ...mono }}>{w.pipeline || 'déclaration API'}</td>
                  <td style={{ ...cell, ...mono }}>
                    {w.steps}
                    {w.requires_approval ? ' · HITL' : ''}
                  </td>
                  <td style={{ ...cell, ...mono, color: 'var(--text-secondary)' }}>
                    {formatConstraints(w.constraints)}
                  </td>
                  <td style={{ ...cell, ...mono }}>
                    {w.valid ? w.backends.join(', ') : (
                      <span style={{ color: 'var(--status-failed)' }} title={w.error}>
                        invalide
                      </span>
                    )}
                  </td>
                  <td style={{ ...cell, ...mono }}>
                    {w.runs} · {formatRate(w.success_rate)}
                  </td>
                  <td style={cell}>
                    {w.last_run ? (
                      <span className={`status-pill ${w.last_run.status}`}>
                        <span className="status-pill-dot" />
                        <span>{w.last_run.status}</span>
                      </span>
                    ) : (
                      <span style={{ ...mono, color: 'var(--text-dim)' }}>jamais</span>
                    )}
                  </td>
                  <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                    <button
                      className="btn btn-ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        openWorkflow(w.id, 'code');
                      }}
                      title="Voir le code"
                    >
                      <Code size={13} /> Code
                    </button>
                    <button
                      className="btn btn-primary"
                      disabled={!w.valid}
                      onClick={(e) => {
                        e.stopPropagation();
                        onRun(w.id);
                      }}
                      title="Exécuter"
                      style={{ marginLeft: '6px' }}
                    >
                      <Play size={13} /> Run
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Selected workflow ── */}
      {detail && (
        <div
          ref={detailRef}
          className="ide-window"
          style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px', scrollMarginTop: '16px' }}
        >
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.9375rem' }}>{detail.name || detail.id}</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.8125rem', marginTop: '4px' }}>
              {detail.objective}
            </div>
            {detail.source_file && (
              <div style={{ ...mono, color: 'var(--text-muted)', marginTop: '4px' }}>
                Fichier : {detail.source_file}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: '4px', borderBottom: '1px solid var(--border-default)' }}>
            {TABS.map((t) => {
              const Icon = t.icon;
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '8px 12px',
                    background: 'none',
                    border: 'none',
                    borderBottom: active ? '2px solid var(--accent-primary)' : '2px solid transparent',
                    color: active ? 'var(--text-primary)' : 'var(--text-muted)',
                    cursor: 'pointer',
                    ...mono,
                  }}
                >
                  <Icon size={13} /> {t.label}
                </button>
              );
            })}
          </div>

          {tab === 'graph' && (
            detail.plan ? (
              <FlowGraph
                graphData={detail.plan}
                nodeStatuses={{}}
                selectedNodeId={selectedNodeId}
                onNodeSelect={setSelectedNodeId}
                title={`Plan // ${detail.id}`}
                height="480px"
              />
            ) : (
              <div style={{ color: 'var(--status-failed)', ...mono }}>
                Déclaration invalide : {detail.error}
              </div>
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
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
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
              <div style={{ color: 'var(--text-muted)', ...mono }}>
                Ce workflow a été déclaré via l'API : il n'a pas de fichier source,
                son code est dans l'onglet « Code ».
              </div>
            )
          )}

          {tab === 'declaration' && (
            <CodeViewer
              code={JSON.stringify(detail.declaration, null, 2)}
              language="json"
              title="Déclaration stockée (champs non par défaut)"
            />
          )}
        </div>
      )}
    </div>
  );
}
