"""Targeted unit tests for Shamir Secret Share routing & dropout recovery gRPC RPCs.

Covers:
  - RouteShareBundles acceptance & share_store state
  - SubmitDropoutShares threshold counting & response
"""

from __future__ import annotations

import pytest

from app.infrastructure.grpc.servicer import FederatedLearningServicer
from app.infrastructure.grpc.types import (
    DropoutRecoveryRequest,
    EncryptedShareBundle,
    ShareRoutingRequest,
)


@pytest.fixture()
def servicer() -> FederatedLearningServicer:
    return FederatedLearningServicer()


@pytest.mark.asyncio()
async def test_route_share_bundles_accepted(servicer: FederatedLearningServicer) -> None:
    """RouteShareBundles should store encrypted bundles in share_store."""
    bundle = EncryptedShareBundle(
        sender_bank_id="bank_alpha",
        recipient_bank_id="bank_beta",
        round_id=5,
        encrypted_b_share=b"ENCRYPTED_B_SHARE",
        encrypted_x_share=b"ENCRYPTED_X_SHARE",
        hmac_signature=b"HMAC_SIG",
    )
    req = ShareRoutingRequest(
        sender_bank_id="bank_alpha",
        round_id=5,
        bundles=[bundle],
    )

    resp = await servicer.RouteShareBundles(req)
    assert resp.accepted is True
    assert resp.routed_count == 1
    assert ("bank_alpha", "bank_beta") in servicer.share_store[5]


@pytest.mark.asyncio()
async def test_submit_dropout_shares_threshold_tracking(
    servicer: FederatedLearningServicer,
) -> None:
    """SubmitDropoutShares should track reporters and signal threshold_met when >= DEFAULT_QUORUM."""
    for i, bank in enumerate(["bank_alpha", "bank_beta", "bank_gamma"]):
        req = DropoutRecoveryRequest(
            reporting_bank_id=bank,
            round_id=10,
            surviving_b_shares=[b"b_share"],
            dropped_node_shares={"bank_delta": b"x_delta_share"},
        )
        resp = await servicer.SubmitDropoutShares(req)
        assert resp.accepted is True
        if i < 2:
            assert resp.threshold_met is False
        else:
            assert resp.threshold_met is True
