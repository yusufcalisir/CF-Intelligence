"""Alert intelligence service.

Generates fraud alerts from model predictions, applies sliding-window
deduplication and intelligent multi-factor triage priority scoring, and
manages the shared cross-bank intelligence layer. Alerts never contain
raw transaction data — only risk scores, reason codes, triage metadata,
and privacy-preserving identifiers.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.entities_phase2 import Alert, SharedIntelligence
from app.domain.enums import (
    AlertSeverity,
    AlertStatus,
    EntityType,
    IntelligenceType,
    TriageAction,
    TriagePriority,
)
from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier
from app.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


# ── Serializers ──────────────────────────────────────────────────────────────


def _alert_to_dict(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "bank_id": a.bank_id,
        "transaction_id": a.transaction_id,
        "risk_score": a.risk_score,
        "severity": a.severity.value,
        "status": a.status.value,
        "reason_codes": a.reason_codes,
        "confidence": a.confidence,
        "involved_entity_ids": a.involved_entity_ids,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        "top_features": a.top_features,
        "risk_factors": a.risk_factors,
        "model_confidence": a.model_confidence,
        "historical_evidence": a.historical_evidence,
        "triage_priority": a.triage_priority.value if hasattr(a.triage_priority, "value") else str(a.triage_priority),
        "triage_action": a.triage_action.value if hasattr(a.triage_action, "value") else str(a.triage_action),
        "sla_minutes": a.sla_minutes,
        "triage_reasons": a.triage_reasons,
        "dedup_key": a.dedup_key,
        "dedup_count": a.dedup_count,
        "is_duplicate": a.is_duplicate,
        "first_seen_at": a.first_seen_at.isoformat() if a.first_seen_at else None,
        "last_duplicate_at": a.last_duplicate_at.isoformat() if a.last_duplicate_at else None,
    }


def _dict_to_alert(d: dict[str, Any]) -> Alert:
    d_copy = d.copy()
    d_copy["severity"] = AlertSeverity(d_copy["severity"])
    d_copy["status"] = AlertStatus(d_copy["status"])
    if "triage_priority" in d_copy and d_copy["triage_priority"]:
        try:
            d_copy["triage_priority"] = TriagePriority(d_copy["triage_priority"])
        except ValueError:
            d_copy["triage_priority"] = TriagePriority.P3_MEDIUM
    else:
        d_copy["triage_priority"] = TriagePriority.P3_MEDIUM

    if "triage_action" in d_copy and d_copy["triage_action"]:
        try:
            d_copy["triage_action"] = TriageAction(d_copy["triage_action"])
        except ValueError:
            d_copy["triage_action"] = TriageAction.QUEUE_STANDARD
    else:
        d_copy["triage_action"] = TriageAction.QUEUE_STANDARD

    d_copy["sla_minutes"] = int(d_copy.get("sla_minutes", 1440))
    d_copy["triage_reasons"] = list(d_copy.get("triage_reasons", []))
    d_copy["dedup_count"] = int(d_copy.get("dedup_count", 1))
    d_copy["is_duplicate"] = bool(d_copy.get("is_duplicate", False))

    d_copy["created_at"] = datetime.fromisoformat(d_copy["created_at"])
    if d_copy.get("updated_at"):
        d_copy["updated_at"] = datetime.fromisoformat(d_copy["updated_at"])
    if d_copy.get("first_seen_at"):
        d_copy["first_seen_at"] = datetime.fromisoformat(d_copy["first_seen_at"])
    if d_copy.get("last_duplicate_at"):
        d_copy["last_duplicate_at"] = datetime.fromisoformat(d_copy["last_duplicate_at"])
    return Alert(**d_copy)


def _intel_to_dict(i: SharedIntelligence) -> dict[str, Any]:
    return {
        "id": i.id,
        "source_bank_id": i.source_bank_id,
        "intelligence_type": i.intelligence_type.value,
        "privacy_hash": i.privacy_hash,
        "risk_indicator": i.risk_indicator,
        "description": i.description,
        "entity_type": i.entity_type.value if i.entity_type else None,
        "related_alert_count": i.related_alert_count,
        "created_at": i.created_at.isoformat(),
        "expires_at": i.expires_at.isoformat() if i.expires_at else None,
    }


def _dict_to_intel(d: dict[str, Any]) -> SharedIntelligence:
    d_copy = d.copy()
    d_copy["intelligence_type"] = IntelligenceType(d_copy["intelligence_type"])
    if d_copy.get("entity_type"):
        d_copy["entity_type"] = EntityType(d_copy["entity_type"])
    d_copy["created_at"] = datetime.fromisoformat(d_copy["created_at"])
    if d_copy.get("expires_at"):
        d_copy["expires_at"] = datetime.fromisoformat(d_copy["expires_at"])
    return SharedIntelligence(**d_copy)


# ── Deduplication Engine ─────────────────────────────────────────────────────


@dataclass
class DedupRecord:
    alert_id: str
    dedup_key: str
    bank_id: str
    primary_entity_id: str
    risk_score: float
    created_at: datetime
    last_seen_at: datetime
    duplicate_count: int
    reason_codes: list[str]


@dataclass
class DeduplicationConfig:
    enabled: bool = True
    window_seconds: float = 300.0  # 5-minute sliding window
    max_records: int = 10000


class AlertDeduplicationEngine:
    """Thread-safe sliding-window alert deduplication engine."""

    def __init__(self, config: DeduplicationConfig | None = None) -> None:
        self.config = config or DeduplicationConfig()
        self._records: dict[str, DedupRecord] = {}
        self._lock = threading.RLock()
        self._total_processed: int = 0
        self._duplicates_detected: int = 0

    def compute_dedup_key(
        self,
        bank_id: str,
        primary_entity_id: str,
        reason_codes: list[str] | None = None,
    ) -> str:
        raw = f"{bank_id}:{primary_entity_id}:fraud"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def process_alert(
        self,
        alert: Alert,
        primary_entity_id: str = "",
        now: datetime | None = None,
    ) -> tuple[bool, Alert]:
        """Process alert through deduplication sliding window."""
        if not self.config.enabled:
            return False, alert

        now = now or datetime.now(UTC)
        entity_id = primary_entity_id or (alert.involved_entity_ids[0] if alert.involved_entity_ids else "none")
        dedup_key = self.compute_dedup_key(alert.bank_id, entity_id, alert.reason_codes)

        with self._lock:
            self._total_processed += 1
            record = self._records.get(dedup_key)

            if record and (now - record.last_seen_at).total_seconds() <= self.config.window_seconds:
                # Existing sliding window duplicate detected
                self._duplicates_detected += 1
                record.duplicate_count += 1
                record.last_seen_at = now
                record.risk_score = max(record.risk_score, alert.risk_score)
                record.reason_codes = list(set(record.reason_codes + alert.reason_codes))

                alert.dedup_key = dedup_key
                alert.dedup_count = record.duplicate_count
                alert.is_duplicate = True
                alert.risk_score = record.risk_score
                alert.first_seen_at = record.created_at
                alert.last_duplicate_at = now
                alert.reason_codes = record.reason_codes
                return True, alert

            # Fresh record
            if len(self._records) >= self.config.max_records:
                self.prune_expired(now)

            self._records[dedup_key] = DedupRecord(
                alert_id=alert.id,
                dedup_key=dedup_key,
                bank_id=alert.bank_id,
                primary_entity_id=entity_id,
                risk_score=alert.risk_score,
                created_at=now,
                last_seen_at=now,
                duplicate_count=1,
                reason_codes=list(alert.reason_codes),
            )
            alert.dedup_key = dedup_key
            alert.dedup_count = 1
            alert.is_duplicate = False
            alert.first_seen_at = now
            return False, alert

    def prune_expired(self, now: datetime | None = None) -> int:
        """Prune records older than window_seconds from the sliding cache."""
        now = now or datetime.now(UTC)
        with self._lock:
            expired_keys = [
                k
                for k, r in self._records.items()
                if (now - r.last_seen_at).total_seconds() > self.config.window_seconds
            ]
            for k in expired_keys:
                del self._records[k]
            return len(expired_keys)

    def get_stats(self) -> dict[str, Any]:
        """Return real-time deduplication metrics."""
        with self._lock:
            ratio = (
                self._duplicates_detected / self._total_processed
                if self._total_processed > 0
                else 0.0
            )
            return {
                "total_processed": self._total_processed,
                "duplicates_detected": self._duplicates_detected,
                "deduplication_ratio": round(ratio, 4),
                "active_sliding_window_keys": len(self._records),
                "window_seconds": self.config.window_seconds,
            }


# ── Intelligent Triage Engine ────────────────────────────────────────────────


@dataclass
class TriageResult:
    priority: TriagePriority
    action: TriageAction
    sla_minutes: int
    reasons: list[str]


class AlertTriageEngine:
    """Multi-factor algorithmic alert triage and SLA assignment engine."""

    HIGH_RISK_COUNTRIES = {"NG", "RU", "PH", "BR", "KP", "IR", "SY"}

    @classmethod
    def evaluate_triage(
        cls,
        txn: dict,
        risk_score: float,
        severity: AlertSeverity,
        dedup_count: int = 1,
        reason_codes: list[str] | None = None,
        entity_overlap_count: int = 0,
    ) -> TriageResult:
        reasons: list[str] = []
        reason_codes = reason_codes or []

        # 1. Base classification from risk score / severity
        if risk_score >= 900.0 or severity == AlertSeverity.CRITICAL:
            priority = TriagePriority.P1_CRITICAL
            reasons.append(f"Critical risk score threshold ({risk_score:.1f}/1000)")
        elif risk_score >= 750.0 or severity == AlertSeverity.HIGH:
            priority = TriagePriority.P2_HIGH
            reasons.append(f"High risk score threshold ({risk_score:.1f}/1000)")
        elif risk_score >= 500.0 or severity == AlertSeverity.MEDIUM:
            priority = TriagePriority.P3_MEDIUM
            reasons.append(f"Medium risk score ({risk_score:.1f}/1000)")
        else:
            priority = TriagePriority.P4_LOW
            reasons.append(f"Low baseline risk score ({risk_score:.1f}/1000)")

        # 2. Amount impact escalation
        amount = float(txn.get("transaction_amount", 0.0) or 0.0)
        if amount >= 10000.0:
            if priority == TriagePriority.P2_HIGH:
                priority = TriagePriority.P1_CRITICAL
            elif priority == TriagePriority.P3_MEDIUM:
                priority = TriagePriority.P2_HIGH
            reasons.append(f"High-value transaction amount (${amount:,.2f} >= $10,000 threshold)")

        # 3. Geopolitical sanctions and high-risk jurisdiction
        country = str(txn.get("country_code", "")).upper()
        if country in cls.HIGH_RISK_COUNTRIES or "GEO-RISK" in reason_codes:
            if priority in (TriagePriority.P3_MEDIUM, TriagePriority.P4_LOW):
                priority = TriagePriority.P2_HIGH
            reasons.append(f"Sanctions / high-risk jurisdiction exposure ({country or 'GEO-RISK'})")

        # 4. Burst velocity / repeated deduplication attack
        if dedup_count >= 3:
            priority = TriagePriority.P1_CRITICAL
            reasons.append(
                f"Burst velocity deduplication attack ({dedup_count} repeated attempts in 5m window)"
            )
        elif dedup_count == 2 and priority in (TriagePriority.P3_MEDIUM, TriagePriority.P4_LOW):
            priority = TriagePriority.P2_HIGH
            reasons.append("Repeated transaction attempt within deduplication window")

        # 5. Cross-bank mule ring / entity overlap
        if entity_overlap_count >= 2:
            priority = TriagePriority.P1_CRITICAL
            reasons.append(
                f"Cross-bank mule syndicate overlap ({entity_overlap_count} institutions linked)"
            )

        # 6. SLA and action mapping
        sla_map = {
            TriagePriority.P1_CRITICAL: 15,    # 15 minutes
            TriagePriority.P2_HIGH: 120,       # 2 hours
            TriagePriority.P3_MEDIUM: 1440,    # 24 hours
            TriagePriority.P4_LOW: 4320,       # 72 hours
        }
        action_map = {
            TriagePriority.P1_CRITICAL: TriageAction.ESCALATE_IMMEDIATE,
            TriagePriority.P2_HIGH: TriageAction.INVESTIGATE_CASE,
            TriagePriority.P3_MEDIUM: TriageAction.QUEUE_STANDARD,
            TriagePriority.P4_LOW: TriageAction.AUTO_MONITOR,
        }

        return TriageResult(
            priority=priority,
            action=action_map[priority],
            sla_minutes=sla_map[priority],
            reasons=reasons,
        )


# ── AlertIntelligenceService ─────────────────────────────────────────────────


class AlertIntelligenceService:
    """Generates alerts from predictions and manages shared intelligence.

    Converts model outputs into actionable alerts, applies sliding-window
    deduplication, assigns multi-factor triage priorities, and publishes
    privacy-preserving intelligence for cross-institution collaboration.
    """

    def __init__(
        self,
        alert_threshold: float = 0.5,
        dedup_window_seconds: float = 300.0,
    ) -> None:
        self.alert_threshold = alert_threshold
        self._intelligence_store = RedisStore("intelligence")
        self._alert_store = RedisStore("alert")
        self._dedup_engine = AlertDeduplicationEngine(
            DeduplicationConfig(window_seconds=dedup_window_seconds)
        )
        self._triage_engine = AlertTriageEngine()
        self._lock = threading.RLock()

    def generate_alerts(
        self,
        bank_id: str,
        transactions: list[dict],
        predictions: list[float],
        threshold: float | None = None,
    ) -> list[Alert]:
        """Generate fraud alerts from model predictions with triage and deduplication.

        Args:
            bank_id: ID of the bank generating alerts.
            transactions: List of transaction dicts (features).
            predictions: Model prediction scores (0-1).
            threshold: Override the default alert threshold.

        Returns:
            List of Alert objects for transactions exceeding the threshold.
        """
        threshold = threshold or self.alert_threshold
        alerts: list[Alert] = []

        with self._lock:
            for txn, score in zip(transactions, predictions, strict=False):
                if score < threshold:
                    continue

                severity = self._classify_severity(score)
                reason_codes = self._generate_reason_codes(txn, score)
                entity_ids = self._extract_entity_ids(txn, bank_id)
                risk_score = round(score * 1000, 1)

                # Initial triage evaluation
                triage_res = self._triage_engine.evaluate_triage(
                    txn=txn,
                    risk_score=risk_score,
                    severity=severity,
                    dedup_count=1,
                    reason_codes=reason_codes,
                )

                alert = Alert(
                    bank_id=bank_id,
                    transaction_id=txn.get("transaction_id", str(uuid.uuid4())),
                    risk_score=risk_score,
                    severity=severity,
                    reason_codes=reason_codes,
                    confidence=round(score, 4),
                    involved_entity_ids=entity_ids,
                    model_confidence=round(score, 4),
                    top_features=self._get_top_features(txn, score),
                    risk_factors=self._get_risk_factors(txn, score),
                    triage_priority=triage_res.priority,
                    triage_action=triage_res.action,
                    sla_minutes=triage_res.sla_minutes,
                    triage_reasons=triage_res.reasons,
                )

                # Deduplication sliding window processing
                is_dup, alert = self._dedup_engine.process_alert(
                    alert=alert,
                    primary_entity_id=entity_ids[0] if entity_ids else str(txn.get("customer_id", "")),
                )

                # Re-evaluate triage on duplicate burst
                if is_dup and alert.dedup_count >= 2:
                    re_triage = self._triage_engine.evaluate_triage(
                        txn=txn,
                        risk_score=alert.risk_score,
                        severity=alert.severity,
                        dedup_count=alert.dedup_count,
                        reason_codes=alert.reason_codes,
                    )
                    alert.triage_priority = re_triage.priority
                    alert.triage_action = re_triage.action
                    alert.sla_minutes = re_triage.sla_minutes
                    alert.triage_reasons = re_triage.reasons

                alerts.append(alert)
                self._alert_store.set(alert.id, _alert_to_dict(alert))

        logger.info(
            "Generated %d alerts for %s (threshold=%.2f, dedup_keys=%d)",
            len(alerts),
            bank_id,
            threshold,
            len(self._dedup_engine._records),
        )
        return alerts

    def update_alert_status(
        self,
        alert_id: str,
        status: AlertStatus,
        resolution_notes: str | None = None,
    ) -> Alert | None:
        """Update alert status with timestamp and optional resolution notes."""
        with self._lock:
            val = self._alert_store.get(alert_id)
            if not val:
                return None
            alert = _dict_to_alert(val)
            alert.status = status
            alert.updated_at = datetime.now(UTC)
            if resolution_notes:
                alert.risk_factors.append(f"Resolution note: {resolution_notes}")
            self._alert_store.set(alert.id, _alert_to_dict(alert))
            return alert

    def triage_alert(
        self,
        alert_id: str,
        txn_override: dict | None = None,
    ) -> Alert | None:
        """On-demand triage re-evaluation for an existing alert."""
        with self._lock:
            alert = self.get_alert(alert_id)
            if not alert:
                return None
            txn = txn_override or {
                "transaction_amount": 0.0,
                "country_code": "US",
                "velocity": 1.0,
            }
            res = self._triage_engine.evaluate_triage(
                txn=txn,
                risk_score=alert.risk_score,
                severity=alert.severity,
                dedup_count=alert.dedup_count,
                reason_codes=alert.reason_codes,
            )
            alert.triage_priority = res.priority
            alert.triage_action = res.action
            alert.sla_minutes = res.sla_minutes
            alert.triage_reasons = res.reasons
            alert.updated_at = datetime.now(UTC)
            self._alert_store.set(alert.id, _alert_to_dict(alert))
            return alert

    def get_dedup_stats(self) -> dict[str, Any]:
        """Retrieve real-time deduplication engine metrics."""
        return self._dedup_engine.get_stats()

    def publish_intelligence(self, alert: Alert) -> SharedIntelligence:
        """Convert an alert to shared intelligence with privacy hashing."""
        privacy_hash = PrivacyPreservingIdentifier.compute(
            alert.transaction_id,
            "transaction",
        )

        intelligence = SharedIntelligence(
            source_bank_id=alert.bank_id,
            intelligence_type=IntelligenceType.FRAUD_ALERT,
            privacy_hash=privacy_hash,
            risk_indicator=alert.risk_score / 1000,
            description=f"Alert {alert.severity.value}: {', '.join(alert.reason_codes[:3])}",
            entity_type=EntityType.CUSTOMER,
            related_alert_count=alert.dedup_count,
        )

        with self._lock:
            self._intelligence_store.push_list("intelligence_list", _intel_to_dict(intelligence))

        logger.info(
            "Published intelligence from %s: hash=%s risk=%.2f",
            alert.bank_id,
            privacy_hash,
            intelligence.risk_indicator,
        )
        return intelligence

    def consume_intelligence(self, bank_id: str) -> list[SharedIntelligence]:
        """Retrieve intelligence from other banks (excluding caller)."""
        with self._lock:
            raw_list = self._intelligence_store.get_list("intelligence_list")
        items = [_dict_to_intel(i) for i in raw_list]
        return [intel for intel in items if intel.source_bank_id != bank_id]

    def get_all_intelligence(self) -> list[SharedIntelligence]:
        """Retrieve all shared intelligence items."""
        with self._lock:
            raw_list = self._intelligence_store.get_list("intelligence_list")
        return [_dict_to_intel(i) for i in raw_list]

    def correlate_alerts(self, alerts: list[Alert]) -> list[dict]:
        """Find patterns across multiple alerts."""
        correlations: list[dict] = []

        # Entity overlap analysis
        entity_to_alerts: dict[str, list[str]] = {}
        for alert in alerts:
            for entity_id in alert.involved_entity_ids:
                entity_to_alerts.setdefault(entity_id, []).append(alert.id)

        for entity_id, alert_ids in entity_to_alerts.items():
            if len(alert_ids) >= 2:
                correlations.append(
                    {
                        "type": "entity_overlap",
                        "entity_id": entity_id,
                        "alert_ids": alert_ids,
                        "count": len(alert_ids),
                        "description": f"Entity {entity_id[:8]} appears in {len(alert_ids)} alerts",
                    }
                )

        # Velocity analysis — alerts within 60 seconds
        sorted_alerts = sorted(alerts, key=lambda a: a.created_at)
        for i in range(len(sorted_alerts) - 1):
            time_diff = (
                sorted_alerts[i + 1].created_at - sorted_alerts[i].created_at
            ).total_seconds()
            if time_diff < 60 and sorted_alerts[i].bank_id == sorted_alerts[i + 1].bank_id:
                correlations.append(
                    {
                        "type": "velocity",
                        "alert_ids": [sorted_alerts[i].id, sorted_alerts[i + 1].id],
                        "time_diff_seconds": time_diff,
                        "description": f"Two alerts within {time_diff:.0f}s from {sorted_alerts[i].bank_id}",
                    }
                )

        return correlations

    def get_alert_by_transaction_id(self, transaction_id: str) -> Alert | None:
        """Find an alert by transaction ID."""
        with self._lock:
            raw_vals = self._alert_store.list_values()
        for v in raw_vals:
            alert = _dict_to_alert(v)
            if alert.transaction_id == transaction_id:
                return alert
        return None

    def create_alert(self, alert: Alert) -> Alert:
        """Store an alert directly into the persistent alert store."""
        with self._lock:
            self._alert_store.set(alert.id, _alert_to_dict(alert))
        return alert

    def get_alert(self, alert_id: str) -> Alert | None:
        """Fetch alert by ID from store. Zero-mock production lookup."""
        with self._lock:
            val = self._alert_store.get(alert_id)
        if val:
            return _dict_to_alert(val)
        return None

    def get_alerts(
        self,
        bank_id: str | None = None,
        severity: AlertSeverity | None = None,
        status: AlertStatus | None = None,
        limit: int = 50,
    ) -> list[Alert]:
        """Retrieve alerts with optional filters."""
        with self._lock:
            raw_vals = self._alert_store.list_values()
        alerts = [_dict_to_alert(v) for v in raw_vals]
        if bank_id:
            alerts = [a for a in alerts if a.bank_id == bank_id]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if status:
            alerts = [a for a in alerts if a.status == status]
        return sorted(alerts, key=lambda a: a.created_at, reverse=True)[:limit]

    def get_intelligence_stats(self) -> dict:
        """Aggregate statistics about shared intelligence."""
        by_type: dict[str, int] = {}
        by_bank: dict[str, int] = {}
        total_risk = 0.0

        with self._lock:
            raw_list = self._intelligence_store.get_list("intelligence_list")
        items = [_dict_to_intel(i) for i in raw_list]

        for intel in items:
            by_type[intel.intelligence_type.value] = (
                by_type.get(intel.intelligence_type.value, 0) + 1
            )
            by_bank[intel.source_bank_id] = by_bank.get(intel.source_bank_id, 0) + 1
            total_risk += intel.risk_indicator

        n = len(items) or 1
        return {
            "total_items": len(items),
            "items_by_type": by_type,
            "items_by_bank": by_bank,
            "avg_risk_indicator": round(total_risk / n, 4),
        }

    # ── Private helpers ────────────────────────

    @staticmethod
    def _classify_severity(score: float) -> AlertSeverity:
        if score >= 0.9:
            return AlertSeverity.CRITICAL
        if score >= 0.75:
            return AlertSeverity.HIGH
        if score >= 0.5:
            return AlertSeverity.MEDIUM
        if score >= 0.3:
            return AlertSeverity.LOW
        return AlertSeverity.INFO

    @staticmethod
    def _generate_reason_codes(txn: dict, score: float) -> list[str]:
        codes: list[str] = []
        if score >= 0.8:
            codes.append("ML-HIGH")
        if txn.get("velocity", 0) > 5:
            codes.append("VEL-001")
        if txn.get("merchant_risk_score", 0) > 0.6:
            codes.append("MERCH-RISK")
        if txn.get("country_code") in {"NG", "RU", "PH", "BR"}:
            codes.append("GEO-RISK")
        if txn.get("account_age_days", 365) < 30:
            codes.append("NEW-ACCT")
        if txn.get("chargeback_count", 0) >= 2:
            codes.append("CB-HIST")
        if txn.get("transaction_amount", 0) > 5000:
            codes.append("HIGH-AMT")
        if txn.get("hour_of_day", 12) < 5 or txn.get("hour_of_day", 12) > 22:
            codes.append("ODD-HOUR")
        return codes or ["ML-FLAG"]

    @staticmethod
    def _extract_entity_ids(txn: dict, bank_id: str) -> list[str]:
        """Extract privacy-preserving entity IDs from a transaction."""
        ids: list[str] = []
        if "customer_id" in txn:
            h = PrivacyPreservingIdentifier.compute(str(txn["customer_id"]), "customer")
            ids.append(h)
        if "merchant_category" in txn:
            h = PrivacyPreservingIdentifier.compute(str(txn["merchant_category"]), "merchant")
            ids.append(h)
        if "device_type" in txn:
            h = PrivacyPreservingIdentifier.compute(str(txn["device_type"]), "device")
            ids.append(h)
        return ids

    @staticmethod
    def _get_top_features(txn: dict, score: float) -> list[dict[str, float | str]]:
        """Estimate feature contributions for explainability using SHAP."""
        try:
            from app.application.services.explainability_service import ExplainabilityService

            explainer = ExplainabilityService()
            shap_features = explainer.compute_shap_values(txn)
            return [
                {
                    "feature": f["feature"],
                    "contribution": round(f["contribution"], 4),
                    "value": round(f["contribution"], 4),
                }
                for f in shap_features
            ]
        except Exception as e:
            logger.warning(
                "SHAP explainability computation failed: %s. Falling back to heuristic.", e
            )
            features: list[dict[str, float | str]] = []
            feature_weights = {
                "transaction_amount": 0.20,
                "velocity": 0.18,
                "merchant_risk_score": 0.15,
                "customer_history_score": 0.12,
                "country_code": 0.10,
                "hour_of_day": 0.08,
                "account_age_days": 0.07,
                "chargeback_count": 0.05,
                "device_type": 0.03,
                "merchant_category": 0.02,
            }
            for feat, base_weight in feature_weights.items():
                val = txn.get(feat, 0)
                if isinstance(val, str):
                    val = hash(val) % 100 / 100  # Normalize categorical
                contribution = base_weight * score * (0.5 + 0.5 * min(1.0, float(val) / 100))
                features.append(
                    {
                        "feature": feat,
                        "contribution": round(contribution, 4),
                        "value": round(contribution, 4),
                    }
                )
            return sorted(features, key=lambda f: float(f["contribution"]), reverse=True)

    @staticmethod
    def _get_risk_factors(txn: dict, score: float) -> list[str]:
        """Generate human-readable risk factor descriptions."""
        factors: list[str] = []
        if txn.get("velocity", 0) > 5:
            factors.append(f"High transaction velocity ({txn['velocity']:.1f} txns/hr)")
        if txn.get("merchant_risk_score", 0) > 0.6:
            factors.append(f"High-risk merchant (score: {txn['merchant_risk_score']:.2f})")
        if txn.get("country_code") in {"NG", "RU", "PH", "BR"}:
            factors.append(f"Transaction from high-risk country ({txn['country_code']})")
        if txn.get("account_age_days", 365) < 30:
            factors.append(f"New account ({txn['account_age_days']} days old)")
        if txn.get("transaction_amount", 0) > 5000:
            factors.append(f"Large transaction amount (${txn['transaction_amount']:,.2f})")
        if score >= 0.8:
            factors.append(f"ML model high confidence ({score:.1%})")
        return factors
