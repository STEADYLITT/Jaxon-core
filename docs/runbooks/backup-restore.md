# Runbook — backup and restore

Scripts: `infra/backup/backup.sh` and `infra/backup/restore.sh` (phase 20, ADR-030). Both need the PostgreSQL 16 client tools (`pg_dump`, `pg_restore`, `psql`) and use the **schema-owner** connection, not the app or worker role.

## Take a backup
```bash
BACKUP_DATABASE_URL=postgresql://<owner>@<host>/jackson \
ARTIFACT_DIR=/var/lib/jackson/artifacts \
infra/backup/backup.sh /backups
```
This creates `/backups/jackson-<UTC timestamp>/` containing:
- `db.dump`: pg_dump custom format. It includes data, grants, row-level-security policies and triggers.
- `artifacts.tar.gz`: the local artifact store. Skipped when artifacts live in S3, which needs bucket versioning instead.
- `counts.json`: row counts per table, used to verify a restore.
- `SHA256SUMS`: checked before any restore.

Recommended schedule:
- nightly backups, keeping 14 dailies and 8 weeklies
- copy each backup off the database host
- encrypt at rest

The backup contains customer data. Treat it like the database.

## Restore (and prove it worked)
1. Create an **empty** database on a server where the roles `jackson_app` and `jackson_worker` exist. On a fresh server, run the migrations once on a scratch database to create them, or create the roles by hand.
2. Run:
   ```bash
   RESTORE_DATABASE_URL=postgresql://<owner>@<host>/jackson_restored \
   ARTIFACT_DIR=/var/lib/jackson/artifacts \
   infra/backup/restore.sh /backups/jackson-<timestamp>
   ```
3. The script refuses to run if any of these hold:
   - a checksum doesn't match
   - the target database isn't empty

   After restoring, it runs `python -m jackson_api.verify_restore`, which must print `PASS` for every check:
   - `schema_at_head`: the latest migration is applied
   - `row_level_security` and `tenant_isolation_policies`: every tenant table is still isolated. `memberships` is the one reviewed exemption, because it's read before a tenant is chosen.
   - `guard_triggers`: the send, site and global-stop guards exist and are enabled
   - `append_only_grants`: the API can't edit the audit log, and the worker can't delete opt-outs
   - `row_counts`: every table matches the backup
4. Point `DATABASE_URL` / `WORKER_DATABASE_URL` at the restored database and start the API, then the workers. Then check `/ready` and the dashboard.

**A restore that fails verification is not a restore.** Don't put it into service. Restore again from the previous backup and investigate.

## Practise it
CI runs the full round trip on every change (`services/api/tests/test_recovery.py`):
- back up, restore into a fresh database, verify, and prove tenant isolation still holds
- confirm a corrupted backup, a non-empty target, or a restore that lost its security are each refused

Do a manual drill once a month against a copy of production, and record the time it took.
