# Hypothesis Property-Based Testing Report — Smart Contracts Subsystem

**Subsystem:** Consortium Incentive Settlement (`ConsortiumIncentiveSettlement.sol`)  
**Date:** August 2026  
**Framework:** Hypothesis Property Testing  
**Total Generated Examples:** 200+ randomized parameter combinations  

## Verified Mathematical & Protocol Properties

1. **`test_property_shapley_basis_points_sum`**: Verified that sum of basis points $\le 10,000$ and total distributed payouts do not exceed total pool balance across 100 randomized inputs ($N \in [2, 20]$ banks, pool sizes up to $10^{24}$ wei).
2. **`test_property_payout_claim_idempotency`**: Verified that a single participant claim transitions state idempotently and blocks all subsequent claim attempts across 50 iterations.
3. **`test_property_quarantined_node_payout_zero`**: Verified that node quarantine guarantees 0 wei payout allocation and blocks payout claiming regardless of reason payload.

## Results Summary
- **Total Properties Tested:** 3
- **Status:** **3/3 PASS** (0 failures, 0 shrinking counterexamples)
