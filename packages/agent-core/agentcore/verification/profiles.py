import json
import logging
from pathlib import Path

from sharedmodels.verification import VerificationProfile

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).resolve().parents[4] / "scenarios"

DEFAULT_PROFILE = VerificationProfile(
    profile_id="m1_default_restart_profile",
    version="1.0",
    description="Default M1 Restart Verification Profile",
    evaluation_interval_seconds=15,
    stabilization_window_seconds=90,
    min_consecutive_passing_samples=7,
    max_verification_duration_seconds=300,
    sample_freshness_limit_seconds=30,
    readiness={
        "required_consecutive_successes": 3,
        "max_latency_seconds": 0.200,
        "path": "/health/ready",
        "expected_status": 200,
    },
    traffic={
        "min_requests_per_window": 20,
        "window_seconds": 60,
        "max_error_rate": 0.02,
        "max_p95_latency_seconds": 0.200,
    },
    cpu={
        "max_saturation": 0.75,
        "window_seconds": 30,
        "consecutive_windows": 2,
        "cpu_budget_cores": 1.0,
    },
    alerts={
        "prohibited_active_alerts": [
            "ContainerDown",
            "HighCpuSaturation",
            "ApiUnresponsive",
        ]
    },
    weights={
        "alerts": 0.25,
        "error_rate": 0.25,
        "latency": 0.25,
        "readiness": 0.25,
    },
)


def load_verification_profile(
    profile_name: str | None = None,
    scenarios_dir: Path | None = None,
) -> VerificationProfile:
    """Loads a scenario verification profile from JSON or returns the default profile."""
    if not profile_name or profile_name == "m1_default_restart_profile":
        return DEFAULT_PROFILE

    base_dir = scenarios_dir or SCENARIOS_DIR
    profile_path = base_dir / profile_name / "verification-profile.json"

    if profile_path.is_file():
        try:
            with profile_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return VerificationProfile.model_validate(data)
        except Exception as exc:
            logger.warning(
                f"Failed to load verification profile from {profile_path}: {exc}. Using default."
            )

    # Check if profile_name matches a scenario ID without directory
    if profile_name.upper() in {"SCN-001", "SCN-002", "SCN-003"}:
        alt_path = base_dir / profile_name.upper() / "verification-profile.json"
        if alt_path.is_file():
            try:
                with alt_path.open(encoding="utf-8") as f:
                    data = json.load(f)
                return VerificationProfile.model_validate(data)
            except Exception as exc:
                logger.warning(
                    f"Failed to load verification profile from {alt_path}: {exc}. Using default."
                )

    return DEFAULT_PROFILE
