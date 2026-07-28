"""Integration tests for Section 37.2: Gradient Transmission — Real SecAgg Wire Protocol."""

from __future__ import annotations

import zlib

import pytest

from app.infrastructure.grpc.client import GRPCBankClient
from app.infrastructure.grpc.servicer import FederatedLearningServicer
from app.infrastructure.grpc.types import SubmitGradientRequest
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


@pytest.mark.asyncio
async def test_valid_gradient_accepted_and_persisted() -> None:
    """Verifies that a valid, compressed, and signed gradient update is accepted by the servicer."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    dummy_gradient = b"SECAGG_MASKED_GRADIENT_WEIGHT_TENSOR_BYTES_12345" * 10

    ack = await client.submit_gradient(
        round_id="round_101",
        bank_id="bank_alpha",
        masked_gradient_bytes=dummy_gradient,
        dp_epsilon_used=1.5,
        participant_count=3,
    )

    assert ack.received is True
    assert "Gradient accepted" in ack.status_message
    assert "round_101" in servicer.round_submissions
    assert len(servicer.round_submissions["round_101"]) == 1
    assert servicer.round_submissions["round_101"][0]["bank_id"] == "bank_alpha"


@pytest.mark.asyncio
async def test_invalid_signature_rejected() -> None:
    """Verifies that a gradient submission with an invalid signature is rejected."""
    servicer = FederatedLearningServicer()

    compressed_gradient = zlib.compress(b"GRADIENT_TENSOR_DATA")

    req = SubmitGradientRequest(
        round_id="round_102",
        bank_id="bank_beta",
        compressed_masked_gradient=compressed_gradient,
        dp_epsilon_used=1.0,
        participant_count=3,
        signature=b"TAMPERED_INVALID_SIGNATURE_BYTES_9999",
        protocol_version="1.0.0",
    )

    ack = await servicer.SubmitGradient(req)

    assert ack.received is False
    assert "REJECTED_SIGNATURE" in ack.status_message


@pytest.mark.asyncio
async def test_epsilon_exceeded_rejected() -> None:
    """Verifies that a gradient submission exceeding the DP epsilon cap is rejected."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    dummy_gradient = b"HIGH_EPSILON_GRADIENT_DATA"

    ack = await client.submit_gradient(
        round_id="round_103",
        bank_id="bank_gamma",
        masked_gradient_bytes=dummy_gradient,
        dp_epsilon_used=99.0,  # Exceeds MAX_EPSILON (10.0)
        participant_count=3,
    )

    assert ack.received is False
    assert "REJECTED_EPSILON" in ack.status_message


@pytest.mark.asyncio
async def test_audit_chain_entry_created() -> None:
    """Verifies that submitting a valid gradient appends a GRADIENT_RECEIVED event to ImmutableAuditChain."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    audit_chain = ImmutableAuditChain.get_instance()
    initial_count = len(audit_chain.chain)

    dummy_gradient = b"AUDIT_CHAIN_TEST_GRADIENT_BYTES"

    ack = await client.submit_gradient(
        round_id="round_104",
        bank_id="bank_delta",
        masked_gradient_bytes=dummy_gradient,
        dp_epsilon_used=2.0,
        participant_count=3,
    )

    assert ack.received is True
    assert len(audit_chain.chain) == initial_count + 1

    last_event = audit_chain.chain[-1]
    assert last_event.event_type == "GRADIENT_RECEIVED"
    assert last_event.actor == "bank_delta"
    assert last_event.target_id == "round_104"
    assert "gradient_hash" in last_event.details


@pytest.mark.asyncio
async def test_quorum_triggers_aggregation() -> None:
    """Verifies that submitting gradients up to quorum count triggers round aggregation."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    round_id = "round_105"
    gradient_data = b"PARTICIPANT_GRADIENT_PAYLOAD"

    # Submission 1 (1/3)
    ack1 = await client.submit_gradient(
        round_id=round_id,
        bank_id="bank_1",
        masked_gradient_bytes=gradient_data,
        dp_epsilon_used=1.0,
        participant_count=3,
    )
    assert ack1.received is True
    assert "Waiting for quorum" in ack1.status_message

    # Submission 2 (2/3)
    ack2 = await client.submit_gradient(
        round_id=round_id,
        bank_id="bank_2",
        masked_gradient_bytes=gradient_data,
        dp_epsilon_used=1.0,
        participant_count=3,
    )
    assert ack2.received is True
    assert "Waiting for quorum" in ack2.status_message

    # Submission 3 (3/3 — Quorum reached!)
    ack3 = await client.submit_gradient(
        round_id=round_id,
        bank_id="bank_3",
        masked_gradient_bytes=gradient_data,
        dp_epsilon_used=1.0,
        participant_count=3,
    )
    assert ack3.received is True
    assert "Quorum reached" in ack3.status_message
    assert "aggregation initiated" in ack3.status_message
