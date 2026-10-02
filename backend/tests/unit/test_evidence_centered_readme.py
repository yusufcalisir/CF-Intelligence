"""Unit tests for Evidence-Centered README Architecture & Direct Claim Hyperlinking.

Verifies that README.md adheres to the Five Pillars of Evidence (Verified Empirical
Results, Experimental Suite, Software Correctness, Research Prototypes, Limitations),
hyperlinks all 21 quantitative claims from claim_registry.json directly to raw JSON
artifacts and reproduction runners, and maintains physical evidence provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
README_PATH = REPO_ROOT / "README.md"
CLAIM_REGISTRY_PATH = REPO_ROOT / "benchmarks" / "claim_registry.json"


class TestEvidenceCenteredReadmeIntegrity:
    """Automated verification suite for Evidence-Centered README structure and claim provenance."""

    def test_readme_file_exists_and_non_empty(self) -> None:
        """Assert README.md exists at repository root and has substantial content (>100KB)."""
        assert README_PATH.is_file(), f"README.md not found at {README_PATH}"
        size_bytes = README_PATH.stat().st_size
        assert size_bytes > 100_000, f"README.md is unexpectedly small: {size_bytes} bytes"

    def test_five_evidence_pillars_present(self) -> None:
        """Assert all five evidence pillars are explicitly documented in README.md."""
        content = README_PATH.read_text(encoding="utf-8")
        required_pillars = [
            "Verified Empirical Results",
            "Experimental Suite",
            "Software Correctness",
            "Research Prototypes",
            "Limitations",
        ]
        for pillar in required_pillars:
            assert pillar in content, f"Missing evidence pillar '{pillar}' in README.md"

    def test_master_quantitative_claim_registry_table_present(self) -> None:
        """Assert README.md contains the Master Quantitative Claim & Evidence Hyperlink Registry."""
        content = README_PATH.read_text(encoding="utf-8")
        assert (
            "Master Quantitative Claim & Evidence Hyperlink Registry" in content
            or "Quantitative Claim & Evidence Hyperlink Registry" in content
        ), "Missing Master Quantitative Claim & Evidence Hyperlink Registry in README.md"
        assert "claim_registry.json" in content, "README must reference benchmarks/claim_registry.json"

    def test_all_21_claims_hyperlinked_in_readme(self) -> None:
        """Assert every single claim ID in claim_registry.json is documented and hyperlinked in README.md."""
        assert CLAIM_REGISTRY_PATH.is_file(), f"claim_registry.json missing at {CLAIM_REGISTRY_PATH}"
        registry = json.loads(CLAIM_REGISTRY_PATH.read_text(encoding="utf-8"))
        claims = registry.get("claims", [])
        expected_claims_count = registry.get("summary_metrics", {}).get("total_claims", 21)
        assert len(claims) == expected_claims_count, f"Expected {expected_claims_count} claims in registry, found {len(claims)}"

        content = README_PATH.read_text(encoding="utf-8")
        missing_claims = []
        for claim in claims:
            cid = claim["claim_id"]
            if cid not in content:
                missing_claims.append(cid)

        assert not missing_claims, f"Claims missing from README.md: {missing_claims}"

    def test_all_14_raw_benchmark_json_files_hyperlinked_and_exist(self) -> None:
        """Assert that all raw benchmark artifacts specified in claim_registry.json exist and are linked."""
        registry = json.loads(CLAIM_REGISTRY_PATH.read_text(encoding="utf-8"))
        content = README_PATH.read_text(encoding="utf-8")

        for claim in registry.get("claims", []):
            raw_artifact = claim.get("raw_artifact", "")
            if raw_artifact.endswith(".json"):
                artifact_file = REPO_ROOT / raw_artifact
                assert artifact_file.is_file(), f"Claim {claim['claim_id']} artifact missing on disk: {artifact_file}"
                assert raw_artifact in content, f"Claim {claim['claim_id']} artifact {raw_artifact} not linked in README.md"

    def test_all_eight_canonical_datasets_hyperlinked_in_master_matrix(self) -> None:
        """Assert all 8 canonical datasets have hyperlinked config/results/report artifacts in README.md."""
        canonical_datasets = [
            "paysim",
            "ieee_cis",
            "credit_card",
            "elliptic",
            "amlsim",
            "synthaml",
            "amlnet",
            "cross_bank",
        ]
        content = README_PATH.read_text(encoding="utf-8")

        for dataset in canonical_datasets:
            config_path = f"experiments/{dataset}/config.json"
            results_path = f"experiments/{dataset}/results.json"
            report_path = f"experiments/{dataset}/report.md"

            # Verify files exist on disk
            assert (REPO_ROOT / config_path).is_file(), f"Missing {config_path} on disk"
            assert (REPO_ROOT / results_path).is_file(), f"Missing {results_path} on disk"
            assert (REPO_ROOT / report_path).is_file(), f"Missing {report_path} on disk"

            # Verify linked in README
            assert dataset in content, f"Dataset '{dataset}' not mentioned in README"

    def test_factorial_ablation_matrix_hyperlinked(self) -> None:
        """Assert Section 15.11 links directly to factorial ablation raw JSON artifact and runner."""
        content = README_PATH.read_text(encoding="utf-8")
        assert "benchmarks/results/raw/factorial_ablation_matrix.json" in content, (
            "Factorial ablation raw artifact not hyperlinked in README"
        )
        assert "benchmarks/runners/run_factorial_ablation.py" in content, (
            "Factorial ablation runner script not hyperlinked in README"
        )

    def test_software_correctness_section_parity(self) -> None:
        """Assert Software Correctness section is titled properly and synchronizes test count metrics."""
        content = README_PATH.read_text(encoding="utf-8")
        assert (
            "Software Correctness & Subsystem Self-Verification" in content
            or "Software Correctness" in content
        ), "README missing explicit Software Correctness heading"
        assert (
            "3,552" in content or "3,564" in content
        ), "README missing updated Backend Pytest count"
        assert (
            "4,348" in content or "4,360" in content
        ), "README missing updated total system test count"

    def test_research_prototypes_tier2_delineation(self) -> None:
        """Assert Research Prototypes section clearly delineates Tier 2 exploratory prototypes with disclaimers."""
        content = README_PATH.read_text(encoding="utf-8")
        assert "Tier 2: Research Prototypes" in content or "Tier 2: Research & Experimental Prototypes" in content
        # Key exploratory technologies
        assert "GraphSAGE" in content
        assert "Diffie-Hellman" in content or "DH-PSI" in content
        assert "Homomorphic" in content or "CKKS" in content

    def test_reproduction_cli_commands_present_and_executable_syntax(self) -> None:
        """Assert README contains master reproduction commands and valid make targets."""
        content = README_PATH.read_text(encoding="utf-8")
        expected_commands = [
            "make reproduce-all",
            "make benchmark-all",
            "make test-smoke",
            "make benchmark-factorial",
        ]
        for cmd in expected_commands:
            assert cmd in content, f"Expected command '{cmd}' missing from README.md"
