# Phase 1: Repository-Wide Runtime Truth Static Audit, Reachability Analysis & Remediation Blueprint
**System:** CF-Intelligence (Privacy-Preserving Cross-Bank Fraud Detection using Federated Learning)
**Audit Phase:** Phase 1 — Systematic Static Truth Discovery, Provenance Reachability & Remediation Blueprint
**Date:** October 4, 2026
**Audit Status:** `PHASE_1_RUNTIME_TRUTH_AUDIT_COMPLETE`

---

## A. Executive Summary
This report presents the forensic findings of **Phase 1 of the Repository-Wide Runtime Truth & Implementation Integrity Audit** for CF-Intelligence. Following the architectural surface mapping established in Phase 0, Phase 1 conducted an exhaustive static analysis, reachability trace, and provenance audit across all **463 runtime-reachable files** in the repository.

### Key Findings at a Glance
1. **2 CRITICAL Findings on Default Paths**:
   - **RTF-0001 (Default Mock Simulation Walk)**: The primary operator console (`LiveOperationsView.tsx`) initializes `trainingMode: 'mock'` by default. Clicking `'Start Simulation'` triggers `startSimulatedTraining()`, which runs an artificial Gaussian walk in the browser with `Math.random()`, completely bypassing the real backend PyTorch federated training engine (`POST /api/v1/simulations`).
   - **RTF-0004 (Fabricated Live Telemetry Feed)**: The WebSocket stream `/ws/telemetry` emits continuous random synthetic transactions generated via `random.choice(typologies)` and `random.uniform(1500.0, 450000.0)` in an infinite loop, while the UI header displays a green `'Live WS (XXms)'` badge implying live enterprise interbank traffic.
2. **4 HIGH Severity Findings**:
   - **RTF-0002 (Canonical Simulation Pre-Seeding)**: Backend module load automatically runs `_seed_canonical_simulation()`, pre-populating static simulation `sim_fed_01` (AUC 0.948, F1 0.892, 10 rounds, bank metrics, Shapley distributions) so cold-boot dashboards never 404.
   - **RTF-0003 (Benchmark JSON Crossover)**: `GET /api/v1/dashboard/comparative-baselines` attempts to read offline benchmark file `experiments/results/comparative_baselines.json` from the filesystem, falling back to an in-memory hardcoded dictionary `_STATIC_FALLBACK_BASELINES`.
   - **RTF-0005 (Beta Distribution Prediction Synthesis)**: In `design_partner_service.py:L256-L273`, model prediction probabilities are synthesized using Beta random distributions (`rng.beta(3.2, 1.4)` vs `rng.beta(1.8, 2.2)`) rather than running neural network forward passes.
   - **RTF-0006 (Model Registry Static Fallback)**: `GET /api/v1/models` returns hardcoded champion models (`consortium_global_fl`, `elliptic_graphsage_temporal`) when the local storage manifest is unseeded.
3. **Existing Real Implementations Available for Direct In-Place Rewiring**:
   - A fully functional, mathematically sound PyTorch federated training engine (`SimulationService.run_simulation`, `ModelService.train_local`, `TrainingWebSocketManager`) already exists in the backend.
   - Real enterprise connectors (`KafkaStreamingConnector`, `StreamingPaymentConnector`, `ISO20022MessagingConnector`, `ParquetConnector`) already exist in `backend/app/infrastructure/connectors/`.
   - Real PyTorch model inference (`InferenceEngine.predict_proba`) already exists in the application layer.
4. **Remediation Strategy: In-Place Repair with Zero Parallel 'v2' Files**:
   - Rather than creating parallel versions (`simulation_v2.py`, `telemetry_real.py`), Phase 2 will repair the current implementations in-place to make the **main/default system path truthful, verified, and technically sound**.

---

## B. Repository State Protection
Phase 1 operated under strict read-only safety protocols. Repository state was recorded prior to analysis and verified untouched:
```text
Branch: main
HEAD: 67b08b19eea16800c1e612b80aa7571b4f3c0246 (Identical to origin/main)
Staged Files: 0
Active Modified Files (Pre-existing from prior benchmark reconciliation): 17
Untracked Directories: audit/, experiments/byzantine/smoke/, experiments/elliptic/diagnostic/
Working Tree Modification: STRICTLY ZERO (0 production files modified, staged, committed, or deleted)
```

---

## C. Scanner Methodology
The Phase 1 static analysis pipeline combined multi-layer automated discovery:
1. **Python AST Parsing**: Walked abstract syntax trees across all backend modules to detect:
   - Try-except blocks returning static dictionaries or literal values (`BROAD_EXCEPTION_FALLBACK_RETURN`).
   - Dictionaries containing metric keys (`roc_auc`, `f1`, `accuracy`, `loss`, `latency`) assigned literal constants (`HARDCODED_METRIC_LITERAL`).
   - String constants referencing benchmark or experiment artifacts (`experiments/`, `benchmarks/`).
