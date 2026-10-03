"""Partition-First Feature Engineering and Local Preprocessing for CrossBank v2.

Enforces strict information boundaries, temporal causality (zero lookahead), and local
preprocessing fitting without global pooling.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from benchmarks.crossbank_v2.config import FeatureRegime

LOCAL_FEATURE_COLUMNS = [
    "log_amount",
    "hour_of_day",
    "is_weekend",
    "is_outward",
    "is_inward",
    "is_cross_bank",
    "local_src_degree",
    "local_tgt_degree",
    "local_velocity_2h",
    "rail_code",
]

CONSORTIUM_SIGNAL_COLUMN = "consortium_hop_signal"


def compute_feature_schema_hash(feature_regime: FeatureRegime) -> str:
    """Compute deterministic SHA-256 of the feature schema."""
    cols = list(LOCAL_FEATURE_COLUMNS)
    if feature_regime == FeatureRegime.CONSORTIUM_SIGNAL:
        cols.append(CONSORTIUM_SIGNAL_COLUMN)

    payload = {
        "regime": feature_regime.value,
        "features": cols,
        "feature_count": len(cols),
        "types": {c: "float32" for c in cols},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class PartitionFirstFeatureExtractor:
    """Computes strictly localized features obeying temporal causality and bank information horizons."""

    PAYMENT_RAIL_MAP = {
        "INTERNAL": 0,
        "SEPA_INSTANT": 1,
        "TARGET2": 2,
        "SWIFT": 3,
        "WIRE": 4,
        "ACH": 5,
    }

    @classmethod
    def extract_features(
        cls,
        df_bank_transactions: pd.DataFrame,
        bank_id: str,
        regime: FeatureRegime = FeatureRegime.LOCAL_ONLY,
    ) -> pd.DataFrame:
        """Extract features strictly using only information visible to `bank_id`.

        Invariant: Transactions where neither source_bank nor target_bank is bank_id
        must be completely absent from df_bank_transactions.
        """
        df = df_bank_transactions.copy()
        if len(df) == 0:
            cols = list(LOCAL_FEATURE_COLUMNS)
            if regime == FeatureRegime.CONSORTIUM_SIGNAL:
                cols.append(CONSORTIUM_SIGNAL_COLUMN)
            return pd.DataFrame(columns=pd.Index(cols))

        # Confirm information boundary: bank_id must be on every transaction
        assert (
            (df["source_bank"] == bank_id) | (df["target_bank"] == bank_id)
        ).all(), f"Information horizon violated: transactions outside {bank_id} detected"

        # Sort strictly chronologically
        df = df.sort_values(by=["step", "transaction_id"]).reset_index(drop=True)

        features: dict[str, list[float]] = {col: [] for col in LOCAL_FEATURE_COLUMNS}
        if regime == FeatureRegime.CONSORTIUM_SIGNAL:
            features[CONSORTIUM_SIGNAL_COLUMN] = []

        # Local temporal state tracking
        src_history: dict[str, list[int]] = {}
        tgt_history: dict[str, list[int]] = {}

        for _, row in df.iterrows():
            amt = float(row["amount"])
            step = int(row["step"])
            s_bank = str(row["source_bank"])
            t_bank = str(row["target_bank"])
            s_acc = str(row["source_account"])
            t_acc = str(row["target_account"])
            rail = str(row["payment_rail"])

            # 1. Pure scalar features
            features["log_amount"].append(float(np.log1p(amt)))
            features["hour_of_day"].append(float(step % 24))
            features["is_weekend"].append(float(1.0 if ((step // 24) % 7 >= 5) else 0.0))
            features["is_outward"].append(float(1.0 if s_bank == bank_id else 0.0))
            features["is_inward"].append(float(1.0 if t_bank == bank_id else 0.0))
            features["is_cross_bank"].append(float(1.0 if s_bank != t_bank else 0.0))
            features["rail_code"].append(float(cls.PAYMENT_RAIL_MAP.get(rail, 1)))

            # 2. Causally bounded degree & velocity (strictly step < current_step or prior row in step)
            # Prior events for this source account in local visibility
            prior_s_steps = src_history.get(s_acc, [])
            features["local_src_degree"].append(float(len(prior_s_steps)))

            # Prior events for this target account in local visibility
            prior_t_steps = tgt_history.get(t_acc, [])
            features["local_tgt_degree"].append(float(len(prior_t_steps)))

            # Velocity in the last 2 hours (s - 2 <= prior_step <= s)
            v_count = sum(1 for p_s in prior_s_steps if 0 <= (step - p_s) <= 2)
            features["local_velocity_2h"].append(float(v_count))

            # Update historical state for future rows
            if s_acc not in src_history:
                src_history[s_acc] = []
            src_history[s_acc].append(step)

            if t_acc not in tgt_history:
                tgt_history[t_acc] = []
            tgt_history[t_acc].append(step)

            # 3. Explicit Consortium Signal (if active regime: ORACLE_UPPER_BOUND_ABLATION)
            if regime == FeatureRegime.CONSORTIUM_SIGNAL:
                # Simulated oracle consortium signal (ORACLE_UPPER_BOUND_ABLATION):
                # Evaluates theoretical upper-bound gain if cross-bank consortium intelligence
                # perfectly recognized multi-hop laundering topology at inference.
                is_scen = row.get("scenario_id") is not None
                features[CONSORTIUM_SIGNAL_COLUMN].append(float(1.0 if is_scen else 0.0))

        feat_df = pd.DataFrame(features)
        return feat_df


class LocalPreprocessor:
    """Fits normalization strictly on local training data. Zero global pooling."""

    def __init__(self) -> None:
        self.scaler = StandardScaler()
        self.is_fitted = False
        self.feature_names: list[str] = []

    def fit(self, df_train_features: pd.DataFrame) -> LocalPreprocessor:
        """Fit scaler on client-local train set only."""
        self.feature_names = list(df_train_features.columns)
        if len(df_train_features) > 0:
            self.scaler.fit(df_train_features[self.feature_names].to_numpy())
        self.is_fitted = True
        return self

    def transform(self, df_features: pd.DataFrame) -> np.ndarray:
        """Transform features using fitted local parameters."""
        assert self.is_fitted, "Preprocessor must be fit on train data before transform"
        if len(df_features) == 0:
            return np.empty((0, len(self.feature_names)), dtype=np.float32)
        transformed = self.scaler.transform(df_features[self.feature_names].to_numpy())
        return np.asarray(transformed, dtype=np.float32)
