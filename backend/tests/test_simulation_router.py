from fastapi.testclient import TestClient

from app.domain.enums import SimulationStatus
from app.main import app

client = TestClient(app)


def test_create_and_get_simulation():
    # 1. Create a simulation
    response = client.post(
        "/api/v1/simulations",
        json={
            "num_rounds": 2,
            "local_epochs": 1,
            "learning_rate": 0.001,
            "batch_size": 32,
            "min_clients_per_round": 2,
            "enable_latency_simulation": False,
            "enable_dropout_simulation": False,
            "enable_reconnect_simulation": True,
            "privacy_mechanism": "none",
            "dp_epsilon": 1.0,
            "dp_delta": 1e-5,
            "dp_max_grad_norm": 1.0,
            "bank_a_transactions": 1000,
            "bank_b_transactions": 1000,
            "bank_c_transactions": 1000,
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    simulation_id = data["id"]

    # 2. Immediately get the simulation (while it's running / pending)
    get_response = client.get(f"/api/v1/simulations/{simulation_id}")
    assert get_response.status_code == 200, f"Failed with {get_response.text}"
    get_data = get_response.json()
    assert get_data["id"] == simulation_id
    assert get_data["status"] in [s.value for s in SimulationStatus]


def test_delete_simulation_success():
    # 1. Create a simulation
    response = client.post(
        "/api/v1/simulations",
        json={
            "num_rounds": 1,
            "local_epochs": 1,
            "learning_rate": 0.001,
            "batch_size": 32,
            "min_clients_per_round": 2,
            "bank_a_transactions": 1000,
            "bank_b_transactions": 1000,
            "bank_c_transactions": 1000,
        },
    )
    assert response.status_code == 202
    simulation_id = response.json()["id"]

    # 2. Confirm it exists
    get_res = client.get(f"/api/v1/simulations/{simulation_id}")
    assert get_res.status_code == 200

    # 3. Delete it
    del_res = client.delete(f"/api/v1/simulations/{simulation_id}")
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "DELETED"
    assert del_data["simulation_id"] == simulation_id

    # 4. Confirm it is gone
    get_after = client.get(f"/api/v1/simulations/{simulation_id}")
    assert get_after.status_code == 404

    # 5. Confirm it is absent from list
    list_res = client.get("/api/v1/simulations")
    assert list_res.status_code == 200
    list_ids = [s["id"] for s in list_res.json()]
    assert simulation_id not in list_ids


def test_delete_nonexistent_simulation_returns_404():
    res = client.delete("/api/v1/simulations/non-existent-sim-id-999")
    assert res.status_code == 404

