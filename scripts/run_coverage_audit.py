"""Master Multi-Dimensional Code & Branch Coverage Audit Runner for CF-Intelligence.

Computes and reports full 4-tier coverage metrics across Frontend and Backend:
  1. Statements Coverage
  2. Branches Coverage (Critical Decision Paths & Boundary Conditions)
  3. Functions Coverage
  4. Lines Coverage

Enforces regression thresholds via --cov-fail-under (default: 75%).

Usage:
    python scripts/run_coverage_audit.py                  # Full frontend + backend audit with 75% gate
    python scripts/run_coverage_audit.py --frontend       # Frontend Vitest V8 audit only
    python scripts/run_coverage_audit.py --backend        # Backend Pytest-Cov branch audit only with 75% gate
    python scripts/run_coverage_audit.py --fail-under 80  # Custom coverage threshold
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def print_banner(title: str) -> None:
    line = "=" * 80
    print(f"\n{line}\n  {title}\n{line}")


def run_command(
    cmd: list[str],
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Execute command cross-platform by resolving executable via PATH."""
    executable = shutil.which(cmd[0]) or cmd[0]
    is_windows = sys.platform.startswith("win")
    use_shell = is_windows and cmd[0] in ("npm", "npx")
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run([executable] + cmd[1:], cwd=cwd, env=run_env, shell=use_shell)


