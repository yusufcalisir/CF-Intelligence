"""Unit tests for Authoritative Dataset Cards and Governance Specifications.

Validates that DATASETS.md (root) and docs/DATASETS.md adhere to Hugging Face
Datasets, ACM FAccT Data Cards, and Gebru et al. (2021) Datasheets for
Datasets standards, ensuring complete provenance, licensing terms, class
imbalance metrics, synthetic vs real designations, known biases, and statutory
demographic minimization disclaimers across all 7 benchmark datasets.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_DATASETS_MD = REPO_ROOT / "DATASETS.md"
DOCS_DATASETS_MD = REPO_ROOT / "docs" / "DATASETS.md"

DATASET_KEYS = [
    "paysim",
    "ieee_cis",
    "credit_card",
    "elliptic",
    "amlsim",
    "synthaml",
    "amlnet",
]


def test_dataset_card_files_exist_at_root_and_docs():
    """Verify DATASETS.md exists at both repository root and docs/ with mirrored content."""
    assert ROOT_DATASETS_MD.exists(), "Root DATASETS.md must exist."
    assert DOCS_DATASETS_MD.exists(), "docs/DATASETS.md must exist."

    root_text = ROOT_DATASETS_MD.read_text(encoding="utf-8")
    docs_text = DOCS_DATASETS_MD.read_text(encoding="utf-8")

    assert len(root_text) > 10000, "Root DATASETS.md must contain comprehensive documentation."
    assert len(docs_text) > 10000, "docs/DATASETS.md must contain comprehensive documentation."
    assert root_text == docs_text, "docs/DATASETS.md must be an exact mirror of root DATASETS.md."


def test_all_seven_benchmark_datasets_have_dedicated_cards():
    """Verify all seven canonical benchmark datasets have dedicated card sections."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    expected_headings = [
        "PaySim Mobile Money Fraud (`paysim`)",
        "IEEE-CIS E-Commerce Fraud Detection (`ieee_cis`)",
        "European Credit Card Fraud (`credit_card`)",
        "Elliptic Bitcoin Transaction Graph (`elliptic`)",
        "IBM Research AMLSim (`amlsim`)",
        "SynthAML Danish Commercial AML (`synthaml`)",
        "AMLNet Australian AUSTRAC AML (`amlnet`)",
    ]

    for heading in expected_headings:
        assert heading in content, f"Missing dedicated section for: {heading}"


def test_dataset_provenance_and_licensing_completeness():
    """Verify complete academic citation, provenance, and copyright licensing for each dataset."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    licensing_assertions = [
        ("paysim", "CC BY-SA 4.0"),
        ("ieee_cis", "Competition License"),
        ("credit_card", "Open Database License (ODbL) v1.0"),
        ("elliptic", "CC BY 4.0"),
        ("amlsim", "Apache License 2.0"),
        ("synthaml", "CC BY 4.0"),
        ("amlnet", "CC BY-NC 4.0"),
    ]

    for key, license_name in licensing_assertions:
        assert license_name in content, f"Licensing term '{license_name}' for '{key}' not found in card."

    citation_terms = [
        "Blekinge Institute of Technology",  # PaySim
        "Vesta Corporation",  # IEEE-CIS
        "Université Libre de Bruxelles",  # Credit Card
        "MIT-IBM Watson AI Lab",  # Elliptic
        "IBM Research AI",  # AMLSim
        "Spar Nord Bank",  # SynthAML
        "Griffith University",  # AMLNet
    ]

    for term in citation_terms:
        assert term in content, f"Institutional attribution '{term}' not found in card."


def test_dataset_scale_and_class_balance_accuracy():
    """Verify documented transaction volumes, fraud counts, and imbalance rates match empirical data."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    empirical_claims = [
        ("PaySim scale", "6{,}362{,}620", "8{,}213", "0.129"),
        ("IEEE-CIS scale", "590{,}540", "20{,}663", "3.498"),
        ("Credit Card scale", "284{,}807", "492", "0.172"),
        ("Elliptic scale", "203{,}769", "4{,}545", "9.76"),
        ("AMLSim scale", "1{,}323{,}234", "1{,}719", "0.129"),
        ("SynthAML scale", "5{,}000", "425", "8.500"),
        ("AMLNet scale", "1{,}090{,}000", "1{,}526", "0.140"),
    ]

    for label, total_tx, fraud_cnt, prev in empirical_claims:
        assert total_tx in content, f"{label} volume {total_tx} not documented in DATASETS.md"
        assert fraud_cnt in content, f"{label} fraud count {fraud_cnt} not documented in DATASETS.md"
        assert prev in content, f"{label} prevalence {prev} not documented in DATASETS.md"


