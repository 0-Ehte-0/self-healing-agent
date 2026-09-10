# M1 Implementation Verification — Dashboard Excluded

**Review date:** 2026-09-10  
**Reviewed commit:** `c526434`  
**Scope:** M1-A through M1-G, plus non-dashboard scenario-validation requirements associated with M1-I. M1-H is excluded.  
**Verdict:** **M1 backend completion is not verified. Substantial components exist and their current tests pass, but the production workflow has blocking integration and safety gaps.**

The M1 plan currently ends after M1-H. M1-I appears in its sequence and the overall deliverables, but has no separate detailed work-package section. This review therefore uses the plan's three-scenario completion criteria and the original v4 automated-scenario requirements to assess the remaining validation work. Missing dashboard pages or dashboard read APIs are not counted as defects here.

## Verification performed

| Check | Result | Evidence / limitation |
| :--- | :--- | :--- |
| Existing unit suite | **107 passed; 0 failed; 0 skipped** | `tests/unit`; JUnit artifact retained |
| Existing M1 integration, adapter, and security suites | **71 passed; 0 failed; 0 skipped** | `tests/integration/m1`, `tests/integration/adapters/docker`, `tests/security/m1`; 75 seconds |
| Fresh PostgreSQL migration | **Passed through M1-G** | Unique disposable review database; application database not modified |
| Repository Ruff lint | **Passed before adding review artifacts** | Original implementation checked with its configured rules |
| Repository Ruff formatting | **Passed: 291 files** | Original implementation checked before review scripts were added |
| Additional workflow/telemetry probes | **Reproduced blocking behavior** | Fake telemetry and fake execution nodes; no real container mutation |
| Additional adapter/database probes | **Reproduced DEFER acceptance and failed RUNNING recovery** | Disposable database plus the existing test suite's in-memory Docker fake |
| Running/stopped Compose services | **Incomplete M1 runtime** | `docker compose ps` and `ps -a` list 14 services; no agent-worker, demo-api, demo-worker, traffic-generator, or fault-injector container |
| Complete live three-scenario healing loop | **Not verified** | Runtime absent and the integration blockers below prevent sign-off |

The existing tests validate many useful components, but several tests manually prepare lifecycle states, persist diagnoses themselves, override graph nodes, or replace Docker with a fake. Passing those tests does not demonstrate that the default worker connects the components correctly. The fresh migrations and database tests also do not establish every live `control_app` privilege and deployment assumption.

No application implementation was changed during this review. Added files are this report and reproducible review evidence/scripts. Temporary review databases were removed. No live fault was injected and no real Docker restart was issued.

## Findings requiring correction

### 1. [P1] The default evidence-to-diagnosis path does not advance a real incident

**Affected:** M1-A, M1-B, M1-C, M1-D.

The evidence node calls the collector without the discovered container ID, binding generation, or a persistence session, then returns only generated evidence IDs. The diagnosis node constructs a mostly empty `ObservationBundle` instead of using the collector's `to_observation_bundle()` conversion or loading persisted observations. Neither node commits the required evidence/diagnosis records and `DETECTED → TRIAGED → DIAGNOSED` transitions. The default runner only wires repository-backed implementations for later nodes.

The read-only probe using the actual diagnosis-node input shape returns `INSUFFICIENT_EVIDENCE`. A default incident remains `DETECTED`; escalation from that state is illegal, and the escalation node logs persistence failure but still returns an `ESCALATED` graph status. Thus the graph can claim an outcome that the database has not recorded.

There is a related binding mismatch: discovery writes `resource.labels["docker_container_id"]`, while the planner reads `resource.labels["container_id"]` and otherwise uses `demo-api-container-id`. The worker does not initialize the missing target binding or invoke discovery. Even a manually persisted diagnosis cannot reliably become an executable plan.

**References:** [evidence node](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/evidence.py:34), [diagnosis node](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/diagnosis.py:43), [planner binding](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/planning.py:63), [escalation persistence](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/escalation.py:31).

