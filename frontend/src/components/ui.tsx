import type { ReactNode } from "react";
import type { Level } from "../types";

export const LEVEL_LABEL: Record<Level, string> = {
  verified_fact: "Verified fact", observed_relationship: "Observed relationship", inferred_pattern: "Inferred pattern",
  hypothesis: "Hypothesis", confirmed_outcome: "Confirmed outcome",
};

export function Badge({ value, kind = "status" }: { value: string | null | undefined; kind?: "status" | "severity" | "level" }) {
  if (!value) return <span className="badge muted">n/a</span>;
  const text = kind === "level" ? LEVEL_LABEL[value as Level] ?? value : value.replaceAll("_", " ");
  return <span className={`badge ${kind}-${value}`}>{text}</span>;
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && <header className="card-head"><h3>{title}</h3><div>{actions}</div></header>}
      {children}
    </section>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export const fmtTime = (s: string | null | undefined) => (s ? new Date(s).toLocaleString() : "");
