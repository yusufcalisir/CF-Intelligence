# Scientific Verification Inventory — Connector Framework

**Subsystem:** Connector Framework & Bank Integration Adapters  
**Audited Modules:** `app.application.interfaces.bank_connector`, `app.infrastructure.connectors.*`, `app.application.services.bank_onboarding_service`, `app.infrastructure.client_daemon.*`  
**Auditor Role:** Senior Researcher in Enterprise Integration, Distributed Systems, API Design, and Scientific Software Verification  
**Evaluation Standard:** Enterprise Integration Patterns (EIP), ISO 20022 MX, Berlin Group NextGenPSD2, AMQP 0-9-1, W3C HTTP/mTLS, Kafka SASL_SSL  
**Audit Date:** 2026-08-01  

---

## 1. Executive Summary & Inventory Overview

This document provides a comprehensive, rigorous scientific verification inventory for the **Connector Framework** implementation in the Privacy-Preserving Cross-Bank Fraud Detection system. The Connector Framework provides the decoupling layer, protocol translation adapters, message mappers, security/auth handshakes, serialization format engines, and daemon transport handlers that connect heterogeneous bank core systems to the federated learning orchestration engine.

A total of **20 distinct connector abstractions, transport protocol adapters, data contracts, and daemon execution components** were identified and analyzed.

---

## 2. Comprehensive Component Inventory

### Component 1: `BankConnectorInterface` (Abstract Port Interface)
- **Component:** `app.application.interfaces.bank_connector.BankConnectorInterface`
- **Purpose:** Abstract port interface decoupling the core federated learning orchestration platform from heterogeneous bank client integrations.
- **Architectural Role:** Primary Ports & Adapters (Hexagonal Architecture) Port interface defining the explicit contract for node dataset initialization, local training execution, and global model evaluation.
- **Interface Contract:**
  - `initialize(bank_id: str, num_transactions: int, seed: int = 42) -> dict[str, Any]`
  - `train(bank_id: str, weights: ModelWeights, learning_rate: float, batch_size: int, epochs: int, enable_dp: bool, dp_epsilon: float, dp_delta: float, dp_max_grad_norm: float, correlation_id: str, **kwargs: Any) -> dict[str, Any]`
  - `evaluate(bank_id: str, weights: ModelWeights, correlation_id: str) -> dict[str, Any]`
- **Expected Invariant:** Every concrete connector adapter MUST implement all three lifecycle methods, returning dictionaries containing standard status indicators and model weights/metrics without altering contract types.
- **Possible Implementation Risks:** Incompatible parameter signatures across different concrete implementations; synchronous blocking calls inside async event loops causing worker thread starvation.
- **Edge Cases:** `bank_id` containing special characters or unicode; missing `ModelWeights` layer shapes; unexpected optional `kwargs` passed during training.
- **Engineering Claim Being Made:** Achieves complete protocol abstraction, allowing arbitrary message queue, REST, file, or stream transports to plug seamlessly into FL round coordination.
- **Appropriate Verification Methodology:** Structural interface compliance tests, dynamic polymorphism verification, and protocol signature introspection.

---

### Component 2: `BaseBankConnector` (Abstract Adapter Base & Default Lifecycle Stubs)
- **Component:** `app.infrastructure.connectors.base_connector.BaseBankConnector`
- **Purpose:** Base class extending `BankConnectorInterface` with default stub implementations for stream/file ingestion connectors and abstract data ingestion contracts.
- **Architectural Role:** Abstract Base Class (ABC) providing fallback implementations for local training/eval and declaring pure abstract methods for streaming/batch consumption.
- **Interface Contract:**
  - Extends `BankConnectorInterface`
  - `@abstractmethod consume_stream() -> Generator[NormalizedTransaction, None, None]`
  - `@abstractmethod parse_batch(payload: Any) -> list[NormalizedTransaction]`
- **Expected Invariant:** Subclasses inheriting from `BaseBankConnector` MUST provide non-stub implementations for transaction stream consumption and batch payload parsing.
- **Possible Implementation Risks:** Ingestion-only connectors inheriting default `train` and `evaluate` stubs returning dummy metrics without warning caller when FL training is invoked.
- **Edge Cases:** Calling `train()` on pure data connectors (e.g. `ParquetConnector`); empty stream yields.
- **Engineering Claim Being Made:** Standardizes batch and stream payment transaction parsing into a uniform streaming generator model.
- **Appropriate Verification Methodology:** Subclass contract verification, ABC instantiation enforcement tests, and inheritance hierarchy analysis.

---

