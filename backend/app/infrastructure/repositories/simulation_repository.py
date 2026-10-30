"""Simulation repository - persistence for simulation runs.

Handles conversion between domain entities and ORM models.
Manages commits at the repository boundary (simpler for this project;
in a larger system you'd use Unit of Work).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.domain.entities import SimulationRun
from app.domain.enums import SimulationStatus
from app.domain.value_objects import SimulationConfig
from app.infrastructure.models import SimulationRunModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SimulationRepository:
    """Persistence layer for simulation runs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, simulation: SimulationRun) -> SimulationRun:
        """Persist a new simulation run."""
        model = SimulationRunModel(
            id=simulation.id,
            status=simulation.status.value,
            config=asdict(simulation.config),
            current_round=simulation.current_round,
            total_rounds=simulation.total_rounds,
            created_at=simulation.created_at,
            started_at=simulation.started_at,
            completed_at=simulation.completed_at,
            banks_data={},
            rounds_data=[],
        )
        self.session.add(model)
        await self.session.commit()
        logger.info("Created simulation %s", simulation.id)
        return simulation

    async def get_by_id(self, simulation_id: str) -> SimulationRun | None:
        """Retrieve a simulation by ID."""
        result = await self.session.execute(
            select(SimulationRunModel).where(SimulationRunModel.id == simulation_id),
        )
        model = result.scalar_one_or_none()
        if not model:
            return None
        return self._to_entity(model)

    async def list_all(self, limit: int = 20, offset: int = 0) -> list[SimulationRun]:
        """List simulation runs, most recent first."""
        result = await self.session.execute(
            select(SimulationRunModel)
            .order_by(SimulationRunModel.created_at.desc())
            .limit(limit)
            .offset(offset),
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update(self, simulation: SimulationRun) -> SimulationRun:
        """Update an existing simulation run."""
        result = await self.session.execute(
            select(SimulationRunModel).where(SimulationRunModel.id == simulation.id),
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Simulation {simulation.id} not found")

        model.status = simulation.status.value
        model.current_round = simulation.current_round
        model.started_at = simulation.started_at
        model.completed_at = simulation.completed_at
        model.error_message = simulation.error_message

        # Serialize banks and rounds as JSON
        banks_data = {}
        for bank in simulation.banks:
            bank_dict: dict = {
                "id": bank.id,
                "name": bank.name,
                "tier": bank.tier.value,
                "fraud_ratio": bank.fraud_ratio,
                "num_transactions": bank.num_transactions,
                "status": bank.status.value,
            }
            if bank.data_profile:
                bank_dict["data_profile"] = asdict(bank.data_profile)
            if bank.local_metrics:
                bank_dict["local_metrics"] = asdict(bank.local_metrics)
            if bank.federated_metrics:
                bank_dict["federated_metrics"] = asdict(bank.federated_metrics)
            banks_data[bank.id] = bank_dict

        model.banks_data = banks_data
        model.config = asdict(simulation.config)

        await self.session.commit()
        return simulation

    async def delete(self, simulation_id: str) -> bool:
        """Delete a simulation run."""
        result = await self.session.execute(
            select(SimulationRunModel).where(SimulationRunModel.id == simulation_id),
        )
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.commit()
        return True

    @staticmethod
    def _to_entity(model: SimulationRunModel) -> SimulationRun:
        """Convert ORM model to domain entity."""
        from app.domain.entities import Bank
        from app.domain.enums import BankTier, ClientStatus
        from app.domain.value_objects import BankDataProfile, EvaluationMetrics

        config = SimulationConfig(**model.config) if model.config else SimulationConfig()

        banks = []
        for bank_data in (model.banks_data or {}).values():
            bank = Bank(
                id=bank_data["id"],
                name=bank_data["name"],
                tier=BankTier(bank_data["tier"]),
                fraud_ratio=bank_data["fraud_ratio"],
                num_transactions=bank_data["num_transactions"],
                status=ClientStatus(bank_data.get("status", "active")),
            )
            if "data_profile" in bank_data and bank_data["data_profile"]:
                bank.data_profile = BankDataProfile(**bank_data["data_profile"])
            if "local_metrics" in bank_data and bank_data["local_metrics"]:
                bank.local_metrics = EvaluationMetrics(**bank_data["local_metrics"])
            if "federated_metrics" in bank_data and bank_data["federated_metrics"]:
                bank.federated_metrics = EvaluationMetrics(**bank_data["federated_metrics"])
            banks.append(bank)

        return SimulationRun(
            id=model.id,
            status=SimulationStatus(model.status),
            config=config,
            banks=banks,
            created_at=model.created_at,
            started_at=model.started_at,
            completed_at=model.completed_at,
            current_round=model.current_round,
            total_rounds=model.total_rounds,
            error_message=model.error_message,
        )
