# Verification Requirements — SCN-006

During the 90-second stabilization window:
1. Database readiness check in `/health/ready` returns HTTP 200.
2. Active connection count returns to baseline idle level (< 5).
3. `demo-api` successfully creates jobs on `/jobs`.