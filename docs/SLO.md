# Service level objectives and runbook

How we decide the Fraud Ring Investigator is healthy, how it pages, and what to do when it does.
The design is explained in DECISIONS.md, D-051 to D-053.

```
docker compose --profile observability up -d
```

| URL | What it is |
|---|---|
| http://localhost:9090 | Prometheus. Alerts are under *Alerts*. |
| http://localhost:3001 | Grafana. Sign in with `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`, then open *Fraud Ring Investigator → Overview*. |
| http://localhost:16686 | Jaeger traces. Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`. |

## What users actually care about

Analysts need three things from this system:

1. The API answers.
2. Pages load quickly.
3. Once they start an investigation, it is ready for review soon enough that they don't switch context.

These are the three SLOs. Everything else is a cause, not a symptom. Causes still raise tickets, but they don't page.

| SLO | SLI (good events / valid events) | Target (30 days) | Error budget |
|---|---|---|---|
| **1. API availability** | `/api/v1/*` responses that are not 5xx. Health checks excluded. | 99.5% | about 3.6 h of full outage, or 0.5% of requests |
| **2. API read latency** | `GET /api/v1/*` served in ≤ 250 ms | 95% | 5% of reads may be slower |
| **3. Investigation freshness** | Investigations that reach *awaiting review* ≤ 5 min after creation | 95% | 5% may be slower |

What is deliberately **not** an SLO:

- **LLM latency.** It's dominated by the provider and is already inside SLO 3.
- **Neo4j query time.** It's a cause.
- **Risk-score distribution.** That's a model-quality signal. It's on the dashboard, and the evaluation gate (D-053) protects it, not paging.

### Why these numbers

- **99.5%, not 99.9%.** This is an internal analyst tool with a human in the loop and no customer-facing transaction path. 99.9% would leave 43 minutes a month, which a single Neo4j restart could use up. A target nobody can meet gets ignored.
- **250 ms reads.** Reads are list and detail pages served from Postgres plus a bounded subgraph. 250 ms keeps the UI feeling instant. Writes are excluded because they only enqueue work.
- **5 minutes to review.** That's the point where an analyst stops waiting and picks up something else. The workflow normally takes seconds with the fake LLM and well under a minute with a real one, so 5 minutes leaves room for retries and LLM back-off.

## Alerting: multi-window burn rates

Alerting on raw thresholds ("error rate > 1%") either pages on every blip or misses slow leaks.
Instead, alerts fire on how fast the **error budget** is being spent, using the multi-window approach from the Google SRE workbook:

| Alert | Fires when | Budget gone in | Severity |
|---|---|---|---|
| `FRIApiAvailabilityFastBurn` | burn > 14.4× over 1 h **and** over 5 m | about 2 days | page |
| `FRIApiAvailabilitySlowBurn` | burn > 6× over 6 h **and** over 30 m | about 5 days | ticket |
| `FRIApiReadLatencyBurn` | slow-read burn > 14.4× over 1 h and 5 m | about 2 days | page |
| `FRIInvestigationFreshnessBurn` | slow-investigation burn > 6× over 6 h and 1 h | about 5 days | ticket |

The short window makes the alert **reset quickly** once the problem is fixed. The long window stops it **firing on a blip**.

### Symptom and cause alerts

| Alert | Condition | Severity |
|---|---|---|
| `FRIWorkerDown` | worker target down or absent for 2 m | page |
| `FRIApiDown` | API target down for 2 m | page |
| `FRIJobQueueBacklog` | oldest runnable job older than 120 s for 5 m | ticket |
| `FRIDeadJobs` | any dead-lettered job | ticket |
| `FRILLMErrorRateHigh` | > 10% of LLM calls fail or fail validation, over 15 m | ticket |
| `FRIGraphQueriesSlow` | p95 Neo4j catalog query > 1 s for 10 m | ticket |

The rules are in `docker/prometheus/rules/slo.rules.yml`. Their unit tests are in `slo.rules.test.yml`, which CI runs with `promtool test rules`.

## Runbook

### SLO 1: API availability
1. **Find the failing route.** On the dashboard, open *API → Requests by status* and look for the route with 5xx responses.
2. **Check readiness.** `curl localhost:8000/api/v1/health/ready`. It names the failing dependency: Postgres, Neo4j or the checkpointer.
3. **Find the error in the logs.** Search the logs for `request_id` values from the 5xx responses. Every error body carries one, and the trace id is in the same log line.
4. **If Neo4j is down:** reads that need the graph fail, while Postgres-only pages keep working. Restart Neo4j. Investigations already in progress retry with back-off (D-019).

### SLO 2: API read latency
- **One slow route:** check *p95 latency by route*. An evidence or graph endpoint on a large investigation usually means a missing limit. Paging is enforced, so look at the subgraph size.
- **Every route slow:** the cause is Postgres or the event loop. Check the connection pool and database CPU.

### SLO 3: investigation freshness
1. **Find the slow node.** Open *Workflow & jobs → Node p95 duration*.
2. **If it's an LLM node:** check *LLM calls by status*. Rate limits show up as `error` with retries.
3. **If nodes are fast but freshness is bad:** the jobs are waiting, not running. See the queue backlog section.

### Queue backlog
- **Workers saturated.** If `fri_worker_running_jobs` is pinned at `WORKER_CONCURRENCY`, scale out with `docker compose up -d --scale worker=3`. Jobs use `SKIP LOCKED`, so extra workers are safe (D-017).
- **Workers idle.** If they're idle but the queue is old, check whether jobs are scheduled in the future because of back-off. That points to a dependency outage.

### Dead jobs
A dead job means its investigation is **FAILED**.
1. **Find the cause.** It's in `jobs.last_error`, and on the investigation timeline.
2. **Retry.** Once the cause is fixed, the analyst can start the investigation again. The old one stays as an audit record.

### Worker down
- **Running but not scraped.** If `docker compose ps worker` shows it running, check `WORKER_METRICS_PORT` and that port 9101 is reachable inside the network.
- **Not running.** Check the worker logs for a crash at startup. A failed checkpointer migration is the usual cause.

### LLM errors
- **`status="error"`:** a provider failure. Check the key, quota or outage.
- **`status="invalid"`:** the model returned output that failed schema or citation validation even after the corrective re-ask (D-022). That points to a prompt or model change. Run the evaluation gate before shipping one.
- **When LLM nodes fail, the workflow degrades rather than stopping** (D-024). Risk stays deterministic.

### Graph latency
- **Find the slow query.** Use the `query` label on `fri_graph_query_duration_seconds`. Labels are catalog names, never raw Cypher (D-012).
- **Then:** check Neo4j page-cache hit rate and whether an index is missing (`app/graph/schema/bootstrap.py`).

## Metric naming rules

Every label is a **bounded** set: route templates (not raw paths), node names, task names, job kinds and catalog query names.
- **No identifiers in labels.** Never investigation ids, account ids or user ids. A unit test enforces this for HTTP routes.
- **Prometheus 3 rewrites bucket labels:** `le="300"` is stored as `le="300.0"`. Rules match bucket bounds with a regex for that reason. This was found by querying a live Prometheus; promtool alone didn't catch it.
