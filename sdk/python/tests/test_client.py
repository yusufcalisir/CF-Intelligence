"""Unit tests for CFI LocalFLClient."""

import zlib
import pytest
from cfi_connector_sdk.client.local_fl_client import LocalFLClient


def test_local_fl_client_initialization():
    client = LocalFLClient(
        bank_id="  BANK_ALPHA ",
        coordinator_url="localhost:50051",
    )
    assert client.bank_id == "bank_alpha"
    assert client.is_connected is False

    assert client.connect() is True
    assert client.is_connected is True


def test_submit_gradient():
    client = LocalFLClient(bank_id="bank_beta")
    masked_gradient = b"\x01\x02\x03\x04\x05\x06\x07\x08" * 100
    round_id = 5

    res = client.submit_gradient(
        round_id=round_id,
        masked_gradient_bytes=masked_gradient,
        dp_epsilon_used=0.75,
    )

    assert res["status"] == "ACCEPTED"
    assert res["bank_id"] == "bank_beta"
    assert res["round_id"] == 5
    assert res["dp_epsilon_used"] == 0.75
    assert res["compressed_size_bytes"] < len(masked_gradient)
    assert "signature" in res


def test_submit_local_weights_backwards_compatible():
    client = LocalFLClient(bank_id="bank_gamma")
    weights = {"layer1.weight": [0.1, 0.2, 0.3], "layer1.bias": [0.01]}

    res = client.submit_local_weights(
        round_id=10,
        weights=weights,
        dp_epsilon=1.2,
        num_samples=1500,
    )

    assert res["status"] == "ACCEPTED"
    assert res["bank_id"] == "bank_gamma"
    assert res["round_id"] == 10
    assert res["num_samples"] == 1500
    assert res["dp_epsilon_used"] == 1.2
