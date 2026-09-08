# Expected Signals — SCN-008

## Metrics
- **Queue Depth:** `demo_worker_queue_depth` increases continuously under steady traffic.
- **Heartbeat:** `demo_worker_heartbeat_seconds` continues to advance (process is alive, but paused).

## Logs (Loki)
- `{service="demo-worker"}` absence of "Processing event" log entries.

## Alerts
- `WorkerQueueBacklogHigh` (firing when queue length exceeds 50 messages).