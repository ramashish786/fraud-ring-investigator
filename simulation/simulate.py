"""CLI: python -m simulation.simulate --out data --customers 4000 --days 60 --seed 42

Writes
  data/entities/*.csv        customers, accounts, devices, ips, merchants, cards, beneficiaries
  data/events/*.csv          logins, transactions, account_events, referrals
  data/cases/fraud_cases.json historical investigations (confirmed, inconclusive, cleared)
  data/alerts/alerts.json    monitoring alerts to feed POST /api/v1/alerts
  data/labels/*.json|csv     ground truth: rings, account labels, benign traps, alert truth
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from . import attacks, extensions
from .config import AttackMix, SimConfig
from .world import World


def run(cfg: SimConfig) -> World:
    w = World(cfg)
    w.build_population()
    w.simulate_benign()
    attacks.benign_referrals(w)
    mix = cfg.attacks
    for i in range(mix.mule_networks):
        attacks.mule_network(w, i + 1)
    for i in range(mix.synthetic_identity_rings):
        attacks.synthetic_identity(w, i + 1)
    for i in range(mix.card_testing_campaigns):
        attacks.card_testing(w, i + 1)
    for i in range(mix.promo_abuse_rings):
        attacks.promo_abuse(w, i + 1)
    for i in range(mix.round_trip_rings):
        attacks.round_tripping(w, i + 1)
    spec = extensions.spec_example(w)
    w.extras = {
        "spec": spec["spec"],
        "beneficiaries": extensions.build_beneficiaries(w),
        "fraud_cases": extensions.build_historical_cases(w, spec["spec"]),
    }
    w.extras["alerts"], w.extras["alert_truth"] = extensions.build_alerts(w, spec["spec"])
    return w


def account_labels(w: World) -> pd.DataFrame:
    rows = {a: {"account_id": a, "label": "benign", "ring_id": None, "pattern": None, "role": None} for a in w.accounts}
    for r in w.rings:
        for a in r["victim_accounts"]:
            rows[a].update(label="victim", ring_id=r["ring_id"], pattern=r["pattern"], role="victim")
        for a, role in r["roles"].items():
            rows[a].update(label="fraud_actor", ring_id=r["ring_id"], pattern=r["pattern"], role=role)
    df = pd.DataFrame(rows.values())
    df["customer_id"] = df.account_id.map(lambda a: w.accounts[a]["customer_id"])
    return df


def write(w: World, out: Path):
    for sub in ("entities", "events", "labels"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    ent = {
        "customers": w.customers,
        "accounts": w.accounts,
        "devices": w.devices,
        "ips": w.ips,
        "merchants": w.merchants,
        "cards": w.cards,
    }
    for name, table in ent.items():
        df = pd.DataFrame(table.values())
        if name == "customers":  # household comparisons without exposing the address itself
            df["address_key"] = df.address.map(lambda a: hashlib.sha256(a.encode()).hexdigest()[:16])
        df.to_csv(out / "entities" / f"{name}.csv", index=False)
    events = {"logins": w.logins, "transactions": w.txns, "account_events": w.account_events, "referrals": w.referrals}
    frames = {}
    for name, rows in events.items():
        df = pd.DataFrame(rows).sort_values("ts")
        df.to_csv(out / "events" / f"{name}.csv", index=False)
        frames[name] = df

    pd.DataFrame(w.extras["beneficiaries"]).sort_values("added_at").to_csv(
        out / "entities" / "beneficiaries.csv", index=False
    )
    for sub, name, key in (
        ("cases", "fraud_cases", "fraud_cases"),
        ("alerts", "alerts", "alerts"),
        ("labels", "alert_truth", "alert_truth"),
        ("labels", "spec_example", "spec"),
    ):
        (out / sub).mkdir(parents=True, exist_ok=True)
        (out / sub / f"{name}.json").write_text(json.dumps(w.extras[key], indent=2, default=str))

    rings = []
    for r in w.rings:
        rings.append(
            {
                "ring_id": r["ring_id"],
                "pattern": r["pattern"],
                "narrative": r["narrative"],
                "start": r["start"].isoformat() if r["start"] else None,
                "end": r["end"].isoformat() if r["end"] else None,
                "actor_accounts": r["roles"],
                "victim_accounts": sorted(r["victim_accounts"]),
                "victim_cards": sorted(r["victim_cards"]),
                "devices": sorted(r["devices"]),
                "ips": sorted(r["ips"]),
            }
        )
    (out / "labels" / "rings.json").write_text(json.dumps(rings, indent=2))
    (out / "labels" / "traps.json").write_text(json.dumps({k: sorted(v) for k, v in w.traps.items()}, indent=2))
    account_labels(w).to_csv(out / "labels" / "account_labels.csv", index=False)


def main():
    p = argparse.ArgumentParser(description="Synthetic bank + fraud ring attack simulator")
    p.add_argument("--out", default="data")
    p.add_argument("--customers", type=int, default=4000)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--camouflage", type=float, default=0.5, help="0 = noisy attackers, 1 = patient & stealthy")
    a = p.parse_args()
    cfg = SimConfig(seed=a.seed, n_customers=a.customers, days=a.days, camouflage=a.camouflage, attacks=AttackMix())
    t = time.time()
    w = run(cfg)
    write(w, Path(a.out))
    fraud_tx = sum(1 for x in w.txns if x["is_fraud"])
    print(f"customers={len(w.customers):,} accounts={len(w.accounts):,} devices={len(w.devices):,} ips={len(w.ips):,}")
    print(f"transactions={len(w.txns):,} (fraud={fraud_tx:,}, {fraud_tx / len(w.txns):.2%}) logins={len(w.logins):,}")
    print(f"rings={len(w.rings)} -> " + ", ".join(r["ring_id"] for r in w.rings))
    print(f"done in {time.time() - t:.1f}s -> {a.out}/")


if __name__ == "__main__":
    main()
