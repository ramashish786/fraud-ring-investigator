# Decision log

> **How to maintain this file**
> - One entry per decision that changes a module boundary, data owner, technology, security posture or workflow.
> - Never edit an accepted decision's text to change its meaning. Add a new entry and mark the old one
>   `Superseded by D-xxx`. Typos and clarifications are fine.
> - Update [`ARCHITECTURE.md`](ARCHITECTURE.md) in the same change.
> - Status values: `Proposed` (awaiting approval), `Accepted`, `Superseded`, `Rejected`.

## Index

| ID | Decision | Status |
|---|---|---|
| D-001 | Synthetic data with ground truth and benign traps | Accepted (slice 1) |
| D-002 | Raw transaction nodes plus derived `PAID` edges | Accepted (slice 1), amended by D-018 |
| D-003 | Prune known shared infrastructure before clustering | Accepted (slice 1) |
| D-004 | Modular monolith, two processes (api, worker) | Accepted |
| D-005 | Postgres owns the investigation process, Neo4j owns the banking graph | Accepted |
| D-006 | Five assertion levels on every finding and evidence record | Accepted |
| D-007 | Postgres-backed job queue; no Kafka, Celery or Redis | Accepted |
| D-008 | Query catalog only; no LLM-generated Cypher | Accepted |
| D-009 | Own hybrid retriever on Neo4j vector and full-text indexes; `neo4j-graphrag` not adopted initially | Accepted |
| D-010 | `LLMService` port with OpenAI and deterministic fake providers | Accepted |
| D-011 | Small LangGraph: 5 LLM touchpoints, everything else deterministic | Accepted |
| D-012 | Citations are evidence IDs, validated after generation | Accepted |
| D-013 | Deterministic, versioned risk rules | Accepted |
| D-014 | Human review via `interrupt()`, resumed by the worker | Accepted |
| D-015 | Extend the existing simulator rather than rewrite it | Accepted |
| D-016 | Self-issued JWT, four roles, document clearance, hash-chained audit log | Accepted |
| D-017 | Neo4j 2026.x Community, no GDS initially | Accepted |
| D-018 | Graph model: provenance on observed edges, customer-attributed logins | Accepted |
| D-019 | Repository layout deviations from the spec | Accepted |
| D-020 | Tooling: uv, ruff, mypy, pytest; Python 3.12 | Accepted |
| D-021 | Testing boundary: what is verified where | Accepted |
| D-022 | Enums stored as VARCHAR + CHECK, not native PG enums | Accepted |
| D-023 | Configuration and secrets: fail fast, dev defaults only in Compose | Accepted |
| D-024 | Database-enforced invariants, not just application checks | Accepted |
| D-025 | Composition root and lazy app construction | Accepted |
| D-026 | Graph and chunk stores behind ports, with Neo4j and in-memory implementations and shared contract tests | Accepted |
| D-027 | Shared dataset reader derives graph edges once for every backend | Accepted |
| D-028 | Evidence citation refs derived from the content hash | Accepted |
| D-029 | Signals (for rules) kept separate from evidence (for people) | Accepted |
| D-030 | Four-eyes review: the analyst who starts an investigation can't review it | Accepted |
| D-031 | Benign context recorded as evidence, never as risk | Accepted |
| D-032 | Workflow node events table for timeline and node status | Accepted |
| D-033 | Clearance: in-index retrieval filter, evidence redaction, workflow runs with the initiator's clearance | Accepted |
| D-034 | Vector search: Cypher 25 `SEARCH` with in-index filters, automatic fallback to the procedure | Accepted |
| D-035 | Content-addressed document storage on a shared volume | Accepted |
| D-036 | Embedding dimensions as a setting; deterministic hashing embedder for offline use | Accepted |
| D-037 | Chunking: section-aware, about 900 characters with 150 overlap; groups `case` and `guidance` | Accepted |
| D-038 | `LLMTask`: schema, prompt, validator and fake in one unit; one corrective re-ask; every call logged | Accepted |
| D-039 | Planner may add operations but never drop mandatory ones; deterministic fallback | Accepted |
| D-040 | Risk signals only from the "core" network (subject plus explicitly added beneficiaries) | Accepted |
| D-041 | Failure policy per node type: retry then degrade, fall back, or fail the job and resume from checkpoint | Accepted |
| D-042 | Checkpointer on its own psycopg pool; its tables live outside Alembic | Accepted |
| D-043 | Reports: LLM narrative plus deterministic risk, review and evidence sections; citations sanitised | Accepted |
| D-044 | Worker: concurrent jobs, renewable leases, dead-letter marks the investigation failed, draining shutdown | Accepted |
| D-045 | Auth: user, roles and clearance reloaded from the database on every request | Accepted |
| D-046 | Idempotency: `Idempotency-Key` on starts, `external_ref` on alerts, SHA-256 on documents | Accepted |
| D-047 | Tracing: OpenTelemetry spans with trace ids in logs; OTLP export opt-in | Accepted |
| D-048 | Evaluation method: full workflow per alert, simulated reviewer, held-out seed | Accepted |
| D-049 | Frontend: React + Vite + TypeScript, Cytoscape loaded lazily, evidence-first UI, same-origin nginx | Accepted |
| D-050 | Compose profiles: `seed`, `test` (with an isolated Neo4j) and `observability`; three-stage Dockerfile | Accepted |
| D-051 | Metrics: Prometheus client, bounded labels, route templates from OpenAPI, worker on its own port | Accepted |
| D-052 | Three user-facing SLOs with multi-window burn-rate alerts; rules unit-tested; dashboard generated from code | Accepted |
| D-053 | Release quality gate: evaluation floors in YAML, fail closed, hard invariants for grounding | Accepted |
| D-054 | CI/CD: GitHub Actions with real Postgres + Neo4j, no-skip rule, Trivy, compose smoke test, GHCR release | Accepted |

---

## D-001: Synthetic data with ground truth and benign traps
**Status:** Accepted (slice 1, formerly `docs/adr/0001`)

**Context.** Real fraud data is private. Public sets (PaySim, the IBM AML datasets) lack identity links: devices, IPs and PII.
A ring detector can't be measured without knowing the rings.

**Decision.** Generate a synthetic bank with a realistic benign population, inject attack playbooks that record full
ground truth, and record **benign traps** (household devices, carrier NAT, payroll fan-out, shopkeeper fan-in, bill-split
cycles, new-phone logins) so every false positive can be explained.

**Consequences.** Per-ring precision and recall are measurable. There's a risk of optimism, because the same author writes the attacks and the detectors.
Mitigations: a camouflage knob, new-seed stress tests, and held-out typologies (Phase 5 evaluation).