### Component 3: `NormalizedTransaction` (Canonical Transaction Schema Contract)
- **Component:** `app.infrastructure.connectors.base_connector.NormalizedTransaction`
- **Purpose:** Canonical Pydantic data model standardizing payment transaction attributes across all disparate bank integration formats (ISO 20022 XML, SWIFT MT103, PSD2 JSON, CSV, Parquet).
- **Architectural Role:** Canonical Data Model (CDM) pattern in Enterprise Integration Architecture.
- **Interface Contract:**
  - `transaction_id: str` (required)
  - `account_id: str` (required)
  - `counterparty_account_id: str` (required)
  - `amount: float` (required, `gt=0`)
  - `currency: str` (default `"USD"`)
  - `timestamp: datetime` (default `datetime.utcnow`)
  - `merchant_category_code: str` (default `"0000"`)
  - `origin_country: str` / `destination_country: str` (default `"US"`)
  - `device_fingerprint: str`, `ip_subnet: str`, `channel_type: str`
- **Expected Invariant:** All incoming raw payment payloads MUST be transformed into valid `NormalizedTransaction` instances with positive non-zero amounts (`amount > 0`).
- **Possible Implementation Risks:** Loss of precision converting high-precision financial decimals to 64-bit IEEE 754 floats; time zone offset corruption during ISO 8601 parsing.
- **Edge Cases:** Negative transaction amounts; missing debtor/creditor fields; malformed ISO 8601 timestamps; non-standard currency codes.
- **Engineering Claim Being Made:** Provides 100% field normalization across heterogeneous banking formats while enforcing schema validation at ingestion boundaries.
- **Appropriate Verification Methodology:** Pydantic schema boundary validation tests, property-based input fuzzing, and ISO 8601 timestamp parsing tests.

---

### Component 4: `BankConnectorFactory` (Dynamic Connector Resolution Engine)
- **Component:** `app.infrastructure.connectors.factory.BankConnectorFactory`
- **Purpose:** Resolves and instantiates concrete bank connector instances based on configuration settings and environment policies.
- **Architectural Role:** Factory Method / Abstract Factory Pattern.
- **Interface Contract:**
  - `@staticmethod get_connector(bank_id: str, settings: Settings, model_service: Any = None, data_generator: Any = None) -> BankConnectorInterface`
- **Expected Invariant:** In `production` environment (`APP_ENV=production`), factory MUST reject any connector type not present in `APPROVED_PRODUCTION_CONNECTORS` and MUST raise `ValueError` for deprecated mock connectors.
- **Possible Implementation Risks:** Unhandled exception when looking up dynamically named settings attributes (`{bank_id}_connector_type`); fallback defaults instantiating local file readers in production environments.
- **Edge Cases:** `bank_id` containing hyphens (e.g. `bank-alpha`) converted to underscore attribute lookups (`bank_alpha_connector_type`); missing settings attributes defaulting to `"parquet"`.
- **Engineering Claim Being Made:** Enforces strict Enterprise Zero-Mock Policy in production environments while enabling runtime protocol selection across 11 connector types.
- **Appropriate Verification Methodology:** Configuration matrix testing, policy enforcement verification, and production environment guard tests.

---

### Component 5: `ISO20022MessagingConnector` (ISO 20022 MX & SWIFT MT103 Adapter)
- **Component:** `app.infrastructure.connectors.iso20022_connector.ISO20022MessagingConnector`
- **Purpose:** Parses ISO 20022 MX XML messages (`pacs.008`, `pacs.002`, `camt.053`, `pain.001`) and legacy SWIFT MT103 text messages into `NormalizedTransaction` streams.
- **Architectural Role:** Specialized Financial Protocol Adapter & XML/SWIFT Serializer.
- **Interface Contract:**
  - `parse_pacs008_xml(xml_content: str) -> NormalizedTransaction`
  - `parse_pain001_xml(xml_content: str) -> NormalizedTransaction`
  - `parse_camt053_xml(xml_content: str) -> list[NormalizedTransaction]`
  - `parse_pacs002_xml(xml_content: str) -> NormalizedTransaction`
  - `parse_swift_mt103(mt103_text: str) -> NormalizedTransaction`
  - `validate_xml_schema(xml_content: str, schema_name: str) -> None`
