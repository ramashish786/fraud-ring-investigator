import { lazy, Suspense, useCallback, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { EvidenceProvider, RefChip, Refs, useEvidence } from "../components/evidence";
import { Badge, Card, ErrorNote, fmtTime, LEVEL_LABEL } from "../components/ui";
import { useData } from "../hooks";
import type { Claim, Evidence, Finding, InvestigationDetail as Detail, Level, Report } from "../types";

// Cytoscape is large: load it only when the Network tab is opened.
const NetworkGraph = lazy(() => import("../components/NetworkGraph").then((m) => ({ default: m.NetworkGraph })));

const TABS = ["Overview", "Network", "Timeline", "Evidence", "Report"] as const;
type Tab = (typeof TABS)[number];
const ACTIVE = new Set(["queued", "running"]);

export function InvestigationDetail() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<Tab>("Overview");
  const inv = useData(() => api.investigation(id), [id], { poll: true, intervalMs: 3000 });
  const polling = !inv.data || ACTIVE.has(inv.data.status);
  const evidence = useData(() => api.evidence(id), [id, inv.data?.status, inv.data?.current_node], {});
  const d = inv.data;
  if (!d) return inv.error ? <ErrorNote error={inv.error} /> : <p>Loading…</p>;
  return (
    <EvidenceProvider evidence={evidence.data?.items ?? []}>
      <div className="stack">
        <Card>
          <div className="inv-head">
            <div>
              <h2>{d.alert.external_ref}: {d.alert.alert_type.replaceAll("_", " ")}</h2>
              <p>{d.alert.summary}</p>
              <p className="muted">Subject account <code>{d.alert.subject_account_id}</code> · started {fmtTime(d.created_at)}
                {polling && d.current_node && <> · running <strong>{d.current_node}</strong> (round {d.round})</>}</p>
            </div>
            <div className="inv-status">
              <Badge value={d.status} />
              {d.risk && <div className={`score severity-${d.risk.category}`}>{d.risk.score}<small>/100 {d.risk.category}</small></div>}
            </div>
          </div>
        </Card>
        <ReviewPanel detail={d} onDone={inv.refresh} />
        <nav className="tabs" role="tablist">
          {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}
        </nav>
        {tab === "Overview" && <Overview detail={d} />}
        {tab === "Network" && <Network id={id} version={evidence.data?.total ?? 0} />}
        {tab === "Timeline" && <Timeline id={id} status={d.status} node={d.current_node} />}
        {tab === "Evidence" && <EvidenceTable items={evidence.data?.items ?? []} />}
        {tab === "Report" && <ReportView id={id} ready={d.status === "completed"} />}
      </div>
    </EvidenceProvider>
  );
}

