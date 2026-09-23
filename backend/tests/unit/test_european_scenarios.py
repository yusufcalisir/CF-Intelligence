"""Comprehensive unit and integration test suite for European AML Monitoring Scenarios & Hybrid Rule Engine.

Validates:
1. Scenario library completeness (all 16 European typologies and regulatory citations).
2. Deterministic rule evaluations for every scenario.
3. Benign transaction execution resulting in clean ALLOW decisions.
4. Hybrid scoring synthesizer blending rule penalties with ML probabilities.
5. Strict regulatory override policy on Sanctions and FATF black-list triggers.
6. Batch evaluation and multi-tenant telemetry isolation.
7. FastAPI presentation router endpoints via TestClient.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.application.schemas.scenario_schemas import (
    AMLScenarioEvaluationRequest,
    BatchScenarioEvaluationRequest,
    HybridAction,
    ScenarioCategory,
    ScenarioSeverity,
    TransactionContext,
)
from app.application.services.european_scenario_library import (
    EuropeanScenarioLibraryService,
)
from app.main import app


@pytest.fixture
def scenario_service() -> EuropeanScenarioLibraryService:
    """Fresh instance of EuropeanScenarioLibraryService."""
    return EuropeanScenarioLibraryService()


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient instance."""
    return TestClient(app)


def _create_base_context(**kwargs: Any) -> TransactionContext:
    """Helper to create a standard benign transaction context."""
    base = TransactionContext(
        transaction_id="TX-BASE-001",
        amount=250.0,
        currency="EUR",
        originator_id="CUST-ALICE-100",
        beneficiary_id="CUST-BOB-200",
        origin_country="DE",
        destination_country="FR",
        payment_rail="SEPA_INSTANT",
        originator_account_age_days=180,
        originator_is_pep=False,
        originator_is_sanctioned=False,
        beneficiary_is_pep=False,
        beneficiary_is_sanctioned=False,
        is_dormant_account=False,
        account_average_daily_volume=500.0,
        inbound_credits_last_1h=250.0,
        outbound_debits_last_1h=0.0,
        transaction_count_last_1h=1,
        recent_distinct_counterparties_24h=1,
        funds_retention_ratio=1.0,
        merchant_category="retail_groceries",
        is_nighttime_execution=False,
        is_crypto_service_provider=False,
        unit_price_deviation_ratio=None,
        cyclic_mule_hops=None,
    )
    if kwargs:
        return base.model_copy(update=kwargs)
    return base