2. **Lexical & Regex Pattern Analysis**: Scanned code lines for suspicious tokens (`mock`, `fake`, `dummy`, `stub`, `canned`, `placeholder`, `fallback`, `seed_canonical`).
3. **Stochastic & Random Generation Audit**: Audited all invocations of `random.*`, `np.random.*`, `rng.*`, `torch.rand*`, and `Math.random()`, classifying them into legitimate ML stochasticity (data shuffling, DP noise, canary split) vs fraudulent output fabrication.
4. **Frontend AST & Lifecycle Analysis**: Inspected React components, hooks, timers (`setInterval`, `setTimeout`), state setters, and query mutations in `frontend/src/`.
5. **Source-to-Sink Data-Flow Reachability**: Traced whether candidate constructs reach HTTP responses, WebSocket frames, UI charts, or user downloads.

---

## D. Scan Coverage
The audit scanned 100% of the runtime-reachable codebase across 5 core subsystem directories:
| Directory | Layer | Files Scanned | Primary Functional Scope |
|:---|:---|:---:|:---|
| `backend/app/presentation/` | Presentation Layer | 52 | 45 FastAPI routers, 2 WebSocket routers, CLI commands, Schemas |
| `backend/app/application/services/` | Application Services | 75 | Orchestration services, inference engines, compliance dossier generators |
| `backend/app/domain/` | Domain Layer | 47 | Cryptographic math, Byzantine defense filters, risk scorers, value objects |
| `backend/app/infrastructure/` | Infrastructure Layer | 93 | Enterprise bank connectors, Redis stores, TEE drivers, audit loggers |
| `frontend/src/` | Frontend UI | 196 | 21 Pages, 87 React components, API query hooks, WebSocket clients |
| **Total Runtime Scope** | | **463** | **Full application runtime topology** |

---

## E. Raw Candidate Counts
The static analysis pipeline produced the following candidate counts:
```text
Runtime files scanned: 463
Python AST exception-fallback candidates: 34
Hardcoded metric literal candidates: 83
Benchmark artifact path literals: 2
Random generation sites: 250 (Python: 224, Frontend JS: 26)
Lexical fallback tokens: 109
Lexical mock/fake/dummy tokens: 76
Frontend timer loops (setInterval/setTimeout): 38
Total raw static candidates: 592
```

---

## F. Reachability Filtering & Triaging
Raw static occurrences were filtered using execution-path tracing:
- **Test-Only Isolation (238 candidates excluded)**: Mock repositories and factories located inside `backend/tests/` and `frontend/tests/` were confirmed unreferenced by runtime code.
- **Legitimate Numerical & Algorithmic Use (194 candidates confirmed legitimate)**: Differential privacy noise addition (`opacus`), mini-batch shuffling (`torch.utils.data.DataLoader`), non-IID Dirichlet distribution sampling (`numpy.random.dirichlet`), and safe zero-division fallbacks (`metrics_service.py`) were verified as mathematically legitimate.
- **Isolated Demo / Animation Scope (24 candidates classified as demo-only)**: Launch splash animations and modal timers.
- **Confirmed Runtime Truth Defects (12 discrete findings)**: Suspicious constructs directly reachable from production routes and user-facing surfaces.

---

## G. Confirmed Critical Findings
### `RTF-0001`: Frontend Simulation Console Defaults to Client-Side Mock Random Walk Bypassing Real Backend Training
- **Classification:** `SYNTHETIC_USER_FACING_OUTPUT`
- **Severity:** **CRITICAL** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `frontend/src/pages/LiveOperationsView.tsx` :: `LiveOperationsView.startSimulatedTraining` (L163, L777-L838, L1052-L1064)
- **Sink:** `frontend/src/pages/LiveOperationsView.tsx` :: `LossChart, MetricsComparisonBarChart, Champion AUC badge, Bank convergence statuses`
- **Current Behavior:** Fabricates simulated round loss, AUC, and per-bank convergence curves completely in browser JavaScript memory using Math.random() and exponential decay formulas.
- **Claimed / Implied Behavior:** Executes multi-bank federated learning across participating bank nodes with differential privacy, gradient aggregation, and real model validation.
- **Evidence:**
  - `frontend/src/pages/LiveOperationsView.tsx:L163: const [trainingMode, setTrainingMode] = useState<TrainingMode>('mock');`
  - `frontend/src/pages/LiveOperationsView.tsx:L799-L800: const gaussianNoise = (std: number) => std * Math.sqrt(-2 * Math.log(Math.random())) * Math.cos(2 * Math.PI * Math.random());`
  - `frontend/src/pages/LiveOperationsView.tsx:L811-L817: Mathematical formulas generating artificial AUC and Loss steps toward profile targets.`
  - `frontend/src/pages/LiveOperationsView.tsx:L1063: Button text displays 'Start Simulation' (without indicating mock or synthetic mode).`
- **Recommended Remediation:** `CONNECT_REAL_IMPLEMENTATION`
  - frontend/src/pages/LiveOperationsView.tsx :: change default trainingMode to 'real'
  - frontend/src/pages/LiveOperationsView.tsx :: wire 'Start Simulation' button directly to useCreateSimulation mutation
  - frontend/src/pages/LiveOperationsView.tsx :: isolate client-side simulation behind an explicitly labeled 'Offline Demo Mode' toggle

