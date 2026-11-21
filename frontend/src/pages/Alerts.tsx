import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { useData } from "../hooks";

export function Alerts() {
  const [status, setStatus] = useState("open");
  const { data, error, refresh } = useData(() => api.alerts(status), [status]);
  const { can } = useAuth();
  const nav = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const start = async (alertId: string) => {
    setBusy(alertId);
    try {
      const r = await api.startInvestigation(alertId);
      nav(`/investigations/${r.investigation_id}`);
    } catch (e) {
      const err = e as ApiError;
      setActionError(err.message);
      void refresh();
    } finally { setBusy(null); }
  };
  return (
    <Card title={`Alerts (${data?.total ?? "…"})`} actions={
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
        <option value="open">Open</option><option value="under_investigation">Under investigation</option>
        <option value="closed">Closed</option><option value="">All</option>
      </select>}>
      <ErrorNote error={error ?? actionError} />
      <table>
        <thead><tr><th>Ref</th><th>Type</th><th>Severity</th><th>Account</th><th>Summary</th><th>Status</th><th>Created</th><th /></tr></thead>
        <tbody>
          {data?.items.map((a) => (
            <tr key={a.id}>
              <td>{a.external_ref}</td><td>{a.alert_type.replaceAll("_", " ")}</td>
              <td><Badge value={a.severity} kind="severity" /></td><td><code>{a.subject_account_id}</code></td>
              <td className="summary">{a.summary}</td><td><Badge value={a.status} /></td><td className="muted">{fmtTime(a.created_at)}</td>
              <td>{can("analyst") && a.status === "open" &&
                <button onClick={() => void start(a.id)} disabled={busy === a.id}>{busy === a.id ? "Starting…" : "Investigate"}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
