# Commercial Multi-Role Web Management Console Specification (2026 Edition)

---

## 1. Executive Overview & Design Vision

The **Collaborative Fraud Intelligence (CFI) Web Management Console** is an enterprise-grade, high-performance web interface designed for cross-institutional fraud operations, anti-money laundering (AML) case investigation, and confidential federated learning governance.

Built with **React 19, TypeScript 5.8, Vite, and TailwindCSS**, the console features a tailored **Dark Slate Glassmorphism** design system with instant sub-millisecond route transitions, zero cumulative layout shift (CLS), mobile-first responsive layouts, and role-tailored operational views.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CFI COMMERCIAL CONSOLE ARCHITECTURE                             │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  [ Enterprise SaaS Landing Page (`/`) ] ──► [ 5-Stage Platform Launch Modal ]          │
│                                                       │ (Zero-Flicker Transition)      │
│                                                       ▼                                │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                    ENTERPRISE APPLICATION SHELL (`Layout.tsx`)                   │  │
│  ├────────────────────────────────┬─────────────────────────────────────────────────┤  │
│  │ SIDEBAR NAVIGATION (`Sidebar`) │ HEADER TELEMETRY (`Header.tsx`)                 │  │
│  │  - Live Consortium Ops (2)     │  - Live WebSocket Connection Indicator          │  │
│  │  - AML Intelligence Hub (8)    │  - Real-Time Latency Meter (<15ms SLA)          │  │
│  │  - Enterprise Platform (5)     │  - Streamed Transactions Running Counter        │  │
│  │  - Observability & Tracing (4) │  - Interactive API Documentation Deep Link      │  │
│  ├────────────────────────────────┴─────────────────────────────────────────────────┤  │
│  │ MULTI-ROLE ADAPTER (`EXECUTIVE`, `COMPLIANCE`, `ML_ENGINEER`, `INVESTIGATOR`)    │  │
│  │  - 16 Modular Pages with React Suspense & Granular Error Boundaries              │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Enterprise Multi-Role Persona Architecture

The console dynamically adapts navigation hierarchies, metric cards, and actionable controls according to the user's authenticated persona and Attribute-Based Access Control (ABAC) clearance:

| Enterprise Persona | Primary Operational Scope | Core UI Modules & Visible Controls |
| :--- | :--- | :--- |
| **`EXECUTIVE`**<br>*(CRO, Head of Financial Crime, VP Anti-Fraud)* | **Consortium ROI, Health & Risk Governance** | • Consortium ROI & Total Dollars Prevented cards.<br>• Global Quorum Status & Participating Bank Health.<br>• Consortium ROC-AUC & PR-AUC vs Single-Bank Baselines.<br>• SLA Breach Monitoring & Availability Gauges (99.99%). |
| **`COMPLIANCE_OFFICER`**<br>*(AML Officer, MLRO, Risk Auditor)* | **Regulatory Governance, Audit & Sanctions** | • Differential Privacy Budget Gauges ($\varepsilon=1.0, \delta=10^{-5}$).<br>• FinCEN BSA Suspicious Activity Report (SAR) XML v1.2 Manager.<br>• **Four-Eyes Dual Control** supervisor signoff workbench.<br>• GDPR Article 17 "Right to Erasure" Federated Unlearning logs.<br>• Automated EU AI Act conformity assessment certification export. |
| **`ML_ENGINEER`**<br>*(MLOps Lead, Research Scientist)* | **FL Orchestration, Convergence & Defense** | • Loss Convergence, Weight Distribution & ROC Curves.<br>• Byzantine Aggregation Algorithm Tuner (Krum, Bulyan, FedProx $\mu=0.01$).<br>• **Interactive Chaos & Byzantine Attack Injector** (`ChaosAttackInjectorPanel`).<br>• **Drag-and-Drop Dataset Ingestion Studio** (`DatasetIngestionStudioModal`).<br>• Feature drift auditor (Wasserstein Distance, JS Divergence, KS Test). |
| **`FRAUD_INVESTIGATOR`**<br>*(Senior Fraud Analyst, Case Officer)* | **Real-Time Triage & Network Investigation** | • Live 500 tx/s streaming fraud feed with severity badges.<br>• **6-Stage AML Case Workbench** (`CaseDetailPage`).<br>• 2D/3D WebGL Multi-Bank Transaction Graph (`GraphPage`).<br>• Local TreeSHAP / KernelSHAP feature attribution panels.<br>• MinHash LSH Fuzzy PSI entity matching console (`PsiPage`). |

