"""Privacy Defense & Attack Benchmarking API endpoints.

Provides enterprise-grade privacy audit capabilities:
- Aggregation method catalogue (Bulyan, Trimmed Mean, etc.)
- Membership Inference Attack (MIA) audit trigger
- Model Inversion Attack audit trigger
- Deep Leakage from Gradients (DLG) audit trigger
- Multi-simulation privacy budget log
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.application.services.privacy_audit_service import PrivacyAuditService
from app.application.services.privacy_service import PrivacyService
from app.infrastructure import telemetry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/privacy-defense", tags=["privacy-defense"])

# Module-level service singletons
_audit_service = PrivacyAuditService()
_privacy_service = PrivacyService()


# ── Request / Response models ──────────────────────


class MIAAuditRequest(BaseModel):
    train_losses: list[float] = Field(..., description="Loss values on training set members")
    test_losses: list[float] = Field(..., description="Loss values on non-member test set")


class ModelInversionAuditRequest(BaseModel):
    gradient_norms: list[float] = Field(
        ..., description="Per-parameter gradient L2 norms from a training round"
    )


class DLGAuditRequest(BaseModel):
    original_gradients: list[float] = Field(
        ..., description="Original gradients before secure aggregation"
    )
    received_gradients: list[float] = Field(
        ..., description="Gradients received after aggregation (potential reconstruction vector)"
    )


class CalibrateNoiseRequest(BaseModel):
    target_epsilon: float = Field(..., gt=0.0, description="Target DP epsilon")
    target_delta: float = Field(1e-5, gt=0.0, lt=1.0, description="Target DP delta")
    sensitivity: float = Field(1.0, gt=0.0, description="L2 sensitivity (clipping bound C)")
    mechanism: str = Field("gaussian", description="Mechanism type ('gaussian')")


class CalibrateNoiseResponse(BaseModel):
    mechanism: str
    target_epsilon: float
    target_delta: float
    sensitivity: float
    calibrated_sigma: float
    formula: str


class RDPCompositionRequest(BaseModel):
    sigmas: list[float] = Field(
        ..., min_length=1, description="Noise multipliers across training rounds"
    )
    target_delta: float = Field(1e-5, gt=0.0, lt=1.0, description="Target delta for (eps, delta)-DP")
    sample_ratio_q: float = Field(1.0, gt=0.0, le=1.0, description="Batch sampling ratio q")
    orders: list[float] | None = Field(None, description="Optional list of Rényi orders to evaluate")


class RDPCompositionResponse(BaseModel):
    total_rounds: int
    cumulative_epsilon: float
    optimal_order_alpha: float
    naive_sum_epsilon: float
    privacy_saving_pct: float
    target_delta: float
    rdp_map: dict[str, float]


# ── Aggregation Method Catalogue ────────────────────

AGGREGATION_METHODS = [
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


@router.get("/aggregation-methods")
async def list_aggregation_methods() -> list[dict]:
    """Return the catalogue of supported aggregation methods including new Byzantine defenses."""
    return AGGREGATION_METHODS


@router.post("/audit/mia")
async def audit_mia(request: MIAAuditRequest) -> dict:
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
    return result


@router.post("/audit/model-inversion")
async def audit_model_inversion(request: ModelInversionAuditRequest) -> dict:
    """Run a Model Inversion Attack audit on gradient norms.

    Evaluates whether high gradient norm variance exposes individual training
    sample features to reconstruction from shared updates.
    """
    result = _audit_service.audit_model_inversion(
        gradient_norms=request.gradient_norms,
    )
    logger.info("Model Inversion audit completed: %s", result)
    return result


@router.post("/audit/dlg")
async def audit_dlg(request: DLGAuditRequest) -> dict:
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
    return result


@router.get("/budget-log")
async def get_budget_log(epsilon_limit: float = 8.0) -> list[dict]:
    """Return the multi-simulation privacy budget consumption log.

    Lists cumulative epsilon expenditure across all tracked federated training sessions.
    Used to detect budget exhaustion attack patterns.
    """
    summaries = _privacy_service.get_all_budgets_summary(epsilon_limit=epsilon_limit)
    if summaries:
        telemetry.cfi_privacy_epsilon_consumed.set(summaries[0]["total_epsilon"])
    return summaries


@router.post("/calibrate-noise", response_model=CalibrateNoiseResponse)
async def calibrate_noise(request: CalibrateNoiseRequest) -> CalibrateNoiseResponse:
    """Calibrate Gaussian mechanism noise scale sigma given target (eps, delta) and L2 sensitivity C."""
    sigma = _privacy_service.calculate_gaussian_noise_scale(
        epsilon=request.target_epsilon,
        delta=request.target_delta,
        sensitivity=request.sensitivity,
    )
    return CalibrateNoiseResponse(
        mechanism="gaussian",
        target_epsilon=request.target_epsilon,
        target_delta=request.target_delta,
        sensitivity=request.sensitivity,
        calibrated_sigma=round(sigma, 6),
        formula="sigma = C * sqrt(2 * ln(1.25 / delta)) / epsilon",
    )


@router.post("/rdp-composition", response_model=RDPCompositionResponse)
async def compose_rdp(request: RDPCompositionRequest) -> RDPCompositionResponse:
    """Compute exact Rényi Differential Privacy (RDP) composition and convex dual optimal (eps, delta)-DP bound."""
    import numpy as np

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
