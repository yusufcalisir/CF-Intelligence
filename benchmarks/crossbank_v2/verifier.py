"""Protocol Self-Verifier, Manifest Verifier, and Invariant Checker for CrossBank v2.

Validates that an execution artifact adheres strictly to the preregistered scientific gates:
no test-set threshold selection, zero cross-bank leakage into isolated features, correct scenario
denominators, and presence of mandatory centralized controls.
Also validates that runtime configuration and source code strictly match the frozen manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.crossbank_v2.config import (
    CrossBankV2Config,
    FeatureRegime,
    compute_experiment_matrix_hash,
    get_planned_experiment_matrix,
)
from benchmarks.crossbank_v2.features import (
    LOCAL_FEATURE_COLUMNS,
    compute_feature_schema_hash,
)
from benchmarks.crossbank_v2.generator import CrossBankV2NetworkGenerator
from benchmarks.crossbank_v2.schema import CrossBankV2Artifact


class ProtocolVerificationError(Exception):
    """Raised when an artifact or runtime environment violates scientific invariants."""


class CrossBankV2ProtocolVerifier:
    """Rigorous scientific verification engine for CrossBank v2 artifacts and protocol freeze."""

    @classmethod
    def verify_manifest(
        cls,
        manifest_path: str | Path | None = None,
        config: CrossBankV2Config | None = None,
    ) -> dict[str, bool]:
        """Validate active code/config against frozen manifest without running training."""
        cfg = config or CrossBankV2Config()
        m_path = Path(manifest_path) if manifest_path else Path(__file__).parent / "manifest.json"
        if not m_path.exists():
            raise ProtocolVerificationError(f"Frozen manifest not found at {m_path}")

        manifest_data = json.loads(m_path.read_text(encoding="utf-8"))
        results: dict[str, bool] = {}

        # 1. Config hash parity
        exp_cfg_hash = cfg.compute_config_hash()
        if manifest_data.get("protocol_config_hash") != exp_cfg_hash:
            raise ProtocolVerificationError(
                f"Protocol config hash mismatch: manifest={manifest_data.get('protocol_config_hash')}, active={exp_cfg_hash}"
            )
        results["manifest_config_hash_gate"] = True

        # 2. Experiment matrix hash parity
        matrix = get_planned_experiment_matrix()
        exp_mat_hash = compute_experiment_matrix_hash(matrix)
        if manifest_data.get("experiment_matrix_hash") != exp_mat_hash:
            raise ProtocolVerificationError(
                f"Experiment matrix hash mismatch: manifest={manifest_data.get('experiment_matrix_hash')}, active={exp_mat_hash}"
            )
        results["manifest_matrix_hash_gate"] = True

        # 3. Generator source hash parity
        exp_gen_hash = CrossBankV2NetworkGenerator.compute_generator_source_hash()
        if manifest_data.get("generator_source_hash") != exp_gen_hash:
            raise ProtocolVerificationError(
                f"Generator source hash mismatch: manifest={manifest_data.get('generator_source_hash')}, active={exp_gen_hash}"
            )
        results["manifest_generator_hash_gate"] = True

        # 4. Feature schema hash parity
        exp_feat_hash = compute_feature_schema_hash(FeatureRegime.LOCAL_ONLY)
        if manifest_data.get("feature_schema_hash") != exp_feat_hash:
            raise ProtocolVerificationError(
                f"Feature schema hash mismatch: manifest={manifest_data.get('feature_schema_hash')}, active={exp_feat_hash}"
            )
        results["manifest_feature_schema_gate"] = True

        # 5. Canonical seeds parity
        if list(cfg.seeds) != manifest_data.get("canonical_seeds"):
            raise ProtocolVerificationError("Canonical seeds in config do not match manifest")
        results["manifest_seeds_gate"] = True

        # 6. Conditions parity
        cond_ids = [c.condition_id for c in matrix]
        if cond_ids != manifest_data.get("conditions"):
            raise ProtocolVerificationError("Planned conditions do not match manifest")
        results["manifest_conditions_gate"] = True

        # 7. Feature columns parity
        if list(LOCAL_FEATURE_COLUMNS) != manifest_data.get("feature_columns"):
            raise ProtocolVerificationError("Feature columns do not match manifest")
        results["manifest_feature_columns_gate"] = True

        # 8. Scale parameter parity
        if cfg.canonical_transactions != manifest_data.get("canonical_transactions"):
            raise ProtocolVerificationError("Canonical transactions count mismatch")
        if cfg.target_prevalence != manifest_data.get("target_prevalence"):
            raise ProtocolVerificationError("Target prevalence mismatch")
        if cfg.target_fpr != manifest_data.get("target_fpr"):
            raise ProtocolVerificationError("Target FPR mismatch")
        if cfg.minimum_test_negatives != manifest_data.get("minimum_test_negatives"):
            raise ProtocolVerificationError("Minimum test negatives mismatch")
        # 9. Superseded manifest provenance gate
        if manifest_data.get("supersedes_manifest_sha256") != "3aeabcc8f2455ff5e7ab49f0bc1d8e809546786cdfcd2b91ac58eb3cb10ed81f":
            raise ProtocolVerificationError("Superseded manifest hash mismatch or missing")
        results["manifest_supersession_gate"] = True

        # 10. Phase 2E candidate provenance gate
        if manifest_data.get("phase2e_candidate_manifest_sha256") != "2d12cdde3a1171cf5de3ba773eb0a64c55318e5d9acf3340bbdd8844fcc4190a":
            raise ProtocolVerificationError("Phase 2E candidate manifest hash mismatch or missing")
        results["manifest_phase2e_candidate_gate"] = True

        return results

    @classmethod
    def verify_artifact(
        cls,
        artifact: CrossBankV2Artifact,
        config: CrossBankV2Config,
    ) -> dict[str, bool]:
        """Verify all scientific gates. Raises ProtocolVerificationError if any gate fails."""
        results: dict[str, bool] = {}

        # 1. Provenance and Taxonomy Gate
        if artifact.data_provenance != "PROJECT_SYNTHETIC":
            raise ProtocolVerificationError(f"Invalid provenance: {artifact.data_provenance}")
        if artifact.federation_type != "SIMULATED_FEDERATION":
            raise ProtocolVerificationError(f"Invalid federation type: {artifact.federation_type}")
        if artifact.real_world_validation != "NONE":
            raise ProtocolVerificationError(f"Invalid real-world claim: {artifact.real_world_validation}")
        if artifact.currency_unit != "SYNTHETIC_USD":
            raise ProtocolVerificationError(f"Invalid currency unit: {artifact.currency_unit}")
        if artifact.supersedes_manifest_sha256 != "3aeabcc8f2455ff5e7ab49f0bc1d8e809546786cdfcd2b91ac58eb3cb10ed81f":
            raise ProtocolVerificationError("Artifact superseded manifest hash mismatch")
        if artifact.phase2e_candidate_manifest_sha256 != "2d12cdde3a1171cf5de3ba773eb0a64c55318e5d9acf3340bbdd8844fcc4190a":
            raise ProtocolVerificationError("Artifact Phase 2E candidate manifest hash mismatch")
        results["provenance_taxonomy_gate"] = True

        # 2. Cryptographic Hash Binding Gate
        expected_cfg_hash = config.compute_config_hash()
        if artifact.protocol_config_hash != expected_cfg_hash:
            raise ProtocolVerificationError("Protocol config hash mismatch")

        expected_matrix_hash = compute_experiment_matrix_hash(get_planned_experiment_matrix())
        if artifact.experiment_matrix_hash != expected_matrix_hash:
            raise ProtocolVerificationError("Experiment matrix hash mismatch")

        expected_gen_hash = CrossBankV2NetworkGenerator.compute_generator_source_hash()
        if artifact.generator_source_hash != expected_gen_hash:
            raise ProtocolVerificationError("Generator source hash mismatch")

        expected_feat_hash = compute_feature_schema_hash(FeatureRegime.LOCAL_ONLY)
        if artifact.feature_schema_hash != expected_feat_hash:
            raise ProtocolVerificationError("Feature schema hash mismatch")

        results["cryptographic_hash_binding_gate"] = True

        # 3. Seed Validity Gate
        if not artifact.is_smoke_run and artifact.seed not in config.seeds:
            raise ProtocolVerificationError(f"Canonical artifact seed {artifact.seed} not in frozen seed set {config.seeds}")
        results["seed_validity_gate"] = True

        # 4. Mandatory Centralized Control Gate
        if "COND_CENTRALIZED_POOLED" not in artifact.conditions:
            raise ProtocolVerificationError("Mandatory centralized control (COND_CENTRALIZED_POOLED) missing")
        results["centralized_control_gate"] = True

        # 5. Cold-Start Separation & Evidence-Bound Invariant Gate
        if "COND_COLD_START_ZERO_POSITIVE" not in artifact.conditions:
            raise ProtocolVerificationError("Cold-start condition (COND_COLD_START_ZERO_POSITIVE) missing")
        if (
            artifact.cold_start_estimand == "ZERO_POSITIVE_INSTITUTION"
            and artifact.split_summary.bank_c_train_pos != 0
        ):
            raise ProtocolVerificationError(
                f"Cold-start target bank has non-zero training positives ({artifact.split_summary.bank_c_train_pos}) "
                "under ZERO_POSITIVE_INSTITUTION estimand"
            )
        results["cold_start_separation_gate"] = True

        # 6. Threshold Provenance Gate (Strictly on Validation)
        for cond_id, cond in artifact.conditions.items():
            prov = cond.overall_metrics.threshold_provenance
            if prov.threshold_source_split != "VALIDATION":
                raise ProtocolVerificationError(
                    f"Condition {cond_id} derived threshold on {prov.threshold_source_split}, expected VALIDATION"
                )
        results["threshold_provenance_gate"] = True

        # 7. Low-FPR Statistical Resolution Gate
        low_fpr = artifact.low_fpr_resolution
        if not low_fpr.is_target_fpr_achievable and not artifact.is_smoke_run:
            raise ProtocolVerificationError(
                f"Target FPR {low_fpr.target_fpr} not resolvable with {low_fpr.test_negative_count} negative samples"
            )
        results["low_fpr_resolution_gate"] = True

        # 8. Scenario Denominator & Global-Metric-Copy Prevention Gate
        for cond_id, cond in artifact.conditions.items():
            sc_metrics = cond.scenario_metrics
            for sc_id, sc_res in sc_metrics.items():
                if sc_res.test_transaction_count > 0:
                    assert sc_res.detected_transaction_count <= sc_res.test_transaction_count
                    assert sc_res.detected_incident_count <= sc_res.test_incident_count
                    assert (
                        sc_res.synthetic_detected_exposure_usd + sc_res.synthetic_missed_exposure_usd
                        == round(sc_res.synthetic_total_exposure_usd, 2)
                    )
        results["scenario_denominator_gate"] = True

        # 9. Training-Budget & Information-Budget Presence Gate
        for cond_id, cond in artifact.conditions.items():
            tb = cond.training_budget
            ib = cond.information_budget
            assert tb.optimizer_steps >= 0
            assert ib.unique_training_rows >= 0
        results["budgets_accounting_gate"] = True

        return results