---

## 3. Design System Tokens & Glassmorphism Aesthetics

The console implements a custom glassmorphism aesthetic adhering strictly to high-contrast readability and enterprise ergonomics:

### 3.1. Color Palette & Functional Tokens
* **Background Canvas (`--color-bg-primary`)**: Dark Slate Navy (`#070718` / `#0B0F19`) minimizing eye strain in 24/7 Security Operations Centers (SOC).
* **Card & Panel Surfaces (`--color-bg-secondary`)**: Frosted Obsidian (`rgba(15, 23, 42, 0.75)`) paired with `backdrop-filter: blur(16px)` and `border: 1px solid rgba(255, 255, 255, 0.08)`.
* **Primary Brand Accent**: Vibrant Indigo Glow (`#6366F1` / `#818CF8`) for primary CTAs, active route badges, and focus rings.
* **Functional Signal Accents**:
  * **Cyan Insight (`#06B6D4`)**: Low-latency meters, telemetry metrics, and API payloads.
  * **Emerald Safe (`#10B981`)**: Consensus quorum achieved, valid signatures, healthy bank nodes.
  * **Amber Warning (`#F59E0B`)**: Concept drift detected, DP budget threshold reached ($>80\%$), pending approvals.
  * **Crimson Alert (`#EF4444`)**: Critical fraud score ($>0.85$), Byzantine poisoning detected, SLA breach.
  * **Purple GNN (`#A855F7`)**: Graph embeddings, attention weights, and multi-hop network links.

### 3.2. Typography & Numerical Formatting
* **Primary Typeface**: Inter / Outfit sans-serif hierarchy for legible scanning of high-density compliance data.
* **Monospace Typeface**: JetBrains Mono / SF Mono for transaction identifiers, IBAN tokens, HMAC-SHA256 signatures, tensor shapes, and cryptographic hashes.
* **Numeric Standard**: Strict localized number formatting with thousands separators (e.g., `$1,450,000.00`, `38,421 tx/s`).

### 3.3. Zero Cumulative Layout Shift (CLS Invariant)
In compliance with workspace guidelines (`AGENTS.md` Rule 5):
* **Fixed Minimum Heights**: Interactive buttons, tabs, and pill selectors maintain bounded heights (`min-h-[44px]`).
* **Static Border Placeholders**: Active tab states alter border colors without changing border widths, eliminating vertical jumps.
* **Responsive Overflow Prevention**: Horizontal scrolling is strictly isolated to designated tab wrappers (`overflow-x-auto no-scrollbar`), preventing global viewport horizontal scrolling.

---

## 4. Comprehensive Console Route & View Directory