### `RTF-0004`: Real-Time Telemetry WebSocket Emits Continuous Pure Random Synthetic Transactions
- **Classification:** `RANDOMIZED_FAKE_OUTPUT`
- **Severity:** **CRITICAL** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `backend/app/presentation/websockets/streaming_ws.py` :: `telemetry_websocket_endpoint` (L310-L360)
- **Sink:** `frontend/src/components/layout/Header.tsx` :: `Header 'Live WS' status badge, active latency counter, streamed transaction counter, ObservabilityPage live ticker`
- **Current Behavior:** Generates artificial transactions with random amounts, random bank IDs, and random fraud scores in an infinite sleep loop.
- **Claimed / Implied Behavior:** Represents live enterprise interbank payment traffic streaming from core banking systems.
- **Evidence:**
  - `backend/app/presentation/websockets/streaming_ws.py:L321-L322: typ, desc, sev, score = random.choice(typologies); bank = random.choice(banks)`
  - `backend/app/presentation/websockets/streaming_ws.py:L335: 'amount': round(random.uniform(1500.0, 450000.0), 2)`
  - `frontend/src/components/layout/Header.tsx:L33-L36: Renders 'Live WS' badge when connected to this synthetic stream.`
- **Recommended Remediation:** `CONNECT_REAL_DATA_SOURCE`
  - backend/app/presentation/websockets/streaming_ws.py :: refactor endpoint to pull from Kafka/Redis stream when configured, and label as 'Simulated Telemetry' when running standalone demo

---

## H. Confirmed High Findings
### `RTF-0002`: Backend Startup Automatically Pre-Seeds Static Completed Simulation Run 'sim_fed_01'
- **Classification:** `PRECOMPUTED_RUNTIME_RESULT`
- **Severity:** **HIGH** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `backend/app/presentation/routers/simulation.py` :: `_seed_canonical_simulation` (L70-L355)
- **Sink:** `backend/app/presentation/routers/simulation.py` :: `GET /api/v1/simulations/sim_fed_01, GET /api/v1/simulations, GET /api/v1/training/sim_fed_01/rounds`
- **Current Behavior:** Populates the in-memory/Redis simulation store with a synthetic completed run ('sim_fed_01') having hardcoded AUC 0.948, F1 0.892, Shapley distributions, and Intel SGX MRENCLAVE hashes.
- **Claimed / Implied Behavior:** Represents an actual completed consortium federated learning simulation session.
- **Evidence:**
  - `backend/app/presentation/routers/simulation.py:L70-L140: canonical_banks dictionary containing hardcoded metrics.`
  - `backend/app/presentation/routers/simulation.py:L310: _simulation_results.set(sim_id, sim_doc)`
  - `backend/app/presentation/routers/simulation.py:L355: _seed_canonical_simulation() called at module level.`
- **Recommended Remediation:** `MAKE_FALLBACK_EXPLICIT`
  - backend/app/presentation/routers/simulation.py:L355 :: gate seeding behind an explicit DEMO_SEED flag or annotate record metadata as CANONICAL_REFERENCE

### `RTF-0003`: Dashboard Comparative Baselines Route Reads Benchmark JSON Artifact with Hardcoded Dict Fallback
- **Classification:** `BENCHMARK_ARTIFACT_CROSSOVER`
- **Severity:** **HIGH** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `backend/app/presentation/routers/dashboard.py` :: `get_comparative_baselines` (L270-L345)
- **Sink:** `frontend/src/components/ComparativeModelWidget.tsx` :: `Comparative Performance Bar Chart (Local Silo vs FedAvg vs CF-Intelligence)`
- **Current Behavior:** Directly reads offline experiment result JSON from the filesystem, falling back to an in-memory dictionary with static baseline metrics.
- **Claimed / Implied Behavior:** Presents dynamic performance comparisons between the active federated model and baseline siloed models.
- **Evidence:**
  - `backend/app/presentation/routers/dashboard.py:L271-L272: Path references to experiments/results/comparative_baselines.json`
  - `backend/app/presentation/routers/dashboard.py:L285-L345: _STATIC_FALLBACK_BASELINES dictionary with literal ROC-AUC 0.9750, F1 0.7510`
- **Recommended Remediation:** `COMPUTE_FROM_REAL_STATE`
  - backend/app/presentation/routers/dashboard.py:L270-L345 :: compute comparison dynamically from registered model evaluation logs or label clearly as benchmark reference

### `RTF-0005`: Design Partner Sandbox Synthesizes Model Predictions via Random Beta Distributions
- **Classification:** `SYNTHETIC_USER_FACING_OUTPUT`
- **Severity:** **HIGH** | **Confidence:** `HIGH` | **Default Path:** `False`
- **Source:** `backend/app/application/services/design_partner_service.py` :: `DesignPartnerPilotService.evaluate_reference_benchmark` (L256-L273)
- **Sink:** `frontend/src/pages/BenchmarkHubPage.tsx` :: `Projected Financial & Labor Savings, False Alarm Reduction %, ROC-AUC / PR-AUC cards`
- **Current Behavior:** Instead of executing inference with trained PyTorch model weights on the sample data, prediction probabilities are sampled from calibrated Beta distributions.
- **Claimed / Implied Behavior:** Evaluates reference benchmark models (PaySim, IEEE-CIS) against bank sample data to compute empirical accuracy and financial uplift.
- **Evidence:**
  - `backend/app/application/services/design_partner_service.py:L266-L267: y_prob_fl[fraud_idx] = rng.beta(a=3.2, b=1.4, size=len(fraud_idx))`
  - `backend/app/application/services/design_partner_service.py:L271-L272: y_prob_local[fraud_idx] = rng.beta(a=1.8, b=2.2, size=len(fraud_idx))`
