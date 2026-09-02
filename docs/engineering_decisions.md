# Engineering Decisions

> ADR-style decision log. Each entry documents what was decided, why, and what was traded off.

---

## ED-001: Custom FL Engine vs Flower (flwr)

**Date**: 2026-06-29
**Status**: Accepted

### Context

Flower (`flwr`) is the standard open-source framework for federated learning, providing gRPC-based client-server communication, strategy abstractions, and multi-machine deployment.

### Decision

Build a custom in-process FL engine instead of using Flower.

### Rationale

1. **Failure injection control**: We need deterministic dropout, reconnection, and latency simulation per round. Flower's client lifecycle is managed by the framework, making fine-grained failure injection harder.
2. **UI observability**: The simulator's value proposition is round-by-round progress visible in the dashboard. Our custom engine emits progress callbacks at every step.
3. **Single-process simulation**: All three "banks" run in the same process. Flower's architecture assumes separate client processes communicating over gRPC, which adds complexity without benefit for a simulator.
4. **Educational clarity**: The custom engine code is self-documenting — readers can follow the FedAvg algorithm step by step.

### Tradeoff

This engine **cannot** scale to distributed multi-machine deployment. In production, use Flower with gRPC for real cross-network federated learning. The README explicitly documents this distinction.

---

## ED-002: Synthetic Data vs Real Datasets

**Date**: 2026-06-29
**Status**: Accepted

### Context

Standard fraud datasets (IEEE-CIS, Kaggle Credit Card) exist but are single-institution and cannot demonstrate Non-IID effects.

### Decision

Generate synthetic Non-IID transaction data with three distinct bank profiles.

### Rationale

1. **Non-IID control**: We can precisely control fraud ratios, transaction patterns, and feature distributions per bank — the core of what makes FL interesting.
2. **Reproducibility**: Deterministic generation with fixed seeds means identical results across runs.
3. **No licensing**: No data distribution restrictions.
4. **Narrative**: Each bank has a named identity (Meridian National, Nexus Digital, Heritage Regional) with distinct fraud patterns that tell a story.

### Tradeoff

Synthetic data lacks the complexity of real financial transactions. Feature engineering is simplified. In production, the same FL pipeline would work with real data.

---

## ED-003: SQLAlchemy 2.0 Async + JSON Columns

**Date**: 2026-06-29
**Status**: Accepted

### Context

Simulation results include nested structures (per-bank metrics, per-round data, confusion matrices, ROC curves) that don't map cleanly to normalized relational tables.

### Decision

Use PostgreSQL JSON columns for denormalized storage of metrics, bank data, and round data.

### Rationale

1. **Schema flexibility**: Metrics structure evolves as we add new evaluation methods.
2. **Read optimization**: A single query returns the full simulation with all nested data.
3. **Development velocity**: No migration needed when adding a new metric field.
4. **Appropriate scale**: The simulator handles hundreds of simulations, not millions.

### Tradeoff

JSON columns lose referential integrity, are harder to query/index for analytics, and can't enforce schema constraints. At production scale, normalize metrics into separate tables with proper indexing.

---

## ED-004: Celery for Training Execution

**Date**: 2026-06-29
**Status**: Accepted

### Context

A federated training simulation with 10 rounds, 3 banks, 50K transactions per bank takes 1-5 minutes. This cannot block the FastAPI event loop.

### Decision

Dispatch simulations as Celery tasks with Redis as broker and result backend.

### Rationale

1. **Non-blocking API**: FastAPI returns 202 Accepted immediately.
2. **Progress tracking**: The Celery task pushes progress to Redis pub/sub.
3. **Retry/monitoring**: Celery provides built-in task tracking and Flower (the monitoring tool, not FL framework) for observability.
4. **Process isolation**: PyTorch training runs in a separate worker process.

### Tradeoff

Adds operational complexity (Redis + Celery worker processes). For a simpler deployment, could use FastAPI BackgroundTasks, but that runs in the same process and can't survive API restarts.

---

## ED-005: React Query Over Redux/Zustand for Server State

**Date**: 2026-06-29
**Status**: Accepted

### Context

The frontend primarily displays server-side data (simulation results, bank configs, training rounds).

### Decision

Use TanStack React Query for all server state. Zustand is a dependency but reserved for future client-only UI state.

### Rationale

1. **Auto-refetch**: Running simulations update automatically via polling intervals.
2. **Cache management**: Completed simulations are cached and don't re-fetch unnecessarily.
3. **Loading/error states**: Built-in, no boilerplate.
4. **Conditional polling**: Simulations auto-poll while running, stop when completed.

