import { useState } from "react";
import { api } from "../api";
import { Card, ErrorNote } from "../components/ui";
import { useData } from "../hooks";
import { InvTable } from "./Dashboard";

export function Investigations() {
  const [status, setStatus] = useState("");
  const { data, error } = useData(() => api.investigations(status), [status], { poll: true, intervalMs: 5000 });
  return (
    <Card title={`Investigations (${data?.total ?? "…"})`} actions={
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
        <option value="">All</option>
        {["queued", "running", "awaiting_review", "completed", "failed", "cancelled"].map((s) =>
          <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}
      </select>}>
      <ErrorNote error={error} />
      <InvTable rows={data?.items ?? []} empty="No investigations." />
    </Card>
  );
}
