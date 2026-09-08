# Scenario SCN-007: Redis Network Disconnect

- **Target:** Redis command processing
- **Fault Class:** `RedisDisconnectFault`
- **Mechanism:** Executes `CLIENT PAUSE 600000 ALL` against Redis, blocking command execution.
- **Severity:** Critical
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Issues `CLIENT UNPAUSE` to restore command pipeline.