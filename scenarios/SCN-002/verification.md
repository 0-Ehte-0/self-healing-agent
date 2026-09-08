# Verification Requirements — SCN-002

During the 90-second stabilization window:
1. Average CPU utilization drops below `75%`.
2. p95 request latency returns below SLO (< 0.200s).
3. API success rate returns to baseline band (> 98%).
4. `HighCpuSaturation` alert is inactive.