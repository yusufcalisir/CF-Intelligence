"""Unit tests for Entities, Entity Resolution, MinHash LSH, and DH-PSI API routes.

Verifies dual prefix mounting (/api/v1/entities and /v1/entities, /api/v1/psi and /v1/psi),
RFC-compliant error status codes (200, 400, 403, 404, 422), GDPR Art. 17 right-to-erasure,
Diffie-Hellman 2048-bit Private Set Intersection, MinHash fuzzy matching, and Zero-PII HMAC tokenization.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.entities_phase2 import Entity
from app.domain.enums import EntityType, RiskLevel
from app.main import app
from app.presentation.routers.entities import get_entity_service

client = TestClient(app)


@pytest.fixture(autouse=True)
def seed_test_entities():
    """Seed known entities into the singleton EntityResolutionService for test predictability."""
    from app.application.services.entity_resolution import _entity_to_dict
    from app.domain.fuzzy_psi import compute_minhash_signature

    service = get_entity_service()

    # Clean up and seed test entities
    ent_a1 = Entity(
        id="ent_psi_test_a1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        bank_id="bank_alpha",
        display_label="Alice Test Customer",
        attributes={"email": "alice@bank-alpha.com", "phone": "+1234567890", "device_id": "dev_alice_001", "raw_standardized": "alice test customer"},
        risk_level=RiskLevel.LOW,
    )
    ent_a2 = Entity(
        id="ent_psi_test_a2",
        entity_type=EntityType.CUSTOMER,
        privacy_id="1111222233334444555566667777888811112222333344445555666677778888",
        bank_id="bank_alpha",
        display_label="Suspicious Syndicate Hub",
        attributes={"email": "mulehub@syndicate.org", "phone": "+1999888777", "device_id": "dev_syndicate_001", "raw_standardized": "suspicious syndicate hub"},
        risk_level=RiskLevel.HIGH,
    )
    ent_b1 = Entity(
        id="ent_psi_test_b1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="1111222233334444555566667777888811112222333344445555666677778888",  # Shared intersection with ent_a2
        bank_id="bank_beta",
        display_label="Suspicious Syndicate Recipient",
        attributes={"email": "mulehub@syndicate.org", "phone": "+1999888777", "device_id": "dev_syndicate_001", "raw_standardized": "suspicious syndicate hub"},
        risk_level=RiskLevel.CRITICAL,
    )
    ent_b2 = Entity(
        id="ent_psi_test_b2",
        entity_type=EntityType.CUSTOMER,
        privacy_id="9999888877776666555544443333222299998888777766665555444433332222",
        bank_id="bank_beta",
        display_label="Bob Beta Clean Customer",
        attributes={"email": "bob@bank-beta.com", "phone": "+1888777665", "device_id": "dev_bob_002", "raw_standardized": "bob beta customer"},
        risk_level=RiskLevel.LOW,
    )

    for ent in (ent_a1, ent_a2, ent_b1, ent_b2):
        ent.attributes["minhash_signature"] = compute_minhash_signature(ent.display_label.lower(), num_hashes=16)
        service._entities.set(ent.id, _entity_to_dict(ent))
        val = service._hash_index.get(ent.privacy_id)
        data = val if val is not None else {"ids": []}
        ids = data.setdefault("ids", [])
        if ent.id not in ids:
            ids.append(ent.id)
        service._hash_index.set(ent.privacy_id, data)
        service._fuzzy_matcher.index_entity(ent.id, ent.bank_id, ent.display_label.lower())

    yield


def test_entities_dual_prefix_and_list():
    """Verifies listing entities under both /api/v1/entities and /v1/entities."""
    # 1. Canonical /api/v1/entities
    res1 = client.get("/api/v1/entities", params={"bank_id": "bank_alpha", "limit": 10})
    assert res1.status_code == 200
    data1 = res1.json()
    assert isinstance(data1, list)
    assert len(data1) >= 2
    assert all(e["bank_id"] == "bank_alpha" for e in data1)

    # 2. V1 Alias /v1/entities
    res2 = client.get("/v1/entities", params={"bank_id": "bank_beta", "limit": 10})
    assert res2.status_code == 200
    data2 = res2.json()
    assert isinstance(data2, list)
    assert len(data2) >= 2
    assert all(e["bank_id"] == "bank_beta" for e in data2)

    # 3. Invalid entity_type -> 400 Bad Request
    res_err_type = client.get("/api/v1/entities", params={"entity_type": "invalid_category_xyz"})
    assert res_err_type.status_code == 400
    assert "Invalid entity_type" in res_err_type.json()["detail"]

    # 4. Invalid risk_level -> 400 Bad Request
    res_err_risk = client.get("/api/v1/entities", params={"risk_level": "super_critical_mega"})
    assert res_err_risk.status_code == 400
    assert "Invalid risk_level" in res_err_risk.json()["detail"]

    # 5. Out of bound limit -> 422 Unprocessable Entity
    res_bound = client.get("/api/v1/entities", params={"limit": 500})
    assert res_bound.status_code == 422


def test_entity_profile_and_tenant_isolation():
    """Verifies profile retrieval, not found handling, blank ID validation, and tenant isolation."""
    # 1. Successful profile lookup under /api/v1/entities
    res_prof1 = client.get("/api/v1/entities/ent_psi_test_a1")
    assert res_prof1.status_code == 200
    p1 = res_prof1.json()
    assert p1["entity_id"] == "ent_psi_test_a1"
    assert p1["bank_id"] == "bank_alpha"
    assert "Alice Test Customer" in p1["display_label"]

    # 2. Successful profile lookup under /v1/entities
    res_prof2 = client.get("/v1/entities/ent_psi_test_b1")
    assert res_prof2.status_code == 200
    p2 = res_prof2.json()
    assert p2["entity_id"] == "ent_psi_test_b1"
    assert p2["bank_id"] == "bank_beta"

    # 3. Non-existent entity -> 404 Not Found
    res_not_found = client.get("/api/v1/entities/non_existent_entity_9999")
    assert res_not_found.status_code == 404
    assert "not found" in res_not_found.json()["detail"].lower()

    # 4. Blank entity ID -> 400 Bad Request
    res_blank = client.get("/api/v1/entities/%20")
    assert res_blank.status_code == 400

    # 5. Cross-tenant BOLA enforcement -> 403 Forbidden
    # When caller tenant headers indicate bank_beta, accessing a bank_alpha entity is blocked
    res_bola = client.get(
        "/api/v1/entities/ent_psi_test_a1",
        headers={"X-Tenant-ID": "bank_beta"},
    )
    assert res_bola.status_code == 403
    detail_lower = res_bola.json()["detail"].lower()
    assert "not authorized" in detail_lower or "tenant" in detail_lower


def test_entity_relationships_endpoint():
    """Verifies retrieval of entity graph connections."""
    # 1. Existing entity relationships
    res_rels = client.get("/api/v1/entities/ent_psi_test_a1/relationships")
    assert res_rels.status_code == 200
    assert isinstance(res_rels.json(), list)

    # 2. V1 Alias relationships
    res_rels_v1 = client.get("/v1/entities/ent_psi_test_b1/relationships")
    assert res_rels_v1.status_code == 200
    assert isinstance(res_rels_v1.json(), list)

    # 3. Non-existent entity -> 404 Not Found
    res_404 = client.get("/api/v1/entities/unknown_rel_entity_000/relationships")
    assert res_404.status_code == 404

    # 4. Blank ID -> 400 Bad Request
    res_blank = client.get("/api/v1/entities/%20/relationships")
    assert res_blank.status_code == 400


def test_entity_gdpr_delete():
    """Verifies GDPR Art. 17 Right-to-Erasure endpoint."""
    service = get_entity_service()
    ephemeral = Entity(
        id="ent_gdpr_purge_target",
        entity_type=EntityType.CUSTOMER,
        privacy_id="deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        bank_id="bank_alpha",
        risk_level=RiskLevel.LOW,
    )
    from app.application.services.entity_resolution import _entity_to_dict

    service._entities.set(ephemeral.id, _entity_to_dict(ephemeral))

    # 1. Purge under /api/v1/entities
    res_del = client.delete("/api/v1/entities/ent_gdpr_purge_target")
    assert res_del.status_code == 200
    del_data = res_del.json()
    assert del_data["deleted"] is True
    assert del_data["entity_id"] == "ent_gdpr_purge_target"
    assert "GDPR Art. 17" in del_data["policy"]

    # 2. Second deletion returns 404 Not Found
    res_del2 = client.delete("/api/v1/entities/ent_gdpr_purge_target")
    assert res_del2.status_code == 404

    # 3. Blank ID -> 400 Bad Request
    res_blank = client.delete("/api/v1/entities/%20")
    assert res_blank.status_code == 400


def test_entity_cross_institution_resolve():
    """Verifies resolution of entities across bank institutions by privacy hash."""
    # Resolve the shared privacy_id
    shared_hash = "1111222233334444555566667777888811112222333344445555666677778888"
    res1 = client.post("/api/v1/entities/resolve", json={"privacy_hash": shared_hash})
    assert res1.status_code == 200
    entities1 = res1.json()
    assert isinstance(entities1, list)
    assert len(entities1) >= 2
    banks = {e["bank_id"] for e in entities1}
    assert "bank_alpha" in banks
    assert "bank_beta" in banks

    # V1 Prefix Parity
    res2 = client.post("/v1/entities/resolve", json={"privacy_hash": shared_hash})
    assert res2.status_code == 200
    assert len(res2.json()) == len(entities1)

    # Invalid non-hex privacy hash -> 422 Unprocessable Entity
    res_bad = client.post("/api/v1/entities/resolve", json={"privacy_hash": "not_hex_!@#$%^&*()"})
    assert res_bad.status_code == 422


def test_entity_fuzzy_resolve_minhash():
    """Verifies MinHash LSH fuzzy identity linkage."""
    # Fuzzy resolve with partial/variant name
    res_fuzzy = client.post(
        "/api/v1/entities/fuzzy-resolve",
        json={"query_name": "Alice Test Customer", "threshold": 0.50, "bank_id": "bank_alpha"},
    )
    assert res_fuzzy.status_code == 200
    matches = res_fuzzy.json()["matches"]
    assert isinstance(matches, list)
    assert len(matches) >= 1
    assert matches[0]["similarity_score"] >= 0.50
    assert "Alice" in matches[0]["entity"]["display_label"]

    # V1 Prefix Parity
    res_fuzzy_v1 = client.post(
        "/v1/entities/fuzzy-resolve",
        json={"query_name": "Syndicate Hub", "threshold": 0.30},
    )
    assert res_fuzzy_v1.status_code == 200
    assert len(res_fuzzy_v1.json()["matches"]) >= 1


def test_hmac_tokenize_zero_pii():
    """Verifies Zero-PII type-salted HMAC tokenization endpoint."""
    # 1. Tokenize with JSON payload
    res_json = client.post(
        "/api/v1/entities/hmac-tokenize",
        json={"identifier": "TR330006100519782549101234", "tenant_salt": "bank_alpha_salt"},
    )
    assert res_json.status_code == 200
    t1 = res_json.json()
    assert len(t1["hmac_token"]) == 64
    assert t1["algorithm"] == "HMAC-SHA256"
    assert "Zero Raw PII" in t1["policy"]

    # 2. Tokenize with query parameters (backward compatibility)
    res_query = client.post(
        "/v1/entities/hmac-tokenize",
        params={"identifier": "+905321112233", "tenant_salt": "telecom_salt"},
    )
    assert res_query.status_code == 200
    t2 = res_query.json()
    assert len(t2["hmac_token"]) == 64

    # 3. Empty identifier -> 400 Bad Request or 422 Unprocessable
    res_blank = client.post("/api/v1/entities/hmac-tokenize", json={"identifier": ""})
    assert res_blank.status_code in (400, 422)


def test_entities_psi_simulation():
    """Verifies simulated Diffie-Hellman Private Set Intersection between bank pairs."""
    # 1. Standard exact PSI between bank_alpha and bank_beta
    res_psi = client.post(
        "/api/v1/entities/psi",
        json={
            "bank_a_id": "bank_alpha",
            "bank_b_id": "bank_beta",
            "enable_fuzzy": False,
        },
    )
    assert res_psi.status_code == 200
    psi_data = res_psi.json()
    assert "matches" in psi_data
    assert "stats" in psi_data
    assert psi_data["stats"]["prime_bit_length"] == 2048

    # The shared entity should be matched in the intersection
    matches = psi_data["matches"]
    assert len(matches) >= 1
    matched_hashes = [m["privacy_hash"] for m in matches]
    assert "1111222233334444555566667777888811112222333344445555666677778888" in matched_hashes

    # 2. Fuzzy PSI execution
    res_fuzzy_psi = client.post(
        "/v1/entities/psi",
        json={
            "bank_a_id": "bank_alpha",
            "bank_b_id": "bank_beta",
            "enable_fuzzy": True,
            "fuzzy_threshold": 2,
        },
    )
    assert res_fuzzy_psi.status_code == 200
    assert len(res_fuzzy_psi.json()["matches"]) >= 1

    # 3. Invalid entity_type -> 400 Bad Request
    res_inv = client.post(
        "/api/v1/entities/psi",
        json={"bank_a_id": "bank_alpha", "bank_b_id": "bank_beta", "entity_type": "invalid_xyz"},
    )
    assert res_inv.status_code == 400

    # 4. Missing bank ID -> 422 Unprocessable Entity
    res_missing = client.post("/api/v1/entities/psi", json={"bank_a_id": ""})
    assert res_missing.status_code == 422


def test_psi_router_direct_match_and_stats():
    """Verifies the dedicated /api/v1/psi and /v1/psi router endpoints."""
    # 1. PSI Stats endpoint under /api/v1/psi and /v1/psi
    res_stats1 = client.get("/api/v1/psi/stats")
    assert res_stats1.status_code == 200
    s1 = res_stats1.json()
    assert s1["prime_bit_length"] == 2048
    assert "HMAC-SHA256" in s1["hash_function"]

    res_stats2 = client.get("/v1/psi/stats")
    assert res_stats2.status_code == 200
    assert res_stats2.json()["prime_bit_length"] == 2048

    # 2. DH-PSI Cryptographic Handshake
    res_handshake = client.post("/api/v1/psi/handshake", params={"bank_id": "bank_alpha"})
    assert res_handshake.status_code == 200
    hs = res_handshake.json()
    assert hs["prime_bit_length"] == 2048
    assert hs["generator"] == 2
    assert hs["status"] == "ready_for_blinded_exchange"

    res_hs_blank = client.post("/api/v1/psi/handshake", params={"bank_id": ""})
    assert res_hs_blank.status_code == 400

    # 3. Direct DH-PSI match targeting /api/v1/psi/match
    res_match1 = client.post(
        "/api/v1/psi/match",
        json={"source_bank_id": "bank_alpha", "target_bank_id": "bank_beta", "enable_fuzzy": True},
    )
    assert res_match1.status_code == 200
    m1 = res_match1.json()
    assert m1["zero_raw_pii_enforced"] is True
    assert m1["matched_cardinality"] >= 1

    # 4. Direct DH-PSI match alias targeting /v1/psi/dh
    res_dh = client.post(
        "/v1/psi/dh",
        json={"source_bank_id": "bank_alpha", "target_bank_id": "bank_beta", "enable_fuzzy": False},
    )
    assert res_dh.status_code == 200
    assert res_dh.json()["zero_raw_pii_enforced"] is True

    # 5. Direct DH-PSI match on /api/v1/entities/psi-match
    res_ent_match = client.post(
        "/api/v1/entities/psi-match",
        params={"bank_a_id": "bank_alpha", "bank_b_id": "bank_beta", "enable_fuzzy": True},
    )
    assert res_ent_match.status_code == 200
    assert res_ent_match.json()["zero_raw_pii_enforced"] is True
