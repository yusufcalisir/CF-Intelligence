# CFI-CrossBank-02: Phase 3.3 Governing Protocol Clarification Record & Final Promotion Gate

`	ext
================================================================================
CFI-CrossBank-02 - Phase 3.3
Protocol Clarification Record + Q3 Claim-Scope Resolution + Final Promotion Gate
================================================================================
`

## 1. Governing Protocol Clarification

For CFI-CrossBank-02, the following prospective interpretation record is adopted without rewriting historical evidence:

`	ext
The immutable executable source at
2f64a02b65caa0b35588ec8c016290d1f2ac6e61
is the authoritative specification of the model-training implementation
that was actually frozen before canonical execution.

The centralized Q3 implementation in that source trains on concatenated
bank-local views.

Earlier Phase 2E / Phase 2F prose reported centralized example counts
using deduplicated unique transactions and therefore understated the
executed centralized exposure count.

Those earlier prose values are preserved as historical audit errors.
They are not silently overwritten.

Because the manifest/config did not independently encode the
concatenated-view construction, example-exposure parity is classified
as an EXECUTION-DERIVED PROPERTY of the frozen implementation, not as a
separately preregistered protocol guarantee.
`

### Clarification Negative Boundaries
This clarification explicitly does **NOT** claim:
1. That Q3 exposure parity was always explicitly preregistered.
2. That the manifest explicitly bound expanded-view training.
3. That the historical 234,380 figure never existed.
4. That the historical audit record was correct.

---

## 2. Canonical Q3 Estimand After Clarification

Following this governing clarification, the canonical Q3 benchmark condition is formally defined as:

- **Frozen high-level estimand**:
  Matched nominal training passes over the same underlying distributed transaction universe.
- **Frozen executable implementation**:
  Centralized training on concatenated bank-local views (_prepare_centralized_data in 
unner.py).
- **Execution-derived properties**:
  - Exact unique transaction-universe parity (ed_only = 0, central_only = 0 across all five seeds).
  - Exact expanded local-view row parity (central_rows = sum(client_rows)).
  - Exact example-exposure parity (330,290 exposures for seed 42).
  - Near but not exact optimizer-step parity (centralized steps lower by 10-20 steps due to per-client batch rounding).
- **Optimizer semantics**:
  - Centralized Adam state persists across all 10 epochs.
  - Federated client Adam state resets at each communication round (5 rounds x 2 local epochs).
  - Optimization is **not** identical between federated and centralized regimes.

### Q3 Provenance Caveat
Any promoted numerical Q3 claim must carry the following mandatory caveat:

> *The centralized expanded-view construction is bound by the frozen executable source but was not independently encoded in the manifest; historical pre-execution prose used an inconsistent unique-transaction exposure count.*

### Approved Descriptive Q3 Claim
> Under the frozen executable matched-view comparison, federated training had higher AP than centralized training in all five seeds (mean paired difference +0.0325 +/- 0.0181), while centralized training had higher ROC-AUC in all five seeds (federated-minus-centralized mean paired difference -0.0079 +/- 0.0056).

- **No equivalence claim**: Federated learning does not match centralized training; metrics diverge in opposite directions.
- **No universal winner**: Neither condition dominates across all evaluation axes.
- **No significance claim**: Descriptive statistics only; no hypothesis test is asserted.
- **No mechanism claim**: Post-hoc batching regularization hypotheses are retired.

---

## 3. Preservation of Phase 3.2 Scientific Corrections

Phase 3.2 is authoritative for all scientific corrections across the benchmark:

1. **Oracle Extra Feature & Classification**:
   - The oracle input contains exactly one extra feature: scenario_id is not None (input dimension 11, not 18).
   - In all regenerated splits across all five canonical seeds, this feature is strictly label-equivalent to is_laundering (zero off-diagonal elements).
   - Classified as GENERATOR_GROUND_TRUTH / LABEL_EQUIVALENT.
   - Graph/topology attribution is NOT_SUPPORTED.
   - The oracle is retained strictly as DIAGNOSTIC_ORACLE_ABLATION and must not support claims regarding deployable or consortium intelligence.
