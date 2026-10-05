import React, { useEffect, useState } from 'react';
import Navbar from './components/Navbar';
import ExecutionList from './components/ExecutionList';
import ExecutionDetail from './components/ExecutionDetail';
import FlowGraph from './components/FlowGraph';
import NodeInspector from './components/NodeInspector';
import ExecutionCompare from './components/ExecutionCompare';
import TrendsView from './components/TrendsView';
import TaskExecutionModal from './components/TaskExecutionModal';
import ErrorBoundary from './components/ErrorBoundary';

const API_BASE_URL = 'http://localhost:8000';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [healthStatus, setHealthStatus] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [selectedExecId, setSelectedExecId] = useState(null);
  const [selectedExecDetail, setSelectedExecDetail] = useState(null);
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  // Fetch executions list
  const fetchExecutions = () => {
    fetch(`${API_BASE_URL}/executions`)
      .then((res) => res.json())
      .then((data) => {
        setExecutions(data);
        if (data.length > 0 && !selectedExecId) {
          setSelectedExecId(data[0].id);
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error('API fetch error:', err);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetch(`${API_BASE_URL}/health`)
      .then((res) => res.json())
      .then((data) => setHealthStatus(data.status))
      .catch(() => setHealthStatus('disconnected'));

    fetchExecutions();
  }, []);

  // Fetch full detail whenever selectedExecId changes or executions first load
  const activeExecId = selectedExecId || (executions.length > 0 ? executions[0].id : null);

  useEffect(() => {
    if (activeExecId) {
      fetch(`${API_BASE_URL}/executions/${activeExecId}`)
        .then((res) => {
          if (!res.ok) throw new Error('Execution detail not found');
          return res.json();
        })
        .then((data) => {
          setSelectedExecDetail(data);
          if (data?.graph?.nodes?.length > 0 && !selectedGraphNodeId) {
            setSelectedGraphNodeId(data.graph.nodes[0].id);
          }
        })
        .catch((err) => console.error(err));
    }
  }, [activeExecId]);

  const handleSelectExecution = (execId) => {
    setSelectedExecId(execId);
    setActiveTab('detail');
  };

  const handleExecutionCreated = (newExec) => {
    setSelectedExecId(newExec.id);
    fetchExecutions();
    setActiveTab('detail');
  };

  const selectedGraphNodeObj = selectedExecDetail?.graph?.nodes?.find(
    (n) => n.id === selectedGraphNodeId
  );
  const selectedGraphRawOutput =
    selectedExecDetail?.workflow_state?.node_outputs?.[selectedGraphNodeId];

  return (
    <div className="app-container">
      {/* Dev-Tool Top Navbar */}
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        healthStatus={healthStatus}
        onRunClick={() => setIsModalOpen(true)}
      />

      {/* Main Viewport Content */}
      <main className="main-content">
        <ErrorBoundary>
          {loading ? (
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
              // Connecting to CEG Runtime (FastAPI + SQLite)...
            </div>
          ) : (
            <div className="view-transition">
              {/* Tab 1: Dashboard */}
              {activeTab === 'dashboard' && (
                <ExecutionList
                  executions={executions}
                  onSelectExecution={handleSelectExecution}
                  onRunNew={() => setIsModalOpen(true)}
                />
              )}

              {/* Tab 2: Flow Canvas (Dedicated Graph Studio) */}
              {activeTab === 'graph' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  {/* Selector Bar */}
                  <div
                    className="ide-window"
                    style={{
                      padding: '12px 18px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <span
                        style={{
                          fontSize: '0.6875rem',
                          fontFamily: 'var(--font-mono)',
                          color: 'var(--text-muted)',
                          textTransform: 'uppercase',
                        }}
                      >
                        SELECT EXECUTION
                      </span>
                      <select
                        value={activeExecId || ''}
                        onChange={(e) => setSelectedExecId(e.target.value)}
                        className="mono"
                        style={{
                          padding: '6px 12px',
                          borderRadius: 'var(--radius-sm)',
                          background: 'var(--bg-surface-elevated)',
                          border: '1px solid var(--border-default)',
                          color: 'var(--text-primary)',
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

                    {selectedExecDetail && (
                      <span className={`status-pill ${selectedExecDetail.status}`}>
                        <span className="status-pill-dot" />
                        <span>{selectedExecDetail.status}</span>
                      </span>
                    )}
                  </div>

                  {/* Studio Split Canvas */}
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'minmax(0, 1.4fr) minmax(320px, 1fr)',
                      gap: '16px',
                      minHeight: '560px',
                    }}
                  >
                    <FlowGraph
                      graphData={selectedExecDetail?.graph}
                      nodeStatuses={selectedExecDetail?.workflow_state?.node_statuses || {}}
                      selectedNodeId={selectedGraphNodeId}
                      onNodeSelect={setSelectedGraphNodeId}
                      title={`CEG Graph Canvas // ${selectedExecDetail?.scenario_name || 'Idle'}`}
                      height="560px"
                    />

                    <NodeInspector
                      selectedNode={selectedGraphNodeObj}
                      rawOutput={selectedGraphRawOutput}
                    />
                  </div>
                </div>
              )}

              {/* Tab 3: Trace & State (Execution Detail) */}
              {activeTab === 'detail' && (
                <ExecutionDetail
                  executionId={activeExecId}
                  executions={executions}
                  onSelectExecution={setSelectedExecId}
                  onBack={() => setActiveTab('dashboard')}
                  onRunNew={() => setIsModalOpen(true)}
                  apiBaseUrl={API_BASE_URL}
                />
              )}

              {/* Tab 4: Diff Compare */}
              {activeTab === 'compare' && (
                <ExecutionCompare
                  executions={executions}
                  apiBaseUrl={API_BASE_URL}
                />
              )}

              {/* Tab 5: Analytics & Trends */}
              {activeTab === 'trends' && (
                <TrendsView
                  executions={executions}
                  apiBaseUrl={API_BASE_URL}
                />
              )}
            </div>
          )}
        </ErrorBoundary>
      </main>

      {/* Modal Dialog */}
      <TaskExecutionModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onExecutionCreated={handleExecutionCreated}
        apiBaseUrl={API_BASE_URL}
      />
    </div>
  );
}
