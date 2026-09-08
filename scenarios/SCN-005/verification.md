# Verification Requirements — SCN-005

During the 90-second stabilization window:
1. Memory usage returns to baseline (< 120MB RSS for injector).
2. No OOM events recorded in container runtime.
3. `HighMemoryUsage` alert is inactive.