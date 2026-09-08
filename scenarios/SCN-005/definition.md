# Scenario SCN-005: Memory Pressure

- **Target:** Container resident memory
- **Fault Class:** `MemoryPressureFault`
- **Mechanism:** Allocates contiguous 256MB bytearrays in resident process memory.
- **Severity:** High
- **Auto-Expiry:** 600 seconds.
- **Reversibility:** Clears buffer references and runs explicit `gc.collect()`.