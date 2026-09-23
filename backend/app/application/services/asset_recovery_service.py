"""Asset Recovery & Collaborative FININT Operational Hub Service.

Tracks and aggregates the tangible financial impact of cross-bank collaborative
fraud-fighting operations:

- Total EUR assets frozen and recovered via ISO 20022 camt.056 payment recalls
  and cross-bank FININT provisional holds.
- Mean Time to Response (MTTR) reduction: alert-to-freeze latency across
  consortium institutions in minutes vs. the legacy 48-hour bilateral baseline.
- Prevented mule ring volume and cross-bank contagion containment rate.
- Typology-level ROI breakdown (smurfing, APP fraud, dormant-burst, etc.).

REST endpoints exposed:
    GET /api/v1/operations/asset-recovery/summary
    GET /api/v1/operations/asset-recovery/timeline
    GET /api/v1/operations/asset-recovery/breakdown-by-typology

Privacy invariants:
    - No raw PII is stored or returned; all entity references use HMAC-SHA256
      privacy identifiers from the existing entity resolution pipeline.
    - Amounts are always denominated in EUR with two-decimal precision.
    - Audit log entries are SHA-256 hash-chained for tamper-evidence.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Legacy bilateral FININT response baseline: 48 hours (EPC SCT Inst rulebook)
_LEGACY_BASELINE_MINUTES: float = 48.0 * 60.0

# Typology identifiers per European AML scenario library
_KNOWN_TYPOLOGIES: list[str] = [
    "smurfing_structuring",
    "app_fraud_mule_chain",
    "dormant_burst_velocity",
    "rapid_pass_through",
    "high_risk_corridor_flight",
    "round_tripping",
    "invoice_manipulation",
    "crypto_gateway_cashout",
]


# ── Domain Dataclasses ─────────────────────────────────────────────────────────


@dataclass
class RecoveryEvent:
    """A single asset recovery event (recall success or provisional hold)."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "RECALL_SUCCESS"  # RECALL_SUCCESS | PROVISIONAL_HOLD | PARTIAL_RECOVERY
    amount_eur: Decimal = Decimal("0.00")
    typology: str = "app_fraud_mule_chain"
    originating_bank_id: str = ""
    receiving_bank_id: str = ""
    alert_raised_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    freeze_confirmed_at: datetime | None = None
    recall_message_id: str = ""  # ISO 20022 camt.056 MsgId reference
    finint_ticket_id: str = ""   # Cross-bank FININT case ticket reference
    # MTTR in minutes (None until freeze is confirmed)
    mttr_minutes: float | None = None
    audit_hash: str = ""

    def compute_mttr(self) -> float | None:
        """Return alert-to-freeze latency in minutes, or None if not yet frozen."""
        if self.freeze_confirmed_at is None:
            return None
        delta = self.freeze_confirmed_at - self.alert_raised_at
        return max(0.0, delta.total_seconds() / 60.0)


@dataclass
class AssetRecoverySummary:
    """Aggregated KPI snapshot for the operational hub dashboard."""

    snapshot_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_events: int = 0
    total_eur_frozen: Decimal = Decimal("0.00")
    total_eur_recovered: Decimal = Decimal("0.00")
    # Contagion containment: fraction of mule chains terminated before second hop
    contagion_containment_rate: float = 0.0
    # MTTR statistics (minutes)
    mttr_mean_minutes: float = 0.0
    mttr_p50_minutes: float = 0.0
    mttr_p90_minutes: float = 0.0
    mttr_p99_minutes: float = 0.0
    legacy_baseline_minutes: float = _LEGACY_BASELINE_MINUTES
    mttr_reduction_pct: float = 0.0  # % improvement vs. legacy baseline
    # Unique mule chains disrupted
    mule_chains_disrupted: int = 0
    # Cross-bank institutions involved
    consortium_banks_active: int = 0
    # Active provisional holds still open
    active_provisional_holds: int = 0


@dataclass
class TimelineDataPoint:
    """Hourly or daily data point for the recovery timeline chart."""

    period_start: str = ""  # ISO 8601 UTC timestamp
    eur_frozen: float = 0.0
    eur_recovered: float = 0.0
    event_count: int = 0
    avg_mttr_minutes: float = 0.0


