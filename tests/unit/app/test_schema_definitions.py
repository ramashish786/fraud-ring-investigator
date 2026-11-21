"""Static checks on the ORM metadata and Neo4j bootstrap (no database needed)."""

from sqlalchemy import CheckConstraint

from app.graph.schema.bootstrap import NODE_KEYS, schema_statements
from app.infrastructure.database.registry import Base

EXPECTED_TABLES = {
    "investigation_events",
    "risk_assessments",
    "roles",
    "users",
    "user_roles",
    "alerts",
    "investigations",
    "investigation_status_history",
    "jobs",
    "evidence",
    "findings",
    "finding_evidence",
    "reviews",
    "reports",
    "audit_logs",
    "documents",
    "llm_calls",
}


def test_all_tables_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_enums_are_not_native_postgres_enums() -> None:  # D-022
    for table in Base.metadata.tables.values():
        for col in table.columns:
            assert not getattr(col.type, "native_enum", False), f"{table.name}.{col.name}"


def test_evidence_enforces_llm_only_hypothesis() -> None:  # D-006
    checks = {c.name for c in Base.metadata.tables["evidence"].constraints if isinstance(c, CheckConstraint)}
    assert "ck_evidence_llm_only_hypothesis" in checks


def test_neo4j_schema_is_idempotent_and_covers_model() -> None:  # D-018
    stmts = schema_statements()
    assert all("IF NOT EXISTS" in s for s in stmts)
    labels = {label for label, _ in NODE_KEYS}
    assert {
        "Customer",
        "Account",
        "Transaction",
        "Device",
        "IPAddress",
        "Beneficiary",
        "FraudCase",
        "Investigation",
        "Document",
    } <= labels
