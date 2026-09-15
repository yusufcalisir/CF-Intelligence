"""Hardening tests for Diffie-Hellman Private Set Intersection (DH-PSI 2048-bit).

Verifies:
1. Commutative modular exponentiation: (H(x)^a)^b mod p == (H(x)^b)^a mod p
2. Secure hash_to_group mapping into Z_p^* [2, p - 2] for arbitrary hex, non-hex, bytes, and UUIDs
3. Zero-Mock invariant in fuzzy feature extraction
4. Non-hex entity ID handling without ValueError crashes
5. Direct client-side blinded hash matching (run_psi_direct / POST /api/v1/psi/match)
6. Pure domain DiffieHellmanPSIProtocol entity operations
7. Input validation and fail-fast behavior
8. Thread concurrency safety under concurrent execution
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import secrets
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.kms_service import get_kms_service
from app.application.services.psi_service import PSIService
from app.domain.entities_phase2 import Entity
from app.domain.enums import EntityType, RiskLevel
from app.domain.psi_service import (
    PRIME_BIT_LENGTH,
    PSI_PRIME,
    DiffieHellmanPSIProtocol,
    blind_element,
    double_blind_element,
    extract_fuzzy_features,
    hash_to_group,
    verify_commutativity,
)
from app.main import app


@pytest.fixture
def entity_service() -> EntityResolutionService:
    service = EntityResolutionService()
    service._entities.clear()
    service._hash_index.clear()
    return service


@pytest.fixture
def psi_service(entity_service: EntityResolutionService) -> PSIService:
    return PSIService(entity_service)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestDHPSICryptographicHardening:
    def test_dh_psi_commutative_law_rigorous(self) -> None:
        """Verify (H(x)^a)^b mod p == (H(x)^b)^a mod p for arbitrary items and keys."""
        test_items = [
            "account_TR990001",
            "IBAN_DE89370400440532013000",
            "user_random_string_xyz_12345",
            "non-hex-with-dashes-uuid-550e8400",
        ]
        for item in test_items:
            key_a = secrets.randbelow(PSI_PRIME - 2) + 2
            key_b = secrets.randbelow(PSI_PRIME - 2) + 2

            assert verify_commutativity(item, key_a, key_b, prime=PSI_PRIME) is True

            elem_int = hash_to_group(item, prime=PSI_PRIME)
            assert 2 <= elem_int <= PSI_PRIME - 2

            # Single-party blinding
            blind_a = blind_element(elem_int, key_a, prime=PSI_PRIME)
            blind_b = blind_element(elem_int, key_b, prime=PSI_PRIME)

            # Double blinding
            double_a_b = double_blind_element(blind_a, key_b, prime=PSI_PRIME)
            double_b_a = double_blind_element(blind_b, key_a, prime=PSI_PRIME)

            assert double_a_b == double_b_a

    def test_hash_to_group_invariants(self) -> None:
        """Verify hash_to_group produces valid group elements for various input types."""
        # 1. Valid hex string
        hex_str = hashlib.sha256(b"hex_sample").hexdigest()
        g_hex = hash_to_group(hex_str)
        assert 2 <= g_hex <= PSI_PRIME - 2

        # 2. Non-hex arbitrary text
        non_hex = "this is not hex! @#$%^&*()_+="
        g_non_hex = hash_to_group(non_hex)
        assert 2 <= g_non_hex <= PSI_PRIME - 2

        # 3. Raw bytes
        raw_bytes = b"\x00\xff\xfe\x01\x42"
        g_bytes = hash_to_group(raw_bytes)
        assert 2 <= g_bytes <= PSI_PRIME - 2

        # 4. Empty string
        g_empty = hash_to_group("")
        assert 2 <= g_empty <= PSI_PRIME - 2

        # 5. Determinism: same input yields identical element
        assert hash_to_group(non_hex) == g_non_hex
        assert hash_to_group(hex_str) == g_hex

        # 6. Invalid exponents rejection
        with pytest.raises(ValueError, match="strictly greater than 1"):
            blind_element(g_hex, 1)
        with pytest.raises(ValueError, match="strictly greater than 1"):
            double_blind_element(g_hex, 0)


class TestZeroMockFuzzyExtraction:
    def test_extract_fuzzy_features_zero_mock(self) -> None:
        """Zero-Mock Invariant: When attributes are missing, never fabricate fake mocks."""
        entity = Entity(
            id="cust_001",
            entity_type=EntityType.CUSTOMER,
            privacy_id="abcdef1234567890abcdef1234567890",
            bank_id="bank_alpha",
            display_label="CUST-001",
            attributes={},  # Completely empty attributes
            risk_level=RiskLevel.LOW,
        )

        features = extract_fuzzy_features(entity)

        # Must be empty strings, NOT synthesized "+1555...", "user_...", or static surnames!
        assert features["phone"] == ""
        assert features["email"] == ""
        assert features["device_id"] == ""
        assert features["birthdate"] == ""
        assert features["surname"] == ""

    def test_extract_fuzzy_features_with_real_attributes(self) -> None:
        """Verify real attributes are extracted without distortion."""
        real_attrs = {
            "phone": "+905559876543",
            "email": "real.user@bank.com",
            "device_id": "device_xyz_999",
            "birthdate": "1988-05-14",
            "surname": "Calisir",
        }
        entity = Entity(
            id="cust_002",
            entity_type=EntityType.CUSTOMER,
            privacy_id="123456abcdef123456abcdef123456ab",
            bank_id="bank_beta",
            display_label="CUST-002",
            attributes=real_attrs,
            risk_level=RiskLevel.MEDIUM,
        )

        features = extract_fuzzy_features(entity)
        for key, val in real_attrs.items():
            assert features[key] == val


class TestPSIServiceHardening:
    def test_psi_service_non_hex_entities_graceful_handling(
        self,
        entity_service: EntityResolutionService,
        psi_service: PSIService,
    ) -> None:
        """Verify entities with non-hex privacy IDs execute without ValueError crashes."""
        # Manually create entity with non-hex privacy_id
        non_hex_id_a = "UUID-CUSTOM-DASHED-IDENTIFIER-BANK-A"
        non_hex_id_b = "UUID-CUSTOM-DASHED-IDENTIFIER-BANK-A"  # Match

        ent_a = Entity(
            id="ent_a_1",
            entity_type=EntityType.CUSTOMER,
            privacy_id=non_hex_id_a,
            bank_id="bank_alpha",
            display_label="CUST-ALPHA-1",
            attributes={"phone": "+12345"},
            risk_level=RiskLevel.LOW,
        )
        ent_b = Entity(
            id="ent_b_1",
            entity_type=EntityType.CUSTOMER,
            privacy_id=non_hex_id_b,
            bank_id="bank_beta",
            display_label="CUST-BETA-1",
            attributes={"phone": "+12345"},
            risk_level=RiskLevel.LOW,
        )
        entity_service._entities.set(ent_a.id, ent_a.__dict__)
        entity_service._entities.set(ent_b.id, ent_b.__dict__)

        result = psi_service.run_psi("bank_alpha", "bank_beta", EntityType.CUSTOMER)
        assert len(result["matches"]) == 1
        assert result["matches"][0]["privacy_hash"] == non_hex_id_a

    def test_run_psi_direct_with_client_blinded_hashes(
        self,
        entity_service: EntityResolutionService,
        psi_service: PSIService,
    ) -> None:
        """Verify client-server DH-PSI direct matching using client blinded hashes H(x)^a."""
        kms = get_kms_service()
        key_a = kms.get_psi_private_exponent("bank_alpha")

        # Bank Beta (target) has 2 entities in DB
        raw_match = "shared.customer@consortium.org"
        raw_non_match_beta = "only.beta@consortium.org"

        ent_match = entity_service.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier=raw_match,
            bank_id="bank_beta",
        )
        entity_service.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier=raw_non_match_beta,
            bank_id="bank_beta",
        )

        # Bank Alpha (client) has matching entity and blinds it with key_a: H(x)^a mod p
        blinded_match = hex(pow(hash_to_group(ent_match.privacy_id), key_a, PSI_PRIME))
        blinded_distinct = hex(pow(hash_to_group("only.alpha.privacy.id"), key_a, PSI_PRIME))

        client_blinded_hashes = [blinded_match, blinded_distinct]

        # Call run_psi_direct
        result = psi_service.run_psi_direct(
            source_bank_id="bank_alpha",
            target_bank_id="bank_beta",
            client_ecdh_blinded_hashes=client_blinded_hashes,
        )

        assert result["stats"]["matched_cardinality"] == 1
        assert len(result["matches"]) == 1
        assert result["matches"][0]["privacy_hash"] == ent_match.privacy_id

    def test_psi_service_input_validation(self, psi_service: PSIService) -> None:
        """Verify empty bank IDs raise ValueError fail-fast."""
        with pytest.raises(ValueError, match="non-empty strings"):
            psi_service.run_psi("", "bank_beta")
        with pytest.raises(ValueError, match="non-empty strings"):
            psi_service.run_psi("bank_alpha", "")
        with pytest.raises(ValueError, match="non-empty strings"):
            psi_service.run_psi_direct("", "bank_beta", ["0x123"])

    def test_psi_service_thread_concurrency(
        self,
        entity_service: EntityResolutionService,
        psi_service: PSIService,
    ) -> None:
        """Verify thread safety under concurrent PSI executions."""
        entity_service.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="concurrent.user@mail.com",
            bank_id="bank_alpha",
        )
        entity_service.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="concurrent.user@mail.com",
            bank_id="bank_beta",
        )

        def worker() -> dict[str, Any]:
            return psi_service.run_psi("bank_alpha", "bank_beta", EntityType.CUSTOMER)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker) for _ in range(16)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 16
        for res in results:
            assert len(res["matches"]) == 1
            assert res["stats"]["prime_bit_length"] == PRIME_BIT_LENGTH


class TestDHPSIProtocolDomainEntity:
    def test_protocol_blind_and_intersect(self) -> None:
        """Verify DiffieHellmanPSIProtocol methods directly."""
        protocol = DiffieHellmanPSIProtocol(prime=PSI_PRIME)
        key_a = 0x11112222333344445555666677778888
        key_b = 0x9999AAAABBBBCCCCDDDDEEEEFFFF0000

        set_a = ["common_elem_1", "common_elem_2", "only_in_a"]
        set_b = ["common_elem_1", "common_elem_2", "only_in_b"]

        # Step 1: Parties blind their own elements
        blinded_a = protocol.blind_elements(set_a, key_a)
        blinded_b = protocol.blind_elements(set_b, key_b)

        # Step 2: Parties double-blind the other party's elements
        double_blinded_a = protocol.double_blind_elements(blinded_a, key_b)
        double_blinded_b = protocol.double_blind_elements(blinded_b, key_a)

        # Step 3: Compute intersection
        intersection = protocol.compute_intersection(double_blinded_a, double_blinded_b)

        assert len(intersection) == 2


class TestPresentationRouterHardening:
    def test_psi_match_direct_router_endpoint(self, client: TestClient) -> None:
        """Verify POST /api/v1/psi/match router accepts blinded hashes and returns protocol confirmation."""
        payload = {
            "source_bank_id": "bank_alpha",
            "target_bank_id": "bank_beta",
            "client_ecdh_blinded_hashes": ["04a1b2c3d4", "04f8e7d6c5"],
            "enable_fuzzy": True,
        }
        resp = client.post("/api/v1/psi/match", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["protocol"] == "Commutative Diffie-Hellman (DH-PSI)"
        assert data["zero_raw_pii_enforced"] is True
        assert "matched_cardinality" in data
        assert "stats" in data
