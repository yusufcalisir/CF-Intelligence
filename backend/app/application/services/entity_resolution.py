"""Entity resolution service.

Creates and resolves entities across institutions using deterministic
hashing. Entities represent real-world objects (customers, merchants,
devices, etc.) identified by HMAC-SHA256 hashes rather than raw PII.

The key insight: the same raw identifier at different banks produces
the same hash, enabling entity matching without exposing the underlying
private information. In production, the HMAC key would be managed by
a trusted third party or derived from MPC.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any, cast

from app.domain.entities_phase2 import Entity, Relationship
from app.domain.enums import EntityType, RelationshipType, RiskLevel
from app.domain.fuzzy_psi import FuzzyPSIMatcher
from app.domain.value_objects_phase2 import (
    PrivacyPreservingIdentifier,
    calculate_jaccard_similarity,
    compute_minhash_signature,
    standardize_input,
)
from app.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


def _entity_to_dict(e: Entity) -> dict[str, Any]:
    return {
        "id": e.id,
        "entity_type": e.entity_type.value,
        "privacy_id": e.privacy_id,
        "bank_id": e.bank_id,
        "display_label": e.display_label,
        "attributes": e.attributes,
        "risk_level": e.risk_level.value,
        "alert_count": e.alert_count,
        "first_seen": e.first_seen.isoformat(),
        "last_seen": e.last_seen.isoformat(),
    }


def _dict_to_entity(d: dict[str, Any]) -> Entity:
    from datetime import datetime

    d_copy = d.copy()
    d_copy["entity_type"] = EntityType(d_copy["entity_type"])
    d_copy["risk_level"] = RiskLevel(d_copy["risk_level"])
    if isinstance(d_copy.get("first_seen"), str):
        d_copy["first_seen"] = datetime.fromisoformat(d_copy["first_seen"])
    if isinstance(d_copy.get("last_seen"), str):
        d_copy["last_seen"] = datetime.fromisoformat(d_copy["last_seen"])
    return Entity(**d_copy)


def _relationship_to_dict(r: Relationship) -> dict[str, Any]:
    return {
        "id": r.id,
        "source_entity_id": r.source_entity_id,
        "target_entity_id": r.target_entity_id,
        "relationship_type": r.relationship_type.value,
        "confidence": r.confidence,
        "evidence": r.evidence,
        "created_at": r.created_at.isoformat(),
    }


def _dict_to_relationship(d: dict[str, Any]) -> Relationship:
    from datetime import datetime

    d_copy = d.copy()
    d_copy["relationship_type"] = RelationshipType(d_copy["relationship_type"])
    d_copy["created_at"] = datetime.fromisoformat(d_copy["created_at"])
    return Relationship(**d_copy)


class EntityResolutionService:
    """Manages privacy-preserving entity resolution across institutions.

    Entities are identified by deterministic hashes. The same underlying
    individual at two different banks will have the same privacy_id,
    enabling cross-institution correlation without PII exposure.
    """

    def __init__(self, num_hashes: int = 16, num_bands: int = 16) -> None:
        self._lock = threading.RLock()
        self._entities = RedisStore("entity")
        self._relationships = RedisStore("relationship")
        self._hash_index = RedisStore("hash_index")
        self._fuzzy_matcher = FuzzyPSIMatcher(
            num_hashes=num_hashes, num_bands=num_bands
        )

    def create_entity(
        self,
        entity_type: EntityType,
        raw_identifier: str,
        bank_id: str,
        attributes: dict | None = None,
    ) -> Entity:
        """Create a privacy-preserving entity.

        The raw identifier is hashed immediately. The hash is stored,
        the raw value is discarded. The entity's display_label is a
        short, non-PII label for UI display.
        """
        attributes_copy = (attributes or {}).copy()
        standardized = standardize_input(raw_identifier, entity_type.value)

        # If customer or merchant, calculate MinHash signature for fuzzy entity resolution
        if entity_type in (EntityType.CUSTOMER, EntityType.MERCHANT):
            attributes_copy["minhash_signature"] = compute_minhash_signature(
                standardized, num_hashes=self._fuzzy_matcher.num_hashes
            )
            attributes_copy["raw_standardized"] = standardized

        privacy_id = PrivacyPreservingIdentifier.compute(raw_identifier, entity_type.value)

        with self._lock:
            # Check if we already have this entity for this bank
            existing = self._find_entity(privacy_id, bank_id)
            if existing:
                existing.last_seen = datetime.now(UTC)
                self._entities.set(existing.id, _entity_to_dict(existing))
                if entity_type in (EntityType.CUSTOMER, EntityType.MERCHANT):
                    self._fuzzy_matcher.index_entity(existing.id, bank_id, standardized)
                return existing

            # Generate a short display label
            type_prefix = {
                EntityType.CUSTOMER: "CUST",
                EntityType.MERCHANT: "MERCH",
                EntityType.DEVICE: "DEV",
                EntityType.CARD: "CARD",
                EntityType.EMAIL: "EMAIL",
                EntityType.PHONE: "PHONE",
                EntityType.IP_ADDRESS: "IP",
            }
            prefix = type_prefix.get(entity_type, "ENT")
            display_label = f"{prefix}-{privacy_id[:6]}"

            entity = Entity(
                entity_type=entity_type,
                privacy_id=privacy_id,
                bank_id=bank_id,
                display_label=display_label,
                attributes=attributes_copy,
            )

            self._entities.set(entity.id, _entity_to_dict(entity))

            val = self._hash_index.get(privacy_id)
            data = cast("dict", val) if val is not None else {"ids": []}
            entity_ids = data.setdefault("ids", [])
            entity_ids.append(entity.id)
            self._hash_index.set(privacy_id, data)

            if entity_type in (EntityType.CUSTOMER, EntityType.MERCHANT):
                self._fuzzy_matcher.index_entity(entity.id, bank_id, standardized)

            return entity

    def resolve_cross_institution(self, privacy_hash: str) -> list[Entity]:
        """Find matching entities across all banks.

        Returns all entities that share the same privacy hash,
        indicating they represent the same real-world entity at
        different institutions.
        """
        val = self._hash_index.get(privacy_hash)
        data = cast("dict", val) if val is not None else {"ids": []}
        entity_ids = data.get("ids", [])
        entities = []
        for eid in entity_ids:
            val = self._entities.get(eid)
            if val:
                entities.append(_dict_to_entity(val))

        if len(entities) > 1:
            banks = set(e.bank_id for e in entities)
            logger.info(
                "Cross-institution match: hash=%s found at %d banks: %s",
                privacy_hash[:8],
                len(banks),
                banks,
            )

        return entities

    def detect_shared_entities(
        self,
        bank_a_id: str,
        bank_b_id: str,
    ) -> list[dict]:
        """Find entities that appear at both institutions.

        Returns a list of matches with the shared privacy hash and
        entity details from each bank.
        """
        raw_entities = [_dict_to_entity(v) for v in self._entities.list_values()]
        bank_a_hashes = {e.privacy_id: e for e in raw_entities if e.bank_id == bank_a_id}
        bank_b_hashes = {e.privacy_id: e for e in raw_entities if e.bank_id == bank_b_id}

        shared_hashes = set(bank_a_hashes.keys()) & set(bank_b_hashes.keys())
        matches = []
        for h in shared_hashes:
            matches.append(
                {
                    "privacy_hash": h,
                    "entity_type": bank_a_hashes[h].entity_type.value,
                    "bank_a_entity_id": bank_a_hashes[h].id,
                    "bank_b_entity_id": bank_b_hashes[h].id,
                    "bank_a_risk": bank_a_hashes[h].risk_level.value,
                    "bank_b_risk": bank_b_hashes[h].risk_level.value,
                }
            )

        logger.info(
            "Found %d shared entities between %s and %s",
            len(matches),
            bank_a_id,
            bank_b_id,
        )
        return matches

    def build_entity_profile(self, entity_id: str) -> dict:
        """Build a risk profile for an entity.

        Aggregates alert count, relationship count, cross-institution
        presence, and risk factors.
        """
        entity_val = self._entities.get(entity_id)
        if not entity_val:
            raise ValueError(f"Entity not found: {entity_id}")
        entity = _dict_to_entity(entity_val)

        # Find all related entities via relationships
        raw_relationships = [_dict_to_relationship(r) for r in self._relationships.list_values()]
        relationships = [
            r
            for r in raw_relationships
            if r.source_entity_id == entity_id or r.target_entity_id == entity_id
        ]

        # Cross-institution presence
        cross_matches = self.resolve_cross_institution(entity.privacy_id)
        banks_present = list(set(e.bank_id for e in cross_matches))

        return {
            "entity_id": entity.id,
            "entity_type": entity.entity_type.value,
            "privacy_id": entity.privacy_id,
            "display_label": entity.display_label,
            "bank_id": entity.bank_id,
            "risk_level": entity.risk_level.value,
            "alert_count": entity.alert_count,
            "relationship_count": len(relationships),
            "cross_institution_count": len(banks_present),
            "banks_present": banks_present,
            "first_seen": entity.first_seen.isoformat(),
            "last_seen": entity.last_seen.isoformat(),
            "attributes": entity.attributes,
        }

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship_type: RelationshipType,
        confidence: float = 1.0,
        evidence: list[str] | None = None,
    ) -> Relationship:
        """Create a relationship between two entities."""
        rel = Relationship(
            source_entity_id=source_id,
            target_entity_id=target_id,
            relationship_type=relationship_type,
            confidence=confidence,
            evidence=evidence or [],
        )
        with self._lock:
            self._relationships.set(rel.id, _relationship_to_dict(rel))
        return rel

    def update_risk_level(self, entity_id: str, risk_level: RiskLevel) -> None:
        with self._lock:
            entity = self.get_entity(entity_id)
            if entity:
                entity.risk_level = risk_level
                self._entities.set(entity.id, _entity_to_dict(entity))

    def increment_alert_count(self, entity_id: str) -> None:
        with self._lock:
            entity = self.get_entity(entity_id)
            if entity:
                entity.alert_count += 1
                self._entities.set(entity.id, _entity_to_dict(entity))

    def delete_entity(self, entity_id: str) -> bool:
        """Purges an entity and removes it from LSH indices (GDPR Art. 17 right-to-erasure)."""
        with self._lock:
            val = self._entities.get(entity_id)
            if not val:
                return False
            entity = _dict_to_entity(val)

            # 1. Remove from primary store
            self._entities.delete(entity_id)

            # 2. Remove from hash_index
            hval = self._hash_index.get(entity.privacy_id)
            if hval and isinstance(hval, dict):
                ids = [i for i in hval.get("ids", []) if i != entity_id]
                if ids:
                    hval["ids"] = ids
                    self._hash_index.set(entity.privacy_id, hval)
                else:
                    self._hash_index.delete(entity.privacy_id)

            # 3. Remove from LSH fuzzy matcher
            self._fuzzy_matcher.remove_entity(entity_id)

            # 4. Remove associated relationships
            for rel in self.get_relationships():
                if rel.source_entity_id == entity_id or rel.target_entity_id == entity_id:
                    self._relationships.delete(rel.id)

            return True

    def clear_all(self) -> None:
        """Clears all entities, relationships, hash indexes, and LSH matcher buckets."""
        with self._lock:
            self._entities.clear()
            self._relationships.clear()
            self._hash_index.clear()
            self._fuzzy_matcher.clear()

    def get_entity(self, entity_id: str) -> Entity | None:
        val = self._entities.get(entity_id)
        return _dict_to_entity(val) if val else None

    def get_entities(
        self,
        entity_type: EntityType | None = None,
        bank_id: str | None = None,
        risk_level: RiskLevel | None = None,
        limit: int = 50,
    ) -> list[Entity]:
        raw_vals = self._entities.list_values()
        entities = [_dict_to_entity(v) for v in raw_vals]
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]
        if bank_id:
            entities = [e for e in entities if e.bank_id == bank_id]
        if risk_level:
            entities = [e for e in entities if e.risk_level == risk_level]
        return sorted(entities, key=lambda e: e.alert_count, reverse=True)[:limit]

    def get_relationships(self) -> list[Relationship]:
        raw_vals = self._relationships.list_values()
        return [_dict_to_relationship(v) for v in raw_vals]

    # ── Private helpers ────────────────────────

    def _find_entity(self, privacy_id: str, bank_id: str) -> Entity | None:
        val = self._hash_index.get(privacy_id)
        data = cast("dict", val) if val is not None else {"ids": []}
        entity_ids = data.get("ids", [])
        for eid in entity_ids:
            entity = self.get_entity(eid)
            if entity and entity.bank_id == bank_id:
                return entity
        return None

    def resolve_fuzzy_entities(
        self,
        query_name: str,
        entity_type: EntityType = EntityType.CUSTOMER,
        threshold: float = 0.70,
        bank_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find entities matching a raw name fuzzily using MinHash LSH candidate pruning.

        Computes the MinHash signature of the query_name, uses LSH buckets
        to retrieve candidate entities (avoiding full table scans),
        calculates Jaccard similarity, and returns matching entities.
        """
        if not query_name or not query_name.strip():
            return []

        threshold = max(0.0, min(1.0, float(threshold)))
        limit = max(1, min(100, int(limit)))

        standardized_query = standardize_input(query_name, entity_type.value)
        query_sig = compute_minhash_signature(
            standardized_query, num_hashes=self._fuzzy_matcher.num_hashes
        )

        with self._lock:
            # 1. First attempt candidate retrieval via LSH band index
            candidate_ids = self._fuzzy_matcher.get_candidate_ids_for_text(standardized_query)

            candidates: list[Entity] = []
            if candidate_ids:
                for cid in candidate_ids:
                    ent = self.get_entity(cid)
                    if ent:
                        candidates.append(ent)
            else:
                # Fallback for entities populated out-of-band without LSH warming
                raw_entities = [_dict_to_entity(v) for v in self._entities.list_values()]
                candidates = raw_entities

            results: list[dict[str, Any]] = []
            for e in candidates:
                if e.entity_type != entity_type:
                    continue
                if bank_id and e.bank_id != bank_id:
                    continue
                sig = e.attributes.get("minhash_signature")
                if not sig:
                    continue
                sim = calculate_jaccard_similarity(query_sig, sig)
                if sim >= threshold:
                    results.append({"entity": e, "similarity_score": round(sim, 2)})

            results.sort(key=lambda x: float(x["similarity_score"]), reverse=True)
            return results[:limit]
