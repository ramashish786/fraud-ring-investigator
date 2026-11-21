from app.infrastructure.observability.logging import redact_sensitive


def test_redacts_sensitive_keys_recursively() -> None:
    event = {
        "event": "x",
        "password": "p",
        "user": "u",
        "headers": {"Authorization": "Bearer t", "accept": "json"},
        "items": [{"api_key": "k", "n": 1}],
    }
    out = redact_sensitive(None, "info", event)
    assert out["password"] == "***"
    assert out["user"] == "u"
    assert out["headers"] == {"Authorization": "***", "accept": "json"}
    assert out["items"] == [{"api_key": "***", "n": 1}]
    assert out["event"] == "x"
