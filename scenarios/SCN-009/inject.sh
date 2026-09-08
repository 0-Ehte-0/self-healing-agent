#!/usr/bin/env bash
set -euo pipefail

curl -s -f -X POST "${FAULT_INJECTOR_URL:-http://localhost:8003}/faults/SCN-009/inject?ttl_seconds=600" \
  -H "X-Fault-Token: ${FAULT_INJECTOR_SECRET:-injector-secret-token}" \
  -H "Content-Type: application/json"