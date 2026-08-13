# Workspace Agent Rules & Operational Guidelines

## 1. Git & Remote Deployment Policy
- **NO Automatic Pushing or Deployment**: Do NOT execute `git push`, `deploy_hf.bat`, or any command that pushes code to remote repositories (GitHub, Hugging Face, Vercel) automatically.
- **Explicit Approval Required**: Always wait for an explicit user request or confirmation (e.g., *"pushla"*, *"deploy"*) before pushing commits or running remote deployment scripts.
- **Natural Engineering Commit Messages**: Commit messages must be concise, natural, single-line technical messages written like a software engineer. Never use AI boilerplate, phase markers (e.g., `Phase 1/4`, `Phase 2`, `(Phase 3/4)`), or robotic multi-paragraph bullet lists in git commit messages. Focus strictly on the exact technical change made.

## 2. Deep System-Wide Synchronization (Code, Docs, Verification & UI)
Whenever a feature, signal, API endpoint, parameter, algorithm, schema, or UI component is added, modified, or REMOVED/DEPRECATED:
- **Comprehensive (Non-Superficial) Updates**: Documentation updates must NEVER be cosmetic or superficial (e.g., just adding a heading or 2 lines of generic text). If a module or feature is added, write complete technical specifications, code/diagram examples, and benchmark parameters. If a feature or endpoint is removed or refactored, thoroughly prune and purge all stale references across `README.md`, `docs/*.md`, `verification/*.md`, and UI specifications.
- **`README.md` Synchronization**: Keep `README.md` fully in sync with the codebase state: update feature matrices, API blueprints, directory structure tree, test count metrics, and benchmark performance tables.
- **`docs/` Technical Specifications**: Update matching technical architectural markdown documents inside `docs/` (e.g., `architecture.md`, `system_design.md`, `threat_model.md`, `aml-platform.md`) to reflect exact system design and data flows.
- **`verification/` Scientific Audit Parity**: Maintain 100/100 scientific audit status across all 17 verification modules. If mathematical formulas, differential privacy parameters ($\epsilon, \delta$), or Byzantine aggregation algorithms change, update the corresponding LaTeX mathematical formulations and scientific audit reports.
- **Full UI & Workflow Coherence**: Ensure changes are coordinated seamlessly across SaaS landing pages (`LandingPage`), launch flow (`PlatformLaunchModal`), investigation dashboards (`InvestigationDashboard`), security controls (`SecurityPage`), observability views (`ObservabilityPage`), and case management (`CaseDetailPage`). The post-demo launch transition must route directly to the target dashboard without flickering intermediate screens.

## 3. End-to-End API & State Contract Integrity
- **Synchronized Frontend-Backend Schemas**: Whenever a backend Pydantic model, WebSocket payload, or FastAPI endpoint contract is modified, immediately update the corresponding frontend TypeScript types, API query client (`frontend/src/api/queries.ts`), and React components.
- **No Orphaned References**: Search and update every invocation site across the codebase whenever a function signature, component prop, or environment variable is modified.

## 4. Targeted Testing & Efficient Verification
- **Module-Specific Testing**: Avoid running the entire 875+ test suite for small, localized edits. Instead, execute targeted `pytest` files for the specific backend module being modified (e.g., `pytest backend/tests/unit/test_bank_onboarding.py`).
- **Targeted Frontend Build Verification**: Verify frontend changes using `npm --prefix frontend run build` (or running Vite check) to ensure zero TypeScript, JSX, or bundling compilation errors.
- **Full Test Suite Validation**: Run the comprehensive test suite (`pytest tests/`) only when performing cross-cutting architectural changes or prior to major release milestones.

## 5. UI/UX, Mobile Responsiveness & Layout Integrity
- **Zero Layout Shift / Jump**: Ensure buttons, tabs, pill selectors, and interactive navigation elements maintain fixed vertical/horizontal dimensions. Dynamic state changes (e.g., active borders, font weight changes) must not cause adjacent UI elements to shift or jump vertically/horizontally (use fixed height bounds like `min-h-[44px]` or static border colors).
- **Mobile First & Overflow Prevention**: All pages and cards must be fully responsive without requiring horizontal window scrolling unless explicitly designed as a scrollable container (e.g., tab bars with `overflow-x-auto no-scrollbar`). Use responsive grid columns (e.g., `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4`) to ensure cards fit cleanly on mobile viewports.
- **Text Truncation & Wrapping**: Use `truncate` or `whitespace-nowrap` appropriately to prevent header text and badge labels from breaking into multi-line wrapped text or overflowing container boundaries.

## 6. Clean Architecture & Security Invariants
- **Layer Independence**: Maintain strict separation between Domain, Application, Infrastructure, and Presentation layers. Dependencies must flow strictly inward.
- **Privacy & Zero Raw PII**: Enforce zero raw PII transmission. Use type-salted HMAC-SHA256 hashing, Differential Privacy budget tracking (Opacus DP), Homomorphic Encryption (TenSEAL CKKS), SGX Enclave isolation, and secure mTLS/ABAC controls at all times.
- **No Superficial Symptom Patches**: Never resolve failing tests or runtime errors by masking symptoms, swallowing exceptions in empty try/except blocks, returning dummy fallbacks, commenting out broken assertions, or deleting failing unit tests. Always diagnose and fix the underlying contract failure.
