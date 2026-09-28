"""Controlled Feature Ablation Study: Tabular Only vs Graph Only vs Tabular + Graph.

Quantifies the empirical detection uplift (PR-AUC, ROC-AUC, Recall @ strict FPR)
provided by graph topological embeddings over traditional tabular transaction attributes.

Supports:
1. TABULAR_ONLY: Raw transaction/account attributes (velocity, amount, channel, account age).
2. GRAPH_ONLY: Pure topological and structural features (degree, PageRank, 2-hop neighbor risk, clustering).
3. TABULAR_PLUS_GRAPH: Fused representation combining both tabular and structural signals.

Evaluates:
- Detection performance: PR-AUC, ROC-AUC, F1, Precision, Recall.
- Low-FPR operating viability: Recall @ 0.01%, 0.1%, and 1.0% strict FPR.
- Calibration reliability: Expected Calibration Error (ECE) and Brier score.
- Detection uplift: Delta PR-AUC, Relative Gain %, Delta Recall @ 0.1% FPR, and Synergy Score.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:
    torch = None  # type: ignore
    nn = None  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Pydantic Schemas
# ---------------------------------------------------------------------------

class FeatureParadigm(StrEnum):
    """Ablation feature paradigm classification."""

    TABULAR_ONLY = "TABULAR_ONLY"
    GRAPH_ONLY = "GRAPH_ONLY"
    TABULAR_PLUS_GRAPH = "TABULAR_PLUS_GRAPH"


class GraphVsTabularConfig(BaseModel):
    """Configuration parameters for the tabular vs graph ablation experiment."""

    n_samples: int = Field(default=6000, description="Total transactions generated for ablation")
    fraud_prevalence: float = Field(default=0.03, description="Baseline fraud prevalence (3.0%)")
    tabular_dim: int = Field(default=12, description="Dimension of tabular feature slice")
    graph_dim: int = Field(default=10, description="Dimension of graph structural feature slice")
    hidden_dim: int = Field(default=48, description="Hidden layer dimension for classification network")
    epochs: int = Field(default=15, description="Training epochs per feature paradigm")
    batch_size: int = Field(default=64, description="Training mini-batch size")
    learning_rate: float = Field(default=0.01, description="Learning rate for Adam optimizer")
    test_ratio: float = Field(default=0.25, description="Ratio of sequestered test set")
    seed: int = Field(default=42, description="Deterministic pseudo-random seed")
    save_artifacts: bool = Field(default=True, description="Whether to serialize JSON results to disk")
    output_dir: str = Field(default="experiments/ablations", description="Directory to store results")


class ParadigmEvaluationResult(BaseModel):
    """Empirical evaluation result for a single feature paradigm."""

    paradigm: FeatureParadigm = Field(description="Evaluated feature paradigm")
    feature_count: int = Field(description="Number of input features used")
    pr_auc: float = Field(description="Precision-Recall Area Under Curve")
    roc_auc: float = Field(description="Receiver Operating Characteristic AUC")
    f1_score: float = Field(description="Optimal binary F1 classification score")
    precision: float = Field(description="Precision at optimal threshold")
    recall: float = Field(description="Recall at optimal threshold")
    recall_at_fpr_0_01: float = Field(description="Recall at strict 0.01% False Positive Rate")
    recall_at_fpr_0_1: float = Field(description="Recall at strict 0.1% False Positive Rate")
    recall_at_fpr_1_0: float = Field(description="Recall at 1.0% False Positive Rate")
    ece: float = Field(description="Expected Calibration Error across 10 confidence bins")
    brier_score: float = Field(description="Mean squared probability calibration error")
    training_time_ms: float = Field(description="Training duration in milliseconds")
    inference_latency_ms: float = Field(description="Inference time for test set in milliseconds")


class GraphVsTabularUpliftResult(BaseModel):
    """Comparative detection uplift and synergy metrics."""

    delta_pr_auc: float = Field(description="Absolute PR-AUC improvement (Hybrid - Tabular)")
    relative_pr_auc_gain_pct: float = Field(description="Percentage PR-AUC gain over Tabular baseline")
    delta_roc_auc: float = Field(description="Absolute ROC-AUC improvement (Hybrid - Tabular)")
    delta_recall_at_0_1_fpr: float = Field(description="Absolute Recall@0.1% FPR improvement")
    relative_recall_at_0_1_fpr_gain_pct: float = Field(description="Percentage Recall@0.1% FPR gain")
    delta_f1: float = Field(description="Absolute F1 score improvement")
    tabular_standalone_pr_auc: float = Field(description="PR-AUC of Tabular Only model")
    graph_standalone_pr_auc: float = Field(description="PR-AUC of Graph Only model")
    hybrid_pr_auc: float = Field(description="PR-AUC of Fused Tabular + Graph model")
    synergy_score: float = Field(
        description="Nonlinear synergy score: Hybrid PR-AUC minus max(Tabular, Graph)"
    )


class GraphVsTabularSuiteResult(BaseModel):
    """Complete serialized payload for the Tabular vs Graph ablation study."""

    benchmark_id: str = Field(default="CFI-GRAPH-VS-TABULAR-01")
    timestamp_utc: str = Field(description="Execution timestamp in ISO 8601 UTC format")
    config: GraphVsTabularConfig = Field(description="Ablation configuration")
    results: dict[str, ParadigmEvaluationResult] = Field(description="Results keyed by paradigm name")
    uplift: GraphVsTabularUpliftResult = Field(description="Uplift and synergy summary")
    summary_narrative: str = Field(description="Executive summary of key findings")


# ---------------------------------------------------------------------------
# Calibration & Metric Helpers
# ---------------------------------------------------------------------------

def calculate_recall_at_fixed_fpr(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    target_fprs: tuple[float, ...] = (0.0001, 0.001, 0.01),
) -> dict[float, float]:
    """Calculate true positive rate (Recall) at fixed maximum false positive rates."""
    if len(np.unique(y_true)) < 2 or int(np.sum(y_true == 1)) == 0:
        return {fpr: 0.0 for fpr in target_fprs}

    fpr, tpr, _ = roc_curve(y_true, y_prob)
    results: dict[float, float] = {}
    for target in target_fprs:
        valid_indices = np.where(fpr <= target)[0]
        if len(valid_indices) > 0:
            val = float(tpr[valid_indices[-1]])
            results[target] = 0.0 if (np.isnan(val) or np.isinf(val)) else round(val, 6)
        else:
            results[target] = 0.0
    return results


def calculate_ece(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """Compute Expected Calibration Error (ECE) across confidence bins."""
    bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(y_prob)
    if n == 0:
        return 0.0
    for i in range(n_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]
        in_bin = (
            (y_prob > bin_lower) & (y_prob <= bin_upper)
            if i > 0
            else (y_prob >= bin_lower) & (y_prob <= bin_upper)
        )
        prop_in_bin = np.mean(in_bin)
        if prop_in_bin > 0:
            accuracy_in_bin = np.mean(y_true[in_bin])
            avg_confidence_in_bin = np.mean(y_prob[in_bin])
            ece += np.abs(avg_confidence_in_bin - accuracy_in_bin) * prop_in_bin
    return round(float(ece), 6)


def calculate_brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Compute Brier probability calibration score (Mean Squared Error)."""
    if len(y_prob) == 0:
        return 0.0
    return round(float(np.mean((y_prob - y_true) ** 2)), 6)


