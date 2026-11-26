"""Every third-party module the app imports must be a declared runtime dependency, so the
production image (which installs only those) can start. Caught a real miss: httpx."""

import ast
import sys
import tomllib
from pathlib import Path

API = Path(__file__).resolve().parents[1]
# import name -> distribution name, where they differ; plus modules that come with another
PROVIDED_BY = {"jwt": "pyjwt", "starlette": "fastapi"}


def test_runtime_imports_are_declared_dependencies():
    deps = {d.split(">")[0].split("<")[0].split("[")[0].strip().lower() for d in tomllib.loads(
        (API / "pyproject.toml").read_text())["project"]["dependencies"]}
    missing = set()
    for f in (API / "jackson_api").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0
                     and node.module else [])
            for n in names:
                top = n.split(".")[0]
                if top in sys.stdlib_module_names or top == "jackson_api":
                    continue
                if PROVIDED_BY.get(top, top).lower() not in deps:
                    missing.add(f"{top} ({f.relative_to(API)})")
    assert not missing, f"imported but not in [project].dependencies: {sorted(missing)}"
