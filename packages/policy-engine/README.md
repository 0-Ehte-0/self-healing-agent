# Policy Engine

Server-side policy evaluation engine, ordered gate pipeline, and approval lifecycle management for autonomous cloud infrastructure self-healing.

## Ordered Gate Sequence

1. Global Kill Switch / Automation Paused
2. Resource Emergency Stop
3. Prohibited Action & Risk Level
4. Target Allowlist
5. Target Environment
6. Evidence Validity & Diagnosis Confidence
7. Remediation Attempt Budget
8. Per-Resource Cooldown Window
9. Resource Lock Eligibility
10. Approval Requirement & Grant Validation
