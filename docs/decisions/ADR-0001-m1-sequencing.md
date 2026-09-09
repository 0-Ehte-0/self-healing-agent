# ADR-0001: M1-First Sequencing and Milestone Scope

## Status
Accepted

## Context
The original comprehensive project specification (`autonomous_cloud_self_healing_agent_plan_v4.md`) defined a 20-phase sequential execution path. In that sequence, Phase 6 (Drain3/Isolation Forest anomaly detection), Phase 7 (LogHub benchmarks), Phase 8 (LangGraph workflow engine), and Phase 10 (LLM providers) preceded Phase 13 (remediation planning) and Phase 14 (remediation actions). Authentication and operator governance were deferred until Phase 19, and scenario automation was placed in Phase 20.

Attempting to execute in this order creates fundamental circular dependencies and security risks:
1. Mutating container operations would be introduced before server-side authentication, authorization, and approval governance exist.
2. Machine-learning and LLM components would be introduced before verifying that deterministic diagnosis, typed action execution, and independent telemetry verification work end-to-end.
3. Remediation actions cannot be safely automated without an authoritative state machine, durable checkpointing, and execution reconciliation.

## Decision
We adopt an **M1-First Execution Sequence** as defined in `autonomous_cloud_self_healing_agent_M1_plan.md`:
1. **Pull Forward Deterministic Core:** Deliver Phase 9 (restricted Docker execution) and the deterministic slices of Phases 8 (resumable workflow runtime), 13 (typed planning), 14–15 (policy and safety gates), and 17 (telemetry verification) before Phase 6.
2. **Pull Forward Security and Governance:** Deliver server-side authentication, role-based authorization (`viewer`, `operator`, `approver`, `admin`), HttpOnly sessions, and approval APIs from Phase 19 immediately to secure all mutation boundaries.
3. **Pull Forward Scenario Validation:** Introduce a repeatable three-scenario test harness (SCN-001, SCN-002, SCN-003) from Phase 20 to continuously validate recovery semantics.
4. **Defer Probabilistic and Cloud Components:** Drain3, Isolation Forest scoring, benchmark pipelines (M2 / Phases 6–7), LLM providers, ChromaDB RAG, prompt pipelines (M3 / Phases 10–12), Kubernetes (M4), and Cloud remediation (M5) remain deferred until M1 is proven.

## Consequences
- **Positive:** Guarantees an auditable, secure, deterministic vertical slice demonstrating full end-to-end self-healing before introducing ML/LLM non-determinism.
- **Positive:** Server-side approval and policy checks protect the system prior to any Docker mutations.
- **Negative:** Phase status reporting in tracking documents deviates from strict numerical phase order; `project.md` and related tracking must document M1 as the active milestone.
