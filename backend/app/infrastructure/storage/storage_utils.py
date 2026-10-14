"""Centralized Storage Utility for Writable Directory Management across Environments."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_cached_storage_dir: str | None = None


def _is_production_environment() -> bool:
    """Determine whether the running process is in production mode."""
    app_env = os.environ.get("APP_ENV", "").lower().strip()
    env = os.environ.get("ENVIRONMENT", "").lower().strip()
    if app_env == "production" or env == "production":
        return True
    try:
        from app.config import get_settings

        return getattr(get_settings(), "app_env", "").lower().strip() == "production"
    except Exception:
        return False


def get_storage_dir() -> str:
    """Returns a guaranteed-writable storage directory path.

    In production mode:
    - Enforces least privilege (0o700 directory permissions on POSIX).
    - Prohibits silent fallback to ephemeral /tmp or tempfile directories.
    - Fails closed with PermissionError if configured/authoritative storage is not writable.

    In development/testing mode:
    - Resolves project storage candidates, falling back safely to local temp storage with 0o700.
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

    is_prod = _is_production_environment()
    candidates: list[str] = []

    # 1. Explicit env var
    env_dir = os.environ.get("CFI_STORAGE_DIR")
    if env_dir:
        target_env = os.path.abspath(env_dir)
        try:
            os.makedirs(target_env, exist_ok=True)
            if os.name != "nt":
                with contextlib.suppress(OSError):
                    os.chmod(target_env, 0o700)
            probe = os.path.join(target_env, f".write_probe_{os.getpid()}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("probe")
            os.remove(probe)
            _cached_storage_dir = target_env
            return target_env
        except OSError as exc:
            if is_prod:
                raise PermissionError(
                    f"Configured production storage directory '{env_dir}' is not writable. "
                    "Ephemeral fallback is prohibited in production."
                ) from exc
            logger.warning("CFI_STORAGE_DIR '%s' not writable; falling back.", env_dir)

    # 2. Existing project storage directories (prefer ones already containing datasets)
    here = os.path.abspath(__file__)
    curr = here
    project_candidates: list[str] = []
    for _ in range(5):
        curr = os.path.dirname(curr)
        project_candidates.append(os.path.join(curr, "backend", "storage"))
        project_candidates.append(os.path.join(curr, "storage"))

    for pc in project_candidates:
        if os.path.isdir(os.path.join(pc, "datasets")) and pc not in candidates:
            candidates.append(pc)

    for pc in project_candidates:
        if os.path.isdir(pc) and pc not in candidates:
            candidates.append(pc)

    # 3. Container standard storage paths (POSIX / container environment only)
    if os.name != "nt":
        candidates.append("/app/storage")
        if not is_prod:
            candidates.append("/tmp/cfi_storage")  # nosec B108

    # 4. Fallback project root candidates
    for pc in project_candidates:
        if pc not in candidates:
            candidates.append(pc)

    # 5. OS temp directories (strictly prohibited in production)
    if not is_prod:
        candidates.append(os.path.join(tempfile.gettempdir(), "cfi_storage"))
        candidates.append(os.path.join(tempfile.gettempdir(), "storage"))

    for target in candidates:
        try:
            os.makedirs(target, exist_ok=True)
            if os.name != "nt":
                with contextlib.suppress(OSError):
                    os.chmod(target, 0o700)
            probe = os.path.join(target, f".write_probe_{os.getpid()}")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("probe")
            os.remove(probe)
            _cached_storage_dir = target
            return target
        except OSError:
            continue

    if is_prod:
        raise PermissionError(
            "Authoritative production storage paths are unavailable or not writable. "
            "Ephemeral temporary directory fallback is prohibited in production."
        )

    # 6. Emergency unique temp directory (development/testing only)
    emergency = tempfile.mkdtemp(prefix="cfi_storage_")
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(emergency, 0o700)
    _cached_storage_dir = emergency
    logger.warning("Fallback storage path not writable; created unique temp directory: %s", emergency)
    return emergency
