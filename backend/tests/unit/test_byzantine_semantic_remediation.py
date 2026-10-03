# type: ignore
# pyright: reportArgumentType=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportOperatorIssue=false
# pyright: reportCallIssue=false
# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
"""Targeted Unit Test Suite for Byzantine Phase 3.1.1 Semantic Remediation.

Verifies:
1. Retention semantics: retention > 1.0 is mathematically valid and accepted.
2. Clean metrics: clean_ap_difference, clean_retention_ratio, and clean_relative_loss.
3. Legacy clean_penalty equals clean_relative_loss, accepting negative values.
4. Self-retention semantics: same-defense denominator vs same-seed clean FedAvg.
5. Hash family separation: preregistered frozen protocol hash vs execution-resolved runtime hash.
6. Scientific protocol field parity across configurations.
7. Absence of unsupported categorical robustness claims.
"""

from __future__ import annotations

from pathlib import Path

import torch
from benchmarks.byzantine.attacks import ALIEAttack, create_attack
from benchmarks.byzantine.config import (
    CANONICAL_EXECUTION_RESOLVED_CONFIG_SHA256,
    CANONICAL_EXECUTION_RESOLVED_MATRIX_SHA256,
    FROZEN_PROTOCOL_CONFIG_SHA256,
    FROZEN_PROTOCOL_MATRIX_SHA256,
    compute_condition_matrix_sha256,
    create_canonical_byzantine_config,
    generate_canonical_condition_matrix,
)
from benchmarks.byzantine.runner import compute_clean_defense_metrics
from benchmarks.byzantine.schema import (
    ByzantineAggregatedMetric,
    ByzantineConditionResult,
)
from benchmarks.runners.run_byzantine_federated_benchmark import build_default_config

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestByzantineRetentionSemantics:
    """Verifies retention ratio definition, range [0, +inf), and acceptance of values > 1.0."""

    def test_retention_greater_than_one_is_accepted_and_valid(self):
        """Retention is AP(condition) / AP(clean_fedavg) and can exceed 1.0 when condition outperforms baseline."""
        fedavg_clean_ap = 0.7000
        defense_ap = 0.7200
        computed_ret = defense_ap / fedavg_clean_ap  # ~1.028571

        result = ByzantineConditionResult(
            seed=42,
            attack_name="none",
            aggregator_name="clean_trimmed_mean",
            actual_f=0,
            assumed_f=2,
            test_pr_auc=defense_ap,
            test_roc_auc=0.9800,
            retention_of_clean=computed_ret,
            defense_self_retention=1.0,
            clean_penalty=1.0 - computed_ret,
            rounds_completed=10,
        )

        assert result.retention_of_clean > 1.0
        assert abs(result.retention_of_clean - 1.028571) < 1e-4
        assert result.test_pr_auc <= 1.0  # Raw metric is bounded in [0, 1]
        assert result.test_roc_auc <= 1.0

    def test_retention_recomputation_exactness(self):
        """Validates exact retention recomputation from raw values."""
        clean_ap = 0.694482
        cand_ap = 0.736365
        expected_ratio = cand_ap / clean_ap  # 1.060308
        res = ByzantineConditionResult(
            seed=123,
            attack_name="none",
            aggregator_name="clean_trimmed_mean",
            actual_f=0,
            assumed_f=2,
            test_pr_auc=cand_ap,
            test_roc_auc=0.9850,
            retention_of_clean=round(expected_ratio, 6),
            defense_self_retention=1.0,
            clean_penalty=round(1.0 - expected_ratio, 6),
            rounds_completed=10,
        )
        assert res.retention_of_clean == 1.060308
        assert res.clean_penalty == -0.060308


