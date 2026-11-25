"""Repo-level guardrail tests (phase 02). Run from repo root: pytest tests"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from jackson_api import contracts as py  # noqa: E402

SPEC_LAYOUT = [
    "apps/console", "services/api", "services/workers", "packages/contracts",
    "packages/policy", "packages/connectors", "skills", "agents", "workflows",
    "infra", "tests", "docs",
]


def test_spec_layout_exists():
    missing = [p for p in SPEC_LAYOUT if not (ROOT / p).is_dir()]
    assert not missing, f"missing spec dirs: {missing}"


def test_spec_files_present():
    spec = ROOT / "docs" / "jackson-os-spec"
    for name in [
        "START_HERE.md", "00_Master_Blueprint.md", "01_Persona_and_Brain.md",
        "02_Architecture_and_Body.md", "03_Execution_and_Business_Skills.md",
        "04_Operations_and_Command_Center.md", "05_Agent_Factory_and_Expansion.md",
        "06_Developer_Reference_and_Build_Order.md", "PHASE_01_GAP_REPORT.md",
    ]:
        assert (spec / name).is_file(), name


def test_env_example_has_names_only():
    lines = (ROOT / ".env.example").read_text().splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        key, _, value = s.partition("=")
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", key), key
        # Only non-secret local defaults are allowed to carry a value.
        if value:
            assert not re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", key), f"{key} must be blank"


def _ts_array(src: str, name: str) -> list[str]:
    m = re.search(rf"export const {name} = \[(.*?)\] as const", src, re.DOTALL)
    assert m, name
    return re.findall(r'"([^"]+)"', m.group(1))


def test_ts_and_python_contracts_match():
    src = (ROOT / "packages/contracts/src/index.ts").read_text()
    assert _ts_array(src, "TASK_STATES") == [s.value for s in py.TaskState]
    assert _ts_array(src, "COMMAND_TYPES") == [c.value for c in py.CommandType]
    assert _ts_array(src, "ENTITY_NAMES") == [e.value for e in py.EntityName]
    m = re.search(r"export const AUTONOMY_LEVELS = \{(.*?)\} as const", src, re.DOTALL)
    ts_levels = {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", m.group(1))}
    assert ts_levels == {lvl.name: lvl.value for lvl in py.AutonomyLevel}


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),          # OpenAI / Anthropic style
    re.compile(r"shpat_[a-f0-9]{32}"),             # Shopify admin token
    re.compile(r"ghp_[A-Za-z0-9]{36}"),            # GitHub PAT
    re.compile(r"AKIA[0-9A-Z]{16}"),               # AWS access key id
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


def test_no_secrets_in_tracked_files():
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hits = []
    for f in files:
        p = ROOT / f
        if not p.is_file() or p.suffix in {".png", ".jpg", ".lock"} or f.endswith("test_repo.py"):
            continue
        text = p.read_text(errors="ignore")
        hits += [f for pat in SECRET_PATTERNS if pat.search(text)]
    assert not hits, f"possible secrets in: {hits}"
    assert ".env" not in files


def _ts_interface_fields(src: str, name: str) -> set[str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", src, re.DOTALL)
    assert m, name
    return set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.MULTILINE))


def test_ts_run_types_match_api_wire_format():
    from jackson_api.routes_runs import CommandIn, RunOut, TaskOut

    src = (ROOT / "packages/contracts/src/index.ts").read_text()
    assert _ts_interface_fields(src, "WorkflowRun") == set(RunOut.model_fields)
    assert _ts_interface_fields(src, "TaskOut") == set(TaskOut.model_fields)
    assert _ts_interface_fields(src, "Command") == set(CommandIn.model_fields)
