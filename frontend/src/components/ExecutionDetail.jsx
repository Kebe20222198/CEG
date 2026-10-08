import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  Zap,
  Activity,
  UserCheck,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import FlowGraph from './FlowGraph';
import NodeInspector from './NodeInspector';

export default function ExecutionDetail({
  executionId,
  executions = [],
  onBack,
  onRunNew,
  apiBaseUrl,
}) {
  const currentExecId = executionId || executions[0]?.id;
  const [detail, setDetail] = useState(null);
  const [traces, setTraces] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [isResuming, setIsResuming] = useState(false);
  const [resumeError, setResumeError] = useState(null);

  const fetchDetail = useCallback(() => {
    if (!currentExecId) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    Promise.all([
      fetch(`${apiBaseUrl}/executions/${currentExecId}`).then((res) => {
        if (!res.ok) throw new Error(`Execution ${currentExecId} not found`);
        return res.json();
      }),
      fetch(`${apiBaseUrl}/executions/${currentExecId}/trace`).then((res) =>
        res.ok ? res.json() : []
      ),
      fetch(`${apiBaseUrl}/executions/${currentExecId}/metrics`).then((res) =>
        res.ok ? res.json() : null
      ),
    ])
      .then(([detailData, traceData, metricsData]) => {
        setDetail(detailData);
        setTraces(traceData);
        setMetrics(metricsData);
        if (traceData.length > 0) {
          setSelectedNodeId(traceData[0].node_id);
        } else if (detailData?.graph?.nodes?.length > 0) {
          setSelectedNodeId(detailData.graph.nodes[0].id);
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setError(err.message);
        setLoading(false);
      });
  }, [currentExecId, apiBaseUrl]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

  // Handle HITL resume action (Approve / Reject)
  const handleHITLResume = async (approved) => {
    setIsResuming(true);
    setResumeError(null);
    try {
      const res = await fetch(`${apiBaseUrl}/executions/${currentExecId}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved,
          comment: approved
            ? 'Approved via CEG Studio UI'
            : 'Rejected via CEG Studio UI',
        }),
      });
      if (res.ok) {
        fetchDetail();
      } else {
        const errData = await res.json().catch(() => ({}));
        setResumeError(errData.detail || `Resume failed (HTTP ${res.status})`);
      }
    } catch (err) {
      console.error('Resume error:', err);
      setResumeError(err.message);
    } finally {
      setIsResuming(false);
    }
  };

  if (!currentExecId && !loading) {
    return (
      <div
        className="ide-window"
        style={{
          padding: '60px',
          textAlign: 'center',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-muted)',
        }}
      >
        <div style={{ marginBottom: '16px', fontSize: '0.875rem' }}>
          // No execution selected or available in database
        </div>
        {onRunNew && (
          <button className="btn btn-primary" onClick={onRunNew}>
            Launch New Task
          </button>
        )}
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className="ide-window"
        style={{
          padding: '60px',
          textAlign: 'center',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-muted)',
          fontSize: '0.875rem',
        }}
      >
        // Connecting to CEG Runtime & resolving traces for {currentExecId || 'execution'}...
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div
        className="ide-window"
        style={{ padding: '40px', textAlign: 'center' }}
      >
        <div style={{ color: 'var(--status-failed)', marginBottom: '16px', fontFamily: 'var(--font-mono)' }}>
          {error || 'Execution not found'}
        </div>
        <button className="btn btn-ghost" onClick={onBack}>
          <ArrowLeft size={14} /> Back to dashboard
        </button>
      </div>
    );
  }

  // Derive node statuses
  const nodeStatuses = {};
  if (detail.workflow_state?.node_statuses) {
    Object.assign(nodeStatuses, detail.workflow_state.node_statuses);
  } else if (traces.length > 0) {
    traces.forEach((t) => {
      nodeStatuses[t.node_id] = t.status;
    });
  }

  const selectedNodeObj = detail.graph?.nodes?.find((n) => n.id === selectedNodeId);
  const selectedTraceObj = traces.find((t) => t.node_id === selectedNodeId);
  const selectedRawOutput = detail.workflow_state?.node_outputs?.[selectedNodeId];

  const hasHITLInterruption = detail.status === 'awaiting_approval';
  const pendingApprovals = detail.workflow_state?.pending_approvals || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* ── Top Bar with back button & execution summary ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button className="btn btn-ghost" onClick={onBack}>
            <ArrowLeft size={14} />
            <span>Dashboard</span>
          </button>

          <span style={{ color: 'var(--text-dim)' }}>/</span>

          <span
            className="mono"
            style={{
              fontWeight: 600,
              fontSize: '0.875rem',
              color: 'var(--text-primary)',
            }}
          >
            {detail.id}
          </span>

          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.75rem',
              padding: '2px 8px',
              borderRadius: '4px',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-default)',
              color: 'var(--text-secondary)',
            }}
          >
            {detail.scenario_name}
          </span>

          {detail.backend && (
            <span
              className="mono"
              title="Execution backend"
              style={{
                fontSize: '0.75rem',
                padding: '2px 8px',
                borderRadius: '4px',
                border: '1px solid var(--border-default)',
                color: 'var(--accent-primary)',
              }}
            >
              ⚙ {detail.backend}
              {detail.optimizer ? ` · ${detail.optimizer}` : ''}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span className={`status-pill ${detail.status}`}>
            <span className="status-pill-dot" />
            <span>{detail.status}</span>
          </span>

          <div
            className="mono"
            style={{
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-default)',
              padding: '4px 10px',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            ${Number(detail.total_cost || 0).toFixed(4)} • {Number(detail.total_latency_ms || 0).toFixed(0)}ms
          </div>
        </div>
      </div>

      {/* ── HITL Action Banner if execution is paused for approval ── */}
      {hasHITLInterruption && (
        <div
          style={{
            padding: '14px 18px',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(168, 85, 247, 0.12)',
            border: '1px solid rgba(168, 85, 247, 0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '16px',
            boxShadow: '0 0 20px rgba(168, 85, 247, 0.2)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <UserCheck size={20} color="#c084fc" />
            <div>
              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  color: '#e9d5ff',
                }}
              >
                HUMAN-IN-THE-LOOP INTERRUPT ACTIVE
              </div>
              <div style={{ fontSize: '0.75rem', color: '#c084fc' }}>
                {pendingApprovals.length > 0
                  ? pendingApprovals.map((p) => p.message).join(' • ')
                  : 'This execution hit a checkpointed approval node. A human decision is required to proceed.'}
              </div>
              {resumeError && (
                <div style={{ fontSize: '0.75rem', color: 'var(--status-failed)', marginTop: '4px' }}>
                  {resumeError}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              className="btn btn-primary"
              onClick={() => handleHITLResume(true)}
              disabled={isResuming}
              style={{ background: 'var(--status-completed)', borderColor: 'var(--status-completed)' }}
            >
              <CheckCircle2 size={14} /> Approve & Continue
            </button>

            <button
              className="btn"
              onClick={() => handleHITLResume(false)}
              disabled={isResuming}
              style={{ color: 'var(--status-failed)', borderColor: 'rgba(239, 68, 68, 0.4)' }}
            >
              <XCircle size={14} /> Reject & Skip
            </button>
          </div>
        </div>
      )}

      {/* ── Evaluation Metrics Banner (S5 Composite Score) ── */}
      {metrics && (
        <div
          style={{
            padding: '12px 18px',
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Zap size={16} color="var(--accent-primary)" />
            <div>
              <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                COMPOSITE SCORE
              </div>
              <div className="mono" style={{ fontSize: '1.125rem', fontWeight: 700, color: 'var(--accent-primary)' }}>
                {formatScore(metrics.composite_score)}
              </div>
            </div>
          </div>

          <div>
            <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
              QUALITY SCORE
            </div>
            <div className="mono" style={{ fontSize: '1.125rem', fontWeight: 700, color: 'var(--status-completed)' }}>
              {formatScore(metrics.quality_score)}
            </div>
          </div>

          <div>
            <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
              ROBUSTNESS SCORE
            </div>
            <div className="mono" style={{ fontSize: '1.125rem', fontWeight: 700, color: 'var(--accent-violet)' }}>
              {formatScore(metrics.robustness_score)}
            </div>
          </div>

          <div>
            <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
              EVAL ENGINE
            </div>
            <div className="mono" style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
              {judgeLabel(metrics.report?.metadata?.judge)}
            </div>
          </div>
        </div>
      )}

      {/* ── Studio Split View: FlowGraph (Left) + NodeInspector (Right) ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.4fr) minmax(320px, 1fr)',
          gap: '16px',
          minHeight: '520px',
        }}
      >
        {/* Left: Studio IDE FlowGraph */}
        <FlowGraph
          graphData={detail.graph}
          nodeStatuses={nodeStatuses}
          selectedNodeId={selectedNodeId}
          onNodeSelect={setSelectedNodeId}
          traceData={traces}
          title={`CEG Execution Flow // ${detail.scenario_name}`}
          height="520px"
        />

        {/* Right: Studio Node Inspector */}
        <NodeInspector
          selectedNode={selectedNodeObj}
          traceInfo={selectedTraceObj}
          rawOutput={selectedRawOutput}
        />
      </div>

      {/* ── Chronological Node Trace Stream ── */}
      <div className="ide-window">
        <div className="ide-header">
          <div className="ide-title">
            <Activity size={13} color="var(--accent-primary)" />
            <span>EXECUTION TRACE LOG ({traces.length} STEPS)</span>
          </div>
        </div>

        <div style={{ padding: '8px' }}>
          {traces.length === 0 ? (
            <div
              style={{
                padding: '24px',
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.75rem',
                color: 'var(--text-dim)',
              }}
            >
              No step traces recorded yet
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {traces.map((trace, idx) => {
                const isSelected = trace.node_id === selectedNodeId;
                return (
                  <div
                    key={trace.node_id + idx}
                    onClick={() => setSelectedNodeId(trace.node_id)}
                    style={{
                      padding: '8px 12px',
                      borderRadius: 'var(--radius-sm)',
                      background: isSelected ? 'var(--bg-surface-active)' : 'transparent',
                      border: isSelected
                        ? '1px solid var(--accent-primary)'
                        : '1px solid transparent',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      transition: 'all var(--transition-fast)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span
                        className="mono"
                        style={{
                          fontSize: '0.6875rem',
                          color: 'var(--text-dim)',
                          width: '24px',
                        }}
                      >
                        #{idx + 1}
                      </span>

                      <span className={`status-pill ${trace.status}`}>
                        <span className="status-pill-dot" />
                        <span>{trace.status}</span>
                      </span>

                      <span
                        className="mono"
                        style={{
                          fontWeight: 600,
                          fontSize: '0.8125rem',
                          color: isSelected ? 'var(--accent-primary)' : 'var(--text-primary)',
                        }}
                      >
                        {trace.node_id}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                      <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        {trace.model || 'default'}
                      </span>
                      <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        ${Number(trace.cost || 0).toFixed(4)}
                      </span>
                      <span className="mono" style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                        {Number(trace.latency_ms || 0).toFixed(0)}ms
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

    </div>
  );
}

// A null score was not measured: show it as such instead of 0%.
function formatScore(value) {
  return value == null ? 'not measured' : `${(value * 100).toFixed(1)}%`;
}

function judgeLabel(judge) {
  if (!judge) return 'LLM-as-Judge';
  return judge.startsWith('Mock') ? `${judge} (simulated scores)` : judge;
}