class TestCleanDefenseMetricsSemantics:
    """Verifies clean_ap_difference, clean_retention_ratio, and clean_relative_loss."""

    def test_compute_clean_defense_metrics_when_defense_has_loss(self):
        """When defense achieves lower clean AP than FedAvg, relative loss is positive."""
        fedavg_ap = 0.725361
        krum_clean_ap = 0.621272

        m = compute_clean_defense_metrics(krum_clean_ap, fedavg_ap)

        # 1. Absolute AP difference: defense - fedavg < 0
        assert m["clean_ap_difference"] == round(0.621272 - 0.725361, 6)
        assert m["clean_ap_difference"] < 0.0

        # 2. Clean retention ratio: defense / fedavg < 1.0
        assert m["clean_retention_ratio"] == round(0.621272 / 0.725361, 6)
        assert m["clean_retention_ratio"] < 1.0

        # 3. Clean relative loss: 1.0 - ratio > 0.0
        assert m["clean_relative_loss"] == round(1.0 - (0.621272 / 0.725361), 6)
        assert m["clean_relative_loss"] > 0.0

    def test_compute_clean_defense_metrics_when_defense_exceeds_fedavg(self):
        """When defense achieves higher clean AP than FedAvg, relative loss is negative."""
        fedavg_ap = 0.694482
        bulyan_clean_ap = 0.734165

        m = compute_clean_defense_metrics(bulyan_clean_ap, fedavg_ap)

        # 1. Absolute AP difference: defense - fedavg > 0
        assert m["clean_ap_difference"] == round(0.734165 - 0.694482, 6)
        assert m["clean_ap_difference"] > 0.0

        # 2. Clean retention ratio: defense / fedavg > 1.0
        assert m["clean_retention_ratio"] == round(0.734165 / 0.694482, 6)
        assert m["clean_retention_ratio"] > 1.0

        # 3. Clean relative loss: 1.0 - ratio < 0.0 (negative loss is mathematically valid)
        assert m["clean_relative_loss"] == round(1.0 - (0.734165 / 0.694482), 6)
        assert m["clean_relative_loss"] < 0.0

    def test_legacy_clean_penalty_property_mapping(self):
        """Verifies clean_relative_loss property maps directly to clean_penalty."""
        res = ByzantineConditionResult(
            seed=42,
            attack_name="none",
            aggregator_name="clean_bulyan",
            actual_f=0,
            assumed_f=2,
            test_pr_auc=0.708944,
            test_roc_auc=0.9750,
            retention_of_clean=0.977367,
            clean_penalty=0.022633,
            rounds_completed=10,
        )
        assert res.clean_relative_loss == 0.022633
        assert res.clean_retention_ratio == round(1.0 - 0.022633, 6)
        diff = res.compute_clean_ap_difference(0.725361)
        assert diff is not None
        assert abs(diff - (-0.016417)) < 1e-5

    def test_aggregated_clean_penalty_mean_property_mapping(self):
        """Verifies clean_relative_loss_mean property on ByzantineAggregatedMetric."""
        agg = ByzantineAggregatedMetric(
            condition_name="clean_krum_under_none",
            attack_name="none",
            aggregator_name="clean_krum",
            actual_f=0,
            assumed_f=2,
            pr_auc_mean=0.6458,
            pr_auc_std=0.0338,
            roc_auc_mean=0.9700,
            roc_auc_std=0.0050,
            retention_ratio_mean=0.8996,
            retention_ratio_std=0.0380,
            clean_penalty_mean=0.1004,
            n_seeds=3,
        )
        assert agg.clean_relative_loss_mean == 0.1004


class TestDefenseSelfRetentionSemantics:
    """Verifies defense_self_retention isolates attack-induced degradation from baseline costs."""

    def test_fedavg_self_retention_equals_retention_of_clean(self):
        """For attacked FedAvg, defense_self_retention and retention_of_clean coincide."""
        clean_fedavg = 0.725361
        attacked_fedavg = 0.165300
        ret = attacked_fedavg / clean_fedavg

        res = ByzantineConditionResult(
            seed=42,
            attack_name="sign_flip",
            aggregator_name="fedavg",
            actual_f=2,
            assumed_f=2,
            test_pr_auc=attacked_fedavg,
            test_roc_auc=0.8500,
            retention_of_clean=round(ret, 6),
            defense_self_retention=round(ret, 6),
            rounds_completed=10,
        )
        assert res.retention_of_clean == res.defense_self_retention

    def test_robust_defense_self_retention_uses_clean_defense_denominator(self):
        """For Krum under ALIE on Seed 42, self-retention isolates attack impact from Krum's clean penalty."""
        clean_fedavg = 0.725361
        clean_krum = 0.621272
        attacked_krum = 0.669500

        ret_clean = attacked_krum / clean_fedavg  # ~0.922989
        self_ret = attacked_krum / clean_krum    # ~1.077628 (exceeds clean Krum!)

        res = ByzantineConditionResult(
            seed=42,
            attack_name="alie_omniscient_z1",
            aggregator_name="krum",
            actual_f=2,
            assumed_f=2,
            test_pr_auc=attacked_krum,
            test_roc_auc=0.9700,
            retention_of_clean=round(ret_clean, 6),
            defense_self_retention=round(self_ret, 6),
            rounds_completed=10,
        )
        assert res.retention_of_clean < 1.0
        assert res.defense_self_retention is not None
        assert res.defense_self_retention > 1.0


