import pytest
from diagnosis.evidence.redactor import redact_payload, redact_string


def test_redact_bearer_token():
    raw = "Request failed with Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    redacted = redact_string(raw)
    assert "Bearer [REDACTED]" in redacted
    assert "eyJhbGci" not in redacted


def test_redact_basic_auth():
    raw = "Connecting with Basic dXNlcjpwYXNz"
    redacted = redact_string(raw)
    assert "Basic [REDACTED]" in redacted
    assert "dXNlcjpwYXNz" not in redacted


def test_redact_connection_strings():
    db_uri = "postgresql://myuser:super_secret_pw@db.internal:5432/control_plane"
    redacted = redact_string(db_uri)
    assert "myuser:[REDACTED]@db.internal:5432/control_plane" in redacted
    assert "super_secret_pw" not in redacted

    redis_uri = "redis://:secretpass@cache.internal:6379/0"
    redacted_redis = redact_string(redis_uri)
    assert ":[REDACTED]@cache.internal" in redacted_redis
    assert "secretpass" not in redacted_redis


def test_redact_query_params():
    url = "https://internal.service/webhook?token=my_secret_token_123&env=prod"
    redacted = redact_string(url)
    assert "token=[REDACTED]" in redacted
    assert "my_secret_token_123" not in redacted
    assert "env=prod" in redacted


def test_redact_nested_payload():
    payload = {
        "service": "demo-api",
        "config": {
            "password": "plain_password_here",
            "api_key": "abc123xyz",
            "host": "localhost",
            "port": 8000,
        },
        "connection_urls": [
            "postgresql://postgres:secret123@localhost:5432/db",
            "https://api.example.com?secret=topsecret",
        ],
        "metrics": {
            "rate": 0.85,
            "healthy": True,
            "null_field": None,
        },
    }

    cleaned = redact_payload(payload)

    # Sensitive keys redacted
    assert cleaned["config"]["password"] == "[REDACTED]"
    assert cleaned["config"]["api_key"] == "[REDACTED]"
    assert cleaned["config"]["host"] == "localhost"
    assert cleaned["config"]["port"] == 8000

    # Strings in lists redacted
    assert "secret123" not in cleaned["connection_urls"][0]
    assert ":[REDACTED]@" in cleaned["connection_urls"][0]
    assert "topsecret" not in cleaned["connection_urls"][1]
    assert "secret=[REDACTED]" in cleaned["connection_urls"][1]

    # Non-sensitive primitives preserved
    assert cleaned["metrics"]["rate"] == 0.85
    assert cleaned["metrics"]["healthy"] is True
    assert cleaned["metrics"]["null_field"] is None