- **Expected Invariant:** XML messages MUST contain required root tags (`FIToFICstmrCdtTrf`, `BkToCstmrStmt`, `CstmrCdtTrfInitn`); XML parsing errors or schema mismatches MUST trigger SIEM log events and raise `ValueError`.
- **Possible Implementation Risks:** XML External Entity (XXE) vulnerabilities if XML parser parses external DTDs (mitigated by `ET.fromstring` without entity expansion); regex parsing errors on malformed SWIFT `:32A:` tags.
- **Edge Cases:** Namespace-qualified XML nodes vs non-namespaced nodes; missing optional tags (`DbtrAcct`, `CdtrAcct`); SWIFT MT103 comma vs period decimal separators (`1000,50` vs `1000.50`).
- **Engineering Claim Being Made:** Fully parses ISO 20022 financial messaging standards and legacy SWIFT formats into normalized payment streams with automatic SIEM audit logging on parse failure.
- **Appropriate Verification Methodology:** XSD schema validation tests, XML payload fuzzing, SWIFT MT103 regex boundary tests, and SIEM parse error log verification.

---

### Component 6: `OpenBankingConnector` (Berlin Group NextGenPSD2 / UK Open Banking REST Adapter)
- **Component:** `app.infrastructure.connectors.open_banking_connector.OpenBankingConnector`
- **Purpose:** Queries Berlin Group NextGenPSD2 / UK Open Banking REST endpoints, manages OAuth2 Client Credentials tokens, calculates HTTP Digest/Signature headers, and handles HTTP 429 rate limits.
- **Architectural Role:** Open Banking Gateway Connector & HTTP REST Client with OAuth2/PSD2 Compliance.
- **Interface Contract:**
  - `fetch_account_transactions(account_id: str, date_from: str | None, date_to: str | None) -> list[NormalizedTransaction]`
  - `parse_psd2_payload(json_payload: dict[str, Any]) -> list[NormalizedTransaction]`
  - `_get_oauth2_token(force_refresh: bool) -> str`
  - `_get_headers(body_bytes: bytes) -> dict[str, str]`
- **Expected Invariant:** HTTP requests MUST include PSD2 mandatory headers (`X-Request-ID`, `Digest`, `PSU-IP-Address`, `Authorization: Bearer <token>`); OAuth2 tokens MUST be proactively refreshed when TTL $< 300\text{s}$.
- **Possible Implementation Risks:** Synchronous HTTP calls via `httpx.get` blocking execution threads; fallback mock token generation in production when token server is unreachable.
- **Edge Cases:** HTTP 429 Rate Limit responses with integer vs float `Retry-After` headers; empty `booked` or `pending` transaction arrays in PSD2 response payloads.
- **Engineering Claim Being Made:** Implements full Berlin Group NextGenPSD2 REST specification with automated OAuth2 token lifecycle management and transparent HTTP 429 backoff handling.
- **Appropriate Verification Methodology:** HTTP mock server tests, OAuth2 token TTL expiration tests, HTTP 429 header backoff tests, and PSD2 header structure validation.

---

### Component 7: `RESTBankConnector` (HTTP REST & Webhook Adapter)
- **Component:** `app.infrastructure.connectors.rest_connector.RESTBankConnector`
- **Purpose:** Triggers federated learning commands (`initialize`, `train`, `evaluate`) over bank client REST APIs and ingests transaction events from incoming HTTP webhooks.
- **Architectural Role:** Outbound HTTP Client & Inbound Webhook Handler with mTLS and HMAC Payload Signing.
- **Interface Contract:**
  - `initialize(bank_id: str, num_transactions: int, seed: int) -> dict[str, Any]`
  - `train(bank_id: str, weights: ModelWeights, ...) -> dict[str, Any]`
  - `evaluate(bank_id: str, weights: ModelWeights, correlation_id: str) -> dict[str, Any]`
  - `_sign_payload(payload: dict, headers: dict) -> tuple[bytes, dict]`
  - `_get_client() -> httpx.Client`
- **Expected Invariant:** Outbound payloads MUST be HMAC-SHA256 signed with `X-Payload-Signature` and `X-Payload-Timestamp` headers when `payload_signing_secret` is configured; mTLS client certificates MUST be loaded when `auth_type == "mtls"`.
- **Possible Implementation Risks:** Long HTTP timeouts ($120\text{s}$ during training) blocking thread pool capacity; fallback placeholder OAuth2 token generation when endpoint fails.
- **Edge Cases:** Missing client certificate or key files on disk; webhooks delivering single dict vs array of transaction objects.
- **Engineering Claim Being Made:** Provides secure, mTLS-enabled and HMAC-signed HTTP transport for federated learning coordination and real-time webhook ingestion.
- **Appropriate Verification Methodology:** HMAC signature verification tests, mTLS client initialization tests, HTTP timeout error handling tests, and REST payload structure tests.

---

