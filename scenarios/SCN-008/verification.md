# Verification Requirements — SCN-008

During the 90-second stabilization window:
1. `demo_worker_queue_depth` drains to 0.
2. Worker logs confirm message processing resumed.
3. `WorkerQueueBacklogHigh` alert is inactive.