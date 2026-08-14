"""Unit tests for ScenarioSimulator and multi-bank fraud scenarios."""

from __future__ import annotations

import pytest

from app.application.services.scenario_service import ScenarioSimulator
from app.domain.enums import ScenarioType


@pytest.fixture
def simulator() -> ScenarioSimulator:
    return ScenarioSimulator(seed=123)


def test_list_available_scenarios(simulator: ScenarioSimulator):
    """Verify list_available_scenarios returns metadata for all 4 scenario types."""
    scenarios = simulator.list_available_scenarios()
    assert len(scenarios) == 4
    types = {s["type"] for s in scenarios}
    assert ScenarioType.FRAUD_RING.value in types
    assert ScenarioType.ACCOUNT_TAKEOVER.value in types
    assert ScenarioType.MONEY_LAUNDERING.value in types
    assert ScenarioType.CARD_TESTING.value in types


@pytest.mark.parametrize(
    "scen_type",
    [
        ScenarioType.FRAUD_RING,
        ScenarioType.ACCOUNT_TAKEOVER,
        ScenarioType.MONEY_LAUNDERING,
        ScenarioType.CARD_TESTING,
    ],
)
def test_create_each_scenario_type(simulator: ScenarioSimulator, scen_type: ScenarioType):
    """Verify each scenario produces valid events, involved banks, and description."""
    scen = simulator.create_scenario(scen_type)
    assert scen.id is not None
    assert scen.name is not None
    assert len(scen.events) > 0
    assert len(scen.banks_involved) >= 2

    # Check that each event has required fields
    for event in scen.events:
        assert event.id is not None
        assert event.bank_id in scen.banks_involved or event.bank_id == "shared"
        assert event.event_type is not None
        assert event.payload is not None
        assert event.delay_ms >= 0

    # Check get_scenario retrieval
    retrieved = simulator.get_scenario(scen.id)
    assert retrieved == scen


def test_invalid_scenario_type_raises(simulator: ScenarioSimulator):
    """Verify passing an unknown scenario type raises ValueError."""
    with pytest.raises(ValueError, match="Unknown scenario type"):
        simulator.create_scenario("non_existent_type")  # type: ignore
