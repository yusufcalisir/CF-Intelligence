"""Cryptographic Password Hashing and Verification Module.

Enforces bcrypt password hashing (work factor / cost = 12) with salted digests.
Rejects plain-text, MD5, and SHA-1 storage.
"""

from __future__ import annotations

import logging
import secrets
from typing import Final

import bcrypt

logger = logging.getLogger(__name__)

# Default bcrypt cost factor (2^12 = 4096 iterations)
DEFAULT_BCRYPT_ROUNDS: Final[int] = 12


def hash_password(plain_password: str, rounds: int = DEFAULT_BCRYPT_ROUNDS) -> str:
    """Hash a plain text password using bcrypt with random per-password salt.

    Args:
        plain_password: The raw password string.
        rounds: The bcrypt cost factor (default: 12).

    Returns:
        The encoded modular crypt format string (e.g., "$2b$12$...").
    """
    if not plain_password or not isinstance(plain_password, str):
        raise ValueError("Password must be a non-empty string.")

    # Convert string to UTF-8 bytes for bcrypt
    password_bytes = plain_password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=rounds)
    hashed_bytes = bcrypt.hashpw(password_bytes, salt)
    return hashed_bytes.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a stored bcrypt hash.

    Args:
        plain_password: The plaintext candidate password.
        hashed_password: The stored bcrypt hash string.

    Returns:
        True if the password matches the hash, False otherwise.
    """
    if not plain_password or not hashed_password:
        return False

    try:
        password_bytes = plain_password.encode("utf-8")
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as exc:
        logger.warning("Bcrypt password verification failed with error: %s", exc)
        return False


def is_bcrypt_hash(hash_candidate: str) -> bool:
    """Check if a string matches the standard bcrypt hash format ($2a$, $2b$, or $2y$)."""
    if not hash_candidate or not isinstance(hash_candidate, str):
        return False
    return (
        hash_candidate.startswith("$2a$")
        or hash_candidate.startswith("$2b$")
        or hash_candidate.startswith("$2y$")
    ) and len(hash_candidate) == 60


def generate_secure_token(length_bytes: int = 32) -> str:
    """Generate a cryptographically secure URL-safe random string for refresh tokens/secrets."""
    return secrets.token_urlsafe(length_bytes)