def parse_backend_coverage(cov_file: Path) -> dict[str, Any] | None:
    """Extract genuinely computed metrics from pytest-cov JSON report."""
    if not cov_file.exists():
        return None
    try:
        with open(cov_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        totals = data.get("totals", {})
        stmts = totals.get("num_statements", 0)
        covered_stmts = totals.get("covered_lines", 0)
        branches = totals.get("num_branches", 0)
        covered_branches = totals.get("covered_branches", 0)
        pct_covered = float(totals.get("percent_covered", 0.0))

        # Calculate function coverage across files
        total_funcs = 0
        covered_funcs = 0
        for file_info in data.get("files", {}).values():
            funcs = file_info.get("functions", {})
            for func_name, func_info in funcs.items():
                if not func_name:
                    continue
                total_funcs += 1
                summary = func_info.get("summary", {})
                if summary.get("covered_lines", 0) > 0:
                    covered_funcs += 1

        func_pct = (covered_funcs / total_funcs * 100.0) if total_funcs > 0 else 0.0
        stmt_pct = float(
            totals.get(
                "percent_statements_covered",
                (covered_stmts / stmts * 100.0) if stmts else 0.0,
            )
        )
        branch_pct = float(
            totals.get(
                "percent_branches_covered",
                (covered_branches / branches * 100.0) if branches else 0.0,
            )
        )
        lines_pct = pct_covered

        return {
            "statements_pct": round(stmt_pct, 2),
            "branches_pct": round(branch_pct, 2),
            "functions_pct": round(func_pct, 2),
            "lines_pct": round(lines_pct, 2),
            "total_coverage": round(pct_covered, 2),
            "statements": stmts,
            "covered_statements": covered_stmts,
            "branches": branches,
            "covered_branches": covered_branches,
            "functions": total_funcs,
            "covered_functions": covered_funcs,
        }
    except Exception as e:
        print(f">> Warning: Failed parsing backend coverage JSON: {e}")
        return None


def parse_frontend_coverage(cov_file: Path) -> dict[str, Any] | None:
    """Extract genuinely computed metrics from Vitest coverage-summary.json."""
    if not cov_file.exists():
        return None
    try:
        with open(cov_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        total = data.get("total", {})
        stmts = total.get("statements", {})
        branches = total.get("branches", {})
        funcs = total.get("functions", {})
        lines = total.get("lines", {})

        return {
            "statements_pct": round(float(stmts.get("pct", 0.0)), 2),
            "branches_pct": round(float(branches.get("pct", 0.0)), 2),
            "functions_pct": round(float(funcs.get("pct", 0.0)), 2),
            "lines_pct": round(float(lines.get("pct", 0.0)), 2),
            "total_coverage": round(float(lines.get("pct", 0.0)), 2),
            "statements": stmts.get("total", 0),
            "covered_statements": stmts.get("covered", 0),
            "branches": branches.get("total", 0),
            "covered_branches": branches.get("covered", 0),
            "functions": funcs.get("total", 0),
            "covered_functions": funcs.get("covered", 0),
        }
    except Exception as e:
        print(f">> Warning: Failed parsing frontend coverage JSON: {e}")
        return None


def run_frontend_coverage() -> tuple[bool, dict[str, Any] | None]:
    """Run Frontend Coverage Audit with Vitest V8."""
    print_banner("1. RUNNING FRONTEND COVERAGE AUDIT (Vitest V8: Statements, Branches, Functions, Lines)")
    cmd = ["npm", "--prefix", "frontend", "test", "--", "--coverage", "--run"]
    start = time.perf_counter()
    res = run_command(cmd)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Frontend Coverage Audit {status} in {duration:.2f}s")

    summary_file = REPO_ROOT / "frontend" / "coverage" / "coverage-summary.json"
    metrics = parse_frontend_coverage(summary_file)
    return success, metrics


def run_backend_coverage(fail_under: int = 75) -> tuple[bool, dict[str, Any] | None]:
    """Run Backend Branch Coverage Audit with Pytest-Cov and enforcement threshold."""
    print_banner(f"2. RUNNING BACKEND BRANCH COVERAGE AUDIT (Pytest-Cov: --cov=app --cov-branch --cov-fail-under={fail_under})")
    cov_json_path = REPO_ROOT / "coverage_backend.json"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "backend/tests/",
        "--cov=app",
        "--cov-branch",
        f"--cov-fail-under={fail_under}",
        "--cov-report=term-missing",
        f"--cov-report=json:{cov_json_path}",
        "-q",
    ]
    backend_dir = str(REPO_ROOT / "backend")
    env = {"PYTHONPATH": backend_dir}

    start = time.perf_counter()
    res = run_command(cmd, env=env)
    duration = time.perf_counter() - start
    success = res.returncode == 0
    status = "PASSED" if success else "FAILED"
    print(f"\n>> Backend Branch Coverage Audit {status} in {duration:.2f}s")

    metrics = parse_backend_coverage(cov_json_path)
    return success, metrics


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CF-Intelligence Multi-Dimensional Code & Branch Coverage Audit."
    )
    parser.add_argument("--frontend", action="store_true", help="Run frontend coverage only")
    parser.add_argument("--backend", action="store_true", help="Run backend coverage only")
    parser.add_argument(
        "--fail-under",
        type=int,
        default=75,
        help="Coverage regression gate percentage threshold (default: 75)",
    )
    args = parser.parse_args()

    overall_start = time.perf_counter()
    print_banner("CF-INTELLIGENCE MULTI-DIMENSIONAL CODE & BRANCH COVERAGE AUDIT")

    run_all = not (args.frontend or args.backend)
    fe_ok = True
    be_ok = True
    fe_metrics: dict[str, Any] | None = None
    be_metrics: dict[str, Any] | None = None

    if run_all or args.frontend:
        fe_ok, fe_metrics = run_frontend_coverage()

    if run_all or args.backend:
        be_ok, be_metrics = run_backend_coverage(fail_under=args.fail_under)

    total_duration = time.perf_counter() - overall_start

    print_banner("4-DIMENSIONAL COVERAGE AUDIT SUMMARY & ENFORCEMENT REPORT")
    print(f"  Enforcement Gate (Threshold)       : >= {args.fail_under}%\n")

    if be_metrics and fe_metrics:
        comp_stmt = round((be_metrics["statements_pct"] + fe_metrics["statements_pct"]) / 2, 2)
        comp_branch = round((be_metrics["branches_pct"] + fe_metrics["branches_pct"]) / 2, 2)
        comp_func = round((be_metrics["functions_pct"] + fe_metrics["functions_pct"]) / 2, 2)
        comp_lines = round((be_metrics["lines_pct"] + fe_metrics["lines_pct"]) / 2, 2)

        print(f"  Dimension 1: Statements Coverage   : Backend: {be_metrics['statements_pct']}% | Frontend: {fe_metrics['statements_pct']}% | Composite: {comp_stmt}%")
        print(f"  Dimension 2: Branches Coverage     : Backend: {be_metrics['branches_pct']}% | Frontend: {fe_metrics['branches_pct']}% | Composite: {comp_branch}%")
        print(f"  Dimension 3: Functions Coverage    : Backend: {be_metrics['functions_pct']}% | Frontend: {fe_metrics['functions_pct']}% | Composite: {comp_func}%")
        print(f"  Dimension 4: Lines Coverage        : Backend: {be_metrics['lines_pct']}% | Frontend: {fe_metrics['lines_pct']}% | Composite: {comp_lines}%")
    elif be_metrics:
        print(f"  Dimension 1: Statements Coverage   : {be_metrics['statements_pct']}% ({be_metrics['covered_statements']}/{be_metrics['statements']} stmts)")
        print(f"  Dimension 2: Branches Coverage     : {be_metrics['branches_pct']}% ({be_metrics['covered_branches']}/{be_metrics['branches']} branches)")
        print(f"  Dimension 3: Functions Coverage    : {be_metrics['functions_pct']}% ({be_metrics['covered_functions']}/{be_metrics['functions']} funcs)")
        print(f"  Dimension 4: Lines Coverage        : {be_metrics['lines_pct']}% (overall total: {be_metrics['total_coverage']}%)")
    elif fe_metrics:
        print(f"  Dimension 1: Statements Coverage   : {fe_metrics['statements_pct']}% ({fe_metrics['covered_statements']}/{fe_metrics['statements']} stmts)")
        print(f"  Dimension 2: Branches Coverage     : {fe_metrics['branches_pct']}% ({fe_metrics['covered_branches']}/{fe_metrics['branches']} branches)")
        print(f"  Dimension 3: Functions Coverage    : {fe_metrics['functions_pct']}% ({fe_metrics['covered_functions']}/{fe_metrics['functions']} funcs)")
        print(f"  Dimension 4: Lines Coverage        : {fe_metrics['lines_pct']}%")

    print(f"\n  Total Audit Execution Time         : {total_duration:.2f}s\n")

    if fe_ok and be_ok:
        print(">> ALL MULTI-DIMENSIONAL COVERAGE AUDITS COMPLETED SUCCESSFULLY!\n")
        return 0
    else:
        print(">> WARNING: COVERAGE AUDIT DETECTED FAILURES OR UNMET THRESHOLDS.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
