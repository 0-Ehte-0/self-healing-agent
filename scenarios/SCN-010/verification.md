# Verification Requirements — SCN-010

During the 90-second stabilization window:
1. `/health/live` returns HTTP 200.
2. `/health/ready` returns HTTP 200 for three consecutive intervals.
3. No configuration or liveness alerts active.