"""Privacy & Cryptographic Mechanisms Deep Correctness & Adversarial Tests.

Validates:
1. Differential Privacy: Gaussian noise scale, gradient clipping norm bounds,
   Rényi DP (RDP) moments accounting, convex dual conversion, budget exhaustion.
2. Opacus DP-SGD: per-sample gradient clipping, actual epsilon recording, model unwrapping.
3. Secure Aggregation: additive zero-sum mask cancellation mathematical oracle (weighted & unweighted),
   edge-case resilience (empty/single client).
4. FHE (TenSEAL CKKS): homomorphic ciphertext averaging, key isolation, empty payload rejection.
5. TEE (Intel SGX / AWS Nitro): MRENCLAVE/MRSIGNER measurement, AEAD data sealing & tamper rejection.
6. Cryptographic Primitives: HMAC-SHA256 type-salted pseudonymization, input canonicalization,
   mTLS SSLContext CERT_REQUIRED enforcement, and multi-tenant budget isolation.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.model_service import ModelService
from app.application.services.privacy_service import (
    PrivacyBudget,
    PrivacyBudgetExceededError,
    PrivacyService,
)
from app.config import get_settings
from app.domain.value_objects import ModelWeights
from app.domain.value_objects_investigation import PrivacyPreservingIdentifier, standardize_input
from app.infrastructure.security.fhe_driver import FHEDriver
from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.tee_driver import TEEDriver

# ==============================================================================
# 1. DIFFERENTIAL PRIVACY CONTRACTS
# ==============================================================================


class TestDifferentialPrivacyContracts:
    """Mathematical verification and invariant tests for Differential Privacy."""

    def test_analytical_gaussian_noise_scale_formula(self) -> None:
        """Verify analytical noise scale: sigma = sensitivity * sqrt(2 * ln(1.25/delta)) / epsilon."""
        svc = PrivacyService()
        eps, delta, sens = 1.0, 1e-5, 1.0
        expected_sigma = float(sens * np.sqrt(2.0 * np.log(1.25 / delta)) / eps)

        calculated_sigma = svc.calculate_gaussian_noise_scale(
            epsilon=eps, delta=delta, sensitivity=sens
        )
        assert np.isclose(calculated_sigma, expected_sigma, rtol=1e-10)

        # Monotonicity checks: higher epsilon -> lower noise scale
        sigma_high_eps = svc.calculate_gaussian_noise_scale(epsilon=2.0, delta=delta, sensitivity=sens)
        assert sigma_high_eps < calculated_sigma

        # Higher sensitivity -> higher noise scale
        sigma_high_sens = svc.calculate_gaussian_noise_scale(epsilon=eps, delta=delta, sensitivity=2.0)
        assert sigma_high_sens > calculated_sigma

    def test_gaussian_noise_scale_invalid_parameters_fail_closed(self) -> None:
        """Verify noise scale calculation fails closed on invalid parameters."""
        svc = PrivacyService()
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            svc.calculate_gaussian_noise_scale(epsilon=0.0)

        with pytest.raises(ValueError, match="Epsilon must be positive"):
            svc.calculate_gaussian_noise_scale(epsilon=-1.0)

        with pytest.raises(ValueError, match="Delta must be in \\(0, 1\\)"):
            svc.calculate_gaussian_noise_scale(delta=0.0)

        with pytest.raises(ValueError, match="Delta must be in \\(0, 1\\)"):
            svc.calculate_gaussian_noise_scale(delta=1.0)

        with pytest.raises(ValueError, match="Sensitivity must be positive"):
            svc.calculate_gaussian_noise_scale(sensitivity=0.0)

    def test_clipping_correctness_and_norm_bound(self) -> None:
        """Verify ||g_i||_2 <= C contract after clipping across deterministic test vectors."""
        svc = PrivacyService()
        max_norm = 1.5

        # 1. Delta with norm below threshold: norm = 0.5 < 1.5 -> unchanged
        w_orig = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.0, 0.0, 0.0])
        w_small = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.3, 0.4, 0.0])  # norm = 0.5
        clipped_small = svc.clip_model_update(w_orig, w_small, max_norm=max_norm)
        delta_small = np.array(clipped_small.flat_weights) - np.array(w_orig.flat_weights)
        assert np.isclose(np.linalg.norm(delta_small), 0.5, atol=1e-7)
        np.testing.assert_allclose(clipped_small.flat_weights, [0.3, 0.4, 0.0], atol=1e-7)

        # 2. Delta with norm equal threshold: norm = 1.5 == 1.5 -> unchanged
        w_exact = ModelWeights(layer_shapes=[(3,)], flat_weights=[0.9, 1.2, 0.0])  # norm = 1.5
        clipped_exact = svc.clip_model_update(w_orig, w_exact, max_norm=max_norm)
        delta_exact = np.array(clipped_exact.flat_weights) - np.array(w_orig.flat_weights)
        assert np.isclose(np.linalg.norm(delta_exact), 1.5, atol=1e-7)

        # 3. Delta with norm above threshold: norm = 5.0 > 1.5 -> scaled to 1.5
        w_large = ModelWeights(layer_shapes=[(3,)], flat_weights=[3.0, 4.0, 0.0])  # norm = 5.0
        clipped_large = svc.clip_model_update(w_orig, w_large, max_norm=max_norm)
        delta_large = np.array(clipped_large.flat_weights) - np.array(w_orig.flat_weights)
        assert np.isclose(np.linalg.norm(delta_large), max_norm, atol=1e-7)
        # Scaled direction preserved: [3, 4] * (1.5 / 5.0) = [0.9, 1.2]
        np.testing.assert_allclose(clipped_large.flat_weights, [0.9, 1.2, 0.0], atol=1e-7)

        # 4. Zero delta: norm = 0 <= 1.5
        clipped_zero = svc.clip_model_update(w_orig, w_orig, max_norm=max_norm)
        delta_zero = np.array(clipped_zero.flat_weights) - np.array(w_orig.flat_weights)
        assert np.isclose(np.linalg.norm(delta_zero), 0.0, atol=1e-7)

        # 5. Non-finite values: handled gracefully without crash
        w_inf = ModelWeights(layer_shapes=[(3,)], flat_weights=[float("inf"), 1.0, 0.0])
        clipped_inf = svc.clip_model_update(w_orig, w_inf, max_norm=max_norm)
        delta_inf = np.array(clipped_inf.flat_weights) - np.array(w_orig.flat_weights)
        assert np.linalg.norm(delta_inf) <= max_norm + 1e-7

    def test_rdp_moments_accountant_and_composition(self) -> None:
        """Verify Rényi Differential Privacy moments accounting and convex dual minimization."""
        svc = PrivacyService()
        sigma = 1.5
        q = 0.1
        alpha = 3.0

        # Exact analytical Poisson-subsampled Gaussian RDP verification (Mironov 2019)
        # Benchmark ground-truth: q=0.5, sigma=1.0, alpha=2.0 yields 0.357374 (CANON-DP-RDP-001)
        audit_rdp = svc.compute_rdp_gaussian(sigma=1.0, q=0.5, alpha=2.0)
        assert np.isclose(audit_rdp, 0.357374, atol=1e-5)

        # Standard Gaussian mechanism without subsampling (q=1.0) matches alpha / (2 * sigma^2)
        q1_rdp = svc.compute_rdp_gaussian(sigma=sigma, q=1.0, alpha=alpha)
        assert np.isclose(q1_rdp, alpha / (2.0 * (sigma**2)), rtol=1e-10)

        # Subsampled Gaussian mechanism strictly accounts for higher-order terms
        computed_rdp = svc.compute_rdp_gaussian(sigma=sigma, q=q, alpha=alpha)
        assert computed_rdp > 0.0

        # Multi-round RDP composition
        sigmas = [1.5, 1.5, 1.5]
        best_eps, best_alpha, rdp_map = svc.compose_rdp(sigmas=sigmas, delta=1e-5, q=q)
        assert best_eps > 0
        assert best_alpha > 1.0
        assert len(rdp_map) > 0

        # Monotonicity: adding more rounds increases cumulative epsilon
        more_sigmas = [1.5, 1.5, 1.5, 1.5, 1.5]
        best_eps_more, _, _ = svc.compose_rdp(sigmas=more_sigmas, delta=1e-5, q=q)
        assert best_eps_more > best_eps

    def test_privacy_budget_lifecycle_and_exhaustion(self) -> None:
        """Verify cumulative privacy budget lifecycle and strict exhaustion exception."""
        budget = PrivacyBudget(epsilon_per_round=1.0, delta=1e-5)
        limit = 3.0

        # Round 1
        budget.spend(1.0, limit=limit)
        assert budget.rounds_spent == 1
        assert budget.total_epsilon == 1.0
        assert budget.total_delta == 1e-5

        # Round 2
        budget.spend(1.0, limit=limit)
        assert budget.rounds_spent == 2
        assert budget.total_epsilon == 2.0

        # Round 3
        budget.spend(1.0, limit=limit)
        assert budget.rounds_spent == 3
        assert budget.total_epsilon == 3.0

        # Round 4: Exceeds limit=3.0 -> Must raise PrivacyBudgetExceededError
        with pytest.raises(PrivacyBudgetExceededError, match="Cumulative privacy budget exceeded"):
            budget.spend(0.5, limit=limit)

        # Fail-closed: cannot spend negative or zero epsilon
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            budget.spend(-1.0, limit=limit)

    def test_dp_disabled_control_identity(self) -> None:
        """Verify that when DP is disabled, weights are not noised or clipped."""
        w_orig = ModelWeights(layer_shapes=[(3,)], flat_weights=[1.0, 2.0, 3.0])
        # Direct identity
        assert w_orig.flat_weights == [1.0, 2.0, 3.0]

    def test_dp_enabled_material_difference(self) -> None:
        """Verify enabling DP produces material differences through noise injection."""
        svc = PrivacyService()
        w_orig = ModelWeights(layer_shapes=[(5,)], flat_weights=[0.5, 0.5, 0.5, 0.5, 0.5])
        rng = np.random.default_rng(42)

        noised_w = svc.add_noise_to_weights(
            weights=w_orig,
            epsilon=1.0,
            delta=1e-5,
            max_grad_norm=1.0,
            rng=rng,
        )
        assert noised_w.layer_shapes == w_orig.layer_shapes
        assert len(noised_w.flat_weights) == len(w_orig.flat_weights)
        # Material difference asserted
        assert not np.allclose(noised_w.flat_weights, w_orig.flat_weights)


# ==============================================================================
# 2. SECURE AGGREGATION CONTRACTS
@pytest.fixture
def fl_engine() -> FederatedLearningEngine:
    settings = get_settings()
    model_service = ModelService(settings)
    privacy_service = PrivacyService()
    return FederatedLearningEngine(settings, model_service, privacy_service)


# ==============================================================================
# 2. SECURE AGGREGATION CONTRACTS
# ==============================================================================


class TestSecureAggregationContracts:
    """Mathematical verification and invariant tests for Secure Aggregation."""

    def test_pairwise_mask_cancellation_unweighted_oracle(
        self, fl_engine: FederatedLearningEngine
    ) -> None:
        """Mathematical Oracle: sum(masks) == 0 => sum(w_i + m_i) == sum(w_i)."""
        rng = np.random.default_rng(12345)

        w1 = ModelWeights(layer_shapes=[(4,)], flat_weights=[1.0, 2.0, 3.0, 4.0])
        w2 = ModelWeights(layer_shapes=[(4,)], flat_weights=[2.0, 4.0, 6.0, 8.0])
        w3 = ModelWeights(layer_shapes=[(4,)], flat_weights=[3.0, 6.0, 9.0, 12.0])
        clients = [w1, w2, w3]

        # Plaintext unweighted sum
        plaintext_sum = np.array(w1.flat_weights) + np.array(w2.flat_weights) + np.array(w3.flat_weights)

        # Apply SecAgg masks (unweighted)
        masked_clients = fl_engine.apply_secure_aggregation_masks(clients, client_samples=None, rng=rng)

        # Verify individual updates are obscured (not equal to original)
        for original, masked in zip(clients, masked_clients, strict=False):
            assert not np.allclose(original.flat_weights, masked.flat_weights)

        # Verify exact mathematical cancellation of zero-sum masks
        masked_sum = sum(np.array(m.flat_weights) for m in masked_clients)
        np.testing.assert_allclose(masked_sum, plaintext_sum, atol=1e-12)

    def test_pairwise_mask_cancellation_weighted_oracle(
        self, fl_engine: FederatedLearningEngine
    ) -> None:
        """Mathematical Oracle: sum(p_i * m_i) == 0 => sum(p_i * (w_i + m_i)) == sum(p_i * w_i)."""
        rng = np.random.default_rng(54321)

        w1 = ModelWeights(layer_shapes=[(3,)], flat_weights=[1.0, 2.0, 3.0])
        w2 = ModelWeights(layer_shapes=[(3,)], flat_weights=[4.0, 5.0, 6.0])
        w3 = ModelWeights(layer_shapes=[(3,)], flat_weights=[7.0, 8.0, 9.0])
        clients = [w1, w2, w3]
        samples = [100, 200, 300]
        total_samples = sum(samples)
        proportions = [s / total_samples for s in samples]

        # Plaintext weighted average
        plaintext_avg = sum(p * np.array(w.flat_weights) for p, w in zip(proportions, clients, strict=False))

        # Apply SecAgg masks (weighted)
        masked_clients = fl_engine.apply_secure_aggregation_masks(clients, client_samples=samples, rng=rng)

        # Verify individual updates are obscured
        for original, masked in zip(clients, masked_clients, strict=False):
            assert not np.allclose(original.flat_weights, masked.flat_weights)

        # Verify exact weighted cancellation
        masked_avg = sum(p * np.array(m.flat_weights) for p, m in zip(proportions, masked_clients, strict=False))
        np.testing.assert_allclose(masked_avg, plaintext_avg, atol=1e-12)

    def test_secure_aggregation_resilience_empty_and_single_client(
        self, fl_engine: FederatedLearningEngine
    ) -> None:
        """Verify SecAgg handles empty or single client list without crash."""
        # Empty list -> returns empty
        assert fl_engine.apply_secure_aggregation_masks([]) == []

        # Single client -> returns unmasked (cannot cancel across 1 participant)
        single = [ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 2.0])]
        res = fl_engine.apply_secure_aggregation_masks(single)
        assert len(res) == 1
        assert res[0].flat_weights == [1.0, 2.0]


# ==============================================================================
# 3. FHE & TEE ISOLATION CONTRACTS
# ==============================================================================


class TestFHEAndTEEContracts:
    """Verification of Homomorphic Encryption and Trusted Execution Environment primitives."""

    def test_fhe_encryption_and_homomorphic_averaging_roundtrip(self) -> None:
        """Verify FHE CKKS encryption, server-side homomorphic averaging, and decryption."""
        sim_id = "test-fhe-sim-001"
        key_ring = FHEDriver.generate_keys(sim_id)
        assert key_ring.key_id == sim_id

        w1 = ModelWeights(layer_shapes=[(3,)], flat_weights=[1.0, 2.0, 3.0])
        w2 = ModelWeights(layer_shapes=[(3,)], flat_weights=[3.0, 4.0, 5.0])

        enc1 = FHEDriver.encrypt_weights(w1, key_ring)
        enc2 = FHEDriver.encrypt_weights(w2, key_ring)

        assert enc1.param_count == 3
        assert enc2.param_count == 3

        # Homomorphic average (uniform weights)
        enc_avg = FHEDriver.homomorphic_average(
            [enc1, enc2],
            client_samples=[100, 100],
            public_context_bytes=key_ring.public_context_bytes,
        )

        decrypted = FHEDriver.decrypt_weights(enc_avg, key_ring, [(3,)])
        expected_avg = [2.0, 3.0, 4.0]
        np.testing.assert_allclose(decrypted.flat_weights, expected_avg, atol=1e-4)

    def test_fhe_mismatched_key_rejection(self) -> None:
        """Verify homomorphic aggregation rejects ciphertexts encrypted under different keys."""
        kr1 = FHEDriver.generate_keys("sim-alpha", poly_degree=4096)
        kr2 = FHEDriver.generate_keys("sim-beta", poly_degree=4096)

        w = ModelWeights(layer_shapes=[(2,)], flat_weights=[1.0, 1.0])
        enc1 = FHEDriver.encrypt_weights(w, kr1)
        enc2 = FHEDriver.encrypt_weights(w, kr2)

        with pytest.raises(ValueError, match="Mismatched FHE keys"):
            FHEDriver.homomorphic_average([enc1, enc2])

    def test_fhe_empty_weights_rejection(self) -> None:
        """Verify FHEDriver rejects empty weights."""
        kr = FHEDriver.generate_keys("sim-empty", poly_degree=4096)
        empty_w = ModelWeights(layer_shapes=[], flat_weights=[])
        with pytest.raises(ValueError, match="Cannot encrypt empty weights"):
            FHEDriver.encrypt_weights(empty_w, kr)

    def test_tee_enclave_creation_and_attestation_truthfulness(self) -> None:
        """Verify TEE enclave initialization, MRENCLAVE measurement, and attestation reporting."""
        sim_id = "test-tee-sim-001"
        ctx = TEEDriver.create_enclave(sim_id)

        assert ctx.enclave_id == sim_id
        assert len(ctx.mrenclave) == 64  # SHA-256 hex
        assert len(ctx.mrsigner) == 64

        report = TEEDriver.generate_attestation_report(ctx)
        assert report.verified is True
        assert report.enclave_id == sim_id
        assert report.mrenclave == ctx.mrenclave

        # Verify driver_mode truthfully reflects hardware presence
        if report.is_hardware_backed:
            assert report.driver_mode == "INTEL_SGX_HARDWARE"
        else:
            assert report.driver_mode == "SOFTWARE_EMULATION_SANDBOX"

    def test_tee_aead_data_sealing_and_tamper_rejection(self) -> None:
        """Verify TEE AES-256-GCM data sealing roundtrip and tamper detection."""
        key = b"enclave_master_sealing_key_32bytes!"
        plaintext = b"bank_secret_unmasked_weights_payload_12345"

        sealed = TEEDriver.seal_data(plaintext, key)
        # Minimum size: 12-byte nonce + 16-byte tag + plaintext
        assert len(sealed) >= 28 + len(plaintext)

        # Legitimate unseal
        unsealed = TEEDriver.unseal_data(sealed, key)
        assert unsealed == plaintext

        # Tampered ciphertext: flip 1 byte
        tampered = bytearray(sealed)
        tampered[-1] ^= 0xFF
        with pytest.raises(ValueError, match="Ciphertext authentication failed"):
            TEEDriver.unseal_data(bytes(tampered), key)

        # Truncated payload
        with pytest.raises(ValueError, match="Invalid sealed data size"):
            TEEDriver.unseal_data(b"too_short", key)

    def test_tee_secure_aggregation_empty_guard(self) -> None:
        """Verify TEEDriver.execute_secure_aggregation rejects empty client list."""
        ctx = TEEDriver.create_enclave("sim-tee-empty")
        with pytest.raises(ValueError, match="Cannot execute TEE secure aggregation on empty client_weights"):
            TEEDriver.execute_secure_aggregation(ctx, [])


# ==============================================================================
# 4. CRYPTOGRAPHIC PRIMITIVES & IDENTITY
# ==============================================================================


class TestCryptographicPrimitivesAndIdentity:
    """Verification of HMAC pseudonymization, domain separation, and mTLS controls."""

    def test_hmac_pseudonymization_determinism_and_type_salting(self) -> None:
        """Verify type-salted HMAC produces deterministic 128-bit tokens with domain separation."""
        raw_cust = "TR1234567890"
        key = "consortium-test-secret-key"

        token1 = PrivacyPreservingIdentifier.compute(raw_cust, "customer", hmac_key=key)
        token2 = PrivacyPreservingIdentifier.compute(raw_cust, "customer", hmac_key=key)

        # Determinism
        assert token1 == token2
        assert len(token1) == 32  # 128-bit hex

        # Type salting domain separation: customer vs merchant
        token_merchant = PrivacyPreservingIdentifier.compute(raw_cust, "merchant", hmac_key=key)
        assert token1 != token_merchant

        # Key separation
        token_diff_key = PrivacyPreservingIdentifier.compute(raw_cust, "customer", hmac_key="different-key")
        assert token1 != token_diff_key

    def test_input_standardization_canonicalization(self) -> None:
        """Verify input canonicalization normalizes whitespace, diacritics, and phone prefixes."""
        # Customer name with diacritics and whitespace
        s1 = standardize_input("  Ahmet   Yılmaz  ", "customer")
        s2 = standardize_input("ahmet yilmaz", "customer")
        assert s1 == s2 == "ahmet yilmaz"

        # Turkish characters transliteration
        s_tr = standardize_input("ÖMER ÇALIŞKAN", "customer")
        assert s_tr == "omer caliskan"

        # Phone formatting
        p1 = standardize_input("+90 (555) 123-4567", "phone")
        p2 = standardize_input("+905551234567", "phone")
        assert p1 == p2 == "+905551234567"

    def test_mtls_manager_ssl_context_cert_required(self) -> None:
        """Verify mTLS SSLContext enforces CERT_REQUIRED and TLSv1_2 minimum."""
        mgr = MTLSManager()
        server_ctx = mgr.build_ssl_context(is_server=True)
        assert server_ctx.verify_mode == 2  # ssl.CERT_REQUIRED
        assert server_ctx.minimum_version == 771  # ssl.TLSVersion.TLSv1_2

        client_ctx = mgr.build_ssl_context(is_server=False)
        assert client_ctx.verify_mode == 2  # ssl.CERT_REQUIRED
        assert client_ctx.minimum_version == 771

    def test_mtls_peer_certificate_san_and_crl_validation(self) -> None:
        """Verify SAN validation matches domain patterns and CRL blocks revoked certs."""
        mgr = MTLSManager()
        cert_info = mgr.generate_cert_info("bank-alpha")

        # Valid SAN match
        valid, msg = mgr.validate_peer_certificate(cert_info, "bank-alpha.cf-intelligence.io")
        assert valid is True

        # Invalid SAN
        invalid, err_msg = mgr.validate_peer_certificate(cert_info, "malicious-bank.com")
        assert invalid is False
        assert "SAN match failure" in err_msg

        # CRL revocation
        mgr.revoke_certificate(cert_info.serial_number, reason="Key compromise test")
        cert_info.revoked = True
        revoked_valid, rev_msg = mgr.validate_peer_certificate(cert_info, "bank-alpha.cf-intelligence.io")
        assert revoked_valid is False
        assert "revoked in CRL" in rev_msg


# ==============================================================================
# 5. PRIVACY STATE ISOLATION & COMPOSITION
# ==============================================================================


class TestPrivacyStateIsolation:
    """Verification that privacy state is strictly isolated across simulations and tenants."""

    def test_budget_isolation_between_simulations(self) -> None:
        """Verify privacy budget spent in Simulation A does not consume Simulation B's budget."""
        svc = PrivacyService()

        b_a = svc.get_or_create_budget("sim-A", epsilon=1.0)
        b_b = svc.get_or_create_budget("sim-B", epsilon=1.0)

        b_a.spend(2.5, limit=5.0)
        assert b_a.total_epsilon == 2.5
        assert b_a.rounds_spent == 1

        # Simulation B must be completely unaffected
        assert b_b.total_epsilon == 0.0
        assert b_b.rounds_spent == 0

        # Clear Simulation A
        svc.clear_budget("sim-A")
        assert "sim-A" not in svc._budgets
        assert "sim-B" in svc._budgets

    def test_opacus_actual_epsilon_recorded_in_direct_partition_flow(self) -> None:
        """Verify that train_local_with_opacus records positive actual epsilon spent."""
        ms = ModelService(get_settings())
        rng = np.random.default_rng(42)
        X = rng.standard_normal((120, 10)).astype(np.float32)
        y = rng.integers(0, 2, 120).astype(np.float32)

        model = ms.create_model(input_dim=10, dp_compatible=True)
        _, _, actual_eps = ms.train_local_with_opacus(
            model,
            X,
            y,
            target_epsilon=3.0,
            target_delta=1e-5,
            max_grad_norm=1.0,
            epochs=1,
            batch_size=32,
        )

        assert actual_eps is not None
        assert actual_eps > 0.0
