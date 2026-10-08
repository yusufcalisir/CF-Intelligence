"""Targeted integration tests verifying data-to-downstream contracts.

Covers:
1. Streaming graph transaction redelivery idempotency vs MultiDiGraph parallel edge preservation.
2. Tenant-scoped database persistence uniqueness and retry semantics.
3. Canonical idempotency payload hashing across arbitrary nested dictionary orderings.
4. Currency default consistency across canonical DTOs, REST prediction, and core banking connectors.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.schemas.transaction import TransactionPredictRequest
from app.application.services.idempotency import IdempotencyService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.infrastructure.connectors.base_connector import NormalizedTransaction
from app.infrastructure.connectors.mambu_connector import MambuConnector
from app.infrastructure.connectors.thought_machine_connector import ThoughtMachineConnector
from app.infrastructure.database import Base
from app.infrastructure.models import AlertModel
from app.infrastructure.repositories.alert_repository import AlertRepository

# ── 1. Streaming Graph Redelivery & MultiDiGraph Invariants ───────────────────


def test_graph_transaction_redelivery_idempotency() -> None:
    """Certify that re-ingesting the exact same canonical transaction via Kafka/transport

    redelivery does NOT create an additional graph edge, alter node degrees, or corrupt state.
    """
    graph = StreamingGraphService(max_window_minutes=60)
    tx = {
        "tenant_id": "bank_a",
        "transaction_id": "tx_001",
        "sender_id": "acct_a",
        "receiver_id": "acct_b",
        "amount": 100.0,
        "timestamp": datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC).isoformat(),
    }

    # Initial ingestion
    graph.add_transaction(tx)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.node_degrees["acct_a"] == 1
    assert graph.node_degrees["acct_b"] == 1

    # Exact redelivery (same tenant and transaction_id)
    graph.add_transaction(tx)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1, "Duplicate edge created on transport redelivery"
    assert graph.node_degrees["acct_a"] == 1, "Node degree corrupted by transport redelivery"
    assert graph.node_degrees["acct_b"] == 1


def test_graph_distinct_parallel_transactions_preserved() -> None:
    """Certify that distinct transactions between the same source and destination

    nodes coexist as legitimate parallel edges under MultiDiGraph semantics.
    """
    graph = StreamingGraphService(max_window_minutes=60)
    tx_1 = {
        "tenant_id": "bank_a",
        "transaction_id": "tx_001",
        "sender_id": "acct_a",
        "receiver_id": "acct_b",
        "amount": 100.0,
        "timestamp": datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC).isoformat(),
    }
    tx_2 = {
        "tenant_id": "bank_a",
        "transaction_id": "tx_002",
        "sender_id": "acct_a",
        "receiver_id": "acct_b",
        "amount": 100.0,
        "timestamp": datetime(2026, 10, 4, 12, 5, 0, tzinfo=UTC).isoformat(),
    }

    graph.add_transaction(tx_1)
    graph.add_transaction(tx_2)

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 2, "Parallel transaction edges collapsed erroneously"
    assert graph.node_degrees["acct_a"] == 2
    assert graph.node_degrees["acct_b"] == 2


def test_graph_cross_tenant_transaction_identity() -> None:
    """Certify that identical transaction IDs across different institutions do not collide

    in graph state and are correctly partitioned by tenant scope.
    """
    graph = StreamingGraphService(max_window_minutes=60)
    tx_bank_a = {
        "bank_id": "bank_a",
        "transaction_id": "tx_common_100",
        "account_id": "acct_a_src",
        "counterparty_account_id": "acct_a_dst",
        "amount": 100.0,
    }
    tx_bank_b = {
        "bank_id": "bank_b",
        "transaction_id": "tx_common_100",
        "account_id": "acct_b_src",
        "counterparty_account_id": "acct_b_dst",
        "amount": 900.0,
    }

    graph.add_transaction(tx_bank_a)
    graph.add_transaction(tx_bank_b)

    assert len(graph.edges) == 2, "Cross-tenant transaction ID collision prevented second edge"
    assert len(graph.nodes) == 4


def test_graph_conflicting_transaction_identity() -> None:
    """Certify that an incoming transaction with the same tenant and transaction ID

    but contradictory payload (amount or destination) fails closed by raising ValueError.
    """
    graph = StreamingGraphService(max_window_minutes=60)
    tx_original = {
        "bank_id": "bank_a",
        "transaction_id": "tx_conflict_1",
        "sender_id": "acct_1",
        "receiver_id": "acct_2",
        "amount": 100.0,
    }
    tx_conflicting_amount = {
        "bank_id": "bank_a",
        "transaction_id": "tx_conflict_1",
        "sender_id": "acct_1",
        "receiver_id": "acct_2",
        "amount": 999.0,  # Contradictory amount
    }
    tx_conflicting_dest = {
        "bank_id": "bank_a",
        "transaction_id": "tx_conflict_1",
        "sender_id": "acct_1",
        "receiver_id": "acct_different",  # Contradictory destination
        "amount": 100.0,
    }

    graph.add_transaction(tx_original)

    with pytest.raises(ValueError, match="Conflicting payload for existing graph transaction"):
        graph.add_transaction(tx_conflicting_amount)

    with pytest.raises(ValueError, match="Conflicting payload for existing graph transaction"):
        graph.add_transaction(tx_conflicting_dest)


def test_graph_redelivery_metamorphic_equivalence() -> None:
    """Metamorphic test: Graph state after sequence [T1, T2, T3] must be identical

    to graph state after sequence with arbitrary transport retries [T1, T1, T2, T2, T2, T3].
    """
    t1 = {
        "bank_id": "bank_meta",
        "transaction_id": "t1",
        "sender_id": "node_x",
        "receiver_id": "node_y",
        "amount": 150.0,
        "timestamp": "2026-10-04T10:00:00Z",
    }
    t2 = {
        "bank_id": "bank_meta",
        "transaction_id": "t2",
        "sender_id": "node_y",
        "receiver_id": "node_z",
        "amount": 250.0,
        "timestamp": "2026-10-04T10:05:00Z",
    }
    t3 = {
        "bank_id": "bank_meta",
        "transaction_id": "t3",
        "sender_id": "node_z",
        "receiver_id": "node_x",
        "amount": 350.0,
        "timestamp": "2026-10-04T10:10:00Z",
    }

    # Clean sequence
    g_clean = StreamingGraphService(max_window_minutes=60)
    for t in [t1, t2, t3]:
        g_clean.add_transaction(t)

    # Retried sequence
    g_retried = StreamingGraphService(max_window_minutes=60)
    for t in [t1, t1, t2, t2, t2, t3]:
        g_retried.add_transaction(t)

    # Financial graph states must be strictly identical
    assert len(g_clean.nodes) == len(g_retried.nodes) == 3
    assert len(g_clean.edges) == len(g_retried.edges) == 3
    for n in ["node_x", "node_y", "node_z"]:
        assert g_clean.node_degrees[n] == g_retried.node_degrees[n]
    assert sum(e["amount"] for e in g_clean.edges) == sum(e["amount"] for e in g_retried.edges) == 750.0


# ── 2. Database Persistence & Tenant Identity ─────────────────────────────────


@pytest.mark.asyncio
async def test_database_cross_tenant_transaction_identity() -> None:
    """Certify that database persistence isolates transactions by tenant, allowing identical

    transaction IDs across different banks to coexist without primary/unique key collisions.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        repo = AlertRepository(session)

        # Bank A alert for tx_common_999
        alert_a = await repo.create(
            bank_id="bank_a",
            transaction_id="tx_common_999",
            risk_score=0.85,
            severity="high",
        )

        # Bank B alert for tx_common_999
        alert_b = await repo.create(
            bank_id="bank_b",
            transaction_id="tx_common_999",
            risk_score=0.15,
            severity="low",
        )

        assert alert_a.id != alert_b.id
        assert alert_a.bank_id == "bank_a"
        assert alert_b.bank_id == "bank_b"

        # Query scoped by bank
        found_a = await repo.get_by_transaction_id("tx_common_999", bank_id="bank_a")
        found_b = await repo.get_by_transaction_id("tx_common_999", bank_id="bank_b")

        assert found_a is not None and found_a.risk_score == 0.85
        assert found_b is not None and found_b.risk_score == 0.15

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_same_tenant_retry() -> None:
    """Certify that same-tenant retry of an alert creation is idempotent and returns

    the existing persistent record without inserting duplicate rows.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        repo = AlertRepository(session)

        first = await repo.create(
            bank_id="bank_a",
            transaction_id="tx_retry_100",
            risk_score=0.72,
            severity="medium",
        )

        # Idempotent retry with identical data
        retry = await repo.create(
            bank_id="bank_a",
            transaction_id="tx_retry_100",
            risk_score=0.72,
            severity="medium",
        )

        assert retry.id == first.id

        # Total rows in alerts table must remain 1
        stmt = select(AlertModel)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        assert len(rows) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_conflicting_transaction_identity() -> None:
    """Certify that attempting to create a persistent alert with the same tenant and transaction ID

    but contradictory risk or severity data fails closed by raising ValueError.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        repo = AlertRepository(session)

        await repo.create(
            bank_id="bank_a",
            transaction_id="tx_conflict_200",
            risk_score=0.90,
            severity="critical",
        )

        with pytest.raises(ValueError, match="Conflicting alert payload"):
            await repo.create(
                bank_id="bank_a",
                transaction_id="tx_conflict_200",
                risk_score=0.10,  # Contradictory risk score
                severity="low",
            )

    await engine.dispose()


