# M1-H dashboard implementation

## Pages and visual behavior

Sentinel is a Next.js/React/Tailwind application served on port 3001. It uses a dark operations-console layout with violet workflow accents, green successful health checks, amber waiting states, red failure states, and explicit unknown states. Text and icons accompany color. Desktop and narrow layouts, keyboard focus indicators, native confirmation dialogs, a skip link, and reduced-motion support are included.

| Page | Implemented behavior |
|---|---|
| Demo Lab `/` | Clickable topology, observed traffic animation, bounded fault controls, injection markers, incident selection, six-stage recovery journey, live verification checks, chart tooltips and adjustable time windows |
| Incidents `/incidents` | Paginated list, state filter, resource, severity, retry count, timestamps |
| Incident `/incidents/{id}` | Overview, evidence, versioned plan and policy records, approval/rejection, structural dry run, executions, preliminary/final verification, paginated audit history |
| Resources `/resources` | Resource labels, immutable binding information, generation, locks and administrator emergency stop/release |
| Policies `/policies` | Read-only persisted policy definitions and reasoned administrator mode changes |
| System `/system` | Database/Redis checks, worker heartbeats, outbox/wakeup information and durable fault command history |

Presentation mode expands the current page. It is a display preference, not a simulated lifecycle or a pause in the backend.

## What the visuals mean

- Only the traffic-generator → API edge is animated from measured `/jobs` request rate. It is not per-request tracing.
- Database and queue edges represent configured topology. Database query rate and worker throughput are not inferred from API traffic.
- API fault highlighting uses observed readiness/CPU. Injection markers denote a registered injector command, not proof that the fault is still affecting service.
- Charts use fixed server-side PromQL expressions. Time windows are bounded to 2–60 minutes, sampled every 15 seconds. Empty/NaN values are gaps, not fabricated zeros. Zero error rate is inferred only alongside an observed request series.
- Verification observations are preliminary, persisted samples. The final verifier record and incident state determine resolution. No frontend timer resolves an incident.
- Retries used are displayed separately from execution attempt numbers.

## HTTP contracts

All paths below are prefixed `/api/v1/console`. They require a valid server-side session. Responses are private and non-cacheable. UUIDs are strings and dates are ISO timestamps. Operational record serialization redacts sensitive keys and credential-like string content; user/session tables are not part of the collection whitelist.

| Method and path | Contract |
|---|---|
| GET `/snapshot` | Server time, audit cursor, newest 50 incidents, state counts, up to 100 resources, automation settings and demo-control availability |
| GET `/incidents` | `state`, `offset`, `limit` (1–100); items and total |
| GET `/incidents/{id}` | Incident/resource plus newest 50 records per operational collection, up to 100 plan steps and 50 approvals/events |
| GET `/incidents/{id}/records/{collection}` | Paginated whitelisted evidence, diagnoses, plans, executions, verifications, observations, policy decisions, attention, escalations, dry runs or schedules |
| GET `/incidents/{id}/timeline` | Newest-first audit entries; `before` is an exclusive sequence cursor; bounded `limit` and `next_cursor` |
| GET `/resources` | Offset/limit resource page plus up to 100 current lock records |
| GET `/policies` | Offset-based pages of 50 policy records |
| GET `/system` | Human-session access to bounded-time system health checks |
| GET `/telemetry` | Fixed query range signals with points, latest value, availability, server time and window |
| GET `/stream` | SSE invalidation ticks with an audit cursor, or `expired` when a persisted session/account is no longer valid |
| GET `/faults` | Injector availability and active injection records |
| POST `/faults` | Operator + CSRF; scenario SCN-001/002/003, action inject/clear, UUID idempotency key; durable command result |
| GET `/commands` | Newest 50 durable fault commands |
| POST `/incidents/{id}/dry-run` | Operator + CSRF; expected incident version, plan version and content hash; persisted structural simulation |

Existing `/auth`, `/automation` and `/incidents/{id}/approval` endpoints remain authoritative for sessions, administrator governance and version-bound approvals.

## Live synchronization and safety

SSE is an invalidation feed, not a replay of every database mutation. Each tick causes a fresh authoritative snapshot, and a five-second polling fallback continues if SSE disconnects. Reconnection re-fetches snapshots rather than assuming an audit sequence gap contains all missed commits. Timeline records are deduplicated by sequence. Requests do not share a static authenticated cache.

The UI labels snapshots older than 15 seconds stale and disables mutation controls. Telemetry has a separate 30-second freshness gate. Mutations are also validated server-side; disabled buttons are not the security boundary. Expired sessions remove protected state and return to login. Approval conflicts display the server error and refresh records.

Fault injection is enabled explicitly by local Compose configuration and is rejected when `ENV=production`. Only the three M1 scenarios are accepted. The API stores intent before calling the injector and serializes command registration through a PostgreSQL advisory lock. A reused key returns the same command and cannot be rebound to another actor/action/scenario. A timeout is uncertain and is never automatically re-dispatched. An explicit clear of the same scenario reconciles uncertain records; stale pending commands have a 30-second guard.

Fault clear results follow the existing injector's semantics. For SCN-001, clearing the record does not start a stopped container. The server and documentation make this distinction explicit.

Dry runs validate and simulate the stored plan. They do not claim current policy authorization, increment retries, create execution/verification records or change incident state. Actual execution must re-check live policy and binding constraints.

## Storage and runtime additions

Migration `20260911_m1h_console` adds durable demo commands and preliminary verification observations. It installs command, dry-run and automation audit triggers and supplies restricted-runtime grants missing from later M1 tables.

Worker verification publishes each observation, independently inspects the bound Docker target, uses the configured demo API URL and real `endpoint` metric label, and checks fault status for attribution. New incident evidence refreshes Compose identity before binding a plan, allowing a rebuilt local workload to be discovered without retargeting an existing approved plan.

Deployment uses a same-origin Next.js API rewrite. The browser never receives injector or system-status secrets, and neither the dashboard nor control API receives the Docker socket. The socket belongs to the worker through the M1 Compose override.

## Deliberate limits

There is no general policy editor, arbitrary Docker command endpoint, traffic-rate slider, ML/RAG view or fabricated historical replay. Large evidence collections and audit timelines have pagination. The initial incident bundle caps steps, events and approvals; it does not claim an unlimited reconstruction. Grafana remains the full telemetry exploration tool.
