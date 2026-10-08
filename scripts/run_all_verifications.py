"""Master Automated Verification Suite Runner for Privacy-Preserving Cross-Bank Fraud Detection Platform.

Discovers and executes reference verifications, Hypothesis property tests,
adversarial robustness tests, and scalability benchmarks across all 21 verified subsystems.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFICATION_DIR = REPO_ROOT / "verification"


def run_all_verifications() -> bool:
    subsystems = sorted([
        d for d in VERIFICATION_DIR.iterdir()
        if d.is_dir() and not d.name.startswith((".", "__")) and ((d / "tests").is_dir() or any(d.glob("test_*.py")))
    ])
    total_dirs = len([d for d in VERIFICATION_DIR.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))])

    logger.info("==========================================================================")
    logger.info("STARTING MASTER SCIENTIFIC VERIFICATION SUITE ACROSS %d VERIFIED SUBSYSTEMS", len(subsystems))
    logger.info("==========================================================================")
    logger.info(
        "Discovered %d verified subsystem directories (out of %d total entries in verification/).",
        len(subsystems),
        total_dirs,
    )

    start_time = time.perf_counter()
    overall_success = True

    # Step 1: Run reference verification runners
    logger.info("\n--- Phase 1: Running Reference Verification Scripts ---")
    ref_scripts = sorted(list(VERIFICATION_DIR.glob("**/tests/*_reference_verification.py")))
    logger.info("Discovered %d reference verification scripts.", len(ref_scripts))
    for script in ref_scripts:
        logger.info("Executing reference script: %s", script.name)
        res = subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("FAILED reference script %s:\nSTDOUT:\n%s\nSTDERR:\n%s", script.name, res.stdout, res.stderr)
            overall_success = False

    # Step 2: Run benchmark scripts
    logger.info("\n--- Phase 2: Running Scalability Benchmark Scripts ---")
    bench_scripts = sorted(list(VERIFICATION_DIR.glob("**/tests/*_benchmark_scalability.py")))
    logger.info("Discovered %d benchmark scalability scripts.", len(bench_scripts))
    for script in bench_scripts:
        logger.info("Executing benchmark script: %s", script.name)
        res = subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("FAILED benchmark script %s:\nSTDOUT:\n%s\nSTDERR:\n%s", script.name, res.stdout, res.stderr)
            overall_success = False

    # Step 3: Run full pytest suite across verification/
    logger.info("\n--- Phase 3: Executing Pytest Verification Suite ---")
    pytest_res = subprocess.run([sys.executable, "-m", "pytest", "verification/", "-v"], cwd=REPO_ROOT, capture_output=True, text=True)
    logger.info("Pytest Suite Output Summary:\n%s", pytest_res.stdout.splitlines()[-1] if pytest_res.stdout else "")

    if pytest_res.returncode != 0:
        logger.error("Pytest suite reported failures:\n%s", pytest_res.stdout)
        if pytest_res.stderr:
            logger.error("Pytest stderr:\n%s", pytest_res.stderr)
        overall_success = False

    elapsed = time.perf_counter() - start_time
    logger.info("\n==========================================================================")
    if overall_success:
        logger.info("✅ ALL %d SUBSYSTEM VERIFICATION SUITES PASSED IN %.2f SECONDS!", len(subsystems), elapsed)
    else:
        logger.error("❌ VERIFICATION SUITE ENCOUNTERED FAILURES.")
    logger.info("==========================================================================")

    return overall_success


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Master Automated Verification Suite Runner across verified subsystems."
    )
    parser.parse_args()
    success = run_all_verifications()
    sys.exit(0 if success else 1)
