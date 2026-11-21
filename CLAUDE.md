# Working agreement for this repository

These rules apply to anyone, human or AI, changing this codebase.

## Living documents
- `ARCHITECTURE.md` describes the system **as it is**, plus the agreed target. Update it in the **same change** as any edit to
  module boundaries, data ownership, the workflow graph, the Docker topology or the request lifecycle.
- `DECISIONS.md` is the decision log. A new or changed decision gets a new `D-xxx` entry. An old entry is never rewritten to
  change its meaning; mark it `Superseded by D-xxx` instead. Keep the index table in sync.
- `docs/BACKLOG.md`: tick items only when their verification has actually run.

## Honesty rules
- Never mark anything *Implemented* or *done* without tests that ran. If something couldn't be run (Neo4j or Docker isn't
  available in the workspace), say "not verified" and give the exact command to verify it.
- Never invent test output or metrics.

## Engineering rules (from the project spec)
- Business logic stays out of API routes and LangGraph nodes. Database code stays in repositories.
- No LLM-generated Cypher. Only catalog queries (`app/graph/queries`), using read transactions with timeouts and clamped limits.
- The LLM never produces risk scores, evidence or citations outside the evidence IDs it was given.
- No secrets, prompts or customer PII in logs.
- Write tests alongside code. Run them before declaring a phase complete.
- Work phase by phase (`docs/BACKLOG.md`) and wait for approval before major changes.
- Any change to Cypher (`app/graph/queries.py`, `app/graph/loader.py`, `app/rag/store.py`) must pass the Neo4j half of
  the contract tests: `docker compose --profile test run --rm --build tests`.
- The in-memory graph and chunk stores are test doubles (D-026). Keep their behaviour identical to Neo4j by extending the
  contract tests, never by special-casing tests.
- Risk scoring stays in `config/risk_rules.yaml` + `app/domains/risk`. Bump `version` when rules change.
- **Metrics:** label values must come from a bounded set. Never put ids, emails or account numbers in a label (D-051).
- **Dashboard:** change `scripts/build_dashboard.py`, never the generated JSON. Regenerate and commit; CI checks it.
- **Quality gates:** a change that lowers a floor in `config/quality_gates.yaml` needs a DECISIONS entry. Hard gates are never lowered.
- **Alert rules:** a change to `docker/prometheus/rules/` comes with a matching case in `slo.rules.test.yml`.

## Commands
`make test` (Postgres at TEST_DATABASE_URL) · `make lint` · `make up && make seed && make smoke` · `make test-docker` ·
`make eval && make gate` · `make rules` · `make dashboard` · `make observe`