class TestEuropeanScenarioLibrary:
    """Unit tests for the 16 European AML scenarios and hybrid engine."""

    def test_scenario_library_completeness(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Verify all 16 scenarios are loaded with valid attributes and citations."""
        library = scenario_service.get_library()
        assert library.total_scenarios == 16
        assert len(library.scenarios) == 16

        expected_codes = {
            "SCN_EUR_STRUCTURING_SUB_10K",
            "SCN_DORMANT_BURST_VELOCITY",
            "SCN_RAPID_PASSTHROUGH_MULE",
            "SCN_HIGH_RISK_FATF_CORRIDOR",
            "SCN_ROUND_AMOUNT_LAYERING",
            "SCN_RAPID_FAN_OUT_DISPERSAL",
            "SCN_RAPID_FAN_IN_AGGREGATION",
            "SCN_OFFSHORE_SHELL_ROUNDTRIP",
            "SCN_PEP_SANCTION_EXPOSURE",
            "SCN_CIRCULAR_MULE_RING",
            "SCN_CRYPTO_ON_OFF_RAMP_BURST",
            "SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN",
            "SCN_HIGH_VELOCITY_NIGHTTIME",
            "SCN_TRADE_OVER_UNDER_INVOICING",
            "SCN_CASINO_GAMBLING_BURST",
            "SCN_LARGE_CASH_OR_INSTANT_SURGE",
        }
        actual_codes = {s.scenario_code for s in library.scenarios}
        assert expected_codes == actual_codes

        for s in library.scenarios:
            assert s.name
            assert s.regulatory_basis
            assert s.base_penalty > 0.0
            assert s.severity in ScenarioSeverity
            assert s.category in ScenarioCategory

    def test_benign_transaction_clears_all_scenarios(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """A normal retail payment must trigger zero scenarios and be allowed."""
        ctx = _create_base_context(amount=120.0, currency="EUR")
        req = AMLScenarioEvaluationRequest(transaction=ctx, ml_risk_score=0.05)
        res = scenario_service.evaluate_transaction("bank_alpha", req)

        assert res.total_scenarios_triggered == 0
        assert res.rule_penalty_score == 0.0
        assert res.action == HybridAction.ALLOW
        assert not res.regulatory_override_applied
        assert "cleared all 16 European AML scenarios" in res.explainability_narrative

    def test_structuring_sub_10k_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Amounts between €8,000 and €9,999.99 must trigger sub-10k structuring."""
        # Triggers
        ctx = _create_base_context(amount=9500.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_EUR_STRUCTURING_SUB_10K" in hits

        # Does not trigger below threshold
        ctx_low = _create_base_context(amount=7500.0)
        res_low = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx_low))
        assert "SCN_EUR_STRUCTURING_SUB_10K" not in [h.scenario_code for h in res_low.triggered_scenarios]

    def test_dormant_burst_velocity_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Dormant account reactivation with high amount triggers."""
        ctx = _create_base_context(is_dormant_account=True, amount=6000.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_DORMANT_BURST_VELOCITY" in hits

    def test_rapid_passthrough_mule_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Low funds retention with rapid outbound drainage triggers pass-through mule."""
        ctx = _create_base_context(
            amount=4000.0,
            inbound_credits_last_1h=4000.0,
            outbound_debits_last_1h=3800.0,
            funds_retention_ratio=0.05,
        )
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_RAPID_PASSTHROUGH_MULE" in hits

    def test_fatf_corridor_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Corridors involving FATF high-risk jurisdictions trigger critical scenario."""
        ctx = _create_base_context(origin_country="KP", destination_country="DE")
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_HIGH_RISK_FATF_CORRIDOR" in hits
        assert res.regulatory_override_applied
        assert res.action == HybridAction.BLOCK

    def test_round_amount_layering_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Multiples of €1,000/€5,000 trigger round-amount layering."""
        ctx = _create_base_context(amount=10000.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_ROUND_AMOUNT_LAYERING" in hits

    def test_rapid_fan_out_dispersal_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Dispersal across >= 4 counterparties with low retention triggers fan-out."""
        ctx = _create_base_context(
            recent_distinct_counterparties_24h=6,
            funds_retention_ratio=0.10,
        )
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_RAPID_FAN_OUT_DISPERSAL" in hits

    def test_rapid_fan_in_aggregation_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Aggregation from >= 4 counterparties totaling >= €5,000 triggers fan-in."""
        ctx = _create_base_context(
            recent_distinct_counterparties_24h=5,
            inbound_credits_last_1h=8500.0,
        )
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_RAPID_FAN_IN_AGGREGATION" in hits

    def test_offshore_shell_roundtrip_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """High-value transfers to offshore centers trigger offshore shell alert."""
        ctx = _create_base_context(
            destination_country="VG",
            amount=20000.0,
        )
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_OFFSHORE_SHELL_ROUNDTRIP" in hits

    def test_pep_and_sanctions_exposure_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """PEP or Sanctions exposure triggers critical risk and mandatory freeze."""
        # Sanctioned trigger
        ctx_sanction = _create_base_context(beneficiary_is_sanctioned=True)
        res_sanction = scenario_service.evaluate_transaction(
            "bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx_sanction)
        )
        assert "SCN_PEP_SANCTION_EXPOSURE" in [h.scenario_code for h in res_sanction.triggered_scenarios]
        assert res_sanction.regulatory_override_applied
        assert res_sanction.action == HybridAction.BLOCK

        # PEP trigger without sanctions
        ctx_pep = _create_base_context(originator_is_pep=True)
        res_pep = scenario_service.evaluate_transaction(
            "bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx_pep, ml_risk_score=0.2)
        )
        assert "SCN_PEP_SANCTION_EXPOSURE" in [h.scenario_code for h in res_pep.triggered_scenarios]

    def test_circular_mule_ring_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Topological circular mule loop triggers critical scenario."""
        ctx = _create_base_context(cyclic_mule_hops=4)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_CIRCULAR_MULE_RING" in hits

    def test_crypto_gateway_burst_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """CASP counterparty with elevated amount triggers digital asset alert."""
        ctx = _create_base_context(is_crypto_service_provider=True, amount=2500.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_CRYPTO_ON_OFF_RAMP_BURST" in hits

    def test_new_account_drain_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Brand new account receiving large sum triggers behavioral anomaly."""
        ctx = _create_base_context(originator_account_age_days=7, amount=18000.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN" in hits

    def test_high_velocity_nighttime_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Nighttime execution with surge volume triggers velocity alert."""
        ctx = _create_base_context(is_nighttime_execution=True, amount=7000.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_HIGH_VELOCITY_NIGHTTIME" in hits

    def test_trade_over_under_invoicing_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Trade price deviation over 3x or under 0.33x triggers TBML alert."""
        # Over-invoicing
        ctx_over = _create_base_context(unit_price_deviation_ratio=3.8)
        res_over = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx_over))
        assert "SCN_TRADE_OVER_UNDER_INVOICING" in [h.scenario_code for h in res_over.triggered_scenarios]

        # Under-invoicing
        ctx_under = _create_base_context(unit_price_deviation_ratio=0.25)
        res_under = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx_under))
        assert "SCN_TRADE_OVER_UNDER_INVOICING" in [h.scenario_code for h in res_under.triggered_scenarios]

    def test_casino_gambling_burst_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Gambling merchant flow with elevated amount triggers casino scenario."""
        ctx = _create_base_context(merchant_category="online_casino_vip", amount=3500.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_CASINO_GAMBLING_BURST" in hits

    def test_large_cash_or_instant_surge_trigger(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """SEPA Instant transfer over €50k triggers extraordinary volume surge."""
        ctx = _create_base_context(payment_rail="SEPA_INSTANT", amount=65000.0)
        res = scenario_service.evaluate_transaction("bank_alpha", AMLScenarioEvaluationRequest(transaction=ctx))
        hits = [h.scenario_code for h in res.triggered_scenarios]
        assert "SCN_LARGE_CASH_OR_INSTANT_SURGE" in hits

    def test_hybrid_synthesizer_blending_and_gnn_boost(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Verifies weighted synthesis between rule penalties and ML probabilities."""
        ctx = _create_base_context(amount=120.0)
        # No rules trigger; ML score = 0.80 -> ml_penalty = 800.0
        # Hybrid score = 0.55 * 0 + 0.45 * 800.0 = 360.0 -> ALLOW
        req = AMLScenarioEvaluationRequest(transaction=ctx, ml_risk_score=0.80)
        res = scenario_service.evaluate_transaction("bank_alpha", req)
        assert res.rule_penalty_score == 0.0
        assert res.ml_penalty_equivalent == 800.0
        assert res.composite_risk_score == 360.0
        assert res.action == HybridAction.ALLOW

        # Add GNN topological boost: norm = 2.0 -> boost = min(150, 60) = 60.0
        # New score = 360.0 + 60.0 = 420.0 -> MANUAL_REVIEW
        req_gnn = AMLScenarioEvaluationRequest(
            transaction=ctx, ml_risk_score=0.80, gnn_anomaly_embedding_norm=2.0
        )
        res_gnn = scenario_service.evaluate_transaction("bank_alpha", req_gnn)
        assert res_gnn.composite_risk_score == 420.0
        assert res_gnn.action == HybridAction.MANUAL_REVIEW

    def test_batch_evaluation_and_telemetry(self, scenario_service: EuropeanScenarioLibraryService) -> None:
        """Batch evaluation aggregates counts accurately and updates telemetry."""
        tx1 = _create_base_context(transaction_id="TX-B1", amount=100.0)  # Benign -> ALLOW
        tx2 = _create_base_context(transaction_id="TX-B2", originator_is_sanctioned=True)  # Sanctions -> BLOCK

        batch_req = BatchScenarioEvaluationRequest(
            evaluations=[
                AMLScenarioEvaluationRequest(transaction=tx1, ml_risk_score=0.0),
                AMLScenarioEvaluationRequest(transaction=tx2, ml_risk_score=0.0),
            ]
        )
        batch_res = scenario_service.evaluate_batch("bank_alpha", batch_req)
        assert batch_res.total_processed == 2
        assert batch_res.total_allowed == 1
        assert batch_res.total_blocked == 1

        # Check telemetry
        metrics = scenario_service.get_metrics("bank_alpha")
        assert metrics.total_evaluations == 2
        assert metrics.action_breakdown.get("BLOCK") == 1
        assert metrics.action_breakdown.get("ALLOW") == 1


