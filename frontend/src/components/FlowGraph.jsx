import React, { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import {
  CheckCircle2,
  AlertCircle,
  Clock,
  Zap,
  RotateCcw,
  UserCheck,
  Layers,
} from 'lucide-react';

// ── Status Styling Helper ───────────────────────────────────────────────────
const getStatusStyles = (status) => {
  switch (status) {
    case 'completed':
      return {
        border: '1px solid #10b981',
        bg: 'rgba(16, 185, 129, 0.12)',
        glow: '0 0 12px rgba(16, 185, 129, 0.25)',
        text: '#34d399',
        icon: CheckCircle2,
      };
    case 'running':
      return {
        border: '1px solid #0ea5e9',
        bg: 'rgba(14, 165, 233, 0.18)',
        glow: '0 0 16px rgba(14, 165, 233, 0.4)',
        text: '#38bdf8',
        icon: Zap,
        pulse: true,
      };
    case 'failed':
      return {
        border: '1px solid #ef4444',
        bg: 'rgba(239, 68, 68, 0.12)',
        glow: '0 0 12px rgba(239, 68, 68, 0.25)',
        text: '#f87171',
        icon: AlertCircle,
      };
    case 'skipped':
      return {
        border: '1px solid #f59e0b',
        bg: 'rgba(245, 158, 11, 0.12)',
        glow: '0 0 12px rgba(245, 158, 11, 0.25)',
        text: '#fbbf24',
        icon: RotateCcw,
      };
    case 'pending':
    default:
      return {
        border: '1px solid #475569',
        bg: 'rgba(51, 65, 85, 0.15)',
        glow: 'none',
        text: '#94a3b8',
        icon: Clock,
      };
  }
};

// ── Custom Node Component (LangGraph Studio style) ──────────────────────────
export function CEGNodeComponent({ data, selected }) {
  const status = data?.status || 'pending';
  const style = getStatusStyles(status);
  const StatusIcon = style.icon;
  const isHITL = Boolean(data?.interrupt_before || data?.interrupt_after);
  const isSubgraph = Boolean(data?.subgraph);

  return (
    <div
      style={{
        padding: '10px 14px',
        borderRadius: '9px',
        background: isSubgraph ? 'rgba(14, 165, 233, 0.12)' : style.bg,
        border: selected
          ? '2px solid #38bdf8'
          : isSubgraph
          ? '1px dashed #38bdf8'
          : style.border,
        boxShadow: selected ? '0 0 20px rgba(14, 165, 233, 0.5)' : style.glow,
        minWidth: '170px',
        maxWidth: '240px',
        backdropFilter: 'blur(8px)',
        cursor: 'pointer',
        transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
        position: 'relative',
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{
          background: '#475569',
          width: '8px',
          height: '8px',
          border: '2px solid #0f172a',
        }}
      />

      {/* Header with status icon & tier badge */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
          gap: '6px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
          <StatusIcon
            size={13}
            color={style.text}
            className={style.pulse ? 'animate-pulse' : ''}
          />
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.65rem',
              color: style.text,
              textTransform: 'uppercase',
              fontWeight: 600,
              letterSpacing: '0.04em',
            }}
          >
            {status}
          </span>
        </div>

        {isSubgraph && (
          <span
            title="Nested Subgraph Node"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.6rem',
              padding: '1px 5px',
              borderRadius: '4px',
              background: 'rgba(56, 189, 248, 0.15)',
              border: '1px solid rgba(56, 189, 248, 0.4)',
              color: '#38bdf8',
              display: 'flex',
              alignItems: 'center',
              gap: '3px',
            }}
          >
            <Layers size={10} /> SUBGRAPH
          </span>
        )}

        {isHITL && !isSubgraph && (
          <span
            title="Human-in-the-Loop Interruption Node"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.6rem',
              padding: '1px 5px',
              borderRadius: '4px',
              background: 'rgba(168, 85, 247, 0.2)',
              border: '1px solid rgba(168, 85, 247, 0.5)',
              color: '#c084fc',
              display: 'flex',
              alignItems: 'center',
              gap: '3px',
            }}
          >
            <UserCheck size={10} /> HITL
          </span>
        )}

        {data?.model_tier_hint && !isHITL && !isSubgraph && (
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.6rem',
              padding: '1px 5px',
              borderRadius: '4px',
              background: 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
            }}
          >
            {data.model_tier_hint}
          </span>
        )}
      </div>

      {/* Node ID in Monospace */}
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontWeight: 600,
          fontSize: '0.8125rem',
          color: '#f8fafc',
          letterSpacing: '-0.01em',
          wordBreak: 'break-word',
          lineHeight: 1.3,
        }}
      >
        {data?.label || data?.id || 'node'}
      </div>

      {/* Metric chips if available */}
      {(Number(data?.cost) > 0 || Number(data?.latency_ms) > 0) && (
        <div
          style={{
            marginTop: '8px',
            paddingTop: '6px',
            borderTop: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)',
            fontSize: '0.65rem',
            color: 'var(--text-muted)',
          }}
        >
          {Number(data?.cost) > 0 && <span>${Number(data.cost).toFixed(4)}</span>}
          {Number(data?.latency_ms) > 0 && <span>{Number(data.latency_ms).toFixed(0)}ms</span>}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          background: '#0ea5e9',
          width: '8px',
          height: '8px',
          border: '2px solid #0f172a',
        }}
      />
    </div>
  );
}

