"""Shamir (t, n) Threshold Secret Sharing Engine.

Implements polynomial secret sharing and Lagrange interpolation over a 256-bit
prime field Z_p (p = 2^256 - 189). Allows splitting arbitrary 32-byte cryptographic
secrets (X25519 private keys, self-mask seeds) into n shares such that any t
shares can reconstruct the secret, while t-1 shares reveal zero information.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 256-bit prime: 2^256 - 189 (largest 256-bit prime)
PRIME_256: int = 2**256 - 189


@dataclass(frozen=True)
class ShamirShare:
    """A single evaluation point (x, y) of a Shamir polynomial share."""

    x: int
    y: int

    def to_bytes(self) -> bytes:
        """Serialize share as 68 bytes (4-byte x || 64-byte y)."""
        return self.x.to_bytes(4, "big") + self.y.to_bytes(64, "big")

    @classmethod
    def from_bytes(cls, data: bytes) -> ShamirShare:
        """Deserialize 68-byte binary share representation."""
        if len(data) != 68:
            raise ValueError(f"Invalid ShamirShare byte length: {len(data)}, expected 68")
        x = int.from_bytes(data[:4], "big")
        y = int.from_bytes(data[4:], "big")
        return cls(x=x, y=y)


class ShamirSecretSharingEngine:
    """Cryptographic engine for Shamir (t, n) threshold secret sharing."""

    def __init__(self, prime: int = PRIME_256) -> None:
        self.prime = prime

    def split_secret(
        self, secret_bytes: bytes, threshold: int, total_shares: int
    ) -> list[ShamirShare]:
        """Split a 32-byte secret into n shares with threshold t.

        Args:
            secret_bytes: 32-byte raw secret payload.
            threshold: Minimum number of shares t required for reconstruction.
            total_shares: Total number of shares n to generate.

        Returns:
            List of n ShamirShare objects with x in [1, total_shares].
        """
        if len(secret_bytes) > 32:
            raise ValueError(f"Secret size {len(secret_bytes)} bytes exceeds 32-byte limit")
        if threshold < 2:
            raise ValueError(f"Threshold t must be at least 2, got {threshold}")
        if threshold > total_shares:
            raise ValueError(
                f"Threshold t ({threshold}) cannot exceed total_shares n ({total_shares})"
            )

        secret_int = int.from_bytes(secret_bytes, "big")
        if secret_int >= self.prime:
            raise ValueError("Secret numerical value exceeds prime field order")

        # Generate t-1 random coefficients a_1, ..., a_{t-1} in [1, prime-1]
        coefficients = [secret_int]
        for _ in range(threshold - 1):
            random_bytes = os.urandom(32)
            coeff = int.from_bytes(random_bytes, "big") % self.prime
            coefficients.append(coeff)

        # Evaluate polynomial f(x) = secret + a_1*x + ... + a_{t-1}*x^{t-1} (mod p)
        shares: list[ShamirShare] = []
        for x in range(1, total_shares + 1):
            y = self._evaluate_polynomial(coefficients, x)
            shares.append(ShamirShare(x=x, y=y))

        return shares

    def reconstruct_secret(self, shares: list[ShamirShare], threshold: int) -> bytes:
        """Reconstruct the 32-byte secret from at least t shares using Lagrange interpolation.

        Args:
            shares: List of at least t distinct ShamirShare objects.
            threshold: Minimum required shares t.

        Returns:
            32-byte reconstructed secret payload.
        """
        if len(shares) < threshold:
            raise ValueError(
                f"Insufficient shares for reconstruction: got {len(shares)}, required {threshold}"
            )

        # Use first t shares for interpolation
        subset = shares[:threshold]

        # Check for duplicate evaluation points x
        x_values = [s.x for s in subset]
        if len(set(x_values)) != len(x_values):
            raise ValueError("Duplicate share evaluation indices detected")

        # Lagrange interpolation at x = 0:
        # f(0) = \sum_{i=1}^t y_i * \prod_{j \neq i} (0 - x_j) / (x_i - x_j) (mod p)
        secret_int = 0
        for i, share_i in enumerate(subset):
            num = 1
            den = 1
            for j, share_j in enumerate(subset):
                if i == j:
                    continue
                num = (num * (-share_j.x)) % self.prime
                den = (den * (share_i.x - share_j.x)) % self.prime

            # Modular inverse of denominator
            den_inv = self._mod_inverse(den, self.prime)
            lagrange_coeff = (num * den_inv) % self.prime
            term = (share_i.y * lagrange_coeff) % self.prime
            secret_int = (secret_int + term) % self.prime

        return secret_int.to_bytes(32, "big")

    def _evaluate_polynomial(self, coefficients: list[int], x: int) -> int:
        """Evaluate polynomial f(x) = sum(a_k * x^k) mod p using Horner's method."""
        result = 0
        for coeff in reversed(coefficients):
            result = (result * x + coeff) % self.prime
        return result

    def _mod_inverse(self, a: int, m: int) -> int:
        """Compute modular multiplicative inverse a^-1 mod m using Fermat's Little Theorem."""
        a = a % m
        if a == 0:
            raise ZeroDivisionError("Modular inverse of 0 does not exist")
        return pow(a, m - 2, m)