class TestByzantineHashFamiliesProvenance:
    """Verifies the two hash families: preregistered frozen protocol vs execution-resolved runtime."""

    def test_frozen_protocol_definition_hashes_are_stable(self):
        """Verifies create_canonical_byzantine_config() reproduces exact frozen Phase 2C hashes."""
        cfg = create_canonical_byzantine_config()
        cfg_hash = cfg.ensure_config_hash()
        mat = generate_canonical_condition_matrix(cfg)
        mat_hash = compute_condition_matrix_sha256(mat)

        assert cfg_hash == FROZEN_PROTOCOL_CONFIG_SHA256
        assert mat_hash == FROZEN_PROTOCOL_MATRIX_SHA256
        assert cfg_hash == "d0640c8e9dbd11df9405db97ace31ad31abd3bfaa2c1ccde25fccba05b25d4b6"
        assert mat_hash == "4b04d0d6ad70dcef780c64b76d21f0de25cfaab095c5987487d89d8a0d8a4ec0"

    def test_execution_resolved_hashes_are_stable(self):
        """Verifies execution-resolved configuration reproduces exact Phase 3 runtime hashes."""
        cfg = build_default_config(is_canonical=True)
        # Instantiate attacks (as runner.py does during execution)
        for att in cfg.attacks:
            create_attack(att)

        resolved_cfg_hash = cfg.ensure_config_hash()
        mat = generate_canonical_condition_matrix(cfg)
        resolved_mat_hash = compute_condition_matrix_sha256(mat)

        assert resolved_cfg_hash == CANONICAL_EXECUTION_RESOLVED_CONFIG_SHA256
        assert resolved_mat_hash == CANONICAL_EXECUTION_RESOLVED_MATRIX_SHA256
        assert resolved_cfg_hash == "f3c8962616571803098d0e8b48b1902a868465aedc400ba1803097b49161df9e"
        assert resolved_mat_hash == "cd8528f77f956d12ca77059ab9448ff4b3fe21d39206eb97f8213704f9de22df"

    def test_scientific_protocol_field_parity(self):
        """Verifies 100% field-by-field parity in scientific parameters between frozen and executed configs."""
        cfg_frozen = create_canonical_byzantine_config()
        cfg_exec = build_default_config(is_canonical=True)

        # 1. Dataset parameters
        assert cfg_frozen.dataset.dataset_name == cfg_exec.dataset.dataset_name == "credit_card"
        assert cfg_frozen.dataset.physical_sha256 == cfg_exec.dataset.physical_sha256
        assert cfg_frozen.dataset.feature_dim == cfg_exec.dataset.feature_dim == 30
        assert cfg_frozen.dataset.train_ratio == cfg_exec.dataset.train_ratio == 0.80
        assert cfg_frozen.dataset.test_ratio == cfg_exec.dataset.test_ratio == 0.20

        # 2. Federation parameters
        assert cfg_frozen.federation.n_clients == cfg_exec.federation.n_clients == 12
        assert cfg_frozen.federation.f_byzantine == cfg_exec.federation.f_byzantine == 2
        assert cfg_frozen.federation.partition_alpha == cfg_exec.federation.partition_alpha == 0.5
        assert cfg_frozen.actual_byzantine_count == cfg_exec.actual_byzantine_count == 2
        assert cfg_frozen.assumed_byzantine_bound == cfg_exec.assumed_byzantine_bound == 2

        # 3. Training parameters
        assert cfg_frozen.training.rounds == cfg_exec.training.rounds == 10
        assert cfg_frozen.training.local_epochs == cfg_exec.training.local_epochs == 1
        assert cfg_frozen.training.batch_size == cfg_exec.training.batch_size == 64
        assert cfg_frozen.training.learning_rate == cfg_exec.training.learning_rate == 0.005
        assert cfg_frozen.training.pos_weight == cfg_exec.training.pos_weight == 10.0

        # 4. Seeds
        assert cfg_frozen.seeds == cfg_exec.seeds == [42, 123, 456]

        # 5. Attack count and types
        assert [a.attack_type.value for a in cfg_frozen.attacks] == [a.attack_type.value for a in cfg_exec.attacks]

        # 6. Aggregator count and types
        assert [ag.aggregator_type.value for ag in cfg_frozen.aggregators] == [ag.aggregator_type.value for ag in cfg_exec.aggregators]


class TestScientificLanguageIntegrity:
    """Verifies absence of unsupported categorical robustness claims in active Byzantine modules."""

    def test_no_unsupported_categorical_robustness_in_schema_and_runner(self):
        """Verifies schema.py and runner.py do not contain blanket 'completely robust' or 'immune' claims."""
        forbidden_phrases = [
            "completely robust",
            "immune to",
            "guarantees byzantine robustness",
            "proven robust",
            "defeats all attacks",
            "explains why",
        ]
        for rel_path in ["benchmarks/byzantine/schema.py", "benchmarks/byzantine/runner.py"]:
            file_path = REPO_ROOT / rel_path
            content = file_path.read_text(encoding="utf-8").lower()
            for phrase in forbidden_phrases:
                assert phrase not in content, f"Forbidden phrase '{phrase}' found in {rel_path}!"


