"""Run the deterministic repository validation path used for review and CI."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"\n=== {label} ===", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Jeet Analyzer from one command")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--skip-review-package", action="store_true")
    args = parser.parse_args(argv)

    run("PYTHON REGRESSION SUITE", [sys.executable, "-m", "pytest", "-q"])
    run("OFFLINE FLAGSHIP REPLAY", [sys.executable, "scripts/demo_flagship.py"])

    if not args.skip_review_package:
        run("SANITIZED REVIEW PACKAGE", [sys.executable, "scripts/build_external_review.py"])

    if not args.skip_frontend:
        npm = shutil.which("npm")
        if not npm:
            raise SystemExit("npm is required for frontend validation; use --skip-frontend to omit it")
        frontend = ROOT / "frontend"
        run("FRONTEND INSTALL", [npm, "ci"], cwd=frontend)
        run("FRONTEND TESTS", [npm, "test"], cwd=frontend)
        run("PRODUCTION FRONTEND BUILD", [npm, "run", "build"], cwd=frontend)

    print("\nVALIDATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