### Tradeoff

WebSocket integration requires manual event handling outside React Query. For the current scope, polling + WebSocket (when connected) provides sufficient real-time experience.

---

## ED-006: Simulated Privacy vs Cryptographic Implementations

**Date**: 2026-06-29
**Status**: Accepted

### Context

Real secure aggregation requires multi-party computation (MPC) protocols. Real differential privacy requires rigorous privacy accounting (Rényi DP).

### Decision

Implement conceptually correct but simplified versions:
- **Secure aggregation**: Pairwise masks that mathematically cancel during summation
- **Differential privacy**: Gaussian mechanism with basic sequential composition

### Rationale

1. **Mathematical correctness**: The masks do cancel. The noise calibration formula is correct.
2. **Educational value**: Readers can understand the principle without MPC library complexity.
3. **Verifiable**: Unit tests prove that masked aggregation produces identical results to plaintext.

### Tradeoff

Not production-grade. The threat model documents the gap between simulator and production security requirements. Production should use `opacus` for DP and PySyft/TF Encrypted for MPC.

---

## ED-007: Tailwind CSS v4 Over Vanilla CSS

**Date**: 2026-06-29
**Status**: Accepted

### Context

The frontend needs a dark-mode, glassmorphism-heavy design with custom theme tokens.

### Decision

Use Tailwind CSS v4 with `@theme` directive for custom design tokens, plus a small amount of custom CSS for glass effects and animations.

### Rationale

1. **Tailwind v4**: New CSS-first configuration (no `tailwind.config.js`), native `@theme` tokens.
2. **Utility-first**: Rapid iteration on component styling without context-switching to CSS files.
3. **Custom tokens**: `@theme` directive lets us define our own color system while keeping utility classes.

### Tradeoff

Larger initial learning curve for Tailwind v4 vs v3. The `@theme` API is relatively new. Custom CSS is still needed for glassmorphism effects.

---

## ED-008: Microservices Decomposition & API Gateway

**Date**: 2026-07-04
**Status**: Accepted

### Context

To deploy the framework as a distributed system, we need autonomous services representing independent components: control control gateway, federated aggregator, entity-graph manager, and risk processing engine.

### Decision

Decompose the monolithic backend into 4 microservices (`gateway`, `fl-coordinator`, `identity-graph`, and `fraud-alert`) orchestrated via Docker Compose and routed through a central API gateway.

### Rationale

