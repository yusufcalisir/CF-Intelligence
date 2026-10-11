"""Embedded PostgreSQL server provider for test suites and development.

Wraps pg0-embedded to provide genuine PostgreSQL relational database instances
with psycopg 3.3.6 client connectivity for test fixtures.
"""

from __future__ import annotations

import importlib.util
import os
import sys

# Locate the installed pg0-embedded package in site-packages to avoid self-shadowing
_site_pg0_path = os.path.join(sys.prefix, "Lib", "site-packages", "pg0", "__init__.py")
if not os.path.exists(_site_pg0_path):
    for p in sys.path:
        candidate = os.path.join(p, "pg0", "__init__.py")
        if os.path.exists(candidate) and os.path.abspath(candidate) != os.path.abspath(__file__):
            _site_pg0_path = candidate
            break

_spec = importlib.util.spec_from_file_location("pg0_real", _site_pg0_path)
if _spec is not None and _spec.loader is not None:
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["pg0_real"] = _mod
    _spec.loader.exec_module(_mod)
    Pg0 = _mod.Pg0
    Pg0Error = _mod.Pg0Error
    Pg0NotFoundError = _mod.Pg0NotFoundError
    Pg0NotRunningError = _mod.Pg0NotRunningError
    Pg0AlreadyRunningError = _mod.Pg0AlreadyRunningError
else:
    raise ImportError(f"Could not load pg0 package from {_site_pg0_path}")

__all__ = [
    "Pg0",
    "Pg0Error",
    "Pg0NotFoundError",
    "Pg0NotRunningError",
    "Pg0AlreadyRunningError",
]