### Component 8: `KafkaBankConnector` (Apache Kafka Streaming Transport)
- **Component:** `app.infrastructure.connectors.kafka_connector.KafkaBankConnector`
- **Purpose:** Publishes federated learning round control commands and weight updates to bank-specific Apache Kafka topics using SASL_SSL authentication.
- **Architectural Role:** Event-Driven Messaging Transport Adapter for Kafka-based banking topologies.
- **Interface Contract:**
  - `initialize(bank_id: str, num_transactions: int, seed: int) -> dict[str, Any]`
  - `train(bank_id: str, weights: ModelWeights, ...) -> dict[str, Any]`
  - `evaluate(bank_id: str, weights: ModelWeights, correlation_id: str) -> dict[str, Any]`
- **Expected Invariant:** Commands MUST be published to structured topics following the naming convention `{topic_prefix}.{bank_id}.{action}`; payloads MUST include serialized model weight layer shapes and flat arrays.
- **Possible Implementation Risks:** Kafka broker disconnection during FL training round; truncation of flat weight arrays in payload dict (`weights.flat_weights[:10]`).
- **Edge Cases:** Broker cluster unavailability; SASL_SSL credential authentication failure; large model weight payloads exceeding Kafka default message size limits ($1\text{MB}$).
- **Engineering Claim Being Made:** Enables enterprise event-driven federated learning orchestration over Apache Kafka with SASL_SSL / SCRAM-SHA-256 security protocols.
- **Appropriate Verification Methodology:** Kafka topic routing tests, SASL_SSL configuration verification, payload truncation auditing, and integration tests.

---

### Component 9: `RabbitMQBankConnector` (AMQP 0-9-1 Messaging Adapter)
- **Component:** `app.infrastructure.connectors.rabbitmq_connector.RabbitMQBankConnector`
- **Purpose:** Publishes FL commands over AMQP queues with exclusive correlation callback reply queues, durable message persistence, and SSL/TLS support.
- **Architectural Role:** Enterprise AMQP Queue Adapter with Request-Reply Correlation Pattern.
- **Interface Contract:**
  - `initialize(bank_id: str, num_transactions: int, seed: int) -> dict[str, Any]`
  - `train(bank_id: str, weights: ModelWeights, ...) -> dict[str, Any]`
  - `evaluate(bank_id: str, weights: ModelWeights, correlation_id: str) -> dict[str, Any]`
  - `_publish_and_await(routing_key: str, payload: dict, timeout: float) -> dict`
- **Expected Invariant:** Every published message MUST declare persistent delivery mode (`delivery_mode=2`) and MUST await matching correlation ID (`props.correlation_id == corr_id`) on an exclusive temporary callback queue.
- **Possible Implementation Risks:** Missing `pika` library at runtime returning `None` connection; blocking `process_data_events()` call timing out when worker node drops.
- **Edge Cases:** RabbitMQ connection loss during reply waiting loop; un-acknowledged messages in queue; missing `pika` package fallback behavior.
- **Engineering Claim Being Made:** Guarantees reliable RPC-style messaging over AMQP with message durability, exclusive reply queues, and correlation ID matching.
- **Appropriate Verification Methodology:** AMQP request-reply correlation tests, Pika connection exception tests, timeout boundary tests, and queue durability validation.

---

### Component 10: `RedisBankConnector` (Redis Pub/Sub Event Transport)
- **Component:** `app.infrastructure.connectors.redis_connector.RedisBankConnector`
- **Purpose:** Triggers federated learning commands asynchronously over Redis Pub/Sub channels and polls for response channel events with timeout boundaries.
- **Architectural Role:** Lightweight Event-Driven Pub/Sub Transport Adapter.
- **Interface Contract:**
  - `initialize(bank_id: str, num_transactions: int, seed: int) -> dict[str, Any]`
  - `train(bank_id: str, weights: ModelWeights, ...) -> dict[str, Any]`
  - `evaluate(bank_id: str, weights: ModelWeights, correlation_id: str) -> dict[str, Any]`
- **Expected Invariant:** Publishes payload to `bank_client_{bank_id}_{action}` channel and polls `bank_client_{bank_id}_{action}_response` channel until matching `correlation_id` is received or timeout is reached.
- **Possible Implementation Risks:** CPU busy-wait loop (`while time.perf_counter() < timeout`) consuming CPU cycles during response polling; message loss in Pub/Sub if subscriber is not connected at publish time.
- **Edge Cases:** Redis connection failure; response channel receiving mismatched correlation IDs; timeout expiration ($20\text{s}$ for init, $120\text{s}$ for train, $60\text{s}$ for eval).
- **Engineering Claim Being Made:** Provides low-latency event-driven channel communication for high-speed local development and testing environments.
- **Appropriate Verification Methodology:** Redis pub/sub channel contract tests, correlation ID matching tests, timeout exception verification, and CPU utilization profiling during polling.

