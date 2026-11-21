"""The release gate must fail closed: a regression, a missing metric or a broken invariant blocks the merge."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.gate import DEFAULT_CONFIG, Gate, GateConfigError, evaluate, load_gates, main

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def report() -> dict[str, Any]:
    return json.loads((ROOT / "reports/evaluation.json").read_text())


def failing(report: dict[str, Any]) -> list[str]:
    return [r.gate.metric for r in evaluate(report, load_gates()) if not r.passed]


@pytest.mark.parametrize("path", ["reports/evaluation.json", "reports/holdout/evaluation.json"])
def test_committed_reports_pass(path: str) -> None:
    assert main([str(ROOT / path)]) == 0


def test_recall_regression_fails(report: dict[str, Any]) -> None:
    report["detection_medium_plus"]["recall"] = 0.8
    assert failing(report) == ["detection_medium_plus.recall"]


def test_any_unsupported_claim_fails(report: dict[str, Any]) -> None:
    report["unsupported_claim_rate"] = 0.01
    assert failing(report) == ["unsupported_claim_rate"]


def test_missing_metric_fails_closed(report: dict[str, Any]) -> None:
    del report["retrieval"]["hybrid"]["mrr"]
    assert failing(report) == ["retrieval.hybrid.mrr"]


def test_crashed_alert_fails_equality_gate(report: dict[str, Any]) -> None:
    broken = copy.deepcopy(report)
    broken["environment"]["completed"] = broken["environment"]["alerts"] - 1
    assert failing(broken) == ["environment.completed"]


def test_non_numeric_value_is_treated_as_missing(report: dict[str, Any]) -> None:
    report["latency_s"]["p95"] = "fast"
    assert failing(report) == ["latency_s.p95"]


def test_main_exits_nonzero_and_writes_summary(tmp_path: Path, report: dict[str, Any]) -> None:
    report["citation_correctness"] = 0.99
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(report))
    summary = tmp_path / "summary.md"
    assert main([str(bad), "--summary", str(summary)]) == 1
    assert "FAIL (hard)" in summary.read_text()


def test_config_rejects_gate_without_bound(tmp_path: Path) -> None:
    cfg = tmp_path / "g.yaml"
    cfg.write_text("gates:\n  - {metric: x}\n")
    with pytest.raises(GateConfigError):
        load_gates(cfg)


def test_config_rejects_typos(tmp_path: Path) -> None:
    cfg = tmp_path / "g.yaml"
    cfg.write_text("gates:\n  - {metric: x, minimum: 1}\n")
    with pytest.raises(GateConfigError):
        load_gates(cfg)


def test_default_config_loads() -> None:
    gates = load_gates(DEFAULT_CONFIG)
    assert all(isinstance(g, Gate) for g in gates)
    assert {g.metric for g in gates if g.hard} >= {"citation_correctness", "unsupported_claim_rate"}
