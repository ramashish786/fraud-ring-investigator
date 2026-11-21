"""Batched, idempotent bulk loader: CSVs -> Neo4j.

    python -m simulation.neo4j_legacy.load_neo4j --data data --uri bolt://localhost:7687 --user neo4j --password fraudlab123

Design notes
* UNWIND batches (default 5k rows) inside managed write transactions -> automatic retry on transient errors.
* MERGE on constrained keys -> re-running the load is safe (idempotent), which the streaming path relies on too.
* Logins are pre-aggregated to Customer-Device / Customer-IP edges (≈190k events -> ≈15k edges).
* Ground-truth labels are NOT loaded by default; the detector must not see them. Use --with-labels for demos.
"""
from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase


def rows(df: pd.DataFrame) -> list[dict]:
    out = df.astype(object).where(pd.notna(df), None).to_dict("records")
    for r in out:  # neo4j driver rejects NaN floats
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
    return out


def batched(session, query: str, data: list[dict], size: int, label: str):
    t = time.time()
    for i in range(0, len(data), size):
        session.execute_write(lambda tx, b=data[i:i + size]: tx.run(query, rows=b).consume())
    print(f"  {label:<28} {len(data):>8,} rows  {time.time() - t:6.1f}s")


Q = {
    "customers": """
UNWIND $rows AS r
MERGE (c:Customer {customer_id: r.customer_id})
SET c.kind = r.kind, c.name = r.name, c.city = r.city, c.dob = r.dob, c.created_at = datetime(r.created_at)
MERGE (p:Phone {number: r.phone})     MERGE (c)-[:HAS_PHONE]->(p)
MERGE (e:Email {address: r.email})    MERGE (c)-[:HAS_EMAIL]->(e)
MERGE (a:Address {text: r.address})   MERGE (c)-[:LIVES_AT]->(a)""",
    "accounts": """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id})
MERGE (a:Account {account_id: r.account_id})
SET a.type = r.type, a.opened_at = datetime(r.opened_at)
MERGE (c)-[:OWNS]->(a)""",
    "cards": """
UNWIND $rows AS r
MATCH (a:Account {account_id: r.account_id})
MERGE (k:Card {card_id: r.card_id}) SET k.network = r.network
MERGE (a)-[:HAS_CARD]->(k)""",
    "devices": "UNWIND $rows AS r MERGE (d:Device {device_id: r.device_id}) SET d.os = r.os, d.emulator = r.emulator",
    "ips": "UNWIND $rows AS r MERGE (i:IP {ip: r.ip}) SET i.kind = r.kind, i.city = r.city, i.country = r.country",
    "merchants": "UNWIND $rows AS r MERGE (m:Merchant {merchant_id: r.merchant_id}) SET m.category = r.category, m.risk = r.risk, m.city = r.city",
    "used_device": """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id}), (d:Device {device_id: r.device_id})
MERGE (c)-[u:USED_DEVICE]->(d)
SET u.first_seen = datetime(r.first_seen), u.last_seen = datetime(r.last_seen), u.logins = r.logins, u.failed = r.failed""",
    "logged_in_from": """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id}), (i:IP {ip: r.ip})
MERGE (c)-[u:LOGGED_IN_FROM]->(i)
SET u.first_seen = datetime(r.first_seen), u.last_seen = datetime(r.last_seen), u.logins = r.logins, u.failed = r.failed""",
    "transactions": """
UNWIND $rows AS r
MERGE (t:Transaction {txn_id: r.txn_id})
SET t.ts = datetime(r.ts), t.amount = r.amount, t.currency = r.currency, t.channel = r.channel, t.status = r.status
WITH t, r
OPTIONAL MATCH (s:Account {account_id: r.src_account})
FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | MERGE (s)-[:SENT]->(t))
WITH t, r
OPTIONAL MATCH (d:Account {account_id: r.dst_account})
FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (t)-[:TO]->(d))
WITH t, r
OPTIONAL MATCH (m:Merchant {merchant_id: r.merchant_id})
FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (t)-[:AT]->(m))
WITH t, r
OPTIONAL MATCH (dv:Device {device_id: r.device_id})
FOREACH (_ IN CASE WHEN dv IS NULL THEN [] ELSE [1] END | MERGE (t)-[:VIA_DEVICE]->(dv))
WITH t, r
OPTIONAL MATCH (i:IP {ip: r.ip})
FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | MERGE (t)-[:FROM_IP]->(i))
WITH t, r
OPTIONAL MATCH (k:Card {card_id: r.card_id})
FOREACH (_ IN CASE WHEN k IS NULL THEN [] ELSE [1] END | MERGE (t)-[:USING_CARD]->(k))""",
    "paid": """
UNWIND $rows AS r
MATCH (s:Account {account_id: r.src_account}), (d:Account {account_id: r.dst_account})
MERGE (s)-[p:PAID]->(d)
SET p.count = r.count, p.total = r.total, p.first_ts = datetime(r.first_ts), p.last_ts = datetime(r.last_ts)""",
    "referrals": """
UNWIND $rows AS r
MATCH (a:Customer {customer_id: r.referrer_customer}), (b:Customer {customer_id: r.referee_customer})
MERGE (a)-[x:REFERRED]->(b) SET x.ts = datetime(r.ts)""",
    "beneficiaries": """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id}), (a:Account {account_id: r.detail})
MERGE (c)-[x:ADDED_BENEFICIARY]->(a) SET x.ts = datetime(r.ts)""",
    "events": """
UNWIND $rows AS r
MATCH (c:Customer {customer_id: r.customer_id})
CREATE (c)-[:HAD_EVENT]->(:AccountEvent {event_id: r.event_id, kind: r.kind, ts: datetime(r.ts), device_id: r.device_id, ip: r.ip})""",
    "labels": """
UNWIND $rows AS r
MATCH (a:Account {account_id: r.account_id})
SET a.gt_label = r.label, a.gt_ring = r.ring_id, a.gt_role = r.role""",
}