const nodeTypes = {
  cegNode: CEGNodeComponent,
};

// ── Automated Dagre Layout Engine ───────────────────────────────────────────
const getLayoutedElements = (nodes, edges, direction = 'TB') => {
  if (!nodes || nodes.length === 0) {
    return { nodes: [], edges: edges || [] };
  }

  try {
    const GraphConstructor = dagre.graphlib ? dagre.graphlib.Graph : dagre.Graph;
    const dagreGraph = new GraphConstructor();
    dagreGraph.setDefaultEdgeLabel(() => ({}));

    const isHorizontal = direction === 'LR';
    dagreGraph.setGraph({
      rankdir: direction,
      nodesep: 45,
      ranksep: 60,
      marginx: 30,
      marginy: 30,
    });

    const nodeIds = new Set(nodes.map((n) => n.id));

    nodes.forEach((node) => {
      dagreGraph.setNode(node.id, { width: 190, height: 80 });
    });

    (edges || []).forEach((edge) => {
      if (nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
        dagreGraph.setEdge(edge.source, edge.target);
      }
    });

    dagre.layout(dagreGraph);

    const layoutedNodes = nodes.map((node) => {
      const nodeWithPosition = dagreGraph.node(node.id) || { x: 150, y: 100 };
      return {
        ...node,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: {
          x: (nodeWithPosition.x || 150) - 95,
          y: (nodeWithPosition.y || 100) - 40,
        },
      };
    });

    return { nodes: layoutedNodes, edges: edges || [] };
  } catch (err) {
    console.warn('Dagre layout fallback:', err);
    const fallbackNodes = nodes.map((node, idx) => ({
      ...node,
      position: { x: 120, y: idx * 110 + 40 },
    }));
    return { nodes: fallbackNodes, edges: edges || [] };
  }
};

const EMPTY_OBJ = {};
const EMPTY_ARR = [];