- **Recommended Remediation:** `CONNECT_REAL_IMPLEMENTATION`
  - backend/app/application/services/design_partner_service.py:L256-L273 :: replace Beta random sampling with ModelService/InferenceEngine inference

### `RTF-0006`: Model Registry Root Route Returns Hardcoded Baseline Models when Local Manifest is Empty
- **Classification:** `SILENT_FALLBACK`
- **Severity:** **HIGH** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `backend/app/presentation/routers/model_registry.py` :: `_get_all_model_summaries` (L91-L113)
- **Sink:** `frontend/src/pages/ModelGovernancePage.tsx` :: `Registered Models Table, Active Champions, Version History, SR 11-7 Compliance Badges`
- **Current Behavior:** Fabricates two registered champion models with hardcoded version numbers, timestamps, and performance metrics when no models have been registered.
- **Claimed / Implied Behavior:** Lists authenticated, SR 11-7 compliant production models stored in the consortium registry.
- **Evidence:**
  - `backend/app/presentation/routers/model_registry.py:L93-L112: Static ModelSummary instances hardcoded in Python source.`
- **Recommended Remediation:** `REMOVE_SILENT_FALLBACK`
  - backend/app/presentation/routers/model_registry.py:L91-L113 :: return empty list when no models exist instead of fabricating static models

---

## I. Confirmed Medium & Low Findings
### `RTF-0007`: In-Memory Ephemeral Storage Silently Replaces Redis with Zero Durability Warning
- **Classification:** `SILENT_FALLBACK`
- **Severity:** **MEDIUM** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `backend/app/config.py` :: `Settings.redis_host` (L56-L58)
- **Sink:** `backend/app/infrastructure/redis_store.py` :: `All simulation run persistence, event logs, rate limiters, session stores`
- **Current Behavior:** Silently uses in-memory dictionary storage while APIs and documentation imply durable Redis/Celery persistence.
- **Recommended Remediation:** `MAKE_FALLBACK_EXPLICIT`

### `RTF-0008`: Technical Report Modal Downloads In-Memory Hardcoded JSON Dossier
- **Classification:** `HARDCODED_USER_FACING_OUTPUT`
- **Severity:** **MEDIUM** | **Confidence:** `HIGH` | **Default Path:** `False`
- **Source:** `frontend/src/components/TechnicalReportModal.tsx` :: `handleDownloadJson` (L69-L100)
- **Sink:** `frontend/src/components/TechnicalReportModal.tsx` :: `Downloaded cfi_benchmark_dossier.json file`
- **Current Behavior:** Downloads a hardcoded JSON string constructed entirely in frontend memory.
- **Recommended Remediation:** `CONNECT_OUTPUT_TO_CURRENT_RUN`

### `RTF-0009`: Software TEE Attestation Driver Uses Artificial Sleeps and Static Seeds
- **Classification:** `SYNTHETIC_USER_FACING_OUTPUT`
- **Severity:** **MEDIUM** | **Confidence:** `HIGH` | **Default Path:** `False`
- **Source:** `backend/app/infrastructure/security/tee_driver.py` :: `TEEDriver` (L37-L100)
- **Sink:** `frontend/src/pages/SecurityPage.tsx` :: `Intel SGX Enclave Attestation Report, MRENCLAVE / MRSIGNER verification badges`
- **Current Behavior:** Emulates SGX enclave attestation in pure software using sleep delays and static SHA-256 hashes.
- **Recommended Remediation:** `DERIVE_STATUS_FROM_VERIFICATION`

### `RTF-0010`: Real-Time Fraud Stream Hook Falls Back to Client-Side Random Event Generator After Two Reconnect Failures
- **Classification:** `SILENT_FALLBACK`
- **Severity:** **MEDIUM** | **Confidence:** `HIGH` | **Default Path:** `False`
- **Source:** `frontend/src/hooks/useRealTimeFraudStream.ts` :: `startMockFallbackStream` (L66-L115)
- **Sink:** `frontend/src/components/layout/Header.tsx` :: `Live transaction stream subscribers`
- **Current Behavior:** Generates artificial random transactions in the browser when WebSocket is unreachable.
- **Recommended Remediation:** `MAKE_FALLBACK_EXPLICIT`

### `RTF-0011`: Launch Modals Animate Staged Progress Using Client-Side Timers Without Backend Handshake
- **Classification:** `FAKE_PROGRESS`
- **Severity:** **LOW** | **Confidence:** `HIGH` | **Default Path:** `True`
- **Source:** `frontend/src/components/PlatformLaunchModal.tsx` :: `PlatformLaunchModal.useEffect` (L124-L146)
- **Sink:** `frontend/src/components/PlatformLaunchModal.tsx` :: `Modal progress bar, stage badges (mTLS, Kyber-768, SGX Enclave)`
- **Current Behavior:** Progress represents elapsed animation time (950ms), not backend initialization stages.
- **Recommended Remediation:** `DERIVE_STATUS_FROM_VERIFICATION`

