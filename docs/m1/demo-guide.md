# Run the app and present the M1 demo

This guide runs the complete application locally with Docker Desktop and uses the Sentinel dashboard to demonstrate fault injection, diagnosis, approval, execution, and recovery verification.

## 1. What you will show

Your project is an **autonomous cloud self-healing control plane**, demonstrated against a deliberately failure-prone local Docker application. The current milestone uses deterministic diagnosis and bounded restart actions. Describe it as an agent workflow; do not claim that M1 uses an LLM or machine-learning diagnosis.

The demonstration tells one story:

> “Normal traffic is flowing. I introduce a controlled fault. The system detects it, gathers evidence, diagnoses the cause, proposes a specific action, checks policy, requests approval when required, restarts the bound container, and independently checks that service has recovered.”

The distinction to emphasize is **an action finishing does not prove recovery**. `SUCCEEDED` belongs to an execution; `RESOLVED` belongs to an incident after independent verification.

## 2. Prerequisites

- Windows with Docker Desktop installed, running, and using Linux containers.
- This repository at `C:\dev\self-healing-agent`.
- PowerShell.
- Internet for the first image build and dependency downloads. Build everything before presentation day.
- A browser; Edge works with the included browser tests.
- Enough resources for the monitoring stack. A practical starting allocation is 4–6 CPUs and 8 GB RAM for Docker; reduce other running applications if your laptop is constrained. This is a starting point, not a measured minimum.

The Compose workflow builds Python and Node dependencies inside containers. You do not need to activate the Python virtual environment or run `npm install` to use the Docker deployment.

## 3. First startup

Start Docker Desktop and wait until the engine is running. Then open PowerShell:

```powershell
Set-Location C:\dev\self-healing-agent
docker info
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d --build
```

Always include `infrastructure/compose/compose.m1.yml`. It supplies the worker's Docker socket, the managed-target labels, the demo API's CPU budget, and the restart policy required by M1. Keep the Compose project name `self-healing-agent`; the execution adapter validates this project boundary.

The first build can take several minutes. The control-plane container applies migrations and seeds local users, policies and logical resources. Named volumes preserve data between ordinary shutdowns.

