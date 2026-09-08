from fault_injector.config import get_settings
from fastapi import Header, HTTPException, status


async def verify_fault_token(x_fault_token: str = Header(..., alias="X-Fault-Token")) -> str:
    settings = get_settings()
    if x_fault_token != settings.fault_injector_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication secret",
        )
    return x_fault_token
