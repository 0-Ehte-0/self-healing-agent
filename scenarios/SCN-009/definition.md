# Scenario SCN-009: Elevated Error Rate

- **Target:** `demo-api` `/jobs` endpoint
- **Fault Class:** `ElevatedErrorRateFault`
- **Mechanism:** Sets `fault:error_rate` in Redis, triggering immediate HTTP 500 exceptions on business write operations.
- **Severity:** High
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Deletes `fault:error_rate` in Redis.