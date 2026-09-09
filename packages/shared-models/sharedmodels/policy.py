from pydantic import BaseModel, ConfigDict, Field

from sharedmodels.enums import RiskLevel


class PolicyContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str
    action: str
    risk: RiskLevel
    approval_required: bool
    enabled: bool = True
    retry_limit: int = Field(default=2, ge=0)
    cooldown_seconds: int = Field(default=600, ge=0)
    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
