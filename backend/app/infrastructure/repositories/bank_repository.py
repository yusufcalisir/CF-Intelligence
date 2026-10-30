"""Bank repository - persistence for bank configurations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.infrastructure.models import BankConfigModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BankRepository:
    """Persistence layer for bank configurations within simulations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_simulation(self, simulation_id: str) -> list[dict]:
        """Get all bank configs for a simulation."""
        result = await self.session.execute(
            select(BankConfigModel).where(
                BankConfigModel.simulation_id == simulation_id,
            ),
        )
        return [self._to_dict(m) for m in result.scalars().all()]

    async def get_by_id(self, bank_id: str) -> dict | None:
        """Get a single bank config."""
        result = await self.session.execute(
            select(BankConfigModel).where(BankConfigModel.id == bank_id),
        )
        model = result.scalar_one_or_none()
        return self._to_dict(model) if model else None

    async def save_banks(self, simulation_id: str, banks_data: list[dict]) -> None:
        """Persist bank configurations for a simulation."""
        for bank in banks_data:
            model = BankConfigModel(
                id=bank["id"],
                simulation_id=simulation_id,
                name=bank["name"],
                tier=bank["tier"],
                fraud_ratio=bank["fraud_ratio"],
                num_transactions=bank["num_transactions"],
                data_profile=bank.get("data_profile"),
                local_metrics=bank.get("local_metrics"),
                federated_metrics=bank.get("federated_metrics"),
            )
            self.session.add(model)
        await self.session.commit()

    @staticmethod
    def _to_dict(model: BankConfigModel) -> dict:
        return {
            "id": model.id,
            "simulation_id": model.simulation_id,
            "name": model.name,
            "tier": model.tier,
            "fraud_ratio": model.fraud_ratio,
            "num_transactions": model.num_transactions,
            "data_profile": model.data_profile,
            "local_metrics": model.local_metrics,
            "federated_metrics": model.federated_metrics,
        }