**Required fix:** Wire authoritative resource discovery, evidence persistence, observation conversion, diagnosis persistence, and legal transitions into the default worker. Unify binding keys. Propagate failures instead of returning an unpersisted terminal status. Add a test using the real default nodes from a newly ingested incident, with only external telemetry/Docker boundaries replaced.

### 2. [P1] LangGraph discards execution and policy fields required by subsequent nodes

**Affected:** M1-B, M1-E, M1-F, M1-G.

`IncidentGraphState` lacks `execution_id`, `current_step_id`, `policy_decision`, plan hash/version, confidence, several approval flags, and retry/attribution metadata consumed elsewhere. LangGraph updates only declared state channels. The probe confirmed that an execution node returning an `execution_id` is followed by a verification node receiving `None` for that ID.

`verify_node` then falls back to the incident UUID as the execution UUID. Persisting a verification against this value cannot link it to the real execution and will violate referential integrity when no execution with that ID exists. Missing approval flags also undermine expiry and rejection resumption.

**References:** [graph state](C:/dev/self-healing-agent/packages/agent-core/agentcore/graph/state.py:6), [execution output](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/execution.py:159), [verification execution lookup](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/verification.py:48).

**Required fix:** Define the complete minimal state contract or load durable records by explicit identifiers at each node. Reject missing execution identity; never substitute an incident ID. Test state propagation through the compiled graph, not only direct node calls.

### 3. [P1] Policy evaluation is disconnected from persisted facts, and DENY/DEFER routing is incorrect

**Affected:** M1-E, M1-F.

The default policy node constructs an in-memory context using fallback confidence `0.90`, a default hash and container identity, and a default approval mode. It does not load the active database policy, resource cooldown, current automation configuration, or evidence freshness, and does not persist its evaluation decision. The Docker precheck then requires a persisted decision that this path never creates.

Separately, the node returns `status="ESCALATED"` for denial, while the graph only recognizes `status="POLICY_DENIED"`. The probe confirmed that a denied policy reaches the execution node. A `POLICY_DEFERRED` result also falls through toward execution rather than a durable wait. At the adapter boundary, only `DENY` and `REQUIRE_APPROVAL` have explicit handling; **a persisted `DEFER` decision passed precheck in the disposable-database probe**.

This does not mean the probe restarted a real container or that every denial bypasses all downstream checks. It proves inconsistent authorization outcomes and that cooldown deferral is not enforced by the final precheck.

**References:** [context construction](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/policy.py:57), [policy routing](C:/dev/self-healing-agent/packages/agent-core/agentcore/graph/builder.py:96), [adapter decision checks](C:/dev/self-healing-agent/packages/provider-adapters/provideradapters/docker/adapter.py:139).

**Required fix:** Persist evaluation from authoritative facts and route by a typed decision enum. Only an explicitly allowed decision or a valid approved plan may execute. DENY escalates; DEFER schedules a bounded wait. Recheck current policy and approval immediately before dispatch.

### 4. [P1] Verification treats unavailable required observations as healthy

**Affected:** M1-G.

Three independent probes reproduced false-positive checks:

- No live container observation, but a target ID string is present: `target_identity = PASS`.
- SCN-003 latency query unavailable: `business_latency = PASS`.
- Total traffic query succeeds but the error numerator query is unavailable: error rate becomes `0.0` and `business_traffic = PASS`.

The evaluator does not obtain live Docker identity/state itself. Required latency also lacks the same freshness handling applied to some other metrics. Consequently the claimed telemetry-only verifier can accept missing evidence, contrary to the M1 verification contract.

**References:** [target check](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/evaluator.py:109), [error numerator](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/evaluator.py:386), [latency fallback](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/evaluator.py:482).

**Required fix:** Query fresh independent container observations; treat unavailable, stale, non-finite, or insufficient required telemetry as `UNKNOWN`. Distinguish a valid empty error-series result from a failed query. Make check applicability explicit per profile. Add negative tests for partial telemetry outages, not only total outages.

### 5. [P1] Worker recovery bypasses waiting schedules and does not recover execution/verification interruptions

**Affected:** M1-B, M1-F, M1-G.

