# Enterprise Terms of Service & Consortium Governance Agreement (ToS)

**Document Reference:** `CFI-LEGAL-TOS-2026-V2`  
**Effective Date:** January 1, 2026

---

## 1. Acceptance of Terms & Master License Grant

By executing an Enterprise Order Form or connecting an edge agent node to the CF-Intelligence network, the Customer ("Institution") agrees to be bound by these Enterprise Terms of Service ("**ToS**").

### 1.1. Commercial Subscription Grant
The Vendor grants the Institution a non-exclusive, non-transferable, worldwide enterprise license during the subscription term to:
1. Deploy the `cfi-agent` edge runtime container within the Institution's on-premises or private cloud VPC environment;
2. Execute real-time risk scoring queries against the deployed GNN and ensemble inference endpoints;
3. Participate in consortium federated training rounds and receive aggregated global model weight updates.

---

## 2. Consortium Rules of Engagement & Adversarial Defense Policies

To preserve the mathematical and operational integrity of the collaborative fraud intelligence network, all participating institutions covenant and agree to the following rules:

### 2.1. Prohibition on Adversarial Weight Poisoning
* The Institution shall not deliberately inject falsified label distributions, random noise vectors, or poisoned gradient updates intended to degrade global fraud detection efficacy or insert model backdoors.
* **Automated Byzantine Enforcement**: The Central Coordinator continuously runs the **Krum & Trimmed Mean Byzantine Defense Filter**. If an Institution's gradient cosine distance deviates beyond statistical tolerance ($p < 0.001$), the update is automatically quenched without impacting other consortium members.
* **Malicious Intent Penalty**: In the event of confirmed intentional gradient poisoning or sybil node injection, the offending Institution's cryptographic credentials shall be immediately revoked via Vault PKI CRL revocation, and the Institution shall be subject to contractual indemnification for remediation costs.

### 2.2. Node Availability & Straggler Mitigation
* Participating institutions in Enterprise and Consortium tiers agree to maintain stable network connectivity during scheduled FL rounds.
* Nodes that fail to submit gradient updates within the round deadline ($T_{\text{timeout}} = 15\text{s}$) are dynamically dropped from that specific aggregation round via the **FedProx Straggler Mitigation Engine** without penalizing global round completion.

---

## 3. Intellectual Property (IP) Rights & Data Ownership

```
┌────────────────────────────────────────────────────────────────────────┐
│                        INTELLECTUAL PROPERTY BOUNDARIES                │
├───────────────────────────────────┬────────────────────────────────────┤
│ INSTITUTIONAL OWNERSHIP (BANK)    │ CONSORTIUM & VENDOR OWNERSHIP (CFI)│
├───────────────────────────────────┼────────────────────────────────────┤
│ • Raw transaction ledgers         │ • Base FL Orchestration Engine    │
│ • Customer account databases      │ • Proprietary GNN Architectures    │
│ • Local feature engineering tables│ • Aggregated Global Model Weights  │
│ • Generated SAR narrative filings │ • Central Telemetry & SIEM Adapters│
└───────────────────────────────────┴────────────────────────────────────┘
```

1. **Customer Ledger Exclusivity**: All raw customer data, transaction records, ISO 20022 messages, and internal compliance filings remain the sole and exclusive property of the Institution.
2. **Global Collaborative Model License**: The aggregated global GNN model parameters synthesized across consortium rounds are licensed to all participating active members for internal fraud prevention purposes.

---

## 4. Warranties, Disclaimer & Limitation of Liability

1. **High-Risk AI System Compliance Warranty**: The Vendor warrants that the CFI Platform is architecturally aligned with the technical requirements of the EU AI Act (Articles 10, 13, 14, 15), including documented model explainability, disparate impact evaluation ($DI \ge 0.80$), and differential privacy guarantees.
2. **Fraud Prevention Disclaimer**: The Platform is a statistical risk scoring and decision support system. The Vendor does not guarantee that 100% of fraudulent transactions will be blocked. Final transaction approval or rejection decisions remain under the operational authority of the Institution's automated policy engine or human compliance analysts.
3. **Limitation of Liability**: Except for gross negligence, willful misconduct, or breach of the Data Processing Agreement (DPA), neither party's aggregate liability arising under this Agreement shall exceed the total fees paid by the Customer in the twelve (12) months preceding the incident.
