"""Tests for entity resolution service."""

import pytest

from app.application.services.entity_resolution import EntityResolutionService
from app.domain.enums import EntityType, RelationshipType
from app.domain.value_objects_phase2 import PrivacyPreservingIdentifier


@pytest.fixture
def entity_service() -> EntityResolutionService:
    return EntityResolutionService()


class TestEntityCreation:
    def test_create_entity(self, entity_service: EntityResolutionService) -> None:
        entity = entity_service.create_entity(
            entity_type=EntityType.CUSTOMER,
            raw_identifier="john.doe@email.com",
            bank_id="bank_a",
        )
        assert entity.entity_type == EntityType.CUSTOMER
        assert entity.bank_id == "bank_a"
        assert entity.display_label.startswith("CUST-")
        assert len(entity.privacy_id) == 32

    def test_deterministic_hashing(self, entity_service: EntityResolutionService) -> None:
        entity1 = entity_service.create_entity(EntityType.CUSTOMER, "same_id", "bank_a")
        # Creating again with same raw ID + bank should return existing entity
        entity2 = entity_service.create_entity(EntityType.CUSTOMER, "same_id", "bank_a")
        assert entity1.id == entity2.id

    def test_display_label_prefix(self, entity_service: EntityResolutionService) -> None:
        cust = entity_service.create_entity(EntityType.CUSTOMER, "c1", "bank_a")
        merch = entity_service.create_entity(EntityType.MERCHANT, "m1", "bank_a")
        dev = entity_service.create_entity(EntityType.DEVICE, "d1", "bank_a")
        assert cust.display_label.startswith("CUST-")
        assert merch.display_label.startswith("MERCH-")
        assert dev.display_label.startswith("DEV-")


class TestCrossInstitutionResolution:
    def test_same_entity_at_different_banks(self, entity_service: EntityResolutionService) -> None:
        # Same raw identifier at two different banks
        e1 = entity_service.create_entity(EntityType.CUSTOMER, "shared_customer", "bank_a")
        e2 = entity_service.create_entity(EntityType.CUSTOMER, "shared_customer", "bank_b")

        # Same privacy hash
        assert e1.privacy_id == e2.privacy_id
        # Different entity IDs (they're different records at different banks)
        assert e1.id != e2.id

        # Cross-institution resolution should find both
        matches = entity_service.resolve_cross_institution(e1.privacy_id)
        assert len(matches) == 2
        banks = {m.bank_id for m in matches}
        assert banks == {"bank_a", "bank_b"}

    def test_detect_shared_entities(self, entity_service: EntityResolutionService) -> None:
        entity_service.create_entity(EntityType.CUSTOMER, "shared_1", "bank_a")
        entity_service.create_entity(EntityType.CUSTOMER, "shared_1", "bank_b")
        entity_service.create_entity(EntityType.CUSTOMER, "unique_a", "bank_a")
        entity_service.create_entity(EntityType.CUSTOMER, "unique_b", "bank_b")

        matches = entity_service.detect_shared_entities("bank_a", "bank_b")
        assert len(matches) == 1
        assert matches[0]["entity_type"] == "customer"


class TestEntityProfile:
    def test_build_profile(self, entity_service: EntityResolutionService) -> None:
        entity = entity_service.create_entity(EntityType.CUSTOMER, "profile_test", "bank_a")
        profile = entity_service.build_entity_profile(entity.id)
        assert profile["entity_id"] == entity.id
        assert profile["entity_type"] == "customer"
        assert "banks_present" in profile

    def test_profile_nonexistent_raises(self, entity_service: EntityResolutionService) -> None:
        with pytest.raises(ValueError, match="Entity not found"):
            entity_service.build_entity_profile("nonexistent")


class TestRelationships:
    def test_add_relationship(self, entity_service: EntityResolutionService) -> None:
        e1 = entity_service.create_entity(EntityType.CUSTOMER, "c1", "bank_a")
        e2 = entity_service.create_entity(EntityType.MERCHANT, "m1", "bank_a")
        rel = entity_service.add_relationship(
            e1.id,
            e2.id,
            RelationshipType.TRANSACTS_WITH,
        )
        assert rel.source_entity_id == e1.id
        assert rel.target_entity_id == e2.id

    def test_relationship_appears_in_profile(self, entity_service: EntityResolutionService) -> None:
        e1 = entity_service.create_entity(EntityType.CUSTOMER, "c1", "bank_a")
        e2 = entity_service.create_entity(EntityType.DEVICE, "d1", "bank_a")
        entity_service.add_relationship(e1.id, e2.id, RelationshipType.USES)

        profile = entity_service.build_entity_profile(e1.id)
        assert profile["relationship_count"] == 1


class TestPrivacyPreservingIdentifier:
    def test_deterministic(self) -> None:
        h1 = PrivacyPreservingIdentifier.compute("test@email.com", "customer")
        h2 = PrivacyPreservingIdentifier.compute("test@email.com", "customer")
        assert h1 == h2

    def test_case_insensitive(self) -> None:
        h1 = PrivacyPreservingIdentifier.compute("Test@Email.com", "customer")
        h2 = PrivacyPreservingIdentifier.compute("test@email.com", "customer")
        assert h1 == h2

    def test_different_types_different_hashes(self) -> None:
        h1 = PrivacyPreservingIdentifier.compute("value", "customer")
        h2 = PrivacyPreservingIdentifier.compute("value", "merchant")
        assert h1 != h2

    def test_hash_length(self) -> None:
        h = PrivacyPreservingIdentifier.compute("test", "customer")
        assert len(h) == 32
