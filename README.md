# Autonomous self-healing agent — M1

Run the local application with Docker Desktop (Linux containers):

```powershell
Set-Location C:\dev\self-healing-agent
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml up -d --build
```

Open **http://localhost:3001** for the Sentinel dashboard. Grafana is on port 3000.

The M1 Compose override is required for managed-container labels, the CPU budget, the worker's Docker access and the crash scenario's restart policy. Ordinary shutdown preserves named database volumes:

```powershell
docker compose -f docker-compose.yml -f infrastructure/compose/compose.m1.yml down
```
