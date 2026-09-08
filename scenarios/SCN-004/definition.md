# Scenario SCN-004: Latency Injection

- **Target:** `demo-api` HTTP middleware
- **Fault Class:** `LatencyFault`
- **Mechanism:** Sets `fault:latency` in Redis, inserting an artificial 2.0-second async sleep in all HTTP route handling.
- **Severity:** Medium
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Removes `fault:latency` from Redis.