#!/usr/bin/env bash
# Jackson OS restore (phase 20, ADR-030). Restores into an EMPTY database, then verifies.
#   RESTORE_DATABASE_URL  connection to the empty target database (schema-owner role)
#   ARTIFACT_DIR          where to unpack artifacts (optional)
#   $1                    a backup folder made by backup.sh
# The roles jackson_app and jackson_worker must exist on the target server (they are
# cluster-wide); migration 0001/0003 create them on a fresh server.
set -euo pipefail
: "${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL}"
IN="${1:?usage: restore.sh <backup-folder>}"
DB_URL="${RESTORE_DATABASE_URL/+psycopg/}"

(cd "$IN" && sha256sum --check --quiet SHA256SUMS)   # refuse a corrupted backup

TABLES="$(psql --no-password -X -A -t "$DB_URL" -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
if [ "$TABLES" != "0" ]; then
  echo "refusing to restore: target database is not empty ($TABLES tables)" >&2
  exit 2
fi

pg_restore --no-password --exit-on-error --dbname="$DB_URL" "$IN/db.dump"

if [ -f "$IN/artifacts.tar.gz" ] && [ -n "${ARTIFACT_DIR:-}" ]; then
  mkdir -p "$ARTIFACT_DIR"
  tar -xzf "$IN/artifacts.tar.gz" -C "$ARTIFACT_DIR"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../../services/api"
python -m jackson_api.verify_restore "$RESTORE_DATABASE_URL" "$IN/counts.json"
