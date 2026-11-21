# Implementation backlog

Status key: ✅ verified here · ⚠️ implemented, needs a run with Docker/Neo4j/OpenAI (commands in ARCHITECTURE §11) · ⏭ deferred.

## Phase 0: Assessment ✅
Repository review, ARCHITECTURE.md, DECISIONS.md, this backlog, approval (2026-11-17).

## Phase 1: Foundation ✅ / ⚠️
| Item | Status |
|---|---|
| uv project, lockfile, ruff, mypy --strict, pytest | ✅ |
| Settings with fail-fast secrets, structured logging, request ids, error envelope | ✅ |
| Async SQLAlchemy, unit of work, 17-table schema, Alembic 0001–0002, invariants | ✅ |
| Neo4j client, schema bootstrap (constraints, range, full-text, vector) | ⚠️ |
| Health and readiness; Docker image and compose | ✅ app · ⚠️ compose run |

## Phase 2: Fraud domain and graph intelligence ✅ / ⚠️
| Item | Status |
|---|---|
| Simulator: beneficiaries, historical cases incl. cleared, spec scenario, alerts and truth | ✅ |
| Graph port, query catalog, in-memory repository, contract tests | ✅ memory · ⚠️ Neo4j run |
| Loader with provenance and derived edges | ⚠️ |
| Alerts, investigations, evidence, jobs, audit services | ✅ |
| Deterministic analysis (transactions, network, benign context) | ✅ |

## Phase 3: RAG ✅ / ⚠️
| Item | Status |
|---|---|
| Document generator and retrieval eval set | ✅ |
| Upload validation, dedupe, storage, ingestion pipeline and status machine | ✅ |
| Chunk stores (memory ✅, Neo4j ⚠️), hybrid RRF search, LangChain retriever | ✅ / ⚠️ |
| Retrieval evaluation (vector / keyword / hybrid) | ✅ lexical · ⚠️ OpenAI |

## Phase 4: LangGraph and OpenAI ✅ / ⚠️
| Item | Status |
|---|---|
| LLMService (OpenAI ⚠️, fake ✅), structured outputs, validation re-ask, llm_calls | ✅ / ⚠️ |
| Typed state, 11 nodes, parallel fan-out, bounded loops, interrupt and resume | ✅ |
| Postgres checkpointer, crash recovery without duplicate work | ✅ |
| Deterministic risk engine | ✅ |
| Worker: claim, lease, backoff, dead-letter, graceful shutdown | ✅ |

## Phase 5: Hardening ✅
JWT and RBAC, four-eyes review, clearance redaction, idempotency, hash-chained audit and verify endpoint, OpenTelemetry,
HTTP lifecycle test, evaluation suite (dev and held-out seeds).

## Phase 6: Frontend ✅
Dashboard, alerts, investigations, detail (overview, network, timeline, evidence, report), review panel, documents.
Headless-browser flow verified.

## Phase 7: Deployment and documentation ✅ / ⚠️
Compose profiles (seed, test, observability), Dockerfiles, nginx, README, ARCHITECTURE, DECISIONS (D-001 to D-050),
OpenAPI export, evaluation reports, screenshots. ⚠️ `docker compose up` not run here.

## Phase 8a: CI/CD and observability ✅ / ⚠️
- ✅ Prometheus metrics in API and worker with bounded labels (D-051), with unit and e2e assertions.
- ✅ SLOs, recording rules and burn-rate and symptom alerts (D-052).
  - promtool unit tests pass.
  - A live Prometheus 3.5 scraped a local API and worker.
  - Every SLI and dashboard query was evaluated.
  - `FRIWorkerDown` fired and resolved.
  - Two rule bugs found this way are fixed: an empty 5xx numerator, and the `le` label normalisation.
- ✅ Grafana provisioning, plus a dashboard generated from code. ⚠️ Rendering not verified (no Grafana image here).
- ✅ Quality gate (D-053). Dev and held-out evaluations reproduce the committed reports exactly and pass. It found and fixed the evaluation dataset-binding bug.
- ✅ HTTP smoke test. It passes against a local API and worker and fails on bad credentials.
- ✅ CI, release and Dependabot workflows pass `actionlint`. ⚠️ Not yet run on GitHub: the Neo4j service, Trivy and the compose smoke test.

## Next (⏭)
- Phase 8b: LLM evaluation gate with a judge model on real-provider output (citation relevance, faithfulness), run nightly.
- Phase 8c: streaming detection (transaction events → incremental alerts) and the reviewer-feedback loop into rule tuning.
- Scale sensitivity: recall at ≥ high drops to 0.65 on a 4,000-customer bank (D-053). Investigate the bounded subgraph and core-network limits.
- Held-out *typologies* and an LLM-judge for citation relevance (D-048).
- OIDC / refresh tokens (D-016, D-045).
- Keyset pagination; GDS community detection for whole-graph ring discovery (D-017).
- `neo4j-graphrag` HybridCypherRetriever benchmark against the same eval set (D-009).