1. **Scalability & Isolation**: Independent services prevent failure propagation (e.g., heavy PyTorch training in `fl-coordinator` doesn't block transaction screening in `fraud-alert`).
2. **Central Routing**: The gateway handles authorization, rate-limiting, and request logging uniformly.
3. **Graceful Fallback**: Dynamic path loading in `main.py` allows the codebase to run either as a monolith or as microservices.

### Tradeoff

Decomposition increases operation overhead (4 independent FastAPI processes, routing tables, and service network configs) and introduces latency overhead over internal function calls.

---

## ED-009: SHAP explainability with analytical fallback

**Date**: 2026-07-06
**Status**: Accepted

### Context

Generating explanations for composite risk scores requires attributions for PyTorch MLP predictions. Real SHAP computation is CPU-heavy and package-dependent.

### Decision

Implement model explainability using `shap.KernelExplainer` with a pre-selected baseline of normal transactions, and compile a fallback analytical heuristic if execution fails.

### Rationale

1. **Mathematical Rigor**: SHAP values provide Shapley-based game-theoretic attributions.
2. **Robustness**: If package loading fails or inference times out, the system degrades gracefully to the analytical fallback without throwing API errors.

### Tradeoff

`KernelExplainer` is slow (requires multiple model evaluations per transaction). The analytical fallback is not game-theoretically optimal, but preserves system liveness.

---

## ED-010: Drift detection metrics

**Date**: 2026-07-06
**Status**: Accepted

### Context

We need to track data drift (feature shift) and concept drift (relationship shifts $P(Y \mid X)$) across independent banks.

### Decision

Implement binned frequency JS Divergence (bounded $[0, 1]$), dynamic percentile Population Stability Index (PSI), Kolmogorov-Smirnov (KS) tests, and model-based concept drift.

### Rationale

1. **Feature Shift**: PSI bucketed dynamically by the reference distribution's quantiles isolates shifts accurately.
2. **Concept Shift**: Training a simple classifier on Reference Bank A and evaluating it on Bank B maps changes in predictions ($P(Y \mid X)$) effectively.

### Tradeoff

Percentile-based binning ignores actual distribution values that fall completely outside the bin boundaries. Epsilon padding is needed to avoid zero division.

---

## ED-011: Model registry and versioning with Canary Promotion Gate

**Date**: 2026-07-08
**Status**: Accepted

### Context

Aggregated models can degrade due to data drift, malicious clients (Byzantine poisoning), or convergence failure. We need to prevent poor models from going live.

### Decision

Build a versioned `ModelRegistry` with file-backed storage, and implement an automated **Canary Promotion Gate** checking if candidate models degrade performance.

### Rationale

1. **Canary Gate**: A newly aggregated candidate is promoted to active (`global_model.pt`) *only* if its validation AUC-ROC matches or exceeds the current active model's AUC-ROC minus a tolerance (`0.005`).
2. **Rollback Ability**: Historical model states can be restored atomically via `/rollback/{version}`, updating symlinks instantly.

### Tradeoff

Requires storing previous state binaries on disk and running model evaluations at the end of each round, slightly increasing round duration.

---

## ED-012: Property-Based Verification (Hypothesis) and Scientific Benchmarking

**Date**: 2026-07-10
**Status**: Accepted

### Context

Validating mathematical correctness and resilience requires verifying general code invariants and testing models empirically on public datasets.

### Decision

Implement property-based tests using `hypothesis` to check mathematical invariants, and build a standalone `benchmark.py` running on the European cardholders dataset.

### Rationale

1. **Invariant Testing**: Hypothesis generates edge cases to falsify code assumptions (such as secure aggregation mask cancellation under float errors).
2. **Empirical Validation**: Running the model on real data validates Krum and Median robustness under poisoning.

### Tradeoff

Property-based testing is slower than simple unit tests. The real dataset benchmark is heavy and requires downloading a ~150MB CSV.

---

## ED-013: Smart Contract Web3 / CBDC Incentive Settlement vs In-Memory Ledger

**Date**: 2026-07-21
**Status**: Accepted

### Context

Cross-bank federated collaboration requires economic incentives (Shapley payout distribution) to prevent free-riding. In-memory virtual ledgers lack financial trust, auditability, and decentralized enforcement.

### Decision

Implement on-chain automated payouts using an EVM Solidity smart contract (`ConsortiumIncentiveSettlement.sol`) integrated via Web3 (`smart_contract_driver.py`).

### Rationale

1. **Decentralized Trust**: Payouts are executed on-chain via smart contracts using 18-decimal wei fixed math, removing central coordinator manipulation.
2. **Automated Quarantine**: Nodes with negative contribution ($SV_i \le -0.05$) or zero variance update attacks are quarantined on-chain (`setNodeQuarantine()`), freezing their wallet claims.
3. **Immutable Audit Binding**: Settlement transaction hashes (`settlement_tx_hash`) and block numbers are recorded immutably in the SHA-256 audit ledger.

### Tradeoff

Introduces EVM runtime dependency (`web3`, `py-solc-x`) and gas costs/latency during automated transaction execution. Fallback drivers are provided when an EVM node is offline.

---

## ED-014: Zero-Inbound Port Egress Bank Client Daemon Architecture

**Date**: 2026-07-21
**Status**: Accepted

### Context

Enterprise bank firewalls strictly forbid opening inbound listening ports to external traffic (such as the central FL coordinator).

### Decision

Deploy a standalone client daemon (`cfi-bank-client`) inside bank enclaves that communicates strictly via outbound-only mTLS connections to the central coordinator.

### Rationale

1. **Zero-Inbound Port Compliance**: Satisfies strict financial security policies by preventing incoming connections through perimeter firewalls.
2. **Local Vault Protection**: Local PyTorch model artifacts, key material, and session state are encrypted on disk with AES-256-GCM and PBKDF2 (100,000 iterations).
3. **Resilient Reconnection**: `ExponentialBackoffReconnector` manages network dropouts using exponential backoff with full jitter.

### Tradeoff

Long-lived streaming outbound channels require heartbeats and session token management to handle transient network drops.

---

## ED-015: Advanced FL Aggregation Strategies (FedProx & SCAFFOLD vs Naive FedAvg)

**Date**: 2026-08-14  
**Status**: Accepted

### Context

Standard Federated Averaging (FedAvg; McMahan et al., 2017) assumes that local client data distributions are Independent and Identically Distributed (IID). In cross-bank deployments, institutional customer bases exhibit extreme statistical heterogeneity (Dirichlet label and feature skew with $\alpha \le 0.50$):
* Bank A specializes in ultra-high-net-worth commercial cross-border wires ($FN$ costs are catastrophic).
* Bank B processes high-frequency retail POS / micro-lending transactions.
* Bank C handles cross-border remittance corridor flows.

Under these conditions, standard FedAvg suffers from severe **Client Drift**: local stochastic gradient descent trajectories pull model parameters toward conflicting local empirical risk minimizers, causing the global model to oscillate, diverge, or experience catastrophic forgetting on minority fraud patterns.

### Decision

Implement a configurable Strategy Factory supporting **FedProx**, **SCAFFOLD**, **FedNova**, and **FedOpt** alongside baseline FedAvg:
1. **FedProx (Li et al., 2020)**: Introduces a proximal regularizer $\frac{\mu}{2} \|\mathbf{w} - \mathbf{w}^t\|^2$ directly into the local client objective function. This bounds the distance between local updates and the global checkpoint, dampening client drift and providing convergence guarantees under Non-IID Dirichlet partitioning ($\mu = 0.01$).
2. **SCAFFOLD (Karimireddy et al., 2020)**: Maintains server-side and client-side control variates ($c, c_i$) that estimate the client drift direction, applying variance reduction corrections to client gradient updates.
3. **FedNova (Wang et al., 2020)**: Normalizes client update magnitudes based on the number of local gradient steps taken, eliminating aggregation bias caused by stragglers or unequal dataset sizes.

### Tradeoff

* FedProx introduces hyperparameter tuning for the proximal weight $\mu$.
* SCAFFOLD doubles communication payload sizes because control variate vectors $c_i$ must be synchronized alongside model weight deltas $\Delta \mathbf{w}_i$.

---

## ED-016: Byzantine-Robust Aggregators (Multi-Krum & Trimmed Mean vs Adversarial Poisoning)

**Date**: 2026-08-14  
**Status**: Accepted

### Context

In a multi-tenant banking consortium, the central coordinator cannot assume all participating nodes are benign. A single compromised bank credential or rogue insider can execute:
1. **Gradient Sign-Flipping & Scaling Attacks**: Sending updates $-\gamma \nabla \mathcal{L}$ with massive $L_2$ norm to stall or reverse global convergence.
2. **Targeted Backdoor Injections**: Embedding stealth triggers into model weights (e.g. ignoring transactions with specific memo strings) while maintaining high utility on standard test benchmarks.

Standard linear weighted averaging ($\sum \frac{n_k}{n} \mathbf{w}_k$) provides zero resistance: a single adversarial node can shift the global weight vector arbitrarily far from the true consensus manifold.

### Decision

Implement Byzantine-tolerant aggregation defenses with theoretical breakdown guarantees:
1. **Multi-Krum (Blanchard et al., 2017)**: Computes pairwise Euclidean distances $d(\mathbf{w}_i, \mathbf{w}_j)$ across all client gradient submissions and selects the top $m$ candidate models that minimize the cumulative distance to their $n - f - 2$ closest neighbors (where $f < n/2$ is the tolerated number of Byzantine nodes).
2. **Coordinate-wise Trimmed Mean & Median (Yin et al., 2018)**: Sorts coordinate values across all received client vectors and discards the top and bottom $\beta$ fraction (e.g. $\beta = 0.20$) before computing the arithmetic mean per parameter, neutralizing extreme magnitude manipulation.

### Tradeoff

* Multi-Krum incurs an $\mathcal{O}(n^2 \cdot d)$ computational cost for pairwise distance calculation over $d$ parameters. For models with $> 1\text{M}$ weights, distance matrix computation is parallelized across worker threads.

---

## ED-017: Differential Privacy Budget Accounting (Why Rényi DP with $\varepsilon=1.0, \delta=10^{-5}$)

**Date**: 2026-08-14  
**Status**: Accepted

### Context

Applying Differential Privacy (DP) requires answering two critical engineering questions:
1. *How is $\varepsilon$ mathematically calibrated?* ($\varepsilon > 10$ provides negligible protection against Membership Inference Attacks; $\varepsilon < 0.1$ destroys gradient signal utility, degrading fraud recall below $30\%$).
2. *How is cumulative privacy budget tracked across multi-round federated training?*

Naive linear composition over $T$ federated rounds yields cumulative privacy loss $\varepsilon_{\text{total}} = \sum_{t=1}^T \varepsilon_t$. For $T = 50$ rounds with local $\varepsilon_t = 0.5$, linear composition reports an astronomical $\varepsilon_{\text{total}} = 25.0$, incorrectly forcing the training engine to halt due to budget exhaustion.

### Decision

1. **Parameter Calibration ($\varepsilon = 1.0, \delta = 10^{-5}$)**: We set $\delta < 1/N$ ($N \approx 100,000$ transactions per bank) to ensure negligible probability of catastrophic privacy failure. The target $\varepsilon = 1.0$ represents the empirical Gold Standard in financial machine learning, providing provable resistance against Membership Inference (MIA accuracy bounded $\le 52.4\% \approx$ random guess) while maintaining high fraud recall ($> 62.4\%$).
2. **Rényi Differential Privacy (RDP) & Moments Accountant**: The privacy engine leverages Opacus RDP accounting:
   $$\varepsilon(\alpha) = \frac{\alpha}{2 \sigma^2}, \quad \varepsilon_{\text{total}} = \min_{\alpha} \left( \sum_{t=1}^T \varepsilon_t(\alpha) + \frac{\ln(1/\delta)}{\alpha - 1} \right) \sim \mathcal{O}(\sigma^{-1} \sqrt{T \ln(1/\delta)})$$
   This provides tight sub-linear $\mathcal{O}(\sqrt{T})$ composition bounds, allowing up to $100+$ federated rounds under strict $\varepsilon \le 1.0$ limits.

### Tradeoff

* Requires calculating analytical RDP conversion orders $\alpha \in [1.5, 64.0]$ and dynamic clipping thresholds $C$, introducing slight accounting overhead during post-round synchronization.

---

## ED-018: Topological Graph Neural Networks (GNN) vs Pure Tabular Classifiers

**Date**: 2026-08-14  
**Status**: Accepted

### Context

Traditional tabular models (e.g. standalone XGBoost or LightGBM) evaluate payment transactions in total isolation ($T_x = [\text{amount}, \text{velocity}, \text{merchant\_mcc}, \dots]$). They are fundamentally blind to multi-hop financial smurfing rings, cyclic round-tripping, and synthetic identity networks spanning across multiple banking institutions.

### Decision

Implement a hybrid two-stage inference ensemble:
1. **Stage 1 (GraphSAGE / GAT Topo-Embedding)**: A 2-layer Graph Attention Network aggregates relational context over 2-hop transaction neighborhoods, generating 512-dimensional topological node embeddings with zero raw PII leakage.
2. **Stage 2 (Calibrated Gradient Boosting Ensemble)**: Blends tabular transaction features with topological graph embeddings and historical velocity signals through Platt Calibration to produce true posterior probability estimates $P(\text{Fraud}) \in [0.0, 1.0]$.

### Tradeoff

* Graph construction requires maintaining an in-memory or graph database (Neo4j / NetworkX) edge index. Real-time inference latency is kept strictly below $15\text{ms}$ by bounding subgraph sampling to $k=2$ hops and caching node embeddings in Redis.

---

## ED-019: Enterprise Authentication & Brute-Force Lockout Defense

**Date**: 2026-09-01  
**Status**: Accepted

### Context

Financial API gateways are prime targets for credential stuffing, password spraying, and dictionary attacks. Permissive authentication models (long-lived stateless JWTs without refresh rotation or lockout mechanics) expose institutions to credential replay and brute-force account compromises.

### Decision

Implement a multi-tier authentication defense suite (`auth_service.py`, `password_hasher.py`):
1. **Adaptive Bcrypt Password Hashing**: Enforce `bcrypt` with work factor `cost=12` (4,096 rounds) and per-password cryptographic salts. Disallow plaintext, MD5, or unsalted SHA digests.
2. **Short-Lived Access Tokens (15 Minutes)**: Issue JWT access tokens with an enforced 900-second lifespan signed via HMAC-SHA256 (RFC 7518).
3. **Single-Use Refresh Token Rotation**: Refresh tokens (7-day validity) are single-use. Exchanging a refresh token via `POST /api/v1/auth/refresh` immediately invalidates the previous refresh token and issues a new pair, detecting and mitigating replay attacks.
4. **Brute-Force Account & IP Lockout**: Track consecutive authentication failures across username and client IP sliding windows. After **5 failed attempts**, the user and IP are temporarily locked out for **15 minutes (900 seconds)**, returning HTTP `429 Too Many Requests` with `Retry-After: 900`.

### Tradeoff

* Stateful tracking of failed attempts and refresh token revocation requires memory or Redis lookup on authentication endpoints.
* Bcrypt work factor `cost=12` introduces ~80-120ms CPU computation per login attempt, which naturally rate-limits brute force attempts while remaining imperceptible to interactive users.

---

## ED-020: Production Error Sanitization & Sentry Tracing (RFC 7807)

**Date**: 2026-09-01  
**Status**: Accepted

### Context

Default unhandled exception handlers in modern web frameworks often return stack traces, internal filesystem paths (`C:\Users\...`, `/var/app/...`), database schema column names, or raw SQL queries to API clients. In banking environments, this information disclosure aids attackers in mapping backend internals.

### Decision

Implement global production error sanitization middleware (`error_handler.py`):
1. **Zero Information Leakage in Production**: When `app_env="production"` or `app_debug=False`, all unhandled 500 exceptions are stripped of traceback details and replaced with a uniform generic error message (`"Something went wrong. An unexpected internal error occurred."`).
2. **RFC 7807 Problem Details**: Responses adhere to standard Problem Details for HTTP APIs (`type`, `title`, `status`, `detail`, `instance`, `incident_id`).
3. **Trace Correlation & Sentry Integration**: Each exception generates a unique incident ID (`inc_<timestamp>_<uuid>`) returned in both response body and `X-Incident-ID` header. Full diagnostics and tracebacks are logged strictly server-side and dispatched to Sentry with attached incident context.

### Tradeoff

* Debugging in production requires cross-referencing the client's `incident_id` in server logs or Sentry rather than reading error bodies directly from HTTP responses.

---

## ED-021: Strict Perimeter Security (Zero Wildcard CORS & Header Hardening)

**Date**: 2026-09-01  
**Status**: Accepted

### Context

Allowing wildcard CORS (`allow_origins=["*"]`) in banking APIs enables cross-origin browser requests from arbitrary third-party web pages, violating financial isolation boundaries. Similarly, missing HTTP security headers leaves the web console vulnerable to clickjacking, MIME-sniffing, and protocol downgrade attacks.

### Decision

1. **Zero Wildcard CORS**: Restrict `allow_origins` to explicitly enumerated production domains (`https://cf-intelligence.vercel.app`, `https://cfi-platform.vercel.app`), local development ports, and authenticated Vercel preview deployment regex patterns. Wildcard `*` is strictly rejected at application startup.
2. **HTTP Security Headers Injection (`security_headers.py`)**: Outbound HTTP responses automatically enforce:
   * `Strict-Transport-Security: max-age=31536000; includeSubDomains`
   * `X-Content-Type-Options: nosniff`
   * `X-Frame-Options: DENY`
   * `Content-Security-Policy: default-src 'self' ...`
   * `Referrer-Policy: strict-origin-when-cross-origin`

### Tradeoff

* Cross-origin requests from newly added staging domains require explicit whitelist configuration in environment variables (`CORS_ALLOWED_ORIGINS`).

---

## ED-022: Sub-100ms Inference SLA Verification via Concurrent Load Harness

**Date**: 2026-09-02  
**Status**: Accepted

### Context

High-throughput payment switches mandate sub-100ms p99 inference SLAs. Relying solely on isolated single-request unit test assertions fails to capture real-world concurrency bottlenecks, thread pool contention, database migration lock contention, and garbage collection pauses.

### Decision

1. **In-Memory Model Caching & Synchronous Thread Execution**: Cache PyTorch model instances in memory (`predict.py`), perform CPU forward passes synchronously without spawning unnecessary threadpool hops, and offload non-critical telemetry/alert persistence to FastAPI `BackgroundTasks`.
2. **Database Single-Flight Lock**: Introduce a single-flight mutex lock during tenant SQLite database initialization (`backend/app/infrastructure/database/__init__.py`), preventing concurrent migration lock collisions under multi-tenant load.
3. **Automated Concurrent Load Testing (`scripts/run_load_test.py`, `scripts/locustfile.py`)**: Codify a 1,000-request multi-tenant concurrent load testing runner measuring p50, p90, p95, and p99 percentiles under sustained payment streams.

### Tradeoff

* In-memory model caching requires explicit invalidation hooks (`_cached_serving_model = None`) whenever a new model checkpoint is promoted by the canary gate.
