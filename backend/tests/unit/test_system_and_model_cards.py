"""Unit tests for institutional Model Card and System Card formalization (Phase 36).

Verifies Hugging Face / ACM FAccT YAML metadata conformance, EU AI Act High-Risk
System Card integrity, 7-dataset cross-referencing, regulatory disclaimers, KaTeX
formatting rules, and exact numerical alignment with golden benchmark artifacts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_CARD_ROOT = REPO_ROOT / "MODEL_CARD.md"
SYSTEM_CARD_ROOT = REPO_ROOT / "SYSTEM_CARD.md"
MODEL_CARD_DOCS = REPO_ROOT / "docs" / "MODEL_CARD.md"
SYSTEM_CARD_DOCS = REPO_ROOT / "docs" / "SYSTEM_CARD.md"
CLAIM_REGISTRY_PATH = REPO_ROOT / "benchmarks" / "claim_registry.json"
ERROR_STRAT_PATH = REPO_ROOT / "benchmarks" / "results" / "raw" / "error_stratification_analysis.json"
DEMO_AUDIT_PATH = REPO_ROOT / "benchmarks" / "results" / "raw" / "demographic_fairness_audit.json"


def _parse_yaml_frontmatter(content: str) -> dict[str, Any]:
    """Basic YAML frontmatter parser for Model Card metadata."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_text = parts[1].strip()
    result: dict[str, Any] = {}
    current_key = None
    current_list: list[str] = []

    for line in yaml_text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            key, val = line.split(":", 1)
            current_key = key.strip()
            val_clean = val.strip()
            if val_clean:
                result[current_key] = val_clean
            else:
                current_list = []
        elif trimmed.startswith("- ") and current_key:
            item = trimmed[2:].strip()
            current_list.append(item)

    if current_key and current_list:
        result[current_key] = current_list

    return result


