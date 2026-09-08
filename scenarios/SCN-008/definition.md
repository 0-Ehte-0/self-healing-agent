# Scenario SCN-008: Worker Pause

- **Target:** `demo-worker` stream consumption loop
- **Fault Class:** `WorkerPauseFault`
- **Mechanism:** Sets `fault:worker_pause` in Redis, causing the worker to bypass message consumption and sleep.
- **Severity:** Medium
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Deletes `fault:worker_pause` from Redis.