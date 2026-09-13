# M1-H validation record

## Implemented

- Interactive authenticated Next.js dashboard and local Docker deployment.
- Demo Lab topology, measured traffic animation, three bounded fault controls, chart markers, recovery journey and preliminary verification display.
- Incident list/detail, paginated evidence and audit views, plan/policy inspection, approval/rejection, structural dry run, resources, automation and system health.
- Authorized SSE invalidations with snapshot refresh and polling fallback.
- Local-only, role/CSRF-protected durable fault commands with idempotency and explicit uncertain-command reconciliation.
- Runtime integration corrections: session creation, missing database grants, demo API `psutil` dependency, fresh Compose discovery before new evidence collection, live verifier target inspection, configured readiness URL and correct metric labels.

## Completed checks

| Check | Observed result |
|---|---|
| Next.js production build | Passed locally and in the dashboard Docker image |
| Mocked Playwright operator journeys | 8 passed; desktop and narrow screenshots inspected |
| Real Compose browser smoke | Passed: login, telemetry, operational pages, system connectivity and logout |
| Console + existing lifecycle + verification regressions | 37 passed before the final expanded console tests |
| Expanded console + verification unit + evidence integration checks | 35 passed, including session revocation, SSE account revalidation and dry-run isolation |
| Docker image startup | Dashboard, control-plane, worker and demo services built and started |

The eight browser journeys cover injection confirmation and CSRF payload, viewer restrictions, versioned approval, rejection/conflict handling, reasoned automation pause, expired sessions, narrow keyboard/reduced-motion use, and missing telemetry.

The live smoke test uses the real API and HttpOnly browser session. Browser fixture data is restricted to `tests/e2e/console.spec.ts`; it is not used by the deployed dashboard.

## Live scenario acceptance

The first controlled SCN-002 run safely escalated because the existing database referenced a container ID replaced by Compose. Incident `11190228-2826-4f20-b0ed-6fc90ad6d4a7` records that escalation and its evidence. The test fault was explicitly cleared. Discovery has since been corrected to refresh the binding before collecting evidence for a new incident.

Post-fix live recovery acceptance is being checked. This document will record the actual outcome; the presence of a live test is not a claim that it passed.

## Reproduction

See [the demo guide](demo-guide.md) for startup and test commands. Use a healthy baseline and Human approval mode for the opt-in live-fault test. It performs a real controlled fault and plan approval and clears its scenario afterwards.

Existing presentation database history includes earlier development fixtures with incomplete plans. Those records have not been deleted or silently resolved. Their recovery warnings are distinct from the newly injected demo incident; follow the new incident ID.
