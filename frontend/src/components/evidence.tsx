// Evidence traceability: every citation chip opens the exact evidence record it refers to.
import { createContext, useContext, useState, type ReactNode } from "react";
import type { Evidence } from "../types";
import { Badge } from "./ui";

interface Ctx { open: (ref: string) => void; byRef: Map<string, Evidence> }
const EvidenceCtx = createContext<Ctx>({ open: () => {}, byRef: new Map() });

export function EvidenceProvider({ evidence, children }: { evidence: Evidence[]; children: ReactNode }) {
  const [selected, setSelected] = useState<string | null>(null);
  const byRef = new Map(evidence.map((e) => [e.ref, e]));
  const e = selected ? byRef.get(selected) : undefined;
  return (
    <EvidenceCtx.Provider value={{ open: setSelected, byRef }}>
      {children}
      {selected && (
        <aside className="drawer" role="dialog" aria-label={`Evidence ${selected}`}>
          <header className="card-head">
            <h3>{selected}</h3>
            <button className="link" onClick={() => setSelected(null)}>Close</button>
          </header>
          {e ? (
            <>
              <p><Badge value={e.assertion_level} kind="level" /> <span className="muted">{e.kind} · {e.source} · {e.produced_by_node}</span></p>
              <p>{e.summary}</p>
              {e.redacted ? <p className="muted">Content redacted for your clearance.</p> : (
                <>
                  <h4>Snapshot (values the investigation relied on)</h4>
                  <pre>{JSON.stringify(e.snapshot, null, 2)}</pre>
                  <h4>Source reference</h4>
                  <pre>{JSON.stringify(e.source_ref, null, 2)}</pre>
                </>
              )}
            </>
          ) : <p className="muted">Evidence not found in this investigation.</p>}
        </aside>
      )}
    </EvidenceCtx.Provider>
  );
}

export const useEvidence = () => useContext(EvidenceCtx);

export function RefChip({ refId }: { refId: string }) {
  const { open, byRef } = useContext(EvidenceCtx);
  const e = byRef.get(refId);
  return (
    <button className={`chip ${e ? `level-${e.assertion_level}` : "missing"}`} onClick={() => open(refId)}
      title={e?.summary ?? "unknown evidence"}>{refId}</button>
  );
}

export function Refs({ refs }: { refs: string[] }) {
  if (!refs.length) return <span className="badge unsupported">unsupported: no evidence</span>;
  return <span className="refs">{refs.map((r) => <RefChip key={r} refId={r} />)}</span>;
}
