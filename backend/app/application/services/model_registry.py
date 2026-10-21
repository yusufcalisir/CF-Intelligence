"""Model Registry service.

Handles versioned model persistence, registry manifest updates, promotions, and rollbacks.
Global models are saved per simulation run with metrics metadata.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import torch
from filelock import FileLock

from app.application.services.training_provenance_service import (
    DOMAIN_APPROVAL_SIGNOFF,
    TrainingExecutionReceipt,
    TrainingReceiptVerifier,
    derive_domain_key,
    get_default_training_verifier,
)
from app.domain.model_serving_errors import (
    ModelAuthorizationError,
    ModelCompatibilityError,
    ModelIntegrityError,
    ModelNotAvailableError,
)

logger = logging.getLogger(__name__)

SIMULATION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_simulation_id(simulation_id: str) -> str:
    """Validate and sanitize a simulation identifier at the service boundary.

    Enforces:
    - Non-empty string, length 1-128 characters.
    - Strictly alphanumeric characters, hyphens, and underscores.
    - Explicit rejection of traversal segments (..), path separators (/ and \\),
      null bytes, whitespace, and reserved filenames.
    """
    if not isinstance(simulation_id, str):
        raise ValueError("Simulation identifier must be a string.")
    clean = simulation_id.strip()
    if not clean or len(clean) > 128 or not SIMULATION_ID_PATTERN.match(clean):
        raise ValueError(
            f"Invalid simulation identifier: '{simulation_id}'. Must be 1-128 characters matching [a-zA-Z0-9_\\-]."
        )
    if clean in (".", ".."):
        raise ValueError(f"Unsafe simulation identifier: '{simulation_id}'.")
    return clean


_REGISTRY_FILE_LOCKS: dict[tuple[str, int], FileLock] = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()


def _get_registry_file_lock(sim_dir: str) -> FileLock:
    """Return shared FileLock instance for simulation dir to ensure process-local reentrancy."""
    lock_path = os.path.abspath(os.path.join(sim_dir, "registry.lock"))
    current_pid = os.getpid()
    cache_key = (lock_path, current_pid)
    with _REGISTRY_LOCKS_GUARD:
        lock = _REGISTRY_FILE_LOCKS.get(cache_key)
        if lock is None or getattr(lock, "_pid", None) not in (None, current_pid):
            lock = FileLock(lock_path, timeout=15)
            _REGISTRY_FILE_LOCKS[cache_key] = lock
        return lock


def compute_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def generate_signoff_signature(
    simulation_id: str,
    version: int,
    artifact_sha256: str,
    role: str,
    user: str,
    signing_secret: str | None = None,
) -> str:
    """Generate canonical HMAC-SHA256 signature binding simulation, version, artifact digest, role, and user."""
    from app.config import get_settings

    secret = signing_secret or get_settings().payload_signing_secret
    norm_role = "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"
    canonical_payload = f"{simulation_id}:{version}:{artifact_sha256}:{norm_role}:{user}"
    domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
    return hmac.new(domain_key, canonical_payload.encode("utf-8"), hashlib.sha256).hexdigest()


DOMAIN_INTERNAL_AUTHORITY = "cfi:internal_execution_context:v2"
DOMAIN_REPLAY_STORE_INTEGRITY = "cfi:replay_nonce_store:v1"
FORBIDDEN_INTERNAL_SECRETS = frozenset(
    {
        "",
        "cfi_internal_trusted_context_secret_key_2026_default",
        "cfi_dev_internal_trusted_context_secret_key_2026_development_only",
        "cfi_dev_replay_store_integrity_secret_2026_dev_only",
        "cfi_local_secret_key_2026_change_me_in_production",
        "change_me",
        "change-me",
        "secret",
        "default",
        "password",
        "test",
        "123456",
    }
)


def _resolve_internal_context_secret(is_issuing: bool = True) -> bytes:
    """Resolve and derive cryptographically separated internal authority signing key.

    In production mode, requires independently configured high-entropy secret
    (CFI_INTERNAL_CONTEXT_SECRET). Known defaults and placeholders fail closed.
    """
    from app.config import get_settings

    settings = get_settings()
    setting_app_env = getattr(settings, "app_env", "development").lower().strip()
    env_app_env = os.environ.get("APP_ENV", "").lower().strip()
    generic_env = os.environ.get("ENVIRONMENT", "").lower().strip()
    is_prod_env = (
        setting_app_env == "production"
        or env_app_env == "production"
        or generic_env == "production"
    )

    configured_secret = os.environ.get("CFI_INTERNAL_CONTEXT_SECRET") or getattr(
        settings, "internal_context_signing_secret", None
    )

    clean_secret = str(configured_secret).strip() if configured_secret else ""

    if is_prod_env and (
        not clean_secret or clean_secret in FORBIDDEN_INTERNAL_SECRETS or len(clean_secret) < 32
    ):
        action_desc = "issue" if is_issuing else "verify"
        raise PermissionError(
            f"Production internal authority rejected: Cannot {action_desc} internal authority context. "
            "CFI_INTERNAL_CONTEXT_SECRET must be an independently provisioned, high-entropy secret "
            "(minimum 32 characters, non-default)."
        )

    if not clean_secret or clean_secret in FORBIDDEN_INTERNAL_SECRETS:
        clean_secret = "cfi_dev_internal_trusted_context_secret_key_2026_development_only"

    return derive_domain_key(clean_secret, DOMAIN_INTERNAL_AUTHORITY)


@dataclass(frozen=True)
class TrustedInternalContext:
    """Cryptographically signed, resource-bound, and replay-protected internal execution authority context.

    Used exclusively for genuine internal, non-HTTP orchestration operations (such as automated
    canary evaluation rollbacks in ModelEvaluationEngine, training orchestrator ownership bindings,
    and consortium coordinator lifecycle operations). Cannot be spoofed or supplied by external
    HTTP callers without possession of the authoritative platform signing secret.
    """

    service_name: str
    action: str
    nonce: str
    created_at: float
    signature: str
    resource_type: str | None = None
    resource_id: str | None = None
    model_version: int | None = None
    bank_id: str | None = None
    target_bank_id: str | None = None
    ttl_seconds: float = 300.0


def _build_internal_context_payload(
    service_name: str,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    model_version: int | None,
    bank_id: str | None,
    target_bank_id: str | None,
    nonce: str,
    created_at: float,
    ttl_seconds: float,
) -> bytes:
    parts = [
        "INTERNAL_AUTHORITY_V2",
        service_name.strip(),
        action.strip(),
        (resource_type or "").strip(),
        (resource_id or "").strip(),
        str(model_version) if model_version is not None else "",
        (bank_id or "").strip(),
        (target_bank_id or "").strip(),
        f"{created_at:.4f}",
        f"{ttl_seconds:.1f}",
        nonce.strip(),
    ]
    return ":".join(parts).encode("utf-8")


def create_trusted_internal_context(
    service_name: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    model_version: int | None = None,
    bank_id: str | None = None,
    target_bank_id: str | None = None,
    created_at: float | None = None,
    ttl_seconds: float = 300.0,
    signing_secret: str | None = None,
) -> TrustedInternalContext:
    """Generate a signed, tamper-evident, scoped internal execution context for automated orchestrators."""
    from app.config import get_settings

    settings = get_settings()
    app_env = getattr(settings, "app_env", "development").lower()
    is_prod = app_env == "production"

    if signing_secret is not None:
        clean = signing_secret.strip()
        if is_prod and (clean in FORBIDDEN_INTERNAL_SECRETS or len(clean) < 32):
            raise PermissionError(
                "Production internal authority rejected: explicit signing_secret is missing, insecure, or using a forbidden default."
            )
        domain_key = derive_domain_key(clean, DOMAIN_INTERNAL_AUTHORITY)
    else:
        domain_key = _resolve_internal_context_secret(is_issuing=True)

    nonce = str(uuid.uuid4())
    now = time.time() if created_at is None else created_at
    payload = _build_internal_context_payload(
        service_name=service_name,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        model_version=model_version,
        bank_id=bank_id,
        target_bank_id=target_bank_id,
        nonce=nonce,
        created_at=now,
        ttl_seconds=ttl_seconds,
    )
    sig = hmac.new(domain_key, payload, hashlib.sha256).hexdigest()
    return TrustedInternalContext(
        service_name=service_name,
        action=action,
        nonce=nonce,
        created_at=now,
        signature=sig,
        resource_type=resource_type,
        resource_id=resource_id,
        model_version=model_version,
        bank_id=bank_id,
        target_bank_id=target_bank_id,
        ttl_seconds=ttl_seconds,
    )


def verify_trusted_internal_context(
    context: Any,
    expected_action: str,
    max_age_seconds: float = 300.0,
    expected_service_name: str | None = None,
    expected_resource_type: str | None = None,
    expected_resource_id: str | None = None,
    expected_model_version: int | None = None,
    expected_bank_id: str | None = None,
    expected_target_bank_id: str | None = None,
    consume_nonce: bool = False,
    nonce_store: Any | None = None,
) -> tuple[bool, str]:
    """Cryptographically verify an internal execution context.

    Enforces:
    1. Type authenticity (isinstance TrustedInternalContext).
    2. Authorized internal service name whitelist.
    3. Action match against expected_action.
    4. Time-boundary and TTL expiration.
    5. Production secret entropy and authenticity (rejects default/placeholder secrets).
    6. Cryptographic domain-separated HMAC signature verification.
    7. Resource and institutional scope binding (resource_type, resource_id, version, bank_id).
    8. Multi-process replay prevention via atomic nonce consumption (when consume_nonce=True).
    """
    if not isinstance(context, TrustedInternalContext):
        return False, "Invalid context type: expected TrustedInternalContext instance."

    ALLOWED_SERVICES = {
        "model_evaluation_engine",
        "training_orchestrator",
        "consortium_coordinator",
    }
    if context.service_name not in ALLOWED_SERVICES:
        return False, f"Unauthorized internal service name: '{context.service_name}'."

    if expected_service_name is not None and context.service_name != expected_service_name:
        return (
            False,
            f"Context service mismatch: expected '{expected_service_name}', got '{context.service_name}'.",
        )

    if context.action != expected_action:
        return (
            False,
            f"Context action mismatch: expected '{expected_action}', got '{context.action}'.",
        )

    now = time.time()
    effective_ttl = min(getattr(context, "ttl_seconds", 300.0), max_age_seconds)
    age = now - context.created_at
    if age < -10.0:
        return False, f"Internal authority context created in future (clock skew: {age:.1f}s)."
    if age > effective_ttl:
        return (
            False,
            f"Internal authority context expired or clock skew exceeded (age={age:.1f}s, ttl={effective_ttl:.1f}s).",
        )

    # Resolve domain key according to production rules
    try:
        domain_key = _resolve_internal_context_secret(is_issuing=False)
    except PermissionError as exc:
        return False, str(exc)

    # Compute expected V2 signature
    v2_payload = _build_internal_context_payload(
        service_name=context.service_name,
        action=context.action,
        resource_type=getattr(context, "resource_type", None),
        resource_id=getattr(context, "resource_id", None),
        model_version=getattr(context, "model_version", None),
        bank_id=getattr(context, "bank_id", None),
        target_bank_id=getattr(context, "target_bank_id", None),
        nonce=context.nonce,
        created_at=context.created_at,
        ttl_seconds=getattr(context, "ttl_seconds", 300.0),
    )
    expected_v2_sig = hmac.new(domain_key, v2_payload, hashlib.sha256).hexdigest()

    v1_matched = False
    # Backward compatibility with V1 signatures for inspection in non-production only
    if not hmac.compare_digest(context.signature, expected_v2_sig):
        from app.config import get_settings

        settings = get_settings()
        setting_app_env = getattr(settings, "app_env", "development").lower().strip()
        env_app_env = os.environ.get("APP_ENV", "").lower().strip()
        generic_env = os.environ.get("ENVIRONMENT", "").lower().strip()
        is_prod = (
            setting_app_env == "production"
            or env_app_env == "production"
            or generic_env == "production"
        )
        if is_prod:
            return (
                False,
                "Cryptographic signature mismatch: production mode strictly requires V2 domain-separated authority signatures.",
            )

        if consume_nonce:
            return (
                False,
                "Legacy V1 internal authority signature cannot authorize privileged state mutations; V2 scoped context required.",
            )

        secret = (
            getattr(settings, "payload_signing_secret", None)
            or getattr(settings, "oidc_jwt_signing_secret", None)
            or ""
        )
        if secret:
            v1_payload = f"INTERNAL_AUTHORITY:{context.service_name}:{context.action}:{context.nonce}:{context.created_at:.4f}".encode()
            expected_v1_sig = hmac.new(
                str(secret).encode("utf-8"), v1_payload, hashlib.sha256
            ).hexdigest()
            expected_v1_domain_sig = hmac.new(domain_key, v1_payload, hashlib.sha256).hexdigest()
            if hmac.compare_digest(context.signature, expected_v1_sig) or hmac.compare_digest(
                context.signature, expected_v1_domain_sig
            ):
                v1_matched = True

        if not v1_matched:
            return False, "Cryptographic signature mismatch on internal execution context."

    # Scope validation: Resource Type (mandatory if expected)
    ctx_res_type = getattr(context, "resource_type", None)
    if expected_resource_type is not None and (
        not ctx_res_type or ctx_res_type.strip() != expected_resource_type.strip()
    ):
        return (
            False,
            f"Context resource_type scope mismatch: expected '{expected_resource_type}', got '{ctx_res_type}'.",
        )

    # Scope validation: Resource ID (mandatory if expected)
    ctx_res_id = getattr(context, "resource_id", None)
    if expected_resource_id is not None and (
        not ctx_res_id or ctx_res_id.strip() != expected_resource_id.strip()
    ):
        return (
            False,
            f"Context resource_id scope mismatch: expected '{expected_resource_id}', got '{ctx_res_id}'.",
        )

    # Scope validation: Model Version (mandatory if expected)
    ctx_ver = getattr(context, "model_version", None)
    if expected_model_version is not None and (
        ctx_ver is None or ctx_ver != expected_model_version
    ):
        return (
            False,
            f"Context model_version scope mismatch: expected {expected_model_version}, got {ctx_ver}.",
        )

    # Scope validation: Institutional Bank ID (mandatory if expected)
    ctx_bank = getattr(context, "bank_id", None)
    if expected_bank_id is not None and (
        not ctx_bank or ctx_bank.strip().lower() != expected_bank_id.strip().lower()
    ):
        return (
            False,
            f"Context bank_id scope mismatch: expected '{expected_bank_id}', got '{ctx_bank}'.",
        )

    # Scope validation: Target Bank ID (mandatory if expected)
    ctx_target_bank = getattr(context, "target_bank_id", None)
    if expected_target_bank_id is not None and (
        not ctx_target_bank
        or ctx_target_bank.strip().lower() != expected_target_bank_id.strip().lower()
    ):
        return (
            False,
            f"Context target_bank_id scope mismatch: expected '{expected_target_bank_id}', got '{ctx_target_bank}'.",
        )

    # Replay protection: atomic nonce consumption
    if consume_nonce:
        from app.infrastructure.security.durable_nonce_store import get_default_nonce_store

        store = nonce_store or get_default_nonce_store()
        expires_at = context.created_at + getattr(context, "ttl_seconds", 300.0)
        metadata = {
            "service_name": context.service_name,
            "action": context.action,
            "resource_id": ctx_res_id,
            "model_version": ctx_ver,
            "bank_id": ctx_bank,
        }
        consumed_ok, reason = store.consume_nonce(
            context.nonce, expires_at=expires_at, metadata=metadata
        )
        if not consumed_ok:
            return False, reason

    return True, "Valid"


def _validate_training_evidence(
    training_run_id: str | None,
    training_evidence: dict[str, Any] | None,
) -> bool:
    """Validate whether genuine recorded training execution evidence is present.

    Requires:
    1. A valid, non-empty training run identifier.
    2. A structured training evidence dictionary containing verifiable numeric execution artifacts:
       - Finite numeric loss (or non-empty list of finite float losses),
       - Positive integer epochs or rounds (>= 1),
       - Positive integer batch_size or dataset sample count (> 0).
    Arbitrary strings, empty dicts, or unverified caller assertions do NOT qualify.
    """
    if not training_run_id or not isinstance(training_run_id, str) or not training_run_id.strip():
        return False
    if training_run_id.strip().lower() in ("unknown", "unspecified", "none"):
        return False
    if not isinstance(training_evidence, dict) or not training_evidence:
        return False

    has_loss = False
    if "loss" in training_evidence:
        val = training_evidence["loss"]
        if isinstance(val, (int, float)) and math.isfinite(val):
            has_loss = True
    elif "losses" in training_evidence:
        losses = training_evidence["losses"]
        if (
            isinstance(losses, (list, tuple))
            and len(losses) > 0
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in losses)
        ):
            has_loss = True

    has_progress = False
    for k in ("epochs", "epoch", "rounds", "round", "steps", "num_rounds"):
        if k in training_evidence:
            val = training_evidence[k]
            if isinstance(val, int) and val >= 1:
                has_progress = True
                break

    has_samples = False
    for k in (
        "samples",
        "sample_count",
        "batch_size",
        "dataset_samples",
        "num_samples",
        "participants",
    ):
        if k in training_evidence:
            val = training_evidence[k]
            if isinstance(val, int) and val > 0:
                has_samples = True
                break
            if isinstance(val, (list, tuple)) and len(val) > 0:
                has_samples = True
                break

    return has_loss and (has_progress or has_samples)


def _sync_dir(dir_path: str) -> None:
    """Flush and synchronize parent directory metadata to durable storage where supported.

    On POSIX systems, directory entries modified by atomic operations like os.replace()
    must be explicitly fsynced via their directory file descriptor to survive power loss or kernel panic.
    On Windows systems, directory handles do not support standard fsync; the operation
    is safely handled without error.
    """
    if os.name != "nt":
        try:
            fd = os.open(dir_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.debug("Directory fsync skipped or unsupported for '%s': %s", dir_path, exc)


def validate_production_admission(
    entry_or_provenance: dict[str, Any] | str,
    context: str = "production_admission",
) -> None:
    """Centralized production admission gate.

    In production (app_env == 'production'), strictly enforces:
    - UNVERIFIED, STRUCTURAL_FIXTURE, and LEGACY_UNVERIFIED cannot become operational champions.
    - Only models with verified/recorded training provenance and verified cryptographic receipts are eligible.
    - Models with is_production_eligible == False are rejected.
    - Revoked models are strictly rejected.
    - Artifacts missing integrity digest (sha256) are rejected.
    - Every production champion activation path requires two verified, distinct institutional sign-offs
      (Four-Eyes Principle: ml_engineer and compliance) signed by non-development accounts.
    """
    if isinstance(entry_or_provenance, dict) and (
        entry_or_provenance.get("is_revoked") is True
        or entry_or_provenance.get("status") == "revoked"
    ):
        raise ModelIntegrityError(
            f"Production admission rejected: model version has been revoked ({context})."
        )

    from app.config import get_settings

    settings = get_settings()
    if settings.app_env.lower() != "production":
        return

    # Enforce dual-approval contract on champion activation and serving entry points
    CHAMPION_CONTEXTS = (
        "save_version",
        "promote_version",
        "rollback",
        "resolve_and_verify_champion",
        "get_active_champion_identity",
        "champion_activation",
        "ModelService.get_champion",
    )

    prov: str
    is_eligible: bool | None = None
    sha256_val: str | None = None

    if isinstance(entry_or_provenance, dict):
        prov = str(entry_or_provenance.get("provenance_type", "UNVERIFIED")).strip().upper()
        is_eligible = entry_or_provenance.get("is_production_eligible")
        sha256_val = entry_or_provenance.get("sha256")
    else:
        prov = entry_or_provenance.strip().upper()

    status = entry_or_provenance.get("status", "") if isinstance(entry_or_provenance, dict) else ""
    target_role = (
        "champion"
        if (
            context in CHAMPION_CONTEXTS
            or "champion" in context
            or status in ("champion", "active")
        )
        else (
            "challenger" if ("challenger" in context or status == "challenger") else "operational"
        )
    )

    if prov == "STRUCTURAL_FIXTURE":
        raise ModelIntegrityError(
            f"Production admission rejected: Structural fixture cannot be promoted to {target_role} in production environment ({context}). "
            f"Structural test fixture cannot be promoted."
        )

    if prov in ("UNVERIFIED", "LEGACY_UNVERIFIED", "CALLER_REPORTED_UNVERIFIED"):
        raise ModelIntegrityError(
            f"Production admission rejected: model provenance '{prov}' is ineligible for "
            f"operational {target_role} serving in production environment ({context})."
        )

    if prov not in ("TRAINING_RECORDED", "TRAINING_EVIDENCE_VERIFIED"):
        raise ModelIntegrityError(
            f"Production admission rejected: model provenance '{prov}' is not production-eligible ({context})."
        )

    if is_eligible is False and context != "sign_off":
        raise ModelIntegrityError(
            f"Production admission rejected: model is explicitly marked as not production-eligible ({context})."
        )

    if isinstance(entry_or_provenance, dict):
        if sha256_val is not None and not sha256_val.strip():
            raise ModelIntegrityError(
                f"Production admission rejected: model artifact digest is missing or empty ({context})."
            )

        # Enforce verified cryptographic training receipt
        if not entry_or_provenance.get("training_receipt"):
            raise ModelIntegrityError(
                f"Production admission rejected: model lacks verified cryptographic training execution receipt ({context})."
            )

        if context in CHAMPION_CONTEXTS:
            sign_offs = entry_or_provenance.get("sign_offs", [])
            active_sign_offs = [
                s
                for s in sign_offs
                if isinstance(s, dict)
                and s.get("is_revoked") is not True
                and s.get("is_active", True) is not False
                and s.get("status", "active") != "revoked"
            ]
            if len(active_sign_offs) < 2:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: model requires two authorized institutional sign-offs ({context})."
                )

            ml_signs = [s for s in active_sign_offs if s.get("role") in ("ml_engineer", "ml_lead")]
            comp_signs = [
                s for s in active_sign_offs if s.get("role") in ("compliance", "compliance_officer")
            ]

            if not ml_signs:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: missing verified ML engineer sign-off ({context})."
                )
            if not comp_signs:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Missing required sign-offs: missing verified compliance sign-off ({context})."
                )

            ml_s = ml_signs[0]
            comp_s = comp_signs[0]

            # Four-Eyes Principle: distinct individuals (prevents alias, case, and token evasion)
            ml_user = (ml_s.get("user") or "").lower().strip()
            comp_user = (comp_s.get("user") or "").lower().strip()
            ml_uid = (ml_s.get("user_id") or "").strip()
            comp_uid = (comp_s.get("user_id") or "").strip()

            if ml_user == comp_user or (ml_uid and comp_uid and ml_uid == comp_uid):
                raise ModelIntegrityError(
                    f"Production promotion rejected: Four-Eyes Principle violation, user '{ml_s.get('user')}' cannot approve both roles ({context})."
                )

            secret = settings.payload_signing_secret
            if (
                not secret
                or not isinstance(secret, str)
                or not secret.strip()
                or len(secret.strip()) < 16
            ):
                raise ModelIntegrityError(
                    f"Production promotion rejected: authoritative signing configuration is missing or invalid ({context})."
                )

            sim_id = entry_or_provenance.get("simulation_id") or (
                entry_or_provenance.get("training_receipt") or {}
            ).get("simulation_id")
            if not sim_id and isinstance(sign_offs, list):
                for s in sign_offs:
                    if s.get("payload_bound"):
                        parts = s.get("payload_bound", "").split(":")
                        if len(parts) >= 5 and parts[0]:
                            sim_id = parts[0]
                            break
            if not sim_id:
                raise ModelIntegrityError(
                    f"Production promotion rejected: simulation identifier missing from model metadata ({context})."
                )

            version_val = entry_or_provenance.get("version")
            if version_val is None:
                raise ModelIntegrityError(
                    f"Production promotion rejected: version number missing from model metadata ({context})."
                )

            if not sha256_val or not sha256_val.strip():
                raise ModelIntegrityError(
                    f"Production promotion rejected: model artifact digest is missing or empty ({context})."
                )

            domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
            SEEDED_ACCOUNTS = {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}

            # Phase 1: Cryptographic verification of all sign-off payloads and HMAC signatures
            for s, expected_role in ((ml_s, "ml_engineer"), (comp_s, "compliance")):
                u = s.get("user", "")
                if not u or not isinstance(u, str) or not u.strip():
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off missing approver user ({context})."
                    )
                if u in SEEDED_ACCOUNTS:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: development seeded account '{u}' cannot authorize promotion ({context})."
                    )
                if not s.get("is_role_verified") or not s.get("is_identity_verified"):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off missing verified role authority ({context})."
                    )

                # Authoritative cryptographic HMAC validation
                canonical_payload = f"{sim_id}:{version_val}:{sha256_val}:{expected_role}:{u}"
                if s.get("payload_bound") and s.get("payload_bound") != canonical_payload:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off payload binding mismatch for user '{u}' ({context})."
                    )

                exp_sig_domain = hmac.new(
                    domain_key,
                    canonical_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                exp_sig_raw = hmac.new(
                    secret.encode("utf-8"),
                    canonical_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

                sig = s.get("signature") or ""
                if not sig or not (
                    hmac.compare_digest(sig, exp_sig_domain)
                    or hmac.compare_digest(sig, exp_sig_raw)
                ):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off for user '{u}' "
                        f"lacks valid cryptographic signature ({context})."
                    )

            # Phase 2: Live Authoritative RBAC check and cross-principal Four-Eyes validation
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()

            for s, expected_role in ((ml_s, "ml_engineer"), (comp_s, "compliance")):
                u = s.get("user", "")
                user_rec = auth_svc.get_user(u)
                if user_rec is not None:
                    if not user_rec.is_active:
                        raise ModelIntegrityError(
                            f"Production promotion rejected: approver '{u}' is deactivated ({context})."
                        )
                    user_roles = [r.lower().strip() for r in user_rec.roles]
                    if expected_role == "ml_engineer" and not any(
                        r in ("ml_engineer", "ml_lead") for r in user_roles
                    ):
                        raise ModelIntegrityError(
                            f"Production promotion rejected: user '{u}' lacks authoritative ml_engineer role ({context})."
                        )
                    if expected_role == "compliance" and not any(
                        r in ("compliance", "compliance_officer") for r in user_roles
                    ):
                        raise ModelIntegrityError(
                            f"Production promotion rejected: user '{u}' lacks authoritative compliance role ({context})."
                        )
                elif settings.app_env.lower() == "production":
                    raise ModelIntegrityError(
                        f"Production promotion rejected: approver principal '{u}' is not an active authoritative user ({context})."
                    )

            ml_rec = auth_svc.get_user(ml_user)
            comp_rec = auth_svc.get_user(comp_user)
            if ml_rec and comp_rec and ml_rec.user_id == comp_rec.user_id:
                raise ModelIntegrityError(
                    f"Production promotion rejected: Four-Eyes Principle violation, approvers resolve to same principal '{ml_rec.user_id}' ({context})."
                )


class ModelRegistry:
    """Manages versioned model artifacts and registry manifests.

    Models are stored in the filesystem under `storage/registry/{simulation_id}/`.
    A manifest file `registry.json` tracks versions, metrics, and active states.
    """

    _shared_manifest_cache: dict[str, tuple[float, float, list[dict[str, Any]]]] = {}
    _manifest_cache_guard: threading.Lock = threading.Lock()
    _shared_champion_sim_hint: str | None = None
    _shared_registry_root_mtime: float = 0.0
    _registry_generation: int = 0

    @classmethod
    def get_registry_generation(cls) -> int:
        with cls._manifest_cache_guard:
            return cls._registry_generation

    def __init__(
        self,
        storage_dir: str | None = None,
        verifier: TrainingReceiptVerifier | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._verifier = verifier
        if storage_dir is None:
            # 1. Prefer explicit env override (e.g. set in docker-compose / HF Space secrets)
            env_dir = os.environ.get("CFI_STORAGE_DIR")
            if env_dir:
                self.storage_dir = os.path.abspath(env_dir)
            else:
                # 2. Default: <repo>/backend/app/storage
                default_dir = os.path.abspath(
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "storage",
                    )
                )
                from app.config import get_settings

                settings = get_settings()
                app_env = getattr(settings, "app_env", "development").lower().strip()
                is_prod = (
                    app_env == "production"
                    or os.environ.get("APP_ENV", "").lower().strip() == "production"
                    or os.environ.get("ENVIRONMENT", "").lower().strip() == "production"
                )
                # 3. Fall back to OS temp dir if the default path is not writable (read-only containers in dev only)
                try:
                    os.makedirs(default_dir, exist_ok=True)
                    if os.name != "nt":
                        with contextlib.suppress(OSError):
                            os.chmod(default_dir, 0o700)
                    # quick writability probe
                    test_file = os.path.join(default_dir, ".write_test")
                    with open(test_file, "w") as _f:
                        pass
                    os.remove(test_file)
                    self.storage_dir = default_dir
                except OSError as exc:
                    if is_prod:
                        raise PermissionError(
                            f"Production storage directory '{default_dir}' is not writable. "
                            "Ephemeral fallback is prohibited in production."
                        ) from exc
                    self.storage_dir = os.path.join(tempfile.gettempdir(), "cfi_storage")
                    logger.info(
                        "Default storage path not writable; using fallback: %s",
                        self.storage_dir,
                    )
        else:
            self.storage_dir = os.path.abspath(storage_dir)

        self.registry_root = os.path.join(self.storage_dir, "registry")
        os.makedirs(self.registry_root, exist_ok=True)
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(self.registry_root, 0o700)

    @property
    def canonical_root(self) -> str:
        """Dynamically resolve canonical root to guarantee parity if registry_root changes."""
        return os.path.realpath(self.registry_root)

    def _get_sim_dir(self, simulation_id: str, create: bool = True) -> str:
        """Resolve canonical simulation directory contained within the registry root.

        Enforces:
        - Strict identifier pattern validation (no traversal, separators, or aliases).
        - Canonical containment check against realpath(self.registry_root).
        - Symlink target containment check (rejects links pointing outside registry root).
        - Prevent read-only callers from creating directories as an unintended side effect.
        """
        clean_id = validate_simulation_id(simulation_id)
        canonical_root = getattr(self, "canonical_root", None) or os.path.realpath(
            self.registry_root
        )
        sim_dir = os.path.abspath(os.path.join(self.registry_root, clean_id))

        # Check for unsafe symlinks pointing outside registry root
        if os.path.islink(sim_dir):
            target = os.path.realpath(sim_dir)
            if os.path.commonpath([target, canonical_root]) != canonical_root:
                raise ModelIntegrityError(
                    f"Simulation symlink '{clean_id}' targets external path '{target}' outside registry root."
                )
        else:
            # Enforce canonical path containment
            # Fast-path: clean_id is validated alphanumeric/hyphen/underscore, so abspath is guaranteed inside registry_root
            # Fall back to realpath only if sim_dir does not directly start with canonical_root
            if not sim_dir.startswith(canonical_root):
                real_sim_dir = os.path.realpath(sim_dir)
                try:
                    if (
                        os.path.commonpath([real_sim_dir, canonical_root]) != canonical_root
                        or real_sim_dir == canonical_root
                    ):
                        raise ModelIntegrityError(
                            f"Simulation directory '{clean_id}' escapes authoritative registry root."
                        )
                except ValueError as exc:
                    raise ModelIntegrityError(
                        f"Path containment check failed for simulation '{clean_id}': {exc}"
                    ) from exc

        if create:
            os.makedirs(sim_dir, exist_ok=True)
            if os.name != "nt":
                with contextlib.suppress(OSError):
                    os.chmod(sim_dir, 0o700)
        return sim_dir

    def _get_ownership_path(self, simulation_id: str, create: bool = False) -> str:
        return os.path.join(self._get_sim_dir(simulation_id, create=create), "ownership.json")

    def get_simulation_ownership(self, simulation_id: str) -> dict[str, Any]:
        """Retrieve authoritative institutional or consortium ownership record for a simulation."""
        try:
            clean_sim_id = validate_simulation_id(simulation_id)
        except ValueError:
            return {
                "simulation_id": simulation_id,
                "owning_bank_id": None,
                "consortium_id": None,
                "is_unbound": True,
            }
        sim_dir = self._get_sim_dir(clean_sim_id, create=False)
        if not os.path.isdir(sim_dir):
            return {
                "simulation_id": clean_sim_id,
                "owning_bank_id": None,
                "consortium_id": None,
                "is_unbound": True,
            }
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            own_path = self._get_ownership_path(clean_sim_id, create=False)
            if os.path.exists(own_path):
                try:
                    with open(own_path, encoding="utf-8") as f:
                        raw = f.read()
                        if not raw.strip():
                            raise ModelIntegrityError(
                                f"Ownership record for simulation '{clean_sim_id}' is empty (0 bytes)."
                            )
                        data = json.loads(raw)
                        if isinstance(data, dict):
                            owning_bank = data.get("owning_bank_id")
                            consortium = data.get("consortium_id")
                            is_unbound = not owning_bank and not consortium
                            return {
                                "simulation_id": simulation_id,
                                "owning_bank_id": owning_bank,
                                "consortium_id": consortium,
                                "bound_at": data.get("bound_at"),
                                "bound_by": data.get("bound_by"),
                                "is_unbound": is_unbound,
                                "last_mutation_id": data.get("last_mutation_id"),
                                "applied_mutation_ids": data.get("applied_mutation_ids", []),
                            }
                        raise ModelIntegrityError(
                            f"Ownership record for simulation '{clean_sim_id}' is corrupted: expected JSON dict, got {type(data).__name__}."
                        )
                except ModelIntegrityError:
                    raise
                except Exception as exc:
                    logger.error("Failed to load ownership record for %s: %s", simulation_id, exc)
                    raise ModelIntegrityError(
                        f"Ownership record for simulation '{clean_sim_id}' is corrupted or unreadable: {exc}"
                    ) from exc

            # Fallback probe to manifest entries
            manifest = self._load_manifest(simulation_id)
            banks_found: set[str] = set()
            consortia_found: set[str] = set()
            for e in manifest:
                b_val = e.get("owning_bank_id")
                if isinstance(b_val, str) and b_val.strip():
                    banks_found.add(b_val.strip())
                c_val = e.get("consortium_id")
                if isinstance(c_val, str) and c_val.strip():
                    consortia_found.add(c_val.strip())

            # Fallback probe to verified sign-offs or training receipt if entries lack explicit ownership tags
            if not banks_found and not consortia_found and manifest:
                has_signoff_users = any(
                    isinstance(s, dict) and bool(s.get("user"))
                    for e in manifest
                    for s in e.get("sign_offs", [])
                )
                if has_signoff_users:
                    from app.infrastructure.security.auth_service import AuthenticationService

                    auth_svc = AuthenticationService.get_instance()
                    for e in manifest:
                        for s in e.get("sign_offs", []):
                            if isinstance(s, dict):
                                s_user = s.get("user")
                                if s_user:
                                    u_rec = auth_svc.get_user(s_user)
                                    if u_rec and getattr(u_rec, "bank_id", None):
                                        banks_found.add(u_rec.bank_id.strip())
                for e in manifest:
                    rcpt = e.get("training_receipt")
                    if isinstance(rcpt, dict):
                        rcpt_b = rcpt.get("owning_bank_id")
                        if rcpt_b and isinstance(rcpt_b, str) and rcpt_b.strip():
                            banks_found.add(rcpt_b.strip())
                        rcpt_c = rcpt.get("consortium_id")
                        if rcpt_c and isinstance(rcpt_c, str) and rcpt_c.strip():
                            consortia_found.add(rcpt_c.strip())
            if len(banks_found) == 1 and len(consortia_found) <= 1:
                return {
                    "simulation_id": simulation_id,
                    "owning_bank_id": next(iter(banks_found)),
                    "consortium_id": next(iter(consortia_found), None),
                    "bound_at": None,
                    "bound_by": None,
                    "is_unbound": False,
                }
            if not banks_found and len(consortia_found) == 1:
                return {
                    "simulation_id": simulation_id,
                    "owning_bank_id": None,
                    "consortium_id": next(iter(consortia_found)),
                    "bound_at": None,
                    "bound_by": None,
                    "is_unbound": False,
                }

            # Unbound or ambiguous ownership -> fail-closed indicator
            return {
                "simulation_id": simulation_id,
                "owning_bank_id": None,
                "consortium_id": None,
                "is_unbound": True,
            }

    def _verify_initial_ownership_authority(
        self,
        simulation_id: str,
        clean_bank: str | None,
        clean_consortium: str | None,
        auth_token: str | None = None,
        trusted_context: TrustedInternalContext | None = None,
        training_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
    ) -> tuple[bool, str]:
        """Verify that genuine institutional authority exists to initially bind ownership.

        Returns:
            (has_authority, effective_bound_by_or_reason)
        """
        # 1. Trusted Internal Context (e.g. training orchestrator, consortium coordinator)
        if trusted_context is not None:
            action = getattr(trusted_context, "action", "")
            if action not in ("bind_ownership", "register_model", "save_version"):
                return (
                    False,
                    f"invalid_trusted_context: Action '{action}' is not permitted for initial ownership binding.",
                )
            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action=action,
                expected_resource_type="model",
                expected_resource_id=simulation_id,
                expected_bank_id=clean_bank,
                consume_nonce=True,
            )
            if is_valid_ctx:
                return True, f"internal:{trusted_context.service_name}"
            return False, f"invalid_trusted_context: {ctx_reason}"

        # 2. Authenticated Principal Identity (DurableAccountStore Bearer JWT)
        if auth_token is not None:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                return False, f"invalid_token: {err}"

            uname = (claims.get("username") or claims.get("sub") or "").strip()
            user_rec = auth_svc.get_user(uname)
            if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
                return False, f"inactive_user: {uname}"

            user_bank = (getattr(user_rec, "bank_id", "") or "").lower().strip()
            user_roles = {r.lower().strip() for r in getattr(user_rec, "roles", [])}
            allowed_roles = {"admin", "ml_lead", "ml_engineer"}
            if not (user_roles & allowed_roles):
                return (
                    False,
                    f"insufficient_role: user '{uname}' lacks registration role ({user_roles})",
                )

            if clean_bank and clean_consortium:
                if user_bank != clean_bank.lower():
                    return (
                        False,
                        f"bank_mismatch: user '{uname}' belongs to '{user_bank}', cannot register for '{clean_bank}'",
                    )

                from app.application.services.consortium_service import (
                    consortium_governance_service,
                )

                c = consortium_governance_service.get_consortium(clean_consortium)
                if not c:
                    return False, f"unknown_consortium: '{clean_consortium}'"
                member_banks = [m.lower().strip() for m in c.members]
                if user_bank not in member_banks:
                    return (
                        False,
                        f"consortium_membership_required: user bank '{user_bank}' not in consortium '{clean_consortium}'",
                    )
                return True, f"user:{uname} ({clean_bank}, consortium:{clean_consortium})"
            elif clean_bank:
                if user_bank != clean_bank.lower():
                    return (
                        False,
                        f"bank_mismatch: user '{uname}' belongs to '{user_bank}', cannot register for '{clean_bank}'",
                    )
                return True, f"user:{uname} ({clean_bank})"
            elif clean_consortium:
                from app.application.services.consortium_service import (
                    consortium_governance_service,
                )

                c = consortium_governance_service.get_consortium(clean_consortium)
                if not c:
                    return False, f"unknown_consortium: '{clean_consortium}'"
                member_banks = [m.lower().strip() for m in c.members]
                if user_bank not in member_banks:
                    return (
                        False,
                        f"consortium_membership_required: user bank '{user_bank}' not in consortium '{clean_consortium}'",
                    )
                return True, f"consortium_member:{uname} ({user_bank})"

        # 3. Cryptographically Verified Training Receipt with Signed Institutional Claims
        if training_receipt is not None:
            receipt_obj: TrainingExecutionReceipt | None = None
            if isinstance(training_receipt, TrainingExecutionReceipt):
                receipt_obj = training_receipt
            elif isinstance(training_receipt, dict):
                try:
                    receipt_obj = TrainingExecutionReceipt.from_dict(training_receipt)
                except Exception as exc:
                    return False, f"invalid_training_receipt_format: {exc}"
            else:
                return False, f"invalid_training_receipt_type: {type(training_receipt)}"

            if receipt_obj is not None:
                verifier = self._verifier or get_default_training_verifier()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_obj,
                    simulation_id=simulation_id,
                )
                if is_valid_rcpt:
                    # ONLY read verified claims from the reconstructed TrainingExecutionReceipt object.
                    # Fields not covered by the cryptographic signature (e.g. unauthenticated 'bank_id')
                    # cannot establish institutional authority.
                    clean_rcpt_bank = (
                        receipt_obj.owning_bank_id.strip().lower()
                        if receipt_obj.owning_bank_id
                        else ""
                    )
                    clean_rcpt_consortium = (
                        receipt_obj.consortium_id.strip() if receipt_obj.consortium_id else ""
                    )
                    rcpt_id = receipt_obj.receipt_id or "verified_receipt"

                    if clean_bank and clean_consortium:
                        bank_ok = clean_rcpt_bank and clean_rcpt_bank == clean_bank.lower()
                        cons_ok = (
                            clean_rcpt_consortium and clean_rcpt_consortium == clean_consortium
                        )
                        if bank_ok and cons_ok:
                            return (
                                True,
                                f"receipt:{rcpt_id} ({clean_bank}, consortium:{clean_consortium})",
                            )
                        return False, (
                            f"receipt_claim_mismatch: receipt binds bank '{clean_rcpt_bank or 'none'}' and "
                            f"consortium '{clean_rcpt_consortium or 'none'}', cannot authenticate claimed '{clean_bank}' / '{clean_consortium}'"
                        )
                    elif clean_bank:
                        if clean_rcpt_bank and clean_rcpt_bank == clean_bank.lower():
                            return True, f"receipt:{rcpt_id} ({clean_bank})"
                        return False, (
                            f"receipt_claim_mismatch: receipt binds bank '{clean_rcpt_bank or 'none'}', "
                            f"cannot authenticate claimed bank '{clean_bank}'"
                        )
                    elif clean_consortium:
                        if clean_rcpt_consortium and clean_rcpt_consortium == clean_consortium:
                            return True, f"receipt:{rcpt_id} (consortium:{clean_consortium})"
                        return False, (
                            f"receipt_claim_mismatch: receipt binds consortium '{clean_rcpt_consortium or 'none'}', "
                            f"cannot authenticate claimed consortium '{clean_consortium}'"
                        )
                else:
                    return False, f"invalid_training_receipt: {rcpt_reason}"

        return (
            False,
            "no verifiable institutional authority provided (auth_token, trusted_context, or authenticating receipt)",
        )

    def bind_simulation_ownership(
        self,
        simulation_id: str,
        owning_bank_id: str | None = None,
        consortium_id: str | None = None,
        bound_by: str | None = None,
        auth_token: str | None = None,
        trusted_context: TrustedInternalContext | None = None,
        training_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Authoritatively bind simulation model artifacts to an owning bank or consortium."""
        bank_arg = owning_bank_id or kwargs.get("bank_id")
        clean_bank = bank_arg.strip() if isinstance(bank_arg, str) and bank_arg.strip() else None
        clean_consortium = (
            consortium_id.strip()
            if isinstance(consortium_id, str) and consortium_id.strip()
            else None
        )

        if not clean_bank and not clean_consortium:
            raise ValueError(
                "bind_simulation_ownership requires non-empty owning_bank_id or consortium_id."
            )

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            existing = self.get_simulation_ownership(simulation_id)
            is_already_bound = not existing.get("is_unbound", True) and (
                existing.get("owning_bank_id") or existing.get("consortium_id")
            )

            if is_already_bound:
                curr_bank = existing.get("owning_bank_id")
                curr_consortium = existing.get("consortium_id")
                if clean_bank == curr_bank and clean_consortium == curr_consortium:
                    return existing

                # Established ownership rebinding requires authenticated transfer authority
                has_transfer_authority = False
                effective_bound_by = bound_by or "ownership_transfer"
                if trusted_context is not None:
                    is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                        trusted_context,
                        expected_action="transfer_ownership",
                        expected_resource_type="model",
                        expected_resource_id=simulation_id,
                        expected_bank_id=curr_bank,
                        expected_target_bank_id=clean_bank,
                        consume_nonce=True,
                    )
                    if is_valid_ctx:
                        has_transfer_authority = True
                        effective_bound_by = f"internal:{trusted_context.service_name}"
                elif auth_token is not None:
                    from app.infrastructure.security.auth_service import AuthenticationService

                    auth_svc = AuthenticationService.get_instance()
                    is_valid, claims, _ = auth_svc.verify_access_token(auth_token)
                    if is_valid and claims:
                        uname = (claims.get("username") or claims.get("sub") or "").strip()
                        user_rec = auth_svc.get_user(uname)
                        if user_rec and user_rec.is_active and user_rec.status.lower() == "active":
                            user_bank = (getattr(user_rec, "bank_id", "") or "").lower().strip()
                            user_roles = {r.lower().strip() for r in getattr(user_rec, "roles", [])}
                            if (
                                curr_bank
                                and user_bank == curr_bank.lower()
                                and (user_roles & {"admin", "ml_lead"})
                            ):
                                has_transfer_authority = True
                                effective_bound_by = f"user:{uname} ({curr_bank})"

                if not has_transfer_authority:
                    raise ModelAuthorizationError(
                        f"Unauthorized model ownership rebinding: Model '{simulation_id}' is authoritatively owned by "
                        f"'{curr_bank or curr_consortium}'. Rebinding to '{clean_bank or clean_consortium}' rejected without authenticated transfer authority."
                    )
            else:
                # First-time ownership binding requires verifiable institutional evidence
                has_init_authority, auth_bound_by = self._verify_initial_ownership_authority(
                    simulation_id=simulation_id,
                    clean_bank=clean_bank,
                    clean_consortium=clean_consortium,
                    auth_token=auth_token,
                    trusted_context=trusted_context,
                    training_receipt=training_receipt,
                )

                if not has_init_authority:
                    raise ModelAuthorizationError(
                        f"Initial institutional ownership binding for model '{simulation_id}' rejected: "
                        f"Requires verifiable institutional authority ({auth_bound_by})."
                    )
                effective_bound_by = auth_bound_by

            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            audit_chain = ImmutableAuditChain.get_instance()
            is_transfer = is_already_bound
            mutation_id = f"mut_own_{uuid.uuid4().hex}"
            intent_event_type = (
                "MODEL_OWNERSHIP_TRANSFER_PENDING"
                if is_transfer
                else "MODEL_OWNERSHIP_ASSIGNMENT_PENDING"
            )
            intent_action = "transfer_ownership" if is_transfer else "bind_simulation_ownership"
            terminal_event_type = (
                "MODEL_OWNERSHIP_TRANSFER" if is_transfer else "MODEL_OWNERSHIP_ASSIGNMENT"
            )
            terminal_failed_event_type = (
                "MODEL_OWNERSHIP_TRANSFER_FAILED"
                if is_transfer
                else "MODEL_OWNERSHIP_ASSIGNMENT_FAILED"
            )
            decision = "OWNERSHIP_TRANSFERRED" if is_transfer else "OWNERSHIP_BOUND"

            intent_entry = audit_chain.append_event(
                event_type=intent_event_type,
                actor=effective_bound_by or "system",
                target_id=f"model:{simulation_id}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "owning_bank_id": clean_bank,
                    "consortium_id": clean_consortium,
                    "previous_bank_id": existing.get("owning_bank_id"),
                    "previous_consortium_id": existing.get("consortium_id"),
                    "bound_by": effective_bound_by,
                    "action": intent_action,
                    "decision": "PENDING",
                },
                actor_type="internal_service"
                if (effective_bound_by or "").startswith("internal:")
                else "human_principal",
                actor_institution=clean_bank or "unknown",
                target_type="model",
                action=intent_action,
                decision="PENDING",
            )

            prev_applied = existing.get("applied_mutation_ids", [])
            if not isinstance(prev_applied, list):
                prev_applied = []
            applied_ids = list(prev_applied)
            if mutation_id not in applied_ids:
                applied_ids.append(mutation_id)

            record = {
                "simulation_id": simulation_id,
                "owning_bank_id": clean_bank,
                "consortium_id": clean_consortium,
                "bound_at": datetime.now(UTC).isoformat(),
                "bound_by": effective_bound_by,
                "is_unbound": False,
                "last_mutation_id": mutation_id,
                "applied_mutation_ids": applied_ids,
            }

            try:
                self._save_ownership(simulation_id, record)
            except Exception as save_err:
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type=terminal_failed_event_type,
                    actor=effective_bound_by or "system",
                    target_id=f"model:{simulation_id}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "FAILED",
                        "mutation_committed": False,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "error": str(save_err),
                        "action": intent_action,
                        "decision": "FAILED",
                    },
                    actor_type="internal_service"
                    if (effective_bound_by or "").startswith("internal:")
                    else "human_principal",
                    actor_institution=clean_bank or "unknown",
                    target_type="model",
                    action=intent_action,
                    decision="FAILED",
                )
                raise

            # Update existing manifest entries if present (derived state)
            try:
                manifest = self._load_manifest(simulation_id)
                if manifest:
                    updated = False
                    for entry in manifest:
                        if (
                            entry.get("owning_bank_id") != clean_bank
                            or entry.get("consortium_id") != clean_consortium
                        ):
                            entry["owning_bank_id"] = clean_bank
                            entry["consortium_id"] = clean_consortium
                            updated = True
                    if updated:
                        self._save_manifest(simulation_id, manifest)
            except Exception as manifest_err:
                logger.warning(
                    "Ownership for '%s' committed to ownership.json, but derived manifest update failed: %s. "
                    "Derived state will be repaired automatically by reconciliation or subsequent manifest reads.",
                    simulation_id,
                    manifest_err,
                )

            audit_chain.finalize_pending_mutation(
                mutation_id=mutation_id,
                event_type=terminal_event_type,
                actor=effective_bound_by or "system",
                target_id=f"model:{simulation_id}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "COMMITTED",
                    "mutation_committed": True,
                    "intent_event_id": intent_entry.event_id,
                    "simulation_id": simulation_id,
                    "owning_bank_id": clean_bank,
                    "consortium_id": clean_consortium,
                    "bound_by": effective_bound_by,
                    "action": intent_action,
                    "decision": decision,
                },
                actor_type="internal_service"
                if (effective_bound_by or "").startswith("internal:")
                else "human_principal",
                actor_institution=clean_bank or "unknown",
                target_type="model",
                action=intent_action,
                decision=decision,
            )

            return record

    def _verify_institutional_governance_authority(
        self,
        simulation_id: str,
        user_rec: Any,
        action_name: str,
    ) -> None:
        """Verify that the user's authoritative institution owns or is authorized to govern the simulation model."""
        if user_rec is None:
            raise ModelAuthorizationError(
                f"Action '{action_name}' rejected: no authoritative identity provided."
            )

        actor_bank = getattr(user_rec, "bank_id", None)
        if not actor_bank or not str(actor_bank).strip():
            raise ModelAuthorizationError(
                f"Institutional authorization rejected: Principal '{user_rec.username}' "
                f"has no authoritative institution (bank_id) assignment in DurableAccountStore."
            )

        clean_actor_bank = str(actor_bank).lower().strip()
        ownership = self.get_simulation_ownership(simulation_id)

        # TENANT-09: Unbound or ambiguous model ownership fails closed
        if ownership.get("is_unbound") or (
            not ownership.get("owning_bank_id") and not ownership.get("consortium_id")
        ):
            raise ModelAuthorizationError(
                f"Institutional authorization rejected: Model '{simulation_id}' lacks authoritative institutional "
                f"ownership binding. Governance operation '{action_name}' rejected fail-closed."
            )

        owning_bank = (ownership.get("owning_bank_id") or "").lower().strip()
        consortium_id = (ownership.get("consortium_id") or "").strip()

        # Case 1: Directly owned by an institution
        if owning_bank:
            if clean_actor_bank == owning_bank:
                return  # Authorized: same-bank governance

            # Check if actor belongs to an explicitly authorized consortium that includes this model
            if consortium_id:
                from app.application.services.consortium_service import (
                    consortium_governance_service,
                )

                c = consortium_governance_service.get_consortium(consortium_id)
                if c and clean_actor_bank in c.members:
                    member = c.members[clean_actor_bank]
                    if member.can_vote:
                        return  # Authorized via active consortium policy

            # Cross-tenant mismatch -> REJECT (TENANT-01, TENANT-02)
            raise ModelAuthorizationError(
                f"Cross-bank authorization rejected: Principal '{user_rec.username}' from institution '{actor_bank}' "
                f"cannot execute '{action_name}' on model '{simulation_id}' owned by institution '{ownership.get('owning_bank_id')}'."
            )

        # Case 2: Consortium-owned model (owning_bank is None, consortium_id is present)
        if consortium_id:
            from app.application.services.consortium_service import consortium_governance_service

            c = consortium_governance_service.get_consortium(consortium_id)
            if not c:
                raise ModelAuthorizationError(
                    f"Consortium governance rejected: Consortium '{consortium_id}' bound to model '{simulation_id}' does not exist."
                )
            if clean_actor_bank not in c.members:
                raise ModelAuthorizationError(
                    f"Consortium governance rejected: Institution '{actor_bank}' is not a member of consortium '{consortium_id}'."
                )
            member = c.members[clean_actor_bank]
            if not member.can_vote:
                raise ModelAuthorizationError(
                    f"Consortium governance rejected: Institution '{actor_bank}' has non-voting role ({member.role.value}) in consortium '{consortium_id}'."
                )
            return

    def _load_manifest(self, simulation_id: str) -> list[dict[str, Any]]:
        try:
            clean_sim_id = validate_simulation_id(simulation_id)
        except ValueError:
            return []
        sim_dir = self._get_sim_dir(clean_sim_id, create=False)
        if not os.path.isdir(sim_dir):
            return []
        manifest_path = os.path.join(sim_dir, "registry.json")
        own_path = os.path.join(sim_dir, "ownership.json")

        # Fast path: check mtimes without acquiring reentrant file lock or reading files
        try:
            mtime_reg = os.path.getmtime(manifest_path)
            mtime_own = os.path.getmtime(own_path) if os.path.exists(own_path) else 0.0
            with ModelRegistry._manifest_cache_guard:
                if clean_sim_id in ModelRegistry._shared_manifest_cache:
                    cached_mtime_reg, cached_mtime_own, cached_data = (
                        ModelRegistry._shared_manifest_cache[clean_sim_id]
                    )
                    if cached_mtime_reg == mtime_reg and cached_mtime_own == mtime_own:
                        return copy.deepcopy(cached_data)
        except OSError:
            if not os.path.exists(manifest_path):
                return []

        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            if not os.path.exists(manifest_path):
                return []
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    content = f.read()
                if not content.strip():
                    raise ModelIntegrityError(
                        f"Registry manifest for simulation '{clean_sim_id}' is empty (0 bytes)."
                    )
                data = json.loads(content)
                if not isinstance(data, list):
                    raise ModelIntegrityError(
                        f"Registry manifest for simulation '{clean_sim_id}' is corrupted: expected JSON list, got {type(data).__name__}."
                    )

                for item in data:
                    if isinstance(item, dict) and "simulation_id" not in item:
                        item["simulation_id"] = clean_sim_id

                # Authoritative Ownership Synchronization:
                # ownership.json is the single authoritative source of truth for institutional ownership.
                # Synchronize derived manifest ownership fields if ownership.json exists and differs.
                own_path = self._get_ownership_path(clean_sim_id, create=False)
                if os.path.exists(own_path):
                    try:
                        with open(own_path, encoding="utf-8") as of:
                            raw_own = of.read()
                        if not raw_own.strip():
                            raise ModelIntegrityError(
                                f"Ownership record for simulation '{clean_sim_id}' is empty (0 bytes)."
                            )
                        own_data = json.loads(raw_own)
                        if not isinstance(own_data, dict):
                            raise ModelIntegrityError(
                                f"Ownership record for simulation '{clean_sim_id}' is corrupted: expected JSON dict, got {type(own_data).__name__}."
                            )
                        auth_bank = own_data.get("owning_bank_id")
                        auth_consortium = own_data.get("consortium_id")
                        manifest_changed = False
                        for item in data:
                            if isinstance(item, dict) and (
                                item.get("owning_bank_id") != auth_bank
                                or item.get("consortium_id") != auth_consortium
                            ):
                                item["owning_bank_id"] = auth_bank
                                item["consortium_id"] = auth_consortium
                                manifest_changed = True
                        if manifest_changed:
                            self._save_manifest(clean_sim_id, data)
                            logger.info(
                                "Synchronized and repaired manifest ownership for simulation '%s' to match authoritative ownership.json",
                                clean_sim_id,
                            )
                    except ModelIntegrityError:
                        raise
                    except Exception as sync_err:
                        logger.error(
                            "Authoritative ownership record for simulation '%s' is corrupted or unreadable: %s",
                            clean_sim_id,
                            sync_err,
                        )
                        raise ModelIntegrityError(
                            f"Authoritative ownership record for simulation '{clean_sim_id}' is corrupted or unreadable: {sync_err}"
                        ) from sync_err

                # Update shared manifest cache
                try:
                    mtime_reg = os.path.getmtime(manifest_path)
                    mtime_own = os.path.getmtime(own_path) if os.path.exists(own_path) else 0.0
                    with ModelRegistry._manifest_cache_guard:
                        ModelRegistry._shared_manifest_cache[clean_sim_id] = (
                            mtime_reg,
                            mtime_own,
                            copy.deepcopy(data),
                        )
                except OSError:
                    pass

                return data
            except ModelIntegrityError:
                raise
            except Exception as e:
                logger.error("Failed to load registry manifest for %s: %s", clean_sim_id, e)
                raise ModelIntegrityError(
                    f"Registry manifest for simulation '{clean_sim_id}' is corrupted or unreadable: {e}"
                ) from e

    def _save_ownership(self, simulation_id: str, record: dict[str, Any]) -> None:
        clean_sim_id = validate_simulation_id(simulation_id)
        sim_dir = self._get_sim_dir(clean_sim_id, create=True)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            own_path = self._get_ownership_path(clean_sim_id, create=True)
            tmp_own_path = os.path.join(
                sim_dir,
                f"ownership_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}_{uuid.uuid4().hex}.tmp",
            )
            try:
                with open(tmp_own_path, "w", encoding="utf-8") as f:
                    json.dump(record, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_own_path, own_path)
                _sync_dir(sim_dir)
                with ModelRegistry._manifest_cache_guard:
                    ModelRegistry._shared_manifest_cache.pop(clean_sim_id, None)
                    ModelRegistry._shared_champion_sim_hint = None
                    ModelRegistry._registry_generation += 1
            except Exception as e:
                logger.error(
                    "Authoritative ownership persistence failed for %s: %s", clean_sim_id, e
                )
                if os.path.exists(tmp_own_path):
                    with contextlib.suppress(OSError):
                        os.remove(tmp_own_path)
                raise ModelIntegrityError(
                    f"Authoritative ownership persistence failed for simulation '{clean_sim_id}': {e}"
                ) from e

    def _save_manifest(self, simulation_id: str, manifest: list[dict[str, Any]]) -> None:
        clean_sim_id = validate_simulation_id(simulation_id)
        sim_dir = self._get_sim_dir(clean_sim_id, create=True)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest_path = os.path.join(sim_dir, "registry.json")
            tmp_manifest_path = os.path.join(
                sim_dir,
                f"registry_{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}_{uuid.uuid4().hex}.tmp",
            )
            try:
                with open(tmp_manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_manifest_path, manifest_path)
                _sync_dir(sim_dir)
                with ModelRegistry._manifest_cache_guard:
                    ModelRegistry._shared_manifest_cache.pop(clean_sim_id, None)
                    ModelRegistry._shared_champion_sim_hint = None
                    ModelRegistry._registry_generation += 1
            except Exception as e:
                logger.error(
                    "Authoritative manifest persistence failed for %s: %s", simulation_id, e
                )
                if os.path.exists(tmp_manifest_path):
                    with contextlib.suppress(OSError):
                        os.remove(tmp_manifest_path)
                raise ModelIntegrityError(
                    f"Authoritative manifest persistence failed for simulation '{simulation_id}': {e}"
                ) from e

    def save_version(
        self,
        simulation_id: str,
        state_dict: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        is_promoted: bool = False,
        git_commit_hash: str | None = None,
        dataset_hash: str | None = None,
        dp_noise_profile: dict[str, Any] | None = None,
        status: str = "inactive",
        provenance_type: str = "UNVERIFIED",
        training_run_id: str | None = None,
        dataset_manifest_id: str | None = None,
        training_evidence: dict[str, Any] | None = None,
        training_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        artifact_bytes: bytes | None = None,
        provenance_receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        expected_architecture: str | None = None,
        expected_feature_schema_version: str | None = None,
        expected_input_dim: int | None = None,
        expected_feature_names: list[str] | tuple[str, ...] | None = None,
        model_contract: Any | None = None,
        owning_bank_id: str | None = None,
        consortium_id: str | None = None,
        auth_token: str | None = None,
        trusted_context: TrustedInternalContext | None = None,
        source_model_path: str | None = None,
        receipt: dict[str, Any] | TrainingExecutionReceipt | None = None,
        actor: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Save a new version of the global model weights. Defaults to inactive candidate.

        Args:
            simulation_id: ID of the active simulation run.
            state_dict: PyTorch model state dict.
            metrics: Dict of metrics (accuracy, f1_score, auc_roc, recall, etc.).
            is_promoted: Whether this version is promoted as active champion (defaults to False).
            git_commit_hash: Hash of the git commit.
            dataset_hash: Hash of the training dataset.
            dp_noise_profile: DP mechanism profile details.
            status: Initial status of the version.
            provenance_type: Provenance classification ('UNVERIFIED', 'STRUCTURAL_FIXTURE',
                             'TRAINING_RECORDED', 'TRAINING_EVIDENCE_VERIFIED', 'LEGACY_UNVERIFIED').
            training_run_id: Identifier of the training job.
            dataset_manifest_id: Identifier of the training dataset manifest.
            training_evidence: Execution evidence produced during training.
            training_receipt: Cryptographically signed training execution receipt from trusted authority.
            artifact_bytes: Raw bytes of the model artifact.
            provenance_receipt: Alias for training_receipt.
            expected_architecture: Expected model architecture name derived from candidate contract.
            expected_feature_schema_version: Expected feature schema version derived from candidate contract.
            expected_input_dim: Expected input dimension derived from candidate contract.
            expected_feature_names: Expected feature names list with exact ordering.
            model_contract: Optional candidate model contract object/dict providing expected schema.
            owning_bank_id: Bank identifier claiming authoritative ownership of the model.
            consortium_id: Consortium identifier claiming authoritative ownership of the model.
            auth_token: Bearer JWT token of the authenticated operator.
            trusted_context: Signed trusted internal context for internal pipeline execution.

        Returns:
            The created version entry metadata.
        """
        from app.config import get_settings

        settings = get_settings()

        if training_receipt is None and receipt is not None:
            training_receipt = receipt
        if metrics is None:
            metrics = {}
        source_bytes: bytes | None = None
        if source_model_path is not None:
            if not os.path.isfile(source_model_path):
                raise ModelIntegrityError(
                    f"Source model file '{source_model_path}' not found on disk."
                )
            with open(source_model_path, "rb") as sf:
                source_bytes = sf.read()
            if state_dict is None:
                try:
                    state_dict = torch.load(
                        source_model_path, map_location="cpu", weights_only=True
                    )
                except Exception as e:
                    raise ModelIntegrityError(
                        f"Failed to load state dict from {source_model_path}: {e}"
                    ) from e

        # Authoritative institutional or consortium ownership handling
        existing_ownership = self.get_simulation_ownership(simulation_id)
        is_already_bound = not existing_ownership.get("is_unbound", True) and (
            existing_ownership.get("owning_bank_id") or existing_ownership.get("consortium_id")
        )

        should_bind_initial = False
        if is_already_bound:
            est_bank = existing_ownership.get("owning_bank_id")
            est_consortium = existing_ownership.get("consortium_id")
            if owning_bank_id is not None:
                clean_req_bank = owning_bank_id.strip() if owning_bank_id else ""
                clean_est_bank = est_bank.strip() if est_bank else ""
                if clean_req_bank != clean_est_bank:
                    raise ModelAuthorizationError(
                        f"Cannot overwrite established model ownership: Model '{simulation_id}' is authoritatively owned by "
                        f"'{est_bank}'. Ordinary model saves cannot rebind ownership to '{owning_bank_id}'."
                    )
            if consortium_id is not None:
                clean_req_consortium = consortium_id.strip() if consortium_id else ""
                clean_est_consortium = est_consortium.strip() if est_consortium else ""
                if clean_req_consortium != clean_est_consortium:
                    raise ModelAuthorizationError(
                        f"Cannot overwrite established model ownership: Model '{simulation_id}' is authoritatively owned by consortium "
                        f"'{est_consortium}'. Ordinary model saves cannot rebind ownership to '{consortium_id}'."
                    )
            effective_owning_bank = est_bank
            effective_consortium = est_consortium
        else:
            if owning_bank_id is not None or consortium_id is not None:
                effective_owning_bank = (
                    owning_bank_id.strip() if owning_bank_id and owning_bank_id.strip() else None
                )
                effective_consortium = (
                    consortium_id.strip() if consortium_id and consortium_id.strip() else None
                )
                should_bind_initial = bool(effective_owning_bank or effective_consortium)
            else:
                effective_owning_bank = None
                effective_consortium = None

        # Derive expected contract parameters if model_contract provided
        exp_arch = expected_architecture
        exp_schema_ver = expected_feature_schema_version
        exp_dim = expected_input_dim
        exp_names = expected_feature_names

        if model_contract is not None:
            if isinstance(model_contract, dict):
                if exp_arch is None:
                    exp_arch = model_contract.get("architecture") or model_contract.get(
                        "expected_architecture"
                    )
                if exp_schema_ver is None:
                    exp_schema_ver = model_contract.get(
                        "feature_schema_version"
                    ) or model_contract.get("expected_feature_schema_version")
                if exp_dim is None:
                    exp_dim = model_contract.get("input_dim") or model_contract.get(
                        "expected_input_dim"
                    )
                if exp_names is None:
                    exp_names = model_contract.get("feature_names") or model_contract.get(
                        "expected_feature_names"
                    )
            else:
                if exp_arch is None:
                    exp_arch = getattr(model_contract, "architecture", None) or getattr(
                        model_contract, "expected_architecture", None
                    )
                if exp_schema_ver is None:
                    exp_schema_ver = getattr(
                        model_contract, "feature_schema_version", None
                    ) or getattr(model_contract, "expected_feature_schema_version", None)
                if exp_dim is None:
                    exp_dim = getattr(model_contract, "input_dim", None) or getattr(
                        model_contract, "expected_input_dim", None
                    )
                if exp_names is None:
                    exp_names = getattr(model_contract, "feature_names", None) or getattr(
                        model_contract, "expected_feature_names", None
                    )

        # Parse training receipt if provided
        receipt_dict: dict[str, Any] | None = None
        receipt_obj: TrainingExecutionReceipt | None = None
        has_valid_receipt = False
        active_receipt = training_receipt or provenance_receipt
        if active_receipt is not None:
            if isinstance(active_receipt, TrainingExecutionReceipt):
                receipt_obj = active_receipt
                receipt_dict = active_receipt.to_dict()
            elif isinstance(active_receipt, dict):
                receipt_dict = dict(active_receipt)
                try:
                    receipt_obj = TrainingExecutionReceipt.from_dict(receipt_dict)
                except Exception:
                    receipt_obj = None

        clean_prov = provenance_type.strip() if provenance_type else "UNVERIFIED"
        clean_upper = clean_prov.upper()

        has_recorded_evidence = _validate_training_evidence(training_run_id, training_evidence)

        attestation_status = "NONE"
        rcpt_consortium: str | None = None
        target_rcpt = receipt_obj if receipt_obj is not None else receipt_dict
        if target_rcpt is not None:
            verifier = self._verifier or get_default_training_verifier()
            initial_candidate_sha = (
                hashlib.sha256(artifact_bytes).hexdigest() if artifact_bytes is not None else None
            )
            is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                target_rcpt,
                candidate_artifact_sha256=initial_candidate_sha,
                simulation_id=simulation_id,
                expected_architecture=exp_arch,
                expected_feature_schema_version=exp_schema_ver,
                expected_input_dim=exp_dim,
                expected_feature_names=exp_names,
                expected_consortium_id=effective_consortium,
            )

            rcpt_consortium = None
            if receipt_obj is not None:
                rcpt_consortium = receipt_obj.consortium_id
            elif isinstance(receipt_dict, dict):
                rcpt_consortium = receipt_dict.get("consortium_id")
            if rcpt_consortium:
                rcpt_consortium = rcpt_consortium.strip()

            if effective_consortium is not None:
                if not rcpt_consortium:
                    raise ModelAuthorizationError(
                        f"Receipt rejected for model '{simulation_id}': Receipt omits required consortium claim "
                        f"for consortium-bound scope '{effective_consortium}'."
                    )
                if rcpt_consortium != effective_consortium:
                    raise ModelAuthorizationError(
                        f"Receipt rejected for model '{simulation_id}': Receipt binds consortium '{rcpt_consortium}', "
                        f"which does not match authorized scope '{effective_consortium}'."
                    )
                if not is_valid_rcpt:
                    raise ModelAuthorizationError(
                        f"Receipt rejected for model '{simulation_id}': Receipt verification failed: {rcpt_reason}"
                    )
            elif rcpt_consortium:
                raise ModelAuthorizationError(
                    f"Receipt rejected for model '{simulation_id}': Receipt binds consortium '{rcpt_consortium}', "
                    f"but model ownership scope is unbound or not configured for this consortium."
                )

            if is_valid_rcpt:
                has_valid_receipt = True
                final_provenance = "TRAINING_RECORDED"
                attestation_status = "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
                is_prod_eligible = True
            else:
                has_valid_receipt = False
                final_provenance = "UNVERIFIED"
                attestation_status = f"INVALID_RECEIPT: {rcpt_reason}"
                is_prod_eligible = False
        else:
            # Caller supplied metadata without a cryptographically verified receipt!
            # Numeric evidence or caller-selected labels must NEVER establish trusted production provenance!
            if clean_upper == "STRUCTURAL_FIXTURE":
                final_provenance = "STRUCTURAL_FIXTURE"
            elif clean_upper == "LEGACY_UNVERIFIED":
                final_provenance = "LEGACY_UNVERIFIED"
            else:
                final_provenance = "UNVERIFIED"

            attestation_status = (
                "CALLER_NUMERIC_EVIDENCE_UNAUTHENTICATED" if has_recorded_evidence else "NONE"
            )
            is_prod_eligible = False

        if is_promoted or status in ("champion", "active"):
            validate_production_admission(
                {
                    "provenance_type": final_provenance,
                    "status": "champion",
                    "is_active": True,
                    "training_receipt": receipt_dict if has_valid_receipt else None,
                    "sign_offs": [],
                    "is_production_eligible": is_prod_eligible,
                },
                context="save_version",
            )

        if should_bind_initial:
            has_init_auth, init_auth_reason = self._verify_initial_ownership_authority(
                simulation_id=simulation_id,
                clean_bank=effective_owning_bank,
                clean_consortium=effective_consortium,
                auth_token=auth_token,
                trusted_context=trusted_context,
                training_receipt=receipt_obj
                if (has_valid_receipt and receipt_obj is not None)
                else None,
            )
            if not has_init_auth:
                raise ModelAuthorizationError(
                    f"Initial institutional ownership binding for model '{simulation_id}' rejected: "
                    f"Requires verifiable institutional authority ({init_auth_reason})."
                )

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)

        with self._lock, file_lock:
            # Re-evaluate ownership under lock to eliminate initial binding races
            existing_ownership = self.get_simulation_ownership(simulation_id)
            is_already_bound = not existing_ownership.get("is_unbound", True) and (
                existing_ownership.get("owning_bank_id") or existing_ownership.get("consortium_id")
            )
            if is_already_bound:
                est_bank = existing_ownership.get("owning_bank_id")
                est_consortium = existing_ownership.get("consortium_id")
                if owning_bank_id is not None:
                    clean_req_bank = owning_bank_id.strip() if owning_bank_id else ""
                    clean_est_bank = est_bank.strip() if est_bank else ""
                    if clean_req_bank != clean_est_bank:
                        raise ModelAuthorizationError(
                            f"Cannot overwrite established model ownership: Model '{simulation_id}' is authoritatively owned by "
                            f"'{est_bank}'. Ordinary model saves cannot rebind ownership to '{owning_bank_id}'."
                        )
                if consortium_id is not None:
                    clean_req_consortium = consortium_id.strip() if consortium_id else ""
                    clean_est_consortium = est_consortium.strip() if est_consortium else ""
                    if clean_req_consortium != clean_est_consortium:
                        raise ModelAuthorizationError(
                            f"Cannot overwrite established model ownership: Model '{simulation_id}' is authoritatively owned by consortium "
                            f"'{est_consortium}'. Ordinary model saves cannot rebind ownership to '{consortium_id}'."
                        )
                effective_owning_bank = est_bank
                effective_consortium = est_consortium
                should_bind_initial = False

                if target_rcpt is not None and est_consortium is not None:
                    if not rcpt_consortium:
                        raise ModelAuthorizationError(
                            f"Receipt rejected for model '{simulation_id}': Receipt omits required consortium claim "
                            f"for consortium-bound scope '{est_consortium}'."
                        )
                    if rcpt_consortium != est_consortium:
                        raise ModelAuthorizationError(
                            f"Receipt rejected for model '{simulation_id}': Receipt binds consortium '{rcpt_consortium}', "
                            f"which does not match authorized scope '{est_consortium}'."
                        )

                # Enforce institutional governance authority for already-bound models
                if auth_token is not None:
                    from app.infrastructure.security.auth_service import AuthenticationService

                    auth_svc = AuthenticationService.get_instance()
                    is_valid, claims, _ = auth_svc.verify_access_token(auth_token)
                    if is_valid and claims:
                        uname = (claims.get("username") or claims.get("sub") or "").strip()
                        user_rec = auth_svc.get_user(uname)
                        if user_rec is not None:
                            self._verify_institutional_governance_authority(
                                simulation_id, user_rec, "save_version"
                            )
                elif actor is not None:
                    from app.infrastructure.security.auth_service import AuthenticationService

                    auth_svc = AuthenticationService.get_instance()
                    user_rec = auth_svc.get_user(actor)
                    if user_rec is not None:
                        self._verify_institutional_governance_authority(
                            simulation_id, user_rec, "save_version"
                        )
            elif owning_bank_id is not None or consortium_id is not None:
                effective_owning_bank = (
                    owning_bank_id.strip() if owning_bank_id and owning_bank_id.strip() else None
                )
                effective_consortium = (
                    consortium_id.strip() if consortium_id and consortium_id.strip() else None
                )
                should_bind_initial = bool(effective_owning_bank or effective_consortium)

            # Clean up any stale temporary files from previous crashed runs
            if os.path.isdir(sim_dir):
                for f_name in os.listdir(sim_dir):
                    if f_name.startswith(".tmp_model_") and f_name.endswith(".pt"):
                        with contextlib.suppress(OSError):
                            os.remove(os.path.join(sim_dir, f_name))

            manifest = self._load_manifest(simulation_id)

            # Idempotency Reconciler: Check if exact verified execution is already registered
            incoming_receipt_id = (
                receipt_dict.get("receipt_id")
                if (receipt_dict and isinstance(receipt_dict, dict))
                else None
            )
            incoming_job_id = (
                receipt_dict.get("job_id")
                if (receipt_dict and isinstance(receipt_dict, dict))
                else None
            )
            incoming_run_id = (
                training_run_id.strip()
                if (training_run_id and training_run_id.strip() and training_run_id != "unspecified")
                else None
            )

            # Determine candidate SHA-256 for identity and conflict checks
            candidate_sha256: str | None = None
            if source_bytes is not None:
                candidate_sha256 = hashlib.sha256(source_bytes).hexdigest()
            elif artifact_bytes is not None:
                candidate_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
            elif receipt_dict and receipt_dict.get("artifact_sha256"):
                candidate_sha256 = str(receipt_dict.get("artifact_sha256")).strip()
            elif state_dict is not None:
                buf = io.BytesIO()
                torch.save(state_dict, buf)
                source_bytes = buf.getvalue()
                candidate_sha256 = hashlib.sha256(source_bytes).hexdigest()

            if manifest and candidate_sha256:
                # If a receipt was provided but failed verification (tampered bytes or revoked key),
                # the candidate is unverified and cannot collide with or reconcile verified registrations.
                # Such invalid submissions must be recorded as unverified without triggering conflict errors.
                is_invalid_receipt_submission = target_rcpt is not None and not has_valid_receipt

                for m_entry in manifest:
                    entry_sha256 = m_entry.get("sha256")
                    entry_receipt = m_entry.get("training_receipt") or {}
                    entry_receipt_id = entry_receipt.get("receipt_id")
                    entry_job_id = entry_receipt.get("job_id")
                    entry_run_id = m_entry.get("training_run_id")

                    if not is_invalid_receipt_submission:
                        # Check for conflicting registrations
                        # 1. Receipt ID collision with conflicting digest
                        if incoming_receipt_id and entry_receipt_id and incoming_receipt_id == entry_receipt_id:
                            if entry_sha256 != candidate_sha256:
                                raise ModelIntegrityError(
                                    f"Conflicting registration: receipt_id '{incoming_receipt_id}' was already registered with artifact digest {entry_sha256}, differing from incoming {candidate_sha256}."
                                )
                            if effective_consortium and m_entry.get("consortium_id") and m_entry.get("consortium_id") != effective_consortium:
                                raise ModelIntegrityError(
                                    f"Conflicting consortium for receipt '{incoming_receipt_id}': registered={m_entry.get('consortium_id')}, incoming={effective_consortium}."
                                )

                        # 2. Training Run ID collision with conflicting digest
                        if incoming_run_id and entry_run_id and incoming_run_id == entry_run_id:
                            if entry_sha256 != candidate_sha256:
                                raise ModelIntegrityError(
                                    f"Conflicting registration: training_run_id '{incoming_run_id}' was already registered with artifact digest {entry_sha256}, differing from incoming {candidate_sha256}."
                                )
                            if effective_consortium and m_entry.get("consortium_id") and m_entry.get("consortium_id") != effective_consortium:
                                raise ModelIntegrityError(
                                    f"Conflicting consortium for training_run_id '{incoming_run_id}': registered={m_entry.get('consortium_id')}, incoming={effective_consortium}."
                                )

                    # 3. Exact match reconciliation:
                    # Same SHA-256 AND matching receipt_id or matching run_id or matching job_id
                    # Only reconcile if receipt is valid and candidate is production eligible
                    is_exact_match = False
                    if (
                        entry_sha256 == candidate_sha256
                        and has_valid_receipt
                        and m_entry.get("is_production_eligible") is True
                        and (
                            (incoming_receipt_id and entry_receipt_id and incoming_receipt_id == entry_receipt_id)
                            or (incoming_run_id and entry_run_id and incoming_run_id == entry_run_id)
                            or (incoming_job_id and entry_job_id and incoming_job_id == entry_job_id)
                        )
                    ):
                        is_exact_match = True

                    if is_exact_match:
                        # Reconcile existing entry: verify physical artifact exists on disk with matching digest
                        entry_filename = m_entry.get("filename")
                        if not entry_filename:
                            raise ModelIntegrityError(f"Corrupt manifest entry: missing filename for version {m_entry.get('version')}.")
                        existing_pt_path = os.path.join(sim_dir, entry_filename)
                        if not os.path.exists(existing_pt_path):
                            raise ModelIntegrityError(
                                f"Manifest records version {m_entry.get('version')} for simulation '{simulation_id}', but artifact file '{entry_filename}' is missing from disk."
                            )
                        if compute_file_sha256(existing_pt_path) != candidate_sha256:
                            raise ModelIntegrityError(
                                f"Disk artifact for version {m_entry.get('version')} does not match recorded digest {candidate_sha256}."
                            )

                        logger.info(
                            "Idempotent registration retry reconciled existing candidate version %s for simulation '%s' (SHA-256: %s)",
                            m_entry.get("version"),
                            simulation_id,
                            candidate_sha256,
                        )
                        return m_entry

            next_version = 1
            if manifest:
                next_version = max(entry["version"] for entry in manifest) + 1

            filename = f"model_v{next_version}.pt"
            filepath = os.path.join(sim_dir, filename)
            final_already_existed = os.path.exists(filepath)

            tmp_filename = f".tmp_model_v{next_version}_{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex}.pt"
            tmp_filepath = os.path.join(sim_dir, tmp_filename)

            newly_created_temp: str | None = None
            newly_created_final: str | None = None

            # Transactional Registration: Record durable intent BEFORE publishing artifact or mutating manifest
            mutation_id = f"mut_reg_{uuid.uuid4().hex}"
            from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

            audit_chain = ImmutableAuditChain.get_instance()
            intent_entry = audit_chain.append_event(
                event_type="MODEL_REGISTRATION_PENDING",
                actor=str(effective_owning_bank or "system"),
                target_id=f"{simulation_id}:v{next_version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": next_version,
                    "owning_bank_id": effective_owning_bank,
                    "consortium_id": effective_consortium,
                    "action": "save_version",
                    "decision": "PENDING",
                },
                actor_type="institutional_authority" if effective_owning_bank else "system",
                actor_institution=effective_owning_bank or "unknown",
                target_type="model_version",
                action="save_version",
                decision="PENDING",
            )

            newly_created_temp = tmp_filepath
            try:
                if source_bytes is not None:
                    # Invoke torch.save to trigger any serialization test hooks or validation
                    if state_dict is not None:
                        torch.save(state_dict, io.BytesIO())
                    with open(tmp_filepath, "wb") as f:
                        f.write(source_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                elif artifact_bytes is not None:
                    with open(tmp_filepath, "wb") as f:
                        f.write(artifact_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                else:
                    with open(tmp_filepath, "wb") as f:
                        torch.save(state_dict, f)
                        f.flush()
                        os.fsync(f.fileno())
                logger.debug(
                    "Serialized model version %d to staging path %s", next_version, tmp_filepath
                )

                file_sha256 = compute_file_sha256(tmp_filepath)

                # If receipt provided, verify artifact SHA256 binding and schema constraints
                if receipt_dict is not None and has_valid_receipt:
                    verifier = self._verifier or get_default_training_verifier()
                    is_valid_binding, binding_reason = verifier.verify_receipt(
                        receipt_dict,
                        candidate_artifact_sha256=file_sha256,
                        simulation_id=simulation_id,
                        expected_architecture=exp_arch,
                        expected_feature_schema_version=exp_schema_ver,
                        expected_input_dim=exp_dim,
                        expected_feature_names=exp_names,
                        expected_consortium_id=effective_consortium,
                    )
                    if not is_valid_binding:
                        raise ModelIntegrityError(
                            f"Supplied cryptographic training receipt is incompatible with candidate artifact bytes or contract: {binding_reason}"
                        )

                max_gen = max((e.get("generation", 0) for e in manifest), default=0)
                entry_gen = (max_gen + 1) if is_promoted else (max_gen if max_gen > 0 else 1)

                verified_schema_meta = None
                if has_valid_receipt and receipt_dict is not None:
                    verified_schema_meta = {
                        "architecture": receipt_dict.get("architecture"),
                        "feature_schema_version": receipt_dict.get("feature_schema_version"),
                        "input_dim": receipt_dict.get("input_dim"),
                        "feature_names": list(receipt_dict.get("feature_names", [])),
                    }

                entry: dict[str, Any] = {
                    "version": next_version,
                    "simulation_id": simulation_id,
                    "generation": entry_gen,
                    "filename": filename,
                    "architecture": verified_schema_meta["architecture"]
                    if verified_schema_meta
                    else exp_arch,
                    "feature_schema_version": verified_schema_meta["feature_schema_version"]
                    if verified_schema_meta
                    else exp_schema_ver,
                    "input_dim": verified_schema_meta["input_dim"]
                    if verified_schema_meta
                    else exp_dim,
                    "feature_names": verified_schema_meta["feature_names"]
                    if verified_schema_meta
                    else (list(exp_names) if exp_names is not None else None),
                    "verified_schema": verified_schema_meta,
                    "metrics": metrics,
                    "metrics_provenance": "CALLER_SUPPLIED_UNVERIFIED",
                    "is_active": is_promoted,
                    "status": status if status != "inactive" or not is_promoted else "champion",
                    "git_commit_hash": git_commit_hash or "unknown",
                    "dataset_hash": dataset_hash or "unknown",
                    "sha256": file_sha256,
                    "dp_noise_profile": dp_noise_profile
                    or {"mechanism": "none", "epsilon": 0.0, "delta": 0.0},
                    "provenance_type": final_provenance,
                    "is_production_eligible": is_prod_eligible,
                    "training_run_id": training_run_id or "unspecified",
                    "dataset_manifest_id": dataset_manifest_id or "unspecified",
                    "training_evidence": training_evidence or {},
                    "training_receipt": receipt_dict if has_valid_receipt else None,
                    "attestation_status": attestation_status,
                    "sign_offs": [],
                    "owning_bank_id": effective_owning_bank,
                    "consortium_id": effective_consortium,
                    "created_at": datetime.now(UTC).isoformat(),
                    "registration_mutation_id": mutation_id,
                    "last_mutation_id": mutation_id,
                    "applied_mutation_ids": [mutation_id],
                }

                # Centralized production admission check on promotion (BEFORE publishing artifact)
                if is_promoted:
                    validate_production_admission(entry, context="save_version")

                # Atomically promote staged artifact to authoritative filename
                os.replace(tmp_filepath, filepath)
                _sync_dir(sim_dir)
                newly_created_temp = None
                newly_created_final = filepath
                logger.info("Saved model version %d to %s", next_version, filepath)

                # If this new version is promoted, set other versions to inactive
                if is_promoted:
                    for existing_entry in manifest:
                        existing_entry["is_active"] = False
                        if existing_entry.get("status") == "champion":
                            existing_entry["status"] = "inactive"

                if should_bind_initial:
                    bound_by_label = (
                        "save_version_with_verified_receipt"
                        if has_valid_receipt
                        else "save_version"
                    )
                    self.bind_simulation_ownership(
                        simulation_id,
                        owning_bank_id=effective_owning_bank,
                        consortium_id=effective_consortium,
                        bound_by=bound_by_label,
                        auth_token=auth_token,
                        trusted_context=trusted_context,
                        training_receipt=receipt_obj
                        if (has_valid_receipt and receipt_obj is not None)
                        else None,
                    )

                manifest.append(entry)
                # Authoritative manifest is saved FIRST (Section 9.1)
                self._save_manifest(simulation_id, manifest)

                # Finalize pending registration mutation only after authoritative persistence
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type="MODEL_VERSION_CREATED",
                    actor=str(effective_owning_bank or "system"),
                    target_id=f"{simulation_id}:v{next_version}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "COMMITTED",
                        "mutation_committed": True,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "version": next_version,
                        "sha256": file_sha256,
                        "provenance_type": final_provenance,
                        "owning_bank_id": effective_owning_bank,
                        "consortium_id": effective_consortium,
                        "status": entry.get("status"),
                        "action": "save_version",
                        "decision": "VERSION_CREATED",
                    },
                    actor_type="institutional_authority" if effective_owning_bank else "system",
                    actor_institution=effective_owning_bank or "unknown",
                    target_type="model_version",
                    action="save_version",
                    decision="VERSION_CREATED",
                )

                # If promoted, update secondary link and invalidate cache
                if is_promoted:
                    try:
                        self._update_global_model_link(simulation_id, filepath)
                    except Exception as link_err:
                        logger.error("Failed to update global model link: %s", link_err)
                    with contextlib.suppress(Exception):
                        from app.application.services.model_service import ModelService

                        ModelService(settings).invalidate_model_cache()

                return entry
            except Exception as exc:
                if newly_created_temp and os.path.exists(newly_created_temp):
                    with contextlib.suppress(OSError):
                        os.remove(newly_created_temp)

                # Authoritative transaction outcome verification:
                # Determine outcome from durable mutation-specific evidence on disk,
                # NOT merely whether a helper returned successfully or a local flag was set.
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, next_version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    # Business commit succeeded: artifact and mutation ID are recorded in authoritative manifest.
                    # NEVER delete newly_created_final!
                    # NEVER mark mutation FAILED in audit chain!
                    # Preserve PENDING audit record so deterministic reconciliation can finalize it to COMMITTED.
                    logger.critical(
                        "Registration mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Preserving committed artifact '%s' "
                        "and leaving audit mutation PENDING for deterministic reconciliation.",
                        mutation_id,
                        simulation_id,
                        next_version,
                        exc,
                        newly_created_final,
                    )
                elif actual_status == "INDETERMINATE":
                    # Authoritative state is corrupt, unreadable, locked, or ambiguous.
                    # NEVER delete potentially committed artifact!
                    # NEVER finalize audit mutation as FAILED or COMMITTED!
                    # Preserve PENDING audit record for later recovery or investigation.
                    logger.warning(
                        "Registration outcome for mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving artifact '%s' and audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        next_version,
                        exc,
                        newly_created_final,
                    )
                else:
                    # Authoritative state is readable and positively proves the mutation was NOT committed (FAILED).
                    # Only remove newly_created_final if it is conclusively NOT referenced in any committed manifest.
                    if (
                        newly_created_final
                        and not final_already_existed
                        and os.path.exists(newly_created_final)
                    ):
                        is_referenced = False
                        try:
                            cur_manifest = self._load_manifest(simulation_id)
                            is_referenced = any(
                                e.get("filename") == filename
                                or mutation_id in e.get("applied_mutation_ids", [])
                                for e in cur_manifest
                            )
                        except Exception:
                            # Manifest unreadable: do NOT delete potentially committed artifact!
                            is_referenced = True

                        if not is_referenced:
                            with contextlib.suppress(OSError):
                                os.remove(newly_created_final)

                    if "intent_entry" in locals() and intent_entry is not None:
                        audit_chain.finalize_pending_mutation(
                            mutation_id=mutation_id,
                            event_type="MODEL_REGISTRATION_FAILED",
                            actor=str(effective_owning_bank or "system"),
                            target_id=f"{simulation_id}:v{next_version}",
                            details={
                                "mutation_id": mutation_id,
                                "mutation_stage": "FAILED",
                                "mutation_committed": False,
                                "intent_event_id": intent_entry.event_id,
                                "simulation_id": simulation_id,
                                "version": next_version,
                                "error": str(exc),
                                "action": "save_version",
                                "decision": "FAILED",
                            },
                            actor_type="institutional_authority"
                            if effective_owning_bank
                            else "system",
                            actor_institution=effective_owning_bank or "unknown",
                            target_type="model_version",
                            action="save_version",
                            decision="FAILED",
                        )
                if not isinstance(exc, (ModelIntegrityError, ModelAuthorizationError)):
                    raise ModelIntegrityError(f"Artifact serialization failed: {exc}") from exc
                raise

    def resolve_and_verify_champion(
        self, simulation_id: str | None = None
    ) -> tuple[str, bytes, dict[str, Any]] | None:
        """Resolve, verify integrity, and return (artifact_path, bytes_data, metadata) for active champion.

        Enforces:
        1. Registry manifest is the single source of truth (status='champion' and is_active=True).
        2. Rejects ambiguous multiple active champions deterministically.
        3. Verifies file existence, containment within storage, regular file type, non-emptiness.
        4. Verifies in-memory snapshot SHA-256 against recorded manifest digest.
        5. Returns in-memory bytes to eliminate TOCTOU races during model loading.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates: list[tuple[str, dict[str, Any], str]] = []

            target_sim_id: str | None = None
            if simulation_id is not None:
                target_sim_id = validate_simulation_id(simulation_id)
            else:
                from app.config import get_settings

                settings = get_settings()
                configured_default = os.environ.get("CFI_DEFAULT_SIMULATION_ID") or getattr(
                    settings, "default_simulation_id", ""
                )
                if configured_default and str(configured_default).strip():
                    target_sim_id = validate_simulation_id(str(configured_default).strip())

            if target_sim_id is not None:
                sim_dirs = [target_sim_id]
            else:
                sim_dirs = []
                for entry_name in sorted(os.listdir(self.registry_root)):
                    try:
                        clean_name = validate_simulation_id(entry_name)
                        candidate_dir = os.path.join(self.registry_root, clean_name)
                        if os.path.isdir(candidate_dir):
                            sim_dirs.append(clean_name)
                    except ValueError:
                        continue

            for s_id in sim_dirs:
                sim_path = os.path.join(self.registry_root, s_id)
                if not os.path.isdir(sim_path):
                    continue
                manifest = self._load_manifest(s_id)
                active_entries = [
                    e for e in manifest if e.get("status") == "champion" and e.get("is_active")
                ]
                if len(active_entries) > 1:
                    raise ModelIntegrityError(
                        f"Corrupt manifest in simulation '{s_id}': multiple active champion entries found "
                        f"({[e.get('version') for e in active_entries]})."
                    )
                if len(active_entries) == 1:
                    entry = active_entries[0]
                    fname = entry.get("filename") or f"model_v{entry.get('version')}.pt"
                    fpath = os.path.join(sim_path, fname)
                    candidates.append((s_id, entry, fpath))

            if not candidates:
                return None

            if len(candidates) > 1:
                conflicting_sims = [c[0] for c in candidates]
                raise ModelCompatibilityError(
                    f"Ambiguous unscoped champion resolution: multiple active champions exist across simulations: "
                    f"{conflicting_sims}. An explicit simulation_id scope or CFI_DEFAULT_SIMULATION_ID configuration is required."
                )

            sim_id, entry, filepath = candidates[0]

            raw_fname = entry.get("filename", os.path.basename(filepath))
            fname = os.path.basename(raw_fname.strip()) if isinstance(raw_fname, str) else ""
            if (
                not fname
                or fname != raw_fname.strip()
                or ".." in raw_fname
                or os.path.isabs(raw_fname)
                or raw_fname in (".", "..")
            ):
                raise ModelIntegrityError(
                    f"Path traversal detected in manifest filename '{raw_fname}'."
                )

            # Resolve real filesystem paths to eliminate symlink traversal and escapes (Defect E)
            real_filepath = os.path.realpath(filepath)
            real_storage = os.path.realpath(self.storage_dir)

            # Path & regular file validation
            if not os.path.exists(real_filepath):
                raise ModelNotAvailableError(
                    f"Active champion artifact file '{filepath}' for simulation '{sim_id}' does not exist on disk."
                )
            if os.path.isdir(real_filepath) or not os.path.isfile(real_filepath):
                raise ModelIntegrityError(
                    f"Active champion artifact path '{filepath}' is not a regular file."
                )

            # Strict containment check: realpath must be strictly contained within real_storage
            try:
                common = os.path.commonpath([real_filepath, real_storage])
                if common != real_storage:
                    raise ModelIntegrityError(
                        f"Active champion artifact path '{filepath}' (realpath: '{real_filepath}') is located outside trusted storage directory '{self.storage_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path containment check failed for artifact '{filepath}': {exc}"
                ) from exc

            # Explicit check if filepath is a symlink: reject if symlink targets outside storage root
            if os.path.islink(filepath):
                try:
                    symlink_target = os.path.realpath(
                        os.readlink(filepath) if hasattr(os, "readlink") else filepath
                    )
                    if os.path.commonpath([symlink_target, real_storage]) != real_storage:
                        raise ModelIntegrityError(
                            f"Active champion symlink '{filepath}' targets external path '{symlink_target}' outside trusted storage directory."
                        )
                except (OSError, ValueError) as exc:
                    raise ModelIntegrityError(
                        f"Symlink validation failed for '{filepath}': {exc}"
                    ) from exc

            # Non-empty check
            if os.path.getsize(real_filepath) == 0:
                raise ModelIntegrityError(
                    f"Active champion artifact file '{filepath}' is empty (0 bytes)."
                )

            # Read bytes into memory snapshot directly from resolved realpath to eliminate TOCTOU races
            try:
                with open(real_filepath, "rb") as f:
                    bytes_data = f.read()
            except Exception as exc:
                raise ModelIntegrityError(
                    f"Failed to read champion artifact '{filepath}': {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(bytes_data).hexdigest()
            expected_sha256 = entry.get("sha256")
            if not expected_sha256:
                raise ModelIntegrityError(
                    f"Champion manifest entry for simulation '{sim_id}' v{entry.get('version')} lacks recorded sha256."
                )
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise ModelIntegrityError(
                    f"Champion artifact SHA-256 digest mismatch for simulation '{sim_id}' v{entry.get('version')}: "
                    f"expected '{expected_sha256}', actual '{actual_sha256}'."
                )

            version_val = entry.get("version")
            if version_val is None or not isinstance(version_val, int) or version_val < 1:
                raise ModelIntegrityError(
                    "Champion manifest entry contains invalid or missing version number."
                )

            # Provenance checks via centralized production admission policy
            provenance_type = entry.get("provenance_type", "UNVERIFIED")
            from app.config import get_settings

            settings = get_settings()
            if settings.app_env.lower() == "production":
                validate_production_admission(entry, context="resolve_and_verify_champion")
                receipt_dict = entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        "Production admission rejected: model lacks verified cryptographic training execution receipt (resolve_and_verify_champion)."
                    )
                verifier = self._verifier or get_default_training_verifier()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=bytes_data,
                    simulation_id=sim_id,
                    expected_architecture=entry.get("architecture"),
                    expected_feature_schema_version=entry.get("feature_schema_version"),
                    expected_input_dim=entry.get("input_dim"),
                    expected_feature_names=entry.get("feature_names"),
                    expected_consortium_id=entry.get("consortium_id"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) (resolve_and_verify_champion)."
                    )
            elif entry.get("training_receipt"):
                receipt_dict = entry.get("training_receipt")
                if isinstance(receipt_dict, dict) and receipt_dict.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(receipt_dict["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{receipt_dict['key_id']}' has been explicitly revoked.) (resolve_and_verify_champion)."
                        )

            # Truthful classification of sign-offs
            sign_offs = entry.get("sign_offs", [])
            authenticity_status = "INTEGRITY_VERIFIED_UNAUTHENTICATED_MANIFEST"
            authorization_scheme = "NONE"
            is_independent_human = False

            if isinstance(sign_offs, list) and len(sign_offs) > 0:
                ml_signs = [s for s in sign_offs if s.get("role") in ("ml_engineer", "ml_lead")]
                comp_signs = [
                    s for s in sign_offs if s.get("role") in ("compliance", "compliance_officer")
                ]

                hmac_valid = False
                if ml_signs and comp_signs:
                    ml_s = ml_signs[0]
                    comp_s = comp_signs[0]
                    # Four-Eyes Principle check: distinct individuals
                    if ml_s.get("user") != comp_s.get("user"):
                        domain_key = derive_domain_key(
                            settings.payload_signing_secret, DOMAIN_APPROVAL_SIGNOFF
                        )
                        expected_ml_sig_domain = hmac.new(
                            domain_key,
                            f"{sim_id}:{version_val}:{actual_sha256}:ml_engineer:{ml_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_ml_sig_raw = hmac.new(
                            settings.payload_signing_secret.encode(),
                            f"{sim_id}:{version_val}:{actual_sha256}:ml_engineer:{ml_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_comp_sig_domain = hmac.new(
                            domain_key,
                            f"{sim_id}:{version_val}:{actual_sha256}:compliance:{comp_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        expected_comp_sig_raw = hmac.new(
                            settings.payload_signing_secret.encode(),
                            f"{sim_id}:{version_val}:{actual_sha256}:compliance:{comp_s.get('user')}".encode(),
                            hashlib.sha256,
                        ).hexdigest()

                        ml_ok = hmac.compare_digest(
                            ml_s.get("signature", ""), expected_ml_sig_domain
                        ) or hmac.compare_digest(ml_s.get("signature", ""), expected_ml_sig_raw)
                        comp_ok = hmac.compare_digest(
                            comp_s.get("signature", ""), expected_comp_sig_domain
                        ) or hmac.compare_digest(comp_s.get("signature", ""), expected_comp_sig_raw)
                        if ml_ok and comp_ok:
                            hmac_valid = True

                if hmac_valid:
                    authenticity_status = "APPLICATION_HMAC_AUTHENTICATED"
                    authorization_scheme = "APPLICATION_HMAC_SHA256"
                    is_independent_human = False
                else:
                    if settings.app_env.lower() == "production":
                        raise ModelIntegrityError(
                            f"Production admission rejected: active champion sign-offs failed cryptographic HMAC verification "
                            f"for simulation '{sim_id}' v{version_val} (resolve_and_verify_champion)."
                        )
                    authenticity_status = "SIGNOFFS_RECORDED_UNVERIFIED"
                    authorization_scheme = "UNVERIFIED"
                    is_independent_human = False

            is_prod_eligible = provenance_type in (
                "TRAINING_RECORDED",
                "TRAINING_EVIDENCE_VERIFIED",
            )

            resolved_metadata = {
                "version": version_val,
                "generation": entry.get("generation", 1),
                "filename": entry.get("filename", os.path.basename(filepath)),
                "simulation_id": sim_id,
                "artifact_path": filepath,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "status": "champion",
                "is_active": True,
                "is_verified": True,
                "authenticity_status": authenticity_status,
                "is_independent_human_authorization": is_independent_human,
                "authorization_scheme": authorization_scheme,
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "training_receipt": entry.get("training_receipt"),
                "metrics": entry.get("metrics", {}),
                "metrics_provenance": entry.get("metrics_provenance", "CALLER_SUPPLIED_UNVERIFIED"),
                "git_commit_hash": entry.get("git_commit_hash", "unknown"),
                "dataset_hash": entry.get("dataset_hash", "unknown"),
                "dp_noise_profile": entry.get("dp_noise_profile", {}),
                "provenance_type": provenance_type,
                "is_production_eligible": is_prod_eligible,
                "training_run_id": entry.get("training_run_id", "unspecified"),
                "dataset_manifest_id": entry.get("dataset_manifest_id", "unspecified"),
                "attestation_status": entry.get("attestation_status", "NONE"),
                "sign_offs": sign_offs,
                "created_at": entry.get("created_at"),
            }

            return filepath, bytes_data, resolved_metadata

    def resolve_and_verify_challenger(
        self, simulation_id: str | None = None
    ) -> tuple[str, bytes, dict[str, Any]] | None:
        """Resolve, verify integrity, and return (artifact_path, bytes_data, metadata) for active challenger.

        Enforces:
        1. Registry manifest is the single source of truth (status='challenger').
        2. Rejects ambiguous multiple active challengers deterministically.
        3. Verifies file existence, containment within storage, regular file type, non-emptiness.
        4. Verifies in-memory snapshot SHA-256 against recorded manifest digest.
        5. In production, enforces valid cryptographic receipt, trusted issuer, artifact integrity,
           schema compatibility, and production admission eligibility (without demanding champion-only signoffs).
        6. Returns in-memory bytes to eliminate TOCTOU races during model loading.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates: list[tuple[str, dict[str, Any], str]] = []

            target_sim_id: str | None = None
            if simulation_id is not None:
                target_sim_id = validate_simulation_id(simulation_id)
            else:
                from app.config import get_settings

                settings = get_settings()
                configured_default = os.environ.get("CFI_DEFAULT_SIMULATION_ID") or getattr(
                    settings, "default_simulation_id", ""
                )
                if configured_default and str(configured_default).strip():
                    target_sim_id = validate_simulation_id(str(configured_default).strip())

            if target_sim_id is not None:
                sim_dirs = [target_sim_id]
            else:
                sim_dirs = []
                for entry_name in sorted(os.listdir(self.registry_root)):
                    try:
                        clean_name = validate_simulation_id(entry_name)
                        candidate_dir = os.path.join(self.registry_root, clean_name)
                        if os.path.isdir(candidate_dir):
                            sim_dirs.append(clean_name)
                    except ValueError:
                        continue

            for s_id in sim_dirs:
                sim_path = os.path.join(self.registry_root, s_id)
                if not os.path.isdir(sim_path):
                    continue
                manifest = self._load_manifest(s_id)
                challenger_entries = [e for e in manifest if e.get("status") == "challenger"]
                if len(challenger_entries) > 1:
                    raise ModelIntegrityError(
                        f"Corrupt manifest in simulation '{s_id}': multiple active challenger entries found "
                        f"({[e.get('version') for e in challenger_entries]})."
                    )
                if len(challenger_entries) == 1:
                    entry = challenger_entries[0]
                    fname = entry.get("filename") or f"model_v{entry.get('version')}.pt"
                    fpath = os.path.join(sim_path, fname)
                    candidates.append((s_id, entry, fpath))

            if not candidates:
                return None

            if len(candidates) > 1:
                conflicting_sims = [c[0] for c in candidates]
                raise ModelCompatibilityError(
                    f"Ambiguous unscoped challenger resolution: multiple active challengers exist across simulations: "
                    f"{conflicting_sims}. An explicit simulation_id scope or CFI_DEFAULT_SIMULATION_ID configuration is required."
                )

            sim_id, entry, filepath = candidates[0]

            raw_fname = entry.get("filename", os.path.basename(filepath))
            fname = os.path.basename(raw_fname.strip()) if isinstance(raw_fname, str) else ""
            if (
                not fname
                or fname != raw_fname.strip()
                or ".." in raw_fname
                or os.path.isabs(raw_fname)
                or raw_fname in (".", "..")
            ):
                raise ModelIntegrityError(
                    f"Path traversal detected in manifest filename '{raw_fname}'."
                )

            real_filepath = os.path.realpath(filepath)
            real_storage = os.path.realpath(self.storage_dir)

            if not os.path.exists(real_filepath):
                raise ModelNotAvailableError(
                    f"Active challenger artifact file '{filepath}' for simulation '{sim_id}' does not exist on disk."
                )
            if os.path.isdir(real_filepath) or not os.path.isfile(real_filepath):
                raise ModelIntegrityError(
                    f"Active challenger artifact path '{filepath}' is not a regular file."
                )

            try:
                common = os.path.commonpath([real_filepath, real_storage])
                if common != real_storage:
                    raise ModelIntegrityError(
                        f"Active challenger artifact path '{filepath}' (realpath: '{real_filepath}') is located outside trusted storage directory '{self.storage_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path containment check failed for artifact '{filepath}': {exc}"
                ) from exc

            if os.path.islink(filepath):
                try:
                    symlink_target = os.path.realpath(
                        os.readlink(filepath) if hasattr(os, "readlink") else filepath
                    )
                    if os.path.commonpath([symlink_target, real_storage]) != real_storage:
                        raise ModelIntegrityError(
                            f"Active challenger symlink '{filepath}' targets external path '{symlink_target}' outside trusted storage directory."
                        )
                except (OSError, ValueError) as exc:
                    raise ModelIntegrityError(
                        f"Symlink validation failed for '{filepath}': {exc}"
                    ) from exc

            if os.path.getsize(real_filepath) == 0:
                raise ModelIntegrityError(
                    f"Active challenger artifact file '{filepath}' is empty (0 bytes)."
                )

            try:
                with open(real_filepath, "rb") as f:
                    bytes_data = f.read()
            except Exception as exc:
                raise ModelIntegrityError(
                    f"Failed to read challenger artifact '{filepath}': {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(bytes_data).hexdigest()
            expected_sha256 = entry.get("sha256")
            if not expected_sha256:
                raise ModelIntegrityError(
                    f"Challenger manifest entry for simulation '{sim_id}' v{entry.get('version')} lacks recorded sha256."
                )
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise ModelIntegrityError(
                    f"Challenger artifact SHA-256 digest mismatch for simulation '{sim_id}' v{entry.get('version')}: "
                    f"expected '{expected_sha256}', actual '{actual_sha256}'."
                )

            version_val = entry.get("version")
            if version_val is None or not isinstance(version_val, int) or version_val < 1:
                raise ModelIntegrityError(
                    "Challenger manifest entry contains invalid or missing version number."
                )

            from app.config import get_settings

            settings = get_settings()
            if settings.app_env.lower() == "production":
                validate_production_admission(entry, context="challenger_serving")
                receipt_dict = entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        "Production admission rejected: model lacks verified cryptographic training execution receipt (challenger_serving)."
                    )
                verifier = self._verifier or get_default_training_verifier()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=bytes_data,
                    simulation_id=sim_id,
                    expected_architecture=entry.get("architecture"),
                    expected_feature_schema_version=entry.get("feature_schema_version"),
                    expected_input_dim=entry.get("input_dim"),
                    expected_feature_names=entry.get("feature_names"),
                    expected_consortium_id=entry.get("consortium_id"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) (challenger_serving)."
                    )
            elif entry.get("training_receipt"):
                receipt_dict = entry.get("training_receipt")
                if isinstance(receipt_dict, dict) and receipt_dict.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(receipt_dict["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{receipt_dict['key_id']}' has been explicitly revoked.) (challenger_serving)."
                        )

            provenance_type = entry.get("provenance_type", "UNVERIFIED")
            is_prod_eligible = provenance_type in (
                "TRAINING_RECORDED",
                "TRAINING_EVIDENCE_VERIFIED",
            )

            resolved_metadata = {
                "version": version_val,
                "generation": entry.get("generation", 1),
                "filename": entry.get("filename", os.path.basename(filepath)),
                "simulation_id": sim_id,
                "artifact_path": filepath,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "status": "challenger",
                "is_active": False,
                "is_verified": True,
                "authenticity_status": entry.get(
                    "attestation_status", "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
                ),
                "is_independent_human_authorization": False,
                "authorization_scheme": "CRYPTOGRAPHIC_TRAINING_RECEIPT",
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "training_receipt": entry.get("training_receipt"),
                "metrics": entry.get("metrics", {}),
                "metrics_provenance": entry.get("metrics_provenance", "CALLER_SUPPLIED_UNVERIFIED"),
                "git_commit_hash": entry.get("git_commit_hash", "unknown"),
                "dataset_hash": entry.get("dataset_hash", "unknown"),
                "dp_noise_profile": entry.get("dp_noise_profile", {}),
                "provenance_type": provenance_type,
                "is_production_eligible": is_prod_eligible,
                "training_run_id": entry.get("training_run_id", "unspecified"),
                "dataset_manifest_id": entry.get("dataset_manifest_id", "unspecified"),
                "attestation_status": entry.get("attestation_status", "NONE"),
                "sign_offs": entry.get("sign_offs", []),
                "created_at": entry.get("created_at"),
            }

            return filepath, bytes_data, resolved_metadata

    def get_challenger_artifact_path(self, simulation_id: str | None = None) -> str | None:
        """Resolve the active challenger model file path on disk.

        Identified strictly via authoritative registry manifest.
        Returns None if no active challenger exists in the registry.
        """
        with self._lock:
            resolved = self.resolve_and_verify_challenger(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[0]
            return None

    def get_challenger_metadata(self, simulation_id: str | None = None) -> dict[str, Any] | None:
        """Return metadata for the currently active challenger model."""
        with self._lock:
            resolved = self.resolve_and_verify_challenger(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[2]
            return None

    def get_champion_artifact_path(self, simulation_id: str | None = None) -> str | None:
        """Resolve the active champion model file path on disk.

        Identified strictly via authoritative registry manifest.
        Returns None if no active champion exists in the registry.
        """
        with self._lock:
            resolved = self.resolve_and_verify_champion(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[0]
            return None

    def get_champion_metadata(self, simulation_id: str | None = None) -> dict[str, Any] | None:
        """Return metadata for the currently active champion model."""
        with self._lock:
            resolved = self.resolve_and_verify_champion(simulation_id=simulation_id)
            if resolved is not None:
                return resolved[2]
            return None

    def get_active_champion_identity(
        self, simulation_id: str | None = None
    ) -> dict[str, Any] | None:
        """Lightweight resolution of current active champion identity.

        Reads only manifest metadata without opening the model artifact file or computing SHA-256.
        Used on warm inference paths to revalidate champion version without avoidable I/O.
        Returns None if no active champion exists.
        """
        with self._lock:
            if not os.path.isdir(self.registry_root):
                return None

            candidates = []
            target_sim_id: str | None = None
            if simulation_id is not None:
                try:
                    target_sim_id = validate_simulation_id(simulation_id)
                except ValueError:
                    return None
            else:
                from app.config import get_settings

                settings = get_settings()
                configured_default = os.environ.get("CFI_DEFAULT_SIMULATION_ID") or getattr(
                    settings, "default_simulation_id", ""
                )
                if configured_default and str(configured_default).strip():
                    try:
                        target_sim_id = validate_simulation_id(str(configured_default).strip())
                    except ValueError:
                        return None

            if target_sim_id is not None:
                sim_dirs = [target_sim_id]
            else:
                sim_dirs = None
                with ModelRegistry._manifest_cache_guard:
                    hint = ModelRegistry._shared_champion_sim_hint
                    cached_root_mtime = ModelRegistry._shared_registry_root_mtime

                if hint is not None:
                    try:
                        current_root_mtime = os.path.getmtime(self.registry_root)
                        if current_root_mtime == cached_root_mtime:
                            hint_manifest = self._load_manifest(hint)
                            hint_active = [
                                e
                                for e in hint_manifest
                                if e.get("status") == "champion" and e.get("is_active")
                            ]
                            if len(hint_active) == 1:
                                candidates.append((hint, hint_active[0]))
                    except OSError:
                        pass

                if not candidates:
                    sim_dirs = []
                    for entry_name in sorted(os.listdir(self.registry_root)):
                        try:
                            clean_name = validate_simulation_id(entry_name)
                            candidate_dir = os.path.join(self.registry_root, clean_name)
                            if os.path.isdir(candidate_dir):
                                sim_dirs.append(clean_name)
                        except ValueError:
                            continue

            if sim_dirs is not None:
                for s_id in sim_dirs:
                    sim_path = os.path.join(self.registry_root, s_id)
                    if not os.path.isdir(sim_path):
                        continue
                    manifest = self._load_manifest(s_id)
                    active_entries = [
                        e for e in manifest if e.get("status") == "champion" and e.get("is_active")
                    ]
                    if len(active_entries) == 1:
                        entry = active_entries[0]
                        candidates.append((s_id, entry))

                if len(candidates) == 1 and target_sim_id is None:
                    try:
                        with ModelRegistry._manifest_cache_guard:
                            ModelRegistry._shared_champion_sim_hint = candidates[0][0]
                            ModelRegistry._shared_registry_root_mtime = os.path.getmtime(
                                self.registry_root
                            )
                    except OSError:
                        pass

            if len(candidates) != 1:
                return None

            s_id, entry = candidates[0]
            from app.config import get_settings

            settings = get_settings()
            if settings.app_env.lower() == "production":
                prov = str(entry.get("provenance_type", "UNVERIFIED")).strip().upper()
                if prov in (
                    "UNVERIFIED",
                    "STRUCTURAL_FIXTURE",
                    "LEGACY_UNVERIFIED",
                ) or prov not in (
                    "TRAINING_RECORDED",
                    "TRAINING_EVIDENCE_VERIFIED",
                ):
                    return None

                # Production champion requires valid cryptographic dual sign-offs
                sign_offs = entry.get("sign_offs", [])
                active_sign_offs = [
                    s
                    for s in sign_offs
                    if isinstance(s, dict)
                    and s.get("is_revoked") is not True
                    and s.get("is_active", True) is not False
                    and s.get("status", "active") != "revoked"
                ]
                if len(active_sign_offs) < 2:
                    return None
                ml_signs = [
                    s for s in active_sign_offs if s.get("role") in ("ml_engineer", "ml_lead")
                ]
                comp_signs = [
                    s
                    for s in active_sign_offs
                    if s.get("role") in ("compliance", "compliance_officer")
                ]
                if not ml_signs or not comp_signs:
                    return None
                ml_s, comp_s = ml_signs[0], comp_signs[0]
                if ml_s.get("user") == comp_s.get("user"):
                    return None
                secret = settings.payload_signing_secret
                if not secret or not secret.strip() or len(secret.strip()) < 16:
                    return None
                domain_key = derive_domain_key(secret, DOMAIN_APPROVAL_SIGNOFF)
                actual_sha = entry.get("sha256") or ""
                ver_val = entry.get("version")
                for s, norm_role in ((ml_s, "ml_engineer"), (comp_s, "compliance")):
                    u = s.get("user", "")
                    if u in {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}:
                        return None
                    if not s.get("is_role_verified") or not s.get("is_identity_verified"):
                        return None
                    canonical_payload = f"{s_id}:{ver_val}:{actual_sha}:{norm_role}:{u}"
                    if s.get("payload_bound") and s.get("payload_bound") != canonical_payload:
                        return None
                    exp_domain = hmac.new(
                        domain_key, canonical_payload.encode("utf-8"), hashlib.sha256
                    ).hexdigest()
                    exp_raw = hmac.new(
                        secret.encode("utf-8"), canonical_payload.encode("utf-8"), hashlib.sha256
                    ).hexdigest()
                    sig = s.get("signature") or ""
                    if not sig or not (
                        hmac.compare_digest(sig, exp_domain) or hmac.compare_digest(sig, exp_raw)
                    ):
                        return None

            key_id = None
            rcpt = entry.get("training_receipt")
            if isinstance(rcpt, dict):
                key_id = rcpt.get("key_id")
            if key_id:
                from app.application.services.training_provenance_service import (
                    get_default_training_verifier,
                )

                verifier = self._verifier or get_default_training_verifier()
                try:
                    if not verifier.is_key_trusted(key_id):
                        logger.warning(
                            "Active champion key_id '%s' for simulation '%s' is revoked or untrusted.",
                            key_id,
                            s_id,
                        )
                        return None
                except Exception as exc:
                    logger.error(
                        "Failed to verify trust for active champion key_id '%s': %s",
                        key_id,
                        exc,
                    )
                    return None
            elif settings.app_env.lower() == "production":
                return None

            return {
                "version": entry.get("version"),
                "generation": entry.get("generation", 1),
                "simulation_id": s_id,
                "sha256": entry.get("sha256"),
                "filename": entry.get("filename"),
                "architecture": entry.get("architecture", "FraudDetectionModel-GroupNorm"),
                "input_dim": entry.get("input_dim", 10),
                "feature_schema_version": entry.get("feature_schema_version"),
                "feature_names": entry.get("feature_names"),
                "verified_schema": entry.get("verified_schema"),
                "provenance_type": entry.get("provenance_type", "UNVERIFIED"),
                "issuer_key_id": key_id,
                "key_id": key_id,
            }

    def _update_global_model_link(self, simulation_id: str, filepath: str) -> None:
        """Update the root global_model.pt file to point to the active version atomically."""
        global_path = os.path.join(self.storage_dir, "global_model.pt")
        tmp_global_path = os.path.join(
            self.storage_dir, f"global_model_{os.getpid()}_{threading.get_ident()}.tmp"
        )
        try:
            shutil.copy2(filepath, tmp_global_path)
            os.replace(tmp_global_path, global_path)
            logger.info("Updated global_model.pt with version from %s", filepath)
        except Exception as e:
            logger.error("Failed to link/copy active version to global_model.pt: %s", e)
            if os.path.exists(tmp_global_path):
                with contextlib.suppress(OSError):
                    os.remove(tmp_global_path)

    def list_versions(self, simulation_id: str) -> list[dict[str, Any]]:
        """List all model versions tracked in this simulation registry."""
        return self._load_manifest(simulation_id)

    def load_version(
        self,
        simulation_id: str,
        version: int,
        allow_revoked: bool = False,
        target_class: type[torch.nn.Module] | None = None,
        model: torch.nn.Module | None = None,
    ) -> Any:
        """Load state dict or model instance of a specific model version from the registry.

        Enforces:
        1. Identifier validation and containment under registry root.
        2. Correct lock ordering: self._lock -> _get_registry_file_lock(sim_dir).
        3. Model revocation validation: rejects revoked versions unless allow_revoked=True for offline analysis.
        4. Manifest filename validation: strict basename, no path traversal or absolute paths.
        5. Path containment within sim_dir, regular file check, non-emptiness.
        6. In-memory snapshot to prevent time-of-check/time-of-use (TOCTOU) substitution.
        7. SHA-256 cryptographic verification of the exact bytes being loaded.
        8. Safe deserialization via io.BytesIO(snapshot_bytes) with weights_only=True.
        """
        clean_sim_id = validate_simulation_id(simulation_id)
        sim_dir = self._get_sim_dir(clean_sim_id, create=False)
        if not os.path.isdir(sim_dir):
            raise ValueError(f"Simulation '{clean_sim_id}' not found in registry.")

        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(clean_sim_id)
            entry = next((e for e in manifest if e.get("version") == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {clean_sim_id}")

            # Revocation validation
            is_revoked = bool(entry.get("is_revoked")) or entry.get("status") == "revoked"
            if is_revoked and not allow_revoked:
                raise ModelIntegrityError(
                    f"Model version {version} for simulation '{clean_sim_id}' has been revoked."
                )

            # Check training receipt key revocation
            rcpt = entry.get("training_receipt")
            if isinstance(rcpt, dict) and rcpt.get("key_id"):
                from app.application.services.training_provenance_service import (
                    get_default_training_verifier,
                )

                verifier = self._verifier or get_default_training_verifier()
                if not verifier.is_key_trusted(rcpt["key_id"]) and not allow_revoked:
                    raise ModelIntegrityError(
                        f"Model version {version} for simulation '{clean_sim_id}' rejected: "
                        f"training receipt key '{rcpt['key_id']}' has been revoked."
                    )

            # Validate manifest filename shape
            raw_filename = entry.get("filename")
            if not raw_filename or not isinstance(raw_filename, str):
                raise ModelIntegrityError(
                    f"Manifest entry for simulation '{clean_sim_id}' v{version} lacks valid filename."
                )

            clean_filename = os.path.basename(raw_filename.strip())
            if (
                clean_filename != raw_filename.strip()
                or ".." in raw_filename
                or os.path.isabs(raw_filename)
                or raw_filename in (".", "..")
            ):
                raise ModelIntegrityError(
                    f"Path traversal detected in manifest filename '{raw_filename}'."
                )

            filepath = os.path.join(sim_dir, clean_filename)
            real_filepath = os.path.realpath(filepath)
            real_sim_dir = os.path.realpath(sim_dir)

            if not os.path.exists(real_filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")

            if not os.path.isfile(real_filepath):
                raise ModelIntegrityError(
                    f"Model artifact path '{filepath}' is not a regular file."
                )

            # Enforce canonical containment within simulation directory
            try:
                if os.path.commonpath([real_filepath, real_sim_dir]) != real_sim_dir:
                    raise ModelIntegrityError(
                        f"Model artifact '{filepath}' escapes simulation directory '{sim_dir}'."
                    )
            except ValueError as exc:
                raise ModelIntegrityError(
                    f"Path containment check failed for artifact '{filepath}': {exc}"
                ) from exc

            # Validate symlink target if artifact is a symlink
            if os.path.islink(filepath):
                symlink_target = os.path.realpath(filepath)
                if os.path.commonpath([symlink_target, real_sim_dir]) != real_sim_dir:
                    raise ModelIntegrityError(
                        f"Model symlink '{filepath}' targets external path '{symlink_target}' outside simulation directory."
                    )

            # Non-empty check
            if os.path.getsize(real_filepath) == 0:
                raise ModelIntegrityError(f"Model artifact file '{filepath}' is empty (0 bytes).")

            # Read snapshot bytes into memory to eliminate TOCTOU races
            try:
                with open(real_filepath, "rb") as f:
                    snapshot_bytes = f.read()
            except Exception as exc:
                raise ModelIntegrityError(
                    f"Failed to read model artifact '{filepath}': {exc}"
                ) from exc

            # Cryptographic SHA-256 verification of the in-memory snapshot
            actual_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
            expected_sha256 = entry.get("sha256")
            if expected_sha256:
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ModelIntegrityError(
                        f"Model artifact SHA-256 digest mismatch for simulation '{clean_sim_id}' v{version}: "
                        f"expected '{expected_sha256}', actual '{actual_sha256}'."
                    )
            else:
                from app.config import get_settings

                if get_settings().app_env.lower() == "production":
                    raise ModelIntegrityError(
                        f"Model manifest entry for simulation '{clean_sim_id}' v{version} lacks recorded SHA-256 digest in production."
                    )

            try:
                loaded = torch.load(
                    io.BytesIO(snapshot_bytes), map_location="cpu", weights_only=True
                )
                if target_class is not None and model is None:
                    model = target_class()
                if model is not None:
                    if isinstance(loaded, dict):
                        model.load_state_dict(loaded)
                    elif isinstance(loaded, torch.nn.Module):
                        model = loaded
                    return model
                return loaded
            except Exception as e:
                logger.error("Failed to deserialize model file %s: %s", filepath, e)
                raise

    def get_active_version(self, simulation_id: str) -> dict[str, Any] | None:
        """Get metadata of the currently active model version."""
        manifest = self._load_manifest(simulation_id)
        return next((e for e in manifest if e["is_active"]), None)

    def get_version_metadata(self, simulation_id: str, version: int) -> dict[str, Any] | None:
        """Get metadata of a specific model version from the registry."""
        manifest = self._load_manifest(simulation_id)
        return next((e for e in manifest if e["version"] == version), None)

    def promote_version(
        self,
        simulation_id: str,
        version: int,
        target_status: str = "champion",
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: TrustedInternalContext | None = None,
    ) -> dict[str, Any]:
        """Promotes a model version to 'champion' or 'challenger' status."""
        if target_status not in ("champion", "challenger"):
            raise ValueError(
                f"Target status must be 'champion' or 'challenger', got '{target_status}'"
            )

        if actor and not auth_token and not trusted_context:
            raise ModelIntegrityError(
                f"Caller-supplied actor identity ('{actor}') cannot establish authority without valid authentication."
            )

        authenticated_operator: str | None = None
        authenticated_institution: str | None = None
        user_rec: Any | None = None
        if trusted_context:
            sim_ownership = self.get_simulation_ownership(simulation_id)
            owning_bank = sim_ownership.get("owning_bank_id")
            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action="promote_version",
                expected_service_name=trusted_context.service_name,
                expected_resource_type="model",
                expected_resource_id=simulation_id,
                expected_model_version=version,
                expected_bank_id=owning_bank,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise ModelIntegrityError(
                    f"Internal orchestration authority rejected: {ctx_reason}"
                )
            authenticated_operator = f"internal:{trusted_context.service_name}"
            authenticated_institution = trusted_context.bank_id or owning_bank
        elif auth_token:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err_detail = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise ModelIntegrityError(f"Operator token authentication failed: {err_detail}")
            authenticated_operator = (claims.get("username") or claims.get("sub") or "").strip()
            if actor and actor.lower().strip() != authenticated_operator.lower():
                raise ModelIntegrityError(
                    f"Actor '{actor}' does not match authenticated token principal '{authenticated_operator}'."
                )
            user_rec = auth_svc.get_user(authenticated_operator)
            if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
                raise ModelIntegrityError(
                    f"Operator '{authenticated_operator}' is not an active authoritative user."
                )
            user_roles = {r.lower().strip() for r in user_rec.roles}
            if not (user_roles & {"admin", "ml_lead", "ml_engineer"}):
                raise ModelIntegrityError(
                    f"Principal '{authenticated_operator}' lacks required role authority for model promotion."
                )
            # Clearance enforcement from live authoritative DurableAccountStore record:
            # Model Promotion requires minimum clearance level 2
            user_clearance = getattr(user_rec, "clearance_level", 1)
            if user_clearance < 2:
                raise ModelIntegrityError(
                    f"Principal '{authenticated_operator}' clearance level {user_clearance} is insufficient "
                    f"for model promotion (minimum clearance level 2 required)."
                )
            authenticated_operator = user_rec.username
            authenticated_institution = getattr(user_rec, "bank_id", None)
            self._verify_institutional_governance_authority(
                simulation_id, user_rec, "model promotion"
            )
        elif actor:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            user_rec = auth_svc.get_user(actor)
            if user_rec is not None:
                authenticated_institution = getattr(user_rec, "bank_id", None)
                self._verify_institutional_governance_authority(
                    simulation_id, user_rec, "model promotion"
                )

        effective_institution = authenticated_institution or "unknown"

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")
            if target_entry.get("is_revoked") is True or target_entry.get("status") == "revoked":
                raise ModelIntegrityError(
                    f"Cannot promote revoked model version {version} for {simulation_id}."
                )
            from app.config import get_settings

            settings = get_settings()

            target_entry["simulation_id"] = simulation_id
            effective_operator = authenticated_operator or actor
            if effective_operator:
                target_entry["promoted_by"] = effective_operator

            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")
            if target_entry.get("sha256"):
                actual_sha = compute_file_sha256(filepath)
                if not hmac.compare_digest(actual_sha, target_entry["sha256"]):
                    raise ModelIntegrityError(
                        f"Artifact integrity verification failed for {simulation_id} v{version}: "
                        f"recorded digest does not match file on disk."
                    )

            admission_context = (
                "promote_version" if target_status == "champion" else "promote_challenger"
            )
            validate_production_admission(target_entry, context=admission_context)

            if settings.app_env.lower() == "production":
                # Strict filename and path containment validation
                fname = target_entry.get("filename", "")
                if ".." in fname or os.path.isabs(fname):
                    raise ModelIntegrityError(
                        f"Path traversal detected in manifest filename '{fname}'."
                    )
                real_filepath = os.path.realpath(filepath)
                real_storage = os.path.realpath(self.storage_dir)
                try:
                    common = os.path.commonpath([real_filepath, real_storage])
                    if common != real_storage:
                        raise ModelIntegrityError(
                            f"Artifact path '{filepath}' is outside trusted storage directory '{self.storage_dir}'."
                        )
                except ValueError as exc:
                    raise ModelIntegrityError(
                        f"Path containment check failed for artifact '{filepath}': {exc}"
                    ) from exc

                if not os.path.isfile(real_filepath) or os.path.getsize(real_filepath) == 0:
                    raise ModelIntegrityError(
                        f"Artifact file '{filepath}' is missing, not a regular file, or empty."
                    )

                actual_sha256 = compute_file_sha256(real_filepath)
                expected_sha256 = target_entry.get("sha256")
                if not expected_sha256:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model version {version} lacks recorded sha256 ({admission_context})."
                    )
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ModelIntegrityError(
                        f"Production admission rejected: artifact SHA-256 digest mismatch for {simulation_id} v{version}: "
                        f"expected '{expected_sha256}', actual '{actual_sha256}' ({admission_context})."
                    )

                receipt_dict = target_entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model lacks verified cryptographic training execution receipt ({admission_context})."
                    )
                verifier = self._verifier or get_default_training_verifier()
                with open(real_filepath, "rb") as rf:
                    artifact_bytes = rf.read()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=artifact_bytes,
                    simulation_id=simulation_id,
                    expected_architecture=target_entry.get("architecture"),
                    expected_feature_schema_version=target_entry.get("feature_schema_version"),
                    expected_input_dim=target_entry.get("input_dim"),
                    expected_feature_names=target_entry.get("feature_names"),
                    expected_consortium_id=target_entry.get("consortium_id"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) ({admission_context})."
                    )

                # Enforce production operator authorization fail-closed
                if not authenticated_operator:
                    raise ModelIntegrityError(
                        f"Production model promotion rejected: unauthenticated operation. "
                        f"Mandatory Bearer auth_token or verified internal authority required ({admission_context})."
                    )
            elif target_entry.get("training_receipt"):
                rcpt = target_entry.get("training_receipt")
                if isinstance(rcpt, dict) and rcpt.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(rcpt["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{rcpt['key_id']}' has been explicitly revoked.) ({admission_context})."
                        )

            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            if target_status == "champion":
                target_entry["generation"] = max_gen + 1
                for entry in manifest:
                    entry["is_active"] = entry["version"] == version
                    if entry["version"] == version:
                        entry["status"] = "champion"
                    elif entry.get("status") == "champion":
                        entry["status"] = "inactive"
            else:  # challenger
                for entry in manifest:
                    if entry["version"] == version:
                        entry["status"] = "challenger"
                        entry["is_active"] = False
                    elif entry.get("status") == "challenger":
                        entry["status"] = "inactive"

            # Emit audit log event BEFORE saving manifest (Intent stage: PENDING)
            mutation_id = f"mut_prom_{uuid.uuid4().hex}"
            from app.infrastructure.security.immutable_audit_chain import (
                AuditLedgerSecurityError,
                ImmutableAuditChain,
            )

            audit_chain = ImmutableAuditChain.get_instance()
            intent_entry = audit_chain.append_event(
                event_type="MODEL_PROMOTION_PENDING",
                actor=effective_operator or "system",
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": version,
                    "target_status": target_status,
                    "promoted_by": effective_operator,
                    "actor_institution": effective_institution,
                    "generation": target_entry.get("generation"),
                    "action": "promote_version",
                    "decision": "PENDING",
                },
                actor_type="internal_service"
                if (effective_operator or "").startswith("internal:")
                else "human_principal",
                actor_institution=effective_institution,
                target_type="model_version",
                action="promote_version",
                decision="PENDING",
            )

            # Record durable mutation evidence on target entry before persistence
            if "applied_mutation_ids" not in target_entry:
                target_entry["applied_mutation_ids"] = []
            if mutation_id not in target_entry["applied_mutation_ids"]:
                target_entry["applied_mutation_ids"].append(mutation_id)
            target_entry["last_mutation_id"] = mutation_id

            # Authoritative manifest is saved FIRST (Section 9.1)
            try:
                self._save_manifest(simulation_id, manifest)
            except Exception as save_err:
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    logger.critical(
                        "Promotion mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Leaving audit mutation PENDING for reconciliation.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                elif actual_status == "INDETERMINATE":
                    logger.warning(
                        "Outcome for promotion mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                else:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_PROMOTION_FAILED",
                        actor=effective_operator or "system",
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "error": str(save_err),
                            "action": "promote_version",
                            "actor_institution": effective_institution,
                            "decision": "FAILED",
                        },
                        actor_type="internal_service"
                        if (effective_operator or "").startswith("internal:")
                        else "human_principal",
                        actor_institution=effective_institution,
                        target_type="model_version",
                        action="promote_version",
                        decision="FAILED",
                    )
                raise

            # Finalize committed audit event
            try:
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type="MODEL_PROMOTION",
                    actor=effective_operator or "system",
                    target_id=f"{simulation_id}:v{version}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "COMMITTED",
                        "mutation_committed": True,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "version": version,
                        "target_status": target_status,
                        "promoted_by": effective_operator,
                        "actor_institution": effective_institution,
                        "generation": target_entry.get("generation"),
                        "action": "promote_version",
                        "decision": "PROMOTED",
                    },
                    actor_type="internal_service"
                    if (effective_operator or "").startswith("internal:")
                    else "human_principal",
                    actor_institution=effective_institution,
                    target_type="model_version",
                    action="promote_version",
                    decision="PROMOTED",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to finalize committed audit event for promotion: %s", audit_err
                )
                raise AuditLedgerSecurityError(
                    f"Critical audit failure: promotion for {simulation_id}:v{version} persisted but could not be finalized in audit ledger: {audit_err}"
                ) from audit_err

            # Secondary global model link updated SECOND
            if target_status == "champion":
                try:
                    self._update_global_model_link(simulation_id, filepath)
                except Exception as link_err:
                    logger.error("Failed to update global model link: %s", link_err)
                with contextlib.suppress(Exception):
                    from app.application.services.model_service import ModelService

                    ModelService(settings).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            logger.info(
                "Promoted model version %d to %s for %s", version, target_status, simulation_id
            )
            return target_entry

    def rollback(
        self,
        simulation_id: str,
        version: int,
        auth_token: str | None = None,
        actor: str | None = None,
        trusted_context: TrustedInternalContext | None = None,
    ) -> dict[str, Any]:
        """Rollback/promote a specific historical version as the active model."""
        if actor and not auth_token and not trusted_context:
            raise ModelIntegrityError(
                f"Caller-supplied actor identity ('{actor}') cannot establish authority without valid authentication."
            )

        authenticated_operator: str | None = None
        authenticated_institution: str | None = None
        user_rec: Any | None = None
        if trusted_context:
            sim_ownership = self.get_simulation_ownership(simulation_id)
            owning_bank = sim_ownership.get("owning_bank_id")
            is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                trusted_context,
                expected_action="rollback",
                expected_service_name=trusted_context.service_name,
                expected_resource_type="model",
                expected_resource_id=simulation_id,
                expected_model_version=version,
                expected_bank_id=owning_bank,
                consume_nonce=True,
            )
            if not is_valid_ctx:
                raise ModelIntegrityError(
                    f"Internal orchestration authority rejected: {ctx_reason}"
                )
            authenticated_operator = f"internal:{trusted_context.service_name}"
            authenticated_institution = trusted_context.bank_id or owning_bank
        elif auth_token:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            is_valid, claims, err_detail = auth_svc.verify_access_token(auth_token)
            if not is_valid or not claims:
                raise ModelIntegrityError(f"Operator token authentication failed: {err_detail}")
            authenticated_operator = (claims.get("username") or claims.get("sub") or "").strip()
            if actor and actor.lower().strip() != authenticated_operator.lower():
                raise ModelIntegrityError(
                    f"Actor '{actor}' does not match authenticated token principal '{authenticated_operator}'."
                )
            user_rec = auth_svc.get_user(authenticated_operator)
            if not user_rec or not user_rec.is_active or user_rec.status.lower() != "active":
                raise ModelIntegrityError(
                    f"Operator '{authenticated_operator}' is not an active authoritative user."
                )
            user_roles = {r.lower().strip() for r in user_rec.roles}
            if not (user_roles & {"admin", "ml_lead", "compliance", "compliance_officer"}):
                raise ModelIntegrityError(
                    f"Principal '{authenticated_operator}' lacks required role authority for model rollback."
                )
            # Clearance enforcement from live authoritative DurableAccountStore record:
            # Model Rollback requires minimum clearance level 3
            user_clearance = getattr(user_rec, "clearance_level", 1)
            if user_clearance < 3:
                raise ModelIntegrityError(
                    f"Principal '{authenticated_operator}' clearance level {user_clearance} is insufficient "
                    f"for model rollback (minimum clearance level 3 required)."
                )
            authenticated_operator = user_rec.username
            authenticated_institution = getattr(user_rec, "bank_id", None)
            self._verify_institutional_governance_authority(
                simulation_id, user_rec, "model rollback"
            )
        elif actor:
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()
            user_rec = auth_svc.get_user(actor)
            if user_rec is not None:
                authenticated_institution = getattr(user_rec, "bank_id", None)
                self._verify_institutional_governance_authority(
                    simulation_id, user_rec, "model rollback"
                )

        effective_institution = authenticated_institution or "unknown"

        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            target_entry = next((e for e in manifest if e["version"] == version), None)
            if not target_entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")
            if target_entry.get("is_revoked") is True or target_entry.get("status") == "revoked":
                raise ModelIntegrityError(
                    f"Cannot roll back to revoked model version {version} for {simulation_id}."
                )
            from app.config import get_settings

            settings = get_settings()

            target_entry["simulation_id"] = simulation_id
            effective_operator = authenticated_operator or actor
            if effective_operator:
                target_entry["rolled_back_by"] = effective_operator

            filepath = os.path.join(sim_dir, target_entry["filename"])
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"Model file {filepath} not found on disk")
            if target_entry.get("sha256"):
                actual_sha = compute_file_sha256(filepath)
                if not hmac.compare_digest(actual_sha, target_entry["sha256"]):
                    raise ModelIntegrityError(
                        f"Artifact integrity verification failed for {simulation_id} v{version}: "
                        f"recorded digest does not match file on disk."
                    )

            admission_context = "rollback"
            validate_production_admission(target_entry, context=admission_context)

            if settings.app_env.lower() == "production":
                fname = target_entry.get("filename", "")
                if ".." in fname or os.path.isabs(fname):
                    raise ModelIntegrityError(
                        f"Path traversal detected in manifest filename '{fname}'."
                    )
                real_filepath = os.path.realpath(filepath)
                real_storage = os.path.realpath(self.storage_dir)
                try:
                    common = os.path.commonpath([real_filepath, real_storage])
                    if common != real_storage:
                        raise ModelIntegrityError(
                            f"Artifact path '{filepath}' is outside trusted storage directory '{self.storage_dir}'."
                        )
                except ValueError as exc:
                    raise ModelIntegrityError(
                        f"Path containment check failed for artifact '{filepath}': {exc}"
                    ) from exc

                if not os.path.isfile(real_filepath) or os.path.getsize(real_filepath) == 0:
                    raise ModelIntegrityError(
                        f"Artifact file '{filepath}' is missing, not a regular file, or empty."
                    )

                actual_sha256 = compute_file_sha256(real_filepath)
                expected_sha256 = target_entry.get("sha256")
                if not expected_sha256:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model version {version} lacks recorded sha256 ({admission_context})."
                    )
                if not hmac.compare_digest(actual_sha256, expected_sha256):
                    raise ModelIntegrityError(
                        f"Production admission rejected: artifact SHA-256 digest mismatch for {simulation_id} v{version}: "
                        f"expected '{expected_sha256}', actual '{actual_sha256}' ({admission_context})."
                    )

                receipt_dict = target_entry.get("training_receipt")
                if not receipt_dict:
                    raise ModelIntegrityError(
                        f"Production admission rejected: model lacks verified cryptographic training execution receipt ({admission_context})."
                    )
                verifier = self._verifier or get_default_training_verifier()
                with open(real_filepath, "rb") as rf:
                    artifact_bytes = rf.read()
                is_valid_rcpt, rcpt_reason = verifier.verify_receipt(
                    receipt_dict,
                    candidate_artifact_sha256=actual_sha256,
                    candidate_artifact_bytes=artifact_bytes,
                    simulation_id=simulation_id,
                    expected_architecture=target_entry.get("architecture"),
                    expected_feature_schema_version=target_entry.get("feature_schema_version"),
                    expected_input_dim=target_entry.get("input_dim"),
                    expected_feature_names=target_entry.get("feature_names"),
                    expected_consortium_id=target_entry.get("consortium_id"),
                )
                if not is_valid_rcpt:
                    raise ModelIntegrityError(
                        f"Production admission rejected: cryptographic training receipt verification failed ({rcpt_reason}) ({admission_context})."
                    )

                # Enforce production operator authorization fail-closed
                if not authenticated_operator:
                    raise ModelIntegrityError(
                        "Production model rollback rejected: unauthenticated operation. "
                        "Mandatory Bearer auth_token or verified internal authority required (rollback)."
                    )
            elif target_entry.get("training_receipt"):
                rcpt = target_entry.get("training_receipt")
                if isinstance(rcpt, dict) and rcpt.get("key_id"):
                    verifier = self._verifier or get_default_training_verifier()
                    if not verifier.is_key_trusted(rcpt["key_id"]):
                        raise ModelIntegrityError(
                            f"Cryptographic training receipt verification failed (Receipt key_id '{rcpt['key_id']}' has been explicitly revoked.) ({admission_context})."
                        )

            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            target_entry["generation"] = max_gen + 1

            # Mark target as active, all others as inactive
            for entry in manifest:
                entry["is_active"] = entry["version"] == version
                if entry["version"] == version:
                    entry["status"] = "champion"
                elif entry.get("status") == "champion":
                    entry["status"] = "inactive"

            # Emit audit log event BEFORE saving manifest (Intent stage: PENDING)
            mutation_id = f"mut_rb_{uuid.uuid4().hex}"
            from app.infrastructure.security.immutable_audit_chain import (
                AuditLedgerSecurityError,
                ImmutableAuditChain,
            )

            audit_chain = ImmutableAuditChain.get_instance()
            intent_entry = audit_chain.append_event(
                event_type="MODEL_ROLLBACK_PENDING",
                actor=effective_operator or "system",
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": version,
                    "rolled_back_by": effective_operator,
                    "actor_institution": effective_institution,
                    "generation": target_entry.get("generation"),
                    "action": "rollback",
                    "decision": "PENDING",
                },
                actor_type="internal_service"
                if (effective_operator or "").startswith("internal:")
                else "human_principal",
                actor_institution=effective_institution,
                target_type="model_version",
                action="rollback",
                decision="PENDING",
            )

            # Record durable mutation evidence on target entry before persistence
            if "applied_mutation_ids" not in target_entry:
                target_entry["applied_mutation_ids"] = []
            if mutation_id not in target_entry["applied_mutation_ids"]:
                target_entry["applied_mutation_ids"].append(mutation_id)
            target_entry["last_mutation_id"] = mutation_id

            # Authoritative manifest is saved FIRST (Section 9.1)
            try:
                self._save_manifest(simulation_id, manifest)
            except Exception as save_err:
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    logger.critical(
                        "Rollback mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Leaving audit mutation PENDING for reconciliation.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                elif actual_status == "INDETERMINATE":
                    logger.warning(
                        "Outcome for rollback mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                else:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_ROLLBACK_FAILED",
                        actor=effective_operator or "system",
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "error": str(save_err),
                            "action": "rollback",
                            "actor_institution": effective_institution,
                            "decision": "FAILED",
                        },
                        actor_type="internal_service"
                        if (effective_operator or "").startswith("internal:")
                        else "human_principal",
                        actor_institution=effective_institution,
                        target_type="model_version",
                        action="rollback",
                        decision="FAILED",
                    )
                raise

            # Finalize committed audit event
            try:
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type="MODEL_ROLLBACK",
                    actor=effective_operator or "system",
                    target_id=f"{simulation_id}:v{version}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "COMMITTED",
                        "mutation_committed": True,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "version": version,
                        "rolled_back_by": effective_operator,
                        "actor_institution": effective_institution,
                        "generation": target_entry.get("generation"),
                        "action": "rollback",
                        "decision": "ROLLED_BACK",
                    },
                    actor_type="internal_service"
                    if (effective_operator or "").startswith("internal:")
                    else "human_principal",
                    actor_institution=effective_institution,
                    target_type="model_version",
                    action="rollback",
                    decision="ROLLED_BACK",
                )
            except Exception as audit_err:
                logger.error("Failed to finalize committed audit event for rollback: %s", audit_err)
                raise AuditLedgerSecurityError(
                    f"Critical audit failure: rollback for {simulation_id}:v{version} persisted but could not be finalized in audit ledger: {audit_err}"
                ) from audit_err

            # Secondary global model link updated SECOND
            try:
                self._update_global_model_link(simulation_id, filepath)
            except Exception as link_err:
                logger.error("Failed to update global model link during rollback: %s", link_err)

            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService

                ModelService(settings).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            logger.info(
                "Rolled back registry of %s to version %d (generation %d)",
                simulation_id,
                version,
                target_entry["generation"],
            )
            return target_entry

    def sign_off(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str,
        signature: str,
        auth_token: str | None = None,
        fairness_score: float = 1.0,
        bias_metric: float = 0.0,
        drift_divergence: float = 0.0,
        auto_promote: bool = False,
    ) -> dict[str, Any]:
        """Approve a model version by adding an authenticated institutional sign-off.

        Institutional approval records and cryptographically authenticates dual sign-offs
        (Four-Eyes Principle: compliance and ml_engineer), establishing 'approved' status
        and production eligibility. Operational promotion is separated and requires
        separately authorized promote_version() (or explicit auto_promote=True).
        """
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")
            if entry.get("is_revoked") is True or entry.get("status") == "revoked":
                raise ModelIntegrityError(
                    f"Cannot sign off on revoked model version {version} for {simulation_id}."
                )

            if role not in ("compliance", "compliance_officer", "ml_engineer"):
                raise ValueError("Role must be 'compliance' or 'ml_engineer'")

            from app.config import get_settings

            settings = get_settings()

            # Prevent development seeded accounts from signing off in production
            SEEDED_ACCOUNTS = {"admin", "analyst_a1", "analyst_b1", "analyst_c1", "auditor"}
            if settings.app_env.lower() == "production" and user in SEEDED_ACCOUNTS:
                raise ModelIntegrityError(
                    f"Production promotion rejected: development seeded account '{user}' cannot authorize promotion."
                )

            norm_role = (
                "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"
            )

            # Initialize sign_offs list if not present (backward compatibility)
            entry["simulation_id"] = simulation_id
            if "sign_offs" not in entry:
                entry["sign_offs"] = []

            # Prevent duplicate sign-offs for the same role (only active sign-offs block new sign-offs)
            existing_roles = [
                (
                    "compliance"
                    if s["role"] in ("compliance", "compliance_officer")
                    else "ml_engineer"
                )
                for s in entry["sign_offs"]
                if s.get("is_revoked") is not True
                and s.get("is_active", True) is not False
                and s.get("status", "active") != "revoked"
            ]
            if norm_role in existing_roles:
                raise ValueError(f"Role '{role}' has already signed off on this version")

            # Verify identity and role membership against AuthenticationService
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()

            # Retrieve authoritative user record first for stable identity and live RBAC checks
            authoritative_user = auth_svc.get_user(user)

            # Four-Eyes Principle: Prevent same user from approving multiple roles on the same version
            # Defend against aliases, casing differences, and token identifier variations
            user_canonical = (
                authoritative_user.username.lower() if authoritative_user else user.lower().strip()
            )
            user_id_canonical = authoritative_user.user_id if authoritative_user else None

            for s in entry["sign_offs"]:
                if (
                    s.get("is_revoked") is True
                    or s.get("is_active", True) is False
                    or s.get("status", "active") == "revoked"
                ):
                    continue
                existing_u = (s.get("user") or "").lower().strip()
                existing_uid = (s.get("user_id") or "").strip()
                if (
                    user_canonical == existing_u
                    or (user_id_canonical and existing_uid and user_id_canonical == existing_uid)
                    or (user.lower().strip() == existing_u)
                ):
                    raise ValueError(
                        f"Four-Eyes Principle violation: user '{user}' cannot approve multiple roles on the same version"
                    )

            is_identity_verified = False
            is_role_verified = False

            if auth_token:
                is_valid_tok, tok_payload, tok_err = auth_svc.verify_access_token(auth_token)
                if not is_valid_tok or not tok_payload:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: sign-off authentication failed: {tok_err}"
                    )
                tok_user = (tok_payload.get("username") or "").lower().strip()
                tok_sub = (tok_payload.get("sub") or "").strip()
                if (
                    tok_user != user_canonical
                    and tok_sub != user_canonical
                    and (not user_id_canonical or tok_sub != user_id_canonical)
                    and tok_user != user.lower().strip()
                ):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: token user '{tok_user}' does not match sign-off user '{user}'."
                    )
                tok_roles = [r.lower().strip() for r in tok_payload.get("roles", [])]
                if norm_role == "compliance" and not any(
                    r in ("compliance", "compliance_officer", "admin") for r in tok_roles
                ):
                    raise ModelIntegrityError(
                        "Production promotion rejected: token claims lack compliance role authority."
                    )
                if norm_role == "ml_engineer" and not any(
                    r in ("ml_engineer", "ml_lead") for r in tok_roles
                ):
                    raise ModelIntegrityError(
                        "Production promotion rejected: token claims lack ml_engineer role authority."
                    )

                if not authoritative_user or not authoritative_user.is_active:
                    raise ModelIntegrityError(
                        f"Production promotion rejected: approver principal '{user}' is not an active authoritative user."
                    )

                # Live Authoritative RBAC check: Must currently possess the required role in DurableAccountStore
                auth_roles = [r.lower().strip() for r in authoritative_user.roles]
                if norm_role == "compliance" and not any(
                    r in ("compliance", "compliance_officer", "admin") for r in auth_roles
                ):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: user '{user}' lacks authoritative compliance role permissions."
                    )
                if norm_role == "ml_engineer" and not any(
                    r in ("ml_engineer", "ml_lead") for r in auth_roles
                ):
                    raise ModelIntegrityError(
                        f"Production promotion rejected: user '{user}' lacks authoritative ml_engineer role permissions."
                    )
                tok_bank = (tok_payload.get("bank_id") or "").lower().strip()
                user_bank = (getattr(authoritative_user, "bank_id", "") or "").lower().strip()
                if tok_bank and user_bank and tok_bank != user_bank:
                    raise ModelAuthorizationError(
                        f"Sign-off token bank claim '{tok_bank}' does not match authoritative institution '{user_bank}'."
                    )
                is_identity_verified = True
                is_role_verified = True
            elif authoritative_user and authoritative_user.is_active:
                auth_roles = [r.lower().strip() for r in authoritative_user.roles]
                has_role = False
                if (
                    norm_role == "compliance"
                    and any(r in ("compliance", "compliance_officer", "admin") for r in auth_roles)
                    or norm_role == "ml_engineer"
                    and any(r in ("ml_engineer", "ml_lead") for r in auth_roles)
                ):
                    has_role = True

                if has_role:
                    is_identity_verified = True
                    is_role_verified = True
                else:
                    if settings.app_env.lower() == "production":
                        is_identity_verified = True
                        is_role_verified = False
                    else:
                        raise ModelIntegrityError(
                            f"Sign-off rejected: user '{user}' lacks authoritative {norm_role} role permissions."
                        )
            elif settings.app_env.lower() == "production":
                raise ModelIntegrityError(
                    "Production promotion rejected: authenticated session token is required for institutional sign-off."
                )
            else:
                raise ModelIntegrityError(
                    f"Sign-off rejected: user '{user}' is not an active authoritative user."
                )

            # Authoritative institutional tenant check
            self._verify_institutional_governance_authority(
                simulation_id, authoritative_user, "model sign-off"
            )

            # Cryptographic signature verification over canonical payload
            artifact_sha = entry.get("sha256") or compute_file_sha256(
                os.path.join(sim_dir, entry["filename"])
            )
            canonical_payload = f"{simulation_id}:{version}:{artifact_sha}:{norm_role}:{user}"

            domain_key = derive_domain_key(settings.payload_signing_secret, DOMAIN_APPROVAL_SIGNOFF)
            expected_sig_domain = hmac.new(
                domain_key,
                canonical_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            expected_sig_raw = hmac.new(
                settings.payload_signing_secret.encode("utf-8"),
                canonical_payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            is_signature_valid = bool(
                signature
                and (
                    hmac.compare_digest(signature, expected_sig_domain)
                    or hmac.compare_digest(signature, expected_sig_raw)
                )
            )

            from app.infrastructure.security.immutable_audit_chain import (
                ImmutableAuditChain,
            )

            audit_chain = ImmutableAuditChain.get_instance()
            mutation_id = f"mut_signoff_{uuid.uuid4().hex}"
            intent_entry = audit_chain.append_event(
                event_type="MODEL_SIGNOFF_PENDING",
                actor=user,
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": version,
                    "role": norm_role,
                    "user": user,
                    "user_id": user_id_canonical,
                    "signature": signature,
                    "artifact_digest": entry.get("sha256"),
                    "action": "sign_off",
                    "decision": "PENDING",
                },
                actor_type="human_principal" if is_identity_verified else "unverified_operator",
                actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                if authoritative_user
                else "unknown",
                target_type="model_version",
                action="sign_off",
                decision="PENDING",
            )

            if settings.app_env.lower() == "production":
                if not is_signature_valid:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_SIGNOFF_FAILED",
                        actor=user,
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "role": norm_role,
                            "user": user,
                            "error": "Production promotion rejected: sign-offs lack valid cryptographic signatures.",
                            "action": "sign_off",
                            "decision": "REJECTED_INVALID_SIGNATURE",
                        },
                        actor_type="human_principal"
                        if is_identity_verified
                        else "unverified_operator",
                        actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                        if authoritative_user
                        else "unknown",
                        target_type="model_version",
                        action="sign_off",
                        decision="FAILED",
                    )
                    raise ModelIntegrityError(
                        "Production promotion rejected: sign-offs lack valid cryptographic signatures."
                    )
                if not is_role_verified:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_SIGNOFF_FAILED",
                        actor=user,
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "role": norm_role,
                            "user": user,
                            "error": "Production promotion rejected: sign-off missing verified role authority.",
                            "action": "sign_off",
                            "decision": "REJECTED_UNAUTHORIZED_ROLE",
                        },
                        actor_type="human_principal"
                        if is_identity_verified
                        else "unverified_operator",
                        actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                        if authoritative_user
                        else "unknown",
                        target_type="model_version",
                        action="sign_off",
                        decision="FAILED",
                    )
                    raise ModelIntegrityError(
                        "Production promotion rejected: sign-off missing verified role authority."
                    )

            # Append new sign-off details with mutation ID tracking
            entry["sign_offs"].append(
                {
                    "role": norm_role,
                    "user": user,
                    "user_id": user_id_canonical,
                    "signature": signature,
                    "signature_scheme": "APPLICATION_HMAC_SHA256",
                    "is_signature_valid": is_signature_valid,
                    "is_identity_verified": is_identity_verified,
                    "is_role_verified": is_role_verified,
                    "is_independent_human_authorization": is_identity_verified
                    and is_role_verified
                    and user not in SEEDED_ACCOUNTS,
                    "payload_bound": canonical_payload,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "fairness_score": fairness_score,
                    "bias_metric": bias_metric,
                    "drift_divergence": drift_divergence,
                    "is_revoked": False,
                    "is_active": True,
                    "status": "active",
                    "mutation_id": mutation_id,
                }
            )

            if is_signature_valid:
                if "applied_mutation_ids" not in entry:
                    entry["applied_mutation_ids"] = []
                if mutation_id not in entry["applied_mutation_ids"]:
                    entry["applied_mutation_ids"].append(mutation_id)
                entry["last_mutation_id"] = mutation_id

            # Check if both compliance and ml_engineer have active sign-offs
            active_signoffs = [
                s
                for s in entry["sign_offs"]
                if s.get("is_revoked") is not True
                and s.get("is_active", True) is not False
                and s.get("status", "active") != "revoked"
            ]
            signed_roles = [
                (
                    "compliance"
                    if s["role"] in ("compliance", "compliance_officer")
                    else "ml_engineer"
                )
                for s in active_signoffs
            ]

            promoted_to_champion = False
            filepath = None

            if "compliance" in signed_roles and "ml_engineer" in signed_roles:
                if settings.app_env.lower() == "production":
                    all_sigs_valid = all(
                        s.get("is_signature_valid", False) for s in active_signoffs
                    )
                    all_roles_valid = all(s.get("is_role_verified", False) for s in active_signoffs)
                    if not all_sigs_valid:
                        audit_chain.finalize_pending_mutation(
                            mutation_id=mutation_id,
                            event_type="MODEL_SIGNOFF_FAILED",
                            actor=user,
                            target_id=f"{simulation_id}:v{version}",
                            details={
                                "mutation_id": mutation_id,
                                "mutation_stage": "FAILED",
                                "mutation_committed": False,
                                "intent_event_id": intent_entry.event_id,
                                "simulation_id": simulation_id,
                                "version": version,
                                "error": "Production promotion rejected: sign-offs lack valid cryptographic signatures.",
                                "action": "sign_off",
                                "decision": "FAILED",
                            },
                            actor_type="human_principal"
                            if is_identity_verified
                            else "unverified_operator",
                            actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                            if authoritative_user
                            else "unknown",
                            target_type="model_version",
                            action="sign_off",
                            decision="FAILED",
                        )
                        raise ModelIntegrityError(
                            "Production promotion rejected: sign-offs lack valid cryptographic signatures."
                        )
                    if not all_roles_valid:
                        audit_chain.finalize_pending_mutation(
                            mutation_id=mutation_id,
                            event_type="MODEL_SIGNOFF_FAILED",
                            actor=user,
                            target_id=f"{simulation_id}:v{version}",
                            details={
                                "mutation_id": mutation_id,
                                "mutation_stage": "FAILED",
                                "mutation_committed": False,
                                "intent_event_id": intent_entry.event_id,
                                "simulation_id": simulation_id,
                                "version": version,
                                "error": "Production promotion rejected: sign-off missing verified role authority.",
                                "action": "sign_off",
                                "decision": "FAILED",
                            },
                            actor_type="human_principal"
                            if is_identity_verified
                            else "unverified_operator",
                            actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                            if authoritative_user
                            else "unknown",
                            target_type="model_version",
                            action="sign_off",
                            decision="FAILED",
                        )
                        raise ModelIntegrityError(
                            "Production promotion rejected: sign-off missing verified role authority."
                        )
                    validate_production_admission(entry, context="sign_off")

                # Separation of Approval from Production Activation:
                # Institutional sign-offs record compliance and ML review, establishing
                # approved status and production eligibility, but do NOT directly activate
                # operational champion status or route live traffic.
                # Operational promotion requires separately authorized promote_version().
                entry["is_approved"] = True
                entry["is_production_eligible"] = True
                if entry.get("status") in ("inactive", "candidate"):
                    entry["status"] = "approved"

                promoted_to_champion = False
                filepath = None

            # Authoritative manifest is saved FIRST (Section 9.1)
            try:
                self._save_manifest(simulation_id, manifest)
            except Exception as save_err:
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    logger.critical(
                        "Sign-off mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Leaving audit mutation PENDING for reconciliation.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                elif actual_status == "INDETERMINATE":
                    logger.warning(
                        "Outcome for sign-off mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                else:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_SIGNOFF_FAILED",
                        actor=user,
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "role": norm_role,
                            "user": user,
                            "error": str(save_err),
                            "action": "sign_off",
                            "decision": "FAILED",
                        },
                        actor_type="human_principal"
                        if is_identity_verified
                        else "unverified_operator",
                        actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                        if authoritative_user
                        else "unknown",
                        target_type="model_version",
                        action="sign_off",
                        decision="FAILED",
                    )
                raise

            # Finalize sign-off audit event as COMMITTED
            audit_chain.finalize_pending_mutation(
                mutation_id=mutation_id,
                event_type="MODEL_SIGNOFF_RECORDED",
                actor=user,
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "COMMITTED",
                    "mutation_committed": True,
                    "intent_event_id": intent_entry.event_id,
                    "simulation_id": simulation_id,
                    "version": version,
                    "role": norm_role,
                    "user": user,
                    "user_id": user_id_canonical,
                    "signature": signature,
                    "artifact_digest": entry.get("sha256"),
                    "action": "sign_off",
                    "decision": "APPROVED",
                    "promoted_to_champion": promoted_to_champion,
                },
                actor_type="human_principal" if is_identity_verified else "unverified_operator",
                actor_institution=getattr(authoritative_user, "bank_id", "unknown")
                if authoritative_user
                else "unknown",
                target_type="model_version",
                action="sign_off",
                decision="APPROVED",
            )

            if promoted_to_champion and filepath:
                try:
                    self._update_global_model_link(simulation_id, filepath)
                except Exception as err:
                    logger.error("Failed to update global link after signoff: %s", err)

            if auto_promote and entry.get("is_approved"):
                return self.promote_version(
                    simulation_id=simulation_id,
                    version=version,
                    target_status="champion",
                    auth_token=auth_token,
                    actor=user,
                )

            return entry

    def revoke_sign_off(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str = "",
        reason: str = "Unspecified",
        revoked_by: str | None = None,
        auth_token: str | None = None,
        internal_context: TrustedInternalContext | None = None,
    ) -> dict[str, Any]:
        """Revoke an approval on a model version, preserving historical evidence and demoting champion."""
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            norm_role = (
                "compliance" if role in ("compliance", "compliance_officer") else "ml_engineer"
            )
            effective_user = user or (revoked_by if not auth_token else "") or ""

            target_sign_off = None
            for s in entry.get("sign_offs", []):
                s_role = (
                    "compliance"
                    if s.get("role") in ("compliance", "compliance_officer")
                    else "ml_engineer"
                )
                if (
                    s_role == norm_role
                    and not s.get("is_revoked", False)
                    and s.get("status", "active") != "revoked"
                    and (
                        not effective_user
                        or s.get("user") == effective_user
                        or effective_user in (s.get("user_id"), "")
                    )
                ):
                    target_sign_off = s
                    break

            if target_sign_off is None:
                raise ValueError(f"No sign-off found for role '{role}' on version {version}")

            from app.config import get_settings

            settings = get_settings()
            from app.infrastructure.security.auth_service import AuthenticationService

            auth_svc = AuthenticationService.get_instance()

            verified_actor = None
            actor_type = "human_principal"
            actor_institution = "unknown"
            authority_type = "unauthenticated"

            if auth_token:
                is_valid_tok, tok_payload, tok_err = auth_svc.verify_access_token(auth_token)
                if not is_valid_tok or not tok_payload:
                    raise ModelIntegrityError(
                        f"Sign-off revocation authentication failed: {tok_err}"
                    )
                tok_user = (tok_payload.get("username") or tok_payload.get("sub") or "").strip()
                tok_bank = (tok_payload.get("bank_id") or "").lower().strip()
                u_rec = auth_svc.get_user(tok_user)
                if (
                    not u_rec
                    or not u_rec.is_active
                    or getattr(u_rec, "status", "active").lower() != "active"
                ):
                    raise ModelIntegrityError(
                        f"Revoking user '{tok_user}' is not an active authoritative user."
                    )

                user_bank = (getattr(u_rec, "bank_id", "") or "").lower().strip()
                if tok_bank and user_bank and tok_bank != user_bank:
                    raise ModelAuthorizationError(
                        f"Revocation token bank claim '{tok_bank}' does not match authoritative institution '{user_bank}'."
                    )

                # Authoritative institutional ownership authorization (DEFECT 1)
                self._verify_institutional_governance_authority(
                    simulation_id, u_rec, "model sign-off revocation"
                )

                user_roles = {r.lower().strip() for r in getattr(u_rec, "roles", [])}
                user_clearance = getattr(u_rec, "clearance_level", 1)

                if "observer" in user_roles and not (user_roles - {"observer"}):
                    raise ModelAuthorizationError(
                        f"Principal '{tok_user}' with observer role is not authorized to revoke model sign-offs."
                    )

                # Distinguish original signatory vs supervisory compliance/governance intervention
                orig_user = str(target_sign_off.get("user", "")).lower().strip()
                orig_uid = str(target_sign_off.get("user_id", "")).strip()
                is_orig = tok_user.lower() == orig_user or (
                    getattr(u_rec, "user_id", None) and u_rec.user_id == orig_uid
                )

                if is_orig:
                    if user_clearance < 1:
                        raise ModelAuthorizationError(
                            f"Principal '{tok_user}' clearance level {user_clearance} is insufficient to self-revoke sign-off."
                        )
                    allowed_roles = {
                        "admin",
                        "ml_lead",
                        "ml_engineer",
                        "compliance",
                        "compliance_officer",
                    }
                    if not (user_roles & allowed_roles):
                        raise ModelAuthorizationError(
                            f"Principal '{tok_user}' lacks authorized role to self-revoke sign-off."
                        )
                else:
                    supervisory_roles = {"admin", "compliance", "compliance_officer", "ml_lead"}
                    if not (user_roles & supervisory_roles):
                        raise ModelAuthorizationError(
                            f"Principal '{tok_user}' lacking compliance or lead role authority cannot revoke another user's sign-off."
                        )
                    if user_clearance < 2:
                        raise ModelAuthorizationError(
                            f"Principal '{tok_user}' clearance level {user_clearance} is insufficient for supervisory sign-off revocation (minimum clearance level 2 required)."
                        )

                verified_actor = tok_user
                actor_type = "human_principal"
                actor_institution = user_bank or tok_bank or "unknown"
                authority_type = "bearer_jwt"
            elif internal_context is not None:
                sim_ownership = self.get_simulation_ownership(simulation_id)
                owning_bank = sim_ownership.get("owning_bank_id")
                expected_res_id = (
                    f"{simulation_id}:v{version}"
                    if getattr(internal_context, "resource_id", "") == f"{simulation_id}:v{version}"
                    else simulation_id
                )
                expected_res_type = (
                    "model_version"
                    if getattr(internal_context, "resource_type", "") == "model_version"
                    else "model"
                )
                is_valid_ctx, ctx_reason = verify_trusted_internal_context(
                    internal_context,
                    expected_action="revoke_sign_off",
                    expected_service_name=internal_context.service_name,
                    expected_resource_type=expected_res_type,
                    expected_resource_id=expected_res_id,
                    expected_model_version=version,
                    expected_bank_id=owning_bank,
                    consume_nonce=True,
                )
                if not is_valid_ctx:
                    raise ModelIntegrityError(
                        f"Internal context verification failed for revoke_sign_off: {ctx_reason}"
                    )
                verified_actor = f"internal:{internal_context.service_name}"
                actor_type = "internal_service"
                actor_institution = getattr(internal_context, "bank_id", "system") or "system"
                authority_type = "trusted_internal_context"
            elif settings.app_env.lower() == "production":
                raise ModelIntegrityError(
                    "Production sign-off revocation rejected: authenticated session token or trusted internal context is required."
                )
            else:
                effective_revoker = revoked_by or user or "unverified_operator"
                u_rec = auth_svc.get_user(effective_revoker)
                if u_rec is not None:
                    self._verify_institutional_governance_authority(
                        simulation_id, u_rec, "model sign-off revocation"
                    )
                    user_roles = {r.lower().strip() for r in getattr(u_rec, "roles", [])}
                    user_clearance = getattr(u_rec, "clearance_level", 1)
                    if "observer" in user_roles and not (user_roles - {"observer"}):
                        raise ModelAuthorizationError(
                            f"Principal '{effective_revoker}' with observer role is not authorized."
                        )
                    orig_user = str(target_sign_off.get("user", "")).lower().strip()
                    is_orig = effective_revoker.lower() == orig_user or (
                        getattr(u_rec, "user_id", None)
                        and u_rec.user_id == str(target_sign_off.get("user_id", ""))
                    )
                    if not is_orig:
                        if not (
                            user_roles & {"admin", "compliance", "compliance_officer", "ml_lead"}
                        ):
                            raise ModelAuthorizationError(
                                f"Principal '{effective_revoker}' lacks supervisory role authority to revoke another user's sign-off."
                            )
                        if user_clearance < 2:
                            raise ModelAuthorizationError(
                                f"Principal '{effective_revoker}' clearance {user_clearance} is insufficient for supervisory revocation."
                            )
                verified_actor = effective_revoker
                actor_type = "unverified_operator"
                actor_institution = getattr(u_rec, "bank_id", "unknown") if u_rec else "unknown"
                authority_type = "unauthenticated_caller"

            # HISTORICAL PRESERVATION: Do NOT delete revoked sign-offs from entry["sign_offs"]!
            target_sign_off["is_revoked"] = True
            target_sign_off["status"] = "revoked"
            target_sign_off["is_active"] = False
            target_sign_off["revoked_at"] = datetime.now(UTC).isoformat()
            target_sign_off["revoked_by"] = verified_actor
            target_sign_off["revocation_reason"] = reason
            evidence_dict = {
                "actor_type": actor_type,
                "actor_institution": actor_institution,
                "authority_type": authority_type,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if internal_context is not None:
                evidence_dict["service_name"] = internal_context.service_name
                evidence_dict["nonce"] = internal_context.nonce
            target_sign_off["revocation_evidence"] = evidence_dict

            # ALWAYS bump generation upon revocation to invalidate caches
            max_gen = max((e.get("generation", 0) for e in manifest), default=0)
            entry["generation"] = max_gen + 1

            # If dual approval is broken by this revocation, demote approval and eligibility
            remaining_active = [
                s
                for s in entry.get("sign_offs", [])
                if not s.get("is_revoked")
                and s.get("status", "active") != "revoked"
                and s.get("is_active", True) is not False
            ]
            has_remaining_ml = any(
                s.get("role") in ("ml_engineer", "ml_lead") for s in remaining_active
            )
            has_remaining_comp = any(
                s.get("role") in ("compliance", "compliance_officer") for s in remaining_active
            )
            if not (has_remaining_ml and has_remaining_comp):
                entry["is_approved"] = False
                entry["is_production_eligible"] = False
                if entry.get("status") in ("approved", "champion", "challenger"):
                    entry["status"] = "candidate"

            # If this version is champion or active, demote it
            if entry.get("is_active") or entry.get("status") == "champion":
                entry["is_active"] = False
                entry["status"] = "inactive"

            # Emit audit log event BEFORE persisting manifest (Intent stage: PENDING)
            mutation_id = f"mut_rev_{uuid.uuid4().hex}"
            from app.infrastructure.security.immutable_audit_chain import (
                AuditLedgerSecurityError,
                ImmutableAuditChain,
            )

            audit_chain = ImmutableAuditChain.get_instance()
            intent_entry = audit_chain.append_event(
                event_type="MODEL_SIGNOFF_REVOCATION_PENDING",
                actor=verified_actor,
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": version,
                    "role": norm_role,
                    "revoked_by": verified_actor,
                    "reason": reason,
                    "actor_type": actor_type,
                    "actor_institution": actor_institution,
                    "authority_type": authority_type,
                    "action": "revoke_sign_off",
                    "decision": "PENDING",
                },
                actor_type=actor_type,
                actor_institution=actor_institution,
                target_type="model_version",
                action="revoke_sign_off",
                decision="PENDING",
            )

            # Record durable mutation evidence on sign-off and version entry before persistence
            target_sign_off["revocation_mutation_id"] = mutation_id
            if "applied_mutation_ids" not in entry:
                entry["applied_mutation_ids"] = []
            if mutation_id not in entry["applied_mutation_ids"]:
                entry["applied_mutation_ids"].append(mutation_id)
            entry["last_mutation_id"] = mutation_id

            try:
                self._save_manifest(simulation_id, manifest)
            except Exception as save_err:
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    logger.critical(
                        "Sign-off revocation mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Leaving audit mutation PENDING for reconciliation.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                elif actual_status == "INDETERMINATE":
                    logger.warning(
                        "Outcome for sign-off revocation mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                else:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_SIGNOFF_REVOCATION_FAILED",
                        actor=verified_actor,
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "role": norm_role,
                            "revoked_by": verified_actor,
                            "error": str(save_err),
                            "action": "revoke_sign_off",
                            "decision": "FAILED",
                        },
                        actor_type=actor_type,
                        actor_institution=actor_institution,
                        target_type="model_version",
                        action="revoke_sign_off",
                        decision="FAILED",
                    )
                raise

            # Finalize committed audit event
            try:
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type="MODEL_SIGNOFF_REVOCATION",
                    actor=verified_actor,
                    target_id=f"{simulation_id}:v{version}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "COMMITTED",
                        "mutation_committed": True,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "version": version,
                        "role": norm_role,
                        "revoked_by": verified_actor,
                        "reason": reason,
                        "actor_type": actor_type,
                        "actor_institution": actor_institution,
                        "authority_type": authority_type,
                        "action": "revoke_sign_off",
                        "decision": "REVOKED",
                    },
                    actor_type=actor_type,
                    actor_institution=actor_institution,
                    target_type="model_version",
                    action="revoke_sign_off",
                    decision="REVOKED",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to finalize committed audit event for sign-off revocation: %s",
                    audit_err,
                )
                raise AuditLedgerSecurityError(
                    f"Critical audit failure: sign-off revocation for {simulation_id}:v{version} persisted but could not be finalized in audit ledger: {audit_err}"
                ) from audit_err

            # Invalidate caches
            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService

                ModelService(settings).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            return entry

    def revoke_version(
        self,
        simulation_id: str,
        version: int,
        reason: str = "Unspecified",
        auth_token: str | None = None,
        actor: str | None = None,
        internal_context: TrustedInternalContext | None = None,
    ) -> dict[str, Any]:
        """Revoke a model version completely, marking it permanently ineligible for production."""
        sim_dir = self._get_sim_dir(simulation_id)
        file_lock = _get_registry_file_lock(sim_dir)
        with self._lock, file_lock:
            manifest = self._load_manifest(simulation_id)
            entry = next((e for e in manifest if e["version"] == version), None)
            if not entry:
                raise ValueError(f"Version {version} not found in registry for {simulation_id}")

            effective_actor = actor or "system"
            actor_type = "human_principal"
            actor_inst = "unknown"
            if internal_context is not None:
                effective_actor = f"internal:{internal_context.service_name}"
                actor_type = "internal_service"
                actor_inst = getattr(internal_context, "bank_id", "system") or "system"
            elif auth_token:
                from app.infrastructure.security.auth_service import AuthenticationService

                is_valid, claims, _ = AuthenticationService.get_instance().verify_access_token(
                    auth_token
                )
                if is_valid and claims:
                    effective_actor = claims.get("username") or claims.get("sub") or effective_actor
                    actor_inst = claims.get("bank_id") or "unknown"

            entry["is_revoked"] = True
            entry["revocation_reason"] = reason
            entry["revoked_at"] = datetime.now(UTC).isoformat()
            entry["is_production_eligible"] = False

            if entry.get("is_active") or entry.get("status") == "champion":
                entry["is_active"] = False
                entry["status"] = "inactive"
                max_gen = max((e.get("generation", 0) for e in manifest), default=0)
                entry["generation"] = max_gen + 1

            # Emit audit event BEFORE saving manifest (Intent stage: PENDING)
            mutation_id = f"mut_vrev_{uuid.uuid4().hex}"
            from app.infrastructure.security.immutable_audit_chain import (
                AuditLedgerSecurityError,
                ImmutableAuditChain,
            )

            audit_chain = ImmutableAuditChain.get_instance()
            intent_entry = audit_chain.append_event(
                event_type="MODEL_VERSION_REVOCATION_PENDING",
                actor=effective_actor,
                target_id=f"{simulation_id}:v{version}",
                details={
                    "mutation_id": mutation_id,
                    "mutation_stage": "PENDING",
                    "mutation_committed": False,
                    "simulation_id": simulation_id,
                    "version": version,
                    "reason": reason,
                    "action": "revoke_version",
                    "decision": "PENDING",
                },
                actor_type=actor_type,
                actor_institution=actor_inst,
                target_type="model_version",
                action="revoke_version",
                decision="PENDING",
            )

            # Record durable mutation evidence on version entry before persistence
            entry["is_revoked"] = True
            entry["revocation_mutation_id"] = mutation_id
            if "applied_mutation_ids" not in entry:
                entry["applied_mutation_ids"] = []
            if mutation_id not in entry["applied_mutation_ids"]:
                entry["applied_mutation_ids"].append(mutation_id)
            entry["last_mutation_id"] = mutation_id

            try:
                self._save_manifest(simulation_id, manifest)
            except Exception as save_err:
                actual_status = "FAILED"
                if "mutation_id" in locals() and mutation_id:
                    try:
                        actual_status = self.check_version_mutation_status(
                            simulation_id, version, mutation_id
                        )
                    except Exception as check_err:
                        logger.warning(
                            "Could not reliably determine mutation status for '%s' (%s); treating as INDETERMINATE.",
                            mutation_id,
                            check_err,
                        )
                        actual_status = "INDETERMINATE"

                if actual_status == "COMMITTED":
                    logger.critical(
                        "Version revocation mutation '%s' for '%s:v%d' was durably committed to manifest, "
                        "but post-commit operation failed: %s. Leaving audit mutation PENDING for reconciliation.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                elif actual_status == "INDETERMINATE":
                    logger.warning(
                        "Outcome for version revocation mutation '%s' ('%s:v%d') is INDETERMINATE after exception: %s. "
                        "Preserving audit intent without recording a terminal event.",
                        mutation_id,
                        simulation_id,
                        version,
                        save_err,
                    )
                else:
                    audit_chain.finalize_pending_mutation(
                        mutation_id=mutation_id,
                        event_type="MODEL_VERSION_REVOCATION_FAILED",
                        actor=effective_actor,
                        target_id=f"{simulation_id}:v{version}",
                        details={
                            "mutation_id": mutation_id,
                            "mutation_stage": "FAILED",
                            "mutation_committed": False,
                            "intent_event_id": intent_entry.event_id,
                            "simulation_id": simulation_id,
                            "version": version,
                            "error": str(save_err),
                            "action": "revoke_version",
                            "decision": "FAILED",
                        },
                        actor_type=actor_type,
                        actor_institution=actor_inst,
                        target_type="model_version",
                        action="revoke_version",
                        decision="FAILED",
                    )
                raise

            # Finalize committed audit event
            try:
                audit_chain.finalize_pending_mutation(
                    mutation_id=mutation_id,
                    event_type="MODEL_VERSION_REVOCATION",
                    actor=effective_actor,
                    target_id=f"{simulation_id}:v{version}",
                    details={
                        "mutation_id": mutation_id,
                        "mutation_stage": "COMMITTED",
                        "mutation_committed": True,
                        "intent_event_id": intent_entry.event_id,
                        "simulation_id": simulation_id,
                        "version": version,
                        "reason": reason,
                        "action": "revoke_version",
                        "decision": "REVOKED",
                    },
                    actor_type=actor_type,
                    actor_institution=actor_inst,
                    target_type="model_version",
                    action="revoke_version",
                    decision="REVOKED",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to finalize committed audit event for version revocation: %s", audit_err
                )
                raise AuditLedgerSecurityError(
                    f"Critical audit failure: model version revocation for {simulation_id}:v{version} persisted but could not be finalized in audit ledger: {audit_err}"
                ) from audit_err

            from app.config import get_settings

            with contextlib.suppress(Exception):
                from app.application.services.model_service import ModelService

                ModelService(get_settings()).invalidate_model_cache()

            with contextlib.suppress(Exception):
                from app.presentation.routers.realtime_inference import reset_model_cache

                reset_model_cache(simulation_id)

            with contextlib.suppress(Exception):
                from app.presentation.routers.predict import clear_all_serving_caches

                clear_all_serving_caches()

            return entry

    def check_ownership_mutation_status(
        self,
        simulation_id: str,
        mutation_id: str,
    ) -> str:
        """Evaluate authoritative persistence status of an ownership mutation.

        Returns:
            - 'COMMITTED': positively proven present in valid ownership record.
            - 'FAILED': ownership record is valid, readable, and mutation is absent.
            - 'INDETERMINATE': ownership record is corrupt, unreadable, or storage state is ambiguous.
        """
        try:
            ownership = self.get_simulation_ownership(simulation_id)
        except ModelIntegrityError as mie:
            logger.warning(
                "Ownership for '%s' is corrupted or unreadable (%s); status is INDETERMINATE.",
                simulation_id,
                mie,
            )
            return "INDETERMINATE"
        except Exception as exc:
            logger.warning(
                "Failed to load ownership for '%s' (%s); status is INDETERMINATE.",
                simulation_id,
                exc,
            )
            return "INDETERMINATE"

        if ownership.get("last_mutation_id") == mutation_id:
            return "COMMITTED"
        applied = ownership.get("applied_mutation_ids", [])
        if isinstance(applied, list) and mutation_id in applied:
            return "COMMITTED"
        return "FAILED"

    def is_mutation_committed_for_ownership(
        self,
        simulation_id: str,
        mutation_id: str,
    ) -> bool:
        """Check whether an ownership mutation was authoritatively committed to disk."""
        return self.check_ownership_mutation_status(simulation_id, mutation_id) == "COMMITTED"

    def check_version_mutation_status(
        self,
        simulation_id: str,
        version: int | None,
        mutation_id: str,
    ) -> str:
        """Evaluate authoritative persistence status of a version mutation.

        Returns:
            - 'COMMITTED': positively proven present in valid authoritative manifest.
            - 'FAILED': authoritative manifest is valid, readable, and mutation is absent.
            - 'INDETERMINATE': manifest is corrupt, unreadable, or storage state is ambiguous.
        """
        try:
            clean_sim_id = validate_simulation_id(simulation_id)
            with ModelRegistry._manifest_cache_guard:
                ModelRegistry._shared_manifest_cache.pop(clean_sim_id, None)
        except Exception:
            pass
        try:
            manifest = self._load_manifest(simulation_id)
        except ModelIntegrityError as mie:
            logger.warning(
                "Registry manifest for '%s' is corrupted or unreadable (%s); status is INDETERMINATE.",
                simulation_id,
                mie,
            )
            return "INDETERMINATE"
        except Exception as exc:
            logger.warning(
                "Failed to load manifest for '%s' (%s); status is INDETERMINATE.",
                simulation_id,
                exc,
            )
            return "INDETERMINATE"

        def _is_entry_matching(entry: dict[str, Any]) -> bool:
            if mutation_id in entry.get("applied_mutation_ids", []):
                return True
            if entry.get("last_mutation_id") == mutation_id:
                return True
            if entry.get("registration_mutation_id") == mutation_id:
                return True
            if entry.get("revocation_mutation_id") == mutation_id:
                return True
            for s in entry.get("sign_offs", []):
                # An invalid or rejected signature NEVER proves an active, authorized approval!
                if (
                    s.get("mutation_id") == mutation_id
                    and s.get("is_signature_valid") is True
                    and s.get("is_active", True) is not False
                    and s.get("is_revoked") is not True
                ):
                    return True
                if s.get("revocation_mutation_id") == mutation_id:
                    return True
            return False

        if version is not None:
            entry = next((e for e in manifest if e.get("version") == version), None)
            if entry and _is_entry_matching(entry):
                return "COMMITTED"
            return "FAILED"

        for entry in manifest:
            if _is_entry_matching(entry):
                return "COMMITTED"
        return "FAILED"

    def is_mutation_committed_for_version(
        self,
        simulation_id: str,
        version: int | None,
        mutation_id: str,
    ) -> bool:
        """Check whether a mutation was authoritatively committed for a specific model version."""
        return (
            self.check_version_mutation_status(simulation_id, version, mutation_id) == "COMMITTED"
        )

    def reconcile_incomplete_mutations(
        self,
        simulation_id: str | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Scan audit chain for interrupted mutations and resolve them truthfully based on persisted disk state.

        Guarantees:
        1. Never marks COMMITTED merely because current business state resembles desired target.
           Strictly verifies durable mutation-specific evidence (applied_mutation_ids, last_mutation_id).
        2. Never marks FAILED while originating worker is actively executing. Synchronizes via
           simulation FileLock. If FileLock is held, leaves mutation in indeterminate state.
        3. Recovers abandoned staged or published-but-unregistered artifacts after process termination.
           Never deletes a committed model artifact while cleaning abandoned files.
        4. Distinguishes INDETERMINATE (corrupt or unreadable state) from proven transaction failure (FAILED).
           Never deletes artifacts or appends fabricated terminal events when state is INDETERMINATE.
        5. Synchronizes and repairs derived manifest ownership metadata from authoritative ownership.json.
        6. Uses atomic finalize_pending_mutation to prevent duplicate resolution across racing reconcilers.
        7. Maintains registry-then-audit lock acquisition order to prevent deadlocks.
        """
        from filelock import Timeout

        from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

        audit_chain = ImmutableAuditChain.get_instance()
        pending = audit_chain.get_pending_mutations()
        reconciled = []

        # Clean abandoned staging temp files (.tmp_model_*) across target simulations
        target_sims = [simulation_id] if simulation_id else []
        if not target_sims and os.path.isdir(self.registry_root):
            with contextlib.suppress(OSError):
                target_sims = [
                    d
                    for d in os.listdir(self.registry_root)
                    if os.path.isdir(os.path.join(self.registry_root, d))
                ]
        for s_id in target_sims:
            try:
                s_dir = self._get_sim_dir(s_id, create=False)
                if os.path.isdir(s_dir):
                    s_lock = _get_registry_file_lock(s_dir)
                    with self._lock:
                        try:
                            s_lock.acquire(timeout=lock_timeout_seconds)
                        except Timeout:
                            continue
                        try:
                            for f_name in os.listdir(s_dir):
                                if f_name.startswith(".tmp_model_") and f_name.endswith(".pt"):
                                    with contextlib.suppress(OSError):
                                        os.remove(os.path.join(s_dir, f_name))
                        finally:
                            s_lock.release()
            except Exception:
                pass

        for p in pending:
            details = p.details or {}
            mut_id = details.get("mutation_id")
            sim_id = details.get("simulation_id")
            if not mut_id or not isinstance(mut_id, str) or not sim_id:
                continue
            if simulation_id and sim_id != simulation_id:
                continue

            ver = details.get("version")
            action = str(details.get("action", ""))

            sim_dir = self._get_sim_dir(sim_id)
            file_lock = _get_registry_file_lock(sim_dir)

            with self._lock:
                try:
                    file_lock.acquire(timeout=lock_timeout_seconds)
                except Timeout:
                    logger.warning(
                        "Simulation '%s' lock actively held by worker; mutation '%s' remains unresolved.",
                        sim_id,
                        mut_id,
                    )
                    reconciled.append(
                        {"mutation_id": mut_id, "status": "INDETERMINATE_IN_PROGRESS"}
                    )
                    continue

                try:
                    # Re-check pending status under lock
                    if not audit_chain.is_mutation_pending(mut_id):
                        reconciled.append({"mutation_id": mut_id, "status": "ALREADY_RESOLVED"})
                        continue

                    # Clean abandoned staging temp files (.tmp_model_*) while lock is held
                    if os.path.isdir(sim_dir):
                        for f_name in os.listdir(sim_dir):
                            if f_name.startswith(".tmp_model_") and f_name.endswith(".pt"):
                                with contextlib.suppress(OSError):
                                    os.remove(os.path.join(sim_dir, f_name))

                    # 1. Ownership mutations
                    if action in (
                        "bind_simulation_ownership",
                        "transfer_ownership",
                    ) or p.event_type.startswith("MODEL_OWNERSHIP_"):
                        own_status = self.check_ownership_mutation_status(sim_id, mut_id)
                        if own_status == "INDETERMINATE":
                            logger.warning(
                                "Ownership state for '%s' (mutation '%s') is INDETERMINATE; skipping reconciliation.",
                                sim_id,
                                mut_id,
                            )
                            reconciled.append({"mutation_id": mut_id, "status": "INDETERMINATE"})
                            continue

                        is_transfer = "transfer" in action or "TRANSFER" in p.event_type
                        if own_status == "COMMITTED":
                            # Defect 4: Ensure derived manifest ownership metadata matches authoritative ownership.json!
                            try:
                                ownership_record = self.get_simulation_ownership(sim_id)
                                target_bank = ownership_record.get("owning_bank_id")
                                target_consortium = ownership_record.get("consortium_id")
                                cur_manifest = self._load_manifest(sim_id)
                                if cur_manifest:
                                    needs_manifest_sync = False
                                    for m_entry in cur_manifest:
                                        if (
                                            m_entry.get("owning_bank_id") != target_bank
                                            or m_entry.get("consortium_id") != target_consortium
                                        ):
                                            m_entry["owning_bank_id"] = target_bank
                                            m_entry["consortium_id"] = target_consortium
                                            needs_manifest_sync = True
                                    if needs_manifest_sync:
                                        self._save_manifest(sim_id, cur_manifest)
                                        logger.info(
                                            "Repaired derived manifest ownership metadata for '%s' to match authoritative ownership (bank=%s, consortium=%s)",
                                            sim_id,
                                            target_bank,
                                            target_consortium,
                                        )
                            except Exception as sync_err:
                                logger.error(
                                    "Failed to sync manifest ownership during reconciliation for %s: %s",
                                    sim_id,
                                    sync_err,
                                )
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "INDETERMINATE"}
                                )
                                continue

                            event_type = (
                                "MODEL_OWNERSHIP_TRANSFER"
                                if is_transfer
                                else "MODEL_OWNERSHIP_ASSIGNMENT"
                            )
                            decision = "OWNERSHIP_TRANSFERRED" if is_transfer else "OWNERSHIP_BOUND"
                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type=event_type,
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "COMMITTED",
                                    "mutation_committed": True,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "decision": decision,
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision=decision,
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "COMMITTED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        else:
                            event_type = (
                                "MODEL_OWNERSHIP_TRANSFER_FAILED"
                                if is_transfer
                                else "MODEL_OWNERSHIP_ASSIGNMENT_FAILED"
                            )
                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type=event_type,
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "FAILED",
                                    "mutation_committed": False,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "error": "Process terminated prior to authoritative ownership persistence completion",
                                    "decision": "FAILED",
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision="FAILED",
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "FAILED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        continue

                    # 2. Registration mutations
                    if action in ("save_version", "register_version") or p.event_type.startswith(
                        "MODEL_REGISTRATION_"
                    ):
                        reg_status = self.check_version_mutation_status(sim_id, ver, mut_id)
                        if reg_status == "INDETERMINATE":
                            # Defect 3: Indeterminate state!
                            # NEVER delete candidate artifact!
                            # NEVER append a fabricated terminal event!
                            logger.warning(
                                "Authoritative manifest state for '%s' (mutation '%s') is INDETERMINATE. "
                                "Preserving candidate artifacts and audit evidence for future investigation.",
                                sim_id,
                                mut_id,
                            )
                            reconciled.append({"mutation_id": mut_id, "status": "INDETERMINATE"})
                            continue

                        if reg_status == "COMMITTED":
                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type="MODEL_VERSION_CREATED",
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "COMMITTED",
                                    "mutation_committed": True,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "decision": "VERSION_CREATED",
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision="VERSION_CREATED",
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "COMMITTED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        else:
                            # Registration was NOT committed to manifest!
                            # Clean up abandoned published-but-unregistered artifact file if present on disk
                            # MUST NOT delete committed artifacts of any version!
                            try:
                                manifest = self._load_manifest(sim_id)
                            except Exception:
                                manifest = []
                            committed_filenames = {
                                e.get("filename") for e in manifest if e.get("filename")
                            }
                            candidate_fname = f"model_v{ver}.pt" if ver else None
                            if candidate_fname and candidate_fname not in committed_filenames:
                                candidate_path = os.path.join(sim_dir, candidate_fname)
                                if os.path.exists(candidate_path):
                                    with contextlib.suppress(OSError):
                                        os.remove(candidate_path)
                                        logger.info(
                                            "Cleaned up abandoned uncommitted model artifact: %s",
                                            candidate_path,
                                        )

                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type="MODEL_REGISTRATION_FAILED",
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "FAILED",
                                    "mutation_committed": False,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "error": "Process terminated prior to authoritative manifest registration completion",
                                    "decision": "FAILED",
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision="FAILED",
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "FAILED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        continue

                    # 3. Sign-off mutations
                    if action == "sign_off" or p.event_type.startswith("MODEL_SIGNOFF_PENDING"):
                        sign_status = self.check_version_mutation_status(sim_id, ver, mut_id)
                        if sign_status == "INDETERMINATE":
                            logger.warning(
                                "Authoritative manifest state for '%s' (mutation '%s') is INDETERMINATE; skipping sign-off reconciliation.",
                                sim_id,
                                mut_id,
                            )
                            reconciled.append({"mutation_id": mut_id, "status": "INDETERMINATE"})
                            continue

                        if sign_status == "COMMITTED":
                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type="MODEL_SIGNOFF_RECORDED",
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "COMMITTED",
                                    "mutation_committed": True,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "decision": "APPROVED",
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision="APPROVED",
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "COMMITTED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        else:
                            final_entry = audit_chain.finalize_pending_mutation(
                                mutation_id=mut_id,
                                event_type="MODEL_SIGNOFF_FAILED",
                                actor=p.actor,
                                target_id=p.target_id,
                                details={
                                    **details,
                                    "mutation_stage": "FAILED",
                                    "mutation_committed": False,
                                    "intent_event_id": p.event_id,
                                    "reconciled": True,
                                    "error": "Process terminated prior to authoritative manifest sign-off persistence completion",
                                    "decision": "FAILED",
                                },
                                actor_type=p.actor_type,
                                actor_institution=p.actor_institution,
                                target_type=p.target_type,
                                action=action,
                                decision="FAILED",
                            )
                            if final_entry is not None:
                                reconciled.append({"mutation_id": mut_id, "status": "FAILED"})
                            else:
                                reconciled.append(
                                    {"mutation_id": mut_id, "status": "ALREADY_RESOLVED"}
                                )
                        continue

                    # 4. Promotion, Rollback, Revocation
                    other_status = self.check_version_mutation_status(sim_id, ver, mut_id)
                    if other_status == "INDETERMINATE":
                        logger.warning(
                            "Authoritative manifest state for '%s' (mutation '%s') is INDETERMINATE; skipping reconciliation.",
                            sim_id,
                            mut_id,
                        )
                        reconciled.append({"mutation_id": mut_id, "status": "INDETERMINATE"})
                        continue

                    if other_status == "COMMITTED":
                        if action == "revoke_sign_off":
                            event_type, decision = "MODEL_SIGNOFF_REVOCATION", "REVOKED"
                        elif action == "promote_version":
                            event_type, decision = "MODEL_PROMOTION", "PROMOTED"
                        elif action == "rollback":
                            event_type, decision = "MODEL_ROLLBACK", "ROLLED_BACK"
                        else:
                            event_type, decision = "MODEL_VERSION_REVOCATION", "REVOKED"

                        final_entry = audit_chain.finalize_pending_mutation(
                            mutation_id=mut_id,
                            event_type=event_type,
                            actor=p.actor,
                            target_id=p.target_id,
                            details={
                                **details,
                                "mutation_stage": "COMMITTED",
                                "mutation_committed": True,
                                "intent_event_id": p.event_id,
                                "reconciled": True,
                                "decision": decision,
                            },
                            actor_type=p.actor_type,
                            actor_institution=p.actor_institution,
                            target_type=p.target_type,
                            action=action,
                            decision=decision,
                        )
                        if final_entry is not None:
                            reconciled.append({"mutation_id": mut_id, "status": "COMMITTED"})
                        else:
                            reconciled.append({"mutation_id": mut_id, "status": "ALREADY_RESOLVED"})
                    else:
                        if action == "revoke_sign_off":
                            event_type = "MODEL_SIGNOFF_REVOCATION_FAILED"
                        elif action == "promote_version":
                            event_type = "MODEL_PROMOTION_FAILED"
                        elif action == "rollback":
                            event_type = "MODEL_ROLLBACK_FAILED"
                        else:
                            event_type = "MODEL_VERSION_REVOCATION_FAILED"

                        final_entry = audit_chain.finalize_pending_mutation(
                            mutation_id=mut_id,
                            event_type=event_type,
                            actor=p.actor,
                            target_id=p.target_id,
                            details={
                                **details,
                                "mutation_stage": "FAILED",
                                "mutation_committed": False,
                                "intent_event_id": p.event_id,
                                "reconciled": True,
                                "error": "Process terminated prior to authoritative business persistence completion",
                                "decision": "FAILED",
                            },
                            actor_type=p.actor_type,
                            actor_institution=p.actor_institution,
                            target_type=p.target_type,
                            action=action,
                            decision="FAILED",
                        )
                        if final_entry is not None:
                            reconciled.append({"mutation_id": mut_id, "status": "FAILED"})
                        else:
                            reconciled.append({"mutation_id": mut_id, "status": "ALREADY_RESOLVED"})
                finally:
                    file_lock.release()

        return reconciled


