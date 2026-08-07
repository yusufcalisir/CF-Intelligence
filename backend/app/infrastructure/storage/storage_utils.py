"""Centralized Storage Utility for Writable Directory Management across Environments."""

from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_cached_storage_dir: str | None = None


def get_storage_dir() -> str:
    """Returns a guaranteed-writable storage directory path.

    Resolves in order:
    1. CFI_STORAGE_DIR environment variable
    2. Default `<repo>/backend/storage` directory
    3. Fallback OS temp directory `tempfile.gettempdir()/cfi_storage` (for read-only containers like HF Spaces)
    """
    global _cached_storage_dir
    if _cached_storage_dir is not None:
        return _cached_storage_dir

    env_dir = os.environ.get("CFI_STORAGE_DIR")
    if env_dir:
        target = os.path.abspath(env_dir)
        try:
            os.makedirs(target, exist_ok=True)
            _cached_storage_dir = target
            return target
        except OSError:
            pass

    default_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "storage",
        )
    )
    try:
        os.makedirs(default_dir, exist_ok=True)
        test_file = os.path.join(default_dir, ".write_test")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("probe")
        os.remove(test_file)
        _cached_storage_dir = default_dir
        return default_dir
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), "cfi_storage")
        os.makedirs(fallback, exist_ok=True)
        _cached_storage_dir = fallback
        logger.info(
            "Primary storage path (%s) not writable; using fallback: %s",
            default_dir,
            fallback,
        )
        return fallback
