import re
from typing import Any

# Regex patterns for sensitive tokens and connection URIs
SENSITIVE_KEY_PATTERN = re.compile(
    r"^(password|passwd|secret|token|authorization|auth|api_key|apikey|access_key|private_key|credential|jwt)$",
    re.IGNORECASE,
)

AUTH_HEADER_PATTERN = re.compile(
    r"(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*",
    re.IGNORECASE,
)

BASIC_AUTH_PATTERN = re.compile(
    r"(Basic\s+)[A-Za-z0-9\+\/]+=*",
    re.IGNORECASE,
)

CONNECTION_STRING_PATTERN = re.compile(
    r"([a-zA-Z0-9\+\.\-]+://)([^:]*):([^@]+)@",
)

QUERY_PARAM_PATTERN = re.compile(
    r"((?:token|key|secret|password|api_key)=)[^&]+",
    re.IGNORECASE,
)


def redact_string(value: str) -> str:
    """Redacts sensitive credentials, tokens, and connection strings in a string."""
    if not value:
        return value

    # Redact connection strings: postgresql://user:pass@host -> postgresql://user:[REDACTED]@host
    res = CONNECTION_STRING_PATTERN.sub(r"\1\2:[REDACTED]@", value)

    # Redact authorization headers
    res = AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", res)
    res = BASIC_AUTH_PATTERN.sub(r"\1[REDACTED]", res)

    # Redact sensitive query parameters
    res = QUERY_PARAM_PATTERN.sub(r"\1[REDACTED]", res)

    return res


def redact_payload(obj: Any) -> Any:
    """Recursively redacts sensitive keys and string patterns across nested dicts, lists, and primitives."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and SENSITIVE_KEY_PATTERN.search(k):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_payload(v)
        return cleaned
    elif isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(redact_payload(item) for item in obj)
    elif isinstance(obj, set):
        return {redact_payload(item) for item in obj}
    elif isinstance(obj, str):
        return redact_string(obj)
    else:
        return obj
