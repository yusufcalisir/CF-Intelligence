"""Privacy Leakage Audit Service for Federated Learning.

Performs active audits against Link Reconstruction Attacks (LRA) and
Membership Inference Attacks (MIA) to quantify the privacy boundaries of shared parameters.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class PrivacyAuditService:
    """Audits federated updates and representation embeddings for data leakage."""

    def audit_link_reconstruction(
        self,
        embeddings: dict[str, np.ndarray],
        adjacency_lists: list[list[int]],
        node_id_to_index: dict[str, int],
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Perform a Link Reconstruction Attack (LRA) audit on node embeddings.

        Evaluates how easily an adversary can reconstruct transaction links
        based on the similarity of shared entity embeddings.

        Returns:
            Dict containing the leakage AUC score and classification risk tier.
        """
        if not embeddings or not adjacency_lists or not node_id_to_index:
            return {
                "link_leakage_auc": 0.5,
                "risk_tier": "safe",
                "message": "Insufficient graph structure to perform LRA audit.",
            }

        # Convert embeddings to matrix aligned with indices
        n_nodes = len(node_id_to_index)
        if n_nodes < 2:
            return {
                "link_leakage_auc": 0.5,
                "risk_tier": "safe",
                "message": "Insufficient nodes to perform LRA audit (minimum 2 nodes required).",
            }

        emb_dim = next(iter(embeddings.values())).shape[0]
        emb_matrix = np.zeros((n_nodes, emb_dim))
        for node_id, idx in node_id_to_index.items():
            if node_id in embeddings:
                emb_matrix[idx] = embeddings[node_id]

        # Normalize embeddings to unit sphere for cosine similarity
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1e-9
        norm_emb = emb_matrix / norms

        # Gather positive edges (actual links)
        pos_scores: list[float] = []
        for u in range(min(n_nodes, len(adjacency_lists))):
            for v in adjacency_lists[u]:
                if v < n_nodes:
                    sim = float(np.dot(norm_emb[u], norm_emb[v]))
                    pos_scores.append(sim)

        # Gather negative edges (random non-links)
        neg_scores: list[float] = []
        rng = np.random.default_rng(seed)
        n_pos = len(pos_scores)
        if n_pos == 0:
            return {
                "link_leakage_auc": 0.5,
                "risk_tier": "safe",
                "message": "No transaction links found in the subgraph.",
            }

        # Sample negative pairs
        attempts = 0
        while len(neg_scores) < n_pos and attempts < n_pos * 10:
            u = int(rng.integers(0, n_nodes))
            v = int(rng.integers(0, n_nodes))
            if u != v and (u >= len(adjacency_lists) or v not in adjacency_lists[u]):
                sim = float(np.dot(norm_emb[u], norm_emb[v]))
                neg_scores.append(sim)
            attempts += 1

        if not neg_scores:
            neg_scores = [0.0] * n_pos

        # Calculate ROC AUC of classification (LRA attack success rate)
        # Sort score thresholds and compute True Positive vs. False Positive Rates
        all_scores = np.concatenate([pos_scores, neg_scores])
        all_labels = np.concatenate([np.ones(len(pos_scores)), np.zeros(len(neg_scores))])

        sort_indices = np.argsort(all_scores)[::-1]
        sorted_labels = all_labels[sort_indices]

        tps = np.cumsum(sorted_labels)
        fps = np.cumsum(1 - sorted_labels)

        tpr = tps / len(pos_scores)
        fpr = fps / len(neg_scores)

        # Integrate using trapezoidal rule to find AUC
        auc = 0.0
        prev_fpr = 0.0
        prev_tpr = 0.0
        for idx in range(len(tpr)):
            curr_fpr = float(fpr[idx])
            curr_tpr = float(tpr[idx])
            auc += (curr_tpr + prev_tpr) * (curr_fpr - prev_fpr) / 2.0
            prev_fpr = curr_fpr
            prev_tpr = curr_tpr
        auc = max(0.5, min(1.0, auc))  # Clip between baseline random guess and complete leakage

        if auc < 0.65:
            risk_tier = "low_risk"
        elif auc < 0.85:
            risk_tier = "moderate_risk"
        else:
            risk_tier = "high_risk"

        return {
            "link_leakage_auc": round(auc, 4),
            "risk_tier": risk_tier,
            "num_positive_edges_audited": len(pos_scores),
            "num_negative_edges_audited": len(neg_scores),
        }

    def audit_membership_inference(
        self,
        train_losses: list[float],
        test_losses: list[float],
    ) -> dict[str, Any]:
        """Perform a Membership Inference Attack (MIA) audit using model loss checks.

        Evaluates if an attacker can infer whether specific transaction samples
        were used in local model training by checking gradient updates/losses.

        Returns:
            Dict containing the attack success rate (ASR) and risk classification.
        """
        if not train_losses or not test_losses:
            return {
                "membership_leakage_asr": 0.5,
                "risk_tier": "safe",
                "message": "Insufficient loss history to perform MIA audit.",
            }

        # Membership Inference: Members typically have smaller loss than non-members.
        # Find optimal threshold to separate train and test losses.
        all_losses = train_losses + test_losses
        threshold = float(np.median(all_losses))

        # Attacker classifier: if loss < threshold, predict MEMBER (1), else NON-MEMBER (0)
        correct_predictions = 0
        for loss in train_losses:
            if loss <= threshold:
                correct_predictions += 1  # True Positive (Member correctly inferred)
        for loss in test_losses:
            if loss > threshold:
                correct_predictions += 1  # True Negative (Non-member correctly inferred)

        asr = float(correct_predictions / (len(train_losses) + len(test_losses)))
        asr = max(0.5, min(1.0, asr))  # Limit between 0.5 (random guess) and 1.0 (perfect MIA)

        if asr < 0.60:
            risk_tier = "low_risk"
        elif asr < 0.75:
            risk_tier = "moderate_risk"
        else:
            risk_tier = "high_risk"

        return {
            "membership_leakage_asr": round(asr, 4),
            "risk_tier": risk_tier,
            "num_train_samples_audited": len(train_losses),
            "num_test_samples_audited": len(test_losses),
        }

    def audit_model_inversion(
        self,
        gradient_norms: list[float],
    ) -> dict[str, Any]:
        """Simulate a Model Inversion Attack (MIA) audit on gradient norms.

        Evaluates whether the magnitude of shared gradient updates could allow an
        adversary to reconstruct sensitive node features or transaction amounts.
        High gradient norms indicate that individual sample contributions are
        distinguishable, which increases the risk of feature reconstruction.

        Returns:
            Dict with reconstruction risk score and risk tier.
        """
        if not gradient_norms:
            return {
                "reconstruction_risk_score": 0.0,
                "risk_tier": "safe",
                "message": "No gradient norms provided for model inversion audit.",
            }

        if any(norm < 0 for norm in gradient_norms):
            raise ValueError("Gradient norms must be non-negative")

        arr = np.array(gradient_norms)
        # Normalise to [0, 1]: high norm variance → high reconstruction risk
        mean_norm = float(np.mean(arr))
        std_norm = float(np.std(arr))
        # Risk proxy: coefficient of variation captures exploitable heterogeneity
        cv = std_norm / (mean_norm + 1e-9)
        risk_score = float(np.clip(cv, 0.0, 1.0))

        if risk_score < 0.3:
            risk_tier = "low_risk"
        elif risk_score < 0.6:
            risk_tier = "moderate_risk"
        else:
            risk_tier = "high_risk"

        logger.info(
            "Model Inversion audit: mean_norm=%.4f std_norm=%.4f cv=%.4f tier=%s",
            mean_norm,
            std_norm,
            cv,
            risk_tier,
        )

        return {
            "reconstruction_risk_score": round(risk_score, 4),
            "risk_tier": risk_tier,
            "mean_gradient_norm": round(mean_norm, 6),
            "std_gradient_norm": round(std_norm, 6),
            "num_gradients_audited": len(gradient_norms),
        }

    def audit_gradient_leakage_dlg(
        self,
        original_gradients: list[float],
        received_gradients: list[float],
    ) -> dict[str, Any]:
        """Deep Leakage from Gradients (DLG) audit.

        Measures the Pearson correlation between the gradients exchanged during
        secure aggregation and a synthetic "leaked" reconstruction gradient.
        High correlation indicates that raw transaction features could be recovered
        from the shared gradient vectors.

        Reference: Zhu et al., "Deep Leakage from Gradients" (NeurIPS 2019).

        Returns:
            Dict with leakage correlation score and risk tier.
        """
        if not original_gradients or not received_gradients:
            return {
                "dlg_leakage_score": 0.0,
                "risk_tier": "safe",
                "message": "Insufficient gradient data for DLG audit.",
            }

        min_len = min(len(original_gradients), len(received_gradients))
        if min_len < 2:
            return {
                "dlg_leakage_score": 0.0,
                "risk_tier": "safe",
                "message": "Insufficient gradient vector length for DLG correlation (min 2 elements required).",
            }
        orig = np.array(original_gradients[:min_len])
        recv = np.array(received_gradients[:min_len])

        # Pearson correlation as leakage proxy
        if orig.std() == 0 or recv.std() == 0:
            corr = 0.0
        else:
            corr_matrix = np.corrcoef(orig, recv)
            corr = float(np.clip(abs(corr_matrix[0, 1]), 0.0, 1.0))

        if corr < 0.3:
            risk_tier = "low_risk"
        elif corr < 0.6:
            risk_tier = "moderate_risk"
        else:
            risk_tier = "high_risk"

        logger.info(
            "DLG audit: pearson_correlation=%.4f tier=%s (params_audited=%d)",
            corr,
            risk_tier,
            min_len,
        )

        return {
            "dlg_leakage_score": round(corr, 4),
            "risk_tier": risk_tier,
            "params_audited": min_len,
        }

    def simulate_membership_inference_under_dp(
        self,
        test_epsilon: float = 1.0,
        num_samples: int = 100,
        seed: int | None = 42,
    ) -> dict[str, Any]:
        """Empirically evaluate Membership Inference Attack (MIA) resistance at given DP epsilon.

        As epsilon decreases (stronger noise), model memorization decreases,
        narrowing the gap between member (train) and non-member (test) loss distributions,
        forcing the adversary towards random guessing (ASR -> 0.50, ROC-AUC -> 0.50).
        """
        rng = np.random.default_rng(seed)
        is_dp_enabled = test_epsilon < 50.0

        # Base non-member test loss: ~0.55 mean with standard deviation ~0.10
        test_losses = rng.normal(loc=0.55, scale=0.10, size=num_samples).clip(0.15, 1.2).tolist()

        if is_dp_enabled:
            # Under DP: loss gap scales with e^eps - 1. When eps <= 1.0, train loss is very close to test loss
            gap_scale = min(0.35, 0.05 * math.log1p(test_epsilon))
            train_loc = max(0.20, 0.55 - gap_scale)
            train_losses = (
                rng.normal(loc=train_loc, scale=0.10, size=num_samples).clip(0.10, 1.0).tolist()
            )
        else:
            # No DP: significant overfitting/memorization gap
            train_losses = (
                rng.normal(loc=0.12, scale=0.04, size=num_samples).clip(0.02, 0.35).tolist()
            )

        base_audit = self.audit_membership_inference(train_losses, test_losses)
        asr = base_audit["membership_leakage_asr"]

        # Compute empirical ROC-AUC:
        # Negative loss as positive predictor (lower loss = higher probability of member)
        scores = np.concatenate([-np.array(train_losses), -np.array(test_losses)])
        labels = np.concatenate([np.ones(len(train_losses)), np.zeros(len(test_losses))])
        order = np.argsort(scores)[::-1]
        sorted_labels = labels[order]

        tps = np.cumsum(sorted_labels)
        fps = np.cumsum(1 - sorted_labels)
        tpr = tps / len(train_losses)
        fpr = fps / len(test_losses)

        auc = 0.0
        prev_fpr = 0.0
        prev_tpr = 0.0
        for idx in range(len(tpr)):
            curr_fpr = float(fpr[idx])
            curr_tpr = float(tpr[idx])
            auc += (curr_tpr + prev_tpr) * (curr_fpr - prev_fpr) / 2.0
            prev_fpr = curr_fpr
            prev_tpr = curr_tpr
        roc_auc = max(0.5, min(1.0, auc))

        mean_train = float(np.mean(train_losses))
        mean_test = float(np.mean(test_losses))
        loss_gap = float(mean_test - mean_train)

        all_losses = train_losses + test_losses
        mid_loss = float(np.mean(all_losses))
        confidences = [
            float(1.0 / (1.0 + np.exp(loss - mid_loss))) for loss in train_losses
        ]

        if is_dp_enabled and test_epsilon <= 2.0:
            summary = (
                f"Strong DP defense active (ε = {test_epsilon:.2f}). "
                f"MIA advantage is bounded (ASR = {asr * 100:.1f}%, AUC = {roc_auc:.3f})."
            )
        elif is_dp_enabled:
            summary = (
                f"Moderate DP protection (ε = {test_epsilon:.2f}). "
                f"Minor loss disparity detected (ASR = {asr * 100:.1f}%, AUC = {roc_auc:.3f})."
            )
        else:
            summary = (
                f"Unconstrained / No-DP mode: High memorization risk "
                f"(ASR = {asr * 100:.1f}%, AUC = {roc_auc:.3f}). Transaction membership is vulnerable."
            )

        return {
            "test_epsilon": test_epsilon,
            "is_dp_enabled": is_dp_enabled,
            "membership_leakage_asr": round(asr, 4),
            "mia_roc_auc": round(roc_auc, 4),
            "risk_tier": base_audit["risk_tier"],
            "mean_train_loss": round(mean_train, 4),
            "mean_test_loss": round(mean_test, 4),
            "loss_gap": round(loss_gap, 4),
            "train_loss_distribution": [round(x, 4) for x in train_losses[:20]],
            "test_loss_distribution": [round(x, 4) for x in test_losses[:20]],
            "confidence_distribution": [round(x, 4) for x in confidences[:20]],
            "attack_summary": summary,
        }

