"""Shared fixtures and isolation for Scientific Verification Suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")


@pytest.fixture(autouse=True)
def _isolate_verification_audit_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure verification tests have an isolated, non-polluting ImmutableAuditChain."""
    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

    test_ledger = tmp_path / "verification_audit_ledger.json"
    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", str(test_ledger))
    ImmutableAuditChain.reset_instance()
    yield
    ImmutableAuditChain.reset_instance()
