import React, { useState } from 'react';
import {
  Code,
  Cpu,
  Clock,
  DollarSign,
  ShieldAlert,
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  Info,
  Terminal,
  Layers,
} from 'lucide-react';

export default function NodeInspector({
  selectedNode,
  traceInfo,
  rawOutput,
  onClose,
}) {
  const [copied, setCopied] = useState(false);
  const [expandedSections, setExpandedSections] = useState({
    output: true,
    subgraph: true,
    prompt: false,
    decision: true,
    meta: true,
  });

  const toggleSection = (sec) => {
    setExpandedSections((prev) => ({ ...prev, [sec]: !prev[sec] }));
  };

  const handleCopyJSON = (data) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!selectedNode && !traceInfo) {
    return (
      <div
        className="ide-window"
        style={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          color: 'var(--text-muted)',
          textAlign: 'center',
        }}
      >
        <Terminal size={32} style={{ marginBottom: '12px', opacity: 0.4 }} />
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8125rem' }}>
          Select a node on the graph to inspect runtime trace & state
        </div>
      </div>
    );
  }

  const nodeId = selectedNode?.id || traceInfo?.node_id;
  const status = traceInfo?.status || selectedNode?.status || 'pending';
  const model = traceInfo?.model || selectedNode?.assigned_model || 'auto-selected';
  const cost = traceInfo?.cost !== undefined ? traceInfo.cost : selectedNode?.accumulated_cost || 0;
  const latency = traceInfo?.latency_ms !== undefined ? traceInfo.latency_ms : selectedNode?.latency_ms || 0;
  const outputData = traceInfo?.response || rawOutput || selectedNode?.output;

  return (
    <div
      className="ide-window"
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--ide-inspector-bg)',
      }}
    >
      {/* Inspector Title Bar */}
      <div
        className="ide-header"
        style={{ background: 'var(--bg-surface-elevated)' }}
      >
        <div className="ide-title">
          <Code size={13} color="var(--accent-primary)" />
          <span>INSPECTOR // {nodeId}</span>
        </div>

        <div className={`status-pill ${status}`}>
          <span className="status-pill-dot" />
          <span>{status}</span>
        </div>
      </div>

      {/* Content Area with dense developer tools layout */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '14px',
        }}
      >
        {/* Objective / Overview */}
        {selectedNode?.objective && (
          <div
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                fontSize: '0.6875rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
                marginBottom: '4px',
              }}
            >
              OBJECTIVE
            </div>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-primary)' }}>
              {selectedNode.objective}
            </div>
          </div>
        )}

        {/* Metrics Grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '8px',
          }}
        >
          <div
            style={{
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '0.6875rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                marginBottom: '2px',
              }}
            >
              <Cpu size={12} color="var(--accent-primary)" />
              <span>MODEL</span>
            </div>
            <div
              className="mono"
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
              }}
            >
              {model}
            </div>
          </div>

          <div
            style={{
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '0.6875rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                marginBottom: '2px',
              }}
            >
              <Clock size={12} color="var(--accent-purple)" />
              <span>LATENCY</span>
            </div>
            <div
              className="mono"
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
              }}
            >
              {Number(latency).toFixed(1)} ms
            </div>
          </div>

          <div
            style={{
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '5px',
                fontSize: '0.6875rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                marginBottom: '2px',
              }}
            >
              <DollarSign size={12} color="var(--accent-cyan)" />
              <span>COST</span>
            </div>
            <div
              className="mono"
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: 'var(--accent-cyan)',
              }}
            >
              ${Number(cost).toFixed(5)}
            </div>
          </div>

          <div
            style={{
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                fontSize: '0.6875rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                marginBottom: '2px',
              }}
            >
              TOKENS IN / OUT
            </div>
            <div
              className="mono"
              style={{
                fontSize: '0.8125rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
              }}
            >
              {traceInfo?.tokens_input || 0} / {traceInfo?.tokens_output || 0}
            </div>
          </div>
        </div>

        {/* Encapsulated Subgraph Details */}
        {selectedNode?.subgraph && (
          <div
            style={{
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid rgba(56, 189, 248, 0.3)',
              overflow: 'hidden',
            }}
          >
            <div
              onClick={() => toggleSection('subgraph')}
              style={{
                padding: '8px 12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                cursor: 'pointer',
                background: 'rgba(56, 189, 248, 0.08)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Layers size={13} color="#38bdf8" />
                <span
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.6875rem',
                    color: '#38bdf8',
                    textTransform: 'uppercase',
                    fontWeight: 600,
                  }}
                >
                  ENCAPSULATED SUBGRAPH ({selectedNode.subgraph.nodes?.length || 0} NODES)
                </span>
              </div>
              {expandedSections.subgraph ? <ChevronDown size={14} color="#38bdf8" /> : <ChevronRight size={14} color="#38bdf8" />}
            </div>

            {expandedSections.subgraph && (
              <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {selectedNode.subgraph.nodes?.map((subNode, idx) => (
                  <div
                    key={subNode.id}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-sm)',
                      background: 'rgba(255, 255, 255, 0.03)',
                      border: '1px solid var(--border-subtle)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '4px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span className="mono" style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        #{idx + 1} {subNode.id}
                      </span>
                      {subNode.model_tier_hint && (
                        <span style={{ fontSize: '0.6rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                          {subNode.model_tier_hint}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: '0.6875rem', color: 'var(--text-secondary)' }}>
                      {subNode.objective}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Runtime Decision / Fallback Section */}
        {traceInfo?.decision && (
          <div
            style={{
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-elevated)',
              border: '1px solid var(--border-subtle)',
              overflow: 'hidden',
            }}
          >
            <div
              onClick={() => toggleSection('decision')}
              style={{
                padding: '8px 12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                cursor: 'pointer',
                background: 'rgba(255, 255, 255, 0.02)',
              }}
            >
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.6875rem',
                  fontWeight: 600,
                  color: 'var(--text-muted)',
                  textTransform: 'uppercase',
                }}
              >
                Runtime Decision
              </span>
              {expandedSections.decision ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </div>

            {expandedSections.decision && (
              <div
                style={{
                  padding: '10px 12px',
                  fontSize: '0.75rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-secondary)',
                  borderTop: '1px solid var(--border-subtle)',
                }}
              >
                {typeof traceInfo.decision === 'string'
                  ? traceInfo.decision
                  : JSON.stringify(traceInfo.decision, null, 2)}
              </div>
            )}
          </div>
        )}

        {/* Fallbacks Triggered if any */}
        {traceInfo?.fallbacks_triggered && (
          <div
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-skipped-bg)',
              border: '1px solid var(--status-skipped-border)',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '8px',
            }}
          >
            <ShieldAlert size={16} color="var(--accent-amber)" style={{ flexShrink: 0, marginTop: '2px' }} />
            <div>
              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.7rem',
                  fontWeight: 600,
                  color: 'var(--status-skipped)',
                  textTransform: 'uppercase',
                }}
              >
                Fallback Strategy Triggered
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                {typeof traceInfo.fallbacks_triggered === 'string'
                  ? traceInfo.fallbacks_triggered
                  : JSON.stringify(traceInfo.fallbacks_triggered)}
              </div>
            </div>
          </div>
        )}

        {/* Output JSON Payload Viewer */}
        <div
          style={{
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-surface-elevated)',
            border: '1px solid var(--border-subtle)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '8px 12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: 'rgba(255, 255, 255, 0.02)',
            }}
          >
            <div
              onClick={() => toggleSection('output')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                cursor: 'pointer',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.6875rem',
                fontWeight: 600,
                color: 'var(--text-muted)',
                textTransform: 'uppercase',
              }}
            >
              {expandedSections.output ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span>Node Output Payload</span>
            </div>

            {outputData && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => handleCopyJSON(outputData)}
                title="Copy JSON"
              >
                {copied ? <Check size={12} color="var(--accent-emerald)" /> : <Copy size={12} />}
                <span style={{ fontSize: '0.6875rem' }}>{copied ? 'Copied' : 'Copy'}</span>
              </button>
            )}
          </div>

          {expandedSections.output && (
            <div
              style={{
                padding: '10px 12px',
                borderTop: '1px solid var(--border-subtle)',
                background: '#070a0f',
                maxHeight: '220px',
                overflowY: 'auto',
              }}
            >
              {outputData ? (
                <pre
                  className="mono"
                  style={{
                    fontSize: '0.75rem',
                    color: '#38bdf8',
                    lineHeight: 1.4,
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                  }}
                >
                  {typeof outputData === 'string'
                    ? outputData
                    : JSON.stringify(outputData, null, 2)}
                </pre>
              ) : (
                <span
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.75rem',
                    color: 'var(--text-dim)',
                  }}
                >
                  // No output available for pending node
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
