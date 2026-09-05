# 🎨 Commercial Multi-Role Web Management Console Specification

The Commercial Web Management Console delivers a premium glassmorphism UI with role-tailored view switching (`EXECUTIVE`, `COMPLIANCE_OFFICER`, `ML_ENGINEER`, `FRAUD_INVESTIGATOR`).

---

## 📌 Multi-Role Tailored Dashboards

| Enterprise Persona | Primary Focus | Visible Widgets & Capabilities |
| :--- | :--- | :--- |
| **`EXECUTIVE`** | ROI & Global Health | ROI charts, total fraud dollars prevented, SLA compliance summary, consortium node quorum status. |
| **`COMPLIANCE_OFFICER`** | Governance & Audit | Differential Privacy budget gauges ($\epsilon$), SAR XML filing manager, Four-Eyes dual supervisor approvals, GDPR Art. 17 audit logs. |
| **`ML_ENGINEER`** | MLOps, Defense & Data | PSI feature drift dashboard, 5-stage model lifecycle, **Interactive Chaos & Byzantine Attack Injector**, **Drag-and-Drop Dataset Ingestion Studio** (GE 1.x contract gating), FL retraining. |
| **`FRAUD_INVESTIGATOR`** | Case Investigation | 6-stage case workbench, interactive entity graphs, SHAP feature attributions, real-time 500 tx/s smurfing burst telemetry, alert feed. |

---

## 💎 Design System & Glassmorphism Theme

- **Color Palette**: Dark Slate (`#0B0F19`), Vibrant Cyan (`#06B6D4`), Emerald Green (`#10B981`), Crimson Alert (`#EF4444`), Indigo Accent (`#6366F1`).
- **Glassmorphism Styling**: `backdrop-filter: blur(16px)`, `background: rgba(15, 23, 42, 0.75)`, subtle 1px border highlights (`border: 1px solid rgba(255, 255, 255, 0.1)`).
- **Typography**: Inter / Outfit modern sans-serif.
- **Zero Layout Shift (CLS)**: Fixed dimensions (`min-h-[44px]`), static border placeholders, and overflow prevention across all mobile viewports.

---

## 🧭 Enterprise Workflows & Navigation Synergy

1. **SaaS Landing Page (`/`)**:
   - **Interactive Browser Mockup (`InteractiveDashboardPreview`)**: Includes live interactive tabs for Telemetry, GNN Graph, Privacy, BFT, SAR, **Chaos Attack Defense** (with explicit "Simulated Demo Proxy" labeling for real-time resilience telemetry), and **Dataset Ingestion**. Direct deep links route to `/scenarios` and `/operations?openIngest=true`.
   - **Platform Modules & Specs**: Lists 12 production modules with complete SLAs, compliance mappings, and live tensor signatures.

2. **Consortium Dashboard (`/dashboard`)**:
   - **Enterprise Quick Actions Bar**: Rapid navigation cards for Chaos Simulator, Custom Ingestion Studio, and On-Premises Docker Stack.
   - **Live Health Metrics**: Participating institutions, data drift status, and global AUC-ROC gauges.

3. **Bank Node Onboarding Wizard (`/onboarding`)**:
   - **5-Stage Setup**: Legal profile, region selection, mTLS X.509 key generation, YAML connector configuration, and Quorum activation.
   - **Direct Data Ingestion**: Step 5 provides an immediate "Import Bank Transactions (CSV / Parquet)" modal launcher for new bank nodes.

4. **Live Operations & Ingestion (`/operations`)**:
   - Deep linking support via `?openIngest=true` to immediately trigger the Zero-PII sanitization and Great Expectations data contract studio.