Check startup:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml ps -a
Invoke-RestMethod http://localhost:8088/health/ready
Invoke-RestMethod http://localhost:8001/health/ready
```

Look for a running dashboard, control-plane, agent-worker, demo-api, demo-worker, traffic-generator and fault-injector. PostgreSQL and Redis should be healthy. Wait approximately 1–2 minutes after the demo API starts for scrapes and one-minute rate windows to populate.

If the demo API exited during startup, inspect its logs and start it again after its database is ready:

```powershell
docker compose logs --tail 60 demo-api
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d demo-api demo-worker traffic-generator
```

Do not make `restart: always` the fix for the demo API: Docker would then heal the crash scenario before your agent could demonstrate its action.

## 4. Open the applications

| Application | Address | Purpose |
|---|---|---|
| Sentinel dashboard | http://localhost:3001 | Main presentation and operator UI |
| Control API docs | http://localhost:8088/docs | API contracts; useful during development |
| Demo API readiness | http://localhost:8001/health/ready | Independent readiness probe |
| Grafana | http://localhost:3000 | Detailed monitoring dashboards |
| Prometheus | http://localhost:9090 | Query metrics and inspect alert state |
| Alertmanager | http://localhost:9093 | Alert routing and grouping |
| Jaeger | http://localhost:16686 | Trace exploration |

The dashboard uses port **3001** because Grafana already uses **3000**. Keep the same hostname throughout the demo; `localhost` and `127.0.0.1` have separate browser cookies.

## 5. Sign in

For a database seeded with the repository's default local accounts:

| Username | Default local password | Capability |
|---|---|---|
| `admin` | `local-admin-change-me` | All demo controls, approvals, automation mode and resource emergency stops |
| `approver` | `local-approver-change-me` | Observe, inject/clear, simulate, approve and reject |
| `operator` | `local-operator-change-me` | Observe, inject/clear, and simulate plans |
| `viewer` | `local-viewer-change-me` | Observe only |

These are development seed defaults. If accounts already existed or their passwords were changed, use those existing credentials. Startup does not reset an existing user's password. `SEED_*_PASSWORD` values apply when a user is first created; pass them into the control-plane container if customizing provisioning.

For a single-presenter college demo, use `admin`. To demonstrate separation of duties, open a second browser profile or private window and sign in as `viewer` or `approver`.

## 6. Pre-demo checklist — do this before the audience arrives

1. Start the stack at least 10 minutes early. Finish builds while internet is available.
2. Open the dashboard and sign in.
3. In **System health**, confirm database and Redis connectivity and a recent worker heartbeat. A recorded worker count alone does not establish health; inspect its heartbeat timestamp.
4. In **Policies & automation**, select **Human approval**. Enter a reason such as `College demonstration: review each recovery action`.
5. In **Resources**, check the demo API resource and ensure it has no emergency stop. Exact container binding is refreshed when evidence is collected for a new incident.
6. Return to **Demo Lab**. Confirm request throughput is nonzero and charts have samples. Actual throughput can be lower than the generator's nominal 10 requests/second because its loop waits for each response.
7. Check that no previous fault is active. Use **Clear fault** or **Clear / reconcile scenario** as appropriate.
8. Wait for the baseline to settle and firing scenario alerts to clear before injecting again.
9. Open Grafana in a spare tab. Keep the dashboard as the primary presentation surface.
10. Rehearse the full sequence on this laptop. Keep the ID of a successfully verified incident as a backup walkthrough.

Historical incidents are real persisted records, including records from earlier development tests. They are not automatically deleted. Follow the new incident created for your demonstration rather than selecting an unrelated old incident from the queue. Avoid running integration tests against the presentation database while its worker is active.

## 7. Suggested 8–12 minute presentation

### Minute 0–1: Explain the architecture

Open **Demo Lab** and select **Present** in the top bar. This hides the sidebar for more screen space; use **Exit presentation** to navigate again.

Click the topology nodes as you explain them:

- **Traffic generator:** continuously submits `/jobs` requests.
- **Demo API:** the managed workload and the only restart target in M1.
- **PostgreSQL:** application data storage.
- **Redis → worker:** background job processing.
- **Healing agent:** evidence, diagnosis, policy, execution and independent verification.

Suggested narration:

> “The moving packets are a visualization scaled to the API's measured request rate. The other edges show the application's configured dependencies; they are not individual traced requests.”

Explain green, fault and unknown states. An unavailable measurement is shown as unknown, not assumed healthy.

### Minute 1–2: Establish the baseline

Point out throughput, P95 latency, server error rate and CPU budget utilization. Hover over chart samples to show a timestamp and value. Change the chart window between 5, 10, 30 or 60 minutes if useful.

Suggested narration:

> “Traffic is flowing before I inject anything. We will compare the fault and recovery against this observed baseline.”

### Minute 2–4: Inject one fault

Choose **CPU saturation / SCN-002** for a visually clear first rehearsal. Click **Inject fault**, read the confirmation and confirm once.

Explain the bounded target and 10-minute fault TTL. Do not inject several scenarios together. Alert detection includes scrape, evaluation and hold times, so incident creation is not instantaneous.

Point out:

- The injection record and TTL countdown.
- The injection marker in the environment map and charts.
- Rising CPU utilization and any throughput/latency effect.
- The new incident in the **Recovery journey** selector.

An active injection record tracks the injector's run; it can remain present after a restart has removed the workload fault. Use telemetry and the verification verdict to explain actual health.

### Minute 4–6: Explain evidence and approve the plan

Click **Investigate** for the incident created by this injection.

1. **Overview:** explain the root cause, confidence, diagnostic rule and any contradictions.
2. **Evidence:** open a metric or container-state record. Show its source, observed time, units and binding generation.
3. **Plan & policy:** show the ordered steps and policy reason codes. Point out the exact container ID, plan version and content hash.
4. Optionally select **Dry run**. Explain that it validates and simulates the stored plan; it does not restart a container or authorize a real execution.
5. Select **Approve plan**, review the confirmation and approve.

Suggested narration:

> “The system has proposed a specific restart of this exact container. My approval is tied to this incident version and plan content. If the plan changes, the server rejects an outdated approval.”

If no approval button appears, inspect the state and policy decision. The system may be paused, may have escalated, or may be operating in automatic mode. Do not describe a missing button as an approval bypass.

### Minute 6–9: Show execution and independent verification

Open **Execution**. When the restart finishes, point out its status and the target binding.

Then open **Verification**, or return to Demo Lab to show the live check strip. It displays persisted preliminary observations while verification is running.

Suggested narration:

> “The restart action has completed. The incident is still verifying because recovery needs sustained evidence: readiness, traffic, error rate, latency, CPU and the scenario's alert conditions.”

The M1 verification target is a continuous 90-second healthy window after readiness prerequisites, with repeated samples. Total time can exceed 90 seconds because of warm-up, scrape stabilization or a failed/unknown check resetting the window.

Wait for the **incident** to become `RESOLVED`. Inspect the final verification record: passed checks, samples, observation window and recovery attribution. A manually cleared or expired fault can produce an externally attributed recovery rather than agent-healed resolution.

### Minute 9–10: Show accountability

Open **Audit timeline**. Expand a before/after entry and show the actor and sequence number. Explain that the view is reconstructed from persisted records.

Finish with:

> “This demo shows detection, evidence-based diagnosis, a bounded and policy-controlled action, and independent verification. If the system cannot establish safety or recovery, it escalates rather than claiming success.”

## 8. Other scenario demonstrations

| Scenario | What to point out | What distinguishes it |
|---|---|---|
| SCN-001 — Container crash | Availability loss, stopped container evidence, subsequent restart | Docker auto-restart is disabled, so the agent must intervene |
| SCN-002 — CPU saturation | CPU utilization and workload degradation | Fault runs inside the target container and restart removes it |
| SCN-003 — API hang | Readiness failures and request latency/timeouts | A running process can still provide an unhealthy service |

Do one complete scenario at a time. For a short presentation, one successful full lifecycle is stronger than rushing all three.

## 9. Reset between scenarios

1. Wait for the previous incident's final outcome. Record its ID if you want to revisit it.
2. In Demo Lab, use **Clear fault** to clear the injector's remaining record. After final resolution this does not rewrite the persisted verdict.
3. If a command timed out, inspect **System health → Fault command history**. Use **Clear / reconcile scenario** for that same scenario. Pending commands must age at least 30 seconds before reconciliation is permitted.
4. Wait for normal traffic and healthy signals. Check Prometheus if a prior scenario alert is still firing.
5. Inject the next scenario. Repeated alerts during an active episode can correlate to an existing incident, so do not assume every click immediately creates a new incident.

Do not click **Clear fault** during verification if you want to demonstrate agent-attributed recovery. Manual clearance is a useful separate demonstration of external recovery attribution.

If you deliberately reject a plan, or an incident escalates while the crash scenario has stopped the API, the crash clear operation only clears the injector's record. Restore the workload explicitly after explaining this manual intervention:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d demo-api
```

