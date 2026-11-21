"""Fast checks on the simulator: determinism, label integrity, no label leakage."""

import json

import pytest

from simulation.config import SimConfig
from simulation.simulate import account_labels, run, write


@pytest.fixture(scope="module")
def world():
    return run(SimConfig(seed=1, n_customers=600, days=30))


def test_deterministic():
    a = run(SimConfig(seed=3, n_customers=300, days=30))
    b = run(SimConfig(seed=3, n_customers=300, days=30))
    assert [t["amount"] for t in a.txns] == [t["amount"] for t in b.txns]


def test_every_ring_has_ground_truth(world):
    assert len(world.rings) == 14  # 13 attack rings + spec example
    for r in world.rings:
        assert r["roles"] or r["victim_accounts"], r["ring_id"]
        assert r["start"] is not None and r["start"] <= r["end"]


def test_fraud_txns_reference_known_rings(world):
    ids = {r["ring_id"] for r in world.rings}
    assert all(t["ring_id"] in ids for t in world.txns if t["is_fraud"])


def test_no_customer_is_both_actor_and_victim(world):
    lab = account_labels(world)
    assert lab.account_id.is_unique
    assert set(lab.label) <= {"benign", "fraud_actor", "victim"}


def test_outputs_and_alert_truth_kept_separate(world, tmp_path):
    write(world, tmp_path)
    alerts = json.loads((tmp_path / "alerts/alerts.json").read_text())
    truth = json.loads((tmp_path / "labels/alert_truth.json").read_text())
    assert len(alerts) == len(truth) >= 15
    assert all("is_fraud" not in json.dumps(a) for a in alerts)
    assert {t["is_fraud"] for t in truth} == {True, False}  # true and false positives
    assert (tmp_path / "entities/beneficiaries.csv").exists()


def test_spec_example_scenario(world):
    spec = world.extras["spec"]
    bens = spec["beneficiaries"]
    assert len(bens) == 3
    # all three beneficiaries logged in from the shared device
    users = {e["customer_id"] for e in world.logins if e["device_id"] == spec["shared_device"]}
    assert {world.accounts[b]["customer_id"] for b in bens} <= users
    statuses = {c["status"] for c in world.extras["fraud_cases"] for a in c["accounts"] if a["account_id"] in bens}
    assert statuses == {"confirmed_fraud", "inconclusive"}
    assert any(c["status"] == "cleared" for c in world.extras["fraud_cases"])


def test_benign_traps_exist(world):
    for t in (
        "household_shared_device",
        "payroll_fan_out",
        "shopkeeper_fan_in",
        "benign_new_phone",
        "friends_bill_split_cycle",
        "shared_carrier_or_wifi_ip",
    ):
        assert world.traps[t], t