The application shell provides access to 16 distinct production views organized into 4 functional hubs:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              CFI CONSOLE ROUTE DIRECTORY                               │
├─────────────────────────┬─────────────────────────┬────────────────────────────────────┤
│ SECTION HUB             │ ROUTE PATH              │ COMPONENT / TARGET SCOPE           │
├─────────────────────────┼─────────────────────────┼────────────────────────────────────┤
│ 1. Live Operations      │ `/dashboard`            │ Consortium Overview & Node Status  │
│                         │ `/operations`           │ Real-Time Verification HUD Grid    │
│                         │ `/operations/:id`       │ Specific Simulation Round Telemetry│
│ 2. AML Intelligence     │ `/investigation`        │ SHAP Attributions & Smurfing Radar │
│                         │ `/alerts`               │ Real-Time Transaction Alert Stream │
│                         │ `/cases`                │ Multi-Bank Case Management Board   │
│                         │ `/cases/:caseId`        │ 6-Stage Case Workbench & SAR XML   │
│                         │ `/rules` / `/policies`  │ Consortium Policy Voting Engine    │
│                         │ `/psi`                  │ Fuzzy MinHash PSI Matching Console │
│                         │ `/security`             │ Vault PKI, HSM & ABAC Simulator    │
│                         │ `/scenarios`            │ Attack Injection & Stress Tests    │
│                         │ `/graph`                │ 2D/3D WebGL Multi-Bank Entity Graph│
│ 3. Enterprise Platform  │ `/benchmarks`           │ Real-World Datasets & Pilot Sandbox│
│                         │ `/developer`            │ Interactive Swagger & Webhook Spec │
│                         │ `/onboarding`           │ 5-Stage Bank Onboarding Wizard     │
│                         │ `/coordinator`          │ FL Aggregator & Quorum Controller  │
│                         │ `/privacy-defense`      │ Rényi DP & Curve25519 SecAgg Suite │
│ 4. Observability        │ `/observability`        │ Prometheus, OpenTelemetry & Drift  │
└─────────────────────────┴─────────────────────────┴────────────────────────────────────┘
```

---

## 5. In-Depth View Specifications & User Workflows

### 5.1. SaaS Landing Page & Launch Sequence (`/`)
* **Component**: [`LandingPage.tsx`](../frontend/src/pages/LandingPage.tsx)
* **Interactive Hero Preview (`InteractiveDashboardPreview`)**:
  * Features 7 interactive tabs: `Telemetry` (home), `GNN Topology` (gnn), `Differential Privacy` (privacy), `BFT Defense` (bft), `FinCEN SAR` (sar), `Chaos Attack` (chaos), and `Dataset Ingest` (ingest).
  * Direct deep linking routes users directly to `/scenarios` or `/operations?openIngest=true`.
* **Platform Launch Flow (`PlatformLaunchModal.tsx`)**:
  * Triggered via primary "Launch Platform Console" button.
  * Displays a 5-stage initialization sequence:
    1. *mTLS 1.3 & Vault PKI Handshake* (`FIPS 140-3 · 1.2ms`)
    2. *Post-Quantum Kyber-768 Exchange* (`NIST FIPS 203 Lattice`)
    3. *PyTorch GAT & Rényi DP Noise Calibration* (`ε=1.0, δ=1e-5`)
    4. *Intel SGX Enclave & Paillier HE Sum* (`Groth16 BN254 TEE`)
    5. *Byzantine Krum Defense & Global Weights* (`Consensus Model Dispatched`)
  * Navigates immediately to the target dashboard without screen flicker or intermediate loading screens.

### 5.2. Consortium Operations Dashboard (`/dashboard`)
* **Component**: [`Dashboard.tsx`](../frontend/src/pages/Dashboard.tsx)
* **Key Widgets**:
  * **Consortium Node Grid**: Live heartbeats for Bank Alpha (JPM), Bank Beta (HSBC), and Bank Gamma (DBK).
  * **Telemetry Summary**: Global ROC-AUC ($0.9120$), active participants, differential privacy spent ($\varepsilon$), and cumulative fraud savings.
  * **Quick Actions Bar**: Fast jump cards to Chaos Simulator, Ingestion Studio, and Benchmark Hub.

### 5.3. Live Verification & HUD Grid (`/operations`)
* **Component**: [`LiveOperationsView.tsx`](../frontend/src/pages/LiveOperationsView.tsx)
* **Visual Verification Metrics**:
  * **ROC Performance Overlay (`ROCCurve.tsx`)**: Real-time comparison between Collaborative FedGNN ($0.912$) and Single-Bank Baselines ($0.835$).
  * **Loss Convergence Line (`LossChart.tsx`)**: Multi-round training and validation loss decay.
  * **Dynamic Confusion Matrix (`ConfusionMatrix.tsx`)**: Real-time TP, FP, TN, FN counts at configurable threshold $\tau \in [0.1, 0.9]$.
  * **Multi-Bank Bar Comparison (`MetricsComparisonBarChart.tsx`)**: Precision, Recall, and PR-AUC breakdown across all consortium members.
* **Deep Linking**: Supports URL parameter `?openIngest=true` to automatically launch the dataset ingestion modal.

### 5.4. AML Case Workbench & SAR Generator (`/cases/:caseId`)
* **Component**: [`CaseDetailPage.tsx`](../frontend/src/pages/CaseDetailPage.tsx)
* **6-Stage Investigation Lifecycle**:
  `NEW` ──► `ASSIGNED` ──► `UNDER_INVESTIGATION` ──► `ESCALATED` ──► `SAR_GENERATED` ──► `CLOSED`
* **Four-Eyes Dual Control**:
  * Resolving or closing a case requires independent cryptographic verification by both a `compliance_officer` and a `risk_analyst`.
  * Single-user closure is strictly rejected by client-side validation and backend ABAC policies.
* **FinCEN BSA SAR XML E-Filing Export**:
  * One-click generation of fully compliant FinCEN SAR XML documents.
  * Embedded XML schema validator against official XSD definitions with copy-to-clipboard and `.xml` download triggers.
* **AI FinCEN Copilot SAR Narrative Persistence & Tab Session Storage**:
  * AI Copilot generates an exhaustive FinCEN SAR narrative, 4-Eyes supervisor briefing, and top SHAP anomaly driver breakdown via `/api/v1/copilot/generate-sar-narrative`.
  * **Case-Scoped Session Cache (`cfi_copilot_case_${caseId}`)**: The synthesized narrative, supervisor summary, and risk factor attributions are cached in browser `sessionStorage`. Analysts can navigate away to Live Operations, Entity Graph, or Alerts and return without losing generated narratives or triggering duplicate LLM API invocations.
  * **Cache State & Purge Controls**: Visual session cache indicators (`💾 Restored from Session Cache` / `Session Cache Active`) and active storage key pill display cache status. Analysts can re-synthesize at any time or manually purge the session cache via "Clear Cache".

### 5.5. Entity Graph Explorer & Deep Linking (`/graph`)
* **Component**: [`GraphPage.tsx`](../frontend/src/pages/GraphPage.tsx)
* **Capabilities**:
  * Interactive 2D/3D WebGL graph rendering multi-hop circular smurfing syndicates across Bank A, Bank B, and Bank C.
  * Node inspection panel displaying anonymized 512-dim GraphSAGE embedding vectors and edge transaction attributes.
  * **Entity Graph Deep Linking & Bi-Directional State Synchronization**:
    * URL Query Parameters: `?entity_id=<entId>&depth=2|3` (clamped $1 \le \mathrm{depth} \le 4$).
    * Direct deep links from **Alerts Page** ([`AlertsPage.tsx`](../frontend/src/pages/AlertsPage.tsx)): Quick jump from alert cards and Explainability Panel (Attribution and GNN Explainer node/edge endpoints) to the suspect entity's 2-hop or 3-hop ego network.
    * Direct deep links from **AML Case Workbench** ([`CaseDetailPage.tsx`](../frontend/src/pages/CaseDetailPage.tsx)): Dedicated **Suspect Entities & Graph Topology Hub** and clickable deep links in the Case Evidence Registry table.
    * **Deep-Linked Ego Focus Banner**: Highlights active entity identifier, matched entity metadata (label, bank node), depth indicator pill, and one-click "Reset Focus & Clear URL" control.

### 5.6. Dataset Ingestion Studio Modal
* **Component**: [`DatasetIngestionStudioModal.tsx`](../frontend/src/components/ingestion/DatasetIngestionStudioModal.tsx)
* **Key Features**:
  * Drag-and-drop CSV / Parquet file upload.
  * **Zero-Raw-PII Regex Scanner**: Pre-flight inspection flagging and quarantining unhashed national IDs (`TCKN`, `SSN`), credit card numbers (Luhn check), and raw IBANs.
  * **Great Expectations 1.x Data Contract**: Automatic schema inference, null-value checks, and range bounds verification.

### 5.7. Interactive Chaos & Byzantine Attack Injector
* **Component**: [`ChaosAttackInjectorPanel.tsx`](../frontend/src/components/chaos/ChaosAttackInjectorPanel.tsx)
* **Attack Scenarios**:
  * *Label Flipping Attack* (Inverting fraud labels in malicious client batch).
  * *Sign Flipping Attack* (Reversing gradient descent direction vectors).
  * *Gaussian Noise Flooding* (Injecting high-variance perturbation vectors).
* **Real-Time Defense Telemetry**: Displays Krum distance matrices, trimmed client weights, and Byzantine filtering efficiency.

---

## 6. Real-Time Streaming & WebSocket Engine

The console integrates continuous telemetry via [`useRealTimeFraudStream.ts`](../frontend/src/hooks/useRealTimeFraudStream.ts) displayed globally in the application header:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        GLOBAL APPLICATION HEADER                       │
├──────────────────────────┬──────────────────────┬──────────────────────┤
│ Brand Title              │ Live WebSocket State │ Documentation Link   │
│ "Collaborative Fraud     │ [• Live WS (4ms)]    │ "API Docs"           │
│  Intelligence Platform"  │ [38,421 txns]        │ "v2.4.1"             │
└──────────────────────────┴──────────────────────┴──────────────────────┘
```