`recover_unprocessed_incidents()` says it excludes incidents with pending schedules, but its query joins only workflow leases. A `DIAGNOSED` incident deliberately waiting 600 seconds for retry can be dispatched again after it has been unchanged for ten seconds. That defeats the scheduler's cooldown wait; the policy integration gap above means another layer does not reliably restore that restriction.

The recovery states omit `EXECUTING` and `VERIFYING`, so a worker killed during these critical stages is not recovered by this sweep. Pending Redis messages are read using `>` without a pending-entry reclaim path. Additionally, when the same RUNNING execution is explicitly resumed, the adapter tries to transition it to RUNNING again rather than reconcile it. The database probe returned `ValueError: Illegal execution transition`.

**Important qualification:** The database guard prevented a second fake restart in that probe: restart count remained one. The confirmed defect is failed recovery and lack of reconciliation, not a reproduced duplicate mutation.

Lease renewal failure also only exits the renewal loop; it does not cancel the active graph, and the runner verifies ownership only before the graph invocation rather than at every durable/mutating boundary.

**References:** [recovery query](C:/dev/self-healing-agent/apps/agent-worker/agent_worker/recovery.py:112), [RUNNING handling](C:/dev/self-healing-agent/packages/provider-adapters/provideradapters/docker/adapter.py:207), [second RUNNING transition](C:/dev/self-healing-agent/packages/provider-adapters/provideradapters/docker/adapter.py:276), [lease renewal](C:/dev/self-healing-agent/packages/agent-core/agentcore/runtime/lease.py:92).

**Required fix:** Exclude future schedules, recover all unfinished lifecycle stages by durable execution identity, reclaim abandoned notifications, and reconcile uncertain intent before any resend. Stop stale workers and verify lease ownership at commit/dispatch boundaries. Add real worker-termination tests around dispatch and verification.

### 6. [P1] Approval request expiry and idempotent submission are incomplete

**Affected:** M1-E.

The approval API accepts an `idempotency_key` but never stores or uses it to replay the original result. It checks `PENDING_APPROVAL` before checking existing decisions, so retrying a successful approval gets a conflict rather than the same recorded response. `existing_approval` is queried but unused.

The API validates grant expiry downstream, but does not enforce the original 30-minute request deadline before accepting a decision. The approval wake-up only supplies `is_resumed_from_approval`; the wait node checks `approval_expired`, which is never derived from the deadline in the default path and is not declared in graph state. A timed-out request can therefore remain pending or be accepted late. The API also does not establish that the requested version is the current active plan rather than merely a version belonging to the incident.

**References:** [state/version checks](C:/dev/self-healing-agent/apps/control-api/app/api/approvals.py:90), [unused idempotency query](C:/dev/self-healing-agent/apps/control-api/app/api/approvals.py:121), [approval wait](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/approval.py:20), [approval scheduling](C:/dev/self-healing-agent/packages/agent-core/agentcore/runtime/runner.py:195).

**Required fix:** Persist request deadline and submission key, enforce current plan/version/hash, return the same response for an identical retry, and atomically reject competing decisions. Derive expiration from server time and persist the legal escalation transition. Add elapsed-deadline and duplicate-request tests through the HTTP API.

### 7. [P1] Scenario-specific verification and recovery attribution are not wired into the live path

**Affected:** M1-G.

`verify_node` passes `root_cause` as `scenario_id`, rather than loading the plan's persisted verification profile. For example, `API_UNRESPONSIVE` loads the default profile instead of SCN-003; the probe confirmed this. The default profile's general error limit is 2%, whereas SCN-003 requires less than 1%.

The production node does not pass injection time, expiry, container start time, or an external fault-status provider. Those capabilities exist on the verifier and are exercised in isolated tests, but the default worker does not connect them. Thus externally cleared or expired faults cannot reliably be distinguished from agent-caused recovery, and counter warm-up lacks the actual restart timestamp. Profile JSON files are also not copied into the worker image, making silent default fallback more likely even if the scenario ID is corrected.

