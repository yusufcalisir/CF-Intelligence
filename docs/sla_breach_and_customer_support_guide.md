# Customer Support, SLA Breach Remediation & Service Credit Guide (2026 Edition)

**Document Reference:** `CFI-SUPPORT-SLA-2026-V2`  
**Applicable SLA Terms:** [`docs/legal/service_level_agreement.md`](legal/service_level_agreement.md)

---

## 1. Enterprise Customer Support Tiers & Dedicated Hotlines

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        CUSTOMER SUPPORT ESCALATION & COVERAGE TIERS                                    │
├──────────────────────┬──────────────────────┬──────────────────────┬───────────────────────────────────┤
│ SUPPORT TIER         │ COVERED PLANS        │ RESPONSE CHANNELS    │ COVERAGE WINDOW & SLA             │
├──────────────────────┼──────────────────────┼──────────────────────┼───────────────────────────────────┤
│ **Standard Support** │ Tier 1 (Pilot / DP)  │ Email & Zendesk Desk │ 8x5 Business Hours (≤ 24 hours)   │
│ **Priority Support** │ Tier 2 (Growth FinT.)│ Slack Channel & Web  │ 8x5 Business Hours (≤ 4 hours)    │
│ **Dedicated 24/7**   │ Tier 3 (Enterprise)  │ Phone Hotline & Slack│ 24/7/365 (≤ 15 mins for P1)       │
│ **Consortium TAM**   │ Tier 4 (Consortium)  │ Dedicated TAM Bridge │ 24/7/365 Dedicated SRE Hotline    │
└──────────────────────┴──────────────────────┴──────────────────────┴───────────────────────────────────┘
```

---

## 2. Automated SLA Breach Detection & Notification Workflow

If a service degradation exceeds contractual thresholds ($Uptime < 99.99\%$ or Latency $p99 > 15\text{ms}$):

```
[Prometheus / OTel Monitor] ──(SLA Breach Detected)──> [Automated SLA Incident Logger]
                                                                  │
                                                                  ▼
[CFO / Billing System] <──(Issue Service Credit)── [Generate Monthly SLA Compliance Audit]
```

1. **Real-Time Telemetry Tracking**: System monitors continuous monthly rolling availability on Prometheus `:9090`.
2. **Instant Customer Notification**: In the event of a P0/P1 outage, an automated incident notice is broadcast to registered CISO/CRO contacts within **fifteen (15) minutes**.
3. **Root Cause Analysis (RCA) Delivery**: A formal forensic RCA document signed by the Lead SRE is delivered within **seventy-two (72) hours**.

---

## 3. Contractual Service Credit Claims & Automated Invoicing

Customers are entitled to claim service credits under Section 2 of the SLA:

### 3.1. Credit Calculation Schedule (Tier 3 Enterprise - 99.99% SLA Target)

$$\text{Service Credit Amount} = \text{Monthly Base Subscription Fee} \times \text{Credit Percentage}$$

| Measured Monthly Availability | Allowed Monthly Downtime | Service Credit Discount | Tier 3 ($12k/mo) Credit |
| :--- | :--- | :---: | :---: |
| **$99.90\% - 99.98\%$** | $4.39\text{ min} - 43.8\text{ min}$ | **$10\%$** | **$\$1,200$ credit** |
| **$99.00\% - 99.89\%$** | $43.9\text{ min} - 7.2\text{ hours}$ | **$25\%$** | **$\$3,000$ credit** |
| **$95.00\% - 98.99\%$** | $7.3\text{ hours} - 36.5\text{ hours}$ | **$50\%$** | **$\$6,000$ credit** |
| **$< 95.00\%$** | $> 36.5\text{ hours}$ | **$100\%$** | **$\$12,000$ credit (Full Refund)** |

### 3.2. Claim Submission & Automated Application Procedure
* **No Bureaucratic Delay**: Credits are calculated automatically at the end of each billing cycle by `sla_audit_reporter.py` and deducted directly from the subsequent monthly invoice.
* **Manual Claim Window**: If a customer disputes availability calculations, a claim may be filed via `support@cf-intelligence.bank` within thirty (30) days of month-end.
