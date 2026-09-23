# Cloud Core Banking Architecture & Connector Gateway Guide

## 1. Overview & Architectural Scope

The **Collaborative Fraud Intelligence (CFI)** platform interfaces directly with modern cloud core banking engines, specifically **Mambu** (v2 REST/Webhooks) and **Thought Machine Vault Core** (real-time Posting Instruction Batches / gRPC / streaming ledger).

Financial institutions deploying CFI can deploy these pre-built connectors without writing bespoke adapter glue code. Ingested events are normalized in memory into canonical ISO 20022 entities (`pacs.008` credit transfer mappings), subjected to type-salted cryptographic HMAC pseudonymization (preserving Zero-Raw-PII across institutional boundaries), and routed to the 9-signal composite risk engine.

When suspicious multi-bank patterns (e.g., smurfing syndicates, rapid mule account depletion, or unauthorized push payment fraud) are identified, CFI dispatches automated or analyst-reviewed **provisional account holds** directly into the core banking system to freeze illicit funds before withdrawal.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CORE BANKING INTEGRATION ARCHITECTURE                          │
│                                                                                        │
│   ┌─────────────────────┐               ┌────────────────────────────────────────┐     │
│   │ Mambu Cloud Banking │               │   Thought Machine Vault Core Engine    │     │
│   │  (v2 REST/Webhooks) │               │   (Posting Instruction Batches - PIB)  │     │
│   └──────────┬──────────┘               └───────────────────┬────────────────────┘     │
│              │ X-Mambu-Signature                            │ X-Vault-Signature        │
│              ▼                                              ▼                          │
│   ┌──────────────────────────────────────────────────────────────────────────────┐     │
│   │          CFI Core Banking Gateway (`/connectors/core-banking/*`)             │     │
│   │   - HMAC-SHA256 Payload Signature Authentication & Replay Prevention         │     │
│   │   - Type-Salted Zero-Raw-PII Pseudonymization (`CONSORTIUM_HMAC_SALT`)       │     │
│   │   - Canonical ISO 20022 (`NormalizedTransaction`) Mapping Engine             │     │
│   │   - Distributed Idempotency Deduplication Key Cache                          │     │
│   └──────────────────────────────────────┬───────────────────────────────────────┘     │
│                                          │                                             │
│                                          ▼                                             │
│   ┌──────────────────────────────────────────────────────────────────────────────┐     │
│   │          9-Signal Composite Risk Scoring Engine & GNN Graph Inference         │     │
│   │   - Sub-15ms fast-path fraud scoring & multi-hop contagion detection         │     │
│   └──────────────────────────────────────┬───────────────────────────────────────┘     │
│                                          │                                             │
│                ┌─────────────────────────┴─────────────────────────┐                   │
│                │ Risk Score >= Threshold (or EPC camt.056 Recall)  │                   │
│                ▼                                                   ▼                   │
│   ┌───────────────────────────┐                       ┌────────────────────────────┐   │
│   │ Outbound Mambu Hold Block │                       │ Outbound Vault Restriction │   │
│   │ `POST /api/deposits/hold` │                       │ `POST /v1/restrictions`   │   │
│   └───────────────────────────┘                       └────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mambu Cloud Banking Connector (`MambuConnector`)

### 2.1 Inbound Webhook Event Handling
The `MambuConnector` processes Mambu v2 event notifications:
1. `deposit-transaction.created`: Ingests debit and credit transactions, maps `accountId`, `amount`, `currencyCode`, and `channel` to `NormalizedTransaction`.
2. `client.created`: Onboarding customer profiles are immediately pseudonymized using type-salted HMAC-SHA256 (`CONSORTIUM_HMAC_SALT`):
   $$\mathrm{Pseudonym} = \mathrm{SHA256}(\text{"CLIENT\_KEY"} \parallel \mathrm{RawID} \parallel \mathrm{Salt})[:16]$$
   Customer names, email addresses, and national identifiers never enter process memory in cleartext.
