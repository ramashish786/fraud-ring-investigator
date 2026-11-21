import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { useData } from "../hooks";
import type { InvStatus } from "../types";

const STATUSES: InvStatus[] = ["queued", "running", "awaiting_review", "completed", "failed"];

export function Dashboard() {
  const inv = useData(() => api.investigations(), [], { poll: true, intervalMs: 5000 });
  const alerts = useData(() => api.alerts("open"), []);
  const items = inv.data?.items ?? [];
  return (
    <div className="stack">
      <ErrorNote error={inv.error ?? alerts.error} />
      <div className="tiles">
        <div className="tile"><span>Open alerts</span><strong>{alerts.data?.total ?? "…"}</strong></div>
        {STATUSES.map((s) => (
          <div key={s} className="tile"><span>{s.replaceAll("_", " ")}</span><strong>{items.filter((i) => i.status === s).length}</strong></div>
        ))}
      </div>
      <Card title="Needs review">
        <InvTable rows={items.filter((i) => i.status === "awaiting_review")} empty="Nothing waiting for review." />
      </Card>
      <Card title="Recent investigations">
        <InvTable rows={items.slice(0, 10)} empty="No investigations yet. Start one from Alerts." />
      </Card>
    </div>
  );
}

export function InvTable({ rows, empty }: { rows: { id: string; status: InvStatus; risk_score: number | null; risk_category: string | null; current_node: string | null; created_at: string }[]; empty: string }) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <table>
      <thead><tr><th>Investigation</th><th>Status</th><th>Step</th><th>Risk</th><th>Started</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td><Link to={`/investigations/${r.id}`}>{r.id.slice(0, 8)}</Link></td>
            <td><Badge value={r.status} /></td>
            <td className="muted">{r.current_node ?? ""}</td>
            <td>{r.risk_score ?? "…"} <Badge value={r.risk_category} kind="severity" /></td>
            <td className="muted">{fmtTime(r.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
