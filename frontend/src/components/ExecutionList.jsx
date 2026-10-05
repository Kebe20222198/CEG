import React, { useState } from 'react';
import {
  Search,
  Filter,
  ArrowUpRight,
  PlayCircle,
  Activity,
  Layers,
  Zap,
  Clock,
  DollarSign,
  TrendingUp,
} from 'lucide-react';

export default function ExecutionList({ executions, onSelectExecution, onRunNew }) {
  const [statusFilter, setStatusFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredExecutions = executions.filter((exec) => {
    const matchesStatus = statusFilter === 'all' || exec.status === statusFilter;
    const matchesSearch =
      exec.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      exec.scenario_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (exec.task_id && exec.task_id.toLowerCase().includes(searchQuery.toLowerCase()));
    return matchesStatus && matchesSearch;
  });

  // Calculate high-density summary statistics
  const totalCount = executions.length;
  const completedCount = executions.filter((e) => e.status === 'completed').length;
  const successRate = totalCount > 0 ? ((completedCount / totalCount) * 100).toFixed(1) : '0';
  const totalCost = executions.reduce((acc, curr) => acc + (curr.total_cost || 0), 0);
  const avgLatency =
    totalCount > 0
      ? (executions.reduce((acc, curr) => acc + (curr.total_latency_ms || 0), 0) / totalCount).toFixed(0)
      : '0';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* ── Top Metric Cards (Dev-Tool Stat Bar) ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
        }}
      >
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 18px',
          }}
        >
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              marginBottom: '6px',
            }}
          >
            TOTAL EXECUTIONS
          </div>
          <div className="mono" style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text-primary)' }}>
            {totalCount}
          </div>
        </div>

        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 18px',
          }}
        >
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              marginBottom: '6px',
            }}
          >
            SUCCESS RATE
          </div>
          <div className="mono" style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--status-completed)' }}>
            {successRate}%
          </div>
        </div>

        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 18px',
          }}
        >
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              marginBottom: '6px',
            }}
          >
            CUMULATIVE COST
          </div>
          <div className="mono" style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--accent-primary)' }}>
            ${totalCost.toFixed(4)}
          </div>
        </div>

        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            padding: '14px 18px',
          }}
        >
          <div
            style={{
              fontSize: '0.6875rem',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              marginBottom: '6px',
            }}
          >
            AVG LATENCY
          </div>
          <div className="mono" style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--accent-violet)' }}>
            {avgLatency} <span style={{ fontSize: '0.9rem', fontWeight: 400 }}>ms</span>
          </div>
        </div>
      </div>

      {/* ── Table Container (IDE Window Style) ── */}
      <div className="ide-window">
        {/* Filter and Search Bar */}
        <div
          style={{
            padding: '12px 16px',
            background: 'var(--bg-surface-elevated)',
            borderBottom: '1px solid var(--border-default)',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '12px',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          {/* Search Box */}
          <div style={{ position: 'relative', minWidth: '280px', flex: '1', maxWidth: '400px' }}>
            <Search
              size={14}
              style={{
                position: 'absolute',
                left: '10px',
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--text-muted)',
              }}
            />
            <input
              type="text"
              placeholder="Search execution ID, scenario, task..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                width: '100%',
                padding: '6px 10px 6px 32px',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-default)',
                color: 'var(--text-primary)',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.8125rem',
                outline: 'none',
              }}
            />
          </div>

          {/* Status Filter Tabs */}
          <div
            style={{
              display: 'flex',
              gap: '4px',
              background: 'var(--bg-surface)',
              padding: '3px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
            }}
          >
            {['all', 'completed', 'failed', 'running', 'skipped'].map((st) => {
              const active = statusFilter === st;
              return (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  style={{
                    padding: '4px 10px',
                    borderRadius: '4px',
                    fontSize: '0.6875rem',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.03em',
                    background: active ? 'var(--bg-surface-active)' : 'transparent',
                    color: active ? 'var(--text-primary)' : 'var(--text-muted)',
                    border: 'none',
                    cursor: 'pointer',
                    transition: 'all var(--transition-fast)',
                  }}
                >
                  {st}
                </button>
              );
            })}
          </div>
        </div>

        {/* Dense Table */}
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              textAlign: 'left',
              fontSize: '0.8125rem',
            }}
          >
            <thead>
              <tr
                style={{
                  background: 'rgba(255, 255, 255, 0.02)',
                  borderBottom: '1px solid var(--border-default)',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.6875rem',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                <th style={{ padding: '10px 16px' }}>Execution ID</th>
                <th style={{ padding: '10px 16px' }}>Scenario</th>
                <th style={{ padding: '10px 16px' }}>Status</th>
                <th style={{ padding: '10px 16px' }}>Cost</th>
                <th style={{ padding: '10px 16px' }}>Latency</th>
                <th style={{ padding: '10px 16px' }}>Timestamp</th>
                <th style={{ padding: '10px 16px', textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredExecutions.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    style={{
                      padding: '40px 16px',
                      textAlign: 'center',
                      color: 'var(--text-muted)',
                      fontFamily: 'var(--font-mono)',
                    }}
                  >
                    // No executions matched the selected filter
                  </td>
                </tr>
              ) : (
                filteredExecutions.map((exec) => (
                  <tr
                    key={exec.id}
                    onClick={() => onSelectExecution(exec.id)}
                    style={{
                      borderBottom: '1px solid var(--border-subtle)',
                      cursor: 'pointer',
                      transition: 'background var(--transition-fast)',
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = 'var(--bg-surface-elevated)')
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.background = 'transparent')
                    }
                  >
                    <td style={{ padding: '12px 16px' }}>
                      <span
                        className="mono"
                        style={{
                          fontWeight: 600,
                          color: 'var(--accent-primary)',
                        }}
                      >
                        {exec.id}
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px' }}>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '0.75rem',
                          color: 'var(--text-secondary)',
                          background: 'rgba(255, 255, 255, 0.04)',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          border: '1px solid var(--border-muted)',
                        }}
                      >
                        {exec.scenario_name}
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px' }}>
                      <span className={`status-pill ${exec.status}`}>
                        <span className="status-pill-dot" />
                        <span>{exec.status}</span>
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px' }}>
                      <span className="mono" style={{ color: 'var(--text-primary)' }}>
                        ${Number(exec.total_cost || 0).toFixed(4)}
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px' }}>
                      <span className="mono" style={{ color: 'var(--text-muted)' }}>
                        {Number(exec.total_latency_ms || 0).toFixed(0)} ms
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px' }}>
                      <span
                        className="mono"
                        style={{
                          fontSize: '0.75rem',
                          color: 'var(--text-dim)',
                        }}
                      >
                        {exec.started_at
                          ? new Date(exec.started_at).toLocaleTimeString()
                          : '--:--:--'}
                      </span>
                    </td>

                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectExecution(exec.id);
                        }}
                      >
                        <span>Inspect</span>
                        <ArrowUpRight size={12} />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
