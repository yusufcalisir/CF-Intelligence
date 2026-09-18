"""Unit test suite verifying scripts/ utility suite integrity, AST syntax, and CLI interfaces.

Ensures that all operational utilities in scripts/ parse valid Python AST,
contain no UTF-8 BOM byte order marks, and execute their command-line help flags
cleanly without crashing on Python 3.12.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def test_scripts_no_utf8_bom() -> None:
    """Verify that zero scripts contain the UTF-8 BOM (U+FEFF / 0xEF 0xBB 0xBF)."""
    scripts = list(SCRIPTS_DIR.glob("*.py"))
    assert len(scripts) >= 30, f"Expected at least 30 scripts, found {len(scripts)}"

    bom_files: list[str] = []
    for script in scripts:
        with open(script, "rb") as f:
            header = f.read(3)
            if header == b"\xef\xbb\xbf":
                bom_files.append(script.name)

    assert not bom_files, f"UTF-8 BOM found in scripts: {bom_files}"


def test_scripts_valid_ast_syntax() -> None:
    """Verify that every script in scripts/ parses into a valid Python AST."""
    scripts = list(SCRIPTS_DIR.glob("*.py"))
    assert len(scripts) >= 30

    syntax_errors: list[tuple[str, str]] = []
    for script in scripts:
        source = script.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(script))
        except SyntaxError as e:
            syntax_errors.append((script.name, str(e)))

    assert not syntax_errors, f"Syntax errors detected in scripts: {syntax_errors}"


@pytest.mark.parametrize(
    "script_name",
    [
        "capture_openapi_snapshot.py",
        "locustfile.py",
        "export_compliance_report.py",
        "realtime_benchmark.py",
        "cfi_cli.py",
        "production_smoke_test.py",
        "generate_secrets.py",
        "run_load_test.py",
        "transaction_stream.py",
        "audit_api_contracts.py",
        "run_all_verifications.py",
        "run_fl_synthetic_benchmark.py",
        "run_mutation_tests.py",
    ],
)
def test_critical_scripts_cli_help(script_name: str) -> None:
    """Verify that critical utility and benchmark scripts execute --help without crashing."""
    script_path = SCRIPTS_DIR / script_name
    assert script_path.exists(), f"Expected script {script_name} does not exist"

    res = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"Script {script_name} --help failed with code {res.returncode}:\n{res.stderr}"
    assert "usage:" in res.stdout.lower() or "options:" in res.stdout.lower()


def test_locustfile_gevent_monkey_patch_guard() -> None:
    """Verify that locustfile.py includes gevent monkey patch guards for Python 3.12."""
    locustfile_path = SCRIPTS_DIR / "locustfile.py"
    content = locustfile_path.read_text(encoding="utf-8")
    assert "gevent.monkey" in content
    assert "patch_all" in content
    assert "__name__ == \"__main__\"" in content
