"""Phase 20: backup and restore (ADR-030). Runs the real scripts in infra/backup against
real PostgreSQL: back up, restore into a fresh database, verify the data AND the security
model came back."""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import auth
from sqlalchemy import create_engine, make_url, text

from jackson_api.verify_restore import verify

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "infra" / "backup"


@pytest.fixture()
def restore_db():
    admin_url = os.environ["JACKSON_TEST_ADMIN_URL"]
    name = f"jackson_restore_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def run(script: str, *args, **env) -> subprocess.CompletedProcess:
    path = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    return subprocess.run(["bash", str(SCRIPTS / script), *map(str, args)],
                          env={**os.environ, "PATH": path, **env},
                          capture_output=True, text=True, timeout=180)


def test_backup_and_restore_round_trip(client, world, make_token, database, restore_db,
                                       tmp_path):
    for t in ("tools", "scripts"):
        assert shutil.which("pg_dump") and shutil.which("pg_restore"), t
    alice = auth(make_token, world.alice)
    client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json={
        "name": "Restore Me", "email": f"r{uuid.uuid4().hex[:6]}@x.example",
        "source_type": "inbound", "source_ref": "test form", "lawful_basis": "consent"})
    arts = tmp_path / "artifacts"
    (arts / "t").mkdir(parents=True)
    (arts / "t" / "report.json").write_text('{"ok": true}')

    b = run("backup.sh", tmp_path / "backups", BACKUP_DATABASE_URL=database["owner_url"],
            ARTIFACT_DIR=str(arts))
    assert b.returncode == 0, b.stderr
    folder = Path(b.stdout.strip().splitlines()[-1])
    assert {"db.dump", "counts.json", "artifacts.tar.gz", "SHA256SUMS"} <= {
        p.name for p in folder.iterdir()}
    counts = json.loads((folder / "counts.json").read_text())
    assert counts["contacts"] >= 1 and counts["tenants"] >= 2

    restored_arts = tmp_path / "restored-artifacts"
    r = run("restore.sh", folder, RESTORE_DATABASE_URL=restore_db,
            ARTIFACT_DIR=str(restored_arts))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL" not in r.stdout and "row_counts" in r.stdout
    assert (restored_arts / "t" / "report.json").read_text() == '{"ok": true}'

    # the restored database enforces tenant isolation exactly like the original
    eng = create_engine(restore_db)
    with eng.connect() as c:
        c.execute(text("SET ROLE jackson_app"))
        c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                  {"t": str(world.tenant_b)})
        assert c.scalar(text("SELECT count(*) FROM contacts WHERE tenant_id = :t"),
                        {"t": world.tenant_a}) == 0
    eng.dispose()


def test_restore_refuses_a_corrupted_backup_or_non_empty_target(database, restore_db,
                                                                tmp_path):
    b = run("backup.sh", tmp_path, BACKUP_DATABASE_URL=database["owner_url"])
    folder = Path(b.stdout.strip().splitlines()[-1])
    with open(folder / "db.dump", "ab") as f:
        f.write(b"tampered")
    r = run("restore.sh", folder, RESTORE_DATABASE_URL=restore_db)
    assert r.returncode != 0 and "FAILED" in (r.stdout + r.stderr)
    (folder / "db.dump").write_bytes((folder / "db.dump").read_bytes()[:-len(b"tampered")])
    r = run("restore.sh", folder, RESTORE_DATABASE_URL=database["owner_url"])
    assert r.returncode == 2 and "target database is not empty" in r.stderr  # never over live


def test_verify_catches_a_restore_that_lost_its_security(database, restore_db, tmp_path):
    b = run("backup.sh", tmp_path, BACKUP_DATABASE_URL=database["owner_url"])
    folder = Path(b.stdout.strip().splitlines()[-1])
    r = run("restore.sh", folder, RESTORE_DATABASE_URL=restore_db)
    assert r.returncode == 0, r.stdout + r.stderr
    eng = create_engine(restore_db, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("ALTER TABLE contacts NO FORCE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE outreach_messages DISABLE TRIGGER outreach_send_guard"))
        c.execute(text("GRANT DELETE ON audit_events TO jackson_app"))
    eng.dispose()
    failed = {x["check"]: x["detail"] for x in verify(restore_db) if not x["ok"]}
    assert set(failed) == {"row_level_security", "guard_triggers", "append_only_grants"}
    assert "contacts" in failed["row_level_security"]
