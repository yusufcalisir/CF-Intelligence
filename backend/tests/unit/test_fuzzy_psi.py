"""Unit tests for Fuzzy LSH and Multi-attribute Fuzzy PSI."""

from __future__ import annotations

from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.psi_service import PSIService
from app.domain.enums import EntityType
from app.domain.value_objects_investigation import (
    calculate_jaccard_similarity,
    compute_minhash_signature,
    standardize_input,
)


def test_standardization_pipeline() -> None:
    # 1. Names/general text
    assert standardize_input("Yusuf Çalışır", "customer") == "yusuf calisir"
    assert standardize_input("  JANE   sMiTh  ", "customer") == "jane smith"
    assert standardize_input("Özge Çelik", "customer") == "ozge celik"
    assert standardize_input("Hüseyin Şen", "customer") == "huseyin sen"

    # 2. Phone numbers (E.164 normalization)
    assert standardize_input("+90 555-123-4567", "phone") == "+905551234567"
    assert standardize_input("0555 123 45 67", "phone") == "05551234567"
    assert standardize_input("  +1 (555) 123-4567 ", "phone") == "+15551234567"

    # 3. Emails
    assert standardize_input("  User.Name+Label@Mail.com  ", "email") == "user.name+label@mail.com"


def test_minhash_lsh() -> None:
    # Test character 3-gram MinHash similarity bounds
    sig1 = compute_minhash_signature("yusuf calisir")
    sig2 = compute_minhash_signature("yusuf calisir")
    sig3 = compute_minhash_signature("yusuf calisr")  # typo: missing 'i'
    sig4 = compute_minhash_signature("jane smith")  # completely different

    assert len(sig1) == 16
    assert calculate_jaccard_similarity(sig1, sig2) == 1.0

    sim_close = calculate_jaccard_similarity(sig1, sig3)
    sim_diff = calculate_jaccard_similarity(sig1, sig4)

    # Close name should have high similarity, completely different should have low
    assert sim_close > 0.3
    assert sim_diff < 0.2


def test_fuzzy_psi_protocol() -> None:
    entity_service = EntityResolutionService()
    psi_service = PSIService(entity_service)

    # Clear existing entries
    entity_service._entities.clear()
    entity_service._hash_index.clear()

    # Create entity A: 5 attributes present
    # Attributes: Phone, Email, Device ID, Birthdate, Surname
    attributes_a = {
        "phone": "+905551234567",
        "email": "yusuf@mail.com",
        "device_id": "device_123",
        "birthdate": "1990-01-01",
        "surname": "Calisir",
    }
    entity_service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="Yusuf Calisir",
        bank_id="bank_a",
        attributes=attributes_a,
    )

    # Create entity B: 3 matching attributes, 2 mismatching attributes
    attributes_b = {
        "phone": "+905551234567",  # match
        "email": "yusuf@mail.com",  # match
        "device_id": "device_999",  # mismatch
        "birthdate": "1990-01-01",  # match
        "surname": "Smith",  # mismatch
    }
    entity_service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="Yusuf Smith",
        bank_id="bank_b",
        attributes=attributes_b,
    )

    # Create entity C: only 2 matching attributes (phone, email)
    attributes_c = {
        "phone": "+905551234567",  # match
        "email": "yusuf@mail.com",  # match
        "device_id": "device_888",  # mismatch
        "birthdate": "1995-12-12",  # mismatch
        "surname": "Jones",  # mismatch
    }
    entity_service.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier="Yusuf Jones",
        bank_id="bank_b",
        attributes=attributes_c,
    )

    # Case 1: Fuzzy threshold = 3. Should match Entity A and Entity B (3 match), but NOT Entity A and Entity C (2 match).
    result_t3 = psi_service.run_psi(
        "bank_a", "bank_b", EntityType.CUSTOMER, enable_fuzzy=True, fuzzy_threshold=3
    )
    matches_t3 = result_t3["matches"]
    assert len(matches_t3) == 1
    assert "phone" in matches_t3[0]["matched_attributes"]
    assert "email" in matches_t3[0]["matched_attributes"]
    assert "birthdate" in matches_t3[0]["matched_attributes"]

    # Case 2: Fuzzy threshold = 2. Should match Entity A with both Entity B and Entity C.
    result_t2 = psi_service.run_psi(
        "bank_a", "bank_b", EntityType.CUSTOMER, enable_fuzzy=True, fuzzy_threshold=2
    )
    matches_t2 = result_t2["matches"]
    assert len(matches_t2) == 2