## D-002: Raw transaction nodes plus derived `PAID` edges
**Status:** Accepted (slice 1, formerly `docs/adr/0002`). Relationship names amended by D-018.

**Decision.** Store transactions as nodes (the evidence layer) and derive aggregated `(:Account)-[:PAID]->(:Account)` edges
for traversal. Pre-aggregate login events into edges with first-seen, last-seen and counts.

**Consequences.** Multi-hop money traversal is one relationship per hop, and evidence can still drill down to individual transactions.
`PAID` must be rebuilt or updated whenever transactions are loaded.

## D-003: Prune known shared infrastructure before clustering
**Status:** Accepted (slice 1, formerly `docs/adr/0003`)

**Decision.** Classify IPs by type. Exclude carrier NAT, public wifi and corporate egress from identity linking.
Only flag cluster members that touch a "hot" identifier (a device with more users than any household, an emulator, a risky IP, a shared phone).

**Consequences.** The naive shared-IP rule ran at 5% precision. The pruned detector ran at 97% on the slice-1 data.
It depends on IP classification quality, so in the new system, risk rules apply benign-explanation discounts the same way (D-013).

---

## D-004: Modular monolith, two processes
**Status:** Accepted (2026-11-17)

**Context.** The spec asks for strong module boundaries but warns against needless microservices.
Investigations run for minutes and must not hold HTTP connections open.

**Decision.** One codebase and one image, started as `api` (FastAPI) or `worker` (job runner plus LangGraph). Modules talk
through Python interfaces (services and repository protocols), not HTTP. Import rules: `api → domains → repositories → infrastructure`;
`agents → domains/graph/rag`. Nothing imports `api`. Import-linter can enforce this later.

**Alternatives.**
- Single process with `BackgroundTasks`: rejected. Work is lost on restart and there's no isolation.
- Microservices: rejected. Network calls and distributed transactions with no scaling need behind them.

**Consequences.** Simple deployment. The worker scales horizontally by adding replicas. A module can be extracted later because its boundaries are already explicit.

## D-005: Postgres owns the investigation process, Neo4j owns the banking graph
**Status:** Accepted (2026-11-17)

**Context.** The spec forbids duplicating the graph in Postgres but makes Postgres the primary application database.

**Decision.**
- **Postgres:** users, roles, alerts, investigations, status history, jobs, evidence, findings, reviews, reports, audit log, document metadata, LLM call log, LangGraph checkpoints.
- **Neo4j:** customers, accounts, transactions, devices, IPs, beneficiaries, merchants, historical `FraudCase`s, a thin `Investigation` mirror, document chunks with embeddings.
- **No distributed transactions.** Evidence rows store graph IDs, an **immutable snapshot** of the values relied on, and a SHA-256 content hash.
- Writing the `Investigation` mirror to Neo4j happens after commit. It's idempotent (`MERGE`) and retried by the worker. A reconciliation script repairs drift.

**Alternatives.**
- Everything in Neo4j: rejected. Weak relational integrity for workflow and audit, and the Community edition has no RBAC.
- Everything in Postgres with recursive CTEs: rejected. Multi-hop network queries are the core workload.

**Consequences.** Reports stay reproducible after a graph reload. Two stores need an ingestion state machine for documents (metadata in Postgres, chunks in Neo4j) with retry and reconciliation.

## D-006: Five assertion levels
**Status:** Accepted (2026-11-17)

**Context.** The spec requires separating verified facts, observed relationships, inferred patterns, hypotheses and
confirmed outcomes, and says a shared device or IP must never count as proof.

**Decision.** An `AssertionLevel` enum (`VERIFIED_FACT`, `OBSERVED_RELATIONSHIP`, `INFERRED_PATTERN`, `HYPOTHESIS`,
`CONFIRMED_OUTCOME`) is a non-null column on evidence and findings. It's set by the **producer**, never by the LLM:
repositories emit facts and relationships, rules emit patterns, LLM nodes can only emit `HYPOTHESIS`, and only human review or
historical case dispositions emit `CONFIRMED_OUTCOME`. Reports group claims by level.

**Consequences.** The rule "a shared device is not proof" becomes a type-level guarantee rather than a prompt instruction. Unit tests assert that LLM nodes can't produce any level other than `HYPOTHESIS`.

## D-007: Postgres-backed job queue
**Status:** Accepted (2026-11-17). **Supersedes** the Kafka streaming slice from the slice-1 README roadmap.

**Context.** Investigations need a separate worker, retries, leases and resume after human review. The spec says no Kafka without justification.