@dataclass
class TypologyBreakdown:
    """Per-typology ROI and volume breakdown."""

    typology: str = ""
    event_count: int = 0
    total_eur: float = 0.0
    avg_mttr_minutes: float = 0.0
    containment_rate: float = 0.0
    risk_label: str = "MEDIUM"  # LOW | MEDIUM | HIGH | CRITICAL


# ── Service ────────────────────────────────────────────────────────────────────


class AssetRecoveryService:
    """Aggregates asset recovery KPIs and ROI metrics for the operational hub.

    Thread-safe in-memory store (production deployments replace with async DB
    repository following the existing clean architecture pattern).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, RecoveryEvent] = {}
        self._audit_head: str = "0" * 64  # SHA-256 genesis hash
        self._audit_seq: int = 0
        self._seed_demonstration_data()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _sha256_chain(self, previous: str, payload: str) -> str:
        raw = f"{previous}|{self._audit_seq}|{payload}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _record_event(self, event: RecoveryEvent) -> None:
        """Append a recovery event with hash-chained audit entry."""
        with self._lock:
            payload = (
                f"{event.event_id}|{event.event_type}|{event.amount_eur}"
                f"|{event.typology}|{event.alert_raised_at.isoformat()}"
            )
            event.audit_hash = self._sha256_chain(self._audit_head, payload)
            self._audit_head = event.audit_hash
            self._audit_seq += 1
            self._events[event.event_id] = event

    def _seed_demonstration_data(self) -> None:
        """Seed realistic demonstration events spanning the last 30 days.

        All amounts and timing are generated from deterministic offsets to
        ensure reproducible KPIs for the SaaS landing presentation without
        any mock randomness.
        """
        now = datetime.now(UTC)

        seed_events: list[dict[str, Any]] = [
            # APP fraud mule chain — rapid freeze within minutes
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("18450.00"),
                "typology": "app_fraud_mule_chain",
                "originating_bank_id": "bank_alpha",
                "receiving_bank_id": "bank_beta",
                "alert_offset_hours": -720,
                "freeze_offset_minutes": 9,
                "recall_message_id": "CAMT056-2026-001-ALPHA",
                "finint_ticket_id": "FININT-2026-0001",
            },
            {
                "event_type": "PROVISIONAL_HOLD",
                "amount_eur": Decimal("73200.00"),
                "typology": "app_fraud_mule_chain",
                "originating_bank_id": "bank_beta",
                "receiving_bank_id": "bank_gamma",
                "alert_offset_hours": -680,
                "freeze_offset_minutes": 14,
                "recall_message_id": "CAMT056-2026-002-BETA",
                "finint_ticket_id": "FININT-2026-0002",
            },
            # Smurfing below €10,000 — multiple small recalls
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("9870.00"),
                "typology": "smurfing_structuring",
                "originating_bank_id": "bank_alpha",
                "receiving_bank_id": "bank_beta",
                "alert_offset_hours": -600,
                "freeze_offset_minutes": 22,
                "recall_message_id": "CAMT056-2026-003-ALPHA",
                "finint_ticket_id": "FININT-2026-0003",
            },
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("9540.00"),
                "typology": "smurfing_structuring",
                "originating_bank_id": "bank_beta",
                "receiving_bank_id": "bank_alpha",
                "alert_offset_hours": -540,
                "freeze_offset_minutes": 18,
                "recall_message_id": "CAMT056-2026-004-BETA",
                "finint_ticket_id": "FININT-2026-0004",
            },
            # Dormant burst velocity — large single transfer
            {
                "event_type": "PROVISIONAL_HOLD",
                "amount_eur": Decimal("142000.00"),
                "typology": "dormant_burst_velocity",
                "originating_bank_id": "bank_gamma",
                "receiving_bank_id": "bank_alpha",
                "alert_offset_hours": -480,
                "freeze_offset_minutes": 31,
                "recall_message_id": "CAMT056-2026-005-GAMMA",
                "finint_ticket_id": "FININT-2026-0005",
            },
            # High-risk corridor flight — cross-border EUR transfer
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("56750.00"),
                "typology": "high_risk_corridor_flight",
                "originating_bank_id": "bank_alpha",
                "receiving_bank_id": "bank_gamma",
                "alert_offset_hours": -400,
                "freeze_offset_minutes": 43,
                "recall_message_id": "CAMT056-2026-006-ALPHA",
                "finint_ticket_id": "FININT-2026-0006",
            },
            # Rapid pass-through mule
            {
                "event_type": "PARTIAL_RECOVERY",
                "amount_eur": Decimal("28300.00"),
                "typology": "rapid_pass_through",
                "originating_bank_id": "bank_beta",
                "receiving_bank_id": "bank_gamma",
                "alert_offset_hours": -336,
                "freeze_offset_minutes": 67,
                "recall_message_id": "CAMT056-2026-007-BETA",
                "finint_ticket_id": "FININT-2026-0007",
            },
            # Invoice manipulation
            {
                "event_type": "PROVISIONAL_HOLD",
                "amount_eur": Decimal("95600.00"),
                "typology": "invoice_manipulation",
                "originating_bank_id": "bank_gamma",
                "receiving_bank_id": "bank_beta",
                "alert_offset_hours": -240,
                "freeze_offset_minutes": 28,
                "recall_message_id": "CAMT056-2026-008-GAMMA",
                "finint_ticket_id": "FININT-2026-0008",
            },
            # Crypto gateway cashout — still under provisional hold
            {
                "event_type": "PROVISIONAL_HOLD",
                "amount_eur": Decimal("211400.00"),
                "typology": "crypto_gateway_cashout",
                "originating_bank_id": "bank_alpha",
                "receiving_bank_id": "bank_gamma",
                "alert_offset_hours": -120,
                "freeze_offset_minutes": 8,
                "recall_message_id": "CAMT056-2026-009-ALPHA",
                "finint_ticket_id": "FININT-2026-0009",
            },
            # Round-tripping scheme
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("44800.00"),
                "typology": "round_tripping",
                "originating_bank_id": "bank_beta",
                "receiving_bank_id": "bank_alpha",
                "alert_offset_hours": -48,
                "freeze_offset_minutes": 19,
                "recall_message_id": "CAMT056-2026-010-BETA",
                "finint_ticket_id": "FININT-2026-0010",
            },
            # Latest APP fraud — very recent
            {
                "event_type": "RECALL_SUCCESS",
                "amount_eur": Decimal("37200.00"),
                "typology": "app_fraud_mule_chain",
                "originating_bank_id": "bank_gamma",
                "receiving_bank_id": "bank_beta",
                "alert_offset_hours": -12,
                "freeze_offset_minutes": 11,
                "recall_message_id": "CAMT056-2026-011-GAMMA",
                "finint_ticket_id": "FININT-2026-0011",
            },
        ]

        for seed in seed_events:
            alert_at = now + timedelta(hours=seed["alert_offset_hours"])  # type: ignore[operator]
            freeze_at = alert_at + timedelta(minutes=seed["freeze_offset_minutes"])  # type: ignore[operator]
            evt = RecoveryEvent(
                event_type=str(seed["event_type"]),
                amount_eur=Decimal(str(seed["amount_eur"])),
                typology=str(seed["typology"]),
                originating_bank_id=str(seed["originating_bank_id"]),
                receiving_bank_id=str(seed["receiving_bank_id"]),
                alert_raised_at=alert_at,
                freeze_confirmed_at=freeze_at,
                recall_message_id=str(seed["recall_message_id"]),
                finint_ticket_id=str(seed["finint_ticket_id"]),
            )
            evt.mttr_minutes = evt.compute_mttr()
            self._record_event(evt)

        logger.info(
            "AssetRecoveryService: seeded %d demonstration events",
            len(seed_events),
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def record_recovery_event(
        self,
        event_type: str,
        amount_eur: str,
        typology: str,
        originating_bank_id: str,
        receiving_bank_id: str,
        recall_message_id: str = "",
        finint_ticket_id: str = "",
        alert_raised_at: datetime | None = None,
        freeze_confirmed_at: datetime | None = None,
    ) -> RecoveryEvent:
        """Record a new asset recovery or provisional hold event.

        Args:
            event_type: RECALL_SUCCESS | PROVISIONAL_HOLD | PARTIAL_RECOVERY
            amount_eur: EUR amount string with up to 2 decimal places.
            typology: AML typology label (see _KNOWN_TYPOLOGIES).
            originating_bank_id: Consortium bank that raised the alert.
            receiving_bank_id: Consortium bank holding the frozen funds.
            recall_message_id: ISO 20022 camt.056 MsgId reference.
            finint_ticket_id: Cross-bank FININT case ticket reference.
            alert_raised_at: When the underlying alert was raised (UTC).
            freeze_confirmed_at: When the freeze was confirmed (UTC).

        Returns:
            The created RecoveryEvent with computed MTTR and audit hash.
        """
        if event_type not in {"RECALL_SUCCESS", "PROVISIONAL_HOLD", "PARTIAL_RECOVERY"}:
            raise ValueError(
                f"Invalid event_type {event_type!r}. "
                "Must be RECALL_SUCCESS | PROVISIONAL_HOLD | PARTIAL_RECOVERY."
            )
        if typology not in _KNOWN_TYPOLOGIES:
            raise ValueError(
                f"Unknown typology {typology!r}. "
                f"Must be one of: {', '.join(_KNOWN_TYPOLOGIES)}"
            )
        try:
            amount = Decimal(amount_eur)
        except Exception as exc:
            raise ValueError(f"Invalid EUR amount: {amount_eur!r}") from exc
        if amount <= 0:
            raise ValueError(f"EUR amount must be positive, got {amount_eur!r}")

        now = datetime.now(UTC)
        evt = RecoveryEvent(
            event_type=event_type,
            amount_eur=amount,
            typology=typology,
            originating_bank_id=originating_bank_id,
            receiving_bank_id=receiving_bank_id,
            alert_raised_at=alert_raised_at or now,
            freeze_confirmed_at=freeze_confirmed_at,
            recall_message_id=recall_message_id,
            finint_ticket_id=finint_ticket_id,
        )
        evt.mttr_minutes = evt.compute_mttr()
        self._record_event(evt)
        logger.info(
            "AssetRecoveryService: recorded %s event %s — €%s — %s",
            event_type,
            evt.event_id,
            amount_eur,
            typology,
        )
        return evt

    def get_summary(self) -> AssetRecoverySummary:
        """Compute and return aggregated KPI snapshot."""
        with self._lock:
            events = list(self._events.values())

        if not events:
            return AssetRecoverySummary()

        total_frozen = Decimal("0.00")
        total_recovered = Decimal("0.00")
        mttr_samples: list[float] = []
        active_holds = 0
        mule_banks: set[str] = set()

        for evt in events:
            total_frozen += evt.amount_eur
            if evt.event_type == "RECALL_SUCCESS":
                total_recovered += evt.amount_eur
            if evt.event_type == "PROVISIONAL_HOLD":
                active_holds += 1
            if evt.mttr_minutes is not None:
                mttr_samples.append(evt.mttr_minutes)
            mule_banks.add(evt.originating_bank_id)
            mule_banks.add(evt.receiving_bank_id)

        # MTTR statistics
        mttr_mean = 0.0
        mttr_p50 = 0.0
        mttr_p90 = 0.0
        mttr_p99 = 0.0
        if mttr_samples:
            sorted_mttr = sorted(mttr_samples)
            n = len(sorted_mttr)
            mttr_mean = sum(sorted_mttr) / n
            mttr_p50 = sorted_mttr[int(n * 0.50)]
            mttr_p90 = sorted_mttr[min(int(n * 0.90), n - 1)]
            mttr_p99 = sorted_mttr[min(int(n * 0.99), n - 1)]

        # MTTR reduction vs. legacy 48-hour bilateral baseline
        mttr_reduction = 0.0
        if mttr_mean > 0:
            mttr_reduction = max(
                0.0,
                (_LEGACY_BASELINE_MINUTES - mttr_mean) / _LEGACY_BASELINE_MINUTES * 100.0,
            )

        # Contagion containment: fraction of events where MTTR < 60 minutes
        fast_responses = sum(1 for m in mttr_samples if m < 60.0)
        containment = fast_responses / len(mttr_samples) if mttr_samples else 0.0

        # Count unique mule chains (approximated by unique finint_ticket_id)
        mule_chains = len({e.finint_ticket_id for e in events if e.finint_ticket_id})

        return AssetRecoverySummary(
            snapshot_at=datetime.now(UTC),
            total_events=len(events),
            total_eur_frozen=total_frozen,
            total_eur_recovered=total_recovered,
            contagion_containment_rate=round(containment, 4),
            mttr_mean_minutes=round(mttr_mean, 2),
            mttr_p50_minutes=round(mttr_p50, 2),
            mttr_p90_minutes=round(mttr_p90, 2),
            mttr_p99_minutes=round(mttr_p99, 2),
            legacy_baseline_minutes=_LEGACY_BASELINE_MINUTES,
            mttr_reduction_pct=round(mttr_reduction, 2),
            mule_chains_disrupted=mule_chains,
            consortium_banks_active=len(mule_banks),
            active_provisional_holds=active_holds,
        )

    def get_timeline(self, window_hours: int = 720) -> list[TimelineDataPoint]:
        """Return hourly aggregated recovery data for the last *window_hours* hours.

        Args:
            window_hours: Lookback window in hours (default 720 = 30 days).

        Returns:
            Ordered list of TimelineDataPoint (oldest → newest).
        """
        with self._lock:
            events = list(self._events.values())

        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=window_hours)

        # Build hourly buckets
        bucket_size_hours = max(1, window_hours // 30)  # ~30 data points
        buckets: dict[int, list[RecoveryEvent]] = {}

        for evt in events:
            if evt.alert_raised_at < cutoff:
                continue
            age_hours = int((now - evt.alert_raised_at).total_seconds() / 3600)
            bucket_idx = age_hours // bucket_size_hours
            buckets.setdefault(bucket_idx, []).append(evt)

        # Sort buckets descending age → ascending chronological
        result: list[TimelineDataPoint] = []
        for bucket_idx in sorted(buckets.keys(), reverse=True):
            bucket_events = buckets[bucket_idx]
            period_start = now - timedelta(hours=(bucket_idx + 1) * bucket_size_hours)
            frozen = sum(float(e.amount_eur) for e in bucket_events)
            recovered = sum(
                float(e.amount_eur)
                for e in bucket_events
                if e.event_type == "RECALL_SUCCESS"
            )
            mttr_vals = [e.mttr_minutes for e in bucket_events if e.mttr_minutes is not None]
            avg_mttr = sum(mttr_vals) / len(mttr_vals) if mttr_vals else 0.0
            result.append(
                TimelineDataPoint(
                    period_start=period_start.isoformat(),
                    eur_frozen=round(frozen, 2),
                    eur_recovered=round(recovered, 2),
                    event_count=len(bucket_events),
                    avg_mttr_minutes=round(avg_mttr, 2),
                )
            )

        return result

    def get_breakdown_by_typology(self) -> list[TypologyBreakdown]:
        """Return per-typology ROI and MTTR breakdown.

        Returns:
            List of TypologyBreakdown ordered by total EUR descending.
        """
        with self._lock:
            events = list(self._events.values())

        # Group events by typology
        groups: dict[str, list[RecoveryEvent]] = {}
        for evt in events:
            groups.setdefault(evt.typology, []).append(evt)

        # Risk classification thresholds (EUR)
        def _risk_label(total_eur: float) -> str:
            if total_eur >= 200_000:
                return "CRITICAL"
            if total_eur >= 50_000:
                return "HIGH"
            if total_eur >= 10_000:
                return "MEDIUM"
            return "LOW"

        breakdowns: list[TypologyBreakdown] = []
        for typology, grp in groups.items():
            total = sum(float(e.amount_eur) for e in grp)
            mttr_vals = [e.mttr_minutes for e in grp if e.mttr_minutes is not None]
            avg_mttr = sum(mttr_vals) / len(mttr_vals) if mttr_vals else 0.0
            fast = sum(1 for m in mttr_vals if m < 60.0)
            containment = fast / len(mttr_vals) if mttr_vals else 0.0
            breakdowns.append(
                TypologyBreakdown(
                    typology=typology,
                    event_count=len(grp),
                    total_eur=round(total, 2),
                    avg_mttr_minutes=round(avg_mttr, 2),
                    containment_rate=round(containment, 4),
                    risk_label=_risk_label(total),
                )
            )

        return sorted(breakdowns, key=lambda b: b.total_eur, reverse=True)

    def get_all_events(self) -> list[RecoveryEvent]:
        """Return a snapshot of all stored recovery events (newest first)."""
        with self._lock:
            events = list(self._events.values())
        return sorted(events, key=lambda e: e.alert_raised_at, reverse=True)


# ── Module-level singleton ─────────────────────────────────────────────────────

_service_instance: AssetRecoveryService | None = None
_init_lock = threading.Lock()


def get_asset_recovery_service() -> AssetRecoveryService:
    """Return the module-level singleton AssetRecoveryService."""
    global _service_instance
    if _service_instance is None:
        with _init_lock:
            if _service_instance is None:
                _service_instance = AssetRecoveryService()
    return _service_instance
