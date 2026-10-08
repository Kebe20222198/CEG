import React, { useCallback, useEffect, useState } from 'react';
import { UserCheck, CheckCircle2, XCircle } from 'lucide-react';
import { apiGet, apiPost, formatDate } from '../api';
import { href } from '../router';

// Approval inbox: every execution paused on a human-in-the-loop step, with
// what is being asked, and the decision buttons.

export default function ApprovalsPage({ onChanged }) {
  const [items, setItems] = useState(null);
  const [comments, setComments] = useState({});
  const [busy, setBusy] = useState(null);
  const [errors, setErrors] = useState({});

  const load = useCallback(() => {
    apiGet('/executions?status=awaiting_approval')
      .then((runs) => Promise.all(runs.map((r) => apiGet(`/executions/${r.id}`))))
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const decide = (run, approved) => {
    setBusy(run.id);
    apiPost(`/executions/${run.id}/resume`, { approved, comment: comments[run.id] || null })
      .then(() => {
        load();
        onChanged?.();
      })
      .catch((err) => setErrors((prev) => ({ ...prev, [run.id]: err.message })))
      .finally(() => setBusy(null));
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">À approuver</h1>
          <p className="page-subtitle">
            Les exécutions en pause sur une étape qui demande une décision humaine. Approuver
            reprend l'exécution ; rejeter saute l'étape et continue le workflow.
          </p>
        </div>
      </div>

      {items === null && <div className="empty">Chargement…</div>}
      {items !== null && items.length === 0 && (
        <div className="card">
          <div className="empty">
            <UserCheck size={22} style={{ marginBottom: 8 }} />
            <div>Rien à approuver pour le moment.</div>
          </div>
        </div>
      )}

      {(items || []).map((run) => {
        const pending = run.workflow_state?.pending_approvals || [];
        return (
          <div className="card" key={run.id}>
            <div className="card-header">
              <UserCheck size={14} color="var(--accent-violet)" />
              <a href={href(`/workflows/${run.task_id}/grid`)}>{run.task_id}</a>
              <span className="muted">·</span>
              <a className="mono small" href={href(`/runs/${run.id}`)}>{run.id}</a>
              <span style={{ flex: 1 }} />
              <span className="small muted">{formatDate(run.started_at)}</span>
            </div>
            <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {pending.map((p) => (
                <div key={p.node_id}>
                  <div style={{ fontWeight: 600 }}>{p.message}</div>
                  {p.objective && <div className="small muted">Étape « {p.node_id} » : {p.objective}</div>}
                </div>
              ))}
              <input
                className="input"
                placeholder="Commentaire (facultatif)"
                value={comments[run.id] || ''}
                onChange={(e) => setComments((prev) => ({ ...prev, [run.id]: e.target.value }))}
              />
              {errors[run.id] && <div className="small" style={{ color: 'var(--status-failed)' }}>{errors[run.id]}</div>}
              <div className="toolbar">
                <button className="btn btn-primary" disabled={busy === run.id} onClick={() => decide(run, true)}>
                  <CheckCircle2 size={14} /> Approuver et continuer
                </button>
                <button className="btn" disabled={busy === run.id} onClick={() => decide(run, false)}>
                  <XCircle size={14} /> Rejeter l'étape
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}
