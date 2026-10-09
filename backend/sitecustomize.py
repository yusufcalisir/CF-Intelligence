"""Environment and runtime customization for backend services and Ray workers."""

from __future__ import annotations

import os
import sys

# Ensure OpenMP and Ray accelerator settings are established across all processes
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("RAY_ACCEL_ENV_VAR_OVERRIDE_ON_ZERO", "0")

if sys.platform == "win32":
    # On Windows, Raylet's process termination signals (TerminateProcess / pipe close)
    # cause Python's faulthandler in exiting workers to dump benign disconnection tracebacks.
    # We disable faulthandler in worker/background processes to ensure clean terminal observability.
    import faulthandler

    try:
        faulthandler.disable()
        # Suppress re-enabling in worker threads/subprocesses
        faulthandler.enable = lambda *args, **kwargs: None
    except Exception:
        pass
