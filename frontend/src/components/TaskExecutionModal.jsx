import React, { useState, useEffect } from 'react';
import { Play, X, Terminal } from 'lucide-react';

const DEFAULT_TASKS = [
  {
    id: 'multi_agent_supervisor',
    name: 'Superviseur Multi-Agents Hiérarchique (Sous-graphes)',
    tag: 'SUBGRAPH',
    defaultScenario: 'supervision_strategique_ia',
  },
  {
    id: 'analyse_ventes_parallele',
    name: 'Pipeline Multi-Régions Parallèle (Fan-out / Fan-in)',
    tag: 'PARALLEL',
    defaultScenario: 'multi_region_q1_parallel',
  },
  {
    id: 'redaction_rapport_iteratif',
    name: 'Rédaction & Critique Itérative (EdgeType.LOOP)',
    tag: 'LOOP',
    defaultScenario: 'rapport_strategique_iteratif',
  },
  {
    id: 'validation_budget_hitl',
    name: 'Validation Budgétaire (Human-in-the-Loop)',
    tag: 'HITL',
    defaultScenario: 'demande_gpu_cloud_hitl',
  },
  {
    id: 'analyse_ventes_alertes',
    name: 'Pipeline Ventes (Séquentiel + Conditionnel)',
    tag: 'SEQUENTIAL',
    defaultScenario: 'scenario_b_single_anomaly',
  },
];

// Scenario proposed by default for a task (sales scenario B otherwise).
function defaultScenarioFor(taskId) {
  const known = DEFAULT_TASKS.find((t) => t.id === taskId);
  return known ? known.defaultScenario : 'scenario_b_single_anomaly';
}

export default function TaskExecutionModal({
  isOpen,
  initialTaskId,
  onClose,
  onExecutionCreated,
  apiBaseUrl,
}) {
  const [tasks, setTasks] = useState(DEFAULT_TASKS);
  const [selectedTaskId, setSelectedTaskId] = useState(DEFAULT_TASKS[0].id);
  const [scenarioName, setScenarioName] = useState(DEFAULT_TASKS[0].defaultScenario);
  // Execution engine: the same task gives the same result on every backend.
  const [backends, setBackends] = useState([{ id: 'langgraph', supports_hitl: true }]);
  const [backend, setBackend] = useState('langgraph');
  // Model selection: static registry ratings, or statistics learned from runs.
  const [optimizer, setOptimizer] = useState('static');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      fetch(`${apiBaseUrl}/tasks`)
        .then((res) => res.ok && res.json())
        .then((data) => {
          if (data && data.length > 0) {
            setTasks(data);
            const pick = data.some((t) => t.id === initialTaskId)
              ? initialTaskId
              : data[0].id;
            setSelectedTaskId(pick);
            setScenarioName(defaultScenarioFor(pick));
          }
        })
        .catch(() => { });
      fetch(`${apiBaseUrl}/backends`)
        .then((res) => res.ok && res.json())
        .then((data) => {
          if (data && data.length > 0) setBackends(data);
        })
        .catch(() => { });
    }
  }, [isOpen, apiBaseUrl, initialTaskId]);

  if (!isOpen) return null;

  const handleTaskChange = (newTaskId) => {
    setSelectedTaskId(newTaskId);
    setScenarioName(defaultScenarioFor(newTaskId));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${apiBaseUrl}/tasks/${selectedTaskId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scenario_name: scenarioName,
          backend,
          optimizer,
        }),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Execution trigger failed');
      }

      const newExec = await res.json();
      setLoading(false);
      onExecutionCreated(newExec);
      onClose();
    } catch (err) {
      console.error(err);
      setError(err.message);
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: 'rgba(9, 13, 22, 0.8)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        className="ide-window"
        style={{
          width: '100%',
          maxWidth: '520px',
          boxShadow: 'var(--shadow-lg)',
          animation: 'fadeIn 0.2s ease',
        }}
      >
        {/* Modal IDE Header */}
        <div className="ide-header">
          <div className="ide-mac-dots">
            <div className="ide-dot ide-dot-red" onClick={onClose} style={{ cursor: 'pointer' }} />
            <div className="ide-dot ide-dot-yellow" />
            <div className="ide-dot ide-dot-green" />
          </div>

          <div className="ide-title">
            <Terminal size={13} color="var(--accent-primary)" />
            <span>EXECUTE COGNITIVE TASK WORKFLOW</span>
          </div>

          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <X size={14} />
          </button>
        </div>

        {/* Modal Form Content */}
        <div style={{ padding: '20px' }}>
          {error && (
            <div
              style={{
                background: 'var(--status-failed-bg)',
                border: '1px solid var(--status-failed-border)',
                color: 'var(--status-failed)',
                padding: '8px 12px',
                borderRadius: 'var(--radius-sm)',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.75rem',
                marginBottom: '14px',
              }}
            >
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div>
              <label
                style={{
                  fontSize: '0.6875rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-muted)',
                  display: 'block',
                  marginBottom: '6px',
                  textTransform: 'uppercase',
                }}
              >
                SELECT WORKFLOW & CONTROL FLOW TYPE
              </label>
              <select
                value={selectedTaskId}
                onChange={(e) => handleTaskChange(e.target.value)}
                className="mono"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name || t.id}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                style={{
                  fontSize: '0.6875rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-muted)',
                  display: 'block',
                  marginBottom: '6px',
                  textTransform: 'uppercase',
                }}
              >
                SCENARIO / RUN IDENTIFIER
              </label>
              <input
                type="text"
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
                required
                className="mono"
                placeholder="e.g. multi_region_q1_parallel"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              />
            </div>

            <div>
              <label
                style={{
                  fontSize: '0.6875rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-muted)',
                  display: 'block',
                  marginBottom: '6px',
                  textTransform: 'uppercase',
                }}
              >
                EXECUTION BACKEND
              </label>
              <select
                value={backend}
                onChange={(e) => setBackend(e.target.value)}
                className="mono"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                {backends.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.id}
                    {b.supports_hitl ? '' : ' (no human approval)'}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                style={{
                  fontSize: '0.6875rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-muted)',
                  display: 'block',
                  marginBottom: '6px',
                  textTransform: 'uppercase',
                }}
              >
                MODEL OPTIMIZER
              </label>
              <select
                value={optimizer}
                onChange={(e) => setOptimizer(e.target.value)}
                className="mono"
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-surface-elevated)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                <option value="static">static (registry ratings)</option>
                <option value="learned">learned (statistics from past runs)</option>
              </select>
            </div>

            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: '8px',
                marginTop: '8px',
                paddingTop: '12px',
                borderTop: '1px solid var(--border-subtle)',
              }}
            >
              <button type="button" className="btn btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={loading}>
                <Play size={12} fill="currentColor" />
                <span>{loading ? 'Compiling & Running CEG...' : 'Execute Task'}</span>
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