---

### Component 11: `BatchEODFileConnector` (Batch End-Of-Day CSV/Parquet Adapter)
- **Component:** `app.infrastructure.connectors.batch_connector.BatchEODFileConnector`
- **Purpose:** Ingests and validates EOD batch CSV strings and Parquet row dictionaries, converting them into a fifo queue of `NormalizedTransaction` objects.
- **Architectural Role:** Bulk File Ingestion & Batch Payload Parsing Adapter.
- **Interface Contract:**
  - `parse_csv_stream(csv_content: str | bytes) -> list[NormalizedTransaction]`
  - `parse_parquet_rows(rows: list[dict[str, Any]]) -> list[NormalizedTransaction]`
  - `consume_stream() -> Generator[NormalizedTransaction, None, None]`
  - `parse_batch(payload: Any) -> list[NormalizedTransaction]`
- **Expected Invariant:** `consume_stream()` MUST yield transactions sequentially from `_batch_queue` until empty (`pop(0)` FIFO ordering).
- **Possible Implementation Risks:** $\mathcal{O}(N^2)$ queue pop overhead when popping from position 0 (`_batch_queue.pop(0)`) for large file drops ($100,000+$ items).
- **Edge Cases:** Alternative CSV column header names (`transaction_id` vs `tx_id`, `account_id` vs `sender`); missing timestamp fields defaulting to current UTC time; byte vs string CSV input.
- **Engineering Claim Being Made:** Flexible column mapping for legacy banking EOD batch file drops across CSV and Parquet formats.
- **Appropriate Verification Methodology:** CSV/Parquet column alias mapping tests, FIFO ordering tests, queue ingestion memory benchmarks, and boundary validation.

---

### Component 12: `ParquetConnector` (PyArrow Zero-Copy Benchmark Dataset Adapter)
- **Component:** `app.infrastructure.connectors.parquet_connector.ParquetConnector`
- **Purpose:** Reads disk-based Parquet and CSV benchmark datasets into Pandas DataFrames and provides PyArrow zero-copy streaming batch iterators.
- **Architectural Role:** Benchmark Dataset File Reader & High-Throughput Columnar Data Ingestion Adapter.
- **Interface Contract:**
  - `load_file(filepath: str | Path) -> list[NormalizedTransaction]`
  - `read_parquet_batches(filepath: str | Path, batch_size: int) -> Generator[list[NormalizedTransaction], None, None]`
  - `parse_batch(payload: Any) -> list[NormalizedTransaction]`
- **Expected Invariant:** `read_parquet_batches()` MUST yield PyArrow record batches of size `batch_size` without loading entire dataset into memory at once.
- **Possible Implementation Risks:** Missing PyArrow dependency raising runtime `ImportError`; pandas type conversion overhead on large dataset files.
- **Edge Cases:** File not found on disk (`FileNotFoundError`); non-existent file path passed to constructor; mixed timestamp data types (ISO string vs datetime object).
- **Engineering Claim Being Made:** Provides zero-copy, memory-efficient columnar data streaming for ultra-large multi-gigabyte benchmark datasets.
- **Appropriate Verification Methodology:** PyArrow record batch streaming tests, memory footprint profiling during batch reads, file extension routing tests, and missing file exception checks.

---

### Component 13: `StreamingPaymentConnector` (High-Throughput Raw Event Streamer)
- **Component:** `app.infrastructure.connectors.streaming_connector.StreamingPaymentConnector`
- **Purpose:** Ingests raw JSON strings or event dictionaries from streaming sources, validates fields, and buffers them for stream consumption.
- **Architectural Role:** In-Memory Event Streaming Buffer Adapter.
- **Interface Contract:**
  - `push_raw_event(event_data: dict[str, Any] | str) -> NormalizedTransaction`
  - `consume_stream() -> Generator[NormalizedTransaction, None, None]`
  - `parse_batch(payload: list[dict[str, Any]] | str) -> list[NormalizedTransaction]`
- **Expected Invariant:** Every pushed raw event MUST be converted into a `NormalizedTransaction` and appended to internal buffer `_buffer`.
- **Possible Implementation Risks:** In-memory buffer `_buffer` growing without bound if events are pushed faster than consumed; `json.loads` failure on malformed string input.
- **Edge Cases:** String JSON vs pre-parsed dict input; alternative field names (`mcc` vs `merchant_category_code`, `id` vs `transaction_id`); malformed ISO timestamps.
- **Engineering Claim Being Made:** Sub-millisecond raw event payload validation and normalization for real-time payment stream ingestion.
- **Appropriate Verification Methodology:** Event push and stream consumption unit tests, JSON string parsing tests, alias resolution tests, and memory buffer bounds checks.

