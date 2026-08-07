"""Python integration test wrapper for Smart Contract Hardhat test suite."""

import subprocess
import sys
from pathlib import Path
import pytest


def test_smart_contract_hardhat_suite():
    contracts_dir = Path(__file__).resolve().parent.parent.parent.parent / "contracts"
    assert (contracts_dir / "hardhat.config.js").exists()

    # Run npx hardhat test inside contracts directory
    res = subprocess.run(
        ["npx.cmd" if sys.platform == "win32" else "npx", "hardhat", "test"],
        cwd=contracts_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0, f"Hardhat test suite failed:\n{res.stdout}\n{res.stderr}"
    assert "13 passing" in res.stdout
