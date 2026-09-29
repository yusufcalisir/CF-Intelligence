# Customer Support, SLA Breach Remediation & Service Credit Guide (2026 Edition)

**Document Reference:** `CFI-SUPPORT-SLA-2026-V2`  
**Applicable SLA Terms:** [`docs/legal/service_level_agreement.md`](legal/service_level_agreement.md)

---

## 1. Enterprise Customer Support Tiers & Dedicated Hotlines

| Support Tier | Covered Plans | Response Channels | Coverage Window & SLA |
| :--- | :--- | :--- | :--- |
| **Standard Support** | Tier 1 (Pilot / DP) | Enterprise Support Desk & Portal | 8x5 Business Hours (≤ 24 hours) |
| **Priority Support** | Tier 2 (Growth FinTech) | Dedicated Slack Channel & Web | 8x5 Business Hours (≤ 4 hours) |
| **Dedicated 24/7** | Tier 3 (Enterprise Bank) | Dedicated Phone Hotline & Slack | 24/7/365 (≤ 15 mins for P1) |
| **Consortium TAM** | Tier 4 (Consortium) | Dedicated Technical Account Manager (TAM) | 24/7/365 Dedicated SRE Hotline |

---

## 2. Automated SLA Breach Detection & Notification Workflow

If a service degradation exceeds contractual thresholds (Uptime < 99.99% or Latency p99 > 15ms):

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

$$\mathrm{Service\ Credit\ Amount} = \mathrm{Monthly\ Base\ Subscription\ Fee} \times \mathrm{Credit\ Percentage}$$

| Measured Monthly Availability | Allowed Monthly Downtime | Service Credit Discount | Tier 3 ($12k/mo) Credit |
| :--- | :--- | :---: | :---: |
| **99.90% – 99.98%** | 4.39 min – 43.8 min | **10%** | **$1,200 credit** |
| **99.00% – 99.89%** | 43.9 min – 7.2 hours | **25%** | **$3,000 credit** |
| **95.00% – 98.99%** | 7.3 hours – 36.5 hours | **50%** | **$6,000 credit** |
| **< 95.00%** | > 36.5 hours | **100%** | **$12,000 credit (Full Refund)** |

### 3.2. Claim Submission & Automated Application Procedure
* **No Bureaucratic Delay**: Credits are calculated automatically at the end of each billing cycle by `SLAContractEngine.generate_monthly_penalty_report()` (`sla_contract_engine.py`) and deducted directly from the subsequent monthly invoice.
* **Manual Claim Window**: If a customer disputes availability calculations, a formal credit claim may be filed via their designated enterprise support channel (Support Desk Portal, Slack Connect, or dedicated TAM bridge) within thirty (30) days of month-end.
