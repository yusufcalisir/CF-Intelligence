# 🔌 Enterprise Bank Connector SDK Integration Guide (`cfi-connector-sdk`)

The `cfi-connector-sdk` package provides standardized, versioned interfaces for external banking IT engineering teams to connect core banking transaction systems (e.g. Oracle Flexcube, Temenos Transact, Thought Machine Vault, Finacle) to the Collaborative Fraud Intelligence (CFI) network.

> [!NOTE]
> All SDK adapters strictly enforce the Zero Raw PII invariant. Customer accounts, IBANs, and national IDs are masked locally using tenant-salted HMAC-SHA256 prior to network transmission. For platform REST APIs and developer portals, see [`docs/developer_and_api_portal.md`](developer_and_api_portal.md). For network deployment guides, see [`docs/deployment_guide.md`](deployment_guide.md).

---

## 🛠️ 1. Installation & Environment Setup

Install the SDK directly from the local repository directory or package wheel:

```bash
# Standard installation
pip install sdk/python

# Editable development installation
pip install -e sdk/python
```

### SDK Module Structure
```
sdk/python/cfi_connector_sdk/
├── __init__.py               # Public exports & version declaration (__version__ = "1.0.0")
├── adapters/
│   ├── entity_adapter.py     # BaseEntityAdapter (HMAC-SHA256 customer & account masking)
│   ├── feature_adapter.py    # BaseFeatureAdapter (Velocity & ratio feature extraction)
│   └── transaction_adapter.py# BaseTransactionAdapter & NormalizedTransaction schema
├── client/
│   └── local_fl_client.py    # LocalFLClient (mTLS connection & gradient/weights submission)
└── health.py                 # ConnectorHealthMonitor & ConnectorHealthStatus probes
```

---

## 🧩 2. Core Adapter Interfaces

### 2.1 Transaction Ingestion Adapter (`BaseTransactionAdapter`)

Extend `BaseTransactionAdapter` to transform heterogeneous core banking JSON, ISO 20022 `pacs.008`, or XML payment records into canonical [`NormalizedTransaction`](../sdk/python/cfi_connector_sdk/adapters/transaction_adapter.py) objects:

```python
from typing import Any, Dict
from cfi_connector_sdk import BaseTransactionAdapter, NormalizedTransaction

class CoreBankingTransactionAdapter(BaseTransactionAdapter):
    def parse_native_payload(self, payload: Dict[str, Any]) -> NormalizedTransaction:
        return NormalizedTransaction(
            transaction_id=str(payload["tx_ref_num"]),
            account_id=str(payload["debtor_iban"]),
            counterparty_account_id=str(payload["creditor_iban"]),
            amount=float(payload["monetary_amount"]),
            currency=str(payload.get("currency_code", "EUR")),
            merchant_category_code=str(payload.get("mcc", "6012")),
            channel_type=str(payload.get("payment_channel", "ONLINE")),
        )

# Validate transaction against consortium schema invariants
adapter = CoreBankingTransactionAdapter()
tx = adapter.parse_native_payload({
    "tx_ref_num": "TXN_99182",
    "debtor_iban": "DE89370400440532013000",
    "creditor_iban": "FR7630006000011234567890189",
    "monetary_amount": 25000.0,
    "currency_code": "EUR",
    "mcc": "6012",
    "payment_channel": "MOBILE",
})
assert adapter.validate_schema(tx) is True
```

---

### 2.2 Privacy-Preserving Customer Entity Masking (`BaseEntityAdapter`)

Use `BaseEntityAdapter` to apply tenant-salted HMAC-SHA256 irreversible hashing to customer identifiers, debtor accounts, and entity metadata inside the bank's secure perimeter:

```python
from cfi_connector_sdk import BaseEntityAdapter

entity_adapter = BaseEntityAdapter(bank_salt="sec_bank_alpha_salt_9983")

# 1. Deterministic one-way customer identifier hashing
masked_id = entity_adapter.hash_customer_id("ACC-883920192")
print(masked_id)  # 64-character SHA-256 HMAC digest

# 2. Masking complete entity payloads
raw_payload = {
    "customer_id": "CUST_88492",
    "account_number": "ACC_10928392",
    "name": "Jane Doe",
}
masked_payload = entity_adapter.mask_entity_payload(raw_payload)
# customer_id and account_number are replaced with cryptographic digests
```

