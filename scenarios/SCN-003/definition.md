# Scenario SCN-003: Unresponsive API

- **Target:** `demo-api` `/health/ready`
- **Fault Class:** `HealthHangFault`
- **Mechanism:** Sets `fault:health_hang` flag in Redis, inducing a 60-second blocking sleep on readiness probes.
- **Severity:** High
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Deletes Redis key `fault:health_hang`.