3. `account.hold`: Real-time notification of external balance blocks and reservations.

### 2.2 Inbound Security & Webhook Signatures
Mambu webhooks are authenticated via HMAC-SHA256 (`X-Mambu-Signature` header):
$$\mathrm{Sig}_{\mathrm{expected}} = \mathrm{HMAC}_{\mathrm{SHA256}}(\mathrm{Secret}, \mathrm{RawBodyBytes})$$
Replay attacks and tampered payloads are rejected with HTTP 401 Unauthorized before parsing.

### 2.3 Outbound Provisional Holds
When a fraud alert triggers automated containment, `MambuConnector.apply_provisional_hold()` executes:
- Dispatches `POST /api/deposits/{accountId}/blocks` with an idempotency token.
- Returns a cryptographic SHA-256 audit digest linking the hold action to the FININT case file:
  $$\mathrm{AuditDigest} = \mathrm{SHA256}(\mathrm{HoldID} \parallel \mathrm{AccountID} \parallel \mathrm{Amount} \parallel \mathrm{Timestamp})$$

---

## 3. Thought Machine Vault Core Connector (`ThoughtMachineConnector`)

### 3.1 Posting Instruction Batch (PIB) Processing
Vault Core processes financial transactions as atomic **Posting Instruction Batches (PIBs)**:
- Batches contain `custom_instruction`, `transfer`, or `settlement` directives.
- Each instruction consists of balanced credit and debit legs:
  - Debtor leg: `credit: false`, `account_id`, `amount`, `denomination` (e.g., EUR, GBP).
  - Creditor leg: `credit: true`, `account_id`, `amount`, `denomination`.
- `ThoughtMachineConnector` automatically unrolls multi-leg PIBs into standardized bilateral transaction edges for Graph Neural Network (GNN) graph construction.

### 3.2 Outbound Account Restrictions
When high contagion velocity is detected, `ThoughtMachineConnector.apply_provisional_hold()` executes:
- Dispatches `POST /v1/accounts/{account_id}/restrictions` to Vault Core with restriction type `SUSPECTED_FRAUD_PROVISIONAL_HOLD`.
- Idempotency tokens ensure that retried network requests never double-restrict or corrupt ledger state.

---

## 4. REST Gateway Endpoints & Schema Specification

The `core_banking_gateway` router exposes dual-prefix endpoints for seamless compatibility:

| Endpoint | Method | Dual-Prefix URL | Description |
|:---|:---|:---|:---|
| Mambu Inbound Webhook | `POST` | `/connectors/core-banking/mambu/webhook`<br>`/api/v1/connectors/core-banking/mambu/webhook` | Ingests Mambu transaction & client events |
| Thought Machine Inbound Webhook | `POST` | `/connectors/core-banking/thought-machine/webhook`<br>`/api/v1/connectors/core-banking/thought-machine/webhook` | Ingests Vault Core posting instruction batches |
| Provisional Hold Dispatcher | `POST` | `/connectors/core-banking/holds/provisional`<br>`/api/v1/connectors/core-banking/holds/provisional` | Dispatches account holds/restrictions (HTTP 201) |
| Core Banking Health & Telemetry | `GET` | `/connectors/core-banking/health`<br>`/api/v1/connectors/core-banking/health` | Connector telemetry, buffer depth & mode |

---

## 5. Factory Integration & Standalone Test Modes

Both connectors are registered in `BankConnectorFactory`:
```python
from app.infrastructure.connectors.factory import CONNECTOR_REGISTRY

# Resolvable keys:
# "mambu" -> MambuConnector
# "thought_machine", "thoughtmachine" -> ThoughtMachineConnector
```

If deployed in offline development or test environments where live cloud banking endpoints are not configured, both connectors automatically operate in **resilient standalone simulation mode** (`SIMULATED_LOOPBACK`), generating valid cryptographic audit receipts and maintaining full test fidelity without external SaaS dependencies.
