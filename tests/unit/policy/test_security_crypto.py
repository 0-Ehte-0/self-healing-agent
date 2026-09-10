import time

import pytest
from app.core.security import (
    InMemoryRateLimiter,
    constant_time_compare,
    generate_csrf_token,
    generate_session_token,
    hash_password,
    verify_password,
)


def test_password_hashing_and_verification():
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)
    assert hashed.startswith("scrypt$16384$8$1$")

    # Positive verification
    assert verify_password(password, hashed) is True

    # Negative verification (wrong password)
    assert verify_password("WrongPassword123!", hashed) is False

    # Corrupted / invalid hash formats handled safely
    assert verify_password(password, "invalid_hash") is False
    assert verify_password(password, "scrypt$invalid$format") is False
    assert verify_password(password, "") is False


def test_token_generation_and_comparison():
    tok1 = generate_session_token()
    tok2 = generate_csrf_token()
    assert len(tok1) == 64
    assert len(tok2) == 64
    assert tok1 != tok2

    assert constant_time_compare(tok1, tok1) is True
    assert constant_time_compare(tok1, tok2) is False


def test_in_memory_rate_limiter():
    limiter = InMemoryRateLimiter()
    key = "test-user-ip"

    # Allow up to 3 requests in a 1-second window
    assert limiter.is_allowed(key, max_requests=3, window_seconds=1.0) is True
    assert limiter.is_allowed(key, max_requests=3, window_seconds=1.0) is True
    assert limiter.is_allowed(key, max_requests=3, window_seconds=1.0) is True

    # 4th request within window must be rejected
    assert limiter.is_allowed(key, max_requests=3, window_seconds=1.0) is False

    # Another key is independent
    assert limiter.is_allowed("other-key", max_requests=3, window_seconds=1.0) is True

    # Reset
    limiter.reset(key)
    assert limiter.is_allowed(key, max_requests=3, window_seconds=1.0) is True
