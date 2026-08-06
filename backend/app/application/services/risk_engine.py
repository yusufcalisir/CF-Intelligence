"""Risk scoring engine.

Modular risk evaluation pipeline that combines multiple independent
signals into a weighted composite score. Each signal evaluates one
dimension of risk (ML prediction, velocity, geography, etc.) and
produces a normalized score with an explanation.

The architecture follows the signal-combiner pattern common in
production fraud platforms: each signal is a pure function, signals
are independent, and the combination logic is configurable.
"""

from __future__ import annotations

import logging

from app.domain.value_objects_phase2 import RiskScore, RiskSignal, RiskWeightConfig

logger = logging.getLogger(__name__)

# Country risk scores — simplified lookup table (FATF-aligned)
COUNTRY_RISK: dict[str, float] = {
    # FATF black-listed / fully sanctioned jurisdictions
    "KP": 1.00,  # North Korea (DPRK)
    "IR": 0.95,  # Iran
    "MM": 0.90,  # Myanmar (post-coup)
    "SY": 0.90,  # Syria
    # FATF grey-listed / high-risk jurisdictions
    "NG": 0.85,
    "RU": 0.80,
    "PH": 0.75,
    "BR": 0.70,
    "TR": 0.55,
    "MX": 0.50,
    "CN": 0.45,
    "IN": 0.40,
    "ZA": 0.45,
    "AE": 0.30,
    # Lower-risk jurisdictions
    "KR": 0.15,
    "JP": 0.10,
    "SG": 0.10,
    "AU": 0.08,
    "NL": 0.05,
    "DE": 0.05,
    "FR": 0.05,
    "CA": 0.04,
    "UK": 0.03,
    "US": 0.02,
}

# Merchant category risk
MERCHANT_RISK: dict[str, float] = {
    "gambling": 0.90,
    "crypto": 0.85,
    "wire_transfer": 0.75,
    "jewelry": 0.60,
    "online_marketplace": 0.45,
    "electronics": 0.35,
    "travel": 0.25,
    "atm_withdrawal": 0.30,
    "entertainment": 0.15,
    "dining": 0.05,
    "grocery": 0.03,
    "fuel": 0.03,
    "clothing": 0.05,
    "healthcare": 0.02,
    "education": 0.02,
    "home": 0.05,
    "automotive": 0.08,
    "subscription": 0.05,
    "insurance": 0.03,
    "charity": 0.10,
}


