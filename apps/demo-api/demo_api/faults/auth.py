import secrets

from demo_api.config import get_settings
from fastapi import Header, HTTPException, status


def verify_fault_token(x_fault_token: str | None = Header(None)) -> None:
    """Verifies the shared secret token for administrative fault injection endpoints."""
    settings = get_settings()
    expected = settings.fault_injector_secret
    if not x_fault_token or not secrets.compare_digest(x_fault_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Fault-Token header",
        )
