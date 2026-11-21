"""Release quality gate: compare evaluation reports against config/quality_gates.yaml (D-053).

    python -m app.evaluation.gate reports/evaluation.json reports/holdout/evaluation.json

Every report must pass every gate. Prints a table and exits 1 on any failure, so CI can block the merge.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "quality_gates.yaml"


@dataclass(frozen=True)
class Gate:
    metric: str
    min: float | None = None
    max: float | None = None
    equals_metric: str | None = None
    hard: bool = False

    def describe(self) -> str:
        if self.equals_metric:
            return f"== {self.equals_metric}"
        parts = []
        if self.min is not None:
            parts.append(f">= {self.min}")
        if self.max is not None:
            parts.append(f"<= {self.max}")
        return " and ".join(parts)


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    value: float | None
    passed: bool
    reason: str = ""


class GateConfigError(ValueError):
    pass


def load_gates(path: Path = DEFAULT_CONFIG) -> list[Gate]:
    raw = yaml.safe_load(path.read_text())
    gates = []
    for i, g in enumerate(raw.get("gates") or []):
        unknown = set(g) - {"metric", "min", "max", "equals_metric", "hard"}
        if unknown or "metric" not in g:
            raise GateConfigError(f"gate #{i}: unknown keys {sorted(unknown)} or missing 'metric'")
        gate = Gate(**g)
        if gate.min is None and gate.max is None and gate.equals_metric is None:
            raise GateConfigError(f"gate {gate.metric}: needs min, max or equals_metric")
        gates.append(gate)
    if not gates:
        raise GateConfigError("no gates configured")
    return gates


def lookup(report: dict[str, Any], dotted: str) -> float | None:
    """Resolve 'a.b.c' against nested dicts. Keys may contain dots-free names like 'recall@5'."""
    node: Any = report
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, int | float) or math.isnan(node):
        return None
    return float(node)


def evaluate(report: dict[str, Any], gates: list[Gate]) -> list[GateResult]:
    results = []
    for g in gates:
        value = lookup(report, g.metric)
        if value is None:
            # A missing metric fails closed: a refactor that silently stops measuring something must not pass.
            results.append(GateResult(g, None, False, "metric missing from report"))
            continue
        if g.equals_metric is not None:
            other = lookup(report, g.equals_metric)
            ok = other is not None and value == other
            results.append(GateResult(g, value, ok, "" if ok else f"{g.equals_metric} = {other}"))
            continue
        ok = (g.min is None or value >= g.min) and (g.max is None or value <= g.max)
        results.append(GateResult(g, value, ok))
    return results


def render(name: str, results: list[GateResult]) -> str:
    lines = [f"## {name}", "", "| | metric | value | gate |", "|---|---|---|---|"]
    for r in results:
        mark = "PASS" if r.passed else ("FAIL (hard)" if r.gate.hard else "FAIL")
        val = "—" if r.value is None else f"{r.value:g}"
        note = f" ({r.reason})" if r.reason else ""
        lines.append(f"| {mark} | `{r.gate.metric}` | {val} | {r.gate.describe()}{note} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--summary", type=Path, help="append the markdown table here (e.g. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args(argv)

    gates = load_gates(args.config)
    failed = 0
    out = []
    for path in args.reports:
        results = evaluate(json.loads(path.read_text()), gates)
        failed += sum(not r.passed for r in results)
        out.append(render(str(path), results))
    text = "\n\n".join(out) + f"\n\n**Quality gate: {'FAILED' if failed else 'PASSED'}** ({failed} failing)\n"
    print(text)
    if args.summary:
        with args.summary.open("a") as fh:
            fh.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
