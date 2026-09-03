"""Unit tests for the live attack injector endpoint in scenarios router."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_inject_byzantine_poisoning_attack() -> None:
    """Verify Byzantine gradient poisoning attack triggers Krum quarantine of Bank Gamma."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/scenarios/inject-attack",
            json={
                "attack_type": "byzantine_poisoning",
                "adversary_bank": "bank_gamma",
                "target_bank": "bank_alpha",
                "intensity_rate": 500,
                "defense_strategy": "krum",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["attack_type"] == "byzantine_poisoning"
        assert data["status"] == "quarantined"
        assert data["adversary_quarantined"] == "bank_gamma"
        assert data["euclidean_distance"] > data["distance_threshold"]
        assert "Krum" in data["defense_activated"]
        assert data["auc_protected"] > 0.90
        assert data["auc_compromised_baseline"] < 0.60


@pytest.mark.asyncio
async def test_inject_smurfing_burst_attack() -> None:
    """Verify 500 tx/s smurfing burst attack triggers GraphSAGE & LSH-PSI interception."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/scenarios/inject-attack",
            json={
                "attack_type": "smurfing_layering",
                "adversary_bank": "bank_gamma",
                "target_bank": "bank_alpha",
                "intensity_rate": 500,
                "defense_strategy": "psi_graph",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["attack_type"] == "smurfing_layering"
        assert data["status"] == "intercepted"
        assert data["packets_blocked"] >= 1500
        assert "GraphSAGE" in data["defense_activated"]
        assert data["auc_protected"] > 0.90


@pytest.mark.asyncio
async def test_inject_invalid_attack_type_validation_error() -> None:
    """Verify invalid attack type fails Pydantic schema validation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/scenarios/inject-attack",
            json={
                "attack_type": "non_existent_exploit",
                "intensity_rate": 500,
            },
        )
        assert response.status_code == 422
