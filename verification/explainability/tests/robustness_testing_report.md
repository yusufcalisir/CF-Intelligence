# Robustness Testing Report — Explainability (XAI) Subsystem

**Module:** `explainability_service.py`, `realtime_explainer.py`  
**Test Suite:** `scratch/test_explainability_robustness.py`  
**Test Execution Date:** 2026-08-01  
**Framework:** pytest 8.x  
**Python Version:** 3.12  
**Total Scenarios Tested:** 14  
**Handled / Passed:** 12  
**Confirmed Production Defects:** 2  

---

## 1. Executive Summary

Fourteen boundary-injection robustness tests were executed against the `ExplainabilityService` and `FastInferenceExplainer` classes. The suite attempts systematic failure of every explanation algorithm via hostile input types: NaN values, IEEE 754 infinities, empty dictionaries/tensors, missing features, malformed data types, extreme floating-point magnitudes ($10^{308}$), path traversal transaction IDs, and non-existent graph nodes.

**Two genuine production defects were identified:**

| ID | Test Scenario | Component | Failure Mode | Severity |
|----|---------------|-----------|--------------|----------|
| **BUG-EX-01** | `test_gex3_negative_infinity_risk_score` | `explain_alert` (`_format_explanation`) | `OverflowError: cannot convert float infinity to integer` when converting `-inf` normalized score to ASCII bar length | **HIGH** |
| **BUG-EX-02** | `test_gex7_malformed_feature_data_types` | `compute_shap_values` | `ValueError: could not convert string to float` when parsing non-numeric strings in `transaction_amount` | **MEDIUM** |

---

## 2. Test Categories and Detailed Results

### 2.1 NaN Inputs (2 tests, 2 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX1 | NaN alert risk score | **PASS** | Risk breakdown signals remain finite; NaN does not crash engine |
| GEX2 | NaN feature values in `compute_shap_values` | **PASS** | `float("nan")` parsed to 0.0 or default; returns 10 finite contributions |

---

### 2.2 Infinite Values (2 tests, 1 passed, 1 failed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX3a | +Inf transaction amount | **PASS** | Clamps `min(1.0, inf / 10000.0) = 1.0` correctly |
| GEX3b | -Inf alert risk score | ❌ **FAIL (BUG-EX-01)** | `OverflowError` during ASCII progress bar formatting |

**BUG-EX-01 — OverflowError on Negative Infinity Risk Score**

```python
# Location: explainability_service.py L168
bar_len = int(signal.normalized_score * 20)
```

- **Trigger:** Alert initialized with `risk_score = float("-inf")`.
- **Root Cause:** When `risk_score = -inf`, `base_norm = -inf`. `scale_factor = -inf / (-inf) = nan` or `-inf`. Line 168 attempts to cast `-inf` to an integer via `int(-inf * 20)`, raising Python's `OverflowError`.
- **Fix Recommendation:** Add a finite guard or clamp before integer casting:
  ```python
  norm_val = max(0.0, min(1.0, signal.normalized_score))
  bar_len = int(norm_val * 20)
  ```

---

### 2.3 Empty Dicts, Tensors & Reason Codes (2 tests, 2 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX4 | Empty transaction dict `{}` | **PASS** | Gracefully substitutes defaults; returns 10 feature contributions |
| GEX5 | Empty alert `reason_codes = []` | **PASS** | Generates default 9-signal risk breakdown |

---

### 2.4 Missing Features (1 test, 1 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX6 | 9 out of 10 features missing | **PASS** | Missing features adopt baseline defaults; returns full 10-feature array |

---

### 2.5 Malformed Data Types (1 test, 1 failed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX7 | Non-numeric string in `transaction_amount` | ❌ **FAIL (BUG-EX-02)** | `ValueError: could not convert string to float: 'invalid_string_1000'` |

**BUG-EX-02 — ValueError on Non-Numeric String Feature**

```python
# Location: explainability_service.py L247
elif name == "transaction_amount":
    val = min(1.0, float(val) / 10000.0)  # Unhandled float conversion
```

- **Trigger:** `txn_dict = {"transaction_amount": "invalid_string_1000"}`.
- **Root Cause:** Line 247 calls `float(val)` directly without a `try...except (ValueError, TypeError)` block (unlike lines 257–260). Malformed string inputs cause an unhandled `ValueError` crash.
- **Fix Recommendation:** Wrap `float(val)` in a try-except block:
  ```python
  try:
      numeric_val = float(val)
  except (ValueError, TypeError):
      numeric_val = 0.0
  val = min(1.0, numeric_val / 10000.0)
  ```

---

### 2.6 Extreme Floating-Point Values (1 test, 1 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX8 | Extreme float values ($10^{308}$, $-10^{308}$, $10^{-308}$) | **PASS** | Clamps gracefully; NumPy emits float32 cast warning without crash |

---

### 2.7 Real-Time Explainer & Webhook Edge Cases (3 tests, 3 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX9 | Empty string merchant category | **PASS** | Handled gracefully; returns valid attribution vector |
| GEX10 | Empty feature vector `[]` in real-time SHAP | **PASS** | Handled gracefully; returns completed default job |
| GEX11 | Path traversal transaction ID (`../../etc/passwd`) | **PASS** | Key serialized safely in Redis and in-memory cache |

---

### 2.8 GNN & Counterfactual Edge Cases (3 tests, 3 passed)

| Test | Scenario | Result | Observation |
|------|----------|--------|-------------|
| GEX12 | Non-existent node ID in GNN explainer | **PASS** | Gracefully triggers synthetic fallback attribution |
| GEX13 | Target score > original score in counterfactuals | **PASS** | `is_cleared = True`, remediated score $\le$ target score |
| GEX14 | Target score = 0.0 | **PASS** | Remediated score clamped to minimum 50.0 safely |

---

## 3. Robustness Coverage Matrix

| Component | Hostile Input Category | Observed Behavior | Status |
|:---|:---|:---|:---:|
| `explain_alert` | Negative Infinity (`-inf`) | `OverflowError` during ASCII bar formatting | ❌ **BUG-EX-01** |
| `explain_alert` | NaN / Empty Reason Codes | Default 9-signal breakdown generated | ✅ **PASS** |
| `compute_shap_values` | Non-numeric String Amount | Unhandled `ValueError` crash | ❌ **BUG-EX-02** |
| `compute_shap_values` | Empty Dict `{}` / Missing Keys | 10 default feature contributions returned | ✅ **PASS** |
| `compute_shap_values` | Extreme Floats ($10^{308}$) | Float32 cast warning; no crash | ✅ **PASS** |
| `explain_realtime_score` | Empty Strings / Extreme Amounts | Valid directional vectors returned | ✅ **PASS** |
| `explain_async` | Path Traversal Transaction ID | Safely serialized in cache | ✅ **PASS** |
| `explain_gnn_embedding` | Non-existent Node ID | Synthetic fallback activated | ✅ **PASS** |
| `generate_counterfactuals` | Target Score = 0.0 | Clamped to minimum 50.0 | ✅ **PASS** |

---

## 4. Recommendations

1. **Resolve BUG-EX-01:** Add bounds clamping `max(0.0, min(1.0, ...))` before integer casting in `_format_explanation` (Line 168).
2. **Resolve BUG-EX-02:** Wrap `float(val)` conversions for `transaction_amount`, `account_age_days`, `velocity`, `hour_of_day`, and `chargeback_count` in `try...except (ValueError, TypeError)` blocks with default fallbacks.

---

*End of Robustness Testing Report — Explainability (XAI) Subsystem*
