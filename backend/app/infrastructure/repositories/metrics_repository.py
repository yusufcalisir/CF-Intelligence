"""Metrics repository - persistence for training round metrics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.infrastructure.models import TrainingRoundModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class MetricsRepository:
    """Persistence layer for training round metrics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_round(self, simulation_id: str, round_data: dict) -> None:
        """Persist a single training round."""
        model = TrainingRoundModel(
            simulation_id=simulation_id,
            round_number=round_data["round_number"],
            global_loss=round_data.get("global_loss", 0.0),
            participating_bank_ids=round_data.get("participating_bank_ids", []),
            dropped_bank_ids=round_data.get("dropped_bank_ids", []),
            per_bank_loss=round_data.get("per_bank_loss", {}),
            per_bank_samples=round_data.get("per_bank_samples", {}),
            aggregation_time_ms=round_data.get("aggregation_time_ms", 0.0),
            round_duration_ms=round_data.get("round_duration_ms", 0.0),
        )
        self.session.add(model)
        await self.session.commit()

    async def save_rounds(self, simulation_id: str, rounds: list[dict]) -> None:
        """Persist multiple training rounds at once."""
        for r in rounds:
            model = TrainingRoundModel(
                simulation_id=simulation_id,
                round_number=r["round_number"],
                global_loss=r.get("global_loss", 0.0),
                participating_bank_ids=r.get("participating_bank_ids", []),
                dropped_bank_ids=r.get("dropped_bank_ids", []),
                per_bank_loss=r.get("per_bank_loss", {}),
                per_bank_samples=r.get("per_bank_samples", {}),
                aggregation_time_ms=r.get("aggregation_time_ms", 0.0),
                round_duration_ms=r.get("round_duration_ms", 0.0),
            )
            self.session.add(model)
        await self.session.commit()

    async def get_by_simulation(self, simulation_id: str) -> list[dict]:
        """Get all rounds for a simulation, ordered by round number."""
        result = await self.session.execute(
            select(TrainingRoundModel)
            .where(TrainingRoundModel.simulation_id == simulation_id)
            .order_by(TrainingRoundModel.round_number),
        )
        return [self._to_dict(m) for m in result.scalars().all()]

    async def get_round(self, simulation_id: str, round_number: int) -> dict | None:
        """Get a specific round."""
        result = await self.session.execute(
            select(TrainingRoundModel).where(
                TrainingRoundModel.simulation_id == simulation_id,
                TrainingRoundModel.round_number == round_number,
            ),
        )
        model = result.scalar_one_or_none()
        return self._to_dict(model) if model else None

    @staticmethod
    def _to_dict(model: TrainingRoundModel) -> dict:
        return {
            "round_number": model.round_number,
            "simulation_id": model.simulation_id,
            "global_loss": model.global_loss,
            "participating_bank_ids": model.participating_bank_ids,
            "dropped_bank_ids": model.dropped_bank_ids,
            "per_bank_loss": model.per_bank_loss,
            "per_bank_samples": model.per_bank_samples,
            "aggregation_time_ms": model.aggregation_time_ms,
            "round_duration_ms": model.round_duration_ms,
        }
