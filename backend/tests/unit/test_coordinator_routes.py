"""Unit tests for Federated Learning Coordinator & Consortium Governance API routes.

Validates multi-prefix routing, client handshake/heartbeat, dynamic capability negotiation,
asynchronous parameter updates, dynamic quorum verification, and weighted consortium governance.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.application.services.consortium_service import consortium_governance_service
from app.application.services.coordinator_service import coordinator_service
from app.main import app


@pytest.fixture(autouse=True)
def clean_coordinator_state() -> Generator[None, None, None]:
    """Ensures clean service state across test executions."""
    from app.domain.async_fl_engine import AsyncFLEngine

    consortium_governance_service.reset()
    coordinator_service.registry.clear()
    coordinator_service.rounds.clear()
    coordinator_service.async_fl_engine = AsyncFLEngine(current_round=1, alpha_staleness=0.5, learning_rate=0.8)
    yield
    consortium_governance_service.reset()
    coordinator_service.registry.clear()
    coordinator_service.rounds.clear()
    coordinator_service.async_fl_engine = AsyncFLEngine(current_round=1, alpha_staleness=0.5, learning_rate=0.8)



@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


# ── Client Handshake & Lifecycle Tests ─────────────────────────


@pytest.mark.parametrize("prefix", ["/api/v1/coordinator", "/v1/coordinator"])
def test_handshake_success(client: TestClient, prefix: str) -> None:
    payload = {
        "bank_id": "bank_test_1",
        "pytorch_version": "2.4.0+cu121",
        "python_version": "3.12.3",
        "hardware_type": "cuda",
        "ram_gb": 64.0,
        "device_count": 2,
    }
    resp = client.post(f"{prefix}/handshake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["registered"] is True
    assert data["status"] in ("ACCEPTED", "REGISTERED", "COMPATIBLE")
    assert "registered_at" in data


def test_handshake_validation_errors(client: TestClient) -> None:
    # Invalid RAM < 0.5 GB
    resp = client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank_invalid",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cpu",
            "ram_gb": 0.1,
            "device_count": 0,
        },
    )
    assert resp.status_code == 422

    # Invalid bank ID characters
    resp2 = client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank@invalid!",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cpu",
            "ram_gb": 16.0,
            "device_count": 1,
        },
    )
    assert resp2.status_code == 422


def test_heartbeat_lifecycle(client: TestClient) -> None:
    # Unregistered bank -> 404
    resp = client.post("/api/v1/coordinator/heartbeat", json={"bank_id": "unknown_bank"})
    assert resp.status_code == 404

    # Register bank first
    client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank_alpha",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cuda",
            "ram_gb": 32.0,
            "device_count": 1,
        },
    )

    # Registered bank -> 200
    hb_resp = client.post("/api/v1/coordinator/heartbeat", json={"bank_id": "bank_alpha"})
    assert hb_resp.status_code == 200
    data = hb_resp.json()
    assert data["success"] is True
    assert data["status"] == "ONLINE"


def test_list_clients(client: TestClient) -> None:
    # Register two banks
    for bid in ["bank_one", "bank_two"]:
        client.post(
            "/api/v1/coordinator/handshake",
            json={
                "bank_id": bid,
                "pytorch_version": "2.4.0",
                "python_version": "3.12.0",
                "hardware_type": "cuda",
                "ram_gb": 32.0,
                "device_count": 1,
            },
        )

    resp = client.get("/api/v1/coordinator/clients")
    assert resp.status_code == 200
    clients = resp.json()
    assert len(clients) >= 2
    bank_ids = [c["bank_id"] for c in clients]
    assert "bank_one" in bank_ids
    assert "bank_two" in bank_ids


def test_negotiate_parameters_get_and_post(client: TestClient) -> None:
    # Register bank
    client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank_gpu",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cuda",
            "ram_gb": 128.0,
            "device_count": 4,
        },
    )

    # GET negotiate
    get_resp = client.get("/api/v1/coordinator/negotiate?bank_id=bank_gpu&base_batch_size=64&base_epochs=10")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["bank_id"] == "bank_gpu"
    assert get_data["batch_size"] >= 32

    # POST negotiate
    post_resp = client.post(
        "/api/v1/coordinator/negotiate",
        json={"bank_id": "bank_gpu", "base_batch_size": 64, "base_epochs": 10},
    )
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["bank_id"] == "bank_gpu"


# ── Asynchronous FL & Quorum Endpoints ─────────────────────────


def test_async_update_and_staleness(client: TestClient) -> None:
    # Register bank
    client.post(
        "/api/v1/coordinator/handshake",
        json={
            "bank_id": "bank_async_1",
            "pytorch_version": "2.4.0",
            "python_version": "3.12.0",
            "hardware_type": "cpu",
            "ram_gb": 16.0,
            "device_count": 0,
        },
    )

    update_payload = {
        "bank_id": "bank_async_1",
        "submitted_round": 1,
        "client_weights": {
            "layer1": [0.1, 0.2, 0.3, 0.4],
            "layer2": [0.5, 0.6],
        },
        "layer_shapes": {
            "layer1": [2, 2],
            "layer2": [2],
        },
        "sample_count": 250,
    }

    resp = client.post("/api/v1/coordinator/async-update", json=update_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["bank_id"] == "bank_async_1"
    assert "layer1" in data["layer_keys"]

    # Shape mismatch error -> 422
    bad_shape_payload = {
        "bank_id": "bank_async_1",
        "submitted_round": 1,
        "client_weights": {"layer1": [0.1, 0.2]},
        "layer_shapes": {"layer1": [5, 5]},  # Cannot reshape 2 elements to 5x5
        "sample_count": 100,
    }
    bad_resp = client.post("/api/v1/coordinator/async-update", json=bad_shape_payload)
    assert bad_resp.status_code == 422


def test_async_engine_status(client: TestClient) -> None:
    resp = client.get("/api/v1/coordinator/async-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_round" in data
    assert "alpha_staleness" in data
    assert "learning_rate" in data


@pytest.mark.parametrize("prefix", ["/api/v1/coordinator", "/v1/coordinator"])
def test_quorum_status_and_canonical_alias(client: TestClient, prefix: str) -> None:
    resp1 = client.get(f"{prefix}/quorum-status")
    assert resp1.status_code == 200
    d1 = resp1.json()
    assert "quorum_threshold_pct" in d1
    assert "current_quorum_pct" in d1

    resp2 = client.get(f"{prefix}/quorum")
    assert resp2.status_code == 200
    d2 = resp2.json()
    assert d2["state"] == d1["state"]


def test_prune_rounds(client: TestClient) -> None:
    resp = client.post("/api/v1/coordinator/rounds/prune?keep_last=25")
    assert resp.status_code == 200
    data = resp.json()
    assert data["keep_last"] == 25
    assert "pruned_rounds_count" in data


# ── Consortium Governance Tests ────────────────────────────────


def test_get_consortium_details(client: TestClient) -> None:
    # Default consortium exists
    resp = client.get("/api/v1/coordinator/consortium/cfi-consortium")
    assert resp.status_code == 200
    data = resp.json()
    assert data["consortium_id"] == "cfi-consortium"
    assert data["quorum_ratio"] == 0.51
    assert len(data["members"]) >= 3

    # Non-existent consortium -> 404
    resp404 = client.get("/api/v1/coordinator/consortium/unknown-consortium")
    assert resp404.status_code == 404


def test_create_new_consortium(client: TestClient) -> None:
    payload = {
        "consortium_id": "nordic-alliance",
        "name": "Nordic Cross-Border Fraud Alliance",
        "founder_bank_id": "bank_nordic_1",
        "quorum_ratio": 0.66,
        "max_epsilon": 4.0,
        "min_members_n": 3,
    }
    resp = client.post("/api/v1/coordinator/consortium", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["consortium_id"] == "nordic-alliance"
    assert data["quorum_ratio"] == 0.66
    assert len(data["members"]) == 1
    assert data["members"][0]["bank_id"] == "bank_nordic_1"
    assert data["members"][0]["role"] == "FOUNDER"


def test_list_consortium_members(client: TestClient) -> None:
    resp = client.get("/api/v1/coordinator/consortium/cfi-consortium/members")
    assert resp.status_code == 200
    members = resp.json()
    bank_ids = [m["bank_id"] for m in members]
    assert "bank_a" in bank_ids
    assert "bank_b" in bank_ids
    assert "bank_c" in bank_ids


def test_membership_proposal_lifecycle_and_voting(client: TestClient) -> None:
    # 1. Propose adding bank_d
    prop_payload = {
        "consortium_id": "cfi-consortium",
        "creator_bank_id": "bank_a",
        "target_bank_id": "bank_d",
        "action": "ADD_MEMBER",
        "ttl_seconds": 86400,
        "metadata": {"jurisdiction": "EU"},
    }
    create_resp = client.post("/api/v1/coordinator/proposals/membership", json=prop_payload)
    assert create_resp.status_code == 201
    prop = create_resp.json()
    proposal_id = prop["proposal_id"]
    assert prop["target_bank_id"] == "bank_d"
    assert prop["status"] == "PENDING"
    assert "bank_a" in prop["votes_for"]

    # 2. Get proposal by ID
    get_resp = client.get(f"/api/v1/coordinator/proposals/{proposal_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["proposal_id"] == proposal_id

    # 3. Cast vote from bank_b (reaches 2/3 = 66% > 51% quorum -> APPROVED)
    vote_resp = client.post(
        f"/api/v1/coordinator/proposals/{proposal_id}/vote",
        json={"bank_id": "bank_b", "approve": True},
    )
    assert vote_resp.status_code == 200
    vote_data = vote_resp.json()
    assert vote_data["status"] == "APPROVED"
    assert vote_data["resolved"] is True
    assert "bank_b" in vote_data["votes_for"]

    # 4. Confirm bank_d was added as member of cfi-consortium
    members_resp = client.get("/api/v1/coordinator/consortium/cfi-consortium/members")
    assert members_resp.status_code == 200
    all_members = [m["bank_id"] for m in members_resp.json()]
    assert "bank_d" in all_members


def test_policy_update_proposal(client: TestClient) -> None:
    policy_payload = {
        "consortium_id": "cfi-consortium",
        "creator_bank_id": "bank_a",
        "policy_updates": {
            "new_quorum_ratio": 0.75,
            "new_max_epsilon": 3.0,
        },
        "ttl_seconds": 3600,
    }
    resp = client.post("/api/v1/coordinator/proposals/policy", json=policy_payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["action"] == "UPDATE_POLICY"
    assert data["status"] == "PENDING"


def test_cancel_proposal(client: TestClient) -> None:
    prop_resp = client.post(
        "/api/v1/coordinator/proposals/membership",
        json={
            "consortium_id": "cfi-consortium",
            "creator_bank_id": "bank_a",
            "target_bank_id": "bank_e",
            "action": "ADD_MEMBER",
        },
    )
    assert prop_resp.status_code == 201
    pid = prop_resp.json()["proposal_id"]

    # Cancel by sponsor
    cancel_resp = client.post(f"/api/v1/coordinator/proposals/{pid}/cancel?creator_bank_id=bank_a")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"


def test_proposal_not_found_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/coordinator/proposals/nonexistent_prop_id")
    assert resp.status_code == 404


def test_handshake_with_bank_name_propagation(client: TestClient) -> None:
    payload = {
        "bank_id": "bank_custom_tr",
        "bank_name": "VakıfBank",
        "pytorch_version": "2.4.0+cu124",
        "python_version": "3.12.3",
        "hardware_type": "cuda",
        "ram_gb": 64.0,
        "device_count": 2,
    }
    resp = client.post("/api/v1/coordinator/handshake", json=payload)
    assert resp.status_code == 200
    assert resp.json()["registered"] is True

    clients_resp = client.get("/api/v1/coordinator/clients")
    assert clients_resp.status_code == 200
    registered = {c["bank_id"]: c for c in clients_resp.json()}
    assert "bank_custom_tr" in registered
    assert registered["bank_custom_tr"]["bank_name"] == "VakıfBank"


def test_list_clients_auto_seeds_consortium_when_empty(client: TestClient) -> None:
    # Ensure coordinator registry is empty
    coordinator_service.registry.clear()

    resp = client.get("/api/v1/coordinator/clients")
    assert resp.status_code == 200
    clients = resp.json()
    assert len(clients) == 5
    bank_map = {c["bank_id"]: c["bank_name"] for c in clients}
    assert bank_map["bank_alpha"] == "Garanti BBVA"
    assert bank_map["bank_beta"] == "İş Bankası"
    assert bank_map["bank_gamma"] == "Akbank"
    assert bank_map["bank_a"] == "Meridian National"
    assert bank_map["bank_b"] == "Nexus Digital"

    # Verify PyTorch 2.4 core specification
    for c in clients:
        assert c["pytorch_version"].startswith("2.4.0")


def test_heartbeat_institutional_alias_resolution(client: TestClient) -> None:
    # Auto-seed consortium nodes
    coordinator_service.seed_consortium_nodes()

    # Heartbeat to alias bank_meridian should resolve to bank_a
    hb_resp = client.post("/api/v1/coordinator/heartbeat", json={"bank_id": "bank_meridian"})
    assert hb_resp.status_code == 200
    assert hb_resp.json()["success"] is True
    assert hb_resp.json()["status"] == "ONLINE"

