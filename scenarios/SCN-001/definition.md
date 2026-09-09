# Scenario SCN-001: Container Crash

- **Target:** `demo-api` (managed Compose service, container label `self-healing.managed=true`)
- **Fault Class:** `ProcessCrashFault`
- **Mechanism:** Injects an unhandled termination signal (`SIGTERM`) via `demo-api` endpoint `POST /_faults/crash` authenticated with `X-Fault-Token`.
- **Severity:** Critical
- **Container Restart Policy:** Explicitly configured as `restart: "no"` in `compose.m1.yml` to prevent Docker from automatically masking the fault before the agent intervenes.
- **Auto-Expiry:** Process termination is immediate; container remains stopped until restarted.
- **Reversibility:** Clean container restart (`restart_container` action).