**Decision.** A `jobs` table (`id, kind[start|resume], investigation_id, payload, status, attempts, run_after, leased_until, last_error`).
Workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED`, renew the lease while running, use exponential backoff, and park a job as `dead` after N attempts.
The job row is inserted in the **same transaction** as the investigation row, which makes it a transactional outbox, so a job is never lost or orphaned.

**Alternatives.**
- Celery or arq plus Redis: rejected. An extra stateful service, and dual writes unless an outbox is added anyway.
- Kafka: rejected. No event-stream consumers exist, and the simulator data is batch-loaded.
- FastAPI `BackgroundTasks`: rejected. Not durable.

**Consequences.** One less moving part, and job visibility through SQL. Throughput tops out around hundreds of jobs per second, far above this workload.
Revisit if multiple consumers need the same events.

## D-008: Query catalog only; no LLM-generated Cypher
**Status:** Accepted (2026-11-17)

**Decision.** Every Cypher statement the application runs lives in `app/graph/queries/` as a named entry: query text, a Pydantic
parameter model, and limits. Depth (1–4) and limits (at most 500) are **clamped server-side**. Variable-length patterns use
literal bounds selected from a whitelist, because Cypher can't parameterise path length. Reads go through `session.execute_read` (the server rejects
writes in read transactions), with a per-query `timeout` and `fetch_size`. The LLM planner selects **operation names** from an enum and
supplies typed parameters, and the planner's output is validated before anything runs.

**Alternatives.** Text2Cypher with validation: rejected for production paths. It may appear later only as an offline analyst tool against a read replica.

**Consequences.** No injection or exfiltration through generated queries, and queries are unit-testable. Novel questions need a new catalog entry, which is the right friction in a bank.

## D-009: Own hybrid retriever; `neo4j-graphrag` not adopted initially
**Status:** Accepted (2026-11-17)

**Context.** The spec asks for semantic, keyword and hybrid retrieval with metadata filtering on the Neo4j vector index, using LangChain abstractions, and an evaluation of the
official `neo4j-graphrag` package without overlapping frameworks. Neo4j 2026.02+ supports in-index vector filtering with the Cypher 25 `SEARCH` clause in the Community edition.

**Decision.** A thin `HybridRetriever` implements LangChain's `BaseRetriever`. It runs a vector `SEARCH` with in-index
`WHERE` filters (classification, doc_type, case_id) and a full-text index query, then fuses them with Reciprocal Rank Fusion (k=60).
It returns `Citation` objects (document_id, chunk_id, page, score, retriever) and can expand to linked `FraudCase` and `Account` nodes in the same query.
LangChain is used for the retriever interface and the OpenAI embeddings and chat clients. Its agent and chain layers aren't used.
`neo4j-graphrag`'s `HybridCypherRetriever` is benchmarked in Phase 3 against the same evaluation set. It's adopted only if it wins on Recall@K or code size without losing ACL pre-filtering.

**Consequences.** Full control over access filtering and citation shape. There's a little more code than using a packaged retriever.
Access classification is filtered **inside** the index, not after retrieval, so restricted chunks never reach the LLM.

**Phase 3 evaluation of `neo4j-graphrag`.** Its `HybridRetriever` and `HybridCypherRetriever` cover vector plus full-text search
and graph expansion, but they bring their own LLM, embedder and retriever abstractions, which overlap with LangChain.
Two things led to not adopting it: per-query access filtering and our `Citation` shape would have to be layered on top,
and its fusion is score-based where we use rank-based RRF. A benchmark against it requires a running Neo4j and isn't included.
**Revisit** if graph-expanded retrieval (chunk → `MENTIONS` → account neighbourhood) becomes a requirement.

## D-010: `LLMService` port with OpenAI and fake providers
**Status:** Accepted (2026-11-17)

**Decision.** `app/infrastructure/llm` exposes `LLMService.structured(task, schema, messages) -> (model, usage)` and
`embed(texts)`. The OpenAI implementation uses `ChatOpenAI.with_structured_output` and `OpenAIEmbeddings`. Model names come from
`OPENAI_CHAT_MODEL` and `OPENAI_EMBEDDING_MODEL`, with a timeout, bounded retries on 429 and 5xx errors, and a semaphore to cap concurrency.
Every call writes to `llm_calls` (task, investigation_id, model, latency, prompt and completion tokens, status), never the prompt contents.
A `FakeLLMService` returns schema-valid deterministic outputs and hash-based embeddings. It's selected by `LLM_PROVIDER=fake`.

**Consequences.** The stack runs and tests pass with no API key, and end-to-end tests are deterministic. Changing provider means writing one class.
The fake's retrieval quality is meaningless, so retrieval metrics are only reported with real embeddings.

## D-011: Small LangGraph
**Status:** Accepted (2026-11-17)

**Decision.** There are 11 nodes as in the spec, but only **5 LLM touchpoints**: planner, case analysis, policy analysis, synthesizer and report.
Investigators and risk are deterministic services. Fan-out uses parallel edges (or `Send` for planner-selected subsets). Fan-in uses reducers on typed state.
Loops are capped at 2 rounds. The planner chooses from an operation catalog and **falls back to a deterministic plan per alert type** on failure.
In Phase 4 the LLM planner is compared against the deterministic plan. If it adds nothing, it's demoted to deterministic (challenge noted below).

**Consequences.** Cheaper, faster and testable. "Agentic" behaviour is limited to where judgement actually helps.

## D-012: Citations are evidence IDs, validated after generation
**Status:** Accepted (2026-11-17)

**Decision.** LLM nodes receive evidence as `[E-xxxx] text` items and must return claims with `evidence_ids: list[str]`.
A validator rejects IDs not in the provided set. Invalid outputs are retried once with the error message. Claims that still have no valid evidence
are kept but marked `unsupported`, and they count toward the **unsupported-claim rate** metric.

**Consequences.** Citations can't be fabricated. They might still be cited for the wrong claim, which Phase 5 measures with a citation-correctness check on the evaluation set.

## D-013: Deterministic, versioned risk rules
**Status:** Accepted (2026-11-17)

**Decision.** Rules live in `config/risk_rules.yaml` (id, description, inputs, thresholds, weight, cap). Example rules: velocity, amount
anomaly against the customer's baseline, shared device (discounted for household or ≤4 users), shared IP (zero weight for carrier NAT and wifi),
beneficiary fan-in, prior `CONFIRMED_OUTCOME`, and network pattern (pass-through, cycle). The output is score (0–100), category, contributing rules,
and the evidence IDs per rule, plus `ruleset_version` stored with the assessment. The LLM never sees or edits scores before they're computed.

**Consequences.** Scores are explainable and reproducible. Rules are tuned against the simulator's labels.

## D-014: Human review via `interrupt()`
**Status:** Accepted (2026-11-17)

**Decision.** The review node calls `interrupt({...summary...})`. The worker sees the interrupt and sets the investigation to `AWAITING_REVIEW`.
`POST /review` records the review in Postgres and enqueues a `resume` job in the same transaction. The worker calls
`graph.ainvoke(Command(resume=decision), {"configurable": {"thread_id": investigation_id}})` with `AsyncPostgresSaver`.
Code before `interrupt()` inside the review node must be idempotent, because the node re-executes on resume.

**Consequences.** Paused investigations survive restarts and can be resumed by any worker replica.

## D-015: Extend the existing simulator
**Status:** Accepted (2026-11-17)

**Decision.** Move `fraudsim` to `simulation/` without rewriting it. Add:
- a `Beneficiary` entity;
- historical `FraudCase`s with dispositions, **including cleared false positives** (for example a family device);
- a document generator (case reports, procedures, policies, monitoring guidelines, typology notes) that references the ground truth;
- scenario presets, including the spec's example (account A → 3 beneficiaries sharing a device and IP, two linked to prior cases);
- Pydantic schemas for outputs.

`detection/` becomes the offline baseline and test oracle. `stream.jsonl` is retired (D-007).

**Consequences.** Existing tests and metrics carry over. Documents have known relevant chunks per query, which gives Recall@K without hand labelling.

## D-016: Auth, roles, document clearance, audit
**Status:** Accepted (2026-11-17)

**Decision.** Self-issued JWT access tokens (HS256 in development, with an RS256 option) and argon2 password hashing. Roles are
`analyst` (create and investigate), `reviewer` (approve and reject), `auditor` (read-only, including the audit log) and `admin`.
Document classification (`public`, `internal`, `confidential`, `restricted`) maps to role clearance and is passed as an in-index retrieval filter.
The audit log is append-only, with `prev_hash` chaining (SHA-256 over the canonical row) and a verification script.
No customer PII is written to logs. Customer IDs appear only as synthetic IDs.

**Alternatives.** Keycloak or OIDC: deferred. Real banks would use it, but it adds a heavy service. The auth module is a seam.

**Consequences.** RBAC is enforced in the application. Neo4j Community has no database RBAC (see challenge 1).

## D-017: Neo4j 2026.x Community, no GDS initially
**Status:** Accepted (2026-11-17). Supersedes the GDS usage in slice-1 `graph/detectors.cypher`.

**Decision.** Use `neo4j:2026.x-community`, with the exact tag pinned in Phase 1, because it has the Cypher 25 `SEARCH` clause and in-index vector filters.
Network discovery uses bounded Cypher traversal from the alert's accounts (depth ≤4, limits), which is enough at investigation scope.
GDS (WCC, Louvain) is reconsidered only for a whole-graph batch "ring discovery" job.

**Consequences.** One fewer plugin. Investigation queries are local by design, so they don't need whole-graph algorithms.

## D-018: Graph model with provenance
**Status:** Accepted (2026-11-17). Amends D-002.

**Decision.** Adopt the spec's labels and relationships (`RECEIVED_BY`, `USES`, `ACCESSED_FROM`, `ADDED`, `LINKED_TO`,
`INVOLVED_IN`, `INVESTIGATES`), **plus** `(:Customer)-[:LOGGED_IN_FROM]->(:IPAddress)`. Observed edges carry
`first_seen, last_seen, observations, source_ref, confidence, verification_status`.

**Why the addition.** `(Device)-[:ACCESSED_FROM]->(IPAddress)` on its own loses *who* used the IP when a device is shared,
which is exactly the case the system must not over-interpret.

**Consequences.** One extra relationship type, and it keeps attribution intact.

## D-019: Repository layout deviations
**Status:** Accepted (2026-11-17)

**Decision.** Follow the spec's structure, except that `fraud_networks` lives under `app/graph`, and risk, reviews, reports and documents become
their own domain subpackages. `simulation/` is never imported by `app/`.

## D-020: Tooling
**Status:** Accepted (2026-11-17)

**Decision.** Python 3.12 (wide wheel support for psycopg, asyncpg and the neo4j driver); `uv` with `pyproject.toml` and `uv.lock`; ruff (lint and format);
mypy (strict on `app/`); pytest with `pytest-asyncio`; a multi-stage Dockerfile running as a non-root user. Versions are pinned in the lockfile, not in prose.

## D-021: Testing boundary
**Status:** Accepted (2026-11-17)

**Decision.**
- Unit tests need no services.
- Postgres integration tests run against a real Postgres (compose, or a local `initdb` in CI).
- Neo4j integration tests run against the compose `neo4j` service and are skipped with a clear reason when it's unreachable.
- End-to-end tests use `LLM_PROVIDER=fake`.
- Any result not actually run is reported as **not verified**. The development workspace can't run Neo4j or Docker (ARCHITECTURE §4).

## D-022: Enums as VARCHAR + CHECK
**Status:** Accepted (2026-11-17, Phase 1)

**Context.** Status and type columns (investigation status, assertion level, job kind and so on) will gain values as the project grows.

**Decision.** Use SQLAlchemy `Enum(native_enum=False, create_constraint=True)`: a `VARCHAR(32)` column with a `CHECK (col IN (...))` constraint.
The Python `StrEnum`s in `app/core/enums.py` are the single source of truth.

**Alternatives.** Native PostgreSQL `ENUM` types: rejected. Removing or renaming a value needs type recreation, and Alembic doesn't autogenerate those changes well.

**Consequences.** Changing a value is a plain constraint swap in a migration. Storage is slightly larger, which doesn't matter at this scale.

## D-023: Configuration and secrets
**Status:** Accepted (2026-11-17, Phase 1)

**Decision.**
- `app/core/settings.py` (pydantic-settings) is the only place environment variables are read.
- `DATABASE_URL`, `NEO4J_PASSWORD` and `JWT_SECRET` **have no defaults in code**, so a missing secret stops startup instead of silently running with a known value.
- Secrets are `SecretStr`, which keeps them out of `repr` and logs.
- With `ENV=prod`, weak or short secrets are rejected. `LLM_PROVIDER=openai` requires the key and both model names; empty strings count as missing.
- Development values exist only in `docker-compose.yml` (`${VAR:-dev-default}`) and `.env.example`. An optional `.env` overrides them and is git-ignored.
- Alembic reads `DATABASE_URL` directly, so migrations don't need unrelated secrets.

**Consequences.** `docker compose up` works with zero setup, and production can't start with dev secrets by accident.

## D-024: Database-enforced invariants
**Status:** Accepted (2026-11-17, Phase 1)

**Context.** The API and the worker both write investigation state. Rules enforced only in Python can be bypassed by a bug, a script or a race between the two.

**Decision.** These invariants live in the schema and are covered by integration tests:

| Invariant | Mechanism |
|---|---|
| One active investigation per alert | Partial unique index `uq_investigations_alert_active` (`WHERE status NOT IN terminal AND deleted_at IS NULL`) |
| LLM output can only be a hypothesis (D-006) | `CHECK ((source = 'llm') = (assertion_level = 'hypothesis'))` on `evidence` |
| Re-running a node doesn't duplicate evidence | `UNIQUE (investigation_id, content_hash)` and `INSERT … ON CONFLICT DO NOTHING` |
| The audit log can't be edited | `BEFORE UPDATE OR DELETE OR TRUNCATE` trigger raising an error, for every role |
| API and worker can't overwrite each other's status change | Optimistic locking (`version` column, SQLAlchemy `version_id_col`) raising `StaleDataError` |
| Jobs always point at a target | `CHECK` tying `kind` to `investigation_id` or `document_id` |
| Live emails and document hashes are unique, deleted rows aside | Partial unique indexes `WHERE deleted_at IS NULL` |

**Consequences.** Some business rules are visible only in migrations, so they're listed here and in ARCHITECTURE §2.2.
Dropping the audit trigger requires a migration, which is reviewable and leaves a record.

## D-025: Composition root and lazy app construction
**Status:** Accepted (2026-11-17, Phase 1)

**Decision.** `app/core/container.py` builds the process-wide resources: settings, the `Database` (async engine and pool) and the `Neo4jClient` (driver and pool).
It's created in the FastAPI lifespan (or by the worker) and closed at shutdown. Routes get it only through `app/api/deps.py`.
`create_app(container=...)` lets tests inject fakes with no network. `app.main.app` is built lazily (module `__getattr__`),
so importing the module doesn't require environment variables, while `uvicorn app.main:app` still works.

**Alternatives.** Module-level singletons: rejected, because they're hard to replace in tests and get created at import time. A DI framework: rejected as unnecessary at this size.

## D-026: Ports with Neo4j and in-memory implementations, verified by contract tests
**Status:** Accepted (2026-11-17, Phase 2)

**Context.** Graph and RAG logic must be testable without Neo4j: in CI, offline, and in the development workspace, which can't run Neo4j.
Mocks per test drift from real behaviour.

**Decision.** `GraphRepository` (and `ChunkStore` in Phase 3) are `Protocol`s with two implementations:
- `Neo4jGraphRepository`, which runs only catalog queries;
- `InMemoryGraphRepository`, built from the same dataset.

`tests/contract/` runs **identical assertions** against both. The Neo4j half runs when `TEST_NEO4J_URI` is set
(`docker compose --profile test run --rm tests`). `GRAPH_BACKEND=memory` exists only for tests and offline evaluation. Deployed environments use Neo4j.

**Consequences.** The workflow, the API and the evaluation can run end to end without Neo4j. The in-memory store is a test double, not a product feature.
Any Cypher change needs the Neo4j contract run before merge. That's documented in CLAUDE.md.

## D-027: One dataset reader for all backends
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** `app/graph/dataset.py` reads the simulator export and derives the aggregated edges once: `USES` (from logins and transaction devices),
`LOGGED_IN_FROM`, `ACCESSED_FROM` and `PAID`, all with first-seen, last-seen and counts. The Neo4j loader and the in-memory repository both consume it.
Ground-truth labels are dropped at read time.

**Consequences.** Both backends see identical relationships. There's one place to change how an edge is derived.

## D-028: Evidence refs from the content hash
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** The citation handle is `"E-" + sha256(content)[:10]`. Investigators run in parallel, and a per-investigation counter would need locking.
A hash-derived ref is race-free, and it's the same on re-execution, which also makes evidence writes idempotent.

**Consequences.** Refs aren't sequential (E-3fa2…), which is acceptable for citations. A collision within one investigation has a probability of about 2⁻⁴⁰.
A collision would fail loudly on the `(investigation_id, ref)` unique constraint rather than corrupt data.

## D-029: Signals separate from evidence
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** Analysis steps return both `EvidenceItem`s (text plus snapshot, for people and citations) and `Signal`s (named numbers with the
evidence refs that justify them). The risk engine reads **only signals**, so its inputs are typed and deterministic, and every factor can cite evidence.

## D-030: Four-eyes review
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** `submit_review` rejects a reviewer who created the investigation, even if they also hold the reviewer role.
Only one review per review cycle: a second submission while a resume job is pending returns 409.

## D-031: Benign context is evidence, not risk
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** A shared device whose users all share one registered address is stored as `shared_device` evidence with
`benign_context = same_household` and counted in `household_shared_devices`, not in `shared_device_links`.
Carrier NAT, public wifi and corporate IPs are stored with `benign_context = shared_infrastructure` and give no risk signal.
Cleared prior cases are `CONFIRMED_OUTCOME` evidence that the synthesizer must surface as a contradiction.

**Consequences.** The report can say "shared device observed, consistent with a household" instead of hiding it or over-weighting it.
This applies the spec's rule that a shared device must not be treated as proof.

## D-032: Workflow node events
**Status:** Accepted (2026-11-17, Phase 2)

**Decision.** The `investigation_events` table stores node, status (started, completed, failed, interrupted), round, duration and small details.
It feeds `GET /timeline` and node-status observability. Status *transitions* stay in `investigation_status_history`.
Findings get a `position` column so their order is stable within a node.

## D-033: Clearance end to end
**Status:** Accepted (2026-11-17, Phase 3)

**Decision.**
- Each chunk stores `classification_rank`, and every vector and keyword search filters `rank <= caller clearance` inside the search.
- Users can't upload above their own clearance, and document listings only show what they're cleared for.
- The workflow retrieves with the clearance of the analyst who **started** the investigation.
- When someone with lower clearance views the investigation later, document-derived evidence above their clearance is
  **redacted** (`[redacted: requires X clearance]`) but stays visible as existing, so citations still resolve and nobody is misled into thinking there's no evidence.

## D-034: Vector search syntax and fallback
**Status:** Accepted (2026-11-17, Phase 3)

**Decision.** Use the Cypher 25 `SEARCH … WHERE classification_rank <= $r [AND doc_group = $g]` clause, which needs the index to be
declared with `WITH [c.classification_rank, c.doc_group]` (GA in Neo4j 2026.02+). If the server rejects it, the store logs one warning and switches
to `db.index.vector.queryNodes` with 5× oversampling and post-filtering. The index DDL has the same fallback.
Keyword search uses a full-text index with the english analyzer, and **all user text is Lucene-escaped**.

**Consequences.** It runs on older 5.x servers, at lower recall under heavy filtering. Not verified against a live server here (ARCHITECTURE §4).

## D-035: Document storage
**Status:** Accepted (2026-11-17, Phase 3)

**Decision.** Bytes are stored under `DOCUMENT_STORAGE_DIR/<sha[:2]>/<sha><ext>`, written atomically, on a named volume shared by the api and worker containers.
The SHA-256 doubles as the dedupe key: uploading the same bytes again returns the existing document.
Paths are checked against the storage root (no traversal). It sits behind a two-method interface, so object storage can replace it.

## D-036: Embeddings
**Status:** Accepted (2026-11-17, Phase 3)

**Decision.** `EMBEDDING_DIMENSIONS` (default 1536) sets both the vector index and the OpenAI `dimensions` parameter.
With `LLM_PROVIDER=fake`, a deterministic feature-hashed bag-of-words embedder (unigrams and bigrams) is used.
Changing provider or dimensions requires re-ingesting documents and recreating the index.

**Consequences.** Offline retrieval numbers are lexical baselines and say nothing about OpenAI embedding quality. `app/evaluation/retrieval.py --provider openai` produces the real ones.

## D-037: Chunking and document groups
**Status:** Accepted (2026-11-17, Phase 3)

**Decision.**
- Chunks never cross a Markdown heading, so the `section` citation is accurate.
- Paragraphs are packed up to about 900 characters with about 150 characters of overlap; overlong paragraphs split on sentences.
- PDFs are chunked per page, with the page number kept for citations.
- Retrieval filters by group: `case` (historical case reports) for the case retriever, and `guidance` (policies, procedures, guidelines, typologies) for the policy retriever.

**Retrieval baseline (hashing embedder, 23 documents, 10 labelled queries):**

| Mode | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| vector | 0.725 | 0.875 | 1.0 | 1.0 |
| keyword | 0.725 | 0.925 | 1.0 | 1.0 |
| hybrid | 0.725 | 0.925 | 1.0 | 1.0 |

The corpus is small, so these mostly guard against regressions. A test enforces hybrid Recall@5 ≥ 0.8.

## D-038: LLM tasks
**Status:** Accepted (2026-11-17, Phase 4)

**Decision.** Each of the 5 LLM touchpoints is an `LLMTask`: output schema (OpenAI `json_schema` structured output through
`ChatOpenAI.with_structured_output`), system prompt, input renderer, validator and deterministic fake.
`LLMService.run()` handles everything around the call:
- an overall timeout;
- SDK retries for 429 and 5xx errors;
- a concurrency semaphore;
- one corrective re-ask on a schema error or validation problem (for example an unknown evidence ref);
- an OpenTelemetry span;
- a row in `llm_calls` (task, model, status, latency, tokens).

Nodes never see provider types.

**Consequences.** Changing provider means writing one class. The fakes make end-to-end tests deterministic.
Prompt contents are never stored; token usage is.

## D-039: Constrained planner
**Status:** Accepted (2026-11-17, Phase 4)

**Decision.** The planner picks from a fixed `PlanOp` enum. `transaction_analysis` and `prior_cases` are always added in round 0,
so the LLM can't skip due diligence. Any planner failure falls back to a per-alert-type deterministic plan and is recorded in `errors`.
Follow-up rounds plan only the operations the synthesizer or the reviewer asked for, and they're bounded by `WORKFLOW_MAX_ROUNDS` (default 2).

## D-040: Core network for risk signals
**Status:** Accepted (2026-11-17, Phase 4). Found by the end-to-end tests.

**Context.** Version 1 counted any shared device in the extended network (payees and their payees). A benign household alert scored *medium*
because a friend's friend used an emulator, and because payees inferred from a first transfer always look "registered then paid within 24h".

**Decision.**
- Risk signals for devices, IPs, beneficiary transfers and fan-in only count links that touch the **core**: the subject plus beneficiaries it **explicitly** registered.
- Wider-network observations are still recorded as evidence, and shown in the graph and report, but carry no weight.

**Consequences.** The household look-alike now scores *low*, and the spec example still scores *critical*. Both are regression-tested.

## D-041: Failure policy per node type
**Status:** Accepted (2026-11-17, Phase 4)

| Node type | On failure |
|---|---|
| Investigators (graph, transactions, retrieval) | 3 attempts with backoff, then **degrade**: record the error and a missing-information note, then continue |
| Case and policy interpretation (LLM) | Keep the retrieved evidence, skip the interpretation, record the error |
| Planner (LLM) | Deterministic plan |
| Synthesizer and report (LLM), database writes | **Fail the job**. The worker retries with backoff, and LangGraph continues from the last checkpoint |

Verified by `test_crash_mid_workflow_recovers_without_duplicate_evidence`: the synthesizer fails once, then on retry the investigators
**don't** re-run and the evidence count is unchanged.

## D-042: Checkpointer
**Status:** Accepted (2026-11-17, Phase 4)

**Decision.** `AsyncPostgresSaver` runs on a small psycopg3 pool (`autocommit`, `dict_row`, `prepare_threshold=0`), separate from SQLAlchemy's asyncpg pool.
`python -m app.agents.checkpoint` creates its tables in the `migrate` step. Alembic ignores them (`include_object`), so migrations never drop them.
`thread_id` is the investigation id.

## D-043: Reports
**Status:** Accepted (2026-11-17, Phase 4)

**Decision.** The LLM writes only `ReportNarrative` sections. The risk score, factors and ruleset version, the reviewer's decision,
the plan, any errors and the evidence appendix are inserted **deterministically**.
Before saving, every cited ref is checked against the investigation's evidence: unknown refs are dropped, and claims left with no
evidence are counted as `unsupported_claims` (the metric). Markdown is rendered deterministically from the structured content.
Regenerating a report creates version n+1.

## D-044: Worker execution model
**Status:** Accepted (2026-11-17, Phase 4)

**Decision.**
- Up to `WORKER_CONCURRENCY` jobs run per process. Each one holds a lease that is renewed every lease/3 seconds.
- Failure sets `failed` with exponential backoff (5 s doubling, up to 5 min). After `max_attempts` the job is `dead`, and the investigation becomes `FAILED` with an audit entry.
- A periodic reaper marks exhausted jobs with expired leases as dead.
- SIGTERM stops claiming, drains running jobs for 20 s, then cancels them. Their leases expire and another worker resumes them from the checkpoint.

## D-045: Authentication details
**Status:** Accepted (2026-11-17, Phase 5)

**Decision.**
- `POST /api/v1/auth/token` (OAuth2 password form) returns a 30-minute HS256 JWT with `sub`, `roles`, `clr`, `iss`, `exp` and `jti`.
- On every request the user is **reloaded** from Postgres. Deactivation takes effect immediately, and roles and clearance come from the database, not from a possibly stale token.
- Login verifies against a dummy hash when the email is unknown (similar timing either way) and returns one generic error message.
- Role guards are FastAPI dependencies (`Reader`, `Analyst`, `Reviewer`, `Auditor`, `Uploader`). Services check roles again (defence in depth).

**Consequences.** One small primary-key lookup per request, cheap enough. There's no refresh token, so the frontend re-authenticates after expiry.
OIDC remains the production path (D-016).

## D-046: Idempotency
**Status:** Accepted (2026-11-17, Phase 5)

**Decision.**
- `POST /investigations` accepts an optional `Idempotency-Key`. The same key and alert return the original investigation (`202` with `Idempotent-Replay: true`). The same key with a different alert returns `409`.
- `POST /alerts` is idempotent on `external_ref` (`201` when created, `200` when it already exists, race-safe through a savepoint).
- Documents are deduplicated by SHA-256.
- A second review while one is still processing returns `409`.

## D-047: Tracing
**Status:** Accepted (2026-11-17, Phase 5)

**Decision.**
- The OpenTelemetry SDK is configured in both processes.
- FastAPI is auto-instrumented, excluding health checks. Manual spans cover workflow nodes and LLM calls (with token counts).
- Every log line carries `trace_id` and `span_id`.
- Export is OTLP/HTTP, enabled only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The compose `observability` profile runs Jaeger.
- Retrieval and query latencies are logged per call. LLM latency and tokens are also persisted in `llm_calls`.

## D-048: Evaluation method
**Status:** Accepted (2026-11-17, Phase 5)

**Decision.** `python -m app.evaluation.investigations` runs every generated alert (true and false positives) through the **real** workflow and worker.
A policy bot stands in for the reviewer: approve at medium or above, otherwise reject. It reports the spec §14 metrics, and it injects one-off node failures to measure recovery.
It is run on the development seed and on a **held-out seed with stealthier attackers** (`--camouflage 0.9`).

**Results (in-memory graph, fake LLM):** see `reports/evaluation.md` and `reports/holdout/evaluation.md`.

| | Dev seed 42 | Held-out seed 99 |
|---|---|---|
| Detection P / R (flag at ≥ medium) | 1.0 / 1.0 | 1.0 / 1.0 |
| Detection P / R (flag at ≥ high) | 1.0 / 0.82 | 1.0 / 0.77 |
| Ring discovery P / R | 0.72 / 0.27 | 0.71 / 0.26 |
| Evidence completeness | 1.0 | 0.97 |
| Recovery from injected failures | 6/6 | 6/6 |

**Honest limits.**
1. The attack *typologies* in both seeds are the ones the rules were designed for, so detection is optimistic. Unseen typologies are the next evaluation step.
2. With the fake LLM, citation correctness and the unsupported-claim rate measure the guardrails, not a model. Rerun with `LLM_PROVIDER=openai`.
3. Ring-discovery recall is low by design: reports name at most 6 entities, and victims are counted as ring members.

## D-049: Frontend
**Status:** Accepted (2026-11-17, Phase 6)

**Decision.**
- React 19, Vite and strict TypeScript, plus `react-router-dom` and Cytoscape. There's no UI kit (fewer dependencies, full control) and no global state library: a small typed API client and a `useData` polling hook are enough.
- **Evidence first:** every claim renders citation chips. A chip opens a drawer with the exact evidence snapshot and source reference, colour-coded by assertion level. Unsupported claims are flagged in red.
- The network view is built from evidence (not live Neo4j), so it matches the report. Benign-context links are hidden by default behind a toggle.
- Cytoscape is lazy-loaded (the main bundle is about 283 kB, the graph chunk about 444 kB).
- The JWT is kept in `sessionStorage`. nginx serves the SPA and proxies `/api` on the same origin, with CSP and other security headers. In development, the Vite proxy does the same.

**Verified** with strict type checks, a production build, and a headless-Chrome run of the analyst → reviewer → report flow.

## D-050: Docker topology
**Status:** Accepted (2026-11-17, Phase 7)

**Decision.**
- The default `up` starts postgres, neo4j, migrate (one-shot), api, worker and frontend.
- The `seed` profile generates and loads synthetic data idempotently.
- The `test` profile runs the full suite in the `dev` image against Postgres (`fri_test`, created by an init script) and a **separate `neo4j-test`**, because contract tests wipe their graph and must never touch seeded data.
- The `observability` profile runs Jaeger.
- The Dockerfile has three stages: `builder` (uv, frozen lockfile), `dev`, and `runtime` (slim, non-root, `/data` volumes owned by the app user).

## D-051: Metrics
**Status:** Accepted (2026-11-17, Phase 8a)

**Context.** Traces (D-047) explain one request. They can't say "5% of investigations are slow today" or wake anyone up. That needs aggregated time series.

**Decision.**
- **Library and endpoints.** `prometheus-client` exposes metrics on `GET /metrics` in the API. It sits outside `/api`, so nginx never proxies it to browsers. The worker serves its own registry on `WORKER_METRICS_PORT` (9101), because it has no HTTP server and its metrics live in its own process.
- **What is measured.**
  - HTTP: rate, errors, duration.
  - Investigations: started, finished by outcome, time to review.
  - Workflow: per-node duration and degraded fallbacks.
  - Jobs: outcomes, duration, queue depth, oldest runnable age, dead count, running.
  - LLM: calls by status, duration, tokens, validation problems.
  - Retrieval and graph-query latency.
  - Risk assessments by category, which is a drift signal.
- **Every label value comes from a closed set.** HTTP routes are labelled with their **template** (`/api/v1/investigations/{inv_id}`), resolved by matching the path against the app's OpenAPI paths. Unknown paths become `unmatched`, so a scanner probing random URLs can't create unbounded series. Graph queries are labelled by catalog name, never by Cypher text. No ids, emails or account numbers ever appear in a label; a unit test enforces this for HTTP routes.
- **Queue gauges are refreshed by the worker from Postgres every 5 ticks.** Queue state belongs to the database, not to any one process, so any worker can report it.

**Alternatives.**
- *OpenTelemetry metrics through a collector.* This would be one SDK, but it needs a collector to reach Prometheus and adds a moving part for no gain at this scale. Traces stay on OTel.
- *Starlette's matched-route attribute.* FastAPI 0.163 keeps nested routers, so the attribute reports the innermost router's prefix rather than the full template. Matching against the public OpenAPI schema avoids relying on private internals.

**Consequences.** Each process has its own registry, so multi-worker API deployments would need `PROMETHEUS_MULTIPROC_DIR`. The API runs one Uvicorn process per container and scales by container, so this isn't needed today.

## D-052: Service level objectives and alerting
**Status:** Accepted (2026-11-17, Phase 8a). Details and runbook in `docs/SLO.md`.

**Decision.**
- **Three SLOs, chosen because analysts would notice them:**
  - API availability: 99.5%.
  - Reads under 250 ms: 95%.
  - Investigations ready for review within 5 minutes: 95%.
- **Causes are tickets, not pages.** That covers queue age, dead jobs, LLM errors and slow graph queries.
- **Paging uses multi-window burn rates** (Google SRE workbook): 14.4× over 1 h and 5 m, then 6× over 6 h and 30 m. It pages for fast budget loss without paging on blips, and clears soon after recovery.
- **Recording rules** keep the dashboard and alerts on the same SLI definitions.
- **Alert rules have unit tests** (`promtool test rules`), including tests that health-check failures don't count and that a healthy API reports 100%, not "no data".
- **The Grafana dashboard is generated by `scripts/build_dashboard.py`.** Panels are reviewable in diffs, and CI fails if the committed JSON is stale.

**Two bugs this caught.** Both were found by running a real Prometheus 3.5 against the live API and worker and querying every dashboard expression. promtool alone missed both.
1. **An empty error numerator.** `sum(rate(...{status=~"5.."}))` returns an *empty* vector when no 5xx has ever happened, so the availability SLI showed "no data" on a healthy system. Fixed with `or vector(0)`.
2. **Bucket labels rewritten.** Prometheus 3 normalises bucket labels at ingestion (`le="300"` becomes `"300.0"`), so the freshness SLI matched nothing. Fixed with regex matchers, plus a rule test that uses the stored form.

**Verified live:** stopping the worker made `FRIWorkerDown` go pending, then fire after 2 minutes, then clear when the worker came back.

**Not verified here:** Grafana's rendering. Docker Hub isn't reachable from this environment, so the dashboard JSON is schema-checked, and each of its queries was run against the live Prometheus.

## D-053: Release quality gate
**Status:** Accepted (2026-11-17, Phase 8a)

**Context.** The evaluation (D-048) measured quality once. Without a gate, a prompt tweak, a rule change or a refactor could quietly drop recall, and nobody would see it until a reviewer missed a ring.

**Decision.**
- **Floors in `config/quality_gates.yaml`.** `python -m app.evaluation.gate` compares evaluation reports against them and exits 1 on any failure. In CI it writes the table to the job summary.
- **Floors sit just below what is measured on both the dev and held-out seeds.** They are regression floors, not aspirations. Raising one is cheap; lowering one needs a decision entry.
- **Hard invariants** mark safety properties rather than trade-offs: citation correctness is exactly 1, the unsupported-claim rate is exactly 0, every alert completes, and every injected crash recovers.
- **The gate fails closed.** A metric missing from the report fails, a non-numeric value fails, and typos in the config are rejected. A refactor that stops measuring something must not pass.
- **CI regenerates the data rather than trusting committed reports.** It simulates the bank from the seed, runs the full workflow per alert and gates the fresh numbers. Simulation and the fake LLM are deterministic, and the dev and held-out runs reproduce the committed reports exactly (verified here).

**What building it found.**
1. **A silent mis-scoring bug.** The evaluation CLI took `--data` for alerts and labels, but the in-memory graph loaded from `MEMORY_DATASET_DIR`. Pointed at a new dataset, it scored alerts against a different bank: recall was 0, and nothing errored. The gate failed it immediately. The CLI now binds the graph to `--data`.
2. **A scale sensitivity.** With the bank doubled (4,000 customers over 60 days, same seed), recall at ≥ high falls from 0.82 to 0.65 and evidence completeness from 1.0 to 0.94, while recall at ≥ medium stays at 1.0. Larger neighbourhoods dilute the bounded subgraph (D-040's core-network limit). This runs in CI as a **non-blocking** `scale` job, so it stays visible, and it's in the backlog.

**Limits.** These numbers come from the fake LLM. They gate the pipeline (retrieval, deterministic risk, grounding guardrails), not model quality. An LLM-as-judge gate on real model output is the next candidate (BACKLOG).

## D-054: CI/CD
**Status:** Accepted (2026-11-17, Phase 8a)

**Decision.** GitHub Actions (`.github/workflows/ci.yml`). Each job backs a row of the verification table in ARCHITECTURE.md:

| Job | What it proves |
|---|---|
| `static` | ruff, formatting, `mypy --strict`, lockfile in sync, OpenAPI and dashboard regenerated and committed |
| `test` | The full suite against **real Postgres 17 and Neo4j 2026.09** service containers. `REQUIRE_NEO4J=1` turns the Neo4j contract skips into failures, and the JUnit report is checked for *any* skip. |
| `observability` | promtool config and rule checks, plus the alert unit tests |
| `frontend` | `tsc --strict` and the production build |
| `evaluation` | dev and held-out evaluations through the quality gate (blocking), plus the non-blocking scale run |
| `image` | Runtime image built with the GHA layer cache; Trivy fails on fixable HIGH or CRITICAL; the image must not run as root |
| `compose-smoke` | The real `docker compose` stack, seeded, then `scripts/smoke_test.py` walks login → start (with an idempotent retry) → awaiting review → citations resolve → four-eyes refusal → approve → report → audit chain → metrics |

- **Release.** `release.yml` runs on a `v*.*.*` tag. It pushes the app and frontend images to GHCR with an SBOM, max-mode provenance and a build attestation.
- **Dependabot** covers uv, npm, Docker base images and the actions themselves.

**Why the no-skip rule.** The most common way a "tested" claim goes false is a test that quietly starts skipping. The Neo4j contract tests skip locally by design, so CI is the one place they must run, and it checks that they did.

**Why the smoke test is standard-library Python.** It runs on the bare runner against the deployed stack, needs no install, and so tests the deployment rather than the dev environment.

**Verification status.**
- **Verified here:** the workflows pass `actionlint`.
- **Verified here:** the smoke test passes against a locally run API and worker (memory graph, fake LLM), and it fails on bad credentials.
- **Verified here:** the evaluation and gate steps were run locally exactly as CI runs them.
- **Not verified here:** running on GitHub, the Neo4j service container, Trivy findings and the compose smoke test, all of which need Docker Hub. The first CI run is the real test of those jobs.

---

## Challenges to the original spec

These are the points where the spec was pushed back on or tightened. Each one is reflected in a decision above.

1. **Neo4j Community has no role-based access control.** "Database access controls" for Neo4j can only mean
   application-side enforcement plus read-mode transactions and a single least-privilege user. That's acceptable for this project, and it's documented (D-008, D-016).
2. **`(Device)-[:ACCESSED_FROM]->(IPAddress)` loses attribution** when a device is shared. The fix is customer-attributed login edges (D-018).
3. **The LLM planner may add little.** For a known alert type, the right operations are mostly deterministic. It's kept, but constrained to a catalog,
   given a deterministic fallback, and measured against a deterministic baseline (D-011).
4. **Two stores for documents** (metadata in Postgres, chunks in Neo4j) create a consistency problem. The answer is an explicit ingestion state machine plus reconciliation (D-005).
5. **A separate worker doesn't need Celery or Kafka.** A Postgres queue is simpler and transactionally safe (D-007).
6. **`neo4j-graphrag` overlaps with LangChain retrievers.** Choose one, based on a benchmark (D-009).
7. **Running the whole stack locally costs OpenAI tokens.** A fake provider makes `docker compose up` and the tests free and deterministic (D-010).
8. **The earlier roadmap (Kafka, GDS) is withdrawn**, because the spec's constraints are right for this scope (D-007, D-017).
