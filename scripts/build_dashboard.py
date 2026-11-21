"""Generate the Grafana dashboard JSON from code so panels stay reviewable in diffs (D-052).

uv run python scripts/build_dashboard.py   # writes docker/grafana/dashboards/fri-overview.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DS = {"type": "prometheus", "uid": "fri-prometheus"}
OUT = Path(__file__).resolve().parents[1] / "docker/grafana/dashboards/fri-overview.json"

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def row(title: str) -> dict[str, Any]:
    return {"type": "row", "title": title, "id": _id(), "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0}}


def stat(
    title: str,
    expr: str,
    x: int,
    unit: str = "short",
    w: int = 6,
    thresholds: list[tuple[str, float | None]] | None = None,
    desc: str = "",
) -> dict[str, Any]:
    steps = [{"color": c, "value": v} for c, v in (thresholds or [("green", None)])]
    return {
        "type": "stat",
        "title": title,
        "id": _id(),
        "datasource": DS,
        "description": desc,
        "gridPos": {"h": 5, "w": w, "x": x},
        "targets": [{"refId": "A", "expr": expr, "datasource": DS}],
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "area",
            "textMode": "auto",
        },
    }


def ts(
    title: str,
    targets: list[tuple[str, str]],
    x: int,
    unit: str = "short",
    w: int = 12,
    desc: str = "",
    stack: bool = False,
) -> dict[str, Any]:
    return {
        "type": "timeseries",
        "title": title,
        "id": _id(),
        "datasource": DS,
        "description": desc,
        "gridPos": {"h": 8, "w": w, "x": x},
        "targets": [
            {"refId": chr(65 + i), "expr": e, "legendFormat": leg, "datasource": DS}
            for i, (e, leg) in enumerate(targets)
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "lineWidth": 1,
                    "fillOpacity": 10,
                    "showPoints": "never",
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def quantile(q: float, metric: str, by: str = "", window: str = "5m", selector: str = "") -> str:
    labels = ", ".join(["le", *([by] if by else [])])
    sel = f"{{{selector}}}" if selector else ""
    return f"histogram_quantile({q}, sum by ({labels}) (rate({metric}_bucket{sel}[{window}])))"


def _stack(panels: list[dict[str, Any]]) -> None:
    """Assign y positions: a panel at x=0 (or a row) starts a new line below the tallest panel of the previous line."""
    y = line_h = 0
    for panel in panels:
        pos = panel["gridPos"]
        if pos["x"] == 0:
            y += line_h
            line_h = 0
        pos["y"] = y
        line_h = max(line_h, pos["h"])


API = 'route=~"/api/v1/.*", route!~"/api/v1/health.*"'


def build() -> dict[str, Any]:
    p: list[dict[str, Any]] = []
    p.append(row("Service level objectives (30-day targets, docs/SLO.md)"))
    p += [
        stat(
            "API availability (1h)",
            "1 - fri:sli_api_errors:ratio_rate1h",
            0,
            "percentunit",
            thresholds=[("red", None), ("orange", 0.99), ("green", 0.995)],
            desc="Target 99.5%; health checks excluded.",
        ),
        stat(
            "Reads under 250 ms (1h)",
            "1 - fri:sli_api_slow_reads:ratio_rate1h",
            6,
            "percentunit",
            thresholds=[("red", None), ("orange", 0.9), ("green", 0.95)],
            desc="Target 95% of GET /api/v1/*.",
        ),
        stat(
            "Investigations ready < 5 min (6h)",
            "1 - fri:sli_investigation_slow:ratio_rate6h",
            12,
            "percentunit",
            thresholds=[("red", None), ("orange", 0.9), ("green", 0.95)],
            desc="Created → awaiting review. Target 95%.",
        ),
        stat(
            "Firing alerts",
            'count(ALERTS{alertstate="firing", alertname=~"FRI.*"}) or vector(0)',
            18,
            thresholds=[("green", None), ("red", 1)],
        ),
    ]
    p.append(
        ts(
            "Error budget burn rate (availability)",
            [
                ("fri:sli_api_errors:ratio_rate1h / 0.005", "1h"),
                ("fri:sli_api_errors:ratio_rate6h / 0.005", "6h"),
                ("vector(14.4)", "page threshold"),
                ("vector(6)", "ticket threshold"),
            ],
            0,
            desc="1.0 = spending budget exactly as fast as allowed over 30 days.",
        )
    )
    p.append(
        ts(
            "Investigation time to review (p50 / p95)",
            [
                (
                    quantile(0.5, "fri_investigation_time_to_review_seconds", "", "30m"),
                    "p50",
                ),
                (
                    quantile(0.95, "fri_investigation_time_to_review_seconds", "", "30m"),
                    "p95",
                ),
            ],
            12,
            "s",
        )
    )

    p.append(row("API"))
    p.append(
        ts(
            "Requests by status",
            [(f"sum by (status) (rate(fri_http_requests_total{{{API}}}[5m]))", "{{status}}")],
            0,
            "reqps",
            stack=True,
        )
    )
    p.append(
        ts(
            "p95 latency by route",
            [
                (
                    quantile(0.95, "fri_http_request_duration_seconds", "route", "5m", API),
                    "{{route}}",
                )
            ],
            12,
            "s",
        )
    )

    p.append(row("Investigations & risk"))
    p.append(
        ts(
            "Investigations started / finished",
            [
                ("sum(rate(fri_investigations_started_total[15m])) * 60", "started/min"),
                ("sum by (outcome) (rate(fri_investigations_finished_total[15m])) * 60", "{{outcome}}/min"),
            ],
            0,
        )
    )
    p.append(
        ts(
            "Risk assessments by category",
            [("sum by (category) (increase(fri_risk_assessments_total[1h]))", "{{category}}")],
            12,
            stack=True,
            desc="A shift here without a rules-version change suggests data drift.",
        )
    )

    p.append(row("Workflow & jobs"))
    p.append(
        ts(
            "Node p95 duration",
            [
                (
                    quantile(0.95, "fri_workflow_node_duration_seconds", "node", "15m"),
                    "{{node}}",
                )
            ],
            0,
            "s",
        )
    )
    p.append(
        ts(
            "Degraded nodes",
            [("sum by (node) (increase(fri_workflow_node_degraded_total[15m]))", "{{node}}")],
            12,
            desc="Nodes that fell back (e.g. LLM unavailable) and continued with reduced output.",
        )
    )
    p.append(ts("Queue depth by kind", [("sum by (kind) (fri_job_queue_depth)", "{{kind}}")], 0, w=8))
    p.append(ts("Oldest runnable job age", [("max(fri_job_queue_oldest_age_seconds)", "age")], 8, "s", w=8))
    p.append(
        ts(
            "Job outcomes",
            [
                ("sum by (status) (rate(fri_jobs_processed_total[5m])) * 60", "{{status}}/min"),
                ("max(fri_jobs_dead)", "dead (total)"),
                ("sum(fri_worker_running_jobs)", "running"),
            ],
            16,
            w=8,
        )
    )

    p.append(row("LLM & retrieval"))
    p.append(
        ts(
            "LLM calls by status",
            [("sum by (task, status) (rate(fri_llm_calls_total[5m])) * 60", "{{task}} {{status}}")],
            0,
            w=8,
        )
    )
    p.append(
        ts(
            "LLM tokens / min",
            [("sum by (task, type) (rate(fri_llm_tokens_total[5m])) * 60", "{{task}} {{type}}")],
            8,
            w=8,
            desc="Multiply by your model's price to watch spend.",
        )
    )
    p.append(
        ts(
            "Retrieval & graph p95",
            [
                (
                    quantile(0.95, "fri_retrieval_duration_seconds", "mode", "10m"),
                    "retrieval {{mode}}",
                ),
                (
                    quantile(0.95, "fri_graph_query_duration_seconds", "query", "10m"),
                    "graph {{query}}",
                ),
            ],
            16,
            "s",
            w=8,
        )
    )
    _stack(p)
    return {
        "uid": "fri-overview",
        "title": "Fraud Ring Investigator — Overview",
        "tags": ["fri", "slo"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "panels": p,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


if __name__ == "__main__":
    text = json.dumps(build(), indent=2) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit(f"{OUT} is stale: run `uv run python scripts/build_dashboard.py` and commit it")
        print(f"{OUT} is up to date")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text)
        print(f"wrote {OUT}")
