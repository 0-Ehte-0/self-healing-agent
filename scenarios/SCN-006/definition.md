# Scenario SCN-006: DB Connection Leak

- **Target:** PostgreSQL connection pool
- **Fault Class:** `DbConnectionLeakFault`
- **Mechanism:** Acquires and holds 15 active connections to PostgreSQL without releasing them to the pool.
- **Severity:** High
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Explicitly closes all retained connection instances and disposes of the engine.