class TestALIEStrictInequalityArithmeticRegression:
    """Verifies strict inequality reference arithmetic and distinguishes maximum from boundary/supremum."""

    def test_n12_m2_reference_boundary_and_strict_inequality(self):
        """For n=12, m=2: s=5, p=0.5, admissible z < 0.0, boundary/supremum = 0.0 (0.0 not attained)."""
        import scipy.stats as stats  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
        n, m = 12, 2
        s = ALIEAttack.compute_reference_supporters(n, m)
        assert s == 5
        p = ALIEAttack.compute_reference_probability_boundary(n, m)
        assert abs(p - 0.50) < 1e-6

        # Reference boundary / supremum
        z_boundary = ALIEAttack.compute_reference_z_boundary(n, m)
        assert abs(z_boundary - 0.0) < 1e-6

        # Strict inequality Phi(z) < 0.50 implies admissible z must be strictly negative
        # 0.0 is the supremum/boundary, NOT an attained maximum under strict inequality
        assert float(stats.norm.cdf(0.0)) == 0.50  # Phi(0) = 0.50, which does NOT satisfy Phi(z) < 0.50
        assert float(stats.norm.cdf(-1e-6)) < 0.50  # Strictly negative z satisfies admissibility

        # Evaluated project parameter is fixed z = 1.0 (stress test outside reference boundary)
        att = ALIEAttack(z_max=1.0)
        assert att.evaluated_z == 1.0
        assert att.evaluated_z > z_boundary

    def test_n50_m24_paper_example_reproduction(self):
        """Baruch et al. (2019) Section 3.3 example: n=50, m=24 -> s=2, p=24/26 ~= 0.923, z ~= 1.43."""
        n, m = 50, 24
        s = ALIEAttack.compute_reference_supporters(n, m)
        assert s == 2
        p = ALIEAttack.compute_reference_probability_boundary(n, m)
        assert abs(p - (24.0 / 26.0)) < 1e-6
        z_boundary = ALIEAttack.compute_reference_z_boundary(n, m)
        assert abs(round(z_boundary, 2) - 1.43) < 0.02


class TestPhase313CanonicalNumericalIntegrity:
    """Phase 3.1.3 Authoritative Raw Numerical Reconstruction and Integrity Sentinels.

    Verifies that all 72 conditions are derived directly from the immutable canonical artifact,
    sentinel values for catastrophic collapses are pinned, aggregate sample SDs use ddof=1,
    and retention > 1.0 is preserved.
    """

    def test_canonical_artifact_identity_and_completeness(self):
        """Verifies byte size, SHA-256, and 72-condition completeness of the canonical artifact."""
        import hashlib
        import json

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        assert artifact_path.is_file(), f"Canonical artifact not found: {artifact_path}"

        raw_bytes = artifact_path.read_bytes()
        assert len(raw_bytes) == 47417, f"Expected 47,417 bytes, got {len(raw_bytes)}"
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        assert sha256 == "c760df9912a1235f0131bd4060ab8fa274dddcb5b25c558ca4436c558723ff4d"

        data = json.loads(raw_bytes.decode("utf-8"))
        per_seed = data["per_seed_results"]
        assert len(per_seed) == 72

        # Verify exact condition coverage: 3 seeds x 4 attacks x 6 aggregators
        conditions = {(r["seed"], r["attack_name"], r["aggregator_name"]) for r in per_seed}
        assert len(conditions) == 72, "Found duplicate conditions in canonical artifact!"

        for seed in [42, 123, 456]:
            seed_conds = [r for r in per_seed if r["seed"] == seed]
            assert len(seed_conds) == 24

    def test_canonical_sentinel_catastrophic_collapses(self):
        """Pins raw sentinel measurements for critical seed-instability conditions."""
        import json

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        per_seed = data["per_seed_results"]

        def get_result(seed: int, attack: str, aggregator: str) -> dict:
            matches = [
                r for r in per_seed
                if r["seed"] == seed and r["attack_name"] == attack and r["aggregator_name"] == aggregator
            ]
            assert len(matches) == 1, f"Expected 1 match for {seed}/{attack}/{aggregator}, found {len(matches)}"
            return matches[0]

        # 1. Sign Flip + FedAvg + Seed 42 (Severe Collapse)
        sf_fedavg_42 = get_result(42, "sign_flip", "fedavg")
        assert sf_fedavg_42["test_pr_auc"] == 0.165317
        assert sf_fedavg_42["test_roc_auc"] == 0.908394

        # 2. Sign Flip + Single Krum + Seed 456 (Catastrophic Collapse)
        sf_krum_456 = get_result(456, "sign_flip", "krum")
        assert sf_krum_456["test_pr_auc"] == 0.001104
        assert sf_krum_456["test_roc_auc"] == 0.112073

        # 3. ALIE + Coordinate Median + Seed 123 (Severe Degradation)
        alie_med_123 = get_result(123, "alie_omniscient_z1", "coordinate_median")
        assert alie_med_123["test_pr_auc"] == 0.204505
        assert alie_med_123["test_roc_auc"] == 0.97926

        # 4. ALIE + Bulyan + Seed 123 (Severe Degradation, true raw value is 0.222, NOT 0.3475)
        alie_bulyan_123 = get_result(123, "alie_omniscient_z1", "bulyan")
        assert alie_bulyan_123["test_pr_auc"] == 0.222
        assert alie_bulyan_123["test_roc_auc"] == 0.982449

    def test_canonical_reconstructed_aggregates_parity(self):
        """Verifies aggregate means and sample SDs (ddof=1) match independent computation."""
        import json

        import numpy as np

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        per_seed = data["per_seed_results"]

        groups: dict[tuple[str, str], list[float]] = {}
        for r in per_seed:
            key = (r["attack_name"], r["aggregator_name"])
            groups.setdefault(key, []).append(r["test_pr_auc"])

        # Sign Flip + Trimmed Mean
        sf_tm = groups[("sign_flip", "trimmed_mean")]
        assert abs(float(np.mean(sf_tm)) - 0.714055) < 1e-4
        assert abs(float(np.std(sf_tm, ddof=1)) - 0.012937) < 1e-4

        # Sign Flip + Multi-Krum
        sf_mk = groups[("sign_flip", "multi_krum")]
        assert abs(float(np.mean(sf_mk)) - 0.706126) < 1e-4
        assert abs(float(np.std(sf_mk, ddof=1)) - 0.034118) < 1e-4

        # Gaussian + FedAvg
        g_fedavg = groups[("gaussian_noise", "fedavg")]
        assert abs(float(np.mean(g_fedavg)) - 0.720153) < 1e-4
        assert abs(float(np.std(g_fedavg, ddof=1)) - 0.003382) < 1e-4

        # Gaussian + Trimmed Mean
        g_tm = groups[("gaussian_noise", "trimmed_mean")]
        assert abs(float(np.mean(g_tm)) - 0.725945) < 1e-4
        assert abs(float(np.std(g_tm, ddof=1)) - 0.000435) < 1e-4

        # ALIE + Bulyan
        alie_bul = groups[("alie_omniscient_z1", "bulyan")]
        assert abs(float(np.mean(alie_bul)) - 0.436330) < 1e-4
        assert abs(float(np.std(alie_bul, ddof=1)) - 0.207180) < 1e-4

    def test_canonical_retention_recomputation_exactness(self):
        """Verifies that all 72 conditions match retention = AP(cond, s) / AP(clean_fedavg, s) with zero error."""
        import json

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        per_seed = data["per_seed_results"]

        clean_fedavg = {
            r["seed"]: r["test_pr_auc"]
            for r in per_seed
            if r["attack_name"] == "none" and r["aggregator_name"] == "clean_fedavg"
        }
        assert clean_fedavg[42] == 0.725361
        assert clean_fedavg[123] == 0.694482
        assert clean_fedavg[456] == 0.733816

        for r in per_seed:
            expected_ret = r["test_pr_auc"] / clean_fedavg[r["seed"]]
            assert abs(expected_ret - r["retention_of_clean"]) < 1e-5