class TestModelAndSystemCardFormalization:
    """Verifies existence, structure, governance, and mathematical integrity of cards."""

    def test_model_card_and_system_card_files_exist_at_root_and_docs(self) -> None:
        """Verifies both root and docs mirror files exist and are non-empty."""
        for path in (MODEL_CARD_ROOT, SYSTEM_CARD_ROOT, MODEL_CARD_DOCS, SYSTEM_CARD_DOCS):
            assert path.exists(), f"Card file missing: {path}"
            assert path.stat().st_size > 1000, f"Card file too short: {path}"

    def test_model_card_yaml_frontmatter_conforms_to_hf_standard(self) -> None:
        """Verifies Hugging Face / ACM FAccT YAML frontmatter tags and properties."""
        content = MODEL_CARD_ROOT.read_text(encoding="utf-8")
        meta = _parse_yaml_frontmatter(content)

        assert meta.get("license") == "mit"
        assert meta.get("library_name") == "pytorch"
        assert "tags" in meta
        tags = meta["tags"]
        assert any("federated-learning" in t for t in tags)
        assert any("fraud-detection" in t for t in tags)
        assert any("differential-privacy" in t for t in tags)
        assert any("byzantine-robustness" in t for t in tags)
        assert any("eu-ai-act" in t for t in tags)
        assert any("sr-11-7" in t for t in tags)

        assert "datasets" in meta
        datasets = meta["datasets"]
        for expected_ds in ("paysim", "ieee_cis", "credit_card", "elliptic", "amlsim", "synthaml", "amlnet"):
            assert expected_ds in datasets

        assert "metrics" in meta
        metrics = meta["metrics"]
        for expected_metric in ("roc_auc", "pr_auc", "recall_at_fpr", "disparate_impact_ratio"):
            assert expected_metric in metrics

    def test_model_card_mandatory_sections_and_disclaimers(self) -> None:
        """Verifies mandatory Model Card sections and regulatory disclaimers."""
        content = MODEL_CARD_ROOT.read_text(encoding="utf-8")

        required_sections = [
            "## 1. Model Details",
            "## 2. Intended Use & Scope",
            "## 3. Training & Evaluation Datasets",
            "## 4. Empirical Performance & Statistical Robustness",
            "## 5. Demographic Attribute Availability & Fairness Governance",
            "## 6. Explainability, Interpretability & Transparency",
            "## 7. Model Limitations & Concrete Failure Modes",
            "## 8. Environmental & Computational Footprint",
            "## 9. Verification & Audit Test Suites",
        ]
        for sec in required_sections:
            assert sec in content, f"Missing section in MODEL_CARD.md: {sec}"

        # Regulatory disclaimers
        assert "FEDERAL RESERVE SR 11-7 / OCC 2011-12 FORMAL BIAS GOVERNANCE DISCLAIMER" in content
        assert "EQUAL CREDIT OPPORTUNITY ACT (ECOA / 12 CFR PART 1002) STATUTORY NOTICE" in content
        assert "EU AI ACT (ARTICLE 10(2)-(3)) DATA GOVERNANCE STATEMENT" in content

        # Operational proxy fairness
        assert "EEOC 80% Four-Fifths Rule" in content
        assert "1.1338" in content  # Channel type DIR
        assert "0.9737" in content  # Country corridor DIR
        assert "1.1401" in content  # Merchant tier DIR

    def test_system_card_mandatory_sections_and_governance(self) -> None:
        """Verifies institutional System Card sections and enterprise controls."""
        content = SYSTEM_CARD_ROOT.read_text(encoding="utf-8")

        required_sections = [
            "## 1. System Overview & Executive Summary",
            "## 2. Clean Architecture & Layer Responsibilities",
            "## 3. Regulatory Compliance & Institutional Governance",
            "## 4. Human-in-the-Loop & Case Management Workflows",
            "## 5. Multi-Tenant Privacy, Security & Cryptographic Perimeter",
            "## 6. Adversarial Robustness, Poisoning Defenses & Fail-Safes",
            "## 7. Deployment Topology, Performance & Scalability",
            "## 8. Verification & Continuous Validation",
        ]
        for sec in required_sections:
            assert sec in content, f"Missing section in SYSTEM_CARD.md: {sec}"

        # Clean architecture layers
        assert "Domain Layer" in content
        assert "Application Layer" in content
        assert "Infrastructure Layer" in content
        assert "Presentation Layer" in content

        # Governance & security
        assert "High-Risk AI System" in content
        assert "Four-Eyes Dual Control" in content
        assert "Zero Raw PII" in content
        assert "Rényi Differential Privacy" in content
        assert "TenSEAL CKKS" in content
        assert "Curve25519" in content
        assert "Bulyan" in content
        assert "Spectral Defense" in content
        assert "1,394.7 requests/second" in content
        assert "15.01 seconds" in content  # RTO

    def test_model_and_system_card_dataset_consistency(self) -> None:
        """Verifies that all 7 benchmark datasets are referenced consistently."""
        mc_content = MODEL_CARD_ROOT.read_text(encoding="utf-8")
        sc_content = SYSTEM_CARD_ROOT.read_text(encoding="utf-8")

        benchmark_datasets = [
            "paysim",
            "ieee_cis",
            "credit_card",
            "elliptic",
            "amlsim",
            "synthaml",
            "amlnet",
        ]
        for ds in benchmark_datasets:
            assert ds in mc_content, f"Dataset {ds} missing from MODEL_CARD.md"

        # Verify that demographic audit artifact exists and confirms 0 / 10
        assert DEMO_AUDIT_PATH.exists()
        with open(DEMO_AUDIT_PATH, encoding="utf-8") as f:
            demo_data = json.load(f)
        assert demo_data["total_datasets_audited"] == 7
        assert demo_data["datasets_with_demographics"] == 0

        # System card refers to the 7-dataset audit
        assert "7-dataset audit" in sc_content or "seven core benchmark datasets" in sc_content or "7 standard benchmark datasets" in sc_content

    def test_model_and_system_card_cross_references_validity(self) -> None:
        """Verifies markdown file paths referenced in the cards resolve on disk."""
        for card_path in (MODEL_CARD_ROOT, SYSTEM_CARD_ROOT):
            content = card_path.read_text(encoding="utf-8")
            # Find relative file links like [`docs/architecture.md`](docs/architecture.md)
            matches = re.findall(r'\[.*?\]\(((?!http|#|mailto)[^\)]+)\)', content)
            for target_rel in matches:
                target_clean = target_rel.split("#")[0].strip()
                if not target_clean:
                    continue
                resolved = (card_path.parent / target_clean).resolve()
                assert resolved.exists(), (
                    f"Broken link in {card_path.name}: '{target_rel}' -> '{resolved}' does not exist"
                )

    def test_katex_math_formatting_integrity(self) -> None:
        """Ensures math blocks comply with KaTeX rules (isolated $$, no unescaped underscores)."""
        for card_path in (MODEL_CARD_ROOT, SYSTEM_CARD_ROOT, MODEL_CARD_DOCS, SYSTEM_CARD_DOCS):
            content = card_path.read_text(encoding="utf-8")

            # Check that $$ display math blocks are preceded and followed by blank lines
            lines = content.splitlines()
            for idx, line in enumerate(lines):
                if line.strip() == "$$" and 0 < idx < len(lines) - 1:
                    prev_line = lines[idx - 1].strip()
                    next_line = lines[idx + 1].strip()
                    # An isolated $$ block has blank lines around it (or closing $$)
                    # We ensure it's not immediately next to a list bullet without a blank line
                    if prev_line.startswith(("- ", "* ", "1. ")) or next_line.startswith(("- ", "* ", "1. ")):
                        msg = f"In {card_path.name}: line {idx+1} has $$ immediately touching a list bullet"
                        raise AssertionError(msg)

            # Check for unescaped underscores inside \text{...} or \mathrm{...}
            bad_underscores = re.findall(r'\\(?:text|mathrm)\{[^}]*_[^}]*\}', content)
            assert len(bad_underscores) == 0, (
                f"In {card_path.name}: found unescaped underscore in text/mathrm: {bad_underscores}"
            )

    def test_failure_mode_parity_with_error_analysis_dossier(self) -> None:
        """Verifies FM-01 to FM-04 in MODEL_CARD.md align with raw error stratification data."""
        content = MODEL_CARD_ROOT.read_text(encoding="utf-8")

        assert ERROR_STRAT_PATH.exists()
        with open(ERROR_STRAT_PATH, encoding="utf-8") as f:
            error_data = json.load(f)

        assert "failure_mode_dossier" in error_data
        fm_ids = [m["mode_id"] for m in error_data["failure_mode_dossier"]]
        assert "FM-01" in fm_ids
        assert "FM-02" in fm_ids
        assert "FM-03" in fm_ids
        assert "FM-04" in fm_ids

        for fm_id in ("FM-01", "FM-02", "FM-03", "FM-04"):
            assert fm_id in content, f"Missing failure mode {fm_id} in MODEL_CARD.md"
        assert "47.67% FNR" in content
        assert "14.98% FPR" in content
        assert "90.00% FNR" in content
        assert "43.48% FNR" in content
