"""Canonical Byzantine Federated Robustness Benchmark Runner.

Orchestrates multi-round federated learning, local client training, adversarial delta attack injection,
robust aggregation, untouched test set evaluation, and atomic artifact serialization.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import average_precision_score, roc_auc_score
from torch.utils.data import DataLoader, TensorDataset

from benchmarks.byzantine.aggregators import (
    InvalidConfigurationError,
    aggregate_by_strategy,
)
from benchmarks.byzantine.attacks import create_attack
from benchmarks.byzantine.config import (
    AggregatorConfig,
    AttackConfig,
    ByzantineAggregatorType,
    ByzantineAttackType,
    ByzantineBenchmarkConfig,
    compute_condition_matrix_sha256,
    generate_canonical_condition_matrix,
    get_git_sha,
)
from benchmarks.byzantine.data import load_and_partition_byzantine_data
from benchmarks.byzantine.model import (
    FraudMLP,
    apply_model_delta,
    compute_model_delta,
    flatten_parameters,
)
from benchmarks.byzantine.schema import (
    ByzantineAggregatedMetric,
    ByzantineBenchmarkArtifact,
    ByzantineConditionResult,
    ByzantineRoundDiagnostic,
)

logger = logging.getLogger(__name__)


def evaluate_model_on_test_set(
    model: FraudMLP,
    test_dataset: TensorDataset,
    batch_size: int = 256,
) -> tuple[float, float]:
    """Evaluates untouched global test set, returning (average_precision, roc_auc)."""
    model.eval()
    loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)
    all_scores: list[float] = []
    all_targets: list[int] = []

    with torch.no_grad():
        for x_b, y_b in loader:
            probs = model.predict_proba(x_b)
            all_scores.extend(probs.cpu().numpy().tolist())
            all_targets.extend(y_b.cpu().numpy().astype(int).tolist())

    y_true = np.array(all_targets)
    y_pred = np.array(all_scores)

    # Handle numerical zero variance
    try:
        ap = float(average_precision_score(y_true, y_pred))
    except Exception:
        ap = float(np.mean(y_true))

    try:
        roc = float(roc_auc_score(y_true, y_pred))
    except Exception:
        roc = 0.5

    return round(ap, 6), round(roc, 6)


def train_client_local_model(
    global_model: FraudMLP,
    client_dataset: TensorDataset,
    epochs: int = 1,
    batch_size: int = 64,
    lr: float = 0.005,
    pos_weight: float = 10.0,
    seed: int = 42,
) -> FraudMLP:
    """Performs local client optimization on private client dataset partition."""
    local_model = FraudMLP(
        input_dim=global_model.input_dim,
        hidden_dims=[64, 32],
        dropout=0.1,
    )
    local_model.load_state_dict(global_model.state_dict())
    local_model.train()

    optimizer = torch.optim.Adam(local_model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight]))

    g = torch.Generator()
    g.manual_seed(seed)
    loader = DataLoader(client_dataset, batch_size=batch_size, shuffle=True, generator=g)

    for _ in range(epochs):
        for x_b, y_b in loader:
            optimizer.zero_grad()
            logits = local_model(x_b)
            loss = criterion(logits, y_b)
            loss.backward()
            optimizer.step()

    return local_model


class ByzantineFederatedRunner:
    """Executes multi-round federated training and robust aggregation evaluations."""

    def __init__(self, config: ByzantineBenchmarkConfig, repo_root: Path | None = None) -> None:
        self.config = config
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]

    def _validate_theoretical_preconditions(self, n: int, f: int) -> None:
        """Validates theoretical algorithm feasibility upfront before running experiments."""
        for ag in self.config.aggregators:
            t = ag.aggregator_type
            if t == ByzantineAggregatorType.KRUM:
                if n < 2 * f + 3:
                    raise InvalidConfigurationError(f"Krum requires n >= 2f + 3 (got n={n}, f={f}; required n >= {2*f+3}).")
            elif t == ByzantineAggregatorType.MULTI_KRUM:
                if n < 2 * f + 3:
                    raise InvalidConfigurationError(f"Multi-Krum requires n >= 2f + 3 (got n={n}, f={f}; required n >= {2*f+3}).")
            elif t == ByzantineAggregatorType.BULYAN:
                if n < 4 * f + 3:
                    raise InvalidConfigurationError(f"Bulyan requires n >= 4f + 3 (got n={n}, f={f}; required n >= {4*f+3}).")
            elif t == ByzantineAggregatorType.TRIMMED_MEAN:
                k = int(n * ag.trimmed_mean_beta)
                if 2 * k >= n:
                    raise InvalidConfigurationError(f"Trimmed mean with beta={ag.trimmed_mean_beta} trims 2k={2*k} >= {n} items.")

    def run_federated_condition(
        self,
        agg_config: AggregatorConfig,
        attack_config: AttackConfig | None,
        client_datasets: dict[int, TensorDataset],
        test_dataset: TensorDataset,
        initial_state_dict: dict[str, torch.Tensor],
        seed: int,
    ) -> tuple[float, float, list[ByzantineRoundDiagnostic]]:
        """Runs multi-round federated training under a specific (aggregator, attack) pairing."""
        n_clients = len(client_datasets)
        rounds = self.config.training.rounds
        local_epochs = self.config.training.local_epochs
        batch_size = self.config.training.batch_size
        lr = self.config.training.learning_rate
        pos_weight = self.config.training.pos_weight

        # Global model initialization from paired starting state
        global_model = FraudMLP(input_dim=self.config.dataset.feature_dim, hidden_dims=[64, 32], dropout=0.1)
        global_model.load_state_dict(initial_state_dict)

        attack_instance = create_attack(attack_config) if attack_config else None
        round_diagnostics: list[ByzantineRoundDiagnostic] = []

        client_sample_weights = [len(client_datasets[i]) for i in range(n_clients)]

        for r_idx in range(rounds):
            honest_deltas: list[torch.Tensor] = []
            honest_client_ids: list[int] = []
            all_deltas: list[torch.Tensor] = [None] * n_clients  # type: ignore

            # 1. Honest Local Training
            for c_id in range(n_clients):
                is_mal = attack_config is not None and c_id in attack_config.malicious_client_ids
                local_m = train_client_local_model(
                    global_model=global_model,
                    client_dataset=client_datasets[c_id],
                    epochs=local_epochs,
                    batch_size=batch_size,
                    lr=lr,
                    pos_weight=pos_weight,
                    seed=seed + r_idx * 100 + c_id,
                )
                delta = compute_model_delta(local_m, global_model)

                if is_mal:
                    # Malicious client produces local delta, then attack transforms it
                    pass
                else:
                    honest_deltas.append(delta)
                    honest_client_ids.append(c_id)
                    all_deltas[c_id] = delta

            # 2. Attack Transformation
            if attack_config and attack_instance:
                for mal_id in attack_config.malicious_client_ids:
                    # Generate legitimate base delta from malicious client's partition
                    local_m_mal = train_client_local_model(
                        global_model=global_model,
                        client_dataset=client_datasets[mal_id],
                        epochs=local_epochs,
                        batch_size=batch_size,
                        lr=lr,
                        pos_weight=pos_weight,
                        seed=seed + r_idx * 100 + mal_id,
                    )
                    base_delta = compute_model_delta(local_m_mal, global_model)
                    adv_delta = attack_instance.apply(
                        local_delta=base_delta,
                        client_id=mal_id,
                        round_idx=r_idx,
                        consortium_deltas=honest_deltas,
                        seed=seed,
                    )
                    all_deltas[mal_id] = adv_delta

            # 3. Robust Aggregation
            if agg_config.aggregator_type == ByzantineAggregatorType.FEDAVG:
                # FedAvg uses sample counts
                agg_delta = aggregate_by_strategy(
                    agg_type=agg_config.aggregator_type,
                    deltas=all_deltas,
                    weights=[float(w) for w in client_sample_weights],
                    config=agg_config,
                )
            else:
                agg_delta = aggregate_by_strategy(
                    agg_type=agg_config.aggregator_type,
                    deltas=all_deltas,
                    config=agg_config,
                )

            # 4. Global Model Update
            apply_model_delta(global_model, agg_delta)

            # Record round diagnostics
            honest_norms = [float(torch.norm(d).item()) for d in honest_deltas]
            h_mean = float(np.mean(honest_norms)) if honest_norms else 0.0
            h_std = float(np.std(honest_norms)) if honest_norms else 0.0
            mal_norms = [
                float(torch.norm(all_deltas[m_id]).item())
                for m_id in (attack_config.malicious_client_ids if attack_config else [])
            ]
            m_mean = float(np.mean(mal_norms)) if mal_norms else None

            round_diagnostics.append(
                ByzantineRoundDiagnostic(
                    round_idx=r_idx + 1,
                    honest_norm_mean=round(h_mean, 4),
                    honest_norm_std=round(h_std, 4),
                    malicious_norm_mean=round(m_mean, 4) if m_mean is not None else None,
                    aggregated_norm=round(float(torch.norm(agg_delta).item()), 4),
                )
            )

        # 5. Untouched Test Set Evaluation
        test_ap, test_roc = evaluate_model_on_test_set(global_model, test_dataset, batch_size=batch_size)
        return test_ap, test_roc, round_diagnostics

    def run_full_suite(
        self,
        is_canonical: bool = False,
        is_smoke: bool = False,
    ) -> ByzantineBenchmarkArtifact:
        """Executes full benchmark across seeds, attacks, and aggregators."""
        if is_canonical and is_smoke:
            raise ValueError("Benchmark cannot be both canonical and smoke test.")

        # Guard canonical execution
        git_sha, is_clean = get_git_sha(self.repo_root)
        if is_canonical:
            if not is_clean:
                raise RuntimeError("Canonical execution requires a clean Git working tree. Commit or stash changes first.")
            if len(self.config.seeds) < 3:
                raise ValueError(f"Canonical benchmark requires at least 3 seeds, got {len(self.config.seeds)}.")

        n = self.config.federation.n_clients
        f = self.config.federation.f_byzantine
        self._validate_theoretical_preconditions(n, f)

        per_seed_results: list[ByzantineConditionResult] = []
        all_round_diagnostics: list[ByzantineRoundDiagnostic] = []

        # Track clean baseline per seed for paired retention calculation
        clean_baselines: dict[int, float] = {}
        ds_meta: dict[str, Any] = {}

        for seed in self.config.seeds:
            logger.info("Executing Byzantine Benchmark for Seed %d...", seed)
            # Load and partition data strictly
            client_datasets, test_dataset, ds_meta = load_and_partition_byzantine_data(
                self.config, seed=seed, repo_root=self.repo_root
            )
            p_hash = ds_meta.get("partition_sha256", "")
            self.config.partition_sha256_by_seed[seed] = p_hash

            # Master initial model state for paired initialization
            torch.manual_seed(seed)
            init_model = FraudMLP(input_dim=self.config.dataset.feature_dim, hidden_dims=[64, 32], dropout=0.1)
            init_state = {k: v.clone() for k, v in init_model.state_dict().items()}
            flat_init = flatten_parameters(init_model).cpu().numpy().tobytes()
            init_hash = hashlib.sha256(flat_init).hexdigest()
            self.config.initial_state_dict_hash_by_seed[seed] = init_hash

            # 1. Clean FedAvg Baseline (actual_f=0, assumed_f=0)
            fedavg_cfg = AggregatorConfig(aggregator_type=ByzantineAggregatorType.FEDAVG)
            clean_ap, clean_roc, clean_diags = self.run_federated_condition(
                agg_config=fedavg_cfg,
                attack_config=None,
                client_datasets=client_datasets,
                test_dataset=test_dataset,
                initial_state_dict=init_state,
                seed=seed,
            )
            clean_baselines[seed] = clean_ap
            per_seed_results.append(
                ByzantineConditionResult(
                    seed=seed,
                    attack_name="none",
                    aggregator_name="clean_fedavg",
                    actual_f=0,
                    assumed_f=0,
                    test_pr_auc=clean_ap,
                    test_roc_auc=clean_roc,
                    retention_of_clean=1.0,
                    defense_self_retention=1.0,
                    clean_penalty=0.0,
                    rounds_completed=self.config.training.rounds,
                )
            )

            # 2. Clean Defense Conditions (Experiment B: actual_f=0, assumed_f=2)
            clean_defense_baselines: dict[str, float] = {}
            defense_aggregators = [
                ag for ag in self.config.aggregators
                if ag.aggregator_type != ByzantineAggregatorType.FEDAVG
            ]
            for d_agg_cfg in defense_aggregators:
                d_ap, d_roc, d_diags = self.run_federated_condition(
                    agg_config=d_agg_cfg,
                    attack_config=None,
                    client_datasets=client_datasets,
                    test_dataset=test_dataset,
                    initial_state_dict=init_state,
                    seed=seed,
                )
                clean_defense_baselines[d_agg_cfg.aggregator_type.value] = d_ap
                penalty = round(1.0 - (d_ap / max(1e-6, clean_ap)), 6)
                ret_clean = round(d_ap / max(1e-6, clean_ap), 6)
                per_seed_results.append(
                    ByzantineConditionResult(
                        seed=seed,
                        attack_name="none",
                        aggregator_name=f"clean_{d_agg_cfg.aggregator_type.value}",
                        actual_f=0,
                        assumed_f=self.config.assumed_byzantine_bound,
                        test_pr_auc=d_ap,
                        test_roc_auc=d_roc,
                        retention_of_clean=ret_clean,
                        defense_self_retention=1.0,
                        clean_penalty=penalty,
                        rounds_completed=self.config.training.rounds,
                    )
                )

            # 3. Poisoned FedAvg and Defenses under each configured attack (actual_f=2, assumed_f=2)
            for att_cfg in self.config.attacks:
                for agg_cfg in self.config.aggregators:
                    ap, roc, diags = self.run_federated_condition(
                        agg_config=agg_cfg,
                        attack_config=att_cfg,
                        client_datasets=client_datasets,
                        test_dataset=test_dataset,
                        initial_state_dict=init_state,
                        seed=seed,
                    )
                    ret = round(ap / max(1e-6, clean_ap), 6)
                    clean_d_ap = clean_defense_baselines.get(agg_cfg.aggregator_type.value, clean_ap)
                    self_ret = round(ap / max(1e-6, clean_d_ap), 6)
                    cond_att_name = "alie_omniscient_z1" if att_cfg.attack_type == ByzantineAttackType.ALIE else att_cfg.attack_type.value
                    per_seed_results.append(
                        ByzantineConditionResult(
                            seed=seed,
                            attack_name=cond_att_name,
                            aggregator_name=agg_cfg.aggregator_type.value,
                            actual_f=self.config.actual_byzantine_count,
                            assumed_f=self.config.assumed_byzantine_bound,
                            test_pr_auc=ap,
                            test_roc_auc=roc,
                            retention_of_clean=ret,
                            defense_self_retention=self_ret,
                            rounds_completed=self.config.training.rounds,
                        )
                    )
                    if len(all_round_diagnostics) < 10:
                        all_round_diagnostics.extend(diags)

        # Statistical multi-seed aggregation
        aggregated_metrics: list[ByzantineAggregatedMetric] = []
        conditions = sorted(list({(r.attack_name, r.aggregator_name) for r in per_seed_results}))

        for att_name, agg_name in conditions:
            matching = [r for r in per_seed_results if r.attack_name == att_name and r.aggregator_name == agg_name]
            pr_scores = [m.test_pr_auc for m in matching]
            roc_scores = [m.test_roc_auc for m in matching]
            ret_scores = [m.retention_of_clean for m in matching]
            self_ret_scores = [m.defense_self_retention for m in matching if m.defense_self_retention is not None]
            penalty_scores = [m.clean_penalty for m in matching if m.clean_penalty is not None]

            pr_mean = float(np.mean(pr_scores))
            pr_std = float(np.std(pr_scores, ddof=1)) if len(pr_scores) > 1 else 0.0
            roc_mean = float(np.mean(roc_scores))
            roc_std = float(np.std(roc_scores, ddof=1)) if len(roc_scores) > 1 else 0.0
            ret_mean = float(np.mean(ret_scores))
            ret_std = float(np.std(ret_scores, ddof=1)) if len(ret_scores) > 1 else 0.0

            self_ret_mean = float(np.mean(self_ret_scores)) if self_ret_scores else None
            penalty_mean = float(np.mean(penalty_scores)) if penalty_scores else None

            sample_item = matching[0]
            aggregated_metrics.append(
                ByzantineAggregatedMetric(
                    condition_name=f"{agg_name}_under_{att_name}",
                    attack_name=att_name,
                    aggregator_name=agg_name,
                    actual_f=sample_item.actual_f,
                    assumed_f=sample_item.assumed_f,
                    pr_auc_mean=round(pr_mean, 4),
                    pr_auc_std=round(pr_std, 4),
                    roc_auc_mean=round(roc_mean, 4),
                    roc_auc_std=round(roc_std, 4),
                    retention_ratio_mean=round(ret_mean, 4),
                    retention_ratio_std=round(ret_std, 4),
                    defense_self_retention_mean=round(self_ret_mean, 4) if self_ret_mean is not None else None,
                    clean_penalty_mean=round(penalty_mean, 4) if penalty_mean is not None else None,
                    n_seeds=len(matching),
                )
            )

        status = "CANONICAL" if is_canonical else ("SMOKE_TEST" if is_smoke else "EXPERIMENTAL")
        lifecycle = "VERIFIED_CANONICAL" if is_canonical else ("VALIDATION_ONLY" if is_smoke else "DRAFT")

        matrix = generate_canonical_condition_matrix(self.config)
        matrix_hash = compute_condition_matrix_sha256(matrix)
        self.config.condition_matrix_sha256 = matrix_hash

        artifact = ByzantineBenchmarkArtifact(
            benchmark_id=self.config.benchmark_id,
            schema_version=self.config.schema_version,
            status=status,
            lifecycle=lifecycle,
            timestamp_utc=datetime.datetime.now(datetime.UTC).isoformat(),
            git_sha=git_sha,
            is_working_tree_clean=is_clean,
            config_sha256=self.config.ensure_config_hash(),
            condition_matrix_sha256=matrix_hash,
            initial_state_dict_hash_by_seed=self.config.initial_state_dict_hash_by_seed,
            partition_sha256_by_seed=self.config.partition_sha256_by_seed,
            dataset={
                "name": self.config.dataset.dataset_name,
                "provenance": ds_meta.get("provenance_type"),
                "physical_sha256": self.config.dataset.physical_sha256,
                "feature_count": self.config.dataset.feature_dim,
                "total_train_samples": ds_meta.get("total_train_samples"),
                "total_test_samples": ds_meta.get("total_test_samples"),
                "test_positive_count": ds_meta.get("test_positive_count"),
                "test_prevalence": ds_meta.get("test_prevalence"),
            },
            federation={
                "n_clients": self.config.federation.n_clients,
                "actual_byzantine_count": self.config.actual_byzantine_count,
                "assumed_byzantine_bound": self.config.assumed_byzantine_bound,
                "f_byzantine": self.config.federation.f_byzantine,
                "byzantine_fraction": self.config.federation.f_byzantine / self.config.federation.n_clients,
                "partition_alpha": self.config.federation.partition_alpha,
                "client_metadata": ds_meta.get("client_metadata"),
            },
            training={
                "model": "FraudMLP(30 -> 64 -> 32 -> 1)",
                "loss": "BCEWithLogitsLoss",
                "optimizer": "Adam",
                "learning_rate": self.config.training.learning_rate,
                "batch_size": self.config.training.batch_size,
                "rounds": self.config.training.rounds,
                "local_epochs": self.config.training.local_epochs,
                "update_representation": self.config.training.update_representation,
            },
            seeds=self.config.seeds,
            attacks_evaluated=[
                ("alie_omniscient_z1" if a.attack_type == ByzantineAttackType.ALIE else a.attack_type.value)
                for a in self.config.attacks
            ],
            aggregators_evaluated=[ag.aggregator_type.value for ag in self.config.aggregators],
            per_seed_results=per_seed_results,
            aggregated_results=aggregated_metrics,
            round_diagnostics=all_round_diagnostics[:10],
            mandatory_caveat=(
                "Evaluates controlled Byzantine adversarial poisoning attacks on simulated federated clients. "
                "Does not prove universal Byzantine security across arbitrary unseen poisoning vectors."
            ),
            communication_scope="CONTROLLED_ADVERSARIAL_FEDERATED_BENCHMARK",
        )

        return artifact

    def save_artifact_atomically(
        self,
        artifact: ByzantineBenchmarkArtifact,
        output_path: Path,
        is_canonical: bool = False,
    ) -> Path:
        """Saves artifact using atomic write pattern: temp file -> schema validation -> atomic rename."""
        canonical_dest = self.repo_root / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"

        # Guard canonical path
        if output_path.resolve() == canonical_dest.resolve() and not is_canonical:
            raise PermissionError(
                f"Cannot write non-canonical output to canonical path: {canonical_dest}. "
                "Smoke tests and dry runs must specify a separate output location (e.g. experiments/byzantine/smoke/)."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = output_path.parent / f"{output_path.name}.tmp.{os.getpid()}"

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(artifact.model_dump(), f, indent=2)

            # Invariant Schema Validation before rename
            with open(temp_file, encoding="utf-8") as f:
                loaded = json.load(f)
            ByzantineBenchmarkArtifact.model_validate(loaded)

            # Atomic rename
            temp_file.replace(output_path)
            logger.info("Atomically saved validated benchmark artifact to: %s", output_path)
            return output_path
        finally:
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()
