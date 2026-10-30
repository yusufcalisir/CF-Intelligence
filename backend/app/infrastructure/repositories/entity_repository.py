"""Entity repository - production AsyncSession CRUD for EntityModel.

Replaces in-memory dict + hash index. PostgreSQL is the single source of truth.
privacy_id is the HMAC-SHA256 hash of the raw customer identifier - never the
raw value.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.infrastructure.models import EntityModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class EntityRepository:
    """Production persistence layer for privacy-preserving entity records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Write ─────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        entity_type: str,
        privacy_id: str,
        bank_id: str,
        display_label: str,
        attributes: dict | None = None,
        risk_level: str = "minimal",
    ) -> EntityModel:
        """Persist a new entity record."""
        model = EntityModel(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            privacy_id=privacy_id,
            bank_id=bank_id,
            display_label=display_label,
            attributes=attributes or {},
            risk_level=risk_level,
            alert_count=0,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info("Entity created id=%s type=%s bank_id=%s", model.id, entity_type, bank_id)
        return model

    async def update_risk_score(
        self, entity_id: str, risk_level: str, alert_count: int
    ) -> EntityModel | None:
        """Update risk level and alert count, refresh last_seen timestamp."""
        await self.session.execute(
            update(EntityModel)
            .where(EntityModel.id == entity_id)
            .values(
                risk_level=risk_level,
                alert_count=alert_count,
                last_seen=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return await self.get_by_id(entity_id)

    async def increment_alert_count(self, entity_id: str) -> None:
        """Atomically increment the alert counter for an entity."""
        from sqlalchemy import text

        await self.session.execute(
            text(
                "UPDATE entities SET alert_count = alert_count + 1, "
                "last_seen = now() WHERE id = :entity_id"
            ),
            {"entity_id": entity_id},
        )
        await self.session.commit()

    # ── Read ──────────────────────────────────────────────────────────

    async def get_by_id(self, entity_id: str) -> EntityModel | None:
        """Fetch a single entity by primary key."""
        result = await self.session.execute(select(EntityModel).where(EntityModel.id == entity_id))
        return result.scalar_one_or_none()

    async def get_by_privacy_id(self, privacy_id: str) -> EntityModel | None:
        """Look up an entity by its HMAC-SHA256 privacy hash.

        This is the primary lookup path - raw customer identifiers are never
        stored or passed through the repository layer.
        """
        result = await self.session.execute(
            select(EntityModel).where(EntityModel.privacy_id == privacy_id)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        bank_id: str,
        *,
        risk_level: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityModel]:
        """List entities for a bank, with optional risk/type filters."""
        stmt = select(EntityModel).where(EntityModel.bank_id == bank_id)
        if risk_level:
            stmt = stmt.where(EntityModel.risk_level == risk_level)
        if entity_type:
            stmt = stmt.where(EntityModel.entity_type == entity_type)
        stmt = stmt.order_by(EntityModel.alert_count.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