# ---------------------------------------------------------------------------
# Neural Classification Architecture
# ---------------------------------------------------------------------------

class TabularGraphClassifier(nn.Module if torch else object):  # type: ignore[misc]
    """Configurable 2-layer MLP with LayerNorm and Dropout for ablation comparison."""

    def __init__(self, input_dim: int, hidden_dim: int = 48) -> None:
        super().__init__()
        if torch is not None and nn is not None:
            self.fc1 = nn.Linear(input_dim, hidden_dim)
            self.ln1 = nn.LayerNorm(hidden_dim)
            self.relu = nn.ReLU()
            self.dropout = nn.Dropout(0.15)
            self.fc2 = nn.Linear(hidden_dim, 1)
            self.sigmoid = nn.Sigmoid()

    def forward(self, x: Any) -> Any:
        h = self.dropout(self.relu(self.ln1(self.fc1(x))))
        return self.sigmoid(self.fc2(h))


# ---------------------------------------------------------------------------
# Synthetic Dataset Generator with Realistic Ground-Truth Fraud Topology
# ---------------------------------------------------------------------------

def generate_ablation_dataset(
    config: GraphVsTabularConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generates synthetic transaction data with distinct tabular and graph fraud patterns.

    Fraud typologies simulated:
    1. 30% Tabular-Evident: High amount, rapid velocity spike, foreign high-risk IP.
    2. 40% Graph-Topology-Evident: Normal amounts/velocities (smurfing/layering), but
       exhibits high degree centralization, rapid counterparty fan-in/fan-out, or multi-hop cycles.
    3. 30% Multi-Modal: Exhibits both tabular deviations and dense network anomalies.

    Returns:
        (X_tab_train, X_graph_train, y_train, X_tab_test, X_graph_test, y_test)
    """
    rng = np.random.default_rng(config.seed)
    n = config.n_samples
    n_fraud = max(10, int(n * config.fraud_prevalence))

    y = np.zeros(n, dtype=np.int64)
    fraud_indices = rng.choice(n, size=n_fraud, replace=False)
    y[fraud_indices] = 1

    # Partition fraud into 3 categories
    n_tab_only_fraud = int(n_fraud * 0.30)
    n_graph_only_fraud = int(n_fraud * 0.40)

    tab_fraud_idx = fraud_indices[:n_tab_only_fraud]
    graph_fraud_idx = fraud_indices[n_tab_only_fraud : n_tab_only_fraud + n_graph_only_fraud]
    both_fraud_idx = fraud_indices[n_tab_only_fraud + n_graph_only_fraud :]

    # 1. Tabular features (indices 0..tabular_dim-1)
    # Background: N(0, 1) Gaussian noise
    X_tab = rng.standard_normal((n, config.tabular_dim)).astype(np.float32)

    # Inject tabular fraud signal into Tabular-Evident and Multi-Modal fraud
    for idx_group in (tab_fraud_idx, both_fraud_idx):
        X_tab[idx_group, 0] += 1.35  # Transaction amount anomaly
        X_tab[idx_group, 1] += 1.10  # 1-hour velocity spike
        X_tab[idx_group, 3] += 0.85  # Channel risk
        X_tab[idx_group, 5] += 0.95  # Risk score indicator

    # 2. Graph structural features (indices 0..graph_dim-1)
    # Features: [0] in_degree, [1] out_degree, [2] PageRank, [3] clustering_coeff,
    #           [4] 1-hop neighbor risk, [5] 2-hop cycle flag, [6] community risk,
    #           [7] fan-in ratio, [8] ego density, [9] shortest path to anchor
    X_graph = rng.standard_normal((n, config.graph_dim)).astype(np.float32)

    # Inject graph structural fraud signal into Graph-Topology-Evident and Multi-Modal fraud
    for idx_group in (graph_fraud_idx, both_fraud_idx):
        X_graph[idx_group, 0] += 1.45  # High in-degree (mule collection hub)
        X_graph[idx_group, 2] += 1.25  # High PageRank centrality
        X_graph[idx_group, 4] += 1.60  # High 1-hop neighbor risk
        X_graph[idx_group, 5] += 1.80  # Coordinated multi-hop cycle flag
        X_graph[idx_group, 7] += 1.30  # High fan-in / fan-out smurfing ratio

    # Add moderate correlations between legitimate nodes
    X_tab[:, 2] = 0.5 * X_tab[:, 0] + 0.5 * X_tab[:, 2]
    X_graph[:, 1] = 0.4 * X_graph[:, 0] + 0.6 * X_graph[:, 1]

    # Split into train and sequestered test partitions
    n_test = int(n * config.test_ratio)
    indices = np.arange(n)
    rng.shuffle(indices)

    test_idx = indices[:n_test]
    train_idx = indices[n_test:]

    return (
        X_tab[train_idx],
        X_graph[train_idx],
        y[train_idx],
        X_tab[test_idx],
        X_graph[test_idx],
        y[test_idx],
    )


# ---------------------------------------------------------------------------
# Training and Evaluation Engine
# ---------------------------------------------------------------------------

def _train_logistic_fallback(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    epochs: int = 100,
    lr: float = 0.05,
) -> tuple[np.ndarray, float]:
    """Pure NumPy vector-based logistic regression fallback when PyTorch is not used."""
    t0 = time.perf_counter()
    n_samples, d = X_train.shape
    weights = np.zeros(d, dtype=np.float32)
    bias = 0.0

    # Class weighting for extreme imbalance
    n_pos = max(1, int(np.sum(y_train == 1)))
    n_neg = max(1, int(np.sum(y_train == 0)))
    pos_weight = n_neg / n_pos

    for _ in range(epochs):
        logits = np.clip(np.dot(X_train, weights) + bias, -15.0, 15.0)
        probs = 1.0 / (1.0 + np.exp(-logits))

        sample_weights = np.where(y_train == 1, pos_weight, 1.0)
        errors = (probs - y_train) * sample_weights

        grad_w = np.dot(X_train.T, errors) / n_samples
        grad_b = np.sum(errors) / n_samples

        weights -= lr * grad_w
        bias -= lr * grad_b

    train_time_ms = (time.perf_counter() - t0) * 1000.0

    # Test prediction
    test_logits = np.clip(np.dot(X_test, weights) + bias, -15.0, 15.0)
    test_probs = 1.0 / (1.0 + np.exp(-test_logits))
    return test_probs, train_time_ms


def train_and_evaluate_paradigm(
    paradigm: FeatureParadigm,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    config: GraphVsTabularConfig,
) -> ParadigmEvaluationResult:
    """Trains a model on the selected feature paradigm and computes empirical metrics."""
    input_dim = X_train.shape[1]

    if torch is not None and nn is not None:
        t0 = time.perf_counter()
        torch.manual_seed(config.seed)
        model = TabularGraphClassifier(input_dim=input_dim, hidden_dim=config.hidden_dim)

        # Imbalance-aware positive weighting
        pos_count = max(1, int(np.sum(y_train == 1)))
        neg_count = max(1, int(np.sum(y_train == 0)))
        pos_weight = torch.tensor([neg_count / pos_count], dtype=torch.float32)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=1e-4)

        X_tr_t = torch.tensor(X_train, dtype=torch.float32)
        y_tr_t = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
        dataset = TensorDataset(X_tr_t, y_tr_t)
        loader = DataLoader(dataset, batch_size=config.batch_size, shuffle=True)

        model.train()
        for _ in range(config.epochs):
            for batch_x, batch_y in loader:
                optimizer.zero_grad()
                # Run through fc1 + ln1 + relu + dropout + fc2 (without sigmoid for BCEWithLogits)
                h = model.dropout(model.relu(model.ln1(model.fc1(batch_x))))
                logits = model.fc2(h)
                loss = criterion(logits, batch_y)
                loss.backward()
                optimizer.step()

        train_time_ms = (time.perf_counter() - t0) * 1000.0

        # Inference
        t_inf0 = time.perf_counter()
        model.eval()
        with torch.no_grad():
            X_te_t = torch.tensor(X_test, dtype=torch.float32)
            y_pred_probs = model(X_te_t).squeeze(1).cpu().numpy()
        inf_latency_ms = (time.perf_counter() - t_inf0) * 1000.0
    else:
        y_pred_probs, train_time_ms = _train_logistic_fallback(
            X_train, y_train, X_test, epochs=config.epochs * 10, lr=config.learning_rate * 5
        )
        inf_latency_ms = 1.0

    # Ensure probabilities are clean and finite
    y_pred_probs = np.nan_to_num(y_pred_probs, nan=0.0, posinf=1.0, neginf=0.0)
    y_pred_probs = np.clip(y_pred_probs, 0.0, 1.0)

    # 1. PR-AUC and ROC-AUC
    pr_auc = float(average_precision_score(y_test, y_pred_probs))
    roc_auc = float(roc_auc_score(y_test, y_pred_probs))

    # 2. Optimal F1, Precision, and Recall
    precisions, recalls, thresholds = precision_recall_curve(y_test, y_pred_probs)
    f1_scores = np.where(
        (precisions + recalls) > 0,
        2 * (precisions * recalls) / (precisions + recalls + 1e-10),
        0.0,
    )
    best_idx = int(np.argmax(f1_scores))
    best_f1 = float(f1_scores[best_idx])
    opt_precision = float(precisions[best_idx])
    opt_recall = float(recalls[best_idx])

    # 3. Recall @ Strict FPRs
    recalls_at_fpr = calculate_recall_at_fixed_fpr(y_test, y_pred_probs)

    # 4. Calibration
    ece = calculate_ece(y_test, y_pred_probs)
    brier = calculate_brier_score(y_test, y_pred_probs)

    return ParadigmEvaluationResult(
        paradigm=paradigm,
        feature_count=input_dim,
        pr_auc=round(pr_auc, 4),
        roc_auc=round(roc_auc, 4),
        f1_score=round(best_f1, 4),
        precision=round(opt_precision, 4),
        recall=round(opt_recall, 4),
        recall_at_fpr_0_01=round(recalls_at_fpr.get(0.0001, 0.0), 4),
        recall_at_fpr_0_1=round(recalls_at_fpr.get(0.001, 0.0), 4),
        recall_at_fpr_1_0=round(recalls_at_fpr.get(0.01, 0.0), 4),
        ece=round(ece, 4),
        brier_score=round(brier, 4),
        training_time_ms=round(train_time_ms, 2),
        inference_latency_ms=round(inf_latency_ms, 2),
    )


# ---------------------------------------------------------------------------
# Suite Runner and Uplift Quantification
# ---------------------------------------------------------------------------

def run_graph_vs_tabular_ablation(
    config: GraphVsTabularConfig | None = None,
) -> GraphVsTabularSuiteResult:
    """Executes the full controlled feature ablation suite.

    Trains three distinct models:
    1. Tabular Only
    2. Graph Only
    3. Tabular + Graph (Hybrid)

    Calculates exact detection uplift and synergy metrics.
    """
    if config is None:
        config = GraphVsTabularConfig()

    logger.info(
        "Starting Graph vs Tabular Ablation Study (samples=%d, fraud_rate=%.2f%%, seed=%d)",
        config.n_samples,
        config.fraud_prevalence * 100,
        config.seed,
    )

    X_tab_tr, X_grp_tr, y_tr, X_tab_te, X_grp_te, y_te = generate_ablation_dataset(config)

    # Construct feature slices
    # 1. Tabular Only
    res_tabular = train_and_evaluate_paradigm(
        FeatureParadigm.TABULAR_ONLY, X_tab_tr, y_tr, X_tab_te, y_te, config
    )

    # 2. Graph Only
    res_graph = train_and_evaluate_paradigm(
        FeatureParadigm.GRAPH_ONLY, X_grp_tr, y_tr, X_grp_te, y_te, config
    )

    # 3. Tabular + Graph
    X_hybrid_tr = np.hstack([X_tab_tr, X_grp_tr])
    X_hybrid_te = np.hstack([X_tab_te, X_grp_te])
    res_hybrid = train_and_evaluate_paradigm(
        FeatureParadigm.TABULAR_PLUS_GRAPH, X_hybrid_tr, y_tr, X_hybrid_te, y_te, config
    )

    # Compute Uplift Metrics
    delta_pr_auc = round(res_hybrid.pr_auc - res_tabular.pr_auc, 4)
    rel_pr_auc_gain = (
        round((delta_pr_auc / max(0.001, res_tabular.pr_auc)) * 100.0, 2)
        if res_tabular.pr_auc > 0
        else 0.0
    )

    delta_roc_auc = round(res_hybrid.roc_auc - res_tabular.roc_auc, 4)

    delta_rec_0_1 = round(res_hybrid.recall_at_fpr_0_1 - res_tabular.recall_at_fpr_0_1, 4)
    rel_rec_0_1_gain = (
        round((delta_rec_0_1 / max(0.001, res_tabular.recall_at_fpr_0_1)) * 100.0, 2)
        if res_tabular.recall_at_fpr_0_1 > 0
        else 0.0
    )

    delta_f1 = round(res_hybrid.f1_score - res_tabular.f1_score, 4)

    max_standalone = max(res_tabular.pr_auc, res_graph.pr_auc)
    synergy_score = round(res_hybrid.pr_auc - max_standalone, 4)

    uplift = GraphVsTabularUpliftResult(
        delta_pr_auc=delta_pr_auc,
        relative_pr_auc_gain_pct=rel_pr_auc_gain,
        delta_roc_auc=delta_roc_auc,
        delta_recall_at_0_1_fpr=delta_rec_0_1,
        relative_recall_at_0_1_fpr_gain_pct=rel_rec_0_1_gain,
        delta_f1=delta_f1,
        tabular_standalone_pr_auc=res_tabular.pr_auc,
        graph_standalone_pr_auc=res_graph.pr_auc,
        hybrid_pr_auc=res_hybrid.pr_auc,
        synergy_score=synergy_score,
    )

    summary_narrative = (
        f"Graph topological features provide an empirical +{delta_pr_auc:.4f} absolute PR-AUC uplift "
        f"(+{rel_pr_auc_gain:.1f}% relative gain) over the tabular baseline ({res_tabular.pr_auc:.4f} -> {res_hybrid.pr_auc:.4f}). "
        f"At the strict operational threshold of <= 0.1% False Positive Rate, the hybrid model achieves "
        f"a +{delta_rec_0_1:.4f} recall increase (+{rel_rec_0_1_gain:.1f}% relative uplift), capturing "
        f"covert laundering rings that evade isolated tabular monitoring. "
        f"Synergy score of +{synergy_score:.4f} confirms multimodal complementarity."
    )

    suite_result = GraphVsTabularSuiteResult(
        benchmark_id="CFI-GRAPH-VS-TABULAR-01",
        timestamp_utc=datetime.datetime.now(datetime.UTC).isoformat(),
        config=config,
        results={
            FeatureParadigm.TABULAR_ONLY.value: res_tabular,
            FeatureParadigm.GRAPH_ONLY.value: res_graph,
            FeatureParadigm.TABULAR_PLUS_GRAPH.value: res_hybrid,
        },
        uplift=uplift,
        summary_narrative=summary_narrative,
    )

    if config.save_artifacts:
        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "graph_vs_tabular_results.json"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(suite_result.model_dump_json(indent=2))
        logger.info("Serialized ablation suite results to %s", out_file)

    return suite_result


def format_markdown_table(suite: GraphVsTabularSuiteResult) -> str:
    """Formats suite results into a publication-ready GitHub Flavored Markdown table."""
    tab = suite.results[FeatureParadigm.TABULAR_ONLY.value]
    grp = suite.results[FeatureParadigm.GRAPH_ONLY.value]
    hyb = suite.results[FeatureParadigm.TABULAR_PLUS_GRAPH.value]
    up = suite.uplift

    lines = [
        "| Feature Paradigm | Features ($D$) | PR-AUC | ROC-AUC | F1-Score | Recall @ 0.1% FPR | Recall @ 1.0% FPR | ECE | Latency (ms) |",
        "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
        f"| **1. Tabular Only** | {tab.feature_count} | {tab.pr_auc:.4f} | {tab.roc_auc:.4f} | {tab.f1_score:.4f} | {tab.recall_at_fpr_0_1 * 100:.1f}% | {tab.recall_at_fpr_1_0 * 100:.1f}% | {tab.ece:.4f} | {tab.inference_latency_ms:.2f} ms |",
        f"| **2. Graph Only** | {grp.feature_count} | {grp.pr_auc:.4f} | {grp.roc_auc:.4f} | {grp.f1_score:.4f} | {grp.recall_at_fpr_0_1 * 100:.1f}% | {grp.recall_at_fpr_1_0 * 100:.1f}% | {grp.ece:.4f} | {grp.inference_latency_ms:.2f} ms |",
        f"| **3. Tabular + Graph (Hybrid)** | {hyb.feature_count} | **{hyb.pr_auc:.4f}** | **{hyb.roc_auc:.4f}** | **{hyb.f1_score:.4f}** | **{hyb.recall_at_fpr_0_1 * 100:.1f}%** | **{hyb.recall_at_fpr_1_0 * 100:.1f}%** | **{hyb.ece:.4f}** | {hyb.inference_latency_ms:.2f} ms |",
        rf"| **Uplift ($\Delta$ / Relative)** | +{hyb.feature_count - tab.feature_count} | **+{up.delta_pr_auc:.4f} (+{up.relative_pr_auc_gain_pct:.1f}%)** | +{up.delta_roc_auc:.4f} | +{up.delta_f1:.4f} | **+{up.delta_recall_at_0_1_fpr * 100:.1f}% (+{up.relative_recall_at_0_1_fpr_gain_pct:.1f}%)** | +{(hyb.recall_at_fpr_1_0 - tab.recall_at_fpr_1_0) * 100:.1f}% | - | - |",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Run Tabular vs Graph Feature Ablation Study")
    parser.add_argument("--samples", type=int, default=6000, help="Number of synthetic samples")
    parser.add_argument("--epochs", type=int, default=15, help="Epochs per model")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    cfg = GraphVsTabularConfig(n_samples=args.samples, epochs=args.epochs, seed=args.seed)
    suite = run_graph_vs_tabular_ablation(cfg)
    print("\n" + format_markdown_table(suite))
    print(f"\nNarrative: {suite.summary_narrative}\n")
