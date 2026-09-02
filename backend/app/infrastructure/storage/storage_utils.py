"""Centralized Storage Utility for Writable Directory Management across Environments."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_cached_storage_dir: str | None = None


def get_storage_dir() -> str:
    """Returns a guaranteed-writable storage directory path.

    Resolves in order:
    1. CFI_STORAGE_DIR environment variable
    2. Container `/app/storage` directory
    3. Project root storage directories
    4. Fallback OS temp directory `tempfile.gettempdir()/cfi_storage`
    5. Emergency unique temp directory
    """
    global _cached_storage_dir
    if _cached_storage_dir is not None and os.path.isdir(_cached_storage_dir):
        try:
            probe = os.path.join(_cached_storage_dir, f".write_probe_{os.getpid()}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("probe")
            os.remove(probe)
            return _cached_storage_dir
        except OSError:
            _cached_storage_dir = None

    candidates: list[str] = []

    # 1. Explicit env var
    env_dir = os.environ.get("CFI_STORAGE_DIR")
    if env_dir:
        candidates.append(os.path.abspath(env_dir))

    # 2. Container standard storage paths
    candidates.append("/app/storage")
    candidates.append("/tmp/cfi_storage")  # nosec B108

    # 3. Project root directory candidates
    here = os.path.abspath(__file__)
    curr = here
    for _ in range(5):
        curr = os.path.dirname(curr)
        candidates.append(os.path.join(curr, "storage"))
        candidates.append(os.path.join(curr, "backend", "storage"))

    # 4. OS temp directories
    candidates.append(os.path.join(tempfile.gettempdir(), "cfi_storage"))
    candidates.append(os.path.join(tempfile.gettempdir(), "storage"))

    for target in candidates:
        try:
            os.makedirs(target, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(target, 0o777)  # nosec B103
            probe = os.path.join(target, f".write_probe_{os.getpid()}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("probe")
            os.remove(probe)
            _cached_storage_dir = target
            return target
        except OSError:
            continue

    # 5. Emergency unique temp directory
    emergency = tempfile.mkdtemp(prefix="cfi_storage_")
    with contextlib.suppress(OSError):
        os.chmod(emergency, 0o777)  # nosec B103
    _cached_storage_dir = emergency
    logger.warning("Fallback storage path not writable; created unique temp directory: %s", emergency)
    return emergency
