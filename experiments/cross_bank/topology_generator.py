"""Deterministic Multi-Institution Fraud Ring & Topology Generator (CFI-CrossBank-01).

Implements 7 canonical cross-bank synthetic fraud scenarios with strict ground-truth
labelling, partial information horizon partitioning, and reproducible random seeds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.app.domain.models.consortium import (  # noqa: E402
    ConsortiumNode,
    ConsortiumScenarioType,
    InstitutionType,
    ScenarioDefinition,
)

# Standard Consortium Institutions
DEFAULT_CONSORTIUM_NODES: list[ConsortiumNode] = [
    ConsortiumNode(
        bank_id="bank_a",
        name="Bank Alpha",
        institution_type=InstitutionType.RETAIL,
        volume_share=0.50,
        account_count=5000,
        positive_prevalence=0.015,
        is_zero_positive=False,
    ),
    ConsortiumNode(
        bank_id="bank_b",
        name="Bank Beta",
        institution_type=InstitutionType.COMMERCIAL,
        volume_share=0.30,
        account_count=3000,
        positive_prevalence=0.012,
        is_zero_positive=False,
    ),
    ConsortiumNode(
        bank_id="bank_c",
        name="Bank Gamma",
        institution_type=InstitutionType.CROSS_BORDER,
        volume_share=0.20,
        account_count=2000,
        positive_prevalence=0.008,
        is_zero_positive=False,
    ),
]

# Canonical 7 Scenarios
SCENARIO_DEFINITIONS: dict[str, ScenarioDefinition] = {
    "SCENARIO_1": ScenarioDefinition(
        scenario_id="SCENARIO_1",
        scenario_type=ConsortiumScenarioType.SCENARIO_1_LOCALIZED,
        title="Scenario 1: Single-Bank Localized Fraud",
        description="Internal account structuring and mule hopping confined entirely within Bank Alpha.",
        risk_typology="LOCAL_SMURFING",
        participating_banks=["bank_a"],
        hop_count=2,
        expected_isolated_vulnerability="Observable locally by Bank Alpha; external banks have zero exposure.",
        collaborative_advantage="Federated model matches or slightly refines local detection without boundary loss.",
    ),
    "SCENARIO_2": ScenarioDefinition(
        scenario_id="SCENARIO_2",
        scenario_type=ConsortiumScenarioType.SCENARIO_2_TWO_BANK_LAYERING,
        title="Scenario 2: Two-Bank Cross-Institutional Layering Chain",
        description="Rapid cross-institution transfer originating at Bank Alpha and layering into Bank Beta.",
        risk_typology="CROSS_BANK_LAYERING",
        participating_banks=["bank_a", "bank_b"],
        hop_count=2,
        expected_isolated_vulnerability="Bank Alpha sees outgoing wire; Bank Beta sees incoming wire without origin context.",
        collaborative_advantage="Federated model links outward disbursement velocity with inward layering signatures.",
    ),
    "SCENARIO_3": ScenarioDefinition(
        scenario_id="SCENARIO_3",
        scenario_type=ConsortiumScenarioType.SCENARIO_3_THREE_BANK_CYCLE,
        title="Scenario 3: Three-Bank Cyclic Laundering Ring (A -> B -> C -> A)",
        description="Closed cyclic multi-hop ring spanning Bank Alpha -> Bank Beta -> Bank Gamma -> Bank Alpha.",
        risk_typology="CYCLIC_MULE_RING",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hop_count=3,
        expected_isolated_vulnerability="Each bank observes only 1 entry and 1 exit. The intermediate B->C leg is completely invisible to Bank Alpha.",
        collaborative_advantage="Federated topological consensus detects the circular flow and smurfing volume preservation.",
    ),
    "SCENARIO_4": ScenarioDefinition(
        scenario_id="SCENARIO_4",
        scenario_type=ConsortiumScenarioType.SCENARIO_4_BEHAVIOR_SHIFTING,
        title="Scenario 4: Behavior-Shifting Multi-Bank Smurfing to High-Value Cash-Out",
        description="Sub-threshold structuring at Bank Alpha ($8.5k–$9.8k), consolidation at Bank Beta, and high-value wire ($180k) at Bank Gamma.",
        risk_typology="BEHAVIOR_SHIFTING_STRUCTURING",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hop_count=3,
        expected_isolated_vulnerability="Bank Alpha sees small benign transfers; Bank Gamma sees large wire with zero local suspicion history.",
        collaborative_advantage="Collaborative weights capture the cross-bank consolidation trajectory across disparate profiles.",
    ),
    "SCENARIO_5": ScenarioDefinition(
        scenario_id="SCENARIO_5",
        scenario_type=ConsortiumScenarioType.SCENARIO_5_NON_IID_PROFILES,
        title="Scenario 5: Highly Non-IID Institutional Archetypes",
        description="Retail Consumer (Bank A), Commercial B2B Wholesale (Bank B), and Cross-Border Remittance (Bank C) with divergent feature distributions.",
        risk_typology="CROSS_ARCHETYPE_ARBITRAGE",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hop_count=2,
        expected_isolated_vulnerability="Local models overfit to their narrow business model and fail on cross-archetype laundering.",
        collaborative_advantage="Federated consensus generalizes across retail, corporate, and international transaction rails.",
    ),
    "SCENARIO_6": ScenarioDefinition(
        scenario_id="SCENARIO_6",
        scenario_type=ConsortiumScenarioType.SCENARIO_6_EXTREME_RARITY,
        title="Scenario 6: Extreme Positive Sample Rarity at Bank Gamma",
        description="Bank Alpha and Beta have adequate training fraud, but Bank Gamma has only 2 positive historical incidents (0.05% prevalence).",
        risk_typology="SAMPLE_STARVATION",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hop_count=2,
        expected_isolated_vulnerability="Bank Gamma's local supervised classifier fails to converge due to severe positive sample starvation.",
        collaborative_advantage="Federated model transfers discriminative feature boundaries from Bank Alpha and Beta to Bank Gamma.",
    ),
    "SCENARIO_7": ScenarioDefinition(
        scenario_id="SCENARIO_7",
        scenario_type=ConsortiumScenarioType.SCENARIO_7_ZERO_POSITIVE_TRANSFER,
        title="Scenario 7: Zero Positive Historical Examples at Bank Gamma (Zero-Positive Transfer)",
        description="Bank Gamma has exactly ZERO positive fraud cases in its historical training log (cold-start institution).",
        risk_typology="ZERO_SHOT_INSTITUTIONAL_TRANSFER",
        participating_banks=["bank_a", "bank_b", "bank_c"],
        hop_count=2,
        expected_isolated_vulnerability="Bank Gamma isolated classifier has 0% detection rate because it has never observed a fraud label.",
        collaborative_advantage="Consortium federated weights grant Bank Gamma immediate high-recall zero-shot detection on incoming attacks.",
    ),
}

FEATURE_COLUMNS: list[str] = [
    "amount",
    "log_amount",
    "is_cross_bank",
    "step",
    "hour_of_day",
    "is_weekend",
    "source_out_degree",
    "target_in_degree",
    "velocity_burst",
    "rapid_hop_indicator",
    "structuring_indicator",
    "high_value_flag",
    "bank_profile_encoded",
    "rail_encoded",
]


class CrossBankNetworkGenerator:
    """Deterministic generator for multi-institution synthetic fraud topologies."""

    def __init__(
        self,
        nodes: list[ConsortiumNode] | None = None,
        seed: int = 42,
    ) -> None:
        self.nodes = nodes or DEFAULT_CONSORTIUM_NODES
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        self.bank_map = {n.bank_id: n for n in self.nodes}

    def generate_benchmark_dataset(
        self,
        n_total_transactions: int = 20000,
        timesteps: int = 168,  # 1 week of hourly steps
    ) -> pd.DataFrame:
        """Generate full consortium dataset with background traffic and all 7 scenarios."""
        # 1. Background Benign Transactions
        records: list[dict[str, object]] = []
        n_benign = int(n_total_transactions * 0.96)
        records.extend(self._generate_benign_traffic(n_benign, timesteps))

        # 2. Inject Illicit Scenarios 1 through 7
        for scenario_id in SCENARIO_DEFINITIONS:
            scenario_records = self._generate_scenario_traffic(scenario_id, timesteps)
            records.extend(scenario_records)

        df = pd.DataFrame(records)
        df = df.sort_values(by=["step", "transaction_id"]).reset_index(drop=True)

        # 3. Compute Sequential Graph and Tabular Features
        df = self._enrich_features(df)
        return df

    def _generate_benign_traffic(self, n_samples: int, timesteps: int) -> list[dict[str, object]]:
        """Generate realistic background traffic distributed by bank volume shares."""
        records: list[dict[str, object]] = []
        bank_ids = [n.bank_id for n in self.nodes]
        bank_probs = [n.volume_share for n in self.nodes]

        for i in range(n_samples):
            step = self.rng.randint(0, timesteps)
            source_bank = self.rng.choice(bank_ids, p=bank_probs)

            # 75% intra-bank, 25% inter-bank
            if self.rng.rand() < 0.75:
                target_bank = source_bank
            else:
                other_banks = [b for b in bank_ids if b != source_bank]
                target_bank = self.rng.choice(other_banks)

            # Profile-specific amount distribution
            source_profile = self.bank_map[source_bank].institution_type
            if source_profile == InstitutionType.RETAIL:
                amount = float(np.clip(self.rng.lognormal(mean=4.5, sigma=1.0), 5.0, 5000.0))
                rail = self.rng.choice(["SEPA_INSTANT", "INTERNAL", "ACH"], p=[0.5, 0.4, 0.1])
            elif source_profile == InstitutionType.COMMERCIAL:
                amount = float(np.clip(self.rng.lognormal(mean=8.5, sigma=1.2), 100.0, 250000.0))
                rail = self.rng.choice(["TARGET2", "SWIFT", "SEPA_INSTANT"], p=[0.5, 0.3, 0.2])
            else:  # CROSS_BORDER
                amount = float(np.clip(self.rng.lognormal(mean=6.8, sigma=1.1), 50.0, 75000.0))
                rail = self.rng.choice(["SWIFT", "WIRE", "SEPA_INSTANT"], p=[0.6, 0.3, 0.1])

            src_acc_id = f"{source_bank}_acc_{self.rng.randint(1, self.bank_map[source_bank].account_count)}"
            tgt_acc_id = f"{target_bank}_acc_{self.rng.randint(1, self.bank_map[target_bank].account_count)}"

            records.append({
                "transaction_id": f"tx_bg_{i:06d}",
                "step": step,
                "source_bank": source_bank,
                "target_bank": target_bank,
                "source_account": src_acc_id,
                "target_account": tgt_acc_id,
                "amount": round(amount, 2),
                "payment_rail": rail,
                "is_laundering": 0,
                "scenario_id": None,
                "hop_index": None,
            })

        return records

    def _generate_scenario_traffic(
        self, scenario_id: str, timesteps: int
    ) -> list[dict[str, object]]:
        """Generate deterministic multi-hop patterns for each scenario."""
        records: list[dict[str, object]] = []

        if scenario_id == "SCENARIO_1":
            # Scenario 1: Localized smurfing within Bank Alpha (20 instances)
            for inst in range(25):
                base_step = self.rng.randint(5, timesteps - 10)
                mule1 = f"bank_a_mule1_{inst:02d}"
                mule2 = f"bank_a_mule2_{inst:02d}"
                exit_acc = f"bank_a_exit_{inst:02d}"

                # Hop 0: Smurfing into mule 1
                records.append({
                    "transaction_id": f"tx_sc1_{inst:02d}_0",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_a",
                    "source_account": f"bank_a_acc_{self.rng.randint(1, 1000)}",
                    "target_account": mule1,
                    "amount": round(float(self.rng.uniform(9200.0, 9850.0)), 2),
                    "payment_rail": "INTERNAL",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_1",
                    "hop_index": 0,
                })
                # Hop 1: Rapid internal transfer to mule 2
                records.append({
                    "transaction_id": f"tx_sc1_{inst:02d}_1",
                    "step": base_step + 1,
                    "source_bank": "bank_a",
                    "target_bank": "bank_a",
                    "source_account": mule1,
                    "target_account": mule2,
                    "amount": round(float(self.rng.uniform(9100.0, 9750.0)), 2),
                    "payment_rail": "INTERNAL",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_1",
                    "hop_index": 1,
                })
                # Hop 2: Cash-out exit
                records.append({
                    "transaction_id": f"tx_sc1_{inst:02d}_2",
                    "step": base_step + 2,
                    "source_bank": "bank_a",
                    "target_bank": "bank_a",
                    "source_account": mule2,
                    "target_account": exit_acc,
                    "amount": round(float(self.rng.uniform(8900.0, 9600.0)), 2),
                    "payment_rail": "INTERNAL",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_1",
                    "hop_index": 2,
                })

        elif scenario_id == "SCENARIO_2":
            # Scenario 2: Two-bank layering chain (Bank Alpha -> Bank Beta -> Cashout) (25 instances)
            for inst in range(25):
                base_step = self.rng.randint(5, timesteps - 10)
                src_a = f"bank_a_orig_{inst:02d}"
                mule_b = f"bank_b_layer_{inst:02d}"
                dest_b = f"bank_b_dest_{inst:02d}"

                # Hop 0: A -> B
                records.append({
                    "transaction_id": f"tx_sc2_{inst:02d}_0",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": src_a,
                    "target_account": mule_b,
                    "amount": round(float(self.rng.uniform(18000.0, 24000.0)), 2),
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_2",
                    "hop_index": 0,
                })
                # Hop 1: Rapid internal layering inside B
                records.append({
                    "transaction_id": f"tx_sc2_{inst:02d}_1",
                    "step": base_step + 1,
                    "source_bank": "bank_b",
                    "target_bank": "bank_b",
                    "source_account": mule_b,
                    "target_account": dest_b,
                    "amount": round(float(self.rng.uniform(17500.0, 23500.0)), 2),
                    "payment_rail": "INTERNAL",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_2",
                    "hop_index": 1,
                })

        elif scenario_id == "SCENARIO_3":
            # Scenario 3: Three-bank cyclic ring (A -> B -> C -> A) (30 instances)
            for inst in range(30):
                base_step = self.rng.randint(5, timesteps - 10)
                acc_a = f"bank_a_ring_{inst:02d}"
                acc_b = f"bank_b_ring_{inst:02d}"
                acc_c = f"bank_c_ring_{inst:02d}"
                cycle_amount = round(float(self.rng.uniform(35000.0, 48000.0)), 2)

                # Hop 0: A -> B
                records.append({
                    "transaction_id": f"tx_sc3_{inst:02d}_0",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": acc_a,
                    "target_account": acc_b,
                    "amount": cycle_amount,
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_3",
                    "hop_index": 0,
                })
                # Hop 1: B -> C (INVISIBLE TO BANK A IN ISOLATION!)
                records.append({
                    "transaction_id": f"tx_sc3_{inst:02d}_1",
                    "step": base_step + 1,
                    "source_bank": "bank_b",
                    "target_bank": "bank_c",
                    "source_account": acc_b,
                    "target_account": acc_c,
                    "amount": round(cycle_amount * float(self.rng.uniform(0.97, 0.99)), 2),
                    "payment_rail": "TARGET2",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_3",
                    "hop_index": 1,
                })
                # Hop 2: C -> A (Closes Cycle)
                records.append({
                    "transaction_id": f"tx_sc3_{inst:02d}_2",
                    "step": base_step + 2,
                    "source_bank": "bank_c",
                    "target_bank": "bank_a",
                    "source_account": acc_c,
                    "target_account": acc_a,
                    "amount": round(cycle_amount * float(self.rng.uniform(0.94, 0.96)), 2),
                    "payment_rail": "SWIFT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_3",
                    "hop_index": 2,
                })

        elif scenario_id == "SCENARIO_4":
            # Scenario 4: Behavior-shifting (Smurfing at A -> Consolidation at B -> High-value cashout at C) (25 instances)
            for inst in range(25):
                base_step = self.rng.randint(5, timesteps - 10)
                smurf_a1 = f"bank_a_smurf1_{inst:02d}"
                smurf_a2 = f"bank_a_smurf2_{inst:02d}"
                pool_b = f"bank_b_pool_{inst:02d}"
                cashout_c = f"bank_c_wire_{inst:02d}"

                amt1 = round(float(self.rng.uniform(9100.0, 9600.0)), 2)
                amt2 = round(float(self.rng.uniform(9200.0, 9700.0)), 2)

                # Hop 0: Small smurfs into B
                records.append({
                    "transaction_id": f"tx_sc4_{inst:02d}_0a",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": smurf_a1,
                    "target_account": pool_b,
                    "amount": amt1,
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_4",
                    "hop_index": 0,
                })
                records.append({
                    "transaction_id": f"tx_sc4_{inst:02d}_0b",
                    "step": base_step + 1,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": smurf_a2,
                    "target_account": pool_b,
                    "amount": amt2,
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_4",
                    "hop_index": 0,
                })
                # Hop 1: High-value consolidated wire to C
                records.append({
                    "transaction_id": f"tx_sc4_{inst:02d}_1",
                    "step": base_step + 3,
                    "source_bank": "bank_b",
                    "target_bank": "bank_c",
                    "source_account": pool_b,
                    "target_account": cashout_c,
                    "amount": round((amt1 + amt2) * 0.98, 2),
                    "payment_rail": "TARGET2",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_4",
                    "hop_index": 1,
                })

        elif scenario_id == "SCENARIO_5":
            # Scenario 5: Non-IID archetype cross-bank flow (20 instances)
            for inst in range(20):
                base_step = self.rng.randint(5, timesteps - 10)
                # Retail to Commercial to Remittance
                records.append({
                    "transaction_id": f"tx_sc5_{inst:02d}_0",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": f"bank_a_retail_{inst:02d}",
                    "target_account": f"bank_b_comm_{inst:02d}",
                    "amount": round(float(self.rng.uniform(12000.0, 18000.0)), 2),
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_5",
                    "hop_index": 0,
                })
                records.append({
                    "transaction_id": f"tx_sc5_{inst:02d}_1",
                    "step": base_step + 2,
                    "source_bank": "bank_b",
                    "target_bank": "bank_c",
                    "source_account": f"bank_b_comm_{inst:02d}",
                    "target_account": f"bank_c_remit_{inst:02d}",
                    "amount": round(float(self.rng.uniform(11500.0, 17500.0)), 2),
                    "payment_rail": "WIRE",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_5",
                    "hop_index": 1,
                })

        elif scenario_id == "SCENARIO_6":
            # Scenario 6: Extreme Rarity at Bank Gamma (Alpha=40, Beta=25, Gamma=2)
            for inst in range(15):
                base_step = self.rng.randint(5, timesteps - 10)
                records.append({
                    "transaction_id": f"tx_sc6_{inst:02d}_a",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_b",
                    "source_account": f"bank_a_rare_{inst:02d}",
                    "target_account": f"bank_b_rare_{inst:02d}",
                    "amount": round(float(self.rng.uniform(15000.0, 22000.0)), 2),
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_6",
                    "hop_index": 0,
                })
            # Only 2 instances reach Bank Gamma
            for inst in range(2):
                base_step = self.rng.randint(5, timesteps - 10)
                records.append({
                    "transaction_id": f"tx_sc6_gamma_{inst:02d}",
                    "step": base_step,
                    "source_bank": "bank_b",
                    "target_bank": "bank_c",
                    "source_account": f"bank_b_rare_g_{inst:02d}",
                    "target_account": f"bank_c_rare_{inst:02d}",
                    "amount": round(float(self.rng.uniform(25000.0, 32000.0)), 2),
                    "payment_rail": "SWIFT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_6",
                    "hop_index": 1,
                })

        elif scenario_id == "SCENARIO_7":
            # Scenario 7: Zero Positive Historical Examples at Bank Gamma
            # In training data, Bank Gamma will have 0 positive samples.
            # We inject test transactions targeting Bank Gamma at later steps.
            for inst in range(20):
                # Put strictly in later timesteps (test set)
                base_step = self.rng.randint(int(timesteps * 0.82), timesteps - 2)
                records.append({
                    "transaction_id": f"tx_sc7_{inst:02d}",
                    "step": base_step,
                    "source_bank": "bank_a",
                    "target_bank": "bank_c",
                    "source_account": f"bank_a_zp_{inst:02d}",
                    "target_account": f"bank_c_zp_{inst:02d}",
                    "amount": round(float(self.rng.uniform(28000.0, 36000.0)), 2),
                    "payment_rail": "SEPA_INSTANT",
                    "is_laundering": 1,
                    "scenario_id": "SCENARIO_7",
                    "hop_index": 1,
                })

        return records

    def _enrich_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute tabular and topological edge features across sequential transactions."""
        df = df.copy()

        # Basic scalar features
        df["log_amount"] = np.log1p(df["amount"].astype(float))
        df["is_cross_bank"] = (df["source_bank"] != df["target_bank"]).astype(int)
        df["hour_of_day"] = df["step"] % 24
        df["is_weekend"] = ((df["step"] // 24) % 7 >= 5).astype(int)

        # Structuring and high-value flags
        df["structuring_indicator"] = (
            (df["amount"] >= 8500.0) & (df["amount"] <= 10000.0)
        ).astype(int)
        df["high_value_flag"] = (df["amount"] >= 50000.0).astype(int)

        # Categorical encodings
        profile_map = {
            InstitutionType.RETAIL: 0,
            InstitutionType.COMMERCIAL: 1,
            InstitutionType.CROSS_BORDER: 2,
            InstitutionType.DIGITAL_CHALLENGER: 3,
        }
        df["bank_profile_encoded"] = df["source_bank"].map(
            lambda b: profile_map.get(self.bank_map[b].institution_type, 0)
        )

        rail_map = {
            "INTERNAL": 0,
            "SEPA_INSTANT": 1,
            "TARGET2": 2,
            "SWIFT": 3,
            "WIRE": 4,
            "ACH": 5,
        }
        df["rail_encoded"] = df["payment_rail"].map(lambda r: rail_map.get(r, 1))

        # Temporal Graph Invariants (Sequential degrees and velocity)
        src_counts: dict[str, int] = {}
        tgt_counts: dict[str, int] = {}
        src_out_deg = []
        tgt_in_deg = []
        rapid_indicators = []
        velocity_bursts = []

        last_seen_step: dict[str, int] = {}

        for _, row in df.iterrows():
            src = str(row["source_account"])
            tgt = str(row["target_account"])
            s = int(row["step"])

            # Degrees
            src_out_deg.append(src_counts.get(src, 0))
            tgt_in_deg.append(tgt_counts.get(tgt, 0))

            src_counts[src] = src_counts.get(src, 0) + 1
            tgt_counts[tgt] = tgt_counts.get(tgt, 0) + 1

            # Rapid Hop Indicator: Has source transacted in the last 2 hours?
            prev_s = last_seen_step.get(src, -999)
            if 0 < (s - prev_s) <= 2:
                rapid_indicators.append(1)
            else:
                rapid_indicators.append(0)

            # Velocity burst
            velocity_bursts.append(min(5, src_counts[src]))
            last_seen_step[src] = s
            last_seen_step[tgt] = s

        df["source_out_degree"] = src_out_deg
        df["target_in_degree"] = tgt_in_deg
        df["rapid_hop_indicator"] = rapid_indicators
        df["velocity_burst"] = velocity_bursts

        return df

    @staticmethod
    def get_local_bank_view(df_transactions: pd.DataFrame, bank_id: str) -> pd.DataFrame:
        """Enforce strict Information Horizon: Bank observes ONLY its incident edges.

        Zero raw PII or cross-bank edge leakage. Transactions between other institutions
        (e.g., Bank B -> Bank C) are strictly absent from Bank A's visibility horizon.
        """
        mask = (df_transactions["source_bank"] == bank_id) | (
            df_transactions["target_bank"] == bank_id
        )
        local_df = df_transactions[mask].copy().reset_index(drop=True)
        return local_df

    @staticmethod
    def split_chronological_train_test(
        df: pd.DataFrame,
        split_ratio: float = 0.80,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Strict zero-lookahead temporal split: max(t_train) <= min(t_test)."""
        max_step = df["step"].max()
        cutoff_step = int(max_step * split_ratio)
        train_df = df[df["step"] <= cutoff_step].copy().reset_index(drop=True)
        test_df = df[df["step"] > cutoff_step].copy().reset_index(drop=True)
        return train_df, test_df
