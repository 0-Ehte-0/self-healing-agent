# Scenario SCN-002: CPU Saturation

- **Target:** System host / container CPU budget
- **Fault Class:** `CpuStressFault`
- **Mechanism:** Spawns `multiprocessing.Process` workers executing tight arithmetic loops across all logical CPU cores.
- **Severity:** High
- **Auto-Expiry:** 600 seconds (process pools joined/terminated).
- **Reversibility:** Clean termination of multiprocessing pool via multiprocessing `Event`.