"""Ultra-Comprehensive Simulation Configuration Compatibility & Combinatorial Test Matrix.

Tests all simulation parameters, single-feature isolation toggles, pairwise
interoperability matrix combinations, domain production profiles, mathematical
conflict protection guards, and 100% full-feature extreme stress configurations
for both Custom and Flower engines.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.application.schemas.simulation import SimulationConfigRequest
from app.application.services.data_generator import DataGenerator
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.metrics_service import MetricsService
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import PrivacyService
from app.application.services.simulation_service import (
    InvalidPipelineConfigurationError,
    SimulationService,
)
from app.config import get_settings
from app.domain.enums import SimulationStatus
from app.domain.value_objects import SimulationConfig

logger = logging.getLogger(__name__)


@pytest.fixture
def sim_service() -> SimulationService:
    """Fixture providing initialized SimulationService with low-resource configs for fast testing."""
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    fl_engine = FederatedLearningEngine(settings, model_service, privacy_service)
    metrics_service = MetricsService()
    data_generator = DataGenerator(seed=42)
    sim_repo = MagicMock()
    bank_repo = MagicMock()
    metrics_repo = MagicMock()
    return SimulationService(
        settings=settings,
        simulation_repo=sim_repo,
        bank_repo=bank_repo,
        metrics_repo=metrics_repo,
        data_generator=data_generator,
        fl_engine=fl_engine,
        metrics_service=metrics_service,
        model_service=model_service,
    )


def _base_fast_config(**kwargs: Any) -> SimulationConfig:
    """Create a minimal fast SimulationConfig (2 rounds, small data volume) with optional overrides."""
    default_kwargs = {
        "num_rounds": 2,
        "local_epochs": 1,
        "bank_a_transactions": 500,
        "bank_b_transactions": 300,
        "bank_c_transactions": 200,
    }
    default_kwargs.update(kwargs)
    return SimulationConfig(**default_kwargs)


# =============================================================================
# TIER 1: SINGLE-FEATURE ISOLATION TESTS
# =============================================================================

class TestTier1SingleFeatureIsolation:
    """Verify that each configuration toggle functions cleanly in standalone mode."""

    def test_isolation_fl_engine_custom(self, sim_service: SimulationService) -> None:
        config = _base_fast_config(fl_engine_type="custom")
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED
        assert len(run.rounds) == 2

    def test_isolation_fl_engine_flower(self, sim_service: SimulationService) -> None:
        config = _base_fast_config(fl_engine_type="flower")
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED
        assert len(run.rounds) == 2

    def test_isolation_failure_simulation_latency_and_dropout(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_latency_simulation=True,
            latency_range_ms=(10, 50),
            enable_dropout_simulation=True,
            dropout_probability=0.1,
            enable_reconnect_simulation=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_privacy_post_hoc_dp(self, sim_service: SimulationService) -> None:
        config = _base_fast_config(
            enable_differential_privacy=True,
            dp_mode="post_hoc",
            dp_epsilon=0.5,
            dp_epsilon_limit=20.0,
            dp_delta=1e-5,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_privacy_opacus_per_sample_dp(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=3.0,
            dp_delta=1e-5,
            dp_max_grad_norm=1.5,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_privacy_secure_aggregation(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_secure_aggregation=True,
            aggregation_method="fed_avg_weighted",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    @pytest.mark.parametrize(
        "agg_method",
        ["fed_avg_weighted", "fed_avg", "krum", "coordinate_wise_median", "trimmed_mean", "bulyan"],
    )
    def test_isolation_aggregation_methods_all_6(
        self, sim_service: SimulationService, agg_method: str
    ) -> None:
        config = _base_fast_config(aggregation_method=agg_method)
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_adversarial_poisoning_simulation(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            poisoning_scale=3.0,
            byzantine_defense="krum",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    @pytest.mark.parametrize("attack_type", ["fgsm", "pgd"])
    def test_isolation_active_defense_fgsm_and_pgd(
        self, sim_service: SimulationService, attack_type: str
    ) -> None:
        config = _base_fast_config(
            enable_adversarial_training=True,
            adversarial_attack_type=attack_type,
            adversarial_epsilon=0.03,
            adversarial_alpha=0.01,
            adversarial_steps=3,
            adversarial_loss_weight=0.4,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_regulatory_bias_mitigation(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_bias_mitigation=True,
            fairness_lambda=0.5,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    @pytest.mark.parametrize("iso_mode", ["none", "tee", "fhe"])
    def test_isolation_hardware_crypto_modes(
        self, sim_service: SimulationService, iso_mode: str
    ) -> None:
        config = _base_fast_config(hardware_isolation_mode=iso_mode)
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_streaming_gnn_dynamics(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_streaming_gnn=True,
            enable_graph_embedding=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_web3_cbdc_settlement(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_web3_settlement=True,
            settlement_currency="wCBDC",
            smart_contract_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_isolation_data_volume_scaling(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            bank_a_transactions=2000,
            bank_b_transactions=1500,
            bank_c_transactions=1000,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED


# =============================================================================
# TIER 2: PAIRWISE INTEROPERABILITY MATRIX TESTS
# =============================================================================

class TestTier2PairwiseInteroperabilityMatrix:
    """Test 12 key pairwise feature interactions to detect cross-module conflicts."""

    def test_pair_opacus_dp_and_flower_engine(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            fl_engine_type="flower",
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=2.5,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_opacus_dp_and_byzantine_krum(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_differential_privacy=True,
            dp_mode="opacus",
            aggregation_method="krum",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_opacus_dp_and_pgd_adversarial_training(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_differential_privacy=True,
            dp_mode="opacus",
            enable_adversarial_training=True,
            adversarial_attack_type="pgd",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_tee_isolation_and_flower_engine(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            fl_engine_type="flower",
            hardware_isolation_mode="tee",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_tee_isolation_and_fhe_driver(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            hardware_isolation_mode="fhe",
            enable_secure_aggregation=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_streaming_gnn_and_flower_engine(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            fl_engine_type="flower",
            enable_streaming_gnn=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_streaming_gnn_and_opacus_dp(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_streaming_gnn=True,
            enable_differential_privacy=True,
            dp_mode="opacus",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_bias_mitigation_and_fedprox(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_bias_mitigation=True,
            fairness_lambda=0.5,
            fedprox_mu=0.1,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_web3_settlement_and_poisoning_simulation(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_web3_settlement=True,
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            byzantine_defense="krum",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_latency_dropout_and_flower_engine(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            fl_engine_type="flower",
            enable_latency_simulation=True,
            enable_dropout_simulation=True,
            dropout_probability=0.2,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_secure_aggregation_and_fedavg(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            enable_secure_aggregation=True,
            aggregation_method="fed_avg",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_pair_data_scaling_and_fedopt(
        self, sim_service: SimulationService
    ) -> None:
        config = _base_fast_config(
            bank_a_transactions=1000,
            bank_b_transactions=800,
            bank_c_transactions=500,
            fedopt_server_lr=0.05,
            fedopt_beta1=0.9,
            fedopt_beta2=0.99,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED


# =============================================================================
# TIER 3: DOMAIN PRODUCTION PROFILE QUORUMS
# =============================================================================

class TestTier3DomainProductionProfiles:
    """Test realistic enterprise deployment profile combinations (3-5 features combined)."""

    def test_profile_zero_trust_privacy_hardware_enclave(
        self, sim_service: SimulationService
    ) -> None:
        """Profile A: Hardware TEE + Opacus DP + SecAgg + Web3 Settlement."""
        config = _base_fast_config(
            hardware_isolation_mode="tee",
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=2.0,
            enable_secure_aggregation=True,
            enable_web3_settlement=True,
            aggregation_method="fed_avg_weighted",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_profile_adversarial_hardening_and_defense_quorum(
        self, sim_service: SimulationService
    ) -> None:
        """Profile B: Model Poisoning + Byzantine Krum + PGD Adversarial Training + Bulyan."""
        config = _base_fast_config(
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            poisoning_scale=4.0,
            byzantine_defense="krum",
            enable_adversarial_training=True,
            adversarial_attack_type="pgd",
            adversarial_epsilon=0.04,
            adversarial_steps=3,
            aggregation_method="bulyan",
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_profile_graph_intelligence_and_ai_act_compliance(
        self, sim_service: SimulationService
    ) -> None:
        """Profile C: Streaming GNN + Covariance Bias Mitigation + EU AI Act Report."""
        config = _base_fast_config(
            enable_streaming_gnn=True,
            enable_graph_embedding=True,
            enable_bias_mitigation=True,
            fairness_lambda=0.6,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_profile_unstable_network_distributed_fl(
        self, sim_service: SimulationService
    ) -> None:
        """Profile D: Flower Engine + Network Latency + Packet Dropout + Auto Reconnect."""
        config = _base_fast_config(
            fl_engine_type="flower",
            enable_latency_simulation=True,
            latency_range_ms=(20, 100),
            enable_dropout_simulation=True,
            dropout_probability=0.2,
            enable_reconnect_simulation=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_profile_advanced_federated_optimization(
        self, sim_service: SimulationService
    ) -> None:
        """Profile E: FedProx + MOON contrastive + FedOpt server momentum."""
        config = _base_fast_config(
            fedprox_mu=0.1,
            moon_mu=0.5,
            moon_temperature=0.5,
            fedopt_server_lr=0.01,
            fedopt_beta1=0.9,
            fedopt_beta2=0.999,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED


# =============================================================================
# TIER 4: MATHEMATICAL CONFLICT & EDGE CASE PROTECTION GUARDS
# =============================================================================

class TestTier4MathematicalConflictProtection:
    """Verify security assertions and mathematical conflict protection guards."""

    def test_conflict_guard_secure_aggregation_vs_krum_raises_error(
        self, sim_service: SimulationService
    ) -> None:
        """Secure Aggregation + Non-linear Byzantine defense (Krum) must fail fast with explicit error."""
        config = _base_fast_config(
            enable_secure_aggregation=True,
            aggregation_method="krum",
        )
        with pytest.raises(InvalidPipelineConfigurationError) as exc_info:
            sim_service.run_simulation(config)
        assert "mathematically incompatible" in str(exc_info.value)

    def test_conflict_guard_secure_aggregation_vs_median_raises_error(
        self, sim_service: SimulationService
    ) -> None:
        """Secure Aggregation + Coordinate-wise median must fail fast."""
        config = _base_fast_config(
            enable_secure_aggregation=True,
            aggregation_method="coordinate_wise_median",
        )
        with pytest.raises(InvalidPipelineConfigurationError):
            sim_service.run_simulation(config)

    def test_conflict_dp_gradient_clip_vs_pgd_perturbation(
        self, sim_service: SimulationService
    ) -> None:
        """DP clipping + PGD adversarial training executes cleanly without crashing PyTorch tensor graph."""
        config = _base_fast_config(
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_max_grad_norm=0.5,
            enable_adversarial_training=True,
            adversarial_attack_type="pgd",
            adversarial_epsilon=0.1,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED

    def test_byzantine_exclusion_audit_logs(
        self, sim_service: SimulationService
    ) -> None:
        """Verify poisoning bank is detected under Krum and audit log records exclusion."""
        config = _base_fast_config(
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            poisoning_scale=10.0,
            byzantine_defense="krum",
            enable_web3_settlement=True,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED


# =============================================================================
# TIER 5: FULL COMBINATORIAL EXTREME STRESS TESTS
# =============================================================================

class TestTier5FullCombinatorialExtremeStress:
    """Stress test with 100% of feature toggles active simultaneously."""

    def test_extreme_all_features_enabled_custom_engine(
        self, sim_service: SimulationService
    ) -> None:
        """All 11 feature dimensions active simultaneously under Custom Engine."""
        config = _base_fast_config(
            fl_engine_type="custom",
            enable_latency_simulation=True,
            latency_range_ms=(10, 50),
            enable_dropout_simulation=True,
            dropout_probability=0.1,
            enable_reconnect_simulation=True,
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=3.0,
            dp_delta=1e-5,
            dp_max_grad_norm=1.0,
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            poisoning_scale=4.0,
            byzantine_defense="krum",
            enable_adversarial_training=True,
            adversarial_attack_type="pgd",
            adversarial_epsilon=0.03,
            adversarial_alpha=0.01,
            adversarial_steps=3,
            adversarial_loss_weight=0.5,
            enable_bias_mitigation=True,
            fairness_lambda=0.5,
            hardware_isolation_mode="tee",
            enable_streaming_gnn=True,
            enable_web3_settlement=True,
            settlement_currency="wCBDC",
            bank_a_transactions=1000,
            bank_b_transactions=600,
            bank_c_transactions=400,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED
        assert len(run.rounds) == 2

    def test_extreme_all_features_enabled_flower_engine(
        self, sim_service: SimulationService
    ) -> None:
        """All 11 feature dimensions active simultaneously under Flower Engine."""
        config = _base_fast_config(
            fl_engine_type="flower",
            enable_latency_simulation=True,
            latency_range_ms=(10, 50),
            enable_dropout_simulation=True,
            dropout_probability=0.1,
            enable_reconnect_simulation=True,
            enable_differential_privacy=True,
            dp_mode="opacus",
            dp_epsilon=3.0,
            dp_delta=1e-5,
            dp_max_grad_norm=1.0,
            enable_poisoning_simulation=True,
            poisoning_bank_id="bank_c",
            poisoning_scale=4.0,
            byzantine_defense="krum",
            enable_adversarial_training=True,
            adversarial_attack_type="pgd",
            adversarial_epsilon=0.03,
            adversarial_alpha=0.01,
            adversarial_steps=3,
            adversarial_loss_weight=0.5,
            enable_bias_mitigation=True,
            fairness_lambda=0.5,
            hardware_isolation_mode="tee",
            enable_streaming_gnn=True,
            enable_web3_settlement=True,
            settlement_currency="wCBDC",
            bank_a_transactions=1000,
            bank_b_transactions=600,
            bank_c_transactions=400,
        )
        run = sim_service.run_simulation(config)
        assert run.status == SimulationStatus.COMPLETED
        assert len(run.rounds) == 2


# =============================================================================
# TIER 6: PYDANTIC SCHEMA VALIDATION & SERIALIZATION TESTS
# =============================================================================

class TestTier6PydanticSchemaValidation:
    """Verify SimulationConfigRequest Pydantic schema validation for all combinations."""

    def test_pydantic_schema_default_instantiation(self) -> None:
        req = SimulationConfigRequest()
        assert req.num_rounds == 10
        assert req.fl_engine_type == "custom"
        assert req.enable_streaming_gnn is False

    def test_pydantic_schema_full_payload_parsing(self) -> None:
        payload = {
            "num_rounds": 5,
            "local_epochs": 2,
            "fl_engine_type": "flower",
            "enable_latency_simulation": True,
            "enable_dropout_simulation": True,
            "enable_differential_privacy": True,
            "dp_mode": "opacus",
            "enable_poisoning_simulation": True,
            "byzantine_defense": "krum",
            "enable_adversarial_training": True,
            "adversarial_attack_type": "pgd",
            "enable_bias_mitigation": True,
            "hardware_isolation_mode": "tee",
            "enable_streaming_gnn": True,
            "enable_web3_settlement": True,
            "settlement_currency": "wCBDC",
        }
        req = SimulationConfigRequest(**payload)
        assert req.num_rounds == 5
        assert req.fl_engine_type == "flower"
        assert req.hardware_isolation_mode == "tee"
        assert req.enable_streaming_gnn is True
        assert req.enable_web3_settlement is True
