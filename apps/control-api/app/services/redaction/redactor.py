import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEY_NAMES = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "session_token",
    "bearer",
    "api_key",
    "apikey",
    "private_key",
    "privkey",
    "credential",
    "credentials",
    "authorization",
    "conn_str",
    "connection_string",
}

# Regex patterns for values inside strings
CONNECTION_STRING_PATTERN = re.compile(r"(?i)\b([a-zA-Z0-9+.-]+)://([^:\s]*):([^@\s]+)@")
BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9\-\._~\+\/]+=*)")
BASIC_AUTH_PATTERN = re.compile(r"(?i)\bBasic\s+([A-Za-z0-9+/=]+)")
INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"]?([^\s'\",;&]+)['\"]?"
)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def _is_sensitive_key(key: str) -> bool:
    clean = key.strip().lower().replace("-", "_")
    if clean in SENSITIVE_KEY_NAMES:
        return True
    return any(
        term in clean
        for term in (
            "password",
            "passwd",
            "secret",
            "private_key",
            "api_key",
            "apikey",
            "bearer_token",
            "auth_token",
            "connection_string",
        )
    )


def redact_string(value: str) -> str:
    """Redacts sensitive substrings (tokens, credentials, connection strings) within a string."""
    # 1. Connection strings: postgresql://user:pass@host -> postgresql://user:[REDACTED]@host
    res = CONNECTION_STRING_PATTERN.sub(r"\1://\2:[REDACTED]@", value)
    # 2. Bearer tokens: Bearer abc123xyz -> Bearer [REDACTED]
    res = BEARER_TOKEN_PATTERN.sub("Bearer [REDACTED]", res)
    # 3. Basic auth: Basic dXNlcjpwYXNz -> Basic [REDACTED]
    res = BASIC_AUTH_PATTERN.sub("Basic [REDACTED]", res)
    # 4. Inline secrets: password=secret123 -> password=[REDACTED]
    res = INLINE_SECRET_PATTERN.sub(r"\1=[REDACTED]", res)
    # 5. JWT tokens
    res = JWT_PATTERN.sub("[REDACTED_JWT]", res)
    # 6. AWS keys
    res = AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", res)
    return res


def redact_sensitive_data(data: Any) -> Any:
    """Recursively redacts sensitive values from dictionaries, lists, strings, and other structures.
    Does not modify inputs in-place.
    """
    if isinstance(data, str):
        return redact_string(data)
    elif isinstance(data, Mapping):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            key_str = str(k)
            if _is_sensitive_key(key_str):
                sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = redact_sensitive_data(v)
        return sanitized
    elif isinstance(data, Sequence) and not isinstance(data, (bytes, bytearray)):
        return [redact_sensitive_data(item) for item in data]
    elif isinstance(data, (set, frozenset)):
        return {redact_sensitive_data(item) for item in data}
    return data
