from agentcore.verification.profiles import DEFAULT_PROFILE, load_verification_profile


def test_load_default_profile():
    profile = load_verification_profile(None)
    assert profile.profile_id == "m1_default_restart_profile"
    assert profile.stabilization_window_seconds == 90
    assert profile.min_consecutive_passing_samples == 7
    assert profile.evaluation_interval_seconds == 15
    assert profile.readiness["required_consecutive_successes"] == 3


def test_load_scenario_profiles():
    # SCN-001
    p1 = load_verification_profile("SCN-001")
    assert p1.profile_id == "SCN-001"
    assert p1.stabilization_window_seconds == 90
    assert "ContainerDown" in p1.alerts["prohibited_active_alerts"]
    assert p1.traffic["max_error_rate"] == 0.02

    # SCN-002
    p2 = load_verification_profile("SCN-002")
    assert p2.profile_id == "SCN-002"
    assert p2.traffic["baseline_success_rate_min"] == 0.98
    assert p2.traffic["baseline_requests_per_sec_min"] == 5.0
    assert p2.cpu["max_saturation"] == 0.75
    assert "HighCpuSaturation" in p2.alerts["prohibited_active_alerts"]

    # SCN-003
    p3 = load_verification_profile("SCN-003")
    assert p3.profile_id == "SCN-003"
    assert p3.traffic["max_5xx_error_rate"] == 0.01
    assert "ApiUnresponsive" in p3.alerts["prohibited_active_alerts"]
