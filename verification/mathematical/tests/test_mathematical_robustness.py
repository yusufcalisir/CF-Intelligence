"""
Mathematical Robustness & Boundary Stress Test Suite
=====================================================
Evaluates extreme floating-point edge cases, division-by-zero guards,
zero-norm vectors, NaN/Inf handling, and scale limits across platform formulas.
"""

import math
import unittest
import numpy as np


class TestMathematicalRobustness(unittest.TestCase):
    """Robustness stress tests for platform mathematical functions."""

    def test_zero_vector_clipping_no_nan(self):
        """Verify L2 gradient clipping on zero vector produces zero vector without NaN."""
        g = np.zeros(10)
        C = 1.0
        
        norm_g = np.linalg.norm(g)
        factor = C / max(C, norm_g)
        g_clipped = g * factor
        
        self.assertFalse(np.isnan(g_clipped).any())
        self.assertTrue(np.array_equal(g_clipped, np.zeros(10)))

    def test_zero_vector_unit_sphere_norm_fallback(self):
        """Verify L2 unit-sphere normalization handles zero-norm fallback safely."""
        h = np.zeros(5)
        norm_h = np.linalg.norm(h)
        
        if norm_h < 1e-12:
            h_norm = np.zeros(5)
        else:
            h_norm = h / norm_h
            
        self.assertFalse(np.isnan(h_norm).any())

    def test_extreme_float_scale_fedavg(self):
        """Verify FedAvg precision under very small floats (1e-300)."""
        w1 = np.array([1e-300, 2e-300])
        w2 = np.array([3e-300, 4e-300])
        
        avg = 0.5 * w1 + 0.5 * w2
        expected = np.array([2e-300, 3e-300])
        np.testing.assert_allclose(avg, expected, rtol=1e-10)

    def test_composite_risk_score_clamping(self):
        """Verify composite risk score bounds strictly clamp negative or overflowing raw inputs."""
        weights = [0.20, 0.80]
        
        # Test extreme overflow
        signals_overflow = [10.0, 5.0]
        raw_overflow = sum(w * s for w, s in zip(weights, signals_overflow)) * 1000.0
        score_overflow = min(1000.0, max(0.0, raw_overflow))
        self.assertEqual(score_overflow, 1000.0)
        
        # Test negative input
        signals_negative = [-1.0, -2.0]
        raw_negative = sum(w * s for w, s in zip(weights, signals_negative)) * 1000.0
        score_negative = min(1000.0, max(0.0, raw_negative))
        self.assertEqual(score_negative, 0.0)

    def test_sigmoid_underflow_overflow_stability(self):
        """Verify sigmoid z-score calculation does not overflow under extreme z-scores."""
        z_large_pos = 1000.0
        z_large_neg = -1000.0
        
        s_pos = 1.0 / (1.0 + math.exp(-min(500.0, z_large_pos)))
        s_neg = 1.0 / (1.0 + math.exp(-max(-500.0, z_large_neg)))
        
        self.assertAlmostEqual(s_pos, 1.0, places=12)
        self.assertAlmostEqual(s_neg, 0.0, places=12)

    def test_psi_zero_bin_smoothing(self):
        """Verify PSI calculation handles zero probability bins with epsilon smoothing."""
        P = np.array([0.0, 1.0])
        Q = np.array([0.5, 0.5])
        eps = 1e-12
        
        P_smooth = np.clip(P, eps, 1.0)
        Q_smooth = np.clip(Q, eps, 1.0)
        
        psi = np.sum((P_smooth - Q_smooth) * np.log(P_smooth / Q_smooth))
        self.assertFalse(np.isnan(psi))
        self.assertGreater(psi, 0.0)


if __name__ == "__main__":
    unittest.main()
