import React from 'react';
import {
  Layers,
  GitCommit,
  Activity,
  GitPullRequest,
  BarChart2,
  Play,
  Terminal,
  Cpu,
} from 'lucide-react';

export default function Navbar({
  activeTab,
  setActiveTab,
  healthStatus,
  onRunClick,
}) {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: Layers },
    { id: 'graph', label: 'Flow Canvas', icon: GitCommit },
    { id: 'detail', label: 'Trace & State', icon: Activity },
    { id: 'compare', label: 'Diff Compare', icon: GitPullRequest },
    { id: 'trends', label: 'Analytics', icon: BarChart2 },
  ];

  return (
    <header
      style={{
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border-default)',
        padding: '0 24px',
        height: '52px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}
    >
      {/* ── Left Brand ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div
          style={{
            width: '28px',
            height: '28px',
            borderRadius: '6px',
            background: 'var(--accent-primary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontWeight: 800,
            fontSize: '0.8125rem',
            fontFamily: 'var(--font-mono)',
            boxShadow: '0 0 14px rgba(14, 165, 233, 0.4)',
          }}
        >
          C
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
          <span
            style={{
              fontWeight: 700,
              fontSize: '0.9375rem',
              letterSpacing: '-0.02em',
              color: 'var(--text-primary)',
            }}
          >
            CEG Studio
          </span>
          <span
            className="mono"
            style={{
              fontSize: '0.6875rem',
              color: 'var(--text-dim)',
            }}
          >
            LangGraph Runtime
          </span>
        </div>

        {/* API status pill */}
        <div
          className="mono"
          style={{
            marginLeft: '8px',
            fontSize: '0.625rem',
            padding: '2px 6px',
            borderRadius: '4px',
            background:
              healthStatus === 'ok'
                ? 'var(--status-completed-bg)'
                : 'var(--status-failed-bg)',
            color:
              healthStatus === 'ok'
                ? 'var(--status-completed)'
                : 'var(--status-failed)',
            border:
              healthStatus === 'ok'
                ? '1px solid var(--status-completed-border)'
                : '1px solid var(--status-failed-border)',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          <span
            style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              background:
                healthStatus === 'ok'
                  ? 'var(--status-completed)'
                  : 'var(--status-failed)',
            }}
          />
          API {healthStatus || 'checking...'}
        </div>
      </div>

      {/* ── Center Tabs ── */}
      <nav
        style={{
          display: 'flex',
          gap: '2px',
          background: 'var(--bg-app)',
          padding: '3px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-default)',
        }}
      >
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '5px 12px',
                fontSize: '0.75rem',
                fontFamily: 'var(--font-sans)',
                fontWeight: isActive ? 600 : 500,
                color: isActive ? 'var(--text-primary)' : 'var(--text-muted)',
                background: isActive ? 'var(--bg-surface-elevated)' : 'transparent',
                border: '1px solid',
                borderColor: isActive ? 'var(--border-default)' : 'transparent',
                borderRadius: '4px',
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
              }}
            >
              <Icon
                size={13}
                color={isActive ? 'var(--accent-primary)' : 'currentColor'}
              />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* ── Right Action ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <button className="btn btn-primary" onClick={onRunClick}>
          <Play size={12} fill="currentColor" />
          <span>Execute Task</span>
        </button>
      </div>
    </header>
  );
}