class TestEuropeanScenariosRouter:
    """Integration tests for FastAPI REST router endpoints."""

    def test_get_library_api(self, client: TestClient) -> None:
        """GET /api/v1/scenarios/european-aml/library returns all 16 scenarios."""
        resp = client.get("/api/v1/scenarios/european-aml/library")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total_scenarios"] == 16
        assert len(data["scenarios"]) == 16

    def test_get_scenario_by_code_api(self, client: TestClient) -> None:
        """GET /api/v1/scenarios/european-aml/library/{code} returns scenario or 404."""
        resp = client.get("/api/v1/scenarios/european-aml/library/SCN_EUR_STRUCTURING_SUB_10K")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["scenario_code"] == "SCN_EUR_STRUCTURING_SUB_10K"
        assert "EU AMLD6" in data["regulatory_basis"]

        # 404 for unknown code
        resp_404 = client.get("/api/v1/scenarios/european-aml/library/SCN_UNKNOWN_TYPOLOGY")
        assert resp_404.status_code == status.HTTP_404_NOT_FOUND

    def test_evaluate_api(self, client: TestClient) -> None:
        """POST /api/v1/scenarios/european-aml/evaluate processes transaction."""
        payload = {
            "transaction": {
                "transaction_id": "TX-API-001",
                "amount": 9500.0,
                "currency": "EUR",
                "originator_id": "PER-ALICE",
                "beneficiary_id": "PER-BOB",
                "origin_country": "DE",
                "destination_country": "FR",
                "payment_rail": "SEPA_INSTANT",
            },
            "ml_risk_score": 0.35,
            "strict_regulatory_override": True,
        }
        resp = client.post(
            "/api/v1/scenarios/european-aml/evaluate",
            json=payload,
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["transaction_id"] == "TX-API-001"
        assert data["total_scenarios_triggered"] >= 1
        assert "SCN_EUR_STRUCTURING_SUB_10K" in [h["scenario_code"] for h in data["triggered_scenarios"]]

    def test_scenario_test_vector_endpoint(self, client: TestClient) -> None:
        """POST /api/v1/scenarios/european-aml/library/{code}/test triggers target scenario."""
        resp = client.post(
            "/api/v1/scenarios/european-aml/library/SCN_OFFSHORE_SHELL_ROUNDTRIP/test",
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total_scenarios_triggered"] >= 1
        assert "SCN_OFFSHORE_SHELL_ROUNDTRIP" in [h["scenario_code"] for h in data["triggered_scenarios"]]

    def test_metrics_api(self, client: TestClient) -> None:
        """GET /api/v1/scenarios/european-aml/metrics returns telemetry."""
        resp = client.get(
            "/api/v1/scenarios/european-aml/metrics",
            headers={"X-Tenant-ID": "bank_alpha"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["tenant_id"] == "bank_alpha"
        assert "total_evaluations" in data
