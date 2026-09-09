# Scenario SCN-003: Unresponsive API

- **Target:** `demo-api` (managed Compose service, container label `self-healing.managed=true`)
- **Fault Class:** `HealthHangFault`
- **Mechanism:** Injects an in-memory process-local hang via authenticated endpoint `POST /_faults/hang/inject` on `demo-api`. Induces a blocking delay (60s sleep) on `/health/ready` and application endpoints.
- **Probe Separation:** `/health/live` remains responsive (HTTP 200) and administrative fault endpoints (`/_faults/*`) remain operational so that orchestrators do not mistake an unresponsive API for a crashed process (SCN-001).
- **Severity:** High
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Clean container restart (`restart_container` resets process memory and clears the hang) or administrative clear endpoint `POST /_faults/hang/clear` (which cancels sleeping tasks immediately).