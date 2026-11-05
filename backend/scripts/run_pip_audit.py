#!/usr/bin/env python3
"""Compatibility entrypoint forwarding to authoritative scripts.run_pip_audit."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_pip_audit import (  # noqa: E402
    ClassificationResult,
    build_audit_cmd,
    classify_pip_audit_result,
    execute_audit_command,
    main,
    run_audit_pipeline,
)

__all__ = [
    "ClassificationResult",
    "build_audit_cmd",
    "classify_pip_audit_result",
    "execute_audit_command",
    "main",
    "run_audit_pipeline",
]

if __name__ == "__main__":
    sys.exit(main())
