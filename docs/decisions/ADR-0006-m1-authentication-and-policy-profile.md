# ADR-0006: M1 Authentication, Authorization, and Policy Profile

## Status
Accepted

## Context
Exposing container mutation endpoints without strong authentication, role enforcement, and auditable policy evaluation presents severe security risks. Even in a local development environment, all mutation decisions and operator actions must be attributed to authenticated identities and governed by server-side policy.

## Decision
1. **Authentication:**
   - Server-validated sessions carried in HttpOnly cookies.
   - Passwords hashed with scrypt using constant-time verification.
   - Webhook authentication for external systems (e.g. Alertmanager) uses shared secret bearer tokens, isolated from human session auth.
2. **Role Hierarchy:**
   - `viewer`: Read-only access to incidents, resources, policies, and audit logs.
   - `operator`: Can trigger dry-runs and clear known active demo faults.
   - `approver`: Can approve or reject pending remediation plans; request safe escalation.
   - `admin`: All approver capabilities, plus automation pause/resume, resource emergency stop, and system configuration.
   - Client-supplied identity headers are strictly ignored; actor identity is derived solely from the validated session.
3. **Policy Confidence Gates:**
   - Confidence >= 0.85: Eligible for automated execution (if automation mode allows).
   - Confidence 0.60 – 0.84: Requires human approval from an `approver` or `admin`.
   - Confidence < 0.60: Ineligible for remediation; transitions to `ESCALATED`.
   - Missing or ambiguous evidence immediately forces `ESCALATED`.
4. **Initial System Startup Mode:**
   - The system starts in **approval required** mode. Automated execution must be explicitly enabled by an authenticated `admin` after environment checks pass.
5. **Approval Expiry:**
   - Pending approval requests expire after 30 minutes. Granted approvals are valid for at most 30 minutes and bind strictly to the exact plan version/content hash.

## Consequences
- **Positive:** Robust defense-in-depth preventing unauthorized container mutations.
- **Positive:** Full audit attribution for every automated or manual decision.
- **Negative:** Requires user login and session cookie management in operator interfaces.
