"""Deterministic Multi-Institution Synthetic Generator for CrossBank v2 (CFI-CrossBank-02).

Redesigned generator eliminating trivial single-feature separability. Injects realistic
benign background noise (high amounts, cross-bank transfers, bursty velocities) and
produces clean raw transactions with explicit incident-level grouping.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from benchmarks.crossbank_v2.config import (
    DEFAULT_INSTITUTIONS,
    DEFAULT_SCENARIOS,
    InstitutionConfig,
    ScenarioConfig,
)


class CrossBankV2NetworkGenerator:
    """Deterministic synthetic transaction and network topology generator."""

    def __init__(
        self,
        institutions: list[InstitutionConfig] | None = None,
        scenarios: list[ScenarioConfig] | None = None,
        seed: int = 42,
    ) -> None:
        self.institutions = institutions or DEFAULT_INSTITUTIONS
        self.scenarios = scenarios or DEFAULT_SCENARIOS
        self.seed = seed
        self.rng = np.random.RandomState(seed)
        self.bank_map = {inst.bank_id: inst for inst in self.institutions}

        # Shared active account pools per institution (20% of account space)
        # Ensures fraud accounts have natural legitimate background activity
        self.active_accounts: dict[str, list[str]] = {}
        for b_id, inst in self.bank_map.items():
            active_count = max(10, inst.account_count // 5)
            self.active_accounts[b_id] = [f"{b_id}_acc_{i:05d}" for i in range(1, active_count + 1)]

    def _sample_distinct_accounts(self, bank_id: str, count: int) -> list[str]:
        """Sample distinct accounts from active pool to avoid self-transfers."""
        pool = self.active_accounts[bank_id]
        if len(pool) >= count:
            return [str(x) for x in self.rng.choice(pool, size=count, replace=False)]
        return [str(self.rng.choice(pool)) for _ in range(count)]

    def generate_raw_dataset(
        self,
        n_total_transactions: int = 35000,
        timesteps: int = 168,
        target_prevalence: float = 0.012,
    ) -> pd.DataFrame:
        """Generate raw transaction dataset with overlapping feature distributions.

        Does NOT pre-enrich features or calculate global graph degrees.
        Returns pure transactional records with ground-truth labels and incident IDs.
        """
        # Calculate positive and negative quotas
        target_fraud_count = max(20, int(n_total_transactions * target_prevalence))
        n_benign = n_total_transactions - target_fraud_count

        records: list[dict[str, Any]] = []

        # 1. Generate Benign Background Traffic
        records.extend(self._generate_realistic_benign_traffic(n_benign, timesteps))

        # 2. Inject Scenario Fraud Traffic
        records.extend(self._generate_scenario_fraud_traffic(target_fraud_count, timesteps))

        df = pd.DataFrame(records)

        # Stable deterministic sorting
        df = df.sort_values(by=["step", "transaction_id"]).reset_index(drop=True)
        return df

    def _generate_realistic_benign_traffic(
        self,
        n_samples: int,
        timesteps: int,
    ) -> list[dict[str, Any]]:
        """Generate benign traffic with realistic dispersion across amounts, rails, and inter-bank edges."""
        records: list[dict[str, Any]] = []
        bank_ids = [inst.bank_id for inst in self.institutions]
        bank_probs = [inst.volume_share for inst in self.institutions]

        for i in range(n_samples):
            step = self.rng.randint(0, timesteps)
            source_bank = str(self.rng.choice(bank_ids, p=bank_probs))

            # 60% intra-bank, 40% inter-bank (substantial cross-bank benign noise)
            if self.rng.rand() < 0.60:
                target_bank = source_bank
            else:
                other_banks = [b for b in bank_ids if b != source_bank]
                target_bank = str(self.rng.choice(other_banks))

            src_acc = str(self.rng.choice(self.active_accounts[source_bank]))
            tgt_acc = str(self.rng.choice(self.active_accounts[target_bank]))

            # Overlapping amount distribution:
            # Mixture of typical transactions and benign high-value / structuring-like amounts
            rand_val = self.rng.rand()
            if rand_val < 0.70:
                # Standard retail/commercial lognormal
                amount = float(self.rng.lognormal(mean=6.5, sigma=1.2))  # median ~$665, 95th ~4.8k
            elif rand_val < 0.90:
                # Benign high-value B2B/payroll
                amount = float(self.rng.uniform(10000.0, 75000.0))
            elif rand_val < 0.97:
                # Benign sub-threshold structuring-like noise ($8,500 - $9,999)
                amount = float(self.rng.uniform(8500.0, 9999.0))
            else:
                # Benign large institutional wholesale wire
                amount = float(self.rng.uniform(75000.0, 200000.0))

            amount = round(float(np.clip(amount, 5.0, 250000.0)), 2)

            # Payment rails
            if source_bank == target_bank:
                rail = str(self.rng.choice(["INTERNAL", "SEPA_INSTANT", "ACH"], p=[0.6, 0.3, 0.1]))
            else:
                rail = str(self.rng.choice(["SEPA_INSTANT", "TARGET2", "SWIFT", "WIRE"], p=[0.4, 0.3, 0.2, 0.1]))

            records.append({
                "transaction_id": f"tx_bg_{i:07d}",
                "step": step,
                "source_bank": source_bank,
                "target_bank": target_bank,
                "source_account": src_acc,
                "target_account": tgt_acc,
                "amount": amount,
                "payment_rail": rail,
                "is_laundering": 0,
                "scenario_id": None,
                "incident_id": None,
                "hop_index": None,
            })

        return records

    def _generate_scenario_fraud_traffic(
        self,
        target_fraud_count: int,
        timesteps: int,
    ) -> list[dict[str, Any]]:
        """Generate structured fraud typologies with natural amount and rail overlap."""
        records: list[dict[str, Any]] = []
        n_scenarios = len(self.scenarios)
        # Allocate roughly equal quota across scenarios
        instances_per_scenario = max(2, target_fraud_count // (n_scenarios * 2))

        incident_counter = 0

        for sc in self.scenarios:
            sc_id = sc.scenario_id

            for inst in range(instances_per_scenario):
                incident_counter += 1
                incident_id = f"inc_{sc_id.lower()}_{inst:04d}"

                if sc.is_cold_start_target:
                    # Scenario 7: Cold-Start target at Bank Gamma
                    # Placed strictly in test set (after 80% cutoff) to test zero-positive transfer
                    base_step = self.rng.randint(int(timesteps * 0.82), timesteps - 2)
                    amt = round(float(self.rng.uniform(12000.0, 38000.0)), 2)
                    src_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                    dest_c = str(self.rng.choice(self.active_accounts["bank_c"]))
                    records.append({
                        "transaction_id": f"tx_{sc_id.lower()}_{inst:04d}_0",
                        "step": base_step,
                        "source_bank": "bank_a",
                        "target_bank": "bank_c",
                        "source_account": src_a,
                        "target_account": dest_c,
                        "amount": amt,
                        "payment_rail": str(self.rng.choice(["SEPA_INSTANT", "SWIFT"], p=[0.7, 0.3])),
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 0,
                    })

                elif sc_id == "SCENARIO_1":
                    # Localized structuring at Bank Alpha (2 hops)
                    base_step = self.rng.randint(2, timesteps - 5)
                    amt1 = round(float(self.rng.uniform(7500.0, 14000.0)), 2)
                    amt2 = round(amt1 * float(self.rng.uniform(0.94, 0.98)), 2)
                    m1, m2, m3 = self._sample_distinct_accounts("bank_a", 3)

                    records.append({
                        "transaction_id": f"tx_sc1_{inst:04d}_0",
                        "step": base_step,
                        "source_bank": "bank_a",
                        "target_bank": "bank_a",
                        "source_account": m1,
                        "target_account": m2,
                        "amount": amt1,
                        "payment_rail": "INTERNAL",
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 0,
                    })
                    records.append({
                        "transaction_id": f"tx_sc1_{inst:04d}_1",
                        "step": base_step + 1,
                        "source_bank": "bank_a",
                        "target_bank": "bank_a",
                        "source_account": m2,
                        "target_account": m3,
                        "amount": amt2,
                        "payment_rail": "INTERNAL",
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 1,
                    })

                elif sc_id == "SCENARIO_2":
                    # 2-Bank Layering (Alpha -> Beta -> Cashout)
                    base_step = self.rng.randint(2, timesteps - 5)
                    amt1 = round(float(self.rng.uniform(15000.0, 45000.0)), 2)
                    amt2 = round(amt1 * 0.97, 2)
                    src_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                    mule_b, dest_b = self._sample_distinct_accounts("bank_b", 2)

                    records.append({
                        "transaction_id": f"tx_sc2_{inst:04d}_0",
                        "step": base_step,
                        "source_bank": "bank_a",
                        "target_bank": "bank_b",
                        "source_account": src_a,
                        "target_account": mule_b,
                        "amount": amt1,
                        "payment_rail": "SEPA_INSTANT",
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 0,
                    })
                    records.append({
                        "transaction_id": f"tx_sc2_{inst:04d}_1",
                        "step": base_step + 1,
                        "source_bank": "bank_b",
                        "target_bank": "bank_b",
                        "source_account": mule_b,
                        "target_account": dest_b,
                        "amount": amt2,
                        "payment_rail": "INTERNAL",
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 1,
                    })

                elif sc_id == "SCENARIO_3":
                    # Cyclic Ring
                    # In train (step <= 112): Alpha -> Beta -> Alpha -> Beta (0 Bank Gamma train positives)
                    # In val/test (step > 112): Alpha -> Beta -> Gamma -> Alpha (3-Bank Cyclic Ring)
                    base_step = self.rng.randint(2, timesteps - 6)
                    amt = round(float(self.rng.uniform(22000.0, 65000.0)), 2)
                    if base_step <= 112:
                        acc_a1, acc_a2 = self._sample_distinct_accounts("bank_a", 2)
                        acc_b = str(self.rng.choice(self.active_accounts["bank_b"]))
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_0",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": acc_a1,
                            "target_account": acc_b,
                            "amount": amt,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_1",
                            "step": base_step + 1,
                            "source_bank": "bank_b",
                            "target_bank": "bank_a",
                            "source_account": acc_b,
                            "target_account": acc_a2,
                            "amount": round(amt * 0.98, 2),
                            "payment_rail": "TARGET2",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_2",
                            "step": base_step + 2,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": acc_a2,
                            "target_account": acc_b,
                            "amount": round(amt * 0.95, 2),
                            "payment_rail": "SWIFT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 2,
                        })
                    else:
                        acc_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                        acc_b = str(self.rng.choice(self.active_accounts["bank_b"]))
                        acc_c = str(self.rng.choice(self.active_accounts["bank_c"]))
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_0",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": acc_a,
                            "target_account": acc_b,
                            "amount": amt,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_1",
                            "step": base_step + 1,
                            "source_bank": "bank_b",
                            "target_bank": "bank_c",
                            "source_account": acc_b,
                            "target_account": acc_c,
                            "amount": round(amt * 0.98, 2),
                            "payment_rail": "TARGET2",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })
                        records.append({
                            "transaction_id": f"tx_sc3_{inst:04d}_2",
                            "step": base_step + 2,
                            "source_bank": "bank_c",
                            "target_bank": "bank_a",
                            "source_account": acc_c,
                            "target_account": acc_a,
                            "amount": round(amt * 0.95, 2),
                            "payment_rail": "SWIFT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 2,
                        })

                elif sc_id == "SCENARIO_4":
                    # Smurfing Consolidation
                    # In train (step <= 112): Alpha -> Beta -> Beta (0 Bank Gamma train positives)
                    # In val/test (step > 112): Alpha -> Beta -> Gamma (Cross-bank consolidation into Gamma)
                    base_step = self.rng.randint(2, timesteps - 6)
                    amt1 = round(float(self.rng.uniform(8800.0, 9950.0)), 2)
                    amt2 = round(float(self.rng.uniform(8800.0, 9950.0)), 2)
                    if base_step <= 112:
                        smurf1, smurf2 = self._sample_distinct_accounts("bank_a", 2)
                        pool_b, dest_b = self._sample_distinct_accounts("bank_b", 2)
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_0a",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": smurf1,
                            "target_account": pool_b,
                            "amount": amt1,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_0b",
                            "step": base_step + 1,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": smurf2,
                            "target_account": pool_b,
                            "amount": amt2,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_1",
                            "step": base_step + 2,
                            "source_bank": "bank_b",
                            "target_bank": "bank_b",
                            "source_account": pool_b,
                            "target_account": dest_b,
                            "amount": round((amt1 + amt2) * 0.97, 2),
                            "payment_rail": "INTERNAL",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })
                    else:
                        smurf1, smurf2 = self._sample_distinct_accounts("bank_a", 2)
                        pool_b = str(self.rng.choice(self.active_accounts["bank_b"]))
                        dest_c = str(self.rng.choice(self.active_accounts["bank_c"]))
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_0a",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": smurf1,
                            "target_account": pool_b,
                            "amount": amt1,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_0b",
                            "step": base_step + 1,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": smurf2,
                            "target_account": pool_b,
                            "amount": amt2,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc4_{inst:04d}_1",
                            "step": base_step + 2,
                            "source_bank": "bank_b",
                            "target_bank": "bank_c",
                            "source_account": pool_b,
                            "target_account": dest_c,
                            "amount": round((amt1 + amt2) * 0.97, 2),
                            "payment_rail": "WIRE",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })

                elif sc_id == "SCENARIO_5":
                    # Cross-Archetype Arbitrage
                    # In train (step <= 112): Alpha -> Beta -> Beta (0 Bank Gamma train positives)
                    # In val/test (step > 112): Alpha -> Beta -> Gamma
                    base_step = self.rng.randint(2, timesteps - 5)
                    amt = round(float(self.rng.uniform(14000.0, 32000.0)), 2)
                    if base_step <= 112:
                        src_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                        dest_b1, dest_b2 = self._sample_distinct_accounts("bank_b", 2)
                        records.append({
                            "transaction_id": f"tx_sc5_{inst:04d}_0",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": src_a,
                            "target_account": dest_b1,
                            "amount": amt,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc5_{inst:04d}_1",
                            "step": base_step + 1,
                            "source_bank": "bank_b",
                            "target_bank": "bank_b",
                            "source_account": dest_b1,
                            "target_account": dest_b2,
                            "amount": round(amt * 0.98, 2),
                            "payment_rail": "INTERNAL",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })
                    else:
                        src_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                        mid_b = str(self.rng.choice(self.active_accounts["bank_b"]))
                        dest_c = str(self.rng.choice(self.active_accounts["bank_c"]))
                        records.append({
                            "transaction_id": f"tx_sc5_{inst:04d}_0",
                            "step": base_step,
                            "source_bank": "bank_a",
                            "target_bank": "bank_b",
                            "source_account": src_a,
                            "target_account": mid_b,
                            "amount": amt,
                            "payment_rail": "SEPA_INSTANT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 0,
                        })
                        records.append({
                            "transaction_id": f"tx_sc5_{inst:04d}_1",
                            "step": base_step + 1,
                            "source_bank": "bank_b",
                            "target_bank": "bank_c",
                            "source_account": mid_b,
                            "target_account": dest_c,
                            "amount": round(amt * 0.98, 2),
                            "payment_rail": "SWIFT",
                            "is_laundering": 1,
                            "scenario_id": sc_id,
                            "incident_id": incident_id,
                            "hop_index": 1,
                        })

                elif sc_id == "SCENARIO_6":
                    # Extreme Sample Starvation
                    base_step = self.rng.randint(2, timesteps - 5)
                    src_a = str(self.rng.choice(self.active_accounts["bank_a"]))
                    tgt_b = str(self.rng.choice(self.active_accounts["bank_b"]))
                    records.append({
                        "transaction_id": f"tx_sc6_{inst:04d}_0",
                        "step": base_step,
                        "source_bank": "bank_a",
                        "target_bank": "bank_b",
                        "source_account": src_a,
                        "target_account": tgt_b,
                        "amount": round(float(self.rng.uniform(11000.0, 28000.0)), 2),
                        "payment_rail": "SEPA_INSTANT",
                        "is_laundering": 1,
                        "scenario_id": sc_id,
                        "incident_id": incident_id,
                        "hop_index": 0,
                    })

        return records

    @staticmethod
    def compute_dataset_content_hash(df: pd.DataFrame) -> str:
        """Compute deterministic SHA-256 hash of dataset content."""
        columns = [
            "transaction_id",
            "step",
            "source_bank",
            "target_bank",
            "source_account",
            "target_account",
            "amount",
            "payment_rail",
            "is_laundering",
            "scenario_id",
            "incident_id",
        ]
        subset = df[columns].copy()
        # Canonical float string formatting
        subset["amount"] = [f"{float(x):.2f}" for x in subset["amount"]]
        csv_bytes = subset.to_csv(index=False, lineterminator="\n").encode("utf-8")
        return hashlib.sha256(csv_bytes).hexdigest()

    @classmethod
    def compute_generator_source_hash(cls) -> str:
        """Compute SHA-256 of the generator Python implementation."""
        src_path = Path(__file__).resolve()
        content = src_path.read_text(encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
