import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hashes a password using scrypt."""
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=16384,
        r=8,
        p=1,
    ).hex()
    return f"scrypt$16384$8$1${salt}${digest}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against an scrypt hash using constant-time comparison."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        _, n_str, r_str, p_str, salt_hex, expected_digest = parts
        n, r, p = int(n_str), int(r_str), int(p_str)
        computed_digest = hashlib.scrypt(
            plain_password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=n,
            r=r,
            p=p,
        ).hex()
        return hmac.compare_digest(expected_digest, computed_digest)
    except Exception as e:
        logger.warning(f"Password verification error: {e}")
        return False


def generate_session_token() -> str:
    return secrets.token_hex(32)


def generate_csrf_token() -> str:
    return secrets.token_hex(32)


def constant_time_compare(val1: str, val2: str) -> bool:
    return hmac.compare_digest(val1, val2)


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter."""

    def __init__(self):
        self._lock = Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._hits[key]
            # Prune expired entries
            valid = [ts for ts in timestamps if ts > cutoff]
            if len(valid) >= max_requests:
                self._hits[key] = valid
                return False
            valid.append(now)
            self._hits[key] = valid
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key:
                self._hits.pop(key, None)
            else:
                self._hits.clear()


# Global rate limiter instance
rate_limiter = InMemoryRateLimiter()