# ── 3. Idempotency Payload Canonicalization ───────────────────────────────────


def test_idempotency_semantically_equivalent_payload_hash() -> None:
    """Certify that semantically identical payloads produce identical hashes

    regardless of top-level or nested dictionary key order or formatting differences.
    """
    p1 = {
        "amount": 100.0,
        "currency": "EUR",
        "metadata": {"a": 1, "b": 2},
    }
    p2 = {
        "metadata": {"b": 2, "a": 1},
        "currency": "EUR",
        "amount": 100.0,
    }

    h1 = IdempotencyService.canonical_payload_hash(p1)
    h2 = IdempotencyService.canonical_payload_hash(p2)

    assert h1 == h2, "Key reordering in nested dictionary altered payload hash"


def test_idempotency_nested_metadata_order() -> None:
    """Certify deep multi-level nested dictionaries canonicalize identically."""
    deep_1 = {
        "user": {
            "profile": {"first": "John", "last": "Doe"},
            "attributes": {"dept": "compliance", "role": "lead"},
        },
        "items": [1, 2, 3],
    }
    deep_2 = {
        "items": [1, 2, 3],
        "user": {
            "attributes": {"role": "lead", "dept": "compliance"},
            "profile": {"last": "Doe", "first": "John"},
        },
    }

    h1 = IdempotencyService.canonical_payload_hash(deep_1)
    h2 = IdempotencyService.canonical_payload_hash(deep_2)

    assert h1 == h2


