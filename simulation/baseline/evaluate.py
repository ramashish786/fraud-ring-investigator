"""Score detectors against ground truth and against naive baselines.

    python -m simulation.baseline.evaluate --data data --out reports

Metric definitions
* fraud-involved precision: flagged accounts that are a fraud actor OR a victim / all flagged
* actor recall / victim recall: share of labelled actors / victims flagged by any graph detector
* ring recall: a ring counts as caught when >= 50% of its labelled accounts are flagged
* false positives are explained by the benign "trap" they fell into
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import pandas as pd

from .detectors import ALL, Data, Finding


# ------------------------------------------------------------ naive baselines
def naive_shared_ip(D: Data, k=5):
    lg = D.logins[D.logins.success]
    n = lg.groupby("ip").customer_id.nunique()
    custs = set(lg[lg.ip.isin(n[n >= k].index)].customer_id)
    return {a for c in custs for a in D.cust_accts.get(c, [])}


def naive_new_device_reset(D: Data):
    lg = D.logins[D.logins.success]
    first = lg.groupby(["customer_id", "device_id"]).ts.min().reset_index()
    first = first[first.ts > first.customer_id.map(lg.groupby("customer_id").ts.min())]
    resets = D.events[D.events.kind == "password_reset"]
    m = first.merge(resets, on="customer_id", suffixes=("", "_e"))
    hit = m[(m.ts_e >= m.ts) & (m.ts_e <= m.ts + pd.Timedelta(hours=1))]
    return {a for c in hit.customer_id for a in D.cust_accts.get(c, [])}


def naive_fan_in(D: Data, k=20):
    tx = D.tx[D.tx.src_account.notna() & D.tx.dst_account.notna()]
    fan = tx.groupby("dst_account").src_account.nunique()
    return set(fan[fan >= k].index)


def naive_fan_out(D: Data, k=20):
    tx = D.tx[D.tx.src_account.notna() & D.tx.dst_account.notna() & (D.tx.channel != "internal")]
    fan = tx.groupby("src_account").dst_account.nunique()
    return set(fan[fan >= k].index)


def naive_any_cycle(D: Data):
    tx = D.tx[D.tx.src_account.notna() & D.tx.dst_account.notna()]
    G = nx.DiGraph(list(tx[["src_account", "dst_account"]].drop_duplicates().itertuples(index=False, name=None)))
    return {n for c in nx.simple_cycles(G, length_bound=3) for n in c}


BASELINES = {  # rule -> (fn, the benign behaviour it is expected to trip over)
    "shared IP (≥5 users)": (naive_shared_ip, "shared_carrier_or_wifi_ip"),
    "new device + password reset": (naive_new_device_reset, "benign_new_phone"),
    "high fan-in (≥20 senders)": (naive_fan_in, "shopkeeper_fan_in"),
    "high fan-out (≥20 recipients)": (naive_fan_out, "payroll_fan_out"),
    "any money cycle (≤3 hops)": (naive_any_cycle, "friends_bill_split_cycle"),
}


# ------------------------------------------------------------------ scoring
TRAP_PRIORITY = [
    "payroll_fan_out",
    "shopkeeper_fan_in",
    "benign_new_phone",
    "household_shared_device",
    "shared_carrier_or_wifi_ip",
    "household_shared_address_ip",
    "friends_bill_split_cycle",
]


def trap_index(D: Data, traps: dict) -> dict:
    """account -> traps it (or its owner, or an IP it used) belongs to."""
    idx = defaultdict(set)
    for t, ids in traps.items():
        ids = set(ids)
        if t == "shared_carrier_or_wifi_ip":
            lg = D.logins[D.logins.ip.isin(ids)]
            ids = set(lg.customer_id)
        for i in ids:
            for a in [i] if i.startswith("A") else D.cust_accts.get(i, []):
                idx[a].add(t)
    return idx


def trap_of(accounts, idx, prefer=None):
    """Explain FPs. With `prefer`, only that trap counts; everything else is ordinary behaviour."""
    order = [prefer] if prefer else TRAP_PRIORITY
    out = Counter()
    for a in accounts:
        tags = idx.get(a, set())
        out[next((t for t in order if t in tags), "ordinary_customer" if prefer else "no_trap")] += 1
    return out


def score(flagged: set, lab: pd.DataFrame):
    involved = set(lab[lab.label != "benign"].account_id)
    actors, victims = set(lab[lab.label == "fraud_actor"].account_id), set(lab[lab.label == "victim"].account_id)
    tp = flagged & involved
    return {
        "flagged": len(flagged),
        "precision": len(tp) / len(flagged) if flagged else 0.0,
        "actor_recall": len(flagged & actors) / len(actors),
        "victim_recall": len(flagged & victims) / len(victims),
        "benign_fp": len(flagged - involved),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="reports")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    D = Data(a.data)
    lab = pd.read_csv(Path(a.data) / "labels/account_labels.csv")
    rings = json.loads((Path(a.data) / "labels/rings.json").read_text())
    traps = json.loads((Path(a.data) / "labels/traps.json").read_text())
    benign = set(lab[lab.label == "benign"].account_id)
    tidx = trap_index(D, traps)

    lines = [
        "# Detection results",
        "",
        f"Dataset: {len(D.accounts):,} accounts, {len(D.tx):,} transactions, {len(D.logins):,} logins, "
        f"{len(rings)} planted rings ({(lab.label == 'fraud_actor').sum()} actor accounts, "
        f"{(lab.label == 'victim').sum()} victim accounts).",
        "",
    ]

    # graph detectors
    findings: list[Finding] = []
    lines += [
        "## Graph detectors",
        "",
        "| Detector | Findings | Accounts flagged | Precision | Actor recall | Victim recall | Benign FPs | Runtime |",
        "|---|---|---|---|---|---|---|---|",
    ]
    all_flagged = set()
    for det in ALL:
        t = time.time()
        fs = det(D)
        rt = time.time() - t
        findings += fs
        flagged = set().union(*(f.accounts for f in fs)) if fs else set()
        all_flagged |= flagged
        s = score(flagged, lab)
        lines.append(
            f"| `{det.__name__}` | {len(fs)} | {s['flagged']} | {s['precision']:.0%} | {s['actor_recall']:.0%} | "
            f"{s['victim_recall']:.0%} | {s['benign_fp']} | {rt:.1f}s |"
        )
    s = score(all_flagged, lab)
    lines.append(
        f"| **all combined** | {len(findings)} | **{s['flagged']}** | **{s['precision']:.0%}** | "
        f"**{s['actor_recall']:.0%}** | **{s['victim_recall']:.0%}** | **{s['benign_fp']}** | |"
    )
    combined = s

    # ring recall
    lines += [
        "",
        "## Ring recall",
        "",
        "| Ring | Pattern | Labelled accounts | Flagged | Caught |",
        "|---|---|---|---|---|",
    ]
    caught = 0
    for r in rings:
        members = set(r["actor_accounts"]) | set(r["victim_accounts"])
        hit = len(members & all_flagged)
        ok = hit >= 0.5 * len(members)
        caught += ok
        lines.append(f"| {r['ring_id']} | {r['pattern']} | {len(members)} | {hit} | {'✅' if ok else '❌'} |")
    lines += ["", f"**{caught}/{len(rings)} rings caught.**", ""]

    fp = all_flagged & benign
    if fp:
        lines += ["Benign accounts flagged by graph detectors, by trap:", ""]
        for t, n in trap_of(fp, tidx).most_common():
            lines.append(f"- {t}: {n}")
        lines.append("")

    # naive baselines
    lines += [
        "## Naive rule baselines (no graph context)",
        "",
        "| Rule | Accounts flagged | Precision | Actor recall | Victim recall | Benign FPs | Top FP cause |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, (fn, prefer) in BASELINES.items():
        fl = fn(D)
        s = score(fl, lab)
        top = trap_of(fl & benign, tidx, prefer).most_common(1)
        cause = f"{top[0][0]} ({top[0][1] / max(1, s['benign_fp']):.0%})" if top else "-"
        lines.append(
            f"| {name} | {s['flagged']:,} | {s['precision']:.1%} | {s['actor_recall']:.0%} | {s['victim_recall']:.0%} | "
            f"{s['benign_fp']:,} | {cause} |"
        )

    (out / "results.md").write_text("\n".join(lines) + "\n")
    cases = [
        {
            "case_id": f"CASE-{i + 1:04d}",
            "detector": f.detector,
            "kind": f.kind,
            "score": f.score,
            "accounts": sorted(f.accounts),
            "devices": sorted(f.devices),
            "evidence": f.evidence,
        }
        for i, f in enumerate(sorted(findings, key=lambda f: -f.score))
    ]
    (out / "cases.json").write_text(json.dumps(cases, indent=2, default=str))
    print("\n".join(lines))
    print(f"\nwrote {out}/results.md and {len(cases)} cases to {out}/cases.json")
    return combined


if __name__ == "__main__":
    main()