class RiskScoringEngine:
    """Modular risk scoring pipeline.

    Combines nine independent risk signals with configurable weights.
    Each signal is a pure function that produces a normalized score
    (0.0 = no risk, 1.0 = maximum risk) and an explanation string.

    Example:
        engine = RiskScoringEngine()
        score = engine.score_transaction(txn_dict)
        print(score.score, score.risk_level, score.top_signals)
    """

    def __init__(self, weights: RiskWeightConfig | None = None) -> None:
        self.weights = weights or RiskWeightConfig()
        # In-memory lookups for historical data (populated by scenario engine)
        self._alert_history: dict[str, int] = {}  # entity_hash → alert count
        self._chargeback_history: dict[str, float] = {}  # entity_hash → chargeback rate
        self._behavior_baselines: dict[str, dict] = {}  # entity_hash → baseline stats
        from app.application.services.feature_store_service import FeatureStoreService

        self.feature_store = FeatureStoreService()

    def score_transaction(
        self,
        transaction: dict,
        ml_prediction: float = 0.0,
        entity_hash: str = "",
    ) -> RiskScore:
        """Score a single transaction by evaluating all risk signals.

        Args:
            transaction: Dict of transaction features.
            ml_prediction: ML model output (0-1).
            entity_hash: Privacy-preserving hash of the transacting entity.

        Returns:
            Composite RiskScore with breakdown.
        """
        from app.config import get_settings

        settings = get_settings()

        txn_eval = transaction.copy()
        if settings.feature_store_enabled and entity_hash:
            entity_rows = [
                {
                    "customer_id": entity_hash,
                    "merchant_id": transaction.get(
                        "merchant_id", f"merch_{transaction.get('merchant_category', 'grocery')}"
                    ),
                }
            ]
            features_to_fetch = [
                "rolling_velocity_1h",
                "avg_amount_24h",
                "customer_history_score",
                "account_age_days",
                "chargeback_count",
                "merchant_risk_score",
                "merchant_category",
            ]
            try:
                online_feats = self.feature_store.get_online_features(
                    entity_rows, features_to_fetch
                )
                if online_feats:
                    feats = online_feats[0]
                    # Map online features back to txn fields for scoring
                    txn_eval["velocity"] = feats.get(
                        "rolling_velocity_1h", txn_eval.get("velocity", 1.0)
                    )
                    txn_eval["customer_history_score"] = feats.get(
                        "customer_history_score", txn_eval.get("customer_history_score", 0.95)
                    )
                    txn_eval["account_age_days"] = feats.get(
                        "account_age_days", txn_eval.get("account_age_days", 365)
                    )
                    txn_eval["chargeback_count"] = feats.get(
                        "chargeback_count", txn_eval.get("chargeback_count", 0)
                    )
                    txn_eval["merchant_risk_score"] = feats.get(
                        "merchant_risk_score", txn_eval.get("merchant_risk_score", 0.05)
                    )
                    txn_eval["merchant_category"] = feats.get(
                        "merchant_category", txn_eval.get("merchant_category", "grocery")
                    )
                    logger.info(
                        "Online Feature Store retrieved successfully for entity %s", entity_hash
                    )
            except Exception as e:
                logger.warning("Feature Store online lookup failed: %s", e)

        signals = [
            self._eval_ml_prediction(ml_prediction),
            self._eval_velocity(txn_eval),
            self._eval_merchant_reputation(txn_eval),
            self._eval_country_risk(txn_eval),
            self._eval_device_anomaly(txn_eval),
            self._eval_customer_history(txn_eval),
            self._eval_previous_alerts(entity_hash),
            self._eval_chargeback_history(entity_hash),
            self._eval_behavior_anomaly(txn_eval, entity_hash),
        ]

        composite = self._combine_signals(signals)

        return RiskScore(
            score=round(composite * 1000, 1),
            signals=signals,
        )

    def update_weights(self, weights: RiskWeightConfig) -> None:
        self.weights = weights

    def register_alert(self, entity_hash: str) -> None:
        """Record an alert for historical signal tracking."""
        self._alert_history[entity_hash] = self._alert_history.get(entity_hash, 0) + 1

    def register_chargeback(self, entity_hash: str, rate: float) -> None:
        self._chargeback_history[entity_hash] = rate

    def register_baseline(self, entity_hash: str, baseline: dict) -> None:
        self._behavior_baselines[entity_hash] = baseline

    # ── Signal evaluators ─────────────────────

    def _eval_ml_prediction(self, prediction: float) -> RiskSignal:
        return RiskSignal(
            signal_name="ml_prediction",
            weight=self.weights.ml_prediction,
            raw_value=prediction,
            normalized_score=max(0.0, min(1.0, prediction)),
            explanation=f"ML model confidence: {prediction:.1%}",
        )

    def _eval_velocity(self, txn: dict) -> RiskSignal:
        velocity = txn.get("velocity", 0.0)
        # Normalize: 0-3 is normal, >10 is extreme
        normalized = min(1.0, max(0.0, (velocity - 2) / 8))
        return RiskSignal(
            signal_name="velocity_rules",
            weight=self.weights.velocity_rules,
            raw_value=velocity,
            normalized_score=normalized,
            explanation=f"Transaction velocity: {velocity:.1f} txns/hr"
            + (" (elevated)" if normalized > 0.5 else ""),
        )

    def _eval_merchant_reputation(self, txn: dict) -> RiskSignal:
        category = txn.get("merchant_category", "")
        merchant_score = txn.get("merchant_risk_score", 0.10)
        category_risk = MERCHANT_RISK.get(category, 0.1)
        # Blend merchant's own risk score with category risk, clamped to [0.0, 1.0]
        normalized = max(0.0, min(1.0, 0.6 * merchant_score + 0.4 * category_risk))
        return RiskSignal(
            signal_name="merchant_reputation",
            weight=self.weights.merchant_reputation,
            raw_value=merchant_score,
            normalized_score=normalized,
            explanation=f"Merchant category: {category} (risk: {category_risk:.0%}), "
            f"merchant score: {merchant_score:.2f}",
        )

    def _eval_country_risk(self, txn: dict) -> RiskSignal:
        country = str(txn.get("country_code", "US")).upper()
        risk = COUNTRY_RISK.get(country, 0.15)
        return RiskSignal(
            signal_name="country_risk",
            weight=self.weights.country_risk,
            raw_value=risk,
            normalized_score=risk,
            explanation=f"Country: {country} (risk: {risk:.0%})",
        )

    def _eval_device_anomaly(self, txn: dict) -> RiskSignal:
        device = txn.get("device_type", "web_browser")
        # Simple heuristic: ATM and phone banking are higher risk channels
        device_scores = {
            "mobile_app": 0.10,
            "web_browser": 0.15,
            "pos_terminal": 0.05,
            "atm": 0.35,
            "phone_banking": 0.40,
        }
        score = device_scores.get(device, 0.20)
        return RiskSignal(
            signal_name="device_anomaly",
            weight=self.weights.device_anomaly,
            raw_value=score,
            normalized_score=score,
            explanation=f"Device: {device}",
        )

    def _eval_customer_history(self, txn: dict) -> RiskSignal:
        history_score = txn.get("customer_history_score", 0.5)
        # Invert: low history score = high risk, clamped to [0.0, 1.0]
        risk = max(0.0, 1.0 - min(1.0, history_score))
        account_age = txn.get("account_age_days", 365)
        if account_age < 30:
            risk = min(1.0, risk + 0.3)
        return RiskSignal(
            signal_name="customer_history",
            weight=self.weights.customer_history,
            raw_value=history_score,
            normalized_score=risk,
            explanation=f"Customer history: {history_score:.2f}, account age: {account_age} days",
        )

    def _eval_previous_alerts(self, entity_hash: str) -> RiskSignal:
        count = self._alert_history.get(entity_hash, 0)
        # Normalize: 0 alerts = 0 risk, 5+ alerts = max risk
        normalized = min(1.0, count / 5) if count > 0 else 0.0
        return RiskSignal(
            signal_name="previous_alerts",
            weight=self.weights.previous_alerts,
            raw_value=float(count),
            normalized_score=normalized,
            explanation=f"Previous alerts: {count}",
        )

    def _eval_chargeback_history(self, entity_hash: str) -> RiskSignal:
        rate = self._chargeback_history.get(entity_hash, 0.0)
        normalized = min(1.0, rate * 10)  # 10% chargeback rate → max risk
        return RiskSignal(
            signal_name="chargeback_history",
            weight=self.weights.chargeback_history,
            raw_value=rate,
            normalized_score=normalized,
            explanation=f"Chargeback rate: {rate:.1%}",
        )

    def _eval_behavior_anomaly(self, txn: dict, entity_hash: str) -> RiskSignal:
        baseline = self._behavior_baselines.get(entity_hash)
        if not baseline:
            return RiskSignal(
                signal_name="behavior_anomaly",
                weight=self.weights.behavior_anomaly,
                raw_value=0.0,
                normalized_score=0.1,  # Slight risk for unknown baseline
                explanation="No baseline established",
            )

        # Compare transaction amount to baseline
        amount = txn.get("transaction_amount", 0)
        mean_amt = baseline.get("mean_amount", 100)
        std_amt = baseline.get("std_amount", 50)

        if std_amt > 0:
            z_score = abs(amount - mean_amt) / std_amt
            normalized = min(1.0, max(0.0, (z_score - 1) / 3))
        else:
            # Zero-variance baseline: any non-zero deviation is anomalous
            deviation = abs(amount - mean_amt)
            z_score = 10.0 if deviation > 1e-6 else 0.0
            normalized = 1.0 if deviation > 1e-6 else 0.0

        return RiskSignal(
            signal_name="behavior_anomaly",
            weight=self.weights.behavior_anomaly,
            raw_value=z_score,
            normalized_score=normalized,
            explanation=f"Amount deviation: {z_score:.1f}σ from baseline",
        )

    # ── Combiner ──────────────────────────────

    def _combine_signals(self, signals: list[RiskSignal]) -> float:
        """Weighted combination of all risk signals.

        Returns a composite score in [0.0, 1.0].
        """
        total_weight = sum(s.weight for s in signals)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(s.weighted_score for s in signals)
        return min(1.0, weighted_sum / total_weight)
