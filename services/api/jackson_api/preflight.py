"""Production preflight: `python -m jackson_api.preflight`. Exit code 1 if any check fails.
Prints check names and descriptions only, never values."""

import sys

from .security import preflight


def main() -> int:
    checks = preflight()
    for c in checks:
        print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']:<26} {c['detail']}")
    failed = sum(not c["ok"] for c in checks)
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