function ReviewPanel({ detail, onDone }: { detail: Detail; onDone: () => void }) {
  const { can } = useAuth();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (detail.status !== "awaiting_review") return null;
  if (!can("reviewer")) return <Card><p className="muted">Waiting for a reviewer's decision.</p></Card>;
  const submit = async (decision: string) => {
    setBusy(true);
    try { await api.review(detail.id, decision, comment); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Card title="Human review">
      <p className="muted">The risk score is a set of indicators, not a fraud determination. Your decision is recorded as a confirmed outcome.</p>
      <textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Reason for your decision (recorded in the audit log)" maxLength={4000} />
      <ErrorNote error={error} />
      <div className="row">
        <button disabled={busy} onClick={() => void submit("approved")}>Approve escalation</button>
        <button disabled={busy} className="secondary" onClick={() => void submit("needs_more_work")}>Needs more work</button>
        <button disabled={busy} className="danger" onClick={() => void submit("rejected")}>Reject (not suspicious)</button>
      </div>
    </Card>
  );
}

function ClaimList({ claims }: { claims: { statement: string; evidence_refs: string[]; is_supported?: boolean }[] }) {
  if (!claims.length) return <p className="muted">None.</p>;
  return <ul className="claims">{claims.map((c, i) => <li key={i}>{c.statement} <Refs refs={c.evidence_refs} /></li>)}</ul>;
}

function Overview({ detail }: { detail: Detail }) {
  const findings = useData(() => api.findings(detail.id), [detail.id, detail.status]);
  const by = (cat: string): Finding[] => (findings.data ?? []).filter((f) => f.category === cat);
  return (
    <div className="grid2">
      <div className="span-all"><Card title="Risk factors (deterministic rules)">
        {detail.risk ? (
          <>
            <p className="muted">{detail.risk.disclaimer} Ruleset {detail.risk.ruleset_version}.</p>
            <table>
              <thead><tr><th>Rule</th><th>Why</th><th>Weight</th><th>Evidence</th></tr></thead>
              <tbody>{detail.risk.factors.map((f) => (
                <tr key={f.rule_id}><td><code>{f.rule_id}</code></td><td>{f.description}</td>
                  <td className={f.weight < 0 ? "mitigation" : ""}>{f.weight > 0 ? "+" : ""}{f.weight}</td><td><Refs refs={f.evidence_refs.slice(0, 4)} /></td></tr>))}
              </tbody>
            </table>
          </>
        ) : <p className="muted">Not assessed yet.</p>}
      </Card></div>
      <Card title="Supporting evidence"><ClaimList claims={by("supporting")} /></Card>
      <Card title="Contradictions and legitimate explanations"><ClaimList claims={by("contradiction")} /></Card>
      <Card title="Missing information"><ul>{by("missing_information").map((f) => <li key={f.id}>{f.statement}</li>)}</ul></Card>
      <Card title="Historical cases"><ClaimList claims={[...by("similarity"), ...by("difference")]} /></Card>
      <Card title="Applicable policy"><ClaimList claims={by("requirement")} /></Card>
    </div>
  );
}

function Network({ id, version }: { id: string; version: number }) {
  const g = useData(() => api.graph(id), [id, version]);
  const { open } = useEvidence();
  const onSelect = useCallback((ref: string) => open(ref), [open]);
  return (
    <Card title="Fraud network">
      {g.data ? <Suspense fallback={<p>Loading graph…</p>}><NetworkGraph graph={g.data} onSelectRef={onSelect} /></Suspense>
        : <ErrorNote error={g.error} />}
    </Card>
  );
}

function Timeline({ id, status, node }: { id: string; status: string; node: string | null }) {
  const t = useData(() => api.timeline(id), [id, status, node]);
  return (
    <Card title="Timeline">
      <ol className="timeline">
        {(t.data ?? []).map((e, i) => (
          <li key={i} className={`tl-${e.kind}`}>
            <span className="muted">{fmtTime(e.ts)}</span> <strong>{e.label}</strong>
            {typeof e.detail.duration_ms === "number" && <span className="muted"> · {e.detail.duration_ms} ms</span>}
            {typeof e.detail.reason === "string" && <span className="muted"> · {e.detail.reason}</span>}
            {typeof e.detail.comment === "string" && e.detail.comment && <span> · “{e.detail.comment}”</span>}
          </li>))}
      </ol>
    </Card>
  );
}

function EvidenceTable({ items }: { items: Evidence[] }) {
  const [level, setLevel] = useState<Level | "">("");
  const shown = level ? items.filter((e) => e.assertion_level === level) : items;
  return (
    <Card title={`Evidence (${shown.length})`} actions={
      <select value={level} onChange={(e) => setLevel(e.target.value as Level | "")} aria-label="Assertion level">
        <option value="">All levels</option>
        {(Object.keys(LEVEL_LABEL) as Level[]).map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
      </select>}>
      <table>
        <thead><tr><th>Ref</th><th>Level</th><th>Kind</th><th>Summary</th><th>Context</th></tr></thead>
        <tbody>{shown.map((e) => (
          <tr key={e.id}><td><RefChip refId={e.ref} /></td><td><Badge value={e.assertion_level} kind="level" /></td>
            <td>{e.kind}</td><td>{e.summary}</td>
            <td>{typeof e.snapshot.benign_context === "string" && <span className="badge benign">{e.snapshot.benign_context.replaceAll("_", " ")}</span>}</td></tr>))}
        </tbody>
      </table>
    </Card>
  );
}

function Section({ title, claims }: { title: string; claims: Claim[] }) {
  return <><h4>{title}</h4><ClaimList claims={claims} /></>;
}

function ReportView({ id, ready }: { id: string; ready: boolean }) {
  const r = useData<Report | null>(() => (ready ? api.report(id) : Promise.resolve(null)), [id, ready]);
  const download = async () => {
    const md = await api.reportMarkdown(id);
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    Object.assign(document.createElement("a"), { href: url, download: `investigation-${id.slice(0, 8)}.md` }).click();
    URL.revokeObjectURL(url);
  };
  if (!ready) return <Card><p className="muted">The report is generated after human review.</p></Card>;
  const c = r.data?.content;
  if (!c) return <ErrorNote error={r.error} />;
  const n = c.narrative;
  return (
    <Card title={`Investigation report v${r.data?.version}`} actions={<button onClick={() => void download()}>Download Markdown</button>}>
      <p>{n.investigation_summary}</p>
      <p><strong>Risk:</strong> {c.risk_assessment.score}/100 <Badge value={c.risk_assessment.category} kind="severity" /> · <strong>Review:</strong> {c.human_review.decision}{c.human_review.comment ? `: ${c.human_review.comment}` : ""}</p>
      <h4>Suspicious entities (hypotheses)</h4>
      <ul>{n.suspicious_entities.map((e) => <li key={e.account_id}><code>{e.account_id}</code>: {e.role_hypothesis} <Refs refs={e.evidence_refs} /></li>)}</ul>
      <Section title="Transaction patterns" claims={n.transaction_patterns} />
      <Section title="Graph relationships" claims={n.graph_relationships} />
      <Section title="Historical case similarities" claims={n.historical_case_similarities} />
      <Section title="Supporting evidence" claims={n.supporting_evidence} />
      <Section title="Contradictions" claims={n.contradictions} />
      <h4>Missing information</h4><ul>{n.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul>
      <h4>Recommended next steps</h4><ul>{n.recommended_next_steps.map((m, i) => <li key={i}>{m}</li>)}</ul>
      <p className="muted">Narrative by {c.generation.model}; {c.generation.unsupported_claims} unsupported claim(s).</p>
    </Card>
  );
}