---

### Component 14: `FixtureConnector` (Offline Test Fixture Ingestion Adapter)
- **Component:** `app.infrastructure.connectors.fixture_connector.FixtureConnector`
- **Purpose:** Test-only connector for loading sample datasets from Parquet, CSV, JSON, and XML fixture files on disk.
- **Architectural Role:** Test Double / Offline Fixture Loader.
- **Interface Contract:**
  - `__init__(fixture_path: str | Path)`
  - `fetch_transactions(limit: int) -> list[NormalizedTransaction]`
  - `consume_stream() -> Generator[NormalizedTransaction, None, None]`
- **Expected Invariant:** `FixtureConnector` MUST raise `ImportError` if executed in a production environment (`APP_ENV=production`).
- **Possible Implementation Risks:** Leakage of `FixtureConnector` into production runtime due to improper environment variable settings.
- **Edge Cases:** Unsupported file extensions (`.txt`, `.bin`); XML fixture files containing `camt.053` vs `pacs.008` root elements; missing fixture files.
- **Engineering Claim Being Made:** Enforces strict environment isolation, preventing test fixture readers from running in production.
- **Appropriate Verification Methodology:** Production environment guard tests (`APP_ENV=production`), multi-format file loader tests (Parquet/CSV/JSON/XML), and limit slicing tests.

---

### Component 15: `MQSkeletonBankConnector` (Deprecated MQ Connector Skeleton)
- **Component:** `app.infrastructure.connectors.mq_skeleton_connector.MQSkeletonBankConnector`
- **Purpose:** Legacy placeholder connector simulating message queue request-reply roundtrips with mock return dictionaries.
- **Architectural Role:** Deprecated Integration Stub.
- **Interface Contract:**
  - `initialize(...)`, `train(...)`, `evaluate(...)`
  - `_publish_and_await(...) -> dict[str, Any]`
- **Expected Invariant:** `BankConnectorFactory` MUST raise `ValueError` if `connector_type == "mq_skeleton"` under Enterprise Zero-Mock Policy.
- **Possible Implementation Risks:** Accidental use of mock connector in integration testing leading to false positive test passes.
- **Edge Cases:** Instantiation of deprecated class directly bypassing factory checks.
- **Engineering Claim Being Made:** Deprecated and formally blocked by factory policy enforcement.
- **Appropriate Verification Methodology:** Deprecation policy enforcement tests via `BankConnectorFactory`.

---

### Component 16: `retry_connector` (Transient I/O Retry Decorator)
- **Component:** `app.infrastructure.connectors.iso20022_connector.retry_connector`
- **Purpose:** Decorator providing exponential backoff retries on transient network/IO exceptions (`ConnectionError`, `TimeoutError`, `OSError`).
- **Architectural Role:** Resiliency Pattern / Fault Handling Decorator.
- **Interface Contract:**
  - `@retry_connector(max_attempts: int = 3, backoff_seconds: float = 2.0, exceptions: tuple = (ConnectionError, TimeoutError, OSError))`
- **Expected Invariant:** Retries operation up to `max_attempts` times with exponential delay $t = \text{backoff\_seconds} \times 2^{(\text{attempt}-1)}$; re-raises last exception if all attempts fail.
- **Possible Implementation Risks:** Synchronous `time.sleep` inside async methods blocking the asyncio event loop thread.
- **Edge Cases:** Non-transient exceptions (e.g. `ValueError`, `KeyError`) bypassing retry logic and raising immediately; `max_attempts = 1`.
- **Engineering Claim Being Made:** Provides automatic fault recovery against transient network hiccups during protocol parsing and I/O.
- **Appropriate Verification Methodology:** Fault injection retry tests, backoff calculation verification, exception filtering tests, and attempt limit verification.

---

### Component 17: `BankOnboardingService` (Automated Registration & Config Generator)
- **Component:** `app.application.services.bank_onboarding_service.BankOnboardingService`
- **Purpose:** Automates bank node onboarding pipeline, including regex validation, mTLS cert generation, Vault key path mapping, schema initialization, and YAML configuration rendering.
- **Architectural Role:** Domain Application Service for Participant Node Onboarding & Provisioning.
- **Interface Contract:**
  - `register_bank(bank_id: str, legal_name: str, ...) -> BankRegistration`
  - `issue_mtls_certificate(bank_id: str) -> tuple[str, str]`
  - `provision_tenant_schema(bank_id: str) -> None`
  - `provision_kms_key(bank_id: str) -> None`
  - `generate_connector_config(bank_id: str) -> str`
  - `activate_bank(bank_id: str) -> BankRegistration | None`
