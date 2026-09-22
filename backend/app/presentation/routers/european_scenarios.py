"""European AML Monitoring Scenarios & Hybrid Deterministic Rule Engine Router.

Provides REST endpoints for:
1. Browsing the 16 pre-configured European AML monitoring scenarios.
2. Fetching scenario metadata, parameters, and regulatory citations.
3. Real-time hybrid transaction risk evaluation (Deterministic rules + Federated ML).
4. Batch evaluation for high-throughput stream processing.
5. Testing synthetic scenario test vectors.
6. Operational metrics and trigger distribution telemetry.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.application.schemas.scenario_schemas import (
    AMLScenarioDefinition,
    AMLScenarioEvaluationRequest,
    BatchScenarioEvaluationRequest,
    BatchScenarioEvaluationResponse,
    HybridScoringResponse,
    ScenarioLibraryResponse,
    ScenarioMetricsResponse,
    TransactionContext,
)
from app.application.services.european_scenario_library import (
    EuropeanScenarioLibraryService,
    get_european_scenario_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/scenarios/european-aml", tags=["European AML Monitoring Scenarios"])
api_router = APIRouter(prefix="/api/v1/scenarios/european-aml", tags=["European AML Monitoring Scenarios"])


def _resolve_tenant(
    x_tenant_id: str | None = None,
    x_bank_id: str | None = None,
) -> str:
    """Extract tenant from headers, defaulting to bank_alpha."""
    tenant = x_tenant_id or x_bank_id or "bank_alpha"
    return tenant.strip().lower()


def _register_scenario_endpoints(target_router: APIRouter) -> None:
    """Bind all European scenario endpoints to target router."""

    @target_router.get(
        "/library",
        response_model=ScenarioLibraryResponse,
        status_code=status.HTTP_200_OK,
        summary="List pre-configured European AML monitoring scenarios",
        description="Returns all 16 active scenarios with regulatory citations and threshold configurations.",
    )
    async def get_scenario_library() -> ScenarioLibraryResponse:
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        return service.get_library()

    @target_router.get(
        "/library/{scenario_code}",
        response_model=AMLScenarioDefinition,
        status_code=status.HTTP_200_OK,
        summary="Get scenario definition",
        description="Retrieves operational thresholds, category, severity, and legal basis for a scenario code.",
    )
    async def get_scenario_by_code(scenario_code: str) -> AMLScenarioDefinition:
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        scenario = service.get_scenario(scenario_code.strip().upper())
        if not scenario:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scenario '{scenario_code}' not found in European scenario library.",
            )
        return scenario

    @target_router.post(
        "/evaluate",
        response_model=HybridScoringResponse,
        status_code=status.HTTP_200_OK,
        summary="Evaluate transaction with hybrid rule and ML engine",
        description="Evaluates transaction context against all 16 European AML scenarios and combines rule penalties with ML/GNN probabilities.",
    )
    async def evaluate_transaction(
        request: AMLScenarioEvaluationRequest,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> HybridScoringResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        try:
            return service.evaluate_transaction(tenant, request)
        except Exception as e:
            logger.error("Failed to evaluate transaction against scenario library: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    @target_router.post(
        "/evaluate-batch",
        response_model=BatchScenarioEvaluationResponse,
        status_code=status.HTTP_200_OK,
        summary="Batch evaluate transactions with hybrid engine",
        description="Evaluates up to 100 transactions concurrently returning consolidated decisions.",
    )
    async def evaluate_batch(
        request: BatchScenarioEvaluationRequest,
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> BatchScenarioEvaluationResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        return service.evaluate_batch(tenant, request)

    @target_router.post(
        "/library/{scenario_code}/test",
        response_model=HybridScoringResponse,
        status_code=status.HTTP_200_OK,
        summary="Test synthetic scenario vector",
        description="Executes a test transaction specifically designed to validate trigger condition of the scenario.",
    )
    async def test_scenario_trigger(
        scenario_code: str,
        ml_risk_score: float = Query(default=0.1, ge=0.0, le=1.0),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> HybridScoringResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        code = scenario_code.strip().upper()
        definition = service.get_scenario(code)
        if not definition:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scenario '{scenario_code}' not found.",
            )

        # Synthesize positive triggering test transaction based on scenario code
        amount = 9500.0 if code == "SCN_EUR_STRUCTURING_SUB_10K" else 15000.0
        origin = "KP" if code == "SCN_HIGH_RISK_FATF_CORRIDOR" else "DE"
        destination = "VG" if code == "SCN_OFFSHORE_SHELL_ROUNDTRIP" else "FR"
        is_pep = code == "SCN_PEP_SANCTION_EXPOSURE"
        is_dormant = code == "SCN_DORMANT_BURST_VELOCITY"
        retention = 0.05 if code in {"SCN_RAPID_PASSTHROUGH_MULE", "SCN_RAPID_FAN_OUT_DISPERSAL"} else 1.0
        counterparties = 5 if code in {"SCN_RAPID_FAN_OUT_DISPERSAL", "SCN_RAPID_FAN_IN_AGGREGATION"} else 1
        hops = 3 if code == "SCN_CIRCULAR_MULE_RING" else None
        is_casp = code == "SCN_CRYPTO_ON_OFF_RAMP_BURST"
        account_age = 5 if code == "SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN" else 365
        is_night = code == "SCN_HIGH_VELOCITY_NIGHTTIME"
        price_dev = 3.5 if code == "SCN_TRADE_OVER_UNDER_INVOICING" else None
        merchant = "Casino Royale Online" if code == "SCN_CASINO_GAMBLING_BURST" else None
        rail = "SEPA_INSTANT"
        if code == "SCN_LARGE_CASH_OR_INSTANT_SURGE":
            amount = 60000.0

        synth_txn = TransactionContext(
            transaction_id=f"TEST-{code}-001",
            amount=amount,
            currency="EUR",
            originator_id="ORIG-TEST-001",
            beneficiary_id="BEN-TEST-002",
            origin_country=origin,
            destination_country=destination,
            payment_rail=rail,
            originator_account_age_days=account_age,
            originator_is_pep=is_pep,
            is_dormant_account=is_dormant,
            inbound_credits_last_1h=10000.0,
            outbound_debits_last_1h=9500.0,
            transaction_count_last_1h=4,
            recent_distinct_counterparties_24h=counterparties,
            funds_retention_ratio=retention,
            merchant_category=merchant,
            is_nighttime_execution=is_night,
            is_crypto_service_provider=is_casp,
            unit_price_deviation_ratio=price_dev,
            cyclic_mule_hops=hops,
        )

        test_request = AMLScenarioEvaluationRequest(
            transaction=synth_txn,
            ml_risk_score=ml_risk_score,
            strict_regulatory_override=True,
        )
        return service.evaluate_transaction(tenant, test_request)

    @target_router.get(
        "/metrics",
        response_model=ScenarioMetricsResponse,
        status_code=status.HTTP_200_OK,
        summary="Consortium scenario engine telemetry metrics",
        description="Returns evaluation volume, decision action breakdown, and top triggered scenarios for tenant.",
    )
    async def get_metrics(
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_bank_id: str | None = Header(None, alias="X-Bank-ID"),
    ) -> ScenarioMetricsResponse:
        tenant = _resolve_tenant(x_tenant_id, x_bank_id)
        service: EuropeanScenarioLibraryService = get_european_scenario_service()
        return service.get_metrics(tenant)


# Register routes on both prefix styles
_register_scenario_endpoints(router)
_register_scenario_endpoints(api_router)