---

### 2.3 Velocity & Ratio Feature Extraction (`BaseFeatureAdapter`)

Extract topological velocity indicators and 24-hour transaction burst ratios from historical windows:

```python
from cfi_connector_sdk import BaseFeatureAdapter

feature_adapter = BaseFeatureAdapter()
features = feature_adapter.extract_velocity_features(current_tx=tx, history=[])

print("Velocity Features:", features)
# Returns: amount, tx_count_1h, tx_count_24h, amount_sum_24h, amount_ratio_24h, velocity_surge_detected
```

---

### 2.4 Connector Health & Certificate Monitoring (`ConnectorHealthMonitor`)

The SDK includes active probes to verify message broker reachability and alert when mTLS client certificates approach expiration:

```python
from cfi_connector_sdk import ConnectorHealthMonitor

monitor = ConnectorHealthMonitor(
    broker_host="message-broker.bank-internal",
    broker_port=5672,
    cert_path="/etc/ssl/certs/bank_node.crt",
)
report = monitor.get_health_report()

print(f"Status: {report.status}")  # "HEALTHY" | "DEGRADED" | "UNHEALTHY"
print(f"Broker Reachable: {report.broker_connected}")
print(f"mTLS Days Remaining: {report.cert_days_remaining}")
```

---

## 🚀 3. Local Federated Learning Client (`LocalFLClient`)

Use `LocalFLClient` to maintain an encrypted mTLS session with the central CFI coordinator and submit differential-privacy masked model updates:

```python
from cfi_connector_sdk import LocalFLClient

client = LocalFLClient(
    bank_id="bank-alpha",
    coordinator_url="coordinator.cfi-network.internal:50051",
    cert_path="/etc/ssl/certs/bank_alpha.crt",
    key_path="/etc/ssl/certs/bank_alpha.key",
    ca_path="/etc/ssl/certs/cfi_ca.crt",
)

# 1. Establish mTLS channel
client.connect()
assert client.is_connected is True

# 2. Option A: Submit compressed binary gradient bytes
gradient_bytes = b"\x01\x02\x03\x04" * 256
res_grad = client.submit_gradient(
    round_id=5,
    masked_gradient_bytes=gradient_bytes,
    dp_epsilon_used=0.75,
)
print("Gradient Submission Status:", res_grad["status"])

# 3. Option B: Submit dictionary layer weights (backward compatible)
res_weights = client.submit_local_weights(
    round_id=5,
    weights={"layer1.weight": [0.12, -0.45, 0.88]},
    dp_epsilon=0.75,
    num_samples=1000,
)
print("Weights Submission Status:", res_weights["status"])
```

---

## 📖 4. Reference Implementation

A fully executable end-to-end reference script is provided in [`sdk/examples/reference_bank_connector.py`](../sdk/examples/reference_bank_connector.py):

```bash
python sdk/examples/reference_bank_connector.py --bank-id bank-a --broker-host localhost
```

---

## 🧪 5. Automated Unit Test Suite Parity

The SDK is backed by **11 automated unit tests** in [`sdk/python/tests/`](../sdk/python/tests):

```bash
python -m pytest sdk/python/tests/ -v
# 11 passed in 0.63s (100% Pass)
```

| Test File | Test Count | Scope |
| :--- | :---: | :--- |
| `test_adapters.py` | 4 | `NormalizedTransaction` schema bounds, currency validation, `BaseFeatureAdapter` velocity metrics, `BaseEntityAdapter` HMAC-SHA256 determinism |
| `test_client.py` | 3 | `LocalFLClient` connection lifecycle, `submit_gradient` compression, and `submit_local_weights` backwards compatibility |
| `test_health.py` | 4 | `ConnectorHealthMonitor` healthy reports, broker disconnection detection, and degraded certificate expiration warnings |
