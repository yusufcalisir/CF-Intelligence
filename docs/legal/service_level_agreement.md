# Enterprise Service Level Agreement (SLA) Contract Template

**Document Reference:** `CFI-LEGAL-SLA-2026-V2`  
**Applicable Service Tiers:** Growth FinTech (Tier 2), Enterprise Bank (Tier 3), Consortium Cluster (Tier 4)

---

## 1. Core Service Commitments & Availability Targets

The Vendor commits to providing continuous, high-availability execution for the CF-Intelligence API Gateway, Real-Time Risk Scoring Engine, and Federated Learning Coordinator in accordance with the following target SLAs:

| Service SLA Dimension | Tier 2 (Growth FinTech) | Tier 3 (Enterprise Bank) | Tier 4 (Consortium Network) |
| :--- | :---: | :---: | :---: |
| **Monthly Uptime SLA** | **99.90%** | **99.99%** | **99.999% (Fault-Tolerant)** |
| **Allowed Unscheduled Downtime** | $\approx 43.8\text{ min/month}$ | $\approx 4.38\text{ min/month}$ | $\approx 26.3\text{ sec/month}$ |
| **Inference Latency SLA (p99)** | $< 15.0\text{ ms}$ | $< 14.2\text{ ms}$ | $< 12.0\text{ ms}$ (Local Edge) |
| **Recovery Time Objective (RTO)** | $< 60\text{ seconds}$ | $< 30\text{ seconds}$ | $< 10\text{ seconds}$ (Active-Active) |
| **Recovery Point Objective (RPO)** | $0\text{ data loss}$ (HA DB) | $0\text{ data loss}$ (Multi-Region) | $0\text{ data loss}$ (Raft Consensus)|

---

## 2. Automated Service Credit Penalty Structure

If the Vendor fails to meet the monthly availability SLA, the Customer is contractually entitled to the following automated Service Credits applied against the subsequent monthly invoice:

### Tier 3 (Enterprise Bank - 99.99% SLA Target) Credit Matrix:

| Actual Monthly Availability Measured | Allowed Downtime Window | Service Credit Percentage Applied |
| :--- | :--- | :---: |
| **$\ge 99.99\%$** | $\le 4.38\text{ minutes}$ | **$0\%$ (Compliant)** |
| **$99.90\% - 99.98\%$** | $4.39\text{ min} - 43.8\text{ min}$ | **$10\%$ Credit Discount** |
| **$99.00\% - 99.89\%$** | $43.9\text{ min} - 7.2\text{ hours}$ | **$25\%$ Credit Discount** |
| **$95.00\% - 98.99\%$** | $7.3\text{ hours} - 36.5\text{ hours}$ | **$50\%$ Credit Discount** |
| **$< 95.00\%$** | $> 36.5\text{ hours}$ | **$100\%$ Credit Discount (Full Month Refund)** |

---

## 3. Incident Severity Levels & Technical Support Response Times

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        SUPPORT SEVERITY MATRIX & SLA RESPONSE TIMES                    │
├──────────────┬──────────────────────────────────────────┬──────────────┬───────────────┤
│ SEVERITY     │ DEFINITION                               │ INITIAL RESP.│ TARGET RESOLV.│
├──────────────┼──────────────────────────────────────────┼──────────────┼───────────────┤
│ P1: CRITICAL │ Core Scoring API down; 100% txns blocked │ ≤ 15 minutes │ ≤ 2 hours     │
│ P2: HIGH     │ FL round failure or latency >50ms        │ ≤ 1 hour     │ ≤ 6 hours     │
│ P3: MEDIUM   │ Non-critical portal issue, minor telemetry│ ≤ 4 hours    │ ≤ 24 hours    │
│ P4: LOW      │ Feature request, general inquiry         │ ≤ 1 bus. day │ Next Release  │
└──────────────┴──────────────────────────────────────────┴──────────────┴───────────────┘
```

---

## 4. Maintenance Windows & Exclusions

1. **Scheduled Maintenance**: The Vendor may schedule routine infrastructure maintenance during off-peak weekend hours (Sunday 02:00 – 04:00 UTC) with a minimum of **five (5) business days' prior written notice**. Scheduled maintenance is excluded from downtime calculations.
2. **Exclusions**: SLA guarantees shall not apply to downtime caused by:
   * Failures in the Customer's internal core banking ledger or local network connectivity;
   * Customer's unapproved modifications to `cfi-agent` container runtime configurations;
   * Extreme Force Majeure events.