def iso(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="ISO8601").dt.strftime("%Y-%m-%dT%H:%M:%S")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--uri", default="bolt://localhost:7687")
    p.add_argument("--user", default="neo4j")
    p.add_argument("--password", default="fraudlab123")
    p.add_argument("--batch", type=int, default=5000)
    p.add_argument("--with-labels", action="store_true")
    a = p.parse_args()
    d = Path(a.data)

    ent = {n: pd.read_csv(d / "entities" / f"{n}.csv") for n in ("customers", "accounts", "cards", "devices", "ips", "merchants")}
    ent["customers"]["created_at"] = iso(ent["customers"]["created_at"])
    ent["accounts"]["opened_at"] = iso(ent["accounts"]["opened_at"])
    logins = pd.read_csv(d / "events" / "logins.csv")
    tx = pd.read_csv(d / "events" / "transactions.csv")
    ev = pd.read_csv(d / "events" / "account_events.csv")
    refs = pd.read_csv(d / "events" / "referrals.csv")
    for df in (logins, tx, ev, refs):
        df["ts"] = iso(df["ts"])

    def agg_logins(key):
        g = logins.groupby(["customer_id", key])
        return g.agg(first_seen=("ts", "min"), last_seen=("ts", "max"), logins=("ts", "size"),
                     failed=("success", lambda s: int((~s.astype(bool)).sum()))).reset_index()

    p2p = tx[tx.src_account.notna() & tx.dst_account.notna() & (tx.status == "approved")]
    paid = p2p.groupby(["src_account", "dst_account"]).agg(
        count=("amount", "size"), total=("amount", "sum"), first_ts=("ts", "min"), last_ts=("ts", "max")).reset_index()
    tx = tx.drop(columns=["is_fraud", "ring_id", "pattern"])  # never leak labels into the graph

    drv = GraphDatabase.driver(a.uri, auth=(a.user, a.password))
    drv.verify_connectivity()
    with drv.session() as s:
        for stmt in (Path(__file__).parent / "schema.cypher").read_text().split(";"):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("//")).strip()
            if body:
                s.run(body).consume()
        print("Loading…")
        for n in ("customers", "accounts", "cards", "devices", "ips", "merchants"):
            batched(s, Q[n], rows(ent[n]), a.batch, n)
        batched(s, Q["used_device"], rows(agg_logins("device_id")), a.batch, "used_device (agg)")
        batched(s, Q["logged_in_from"], rows(agg_logins("ip")), a.batch, "logged_in_from (agg)")
        batched(s, Q["transactions"], rows(tx), a.batch, "transactions")
        batched(s, Q["paid"], rows(paid), a.batch, "paid (derived)")
        batched(s, Q["referrals"], rows(refs[["referrer_customer", "referee_customer", "ts"]]), a.batch, "referrals")
        batched(s, Q["beneficiaries"], rows(ev[ev.kind == "beneficiary_added"]), a.batch, "beneficiaries")
        batched(s, Q["events"], rows(ev.drop(columns=["is_fraud", "ring_id", "detail"])), a.batch, "account events")
        if a.with_labels:
            lab = pd.read_csv(d / "labels" / "account_labels.csv")
            batched(s, Q["labels"], rows(lab), a.batch, "ground-truth labels")
    drv.close()
    print("Done.")


if __name__ == "__main__":
    main()