class TestPhase313PartitionSemantics:
    """Verifies partition Dirichlet constraints and fraud-sparse client distribution."""

    def test_partition_hashes_match_canonical_specification(self):
        """Verifies deterministic partition hashes for seeds 42, 123, 456."""
        from benchmarks.byzantine.config import ByzantineBenchmarkConfig
        from benchmarks.byzantine.data import load_and_partition_byzantine_data

        config = ByzantineBenchmarkConfig()

        expected_hashes = {
            42: "ac930eca0ad0867c0180cf73a6ad749db1e56d4875d0fe0e7861bb836d20a0ab",
            123: "e8338420a891c19b1938abdc7e5129be4824736ef1878b9227b84b09119f2f2b",
            456: "01aa418f3b202710813f25845116d6a7fad5557d2db3f5bc32867f544482a209",
        }

        for seed, expected_hash in expected_hashes.items():
            _, _, meta = load_and_partition_byzantine_data(config, seed)
            assert meta["partition_sha256"] == expected_hash

    def test_fraud_sparse_and_zero_positive_clients_permitted(self):
        """Verifies that Dirichlet partitioning permits zero-positive clients (e.g. Seed 456 Client 6)."""
        from benchmarks.byzantine.config import ByzantineBenchmarkConfig
        from benchmarks.byzantine.data import load_and_partition_byzantine_data

        config = ByzantineBenchmarkConfig()
        _, _, meta_456 = load_and_partition_byzantine_data(config, 456)
        client_6 = meta_456["client_metadata"][6]

        # Seed 456 Client 6 has 8741 total samples, exactly 0 fraud positives (prevalence = 0.0)
        assert client_6["sample_count"] == 8741
        assert client_6["positive_count"] == 0
        assert client_6["prevalence"] == 0.0


