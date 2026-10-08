import React from 'react';
import { formatCost, formatMs } from '../api';

// Timeline (Gantt) of an execution. Steps report how long they took, not
// when they started, so the schedule is rebuilt from the plan: a step starts
// when the steps it depends on have finished. Parallel branches therefore
// overlap, and the critical path shows. A repeated step (loop) starts after
// everything that ran before it.

const COLORS = {
  completed: 'var(--status-completed)',
  failed: 'var(--status-failed)',
  skipped: 'var(--status-skipped)',
};

function schedule(graph, log) {
  const preds = {};
  for (const node of graph?.nodes || []) {
    preds[node.id] = new Set(node.dependencies || []);
  }
  for (const edge of graph?.edges || []) {
    if (edge.edge_type !== 'loop') {
      (preds[edge.target] ||= new Set()).add(edge.source);
    }
  }

  const end = {};
  const seen = new Set();
  let horizon = 0;
  const bars = [];
  for (const entry of log || []) {
    if (entry.subgraph_parent) continue; // inner steps are part of their team's bar
    const id = entry.node_id;
    const depsEnd = [...(preds[id] || [])].map((p) => end[p] ?? 0);
    const start = seen.has(id) ? horizon : Math.max(0, ...depsEnd);
    const duration = Number(entry.latency_ms || 0);
    end[id] = start + duration;
    horizon = Math.max(horizon, end[id]);
    seen.add(id);
    bars.push({ id, start, duration, entry });
  }
  return { bars, total: horizon };
}

export default function Timeline({ graph, log }) {
  const { bars, total } = schedule(graph, log);
  if (bars.length === 0) return <div className="empty">Aucune étape exécutée.</div>;
  const scale = total || 1;

  return (
    <div className="gantt">
      {bars.map(({ id, start, duration, entry }, i) => (
        <div className="gantt-row" key={`${id}-${i}`}>
          <div className="gantt-label" title={id}>{id}</div>
          <div className="gantt-track">
            <div
              className="gantt-bar"
              style={{
                left: `${(start / scale) * 100}%`,
                width: `${(duration / scale) * 100}%`,
                background: COLORS[entry.status] || 'var(--status-pending)',
                opacity: entry.status === 'skipped' ? 0.6 : 1,
              }}
              title={`${id} — ${entry.status}\nmodèle : ${entry.model_used || '—'}\n${formatCost(entry.cost)} · ${formatMs(duration)}${entry.failed_calls ? `\n${entry.failed_calls} appel(s) raté(s)` : ''}`}
            />
          </div>
          <div className="gantt-time">{formatMs(duration)}</div>
        </div>
      ))}
      <div className="small muted" style={{ marginTop: 6 }}>
        Durée sur le chemin critique : {formatMs(total)} — reconstruite à partir du plan et des latences de chaque étape.
      </div>
    </div>
  );
}