* **Connection State Machine**:
  * `connected`: Live WebSocket link active, rendering green pulsing indicator with measured round-trip ping latency.
  * `mock_active`: WebSocket disconnected; automatically falls back to deterministic simulated sandbox telemetry with an indigo badge.
  * `connecting`: Transient amber badge during reconnection attempts with exponential backoff.
  * `disconnected`: Offline slate indicator.

---

## 7. Accessibility, Resilience & Form Hardening

1. **Modal Accessibility (`useModalA11y.ts`)**:
   * Escape key dismiss listener.
   * Focus trap preventing keyboard focus from escaping active modal windows.
   * `aria-modal="true"`, `role="dialog"`, and `aria-labelledby` semantics across all modal dialogs.
2. **Granular Error Boundaries (`ErrorBoundary.tsx`)**:
   * Critical sub-components (e.g., WebGL Canvas, dynamic charts) are wrapped in isolated error boundaries. A rendering failure in a chart displays a clean fallback card without crashing the rest of the application shell.
3. **Form Hardening & Mutex Locks**:
   * In-flight mutation buttons display animated spinner states and disable repeated clicks to prevent double-submission during round triggers or case closures.

---

## 8. Automated Test Verification Matrix

All console components, navigation routes, deep-linking rules, and error states are validated by the frontend Vitest and React Testing Library test suites:

