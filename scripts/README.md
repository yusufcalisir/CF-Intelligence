# Platform Developer Tools, Benchmarks & Operational Automation (`scripts/`)

This directory houses the unified operational CLI, benchmarking harnesses, security and regulatory compliance exporters, code integrity scanners, and deployment verification tools for the **Collaborative Fraud Intelligence Platform (CF-Intelligence)**.

---

## 1. Scripts Taxonomy & Directory Index

The 32 scripts are organized into five primary engineering domains:

```text
scripts/
├── [1. Master Runners & Orchestration]
│   ├── run_all_tests.py                 # Master unified test runner across all test suites (Frontend, Backend, Contracts)
│   ├── run_all_verifications.py         # Master runner for all 18 scientific verification audit modules
│   └── cfi_cli.py                       # Self-service bank onboarding & consortium integration CLI
│
├── [2. Benchmarking & Simulation Engines]
│   ├── run_benchmark.py                 # 9-Configuration architectural evaluation runner (C1–C9 matrix)
│   ├── benchmark_prepare_datasets.py    # Synthetic dataset generator & real-world dataset preprocessor
│   ├── run_enterprise_stress_test.py    # High-throughput ISO 20022 payment stream stress test (>38k tx/s)
│   ├── realtime_benchmark.py            # Sub-100ms in-process ASGI scoring latency benchmark
│   ├── run_elliptic_benchmark.py        # Real Elliptic Bitcoin AML transaction graph benchmark runner
│   ├── run_fl_synthetic_benchmark.py    # Multi-bank federated learning convergence benchmark
│   ├── run_abac_benchmark.py            # Attribute-Based Access Control authorization engine benchmark
│   ├── transaction_stream.py            # Live real-time transaction streaming simulation service
│   ├── locustfile.py                    # Distributed Locust user load testing suite
│   ├── run_load_test.py                 # Asynchronous HTTP & WebSocket load tester with SLA reports
│   ├── download_real_benchmarks.py      # Automated downloader for IEEE-CIS, PaySim, and Elliptic datasets
│   ├── etl_dataset_pipeline.py          # Pandera data contract ETL pipeline with distribution bounds
│   └── generate_plots.py                # Generates publication-ready PR-AUC, ROC-AUC & convergence charts
│
├── [3. Quality, Integrity & Security Scanners]
│   ├── codebase_integrity_scanner.py    # Autonomous 22-vector zero-mock and dead-code scanner
│   ├── audit_api_contracts.py           # Deep bidirectional contract auditor (FastAPI OpenAPI <-> TypeScript)
│   ├── capture_openapi_snapshot.py      # Captures reference OpenAPI v3.1 schema snapshot
│   ├── run_coverage_audit.py            # 4-Tier branch coverage auditor (Statements, Branches, Functions, Lines)
│   ├── run_mutation_tests.py            # Mutation testing suite driver for frontend and backend
│   ├── ast_mutation_engine.py           # AST mutation engine injecting arithmetic, boundary & logic mutations
│   └── chaos_harness.py                 # Chaos engineering harness (network delay, packet drop, node crash)
│
├── [4. Security, Governance & Regulatory Compliance]
│   ├── generate_secrets.py              # Cryptographically secure 256-bit secret generator for .env
│   ├── generate_sbom.py                 # Automated CycloneDX 1.5 & SPDX JSON SBOM generator with pip-audit
│   ├── export_compliance_report.py      # EU AI Act & Article 13/14 compliance report generator
│   ├── init_vault_pki.py                # HashiCorp Vault mTLS intermediate CA & certificate provisioning
│   └── setup_cloudflare_waf.py          # Cloudflare WAF perimeter rules, rate limits & TLS 1.3 setup
│
└── [5. Deployment & Cloud Verification]
    ├── verify_docker_deployment.py      # Automated Docker Compose pre-flight & runtime verification
    ├── validate_k8s_manifests.py        # Rendered Helm manifest dry-run validator (kubectl apply --dry-run=client)
    └── production_smoke_test.py         # Post-deployment end-to-end smoke test validating all live endpoints
```

---

## 2. Key Developer Workflows

### 2.1 Running the Full Test Suite (`run_all_tests.py`)
```bash
# Run all core test suites (Frontend + Backend + Scientific Verification)
python scripts/run_all_tests.py

# Run specific individual suites
python scripts/run_all_tests.py --frontend      # Vitest unit & E2E tests
python scripts/run_all_tests.py --backend       # Pytest unit, integration & chaos tests
python scripts/run_all_tests.py --verification  # 18-module scientific verification suite
python scripts/run_all_tests.py --contracts     # Hardhat EVM smart contract suite
python scripts/run_all_tests.py --coverage      # 4-tier branch coverage audit with 75% gate
python scripts/run_all_tests.py --all           # Everything including Playwright visual & mutation tests
```

### 2.2 Benchmarking & Stress Testing
```bash
# 9-Configuration matrix evaluation (ROC-AUC, PR-AUC, F1, DP budget consumption)
python scripts/run_benchmark.py --samples 5000 --rounds 10

# High-throughput ISO 20022 payment stream stress test (pacs.008 messages)
python scripts/run_enterprise_stress_test.py --banks 5 --target-tps 10000 --duration 10

# Real-time sub-100ms inference latency benchmark
python scripts/realtime_benchmark.py --concurrency 50 --requests 1000

# Elliptic Bitcoin AML dataset benchmark
python scripts/run_elliptic_benchmark.py
```

### 2.3 Codebase Quality, Anti-Mock & Contract Audits
```bash
# Autonomous 22-vector anti-mock & integrity scanner
python scripts/codebase_integrity_scanner.py --all --strict

# Bidirectional API contract audit (OpenAPI schemas vs TypeScript types)
python scripts/audit_api_contracts.py

# AST mutation testing suite (Backend Python & Frontend TypeScript mutants)
python scripts/run_mutation_tests.py
```

### 2.4 Production Deployment Verification
```bash
# Validate Docker Compose configuration, multi-stage builds & health probes
python scripts/verify_docker_deployment.py

# Validate all Kubernetes Helm manifests via kubectl client dry-run (39 resources)
python scripts/validate_k8s_manifests.py --all

# Post-deployment live cluster smoke test
python scripts/production_smoke_test.py --base-url http://localhost:8000
```

### 2.5 Bank Onboarding CLI (`cfi_cli.py`)
```bash
# Initialize bank configuration scaffold
python scripts/cfi_cli.py init --bank-id bank_alpha --coordinator coordinator.cfi.internal:50051

# Generate mTLS Certificate Signing Request (CSR)
python scripts/cfi_cli.py cert generate-csr --bank-id bank_alpha --output-dir ./certs

# Verify coordinator connectivity and mTLS handshake
python scripts/cfi_cli.py test-connection --host coordinator.cfi.internal --port 50051

# Run local self-service integration sandbox
python scripts/cfi_cli.py sandbox run --transactions 5000
```

---

## 3. Maintenance & Standards

- All scripts adhere to strict PEP 8 formatting validated by `ruff check scripts/`.
- No mock or dummy fallbacks: all benchmarks and utilities execute authentic computational pipelines.
- Zero hardcoded production secrets: all cryptographic keys and credentials are dynamically sourced from environment variables.
