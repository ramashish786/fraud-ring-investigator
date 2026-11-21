// Thin typed client. The token lives in sessionStorage (cleared when the tab closes), never localStorage.
import type {
  Alert, DocumentRow, Evidence, Finding, InvestigationDetail, Investigation, Me, Page, Report, Subgraph, TimelineEntry,
} from "./types";

const TOKEN_KEY = "fri.token";
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message); }
}

export const token = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (t: string) => sessionStorage.setItem(TOKEN_KEY, t),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = token.get();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`/api/v1${path}`, { ...init, headers });
  if (res.status === 401 && path !== "/auth/token") {
    token.clear();
    window.dispatchEvent(new Event("fri:logout"));
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "error", e.message ?? res.statusText, e.request_id);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const qs = (p: Record<string, string | number | undefined | string[]>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (Array.isArray(v)) v.forEach((x) => s.append(k, x));
    else if (v !== undefined && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
};

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/auth/token", {
      method: "POST", body: new URLSearchParams({ username: email, password }),
    }),
  me: () => request<Me>("/auth/me"),
  alerts: (status?: string, offset = 0) => request<Page<Alert>>(`/alerts${qs({ status, limit: 50, offset })}`),
  investigations: (status?: string, offset = 0) =>
    request<Page<Investigation>>(`/investigations${qs({ status, limit: 50, offset })}`),
  startInvestigation: (alertId: string) =>
    request<{ investigation_id: string; status_url: string }>("/investigations", {
      method: "POST", body: JSON.stringify({ alert_id: alertId }),
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
  investigation: (id: string) => request<InvestigationDetail>(`/investigations/${id}`),
  evidence: (id: string, levels: string[] = []) =>
    request<Page<Evidence>>(`/investigations/${id}/evidence${qs({ level: levels, limit: 500 })}`),
  findings: (id: string) => request<Finding[]>(`/investigations/${id}/findings`),
  graph: (id: string) => request<Subgraph>(`/investigations/${id}/graph`),
  timeline: (id: string) => request<TimelineEntry[]>(`/investigations/${id}/timeline`),
  report: (id: string) => request<Report>(`/investigations/${id}/report`),
  reportMarkdown: (id: string) => request<string>(`/investigations/${id}/report?format=markdown`),
  review: (id: string, decision: string, comment: string) =>
    request(`/investigations/${id}/review`, { method: "POST", body: JSON.stringify({ decision, comment }) }),
  documents: () => request<Page<DocumentRow>>("/documents"),
  upload: (form: FormData) => request<DocumentRow>("/documents", { method: "POST", body: form }),
};