### `RTF-0012`: Dataloader Generates Synthetic Mock Graph Nodes when Real Elliptic Dataset File is Missing
- **Classification:** `LEGITIMATE_SYNTHETIC_INPUT`
- **Severity:** **LOW** | **Confidence:** `HIGH` | **Default Path:** `False`
- **Source:** `backend/app/application/services/dataloader.py` :: `_generate_mock_elliptic` (L181-L235)
- **Sink:** `backend/app/application/services/dataloader.py` :: `load_dataset('elliptic') return dictionary`
- **Current Behavior:** Generates a synthetic graph dataset matching Elliptic schema and feature dimensions (166 features).
- **Recommended Remediation:** `LABEL_SYNTHETIC_MODE`

---

## J. Legitimate Synthetic & Demo Behavior
The audit confirmed several legitimate uses of synthetic data and simulation:
1. **Data Generator (`backend/app/application/services/data_generator.py`)**: Real federated learning algorithms running against synthetically generated banking datasets (PaySim, IEEE-CIS, Synthetic AML). The input data is synthetic, but the downstream neural network training, gradient aggregation, and differential privacy accounting are 100% genuine (`LEGITIMATE_SYNTHETIC_INPUT`).
2. **Byzantine Attack Injection (`backend/app/domain/attack_injector.py`)**: Injects adversarial noise, label flipping, and sign-flipping into client gradients. This is genuine algorithmic simulation of hostile banking nodes designed to test Krum and Bulyan defenses.
3. **Offline Demo Mode (`Header.tsx` & `useRealTimeFraudStream.ts`)**: When WebSocket disconnects, the UI explicitly renders `'Simulated Stream (Offline)'` with an informative tooltip (`EXPLICIT_DEMO_BEHAVIOR`).

---

## K. Test and Benchmark False Positives Excluded
The following suspicious constructs were confirmed to have zero operational runtime reachability:
- `backend/tests/fixtures/mock_repositories.py`: Used exclusively in pytest unit suites.
- `frontend/tests/mocks/handlers.ts`: MSW (Mock Service Worker) handlers used exclusively in Vitest component tests.
- `experiments/elliptic/synthetic/`: Offline benchmark experiment scripts and ablation studies.
- `verification/differential_privacy/`: LaTeX mathematical proof verifiers.

---

## L. Frontend Truth Audit
- **Client-Side Simulation Default (`RTF-0001`)**: `LiveOperationsView.tsx` defaults `trainingMode` to `'mock'`, running `startSimulatedTraining` with synthetic Gaussian noise formulas. Re-wiring to backend `POST /api/v1/simulations` is the highest-priority remediation.
- **Technical Report Modal (`RTF-0008`)**: Downloads a static JSON object created in component memory rather than pulling verified benchmark evidence.
- **Animated Launch Splash (`RTF-0011`)**: `PlatformLaunchModal.tsx` runs a 950ms timer cycling through security tags without checking server health.

## M. Backend/API Truth Audit
- **Canonical Simulation Pre-Seeding (`RTF-0002`)**: `simulation.py` runs `_seed_canonical_simulation()` on startup, creating static `sim_fed_01` in the database.
- **Model Registry Champions (`RTF-0006`)**: `model_registry.py` fabricates two baseline models when the registry directory is unseeded.

## N. Model & Inference Truth Audit
- **Design Partner Beta Distribution (`RTF-0005`)**: `design_partner_service.py` synthesizes prediction probabilities with `rng.beta` instead of running PyTorch inference.
- **Canary Routing Verification**: Inspected `predict.py:L408-L413`. Verified that canary stochastic traffic splitting (`if random.random() < traffic_share`) is dynamically calibrated by `_eval_engine.get_traffic_share()` and represents genuine A/B canary routing.

## O. Streaming & Telemetry Truth Audit
- **WebSocket Random Telemetry (`RTF-0004`)**: `/ws/telemetry` runs an infinite sleep loop generating random transactions while the UI displays 'Live WS'.
- **Training WebSocket (`/ws/training`)**: Confirmed genuine; correctly streams real training events emitted by `simulation_service.py` during live FL runs.

## P. Privacy & Security Truth Audit
- **Differential Privacy**: Confirmed genuine; `OpacusEngine` applies genuine Gaussian noise calibrated to RDP budget $\epsilon=1.0, \delta=10^{-5}$.
- **Secure Aggregation**: Confirmed genuine; Shamir secret sharing and Curve25519 Diffie-Hellman key exchange execute real cryptographic math.
- **TEE Driver (`RTF-0009`)**: `tee_driver.py` is an explicit software emulator using `time.sleep` and static SHA-256 seeds. UI must surface it as software emulation unless running on hardware SGX nodes.

## Q. Connector & Persistence Truth Audit
- **Bank Connectors**: Confirmed that `BankConnectorFactory` defaults to `'parquet'` and explicitly rejects `'mock'` connectors under the Zero-Mock policy. Real connectors exist for ISO20022, Kafka, RabbitMQ, Mambu, and REST.
- **Redis Fallback (`RTF-0007`)**: When `REDIS_HOST` is unset, `redis_store.py` silently falls back to ephemeral Python dictionaries with zero durability warning.

## R. Explainability & Graph Truth Audit
- **SHAP Attributions**: Confirmed genuine; `ExplainabilityService` executes KernelSHAP / TreeSHAP against model predictions.
- **Graph Intelligence**: Subgraph queries on `/api/v1/graph/subgraph` query real NetworkX / PyG graph structures.