**References:** [verification invocation](C:/dev/self-healing-agent/packages/agent-core/agentcore/nodes/verification.py:55), [profile fallback](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/profiles.py:57), [optional attribution inputs](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/verifier.py:46), [worker image](C:/dev/self-healing-agent/apps/agent-worker/Dockerfile:20).

**Required fix:** Load profile/version and exact execution identity from the plan/execution records; package the profiles; require explicit attribution observations for scenario evaluation. Unknown required profiles should fail closed. Verify SCN-003's stricter limit and manual-clear/expiry cases through the full graph.

### 8. [P1] The worker image and service endpoints do not support the intended Compose deployment

**Affected:** M1-A, M1-B, M1-C, M1-F, M1-G.

The agent-worker Dockerfile copies/installs shared models, diagnosis, action catalog, agent core, control API, and worker. It does not copy/install the local `telemetry-client`, `policy-engine`, or `provider-adapters` packages, although runtime imports require them. Package dependency declarations do not supply these missing local packages transitively. The local virtual environment and pytest's source-path configuration conceal this deployment gap.

Default telemetry URLs are `localhost:9090`, `localhost:3100`, and `localhost:8000`; the worker does not replace them with Compose service URLs. Inside the worker container, localhost addresses the worker, not Prometheus, Loki, or demo-api.

The current `docker compose ps -a` snapshot contains no agent-worker or demo workload containers. No clean image build/start was completed during this review, so the packaging findings are source-proven omissions rather than a captured Docker build failure.

**References:** [worker Dockerfile](C:/dev/self-healing-agent/apps/agent-worker/Dockerfile:20), [worker dependencies](C:/dev/self-healing-agent/apps/agent-worker/pyproject.toml:10), [Prometheus URL](C:/dev/self-healing-agent/packages/telemetry-client/telemetryclient/prometheus.py:46), [Loki URL](C:/dev/self-healing-agent/packages/telemetry-client/telemetryclient/loki.py:32), [readiness URL](C:/dev/self-healing-agent/packages/agent-core/agentcore/verification/evaluator.py:25).

**Required fix:** Declare and install the complete local package graph, include verification assets, inject validated service endpoints, and run a clean Compose build/start using the M1 override. Validate under the restricted runtime database role, not only the test migration owner.

### 9. [P2] Trusted target discovery is not yet fail-closed under ambiguity

**Affected:** M1-A, M1-F.

Discovery can filter by project, but `sync_resource_binding()` calls it without a project. If multiple containers have the same service label, it selects the first running container rather than treating the identity as ambiguous. The adapter permits a missing Compose project label (`if project_label and ...`) and permits a missing database Docker ID (`if db_container_id and ...`), so absence is not treated as a binding failure.

The adapter does check several important boundaries, including service labels, local environment, generation when present, and persisted decisions. Those controls do not satisfy the stricter exact-project/exact-binding requirement when required identity fields are absent.

**References:** [discovery selection](C:/dev/self-healing-agent/apps/control-api/app/services/discovery/docker.py:33), [binding sync](C:/dev/self-healing-agent/apps/control-api/app/services/discovery/docker.py:75), [adapter identity checks](C:/dev/self-healing-agent/packages/provider-adapters/provideradapters/docker/adapter.py:105).

**Required fix:** Require the configured project/environment and exactly one eligible binding. Reject missing required identity fields. Validate full immutable IDs and preserve unmanaged status for unknown/ambiguous resources. Test two Compose projects sharing a service name and missing identity labels.

### 10. [P1] No complete M1 scenario acceptance evidence exists

**Affected:** M1-I and milestone sign-off.

The inspected repository has component tests, observability scenario tooling, and earlier-phase evaluation output. It does not contain an M1 runner/report demonstrating all three faults through actual ingestion, default diagnosis/planning, policy, approval, restricted restart, verification, resolution, audit reconstruction, and reset. The fault restart-semantics tests simulate a fresh manager instead of performing a real container restart. The diagnosis tests manually insert diagnoses and advance states that the default nodes do not implement.

