# Verification Requirements — SCN-009

During the 90-second stabilization window:
1. HTTP 500 error rate drops to `< 1%`.
2. `/jobs` endpoint successfully writes records to PostgreSQL and Redis.
3. `HighHttpErrorRate` alert is cleared.