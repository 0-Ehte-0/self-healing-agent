# Scenario SCN-010: Bad Deployment

- **Target:** `demo-api` startup and probe validation
- **Fault Class:** `BadDeploymentFault`
- **Mechanism:** Sets `fault:bad_deployment` in Redis, causing `/health/live` and `/health/ready` to fail with HTTP 500.
- **Severity:** Critical
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Clears `fault:bad_deployment` in Redis.