- **Expected Invariant:** `bank_id` MUST match `^[a-zA-Z0-9_-]{3,36}$`; duplicate `bank_id` registration MUST raise `BankAlreadyExistsError`; generated YAML config MUST contain valid `bank_id`, cert paths, and connector types.
- **Possible Implementation Risks:** Generating synthetic self-signed mTLS certificate strings in production instead of querying Vault PKI engine; uncommitted DB transactions during provisioning steps.
- **Edge Cases:** `bank_id` shorter than 3 or longer than 36 characters; invalid email formats; database constraint violations during concurrent onboarding requests.
- **Engineering Claim Being Made:** Automates 100% of participant bank node onboarding tasks, rendering valid daemon deployment YAML configs in a single pipeline.
- **Appropriate Verification Methodology:** Onboarding pipeline integration tests, `bank_id` regex validation tests, duplicate registration error handling tests, and YAML syntax validation.

---

### Component 18: `ExponentialBackoffReconnector` (Daemon Reconnection Handler)
- **Component:** `app.infrastructure.client_daemon.reconnector.ExponentialBackoffReconnector`
- **Purpose:** Manages outbound daemon reconnection attempts using exponential backoff with full random jitter for network resilience.
- **Architectural Role:** Resiliency Transport Component in Standalone Client Daemon.
- **Interface Contract:**
  - `compute_next_delay() -> float`
  - `reset() -> None`
  - `execute_with_retry(action: Callable, on_error_callback: Callable | None) -> T`
- **Expected Invariant:** Delay MUST scale exponentially $d = \text{initial\_delay} \times \text{backoff\_factor}^{\text{attempt}}$, capped at `max_delay`, and randomized by full jitter multiplier in $[0.5, 1.0]$; successful execution MUST reset attempt counter to 0.
- **Possible Implementation Risks:** Async `asyncio.sleep(delay)` during long outage causing daemon to stall if shutdown signal arrives while sleeping.
- **Edge Cases:** `current_attempt` exceeding `max_retries` raising the underlying exception; zero initial delay.
- **Engineering Claim Being Made:** Prevents thundering herd problems on coordinator reconnection via randomized full-jitter exponential backoff.
- **Appropriate Verification Methodology:** Mathematical delay formula verification, jitter range verification, attempt counter reset tests, and max retries exception escalation tests.

---

### Component 19: `load_config` (Daemon Config Loader & Vault Secret Resolver)
- **Component:** `app.infrastructure.client_daemon.config_loader.load_config`
- **Purpose:** Reads bank daemon YAML configuration files from disk and resolves any secret fields (`*_secret`) via environment variables or HashiCorp Vault.
- **Architectural Role:** Configuration Resolution & Secret Injection Engine for Client Daemon.
- **Interface Contract:**
  - `load_config(bank_id: str, config_path: str | None = None) -> dict[str, Any]`
- **Expected Invariant:** Any configuration key ending with `_secret` MUST be resolved via environment variable `CFI_{KEY.UPPER()}` or HashiCorp Vault `VaultClient.get_secret()`.
- **Possible Implementation Risks:** Hardcoded placeholder fallback string (`"resolved_secret_val"`) returned when Vault is unreachable and environment variable is not set; YAML parsing exceptions falling back to defaults silently.
- **Edge Cases:** Missing YAML file on disk defaulting to standard config dict; malformed YAML structure; secret key containing uppercase letters.
- **Engineering Claim Being Made:** Seamless secret resolution decoupling sensitive credentials from daemon configuration files via Vault transit integration.
- **Appropriate Verification Methodology:** Vault integration mock tests, environment variable override tests, YAML config loading tests, and fallback resolution checks.

---

### Component 20: `BankClientDaemon` (`cfi-bank-client` Standalone Execution Engine)
- **Component:** `app.infrastructure.client_daemon.daemon.BankClientDaemon`
- **Purpose:** Standalone containerized client daemon (`cfi-bank-client`) running inside participating bank private subnets, managing outbound-only gRPC mTLS sessions, hardware acceleration detection, local training rounds, and encrypted vault checkpoints.
- **Architectural Role:** Participant Node Execution Engine & Outbound Daemon Architecture.
- **Interface Contract:**
  - `initialize() -> None`
  - `start() -> None`
  - `stop() -> None` / `graceful_shutdown(timeout_seconds: float) -> None`
  - `execute_local_training_round(round_id: int, model_params: dict) -> dict[str, Any]`
  - `_connect_and_stream() -> dict[str, Any]`
