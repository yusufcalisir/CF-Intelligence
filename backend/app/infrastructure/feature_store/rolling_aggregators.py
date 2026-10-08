"""Rolling Behavioral Feature Aggregators for Payment Streams."""

from __future__ import annotations

import datetime
import logging
import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.infrastructure.connectors.base_connector import NormalizedTransaction

logger = logging.getLogger(__name__)

# FATF High-Risk & Blacklisted Jurisdiction Weights
FATF_COUNTRY_RISK_MAP = {
    "IR": 1.0,  # Iran (FATF Blacklist)
    "KP": 1.0,  # DPRK / North Korea (FATF Blacklist)
    "MM": 0.85,  # Myanmar (FATF Blacklist)
    "SY": 0.75,  # Syria (FATF Grey List)
    "YE": 0.70,  # Yemen (FATF Grey List)
    "KY": 0.60,  # Cayman Islands
    "PA": 0.55,  # Panama
    "US": 0.05,  # Standard low risk
    "GB": 0.05,
    "DE": 0.05,
    "TR": 0.15,
}
DEFAULT_COUNTRY_RISK = 0.20


class RollingFeatureAggregator:
    """Computes sliding-window behavioral aggregations and entity features

    across high-frequency transaction streams.
    """

    def __init__(self) -> None:
        # In-memory history stores per account_id
        # account_history[account_id] = list of (timestamp, amount, mcc, device_fp, ip_subnet, dest_country)
        self.account_history: dict[str, list[dict[str, Any]]] = {}
        # account_creation_times[account_id] = datetime
        self.account_creation_times: dict[str, datetime.datetime] = {}
        # sar_alerts_history[account_id] = list of alert timestamps
        self.sar_alerts_history: dict[str, list[datetime.datetime]] = {}

    def register_account(
        self, account_id: str, creation_time: datetime.datetime | None = None
    ) -> None:
        """Registers an account creation timestamp for account_age_days calculation."""
        if account_id not in self.account_creation_times:
            now = datetime.datetime.now(datetime.UTC)
            self.account_creation_times[account_id] = creation_time or (now - timedelta(days=30))

    def record_sar_alert(
        self, account_id: str, alert_time: datetime.datetime | None = None
    ) -> None:
        """Records an AML SAR alert for previous_alerts_30d calculation."""
        atime = alert_time or datetime.datetime.now(datetime.UTC)
        if account_id not in self.sar_alerts_history:
            self.sar_alerts_history[account_id] = []
        self.sar_alerts_history[account_id].append(atime)

    def compute_features(self, tx: NormalizedTransaction) -> dict[str, float]:
        """Calculates all 7 rolling feature specifications for an incoming transaction.

        Features:
        1. account_age_days
        2. merchant_velocity_1h
        3. device_entropy
        4. country_risk_score
        5. hour_of_day_cos
        6. hour_of_day_sin
        7. rolling_amount_zscore_24h
        8. previous_alerts_30d
        """
        now = tx.timestamp

        # 1. account_age_days
        creation_time = self.account_creation_times.get(tx.account_id, now - timedelta(days=90))
        account_age_days = max(0.0, (now - creation_time).total_seconds() / 86400.0)

        # Retrieve prior transaction history for this account
        history = self.account_history.get(tx.account_id, [])

        # 2. merchant_velocity_1h (count in past 60 mins matching merchant_category_code)
        cutoff_1h = now - timedelta(hours=1)
        merchant_velocity_1h = sum(
            1
            for item in history
            if item["timestamp"] >= cutoff_1h
            and item["merchant_category_code"] == tx.merchant_category_code
        )

        # 3. device_entropy (uniqueness score over IP subnet, device fingerprint, channel)
        cutoff_30d = now - timedelta(days=30)
        tuples_30d = [
            (item["ip_subnet"], item["device_fingerprint"], item["channel_type"])
            for item in history
            if item["timestamp"] >= cutoff_30d
        ]
        current_tuple = (tx.ip_subnet, tx.device_fingerprint, tx.channel_type)
        tuples_30d.append(current_tuple)

        unique_count = len(set(tuples_30d))
        total_count = len(tuples_30d)
        # Shannon entropy / normalized diversity index
        device_entropy = round(unique_count / total_count, 4) if total_count > 0 else 0.0

        # 4. country_risk_score (FATF destination country weight)
        if tx.destination_country is not None:
            dest_country = tx.destination_country.strip().upper()
            country_risk_score = FATF_COUNTRY_RISK_MAP.get(dest_country, DEFAULT_COUNTRY_RISK)
        else:
            country_risk_score = DEFAULT_COUNTRY_RISK

        # 5. Cyclical time encodings (hour_of_day_cos and hour_of_day_sin)
        hour = now.hour + (now.minute / 60.0)
        radians = (2.0 * math.pi * hour) / 24.0
        hour_of_day_cos = round(math.cos(radians), 4)
        hour_of_day_sin = round(math.sin(radians), 4)

        # 6. rolling_amount_zscore_24h (Z-score relative to 24h rolling mean and std)
        cutoff_24h = now - timedelta(hours=24)
        amounts_24h = [item["amount"] for item in history if item["timestamp"] >= cutoff_24h]
        if amounts_24h:
            mean_24h = sum(amounts_24h) / len(amounts_24h)
            variance_24h = sum((x - mean_24h) ** 2 for x in amounts_24h) / len(amounts_24h)
            std_24h = math.sqrt(variance_24h)
            if std_24h > 1e-4:
                rolling_amount_zscore_24h = round((tx.amount - mean_24h) / std_24h, 4)
            else:
                rolling_amount_zscore_24h = round(tx.amount - mean_24h, 4)
        else:
            rolling_amount_zscore_24h = 0.0

        # 7. previous_alerts_30d (count of prior AML SAR alerts in last 30 days)
        alerts_history = self.sar_alerts_history.get(tx.account_id, [])
        previous_alerts_30d = sum(1 for atime in alerts_history if atime >= cutoff_30d)

        # Update history with current transaction
        if tx.account_id not in self.account_history:
            self.account_history[tx.account_id] = []
        self.account_history[tx.account_id].append(
            {
                "timestamp": tx.timestamp,
                "amount": tx.amount,
                "merchant_category_code": tx.merchant_category_code,
                "ip_subnet": tx.ip_subnet,
                "device_fingerprint": tx.device_fingerprint,
                "channel_type": tx.channel_type,
                "destination_country": tx.destination_country,
            }
        )

        return {
            "account_age_days": round(account_age_days, 2),
            "merchant_velocity_1h": float(merchant_velocity_1h),
            "device_entropy": device_entropy,
            "country_risk_score": country_risk_score,
            "hour_of_day_cos": hour_of_day_cos,
            "hour_of_day_sin": hour_of_day_sin,
            "rolling_amount_zscore_24h": rolling_amount_zscore_24h,
            "previous_alerts_30d": float(previous_alerts_30d),
        }
