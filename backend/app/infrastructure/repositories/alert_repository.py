"""Alert repository — production AsyncSession CRUD for AlertModel.

Replaces in-memory dict storage. PostgreSQL is the single source of truth;
Redis is populated by the write-through cache decorator in cache.py.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from app.infrastructure.models import AlertModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AlertRepository:
    """Production persistence layer for fraud alerts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Write ─────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        bank_id: str,
        transaction_id: str,
        risk_score: float,
        severity: str,
        reason_codes: list[str] | None = None,
        confidence: float = 0.0,
        involved_entity_ids: list[str] | None = None,
        top_features: list[dict] | None = None,
        risk_factors: list[str] | None = None,
        model_confidence: float = 0.0,
        historical_evidence: list[dict] | None = None,
        triage_priority: str = "p3_medium",
        triage_action: str = "queue_standard",
        sla_minutes: int = 1440,
        triage_reasons: list[str] | None = None,
        dedup_key: str | None = None,
        dedup_count: int = 1,
    ) -> AlertModel:
        """Persist a new fraud alert and return the saved model."""
        model = AlertModel(
            id=str(uuid.uuid4()),
            bank_id=bank_id,
            transaction_id=transaction_id,
            risk_score=risk_score,
            severity=severity,
            status="new",
            reason_codes=reason_codes or [],
            confidence=confidence,
            involved_entity_ids=involved_entity_ids or [],
            top_features=top_features or [],
            risk_factors=risk_factors or [],
            model_confidence=model_confidence,
            historical_evidence=historical_evidence or [],
            triage_priority=triage_priority,
            triage_action=triage_action,
            sla_minutes=sla_minutes,
            triage_reasons=triage_reasons or [],
            dedup_key=dedup_key,
            dedup_count=dedup_count,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("Alert created id=%s bank_id=%s risk_score=%.3f", model.id, bank_id, risk_score)
        return model

    async def update_status(self, alert_id: str, status: str) -> AlertModel | None:
        """Update alert status and set updated_at timestamp."""
        await self.session.execute(
            update(AlertModel)
            .where(AlertModel.id == alert_id)
            .values(status=status, updated_at=datetime.now(UTC))
        )
        await self.session.commit()
        return await self.get_by_id(alert_id)

    async def delete(self, alert_id: str) -> bool:
        """Delete an alert by ID. Returns True if deleted, False otherwise."""
        result = await self.session.execute(delete(AlertModel).where(AlertModel.id == alert_id))
        await self.session.commit()
        return (result.rowcount or 0) > 0

    # ── Read ──────────────────────────────────────────────────────────

    async def get_by_id(self, alert_id: str) -> AlertModel | None:
        """Fetch a single alert by primary key. Returns None if not found."""
        result = await self.session.execute(select(AlertModel).where(AlertModel.id == alert_id))
        return result.scalar_one_or_none()

    async def get_by_transaction_id(self, transaction_id: str) -> AlertModel | None:
        """Fetch an alert by transaction ID. Returns None if not found."""
        result = await self.session.execute(
            select(AlertModel).where(AlertModel.transaction_id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def list_by_bank(
        self,
        bank_id: str,
        *,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlertModel]:
        """List alerts for a bank, optionally filtered by status and severity."""
        stmt = select(AlertModel).where(AlertModel.bank_id == bank_id)
        if status:
            stmt = stmt.where(AlertModel.status == status)
        if severity:
            stmt = stmt.where(AlertModel.severity == severity)
        stmt = stmt.order_by(AlertModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_bank(
        self, bank_id: str, *, status: str | None = None, severity: str | None = None
    ) -> int:
        """Count alerts for a bank."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(AlertModel).where(AlertModel.bank_id == bank_id)
        if status:
            stmt = stmt.where(AlertModel.status == status)
        if severity:
            stmt = stmt.where(AlertModel.severity == severity)
        result = await self.session.execute(stmt)
        return result.scalar_one()