// ── Inner Flow Component ────────────────────────────────────────────────────
function FlowInner({
  graphData,
  nodeStatuses = EMPTY_OBJ,
  selectedNodeId,
  onNodeSelect,
  traceData = EMPTY_ARR,
  title,
  height,
  showMiniMap,
}) {
  const { nodes: initialNodes = EMPTY_ARR, edges: initialEdges = EMPTY_ARR } = useMemo(() => {
    if (!graphData || !graphData.nodes || !Array.isArray(graphData.nodes)) {
      return { nodes: EMPTY_ARR, edges: EMPTY_ARR };
    }

    const traceMap = {};
    if (Array.isArray(traceData)) {
      traceData.forEach((t) => {
        if (t && t.node_id) {
          traceMap[t.node_id] = t;
        }
      });
    }

    const rawNodes = graphData.nodes.map((node) => {
      const status =
        nodeStatuses[node.id] ||
        (traceMap[node.id] ? traceMap[node.id].status : 'pending');
      const trace = traceMap[node.id];

      return {
        id: node.id,
        type: 'cegNode',
        data: {
          id: node.id,
          label: node.id,
          objective: node.objective,
          status,
          model_tier_hint: node.model_tier_hint,
          interrupt_before: node.interrupt_before,
          interrupt_after: node.interrupt_after,
          subgraph: node.subgraph,
          cost: trace ? trace.cost : node.accumulated_cost || 0,
          latency_ms: trace ? trace.latency_ms : node.latency_ms || 0,
        },
        position: { x: 0, y: 0 },
      };
    });

    const rawEdges = (graphData.edges || []).map((edge, idx) => {
      const isLoop = edge.edge_type === 'loop';
      const isConditional = edge.edge_type === 'conditional';
      const isParallel = edge.edge_type === 'parallel';

      let edgeColor = '#475569';
      let edgeStyle = { strokeWidth: 1.5 };
      let label = null;

      if (isConditional) {
        label = edge.condition ? `if: ${edge.condition}` : 'cond';
        edgeColor = '#f59e0b';
      } else if (isLoop) {
        label = `loop (max ${edge.loop_max_iterations || '∞'})`;
        edgeColor = '#a855f7';
        edgeStyle.strokeDasharray = '4 4';
      } else if (isParallel) {
        edgeColor = '#0ea5e9';
      }

      const sourceStatus = nodeStatuses[edge.source];
      if (sourceStatus === 'completed') {
        edgeColor = '#0ea5e9';
        edgeStyle.strokeWidth = 2;
      }

      return {
        id: `e-${edge.source}-${edge.target}-${idx}`,
        source: edge.source,
        target: edge.target,
        animated: isLoop || sourceStatus === 'running',
        label,
        labelStyle: {
          fill: '#94a3b8',
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          fontWeight: 500,
        },
        labelBgStyle: {
          fill: '#0f172a',
          fillOpacity: 0.85,
        },
        style: {
          ...edgeStyle,
          stroke: edgeColor,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edgeColor,
          width: 16,
          height: 16,
        },
      };
    });

    return getLayoutedElements(rawNodes, rawEdges, 'TB') || { nodes: EMPTY_ARR, edges: EMPTY_ARR };
  }, [graphData, nodeStatuses, traceData]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Use ref signature check to prevent infinite re-render cycles
  const prevSignatureRef = React.useRef('');
  React.useEffect(() => {
    // Node ids and edges identify the graph: graphs have no id of their own,
    // and two different graphs can have the same number of nodes.
    const shape = JSON.stringify([
      (graphData?.nodes || []).map((n) => n.id),
      (graphData?.edges || []).map((e) => [e.source, e.target, e.edge_type]),
    ]);
    const signature = `${shape}_${JSON.stringify(nodeStatuses)}`;
    if (prevSignatureRef.current !== signature) {
      prevSignatureRef.current = signature;
      setNodes(initialNodes);
      setEdges(initialEdges);
    }
  }, [graphData, nodeStatuses, initialNodes, initialEdges, setNodes, setEdges]);

  const displayNodes = useMemo(() => {
    return (nodes || []).map((n) => ({
      ...n,
      selected: n.id === selectedNodeId,
    }));
  }, [nodes, selectedNodeId]);

  const handleNodeClick = useCallback(
    (_, node) => {
      if (onNodeSelect && node) {
        onNodeSelect(node.id);
      }
    },
    [onNodeSelect]
  );

  return (
    <div className="ide-window" style={{ width: '100%' }}>
      {/* IDE macOS Window Header */}
      <div className="ide-header">
        <div className="ide-mac-dots">
          <div className="ide-dot ide-dot-red" />
          <div className="ide-dot ide-dot-yellow" />
          <div className="ide-dot ide-dot-green" />
        </div>

        <div className="ide-title">
          <Layers size={13} color="var(--accent-primary)" />
          <span>{title}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.6875rem',
              color: 'var(--text-dim)',
            }}
          >
            {nodes.length} nodes • {edges.length} edges
          </span>
        </div>
      </div>

      {/* Canvas Area */}
      <div style={{ height, background: 'var(--ide-canvas-bg)', position: 'relative' }}>
        {nodes.length === 0 ? (
          <div
            style={{
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-muted)',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.875rem',
            }}
          >
            No graph nodes loaded
          </div>
        ) : (
          <ReactFlow
            nodes={displayNodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            onNodeClick={handleNodeClick}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            minZoom={0.2}
            maxZoom={2}
          >
            <Background color="#30363d" gap={18} size={1} />
            <Controls
              style={{
                background: 'var(--bg-surface-elevated)',
                border: '1px solid var(--border-default)',
                borderRadius: '6px',
                fill: '#94a3b8',
              }}
            />
            {showMiniMap && (
              <MiniMap
                nodeColor={(n) => {
                  const st = n.data?.status;
                  if (st === 'completed') return '#10b981';
                  if (st === 'running') return '#0ea5e9';
                  if (st === 'failed') return '#ef4444';
                  if (st === 'skipped') return '#f59e0b';
                  return '#475569';
                }}
                maskColor="rgba(11, 15, 23, 0.75)"
                style={{
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-default)',
                  borderRadius: '6px',
                  height: 90,
                  width: 130,
                }}
              />
            )}
          </ReactFlow>
        )}
      </div>
    </div>
  );
}

// ── Export FlowGraph wrapped in ReactFlowProvider ───────────────────────────
export default function FlowGraph(props) {
  return (
    <ReactFlowProvider>
      <FlowInner {...props} />
    </ReactFlowProvider>
  );
}
