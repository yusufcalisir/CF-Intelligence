# CFI-CrossBank-02: Phase 3.2 Final Report Correction, Claim Normalization and Promotion Gate

```text
================================================================================
CFI-CrossBank-02 - Phase 3.2
Report Correction + Claim Normalization + Protocol-Semantics Confirmation
================================================================================
```

All numbers below were recomputed in this phase from the immutable artifact, from dataset regeneration (no training), or from the frozen source at `2f64a02b`. Scripts: `scratch/phase3_2_forensics.py`, `scratch/phase3_2_exact_stats.py` (outputs in `scratch/phase3_2_forensics_output.txt`). Nothing in the repository was modified.

---

## A. Executive Decision

```text
Q3_CENTRAL_BASELINE_FREEZE_BINDING:   NOT_VERIFIED
COLD_START_DEGENERACY_MECHANISM:      DIRECTLY_VERIFIED (mechanism differs from Phase 3.1)
LOW_FPR_POPULATION_RECONCILIATION:    PASS_WITH_POPULATION_MAP_DOCUMENTED
RAW_ARTIFACT_PROMOTION:               BLOCKED
REPORT_CORRECTION_STATUS:             INCOMPLETE
CLAIM_REGISTRY_STATUS:                NOT_CANONICAL
CANONICAL_RERUN:                      NOT_REQUIRED
EVIDENCE_COMMIT:                      BLOCKED
commit: NO     push: NO

FINAL_DECISION:
CROSSBANK_V2_FINAL_RECONCILIATION_FAILED
```

This is a **gate failure, not an execution-invalidity finding**. The executed code is byte-identical to the frozen commit, all 30 cells are intact, and the raw artifact hash is unchanged. What failed is the requirement that the centralized Q3 construction be *proven* frozen rather than justified afterwards (section D).

> [!WARNING]
> Several statements in my **Phase 3.1 report were unverified or wrong**. They are retracted in section AF (supersession map). The most material: the oracle "eight consortium features" do not exist; the real oracle input is one binary column that equals the label.

---

## B. Canonical Artifact Identity

| Check | Start of phase | End of phase |
|:---|:---|:---|
| SHA-256 | `b6f802cad979c8cca083dd030cfc0bd12beb06846ea1b4ee317b8747ba0efe6a` | identical |
| Bytes | 322468 | 322468 |
| Result | MATCH | MATCH |

`CANONICAL_RAW_ARTIFACT_MUTATION_DETECTED`: not triggered.

## C. Frozen Protocol Identity

- `HEAD` = `2f64a02b65caa0b35588ec8c016290d1f2ac6e61`; `git diff -- benchmarks/crossbank_v2 backend/tests/unit/test_crossbank_v2.py` empty; nothing staged.
- `git log -- benchmarks/crossbank_v2` returns **one** commit (the freeze commit). There is no earlier VCS history of `runner.py`, so any claim about what the code did before the freeze cannot be verified from git.
- The manifest binds: protocol config hash, matrix hash, **generator** source hash, feature-schema hash (LOCAL_ONLY regime only), seeds, conditions, dataset hashes. It does **not** hash `runner.py`, `model.py`, `metrics.py` or the CONSORTIUM_SIGNAL feature schema. Those are bound only through the git commit.
- All five regenerated dataset hashes match the manifest (`dataset_hash_match = True` for every seed).

---

## D. Q3 Central Baseline Freeze-Binding Audit

| # | Question | Finding |
|:--|:---|:---|
| 1 | Explicitly or unambiguously frozen to concatenated bank-local views? | **Source: yes.** `_prepare_centralized_data` (runner.py L292-341) concatenates, per bank, every row where the bank is source *or* target. **Config/manifest: no.** The only wording is `"Equally informed centralized pooled upper bound with matched optimization budget"` and Q3 text `"same underlying distributed information universe"`. The source comment (L300-301) says "global transaction view ... with respect to its source bank", which does not match the code. |
| 2 | Present before canonical execution? | Yes, in the freeze commit that Phase 3 verified before running. Whether it was identical *before* the freeze cannot be shown (single commit). |
| 3 | Does manifest/config/source bind it? | Manifest: **no**. Config: **no** (only `centralized_epochs = 10`). Source: **yes, via commit SHA only**. |
| 4 | Did Phase 3 execute exactly that frozen behavior? | Yes. Reconstruction from regenerated data matches the artifact exactly for all five seeds (examples and optimizer steps). |
| 5 | Any post-execution reinterpretation? | **Yes.** See below. |
| 6 | Does the Q3 estimand remain as described in the prompt? | The *frozen* estimand (matched nominal passes, same underlying transaction universe) holds. "Equal example exposures" and "same expanded local-view information" are execution-derived descriptions, not frozen statements. |

**The decisive contradiction.** Before execution, the freeze record stated a different central budget:

| Source | Centralized exposures | Centralized steps | Exposure parity |
|:---|:---:|:---:|:---|
| Phase 2E report §AJ/§AK | 234,380 | 3,670 | `NOT_EQUAL (330,290 vs 234,380)` |
| Phase 2F report §AF ("preserved") | 234,380 | 3,670 | not equal |
| Phase 3 task specification §38 | 234,380 | 3,670 | "investigate if values differ" |
| **Executed (artifact, seed 42)** | **330,290** | **5,170** | exposure parity **equal** |

234,380 = 23,438 unique transactions x 10. The committed source trains on 33,029 expanded view rows. Phase 3 accepted the discrepancy without the investigation §38 required, and Phase 3.1 then described it as a "transition" in accounting. That framing was a retrospective justification, which is what principle 12 prohibits. Phase 2E's own diagnostic scripts call `runner._prepare_centralized_data`, which suggests the prose figure was an arithmetic error in the audit report, but without VCS history this cannot be proven.

