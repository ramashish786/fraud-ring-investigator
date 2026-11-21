"""Graph detectors (offline, pandas + networkx).

Each detector mirrors a Cypher query in simulation/neo4j_legacy/detectors.cypher, so the logic can be
unit-tested and benchmarked without a database, then served from Neo4j in prod.

Every detector returns Findings: what it flags, why (evidence), and whether the
flagged account is believed to be an *actor* (fraudster/mule) or a *victim*.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import pandas as pd

SHARED_INFRA = {"mobile_cgnat", "public_wifi", "corporate"}  # known-shared IP types (IP intelligence feed)
RISKY_IP = {"datacenter", "vpn"}


@dataclass
class Finding:
    detector: str
    accounts: set[str]
    kind: str  # "actor" | "victim"
    score: float
    evidence: dict = field(default_factory=dict)
    devices: set[str] = field(default_factory=set)


def _read(path, ts_cols=(), **kw) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False, **kw)
    for c in ts_cols:  # timestamps mix second/microsecond precision -> parse as ISO8601 explicitly
        df[c] = pd.to_datetime(df[c], format="ISO8601")
    return df


class Data:
    def __init__(self, d: str | Path):
        d = Path(d)
        self.customers = _read(d / "entities/customers.csv", ["created_at"])
        self.accounts = _read(d / "entities/accounts.csv", ["opened_at"])
        self.devices = pd.read_csv(d / "entities/devices.csv")
        self.ips = pd.read_csv(d / "entities/ips.csv")
        self.logins = _read(d / "events/logins.csv", ["ts"])
        tx = _read(d / "events/transactions.csv", ["ts"])
        self.tx = tx.drop(columns=["is_fraud", "ring_id", "pattern"])  # detectors never see labels
        self.events = _read(d / "events/account_events.csv", ["ts"]).drop(columns=["is_fraud", "ring_id"])
        self.referrals = _read(d / "events/referrals.csv", ["ts"]).drop(columns=["is_fraud", "ring_id"])
        self.acct_owner = dict(zip(self.accounts.account_id, self.accounts.customer_id))
        self.cust_accts = self.accounts.groupby("customer_id").account_id.apply(list).to_dict()
        self.ip_kind = dict(zip(self.ips.ip, self.ips.kind))
        self.emulator = dict(zip(self.devices.device_id, self.devices.emulator))
        self.sim_start = self.tx.ts.min().normalize()


# ---------------------------------------------------------------- D1 identity
def shared_identity_clusters(D: Data, min_size=5, min_score=2.0) -> list[Finding]:
    """Customers linked through shared devices / phones / addresses / non-shared IPs.
    Known shared infrastructure (carrier NAT, public wifi, corporate egress) is pruned —
    otherwise the whole city becomes one giant component."""
    G = nx.Graph()
    links = []
    lg = D.logins[D.logins.success]
    links += [(c, f"dev:{d}") for c, d in lg[["customer_id", "device_id"]].drop_duplicates().itertuples(index=False)]
    links += [
        (c, f"ip:{i}")
        for c, i in lg[["customer_id", "ip"]].drop_duplicates().itertuples(index=False)
        if D.ip_kind.get(i) not in SHARED_INFRA
    ]
    links += [(c, f"phone:{p}") for c, p in D.customers[["customer_id", "phone"]].itertuples(index=False)]
    links += [(c, f"addr:{a}") for c, a in D.customers[["customer_id", "address"]].itertuples(index=False)]
    G.add_edges_from(links)
    out = []
    for comp in nx.connected_components(G):
        custs = {n for n in comp if n.startswith("C")}
        if len(custs) < min_size:
            continue
        idents = comp - custs
        devs = {n[4:] for n in idents if n.startswith("dev:")}
        ips = {n[3:] for n in idents if n.startswith("ip:")}
        shared_devs = [d for d in devs if sum(1 for _ in G.neighbors(f"dev:{d}")) >= 3]
        shared_phones = [n for n in idents if n.startswith("phone:") and G.degree(n) >= 2]
        cdf = D.customers[D.customers.customer_id.isin(custs)]
        young = (cdf.created_at >= D.sim_start - pd.Timedelta(days=30)).mean()
        risky_ip = sum(D.ip_kind.get(i) in RISKY_IP for i in ips) / max(1, len(ips))
        emu = sum(bool(D.emulator.get(d)) for d in devs)
        score = (
            2.0 * young
            + 2.0 * risky_ip
            + 1.0 * min(emu, 2)
            + 0.5 * min(len(shared_devs), 4)
            + 0.5 * min(len(shared_phones), 4)
        )
        if score >= min_score:
            # flag only members touching a "hot" identifier, not every relative pulled in by a home IP
            hot = {f"dev:{d}" for d in devs if G.degree(f"dev:{d}") >= 5 or D.emulator.get(d)}
            hot |= {f"ip:{i}" for i in ips if D.ip_kind.get(i) in RISKY_IP}
            hot |= set(shared_phones)
            members = {c for c in custs if any(n in hot for n in G.neighbors(c))}
            accts = {a for c in members for a in D.cust_accts.get(c, [])}
            out.append(
                Finding(
                    "shared_identity_cluster",
                    accts,
                    "actor",
                    round(score, 2),
                    {
                        "customers": len(custs),
                        "shared_devices": len(shared_devs),
                        "shared_phones": len(shared_phones),
                        "young_share": round(young, 2),
                        "risky_ip_share": round(risky_ip, 2),
                        "emulators": emu,
                    },
                    devs,
                )
            )
    return out


# ------------------------------------------------------------ D2 money mules
def rapid_pass_through(D: Data, window_h=24, min_in=5_000, ratio=0.8, min_events=2) -> list[Finding]:
    """Funds that arrive and leave within hours, repeatedly — the signature of a mule."""
    tx = D.tx[D.tx.status == "approved"]
    inflow = tx[tx.dst_account.notna() & (tx.amount >= min_in)][["dst_account", "ts", "amount"]]
    outflow = tx[tx.src_account.notna() & (tx.merchant_id.isna() | (tx.channel == "atm"))][
        ["src_account", "ts", "amount"]
    ]
    out_by = {a: g.sort_values("ts") for a, g in outflow.groupby("src_account")}
    hits = defaultdict(list)
    for a, g in inflow.groupby("dst_account"):
        o = out_by.get(a)
        if o is None:
            continue
        for ts, amt in zip(g.ts, g.amount):
            sent = o[(o.ts > ts) & (o.ts <= ts + pd.Timedelta(hours=window_h))].amount.sum()
            if sent >= ratio * amt:
                hits[a].append((ts, amt, sent))
    struct = tx[(tx.channel == "cash_deposit") & (tx.amount.between(42_500, 49_999))].groupby("dst_account").size()
    out = []
    for a, h in hits.items():
        if len(h) >= min_events:
            s = len(h) + 2 * struct.get(a, 0)
            out.append(
                Finding(
                    "rapid_pass_through",
                    {a},
                    "actor",
                    float(s),
                    {
                        "pass_through_events": len(h),
                        "structured_deposits": int(struct.get(a, 0)),
                        "amount_passed_through": round(sum(x[2] for x in h)),
                    },
                )
            )
    return out


# ---------------------------------------------------------- D3 round trips
def value_cycles(D: Data, min_edge=100_000, max_len=6, max_decay=0.05) -> list[Finding]:
    """Directed cycles of large transfers where value comes back nearly intact."""
    tx = D.tx[(D.tx.status == "approved") & D.tx.src_account.notna() & D.tx.dst_account.notna()]
    big = (
        tx[tx.amount >= min_edge]
        .groupby(["src_account", "dst_account"])
        .amount.agg(["sum", "count", "mean"])
        .reset_index()
    )
    G = nx.DiGraph()
    for r in big.itertuples(index=False):
        G.add_edge(r.src_account, r.dst_account, total=r.sum, count=r.count, mean=r.mean)
    out, seen = [], set()
    for cyc in nx.simple_cycles(G, length_bound=max_len):
        if len(cyc) < 3:
            continue
        means = [G[cyc[i]][cyc[(i + 1) % len(cyc)]]["mean"] for i in range(len(cyc))]
        decay = 1 - min(means) / max(means)
        key = frozenset(cyc)
        if decay <= max_decay and key not in seen:
            seen.add(key)
            out.append(
                Finding(
                    "value_cycle",
                    set(cyc),
                    "actor",
                    round(len(cyc) * (1 - decay), 2),
                    {"cycle": cyc, "mean_amount": round(sum(means) / len(means)), "value_decay": round(decay, 4)},
                )
            )
    return out


# ------------------------------------------------------- D4 account takeover
def account_takeover(D: Data, horizon_min=120) -> list[Finding]:
    """New device + risky network + credential stuffing / reset / new beneficiary,
    followed by an outbound transfer to that new beneficiary."""
    lg = D.logins.sort_values("ts")
    ok = lg[lg.success]
    first_seen = ok.groupby(["customer_id", "device_id"]).ts.min().rename("first_seen").reset_index()
    ok = ok.merge(first_seen, on=["customer_id", "device_id"])
    new_dev = ok[ok.ts == ok.first_seen]  # first *successful* session on this device
    # ignore the customer's very first device (account opening)
    first_any = ok.groupby("customer_id").ts.min()
    new_dev = new_dev[new_dev.ts > new_dev.customer_id.map(first_any)]
    fails = lg[~lg.success]
    ev = D.events
    tx = D.tx[D.tx.dst_account.notna() & D.tx.src_account.notna()]
    out = []
    for r in new_dev.itertuples(index=False):
        t0, t1 = r.ts, r.ts + pd.Timedelta(minutes=horizon_min)
        score, why = 1.0, ["new_device"]
        if D.ip_kind.get(r.ip) in RISKY_IP:
            score += 1.5
            why.append(f"risky_ip:{D.ip_kind.get(r.ip)}")
        nf = ((fails.customer_id == r.customer_id) & fails.ts.between(t0 - pd.Timedelta(minutes=30), t0)).sum()
        if nf:
            score += 1.0
            why.append(f"failed_logins:{nf}")
        e = ev[(ev.customer_id == r.customer_id) & ev.ts.between(t0, t1)]
        if (e.kind == "password_reset").any():
            score += 0.5
            why.append("password_reset")
        bens = set(e[e.kind == "beneficiary_added"].detail)
        if bens:
            score += 1.0
            why.append("beneficiary_added")
        accts = D.cust_accts.get(r.customer_id, [])
        drain = tx[tx.src_account.isin(accts) & tx.ts.between(t0, t1) & tx.dst_account.isin(bens)]
        if len(drain):
            score += 1.5
            why.append(f"transfer_to_new_beneficiary:{drain.amount.sum():,.0f}")
        if score >= 4.5:
            out.append(
                Finding(
                    "account_takeover",
                    set(accts),
                    "victim",
                    score,
                    {"signals": why, "device": r.device_id, "ip": r.ip, "at": str(t0)},
                    {r.device_id},
                )
            )
    return out


# ---------------------------------------------------------- D5 card testing
def card_testing(D: Data, window_h=12, min_cards=10, max_amt=20) -> list[Finding]:
    """One device touching many distinct cards with micro-auths."""
    cp = D.tx[D.tx.card_id.notna() & D.tx.device_id.notna()]
    micro = cp[cp.amount <= max_amt]
    out = []
    for dev, g in micro.groupby("device_id"):
        g = g.sort_values("ts")
        best = 0
        for t in g.ts:
            w = g[(g.ts >= t) & (g.ts <= t + pd.Timedelta(hours=window_h))]
            best = max(best, w.card_id.nunique())
        if best >= min_cards:
            cards = set(cp[cp.device_id == dev].card_id)
            accts = set(cp[cp.device_id == dev].src_account)
            decl = (g.status == "declined").mean()
            out.append(
                Finding(
                    "card_testing",
                    accts,
                    "victim",
                    float(best),
                    {
                        "device": dev,
                        "distinct_cards_in_window": best,
                        "decline_rate": round(decl, 2),
                        "proxy_ips": int(g.ip.nunique()),
                        "compromised_cards": len(cards),
                    },
                    {dev},
                )
            )
    return out


# ----------------------------------------------------------- D6 promo abuse
def referral_farms(D: Data, min_size=4) -> list[Finding]:
    """Referral edges where referrer and referee share a device, grown into components;
    plus the account that sweeps their bonuses."""
    dev_of = D.logins.groupby("customer_id").device_id.apply(set).to_dict()
    G = nx.Graph()
    for r in D.referrals.itertuples(index=False):
        a, b = r.referrer_customer, r.referee_customer
        shared = dev_of.get(a, set()) & dev_of.get(b, set())
        emu = any(D.emulator.get(d) for d in dev_of.get(b, set()))
        if shared or emu:
            G.add_edge(a, b)
    out = []
    tx = D.tx[D.tx.src_account.notna() & D.tx.dst_account.notna()]
    for comp in nx.connected_components(G):
        if len(comp) < min_size:
            continue
        accts = {a for c in comp for a in D.cust_accts.get(c, [])}
        sweeps = tx[tx.src_account.isin(accts)].groupby("dst_account").src_account.nunique()
        collectors = set(sweeps[sweeps >= 3].index)
        devs = set().union(*(dev_of.get(c, set()) for c in comp))
        out.append(
            Finding(
                "referral_farm",
                accts | collectors,
                "actor",
                float(len(comp)),
                {"accounts": len(comp), "collectors": sorted(collectors), "devices": len(devs)},
                devs,
            )
        )
    return out


ALL = [shared_identity_clusters, rapid_pass_through, value_cycles, account_takeover, card_testing, referral_farms]


def run_all(D: Data) -> list[Finding]:
    return [f for det in ALL for f in det(D)]
