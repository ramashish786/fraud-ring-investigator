// Cypher versions of detection/detectors.py — what the investigation API runs in production.
// Parameters are passed by the API layer; never string-format user input into Cypher.

// ---------------------------------------------------------------- D1 shared identity clusters
// GDS: project customers linked through shared devices / phones / addresses / non-shared IPs,
// pruning known shared infrastructure (carrier NAT, public wifi, corporate egress).
MATCH (c1:Customer)-[:USED_DEVICE|HAS_PHONE|LIVES_AT|LOGGED_IN_FROM]->(x)<-[:USED_DEVICE|HAS_PHONE|LIVES_AT|LOGGED_IN_FROM]-(c2:Customer)
WHERE elementId(c1) < elementId(c2) AND NOT (x:IP AND x.kind IN ['mobile_cgnat','public_wifi','corporate'])
WITH gds.graph.project('identity', c1, c2, {}, {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.graphName, g.nodeCount, g.relationshipCount;

CALL gds.wcc.write('identity', {writeProperty: 'identity_cluster'});

// score clusters
MATCH (c:Customer) WHERE c.identity_cluster IS NOT NULL
WITH c.identity_cluster AS cluster, collect(c) AS members WHERE size(members) >= $min_size
UNWIND members AS m
OPTIONAL MATCH (m)-[:USED_DEVICE]->(d:Device)
OPTIONAL MATCH (m)-[:LOGGED_IN_FROM]->(ip:IP)
WITH cluster, members,
     avg(CASE WHEN m.created_at >= $sim_start - duration('P30D') THEN 1.0 ELSE 0.0 END) AS young_share,
     avg(CASE WHEN ip.kind IN ['datacenter','vpn'] THEN 1.0 ELSE 0.0 END) AS risky_ip_share,
     count(DISTINCT CASE WHEN d.emulator THEN d END) AS emulators
RETURN cluster, size(members) AS customers, young_share, risky_ip_share, emulators,
       2*young_share + 2*risky_ip_share + CASE WHEN emulators > 2 THEN 2 ELSE emulators END AS score
ORDER BY score DESC;

// ---------------------------------------------------------------- D2 rapid pass-through (mules)
MATCH (tin:Transaction {status:'approved'})-[:TO]->(a:Account)   // includes cash deposits (no sender)
WHERE tin.amount >= 5000
MATCH (a)-[:SENT]->(tout:Transaction {status:'approved'})
WHERE tout.ts > tin.ts AND tout.ts <= tin.ts + duration('PT24H') AND NOT (tout)-[:AT]->(:Merchant)
WITH a, tin, sum(tout.amount) AS forwarded
WHERE forwarded >= 0.8 * tin.amount
WITH a, count(tin) AS pass_through_events, sum(forwarded) AS amount_passed_through
WHERE pass_through_events >= 2
OPTIONAL MATCH (dep:Transaction {channel:'cash_deposit'})-[:TO]->(a)
WHERE dep.amount >= 42500 AND dep.amount < 50000          // just under the reporting threshold
RETURN a.account_id, pass_through_events, amount_passed_through, count(dep) AS structured_deposits
ORDER BY pass_through_events DESC;

// ---------------------------------------------------------------- D3 value cycles (round-tripping)
MATCH path = (a:Account)-[:PAID*3..6]->(a)
WHERE all(r IN relationships(path) WHERE r.total / r.count >= 100000)
WITH path, [r IN relationships(path) | r.total / r.count] AS means
WITH path, reduce(mn = means[0], x IN means | CASE WHEN x < mn THEN x ELSE mn END) AS lo,
           reduce(mx = means[0], x IN means | CASE WHEN x > mx THEN x ELSE mx END) AS hi
WHERE 1 - lo / hi <= 0.05
RETURN [n IN nodes(path) | n.account_id] AS cycle, 1 - lo / hi AS value_decay
LIMIT 100;

// ---------------------------------------------------------------- D4 account takeover
// new device + beneficiary added + transfer to that beneficiary within 2h
MATCH (c:Customer)-[u:USED_DEVICE]->(d:Device)
WHERE u.first_seen > c.created_at + duration('P1D')
MATCH (c)-[b:ADDED_BENEFICIARY]->(mule:Account)
WHERE b.ts >= u.first_seen AND b.ts <= u.first_seen + duration('PT2H')
MATCH (c)-[:OWNS]->(acct:Account)-[:SENT]->(t:Transaction)-[:TO]->(mule)
WHERE t.ts >= b.ts AND t.ts <= u.first_seen + duration('PT2H')
OPTIONAL MATCH (t)-[:FROM_IP]->(ip:IP)
RETURN c.customer_id AS victim, acct.account_id, d.device_id, ip.kind AS ip_kind, mule.account_id AS beneficiary,
       sum(t.amount) AS drained
ORDER BY drained DESC;

// ---------------------------------------------------------------- D5 card testing
MATCH (t:Transaction)-[:VIA_DEVICE]->(d:Device), (t)-[:USING_CARD]->(k:Card)
WHERE t.amount <= 20
WITH d, count(DISTINCT k) AS cards, avg(CASE WHEN t.status = 'declined' THEN 1.0 ELSE 0.0 END) AS decline_rate,
     min(t.ts) AS first, max(t.ts) AS last
WHERE cards >= 10
RETURN d.device_id, cards, decline_rate, duration.between(first, last) AS span
ORDER BY cards DESC;

// ---------------------------------------------------------------- D6 referral farms
MATCH (a:Customer)-[:REFERRED]->(b:Customer)
WHERE EXISTS { (a)-[:USED_DEVICE]->(:Device)<-[:USED_DEVICE]-(b) }
   OR EXISTS { (b)-[:USED_DEVICE]->(:Device {emulator: true}) }
WITH collect(DISTINCT a) + collect(DISTINCT b) AS farm
UNWIND farm AS m
MATCH (m)-[:OWNS]->(acc:Account)-[:PAID]->(collector:Account)
WITH collector, count(DISTINCT acc) AS feeders WHERE feeders >= 3
RETURN collector.account_id, feeders ORDER BY feeders DESC;

// ---------------------------------------------------------------- Investigation: explain one account
// "Why is account $account_id risky?" — evidence paths up to 3 hops for the agent to cite.
MATCH (a:Account {account_id: $account_id})<-[:OWNS]-(c:Customer)
OPTIONAL MATCH p1 = (c)-[:USED_DEVICE]->(:Device)<-[:USED_DEVICE]-(other:Customer)
OPTIONAL MATCH p2 = (a)-[:PAID*1..3]->(:Account)
RETURN c, collect(DISTINCT p1)[..25] AS shared_device_paths, collect(DISTINCT p2)[..25] AS money_paths;
