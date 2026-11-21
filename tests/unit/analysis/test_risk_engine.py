from app.core.enums import Severity
from app.domains.analysis.signals import Signal
from app.domains.risk.engine import RiskEngine

ENGINE = RiskEngine.from_yaml()


def sig(name: str, value: float, refs: list[str] | None = None) -> tuple[str, Signal]:
    return name, Signal(name=name, value=value, evidence_refs=refs or [f"E-{name[:8]}"])


def test_no_signals_is_low_zero() -> None:
    r = ENGINE.assess({})
    assert (r.score, r.category, r.factors) == (0, Severity.LOW, [])


def test_spec_example_profile_is_critical_and_cites_evidence() -> None:
    r = ENGINE.assess(
        dict(
            [
                sig("pass_through_events", 2),
                sig("structured_deposits", 2),
                sig("new_beneficiary_transfers", 3),
                sig("beneficiaries_sharing_identifiers", 3),
                sig("linked_confirmed_fraud_cases", 1),
                sig("risky_shared_ips", 1),
            ]
        )
    )
    assert r.category == Severity.CRITICAL and r.score == 100
    assert all(f.evidence_refs for f in r.factors)
    caps = {f.rule_id: f.weight for f in r.factors}
    assert caps["R-BEN-01"] == 15  # per-unit growth capped at max


def test_household_mitigation_only_without_real_links() -> None:
    hh = ENGINE.assess(dict([sig("household_shared_devices", 1), sig("velocity_24h", 12)]))
    assert hh.score == 0 and any(f.rule_id == "M-HH-01" for f in hh.factors)
    mixed = ENGINE.assess(dict([sig("household_shared_devices", 1), sig("shared_device_links", 1)]))
    assert not any(f.rule_id == "M-HH-01" for f in mixed.factors) and mixed.score == 10


def test_deterministic() -> None:
    s = dict([sig("pass_through_events", 4), sig("emulator_devices", 1)])
    assert ENGINE.assess(s) == ENGINE.assess(s)
