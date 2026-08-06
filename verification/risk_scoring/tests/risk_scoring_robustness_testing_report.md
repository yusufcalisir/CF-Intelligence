# Adversarial Robustness & Stress Testing Report — Risk Scoring Engine Subsystem

**Subsystem:** Risk Scoring Engine (`RiskScoringEngine`) & AST Policy Engine (`PolicyEngineService`)  
**Repository:** Privacy-preserving Cross-Bank Fraud Detection using Federated Learning  
**Date:** August 2026  
**Status:** ALL 10 FAILURE INJECTION SCENARIOS PASSED (10/10 Passed)  

---

## 1. Executive Summary

A dedicated failure injection test suite (`verification/risk_scoring/tests/test_risk_scoring_robustness.py`) executed 10 stress scenarios attempting to break scoring rules, inject corrupted inputs, supply malformed transaction structures, or trigger unhandled application crashes.

All 10 scenarios passed, proving fault tolerance, safe default fallback mechanisms, and robust AST policy condition handling.

---

## 2. Robustness Test Matrix

| ID | Test Scenario | Stress Vector / Attack Type | Expected Behavior | Observed Result | Status |
|:---:|:---|:---|:---|:---|:---:|
| **R1** | Empty Payload | `transaction = {}` | Evaluates default baseline signals without `KeyError` | Handled safely | 🟢 **PASS** ✓ |
| **R2** | Malformed Non-Dict Payload | `transaction = "not_a_dict"` | Raises `TypeError` / `AttributeError` cleanly | Handled cleanly | 🟢 **PASS** ✓ |
| **R3** | NaN Value Injection | `velocity = NaN`, `amount = NaN` | Safe execution without process crash | Handled safely | 🟢 **PASS** ✓ |
| **R4** | Infinite Value Injection | `velocity = +Inf`, `amount = +Inf` | `min(1.0, +Inf)` caps score to $1000.0$ | Score $= 1000.0$ | 🟢 **PASS** ✓ |
| **R5** | Invalid Merchant Category | `category = "weapons_smuggling"` | Defaults to $0.10$ category risk cleanly | Handled cleanly | 🟢 **PASS** ✓ |
| **R6** | Country Code Casing | Lowercase `"kp"` (North Korea) | Case-insensitive lookup maps to $1.00$ sanctions risk | Score $= 1.00$ | 🟢 **PASS** ✓ |
| **R7** | Unsupported Device Type | `device = "neural_implant"` | Defaults to channel risk $0.20$ cleanly | Handled cleanly | 🟢 **PASS** ✓ |
| **R8** | Invalid Customer History | Out-of-bounds history scores | Clamped to $[0.0, 1.0]$; adds $+0.30$ new account fee | Handled safely | 🟢 **PASS** ✓ |
| **R9** | Extreme Numerical Values | `velocity = 1e308`, `amount = 1e308` | Evaluated without numeric overflow or NaN | Handled safely | 🟢 **PASS** ✓ |
| **R10** | Malformed AST Policy Rules | Malformed `and` list or type mismatch | Catches exception and returns `False` safely | Returned `False` | 🟢 **PASS** ✓ |

---

## 3. Detailed Robustness Findings

1. **Case-Insensitive Sanctions Verification:** Lowercase country codes (e.g. `"kp"`) map to North Korea sanctions risk ($1.00$), preventing sanctions evasion through casing tricks.
2. **Infinite Value Capping:** Submitting $+\infty$ for transaction velocity or amount caps normalized signals to $1.0$, producing a maximum risk score of $1000.0$ without causing float overflow.
3. **AST Policy Engine Fault Isolation:** Malformed AST boolean trees or string-to-float comparison type mismatches (e.g. comparing `"country_code"` $> 500$) catch internal exceptions gracefully and evaluate to `False`, preventing policy engine crashes.
