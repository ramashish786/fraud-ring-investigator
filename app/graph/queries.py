"""The query catalog (D-008): every Cypher statement the application runs.

Rules:
* Parameters only, never string interpolation of input.
* Variable-length bounds can't be parameters in Cypher, so each allowed depth is a
  separate pre-built statement selected from a whitelist (`money_trail_query`).
* Limits are clamped by the repository before the call (app/graph/models.py).
* All application reads go through Neo4jClient.read (read transaction + timeout).
"""

from __future__ import annotations

from typing import Literal, LiteralString

from .models import MAX_DEPTH, clamp_depth

ACCOUNT_SUMMARIES: LiteralString = """
MATCH (c:Customer)-[:OWNS]->(a:Account) WHERE a.account_id IN $account_ids
RETURN a.account_id AS account_id, c.customer_id AS customer_id, c.kind AS customer_kind, a.type AS account_type,
       toString(a.opened_at) AS opened_at, toString(c.created_at) AS customer_created_at,
       c.address_key AS address_key, c.city AS city
"""

TRANSACTIONS: LiteralString = """
MATCH (a:Account {account_id: $account_id})
CALL (a) {
  MATCH (a)-[:SENT]->(t:Transaction) RETURN t
  UNION
  MATCH (t:Transaction)-[:RECEIVED_BY]->(a) RETURN t
}
WITH t WHERE ($since IS NULL OR t.ts >= datetime($since)) AND ($until IS NULL OR t.ts <= datetime($until))
WITH t ORDER BY t.ts LIMIT $limit
OPTIONAL MATCH (s:Account)-[:SENT]->(t)
OPTIONAL MATCH (t)-[:RECEIVED_BY]->(d:Account)
OPTIONAL MATCH (t)-[:AT]->(m:Merchant)
OPTIONAL MATCH (t)-[:VIA_DEVICE]->(dv:Device)
OPTIONAL MATCH (t)-[:FROM_IP]->(ip:IPAddress)
RETURN t.txn_id AS txn_id, toString(t.ts) AS ts, t.amount AS amount, t.channel AS channel, t.status AS status,
       s.account_id AS src_account, d.account_id AS dst_account, m.merchant_id AS merchant_id,
       dv.device_id AS device_id, ip.ip AS ip
ORDER BY t.ts
"""

_TRAIL_TEMPLATE = """
MATCH p = (a:Account {{account_id: $account_id}}){arrow}(b:Account)
WHERE all(x IN relationships(p) WHERE x.total >= $min_amount) AND b <> a
WITH p LIMIT $path_limit
UNWIND range(0, length(p) - 1) AS i
WITH relationships(p)[i] AS r, i
WITH r, min(i) + 1 AS hop
RETURN startNode(r).account_id AS src, endNode(r).account_id AS dst, r.count AS count, r.total AS total,
       toString(r.first_ts) AS first_ts, toString(r.last_ts) AS last_ts, hop
ORDER BY hop, total DESC LIMIT $limit
"""

# Built once from constants at import time; the dict is the whitelist.
_MONEY_TRAIL: dict[tuple[str, int], LiteralString] = {
    (direction, depth): _TRAIL_TEMPLATE.format(  # type: ignore[misc,unused-ignore]
        arrow=f"-[:PAID*1..{depth}]->" if direction == "out" else f"<-[:PAID*1..{depth}]-"
    )
    for direction in ("out", "in")
    for depth in range(1, MAX_DEPTH + 1)
}


def money_trail_query(direction: Literal["out", "in"], depth: int) -> LiteralString:
    return _MONEY_TRAIL[(direction, clamp_depth(depth))]


SHARED_DEVICES: LiteralString = """
MATCH (a:Account)<-[:OWNS]-(:Customer)-[:USES]->(d:Device)
WHERE a.account_id IN $account_ids
WITH DISTINCT d
MATCH (other:Customer)-[u:USES]->(d)
WITH d, count(other) AS user_count,
     collect({customer_id: other.customer_id, first_seen: toString(u.first_seen), last_seen: toString(u.last_seen),
              observations: u.observations, address_key: other.address_key, source_ref: u.source_ref,
              account_ids: [(other)-[:OWNS]->(oa:Account) | oa.account_id]}) AS all_users
WHERE user_count >= 2
OPTIONAL MATCH (d)-[:ACCESSED_FROM]->(ip:IPAddress)
RETURN d.device_id AS device_id, coalesce(d.emulator, false) AS emulator, d.os AS os, user_count,
       all_users[..$max_users] AS users, collect(DISTINCT ip.kind) AS ip_kinds
ORDER BY user_count DESC, device_id LIMIT $limit
"""

SHARED_IPS: LiteralString = """
MATCH (a:Account)<-[:OWNS]-(:Customer)-[:LOGGED_IN_FROM]->(ip:IPAddress)
WHERE a.account_id IN $account_ids
WITH DISTINCT ip
MATCH (other:Customer)-[u:LOGGED_IN_FROM]->(ip)
WITH ip, count(other) AS user_count,
     collect({customer_id: other.customer_id, first_seen: toString(u.first_seen), last_seen: toString(u.last_seen),
              observations: u.observations, address_key: other.address_key, source_ref: u.source_ref,
              account_ids: [(other)-[:OWNS]->(oa:Account) | oa.account_id]}) AS all_users
WHERE user_count >= 2
RETURN ip.ip AS ip, ip.kind AS kind, ip.country AS country, user_count, all_users[..$max_users] AS users
ORDER BY user_count DESC, ip LIMIT $limit
"""

BENEFICIARIES: LiteralString = """
MATCH (a:Account)-[r:ADDED]->(b:Beneficiary)-[:LINKED_TO]->(t:Account)
WHERE a.account_id IN $account_ids
OPTIONAL MATCH (t)<-[:OWNS]-(tc:Customer)
RETURN b.beneficiary_id AS beneficiary_id, a.account_id AS owner_account, t.account_id AS target_account,
       tc.customer_id AS target_customer_id, toString(r.ts) AS added_at, r.device_id AS device_id, r.ip AS ip,
       r.source AS source
ORDER BY r.ts DESC, beneficiary_id LIMIT $limit
"""

BENEFICIARY_FAN_IN: LiteralString = """
UNWIND $targets AS tid
OPTIONAL MATCH (o:Account)-[:ADDED]->(:Beneficiary)-[:LINKED_TO]->(:Account {account_id: tid})
RETURN tid AS target_account, count(DISTINCT o) AS owners
"""

PRIOR_CASES: LiteralString = """
MATCH (a:Account)-[r:INVOLVED_IN]->(f:FraudCase) WHERE a.account_id IN $account_ids
RETURN f.case_id AS case_id, f.typology AS typology, f.status AS status, r.role AS role, a.account_id AS account_id,
       toString(f.closed_at) AS closed_at, f.summary AS summary
ORDER BY f.closed_at DESC
"""

UPSERT_INVESTIGATION: LiteralString = """
MERGE (i:Investigation {investigation_id: $investigation_id})
SET i.status = $status, i.updated_at = datetime()
WITH i
UNWIND $account_ids AS aid
MATCH (a:Account {account_id: aid})
MERGE (i)-[:INVESTIGATES]->(a)
"""
