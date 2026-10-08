import React from 'react';
import { navigate } from '../router';
import { formatCost, formatDate, formatMs } from '../api';

// Grid view, like Airflow's: one column per execution (oldest first), one row
// per step, one coloured cell per (execution, step). The bar above each column
// is the run's latency, coloured by the run's final status.

const STATUS_LABELS = {
  completed: 'terminé',
  failed: 'échec',
  skipped: 'sauté',
  awaiting_approval: 'en attente d\'approbation',
  running: 'en cours',
};

export function GridLegend() {
  return (
    <div className="legend">
      {Object.entries(STATUS_LABELS).map(([status, label]) => (
        <span key={status}>
          <i className={`cell s-${status}`} /> {label}
        </span>
      ))}
      <span>
        <i className="cell" /> pas exécuté
      </span>
    </div>
  );
}

export default function RunGrid({ grid }) {
  if (!grid || grid.runs.length === 0) {
    return <div className="empty">Ce workflow n'a pas encore été exécuté.</div>;
  }
  const maxLatency = Math.max(...grid.runs.map((r) => r.total_latency_ms), 1);

  return (
    <div className="grid-wrap">
      <table className="run-grid">
        <thead>
          <tr>
            <th />
            {grid.runs.map((run) => (
              <th key={run.id} className="run-head">
                <i
                  className={`run-bar s-${run.status}`}
                  style={{ height: `${Math.max(4, (run.total_latency_ms / maxLatency) * 42)}px` }}
                  title={`${run.id}\n${formatDate(run.started_at)} — ${run.status}\n${formatCost(run.total_cost)} · ${formatMs(run.total_latency_ms)}\n${run.backend || ''} · ${run.optimizer || ''}`}
                  onClick={() => navigate(`/runs/${run.id}`)}
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((row) => (
            <tr key={row.node_id}>
              <th className="step">{row.node_id}</th>
              {row.statuses.map((status, i) => {
                const run = grid.runs[i];
                return (
                  <td key={run.id}>
                    <i
                      className={`cell ${status ? `s-${status}` : ''}`}
                      title={`${row.node_id} — ${status ? STATUS_LABELS[status] || status : 'pas exécuté'}\n${run.id} · ${formatDate(run.started_at)}`}
                      onClick={() => navigate(`/runs/${run.id}`)}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