class TestPhase313AttackParameterAndConditionSemantics:
    """Verifies machine execution semantics for Sign-Flip, Gaussian, ALIE, and condition counts."""

    def test_sign_flip_exact_transformation_and_configured_scale(self):
        """Verifies malicious_delta = -3.0 * local_delta, NOT -1.0 * local_delta."""
        from benchmarks.byzantine.attacks import SignFlipAttack
        from benchmarks.byzantine.config import (
            ByzantineAttackType,
            create_canonical_byzantine_config,
        )
        from benchmarks.runners.run_byzantine_federated_benchmark import build_default_config

        # 1. Configured scale across canonical configs is 3.0
        cfg_frozen = create_canonical_byzantine_config()
        sf_cfg_frozen = next(a for a in cfg_frozen.attacks if a.attack_type == ByzantineAttackType.SIGN_FLIP)
        assert sf_cfg_frozen.scale == 3.0

        cfg_exec = build_default_config(is_canonical=True)
        sf_cfg_exec = next(a for a in cfg_exec.attacks if a.attack_type == ByzantineAttackType.SIGN_FLIP)
        assert sf_cfg_exec.scale == 3.0

        # 2. Implemented mathematical transformation is Delta_mal = -3 * Delta_local
        attack = SignFlipAttack(sf_cfg_frozen)
        assert attack.scale == 3.0

        local_delta = torch.tensor([0.1, -0.2, 0.5, -1.0], dtype=torch.float32)
        adv_delta = attack.apply(local_delta, client_id=10, round_idx=0)

        expected_delta = -3.0 * local_delta
        assert torch.allclose(adv_delta, expected_delta)
        assert not torch.allclose(adv_delta, -1.0 * local_delta)

    def test_gaussian_parameter_semantics(self):
        """Verifies Gaussian noise uses isotropic N(0, sigma_eff^2 I) scaled to local delta std."""
        from benchmarks.byzantine.attacks import GaussianNoiseAttack
        from benchmarks.byzantine.config import (
            ByzantineAttackType,
            create_canonical_byzantine_config,
        )

        cfg_frozen = create_canonical_byzantine_config()
        g_cfg = next(a for a in cfg_frozen.attacks if a.attack_type == ByzantineAttackType.GAUSSIAN_NOISE)
        assert g_cfg.noise_std == 1.0

        attack = GaussianNoiseAttack(g_cfg)
        assert attack.noise_std == 1.0

        local_delta = torch.randn(1000, dtype=torch.float32)
        adv_delta = attack.apply(local_delta, client_id=10, round_idx=0, seed=42)

        # Output update replaces delta with noise scaled by local std
        delta_std = float(torch.std(local_delta).item())
        adv_std = float(torch.std(adv_delta).item())
        assert abs(adv_std - delta_std) / delta_std < 0.15  # within sampling variance for n=1000

    def test_alie_execution_semantics(self):
        """Verifies ALIE computes mu - z * sigma with sample std ddof=1 and z=1.0 on honest deltas."""
        from benchmarks.byzantine.attacks import ALIEAttack
        from benchmarks.byzantine.config import (
            ByzantineAttackType,
            create_canonical_byzantine_config,
        )

        cfg_frozen = create_canonical_byzantine_config()
        alie_cfg = next(a for a in cfg_frozen.attacks if a.attack_type == ByzantineAttackType.ALIE)
        assert alie_cfg.alie_evaluated_z == 1.0

        attack = ALIEAttack(alie_cfg)
        assert attack.evaluated_z == 1.0
        assert attack.std_correction == 1  # sample std ddof=1
        assert attack.direction_rule == "CONSTANT_NEGATIVE_OFFSET"

        d1 = torch.tensor([1.0, 2.0, 3.0], dtype=torch.float32)
        d2 = torch.tensor([3.0, 4.0, 5.0], dtype=torch.float32)
        consortium_deltas = [d1, d2]

        adv = attack.apply(torch.zeros(3), client_id=10, round_idx=0, consortium_deltas=consortium_deltas)

        # mu = [2.0, 3.0, 4.0]
        # sigma (sample, ddof=1) = [sqrt(2), sqrt(2), sqrt(2)] ~= 1.41421356
        # adv = mu - 1.0 * sigma
        mu = torch.tensor([2.0, 3.0, 4.0], dtype=torch.float32)
        sigma = torch.tensor([1.41421356, 1.41421356, 1.41421356], dtype=torch.float32)
        expected = mu - 1.0 * sigma
        assert torch.allclose(adv, expected, atol=1e-5)

    def test_condition_count_semantics_clean_vs_attacked(self):
        """Verifies exactly 72 total conditions: 18 clean baselines and 54 attacked defense conditions."""
        import json

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        per_seed = data["per_seed_results"]

        total_conditions = len(per_seed)
        clean_conditions = [r for r in per_seed if r["attack_name"] == "none"]
        attacked_conditions = [r for r in per_seed if r["attack_name"] != "none"]

        assert total_conditions == 72
        assert len(clean_conditions) == 18  # 3 seeds x 1 clean state x 6 aggregators
        assert len(attacked_conditions) == 54  # 3 seeds x 3 attack families x 6 aggregators

        # All 18 clean conditions have trivial defense_self_retention = 1.0
        assert all(r["defense_self_retention"] == 1.0 for r in clean_conditions)

        # Exactly 54 attacked conditions have non-trivial defense_self_retention
        assert len(attacked_conditions) == 54
        for r in attacked_conditions:
            assert r["defense_self_retention"] is not None


