"""Case repository — production AsyncSession CRUD for CaseModel.

Replaces in-memory dict storage. PostgreSQL is the single source of truth.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from app.infrastructure.models import CaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class CaseRepository:
    """Production persistence layer for fraud investigation cases."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Write ─────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        title: str,
        priority: str = "p3_medium",
        alert_ids: list[str] | None = None,
        assigned_to: str | None = None,
        total_risk_score: float = 0.0,
        bank_id: str | None = None,
    ) -> CaseModel:
        """Persist a new investigation case.

        Institutional ownership (bank_id) is optional for standalone unbound cases.
        When provided, it must be validated upstream by the authoritative service layer.
        """
        model = CaseModel(
            id=str(uuid.uuid4()),
            bank_id=bank_id,
            title=title,
            status="open",
            priority=priority,
            assigned_to=assigned_to,
            alert_ids=alert_ids or [],
            evidence_ids=[],
            notes=[],
            timeline=[],
            total_risk_score=total_risk_score,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info(
            "Case created id=%s title=%r priority=%s bank_id=%s",
            model.id,
            title,
            priority,
            bank_id,
        )
        return model

    async def update_status(self, case_id: str, status: str) -> CaseModel | None:
        """Update case status. Sets closed_at when status is a terminal state."""
        values: dict = {"status": status, "updated_at": datetime.now(UTC)}
        terminal_statuses = {
            "resolved_confirmed_fraud",
            "resolved_false_positive",
            "closed",
            "closed_confirmed",
            "closed_false_positive",
        }
        if status in terminal_statuses:
            values["closed_at"] = datetime.now(UTC)
        case = await self.get_by_id(case_id)
        if case is not None:
            case.status = status
            if "closed_at" in values:
                case.closed_at = values["closed_at"]
        await self.session.execute(
            update(CaseModel).where(CaseModel.id == case_id).values(**values)
        )
        await self.session.commit()
        return await self.get_by_id(case_id)

    async def link_alert(
        self, case_id: str, alert_id: str, bank_id: str | None = None
    ) -> CaseModel | None:
        """Link an alert ID to the case.

        Preconditions:
            - bank_id, if provided, must represent the verified institutional owner of the alert,
              authorized by an upstream service/API boundary.

        Semantics:
            - If case does not exist: returns None.
            - If case.bank_id is already set and bank_id is provided:
                - If case.bank_id != bank_id: raises ValueError for cross-tenant conflict.
            - If case.bank_id is unset/None and bank_id is provided:
                - Binds case.bank_id = bank_id.
            - If alert_id is not already in case.alert_ids:
                - Appends alert_id (no duplicates).
            - Idempotent: repeated links with same alert and compatible bank_id do not duplicate
              alert_ids or corrupt state.
        """
        case = await self.get_by_id(case_id)
        if case is None:
            return None

        # Cross-tenant check: if case already bound to a different institution
        if case.bank_id is not None and bank_id is not None and case.bank_id != bank_id:
            logger.warning(
                "Cross-tenant alert link rejected: case_id=%s owned by %s, alert %s bank is %s",
                case_id,
                case.bank_id,
                alert_id,
                bank_id,
            )
            raise ValueError(
                f"Cross-tenant alert linking rejected: case '{case_id}' belongs to bank '{case.bank_id}', "
                f"cannot link alert '{alert_id}' from bank '{bank_id}'."
            )

        alert_ids = list(case.alert_ids or [])
        values_to_update: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        needs_update = False

        if alert_id not in alert_ids:
            alert_ids.append(alert_id)
            case.alert_ids = alert_ids
            values_to_update["alert_ids"] = alert_ids
            needs_update = True

        if bank_id is not None and case.bank_id is None:
            case.bank_id = bank_id
            values_to_update["bank_id"] = bank_id
            needs_update = True

        if needs_update:
            await self.session.execute(
                update(CaseModel)
                .where(CaseModel.id == case_id)
                .values(**values_to_update)
            )
            await self.session.commit()

        return await self.get_by_id(case_id)

    async def assign_to(self, case_id: str, investigator: str) -> CaseModel | None:
        """Assign or reassign a case to an investigator."""
        await self.session.execute(
            update(CaseModel)
            .where(CaseModel.id == case_id)
            .values(assigned_to=investigator, updated_at=datetime.now(UTC))
        )
        await self.session.commit()
        return await self.get_by_id(case_id)

    async def append_note(self, case_id: str, note: dict) -> CaseModel | None:
        """Append a note entry to the case notes JSON list."""
        case = await self.get_by_id(case_id)
        if case is None:
            return None
        notes = list(case.notes or [])
        notes.append({**note, "timestamp": datetime.now(UTC).isoformat()})
        await self.session.execute(
            update(CaseModel)
            .where(CaseModel.id == case_id)
            .values(notes=notes, updated_at=datetime.now(UTC))
        )
        await self.session.commit()
        return await self.get_by_id(case_id)

    # ── Read ──────────────────────────────────────────────────────────

    async def get_by_id(self, case_id: str) -> CaseModel | None:
        """Fetch a single case by primary key."""
        result = await self.session.execute(select(CaseModel).where(CaseModel.id == case_id))
        return result.scalar_one_or_none()

    async def list_by_assignee(
        self, investigator: str, *, status: str | None = None, limit: int = 50
    ) -> list[CaseModel]:
        """List cases assigned to an investigator, optionally filtered by status."""
        stmt = select(CaseModel).where(CaseModel.assigned_to == investigator)
        if status:
            stmt = stmt.where(CaseModel.status == status)
        stmt = stmt.order_by(CaseModel.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_bank(
        self, bank_id: str, *, status: str | None = None, limit: int = 50
    ) -> list[CaseModel]:
        """List cases belonging to a specific institution (bank_id), optionally filtered by status."""
        stmt = select(CaseModel).where(CaseModel.bank_id == bank_id)
        if status:
            stmt = stmt.where(CaseModel.status == status)
        stmt = stmt.order_by(CaseModel.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_open(self, *, limit: int = 100) -> list[CaseModel]:
        """List all open cases ordered by risk score descending."""
        result = await self.session.execute(
            select(CaseModel)
            .where(CaseModel.status == "open")
            .order_by(CaseModel.total_risk_score.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