| Test Suite Category | Representative Test File | Verified Capabilities | Status |
| :--- | :--- | :--- | :---: |
| **Routing & Deep Linking** | [`RoutingAndDeepLinking.test.tsx`](../frontend/src/pages/__tests__/RoutingAndDeepLinking.test.tsx) | 16 route resolutions, URL query params (`?openIngest=true`), dynamic `:caseId` parsing | `14/14 PASSED` |
| **Entity Graph Deep Linking** | `GraphPage.test.tsx`, `AlertsPage.test.tsx`, `CaseDetailPage.test.tsx` | Cross-bank entity deep linking (`?entity_id=...&depth=...`), 2-hop/3-hop ego nets, URL state sync | `19/19 PASSED` |
| **FinCEN Copilot Narrative Persistence** | [`CaseDetailPage.test.tsx`](../frontend/src/pages/__tests__/CaseDetailPage.test.tsx) | Session storage caching (`cfi_copilot_case_${caseId}`), lazy state hydration, route switch sync, cache clearing | `7/7 PASSED` |
| **Comprehensive Error States** | `ComprehensiveErrorStates.integration.test.tsx` | Pristine states, form validation, 401/403 ABAC errors, Four-Eyes enforcement | `15/15 PASSED` |
| **Viewport Overflow & Fit** | [`DesktopComponentFit.test.tsx`](../frontend/src/pages/__tests__/DesktopComponentFit.test.tsx) | 1920x1080, 1440x900, and 1280x800 desktop overflow prevention | `3/3 PASSED` |
| **Modal Accessibility** | `ModalAccessibility.test.tsx` | Keyboard trap, Esc key listener, aria semantics on Platform Launch and Ingest | `4/4 PASSED` |
| **Real-Time Stream Hook** | `useRealTimeFraudStream.test.ts` | WebSocket state machine, latency calculation, offline fallback transition | `3/3 PASSED` |
| **Interactive Charts Suite** | `ROCCurve.test.tsx`, `MetricsComparisonBarChart.test.tsx` | Chart SVG rendering, tooltip bindings, grouped consortium metrics | `12/12 PASSED` |
| **Security & Compliance UI** | [`SecurityPage.test.tsx`](../frontend/src/pages/__tests__/SecurityPage.test.tsx) | Vault seal status, ABAC simulator tab switches, EU AI Act export | `2/2 PASSED` |
| **Complete Test Suite** | **81 Test Files** | **Comprehensive UI/UX, Contract & Integration Verification** | **300/300 PASSED** |
| **Production Build** | `tsc -b && vite build` | **Zero TypeScript compile errors, 35 production assets bundled cleanly** | **0 ERRORS** |

---

## 9. Related Architectural & System Specifications

* **Consortium Governance & Voting Spec:** [`docs/consortium_governance_spec.md`](consortium_governance_spec.md)
* **Bank Node Onboarding Architecture:** [`docs/bank_onboarding_guide.md`](bank_onboarding_guide.md)
* **Real-Time Inference API Blueprints:** [`docs/realtime_inference_api.md`](realtime_inference_api.md)
* **AML Platform Architecture & Threat Model:** [`docs/aml-platform.md`](aml-platform.md)
* **Observability & SIEM Integration Guide:** [`docs/siem_and_support_guide.md`](siem_and_support_guide.md)


