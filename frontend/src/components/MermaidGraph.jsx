import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  securityLevel: 'loose',
  flowchart: {
    useMaxWidth: true,
    htmlLabels: true,
    curve: 'basis',
  },
});

export default function MermaidGraph({ graphData, nodeStatuses = {} }) {
  const containerRef = useRef(null);
  const [svgContent, setSvgContent] = useState('');
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!graphData || !graphData.nodes) {
      setSvgContent('');
      return;
    }

    try {
      // Build Mermaid flowchart definition string
      let mermaidDef = 'graph TD;\n';

      // Define Node styles
      mermaidDef += `  classDef completedNode fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#d1fae5,font-weight:600;\n`;
      mermaidDef += `  classDef failedNode fill:#7f1d1d,stroke:#ef4444,stroke-width:2px,color:#fee2e2,font-weight:600;\n`;
      mermaidDef += `  classDef runningNode fill:#1e3a8a,stroke:#3b82f6,stroke-width:2px,color:#dbeafe,font-weight:600;\n`;
      mermaidDef += `  classDef skippedNode fill:#78350f,stroke:#f59e0b,stroke-width:2px,color:#fef3c7,font-weight:600;\n`;
      mermaidDef += `  classDef pendingNode fill:#1f2937,stroke:#6b7280,stroke-width:2px,color:#e5e7eb,font-weight:600;\n`;

      // 1. Declare nodes
      graphData.nodes.forEach((node) => {
        const status = nodeStatuses[node.id] || 'pending';
        const label = `${node.id}<br/><small>(${node.model_tier_hint || 'fast'})</small>`;
        mermaidDef += `  ${node.id}["${label}"]:::${status}Node\n`;
      });

      // 2. Declare edges
      if (graphData.edges && graphData.edges.length > 0) {
        graphData.edges.forEach((edge) => {
          if (edge.edge_type === 'conditional' || edge.condition) {
            mermaidDef += `  ${edge.source} -- "${edge.condition || 'conditional'}" --> ${edge.target}\n`;
          } else {
            mermaidDef += `  ${edge.source} --> ${edge.target}\n`;
          }
        });
      }

      // Render mermaid chart to SVG string
      const uniqueId = `mermaid_${Math.random().toString(36).substring(2, 9)}`;
      mermaid.render(uniqueId, mermaidDef).then((result) => {
        setSvgContent(result.svg);
        setError(null);
      }).catch((err) => {
        console.error('Mermaid render error:', err);
        setError('Erreur lors du rendu du graphe Mermaid.');
      });

    } catch (err) {
      console.error(err);
      setError('Impossible de générer la définition du graphe.');
    }
  }, [graphData, nodeStatuses]);

  if (error) {
    return <div style={{ padding: '20px', color: '#f87171', textAlign: 'center' }}>{error}</div>;
  }

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        padding: '24px',
        overflowX: 'auto',
      }}
      dangerouslySetInnerHTML={{ __html: svgContent }}
    />
  );
}
