# 🕵️ Human-in-the-Loop Case Management & Workbench Specification

The Case Management Workbench provides fraud investigators and compliance officers with a 6-stage lifecycle for reviewing, escalating, and resolving suspicious transaction alerts.

---

## 📌 Case Lifecycle & Dual-Control Progression

```
[NEW] ➔ [ASSIGNED] ➔ [UNDER_INVESTIGATION] ➔ [PENDING_SECOND_SIGNATURE] ➔ [RESOLVED_CONFIRMED_FRAUD / RESOLVED_FALSE_POSITIVE]
              │                  │
              └───➔ [ESCALATED] ─┘
```

1. **`NEW`**: Case automatically opened from single or grouped fraud alerts.
2. **`ASSIGNED`**: Assigned to a specific fraud investigator analyst (`assigned_to`).
3. **`UNDER_INVESTIGATION`**: Active review underway (KYC check, graph entity expansion, SHAP feature attribution inspection).
4. **`ESCALATED`**: Escalated to senior compliance officer or legal supervisor for high-value/complex cases.
5. **`PENDING_SECOND_SIGNATURE`**: First supervisor signature recorded (`SIG_SUPERVISOR_<ID1>`); awaiting secondary independent supervisor sign-off.
6. **`RESOLVED_CONFIRMED_FRAUD`**: Terminal status confirming malicious fraud (requires two distinct supervisor signatures).
7. **`RESOLVED_FALSE_POSITIVE`**: Terminal status closing benign alert (requires two distinct supervisor signatures).

---

## 🔐 Four-Eyes Dual Authorization Rule

To satisfy SOC2 CC6.1, ISO 27001 A.9.4.2, and EU AI Act Article 14 human oversight requirements:
- Resolving a case (`RESOLVED_CONFIRMED_FRAUD` or `RESOLVED_FALSE_POSITIVE`) **strictly requires TWO DISTINCT supervisor signatures** matching format `SIG_SUPERVISOR_<ID>`.
- Both signatures must carry different supervisor identities (`SIG_SUPERVISOR_ALICE`, `SIG_SUPERVISOR_BOB`). Submitting a single signature or having the same supervisor sign twice raises `InvalidCaseTransitionError`.
- Cases can enter `PENDING_SECOND_SIGNATURE` asynchronously after the first supervisor signs, allowing distributed, asynchronous dual-control authorization across shifts.