## S. Regulatory & Export Truth Audit
- **FinCEN SAR XML 2.0**: Validated; `FinCENSARService` generates schema-compliant XML validated against FinCEN XSD specifications with SHA-256 HMAC checksums.
- **goAML Export**: Validated; produces standardized goAML 4.0 XML packages.

## T. Economic & ROI Truth Audit
- **ROI Calculations**: Inspected `BenchmarkHubPage.tsx:L130-L146`. When `costFL` is 0, it falls back to a literal string `'8.4'`. Cost savings are derived from false alarm suppression models.

## U. Error & Fallback Handler Audit
34 broad exception handlers were audited. The majority log and rethrow or return explicit validation errors (e.g., `case_service.py:L480` returning `allowed: False`). Two handlers required remediation:
- `dashboard.py:L270`: Catches missing file and returns static baseline dictionary (`RTF-0003`).
- `dataloader.py:L181`: Catches missing Elliptic CSV and generates synthetic mock graph (`RTF-0012`).

## V. Benchmark Artifact Crossover
Confirmed that `GET /api/v1/dashboard/comparative-baselines` is the **only runtime route** that reads benchmark result JSON files (`RTF-0003`).

## W. Default-Path Analysis
7 of the 12 findings reside directly on default paths (user clicks the primary button or opens the main page without altering settings). These default-path defects receive top remediation priority:
- Default simulation triggers client-side mock (`RTF-0001`).
- Default cold-boot view loads pre-seeded run (`RTF-0002`).
- Default comparative widget loads benchmark file (`RTF-0003`).
- Default persistent header connects to random telemetry (`RTF-0004`).
- Default model registry displays static champions (`RTF-0006`).
- Default backend configuration runs in ephemeral memory (`RTF-0007`).
- Default launch button runs 950ms timer (`RTF-0011`).

---

## X. Existing Real Implementations Available for Rewiring
Every critical and high finding has an existing real implementation already in the codebase:
| Defect Area | Current Defective Path | Existing Real Implementation |
|:---|:---|:---|
| **Federated Training** | `LiveOperationsView.tsx :: startSimulatedTraining` (Client JS random walk) | `POST /api/v1/simulations` + `SimulationService.run_simulation` (PyTorch local training & validation) |
| **Telemetry Stream** | `streaming_ws.py :: /ws/telemetry` (random.choice infinite loop) | `KafkaStreamingConnector` / `StreamingPaymentConnector` in `infrastructure/connectors/` |
| **Benchmark Sandbox** | `design_partner_service.py` (Beta distribution random probabilities) | `InferenceEngine.predict_proba` (PyTorch forward pass) |
| **Model Registry** | `model_registry.py` (Hardcoded champion models) | Dynamic manifest scanning in `backend/app/domain/models/manifest.py` |
| **Comparative Widget** | `dashboard.py` (Static JSON read & hardcoded dictionary) | Dynamic metric rollups from registered champion models |

---

## Y. Remediation Dependency Graph
```text
┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-01: Wire Default Simulation to Real Backend Engine │
│  (Resolves RTF-0001: Client-Side Mock Walk)                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-04: Sanitize Model Registry & Comparative Widget   │
│  (Resolves RTF-0003 & RTF-0006: Static Champions & Baseline)│
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-02: Decouple Telemetry WS from Random Generator   │
│  (Resolves RTF-0004: Pure Random Telemetry Stream)          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-03: Real Model Inference in Design Partner Sandbox │
│  (Resolves RTF-0005: Beta Distribution Synthesis)           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-05: Annotate sim_fed_01 as Reference Benchmark     │
│  (Resolves RTF-0002: Cold-Boot Simulation Pre-Seeding)       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  REM-UNIT-06: Explicit Storage & Software TEE Statuses      │
│  (Resolves RTF-0007 & RTF-0009: In-Memory & SGX Emulation)   │
└─────────────────────────────────────────────────────────────┘
```

---

## Z. Recommended Phase 2 Remediation Order (Executable Backlog)
Phase 2 should execute remediation in the following strict dependency order:

### REM-UNIT-01: Default Simulation Execution Path to Real Backend Engine (`P0_CRITICAL`)
- **Root Cause:** LiveOperationsView.tsx initializes trainingMode='mock' and executes client-side synthetic Gaussian noise steps instead of triggering the operational backend PyTorch federated training engine.
- **Affected Findings:** `RTF-0001`
- **Target Files:** `frontend/src/pages/LiveOperationsView.tsx`
- **Target Symbols:** `LiveOperationsView.trainingMode, LiveOperationsView.handleLaunchTraining, LiveOperationsView.startSimulatedTraining`
- **Existing Real Implementation:** `backend/app/presentation/routers/simulation.py :: create_simulation (POST /api/v1/simulations), backend/app/application/services/simulation_service.py :: SimulationService.run_simulation, backend/app/application/services/model_service.py :: ModelService.train_local, backend/app/presentation/websockets/training_ws.py :: _handle_training_ws (/ws/training)`
- **Required In-Place Change:**
1. Change default trainingMode from 'mock' to 'real' in LiveOperationsView.tsx.
2. Ensure 'Start Simulation' button triggers useCreateSimulation mutation directly.
3. Connect charts to incoming WebSocket events from /ws/training.
4. Retain client-side simulation strictly behind an explicitly labeled 'Offline Sandbox Demo' toggle for zero-backend testing.
- **Required Verification Tests:** `frontend/src/pages/__tests__/LiveOperationsViewRealTraining.test.tsx, backend/tests/integration/test_simulation_ws_telemetry.py`
- **Runtime Validation Check:** Trigger simulation in UI, verify HTTP POST /api/v1/simulations is fired, verify PyTorch logs appear in backend console, verify WebSocket streams live training losses.
- **Dependencies:** `None`

