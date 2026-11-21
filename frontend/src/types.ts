// Mirrors the FastAPI response models (see /docs). Keep in sync with app/domains/*/schemas.py.
export type Role = "analyst" | "reviewer" | "auditor" | "admin";
export type Level = "verified_fact" | "observed_relationship" | "inferred_pattern" | "hypothesis" | "confirmed_outcome";
export type InvStatus = "queued" | "running" | "awaiting_review" | "completed" | "failed" | "cancelled";
export type Severity = "low" | "medium" | "high" | "critical";

export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface Me { user_id: string; roles: Role[]; clearance: string }

export interface Alert {
  id: string; external_ref: string; alert_type: string; severity: Severity; status: string;
  subject_account_id: string; subject_customer_id: string | null; summary: string; created_at: string;
}
export interface RiskFactor { rule_id: string; description: string; weight: number; evidence_refs: string[]; detail: Record<string, unknown> }
export interface Risk { score: number; category: Severity; ruleset_version: string; factors: RiskFactor[]; disclaimer: string }
export interface Investigation {
  id: string; alert_id: string; status: InvStatus; current_node: string | null; round: number;
  risk_score: number | null; risk_category: Severity | null; created_at: string; completed_at: string | null;
}
export interface InvestigationDetail extends Investigation { alert: Alert; risk: Risk | null }
export interface Evidence {
  id: string; ref: string; kind: string; assertion_level: Level; source: string; summary: string;
  snapshot: Record<string, unknown>; source_ref: Record<string, unknown>; produced_by_node: string; redacted: boolean;
}
export interface Finding { id: string; statement: string; category: string; assertion_level: Level; is_supported: boolean; produced_by_node: string; evidence_refs: string[] }
export interface TimelineEntry { ts: string; kind: "status" | "node" | "review"; label: string; detail: Record<string, unknown> }
export interface GraphNode { id: string; label: string; properties: Record<string, unknown> }
export interface GraphEdge { source: string; target: string; type: string; properties: Record<string, unknown> }
export interface Subgraph { nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean }
export interface Claim { statement: string; evidence_refs: string[] }
export interface Report {
  investigation_id: string; version: number; markdown: string; created_at: string;
  content: {
    narrative: {
      investigation_summary: string; suspicious_entities: { account_id: string; role_hypothesis: string; evidence_refs: string[] }[];
      transaction_patterns: Claim[]; graph_relationships: Claim[]; historical_case_similarities: Claim[];
      supporting_evidence: Claim[]; contradictions: Claim[]; missing_information: string[]; recommended_next_steps: string[];
    };
    risk_assessment: Risk; human_review: { decision: string; comment?: string };
    generation: { model: string; unsupported_claims: number; validation_problems: number };
  };
}
export interface DocumentRow { id: string; title: string; doc_type: string; classification: string; status: string; chunk_count: number; created_at: string; error: string | null }
