# CF-Intelligence Domain Correctness Audits

> **Governing Repository Standard:** [`.agents/AGENTS.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/.agents/AGENTS.md) ("Never Fabricate Runtime Truth")  
> **Master Technical Certification Baseline:** [`CFI-CERT-MASTER-2026-V1`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/technical_certification.md)  
> **Platform Version:** `v2.4.0`

---

## Overview

The `audit/correctness/` directory contains deep, domain-specific technical correctness audits across the 10 functional subsystems of the **CF-Intelligence** platform.

Each subsystem audit includes:
1. **Capability Inventory**: Complete catalog of domain operations, API surfaces, and parameters.
2. **Execution Map / Flow Matrix**: Dataflow from user action to domain logic, models, and persistence.
3. **Invariant Ledger**: Formal mathematical and system invariants enforced across the subsystem.
4. **Findings & Remediation Ledger**: Itemized list of defects identified, repaired, and revalidated.
5. **Formal Markdown Report**: Executive narrative, architecture reconstruction, and verification proof.

---

## Subsystem Navigation Directory

### 1. Business & Case Management ([`business/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/business))
- **Report:** [`business_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/business/business_correctness_report.md)
- **Key Artifacts:** `business_capability_inventory.json`, `business_execution_map.json`, `business_invariants.json`, `state_machine_contracts.json`, `regulatory_capability_matrix.json`, `role_permission_matrix.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Deterministic case lifecycle state machines, role-based permission gates (Investigator, Compliance Officer, Admin), side-effect isolation, CAS concurrency, immutable audit logging.

### 2. Byzantine Defenses ([`byzantine/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/byzantine))
- **Report:** [`byzantine_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/byzantine/byzantine_correctness_report.md)
- **Key Artifacts:** `algorithm_inventory.json`, `attack_inventory.json`, `composition_matrix.json`, `execution_map.json`, `invariants.json`, `precondition_matrix.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Mathematical validity of Krum, Multi-Krum, Bulyan, Coordinate-wise Trimmed Mean, and Coordinate-wise Median; strict adversary fraction limits ($\alpha < \frac{1}{2}$ for Trimmed Mean/Median, $\alpha < \frac{1}{3}$ for Krum); zero synthetic bypasses under adversarial gradient injection.

### 3. Data Connectors & Ingestion ([`data/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/data))
- **Report:** [`data_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/data/data_correctness_report.md)
- **Key Artifacts:** `connectors_inventory.json`, `data_flow_map.json`, `data_invariants.json`, `feature_lineage.json`, `schema_contracts.json`, `temporal_semantics.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Strict Great Expectations (1.x) validation schemas, temporal ordering preservation (no future data leakage in training folds), explicit fail-closed on missing dataset files, feature provenance integrity.

### 4. Explainability & Attribution ([`explainability/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/explainability))
- **Report:** [`explainability_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/explainability/explainability_correctness_report.md)
- **Key Artifacts:** `capability_inventory.json`, `execution_map.json`, `invariants.json`, `model_binding_matrix.json`, `output_semantics.json`, `background_semantics.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Explicit architectural separation of SLA fast-path heuristic attribution from deep SHAP `KernelExplainer`/`TreeExplainer`; zero heuristic outputs masquerading as SHAP values; non-negative attributions where axiomatically bounded.

### 5. Federated Learning Engine ([`fl/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/fl))
- **Report:** [`fl_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/fl/fl_correctness_report.md)
- **Key Artifacts:** `execution_map.json`, `invariants.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Round orchestration determinism, weight tensor shape preservation, client sample weighting, Dirichlet non-IID partitioning preservation, convergence tracking from actual local training rounds.

### 6. Frontend Contracts & UI State ([`frontend/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/frontend))
- **Report:** [`frontend_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/frontend/frontend_correctness_report.md)
- **Key Artifacts:** `frontend_surface_inventory.json`, `frontend_contract_map.json`, `frontend_state_invariants.json`, `query_cache_contracts.json`, `frontend_findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Elimination of client-side random walks (`RTF-0001`); explicit warning badges when running offline sandboxes; truthful backend connection error handling; query cache eviction consistency; TypeScript strictness.

### 7. Graph Intelligence ([`graph/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/graph))
- **Report:** [`graph_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/graph/graph_correctness_report.md)
- **Key Artifacts:** `capability_inventory.json`, `execution_map.json`, `invariants.json`, `gnn_semantics.json`, `identity_semantics.json`, `temporal_semantics.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Strict temporal edge windowing (resolution of historical `GRAPH-0004` defect); temporal decay functions monotonic with transaction timestamp; GNN embeddings derived exclusively from valid temporal subgraphs.

### 8. Model Inference & Serving ([`model_inference/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/model_inference))
- **Report:** [`model_inference_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/model_inference/model_inference_correctness_report.md)
- **Key Artifacts:** `model_inventory.json`, `model_lifecycle_map.json`, `feature_schema_matrix.json`, `metric_semantics.json`, `invariants.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Strict feature vector dimensions and typing; fail-closed behavior on NaN/Inf inputs; model version lineage tracking; zero synthetic default probabilities upon inference errors.

### 9. Privacy Guarantees & Differential Privacy ([`privacy/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/privacy))
- **Report:** [`privacy_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/privacy/privacy_correctness_report.md)
- **Key Artifacts:** `execution_map.json`, `invariants.json`, `threat_model_matrix.json`, `findings.json`, `remediation_ledger.json`.
- **Core Invariants:** Renyi Differential Privacy (RDP) accounting; $L_2$ gradient clipping bounds; Gaussian noise injection; budget exhaustion ($\epsilon > \epsilon_{\max}$) terminates training with fail-closed status; truthful labeling of software TEE emulation vs. hardware SGX.

### 10. Master System Lifecycle & Certification ([`system/`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system))
- **Primary Reports:**
  - [`technical_certification.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/technical_certification.md): Master technical certification baseline (`CFI-CERT-MASTER-2026-V1`).
  - [`lifecycle_integrity_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/lifecycle_integrity_report.md): Compound lifecycle and distributed failure state transitions.
  - [`distributed_correctness_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/distributed_correctness_report.md): Distributed consensus, multi-worker race handling, and persistence fallbacks.
  - [`system_verification_report.md`](file:///c:/Users/Yusuf/Desktop/projects/Privacy-preserving%20cross-bank%20fraud%20detection%20using%20Federated%20Learning/audit/correctness/system/system_verification_report.md): Verification matrix and empirical reconciliation.
- **Key Artifacts:** `evidence_inventory.json`, `fault_injection_matrix.json`, `known_limitations.json`, `runtime_path_map.json`, `system_invariant_matrix.json`, `verified_capabilities.json`, `adversarial_composition_matrix.json`.
- **Core Invariants:** Master repository integrity; failure injection recovery without state corruption; separation of authoritative vs. superseded evidence.
