# Action Catalog and Deterministic Planning

This package implements milestone M1-D of the Autonomous Cloud Self-Healing Agent:
- Strongly typed action schemas and registry.
- 5 registered actions: `inspect_container`, `restart_container`, `wait_for_stabilization`, `notify_operator`, `open_incident_ticket`.
- Security-hardened plan validator rejecting commands, shell injections, unknown parameters, and mismatched container targets.
- Deterministic mapper transforming supported root causes (`CONTAINER_STOPPED`, `CPU_SATURATION`, `API_UNRESPONSIVE`) into bounded restart plans.
- Safe escalation for unsupported / ambiguous diagnoses.
- Dry-run simulation semantics with metric non-inflation guarantees.
