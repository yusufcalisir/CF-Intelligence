"""Python integration test wrapper for Smart Contract Hardhat test suite."""

import subprocess
import sys
from pathlib import Path
import pytest


def test_smart_contract_hardhat_suite():
    contracts_dir = Path(__file__).resolve().parent.parent.parent.parent / "contracts"
    hardhat_config = contracts_dir / "hardhat.config.js"
    node_modules = contracts_dir / "node_modules"

    if not hardhat_config.exists():
        pytest.skip("hardhat.config.js not found in contracts directory")

    if not node_modules.exists() or not (node_modules / "hardhat").exists():
        pytest.skip("Hardhat local node_modules not installed in contracts directory")

    # Run npx hardhat test inside contracts directory
    res = subprocess.run(
        ["npx.cmd" if sys.platform == "win32" else "npx", "hardhat", "test"],
        cwd=contracts_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    if "Error HHE22" in res.stderr or "Trying to use a non-local installation of Hardhat" in res.stderr:
        pytest.skip("Hardhat local installation missing in CI runner")

    assert res.returncode == 0, f"Hardhat test suite failed:\n{res.stdout}\n{res.stderr}"
    assert "13 passing" in res.stdout
