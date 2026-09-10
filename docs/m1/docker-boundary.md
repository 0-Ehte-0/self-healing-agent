# M1 Docker Adapter Security and Execution Boundary

**Version:** 1.0  
**Milestone:** M1 — Deterministic Docker Vertical Slice  
**Specification:** `autonomous_cloud_self_healing_agent_M1_plan.md` (Section 10, Step 14)

---

## 1. Architectural Overview & Component Isolation

In M1, physical mutations to workload containers are strictly isolated to the background worker (`agent-worker`) via the `provideradapters` package. No mutation capabilities or Docker socket mounts exist on the public-facing or operator-facing components.

```text
Operator Browser (Next.js Dashboard)
       │ HTTP / SSE
       ▼
Control API (FastAPI)  ──────────  PostgreSQL
       │                            (Incidents, Plans, Policies,
       │ Redis Streams               Executions, Resource Locks)
       ▼
Agent Worker (LangGraph)
       │
       ▼
Restricted Docker Adapter
  • Validate & Precheck
  • Resource Lease
  • Idempotency Check
  • Exact Hex ID Restart
       │
       ▼ /var/run/docker.sock
Docker Host Daemon
       │
       ▼
Managed Target: [demo-api]
(Labels: self-healing.managed=true, com.docker.compose.service=demo-api)
```

| Component | Network Access | Database Access | Docker Socket Mount | Capability |
| :--- | :--- | :--- | :--- | :--- |
| **Control Plane API** | Public / Internal | Read / Write | **NONE** | Ingestion, Auth, Read Models, Policies, Approvals |
| **Operator Dashboard** | Browser-facing | Via API only | **NONE** | Observation, Human Approvals, System Status |
| **Agent Worker** | Internal network | Read / Write | **`/var/run/docker.sock`** | Workflow runner, Evidence collection, Docker mutation |
| **Demo Workloads** | Internal network | Demo DB / Redis | **NONE** | Synthetic business traffic and faults |

---

## 2. Application-Level Defense in Depth

While the raw Docker socket allows container lifecycle mutations on the host, the `DockerExecutionAdapter` imposes multiple defense-in-depth safety gates before any dispatch:

### 2.1 Identity & Target Binding
1. **Exact Container Hex ID:** Mutations target an exact 12-to-64 hex character container ID (`[a-fA-F0-9]{12,64}`). Container names, prefixes, or arbitrary wildcard patterns are strictly rejected.
2. **Binding Generation Matching:** The adapter checks that the target container's `binding_generation` in the database matches the plan. If Compose recreated the container, the old binding is immediately invalidated (`TargetRecreatedError`), requiring fresh evidence, diagnosis, planning, and policy evaluation.

### 2.2 Strict Allowlist & Label Verification
Immediately before dispatch, the adapter inspects the live Docker container to verify:
- `labels["self-healing.managed"] == "true"`
- `labels["com.docker.compose.service"] == "demo-api"`
- `labels["com.docker.compose.project"] == "self-healing-agent"`
- `resource.environment == "local"`

Any missing, forged, or mismatched label halts execution at the precheck phase without mutating physical infrastructure.

### 2.3 Parameter Tampering & Shell Injection Prevention
All catalog actions utilize strict Pydantic schemas with `extra="forbid"` and regex validation forbidding shell metacharacters (`;`, `&`, `|`, `` ` ``, `$`, `>`, `<`, `\n`, `\r`, `sudo`, `rm`, `exec`, `sh`, `bash`). The Docker adapter calls the typed Docker Python SDK `container.restart(timeout=...)` method; no shell strings or CLI commands are ever executed.

### 2.4 Precheck State Machine Protection (ADR-0003)
Prechecks execute while the incident remains in `APPROVED` or `PLANNED`. If prechecks fail (e.g., container was replaced, policy was denied, or an emergency stop was triggered), the incident transitions directly `APPROVED -> ESCALATED` or `PLANNED -> ESCALATED`. The system never pretends execution started when prechecks fail.

### 2.5 Concurrency Serialization & Distributed Resource Leases
1. **PostgreSQL Resource Leases:** An exclusive `ResourceLock` must be acquired before dispatch. A competing worker attempting an overlapping mutation receives a `ResourceLockedError`.
2. **Lock Ownership Verification:** Lock release explicitly checks token and owner identity. An expired worker cannot release a new worker's lock.
3. **In-Process Mutex:** A per-container `asyncio.Lock` serializes concurrent dispatches inside the worker process to prevent local race conditions.

---

## 3. Honest Assessment of Raw Docker Socket Privileges

> [!WARNING]
> Mounting `/var/run/docker.sock` into a container is **not** a least-privilege security boundary. Access to the Docker socket effectively provides host root equivalency:
> - A compromised worker container could manipulate other containers or the host filesystem.
> - The application allowlist enforces business logic and operational safety within our stack, but does not provide Linux kernel-level containment.

For M1 (local Docker Compose environment), this architecture is a deliberate, pragmatic trade-off to enable real, honest container restarts without stubbing or simulation.

---

## 4. Evolution Path to Least Privilege

The architecture is explicitly designed so the adapter interface remains stable while the underlying provider evolves to true least privilege in subsequent milestones:

1. **M1 (Current):** Restricted Docker socket adapter running in a trusted worker service with strict application allowlists.
2. **M4 (Kubernetes Vertical Slice):**
   - Eliminate Docker socket mounts entirely.
   - Run the agent worker with a Kubernetes `ServiceAccount` bounded by a restrictive `Role` / `RoleBinding`.
   - RBAC rules grant permission only to `delete` (restart) pods with specific labels in the designated workload namespace (e.g., `apiGroups: [""]`, `resources: ["pods"]`, `verbs: ["delete"]`).
3. **M5 (Cloud / AWS Remediation):**
   - Use IAM Roles for Service Accounts (IRSA) / OIDC federation.
   - Restrict remediation permissions via IAM policies to specific ECS task definitions, Auto Scaling groups, or Lambda functions with condition keys.
