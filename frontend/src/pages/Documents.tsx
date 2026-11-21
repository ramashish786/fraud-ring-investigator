import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { useData } from "../hooks";

export function Documents() {
  const { can, me } = useAuth();
  const docs = useData(() => api.documents(), [], { poll: true, intervalMs: 5000 });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.upload(new FormData(e.currentTarget));
      e.currentTarget.reset();
      void docs.refresh();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="stack">
      {can("analyst") && (
        <Card title="Upload document">
          <form className="upload" onSubmit={(e) => void submit(e)}>
            <input name="file" type="file" accept=".md,.txt,.pdf" required />
            <input name="title" placeholder="Title" required maxLength={300} />
            <select name="doc_type" defaultValue="policy">
              {["case_report", "policy", "procedure", "monitoring_guideline", "fraud_pattern"].map((t) => <option key={t}>{t}</option>)}
            </select>
            <select name="classification" defaultValue="internal">
              {["public", "internal", "confidential", "restricted"].map((t) => <option key={t}>{t}</option>)}
            </select>
            <input name="case_id" placeholder="Case ID (optional)" maxLength={64} />
            <button disabled={busy}>{busy ? "Uploading…" : "Upload"}</button>
          </form>
          <p className="muted">Max 10 MB · .md, .txt, text PDFs · you can't upload above your clearance ({me?.clearance}).</p>
          <ErrorNote error={error} />
        </Card>
      )}
      <Card title={`Documents visible at your clearance (${docs.data?.total ?? "…"})`}>
        <ErrorNote error={docs.error} />
        <table>
          <thead><tr><th>Title</th><th>Type</th><th>Classification</th><th>Status</th><th>Chunks</th><th>Uploaded</th></tr></thead>
          <tbody>{docs.data?.items.map((d) => (
            <tr key={d.id}><td>{d.title}</td><td>{d.doc_type}</td><td><Badge value={d.classification} /></td>
              <td><Badge value={d.status} />{d.error && <span className="muted"> {d.error}</span>}</td><td>{d.chunk_count}</td>
              <td className="muted">{fmtTime(d.created_at)}</td></tr>))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
