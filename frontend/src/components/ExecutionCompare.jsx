import React, { useState, useEffect } from 'react';
import { ArrowRightLeft } from 'lucide-react';
import FlowGraph from './FlowGraph';

export default function ExecutionCompare({ executions, apiBaseUrl }) {
  const [execIdA, setExecIdA] = useState(executions[0]?.id || '');
  const [execIdB, setExecIdB] = useState(
    executions[1]?.id || executions[0]?.id || ''
  );

  const [detailA, setDetailA] = useState(null);
  const [detailB, setDetailB] = useState(null);

  const [metricsA, setMetricsA] = useState(null);
  const [metricsB, setMetricsB] = useState(null);

  // Synchronize execution IDs when executions array loads
  useEffect(() => {
    if (executions && executions.length > 0) {
      const known = (id) => id && executions.some((e) => e.id === id);
      setExecIdA((prev) => (known(prev) ? prev : executions[0].id));
      setExecIdB((prev) =>
        known(prev) ? prev : executions[1]?.id || executions[0].id
      );
    }
  }, [executions]);

  useEffect(() => {
    if (execIdA) {
      fetch(`${apiBaseUrl}/executions/${execIdA}`)
        .then((r) => r.ok && r.json())
        .then(setDetailA);
      fetch(`${apiBaseUrl}/executions/${execIdA}/metrics`)
        .then((r) => r.ok && r.json())
        .then(setMetricsA);
    }
  }, [execIdA, apiBaseUrl]);

  useEffect(() => {
    if (execIdB) {
      fetch(`${apiBaseUrl}/executions/${execIdB}`)
        .then((r) => r.ok && r.json())
        .then(setDetailB);
      fetch(`${apiBaseUrl}/executions/${execIdB}/metrics`)
        .then((r) => r.ok && r.json())
        .then(setMetricsB);
    }
  }, [execIdB, apiBaseUrl]);

  // Compute differential deltas (B - A)
  const costDiff = (detailB?.total_cost || 0) - (detailA?.total_cost || 0);
  const latencyDiff = (detailB?.total_latency_ms || 0) - (detailA?.total_latency_ms || 0);
  // A null score was not measured: no delta can be computed from it.
  const scoreDelta = (a, b) => (a == null || b == null ? null : (b - a) * 100);
  const qualityDiff = scoreDelta(metricsA?.quality_score, metricsB?.quality_score);
  const compositeDiff = scoreDelta(metricsA?.composite_score, metricsB?.composite_score);
  const formatScore = (v) => (v == null ? 'n/a' : `${(v * 100).toFixed(1)}%`);
  const formatDelta = (d) =>
    d == null ? 'n/a' : d >= 0 ? `+${d.toFixed(1)}%` : `${d.toFixed(1)}%`;
  const deltaColor = (d) =>
    d == null
      ? 'var(--text-muted)'
      : d >= 0
        ? 'var(--status-completed)'
        : 'var(--status-failed)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      
      {/* ── Selectors Bar ── */}
      <div
        className="ide-window"
        style={{
          padding: '14px 18px',
          display: 'grid',
          gridTemplateColumns: '1fr auto 1fr',
          gap: '16px',
          alignItems: 'center',
        }}
      >
        <div>
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              marginBottom: '6px',
              textTransform: 'uppercase',
            }}
          >
            BASELINE (EXECUTION A)
          </div>
          <select
            value={execIdA}
            onChange={(e) => setExecIdA(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-default)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          >
            {executions.map((e) => (
              <option key={e.id} value={e.id}>
                {e.scenario_name} ({e.id}) — {e.status}
              </option>
            ))}
          </select>
        </div>

        <div style={{ padding: '8px', color: 'var(--text-muted)' }}>
          <ArrowRightLeft size={18} color="var(--accent-primary)" />
        </div>

        <div>
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              marginBottom: '6px',
              textTransform: 'uppercase',
            }}
          >
            COMPARISON (EXECUTION B)
          </div>
          <select
            value={execIdB}
            onChange={(e) => setExecIdB(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-default)',
              color: 'var(--text-primary)',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          >
            {executions.map((e) => (
              <option key={e.id} value={e.id}>
                {e.scenario_name} ({e.id}) — {e.status}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ── Differential Metrics Panel ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
        }}
      >
        {/* Cost Delta */}
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '12px 16px',
          }}
        >
          <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            COST DELTA (B vs A)
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '4px' }}>
            <span className="mono" style={{ fontSize: '1.25rem', fontWeight: 700 }}>
              ${(detailB?.total_cost || 0).toFixed(4)}
            </span>
            <span
              className="mono"
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: costDiff <= 0 ? 'var(--status-completed)' : 'var(--status-failed)',
              }}
            >
              {costDiff > 0 ? `+$${costDiff.toFixed(4)}` : `-$${Math.abs(costDiff).toFixed(4)}`}
            </span>
          </div>
        </div>

        {/* Latency Delta */}
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '12px 16px',
          }}
        >
          <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            LATENCY DELTA
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '4px' }}>
            <span className="mono" style={{ fontSize: '1.25rem', fontWeight: 700 }}>
              {(detailB?.total_latency_ms || 0).toFixed(0)} ms
            </span>
            <span
              className="mono"
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: latencyDiff <= 0 ? 'var(--status-completed)' : 'var(--status-failed)',
              }}
            >
              {latencyDiff > 0 ? `+${latencyDiff.toFixed(0)}ms` : `${latencyDiff.toFixed(0)}ms`}
            </span>
          </div>
        </div>

        {/* Quality Score Delta */}
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '12px 16px',
          }}
        >
          <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            QUALITY SCORE DELTA
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '4px' }}>
            <span className="mono" style={{ fontSize: '1.25rem', fontWeight: 700 }}>
              {formatScore(metricsB?.quality_score)}
            </span>
            <span
              className="mono"
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: deltaColor(qualityDiff),
              }}
            >
              {formatDelta(qualityDiff)}
            </span>
          </div>
        </div>

        {/* Composite Score Delta */}
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '12px 16px',
          }}
        >
          <div style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            COMPOSITE SCORE DELTA
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginTop: '4px' }}>
            <span className="mono" style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--accent-primary)' }}>
              {formatScore(metricsB?.composite_score)}
            </span>
            <span
              className="mono"
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: deltaColor(compositeDiff),
              }}
            >
              {formatDelta(compositeDiff)}
            </span>
          </div>
        </div>
      </div>

      {/* ── Dual Side-by-Side Graph Canvases ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '16px',
        }}
      >
        {/* Graph A */}
        <FlowGraph
          graphData={detailA?.graph}
          nodeStatuses={detailA?.workflow_state?.node_statuses || {}}
          title={`Graph A: ${detailA?.scenario_name || 'Loading...'}`}
          height="460px"
          showMiniMap={false}
        />

        {/* Graph B */}
        <FlowGraph
          graphData={detailB?.graph}
          nodeStatuses={detailB?.workflow_state?.node_statuses || {}}
          title={`Graph B: ${detailB?.scenario_name || 'Loading...'}`}
          height="460px"
          showMiniMap={false}
        />
      </div>
    </div>
  );
}