```text
Q3_CENTRAL_BASELINE_FREEZE_BINDING:
NOT_VERIFIED
```

Basis: the frozen record is internally contradictory (source vs. Phase 2E/2F statements), the manifest does not bind the construction, and the "equal exposure" characterisation was introduced after execution.

**Consequence per the task rules:** claim promotion and raw-artifact promotion stop. I did not rerun anything.

**Decision that is yours, not mine** (options, none executed):
1. Issue an explicit written protocol-amendment record stating that committed source governs, and that Q3 claims are limited to nominal-pass and transaction-universe matching, with exposure parity reported only as an observed (not pre-registered) property. Then re-run this gate.
2. If the pre-registered unique-row centralized design (234,380 / 3,670) is what you intended, that is a **new versioned experiment**, not a repair of this one.

## E. Q3 Final Estimand (provisional)

```text
Frozen estimand (config.py Q3):
  matched nominal passes (10 vs 10) over the same underlying distributed
  transaction universe.

Execution-derived properties (NOT frozen):
  unique-transaction universe parity: exact (fed_only = 0, central_only = 0, all 5 seeds)
  local-view row parity: exact (central rows = sum of client rows)
  example-exposure parity: exact (artifact == reconstruction, all 5 seeds)
  optimizer-step parity: NEAR_BUT_NOT_EQUAL (central lower by 10-20 steps)
```

Per-seed reconstruction (regenerated from datasets, matches the artifact):

| Seed | Unique tx | Client rows A / B / C | Sum views | Dup. factor | Dup. IDs | Fed steps | Central steps (views) | Central steps if unique rows | Examples (both) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 42 | 23,438 | 14,115 / 10,523 / 8,391 | 33,029 | 1.4092 | 9,591 | 5,180 | 5,170 | 3,670 | 330,290 |
| 123 | 23,451 | 14,177 / 10,403 / 8,291 | 32,871 | 1.4017 | 9,420 | 5,150 | 5,140 | 3,670 | 328,710 |
| 456 | 23,512 | 14,094 / 10,632 / 8,347 | 33,073 | 1.4066 | 9,561 | 5,190 | 5,170 | 3,680 | 330,730 |
| 789 | 23,367 | 14,011 / 10,447 / 8,389 | 32,847 | 1.4057 | 9,480 | 5,150 | 5,140 | 3,660 | 328,470 |
| 2025 | 23,481 | 14,043 / 10,507 / 8,455 | 33,005 | 1.4056 | 9,524 | 5,180 | 5,160 | 3,670 | 330,050 |

Maximum multiplicity is 2 (a cross-bank transaction appears in the source and target bank views). A duplicated transaction is **not** the same row twice: it carries different feature values per bank perspective (`is_outward` / `is_inward`, local degree).

Schema defect: the artifact fields `information_budget.unique_training_rows` and `training_budget.unique_information_rows` hold the **expanded view row count** (e.g. 33,029), not unique transactions (23,438). The field name is misleading.

## F. Optimizer Semantics

Verified from `train_pytorch_model` and the runner:

- Federated: a new `Adam` is created on every call, so **client optimizers are reinitialized each communication round** (5 rounds x 2 local epochs). FedAvg averages the full `state_dict` weighted by client row counts.
- Centralized and isolated: one call with 10 epochs, so Adam state persists across the 10 epochs.
- Initial state: shared `initial_state` for the isolated, federated-local and centralized MLPs. The oracle federated condition does **not** load it (input dimension differs). Cold-start and logistic have no MLP.
- Seeding: `torch.manual_seed(seed)` and `np.random.seed(seed)` are called once per seed, so conditions consume a single sequential RNG stream. `torch_deterministic_algorithms = False` is recorded in the artifact environment.

Approved characterisation: *matched information universe, expanded local-view rows, nominal passes and example exposures, with small optimizer-step differences and different optimizer-state trajectories.* Not "identical optimization".

---

## G. Inferential-Language Corrections

Retired permanently: `p < 0.001`, `statistically significant`, `significantly outperforms`, `highly significant`.

Occurrences in the Phase 3 report (found by line scan): L400 (`p < 0.001`), L604 (`highly significant`), L734 (`significantly outperforms`). No test is stored in the artifact and none was preregistered in `config.py`/`manifest.json`.

**Retraction:** my Phase 3.1 §K cited "t = 20.81, p = 2.4e-5". I did not compute those values from any stored procedure. They are withdrawn and no replacement test is introduced.

## H. Derived-Utility Claim Corrections

">99% of centralized ranking utility" is retired. It was a ratio of two ROC-AUC means (0.9013 / 0.9091), not a preregistered quantity. Use direct values only: federated ROC 0.9013 +/- 0.0066, centralized ROC 0.9091 +/- 0.0112, paired difference -0.0079 +/- 0.0056.

## I. Q3 Mechanism Correction

"Decentralized per-bank client batching regularizes against majority bank domination" is retired: `SPECULATIVE_NOT_EVALUATED`. No ablation separates client batching from other differences (optimizer reset per round, per-client scalers, FedAvg weighting).

---

## J. Scenario 7 Necessity-Language Audit

Retired: any statement that local features "cannot" detect Scenario 7 or that graph topology is necessary. Replacement:

> Under the evaluated local-feature regimes in this controlled synthetic benchmark, Scenario 7 detection remained low: the federated condition detected 5 of 133 pooled test incidents.

