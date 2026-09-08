# Scenario SCN-001: Container Crash

- **Target:** `demo-api`
- **Fault Class:** `ProcessCrashFault`
- **Mechanism:** Injects an unhandled termination signal (SIGTERM) via `demo-api` endpoint `/_faults/crash`.
- **Severity:** Critical
- **Auto-Expiry:** Process termination is immediate; container restarts or remains down depending on orchestrator policy.
- **Reversibility:** Reversible via container restart (`restart_container`).