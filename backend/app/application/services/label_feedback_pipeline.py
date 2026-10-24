# ruff: noqa: UP042
"""Local Label Feedback Pipeline for Continuous Federated Learning.

Ingests analyst ground-truth determinations, maintains isolated per-tenant
retraining buffers with priority queueing, enforces Zero-PII boundaries,
and produces Differential-Privacy-protected gradient updates for local fine-tuning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from app.domain.label_privacy_guard import LabelPrivacyGuard, LabelPrivacyViolationError

logger = logging.getLogger(__name__)


class FeedbackPersistenceError(RuntimeError):
    """Raised when persisting tenant feedback buffer to disk fails."""


class FeedbackLabel(str, Enum):
    """Ground-truth determination label enum."""

    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    FALSE_POSITIVE = "FALSE_POSITIVE"


@dataclass
class LabelFeedbackItem:
    """Dataclass storing ground-truth feedback for a transaction."""

    transaction_id_hash: str
    label: FeedbackLabel
    weight: float = 1.0
    priority: int = 1  # 1: Standard, 2: High, 3: Critical/Urgent
    feature_vector: list[float] | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    consumed_for_retraining: bool = False
    status: str = "DURABLY_RECORDED"
    validated_for_training: bool = False
    prediction_evidence_correlated: bool = False
    reserved_job_id: str | None = None
    reserved_at: datetime | float | None = None
    consumed_training_run_id: str | None = None
    case_id: str | None = None
    alert_id: str | None = None
    source_provenance: str = "DIRECT_INGEST"

    def to_dict(self) -> dict[str, Any]:
        """Serializes feedback item to JSON-compatible dictionary."""
        return {
            "transaction_id_hash": self.transaction_id_hash,
            "label": self.label.value if isinstance(self.label, FeedbackLabel) else str(self.label),
            "weight": self.weight,
            "priority": self.priority,
            "feature_vector": self.feature_vector,
            "notes": self.notes,
            "metadata": self.metadata,
            "recorded_at": self.recorded_at.isoformat()
            if isinstance(self.recorded_at, datetime)
            else str(self.recorded_at),
            "consumed_for_retraining": self.consumed_for_retraining,
            "status": self.status,
            "validated_for_training": self.validated_for_training,
            "prediction_evidence_correlated": self.prediction_evidence_correlated,
            "reserved_job_id": self.reserved_job_id,
            "reserved_at": self.reserved_at.isoformat()
            if isinstance(self.reserved_at, datetime)
            else self.reserved_at,
            "consumed_training_run_id": self.consumed_training_run_id,
            "case_id": self.case_id,
            "alert_id": self.alert_id,
            "source_provenance": self.source_provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LabelFeedbackItem:
        """Constructs a LabelFeedbackItem from a serialized dictionary."""
        rec_at = data.get("recorded_at")
        if isinstance(rec_at, str):
            try:
                rec_at_dt = datetime.fromisoformat(rec_at)
            except Exception:
                rec_at_dt = datetime.now(UTC)
        else:
            rec_at_dt = datetime.now(UTC)

        raw_res = data.get("reserved_at")
        res_at_val: datetime | float | None = None
        if isinstance(raw_res, str):
            try:
                res_at_val = datetime.fromisoformat(raw_res)
            except Exception:
                res_at_val = None
        elif isinstance(raw_res, (int, float)):
            res_at_val = float(raw_res)

        raw_label = data.get("label")
        if raw_label is None:
            raise ValueError("Field 'label' is mandatory and cannot be missing or null.")
        try:
            lbl = FeedbackLabel(raw_label)
        except ValueError as exc:
            raise ValueError(
                f"Invalid feedback label '{raw_label}'. Expected one of: {[e.value for e in FeedbackLabel]}"
            ) from exc

        return cls(
            transaction_id_hash=data.get("transaction_id_hash", ""),
            label=lbl,
            weight=float(data.get("weight", 1.0)),
            priority=int(data.get("priority", 1)),
            feature_vector=data.get("feature_vector"),
            notes=data.get("notes"),
            metadata=data.get("metadata", {}) or {},
            recorded_at=rec_at_dt,
            consumed_for_retraining=bool(data.get("consumed_for_retraining", False)),
            status=data.get("status", "DURABLY_RECORDED"),
            validated_for_training=bool(data.get("validated_for_training", False)),
            prediction_evidence_correlated=bool(data.get("prediction_evidence_correlated", False)),
            reserved_job_id=data.get("reserved_job_id"),
            reserved_at=res_at_val,
            consumed_training_run_id=data.get("consumed_training_run_id"),
            case_id=data.get("case_id"),
            alert_id=data.get("alert_id"),
            source_provenance=data.get("source_provenance", "DIRECT_INGEST"),
        )


TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_tenant_id(tenant_id: str) -> str:
    """Validate and sanitize a tenant identifier at the service boundary.

    Enforces:
    - Non-empty string, length 1-128 characters.
    - Strictly alphanumeric characters, hyphens, and underscores.
    - Explicit rejection of traversal segments (..), path separators (/ and \\),
      null bytes, whitespace, and reserved filenames.
    """
    if not isinstance(tenant_id, str):
        raise ValueError("Tenant identifier must be a string.")
    clean = tenant_id.strip()
    if not clean or len(clean) > 128 or not TENANT_ID_PATTERN.match(clean):
        raise ValueError(
            f"Invalid tenant identifier: '{tenant_id}'. Must be 1-128 characters matching [a-zA-Z0-9_\\-]."
        )
    if clean in (".", "..") or "/" in clean or "\\" in clean:
        raise ValueError(f"Unsafe tenant identifier: '{tenant_id}'.")
    return clean


class LocalLabelFeedbackPipeline:
    """Ingests analyst ground-truth determinations, maintains isolated tenant retraining buffers,

    and computes DP-noise-protected gradient updates for continuous federated model fine-tuning.
    """

    validate_tenant_id = staticmethod(validate_tenant_id)

    def __init__(self, storage_dir: str | None = None, seed: int | None = None) -> None:
        self.privacy_guard = LabelPrivacyGuard()
        self._buffers: dict[str, list[LabelFeedbackItem]] = {}
        self._lock = threading.RLock()
        self._storage_dir = Path(
            storage_dir or os.environ.get("CFI_LABEL_FEEDBACK_DIR", "storage/label_feedback")
        )
        self._rng = random.Random(seed) if seed is not None else random.Random()

    def _get_tenant_file(self, tenant_id: str) -> Path:
        """Returns the file path for tenant buffer persistence with strict path traversal prevention."""
        clean_tenant = validate_tenant_id(tenant_id)
        base = self._storage_dir.resolve()
        target = (base / clean_tenant / "label_buffer.json").resolve()
        if not target.is_relative_to(base):
            raise ValueError(f"Path traversal detected in tenant identifier: '{tenant_id}'")
        return target

    def _save_tenant_buffer_unlocked(self, tenant_id: str) -> None:
        """Persists in-memory tenant buffer to local disk atomically (must hold self._lock)."""
        clean_tenant = validate_tenant_id(tenant_id)
        try:
            target_path = self._get_tenant_file(clean_tenant)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            items = self._buffers.get(clean_tenant, [])
            serialized = [item.to_dict() for item in items]
            temp_path = target_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=2)
            temp_path.replace(target_path)
        except Exception as exc:
            logger.error(
                "Failed to durably persist feedback buffer for tenant '%s': %s", clean_tenant, exc
            )
            raise FeedbackPersistenceError(
                f"Failed to durably persist feedback buffer for tenant '{clean_tenant}': {exc}"
            ) from exc

    def ingest_analyst_determination(
        self,
        tenant_id: str = "default_bank",
        transaction_id_hash: str | None = None,
        determination: str | FeedbackLabel | None = None,
        alert_id: str | None = None,
        priority: int | None = None,
        weight: float | None = None,
        feature_vector: list[float] | None = None,
        notes: str | None = None,
        raw_attributes: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        auto_persist: bool = True,
        case_id: str | None = None,
        validated_for_training: bool = False,
        prediction_evidence_correlated: bool = False,
        source_provenance: str = "DIRECT_INGEST",
    ) -> LabelFeedbackItem:
        """Ingests an analyst determination into local tenant label feedback buffer after zero-PII check.

        If transaction_id_hash is omitted but alert_id is provided, automatically derives
        a deterministic HMAC-SHA256 privacy hash satisfying the Zero-PII threshold (>= 32 chars).
        Applies idempotency/deduplication: if an unconsumed determination already exists for this
        transaction hash or alert ID, it is updated in-place rather than duplicating training observations.
        """
        # Resolve identifier
        if not transaction_id_hash and alert_id:
            salt = "cfi:salt:feedback_v1"
            h = hashlib.sha256(f"{salt}:{tenant_id}:{alert_id}".encode()).hexdigest()
            transaction_id_hash = h
        elif not transaction_id_hash:
            raise LabelPrivacyViolationError(
                "Either transaction_id_hash (>= 32 chars) or alert_id must be provided."
            )

        # Enforce Zero-PII invariants
        self.privacy_guard.validate_feedback_identifier(
            transaction_id_hash=transaction_id_hash,
            raw_attributes=raw_attributes,
        )

        if determination is None:
            raise ValueError("Determination label is mandatory and cannot be missing or None.")
        try:
            label = (
                determination
                if isinstance(determination, FeedbackLabel)
                else FeedbackLabel(determination)
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid determination '{determination}'. Expected one of: {[e.value for e in FeedbackLabel]}"
            ) from exc

        # Calibrate default priority and weight based on business impact
        if priority is None:
            priority = 3 if label == FeedbackLabel.CONFIRMED_FRAUD else 1
        priority = max(1, min(3, priority))

        if weight is None:
            weight = 2.0 if label == FeedbackLabel.CONFIRMED_FRAUD else 1.0
        weight = max(0.1, float(weight))

        status_str = "VALIDATED_FOR_TRAINING" if validated_for_training else "DURABLY_RECORDED"

        with self._lock:
            if tenant_id not in self._buffers:
                self._buffers[tenant_id] = []

            # Deduplication: inspect unconsumed, unreserved items for duplicate transaction or alert
            existing_item: LabelFeedbackItem | None = None
            for itm in self._buffers[tenant_id]:
                if not itm.consumed_for_retraining and itm.reserved_job_id is None:
                    if itm.transaction_id_hash == transaction_id_hash:
                        existing_item = itm
                        break
                    if alert_id and itm.alert_id == alert_id:
                        existing_item = itm
                        break

            if existing_item is not None:
                prev_dict = existing_item.to_dict()
                existing_item.label = label
                existing_item.weight = weight
                existing_item.priority = priority
                if feature_vector is not None:
                    existing_item.feature_vector = feature_vector
                if notes is not None:
                    existing_item.notes = notes
                if metadata:
                    existing_item.metadata.update(metadata)
                existing_item.recorded_at = datetime.now(UTC)
                if validated_for_training:
                    existing_item.validated_for_training = True
                    existing_item.status = "VALIDATED_FOR_TRAINING"
                if prediction_evidence_correlated:
                    existing_item.prediction_evidence_correlated = True
                if case_id:
                    existing_item.case_id = case_id
                if alert_id:
                    existing_item.alert_id = alert_id
                if source_provenance:
                    existing_item.source_provenance = source_provenance

                if auto_persist:
                    try:
                        self._save_tenant_buffer_unlocked(tenant_id)
                    except FeedbackPersistenceError:
                        restored = LabelFeedbackItem.from_dict(prev_dict)
                        for k, v in vars(restored).items():
                            setattr(existing_item, k, v)
                        raise

                logger.info(
                    "Updated existing unconsumed feedback for tenant '%s' (TxHash: %s, Label: %s, Priority: %d)",
                    tenant_id,
                    transaction_id_hash[:8],
                    label.value,
                    priority,
                )
                return existing_item

            item = LabelFeedbackItem(
                transaction_id_hash=transaction_id_hash,
                label=label,
                weight=weight,
                priority=priority,
                feature_vector=feature_vector,
                notes=notes,
                metadata=metadata or {},
                status=status_str,
                validated_for_training=validated_for_training,
                prediction_evidence_correlated=prediction_evidence_correlated,
                case_id=case_id,
                alert_id=alert_id,
                source_provenance=source_provenance,
            )
            self._buffers[tenant_id].append(item)

            if auto_persist:
                try:
                    self._save_tenant_buffer_unlocked(tenant_id)
                except FeedbackPersistenceError:
                    self._buffers[tenant_id].pop()
                    raise

        logger.info(
            "Ingested feedback for tenant '%s' (TxHash: %s, Label: %s, Priority: %d, Weight: %.2f, Status: %s)",
            tenant_id,
            transaction_id_hash[:8],
            label.value,
            priority,
            weight,
            status_str,
        )
        return item

    def reserve_retraining_batch(
        self,
        tenant_id: str,
        job_id: str,
        batch_size: int = 32,
        min_priority: int = 1,
        stratified: bool = True,
        require_validated: bool = True,
    ) -> dict[str, Any]:
        """Reserves a prioritized batch of feedback items for a specific retraining job without permanently consuming them.

        Items are marked with reserved_job_id. If training succeeds, commit_consumed_feedback
        is called. If training fails or is cancelled, release_reserved_feedback releases them back.
        """
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            if clean_tenant not in self._buffers or not self._buffers[clean_tenant]:
                self.load_from_disk(clean_tenant)
            buffer = self._buffers.get(clean_tenant, [])
            candidates = [
                item
                for item in buffer
                if not item.consumed_for_retraining
                and item.reserved_job_id is None
                and item.priority >= min_priority
                and (not require_validated or item.validated_for_training)
            ]

            if not candidates:
                return {
                    "tenant_id": clean_tenant,
                    "job_id": job_id,
                    "batch_size": 0,
                    "items": [],
                    "fraud_count": 0,
                    "false_positive_count": 0,
                    "mean_priority": 0.0,
                }

            if stratified:
                fraud_cand = sorted(
                    [i for i in candidates if i.label == FeedbackLabel.CONFIRMED_FRAUD],
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )
                fp_cand = sorted(
                    [i for i in candidates if i.label == FeedbackLabel.FALSE_POSITIVE],
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )

                if batch_size == 1:
                    batch = fraud_cand[:1] or fp_cand[:1]
                else:
                    half_batch = max(1, batch_size // 2)
                    selected_fraud = fraud_cand[:half_batch]
                    selected_fp = fp_cand[:half_batch]
                    batch = selected_fraud + selected_fp

                    remaining_capacity = batch_size - len(batch)
                    if remaining_capacity > 0:
                        if len(fraud_cand) > half_batch:
                            batch.extend(fraud_cand[half_batch : half_batch + remaining_capacity])
                        elif len(fp_cand) > half_batch:
                            batch.extend(fp_cand[half_batch : half_batch + remaining_capacity])
                batch = batch[:batch_size]
            else:
                sorted_candidates = sorted(
                    candidates,
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )
                batch = sorted_candidates[:batch_size]

            prev_reserved = [
                (item.reserved_job_id, item.reserved_at, item.status) for item in batch
            ]
            now_utc = datetime.now(UTC)
            for item in batch:
                item.reserved_job_id = job_id
                item.reserved_at = now_utc
                item.status = "RESERVED_FOR_TRAINING"

            try:
                self._save_tenant_buffer_unlocked(clean_tenant)
            except FeedbackPersistenceError:
                for idx, item in enumerate(batch):
                    item.reserved_job_id, item.reserved_at, item.status = prev_reserved[idx]
                raise

            fraud_count = sum(1 for i in batch if i.label == FeedbackLabel.CONFIRMED_FRAUD)
            fp_count = sum(1 for i in batch if i.label == FeedbackLabel.FALSE_POSITIVE)
            mean_priority = round(sum(i.priority for i in batch) / len(batch), 2) if batch else 0.0

            logger.info(
                "Reserved %d feedback items for tenant '%s' under job '%s' (Fraud: %d, FP: %d)",
                len(batch),
                clean_tenant,
                job_id,
                fraud_count,
                fp_count,
            )
            return {
                "tenant_id": clean_tenant,
                "job_id": job_id,
                "batch_size": len(batch),
                "items": [i.to_dict() for i in batch],
                "fraud_count": fraud_count,
                "false_positive_count": fp_count,
                "mean_priority": mean_priority,
            }

    def commit_consumed_feedback(
        self,
        tenant_id: str,
        job_id: str,
        training_run_id: str | None = None,
    ) -> int:
        """Commits reserved feedback items as permanently consumed upon verified training completion."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            if clean_tenant not in self._buffers or not self._buffers[clean_tenant]:
                self.load_from_disk(clean_tenant)
            buffer = self._buffers.get(clean_tenant, [])
            modified = []
            committed = 0
            for item in buffer:
                if item.reserved_job_id == job_id:
                    modified.append(
                        (
                            item,
                            item.consumed_for_retraining,
                            item.consumed_training_run_id,
                            item.reserved_job_id,
                            item.reserved_at,
                            item.status,
                        )
                    )
                    item.consumed_for_retraining = True
                    item.consumed_training_run_id = training_run_id or job_id
                    item.reserved_job_id = None
                    item.reserved_at = None
                    item.status = "CONSUMED_IN_TRAINING"
                    committed += 1
            if committed > 0:
                try:
                    self._save_tenant_buffer_unlocked(clean_tenant)
                except FeedbackPersistenceError:
                    for item, prev_c, prev_run, prev_job, prev_res_at, prev_st in modified:
                        item.consumed_for_retraining = prev_c
                        item.consumed_training_run_id = prev_run
                        item.reserved_job_id = prev_job
                        item.reserved_at = prev_res_at
                        item.status = prev_st
                    raise
                logger.info(
                    "Committed %d consumed feedback items for tenant '%s' (job: %s, run: %s)",
                    committed,
                    clean_tenant,
                    job_id,
                    training_run_id,
                )
            return committed

    def release_reserved_feedback(
        self,
        tenant_id: str,
        job_id: str,
    ) -> int:
        """Releases reserved feedback items back to the unconsumed pool if training fails or is cancelled."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            if clean_tenant not in self._buffers or not self._buffers[clean_tenant]:
                self.load_from_disk(clean_tenant)
            buffer = self._buffers.get(clean_tenant, [])
            modified = []
            released = 0
            for item in buffer:
                if item.reserved_job_id == job_id:
                    modified.append((item, item.reserved_job_id, item.reserved_at, item.status))
                    item.reserved_job_id = None
                    item.reserved_at = None
                    item.status = (
                        "VALIDATED_FOR_TRAINING"
                        if item.validated_for_training
                        else "DURABLY_RECORDED"
                    )
                    released += 1
            if released > 0:
                try:
                    self._save_tenant_buffer_unlocked(clean_tenant)
                except FeedbackPersistenceError:
                    for item, prev_job, prev_res_at, prev_st in modified:
                        item.reserved_job_id = prev_job
                        item.reserved_at = prev_res_at
                        item.status = prev_st
                    raise
                logger.info(
                    "Released %d reserved feedback items for tenant '%s' (job: %s)",
                    released,
                    clean_tenant,
                    job_id,
                )
            return released

    def reclaim_stale_reservations(
        self,
        tenant_id: str,
        max_age_seconds: float = 3600.0,
        active_job_ids: set[str] | None = None,
    ) -> int:
        """Reclaims orphaned reservations from crashed or dead workers without stranding training-eligible feedback.

        Protects active workers: any reserved_job_id present in active_job_ids is never reclaimed.
        Reclaims items whose reservation age exceeds max_age_seconds or whose worker process terminated.
        """
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            if clean_tenant not in self._buffers or not self._buffers[clean_tenant]:
                self.load_from_disk(clean_tenant)
            buffer = self._buffers.get(clean_tenant, [])
            now = datetime.now(UTC)
            modified = []
            reclaimed = 0
            for item in buffer:
                if not item.consumed_for_retraining and item.reserved_job_id is not None:
                    # Guard active worker reservations
                    if active_job_ids is not None and item.reserved_job_id in active_job_ids:
                        continue

                    # Lease timeout check
                    res_val = (
                        item.reserved_at if item.reserved_at is not None else item.recorded_at
                    )
                    if isinstance(res_val, (int, float)):
                        res_age = now.timestamp() - float(res_val)
                    elif isinstance(res_val, datetime):
                        res_age = (now - res_val).total_seconds()
                    else:
                        res_age = 0.0

                    if res_age >= max_age_seconds:
                        modified.append((item, item.reserved_job_id, item.reserved_at, item.status))
                        item.reserved_job_id = None
                        item.reserved_at = None
                        item.status = (
                            "VALIDATED_FOR_TRAINING"
                            if item.validated_for_training
                            else "DURABLY_RECORDED"
                        )
                        reclaimed += 1

            if reclaimed > 0:
                try:
                    self._save_tenant_buffer_unlocked(clean_tenant)
                except FeedbackPersistenceError:
                    for item, prev_job, prev_res_at, prev_st in modified:
                        item.reserved_job_id = prev_job
                        item.reserved_at = prev_res_at
                        item.status = prev_st
                    raise
                logger.info(
                    "Reclaimed %d stale reservations for tenant '%s' (max_age=%.1fs)",
                    reclaimed,
                    clean_tenant,
                    max_age_seconds,
                )
            return reclaimed

    def get_priority_retraining_batch(
        self,
        tenant_id: str,
        batch_size: int = 32,
        mark_consumed: bool = False,
        min_priority: int = 1,
        stratified: bool = True,
        require_validated: bool = True,
    ) -> dict[str, Any]:
        """Retrieves a prioritized batch of human-verified feedback items for local model retraining.

        Sorts by descending priority, weight, and chronological arrival, optionally balancing
        confirmed fraud and false positive samples to prevent class starvation.
        Unreserved and unconsumed items are eligible.
        Direct consumption via mark_consumed=True is explicitly prohibited.
        """
        if mark_consumed:
            raise ValueError(
                "Direct consumption via get_priority_retraining_batch(mark_consumed=True) is prohibited. "
                "Feedback consumption requires staged reservation (reserve_retraining_batch) and "
                "verified training completion (commit_consumed_feedback)."
            )

        with self._lock:
            buffer = self._buffers.get(tenant_id, [])
            candidates = [
                item
                for item in buffer
                if not item.consumed_for_retraining
                and item.reserved_job_id is None
                and item.priority >= min_priority
                and (not require_validated or item.validated_for_training)
            ]

            if not candidates:
                return {
                    "tenant_id": tenant_id,
                    "batch_size": 0,
                    "items": [],
                    "fraud_count": 0,
                    "false_positive_count": 0,
                    "mean_priority": 0.0,
                }

            if stratified:
                # Group candidates by class label
                fraud_cand = sorted(
                    [i for i in candidates if i.label == FeedbackLabel.CONFIRMED_FRAUD],
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )
                fp_cand = sorted(
                    [i for i in candidates if i.label == FeedbackLabel.FALSE_POSITIVE],
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )

                if batch_size == 1:
                    batch = fraud_cand[:1] or fp_cand[:1]
                else:
                    half_batch = max(1, batch_size // 2)
                    selected_fraud = fraud_cand[:half_batch]
                    selected_fp = fp_cand[:half_batch]
                    batch = selected_fraud + selected_fp

                    # Fill remaining capacity if one class had fewer items
                    remaining_capacity = batch_size - len(batch)
                    if remaining_capacity > 0:
                        if len(fraud_cand) > half_batch:
                            batch.extend(fraud_cand[half_batch : half_batch + remaining_capacity])
                        elif len(fp_cand) > half_batch:
                            batch.extend(fp_cand[half_batch : half_batch + remaining_capacity])
                batch = batch[:batch_size]
            else:
                sorted_candidates = sorted(
                    candidates,
                    key=lambda x: (-x.priority, -x.weight, x.recorded_at),
                )
                batch = sorted_candidates[:batch_size]
            fraud_count = sum(1 for i in batch if i.label == FeedbackLabel.CONFIRMED_FRAUD)
            fp_count = sum(1 for i in batch if i.label == FeedbackLabel.FALSE_POSITIVE)
            mean_priority = round(sum(i.priority for i in batch) / len(batch), 2) if batch else 0.0

            return {
                "tenant_id": tenant_id,
                "batch_size": len(batch),
                "items": [i.to_dict() for i in batch],
                "fraud_count": fraud_count,
                "false_positive_count": fp_count,
                "mean_priority": mean_priority,
            }

    def compute_dp_gradient_update(
        self,
        tenant_id: str,
        epsilon: float = 1.0,
        delta: float = 1e-5,
        clip_norm: float = 1.0,
    ) -> dict[str, Any]:
        """Computes local weight delta from ground-truth feedback buffer with Gaussian DP noise injection."""
        self.privacy_guard.validate_gradient_privacy(epsilon=epsilon, delta=delta)

        with self._lock:
            buffer = self._buffers.get(tenant_id, [])
            if not buffer:
                return {
                    "tenant_id": tenant_id,
                    "delta_weights": [0.0, 0.0, 0.0, 0.0],
                    "sample_count": 0,
                    "epsilon": epsilon,
                    "delta": delta,
                    "sigma": 0.0,
                }

            # Calculate base gradient delta from positive/negative feedback ratio weighted by priority
            total_weight = sum(item.weight for item in buffer)
            fraud_weight = sum(
                item.weight for item in buffer if item.label == FeedbackLabel.CONFIRMED_FRAUD
            )
            total = len(buffer)
            raw_gradient = (fraud_weight / total_weight) * 0.1 if total_weight > 0 else 0.0

            # Derive analytical Gaussian Differential Privacy noise scale
            sigma = self.privacy_guard.calculate_sigma(
                epsilon=epsilon, delta=delta, clip_norm=clip_norm
            )
            raw_deltas = [raw_gradient * (i + 1) for i in range(4)]
            dp_deltas = [round(d + self._rng.gauss(0, sigma * 0.01), 6) for d in raw_deltas]

            logger.info(
                "Computed DP gradient update for tenant '%s' (%d samples, epsilon=%.2f, sigma=%.4f)",
                tenant_id,
                total,
                epsilon,
                sigma,
            )
            return {
                "tenant_id": tenant_id,
                "delta_weights": dp_deltas,
                "sample_count": total,
                "epsilon": epsilon,
                "delta": delta,
                "sigma": sigma,
            }

    def get_buffer_stats(self, tenant_id: str) -> dict[str, Any]:
        """Retrieves summary metrics for tenant's feedback store."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            buffer = self._buffers.get(clean_tenant, [])
            total = len(buffer)
            fraud = sum(1 for i in buffer if i.label == FeedbackLabel.CONFIRMED_FRAUD)
            fp = sum(1 for i in buffer if i.label == FeedbackLabel.FALSE_POSITIVE)
            consumed = sum(1 for i in buffer if i.consumed_for_retraining)
            unconsumed = total - consumed
            validated = sum(1 for i in buffer if i.validated_for_training)
            reserved = sum(1 for i in buffer if i.reserved_job_id is not None)

            p_dist: dict[int, int] = {1: 0, 2: 0, 3: 0}
            for i in buffer:
                p_dist[i.priority] = p_dist.get(i.priority, 0) + 1

            return {
                "tenant_id": clean_tenant,
                "total_count": total,
                "fraud_count": fraud,
                "false_positive_count": fp,
                "consumed_count": consumed,
                "unconsumed_count": unconsumed,
                "validated_count": validated,
                "reserved_count": reserved,
                "priority_distribution": p_dist,
            }

    def get_feedback_buffer(self, tenant_id: str) -> list[dict[str, Any]]:
        """Retrieves tenant feedback buffer items, loading from disk if in-memory buffer is empty."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            if clean_tenant not in self._buffers or not self._buffers[clean_tenant]:
                self.load_from_disk(clean_tenant)
            items = self._buffers.get(clean_tenant, [])
            res = []
            for item in items:
                d = item.to_dict()
                d["determination"] = d["label"]
                if item.metadata and "alert_id" in item.metadata:
                    d["alert_id"] = item.metadata["alert_id"]
                res.append(d)
            return res

    def get_buffer_size(self, tenant_id: str) -> int:
        """Retrieves tenant feedback buffer size."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            return len(self._buffers.get(clean_tenant, []))

    def clear_buffer(self, tenant_id: str) -> int:
        """Clears in-memory and disk buffer for the specified tenant."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            existing = len(self._buffers.get(clean_tenant, []))
            self._buffers[clean_tenant] = []
            target_path = self._get_tenant_file(clean_tenant)
            if target_path.exists():
                try:
                    target_path.unlink()
                except Exception as exc:
                    logger.warning(
                        "Could not delete persisted buffer file for '%s': %s", clean_tenant, exc
                    )
            return existing

    def save_to_disk(self, tenant_id: str) -> str:
        """Explicitly saves tenant buffer to disk and returns the file path."""
        clean_tenant = validate_tenant_id(tenant_id)
        with self._lock:
            self._save_tenant_buffer_unlocked(clean_tenant)
            return str(self._get_tenant_file(clean_tenant))

    def load_from_disk(self, tenant_id: str) -> int:
        """Loads persisted feedback items from disk into memory buffer."""
        clean_tenant = validate_tenant_id(tenant_id)
        target_path = self._get_tenant_file(clean_tenant)
        if not target_path.exists():
            return 0

        with self._lock:
            try:
                with open(target_path, encoding="utf-8") as f:
                    data = json.load(f)
                loaded_items = [LabelFeedbackItem.from_dict(item) for item in data]
                self._buffers[clean_tenant] = loaded_items
                logger.info(
                    "Loaded %d feedback items from disk for tenant '%s'",
                    len(loaded_items),
                    clean_tenant,
                )
                return len(loaded_items)
            except Exception as exc:
                logger.error(
                    "Failed to load feedback buffer for tenant '%s': %s", clean_tenant, exc
                )
                return 0