## 10. Optional governance demonstrations

- **Viewer role:** use another browser session and show that mutation controls are disabled or absent.
- **Reject a plan:** provide a reason and show the resulting state and approval record. Reset the fault afterwards.
- **Pause automation:** choose **Paused** under Policies & automation and supply a reason. The control plane continues observing; new workload mutations are blocked. Already dispatched actions may still finish.
- **Resource emergency stop:** use Resources to stop new mutations for a specific resource. Release the stop before the main recovery demonstration.
- **Stale approval:** an outdated incident/plan version yields a conflict and requires a fresh review. Do not retry an old approval blindly.

## 11. Daily startup, shutdown and rebuild

Start again without rebuilding unchanged images:

```powershell
Set-Location C:\dev\self-healing-agent
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d
```

After source changes:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d --build
```

Stop containers while preserving data:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml stop
```

Remove the project's containers and network while preserving named volumes:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml down
```

Avoid adding `-v` to shutdown commands unless you intentionally want to delete the databases and monitoring history. A clean screenshot is not a reason to discard your audit history.

## 12. Troubleshooting during rehearsal

| Symptom | Check and response |
|---|---|
| Docker named-pipe/engine error | Start Docker Desktop, select Linux containers, wait for `docker info` to succeed |
| Dashboard will not open | Check `docker compose ps -a` and `docker compose logs --tail 60 dashboard`; use port 3001 |
| Sign-in fails | Use the provisioned account; an existing database keeps its existing passwords. Inspect control-plane logs for migration or database errors |
| Charts say “Waiting for telemetry” | Check demo API readiness, traffic-generator logs and Prometheus targets; allow one-minute rate windows to fill |
| Demo API exited | Inspect `docker compose logs --tail 60 demo-api`; start it after dependencies are healthy |
| No incident yet | Check Prometheus alert state and Alertmanager routing; hold times and correlation delay are expected |
| No plan/approval appears | Read Overview, evidence and policy decisions; `ESCALATED` is an explicit outcome, not a UI failure |
| “Target resource/container not found” | Confirm the M1 override is used and the correct worker image is built; new evidence collection refreshes Compose bindings |
| Approval returns conflict | Refresh and inspect the new plan/version before deciding again |
| Verification keeps waiting | Read each check's reason. Missing traffic or unknown telemetry prevents resolution. Do not equate the progress display with a final verdict |
| SSE disconnects | The dashboard polls every five seconds and re-fetches snapshots on reconnect; stale data is labeled |
| Fault command uncertain | Inspect command history; clear/reconcile the same scenario before reinjecting |
| Many old stuck incidents | Earlier development tests may have populated the database. Inspect their IDs and records. Do not approve unrelated historical test plans during the demo |

Useful logs:

```powershell
docker compose logs --tail 80 control-plane
docker compose logs --tail 80 agent-worker
docker compose logs --tail 80 fault-injector
docker compose logs --tail 50 traffic-generator
```

## 13. Development and acceptance tests

For frontend development while the backend runs in Docker, stop the dashboard container to free port 3001:

```powershell
docker compose stop dashboard
Set-Location C:\dev\self-healing-agent\apps\dashboard
npm.cmd ci
npm.cmd run dev
```

The default local API rewrite points to `http://127.0.0.1:8088`. `CONTROL_API_URL` is a server-side setting. In a production build, changing the rewrite destination requires rebuilding the frontend.

