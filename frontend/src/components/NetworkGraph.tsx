// Fraud network view (Cytoscape). Built from evidence snapshots, so every edge links back to evidence.
import cytoscape, { type Core } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Subgraph } from "../types";

const LEVEL_COLOR: Record<string, string> = {
  observed_relationship: "#2563eb", inferred_pattern: "#d97706", confirmed_outcome: "#7c3aed",
  verified_fact: "#059669", hypothesis: "#6b7280",
};

export function NetworkGraph({ graph, onSelectRef }: { graph: Subgraph; onSelectRef: (ref: string) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const [showBenign, setShowBenign] = useState(false);
  const shown = useMemo(() => {
    // Benign-context links (households, carrier NAT, public wifi) stay in the evidence but are hidden by default.
    const edges = showBenign ? graph.edges : graph.edges.filter((e) => !e.properties.benign_context);
    const used = new Set(edges.flatMap((e) => [e.source, e.target]));
    const nodes = graph.nodes.filter((n) => used.has(n.id) || n.properties.subject);
    return { nodes, edges, hidden: graph.edges.length - edges.length };
  }, [graph, showBenign]);
  useEffect(() => {
    if (!el.current) return;
    cy.current = cytoscape({
      container: el.current,
      elements: [
        ...shown.nodes.map((n) => ({ data: { id: n.id, label: String(n.properties.name ?? n.id), type: n.label, ...n.properties } })),
        ...shown.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, type: e.type, ...e.properties } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 9, "text-valign": "bottom", "text-margin-y": 3, width: 18, height: 18, "background-color": "#94a3b8" } },
        { selector: 'node[type = "Account"]', style: { "background-color": "#0f172a", shape: "round-rectangle" } },
        { selector: "node[?subject]", style: { "background-color": "#dc2626", width: 28, height: 28, "font-weight": "bold" } },
        { selector: 'node[type = "Device"]', style: { "background-color": "#2563eb", shape: "diamond" } },
        { selector: 'node[type = "IPAddress"]', style: { "background-color": "#0891b2", shape: "hexagon" } },
        { selector: 'node[type = "FraudCase"]', style: { "background-color": "#7c3aed", shape: "star", width: 24, height: 24 } },
        { selector: "node[benign_context]", style: { "background-opacity": 0.35, "border-width": 2, "border-style": "dashed", "border-color": "#16a34a" } },
        { selector: "edge", style: { width: 1.5, "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7, "line-color": "#cbd5e1", "target-arrow-color": "#cbd5e1", "font-size": 7, color: "#64748b" } },
        ...Object.entries(LEVEL_COLOR).map(([lvl, c]) => ({ selector: `edge[assertion_level = "${lvl}"]`, style: { "line-color": c, "target-arrow-color": c } })),
        { selector: "edge[benign_context]", style: { "line-style": "dashed", "line-color": "#16a34a", "target-arrow-color": "#16a34a" } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 22000, idealEdgeLength: () => 110, padding: 30 },
    });
    cy.current.on("tap", "edge", (evt) => onSelectRef(String(evt.target.data("evidence_ref"))));
    cy.current.on("mouseover", "edge", (evt) => evt.target.style("label", evt.target.data("type")));
    cy.current.on("mouseout", "edge", (evt) => evt.target.style("label", ""));
    return () => cy.current?.destroy();
  }, [shown, onSelectRef]);
  return (
    <div>
      <label className="toggle">
        <input type="checkbox" checked={showBenign} onChange={(e) => setShowBenign(e.target.checked)} />
        Show benign context ({showBenign ? "shown" : `${shown.hidden} links hidden`})
      </label>
      <div ref={el} className="graph" aria-label="Fraud network graph" />
      <p className="legend">
        Red: alert subject · squares: accounts · diamonds: devices · hexagons: IPs · stars: prior cases.
        Edge colour = assertion level. <span className="benign">Dashed green = benign context</span> (household or shared network), not a risk signal.
        Hover an edge for its relationship type; click it to open the evidence. {graph.truncated && <strong>Graph truncated.</strong>}
      </p>
    </div>
  );
}