2. **Q2 Per-Bank Decomposition**:
   - The pooled ROC-AUC improvement is not uniform across institutions:
     - Bank A: federated ROC is lower than isolated local in 5/5 seeds (-0.0293 +/- 0.0104).
     - Bank B: federated ROC is lower than isolated local in 4/5 seeds (-0.0171 +/- 0.0226).
     - Bank C: federated ROC is higher than isolated local in 5/5 seeds (+0.3514 +/- 0.2009).
   - The pooled gain is concentrated entirely in rescuing the zero-positive Bank C. Causal mechanisms remain unisolated.
3. **Cold-Start Tripartite Separation**:
   - Concept A: Explicit zero-positive local baseline has zero discrimination (ROC 0.5000, FPR 1.0, recall 1.0, all-positive operating point).
   - Concept B: Federated Bank C ranking improved relative to isolated local training (+0.3514 dROC, +0.1086 dAP).
   - Concept C: Operational low-FPR detection remained at 0% recall across all five seeds.
   - Approved summary: *In the project-synthetic zero-positive Bank C setting, federated parameter sharing improved ranking metrics relative to isolated local training across all five evaluated seeds, but this did not translate into positive Bank C recall at the validation-selected ultra-low-FPR operating regime.*
4. **Scenario 7 Scoring Pipeline Caveat**:
   - Scenario 7 is classified as POST_TRAINING_UNSEEN_TYPOLOGY (absent from training, present in validation and test).
   - Federated pooled test detection was 5 / 133 incidents (3.76%); mean per-seed recall was 3.92%.
   - Mandatory caveat: Scenario metrics use source-bank-only scoring while the operating threshold is chosen from concatenated-view validation scores.
5. **Synthetic Exposure Units**:
   - All monetary figures are reported strictly in SYNTHETIC_USD. Real-world fraud prevention, recovery, and avoided chargebacks are NOT_EVALUATED.
6. **Privacy & Real-World Scope**:
   - Privacy is NOT_EVALUATED (no cryptographic DP, SecAgg, or encryption evaluation).
   - Real-world generalizability is NOT_EVALUATED (project-synthetic benchmark).

---

## 4. Technical Debt Registry

1. **CONFIRMED_THRESHOLD_TIE_DEFECT**:
   select_threshold_on_validation reports a target-like validation FPR when score ties later classified with score >= threshold produce realized FPR 1.0. Preserved without code modification; fix scheduled for Protocol v2.2.
2. **SCHEMA_NAMING_DEFECT**:
   information_budget.unique_training_rows and 	raining_budget.unique_information_rows contain expanded local-view row counts rather than deduplicated unique transaction counts. Raw artifact is preserved immutable.
3. **MANIFEST_BINDING_INCOMPLETENESS**:
   Manifest binds config, matrix, generator, and local schema, but leaves runner, model, and metrics bound through git commit SHA only.

---

## 5. Promotion Package Verification

| Component | Path / Identification | Status |
|:---|:---|:---:|
| Canonical Raw Artifact | enchmarks/results/raw/crossbank_v2_canonical.json (SHA-256: 81e3b39dab... LF / 6f802cad9... CRLF, 313,965 / 322,468 bytes) | VERIFIED |
| Canonical Manifest | enchmarks/crossbank_v2/manifest.json (SHA-256: e8dcc5aad8...) | VERIFIED |
| Phase 3.2 Reconciliation Report | enchmarks/results/crossbank_v2/phase3_2_reconciliation_report.md | VERIFIED |
| Phase 3.3 Clarification Record | enchmarks/results/crossbank_v2/phase3_3_clarification_record.md | VERIFIED |
| Canonical Claim Registry | enchmarks/results/crossbank_v2/claim_registry.json | VERIFIED |
| Negative-Result Registry | enchmarks/results/crossbank_v2/negative_result_registry.json | VERIFIED |
| Technical-Debt Registry | enchmarks/results/crossbank_v2/technical_debt_registry.json | VERIFIED |

---

## 6. Final Promotion Decision

`	ext
FINAL_DECISION:
CROSSBANK_V2_EVIDENCE_PROMOTION_COMPLETE
`