class ModelEvaluationEngine:
    """Tracks model scoring performance, shadow latency, and triggers auto-promotion/rollbacks."""

    def __init__(self, registry: ModelRegistry) -> None:
        from app.infrastructure.redis_store import RedisStore

        self.registry = registry
        self._store = RedisStore("model_evaluation", strict_production=True)
        self._lock = threading.RLock()

    def log_prediction(
        self,
        simulation_id: str,
        transaction_id: str,
        champion_version: int | None = None,
        champion_prob: float | None = None,
        champion_latency_ms: float | None = None,
        challenger_version: int | None = None,
        challenger_prob: float | None = None,
        challenger_latency_ms: float | None = None,
        routed_to: str = "champion",
        bank_id: str | None = None,
    ) -> None:
        """Log scoring details for a transaction.

        Thread-safe and process-safe: executes via atomic Redis Lua script across workers,
        and thread-safe in-memory store in local development mode.
        Fails closed in production if shared Redis publication fails.
        """
        norm_bank = bank_id.lower().replace("-", "_").strip() if bank_id else None
        payload = {
            "champion_version": champion_version,
            "champion_prob": champion_prob,
            "champion_latency_ms": champion_latency_ms,
            "challenger_version": challenger_version,
            "challenger_prob": challenger_prob,
            "challenger_latency_ms": challenger_latency_ms,
            "actual_label": None,
            "routed_to": routed_to,
            "bank_id": norm_bank,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._store.record_prediction_evidence(
                simulation_id=simulation_id,
                transaction_id=transaction_id,
                payload=payload,
                bank_id=norm_bank,
                max_window_size=1000,
            )

    def get_prediction_record(
        self,
        simulation_id: str,
        transaction_id: str,
        bank_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve stored prediction evidence for a transaction with institutional scoping."""
        with self._lock:
            return self._store.get_prediction_evidence(
                simulation_id=simulation_id,
                transaction_id=transaction_id,
                bank_id=bank_id,
            )

    def log_feedback(
        self,
        simulation_id: str,
        transaction_id: str,
        actual_label: int,
        target_challenger_version: int | None = None,
        bank_id: str | None = None,
    ) -> dict[str, Any]:
        """Record the ground truth label for a transaction and evaluate performance.

        Raises:
            KeyError: If transaction prediction was not previously logged (Zero-Mock Invariant).
            PermissionError: If record's institutional ownership mismatches caller or lacks binding in production.
        """
        with self._lock:
            res = self._store.record_feedback(
                simulation_id=simulation_id,
                transaction_id=transaction_id,
                actual_label=actual_label,
                bank_id=bank_id,
            )
            if not res.get("found"):
                raise KeyError(
                    f"Prediction for transaction '{transaction_id}' not found in evaluation store. "
                    "Cannot log feedback for unscored transactions (Zero-Mock Invariant)."
                )
            if res.get("mismatch"):
                err_type = res.get("error")
                if err_type == "missing_owner":
                    raise PermissionError(
                        f"Prediction for transaction '{transaction_id}' lacks authoritative institutional tenant binding."
                    )
                raise PermissionError(
                    f"Cross-tenant feedback rejected: institutional owner mismatch for transaction '{transaction_id}'."
                )

            return self.evaluate_performance(
                simulation_id, target_challenger_version=target_challenger_version
            )

    def evaluate_performance(
        self,
        simulation_id: str,
        target_challenger_version: int | None = None,
    ) -> dict[str, Any]:
        """Compute metrics over logged predictions with labels and check routing rules."""
        with self._lock:
            from app.config import get_settings

            _settings = get_settings()
            is_prod_eval = (
                getattr(_settings, "app_env", "development").lower().strip() == "production"
            )
            if is_prod_eval and not self._store.has_active_shared_storage():
                active_v = self.registry.get_active_version(simulation_id)
                active_num = active_v["version"] if active_v else 1
                return {
                    "status": "unavailable",
                    "sample_count": 0,
                    "champion_version": active_num,
                    "traffic_share": 0.0,
                    "promotion_triggered": False,
                    "promotion_message": "Evaluation unavailable: shared authoritative evaluation storage (Redis) is degraded or unreachable in production.",
                    "rollback_triggered": False,
                    "rollback_message": "",
                }

            list_key = f"{simulation_id}:prediction_keys"
            keys = self._store.get(list_key) or []
            if not keys:
                return {}

            records = []
            for k in keys:
                rec = self._store.get(k)
                if rec and rec.get("actual_label") is not None:
                    records.append(rec)

            # Retrieve traffic share and active canary target version
            share_key = f"{simulation_id}:challenger_traffic_share"
            traffic_version_key = f"{simulation_id}:challenger_traffic_version"
            raw_share = self._store.get(share_key)
            traffic_share = float(raw_share) if raw_share is not None else 0.0
            raw_canary_ver = self._store.get(traffic_version_key)
            canary_target_version = int(raw_canary_ver) if raw_canary_ver is not None else None

            # Get active/champion version
            active_version = self.registry.get_active_version(simulation_id)
            active_ver_num = active_version["version"] if active_version else 1

            if len(records) < 5:  # Require small warmup size for local testing/simulations
                return {
                    "status": "warmup",
                    "sample_count": len(records),
                    "champion_version": active_ver_num,
                    "traffic_share": traffic_share,
                    "promotion_triggered": False,
                    "promotion_message": "Evaluation warmup in progress (< 5 samples).",
                }

            from app.domain.metrics_service import safe_pr_auc_score, safe_roc_auc_score

            # Global descriptive champion metrics (for fleet monitoring & health)
            champ_records = [
                r
                for r in records
                if r.get("champion_prob") is not None
                and (
                    r.get("champion_version") == active_ver_num or r.get("champion_version") is None
                )
            ]
            y_true_champ = [r["actual_label"] for r in champ_records]
            y_pred_champ = [r["champion_prob"] for r in champ_records]
            champ_latencies = [
                r["champion_latency_ms"]
                for r in champ_records
                if r.get("champion_latency_ms") is not None
            ]
            avg_champ_latency = (
                sum(champ_latencies) / len(champ_latencies) if champ_latencies else 0.0
            )

            champ_auc = (
                safe_roc_auc_score(y_true_champ, y_pred_champ) if len(champ_records) >= 5 else None
            )
            champ_pr_auc = (
                safe_pr_auc_score(y_true_champ, y_pred_champ) if len(champ_records) >= 5 else None
            )

            champ_fp = 0
            champ_tn = 0
            for r in champ_records:
                if r["actual_label"] == 0:
                    if r["champion_prob"] >= 0.5:
                        champ_fp += 1
                    else:
                        champ_tn += 1
            champ_fpr = champ_fp / (champ_fp + champ_tn) if (champ_fp + champ_tn) > 0 else 0.0

            # Rollback Gating:
            rollback_triggered = False
            rollback_message = ""
            has_both_classes = len(set(y_true_champ)) >= 2
            if (
                (has_both_classes and champ_auc is not None and champ_auc < 0.65)
                or avg_champ_latency > 200.0
                or champ_fpr > 0.05
            ):
                from app.config import get_settings

                settings = get_settings()
                sim_dir = self.registry._get_sim_dir(simulation_id)
                verifier = self.registry._verifier
                if verifier is None:
                    try:
                        verifier = get_default_training_verifier()
                    except Exception as verifier_err:
                        logger.warning(
                            "Could not initialize default training verifier: %s", verifier_err
                        )
                        verifier = None
                manifest = self.registry._load_manifest(simulation_id)

                eligible_versions = []
                for v in manifest:
                    ver_num = v.get("version", 0)
                    if ver_num >= active_ver_num:
                        continue
                    if v.get("is_revoked") is True or v.get("status") == "revoked":
                        continue
                    if (
                        settings.app_env.lower() == "production"
                        and v.get("is_production_eligible") is False
                    ):
                        continue
                    fname = v.get("filename")
                    if not fname:
                        continue
                    filepath = os.path.join(sim_dir, fname)
                    if not (os.path.isfile(filepath) and os.path.getsize(filepath) > 0):
                        continue
                    if v.get("sha256"):
                        actual_sha = compute_file_sha256(filepath)
                        if not hmac.compare_digest(actual_sha, v["sha256"]):
                            continue
                    rcpt = v.get("training_receipt")
                    if isinstance(rcpt, dict) and rcpt.get("key_id"):
                        try:
                            if not verifier or not verifier.is_key_trusted(rcpt["key_id"]):
                                continue
                        except Exception:
                            continue
                    elif settings.app_env.lower() == "production":
                        # In production, receipt is strictly mandatory
                        continue
                    if settings.app_env.lower() == "production":
                        sign_offs = [
                            s
                            for s in v.get("sign_offs", [])
                            if isinstance(s, dict)
                            and not s.get("is_revoked")
                            and s.get("status", "active") != "revoked"
                        ]
                        has_ml = any(s.get("role") in ("ml_engineer", "ml_lead") for s in sign_offs)
                        has_comp = any(
                            s.get("role") in ("compliance", "compliance_officer") for s in sign_offs
                        )
                        if not (has_ml and has_comp):
                            continue
                    eligible_versions.append(v)

                if eligible_versions:
                    stable_version = max(eligible_versions, key=lambda x: x["version"])
                    target_ver = stable_version["version"]
                    ownership = self.registry.get_simulation_ownership(simulation_id)
                    owning_bank = ownership.get("owning_bank_id")
                    trusted_ctx = create_trusted_internal_context(
                        "model_evaluation_engine",
                        action="rollback",
                        resource_type="model",
                        resource_id=simulation_id,
                        model_version=target_ver,
                        bank_id=owning_bank,
                    )
                    rollback_committed = False
                    try:
                        self.registry.rollback(
                            simulation_id,
                            target_ver,
                            trusted_context=trusted_ctx,
                        )
                        # Authoritative verification of post-rollback state
                        active_after_rb = self.registry.get_active_version(simulation_id)
                        if active_after_rb and active_after_rb.get("version") == target_ver:
                            rollback_committed = True
                            active_ver_num = target_ver
                        else:
                            rollback_committed = False
                            rollback_triggered = False
                            rollback_message = (
                                f"Rollback execution completed but authoritative champion version is "
                                f"{active_after_rb.get('version') if active_after_rb else 'None'}, expected {target_ver}."
                            )
                            logger.error(rollback_message)
                    except Exception as rb_err:
                        rollback_committed = False
                        rollback_triggered = False
                        rollback_message = f"Rollback to candidate version {target_ver} failed admission/execution: {rb_err}"
                        logger.error(rollback_message)

                    if rollback_committed:
                        rollback_triggered = True
                        traffic_share = 0.0
                        self._store.set(share_key, 0.0)
                        self._store.delete(traffic_version_key)
                        self._store.delete(f"{simulation_id}:canary_start_samples")
                        self._store.delete(f"{simulation_id}:canary_state")

                        auc_str = f"{champ_auc:.4f}" if champ_auc is not None else "None"
                        fpr_str = f"{champ_fpr:.4f}" if champ_fpr is not None else "None"
                        lat_str = f"{avg_champ_latency:.1f}ms"
                        try:
                            rollback_message = (
                                f"Auto-rollback triggered to version {target_ver} due to performance degradation "
                                f"(AUC: {auc_str}, FPR: {fpr_str}, Latency: {lat_str})."
                            )
                            logger.warning(rollback_message)
                        except Exception as fmt_err:
                            rollback_message = f"Auto-rollback committed to version {target_ver} (diagnostics formatting: {fmt_err})."
                            logger.warning(rollback_message)
                else:
                    traffic_share = 0.0
                    self._store.set(share_key, 0.0)
                    self._store.delete(traffic_version_key)
                    self._store.delete(f"{simulation_id}:canary_start_samples")
                    self._store.delete(f"{simulation_id}:canary_state")
                    rollback_message = f"Performance degradation detected on champion v{active_ver_num}, but no safe eligible rollback candidate exists in registry."
                    logger.warning(rollback_message)
                    rollback_triggered = False

            # Challenger Version Resolution & Cohort Isolation
            challenger_versions_present = sorted(
                list(
                    {
                        r["challenger_version"]
                        for r in records
                        if r.get("challenger_version") is not None
                    }
                )
            )

            eval_challenger_ver: int | None = None
            is_ambiguous_window = False

            if target_challenger_version is not None:
                eval_challenger_ver = target_challenger_version
            elif len(challenger_versions_present) == 1:
                eval_challenger_ver = challenger_versions_present[0]
            elif len(challenger_versions_present) > 1:
                if (
                    canary_target_version is not None
                    and canary_target_version in challenger_versions_present
                ):
                    eval_challenger_ver = canary_target_version
                else:
                    is_ambiguous_window = True

            if eval_challenger_ver == active_ver_num:
                eval_challenger_ver = None

            matched_cohort: list[dict[str, Any]] = []
            chall_auc: float | None = None
            chall_pr_auc: float | None = None
            chall_fpr: float | None = None
            paired_champ_pr_auc: float | None = None
            avg_chall_latency: float = 0.0

            if not is_ambiguous_window and eval_challenger_ver is not None:
                matched_cohort = [
                    r
                    for r in records
                    if r.get("champion_version") == active_ver_num
                    and r.get("challenger_version") == eval_challenger_ver
                    and r.get("champion_prob") is not None
                    and r.get("challenger_prob") is not None
                    and r.get("actual_label") is not None
                    and r.get("actual_label") in (0, 1)
                ]

                if matched_cohort:
                    y_true_matched = [r["actual_label"] for r in matched_cohort]
                    y_pred_chall_matched = [r["challenger_prob"] for r in matched_cohort]
                    y_pred_champ_matched = [r["champion_prob"] for r in matched_cohort]
                    chall_latencies = [
                        r["challenger_latency_ms"]
                        for r in matched_cohort
                        if r.get("challenger_latency_ms") is not None
                    ]
                    avg_chall_latency = (
                        sum(chall_latencies) / len(chall_latencies) if chall_latencies else 0.0
                    )

                    if len(matched_cohort) >= 5:
                        chall_auc = safe_roc_auc_score(y_true_matched, y_pred_chall_matched)
                        chall_pr_auc = safe_pr_auc_score(y_true_matched, y_pred_chall_matched)
                        paired_champ_pr_auc = safe_pr_auc_score(
                            y_true_matched, y_pred_champ_matched
                        )

                    # Correct Challenger False-Positive Rate calculation:
                    # Uses challenger-specific false positives and true negatives on matched cohort
                    chall_fp = 0
                    chall_tn = 0
                    for r in matched_cohort:
                        if r["actual_label"] == 0:
                            if r["challenger_prob"] >= 0.5:
                                chall_fp += 1
                            else:
                                chall_tn += 1
                    chall_negatives = chall_fp + chall_tn
                    chall_fpr = chall_fp / chall_negatives if chall_negatives > 0 else None

            # Promotion Gating (Fail-closed on undefined/missing PR-AUC):
            promotion_triggered = False
            promotion_message = ""
            canary_start_key = f"{simulation_id}:canary_start_samples"
            canary_state_key = f"{simulation_id}:canary_state"
            seq_key = f"{simulation_id}:prediction_seq"

            if not rollback_triggered:
                if is_ambiguous_window:
                    promotion_message = f"Challenger promotion deferred: ambiguous mixed challenger versions in evaluation window ({challenger_versions_present})."
                    logger.info(promotion_message)
                elif eval_challenger_ver is not None:
                    canary_state = self._store.get(canary_state_key)

                    # Check for version change occurring during canary evaluation
                    if traffic_share == 0.1:
                        target_mismatch = (
                            canary_target_version is not None
                            and canary_target_version != eval_challenger_ver
                        )
                        state_mismatch = (
                            isinstance(canary_state, dict)
                            and canary_state.get("challenger_version") != eval_challenger_ver
                        )
                        champ_mismatch = (
                            isinstance(canary_state, dict)
                            and canary_state.get("champion_version") != active_ver_num
                        )
                        if target_mismatch or state_mismatch:
                            traffic_share = 0.0
                            self._store.set(share_key, 0.0)
                            self._store.delete(traffic_version_key)
                            self._store.delete(canary_start_key)
                            self._store.delete(canary_state_key)
                            promotion_message = (
                                f"Challenger version changed from v{canary_target_version} to v{eval_challenger_ver} "
                                "during canary evaluation. Resetting canary traffic share to 0.0 and deferring promotion."
                            )
                            logger.warning(promotion_message)
                            promotion_triggered = False
                        elif champ_mismatch:
                            traffic_share = 0.0
                            self._store.set(share_key, 0.0)
                            self._store.delete(traffic_version_key)
                            self._store.delete(canary_start_key)
                            self._store.delete(canary_state_key)
                            promotion_message = (
                                f"Champion version changed from v{canary_state.get('champion_version')} to v{active_ver_num} "
                                "during canary evaluation. Resetting canary traffic share to 0.0 and deferring promotion."
                            )
                            logger.warning(promotion_message)
                            promotion_triggered = False

                    if not promotion_message:
                        if len(matched_cohort) < 5:
                            promotion_message = (
                                f"Challenger v{eval_challenger_ver} promotion evaluation deferred: "
                                f"insufficient matched observations ({len(matched_cohort)} < 5 minimum)."
                            )
                            logger.info(promotion_message)
                        elif chall_pr_auc is None or paired_champ_pr_auc is None:
                            promotion_message = (
                                f"Challenger v{eval_challenger_ver} promotion evaluation deferred: "
                                "PR-AUC undefined on matched cohort (single class or degenerate window)."
                            )
                            logger.info(promotion_message)
                        elif traffic_share == 0.0 and chall_pr_auc > paired_champ_pr_auc:
                            from app.config import get_settings

                            _settings = get_settings()
                            is_prod_eval = (
                                getattr(_settings, "app_env", "development").lower().strip()
                                == "production"
                            )
                            if is_prod_eval and not self._store.has_active_shared_storage():
                                promotion_message = (
                                    f"Challenger v{eval_challenger_ver} canary activation deferred: "
                                    "shared authoritative evaluation storage (Redis) is unavailable in production. "
                                    "Canary traffic cannot be routed using isolated worker-local evidence."
                                )
                                logger.warning(promotion_message)
                                promotion_triggered = False
                            else:
                                traffic_share = 0.1
                                self._store.set(share_key, 0.1)
                                self._store.set(traffic_version_key, eval_challenger_ver)
                                current_seq = int(self._store.get(seq_key) or 0)
                                new_canary_state = {
                                    "activation_id": uuid.uuid4().hex,
                                    "challenger_version": eval_challenger_ver,
                                    "champion_version": active_ver_num,
                                    "activation_seq": current_seq,
                                    "activation_time": datetime.now(UTC).isoformat(),
                                }
                                self._store.set(canary_state_key, new_canary_state)
                                self._store.set(canary_start_key, current_seq)
                                promotion_message = (
                                    f"Challenger v{eval_challenger_ver} (PR-AUC: {chall_pr_auc:.4f}) outperformed "
                                    f"Champion v{active_ver_num} (PR-AUC: {paired_champ_pr_auc:.4f}) on matched cohort. "
                                    f"Routed 10% traffic to Challenger v{eval_challenger_ver}."
                                )
                                logger.info(promotion_message)
                                promotion_triggered = True
                        elif traffic_share == 0.1:
                            if (
                                not isinstance(canary_state, dict)
                                or "activation_seq" not in canary_state
                            ):
                                traffic_share = 0.0
                                self._store.set(share_key, 0.0)
                                self._store.delete(traffic_version_key)
                                self._store.delete(canary_start_key)
                                self._store.delete(canary_state_key)
                                promotion_message = (
                                    f"Challenger v{eval_challenger_ver} canary evaluation deferred: "
                                    "canary activation boundary unavailable or ambiguous."
                                )
                                logger.warning(promotion_message)
                                promotion_triggered = False
                            elif chall_pr_auc > paired_champ_pr_auc:
                                activation_seq = int(canary_state["activation_seq"])

                                # Filter genuine, correctly versioned observations scored strictly post-activation
                                canary_records_by_tx = {}
                                for r in matched_cohort:
                                    tx_id = r.get("transaction_id")
                                    pred_seq = r.get("pred_seq")
                                    if (
                                        tx_id
                                        and pred_seq is not None
                                        and pred_seq > activation_seq
                                        and r.get("champion_version") == active_ver_num
                                        and r.get("challenger_version") == eval_challenger_ver
                                        and r.get("actual_label") in (0, 1)
                                    ):
                                        canary_records_by_tx[tx_id] = r

                                canary_samples = len(canary_records_by_tx)

                                # Inspect canary policy for required real challenger routing
                                policy = self._store.get(f"{simulation_id}:canary_policy") or {}
                                require_challenger_routing = False
                                min_challenger_routed = 1
                                if isinstance(policy, dict):
                                    require_challenger_routing = bool(
                                        policy.get("require_challenger_routing", False)
                                    )
                                    min_challenger_routed = int(
                                        policy.get("min_challenger_routed_samples", 1)
                                    )
                                elif isinstance(policy, bool):
                                    require_challenger_routing = policy
                                if not require_challenger_routing:
                                    raw_rcr = self._store.get(
                                        f"{simulation_id}:require_challenger_routing"
                                    )
                                    if raw_rcr:
                                        require_challenger_routing = True

                                routed_challenger_count = sum(
                                    1
                                    for r in canary_records_by_tx.values()
                                    if r.get("routed_to") == "challenger"
                                )

                                if canary_samples < 5:
                                    promotion_message = (
                                        f"Challenger v{eval_challenger_ver} canary evaluation in progress "
                                        f"({canary_samples}/5 canary samples). PR-AUC: {chall_pr_auc:.4f} vs {paired_champ_pr_auc:.4f}."
                                    )
                                    logger.info(promotion_message)
                                    promotion_triggered = False
                                elif (
                                    require_challenger_routing
                                    and routed_challenger_count < min_challenger_routed
                                ):
                                    promotion_message = (
                                        f"Challenger v{eval_challenger_ver} canary evaluation in progress: waiting for real challenger-routed traffic "
                                        f"({routed_challenger_count}/{min_challenger_routed} required routed samples, {canary_samples}/5 total canary samples)."
                                    )
                                    logger.info(promotion_message)
                                    promotion_triggered = False
                                else:
                                    from app.config import get_settings

                                    _settings = get_settings()
                                    is_prod_eval = (
                                        getattr(_settings, "app_env", "development").lower().strip()
                                        == "production"
                                    )
                                    if is_prod_eval and not self._store.has_active_shared_storage():
                                        promotion_message = (
                                            f"Challenger version {eval_challenger_ver} promotion rejected: "
                                            "shared authoritative evaluation storage (Redis) is unavailable in production. "
                                            "Promotion cannot occur using evidence assembled from inconsistent worker-local stores."
                                        )
                                        logger.error(promotion_message)
                                        promotion_triggered = False
                                    else:
                                        active_now = self.registry.get_active_version(simulation_id)
                                        if (
                                            active_now
                                            and active_now.get("version") == eval_challenger_ver
                                        ):
                                            traffic_share = 0.0
                                            self._store.set(share_key, 0.0)
                                            self._store.delete(traffic_version_key)
                                            self._store.delete(canary_start_key)
                                            self._store.delete(canary_state_key)
                                            promotion_message = f"Challenger version {eval_challenger_ver} is already promoted to Champion."
                                            logger.info(promotion_message)
                                            promotion_triggered = True
                                        else:
                                            # Promotion Target is unambiguously eval_challenger_ver after full canary validation
                                            ownership = self.registry.get_simulation_ownership(
                                                simulation_id
                                            )
                                            owning_bank = ownership.get("owning_bank_id")
                                            trusted_ctx = create_trusted_internal_context(
                                                "model_evaluation_engine",
                                                action="promote_version",
                                                resource_type="model",
                                                resource_id=simulation_id,
                                                model_version=eval_challenger_ver,
                                                bank_id=owning_bank,
                                            )
                                            try:
                                                self.registry.promote_version(
                                                    simulation_id,
                                                    eval_challenger_ver,
                                                    target_status="champion",
                                                    trusted_context=trusted_ctx,
                                                )
                                                traffic_share = 0.0
                                                self._store.set(share_key, 0.0)
                                                self._store.delete(traffic_version_key)
                                                self._store.delete(canary_start_key)
                                                self._store.delete(canary_state_key)
                                                promotion_message = (
                                                    f"Challenger version {eval_challenger_ver} promoted to Champion after continued "
                                                    f"outperformance on matched cohort ({canary_samples} canary samples, PR-AUC: {chall_pr_auc:.4f} vs {paired_champ_pr_auc:.4f})."
                                                )
                                                logger.info(promotion_message)
                                                promotion_triggered = True
                                            except Exception as prom_err:
                                                promotion_message = f"Challenger version {eval_challenger_ver} promotion failed admission checks: {prom_err}"
                                                logger.error(promotion_message)
                                                promotion_triggered = False
                            else:
                                traffic_share = 0.0
                                self._store.set(share_key, 0.0)
                                self._store.delete(traffic_version_key)
                                self._store.delete(canary_start_key)
                                self._store.delete(canary_state_key)
                                promotion_message = (
                                    f"Challenger v{eval_challenger_ver} failed canary evaluation "
                                    f"(PR-AUC: {chall_pr_auc:.4f} <= {paired_champ_pr_auc:.4f}). Resetting canary traffic share to 0.0."
                                )
                                logger.warning(promotion_message)
                                promotion_triggered = False

            metrics = {
                "champion_version": active_ver_num,
                "champion_auc": champ_auc,
                "champion_pr_auc": champ_pr_auc,
                "champion_fpr": champ_fpr,
                "champion_latency_ms": avg_champ_latency,
                "challenger_version": eval_challenger_ver,
                "challenger_auc": chall_auc,
                "challenger_pr_auc": chall_pr_auc,
                "challenger_fpr": chall_fpr,
                "challenger_latency_ms": avg_chall_latency,
                "paired_champion_pr_auc": paired_champ_pr_auc,
                "paired_sample_count": len(matched_cohort),
                "traffic_share": traffic_share,
                "sample_count": len(records),
                "rollback_triggered": rollback_triggered,
                "rollback_message": rollback_message,
                "promotion_triggered": promotion_triggered,
                "promotion_message": promotion_message,
            }

            metrics_key = f"{simulation_id}:shadow_metrics"
            self._store.set(metrics_key, metrics)

            from app.presentation.routers.simulation import _simulation_events

            if rollback_triggered:
                try:
                    _simulation_events.push_list(
                        simulation_id,
                        {
                            "event_type": "rollback",
                            "data": {
                                "message": rollback_message,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        },
                    )
                except Exception as evt_err:
                    logger.warning("Failed to publish rollback simulation event: %s", evt_err)
            elif promotion_triggered:
                try:
                    _simulation_events.push_list(
                        simulation_id,
                        {
                            "event_type": "canary",
                            "data": {
                                "message": promotion_message,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        },
                    )
                except Exception as evt_err:
                    logger.warning("Failed to publish canary simulation event: %s", evt_err)

            return metrics

    def get_shadow_metrics(self, simulation_id: str) -> dict[str, Any]:
        """Retrieve the latest computed shadow deployment metrics."""
        metrics_key = f"{simulation_id}:shadow_metrics"
        return self._store.get(metrics_key) or {
            "champion_version": 1,
            "champion_auc": 0.0,
            "champion_pr_auc": 0.0,
            "champion_fpr": 0.0,
            "champion_latency_ms": 0.0,
            "challenger_version": None,
            "challenger_auc": 0.0,
            "challenger_pr_auc": 0.0,
            "challenger_fpr": None,
            "challenger_latency_ms": 0.0,
            "paired_champion_pr_auc": None,
            "paired_sample_count": 0,
            "traffic_share": 0.0,
            "sample_count": 0,
        }

    def get_traffic_share(self, simulation_id: str) -> float:
        """Get the current traffic share routed to the challenger model."""
        share_key = f"{simulation_id}:challenger_traffic_share"
        val = self._store.get(share_key)
        return float(val) if val is not None else 0.0