def test_idempotency_material_payload_difference() -> None:
    """Certify that material differences in business values produce strictly distinct hashes."""
    base = {"amount": 100.0, "currency": "EUR", "account": "ACC_1"}
    diff_amt = {"amount": 100.01, "currency": "EUR", "account": "ACC_1"}
    diff_ccy = {"amount": 100.0, "currency": "USD", "account": "ACC_1"}
    diff_acc = {"amount": 100.0, "currency": "EUR", "account": "ACC_2"}

    h_base = IdempotencyService.canonical_payload_hash(base)
    h_amt = IdempotencyService.canonical_payload_hash(diff_amt)
    h_ccy = IdempotencyService.canonical_payload_hash(diff_ccy)
    h_acc = IdempotencyService.canonical_payload_hash(diff_acc)

    assert len({h_base, h_amt, h_ccy, h_acc}) == 4


# ── 4. Unified Currency Defaults Across Entry Boundaries ───────────────────────


def test_currency_default_entry_path_consistency() -> None:
    """Certify that omitting currency across canonical DTOs, REST prediction requests,

    and core banking connectors resolves consistently to the platform base default ('EUR').
    """
    # 1. NormalizedTransaction canonical DTO
    tx_dto = NormalizedTransaction(
        transaction_id="tx_u1",
        account_id="acc_u1",
        counterparty_account_id="acc_u2",
        amount=250.0,
    )
    assert tx_dto.currency == "EUR"

    # 2. REST Prediction Request
    req = TransactionPredictRequest(transaction_amount=250.0)
    assert req.currency == "EUR"

    # 3. Mambu core banking connector
    mambu = MambuConnector(tenant_id="bank_mambu")
    tx_mambu = mambu._normalize_transaction_event(
        {
            "id": "m1",
            "accountId": "acc1",
            "counterpartyAccountId": "acc2",
            "amount": 250.0,
            "creationDate": "2026-10-04T12:00:00Z",
        }
    )
    assert tx_mambu.currency == "EUR"

    # 4. Thought Machine core banking connector
    tm = ThoughtMachineConnector(tenant_id="bank_tm")
    tx_tm = tm._normalize_single_instruction(
        {
            "id": "inst_1",
            "custom_instruction": {
                "postings": [
                    {
                        "credit": True,
                        "amount": "250.0",
                        "account_id": "acc_tm_1",
                    },
                    {
                        "credit": False,
                        "amount": "250.0",
                        "account_id": "acc_tm_2",
                    },
                ]
            },
            "value_timestamp": "2026-10-04T12:00:00Z",
        },
        batch_id="batch_tm_1",
        index=0,
    )
    assert tx_tm is not None
    assert tx_tm.currency == "EUR"