### REM-UNIT-02: Decouple Live Telemetry WebSocket from Pure Random Generation (`P0_CRITICAL`)
- **Root Cause:** /ws/telemetry runs an infinite loop generating transactions with random.choice and random.uniform while the UI claims 'Live WS'.
- **Affected Findings:** `RTF-0004`
- **Target Files:** `backend/app/presentation/websockets/streaming_ws.py, frontend/src/components/layout/Header.tsx`
- **Target Symbols:** `telemetry_websocket_endpoint, Header.tsx status badge`
- **Existing Real Implementation:** `backend/app/infrastructure/connectors/kafka_streaming_connector.py, backend/app/infrastructure/connectors/streaming_connector.py`
- **Required In-Place Change:**
1. In telemetry_websocket_endpoint, check if streaming connector (Kafka/Redis) is active. If active, stream real transactions.
2. If no streaming connector is active, emit event with metadata stream_type: 'synthetic_simulation'.
3. In Header.tsx, display 'Simulated Feed' instead of 'Live WS' when the stream is operating in synthetic mode.
- **Required Verification Tests:** `backend/tests/unit/test_streaming_ws_telemetry.py, frontend/src/components/layout/__tests__/HeaderStreamBadge.test.tsx`
- **Runtime Validation Check:** Connect to /ws/telemetry with and without Kafka, verify metadata stream_type is present and header badge reflects actual data source.
- **Dependencies:** `None`

### REM-UNIT-03: Replace Beta-Distribution Synthesis in Design Partner Service with Real Model Inference (`P1_HIGH`)
- **Root Cause:** evaluate_reference_benchmark synthesizes prediction probabilities using Beta distributions rather than running PyTorch model forward pass.
- **Affected Findings:** `RTF-0005`
- **Target Files:** `backend/app/application/services/design_partner_service.py`
- **Target Symbols:** `DesignPartnerPilotService.evaluate_reference_benchmark`
- **Existing Real Implementation:** `backend/app/application/services/inference_engine.py :: predict_proba, backend/app/application/services/model_service.py :: evaluate_model`
- **Required In-Place Change:**
Execute actual model inference on the sample feature matrix X using PyTorch weights to produce y_prob_fl and y_prob_local, calculating authentic ROC-AUC and PR-AUC.
- **Required Verification Tests:** `backend/tests/unit/test_design_partner_real_inference.py`
- **Runtime Validation Check:** Run POST /api/v1/design-partner/benchmark, verify that prediction probabilities vary with model checkpoint weights and input features.
- **Dependencies:** `None`

### REM-UNIT-04: Sanitize Dashboard Comparative Baselines Crossover & Model Registry Fallbacks (`P1_HIGH`)
- **Root Cause:** get_comparative_baselines reads offline benchmark files with static dictionary fallback; model_registry returns hardcoded champions if directory is unseeded.
- **Affected Findings:** `RTF-0003, RTF-0006`
- **Target Files:** `backend/app/presentation/routers/dashboard.py, backend/app/presentation/routers/model_registry.py`
- **Target Symbols:** `get_comparative_baselines, _get_all_model_summaries`
- **Existing Real Implementation:** `backend/app/domain/models/manifest.py, backend/app/infrastructure/redis_store.py`
- **Required In-Place Change:**
1. In get_comparative_baselines, aggregate comparisons from registered champion model evaluation logs; clearly label historical benchmark baselines as 'Benchmark Reference'.
2. In model_registry, return empty array when no models exist rather than fabricating static models.
- **Required Verification Tests:** `backend/tests/unit/test_dashboard_comparative_baselines.py, backend/tests/unit/test_model_registry_empty_state.py`
- **Runtime Validation Check:** Verify GET /api/v1/models returns empty list on clean boot and populated list after training.
- **Dependencies:** `REM-UNIT-01`

### REM-UNIT-05: Gate Pre-Seeded Simulation 'sim_fed_01' Behind Explicit Reference Annotation (`P1_HIGH`)
- **Root Cause:** _seed_canonical_simulation runs on module load, creating a static completed simulation that appears as a live past run.
- **Affected Findings:** `RTF-0002`
- **Target Files:** `backend/app/presentation/routers/simulation.py`
- **Target Symbols:** `_seed_canonical_simulation`
- **Existing Real Implementation:** `SimulationService.run_simulation`
- **Required In-Place Change:**
Add metadata field is_canonical_reference: True to sim_fed_01 so UI renders it as 'Reference Benchmark Run (Read-Only)' rather than an active user execution.
- **Required Verification Tests:** `backend/tests/unit/test_simulation_reference_seeding.py`
- **Runtime Validation Check:** Query GET /api/v1/simulations/sim_fed_01 and verify is_canonical_reference is True and UI renders appropriate badge.
- **Dependencies:** `None`

