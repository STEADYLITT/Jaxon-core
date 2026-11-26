#!/usr/bin/env bash
# Jackson OS backup (phase 20, ADR-030).
#   BACKUP_DATABASE_URL  schema-owner connection (postgresql:// or postgresql+psycopg://)
#   ARTIFACT_DIR         local artifact folder (skipped if unset or empty; S3 has its own
#                        versioning)
#   $1                   output folder (a new timestamped folder is created inside it)
# Produces: db.dump (pg_dump custom format, includes grants, RLS policies and triggers),
# artifacts.tar.gz, counts.json (row counts per table) and SHA256SUMS.
set -euo pipefail
: "${BACKUP_DATABASE_URL:?set BACKUP_DATABASE_URL}"
OUT_ROOT="${1:?usage: backup.sh <output-folder>}"
DB_URL="${BACKUP_DATABASE_URL/+psycopg/}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_ROOT/jackson-$STAMP"
mkdir -p "$OUT"
umask 077

pg_dump --format=custom --no-password --file="$OUT/db.dump" "$DB_URL"

psql --no-password -X -A -t -v ON_ERROR_STOP=1 "$DB_URL" > "$OUT/counts.json" <<'SQL'
SELECT json_object_agg(table_name, n ORDER BY table_name) FROM (
  SELECT c.relname AS table_name,
         (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I', c.relname),
                false, true, '')))[1]::text::bigint AS n
  FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
  WHERE s.nspname = 'public' AND c.relkind = 'r') t;
SQL

if [ -n "${ARTIFACT_DIR:-}" ] && [ -d "$ARTIFACT_DIR" ]; then
  tar -czf "$OUT/artifacts.tar.gz" -C "$ARTIFACT_DIR" .
fi

(cd "$OUT" && sha256sum ./* > SHA256SUMS)
echo "$OUT"
