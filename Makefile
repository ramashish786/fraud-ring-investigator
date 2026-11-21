.PHONY: up seed down logs ready test-docker test test-unit lint typecheck eval gate smoke observe rules dashboard openapi ui-dev
up:          ; docker compose up --build -d && docker compose ps
seed:        ; docker compose --profile seed run --rm seed
down:        ; docker compose down
logs:        ; docker compose logs -f api worker
ready:       ; curl -s localhost:8000/api/v1/health/ready | python -m json.tool
test-docker: ; docker compose --profile test run --rm --build tests   # includes Neo4j contract tests
test:        ; uv run pytest -q                                        # Postgres at TEST_DATABASE_URL
test-unit:   ; uv run pytest -q tests/unit tests/contract
lint:        ; uv run ruff check . && uv run ruff format --check . && uv run mypy
eval:        ; uv run python -m app.evaluation.investigations --data data --out reports
gate:        ; uv run python -m app.evaluation.gate reports/evaluation.json reports/holdout/evaluation.json
smoke:       ; python3 scripts/smoke_test.py --password "$${SEED_USER_PASSWORD:-ChangeMe-123!}"   # after make up && make seed
observe:     ; docker compose --profile observability up -d   # Prometheus :9090, Grafana :3001, Jaeger :16686
rules:       ; cd docker/prometheus/rules && promtool check rules slo.rules.yml && promtool test rules slo.rules.test.yml
dashboard:   ; uv run python scripts/build_dashboard.py
openapi:     ; uv run python -m scripts.export_openapi
ui-dev:      ; cd frontend && npm install && npm run dev
