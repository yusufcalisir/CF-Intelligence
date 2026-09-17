"""Privacy Defense & Attack Benchmarking API endpoints.

Provides enterprise-grade privacy audit capabilities:
- Aggregation method catalogue (Bulyan, Trimmed Mean, etc.)
- Membership Inference Attack (MIA) audit trigger
- Model Inversion Attack audit trigger
- Deep Leakage from Gradients (DLG) audit trigger
- Multi-simulation privacy budget log
- Noise calibration for Gaussian DP mechanisms
- Rényi Differential Privacy (RDP) composition and optimal dual bounds
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastapi import APIRouter

from app.application.schemas.privacy_defense import (
    AggregationMethodItem,
    BudgetLogEntryResponse,
    CalibrateNoiseRequest,
    CalibrateNoiseResponse,
    DLGAuditRequest,
    DLGAuditResponse,
    MIAAuditRequest,
    MIAAuditResponse,
    ModelInversionAuditRequest,
    ModelInversionAuditResponse,
    RDPCompositionRequest,
    RDPCompositionResponse,
)
from app.application.services.privacy_audit_service import PrivacyAuditService
from app.application.services.privacy_service import PrivacyService
from app.infrastructure import telemetry

logger = logging.getLogger(__name__)

# Legacy and Canonical Routers for dual-prefix support (/v1/privacy-defense and /api/v1/privacy-defense)
router = APIRouter(prefix="/v1/privacy-defense", tags=["privacy-defense"])
api_router = APIRouter(prefix="/api/v1/privacy-defense", tags=["privacy-defense"])

# Module-level service singletons
_audit_service = PrivacyAuditService()
_privacy_service = PrivacyService()


# ── Aggregation Method Catalogue ────────────────────

AGGREGATION_METHODS: list[dict[str, Any]] = [
    {
        "id": "fed_avg",
        "label": "FedAvg (Unweighted)",
        "description": "Simple unweighted average of all client updates. Fast but vulnerable to Byzantine attacks.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "McMahan et al. (2017)",
    },
    {
        "id": "fed_avg_weighted",
        "label": "FedAvg Weighted (Default)",
        "description": "Sample-count weighted average. Default production method for honest clients.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "McMahan et al. (2017)",
    },
    {
        "id": "krum",
        "label": "Krum (Byzantine-Robust)",
        "description": "Selects the single client update closest to all others. Defends against a single Byzantine attacker (f=1).",
        "byzantine_robust": True,
        "colluding_defense": False,
        "paper": "Blanchard et al. (2017)",
    },
    {
        "id": "coordinate_wise_median",
        "label": "Coordinate-wise Median",
        "description": "Element-wise median across all client updates. Robust to outlier parameters from a single malicious node.",
        "byzantine_robust": True,
        "colluding_defense": False,
        "paper": "Yin et al. (2018)",
    },
    {
        "id": "trimmed_mean",
        "label": "Trimmed Mean (Coordinate Byzantine)",
        "description": "Drops the f largest and f smallest values per coordinate before averaging. Robust to a fraction of Byzantine workers.",
        "byzantine_robust": True,
        "colluding_defense": True,
        "paper": "Yin et al. (2018)",
    },
    {
        "id": "bulyan",
        "label": "Bulyan (Multi-Byzantine Robust)",
        "description": "Combines Krum selection (n-2f candidates) with coordinate-wise Trimmed Mean on the selected subset. Defeats colluding Byzantine attackers that evade single-step Krum.",
        "byzantine_robust": True,
        "colluding_defense": True,
        "paper": "El Mhamdi et al. (2018)",
    },
    {
        "id": "fed_adam",
        "label": "FedAdam (Adaptive Server)",
        "description": "Server-side Adam optimizer on aggregated pseudo-gradients. Improves convergence on heterogeneous data.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "Reddi et al. (2020)",
    },
    {
        "id": "fed_adagrad",
        "label": "FedAdaGrad (Adaptive Server)",
        "description": "Server-side AdaGrad optimizer. Adaptive per-parameter learning rates for Non-IID scenarios.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "Reddi et al. (2020)",
    },
    {
        "id": "fed_prox",
        "label": "FedProx (Proximal Regularization)",
        "description": "Federated averaging with proximal constraint term (mu/2)||w - w_t||^2 to stabilize Non-IID drift and client heterogeneity.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "Li et al. (2020)",
    },
    {
        "id": "fed_yogi",
        "label": "FedYogi (Adaptive Server)",
        "description": "Server-side Yogi optimizer using sign-based variance tracking to prevent learning rate collapse on Non-IID data.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "Reddi et al. (2021)",
    },
    {
        "id": "scaffold",
        "label": "SCAFFOLD (Control Variates)",
        "description": "Server FedAvg aggregation with client drift correction using variance reduction and client-server control variates.",
        "byzantine_robust": False,
        "colluding_defense": False,
        "paper": "Karimireddy et al. (2020)",
    },
]


# ── Handler Implementations ─────────────────────────


async def list_aggregation_methods() -> list[AggregationMethodItem]:
    """Return the catalogue of supported aggregation methods including Byzantine defenses."""
    return [AggregationMethodItem(**method) for method in AGGREGATION_METHODS]


async def audit_mia(request: MIAAuditRequest) -> MIAAuditResponse:
    """Run a Membership Inference Attack (MIA) audit.

    Evaluates whether an attacker can determine if a specific customer record
    was included in the local training batch by analysing loss distribution gaps.
    """
    result = _audit_service.audit_membership_inference(
        train_losses=request.train_losses,
        test_losses=request.test_losses,
    )
    telemetry.cfi_mia_attack_success_rate.set(result.get("membership_leakage_asr", 0.0))
    logger.info("MIA audit completed: %s", result)
    return MIAAuditResponse(**result)


async def audit_model_inversion(request: ModelInversionAuditRequest) -> ModelInversionAuditResponse:
    """Run a Model Inversion Attack audit on gradient norms.

    Evaluates whether high gradient norm variance exposes individual training
    sample features to reconstruction from shared updates.
    """
    result = _audit_service.audit_model_inversion(
        gradient_norms=request.gradient_norms,
    )
    logger.info("Model Inversion audit completed: %s", result)
    return ModelInversionAuditResponse(**result)


async def audit_dlg(request: DLGAuditRequest) -> DLGAuditResponse:
    """Run a Deep Leakage from Gradients (DLG) audit.

    Measures Pearson correlation between original and received gradient vectors.
    High correlation indicates that local training data could be reconstructed.
    """
    result = _audit_service.audit_gradient_leakage_dlg(
        original_gradients=request.original_gradients,
        received_gradients=request.received_gradients,
    )
    telemetry.cfi_dlg_gradient_leakage_score.set(result.get("dlg_leakage_score", 0.0))
    logger.info("DLG audit completed: %s", result)
    return DLGAuditResponse(**result)


async def get_budget_log(epsilon_limit: float = 8.0) -> list[BudgetLogEntryResponse]:
    """Return the multi-simulation privacy budget consumption log.

    Lists cumulative epsilon expenditure across all tracked federated training sessions.
    Used to detect budget exhaustion attack patterns.
    """
    summaries = _privacy_service.get_all_budgets_summary(epsilon_limit=epsilon_limit)
    if summaries:
        telemetry.cfi_privacy_epsilon_consumed.set(summaries[0]["total_epsilon"])
    return [BudgetLogEntryResponse(**entry) for entry in summaries]


async def calibrate_noise(request: CalibrateNoiseRequest) -> CalibrateNoiseResponse:
    """Calibrate Gaussian mechanism noise scale sigma given target (eps, delta) and L2 sensitivity C."""
    sigma = _privacy_service.calculate_gaussian_noise_scale(
        epsilon=request.target_epsilon,
        delta=request.target_delta,
        sensitivity=request.sensitivity,
    )
    return CalibrateNoiseResponse(
        mechanism=request.mechanism or "gaussian",
        target_epsilon=request.target_epsilon,
        target_delta=request.target_delta,
        sensitivity=request.sensitivity,
        calibrated_sigma=round(sigma, 6),
        formula="sigma = C * sqrt(2 * ln(1.25 / delta)) / epsilon",
    )


async def compose_rdp(request: RDPCompositionRequest) -> RDPCompositionResponse:
    """Compute exact Rényi Differential Privacy (RDP) composition and convex dual optimal (eps, delta)-DP bound."""
    best_eps, best_alpha, rdp_map = _privacy_service.compose_rdp(
        sigmas=request.sigmas,
        delta=request.target_delta,
        q=request.sample_ratio_q,
        orders=request.orders,
    )

    # Compute naive linear sum of analytical epsilons for comparison
    naive_sum = sum(
        (request.sample_ratio_q * (2.0 * float(np.log(1.25 / request.target_delta))) ** 0.5) / s
        for s in request.sigmas
    )
    saving_pct = max(0.0, (naive_sum - best_eps) / max(naive_sum, 1e-6) * 100.0)

    # Convert keys to string for JSON serialization
    str_rdp_map = {str(k): round(v, 6) for k, v in rdp_map.items()}

    return RDPCompositionResponse(
        total_rounds=len(request.sigmas),
        cumulative_epsilon=round(best_eps, 6),
        optimal_order_alpha=round(best_alpha, 4),
        naive_sum_epsilon=round(naive_sum, 6),
        privacy_saving_pct=round(saving_pct, 2),
        target_delta=request.target_delta,
        rdp_map=str_rdp_map,
    )


# ── Route Registrations ──────────────────────────────


def _register_privacy_defense_routes(r: APIRouter, prefix_tag: str) -> None:
    r.add_api_route(
        "/aggregation-methods",
        list_aggregation_methods,
        methods=["GET"],
        response_model=list[AggregationMethodItem],
        summary="List Supported Byzantine Robust Aggregation Methods",
        operation_id=f"{prefix_tag}_list_aggregation_methods",
    )
    r.add_api_route(
        "/audit/mia",
        audit_mia,
        methods=["POST"],
        response_model=MIAAuditResponse,
        summary="Audit Membership Inference Attack Leakage",
        operation_id=f"{prefix_tag}_audit_mia",
    )
    r.add_api_route(
        "/audit/model-inversion",
        audit_model_inversion,
        methods=["POST"],
        response_model=ModelInversionAuditResponse,
        summary="Audit Model Inversion Attack Gradient Reconstruction Risk",
        operation_id=f"{prefix_tag}_audit_model_inversion",
    )
    r.add_api_route(
        "/audit/dlg",
        audit_dlg,
        methods=["POST"],
        response_model=DLGAuditResponse,
        summary="Audit Deep Leakage from Gradients Pearson Score",
        operation_id=f"{prefix_tag}_audit_dlg",
    )
    r.add_api_route(
        "/budget-log",
        get_budget_log,
        methods=["GET"],
        response_model=list[BudgetLogEntryResponse],
        summary="Get Multi-Simulation DP Privacy Budget Log",
        operation_id=f"{prefix_tag}_get_budget_log",
    )
    r.add_api_route(
        "/calibrate-noise",
        calibrate_noise,
        methods=["POST"],
        response_model=CalibrateNoiseResponse,
        summary="Calibrate Gaussian Mechanism Noise Scale Sigma",
        operation_id=f"{prefix_tag}_calibrate_noise",
    )
    r.add_api_route(
        "/rdp-composition",
        compose_rdp,
        methods=["POST"],
        response_model=RDPCompositionResponse,
        summary="Compute Rényi DP Composition and Dual Bound",
        operation_id=f"{prefix_tag}_compose_rdp",
    )


_register_privacy_defense_routes(router, "v1")
_register_privacy_defense_routes(api_router, "api_v1")
