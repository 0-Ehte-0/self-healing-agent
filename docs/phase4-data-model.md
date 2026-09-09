# Phase 4: control-plane record model

PostgreSQL is the system of record. The schema contains all sixteen tables in Phase 4 plus `users` for the seeded actor accounts. UUID keys, timezone-aware timestamps, native database enums, JSONB evidence payloads, foreign keys, uniqueness constraints and range checks are defined in `apps/control-api/app/db/models/`.

## Transactions and auditing

Use `unit_of_work(session_factory, actor)` from `app.db.repositories.control_plane`. It creates one transaction, sets a transaction-local actor, commits on success, and rolls back the entire operation on failure. Repository methods flush but never commit independently. Every domain-table mutation produces an audit entry in the same transaction through a PostgreSQL trigger, including direct SQL writes. Mutation without an actor is rejected. Audit snapshots exclude user password hashes.

The runtime role is `control_app`: it cannot change the schema, modify the migration history, insert fabricated audit rows, or update/delete/truncate audit records. A security-definer trigger owned by the migration role appends audit entries. An additional trigger rejects audit modification even when using the migration owner. As with any PostgreSQL protection, a database superuser can deliberately disable enforcement; the application never runs as that role.

Incident transitions are checked in the repository and database against the canonical diagram. New incidents start `DETECTED`; only `VERIFYING` may reach `RESOLVED`. Approval routing uses the recorded requirement, retries increment only on `VERIFYING -> DIAGNOSED`, and attempts cannot exceed the recorded retry limit. Incident and execution updates require version increments. Approved plans and their steps, and approval decision records, are immutable at the database layer.

`store_event` deduplicates the tuple `(source, resource_id, fingerprint, dedup_window)` atomically. The caller supplies a SHA-256 fingerprint and correlation window. Phase 5 will define ingestion normalization and sliding-window selection. Active incident correlation keys are unique; terminal incidents release the key. `record_execution` returns the existing execution on an identical idempotency key, rejects a key reused for another target/step/incident, and validates plan ownership. Phase 15 remains responsible for executing an action and honoring its recorded identity.

Resource locks have unique resource IDs, expiring leases, and owner/token checks; expired leases can be claimed atomically. `reconstruct` reads events, evidence, diagnoses, plans, steps, approvals, executions, verification results, model calls, anomaly scores, and the ordered audit timeline through a fresh database session.

## Startup and seeds

`docker compose up -d --build` runs migrations and seeds as the migration owner in a bootstrap process, configures the restricted runtime login, then replaces that process with the API. Runtime `DATABASE_URL` uses `control_app`. The owner URL and bootstrap password are removed from the API process environment.

The development seed creates `approver` and `viewer`, with scrypt password hashes; passwords default to `local-approver-change-me` and `local-viewer-change-me`, or can be supplied through `SEED_APPROVER_PASSWORD` and `SEED_VIEWER_PASSWORD`. Authentication endpoints and role enforcement arrive in later phases. Seeds are idempotent and do not reset existing passwords or policies.

Five local policy records cover the MVP catalog with retry limit 2 and cooldown 600 seconds. The two demo resources are logical Compose discovery records with `managed=false`; the Docker adapter must resolve an immutable container ID before enabling execution.

## Verification

Set `TEST_DATABASE_URL` to a PostgreSQL migration-owner connection URL, then run:

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/control_plane'
$env:OTEL_SDK_DISABLED='true'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase4_database.py
```

The suite creates a unique `phase4_test_<uuid>` database, exercises upgrade/downgrade/upgrade, verifies concurrent deduplication and optimistic locking, checks execution replay and lock ownership, reconstructs a persisted lifecycle, attempts audit mutation as both runtime role and owner, and tests seed and approval-record constraints. It drops only its disposable database afterward. Without `TEST_DATABASE_URL`, these integration tests are explicitly skipped. CI supplies the URL.

Versioned JSON schemas for incident, execution and policy records live in `contracts/`. The initial migration uses frozen DDL rather than importing mutable application models. Subsequent schema changes require a new Alembic revision.