Build and run mocked browser acceptance tests:

```powershell
Set-Location C:\dev\self-healing-agent\apps\dashboard
npm.cmd run build
npm.cmd run test:e2e -- console.spec.ts
```

Tests use installed Edge by default. To use Playwright Chromium, install it and set `PLAYWRIGHT_CHANNEL=chromium`. Mocked browser fixtures live only in the tests; the application itself does not substitute demo data for an unavailable backend.

Live, non-fault browser smoke test, with the whole stack running:

```powershell
$env:M1_LIVE='1'
npm.cmd run test:e2e -- live.spec.ts
Remove-Item Env:M1_LIVE
```

Opt-in live fault acceptance test — this injects a real local fault and approves its generated plan. Use Human approval mode, a healthy baseline, and no active fault. Run during rehearsal:

```powershell
$env:M1_LIVE_FAULT='SCN-002'
npm.cmd run test:e2e -- live-fault.spec.ts
Remove-Item Env:M1_LIVE_FAULT
```

`M1_USERNAME` and `M1_PASSWORD` override the test's default local admin login. The live fault test selects Human approval through the dashboard if necessary and leaves that safer mode selected. It clears its scenario afterwards. Screenshots and traces are written under `apps/dashboard/test-results`; copy screenshots you want to retain before another Playwright run replaces that directory.

Backend console contracts use an isolated temporary PostgreSQL database:

```powershell
Set-Location C:\dev\self-healing-agent
.\.venv\Scripts\python.exe -m pytest tests/integration/m1/test_console_api.py
```

Consult `docs/m1/dashboard-spec.md` for the implemented API and display semantics. Consult `docs/m1/m1h-validation.md` for what was actually tested; do not treat a test script's existence as proof that every scenario passed live.

## 14. A short backup plan

Before presentation day, retain screenshots and a previously verified incident ID. If a live run encounters an environmental issue, say so plainly, open that historical incident, and walk through its persisted evidence, plan, execution, verification and audit timeline. Label the walkthrough as a recorded prior run. Do not present a mocked browser-test screenshot as evidence of live autonomous healing.
