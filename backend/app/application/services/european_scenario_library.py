"""European AML Monitoring Scenario Library & Hybrid Deterministic Rule Engine.

Implements:
1. 16 pre-configured European banking AML monitoring scenarios (FATF, EU AMLD6, EBA guidelines).
2. Rule evaluation engine validating transaction context against statutory thresholds.
3. Hybrid scoring synthesizer blending deterministic compliance penalties with ML/GNN probabilities.
4. Explainable decision generation and operational telemetry tracking.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.application.schemas.scenario_schemas import (
    AMLScenarioDefinition,
    AMLScenarioEvaluationRequest,
    AMLScenarioHit,
    BatchScenarioEvaluationRequest,
    BatchScenarioEvaluationResponse,
    HybridAction,
    HybridScoringResponse,
    ScenarioCategory,
    ScenarioLibraryResponse,
    ScenarioMetricsResponse,
    ScenarioSeverity,
    TransactionContext,
)

logger = logging.getLogger(__name__)

# Jurisdictions subject to FATF call for action (Black-List) and increased monitoring (Grey-List)
FATF_CALL_FOR_ACTION_JURISDICTIONS = {"KP", "IR", "MM"}
FATF_HIGH_RISK_JURISDICTIONS = {
    "KP", "IR", "MM", "SY", "RU", "YE", "CU", "SS", "HT", "ML", "BF", "MZ",
}
# Non-cooperative tax and offshore shell jurisdictions
OFFSHORE_SHELL_JURISDICTIONS = {
    "VG", "KY", "PA", "BZ", "SC", "MH", "BM", "LI", "VU", "CK",
}


def _build_default_scenario_library() -> dict[str, AMLScenarioDefinition]:
    """Instantiate the 16 pre-configured European AML scenarios."""
    scenarios: list[AMLScenarioDefinition] = [
        AMLScenarioDefinition(
            scenario_code="SCN_EUR_STRUCTURING_SUB_10K",
            name="Sub-€10,000 Threshold Structuring (Smurfing)",
            category=ScenarioCategory.STRUCTURING,
            severity=ScenarioSeverity.HIGH,
            base_penalty=320.0,
            regulatory_basis="EU AMLD6 Art. 33 & FATF Recommendation 10",
            description="Transaction structured immediately below the €10,000 European statutory reporting threshold (€8,000 - €9,999.99).",
            parameters={"min_amount": 8000.0, "max_amount": 9999.99},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_DORMANT_BURST_VELOCITY",
            name="Dormant Account Re-activation Burst",
            category=ScenarioCategory.BEHAVIORAL_ANOMALY,
            severity=ScenarioSeverity.HIGH,
            base_penalty=300.0,
            regulatory_basis="EBA Guidelines on ML/TF Risk Factors (EBA/GL/2021/02)",
            description="High-value activity or rapid velocity spike on an account previously dormant (>90 days).",
            parameters={"min_spike_amount": 5000.0, "min_burst_count": 3},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_RAPID_PASSTHROUGH_MULE",
            name="Rapid Pass-Through Mule Behavior",
            category=ScenarioCategory.MULE_ACTIVITY,
            severity=ScenarioSeverity.HIGH,
            base_penalty=350.0,
            regulatory_basis="Europol European Financial and Economic Crime Centre (EFECC) Mule Typologies",
            description="Inbound funds rapidly depleted with less than 10% retention, indicative of transit mule laundering.",
            parameters={"max_retention_ratio": 0.10, "min_depletion_amount": 3000.0},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_HIGH_RISK_FATF_CORRIDOR",
            name="High-Risk FATF / Sanctioned Corridor Transfer",
            category=ScenarioCategory.CORRIDOR_RISK,
            severity=ScenarioSeverity.CRITICAL,
            base_penalty=550.0,
            regulatory_basis="EU Delegated Regulation (EU) 2016/1675 on High-Risk Third Countries",
            description="Cross-border payment rail routing to or from a high-risk or sanctioned non-cooperative jurisdiction.",
            parameters={"high_risk_jurisdictions": list(FATF_HIGH_RISK_JURISDICTIONS)},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_ROUND_AMOUNT_LAYERING",
            name="Repetitive Round-Amount Layering",
            category=ScenarioCategory.CORPORATE_LAYERING,
            severity=ScenarioSeverity.MEDIUM,
            base_penalty=200.0,
            regulatory_basis="Wolfsberg Group AML Principles & FATF Typology Report",
            description="Transactions executed in exact round denominations without cents or commercial variance (multiples of €1,000/€5,000).",
            parameters={"min_round_amount": 5000.0},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_RAPID_FAN_OUT_DISPERSAL",
            name="Rapid Fan-Out Fund Dispersal",
            category=ScenarioCategory.STRUCTURING,
            severity=ScenarioSeverity.HIGH,
            base_penalty=280.0,
            regulatory_basis="EBA ML/TF Guidelines Section 8 & AMLD6 Art. 18",
            description="Single large credit followed by rapid outward dispersion to 4 or more distinct counterparties within 24 hours.",
            parameters={"min_distinct_counterparties": 4, "max_retention_ratio": 0.20},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_RAPID_FAN_IN_AGGREGATION",
            name="Rapid Fan-In Fund Aggregation",
            category=ScenarioCategory.MULE_ACTIVITY,
            severity=ScenarioSeverity.HIGH,
            base_penalty=280.0,
            regulatory_basis="FATF Report on Money Laundering Typologies & Smurfing Rings",
            description="Multiple small credits originating from 4 or more distinct sources pooled together for immediate outward transfer.",
            parameters={"min_distinct_originators": 4, "min_inbound_sum": 5000.0},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_OFFSHORE_SHELL_ROUNDTRIP",
            name="Offshore Non-Cooperative Jurisdiction Flow",
            category=ScenarioCategory.CORRIDOR_RISK,
            severity=ScenarioSeverity.HIGH,
            base_penalty=340.0,
            regulatory_basis="AMLD6 Art. 9 & EU List of Non-Cooperative Jurisdictions for Tax Purposes",
            description="Substantial payment interaction with entities domiciled in offshore secrecy havens (BVI, Cayman, Panama, etc.).",
            parameters={"min_amount": 15000.0, "offshore_jurisdictions": list(OFFSHORE_SHELL_JURISDICTIONS)},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_PEP_SANCTION_EXPOSURE",
            name="PEP or Sanctioned Entity Exposure",
            category=ScenarioCategory.CORRIDOR_RISK,
            severity=ScenarioSeverity.CRITICAL,
            base_penalty=600.0,
            regulatory_basis="AMLD6 Art. 20 (PEPs) & UN/EU Sanctions Regulations",
            description="Transaction involving a Politically Exposed Person (PEP) or an individual/entity on an official sanctions watchlist.",
            parameters={},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_CIRCULAR_MULE_RING",
            name="Topological Circular Mule Ring",
            category=ScenarioCategory.CORPORATE_LAYERING,
            severity=ScenarioSeverity.CRITICAL,
            base_penalty=480.0,
            regulatory_basis="FATF Guidance on Complex Layering Schemes & Graph Analysis",
            description="Funds traversing a closed directed cycle through multiple intermediary institutions returning to source.",
            parameters={"min_cycle_hops": 2},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_CRYPTO_ON_OFF_RAMP_BURST",
            name="High-Velocity Crypto Asset Gateway Flow",
            category=ScenarioCategory.DIGITAL_ASSET,
            severity=ScenarioSeverity.MEDIUM,
            base_penalty=240.0,
            regulatory_basis="EU Regulation (EU) 2023/1113 (Transfer of Funds / Travel Rule for Crypto)",
            description="Rapid or high-frequency fiat movements interacting with unhosted wallets or Crypto Asset Service Providers (CASPs).",
            parameters={"min_amount": 1000.0, "min_velocity": 3},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN",
            name="New Account High-Value Inflow & Drain",
            category=ScenarioCategory.BEHAVIORAL_ANOMALY,
            severity=ScenarioSeverity.HIGH,
            base_penalty=310.0,
            regulatory_basis="EBA Guidelines on Customer Due Diligence (EBA/GL/2021/02)",
            description="Account open less than 14 days receiving large sums (>€10,000 or >5x baseline) followed by complete liquidation.",
            parameters={"max_account_age_days": 14, "min_amount": 10000.0, "volume_multiplier": 5.0},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_HIGH_VELOCITY_NIGHTTIME",
            name="Non-Standard Nocturnal High-Velocity Transfer",
            category=ScenarioCategory.VELOCITY,
            severity=ScenarioSeverity.MEDIUM,
            base_penalty=180.0,
            regulatory_basis="ECB Operational Risk Framework for Instant Payments",
            description="High-value or rapid-burst transfers executed during nocturnal non-business hours (01:00 to 05:00).",
            parameters={"min_amount": 5000.0, "min_count": 3},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_TRADE_OVER_UNDER_INVOICING",
            name="Trade-Based Money Laundering (TBML) Invoicing Anomaly",
            category=ScenarioCategory.TRADE_BASED,
            severity=ScenarioSeverity.HIGH,
            base_penalty=330.0,
            regulatory_basis="FATF-Egmont Best Practices on Trade-Based Money Laundering",
            description="Declared unit price deviates by more than 300% or under 33% from the fair market value benchmark.",
            parameters={"over_invoicing_threshold": 3.0, "under_invoicing_threshold": 0.33},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_CASINO_GAMBLING_BURST",
            name="High-Stakes Gambling / Casino Layering Burst",
            category=ScenarioCategory.BEHAVIORAL_ANOMALY,
            severity=ScenarioSeverity.MEDIUM,
            base_penalty=220.0,
            regulatory_basis="AMLD6 Annex III & FATF Gambling Risk Indicators",
            description="Repetitive high-value deposits into gambling or online casino merchants without sustained gameplay velocity.",
            parameters={"min_amount": 2500.0, "min_velocity": 3},
        ),
        AMLScenarioDefinition(
            scenario_code="SCN_LARGE_CASH_OR_INSTANT_SURGE",
            name="Instant Payment Extraordinary Volume Surge",
            category=ScenarioCategory.VELOCITY,
            severity=ScenarioSeverity.HIGH,
            base_penalty=290.0,
            regulatory_basis="EPC SEPA Instant Credit Transfer Scheme Rulebook",
            description="Single SEPA Instant credit transfer exceeding €50,000 or single transfer exceeding 10x average daily account volume.",
            parameters={"sepa_large_threshold": 50000.0, "volume_surge_multiplier": 10.0},
        ),
    ]
    return {s.scenario_code: s for s in scenarios}


class EuropeanScenarioLibraryService:
    """Thread-safe, multi-tenant European AML scenario and hybrid rule engine."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._library: dict[str, AMLScenarioDefinition] = _build_default_scenario_library()
        # Per-tenant telemetry: tenant_id -> state dict
        self._telemetry: dict[str, dict[str, Any]] = {}

    def get_library(self) -> ScenarioLibraryResponse:
        """Return all active scenario definitions."""
        with self._lock:
            return ScenarioLibraryResponse(
                total_scenarios=len(self._library),
                scenarios=list(self._library.values()),
            )

    def get_scenario(self, scenario_code: str) -> AMLScenarioDefinition | None:
        """Retrieve definition for a single scenario code."""
        with self._lock:
            return self._library.get(scenario_code)

    def evaluate_transaction(
        self,
        tenant_id: str,
        request: AMLScenarioEvaluationRequest,
    ) -> HybridScoringResponse:
        """Evaluate a transaction context against the European scenario library and compute hybrid score."""
        txn: TransactionContext = request.transaction
        triggered: list[AMLScenarioHit] = []

        # 1. SCN_EUR_STRUCTURING_SUB_10K
        if 8000.0 <= txn.amount <= 9999.99 and txn.currency == "EUR":
            s = self._library["SCN_EUR_STRUCTURING_SUB_10K"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Transfer of €{txn.amount:,.2f} positioned just below the €10,000 statutory reporting threshold.",
                    evidence={"amount": txn.amount, "currency": txn.currency},
                )
            )

        # 2. SCN_DORMANT_BURST_VELOCITY
        if txn.is_dormant_account and (txn.amount >= 5000.0 or txn.transaction_count_last_1h >= 3):
            s = self._library["SCN_DORMANT_BURST_VELOCITY"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale="Sudden high-velocity/high-amount activity on an account flagged as historically dormant.",
                    evidence={
                        "is_dormant": txn.is_dormant_account,
                        "amount": txn.amount,
                        "count_1h": txn.transaction_count_last_1h,
                    },
                )
            )

        # 3. SCN_RAPID_PASSTHROUGH_MULE
        if txn.funds_retention_ratio < 0.10 and (
            txn.amount >= 3000.0 or (txn.inbound_credits_last_1h >= 3000.0 and txn.outbound_debits_last_1h >= 2700.0)
        ):
            s = self._library["SCN_RAPID_PASSTHROUGH_MULE"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Pass-through depletion observed: retention ratio of {txn.funds_retention_ratio:.1%} indicates transit mule laundering.",
                    evidence={
                        "retention_ratio": txn.funds_retention_ratio,
                        "inbound_1h": txn.inbound_credits_last_1h,
                        "outbound_1h": txn.outbound_debits_last_1h,
                    },
                )
            )

        # 4. SCN_HIGH_RISK_FATF_CORRIDOR
        corridor_hit = False
        corridor_detail = ""
        if txn.origin_country.upper() in FATF_HIGH_RISK_JURISDICTIONS:
            corridor_hit = True
            corridor_detail = f"Origin country '{txn.origin_country}' is an FATF high-risk jurisdiction."
        elif txn.destination_country.upper() in FATF_HIGH_RISK_JURISDICTIONS:
            corridor_hit = True
            corridor_detail = f"Destination country '{txn.destination_country}' is an FATF high-risk jurisdiction."

        if corridor_hit:
            s = self._library["SCN_HIGH_RISK_FATF_CORRIDOR"]
            # Extra penalty for call-for-action black list
            penalty = (
                s.base_penalty + 150.0
                if (txn.origin_country.upper() in FATF_CALL_FOR_ACTION_JURISDICTIONS or txn.destination_country.upper() in FATF_CALL_FOR_ACTION_JURISDICTIONS)
                else s.base_penalty
            )
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=min(1000.0, penalty),
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=corridor_detail,
                    evidence={"origin": txn.origin_country, "destination": txn.destination_country},
                )
            )

        # 5. SCN_ROUND_AMOUNT_LAYERING
        if txn.amount >= 5000.0 and (txn.amount % 1000.0 == 0.0 or txn.amount % 5000.0 == 0.0):
            s = self._library["SCN_ROUND_AMOUNT_LAYERING"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Transfer of exactly €{txn.amount:,.2f} represents artificial round-amount placement.",
                    evidence={"amount": txn.amount},
                )
            )

        # 6. SCN_RAPID_FAN_OUT_DISPERSAL
        if txn.recent_distinct_counterparties_24h >= 4 and txn.funds_retention_ratio <= 0.20:
            s = self._library["SCN_RAPID_FAN_OUT_DISPERSAL"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Dispersal across {txn.recent_distinct_counterparties_24h} distinct counterparties with only {txn.funds_retention_ratio:.1%} retained.",
                    evidence={"distinct_counterparties": txn.recent_distinct_counterparties_24h},
                )
            )

        # 7. SCN_RAPID_FAN_IN_AGGREGATION
        if txn.recent_distinct_counterparties_24h >= 4 and txn.inbound_credits_last_1h >= 5000.0:
            s = self._library["SCN_RAPID_FAN_IN_AGGREGATION"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Aggregation from {txn.recent_distinct_counterparties_24h} originators totaling €{txn.inbound_credits_last_1h:,.2f} within 1h.",
                    evidence={"distinct_originators": txn.recent_distinct_counterparties_24h, "inbound_1h": txn.inbound_credits_last_1h},
                )
            )

        # 8. SCN_OFFSHORE_SHELL_ROUNDTRIP
        if (
            (txn.origin_country.upper() in OFFSHORE_SHELL_JURISDICTIONS or txn.destination_country.upper() in OFFSHORE_SHELL_JURISDICTIONS)
            and txn.amount >= 15000.0
        ):
            s = self._library["SCN_OFFSHORE_SHELL_ROUNDTRIP"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"High-value transfer of €{txn.amount:,.2f} interfacing with non-cooperative offshore tax haven.",
                    evidence={"origin": txn.origin_country, "destination": txn.destination_country, "amount": txn.amount},
                )
            )

        # 9. SCN_PEP_SANCTION_EXPOSURE
        pep_sanction_hit = False
        pep_detail = ""
        if txn.originator_is_sanctioned or txn.beneficiary_is_sanctioned:
            pep_sanction_hit = True
            pep_detail = "Direct party to transaction is listed on an official UN/EU/OFAC Sanctions List."
        elif txn.originator_is_pep or txn.beneficiary_is_pep:
            pep_sanction_hit = True
            pep_detail = "Party to transaction is classified as a Politically Exposed Person (PEP)."

        if pep_sanction_hit:
            s = self._library["SCN_PEP_SANCTION_EXPOSURE"]
            penalty = 800.0 if (txn.originator_is_sanctioned or txn.beneficiary_is_sanctioned) else s.base_penalty
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=min(1000.0, penalty),
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=pep_detail,
                    evidence={
                        "originator_pep": txn.originator_is_pep,
                        "originator_sanctioned": txn.originator_is_sanctioned,
                        "beneficiary_pep": txn.beneficiary_is_pep,
                        "beneficiary_sanctioned": txn.beneficiary_is_sanctioned,
                    },
                )
            )

        # 10. SCN_CIRCULAR_MULE_RING
        if txn.cyclic_mule_hops is not None and txn.cyclic_mule_hops >= 2:
            s = self._library["SCN_CIRCULAR_MULE_RING"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Directed circular transaction cycle identified spanning {txn.cyclic_mule_hops} hops.",
                    evidence={"cycle_hops": txn.cyclic_mule_hops},
                )
            )

        # 11. SCN_CRYPTO_ON_OFF_RAMP_BURST
        if txn.is_crypto_service_provider and (txn.amount >= 1000.0 or txn.transaction_count_last_1h >= 3):
            s = self._library["SCN_CRYPTO_ON_OFF_RAMP_BURST"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale="Accelerated volume or frequency interacting with a Crypto Asset Service Provider (CASP).",
                    evidence={"is_casp": txn.is_crypto_service_provider, "amount": txn.amount, "velocity": txn.transaction_count_last_1h},
                )
            )

        # 12. SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN
        if txn.originator_account_age_days <= 14 and (
            txn.amount >= 10000.0 or (txn.account_average_daily_volume > 0 and txn.amount >= 5.0 * txn.account_average_daily_volume)
        ):
            s = self._library["SCN_NEW_ACCOUNT_HIGH_VALUE_DRAIN"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"New account ({txn.originator_account_age_days}d old) executing an extraordinary transfer of €{txn.amount:,.2f}.",
                    evidence={"account_age_days": txn.originator_account_age_days, "amount": txn.amount},
                )
            )

        # 13. SCN_HIGH_VELOCITY_NIGHTTIME
        if txn.is_nighttime_execution and (txn.amount >= 5000.0 or txn.transaction_count_last_1h >= 3):
            s = self._library["SCN_HIGH_VELOCITY_NIGHTTIME"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale="Execution during non-standard nocturnal hours (01:00-05:00) with elevated volume or burst count.",
                    evidence={"nighttime": txn.is_nighttime_execution, "amount": txn.amount, "count_1h": txn.transaction_count_last_1h},
                )
            )

        # 14. SCN_TRADE_OVER_UNDER_INVOICING
        if txn.unit_price_deviation_ratio is not None and (
            txn.unit_price_deviation_ratio >= 3.0 or txn.unit_price_deviation_ratio <= 0.33
        ):
            s = self._library["SCN_TRADE_OVER_UNDER_INVOICING"]
            direction = "Over-invoicing" if txn.unit_price_deviation_ratio >= 3.0 else "Under-invoicing"
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"{direction} detected: unit price is {txn.unit_price_deviation_ratio:.2f}x benchmark fair market value.",
                    evidence={"deviation_ratio": txn.unit_price_deviation_ratio},
                )
            )

        # 15. SCN_CASINO_GAMBLING_BURST
        mcc = (txn.merchant_category or "").lower()
        if ("gambling" in mcc or "casino" in mcc or "betting" in mcc) and (
            txn.amount >= 2500.0 or txn.transaction_count_last_1h >= 3
        ):
            s = self._library["SCN_CASINO_GAMBLING_BURST"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Gambling/casino merchant flow with amount €{txn.amount:,.2f} or velocity {txn.transaction_count_last_1h}.",
                    evidence={"merchant": txn.merchant_category, "amount": txn.amount},
                )
            )

        # 16. SCN_LARGE_CASH_OR_INSTANT_SURGE
        if (
            (txn.payment_rail == "SEPA_INSTANT" and txn.amount >= 50000.0)
            or (txn.account_average_daily_volume > 0 and txn.amount >= 10.0 * txn.account_average_daily_volume)
        ):
            s = self._library["SCN_LARGE_CASH_OR_INSTANT_SURGE"]
            triggered.append(
                AMLScenarioHit(
                    scenario_code=s.scenario_code,
                    scenario_name=s.name,
                    category=s.category,
                    severity=s.severity,
                    penalty_score=s.base_penalty,
                    regulatory_citation=s.regulatory_basis,
                    description=s.description,
                    trigger_rationale=f"Instant credit transfer of €{txn.amount:,.2f} represents an extraordinary surge above baseline.",
                    evidence={"amount": txn.amount, "rail": txn.payment_rail},
                )
            )

        # ---------------------------------------------------------
        # HYBRID SYNTHESIZER: Blend Deterministic Rules + Federated ML
        # ---------------------------------------------------------
        total_rule_penalty = min(1000.0, sum(h.penalty_score for h in triggered))
        ml_penalty_equivalent = min(1000.0, request.ml_risk_score * 1000.0)

        # Check for mandatory statutory overrides
        regulatory_override = False
        override_reason: str | None = None

        if request.strict_regulatory_override:
            # 1. Sanctions trigger forces instant BLOCK
            if txn.originator_is_sanctioned or txn.beneficiary_is_sanctioned:
                regulatory_override = True
                override_reason = "Mandatory freeze: direct counterparty match against official UN/EU/OFAC Sanctions List."
            # 2. FATF Call for Action (Black-List) forces instant BLOCK
            elif (
                txn.origin_country.upper() in FATF_CALL_FOR_ACTION_JURISDICTIONS
                or txn.destination_country.upper() in FATF_CALL_FOR_ACTION_JURISDICTIONS
            ):
                regulatory_override = True
                override_reason = "Mandatory freeze: payment corridor with FATF Call for Action (Black-List) jurisdiction."

        # Compute hybrid composite score
        # Base weights: 55% deterministic compliance rules, 45% probabilistic ML model
        rule_weight = 0.55
        ml_weight = 0.45
        blended_score = (rule_weight * total_rule_penalty) + (ml_weight * ml_penalty_equivalent)

        # Boost by GNN embedding norm if present
        if request.gnn_anomaly_embedding_norm is not None and request.gnn_anomaly_embedding_norm > 0:
            gnn_boost = min(150.0, request.gnn_anomaly_embedding_norm * 30.0)
            blended_score += gnn_boost

        if regulatory_override:
            composite_score = max(950.0, blended_score)
            action = HybridAction.BLOCK
        else:
            composite_score = min(1000.0, blended_score)
            if composite_score >= 850.0:
                action = HybridAction.BLOCK
            elif composite_score >= 700.0:
                action = HybridAction.SUSPEND
            elif composite_score >= 400.0:
                action = HybridAction.MANUAL_REVIEW
            else:
                action = HybridAction.ALLOW

        # Narrative Generation
        if regulatory_override:
            narrative = f"REGULATORY OVERRIDE ENFORCED: {override_reason}"
        elif triggered:
            scenario_names = ", ".join(f"[{h.scenario_code}]" for h in triggered)
            narrative = (
                f"Action '{action.value}' decided with composite risk {composite_score:.1f}/1000. "
                f"Triggered {len(triggered)} European AML scenario(s): {scenario_names}. "
                f"Rule penalty contribution: {total_rule_penalty:.1f}pts; ML probability contribution: {ml_penalty_equivalent:.1f}pts."
            )
        else:
            narrative = (
                f"Transaction cleared all 16 European AML scenarios without rule triggers. "
                f"Action '{action.value}' based on background ML risk score ({ml_penalty_equivalent:.1f}/1000)."
            )

        response = HybridScoringResponse(
            transaction_id=txn.transaction_id,
            action=action,
            composite_risk_score=round(composite_score, 1),
            rule_penalty_score=round(total_rule_penalty, 1),
            ml_risk_score=request.ml_risk_score,
            ml_penalty_equivalent=round(ml_penalty_equivalent, 1),
            regulatory_override_applied=regulatory_override,
            override_reason=override_reason,
            triggered_scenarios=triggered,
            total_scenarios_evaluated=len(self._library),
            total_scenarios_triggered=len(triggered),
            explainability_narrative=narrative,
            evaluated_at=datetime.now(UTC),
        )

        # Update telemetry
        self._record_telemetry(tenant_id, response)
        return response

    def evaluate_batch(
        self,
        tenant_id: str,
        request: BatchScenarioEvaluationRequest,
    ) -> BatchScenarioEvaluationResponse:
        """Batch evaluation of multiple transactions."""
        results: list[HybridScoringResponse] = []
        blocked = 0
        suspended = 0
        manual = 0
        allowed = 0

        for req in request.evaluations:
            res = self.evaluate_transaction(tenant_id, req)
            results.append(res)
            if res.action == HybridAction.BLOCK:
                blocked += 1
            elif res.action == HybridAction.SUSPEND:
                suspended += 1
            elif res.action == HybridAction.MANUAL_REVIEW:
                manual += 1
            else:
                allowed += 1

        return BatchScenarioEvaluationResponse(
            total_processed=len(results),
            total_blocked=blocked,
            total_suspended=suspended,
            total_flagged_for_review=manual,
            total_allowed=allowed,
            results=results,
        )

    def _record_telemetry(self, tenant_id: str, response: HybridScoringResponse) -> None:
        """Record evaluation telemetry under thread lock."""
        with self._lock:
            state = self._telemetry.setdefault(
                tenant_id,
                {
                    "total_evaluations": 0,
                    "action_breakdown": Counter(),
                    "scenario_hits": Counter(),
                    "total_score_sum": 0.0,
                    "last_evaluation_time": None,
                },
            )
            state["total_evaluations"] += 1
            state["action_breakdown"][response.action.value] += 1
            state["total_score_sum"] += response.composite_risk_score
            state["last_evaluation_time"] = response.evaluated_at
            for hit in response.triggered_scenarios:
                state["scenario_hits"][hit.scenario_code] += 1

    def get_metrics(self, tenant_id: str) -> ScenarioMetricsResponse:
        """Return operational telemetry for tenant."""
        with self._lock:
            state = self._telemetry.get(tenant_id)
            if not state or state["total_evaluations"] == 0:
                return ScenarioMetricsResponse(
                    tenant_id=tenant_id,
                    total_evaluations=0,
                    action_breakdown={},
                    top_triggered_scenarios={},
                    average_composite_score=0.0,
                    last_evaluation_time=None,
                )
            total = state["total_evaluations"]
            avg_score = state["total_score_sum"] / total if total > 0 else 0.0
            return ScenarioMetricsResponse(
                tenant_id=tenant_id,
                total_evaluations=total,
                action_breakdown=dict(state["action_breakdown"]),
                top_triggered_scenarios=dict(state["scenario_hits"]),
                average_composite_score=round(avg_score, 1),
                last_evaluation_time=state["last_evaluation_time"],
            )


# Global singleton instance
_european_scenario_service_instance: EuropeanScenarioLibraryService | None = None
_service_init_lock = threading.Lock()


def get_european_scenario_service() -> EuropeanScenarioLibraryService:
    """Retrieve or initialize the European scenario library singleton."""
    global _european_scenario_service_instance
    if _european_scenario_service_instance is None:
        with _service_init_lock:
            if _european_scenario_service_instance is None:
                _european_scenario_service_instance = EuropeanScenarioLibraryService()
    return _european_scenario_service_instance
