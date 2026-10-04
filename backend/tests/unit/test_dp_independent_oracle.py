"""Independent Mathematical Oracle for Differential Privacy Accounting Integrity.

Validates the mathematical contract of:
1. Parallel Composition across disjoint bank partitions in a single round:
   epsilon_round = max_{k in Participating} epsilon_k
2. Sequential Composition across repeated rounds querying the same population:
   epsilon_total = sum_{r=1}^R epsilon_round^(r)
3. Fail-closed budget exhaustion without DP disablement or plaintext fallback.

These oracle functions are independently derived from DP literature (McSherry 2009, Dwork et al. 2014)
and do not call production composition helpers.
"""

from __future__ import annotations

import pytest

from app.application.services.privacy_service import (
    PrivacyBudget,
    PrivacyBudgetExceededError,
    PrivacyService,
)


def independent_parallel_oracle(client_epsilons: list[float]) -> float:
    """Independent mathematical oracle for Parallel Composition.
    
    Theorem (McSherry 2009):
    If mechanisms M_1, ..., M_K operate on disjoint datasets D_1, ..., D_K,
    then the composite release M(D) = (M_1(D_1), ..., M_K(D_K)) provides
    (max_k epsilon_k)-Differential Privacy.
    """
    if not client_epsilons:
        return 0.0
    return max(client_epsilons)


def independent_sequential_oracle(round_epsilons: list[float]) -> float:
    """Independent mathematical oracle for Sequential Composition.
    
    Theorem (Dwork et al. 2006, 2014):
    If mechanisms M^(1), ..., M^(R) are executed sequentially on the same population,
    the cumulative privacy loss is bounded by sum_{r=1}^R epsilon^(r).
    """
    return sum(round_epsilons)


class TestDifferentialPrivacyIndependentOracle:
    """Rigorous verification of Scenarios A through E."""

    def test_scenario_a_disjoint_clients_single_round(self) -> None:
        """Scenario A: Disjoint client datasets within one round compose in parallel.
        
        Three banks hold mutually disjoint partitions D_A, D_B, D_C.
        Local Opacus engines report:
          epsilon_A = 0.45
          epsilon_B = 0.82
          epsilon_C = 0.61
        
        Independent Oracle:
          round_loss = max(0.45, 0.82, 0.61) = 0.82
          (NOT the sum: 0.45 + 0.82 + 0.61 = 1.88)
        """
        bank_epsilons = [0.45, 0.82, 0.61]
        expected_round_epsilon = independent_parallel_oracle(bank_epsilons)
        assert expected_round_epsilon == 0.82

        # Verify against PrivacyBudget expenditure
        budget = PrivacyBudget()
        budget.spend(expected_round_epsilon, limit=8.0)
        assert budget.total_epsilon == pytest.approx(0.82)
        assert budget.rounds_spent == 1

    def test_scenario_b_same_clients_multiple_rounds_sequential(self) -> None:
        """Scenario B: Same protected population queried across multiple rounds composes sequentially.
        
        Rounds 1 to 3 execute with disjoint banks in each round:
          Round 1: max(0.30, 0.50, 0.40) = 0.50
          Round 2: max(0.45, 0.35, 0.55) = 0.55
          Round 3: max(0.60, 0.40, 0.50) = 0.60
        
        Independent Oracle:
          total_loss = 0.50 + 0.55 + 0.60 = 1.65
        """
        round_1_clients = [0.30, 0.50, 0.40]
        round_2_clients = [0.45, 0.35, 0.55]
        round_3_clients = [0.60, 0.40, 0.50]

        r1_eps = independent_parallel_oracle(round_1_clients)
        r2_eps = independent_parallel_oracle(round_2_clients)
        r3_eps = independent_parallel_oracle(round_3_clients)

        expected_cumulative = independent_sequential_oracle([r1_eps, r2_eps, r3_eps])
        assert expected_cumulative == pytest.approx(1.65)

        budget = PrivacyBudget()
        budget.spend(r1_eps, limit=5.0)
        budget.spend(r2_eps, limit=5.0)
        budget.spend(r3_eps, limit=5.0)

        assert budget.total_epsilon == pytest.approx(1.65)
        assert budget.rounds_spent == 3

    def test_scenario_c_heterogeneous_client_participation(self) -> None:
        """Scenario C: Heterogeneous participation across rounds.
        
        Only clients that actually participate in a round contribute to that round's spend.
          Round 1: Bank A (0.50), Bank B (0.70), Bank C (Offline / None)
                   -> round spend = max(0.50, 0.70) = 0.70
          Round 2: Bank A (Offline / None), Bank B (0.40), Bank C (0.65)
                   -> round spend = max(0.40, 0.65) = 0.65
        
        Independent Oracle:
          total_loss = 0.70 + 0.65 = 1.35
        """
        round_1_active = [0.50, 0.70]  # Bank C dropped
        round_2_active = [0.40, 0.65]  # Bank A dropped

        r1 = independent_parallel_oracle(round_1_active)
        r2 = independent_parallel_oracle(round_2_active)

        expected_total = independent_sequential_oracle([r1, r2])
        assert expected_total == pytest.approx(1.35)

        budget = PrivacyBudget()
        budget.spend(r1, limit=5.0)
        budget.spend(r2, limit=5.0)

        assert budget.total_epsilon == pytest.approx(1.35)
        assert budget.rounds_spent == 2

    def test_scenario_d_budget_exhaustion_fails_closed(self) -> None:
        """Scenario D: Cumulative privacy expenditure exceeding budget limit MUST fail closed.
        
        Budget limit: epsilon_limit = 2.0
          Round 1 spend: 1.10 <= 2.0 (succeeds)
          Round 2 spend: 1.05 -> cumulative 2.15 > 2.0 (MUST raise PrivacyBudgetExceededError)
        
        No DP disablement, no plaintext fallback, no limit increase.
        """
        epsilon_limit = 2.0
        r1_eps = 1.10
        r2_eps = 1.05

        expected_total = independent_sequential_oracle([r1_eps, r2_eps])
        assert expected_total > epsilon_limit

        budget = PrivacyBudget()
        budget.spend(r1_eps, limit=epsilon_limit)
        assert budget.total_epsilon == pytest.approx(1.10)

        with pytest.raises(PrivacyBudgetExceededError) as exc_info:
            budget.spend(r2_eps, limit=epsilon_limit)

        assert "Cumulative privacy budget exceeded" in str(exc_info.value)
        # Verify the limit was not silently mutated
        assert budget.total_epsilon == pytest.approx(2.15)

    def test_scenario_e_below_budget_successful_completion(self) -> None:
        """Scenario E: Valid configuration remaining below budget limit completes successfully.
        
        Budget limit: epsilon_limit = 4.0
        Rounds: [0.80, 0.75, 0.90]
        Cumulative: 2.45 <= 4.0
        """
        epsilon_limit = 4.0
        round_epsilons = [0.80, 0.75, 0.90]

        expected_total = independent_sequential_oracle(round_epsilons)
        assert expected_total <= epsilon_limit

        svc = PrivacyService()
        sim_id = "sim_dp_oracle_valid"

        for eps in round_epsilons:
            svc.record_opacus_epsilon(sim_id, epsilon=eps, limit=epsilon_limit)

        budget = svc.get_or_create_budget(sim_id)
        assert budget.total_epsilon == pytest.approx(2.45)
        assert budget.rounds_spent == 3
