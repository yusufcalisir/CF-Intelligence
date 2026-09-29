# Fraud Intelligence Commercial Web Console (Frontend) 🛡️

[![React Version](https://img.shields.io/badge/React-19.3.0-61DAFB.svg?style=flat&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6.svg?style=flat&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Vite](https://img.shields.io/badge/Vite-6.0-646CFF.svg?style=flat&logo=vite&logoColor=white)](https://vitejs.dev)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4.0-06B6D4.svg?style=flat&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![Vitest Passing](https://img.shields.io/badge/tests-355%2F355_passing-success.svg?style=flat&logo=vitest&logoColor=white)](https://vitest.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

This directory contains the production-grade **Enterprise Fraud Intelligence Web Console & Commercial Dashboard** for the **Collaborative Fraud Intelligence (CF-Intelligence)** platform. Built with React 19, TypeScript, and Vite, the frontend delivers a high-performance, real-time user interface for fraud analysts, compliance officers, and consortium operators.

---

## 1. Directory Structure & Architecture

```
frontend/
├── package.json                   # Pinned production & development dependencies and scripts
├── vite.config.ts                 # Vite bundler configuration & proxy rewrites
├── vitest.config.ts               # Vitest unit & component test configuration (jsdom environment)
├── vitest.setup.ts                # Global test setup (matchers, mocks, polyfills)
├── tsconfig.json                  # TypeScript compiler options & path aliases
├── eslint.config.js               # ESLint flat config with React Hooks & TypeScript rules
├── vercel.json                    # Edge reverse proxy, rewrites, and security headers (CSP, HSTS)
├── nginx.conf                     # Production containerized Nginx reverse proxy configuration
├── Dockerfile                     # Multi-stage Docker build (Node 22 builder + Nginx Alpine runtime)
│
├── e2e-workflows/                 # Playwright E2E user workflow automation suites
├── e2e-visual/                    # Playwright visual regression baseline tests
├── e2e-responsive/                # Viewport responsive layout regression tests
├── e2e-a11y/                      # Axe-core automated accessibility compliance tests
│
└── src/
    ├── main.tsx                   # Application entry point & React root mount
    ├── App.tsx                    # Root routing, layout structure, and QueryClient provider
    ├── index.css                  # Global design system tokens & Tailwind CSS directives
    │
    ├── api/                       # API integration layer & backend schema contracts
    │   ├── client.ts              # Axios HTTP client with authentication & tenant interceptors
    │   ├── queries.ts             # TanStack React Query hooks for REST endpoints
    │   └── types.ts               # TypeScript schemas synchronized with backend Pydantic models
    │
    ├── pages/                     # 19 Enterprise Commercial Console Views
    │   ├── LandingPage.tsx        # High-converting SaaS landing page & interactive capability demo
    │   ├── Dashboard.tsx          # Executive overview & multi-bank consortium KPI telemetry
    │   ├── LiveOperationsView.tsx # Real-time transaction streaming terminal & scoring inspector
    │   ├── InvestigationDashboard.tsx # Multi-stage alert triage workbench & evidence timeline
    │   ├── CaseDetailPage.tsx     # Case detail view, Four-Eyes supervisor signing & SAR XML export
    │   ├── CasesPage.tsx          # Case management index, SLA timers & status filtering
    │   ├── AlertsPage.tsx         # Real-time fraud alert feed & bulk disposition controls
    │   ├── GraphPage.tsx          # 2D/3D knowledge graph visualizer & multi-hop ego networks
    │   ├── SecurityPage.tsx       # Zero-Trust security posture, ABAC policy tester & audit ledger
    │   ├── ObservabilityPage.tsx  # Prometheus SLA metrics, latency heatmaps & health telemetry
    │   ├── PrivacyDefensePage.tsx # Differential Privacy budget gauges & Membership Inference defense
    │   ├── CoordinatorPage.tsx    # Federated learning coordinator console & client node status
    │   ├── BankOnboardingPage.tsx # Consortium member onboarding & mTLS credential provisioning wizard
    │   ├── BenchmarkHubPage.tsx   # Empirical benchmark comparison hub (FL vs. Isolated across 4 datasets)
    │   ├── ApiDocsPage.tsx        # Interactive API documentation portal & live OpenAPI runner (/developer)
    │   ├── PoliciesPage.tsx       # Dynamic AML risk policy rule manager & threshold tuning
    │   ├── PsiPage.tsx            # Private Set Intersection (Fuzzy PSI) cross-bank entity lookup
    │   ├── SimulationView.tsx     # Multi-bank round orchestrator & live simulation launcher
    │   └── ScenariosPage.tsx      # Pre-packaged fraud typology attack scenario simulator
    │
    ├── components/                # Modular UI Design System
    │   ├── layout/                # Responsive layout primitives (Header, Sidebar, Layout)
    │   ├── dashboard/             # Specialized operational panels (GNN, Privacy, Model Registry)
    │   ├── cases/                 # Case management & counterfactual explorer components
    │   ├── network/               # Interactive network topology & cross-bank graphs
    │   ├── charts/                # Recharts-based data visualizations (ROC, Loss, Confusion Matrix)
    │   └── common/                # Shared UI primitives (ErrorBoundary, Modals, Buttons)
    │
    ├── hooks/                     # Custom React hooks (real-time stream, accessibility, modals)
    ├── services/                  # Browser services (API client, WebSocket manager, sound alerts)
    ├── stores/                    # Lightweight client state stores (Zustand)
    └── utils/                     # Formatting, PII sanitization, and dataset profile helpers
```

---

## 2. Core Capabilities & Technology Stack

| Capability | Implementation | Description |
| :--- | :--- | :--- |
| **Core Framework** | React 19 + TypeScript 5.7 | Modern component architecture with strict type safety |
| **Build & Bundler** | Vite 6 | Sub-second HMR and optimized production code splitting |
| **Styling & Design System** | Tailwind CSS v4 | Curated dark-mode aesthetic with glassmorphic depth |
| **Server State Management** | TanStack React Query v5 | Efficient caching, automatic invalidation, and background refetching |
| **Client State Management** | Zustand v5 | Lightweight, unopinionated global state management |
| **Data Visualizations** | Recharts 3 + Cytoscape + Three.js | High-density charts, ROC curves, confusion matrices, and 3D graphs |
| **Animations** | Framer Motion 11 | Smooth micro-animations and zero-layout-shift transitions |
| **Real-Time Feed** | Native WebSocket client | Low-latency duplex communication with heartbeat and auto-reconnect |

---

## 3. Security, Privacy & Compliance Invariants

1. **Zero Raw PII Exposure**: All customer identifiers and payment account numbers rendered across views are strictly masked with type-salted HMAC-SHA256 privacy tokens.
2. **Defensive Content Security Policy**: Configured in both `vercel.json` and `nginx.conf` to enforce strict script origin policies, prevent clickjacking (`frame-ancestors 'none'`), and mandate HTTPS (`Strict-Transport-Security`).
3. **Zero-Vulnerability Dependency Governance**: Dependency trees are audited against CVE advisories. Package overrides enforce patched upstream libraries (e.g. `undici ^8.11.2` for GHSA-3wwx-pv8p-q78v, resulting in `0 vulnerabilities`).
4. **React Rules of Hooks Strict Adherence**: Conditional hooks and early-return anti-patterns are strictly eliminated to ensure stable rendering cycles under high-frequency WebSocket updates.

---

## 4. Verification & Testing Standards

The frontend enforces strict quality gates across multiple testing dimensions:

```
┌────────────────────────────────────────────────────────────────────────┐
│                     FRONTEND VERIFICATION PIPELINE                     │
│                                                                        │
│   [ Vitest Suite ]       [ TypeScript Engine ]     [ ESLint Engine ]   │
│   (355 / 355 Tests)      (tsc -b Clean Build)      (0 Errors)          │
│          │                         │                        │          │
│          └─────────────────────────┼────────────────────────┘          │
│                                    ▼                                   │
│                     ┌─────────────────────────────┐                    │
│                     │ Vite Production Bundle Pass │                    │
│                     │ (3,108 Modules Transformed) │                    │
│                     └─────────────────────────────┘                    │
└────────────────────────────────────────────────────────────────────────┘
```

* **Vitest Unit & Component Suite**: 355 tests across 86 test files validating UI rendering, hook state lifecycles, and API query client contracts.
* **Static Type Checking**: `tsc -b` runs with zero compilation errors across all source files and test suites.
* **Production Linting**: `eslint .` enforces zero errors across React Hooks, JSX syntax, and TypeScript invariants.
* **End-to-End Playwright Automation**: Multi-device viewport validation (Desktop 1440, Laptop 1280), visual snapshot regression, and Axe-core accessibility compliance.

---

## 5. Development & Production Operations

### Local Development
```bash
# Install dependencies
npm install

# Start Vite development server with hot module replacement
npm run dev
```

### Production Build & Linting
```bash
# Type check and build optimized static assets
npm run build

# Run ESLint validation
npm run lint

# Preview production build locally
npm run preview
```

### Automated Testing
```bash
# Run complete Vitest suite
npm test

# Run targeted component or service tests
npm test -- src/components/dashboard/__tests__/StreamingGNNPanel.test.tsx

# Run Playwright end-to-end workflow suite
npm run test:e2e:workflows
```