**References:** [fault simulation](C:/dev/self-healing-agent/tests/integration/m1/test_fault_restart_semantics.py:93), [manual diagnosis lifecycle test](C:/dev/self-healing-agent/tests/integration/m1/evidence/test_deterministic_diagnosis_e2e.py:16), [M1 outcome definition](C:/dev/self-healing-agent/autonomous_cloud_self_healing_agent_M1_plan.md:15).

**Required fix:** After the integration defects are corrected, add and run the actual three-scenario harness with timestamps, fault attribution, approval/denial paths, real verification windows, audit checks, reset, and repeated runs. Dashboard work can remain excluded; backend approval endpoints can be exercised directly by authenticated test clients.

## Work-package assessment

| Work package | Assessment excluding M1-H | Main remaining gate |
| :--- | :--- | :--- |
| M1-A: faults, targets, contracts | **Partially implemented** | Real restart-clearability proof; unambiguous discovery; deployed M1 profile |
| M1-B: durable workflow | **Partially implemented; blocked** | Correct node/state integration, cooldown-aware recovery, execution/verification resumption |
| M1-C: evidence and diagnosis | **Components implemented; live path incomplete** | Persist and connect observations, diagnoses, and authoritative transitions |
| M1-D: catalog and planning | **Components implemented; live path incomplete** | Correct Docker binding; fresh diagnosis/plan per retry; real upstream integration |
| M1-E: policy and approval | **Partially implemented; safety blockers** | Persisted authoritative policy, correct routing, request expiry/idempotency |
| M1-F: Docker execution | **Adapter implemented; completion unverified** | DEFER rejection, crash reconciliation, required target identity, real Docker validation |
| M1-G: verification/retry | **Partially implemented; safety blockers** | Fail-closed telemetry, proper execution/profile identity, attribution, durable retry integration |
| M1-H: dashboard | **Excluded** | No assessment |
| M1-I: scenario validation/sign-off | **Not demonstrated** | Three real scenario lifecycles and retained evaluation report |

## Recommended correction and verification order

1. Repair default worker composition, package installation, service URLs, discovery, graph state, and database transitions.
2. Make persisted policy authoritative; correct DENY/DEFER routing; finish approval expiry/idempotency and dispatch-time authorization.
3. Make verification fail closed, wire exact profiles and attribution, and link every result to the correct execution.
4. Repair cooldown scheduling, stale-worker handling, and recovery from interrupted execution/verification. Collect fresh evidence and create new plan versions for retries.
5. Add regression tests for the reproduced gaps while retaining the currently passing component tests.
6. Build/start the M1 Compose stack and run the three real fault scenarios with automatic and approval-required profiles, denial, failed recovery, worker termination, and audit reconstruction.

M1 backend sign-off should follow these gates, not a percentage based on files present or the current green test count. There is no need to implement the excluded dashboard before correcting and verifying this backend path.

## Retained evidence

- [Existing unit results](C:/dev/self-healing-agent/evaluation/results/m1-review-unit.xml)
- [Existing integration/security results](C:/dev/self-healing-agent/evaluation/results/m1-review-integration.xml)
- [Integration test output](C:/dev/self-healing-agent/evaluation/results/m1-review-integration.txt)
- [Fresh migration output](C:/dev/self-healing-agent/evaluation/results/m1-review-migration.txt)
- [Workflow and telemetry probe output](C:/dev/self-healing-agent/evaluation/results/m1-review-probes.json)
- [Adapter/database probe output](C:/dev/self-healing-agent/evaluation/results/m1-review-database-probes.json)
- [Isolated test runner](C:/dev/self-healing-agent/evaluation/results/m1_review_run.py)
- [Read-only workflow probes](C:/dev/self-healing-agent/evaluation/results/m1_review_probes.py)
- [Database probes using fake Docker](C:/dev/self-healing-agent/evaluation/results/m1_review_database_probes.py)

The isolated runner uses the local development PostgreSQL defaults already present in repository tests, creates a database with a generated `m1_review_` prefix, applies migrations, and removes that generated database in `finally`. It never points test mutations at `control_plane`.
