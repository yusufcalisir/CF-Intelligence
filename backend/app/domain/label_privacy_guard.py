"""Label Privacy Guard for Zero-PII Leak Enforcement."""

from __future__ import annotations

import logging
import math
import random
import re
from typing import Any

logger = logging.getLogger(__name__)


class LabelPrivacyViolationError(Exception):
    """Raised when unmasked PII or non-compliant DP parameters are detected."""

    pass


# Unhashed PII regex patterns (raw IBAN, SSN, email, phone, credit cards)
UNHASHED_PII_PATTERNS = [
    re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{12,30}$", re.IGNORECASE),  # Raw IBAN
    re.compile(r"^\d{3}-\d{2}-\d{4}$"),  # Raw SSN
    re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$"),  # Raw Email
    re.compile(r"^\+?[1-9]\d{1,14}$"),  # E.164 Phone Number
    re.compile(r"^(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})$"),  # Credit Card (Visa, MC, Amex)
]


class LabelPrivacyGuard:
    """Enforces zero-PII leak constraints and Differential Privacy parameter bounds."""

    def validate_feedback_identifier(
        self,
        transaction_id_hash: str,
        raw_attributes: dict[str, Any] | None = None,
    ) -> None:
        """Validates that transaction identifier is properly hashed and no raw PII exists."""
        if not isinstance(transaction_id_hash, str) or not transaction_id_hash.strip():
            raise LabelPrivacyViolationError("Transaction identifier must be a non-empty string.")

        clean_id = transaction_id_hash.strip()

        # 1. Identifier length check (Hex-encoded hash should be >= 32 chars)
        if len(clean_id) < 32:
            raise LabelPrivacyViolationError(
                f"Transaction identifier '{clean_id}' is too short; must be an HMAC-SHA256 hash (>= 32 chars)."
            )

        # 2. Check for cleartext PII patterns
        for pattern in UNHASHED_PII_PATTERNS:
            if pattern.match(clean_id):
                raise LabelPrivacyViolationError(
                    f"Transaction identifier '{clean_id}' matches raw PII format. Cleartext PII is strictly forbidden!"
                )

        # 3. Inspect raw attributes dictionary if provided
        if raw_attributes:
            forbidden_keys = {
                "iban",
                "ssn",
                "email",
                "customer_name",
                "credit_card",
                "phone",
                "password",
                "dob",
                "account_number",
                "tax_id",
            }
            for key in raw_attributes:
                if key.lower() in forbidden_keys:
                    raise LabelPrivacyViolationError(
                        f"Forbidden raw PII key '{key}' found in label feedback attributes!"
                    )

    def validate_gradient_privacy(
        self, epsilon: float, max_epsilon: float = 2.0, delta: float | None = None
    ) -> None:
        """Validates Differential Privacy budget parameters epsilon and optional delta."""
        if not math.isfinite(epsilon) or epsilon <= 0.0 or epsilon > max_epsilon:
            raise LabelPrivacyViolationError(
                f"Differential Privacy epsilon {epsilon} is invalid. Must be in range (0.0, {max_epsilon}]."
            )

        if delta is not None and (not math.isfinite(delta) or delta <= 0.0 or delta >= 1.0):
            raise LabelPrivacyViolationError(
                f"Differential Privacy delta {delta} is invalid. Must be in range (0.0, 1.0)."
            )

    def calculate_sigma(
        self, epsilon: float, delta: float, clip_norm: float = 1.0
    ) -> float:
        """Derives analytical Gaussian noise scale required to satisfy target privacy budget bounds (ε, δ).

        Mathematical Formulation (Dwork & Roth, 2014; Claim M-09):
            σ = (C · √(2 · ln(1.25 / δ))) / ε
        """
        if not math.isfinite(epsilon) or epsilon <= 0.0:
            raise LabelPrivacyViolationError("Differential Privacy epsilon must be strictly positive (> 0.0).")
        if not math.isfinite(delta) or delta <= 0.0 or delta >= 1.0:
            raise LabelPrivacyViolationError("Differential Privacy delta must be in range (0.0, 1.0).")
        if not math.isfinite(clip_norm) or clip_norm <= 0.0:
            raise LabelPrivacyViolationError("Gradient clipping norm must be strictly positive (> 0.0).")

        sigma = (clip_norm * math.sqrt(2.0 * math.log(1.25 / delta))) / epsilon
        return round(sigma, 6)

    def apply_randomized_response(
        self, label: int, epsilon: float = 1.0, seed: int | None = None
    ) -> int:
        """Applies local randomized response to binary labels to guarantee ε-Differential Privacy.

        Probability of flipping label: p = 1 / (1 + e^ε).
        """
        if label not in (0, 1):
            raise LabelPrivacyViolationError(f"Binary label must be 0 or 1, got: {label}")
        if not math.isfinite(epsilon) or epsilon <= 0.0:
            raise LabelPrivacyViolationError("Epsilon must be strictly positive for randomized response.")

        rng = random.Random(seed) if seed is not None else random.Random()
        flip_prob = 1.0 / (1.0 + math.exp(epsilon))

        if rng.random() < flip_prob:
            return 1 - label
        return label
