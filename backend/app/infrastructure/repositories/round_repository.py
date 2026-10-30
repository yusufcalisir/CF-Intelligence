"""Round repository - production AsyncSession CRUD for FederatedRoundModel
and GradientSubmissionModel.

Manages the full lifecycle of production federated training rounds:
round creation → gradient collection → quorum detection → aggregation → completion.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.infrastructure.models import FederatedRoundModel, GradientSubmissionModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class RoundRepository:
    """Production persistence layer for federated training round lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Round Write ───────────────────────────────────────────────────

    async def create_round(
        self,
        *,
        consortium_id: str,
        round_number: int,
        quorum_required: int = 2,
        aggregation_strategy: str = "fedavg",
    ) -> FederatedRoundModel:
        """Create a new federated round in COLLECTING_GRADIENTS status."""
        model = FederatedRoundModel(
            id=str(uuid.uuid4()),
            consortium_id=consortium_id,
            round_number=round_number,
            status="collecting_gradients",
            submitted_bank_ids=[],
            quorum_required=quorum_required,
            aggregation_strategy=aggregation_strategy,
        )
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        logger.info(
            "Federated round created id=%s consortium=%s round_number=%d",
            model.id,
            consortium_id,
            round_number,
        )
        return model

    async def mark_complete(
        self, round_id: str, global_model_id: str
    ) -> FederatedRoundModel | None:
        """Mark round as COMPLETE and record the resulting global model ID."""
        await self.session.execute(
            update(FederatedRoundModel)
            .where(FederatedRoundModel.id == round_id)
            .values(
                status="complete",
                global_model_id=global_model_id,
                completed_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return await self.get_round_by_id(round_id)

    async def mark_failed(self, round_id: str, reason: str = "") -> None:
        """Mark round as FAILED - used when aggregation fails or quorum is not met."""
        await self.session.execute(
            update(FederatedRoundModel)
            .where(FederatedRoundModel.id == round_id)
            .values(status="failed", completed_at=datetime.now(UTC))
        )
        await self.session.commit()
        logger.warning("Round %s marked failed: %s", round_id, reason)

    # ── Round Read ────────────────────────────────────────────────────

    async def get_round_by_id(self, round_id: str) -> FederatedRoundModel | None:
        """Fetch a round by primary key."""
        result = await self.session.execute(
            select(FederatedRoundModel).where(FederatedRoundModel.id == round_id)
        )
        return result.scalar_one_or_none()

    async def get_active_round(self, consortium_id: str) -> FederatedRoundModel | None:
        """Return the current open round for a consortium, or None if no active round."""
        result = await self.session.execute(
            select(FederatedRoundModel)
            .where(
                FederatedRoundModel.consortium_id == consortium_id,
                FederatedRoundModel.status == "collecting_gradients",
            )
            .order_by(FederatedRoundModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ── Gradient Submission Write ─────────────────────────────────────

    async def record_gradient_submission(
        self,
        *,
        round_id: str,
        bank_id: str,
        gradient_hash: str,
        dp_epsilon_used: float,
        participant_count: int,
        validation_status: str = "accepted",
        rejection_reason: str | None = None,
    ) -> GradientSubmissionModel:
        """Persist a gradient submission record and update the round's submitted_bank_ids list."""
        submission = GradientSubmissionModel(
            id=str(uuid.uuid4()),
            round_id=round_id,
            bank_id=bank_id,
            gradient_hash=gradient_hash,
            dp_epsilon_used=dp_epsilon_used,
            participant_count=participant_count,
            validation_status=validation_status,
            rejection_reason=rejection_reason,
        )
        self.session.add(submission)

        # Append bank_id to the round's submitted_bank_ids JSON list
        if validation_status == "accepted":
            round_model = await self.get_round_by_id(round_id)
            if round_model:
                current_ids = list(round_model.submitted_bank_ids or [])
                if bank_id not in current_ids:
                    current_ids.append(bank_id)
                await self.session.execute(
                    update(FederatedRoundModel)
                    .where(FederatedRoundModel.id == round_id)
                    .values(submitted_bank_ids=current_ids)
                )

        await self.session.commit()
        await self.session.refresh(submission)
        logger.info(
            "Gradient submission recorded round=%s bank=%s status=%s",
            round_id,
            bank_id,
            validation_status,
        )
        return submission

    # ── Gradient Submission Read ──────────────────────────────────────

    async def get_accepted_submissions(self, round_id: str) -> list[GradientSubmissionModel]:
        """Return all ACCEPTED gradient submissions for a round (used by aggregator)."""
        result = await self.session.execute(
            select(GradientSubmissionModel).where(
                GradientSubmissionModel.round_id == round_id,
                GradientSubmissionModel.validation_status == "accepted",
            )
        )
        return list(result.scalars().all())

    async def quorum_reached(self, round_id: str) -> bool:
        """Return True if the number of accepted submissions meets the quorum requirement."""
        round_model = await self.get_round_by_id(round_id)
        if not round_model:
            return False
        accepted_count = len(round_model.submitted_bank_ids or [])
        return accepted_count >= round_model.quorum_required
