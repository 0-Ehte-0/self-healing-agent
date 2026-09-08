# Verification Requirements — SCN-007

During the 90-second stabilization window:
1. Redis `PING` succeeds with `PONG` in `< 5ms`.
2. `/health/ready` returns `{"ready": true, "redis": "ok"}`.
3. `demo_worker_heartbeat_seconds` advances continuously.