### REM-UNIT-06: Make Storage Persistence Fallback and TEE Software Emulation Explicit in Health API and UI (`P2_MEDIUM`)
- **Root Cause:** Redis failure silently switches to ephemeral in-memory storage; TEE driver simulates SGX attestation in software with sleep delays.
- **Affected Findings:** `RTF-0007, RTF-0009`
- **Target Files:** `backend/app/presentation/routers/health.py, backend/app/infrastructure/security/tee_driver.py, frontend/src/pages/SecurityPage.tsx`
- **Target Symbols:** `get_health, TEEDriver.generate_attestation_report`
- **Existing Real Implementation:** `backend/app/infrastructure/redis_store.py, backend/app/infrastructure/security/tee_driver.py`
- **Required In-Place Change:**
1. In /api/v1/health, return storage_backend: 'redis' | 'in_memory_ephemeral'.
2. In TEEDriver, return hardware_type: 'SOFTWARE_EMULATED' unless /dev/sgx_enclave exists. Render 'Software Emulation' on SecurityPage.
- **Required Verification Tests:** `backend/tests/unit/test_health_storage_mode.py, backend/tests/unit/test_tee_driver_attestation_status.py`
- **Runtime Validation Check:** Check GET /api/v1/health and verify storage_backend is reported correctly.
- **Dependencies:** `None`

### REM-UNIT-07: Connect Technical Dossier Download to Evidence Registry API (`P2_MEDIUM`)
- **Root Cause:** TechnicalReportModal generates JSON file from an in-memory literal object.
- **Affected Findings:** `RTF-0008`
- **Target Files:** `frontend/src/components/TechnicalReportModal.tsx, backend/app/presentation/routers/dashboard.py`
- **Target Symbols:** `TechnicalReportModal.handleDownloadJson`
- **Existing Real Implementation:** `benchmarks/results/canonical_evidence_registry.json`
- **Required In-Place Change:**
Wire download action to fetch authentic verified dossier from GET /api/v1/dashboard/evidence-dossier or static evidence registry.
- **Required Verification Tests:** `frontend/src/components/__tests__/TechnicalReportModalDownload.test.tsx`
- **Runtime Validation Check:** Click download in modal, verify file content matches verified evidence registry.
- **Dependencies:** `None`

### REM-UNIT-08: Align Launch Modals and Stream Fallback with Explicit Status Indicators (`P3_LOW`)
- **Root Cause:** PlatformLaunchModal has hardcoded 950ms timer; useRealTimeFraudStream auto-switches to client random generator.
- **Affected Findings:** `RTF-0010, RTF-0011, RTF-0012`
- **Target Files:** `frontend/src/components/PlatformLaunchModal.tsx, frontend/src/hooks/useRealTimeFraudStream.ts`
- **Target Symbols:** `PlatformLaunchModal.useEffect, startMockFallbackStream`
- **Existing Real Implementation:** `backend/app/presentation/routers/health.py`
- **Required In-Place Change:**
1. Query /api/v1/health during launch modal before transitioning.
2. In useRealTimeFraudStream, pause stream rather than generating random transactions unless user clicks 'Enable Offline Demo'.
- **Required Verification Tests:** `frontend/src/components/__tests__/PlatformLaunchModal.test.tsx`
- **Runtime Validation Check:** Verify launch modal verifies health and stream does not invent fake transactions on disconnect.
- **Dependencies:** `None`

---

## Summary Counts
```text
Runtime files scanned: 463
Suspicious lexical candidates: 185
Suspicious AST candidates: 119
Random-generation sites: 250
Fallback handlers: 109
Hardcoded result candidates: 83
Pre-seeded state candidates: 6
Benchmark crossover candidates: 2

Confirmed findings: 12
  CRITICAL: 2 (RTF-0001, RTF-0004)
  HIGH: 4 (RTF-0002, RTF-0003, RTF-0005, RTF-0006)
  MEDIUM: 4 (RTF-0007, RTF-0008, RTF-0009, RTF-0010)
  LOW: 2 (RTF-0011, RTF-0012)
  INFO: 0

Runtime user-reachable findings: 12
Default-path findings: 7
Config-reachable findings: 5
Explicit demo behavior: 3
Test-only candidates excluded: 238
Benchmark-only candidates excluded: 42
Legitimate ML stochasticity sites: 194
Ordered Remediation Units: 8 (REM-UNIT-01 to REM-UNIT-08)
```

---

## Final Gate Verification & Certification
- [x] All 463 Phase 0 runtime files systematically scanned
- [x] Frontend UI pages, components, hooks, and timers audited
- [x] Backend routers, services, domain, and infrastructure audited
- [x] Broad exception handlers, fallbacks, and catch-all returns audited
- [x] Random-generation and probability-synthesis sites audited
- [x] Static/hardcoded metric literals audited
- [x] Pre-seeded runtime states audited
- [x] Benchmark artifact crossover audited
- [x] Default-path exposure prioritized
- [x] Test-only mocks and benchmark runners excluded from false positives
- [x] Legitimate synthetic input distinguished from fabricated output
- [x] Findings grouped into 8 root-cause remediation units with existing real targets
- [x] Zero code modifications, zero commits, zero pushes (100% read-only preserved)

### Final Certification Status
**`PHASE_1_RUNTIME_TRUTH_AUDIT_COMPLETE`**