"""Scenario simulator.

Generates realistic multi-bank fraud scenarios that demonstrate
why collaborative intelligence improves detection. Each scenario
creates a scripted sequence of events across multiple banks.

Key scenarios:
- Fraud ring: Multiple actors sharing resources across institutions
- Account takeover: Device change → rapid withdrawals
- Money laundering: Layering pattern across institutions
- Card testing: Repeated small charges → large purchase

Each scenario produces a list of StreamingEvent objects with
realistic timing for replay through the streaming engine.
"""

from __future__ import annotations

import logging
import uuid

import numpy as np

from app.domain.enums import ScenarioType
from app.domain.investigation_entities import Scenario, StreamingEvent

logger = logging.getLogger(__name__)

BANK_IDS = ["bank_a", "bank_b", "bank_c"]
BANK_NAMES = {
    "bank_a": "Meridian National",
    "bank_b": "Nexus Digital",
    "bank_c": "Heritage Regional",
}


class ScenarioSimulator:
    """Generates pre-built fraud scenarios for simulation.

    Each scenario produces a sequence of events that, when replayed
    through the streaming engine, demonstrates the advantage of
    cross-institution collaboration.

    The key insight each scenario demonstrates:
    - Individually: each bank sees a fragment with low confidence
    - Collaboratively: shared intelligence raises confidence significantly
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)
        self._scenarios: dict[str, Scenario] = {}

    def create_scenario(self, scenario_type: ScenarioType) -> Scenario:
        """Create a scenario by type."""
        creators = {
            ScenarioType.FRAUD_RING: self._create_fraud_ring,
            ScenarioType.ACCOUNT_TAKEOVER: self._create_account_takeover,
            ScenarioType.MONEY_LAUNDERING: self._create_money_laundering,
            ScenarioType.CARD_TESTING: self._create_card_testing,
        }
        creator = creators.get(scenario_type)
        if not creator:
            raise ValueError(f"Unknown scenario type: {scenario_type}")

        scenario = creator()
        self._scenarios[scenario.id] = scenario
        logger.info(
            "Created scenario %s: %s (%d events)",
            scenario.id[:8],
            scenario.name,
            len(scenario.events),
        )
        return scenario

    def get_scenario(self, scenario_id: str) -> Scenario | None:
        return self._scenarios.get(scenario_id)

    def list_available_scenarios(self) -> list[dict]:
        """Return metadata about available scenario types."""
        return [
            {
                "type": ScenarioType.FRAUD_RING.value,
                "name": "Cross-Institution Fraud Ring",
                "description": (
                    "A fraud ring operates across three banks using shared devices "
                    "and stolen identities. Each bank individually detects fragments "
                    "with low confidence. Shared intelligence reveals the full ring."
                ),
                "banks_involved": BANK_IDS,
                "estimated_events": 25,
                "estimated_duration_seconds": 60,
            },
            {
                "type": ScenarioType.ACCOUNT_TAKEOVER.value,
                "name": "Account Takeover Attack",
                "description": (
                    "An attacker gains access to accounts at two banks via credential "
                    "stuffing. They change device, update contact info, then execute "
                    "rapid withdrawals. Cross-bank intelligence links the attacks."
                ),
                "banks_involved": ["bank_a", "bank_b"],
                "estimated_events": 18,
                "estimated_duration_seconds": 45,
            },
            {
                "type": ScenarioType.MONEY_LAUNDERING.value,
                "name": "Layered Money Laundering",
                "description": (
                    "Funds flow through a layering scheme: large deposit → split into "
                    "smaller transfers across banks → reconsolidation. Each bank sees "
                    "normal transactions individually; collaboration reveals the pattern."
                ),
                "banks_involved": BANK_IDS,
                "estimated_events": 22,
                "estimated_duration_seconds": 55,
            },
            {
                "type": ScenarioType.CARD_TESTING.value,
                "name": "Distributed Card Testing",
                "description": (
                    "Stolen cards are tested with small charges across multiple "
                    "merchants and banks before large purchases. Individual banks "
                    "see normal small transactions; correlation reveals the testing."
                ),
                "banks_involved": ["bank_a", "bank_c"],
                "estimated_events": 20,
                "estimated_duration_seconds": 50,
            },
        ]

    # ── Scenario generators ───────────────────

    def _create_fraud_ring(self) -> Scenario:
        """Fraud ring: shared entities across three banks."""
        events: list[StreamingEvent] = []
        delay = 0

        # Shared fraud ring members (same privacy hashes across banks)
        ring_members = [f"ring_member_{i}" for i in range(4)]
        shared_device = "device_fraud_ring_001"
        shared_ip = "192.168.99.1"

        # Phase 1: Normal-looking transactions at each bank (2-3 per bank)
        for bank_id in BANK_IDS:
            for member in ring_members[:2]:
                delay += int(self._rng.integers(1500, 3000))
                events.append(
                    self._make_transaction_event(
                        bank_id=bank_id,
                        customer_id=member,
                        amount=self._rng.uniform(50, 300),
                        merchant="grocery",
                        country="US",
                        device=shared_device,
                        risk_score=0.15,
                        delay_ms=delay,
                    )
                )

        # Phase 2: Suspicious transactions start
        for bank_id in BANK_IDS:
            for member in ring_members:
                delay += int(self._rng.integers(1000, 2500))
                events.append(
                    self._make_transaction_event(
                        bank_id=bank_id,
                        customer_id=member,
                        amount=self._rng.uniform(2000, 8000),
                        merchant=str(self._rng.choice(["crypto", "wire_transfer", "jewelry"])),
                        country=str(self._rng.choice(["NG", "RU", "PH"])),
                        device=shared_device,
                        risk_score=self._rng.uniform(0.55, 0.75),
                        delay_ms=delay,
                    )
                )

        # Phase 3: Alerts generated at each bank
        for bank_id in BANK_IDS:
            delay += int(self._rng.integers(500, 1500))
            events.append(
                StreamingEvent(
                    event_type="alert",
                    bank_id=bank_id,
                    delay_ms=delay,
                    payload={
                        "severity": "medium",
                        "reason_codes": ["VEL-001", "GEO-RISK"],
                        "confidence": 0.62,
                        "description": f"{BANK_NAMES[bank_id]}: Suspicious velocity pattern",
                        "individual_assessment": "Medium confidence — possible false positive",
                    },
                )
            )

        # Phase 4: Intelligence sharing
        delay += 2000
        events.append(
            StreamingEvent(
                event_type="intelligence",
                bank_id="shared",
                delay_ms=delay,
                payload={
                    "type": "entity_correlation",
                    "shared_device_hash": shared_device[:8],
                    "shared_ip_hash": shared_ip[:8],
                    "banks_reporting": BANK_IDS,
                    "combined_confidence": 0.91,
                    "description": "Cross-institution correlation: shared device/IP across 3 banks, 4 entities",
                    "collaborative_assessment": "HIGH confidence — fraud ring detected",
                },
            )
        )

        # Phase 5: Escalated alerts
        for bank_id in BANK_IDS:
            delay += 500
            events.append(
                StreamingEvent(
                    event_type="escalation",
                    bank_id=bank_id,
                    delay_ms=delay,
                    payload={
                        "severity": "critical",
                        "confidence_before": 0.62,
                        "confidence_after": 0.91,
                        "reason": "Cross-institution intelligence confirmed fraud ring",
                        "description": f"{BANK_NAMES[bank_id]}: Alert escalated to CRITICAL",
                    },
                )
            )

        return Scenario(
            scenario_type=ScenarioType.FRAUD_RING,
            name="Cross-Institution Fraud Ring",
            description=(
                "A fraud ring of 4 members operates across all three banks using "
                "shared devices and IPs. Individual bank confidence: ~62%. "
                "After intelligence sharing: ~91%."
            ),
            banks_involved=BANK_IDS,
            events=events,
            duration_seconds=delay / 1000,
        )

    def _create_account_takeover(self) -> Scenario:
        events: list[StreamingEvent] = []
        delay = 0
        victim_id = "ato_victim_001"

        # Phase 1: Normal transactions (baseline)
        for _ in range(3):
            delay += int(self._rng.integers(2000, 4000))
            events.append(
                self._make_transaction_event(
                    bank_id="bank_a",
                    customer_id=victim_id,
                    amount=self._rng.uniform(20, 150),
                    merchant="grocery",
                    country="US",
                    device="mobile_app",
                    risk_score=0.05,
                    delay_ms=delay,
                )
            )

        # Phase 2: Device change + contact update
        delay += 2000
        events.append(
            StreamingEvent(
                event_type="account_change",
                bank_id="bank_a",
                delay_ms=delay,
                payload={
                    "change_type": "device_change",
                    "description": "New device registered (was: iPhone 14, now: Android unknown)",
                    "risk_signal": 0.35,
                },
            )
        )

        delay += 1000
        events.append(
            StreamingEvent(
                event_type="account_change",
                bank_id="bank_a",
                delay_ms=delay,
                payload={
                    "change_type": "contact_update",
                    "description": "Email and phone number changed",
                    "risk_signal": 0.45,
                },
            )
        )

        # Phase 3: Rapid withdrawals at bank_a
        for i in range(4):
            delay += int(self._rng.integers(500, 1500))
            events.append(
                self._make_transaction_event(
                    bank_id="bank_a",
                    customer_id=victim_id,
                    amount=self._rng.uniform(3000, 9000),
                    merchant=str(self._rng.choice(["wire_transfer", "crypto"])),
                    country="RU",
                    device="web_browser",
                    risk_score=0.5 + i * 0.1,
                    delay_ms=delay,
                )
            )

        # Phase 4: Same attacker at bank_b
        delay += 3000
        for i in range(3):
            delay += int(self._rng.integers(500, 1500))
            events.append(
                self._make_transaction_event(
                    bank_id="bank_b",
                    customer_id=victim_id,
                    amount=self._rng.uniform(2000, 7000),
                    merchant="wire_transfer",
                    country="RU",
                    device="web_browser",
                    risk_score=self._rng.uniform(0.45, 0.65),
                    delay_ms=delay,
                )
            )

        # Phase 5: Alerts + intelligence
        delay += 1500
        events.append(
            StreamingEvent(
                event_type="alert",
                bank_id="bank_a",
                delay_ms=delay,
                payload={
                    "severity": "high",
                    "confidence": 0.72,
                    "reason_codes": ["VEL-001", "DEV-ANOM", "AMT-ANOM"],
                    "description": "Rapid withdrawals after device change",
                },
            )
        )
        delay += 500
        events.append(
            StreamingEvent(
                event_type="alert",
                bank_id="bank_b",
                delay_ms=delay,
                payload={
                    "severity": "medium",
                    "confidence": 0.55,
                    "reason_codes": ["DEV-ANOM", "AMT-ANOM"],
                    "description": "Unusual wire transfers from new device",
                },
            )
        )
        delay += 2000
        events.append(
            StreamingEvent(
                event_type="intelligence",
                bank_id="shared",
                delay_ms=delay,
                payload={
                    "combined_confidence": 0.94,
                    "description": "Same entity targeted at 2 banks — account takeover confirmed",
                    "collaborative_assessment": "CRITICAL — coordinated ATO across Meridian + Nexus",
                },
            )
        )

        return Scenario(
            scenario_type=ScenarioType.ACCOUNT_TAKEOVER,
            name="Account Takeover Attack",
            description="Credential stuffing → device change → rapid withdrawals across two banks.",
            banks_involved=["bank_a", "bank_b"],
            events=events,
            duration_seconds=delay / 1000,
        )

    def _create_money_laundering(self) -> Scenario:
        events: list[StreamingEvent] = []
        delay = 0
        launderer = "ml_entity_001"

        # Phase 1: Large deposit
        delay += 2000
        events.append(
            self._make_transaction_event(
                bank_id="bank_a",
                customer_id=launderer,
                amount=50000.0,
                merchant="wire_transfer",
                country="AE",
                device="web_browser",
                risk_score=0.30,
                delay_ms=delay,
            )
        )

        # Phase 2: Split into smaller transfers (layering)
        for i in range(6):
            target_bank = BANK_IDS[i % 3]
            delay += int(self._rng.integers(2000, 4000))
            events.append(
                self._make_transaction_event(
                    bank_id=target_bank,
                    customer_id=f"ml_recipient_{i}",
                    amount=self._rng.uniform(5000, 12000),
                    merchant=str(self._rng.choice(["wire_transfer", "online_marketplace"])),
                    country=str(self._rng.choice(["US", "UK", "DE", "NL"])),
                    device="web_browser",
                    risk_score=self._rng.uniform(0.15, 0.35),
                    delay_ms=delay,
                )
            )

        # Phase 3: Reconsolidation
        for i in range(3):
            delay += int(self._rng.integers(2000, 3500))
            events.append(
                self._make_transaction_event(
                    bank_id="bank_c",
                    customer_id=f"ml_consolidator_{i}",
                    amount=self._rng.uniform(14000, 18000),
                    merchant="wire_transfer",
                    country="SG",
                    device="web_browser",
                    risk_score=self._rng.uniform(0.25, 0.45),
                    delay_ms=delay,
                )
            )

        # Phase 4: Individual bank assessment
        for bank_id in BANK_IDS:
            delay += 1000
            events.append(
                StreamingEvent(
                    event_type="alert",
                    bank_id=bank_id,
                    delay_ms=delay,
                    payload={
                        "severity": "low",
                        "confidence": 0.35,
                        "reason_codes": ["AMT-ANOM", "GEO-RISK"],
                        "description": f"{BANK_NAMES[bank_id]}: Wire transfers within normal range",
                        "individual_assessment": "Low confidence — amounts below reporting threshold individually",
                    },
                )
            )

        # Phase 5: Collaborative detection
        delay += 2500
        events.append(
            StreamingEvent(
                event_type="intelligence",
                bank_id="shared",
                delay_ms=delay,
                payload={
                    "combined_confidence": 0.88,
                    "total_amount_detected": 50000,
                    "layering_hops": 3,
                    "description": "Layering pattern: $50K split across 3 banks, 6 transfers, reconsolidated",
                    "collaborative_assessment": "HIGH — classic layering pattern, individually invisible",
                },
            )
        )

        return Scenario(
            scenario_type=ScenarioType.MONEY_LAUNDERING,
            name="Layered Money Laundering",
            description="Large deposit → layering across 3 banks → reconsolidation. Individual confidence: ~35%. Collaborative: ~88%.",
            banks_involved=BANK_IDS,
            events=events,
            duration_seconds=delay / 1000,
        )

    def _create_card_testing(self) -> Scenario:
        events: list[StreamingEvent] = []
        delay = 0
        stolen_cards = [f"card_{i}" for i in range(5)]

        # Phase 1: Small test charges across banks
        for card in stolen_cards:
            for bank_id in ["bank_a", "bank_c"]:
                delay += int(self._rng.integers(800, 2000))
                events.append(
                    self._make_transaction_event(
                        bank_id=bank_id,
                        customer_id=card,
                        amount=self._rng.uniform(0.50, 4.99),
                        merchant=str(self._rng.choice(["grocery", "fuel", "subscription"])),
                        country="US",
                        device="web_browser",
                        risk_score=self._rng.uniform(0.05, 0.15),
                        delay_ms=delay,
                    )
                )

        # Phase 2: Large purchases with validated cards
        for card in stolen_cards[:3]:
            delay += int(self._rng.integers(1500, 3000))
            events.append(
                self._make_transaction_event(
                    bank_id=str(self._rng.choice(["bank_a", "bank_c"])),
                    customer_id=card,
                    amount=self._rng.uniform(2000, 6000),
                    merchant=str(self._rng.choice(["electronics", "jewelry"])),
                    country="US",
                    device="web_browser",
                    risk_score=self._rng.uniform(0.50, 0.70),
                    delay_ms=delay,
                )
            )

        # Phase 3: Alerts
        delay += 1500
        events.append(
            StreamingEvent(
                event_type="alert",
                bank_id="bank_a",
                delay_ms=delay,
                payload={
                    "severity": "medium",
                    "confidence": 0.55,
                    "reason_codes": ["AMT-ANOM", "MERCH-RISK"],
                    "description": "Unusual purchase pattern",
                },
            )
        )
        delay += 800
        events.append(
            StreamingEvent(
                event_type="alert",
                bank_id="bank_c",
                delay_ms=delay,
                payload={
                    "severity": "low",
                    "confidence": 0.40,
                    "reason_codes": ["VEL-001", "AMT-ANOM"],
                    "description": "Multiple small charges from web",
                },
            )
        )

        # Phase 4: Intelligence
        delay += 2000
        events.append(
            StreamingEvent(
                event_type="intelligence",
                bank_id="shared",
                delay_ms=delay,
                payload={
                    "combined_confidence": 0.92,
                    "cards_identified": len(stolen_cards),
                    "description": f"Card testing detected: {len(stolen_cards)} cards tested across 2 banks",
                    "collaborative_assessment": "CRITICAL — distributed card testing ring identified",
                },
            )
        )

        return Scenario(
            scenario_type=ScenarioType.CARD_TESTING,
            name="Distributed Card Testing",
            description="5 stolen cards tested with small charges across 2 banks before large purchases.",
            banks_involved=["bank_a", "bank_c"],
            events=events,
            duration_seconds=delay / 1000,
        )

    # ── Helpers ────────────────────────────────

    def _make_transaction_event(
        self,
        bank_id: str,
        customer_id: str,
        amount: float,
        merchant: str,
        country: str,
        device: str,
        risk_score: float,
        delay_ms: int,
    ) -> StreamingEvent:
        return StreamingEvent(
            event_type="transaction",
            bank_id=bank_id,
            delay_ms=delay_ms,
            payload={
                "transaction_id": str(uuid.uuid4())[:8],
                "customer_id": customer_id,
                "amount": round(amount, 2),
                "merchant_category": merchant,
                "country_code": country,
                "device_type": device,
                "risk_score": round(risk_score, 3),
                "bank_name": BANK_NAMES.get(bank_id, bank_id),
            },
        )
