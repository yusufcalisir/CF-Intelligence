"""Spectral Byzantine Anomaly Defense Domain Engine."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class SpectralByzantineDefense:
    """Detects and filters malicious model gradient updates using median absolute deviation (MAD) and spectral norm anomaly detection."""

    def __init__(self, contamination_ratio: float = 0.33) -> None:
        self.contamination_ratio = contamination_ratio

    def filter_anomalous_updates(
        self, updates: dict[str, np.ndarray | Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """Identifies and removes outlier gradients from malicious or corrupt nodes using robust median stats.

        Returns:
            Tuple of (sanitized_updates_dict, list_of_anomalous_bank_ids).
        """
        if len(updates) <= 2:
            return updates, []

        bank_ids = list(updates.keys())
        norms: list[float] = []

        for b_id in bank_ids:
            arr = np.asarray(updates[b_id], dtype=np.float64)
            norm_val = float(np.linalg.norm(arr))
            norms.append(norm_val)

        norms_arr = np.array(norms)
        median_norm = float(np.median(norms_arr))
        mad = float(np.median(np.abs(norms_arr - median_norm)))

        anomalies: list[str] = []
        sanitized: dict[str, Any] = {}

        for b_id, norm_val in zip(bank_ids, norms, strict=False):
            is_anomaly = False
            if mad > 1e-4 and (norm_val - median_norm) > 3.0 * mad:
                is_anomaly = True
            elif median_norm > 0 and norm_val > 3.0 * max(median_norm, 1.0):
                is_anomaly = True

            if is_anomaly:
                anomalies.append(b_id)
                logger.warning(
                    "SpectralByzantineDefense: Isolated malicious update from '%s' (norm: %.2f vs median: %.2f)",
                    b_id,
                    norm_val,
                    median_norm,
                )
            else:
                sanitized[b_id] = updates[b_id]

        return sanitized, anomalies
