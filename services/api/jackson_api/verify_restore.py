"""Verify a restored (or any) Jackson OS database: `python -m jackson_api.verify_restore
<db-url> [counts.json]`. Exit code 1 if anything is wrong.

A restore only counts when the security model came back with the data: every tenant table
still has row-level security (enabled and forced) and its isolation policy, the guard
triggers exist, the API role still can't edit the audit log, the schema is at the latest
migration, and row counts match the backup.
"""

import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

API_DIR = Path(__file__).resolve().parent.parent
# Tenant tables deliberately without RLS, and why (reviewed in ADR-030).
RLS_EXEMPT = {
    "memberships": "read before a tenant is bound, to decide whether the user belongs to it; "
                   "the API always filters it by the signed-in user",
}
GUARD_TRIGGERS = {
    ("sites", "sites_only_tested_versions"),
    ("site_versions", "site_versions_immutable"),
    ("outreach_messages", "outreach_send_guard"),
    ("workflow_runs", "tenant_not_stopped"),
    ("tasks", "tenant_not_stopped"),
}


def head_revision() -> str:
    cfg = Config(str(API_DIR / "alembic.ini"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def verify(url: str, counts: dict | None = None) -> list[dict]:
    out = []

    def check(name, ok, detail):
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    eng = create_engine(url)
    try:
        with eng.connect() as c:
            rev = c.scalar(text("SELECT version_num FROM alembic_version"))
            check("schema_at_head", rev == head_revision(),
                  f"schema revision {rev}, latest {head_revision()}")
            tenant_tables = c.scalars(text(
                "SELECT table_name FROM information_schema.columns WHERE table_schema = "
                "'public' AND column_name = 'tenant_id' ORDER BY 1")).all()
            tenant_tables = [t for t in tenant_tables if t not in RLS_EXEMPT]
            weak = c.scalars(text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:t) AND NOT "
                "(c.relrowsecurity AND c.relforcerowsecurity)"), {"t": tenant_tables}).all()
            check("row_level_security", not weak,
                  "RLS enabled and forced on every tenant table"
                  + (f"; missing on {sorted(weak)}" if weak else ""))
            no_policy = c.scalars(text(
                "SELECT t FROM unnest(CAST(:t AS text[])) t WHERE NOT EXISTS (SELECT 1 FROM "
                "pg_policies p WHERE p.schemaname = 'public' AND p.tablename = t AND "
                "p.policyname = 'tenant_isolation')"), {"t": tenant_tables}).all()
            check("tenant_isolation_policies", not no_policy,
                  "every tenant table has its tenant_isolation policy"
                  + (f"; missing on {sorted(no_policy)}" if no_policy else ""))
            triggers = {(r[0], r[1]) for r in c.execute(text(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = "
                "t.tgrelid WHERE NOT t.tgisinternal AND t.tgenabled <> 'D'"))}
            missing = GUARD_TRIGGERS - triggers
            check("guard_triggers", not missing, "safety triggers present and enabled"
                  + (f"; missing {sorted(missing)}" if missing else ""))
            audit_locked = c.scalar(text(
                "SELECT NOT has_table_privilege('jackson_app', 'audit_events', 'UPDATE') AND "
                "NOT has_table_privilege('jackson_app', 'audit_events', 'DELETE') AND "
                "NOT has_table_privilege('jackson_worker', 'suppressions', 'DELETE')"))
            check("append_only_grants", audit_locked,
                  "API can't edit the audit log; worker can't delete opt-outs")
            if counts:
                diffs = {}
                for table, n in counts.items():
                    got = c.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                    if got != n:
                        diffs[table] = {"backup": n, "restored": got}
                check("row_counts", not diffs, f"{len(counts)} tables match the backup"
                      if not diffs else f"mismatch: {diffs}")
    finally:
        eng.dispose()
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m jackson_api.verify_restore <db-url> [counts.json]")
        return 2
    url = argv[1] if "+psycopg" in argv[1] else argv[1].replace("postgresql://",
                                                                "postgresql+psycopg://", 1)
    counts = json.loads(Path(argv[2]).read_text()) if len(argv) > 2 else None
    results = verify(url, counts)
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']:<26} {r['detail']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
