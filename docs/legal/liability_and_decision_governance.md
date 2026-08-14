# Legal Liability & Risk Decision Governance: False Positives, False Negatives & Automated Blocking (2026 Edition)

**Document Reference:** `CFI-LEGAL-GOV-2026-V2`  
**Applicable Standards:** EU AI Act (Articles 14 & 15: Human Oversight & Accuracy), PSD2 Article 94 (Fraud Prevention), US Uniform Commercial Code (UCC § 4A-202 / § 4A-204), UK Payment Services Regulations 2017.

---

## 1. The Core Legal Question: Who is Liable When a "BLOCK" Decision is Wrong?

In automated real-time fraud detection and anti-money laundering, two primary dispute scenarios arise:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 AUTOMATED RISK SCORING DISPUTE VECTORS                                 │
├────────────────────────────────────────────────────┬───────────────────────────────────────────────────┤
│ SCENARIO A: FALSE POSITIVE (WRONGFUL BLOCK)        │ SCENARIO B: FALSE NEGATIVE (MISSED FRAUD)         │
├────────────────────────────────────────────────────┼───────────────────────────────────────────────────┤
│ • Legitimate corporate transfer ($500k) is blocked.│ • Account takeover fraud ring drains $120k.       │
│ • Customer misses acquisition deadline / penalty.  │ • Merchant or cardholder files chargeback/dispute.│
│ • LEGAL QUESTION: Can Customer sue Vendor for      │ • LEGAL QUESTION: Is Vendor liable to reimburse   │
│   commercial damages / lost profits?               │   the stolen funds?                               │
├────────────────────────────────────────────────────┼───────────────────────────────────────────────────┤
│ VERDICT: VENDOR IMMUNE (BANK POLICY ARBITER)       │ VERDICT: VENDOR IMMUNE (STATISTICAL SCORING AID)  │
└────────────────────────────────────────────────────┴───────────────────────────────────────────────────┘
```

---

## 2. The 4-Layer Institutional Decision & Governance Architecture

To maintain strict regulatory compliance under **EU AI Act Article 14 (Human Oversight)** and global banking secrecy, CF-Intelligence is architected as an **Advisory Decision-Support Engine**, not the autonomous legal executioner:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        INSTITUTIONAL RISK DECISION & RESPONSIBILITY STACK                              │
├───────────────────────────────────┬───────────────────────────────────┬────────────────────────────────┤
│ LAYER                             │ OPERATING ENTITY                  │ LEGAL RESPONSIBILITY           │
├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────┤
│ Layer 1: Statistical Risk Scoring │ Vendor (CF-Intelligence)          │ Algorithmic Transparency, SHAP │
│                                   │                                   │ Attributions & SLA Uptime.     │
├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────┤
│ Layer 2: Policy Threshold Rules   │ Bank Risk Committee / CRO         │ Setting score thresholds       │
│                                   │                                   │ (e.g. Score > 0.85 => BLOCK).  │
├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────┤
│ Layer 3: Payment Interception     │ Bank Core Ledger / Switch         │ Physical halting of ISO 20022  │
│                                   │                                   │ `pacs.008` transfer message.   │
├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────┤
│ Layer 4: Human-in-the-Loop Review │ Bank AML / Fraud Compliance Team  │ 4-Eyes Manual Override, SAR    │
│                                   │                                   │ filing & Customer Redress.     │
└───────────────────────────────────┴───────────────────────────────────┴────────────────────────────────┘
```

---

## 3. Detailed Legal Allocation of Liability

### 3.1. False Positives (Wrongful Transaction Blocks / Account Freezes)
* **The Legal Principle**: When a bank blocks a transaction based on a CF-Intelligence score of `0.92`, the bank is exercising its statutory mandate to prevent financial crime under national AML laws (e.g., MASAK, FinCEN BSA, EU 6AMLD, UK POCA).
* **Vendor Safe Harbor**: 
  1. The Vendor delivers probabilistic risk signals ($P(\text{Fraud}) \in [0.0, 1.0]$) and local explainability vectors (SHAP values).
  2. The Vendor **never unilaterally issues execution commands to banking ledgers**.
  3. Under Section 4.4 of the Enterprise Terms of Service (ToS), the Vendor is expressly indemnified against indirect, consequential, or punitive damages arising from legitimate transactions delayed, investigated, or declined by the Institution.
* **Bank's Legal Defense against Customers**: Banking terms and conditions in all major jurisdictions permit reasonable delays and temporary freezes for anti-fraud validation without incurring liability for commercial lost profits (Safe Harbor for Good Faith Fraud Prevention).

---

### 3.2. False Negatives (Undetected Fraud & Chargeback Losses)
* **The Legal Principle**: Fraud detection is a statistical estimation problem over adversarial, evolving distributions. No mathematical or machine learning system can achieve $100.0\%$ recall without blocking $100\%$ of legitimate economic activity.
* **Vendor Warranty Scope**:
  1. The Vendor warrants that the platform executes with $<15\text{ms}$ latency, achieves $\ge 99.99\%$ uptime SLA, enforces zero raw PII leakage, and applies Byzantine-robust federated aggregation.
  2. The Vendor **does NOT act as an insurer or guarantor against fraud losses**.
  3. The Institution retains sole responsibility for maintaining fraud reserves, chargeback processing insurance, and customer dispute resolution workflows.

---

### 3.3. Algorithmic Bias & Disparate Impact Safe Harbor (EU AI Act Art. 10 & 13)
* The Vendor warrants that the risk scoring ensemble does not ingest prohibited protected attributes (e.g. race, religion, gender, political affiliation).
* Automated Continuous Fairness Auditing computes the **Disparate Impact Ratio ($DI$)**:
  $$DI = \frac{P(\text{Score} > \tau \mid \text{Group A})}{P(\text{Score} > \tau \mid \text{Group B})} \ge 0.80$$
* Verifiable audit logs prove non-discriminatory algorithmic scoring, protecting participating institutions during regulatory audits.

---

## 4. Contractual Clause Language for Master Services Agreements (MSA)

Participating institutions incorporate the following binding covenant in their commercial MSAs:

> **Section 8.4 (Risk Scoring Advisory Nature & Limitation of Operational Liability):**  
> *(a) The Customer acknowledges that the CF-Intelligence Platform generates statistical risk assessments and feature attributions intended to assist the Customer’s fraud and AML operations. The Platform does not make final, binding, or unreviewable legal determinations regarding transaction approval, rejection, or account termination.*  
> *(b) The Customer retains exclusive discretion over the configuration of risk scoring thresholds, automated blocking rules, and manual review workflows.*  
> *(c) To the maximum extent permitted by applicable law, Vendor shall not be liable to Customer, its account holders, or any third party for any direct or indirect loss of business, revenue, profits, goodwill, or regulatory fines arising out of or relating to: (i) the blocking, delaying, or rejection of any legitimate transaction (False Positive); or (ii) the processing or approval of any fraudulent, unauthorized, or criminal transaction (False Negative).*