- **Expected Invariant:** Daemon MUST write process ID to `storage/daemon.pid` on initialization and remove it cleanly on shutdown; session tokens MUST be persisted to encrypted `LocalVault`; outbound gRPC sessions MUST operate with zero inbound open firewall ports.
- **Possible Implementation Risks:** Unhandled SIGTERM signals leaving stale PID files on disk; simulated gRPC streaming session (`await asyncio.sleep(0.05)`) failing to establish real HTTP/2 gRPC channels when deployed.
- **Edge Cases:** Graceful shutdown invoked while local training round is in progress; double initialization calls; vault passphrase missing.
- **Engineering Claim Being Made:** Provides zero-inbound-port security architecture for bank nodes with automated backoff reconnection, hardware acceleration detection, and local checkpoint encryption.
- **Appropriate Verification Methodology:** Daemon lifecycle start/stop integration tests, PID file creation/cleanup verification, vault session token persistence tests, and SIGTERM/SIGINT signal handling tests.

---

## 3. Inventory Summary Table

| ID | Component Name | Architectural Role | Transport / Protocol | Key Invariant / Guarantee |
|:---:|:---|:---|:---|:---|
| **C-01** | `BankConnectorInterface` | Primary Port Interface | Abstract | Decouples platform from bank integrations |
| **C-02** | `BaseBankConnector` | Abstract Base Class | Abstract Stream/Batch | Enforces stream & batch parsing interface |
| **C-03** | `NormalizedTransaction` | Canonical Data Model | Schema (Pydantic) | Enforces positive amount & ISO normalization |
| **C-04** | `BankConnectorFactory` | Dynamic Resolver Factory | Factory Pattern | Enforces Zero-Mock policy in production |
| **C-05** | `ISO20022MessagingConnector` | Protocol Adapter | ISO 20022 MX / SWIFT MT | Validates XSD schema & logs SIEM errors |
| **C-06** | `OpenBankingConnector` | Open Banking REST Client | NextGenPSD2 / OAuth2 | Auto-refreshes OAuth2 TTL & handles 429 backoff |
| **C-07** | `RESTBankConnector` | HTTP Client & Webhook | REST / mTLS / HMAC | Signs payloads via HMAC-SHA256 & mTLS |
| **C-08** | `KafkaBankConnector` | Event-Driven Adapter | Apache Kafka SASL_SSL | Publishes round commands to Kafka topics |
| **C-09** | `RabbitMQBankConnector` | AMQP Queue Adapter | AMQP 0-9-1 / TLS | Exclusive correlation callback reply queues |
| **C-10** | `RedisBankConnector` | Pub/Sub Event Adapter | Redis Pub/Sub | Polls response channels with correlation matching |
| **C-11** | `BatchEODFileConnector` | Bulk File Ingestion | EOD CSV / Parquet | FIFO queue parsing with column alias mapping |
| **C-12** | `ParquetConnector` | High-Throughput Columnar | PyArrow Parquet | Zero-copy batch iterator processing |
| **C-13** | `StreamingPaymentConnector` | In-Memory Stream Buffer | Event Stream | Sub-millisecond raw payload push & stream yield |
| **C-14** | `FixtureConnector` | Test Double / Loader | Multi-format Fixtures | Raises `ImportError` in production environment |
| **C-15** | `MQSkeletonBankConnector` | Deprecated Stub | AMQP Mock | Formally blocked by factory policy |
| **C-16** | `retry_connector` | Resiliency Decorator | Exponential Backoff | Retries transient I/O with exponential backoff |
| **C-17** | `BankOnboardingService` | Application Service | YAML Config / mTLS | Validates `bank_id` & renders daemon YAML |
| **C-18** | `ExponentialBackoffReconnector` | Transport Resiliency | Jittered Backoff | Full-jitter backoff preventing thundering herds |
| **C-19** | `load_config` | Config & Secret Engine | YAML / HashiCorp Vault | Resolves `*_secret` fields via Vault/Env |
| **C-20** | `BankClientDaemon` | Standalone Daemon Engine | Outbound gRPC mTLS | Zero-inbound-port security & PID file management |

---

*Scientific Verification Inventory — Connector Framework*  
*Privacy-Preserving Cross-Bank Fraud Detection using Federated Learning*  
*Version 1.0 — 2026-08-01*
