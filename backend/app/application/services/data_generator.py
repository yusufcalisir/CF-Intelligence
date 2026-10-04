"""Synthetic transaction data generator.

Generates realistic Non-IID financial transaction data for three banks.
Each bank has intentionally different fraud patterns, feature distributions,
and fraud ratios to simulate real-world data heterogeneity.

This is the core of why federated learning matters: each bank sees
different slices of fraud, and collaboration fills blind spots.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd
import torch

from app.domain.entities import Bank
from app.domain.enums import BankTier
from app.domain.value_objects import BankDataProfile

logger = logging.getLogger(__name__)

# Feature names used across all banks
FEATURE_NAMES: list[str] = [
    "transaction_amount",
    "merchant_category",
    "country_code",
    "device_type",
    "velocity",
    "hour_of_day",
    "merchant_risk_score",
    "customer_history_score",
    "chargeback_count",
    "account_age_days",
]

MERCHANT_CATEGORIES: list[str] = [
    "grocery",
    "electronics",
    "travel",
    "dining",
    "fuel",
    "entertainment",
    "healthcare",
    "clothing",
    "home",
    "automotive",
    "jewelry",
    "gambling",
    "crypto",
    "wire_transfer",
    "online_marketplace",
    "subscription",
    "insurance",
    "education",
    "charity",
    "atm_withdrawal",
]

COUNTRIES: list[str] = [
    "US",
    "UK",
    "DE",
    "FR",
    "NL",
    "CA",
    "AU",
    "JP",
    "SG",
    "BR",
    "NG",
    "RU",
    "CN",
    "IN",
    "AE",
    "KR",
    "MX",
    "ZA",
    "TR",
    "PH",
]

DEVICES: list[str] = ["mobile_app", "web_browser", "pos_terminal", "atm", "phone_banking"]

# Countries and merchants with elevated risk (for fraud pattern generation)
HIGH_RISK_COUNTRIES = {"NG", "RU", "PH", "BR"}
HIGH_RISK_MERCHANTS = {"gambling", "crypto", "wire_transfer", "jewelry"}

# Authoritative dataset reference bounds for feature scaling
REFERENCE_BOUNDS: dict[str, tuple[float, float]] = {
    "transaction_amount": (0.0, 5000.0),
    "merchant_category": (0.0, 19.0),
    "country_code": (0.0, 19.0),
    "device_type": (0.0, 4.0),
    "velocity": (0.0, 30.0),
    "hour_of_day": (0.0, 23.0),
    "merchant_risk_score": (0.0, 1.0),
    "customer_history_score": (0.0, 1.0),
    "chargeback_count": (0.0, 10.0),
    "account_age_days": (0.0, 1000.0),
}


def preprocess_transaction(txn: dict[str, Any]) -> torch.Tensor:
    """Preprocess, ordinal-encode, and min-max scale a single transaction payload.

    Canonical authoritative reference preprocessor ensuring strict 100% feature
    representation parity across model training, real-time inference, and explainability (SHAP & LIME).
    """
    vals = []
    for name in FEATURE_NAMES:
        val = txn.get(name)
        if val is None:
            # Defaults matching data generator logic
            if name == "country_code":
                val = "US"
            elif name == "merchant_category":
                val = "grocery"
            elif name == "device_type":
                val = "web_browser"
            else:
                val = 0.0

        # Validate non-finite numeric input (MODEL-INV-13 fail-closed)
        if isinstance(val, (int, float)) and not math.isfinite(val):
            raise ValueError(f"Feature '{name}' contains non-finite value: {val}")

        # Encode categorical variables using constant list indexes
        if name == "merchant_category":
            val_str = val if isinstance(val, str) else str(val)
            if val_str not in MERCHANT_CATEGORIES:
                cat_aliases = {"retail": "grocery", "food": "dining", "online_retail": "online_marketplace"}
                val_str = cat_aliases.get(val_str, val_str)
            val = float(MERCHANT_CATEGORIES.index(val_str) if val_str in MERCHANT_CATEGORIES else 0)
        elif name == "country_code":
            val_str = val if isinstance(val, str) else str(val)
            if val_str not in COUNTRIES:
                country_aliases = {"GB": "UK"}
                val_str = country_aliases.get(val_str, val_str)
            val = float(COUNTRIES.index(val_str) if val_str in COUNTRIES else 0)
        elif name == "device_type":
            val_str = val if isinstance(val, str) else str(val)
            if val_str not in DEVICES:
                device_aliases = {"web": "web_browser", "mobile": "mobile_app", "mobile_web": "web_browser"}
                val_str = device_aliases.get(val_str, val_str)
            val = float(DEVICES.index(val_str) if val_str in DEVICES else 0)
        else:
            try:
                val = float(val)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid numeric value for feature '{name}': {val}") from exc
            if not math.isfinite(val):
                raise ValueError(f"Feature '{name}' contains non-finite numeric value: {val}")

        # Scale with pre-defined dataset reference bounds
        c_min, c_max = REFERENCE_BOUNDS.get(name, (0.0, 1.0))
        if c_max > c_min:
            val_norm = (val - c_min) / (c_max - c_min)
            val_norm = max(0.0, min(1.0, val_norm))  # clip to [0, 1]
        else:
            val_norm = 0.0
        vals.append(val_norm)

    return torch.FloatTensor([vals])


class DataGenerator:
    """Generates synthetic Non-IID transaction datasets for federated learning.

    The three banks have deliberately different distributions:

    Bank A (Meridian National) — Large retail bank
      - High volume (50k default), low fraud rate (0.8%)
      - Fraud pattern: velocity spikes + unusual merchant at night
      - Broad geographic distribution, most transactions domestic

    Bank B (Nexus Digital) — Digital-only bank
      - Medium volume (30k default), higher fraud rate (2.5%)
      - Fraud pattern: new accounts + high-risk countries + crypto/wire
      - Heavy mobile usage, international transactions

    Bank C (Heritage Regional) — Traditional regional bank
      - Lower volume (20k default), moderate fraud rate (1.2%)
      - Fraud pattern: repeated small amounts before large charge (testing)
      - Concentrated geography, POS-heavy
    """

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def generate_bank_datasets(
        self,
        bank_a_size: int = 50000,
        bank_b_size: int = 30000,
        bank_c_size: int = 20000,
    ) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
        """Generate datasets for all three banks.

        Returns:
            Dict mapping bank_id to (features_df, labels_series).
            Labels are binary: 0 = legitimate, 1 = fraud.
        """
        rng = np.random.default_rng(self.seed)

        datasets = {
            "bank_a": self._generate_bank_a(bank_a_size, rng),
            "bank_b": self._generate_bank_b(bank_b_size, rng),
            "bank_c": self._generate_bank_c(bank_c_size, rng),
        }

        for bank_id, (_features, labels) in datasets.items():
            fraud_count = int(labels.sum())
            logger.info(
                "Generated %d transactions for %s (fraud: %d, ratio: %.2f%%)",
                len(labels),
                bank_id,
                fraud_count,
                100 * fraud_count / len(labels),
            )

        return datasets

    def create_bank_profiles(
        self,
        datasets: dict[str, tuple[pd.DataFrame, pd.Series]],
    ) -> dict[str, BankDataProfile]:
        """Extract statistical profiles from generated datasets."""
        profiles: dict[str, BankDataProfile] = {}
        bank_names = {
            "bank_a": "Meridian National",
            "bank_b": "Nexus Digital",
            "bank_c": "Heritage Regional",
        }

        for bank_id, (df, labels) in datasets.items():
            profiles[bank_id] = BankDataProfile(
                bank_name=bank_names[bank_id],
                num_transactions=len(df),
                fraud_ratio=float(labels.mean()),
                mean_transaction_amount=float(df["transaction_amount"].mean()),
                std_transaction_amount=float(df["transaction_amount"].std()),
                top_merchant_categories=df["merchant_category"]
                .value_counts()
                .head(5)
                .index.tolist(),
                top_countries=df["country_code"].value_counts().head(5).index.tolist(),
                mean_account_age_days=float(df["account_age_days"].mean()),
                mean_velocity=float(df["velocity"].mean()),
            )

        return profiles

    def create_bank_entities(
        self,
        datasets: dict[str, tuple[pd.DataFrame, pd.Series]],
        profiles: dict[str, BankDataProfile],
    ) -> list[Bank]:
        """Create Bank domain entities from generated data."""
        bank_configs = {
            "bank_a": ("Meridian National", BankTier.LARGE),
            "bank_b": ("Nexus Digital", BankTier.MEDIUM),
            "bank_c": ("Heritage Regional", BankTier.SMALL),
        }

        banks = []
        for bank_id, (_, labels) in datasets.items():
            name, tier = bank_configs[bank_id]
            banks.append(
                Bank(
                    id=bank_id,
                    name=name,
                    tier=tier,
                    fraud_ratio=float(labels.mean()),
                    num_transactions=len(labels),
                    data_profile=profiles.get(bank_id),
                )
            )

        return banks

    # ── Private: Bank-specific generators ─────

    def _generate_bank_a(
        self,
        n: int,
        rng: np.random.Generator,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """Meridian National — Large retail bank, 0.8% fraud.

        Fraud pattern: velocity spikes during late-night hours
        with unusual merchant categories.
        """
        fraud_ratio = 0.008
        n_fraud = int(n * fraud_ratio)
        n_legit = n - n_fraud

        # Legitimate transactions
        legit = self._generate_base_transactions(n_legit, rng, profile="retail_domestic")

        # Fraudulent transactions — velocity spikes at night
        fraud = self._generate_base_transactions(n_fraud, rng, profile="retail_domestic")
        fraud["velocity"] = pd.Series(rng.uniform(8, 25, n_fraud).tolist(), index=fraud.index)
        fraud["hour_of_day"] = pd.Series(
            rng.choice([0, 1, 2, 3, 4, 22, 23], n_fraud).tolist(), index=fraud.index
        )
        fraud["merchant_category"] = pd.Series(
            rng.choice(
                ["electronics", "jewelry", "gambling", "crypto"],
                n_fraud,
            ).tolist(),
            index=fraud.index,
        )
        fraud["transaction_amount"] = pd.Series(
            rng.lognormal(6.5, 1.2, n_fraud).tolist(), index=fraud.index
        )
        fraud["merchant_risk_score"] = pd.Series(
            rng.uniform(0.6, 1.0, n_fraud).tolist(), index=fraud.index
        )

        features = pd.concat([df for df in [legit, fraud] if not df.empty], ignore_index=True)
        labels = pd.Series(
            [0] * n_legit + [1] * n_fraud,
            name="is_fraud",
        )

        # Shuffle
        idx = rng.permutation(len(features)).tolist()
        return features.iloc[idx].reset_index(drop=True), labels.iloc[idx].reset_index(drop=True)

    def _generate_bank_b(
        self,
        n: int,
        rng: np.random.Generator,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """Nexus Digital — Digital bank, 2.5% fraud.

        Fraud pattern: new accounts + high-risk countries + crypto/wire transfers.
        """
        fraud_ratio = 0.025
        n_fraud = int(n * fraud_ratio)
        n_legit = n - n_fraud

        legit = self._generate_base_transactions(n_legit, rng, profile="digital_international")

        fraud = self._generate_base_transactions(n_fraud, rng, profile="digital_international")
        fraud["account_age_days"] = pd.Series(
            rng.integers(0, 30, n_fraud).tolist(), index=fraud.index
        )
        fraud["country_code"] = pd.Series(
            rng.choice(list(HIGH_RISK_COUNTRIES), n_fraud).tolist(), index=fraud.index
        )
        fraud["merchant_category"] = pd.Series(
            rng.choice(
                ["crypto", "wire_transfer", "gambling", "online_marketplace"],
                n_fraud,
            ).tolist(),
            index=fraud.index,
        )
        fraud["transaction_amount"] = pd.Series(
            rng.lognormal(7.0, 1.5, n_fraud).tolist(), index=fraud.index
        )
        fraud["device_type"] = pd.Series(
            rng.choice(["mobile_app", "web_browser"], n_fraud).tolist(), index=fraud.index
        )
        fraud["chargeback_count"] = pd.Series(
            rng.integers(1, 8, n_fraud).tolist(), index=fraud.index
        )

        features = pd.concat([df for df in [legit, fraud] if not df.empty], ignore_index=True)
        labels = pd.Series([0] * n_legit + [1] * n_fraud, name="is_fraud")

        idx = rng.permutation(len(features)).tolist()
        return features.iloc[idx].reset_index(drop=True), labels.iloc[idx].reset_index(drop=True)

    def _generate_bank_c(
        self,
        n: int,
        rng: np.random.Generator,
    ) -> tuple[pd.DataFrame, pd.Series]:
        """Heritage Regional - Regional bank, 1.2% fraud.

        Fraud pattern: card testing - repeated small amounts
        followed by a large charge.
        """
        fraud_ratio = 0.012
        n_fraud = int(n * fraud_ratio)
        n_legit = n - n_fraud

        legit = self._generate_base_transactions(n_legit, rng, profile="regional_pos")

        fraud = self._generate_base_transactions(n_fraud, rng, profile="regional_pos")
        fraud["transaction_amount"] = pd.Series(
            np.where(
                rng.random(n_fraud) < 0.7,
                rng.uniform(0.50, 5.00, n_fraud),  # Small test charges
                rng.lognormal(7.5, 0.8, n_fraud),  # Large follow-up
            ).tolist(),
            index=fraud.index,
        )
        fraud["velocity"] = pd.Series(rng.uniform(5, 20, n_fraud).tolist(), index=fraud.index)
        fraud["customer_history_score"] = pd.Series(
            rng.uniform(0.0, 0.3, n_fraud).tolist(), index=fraud.index
        )
        fraud["merchant_risk_score"] = pd.Series(
            rng.uniform(0.4, 0.9, n_fraud).tolist(), index=fraud.index
        )

        features = pd.concat([df for df in [legit, fraud] if not df.empty], ignore_index=True)
        labels = pd.Series([0] * n_legit + [1] * n_fraud, name="is_fraud")

        idx = rng.permutation(len(features)).tolist()
        return features.iloc[idx].reset_index(drop=True), labels.iloc[idx].reset_index(drop=True)

    def _generate_base_transactions(
        self,
        n: int,
        rng: np.random.Generator,
        profile: str = "retail_domestic",
    ) -> pd.DataFrame:
        """Generate baseline legitimate-looking transactions."""
        if profile == "retail_domestic":
            amounts = rng.lognormal(3.5, 1.0, n)
            countries = rng.choice(["US", "US", "US", "CA", "UK", "DE"], n)
            merchants = rng.choice(MERCHANT_CATEGORIES[:12], n)
            devices = rng.choice(DEVICES, n, p=[0.25, 0.20, 0.40, 0.10, 0.05])
            velocities = rng.exponential(1.5, n)
            account_ages = rng.integers(90, 3650, n)

        elif profile == "digital_international":
            amounts = rng.lognormal(4.0, 1.2, n)
            countries = rng.choice(COUNTRIES, n)
            merchants = rng.choice(MERCHANT_CATEGORIES, n)
            devices = rng.choice(DEVICES, n, p=[0.50, 0.35, 0.05, 0.05, 0.05])
            velocities = rng.exponential(2.0, n)
            account_ages = rng.integers(30, 1825, n)

        elif profile == "regional_pos":
            amounts = rng.lognormal(3.0, 0.8, n)
            countries = rng.choice(["US", "US", "US", "US", "CA"], n)
            merchants = rng.choice(MERCHANT_CATEGORIES[:10], n)
            devices = rng.choice(DEVICES, n, p=[0.15, 0.10, 0.55, 0.15, 0.05])
            velocities = rng.exponential(1.0, n)
            account_ages = rng.integers(365, 7300, n)

        else:
            raise ValueError(f"Unknown transaction profile: {profile}")

        return pd.DataFrame(
            {
                "transaction_amount": np.clip(amounts, 0.01, 50000),
                "merchant_category": merchants,
                "country_code": countries,
                "device_type": devices,
                "velocity": np.clip(velocities, 0, 30),
                "hour_of_day": rng.integers(0, 24, n),
                "merchant_risk_score": np.clip(rng.beta(2, 5, n), 0, 1),
                "customer_history_score": np.clip(rng.beta(5, 2, n), 0, 1),
                "chargeback_count": rng.poisson(0.3, n),
                "account_age_days": account_ages,
            }
        )

    @staticmethod
    def encode_features(df: pd.DataFrame) -> np.ndarray:
        """Convert categorical features to numeric and normalize for model training.

        Enforces strict feature schema alignment with FEATURE_NAMES and
        authoritative vocabularies/reference bounds to ensure training and
        inference preprocessing parity.
        """
        missing = [f for f in FEATURE_NAMES if f not in df.columns]
        if missing:
            raise ValueError(f"Missing required features for model encoding: {missing}")

        encoded = df[FEATURE_NAMES].copy()

        # Encode categoricals using authoritative vocabulary indices matching inference
        cat_mappings = [
            ("merchant_category", MERCHANT_CATEGORIES),
            ("country_code", COUNTRIES),
            ("device_type", DEVICES),
        ]
        for col, vocab in cat_mappings:
            vocab_map = {val: float(idx) for idx, val in enumerate(vocab)}
            encoded[col] = encoded[col].map(lambda x, vm=vocab_map: vm.get(str(x), 0.0)).astype(float)

        # Normalize features using shared REFERENCE_BOUNDS
        for col in encoded.columns:
            c_min, c_max = REFERENCE_BOUNDS.get(col, (0.0, 1.0))
            if c_max > c_min:
                col_vals = encoded[col].astype(float)
                val_norm = (col_vals - c_min) / (c_max - c_min)
                encoded[col] = val_norm.clip(0.0, 1.0)
            else:
                encoded[col] = 0.0

        return np.asarray(encoded.to_numpy(), dtype=np.float32)
