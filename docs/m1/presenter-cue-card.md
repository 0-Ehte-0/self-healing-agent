# M1 presenter cue card

Use [the full guide](demo-guide.md) for installation, credentials and troubleshooting. Keep this page beside the browser during your presentation.

## Before the audience arrives

```powershell
Set-Location C:\dev\self-healing-agent
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d
```

Open http://localhost:3001. Sign in, check System health, select Human approval in Policies & automation, clear old fault records and wait for baseline traffic. Build changed code before presentation day using `up -d --build` with the same two Compose files.

## The demonstration

| Time | Screen and action | Say |
|---|---|---|
| 0:00 | Demo Lab → Present; click topology nodes | “This local workload receives real generated traffic. The agent observes and repairs the managed API container.” |
| 1:00 | Hover throughput, latency and CPU charts | “These are measured signals. Moving packets represent request rate; dependency edges show the configured architecture.” |
| 2:00 | Inject CPU saturation, SCN-002; confirm once | “This bounded fault runs inside the API container. Its maximum lifetime is ten minutes.” |
| 3:00 | Show CPU rise, injection marker and new incident | “Detection includes monitoring hold times. The agent now collects evidence and classifies the fault.” |
| 4:00 | Investigate → Evidence → Plan & policy | “Here are the source measurements, diagnosis, exact target binding and proposed restart.” |
| 5:00 | Approve plan; confirm | “Approval is tied to this incident version and plan content. An outdated approval is rejected.” |
| 6:00 | Execution → Verification | “Restart success is only the action result. The incident remains open while recovery is independently measured.” |
| 7:00–9:00 | Show observations and then RESOLVED | “The verifier requires a sustained healthy window. This final verdict records the checks and attributes recovery.” |
| 9:00 | Audit timeline; expand before/after | “The history records who acted, what changed and the sequence of decisions.” |

Timing varies with laptop load, scrape intervals and warm-up. Explain the observations while waiting. Do not clear a fault during verification when demonstrating agent-attributed recovery.

## Finish and reset

Record the resolved incident ID. Return to Demo Lab, clear the remaining injection record and wait for a healthy baseline before another scenario. Exit Present to restore navigation.

If the live run escalates, open its explanation and identify the cause. For a backup walkthrough, open a previously verified incident and clearly describe it as a recorded earlier run.

## Questions you may receive

- **Is this cloud production infrastructure?** M1 demonstrates the control-plane design locally with Docker and a bounded target. Wider cloud providers belong to later milestones.
- **Does M1 use an LLM?** Current diagnosis and planning are deterministic and explainable; do not claim learned diagnosis.
- **Why approve an autonomous agent?** Human approval is one policy mode. Automatic mode can execute eligible low-risk plans, while the safety and verification checks still apply.
- **Why is restart success insufficient?** A running process may still be unhealthy. Recovery requires independent readiness and telemetry checks over time.
- **What happens if it cannot fix the fault?** It records failure or uncertainty, applies bounded retry policy where eligible, and escalates instead of claiming resolution.
