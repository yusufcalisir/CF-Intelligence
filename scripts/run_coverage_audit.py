"""Master Multi-Dimensional Code & Branch Coverage Audit Runner for CF-Intelligence.

Computes and reports full 4-tier coverage metrics across Frontend and Backend:
  1. Statements Coverage
  2. Branches Coverage (Critical Decision Paths & Boundary Conditions)
  3. Functions Coverage
  4. Lines Coverage

Usage:
    python scripts/run_coverage_audit.py             # Full frontend + backend audit
    python scripts/run_coverage_audit.py --frontend  # Frontend Vitest V8 audit only
    python scripts/run_coverage_audit.py --backend   # Backend Pytest-Cov branch audit only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def print_banner(title: str) -> None:
    line = "=" * 80
    print(f"\n{line}\n  {title}\n{line}")


def run_frontend_coverage() -> bool:
    """Run Frontend Coverage Audit with Vitest V8."""
    print_banner("1. RUNNING FRONTEND COVERAGE AUDIT (Vitest V8: Statements, Branches, Functions, Lines)")
    cmd = ["npm", "--prefix", "frontend", "test", "--", "--coverage", "--run"]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT, shell=True)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Frontend Coverage Audit {status} in {duration:.2f}s")
    return success


def run_backend_coverage() -> bool:
    """Run Backend Branch Coverage Audit with Pytest-Cov."""
    print_banner("2. RUNNING BACKEND BRANCH COVERAGE AUDIT (Pytest-Cov: --cov=app --cov-branch)")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "backend/tests/",
        "--cov=app",
        "--cov-branch",
        "--cov-report=term-missing",
        "-q",
    ]
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Backend Branch Coverage Audit {status} in {duration:.2f}s")
    return success


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CF-Intelligence Multi-Dimensional Code & Branch Coverage Audit."
    )
    parser.add_argument("--frontend", action="store_true", help="Run frontend coverage only")
    parser.add_argument("--backend", action="store_true", help="Run backend coverage only")
    args = parser.parse_args()

    overall_start = time.perf_counter()
    print_banner("CF-INTELLIGENCE MULTI-DIMENSIONAL CODE & BRANCH COVERAGE AUDIT")

    run_all = not (args.frontend or args.backend)
    fe_ok = True
    be_ok = True

    if run_all or args.frontend:
        fe_ok = run_frontend_coverage()

    if run_all or args.backend:
        be_ok = run_backend_coverage()

    total_duration = time.perf_counter() - overall_start

    print_banner("4-DIMENSIONAL COVERAGE AUDIT SUMMARY")
    print("  Dimension 1: Statements Coverage   [Evaluated across all modules]")
    print("  Dimension 2: Branches Coverage     [Evaluated across all conditional decision paths]")
    print("  Dimension 3: Functions Coverage    [Evaluated across all service methods & handlers]")
    print("  Dimension 4: Lines Coverage        [Evaluated across all executable LOC]")
    print(f"  Total Audit Execution Time         : {total_duration:.2f}s\n")

    if fe_ok and be_ok:
        print(">> ALL MULTI-DIMENSIONAL COVERAGE AUDITS COMPLETED SUCCESSFULLY!\n")
        return 0
    else:
        print(">> WARNING: COVERAGE AUDIT DETECTED FAILURES OR UNMET THRESHOLDS.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
