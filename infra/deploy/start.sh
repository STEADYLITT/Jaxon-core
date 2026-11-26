#!/usr/bin/env bash
# Container entrypoint for a single-owner deployment (ADR-031): one container runs the API
# and the worker, sharing one persistent volume (artifacts + generated secrets).
#   1. migrate + give the DB roles their passwords + set up the owner (idempotent)
#   2. derive the remaining settings (owner-set variables always win)
#   3. print the production checklist (names only) - the API refuses to start if it fails
#   4. run worker + API; if either stops, stop the container so the host restarts it
set -euo pipefail
cd /app/services/api

python -m jackson_api.deploy_init
eval "$(python -m jackson_api.deploy_env --export)"
mkdir -p "$ARTIFACT_DIR"
python -m jackson_api.preflight || true   # the API itself fails closed below

python -m jackson_api.worker &
WORKER=$!
uvicorn jackson_api.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*' &
API=$!

trap 'kill -TERM $WORKER $API 2>/dev/null; wait' TERM INT
STATUS=0
wait -n $WORKER $API || STATUS=$?
echo "a process stopped (exit $STATUS); stopping the container so it restarts" >&2
kill -TERM $WORKER $API 2>/dev/null || true
wait || true
exit $STATUS
