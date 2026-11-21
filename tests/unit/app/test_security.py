import time
import uuid

import jwt
import pytest

from app.core.enums import Classification, Role
from app.core.errors import AuthenticationError
from app.domains.identity.actor import Actor
from app.domains.identity.security import create_access_token, decode_access_token, hash_password, verify_password

SECRET = "s" * 40


def test_password_hashing() -> None:
    h = hash_password("correct horse")
    assert h.startswith("$argon2id$") and verify_password(h, "correct horse") and not verify_password(h, "wrong")
    assert not verify_password("not-a-hash", "x")


def test_token_round_trip_and_tampering() -> None:
    uid = uuid.uuid4()
    tok = create_access_token(uid, [Role.ANALYST], Classification.CONFIDENTIAL, SECRET, 5)
    claims = decode_access_token(tok, SECRET)
    assert claims["sub"] == str(uid) and claims["roles"] == ["analyst"]
    with pytest.raises(AuthenticationError):
        decode_access_token(tok, "x" * 40)  # wrong key
    forged = jwt.encode({**claims, "roles": ["admin"]}, "attacker-key-000000000000000000000", algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(forged, SECRET)
    none_alg = jwt.encode({**claims}, key="", algorithm="none") if hasattr(jwt, "encode") else ""
    with pytest.raises(AuthenticationError):
        decode_access_token(none_alg, SECRET)  # alg=none rejected


def test_expired_token() -> None:
    now = int(time.time())
    tok = jwt.encode(
        {"sub": "x", "iat": now - 100, "exp": now - 10, "iss": "fraud-ring-investigator"}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(tok, SECRET)


def test_admin_has_every_role_and_clearance_order() -> None:
    assert Actor(user_id=None, roles=frozenset({Role.ADMIN})).has_any(Role.REVIEWER)
    assert not Actor(user_id=None, roles=frozenset({Role.AUDITOR})).has_any(Role.ANALYST)
    assert Classification.RESTRICTED.rank > Classification.CONFIDENTIAL.rank > Classification.PUBLIC.rank
