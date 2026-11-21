// Graph model (see docs/adr/0002-graph-model.md)
//
// (:Customer)-[:OWNS]->(:Account)-[:HAS_CARD]->(:Card)
// (:Customer)-[:HAS_PHONE]->(:Phone)   (:Customer)-[:HAS_EMAIL]->(:Email)   (:Customer)-[:LIVES_AT]->(:Address)
// (:Customer)-[:USED_DEVICE {first_seen,last_seen,logins,failed}]->(:Device)
// (:Customer)-[:LOGGED_IN_FROM {first_seen,last_seen,logins,failed}]->(:IP)
// (:Account)-[:SENT]->(:Transaction)-[:TO]->(:Account)          raw ledger (audit / evidence)
// (:Transaction)-[:AT]->(:Merchant) / [:VIA_DEVICE]->(:Device) / [:FROM_IP]->(:IP) / [:USING_CARD]->(:Card)
// (:Account)-[:PAID {count,total,first_ts,last_ts}]->(:Account) derived, for fast multi-hop traversal
// (:Customer)-[:REFERRED {ts}]->(:Customer)
// (:Customer)-[:ADDED_BENEFICIARY {ts}]->(:Account)

CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (n:Customer)    REQUIRE n.customer_id IS UNIQUE;
CREATE CONSTRAINT account_id  IF NOT EXISTS FOR (n:Account)     REQUIRE n.account_id IS UNIQUE;
CREATE CONSTRAINT card_id     IF NOT EXISTS FOR (n:Card)        REQUIRE n.card_id IS UNIQUE;
CREATE CONSTRAINT device_id   IF NOT EXISTS FOR (n:Device)      REQUIRE n.device_id IS UNIQUE;
CREATE CONSTRAINT ip_addr     IF NOT EXISTS FOR (n:IP)          REQUIRE n.ip IS UNIQUE;
CREATE CONSTRAINT merchant_id IF NOT EXISTS FOR (n:Merchant)    REQUIRE n.merchant_id IS UNIQUE;
CREATE CONSTRAINT txn_id      IF NOT EXISTS FOR (n:Transaction) REQUIRE n.txn_id IS UNIQUE;
CREATE CONSTRAINT phone       IF NOT EXISTS FOR (n:Phone)       REQUIRE n.number IS UNIQUE;
CREATE CONSTRAINT email       IF NOT EXISTS FOR (n:Email)       REQUIRE n.address IS UNIQUE;
CREATE CONSTRAINT address     IF NOT EXISTS FOR (n:Address)     REQUIRE n.text IS UNIQUE;

CREATE INDEX txn_ts       IF NOT EXISTS FOR (t:Transaction) ON (t.ts);
CREATE INDEX txn_channel  IF NOT EXISTS FOR (t:Transaction) ON (t.channel);
CREATE INDEX ip_kind      IF NOT EXISTS FOR (i:IP)          ON (i.kind);
CREATE INDEX cust_created IF NOT EXISTS FOR (c:Customer)    ON (c.created_at);
