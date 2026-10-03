"""Canonical Execution Engine for CrossBank v2 (CFI-CrossBank-02).

Orchestrates multi-institution dataset generation, partition-first feature extraction,
temporal splits, isolated/federated/centralized training, validation-threshold metric
derivation, and artifact assembly.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import torch

from benchmarks.crossbank_v2.config import (
    ConditionType,
    CrossBankV2Config,
    FeatureRegime,
    compute_experiment_matrix_hash,
    get_planned_experiment_matrix,
)
from benchmarks.crossbank_v2.features import (
    LOCAL_FEATURE_COLUMNS,
    LocalPreprocessor,
    PartitionFirstFeatureExtractor,
    compute_feature_schema_hash,
)
from benchmarks.crossbank_v2.generator import CrossBankV2NetworkGenerator
from benchmarks.crossbank_v2.metrics import (
    compute_comprehensive_metrics,
    compute_low_fpr_resolution,
    compute_scenario_specific_metrics,
    select_threshold_on_validation,
)
from benchmarks.crossbank_v2.model import (
    ColdStartLocalBaseline,
    CrossBankMLP,
    SimpleLogisticBaseline,
    aggregate_fedavg,
    train_pytorch_model,
)
from benchmarks.crossbank_v2.schema import (
    ConditionResult,
    CrossBankV2Artifact,
    InformationBudgetAccounting,
    SplitSummary,
    TrainingBudgetAccounting,
)

logger = logging.getLogger(__name__)


class CrossBankV2Runner:
    """Execution engine for CrossBank v2 controlled protocol."""

    def __init__(self, config: CrossBankV2Config | None = None) -> None:
        self.config = config or CrossBankV2Config()

    def run_experiment(
        self,
        seed: int = 42,
        is_smoke: bool = False,
    ) -> CrossBankV2Artifact:
        """Execute complete CrossBank v2 condition matrix under frozen protocol."""
        torch.manual_seed(seed)
        np.random.seed(seed)

        n_tx = self.config.smoke_transactions if is_smoke else self.config.canonical_transactions
        logger.info(
            "Starting CrossBank v2 execution: N=%d, seed=%d, smoke=%s",
            n_tx,
            seed,
            is_smoke,
        )

        # 1. Generate Raw Dataset
        generator = CrossBankV2NetworkGenerator(
            institutions=list(self.config.institutions),
            scenarios=list(self.config.scenarios),
            seed=seed,
        )
        df_raw = generator.generate_raw_dataset(
            n_total_transactions=n_tx,
            timesteps=self.config.timesteps,
            target_prevalence=self.config.target_prevalence,
        )
        dataset_content_hash = generator.compute_dataset_content_hash(df_raw)

        # 2. Strict 3-Way Temporal Split (Train / Validation / Test)
        train_df = pd.DataFrame(df_raw[df_raw["step"] <= self.config.train_end_step].copy().reset_index(drop=True))
        val_df = pd.DataFrame(
            df_raw[
                (df_raw["step"] > self.config.train_end_step)
                & (df_raw["step"] <= self.config.val_end_step)
            ]
            .copy()
            .reset_index(drop=True)
        )
        test_df = pd.DataFrame(df_raw[df_raw["step"] > self.config.val_end_step].copy().reset_index(drop=True))

        c_train_pos = int(
            train_df[(train_df["source_bank"] == "bank_c") | (train_df["target_bank"] == "bank_c")]["is_laundering"].sum()
        )
        c_val_pos = int(
            val_df[(val_df["source_bank"] == "bank_c") | (val_df["target_bank"] == "bank_c")]["is_laundering"].sum()
        )
        c_test_pos = int(
            test_df[(test_df["source_bank"] == "bank_c") | (test_df["target_bank"] == "bank_c")]["is_laundering"].sum()
        )
        c_sc7_train_pos = int(
            train_df[
                ((train_df["source_bank"] == "bank_c") | (train_df["target_bank"] == "bank_c"))
                & (train_df["scenario_id"] == "SCENARIO_7")
            ]["is_laundering"].sum()
        )

        split_summary = SplitSummary(
            train_step_range=(0, self.config.train_end_step),
            val_step_range=(self.config.train_end_step + 1, self.config.val_end_step),
            test_step_range=(self.config.val_end_step + 1, self.config.timesteps - 1),
            train_n=len(train_df),
            train_pos=int(train_df["is_laundering"].sum()),
            train_prevalence=float(train_df["is_laundering"].mean()),
            val_n=len(val_df),
            val_pos=int(val_df["is_laundering"].sum()),
            val_prevalence=float(val_df["is_laundering"].mean()),
            test_n=len(test_df),
            test_pos=int(test_df["is_laundering"].sum()),
            test_prevalence=float(test_df["is_laundering"].mean()),
            bank_c_train_pos=c_train_pos,
            bank_c_val_pos=c_val_pos,
            bank_c_test_pos=c_test_pos,
            bank_c_scenario7_train_pos=c_sc7_train_pos,
        )

        low_fpr_res = compute_low_fpr_resolution(
            test_df["is_laundering"].to_numpy(dtype=int),
            target_fpr=self.config.target_fpr,
        )

        # 3. Partition-First Feature Extraction & Local Preprocessing
        bank_ids = [inst.bank_id for inst in self.config.institutions]
        bank_data = self._prepare_bank_partitions(
            train_df, val_df, test_df, bank_ids, FeatureRegime.LOCAL_ONLY
        )

        # Also prepare consortium signal regime
        bank_data_consortium = self._prepare_bank_partitions(
            train_df, val_df, test_df, bank_ids, FeatureRegime.CONSORTIUM_SIGNAL
        )

        # 4. Centralized Preprocessing & Matrices (for matched centralized control)
        cent_data = self._prepare_centralized_data(
            train_df, val_df, test_df, FeatureRegime.LOCAL_ONLY
        )

        # 4b. Base neural model initialization for strict initial parameter parity
        base_mlp = CrossBankMLP(input_dim=len(LOCAL_FEATURE_COLUMNS), hidden_dim=self.config.hidden_dim)
        initial_model_state_hash = self._compute_model_state_hash(base_mlp)
        initial_state = copy.deepcopy(base_mlp.state_dict())

        # 5. Execute Conditions in Planned Matrix
        matrix = get_planned_experiment_matrix()
        condition_results: dict[str, ConditionResult] = {}

        for cond in matrix:
            c_id = cond.condition_id
            logger.info("Executing condition: %s (%s)", c_id, cond.condition_type)

            if cond.condition_type == ConditionType.LOCAL_ISOLATED:
                res = self._execute_local_isolated(
                    bank_data, test_df, val_df, cond.description, initial_state=initial_state
                )
            elif cond.condition_type == ConditionType.FEDERATED_FEDAVG:
                active_bank_data = (
                    bank_data_consortium
                    if cond.feature_regime == FeatureRegime.CONSORTIUM_SIGNAL
                    else bank_data
                )
                res = self._execute_federated_fedavg(
                    active_bank_data, test_df, val_df, cond.description, cond.feature_regime, initial_state=initial_state
                )
            elif cond.condition_type == ConditionType.CENTRALIZED_POOLED:
                res = self._execute_centralized_pooled(
                    cent_data, test_df, val_df, cond.description, initial_state=initial_state
                )
            elif cond.condition_type == ConditionType.COLD_START_ZERO_POSITIVE:
                res = self._execute_cold_start_transfer(
                    bank_data, test_df, val_df, cond.description
                )
            elif cond.condition_type == ConditionType.SIMPLE_BASELINE_LOGISTIC:
                res = self._execute_simple_logistic(
                    cent_data, test_df, val_df, cond.description, seed
                )
            else:
                continue

            condition_results[c_id] = res

        # 6. Assemble Final Artifact
        artifact = CrossBankV2Artifact(
            schema_version="2.1.0",
            benchmark_id=self.config.benchmark_id,
            data_provenance=self.config.data_provenance,
            federation_type=self.config.federation_type,
            real_world_validation="NONE",
            currency_unit=self.config.currency_unit,
            cold_start_estimand="ZERO_POSITIVE_INSTITUTION",
            preprocessing_regime="LOCAL_ONLY_PREPROCESSING",
            initial_model_state_hash=initial_model_state_hash,
            supersedes_manifest_sha256="3aeabcc8f2455ff5e7ab49f0bc1d8e809546786cdfcd2b91ac58eb3cb10ed81f",
            phase2e_candidate_manifest_sha256="2d12cdde3a1171cf5de3ba773eb0a64c55318e5d9acf3340bbdd8844fcc4190a",
            protocol_config_hash=self.config.compute_config_hash(),
            experiment_matrix_hash=compute_experiment_matrix_hash(matrix),
            generator_source_hash=generator.compute_generator_source_hash(),
            feature_schema_hash=compute_feature_schema_hash(FeatureRegime.LOCAL_ONLY),
            dataset_content_hash=dataset_content_hash,
            timestamp_utc=datetime.now(UTC).isoformat(),
            seed=seed,
            is_smoke_run=is_smoke,
            split_summary=split_summary,
            low_fpr_resolution=low_fpr_res,
            conditions=condition_results,
        )

        logger.info("CrossBank v2 execution finished successfully.")
        return artifact

    @staticmethod
    def _compute_model_state_hash(model: torch.nn.Module) -> str:
        """Compute SHA-256 digest of PyTorch model parameters for initial state parity audit."""
        hasher = hashlib.sha256()
        for k, v in sorted(model.state_dict().items()):
            hasher.update(k.encode("utf-8"))
            hasher.update(v.detach().cpu().numpy().tobytes())
        return hasher.hexdigest()

    def _prepare_bank_partitions(
        self,
        train_df: pd.DataFrame,
        val_df: pd.DataFrame,
        test_df: pd.DataFrame,
        bank_ids: list[str],
        regime: FeatureRegime,
    ) -> dict[str, dict[str, Any]]:
        """Extract partition-first features and fit client-local preprocessors strictly on train."""
        bank_data: dict[str, dict[str, Any]] = {}

        for b_id in bank_ids:
            # 1. Filter raw transactions visible to this bank
            b_train = pd.DataFrame(train_df[(train_df["source_bank"] == b_id) | (train_df["target_bank"] == b_id)].copy())
            b_val = pd.DataFrame(val_df[(val_df["source_bank"] == b_id) | (val_df["target_bank"] == b_id)].copy())
            b_test = pd.DataFrame(test_df[(test_df["source_bank"] == b_id) | (test_df["target_bank"] == b_id)].copy())

            # 2. Extract partition-first features (strict local visibility & causality)
            f_train = PartitionFirstFeatureExtractor.extract_features(b_train, b_id, regime)
            f_val = PartitionFirstFeatureExtractor.extract_features(b_val, b_id, regime)
            f_test = PartitionFirstFeatureExtractor.extract_features(b_test, b_id, regime)

            # 3. Fit LocalPreprocessor strictly on local train
            preproc = LocalPreprocessor().fit(f_train)
            X_train = preproc.transform(f_train)
            X_val = preproc.transform(f_val)
            X_test = preproc.transform(f_test)

            y_train = np.asarray(b_train["is_laundering"], dtype=int)
            y_val = np.asarray(b_val["is_laundering"], dtype=int)
            y_test = np.asarray(b_test["is_laundering"], dtype=int)

            bank_data[b_id] = {
                "b_train": b_train,
                "b_val": b_val,
                "b_test": b_test,
                "X_train": X_train,
                "y_train": y_train,
                "X_val": X_val,
                "y_val": y_val,
                "X_test": X_test,
                "y_test": y_test,
                "preproc": preproc,
                "regime": regime,
            }

        return bank_data

    def _prepare_centralized_data(
        self,
        train_df: pd.DataFrame,
        val_df: pd.DataFrame,
        test_df: pd.DataFrame,
        regime: FeatureRegime,
    ) -> dict[str, Any]:
        """Prepare centralized pooled dataset for matched upper-bound control."""
        # For centralized model, features use global transaction view
        # We model each transaction with respect to its source bank
        f_train_list, f_val_list, f_test_list = [], [], []

        for df, f_list in [(train_df, f_train_list), (val_df, f_val_list), (test_df, f_test_list)]:
            feats = []
            for b_id in ["bank_a", "bank_b", "bank_c"]:
                sub = pd.DataFrame(df[(df["source_bank"] == b_id) | (df["target_bank"] == b_id)])
                if len(sub) > 0:
                    f = PartitionFirstFeatureExtractor.extract_features(sub, b_id, regime)
                    feats.append(f)
            if feats:
                f_list.append(pd.concat(feats, ignore_index=True))

        f_tr = f_train_list[0] if f_train_list else pd.DataFrame(columns=pd.Index(LOCAL_FEATURE_COLUMNS))
        f_v = f_val_list[0] if f_val_list else pd.DataFrame(columns=pd.Index(LOCAL_FEATURE_COLUMNS))
        f_te = f_test_list[0] if f_test_list else pd.DataFrame(columns=pd.Index(LOCAL_FEATURE_COLUMNS))

        preproc = LocalPreprocessor().fit(f_tr)

        y_tr_arrays = [
            np.asarray(train_df[(train_df["source_bank"] == b) | (train_df["target_bank"] == b)]["is_laundering"], dtype=int)
            for b in ["bank_a", "bank_b", "bank_c"]
        ]
        y_val_arrays = [
            np.asarray(val_df[(val_df["source_bank"] == b) | (val_df["target_bank"] == b)]["is_laundering"], dtype=int)
            for b in ["bank_a", "bank_b", "bank_c"]
        ]
        y_te_arrays = [
            np.asarray(test_df[(test_df["source_bank"] == b) | (test_df["target_bank"] == b)]["is_laundering"], dtype=int)
            for b in ["bank_a", "bank_b", "bank_c"]
        ]

        return {
            "X_train": preproc.transform(f_tr),
            "y_train": np.concatenate(y_tr_arrays),
            "X_val": preproc.transform(f_v),
            "y_val": np.concatenate(y_val_arrays),
            "X_test": preproc.transform(f_te),
            "y_test": np.concatenate(y_te_arrays),
            "preproc": preproc,
        }

    def _predict_test_scores(
        self,
        test_df: pd.DataFrame,
        model_or_models: Any,
        bank_data: dict[str, dict[str, Any]] | None = None,
        cent_data: dict[str, Any] | None = None,
        regime: FeatureRegime = FeatureRegime.LOCAL_ONLY,
        is_isolated: bool = False,
    ) -> np.ndarray:
        """Score each transaction in test_df from the perspective of its originating source_bank."""
        scores = np.zeros(len(test_df), dtype=np.float32)

        for b_id in [inst.bank_id for inst in self.config.institutions]:
            mask = np.asarray(test_df["source_bank"] == b_id, dtype=bool)
            if not mask.any():
                continue
            b_sub = pd.DataFrame(test_df[mask].copy())
            feats = PartitionFirstFeatureExtractor.extract_features(b_sub, b_id, regime)

            if cent_data is not None:
                X_scaled = cent_data["preproc"].transform(feats)
                m = model_or_models
            elif bank_data is not None:
                X_scaled = bank_data[b_id]["preproc"].transform(feats)
                m = model_or_models[b_id] if is_isolated else model_or_models
            else:
                raise ValueError("Either bank_data or cent_data must be provided")

            scores[mask] = m.predict_proba(X_scaled)

        return scores

    def _execute_local_isolated(
        self,
        bank_data: dict[str, dict[str, Any]],
        test_df: pd.DataFrame,
        val_df: pd.DataFrame,
        desc: str,
        initial_state: dict[str, Any] | None = None,
    ) -> ConditionResult:
        """Execute independent local models per institution."""
        models: dict[str, CrossBankMLP] = {}
        total_steps = 0
        total_examples = 0
        per_bank_metrics = {}

        input_dim = len(LOCAL_FEATURE_COLUMNS)

        for b_id, bd in bank_data.items():
            m = CrossBankMLP(input_dim=input_dim, hidden_dim=self.config.hidden_dim)
            if initial_state is not None:
                m.load_state_dict(copy.deepcopy(initial_state))
            m, steps = train_pytorch_model(
                m,
                bd["X_train"],
                bd["y_train"],
                epochs=self.config.rounds * self.config.local_epochs_per_round,
                lr=self.config.learning_rate,
                batch_size=self.config.batch_size,
            )
            models[b_id] = m
            total_steps += steps
            total_examples += len(bd["X_train"]) * self.config.rounds * self.config.local_epochs_per_round

            # Bank local evaluation
            v_scores = m.predict_proba(bd["X_val"])
            t_scores = m.predict_proba(bd["X_test"])
            per_bank_metrics[b_id] = compute_comprehensive_metrics(
                bd["y_val"], v_scores, bd["y_test"], t_scores, target_fpr=self.config.target_fpr
            )

        # Global evaluation: evaluate transactions via host bank model
        val_scores_global = np.concatenate([models[b].predict_proba(bank_data[b]["X_val"]) for b in models])
        val_y_global = np.concatenate([bank_data[b]["y_val"] for b in models])
        test_scores_global = np.concatenate([models[b].predict_proba(bank_data[b]["X_test"]) for b in models])
        test_y_global = np.concatenate([bank_data[b]["y_test"] for b in models])

        overall_metrics = compute_comprehensive_metrics(
            val_y_global,
            val_scores_global,
            test_y_global,
            test_scores_global,
            target_fpr=self.config.target_fpr,
        )

        thresh, _ = select_threshold_on_validation(
            val_y_global, val_scores_global, target_fpr=self.config.target_fpr
        )
        test_scores_sc = self._predict_test_scores(
            test_df,
            models,
            bank_data=bank_data,
            regime=FeatureRegime.LOCAL_ONLY,
            is_isolated=True,
        )
        sc_metrics = compute_scenario_specific_metrics(
            test_df,
            test_scores_sc,
            thresh,
            self.config.scenarios,
        )

        return ConditionResult(
            condition_id="COND_LOCAL_ISOLATED",
            condition_type=ConditionType.LOCAL_ISOLATED.value,
            feature_regime=FeatureRegime.LOCAL_ONLY.value,
            description=desc,
            training_budget=TrainingBudgetAccounting(
                nominal_effective_passes=float(self.config.rounds * self.config.local_epochs_per_round),
                optimizer_steps=total_steps,
                examples_processed=total_examples,
                effective_passes=float(self.config.rounds * self.config.local_epochs_per_round),
                rounds=1,
                local_epochs_per_round=self.config.rounds * self.config.local_epochs_per_round,
                batch_size=self.config.batch_size,
                unique_information_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                local_client_view_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                budget_parity_classification="EXACT_NOMINAL_PASS_PARITY",
            ),
            information_budget=InformationBudgetAccounting(
                unique_training_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                unique_positive_examples=sum(int(np.sum(bd["y_train"] == 1)) for bd in bank_data.values()),
                unique_entities=sum(len(set(bd["b_train"]["source_account"])) for bd in bank_data.values()),
                unique_scenarios=len(self.config.scenarios),
                unique_institutions=len(bank_data),
            ),
            overall_metrics=overall_metrics,
            per_bank_metrics=per_bank_metrics,
            scenario_metrics=sc_metrics,
        )

    def _execute_federated_fedavg(
        self,
        bank_data: dict[str, dict[str, Any]],
        test_df: pd.DataFrame,
        val_df: pd.DataFrame,
        desc: str,
        regime: FeatureRegime,
        initial_state: dict[str, Any] | None = None,
    ) -> ConditionResult:
        """Execute Federated Averaging across all banking nodes."""
        input_dim = (
            len(LOCAL_FEATURE_COLUMNS) + 1
            if regime == FeatureRegime.CONSORTIUM_SIGNAL
            else len(LOCAL_FEATURE_COLUMNS)
        )
        global_model = CrossBankMLP(input_dim=input_dim, hidden_dim=self.config.hidden_dim)
        if initial_state is not None and regime == FeatureRegime.LOCAL_ONLY:
            global_model.load_state_dict(copy.deepcopy(initial_state))

        total_steps = 0
        total_examples = 0

        for _ in range(self.config.rounds):
            local_models = []
            weights = []

            for b_id, bd in bank_data.items():
                local_m = CrossBankMLP(input_dim=input_dim, hidden_dim=self.config.hidden_dim)
                local_m.load_state_dict(global_model.state_dict())
                local_m, steps = train_pytorch_model(
                    local_m,
                    bd["X_train"],
                    bd["y_train"],
                    epochs=self.config.local_epochs_per_round,
                    lr=self.config.learning_rate,
                    batch_size=self.config.batch_size,
                )
                local_models.append(local_m)
                weights.append(len(bd["X_train"]))
                total_steps += steps
                total_examples += len(bd["X_train"]) * self.config.local_epochs_per_round

            agg_state = aggregate_fedavg(local_models, weights)
            global_model.load_state_dict(agg_state)

        # Evaluate global consensus model per bank and globally
        per_bank_metrics = {}
        for b_id, bd in bank_data.items():
            v_scores = global_model.predict_proba(bd["X_val"])
            t_scores = global_model.predict_proba(bd["X_test"])
            per_bank_metrics[b_id] = compute_comprehensive_metrics(
                bd["y_val"], v_scores, bd["y_test"], t_scores, target_fpr=self.config.target_fpr
            )

        val_scores_global = np.concatenate([global_model.predict_proba(bank_data[b]["X_val"]) for b in bank_data])
        val_y_global = np.concatenate([bank_data[b]["y_val"] for b in bank_data])
        test_scores_global = np.concatenate([global_model.predict_proba(bank_data[b]["X_test"]) for b in bank_data])
        test_y_global = np.concatenate([bank_data[b]["y_test"] for b in bank_data])

        overall_metrics = compute_comprehensive_metrics(
            val_y_global,
            val_scores_global,
            test_y_global,
            test_scores_global,
            target_fpr=self.config.target_fpr,
        )

        thresh, _ = select_threshold_on_validation(
            val_y_global, val_scores_global, target_fpr=self.config.target_fpr
        )
        test_scores_sc = self._predict_test_scores(
            test_df,
            global_model,
            bank_data=bank_data,
            regime=regime,
            is_isolated=False,
        )
        sc_metrics = compute_scenario_specific_metrics(
            test_df,
            test_scores_sc,
            thresh,
            self.config.scenarios,
        )

        cond_id = (
            "COND_FEDERATED_CONSORTIUM_SIGNAL"
            if regime == FeatureRegime.CONSORTIUM_SIGNAL
            else "COND_FEDERATED_FEDAVG_LOCAL_FEATS"
        )

        return ConditionResult(
            condition_id=cond_id,
            condition_type=ConditionType.FEDERATED_FEDAVG.value,
            feature_regime=regime.value,
            description=desc,
            training_budget=TrainingBudgetAccounting(
                nominal_effective_passes=float(self.config.rounds * self.config.local_epochs_per_round),
                optimizer_steps=total_steps,
                examples_processed=total_examples,
                effective_passes=float(self.config.rounds * self.config.local_epochs_per_round),
                rounds=self.config.rounds,
                local_epochs_per_round=self.config.local_epochs_per_round,
                batch_size=self.config.batch_size,
                unique_information_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                local_client_view_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                budget_parity_classification="EXACT_NOMINAL_PASS_PARITY",
            ),
            information_budget=InformationBudgetAccounting(
                unique_training_rows=sum(len(bd["X_train"]) for bd in bank_data.values()),
                unique_positive_examples=sum(int(np.sum(bd["y_train"] == 1)) for bd in bank_data.values()),
                unique_entities=sum(len(set(bd["b_train"]["source_account"])) for bd in bank_data.values()),
                unique_scenarios=len(self.config.scenarios),
                unique_institutions=len(bank_data),
            ),
            overall_metrics=overall_metrics,
            per_bank_metrics=per_bank_metrics,
            scenario_metrics=sc_metrics,
        )

    def _execute_centralized_pooled(
        self,
        cent_data: dict[str, Any],
        test_df: pd.DataFrame,
        val_df: pd.DataFrame,
        desc: str,
        initial_state: dict[str, Any] | None = None,
    ) -> ConditionResult:
        """Execute centralized pooled model with matched optimization budget."""
        input_dim = len(LOCAL_FEATURE_COLUMNS)
        model = CrossBankMLP(input_dim=input_dim, hidden_dim=self.config.hidden_dim)
        if initial_state is not None:
            model.load_state_dict(copy.deepcopy(initial_state))

        model, steps = train_pytorch_model(
            model,
            cent_data["X_train"],
            cent_data["y_train"],
            epochs=self.config.centralized_epochs,
            lr=self.config.learning_rate,
            batch_size=self.config.batch_size,
        )

        val_scores = model.predict_proba(cent_data["X_val"])
        test_scores = model.predict_proba(cent_data["X_test"])

        overall_metrics = compute_comprehensive_metrics(
            cent_data["y_val"],
            val_scores,
            cent_data["y_test"],
            test_scores,
            target_fpr=self.config.target_fpr,
        )

        thresh, _ = select_threshold_on_validation(
            cent_data["y_val"], val_scores, target_fpr=self.config.target_fpr
        )
        test_scores_sc = self._predict_test_scores(
            test_df,
            model,
            cent_data=cent_data,
            regime=FeatureRegime.LOCAL_ONLY,
        )
        sc_metrics = compute_scenario_specific_metrics(
            test_df,
            test_scores_sc,
            thresh,
            self.config.scenarios,
        )

        return ConditionResult(
            condition_id="COND_CENTRALIZED_POOLED",
            condition_type=ConditionType.CENTRALIZED_POOLED.value,
            feature_regime=FeatureRegime.LOCAL_ONLY.value,
            description=desc,
            training_budget=TrainingBudgetAccounting(
                nominal_effective_passes=float(self.config.centralized_epochs),
                optimizer_steps=steps,
                examples_processed=len(cent_data["X_train"]) * self.config.centralized_epochs,
                effective_passes=float(self.config.centralized_epochs),
                rounds=1,
                local_epochs_per_round=self.config.centralized_epochs,
                batch_size=self.config.batch_size,
                unique_information_rows=len(cent_data["X_train"]),
                local_client_view_rows=len(cent_data["X_train"]),
                budget_parity_classification="EXACT_NOMINAL_PASS_PARITY",
            ),
            information_budget=InformationBudgetAccounting(
                unique_training_rows=len(cent_data["X_train"]),
                unique_positive_examples=int(np.sum(cent_data["y_train"] == 1)),
                unique_entities=len(cent_data["X_train"]),
                unique_scenarios=len(self.config.scenarios),
                unique_institutions=3,
            ),
            overall_metrics=overall_metrics,
            per_bank_metrics={"consortium_union": overall_metrics},
            scenario_metrics=sc_metrics,
        )

    def _execute_cold_start_transfer(
        self,
        bank_data: dict[str, dict[str, Any]],
        test_df: pd.DataFrame,
        val_df: pd.DataFrame,
        desc: str,
    ) -> ConditionResult:
        """Evaluate cold-start transfer: local empirical prior vs federated transfer."""
        gamma_data = bank_data["bank_c"]
        n_neg = len(gamma_data["y_train"])
        baseline = ColdStartLocalBaseline(train_negative_count=n_neg)

        val_scores = baseline.predict_proba(gamma_data["X_val"])
        test_scores = baseline.predict_proba(gamma_data["X_test"])

        metrics = compute_comprehensive_metrics(
            gamma_data["y_val"],
            val_scores,
            gamma_data["y_test"],
            test_scores,
            target_fpr=self.config.target_fpr,
        )

        thresh, _ = select_threshold_on_validation(
            gamma_data["y_val"], val_scores, target_fpr=self.config.target_fpr
        )
        test_scores_sc = baseline.predict_proba(np.zeros((len(test_df), 1)))
        sc_metrics = compute_scenario_specific_metrics(
            test_df,
            test_scores_sc,
            thresh,
            self.config.scenarios,
        )

        return ConditionResult(
            condition_id="COND_COLD_START_ZERO_POSITIVE",
            condition_type=ConditionType.COLD_START_ZERO_POSITIVE.value,
            feature_regime=FeatureRegime.LOCAL_ONLY.value,
            description=desc,
            training_budget=TrainingBudgetAccounting(
                nominal_effective_passes=0.0,
                optimizer_steps=0,
                examples_processed=n_neg,
                effective_passes=0.0,
                rounds=0,
                local_epochs_per_round=0,
                batch_size=self.config.batch_size,
                unique_information_rows=n_neg,
                local_client_view_rows=n_neg,
                budget_parity_classification="NON_OPTIMIZED_EMPIRICAL_PRIOR",
            ),
            information_budget=InformationBudgetAccounting(
                unique_training_rows=n_neg,
                unique_positive_examples=0,
                unique_entities=n_neg,
                unique_scenarios=0,
                unique_institutions=1,
            ),
            overall_metrics=metrics,
            per_bank_metrics={"bank_c": metrics},
            scenario_metrics=sc_metrics,
        )

    def _execute_simple_logistic(
        self,
        cent_data: dict[str, Any],
        test_df: pd.DataFrame,
        val_df: pd.DataFrame,
        desc: str,
        seed: int,
    ) -> ConditionResult:
        """Evaluate non-neural Logistic Regression baseline to benchmark separability."""
        clf = SimpleLogisticBaseline(seed=seed).fit(cent_data["X_train"], cent_data["y_train"])

        val_scores = clf.predict_proba(cent_data["X_val"])
        test_scores = clf.predict_proba(cent_data["X_test"])

        metrics = compute_comprehensive_metrics(
            cent_data["y_val"],
            val_scores,
            cent_data["y_test"],
            test_scores,
            target_fpr=self.config.target_fpr,
        )

        thresh, _ = select_threshold_on_validation(
            cent_data["y_val"], val_scores, target_fpr=self.config.target_fpr
        )
        test_scores_sc = self._predict_test_scores(
            test_df,
            clf,
            cent_data=cent_data,
            regime=FeatureRegime.LOCAL_ONLY,
        )
        sc_metrics = compute_scenario_specific_metrics(
            test_df,
            test_scores_sc,
            thresh,
            self.config.scenarios,
        )

        return ConditionResult(
            condition_id="COND_SIMPLE_BASELINE_LOGISTIC",
            condition_type=ConditionType.SIMPLE_BASELINE_LOGISTIC.value,
            feature_regime=FeatureRegime.LOCAL_ONLY.value,
            description=desc,
            training_budget=TrainingBudgetAccounting(
                nominal_effective_passes=1.0,
                optimizer_steps=100,
                examples_processed=len(cent_data["X_train"]),
                effective_passes=1.0,
                rounds=1,
                local_epochs_per_round=1,
                batch_size=len(cent_data["X_train"]),
                unique_information_rows=len(cent_data["X_train"]),
                local_client_view_rows=len(cent_data["X_train"]),
                budget_parity_classification="CONVEX_L2_LOGISTIC",
            ),
            information_budget=InformationBudgetAccounting(
                unique_training_rows=len(cent_data["X_train"]),
                unique_positive_examples=int(np.sum(cent_data["y_train"] == 1)),
                unique_entities=len(cent_data["X_train"]),
                unique_scenarios=len(self.config.scenarios),
                unique_institutions=3,
            ),
            overall_metrics=metrics,
            per_bank_metrics={"centralized_union": metrics},
            scenario_metrics=sc_metrics,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="CrossBank v2 Protocol Runner")
    parser.add_argument("--canonical", action="store_true", help="Execute canonical protocol under strict frozen manifest")
    parser.add_argument("--smoke", action="store_true", help="Execute in fast smoke mode")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic random seed")
    parser.add_argument("--verify-manifest", action="store_true", help="Validate active source and configuration against frozen manifest")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    runner = CrossBankV2Runner()

    if args.verify_manifest:
        from benchmarks.crossbank_v2.verifier import CrossBankV2ProtocolVerifier
        results = CrossBankV2ProtocolVerifier.verify_manifest(config=runner.config)
        print("SUCCESS: Protocol manifest verification passed all gates:")
        for k, v in results.items():
            print(f"  [PASS] {k}: {v}")
        return

    # CLI Immutability & Parameter Locking
    if args.canonical:
        if args.smoke:
            raise ValueError("CANONICAL_CLI_ERROR: --canonical and --smoke are strictly mutually exclusive.")
        if args.seed is not None and args.seed not in runner.config.seeds:
            raise ValueError(
                f"CANONICAL_CLI_ERROR: Cannot override seed to {args.seed} under --canonical. "
                f"Canonical protocol strictly permits only frozen seeds: {runner.config.seeds}"
            )
        # Runtime Hash Binding: Fail closed before training
        from benchmarks.crossbank_v2.verifier import CrossBankV2ProtocolVerifier
        CrossBankV2ProtocolVerifier.verify_manifest(config=runner.config)
        target_seed = args.seed if args.seed is not None else runner.config.seeds[0]
        artifact = runner.run_experiment(seed=target_seed, is_smoke=False)
    else:
        target_seed = args.seed if args.seed is not None else 42
        artifact = runner.run_experiment(seed=target_seed, is_smoke=args.smoke)

    print("\n=== CrossBank v2 Execution Completed ===")
    print(f"Benchmark ID: {artifact.benchmark_id}")
    print(f"Data Provenance: {artifact.data_provenance}")
    print(f"Protocol Config Hash: {artifact.protocol_config_hash}")
    print(f"Total Conditions: {len(artifact.conditions)}")


if __name__ == "__main__":
    main()
