# ADR-0002: Restart-Clearable M1 Faults and Process Ownership

## Status
Accepted

## Context
In M1, the sole authorized corrective action is `restart_container` targeted exclusively at `demo-api`. For self-healing demonstrations to be honest and verifiable:
1. SCN-001 (Process Crash): Terminating the `demo-api` main process must leave the container stopped unless restarted by the agent. A Docker restart must bring `demo-api` back to verified health.
2. SCN-002 (CPU Saturation): Previously, `fault_injector/faults/cpustress.py` spawned CPU worker processes inside the `fault-injector` container. Restarting `demo-api` had zero effect on the fault, making container restart ineffective as a corrective action.
3. SCN-003 (Unresponsive API): Previously, `fault_injector/faults/healthhang.py` set a persistent key in Redis (`fault:health_hang`). When `demo-api` was restarted, it re-read the Redis key and continued hanging, failing remediation.

## Decision
We realign fault ownership and runtime state for M1 scenarios:
1. **Target Boundary:** `demo-api` is the single managed M1 restart target. `demo-worker`, control-plane, data stores, and monitoring services are strictly unmanaged.
2. **Container-Internal CPU Stress (SCN-002):** CPU stress is initiated inside the `demo-api` container via an authenticated internal endpoint (`POST /_faults/cpu/inject`). Worker processes are children of `demo-api` and consume CPU within its assigned budget. When `demo-api` restarts, all child processes are killed by the Docker daemon.
3. **Process-Local Hang (SCN-003):** Hang state is stored in `demo-api` process memory (`ProcessHangManager`). When `demo-api` restarts, process memory is discarded, automatically clearing the hang.
4. **Health Probe Separation (SCN-003):** During SCN-003, `/health/live` remains responsive (HTTP 200) and administrative fault endpoints (`/_faults/*`) remain operational. Only `/health/ready` and application endpoints block or return 503. This prevents Docker or orchestrators from misidentifying an unresponsive API as a dead container loop (SCN-001).
5. **Idempotent Clear and Expiry:** Administrative clear endpoints (`POST /_faults/*/clear`) cancel active tasks/processes immediately. Auto-expiry and manual clear operations are strictly idempotent.

## Consequences
- **Positive:** Container restart physically and honestly remediates all three M1 faults without backchannel state manipulation.
- **Positive:** Clear separation between crash loops (liveness failure) and unresponsive API (readiness failure).
- **Negative:** `demo-api` must host authenticated fault endpoints (`/_faults/*`), protected by a shared secret (`X-Fault-Token`).
