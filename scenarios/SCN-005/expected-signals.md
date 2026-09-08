# Expected Signals — SCN-005

## Metrics
- **Memory Working Set:** Container resident memory spikes by ~256MB.
- **System Memory Usage:** Host/container memory usage exceeds 85%.

## Logs (Loki)
- `{service="fault-injector"} |= "Allocated chunk"`

## Alerts
- `HighMemoryUsage` (firing when container memory > 80%).