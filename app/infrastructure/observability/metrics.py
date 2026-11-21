"""Prometheus metrics (D-051).

Naming follows Prometheus conventions (`_total` counters, `_seconds` histograms, base units).
Label values are always bounded: HTTP routes use the *route template* ("/api/v1/investigations/{inv_id}"),
never the raw path, and nodes, tasks and rules come from fixed sets. Unbounded labels (ids, emails) are
never used, because they would explode cardinality and leak data.

The api exposes these on GET /metrics (not proxied by nginx, so it isn't public). The worker serves them
on WORKER_METRICS_PORT. Each process has its own registry, and Prometheus aggregates across instances.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_WORKFLOW = (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800)
_LLM = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60)

# --- HTTP (RED)
HTTP_REQUESTS = Counter("fri_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "fri_http_request_duration_seconds", "HTTP request latency", ["method", "route"], buckets=_LATENCY
)

# --- investigations (business SLIs)
INVESTIGATIONS_STARTED = Counter("fri_investigations_started_total", "Investigations started")
INVESTIGATIONS_FINISHED = Counter(
    "fri_investigations_finished_total", "Investigations reaching a terminal state", ["outcome"]
)  # completed, failed, cancelled
TIME_TO_REVIEW = Histogram(
    "fri_investigation_time_to_review_seconds", "From creation to awaiting human review", buckets=_WORKFLOW
)
RISK_CATEGORY = Counter("fri_risk_assessments_total", "Risk assessments by category", ["category"])

# --- workflow nodes
NODE_DURATION = Histogram(
    "fri_workflow_node_duration_seconds",
    "Workflow node execution time",
    ["node", "status"],
    buckets=(*_LATENCY, 30, 60),
)
NODE_DEGRADED = Counter("fri_workflow_node_degraded_total", "Investigator nodes that degraded after retries", ["node"])

# --- jobs / queue
JOBS_PROCESSED = Counter("fri_jobs_processed_total", "Jobs processed by the worker", ["kind", "status"])
JOB_DURATION = Histogram("fri_job_duration_seconds", "Job execution time", ["kind"], buckets=_WORKFLOW)
QUEUE_DEPTH = Gauge("fri_job_queue_depth", "Runnable jobs waiting", ["kind"])
QUEUE_OLDEST_AGE = Gauge("fri_job_queue_oldest_age_seconds", "Age of the oldest runnable job")
DEAD_JOBS = Gauge("fri_jobs_dead", "Jobs that exhausted their retries (need a human)")
WORKER_RUNNING = Gauge("fri_worker_running_jobs", "Jobs currently executing in this worker")

# --- LLM
LLM_CALLS = Counter("fri_llm_calls_total", "LLM calls", ["task", "status"])
LLM_LATENCY = Histogram("fri_llm_call_duration_seconds", "LLM call latency", ["task"], buckets=_LLM)
LLM_TOKENS = Counter("fri_llm_tokens_total", "LLM tokens", ["task", "type"])  # label "type" is prompt or completion
LLM_VALIDATION_PROBLEMS = Counter(
    "fri_llm_validation_problems_total", "Validation problems remaining after the corrective retry", ["task"]
)

# --- retrieval and graph
RETRIEVAL_LATENCY = Histogram("fri_retrieval_duration_seconds", "Hybrid retrieval latency", ["mode"], buckets=_LATENCY)
GRAPH_QUERY_LATENCY = Histogram(
    "fri_graph_query_duration_seconds", "Catalog query latency", ["query"], buckets=_LATENCY
)