def test_synthetic_vs_real_designations_present():
    """Verify each dataset has explicit Real vs Synthetic designations."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    designation_assertions = [
        ("paysim", "Synthetic Agent-Based Mobile Money Simulation"),
        ("ieee_cis", "Real Production E-Commerce Card-Not-Present"),
        ("credit_card", "Real Anonymized Consumer Credit Card Transactions"),
        ("elliptic", "Real Public Bitcoin Blockchain Directed Acyclic"),
        ("amlsim", "Synthetic Multi-Agent Banking Network Graph Simulator"),
        ("synthaml", "Real-Topology Synthetic AML Benchmark"),
        ("amlnet", "AUSTRAC Knowledge-Guided Multi-Agent Synthetic AML Benchmark"),
    ]

    for key, designation in designation_assertions:
        assert designation in content, f"Designation '{designation}' for '{key}' not found in card."


def test_known_biases_and_limitations_documented():
    """Verify each dataset contains a dedicated Data Hygiene, Biases & Limitations section."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    bias_subheadings = content.count("Data Hygiene, Biases & Limitations")
    assert bias_subheadings == 8, (
        f"Expected exactly 8 'Data Hygiene, Biases & Limitations' sections, found {bias_subheadings}."
    )

    specific_bias_factors = [
        "Selective Fraud Typologies",  # PaySim (TRANSFER / CASH_OUT only)
        "High Missingness",  # IEEE-CIS (>200 features with >50% nulls)
        "Short Observation Horizon",  # Credit Card (48 hours only)
        "High Background Ratio",  # Elliptic (77.15% unknown labels)
        "Rigid Geometric Typologies",  # AMLSim (cycle and fan-in templates)
        "Investigation Filter Conditioning",  # SynthAML (only flagged alerts)
        "Threshold Boundary Artifacts",  # AMLNet ($8,500-$9,950 structuring cluster)
        "Simulated Cross-Bank Rails",  # CFI-CrossBank-01 (deterministic multi-agent orchestration)
    ]

    for factor in specific_bias_factors:
        assert factor in content, f"Specific bias factor '{factor}' not documented in limitations."


def test_zero_demographic_pii_statutory_table_present():
    """Verify Section 5 contains the 0/10 protected demographic attributes table and statutory basis."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    assert "PROTECTED DEMOGRAPHIC ATTRIBUTE SCAN INVARIANT" in content
    assert "GDPR Article 9" in content
    assert "ECOA Regulation B (12 CFR Part 1002)" in content
    assert "Federal Reserve SR 11-7" in content

    statutory_categories = [
        "Age",
        "Gender / Sex",
        "Race / Ethnicity",
        "Religion / Creed",
        "Marital Status",
        "Nationality / Origin",
        "Sexual Orientation",
        "Disability Status",
        "Genetic / Biometric",
        "Socioeconomic Status",
    ]

    for category in statutory_categories:
        assert category in content, f"Statutory category '{category}' not found in scan invariant table."

    assert "EXCLUDED [OK]" in content
    assert content.count("EXCLUDED [OK]") == 10, "All 10 demographic attributes must be certified EXCLUDED [OK]."


def test_dataset_card_math_and_cross_reference_integrity():
    """Verify KaTeX display math block isolation and reachability of referenced test files."""
    content = ROOT_DATASETS_MD.read_text(encoding="utf-8")

    # Verify display math block ($$...$$) isolation
    display_math_blocks = re.findall(r"\n\n\$\$(.*?)\$\$\n\n", content, re.DOTALL)
    assert len(display_math_blocks) >= 4, (
        f"Expected at least 4 display math blocks with blank line isolation, found {len(display_math_blocks)}."
    )

    # Verify referenced verification test files exist
    referenced_test_files = [
        "backend/tests/unit/test_dataset_cards.py",
        "backend/tests/unit/test_real_dataloaders.py",
        "backend/tests/unit/test_paysim_loader.py",
        "backend/tests/unit/test_creditcard_loader.py",
        "backend/tests/unit/test_synthaml_loader.py",
        "backend/tests/unit/test_amlnet_loader.py",
        "backend/tests/unit/test_demographic_fairness_audit.py",
        "backend/tests/unit/test_split_isolation.py",
        "backend/tests/unit/test_feature_leakage.py",
    ]

    for rel_path in referenced_test_files:
        full_path = REPO_ROOT / rel_path
        assert full_path.exists(), f"Referenced test file does not exist on disk: {rel_path}"