**Additional caveat found in source (not previously reported).** Scenario metrics use a different scoring pipeline from `overall_metrics`:

- `overall_metrics` score each bank's full view (source or target rows) with that bank's preprocessor.
- `scenario_metrics` call `_predict_test_scores`, which selects `test_df` rows by `source_bank == b_id` only, re-extracts features on that subset, and applies the threshold chosen on the **concatenated-view validation scores**.

So the Scenario 7 operating threshold and the scenario scores come from different feature constructions. This is frozen behaviour, not an execution defect, but it must accompany every Scenario 7 number.

## K. Oracle Attribution Audit

**Retraction of Phase 3.1 §AD/§AE/§AX.** The eight features I listed (`consortium_global_src_degree`, `consortium_structuring_depth`, `consortium_is_burst`, ...) **do not exist** in the code. The task prompt repeated that list from my report. The real implementation (`features.py` L136-142):

```python
is_scen = row.get("scenario_id") is not None
features["consortium_hop_signal"].append(float(1.0 if is_scen else 0.0))
```

- Oracle input dimension is **11** (10 local + 1), not 18.
- The single feature is "scenario_id is not None". Contingency check on the regenerated data, every seed, every split:

| Split | scenario_id present AND label 1 | present AND label 0 | absent AND label 1 | absent AND label 0 |
|:---|:---:|:---:|:---:|:---:|
| Seed 42 train | 245 | **0** | **0** | 23,193 |
| Seed 42 val | 86 | **0** | **0** | 5,753 |
| Seed 42 test | 89 | **0** | **0** | 5,634 |

The other four seeds show the same zero off-diagonal pattern in all splits. **The oracle feature is identical to `is_laundering`.**

## L. Oracle Component Classification

| Feature | Class | Uses global topology | Uses generator truth | Uses future info | Uses private cross-bank info |
|:---|:---|:---:|:---:|:---:|:---:|
| `consortium_hop_signal` | `GENERATOR_GROUND_TRUTH` (label-equivalent) | no | **yes** | no | no |

```text
ORACLE_COMPONENT_CAUSAL_ATTRIBUTION:
NOT_APPLICABLE (single feature; it equals the label)
GRAPH_TOPOLOGY_ATTRIBUTION:
NOT_SUPPORTED (no graph, global-degree or connectivity feature exists in this regime)
```

Consequences:
- Oracle results measure how well an MLP exploits a **label copy**, not graph intelligence. Any wording about "graph", "topology" or "consortium intelligence" is unsupported.
- Because the feature equals the label, the oracle metrics being below 1.0 (AP 0.7313-0.9976, ROC 0.8232-0.9999 across seeds) reflects optimization, scaling and FedAvg dynamics, not an information limit. It is therefore not even a clean informational upper bound.
- The manifest `feature_schema_hash` covers only `LOCAL_ONLY`; the consortium regime is not hash-bound.
- Realistic conditions: feature extraction for `LOCAL_ONLY` never reads `scenario_id` (the assertion and feature list use only transaction columns), so no oracle leakage into realistic conditions was found.

---

## M. Cold-Start Degeneracy Mechanism Audit

```text
COLD_START_DEGENERACY_MECHANISM:
DIRECTLY_VERIFIED
```

Reproduced for all five seeds by re-running only the threshold function on the baseline's constant output:

| Seed | Baseline constant (float32) | Recomputed threshold | Artifact threshold | Selection-time val FPR | **Actual val FPR under `>=`** | Unique val scores |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 42 | 0.00011915 | 0.00011915 | 0.000119 | 0.000979 | **1.0** | 1 |
| 123 | 0.00012058 | 0.00012058 | 0.000121 | 0.000958 | **1.0** | 1 |
| 456 | 0.00011977 | 0.00011977 | 0.00012 | 0.000977 | **1.0** | 1 |
| 789 | 0.00011918 | 0.00011918 | 0.000119 | 0.000961 | **1.0** | 1 |
| 2025 | 0.00011825 | 0.00011825 | 0.000118 | 0.000993 | **1.0** | 1 |

