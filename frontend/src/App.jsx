import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import {
  Workflow,
  ListChecks,
  UserCheck,
  GitCompare,
  BarChart2,
  Cpu,
  Play,
  Sun,
  Moon,
  BookOpen,
} from 'lucide-react';
import './studio.css';
import ErrorBoundary from './components/ErrorBoundary';
import TaskExecutionModal from './components/TaskExecutionModal';
import WorkflowsPage from './pages/WorkflowsPage';
import ApprovalsPage from './pages/ApprovalsPage';
import RunsPage from './pages/RunsPage';
import ModelsPage from './pages/ModelsPage';
import { API_BASE_URL, apiGet } from './api';
import { href, navigate, useRoute } from './router';

// Loaded on demand: the graph (React Flow) and charts (recharts) are heavy.
const WorkflowPage = lazy(() => import('./pages/WorkflowPage'));
const RunPage = lazy(() => import('./pages/RunPage'));
const ExecutionCompare = lazy(() => import('./components/ExecutionCompare'));
const TrendsView = lazy(() => import('./components/TrendsView'));

const NAV = [
  { section: 'Plateforme' },
  { path: '/workflows', label: 'Workflows', icon: Workflow },
  { path: '/runs', label: 'Exécutions', icon: ListChecks },
  { path: '/approvals', label: 'À approuver', icon: UserCheck, badge: 'approvals' },
  { section: 'Analyse' },
  { path: '/compare', label: 'Comparer', icon: GitCompare },
  { path: '/trends', label: 'Tendances', icon: BarChart2 },
  { path: '/models', label: 'Modèles & optimiseur', icon: Cpu },
];

// The user's choice if any, otherwise the system's light/dark preference.
function readTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem('ceg-theme');
  } catch {
    // storage unavailable
  }
  if (stored) return stored;
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export default function App() {
  const { segments } = useRoute();
  const [executions, setExecutions] = useState([]);
  const [health, setHealth] = useState(null);
  const [theme, setTheme] = useState(readTheme);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalTaskId, setModalTaskId] = useState(null);

  const loadExecutions = useCallback(() => {
    apiGet('/executions').then(setExecutions).catch(() => setExecutions([]));
  }, []);

  useEffect(() => {
    loadExecutions();
    apiGet('/health').then(setHealth).catch(() => setHealth({ status: 'down' }));
  }, [loadExecutions]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('ceg-theme', theme);
    } catch {
      // storage unavailable: the theme just isn't remembered
    }
  }, [theme]);

  const openRun = (taskId = null) => {
    setModalTaskId(taskId);
    setModalOpen(true);
  };

  const onExecutionCreated = (execution) => {
    loadExecutions();
    navigate(`/runs/${execution.id}`);
  };

  const [section = 'workflows', id, tab] = segments;
  const pendingApprovals = executions.filter((e) => e.status === 'awaiting_approval').length;

  // ── Breadcrumbs ──
  const crumbs = [];
  const sectionLabels = {
    workflows: 'Workflows',
    runs: 'Exécutions',
    approvals: 'À approuver',
    compare: 'Comparer',
    trends: 'Tendances',
    models: 'Modèles & optimiseur',
  };
  crumbs.push({ label: sectionLabels[section] || section, path: `/${section}` });
  if (id) crumbs.push({ label: id, path: `/${section}/${id}` });

  // ── Page ──
  let page;
  if (section === 'workflows' && id) {
    page = <WorkflowPage workflowId={id} tab={tab} onRun={openRun} />;
  } else if (section === 'workflows') {
    page = <WorkflowsPage onRun={openRun} />;
  } else if (section === 'runs' && id) {
    page = <RunPage runId={id} tab={tab} onRunNew={() => openRun()} />;
  } else if (section === 'runs') {
    page = <RunsPage executions={executions} onRefresh={loadExecutions} />;
  } else if (section === 'approvals') {
    page = <ApprovalsPage onChanged={loadExecutions} />;
  } else if (section === 'compare') {
    page = <ExecutionCompare executions={executions} apiBaseUrl={API_BASE_URL} />;
  } else if (section === 'trends') {
    page = <TrendsView executions={executions} apiBaseUrl={API_BASE_URL} />;
  } else if (section === 'models') {
    page = <ModelsPage />;
  } else {
    page = <div className="empty">Page introuvable. <a href={href('/workflows')}>Revenir aux workflows</a></div>;
  }

  const healthState = health === null ? '' : health.status === 'ok' ? 'ok' : 'down';

  return (
    <div className="studio">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-logo">CEG</div>
          <div>
            <div className="sidebar-title">CEG Studio</div>
            <div className="sidebar-subtitle">Cognitive Execution Graph</div>
          </div>
        </div>

        {NAV.map((item) =>
          item.section ? (
            <div className="sidebar-section" key={item.section}>{item.section}</div>
          ) : (
            <a
              key={item.path}
              href={href(item.path)}
              className={`nav-link ${`/${section}` === item.path ? 'active' : ''}`}
            >
              <item.icon size={16} />
              {item.label}
              {item.badge === 'approvals' && pendingApprovals > 0 && (
                <span className="nav-badge">{pendingApprovals}</span>
              )}
            </a>
          )
        )}

        <div className="sidebar-footer">
          <div className="health">
            <span className={`health-dot ${healthState}`} />
            API {healthState === 'ok' ? 'connectée' : healthState === 'down' ? 'injoignable' : '…'}
            {health?.version && <span className="dim mono">v{health.version}</span>}
          </div>
          <a className="nav-link" href={`${API_BASE_URL}/docs`} target="_blank" rel="noreferrer">
            <BookOpen size={15} /> Documentation de l'API
          </a>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          >
            {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
            Thème {theme === 'dark' ? 'clair' : 'sombre'}
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <nav className="breadcrumbs">
            <a href={href('/workflows')}>CEG</a>
            {crumbs.map((c, i) => (
              <React.Fragment key={c.path}>
                <span className="dim">/</span>
                {i === crumbs.length - 1 ? (
                  <span className="current">{c.label}</span>
                ) : (
                  <a href={href(c.path)}>{c.label}</a>
                )}
              </React.Fragment>
            ))}
          </nav>
          <div className="topbar-actions">
            <button className="btn btn-primary" onClick={() => openRun(section === 'workflows' ? id : null)}>
              <Play size={14} /> Exécuter un workflow
            </button>
          </div>
        </header>

        <main className="content">
          <ErrorBoundary>
            <Suspense fallback={<div className="empty">Chargement…</div>}>{page}</Suspense>
          </ErrorBoundary>
        </main>
      </div>

      <TaskExecutionModal
        isOpen={modalOpen}
        initialTaskId={modalTaskId}
        onClose={() => setModalOpen(false)}
        onExecutionCreated={onExecutionCreated}
        apiBaseUrl={API_BASE_URL}
      />
    </div>
  );
}
