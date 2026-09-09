# Scenario SCN-002: CPU Saturation

- **Target:** `demo-api` (managed Compose service, container label `self-healing.managed=true`)
- **Fault Class:** `CpuStressFault`
- **Mechanism:** Injects container-internal CPU stress via authenticated endpoint `POST /_faults/cpu/inject` on `demo-api`. Spawns child worker processes directly within the `demo-api` container executing tight arithmetic loops bounded to its assigned CPU core budget (`cpu_budget_cores=1.0`).
- **Severity:** High
- **Auto-Expiry:** 600 seconds (process pools joined/terminated).
- **Reversibility:** Reversible via container restart (`restart_container` terminates all child processes) or administrative clear endpoint `POST /_faults/cpu/clear`.