**Corrected mechanism** (Phase 3.1's "threshold forced below the constant" was wrong):
1. `ColdStartLocalBaseline` returns one constant (Laplace prior `1/(n+2)`) for every row.
2. `select_threshold_on_validation` sorts scores and takes the cumulative false-positive curve **ignoring ties**. With one unique score it picks the constant itself as the threshold, and its reported "achieved validation FPR" (about 0.001) is a tie-blind figure.
3. `compute_comprehensive_metrics` classifies with `score >= threshold`. Every row equals the threshold, so **every row is positive**: TP = all positives, FP = all negatives, TN = FN = 0.

So `achieved_validation_fpr` in the artifact for this condition is misleading: the true validation FPR at that threshold is 1.0. The same tie-blindness could in principle affect other conditions with saturated float32 scores, but scores are not persisted, so that cannot be reconstructed.

Observational statement retained: *at the selected operating threshold, the cold-start baseline classified all evaluated test examples as positive (ROC-AUC 0.5000, FPR 1.0, recall 1.0).*

```text
COLD_START_LOCAL_BASELINE_DISCRIMINATION:  NONE
COLD_START_100_PERCENT_RECALL:             DEGENERATE_ALL_POSITIVE_OPERATING_POINT
```

The scenario-level 100% rows for this condition (all seven scenarios, 133/133 for Scenario 7) follow from the same all-positive rule (`all_scenarios_100pct = True` in every seed): `MATHEMATICALLY_CORRECT_BUT_OPERATIONALLY_NON_DISCRIMINATIVE`.

Note: `COND_COLD_START_ZERO_POSITIVE` only evaluates the local prior on Bank C. It is **not** a federated transfer evaluation. Federated Bank C behaviour lives in the federated condition's `per_bank_metrics["bank_c"]`.

## N. Cold-Start Ranking Evidence (concept B)

Bank C has zero training positives in all five seeds (`bank_c_train_pos = 0`). Values re-read from the artifact:

| Seed | Isolated AP | Federated AP | dAP | Isolated ROC | Federated ROC | dROC |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 42 | 0.0385 | 0.2148 | +0.1763 | 0.6866 | 0.8912 | +0.2046 |
| 123 | 0.0553 | 0.0852 | +0.0299 | 0.7660 | 0.8689 | +0.1029 |
| 456 | 0.0201 | 0.1067 | +0.0866 | 0.5043 | 0.8570 | +0.3527 |
| 789 | 0.0126 | 0.1030 | +0.0904 | 0.3296 | 0.8939 | +0.5643 |
| 2025 | 0.0154 | 0.1753 | +0.1599 | 0.3337 | 0.8664 | +0.5327 |
| **Mean +/- SD** | 0.0284 | 0.1370 | **+0.1086 +/- 0.0596** | 0.5240 | 0.8755 | **+0.3514 +/- 0.2009** |

- Mean Bank C dROC is **+0.3514** (0.351440 unrounded). Phase 3.1's "+0.3515" came from subtracting rounded means; use +0.3514.
- Sign: positive in 5/5 seeds for both metrics (`CONSISTENT_ALL_SEEDS`), but the magnitude is very heterogeneous (dROC 0.1029 to 0.5643).
- Caveat on the comparator: the isolated Bank C model was trained on negatives only and scores **below chance** in two seeds (ROC 0.3296, 0.3337). Part of the delta is the comparator being worse than random, not a transferred signal. Against a chance-level ROC of 0.5, federated Bank C ROC is +0.3755 on average.

Approved wording: *In the synthetic Bank C zero-positive-training setting, federated parameter sharing improved Bank C ranking metrics relative to isolated local training across the evaluated seeds.*

## O. Cold-Start Low-FPR Limitation (concept C, mandatory with every cold-start summary)

> At the validation-selected 0.1% FPR operating regime, Bank C test recall remained 0% in all five seeds for the realistic local-feature federated condition.

Per seed: TP = 0; FN = 50, 44, 44, 36, 43; FP = 2, 0, 1, 2, 0. Concepts A (local baseline: none), B (ranking transfer: improved) and C (operational detection: none) stay separate.

Not claimable: successful fraud detection, "solved cold start", production-ready cold start.

---

## P. Scenario 7 Claim Resolution

Two separate statements, each with exactly one classification:

1. *Under the realistic federated local-feature condition, 5 of 133 pooled Scenario 7 test incidents were detected across the five canonical seeds.* -> `SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT` (with the scoring-pipeline caveat in section J).
2. *Reliable Scenario 7 detection using the evaluated local-feature regime.* -> `NOT_SUPPORTED`.

## Q. Scenario 7 Aggregation Correction

Per-seed integers (test incidents = test transactions = positive rows; each incident is one transaction):

| Condition | Detected / test incidents per seed (42, 123, 456, 789, 2025) | Pooled | Mean per-seed recall | Detected SYNTHETIC_USD (sum / mean per seed) |
|:---|:---|:---:|:---:|:---:|
| Isolated local | 0/26, 1/27, 0/27, 1/24, 0/29 | 2/133 = 1.50% | 1.57% | 37,489.84 / 7,497.97 |
| Federated local | 1/26, 2/27, 0/27, 2/24, 0/29 | **5/133 = 3.76%** | **3.92%** | 104,904.05 / 20,980.81 |
| Centralized local | 0/26 ... 0/29 | 0/133 = 0.00% | 0.00% | 0.00 / 0.00 |
| Oracle (label-equivalent feature) | 26/26, 27/27, 27/27, 24/24, 29/29 | 133/133 = 100% | 100% | 3,356,392.71 / 671,278.54 |

Total Scenario 7 exposure in the test splits: 3,356,392.71 SYNTHETIC_USD across five seeds (671,278.54 mean per seed).

The two federated figures are different estimands: **mean per-seed recall 3.92%** and **pooled 5/133 = 3.76%**. They must never be written as "3.92% (5/133)". The earlier "1.0 / 26.6" is an average of per-seed integers, not a fractional incident.

Chronology (regenerated datasets):

| Seed | Train instances | Val instances (incidents) | Test incidents |
|:---:|:---:|:---:|:---:|
| 42 | 0 | 4 | 26 |
| 123 | 0 | 3 | 27 |
| 456 | 0 | 3 | 27 |
| 789 | 0 | 6 | 24 |
| 2025 | 0 | 1 | 29 |

`POST_TRAINING_UNSEEN_TYPOLOGY` (absent from training, present in validation and test). Retraction: Phase 3.1 §AH said validation had "24-27" instances; the true values are 1-6.

## R. Historical Scenario 7 Root-Cause Classification

```text
HISTORICAL_100_PERCENT_ROOT_CAUSE:
STRONGLY_SUPPORTED_BUT_NOT_CAUSALLY_ISOLATED
```

What the Phase 1 forensic record directly supports:
- The "2/2 incidents" figure came from a table column (`Hops: 2`) mistaken for an incident count and hard-coded as `target_test_incidents=2`; the data held 20 transactions (Phase 1 report, "Scenario 7 Deconstructed").
- Features such as `rapid_hop_indicator` were computed on the pooled multi-bank stream before partitioning (cross-bank leakage; fires on 49.05% of fraud vs 0.35% of legitimate).

What it does **not** support: Phase 3.1's statement of a "source-degree leakage" in the historical generator, and any claim that this leakage alone produced the historical detection rates. No ablation isolates it. That statement is retracted.

## S. Logistic Diagnostic Wording Correction

Canonical logistic (`COND_SIMPLE_BASELINE_LOGISTIC`, `class_weight="balanced"`, `max_iter=500`, runner pipeline, five seeds): AP 0.0607 +/- 0.0079, ROC 0.8461 +/- 0.0137, recall/precision/F1 at the validation-selected threshold all 0 in every seed.

The Phase 2D diagnostic (seed 42: ROC 0.84699, AP 0.08043) used a different script (no `class_weight`, `max_iter=1000`, scratch generator copy). Canonical seed 42 is ROC 0.8494, AP 0.0707. They are comparable in magnitude but are **not the same experiment**. Retraction: Phase 3.1's "Phase 2C diagnostic ROC ~0.9999" was not verified in this phase and is removed.

Approved wording: *The canonical logistic results are consistent with the repaired separability diagnostics, and the frozen feature audits found no reappearance of the previously identified degree shortcut.* The second clause rests on the Phase 2E feature audit record; it was not re-executed here, and ROC similarity alone is not evidence of absence.

## T. Synthetic Exposure Claim Classification

Claim I ("synthetic monetary exposure represents prevented fraud") -> **`NOT_EVALUATED`**.

Reason: `NOT_SUPPORTED` / `CONTRADICTED_BY_CANONICAL_RESULT` would mean the experiment measured prevention and found it absent. The experiment never measures prevention, avoided loss, chargebacks or ROI. It sums the `amount` of detected positive synthetic transactions (`SYNTHETIC_USD`). Absence of a measurement is not a negative result. Phase 3's `CONTRADICTED` label is superseded.

---

## U. Low-FPR Population Map

Verified from source and regenerated data:

| Metric group | Population | Seed 42 N / negatives | Source |
|:---|:---|:---:|:---|
| `overall_metrics` (AP, ROC, confusion matrix) | concatenated bank-local test **views** | 8,019 / 7,851 | `compute_comprehensive_metrics` on concatenated bank views; recon 8,019 = sum of test views |
| `per_bank_metrics[bank_x]` | that bank's test partition | Bank C: 2,035 rows (50 positive) | per-bank views |
| `split_summary`, `low_fpr_resolution` | **deduplicated unique** test transactions | 5,723 / 5,634 | `compute_low_fpr_resolution(test_df.is_laundering)` |
| `scenario_metrics` | deduplicated unique test transactions, source-bank scoring | 5,723 rows | `compute_scenario_specific_metrics(test_df, ...)` |

All seeds: view negatives 7,851 / 7,919 / 7,771 / 7,883 / 7,913; unique negatives 5,634 / 5,656 / 5,537 / 5,648 / 5,670.

```text
LOW_FPR_POPULATION_RECONCILIATION:
PASS_WITH_POPULATION_MAP_DOCUMENTED
```

The resolution section (1/5,634 = 0.000177) therefore does **not** describe the population behind the reported confusion matrices (1/7,851 = 0.000127). The two populations are not interchangeable.

Definition note: the artifact field `recall_at_validation_fpr` (reported as "Recall@0.1% FPR") is recall at a **validation-selected threshold**. The achieved test FPR is not 0.1% (e.g. 0.0011-0.0016 for federated; 1.0 for cold-start).

---

## V. Q1 Final Claim

> The simulated federated training path completed multi-client parameter aggregation without centrally pooling raw client training datasets.

Mandatory caveat: this benchmark does not establish cryptographic privacy, secure aggregation, differential privacy, legal compliance, or production security. The synthetic table is generated centrally and then partitioned, so the statement concerns the training path only.

## W. Q2 Final Claims

> Across five canonical seeds in the project-synthetic CrossBank benchmark, the federated local-feature condition had higher ROC-AUC than isolated local training in all five seeds, with a paired mean difference of +0.1897 +/- 0.0204 (pooled concatenated bank-local test views).
>
> The AP difference was smaller and seed-dependent: +0.0123 +/- 0.0138, positive in four of five seeds.
>
> The Recall at the validation-selected 0.1%-FPR threshold difference was mixed: +0.0032 +/- 0.0128, two positive, two negative, one zero paired difference.

Per-seed values: dROC +0.1851, +0.1561, +0.2029, +0.2052, +0.1993; dAP +0.0166, +0.0010, -0.0048, +0.0285, +0.0201; dRecall +0.0238, +0.0062, -0.0071, 0.0000, -0.0069.

**New mandatory finding: the pooled gain is not uniform across banks.** Per-bank paired federated-minus-isolated differences (this phase):

| Bank | dROC per seed | dROC mean +/- SD | Sign | dAP mean +/- SD | AP sign |
|:---|:---|:---:|:---:|:---:|:---:|
| Bank A | -0.0401, -0.0136, -0.0251, -0.0364, -0.0311 | **-0.0293 +/- 0.0104** | 5 negative | -0.0016 +/- 0.0568 | 2+ / 3- |
| Bank B | +0.0001, -0.0531, -0.0067, -0.0006, -0.0252 | **-0.0171 +/- 0.0226** | 4 negative / 1 positive | -0.0788 +/- 0.0820 | 1+ / 4- |
| Bank C | +0.2046, +0.1029, +0.3527, +0.5643, +0.5327 | **+0.3514 +/- 0.2009** | 5 positive | +0.1086 +/- 0.0596 | 5 positive |

The isolated global figure also pools scores from three separately trained, differently calibrated models, whereas the federated figure uses one model; the pooled ROC difference is therefore not a clean measure of "information gain". The improvement is concentrated in the zero-positive Bank C, while Banks A and B were worse under federation in ROC (and mostly in AP). "Federation improves every institution" is **not supported**; the mechanism behind the pooled gain is `SPECULATIVE_NOT_EVALUATED`.

## X. Q3 Final Claims (PROVISIONAL; promotion BLOCKED by section D)

If the binding gate is later cleared, the approved form is:

> Under the frozen matched-view comparison, federated training produced higher AP than centralized training in all five seeds (mean paired difference +0.0325 +/- 0.0181), while centralized training produced higher ROC-AUC in all five seeds (federated-minus-centralized mean paired difference -0.0079 +/- 0.0056). The experiment therefore does not identify a universal winner across metrics and does not establish statistical equivalence.

Per-seed dAP: +0.0487, +0.0470, +0.0294, +0.0037, +0.0335. dROC: -0.0060, -0.0094, -0.0079, -0.0158, -0.0002. dRecall (validation-selected threshold): +0.0178, +0.0187, 0.0000, +0.0080, 0.0000 (mean +0.0089 +/- 0.0091; 3 positive, 2 zero).

Federated and centralized values with SD: AP 0.1779 +/- 0.0260 vs 0.1454 +/- 0.0186; ROC 0.9013 +/- 0.0066 vs 0.9091 +/- 0.0112; recall 0.0130 +/- 0.0096 vs 0.0041 +/- 0.0038. No equivalence test was preregistered; "matches centralized" is retired.

## Y. Cold-Start Final Claims

- (A) The explicit zero-positive local baseline had no discrimination (ROC-AUC 0.5000, all-positive operating point, FPR 1.0, section M).
- (B) Federated Bank C ranking improved: mean dROC +0.3514 +/- 0.2009, mean dAP +0.1086 +/- 0.0596 over isolated local training, positive in 5/5 seeds (section N caveats apply).
- (C) Bank C recall at the validation-selected 0.1%-FPR regime was 0% in all five seeds (section O).

## Z. Scenario 7 Final Claims

> Scenario 7 was absent from training and present in validation and test, so it is classified as a POST_TRAINING_UNSEEN_TYPOLOGY. Under the realistic federated local-feature condition, pooled test detection was 5 / 133 incidents (3.76%). Mean per-seed incident recall was 3.92%. The oracle condition detected 133 / 133 incidents, but its input includes a feature identical to the label and it is an upper-bound ablation.

Caveat: scenario scoring uses the source-bank-only pipeline described in section J.

## AA. Oracle Final Claims

> The generator-truth oracle condition, whose added input is a single binary feature equal to the label, achieved mean AP 0.8140 +/- 0.1133 and mean recall 0.7840 +/- 0.1112 at the validation-selected threshold, versus 0.1779 and 0.0130 for the realistic federated local-feature condition, in this project-synthetic benchmark. This is an `ORACLE_UPPER_BOUND_ABLATION`.

Per-seed oracle AP: 0.7313, 0.9976, 0.7604, 0.8489, 0.7320. ROC: 0.8232, 0.9999, 0.9505, 0.9762, 0.8638. Recall: 0.7024, 0.9750, 0.7376, 0.7857, 0.7192. The oracle-minus-centralized paired dROC is mixed (-0.091, +0.090, +0.045, +0.053, -0.029). Forbidden: attributing the gain to graph topology, or implying deployability.

## AB. Privacy Boundary

Federated aggregation of model parameters is not a privacy mechanism. No differential privacy, secure aggregation, encryption, membership-inference or inversion evaluation was run. `CB-PRIVACY: NOT_EVALUATED`.

## AC. Real-World Generalizability

```text
EVIDENCE_TAXONOMY:            PROJECT_SYNTHETIC
FEDERATION_NATURE:            SIMULATED_PROCESS
REAL_WORLD_GENERALIZABILITY:  NOT_EVALUATED
```

---

## AD. Canonical Claim Registry (DRAFT - status NOT_CANONICAL)

Registry cannot be canonical while `CB-Q3-*` entries depend on an unverified binding.

| Claim ID | Exact claim text | Classification | Metric population | Evidence | Mandatory caveat | Forbidden stronger interpretation | Promotion |
|:---|:---|:---|:---|:---|:---|:---|:---|
| CB-Q1-ARCH | Simulated federated training aggregated parameters across three clients without centrally pooling raw client training datasets | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | n/a (training path) | `_execute_federated_fedavg` exchanges state_dicts only | No privacy mechanism; data generated centrally then partitioned | secure / private / compliant | eligible |
| CB-Q2-ROC | Pooled ROC-AUC was higher for federated than isolated in 5/5 seeds, mean paired +0.1897 +/- 0.0204 | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | concatenated bank-local test views | per-seed +0.1851 ... +0.1993 | Pooled isolated score mixes three models; per-bank A/B lower, C higher | federation improves every bank; significance; mechanism | eligible |
| CB-Q2-AP | AP difference +0.0123 +/- 0.0138, positive in 4 of 5 seeds | SUPPORTED_WITH_CAVEAT | concatenated views | seed 456 = -0.0048 | Seed-dependent; per-bank AP mostly lower for A/B | consistent AP gain | eligible |
| CB-Q2-LOWFPR | Recall at validation-selected threshold difference +0.0032 +/- 0.0128 (2 pos / 2 neg / 1 zero) | SUPPORTED_WITH_CAVEAT | concatenated views | per-seed list in section W | Achieved test FPR is not exactly 0.1% | any low-FPR benefit | eligible |
| CB-Q2-PERBANK | Federated per-bank ROC was lower than isolated for Bank A (5/5) and mostly Bank B (4/5) and higher for Bank C (5/5) | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | per-bank test partitions | section W table | Bank C isolated model had zero positives | uniform benefit | eligible |
| CB-Q3-AP | Federated AP above centralized in 5/5 seeds, mean +0.0325 +/- 0.0181 | SUPPORTED_WITH_CAVEAT | concatenated views | section X | Depends on centralized-baseline construction | winner claim; mechanism | **BLOCKED** |
| CB-Q3-ROC | Centralized ROC above federated in 5/5 seeds, mean -0.0079 +/- 0.0056 | SUPPORTED_WITH_CAVEAT | concatenated views | section X | Same | winner claim | **BLOCKED** |
| CB-Q3-EQUIVALENCE | Federated matches centralized | NOT_EVALUATED | n/a | no equivalence test preregistered | Metrics diverge | equivalence / "matches" | **BLOCKED** |
| CB-COLD-RANK | Bank C zero-positive ranking improved under federation (mean dROC +0.3514, dAP +0.1086, 5/5 seeds) | SUPPORTED_WITH_CAVEAT | Bank C test partition | section N | Isolated comparator below chance in 2 seeds; recall 0% (CB-COLD-LOWFPR) | solved cold start; detection | eligible |
| CB-COLD-LOWFPR | Bank C recall at validation-selected 0.1%-FPR regime was 0% in all five seeds | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | Bank C test partition | TP = 0 all seeds | none | operational cold-start detection | eligible |
| CB-S7-REALISTIC | Federated local features detected 5/133 pooled Scenario 7 test incidents (3.76%; mean per-seed 3.92%) | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | unique test transactions, source-bank scoring | section Q | Scenario scoring pipeline differs from overall metrics | reliable detection; impossibility without graph | eligible |
| CB-S7-RELIABLE | Reliable Scenario 7 detection with evaluated local features | NOT_SUPPORTED | same | 5/133 | n/a | n/a | eligible |
| CB-S7-ORACLE | Oracle detected 133/133 Scenario 7 incidents | ORACLE_ONLY | unique test transactions | section Q | Feature equals label | deployable detection | eligible |
| CB-ORACLE | Label-equivalent oracle feature raised AP to 0.8140 +/- 0.1133 | ORACLE_ONLY | concatenated views | section AA | Optimization-limited; not graph evidence | graph topology; consortium intelligence | eligible |
| CB-PRIVACY | The system preserves privacy | NOT_EVALUATED | n/a | none | none | any privacy guarantee | eligible (as NOT_EVALUATED) |
| CB-REALWORLD | Results generalize to real banks | NOT_EVALUATED | n/a | project-synthetic | none | any real-world effectiveness | eligible (as NOT_EVALUATED) |
| CB-SYNTHETIC-USD | Detected synthetic incidents represented X SYNTHETIC_USD of generator-defined exposure | SUPPORTED_BY_CANONICAL_SYNTHETIC_EXPERIMENT | unique test transactions | section Q | Not prevention | saved, prevented, chargebacks, ROI | eligible |
| CB-PREVENTED-FRAUD | Synthetic exposure represents prevented fraud | NOT_EVALUATED | n/a | not measured | none | any prevention claim | eligible (as NOT_EVALUATED) |
| CB-PVALUE | Q2 ROC improvement is significant (p < 0.001) | UNSUPPORTED_POST_HOC_INFERENCE | n/a | no stored or preregistered test | none | any significance | retired |
| CB-Q3-MECH | Per-bank batching regularizes against majority domination | SPECULATIVE_NOT_EVALUATED | n/a | no ablation | none | any mechanism | retired |

## AE. Negative Result Registry

- Scenario 7 realistic federated pooled detection: **5/133 = 3.76%**; mean per-seed 3.92%. Centralized local: 0/133.
- Bank C federated recall at validation-selected 0.1%-FPR: **0%, all five seeds**.
- Q2 AP: one negative paired seed (456, -0.0048). Q2 low-FPR recall: 2 positive / 2 negative / 1 zero.
- **Banks A and B: federated ROC lower than isolated** (A 5/5 seeds, B 4/5).
- Centralized ROC-AUC higher than federated in 5/5 seeds.
- Cold-start zero-positive baseline: ROC 0.5, FPR 1.0, all-positive operating point.
- Canonical logistic recall at the validation-selected threshold: 0 in all five seeds.
- Federated recall seed 456: 0.0000 (TP = 0 on the pooled test views).
- Oracle is not a clean ceiling: AP min 0.7313, ROC min 0.8232 despite a label-equivalent input.

## AF. Supersession Map

| Previous statement | Defect | Corrected statement | Status |
|:---|:---|:---|:---|
| `p < 0.001` (Phase 3 L400) | No stored or preregistered test | Descriptive only: +0.1897 +/- 0.0204, 5/5 positive | RETIRED |
| "t = 20.81, p = 2.4e-5" (Phase 3.1 §K) | Never computed from a stored procedure | Withdrawn | RETRACTED |
| "significantly outperforms", "highly significant" (L604, L734) | Unsupported inference | Delete | RETIRED |
| ">99% centralized ranking utility" | Derived ROC ratio | Direct values and paired delta | RETIRED |
| Batching regularization explanation | No ablation | Not stated | SPECULATIVE_NOT_EVALUATED |
| "Graph topology is necessary" / "contain the necessary signal" | Over-generalization; no topology feature exists | Section J wording | RETIRED |
| Eight consortium features, 18-feature oracle (Phase 3.1 §AD/§AX; task §9) | **Features do not exist**; oracle is 1 label-equivalent column, 11 inputs | Sections K-L | RETRACTED |
| Cold-start "100% detection" | All-positive operating point | Sections M and Y | RECLASSIFIED |
| Cold-start "forces threshold below the constant" (Phase 3.1 §Z) | Threshold equals the constant; ties plus `>=` | Section M | CORRECTED |
| "dramatically improves cold-start discrimination" | Adjective | dROC +0.3514, dAP +0.1086, with caveats | REPLACED |
| "3.92% (5/133)" | Mean per-seed vs pooled conflated | 3.92% mean per-seed; 3.76% pooled (5/133) | CORRECTED |
| "completely preserving data locality" | Privacy overreach | Q1 wording | RETIRED |
| "Synthetic exposure ... CONTRADICTED" (claim I) | Absence of measurement is not a negative result | NOT_EVALUATED | RECLASSIFIED |
| Generic "matches centralized" / "on par with centralized" | Metrics disagree; no equivalence test | Metric-specific statements | RETIRED |
| Claim F "NOT_SUPPORTED or SUPPORTED_WITH_..." | Two classifications | Two separate claims, one class each | CORRECTED |
| Phase 3.1 §G median / min / max columns | **Not computed** | Recomputed values (below) | CORRECTED |
| Phase 3.1 §R per-client rows and steps | **Wrong** | Section E table | CORRECTED |
| Phase 3.1 §Y cold-start thresholds for seeds 123-2025 | **Wrong** | Section M table | CORRECTED |
| Phase 3.1 §AH Scenario 7 validation "24-27" | **Wrong** | 4, 3, 3, 6, 1 | CORRECTED |
| Phase 3.1 §AL "source-degree leakage" as historical root cause | Not supported by Phase 1 record | Section R | DOWNGRADED |
| Phase 3.1 §AW "Phase 2C logistic ROC ~0.9999" | Unverified | Removed | RETRACTED |
| Phase 3.1 §X "deliberate transition" of central accounting | Retrospective justification | Section D | RETRACTED |
| Phase 3 "EXACT exposure parity" as frozen property | Contradicts Phase 2E/2F record | Execution-derived observation only | OPEN (gate D) |
| Phase 3 "federation improves ranking" (unqualified) | Banks A/B lower | Section W | CORRECTED |

Recomputed aggregate medians (min, max) for the two headline conditions, replacing Phase 3.1 §G: isolated AP 0.1584 (0.1328, 0.2031), ROC 0.7023 (0.6934, 0.7444), recall 0.0125 (0.0000, 0.0159); federated AP 0.1704 (0.1536, 0.2197), ROC 0.9005 (0.8927, 0.9082), recall 0.0159 (0.0000, 0.0238); centralized AP 0.1375 (0.1242, 0.1710), ROC 0.9099 (0.8929, 0.9233), recall 0.0060 (0.0000, 0.0079). Means and sample SDs (ddof = 1) in Phase 3.1 §G were correct. Remaining precision/F1 summaries: federated precision 0.2603 +/- 0.1493, F1 0.0247 +/- 0.0178; isolated precision 0.2649 +/- 0.2250, F1 0.0185 +/- 0.0118; centralized precision 0.1583 +/- 0.2050, F1 0.0080 +/- 0.0074.

FedAvg weights per seed (client rows / sum), A / B / C: 42: 0.4274 / 0.3186 / 0.2540; 123: 0.4313 / 0.3165 / 0.2522; 456: 0.4261 / 0.3215 / 0.2524; 789: 0.4266 / 0.3181 / 0.2554; 2025: 0.4255 / 0.3183 / 0.2562.

---

## AG. Final Raw Artifact Hash Recheck

```text
SHA-256: b6f802cad979c8cca083dd030cfc0bd12beb06846ea1b4ee317b8747ba0efe6a
Bytes:   322468
Result:  EXACT_MATCH (no mutation)
```

## AH. Report Correction Status

```text
REPORT_CORRECTION_STATUS:
INCOMPLETE
```
All non-Q3 corrections are written above. The Q3 sections (E, X) are provisional and cannot be finalized until gate D is resolved.

## AI. Claim Registry Status

```text
CLAIM_REGISTRY_STATUS:
NOT_CANONICAL
```

## AJ. Raw Artifact Promotion Gate

```text
RAW_ARTIFACT_PROMOTION:
BLOCKED
```
Required by the task rule for `NOT_VERIFIED` Q3 binding. Additional findings that any promoted record must carry: oracle label-equivalence, per-bank decomposition, cold-start tie mechanism, mislabeled `unique_*_rows` schema fields, un-hashed `runner.py`/consortium schema.

## AK. Canonical Rerun Gate

```text
CANONICAL_RERUN:
NOT_REQUIRED
```
The executed code equals the frozen commit and every reconstruction matches. A different centralized design would be a new protocol version, not a rerun.

## AL. Evidence Commit Gate

```text
EVIDENCE_COMMIT:
BLOCKED
```

## AM. Commit Check

```text
commit:
NO
```

## AN. Push Check

```text
push:
NO
```

## AO. Final Decision

```text
CROSSBANK_V2_FINAL_RECONCILIATION_FAILED
```