class TestByzantineCanonicalPromotionEvidence:
    """Verifies Phase 3.2 controlled promotion evidence invariants and sentinels."""

    def test_canonical_raw_artifact_identity(self):
        """Verifies exact byte size, SHA-256, execution git SHA, dataset SHA, and partition hashes."""
        import hashlib
        import json

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        assert artifact_path.exists(), "Canonical artifact missing"

        raw_bytes = artifact_path.read_bytes()
        assert len(raw_bytes) == 47417, f"Size mismatch: {len(raw_bytes)}"
        computed_sha = hashlib.sha256(raw_bytes).hexdigest()
        assert computed_sha == "c760df9912a1235f0131bd4060ab8fa274dddcb5b25c558ca4436c558723ff4d"

        data = json.loads(raw_bytes.decode("utf-8"))
        assert data["git_sha"] == "85e42a45a2ed3a18cd118ffdb254a072688444de"
        assert data["dataset"]["physical_sha256"] == "76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89"

        expected_partitions = {
            "42": "ac930eca0ad0867c0180cf73a6ad749db1e56d4875d0fe0e7861bb836d20a0ab",
            "123": "e8338420a891c19b1938abdc7e5129be4824736ef1878b9227b84b09119f2f2b",
            "456": "01aa418f3b202710813f25845116d6a7fad5557d2db3f5bc32867f544482a209",
        }
        for seed_str, exp_hash in expected_partitions.items():
            assert data["partition_sha256_by_seed"][seed_str] == exp_hash

    def test_canonical_numerical_sentinels(self):
        """Verifies Section 24 numerical sentinels directly against the canonical artifact."""
        import json

        import numpy as np

        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        agg = { (r["attack_name"], r["aggregator_name"]): r for r in data["aggregated_results"] }
        per_seed = { (r["attack_name"], r["aggregator_name"], r["seed"]): r for r in data["per_seed_results"] }

        # Sign-Flip + Trimmed Mean: PR-AUC 0.714055 +/- 0.012937, retention 0.995393
        sf_tm_seed_vals = [per_seed[("sign_flip", "trimmed_mean", s)]["test_pr_auc"] for s in [42, 123, 456]]
        assert abs(float(np.mean(sf_tm_seed_vals)) - 0.714055) < 1e-5
        assert abs(float(np.std(sf_tm_seed_vals, ddof=1)) - 0.012937) < 1e-5
        sf_tm_agg = agg[("sign_flip", "trimmed_mean")]
        assert abs(sf_tm_agg["pr_auc_mean"] - 0.7141) < 1e-4
        assert abs(sf_tm_agg["retention_ratio_mean"] - 0.9954) < 1e-4

        # Sign-Flip + Multi-Krum: PR-AUC 0.706126 +/- 0.034118, retention 0.984752
        sf_mk_seed_vals = [per_seed[("sign_flip", "multi_krum", s)]["test_pr_auc"] for s in [42, 123, 456]]
        assert abs(float(np.mean(sf_mk_seed_vals)) - 0.706126) < 1e-5
        assert abs(float(np.std(sf_mk_seed_vals, ddof=1)) - 0.034118) < 1e-5
        sf_mk_agg = agg[("sign_flip", "multi_krum")]
        assert abs(sf_mk_agg["pr_auc_mean"] - 0.7061) < 1e-4
        assert abs(sf_mk_agg["retention_ratio_mean"] - 0.9848) < 1e-4

        # Sign-Flip + FedAvg Seed 42: 0.165317
        sf_fa_42 = per_seed[("sign_flip", "fedavg", 42)]
        assert abs(sf_fa_42["test_pr_auc"] - 0.165317) < 1e-5

        # Sign-Flip + Single Krum Seed 456: 0.001104
        sf_sk_456 = per_seed[("sign_flip", "krum", 456)]
        assert abs(sf_sk_456["test_pr_auc"] - 0.001104) < 1e-5

        # Gaussian + FedAvg: 0.720153 +/- 0.003382
        gn_fa_seed_vals = [per_seed[("gaussian_noise", "fedavg", s)]["test_pr_auc"] for s in [42, 123, 456]]
        assert abs(float(np.mean(gn_fa_seed_vals)) - 0.720153) < 1e-5
        assert abs(float(np.std(gn_fa_seed_vals, ddof=1)) - 0.003382) < 1e-5

        # Gaussian + Trimmed Mean: 0.725945 +/- 0.000435
        gn_tm_seed_vals = [per_seed[("gaussian_noise", "trimmed_mean", s)]["test_pr_auc"] for s in [42, 123, 456]]
        assert abs(float(np.mean(gn_tm_seed_vals)) - 0.725945) < 1e-5
        assert abs(float(np.std(gn_tm_seed_vals, ddof=1)) - 0.000435) < 1e-5

        # ALIE + Trimmed Mean: 0.718892 +/- 0.017105 (agg 0.7189, mean retention 1.0023)
        al_tm_agg = agg[("alie_omniscient_z1", "trimmed_mean")]
        assert abs(al_tm_agg["pr_auc_mean"] - 0.7189) < 1e-4
        assert abs(al_tm_agg["pr_auc_std"] - 0.0171) < 1e-4
        assert abs(al_tm_agg["retention_ratio_mean"] - 1.0023) < 1e-4

        # ALIE + Multi-Krum: 0.713139 +/- 0.019129 (agg 0.7131, mean retention 0.9943)
        al_mk_agg = agg[("alie_omniscient_z1", "multi_krum")]
        assert abs(al_mk_agg["pr_auc_mean"] - 0.7131) < 1e-4
        assert abs(al_mk_agg["pr_auc_std"] - 0.0191) < 1e-4
        assert abs(al_mk_agg["retention_ratio_mean"] - 0.9943) < 1e-4

        # ALIE + Coordinate Median Seed 123: 0.204505
        al_cm_123 = per_seed[("alie_omniscient_z1", "coordinate_median", 123)]
        assert abs(al_cm_123["test_pr_auc"] - 0.204505) < 1e-5

        # ALIE + Bulyan Seed 123: 0.222000
        al_bu_123 = per_seed[("alie_omniscient_z1", "bulyan", 123)]
        assert abs(al_bu_123["test_pr_auc"] - 0.222000) < 1e-5

    def test_claim_registry_canonical_byzantine_promotion(self):
        """Verifies claim registry entries for canonical Byzantine results and historical quarantine."""
        import json

        registry_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        claims = { c["claim_id"]: c for c in data["claims"] }

        # Check canonical claims are present and verified
        assert "CLM-BYZ-CANONICAL-TRIMMED-MEAN" in claims
        assert claims["CLM-BYZ-CANONICAL-TRIMMED-MEAN"]["verification_status"] == "VERIFIED_EMPIRICAL_RUN"
        assert abs(claims["CLM-BYZ-CANONICAL-TRIMMED-MEAN"]["empirical_measured_value"] - 0.7141) < 1e-4

        assert "CLM-BYZ-CANONICAL-MULTIKRUM" in claims
        assert claims["CLM-BYZ-CANONICAL-MULTIKRUM"]["verification_status"] == "VERIFIED_EMPIRICAL_RUN"
        assert abs(claims["CLM-BYZ-CANONICAL-MULTIKRUM"]["empirical_measured_value"] - 0.7061) < 1e-4

        assert "CLM-BYZ-CANONICAL-ALIE-TRIMMED-MEAN" in claims
        assert claims["CLM-BYZ-CANONICAL-ALIE-TRIMMED-MEAN"]["verification_status"] == "VERIFIED_EMPIRICAL_RUN"
        assert abs(claims["CLM-BYZ-CANONICAL-ALIE-TRIMMED-MEAN"]["empirical_measured_value"] - 0.7189) < 1e-4

        assert "CLM-BYZ-CANONICAL-SEED-INSTABILITY" in claims
        assert claims["CLM-BYZ-CANONICAL-SEED-INSTABILITY"]["verification_status"] == "VERIFIED_EMPIRICAL_RUN"

        # Check historical proxy claims are quarantined
        assert claims["CLM-BYZ-TRIMMED"]["verification_status"] == "HISTORICAL_AUDITED"
        assert claims["CLM-BYZ-KRUM"]["verification_status"] == "HISTORICAL_AUDITED"
        assert claims["CLM-BYZ-BULYAN"]["verification_status"] == "HISTORICAL_AUDITED"

    def test_canonical_registry_promotion(self):
        """Verifies canonical_registry.py has promoted Byzantine benchmark to CANONICAL."""
        from benchmarks.canonical_registry import (
            CANONICAL_REGISTRY,
            ArtifactStatus,
            CommunicationEligibility,
        )

        byz_new = CANONICAL_REGISTRY["byzantine_federated_canonical"]
        assert byz_new.status == ArtifactStatus.CANONICAL
        assert byz_new.communication_eligibility == CommunicationEligibility.SAFE_WITH_CAVEAT
        assert byz_new.canonical_artifact_relpath == "benchmarks/results/raw/byzantine_federated_canonical.json"
        assert "Credit Card" in byz_new.dataset_name

        byz_old = CANONICAL_REGISTRY["byzantine_sign_inversion"]
        assert byz_old.status == ArtifactStatus.HISTORICAL
        assert byz_old.communication_eligibility == CommunicationEligibility.HISTORICAL_ONLY
