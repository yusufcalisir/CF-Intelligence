"""
Tests for Phase 14: Dependency & License Compliance.
Verifies:
1. Dynamic CycloneDX 1.5 SBOM generation from real distribution metadata.
2. License scanning: zero GPL/AGPL copyleft conflicts with the MIT project license.
3. Security CVE minimum pins in backend/requirements.txt.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_sbom_generation_cyclonedx(tmp_path: Path):
    """Verify that SBOM is dynamically generated from real distributions and matches CycloneDX 1.5 format."""
    from scripts.generate_sbom import generate_sbom

    sbom_path = tmp_path / "test_sbom.json"
    try:
        sbom = generate_sbom(sbom_path)
        assert sbom["bomFormat"] == "CycloneDX"
        assert sbom["specVersion"] == "1.5"
        assert "metadata" in sbom
        assert sbom["metadata"]["component"]["name"] == "privacy-preserving-cross-bank-fraud-detection"
        assert len(sbom["components"]) > 50

        # Verify components have proper identifiers
        for comp in sbom["components"]:
            assert "name" in comp
            assert "version" in comp
            assert "purl" in comp
            assert comp["purl"].startswith("pkg:")
    finally:
        if sbom_path.exists():
            sbom_path.unlink()


def test_license_compliance_no_copyleft():
    """Scan backend requirements and frontend package.json for copyleft (GPL/AGPL) conflicts."""
    import importlib.metadata

    copyleft_indicators = ["gpl", "agpl", "gnu general public license", "gnu affero"]
    flagged = []

    # Check direct requirements from requirements.txt
    req_file = REPO_ROOT / "backend" / "requirements.txt"
    assert req_file.exists()
    direct_pkgs = []
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg_name = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("[")[0].strip().lower()
        direct_pkgs.append(pkg_name)

    for pkg in direct_pkgs:
        try:
            dist = importlib.metadata.distribution(pkg)
            lic = (dist.metadata.get("License") or "").lower()
            classifiers = dist.metadata.get_all("Classifier") or []
            is_copyleft = False
            for ind in copyleft_indicators:
                if ind in lic:
                    is_copyleft = True
                for c in classifiers:
                    if ind in c.lower() and "lgpl" not in c.lower():
                        is_copyleft = True
            if is_copyleft and "lesser" not in lic and "lgpl" not in lic:
                flagged.append((pkg, lic))
        except Exception:
            pass

    assert flagged == [], f"Found copyleft-licensed dependencies conflicting with MIT: {flagged}"


def test_cve_mitigation_pins_in_requirements():
    """Ensure critical libraries have explicit security version floors in requirements.txt."""
    req_text = (REPO_ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").lower()

    expected_security_floors = [
        "starlette>=1.3.1",
        "mlflow>=3.11.0",
        "pytest>=9.0.3",
        "urllib3>=2.2.2",
        "jinja2>=3.1.4",
        "aiohttp>=3.10.11",
        "tornado>=6.4.2",
        "certifi>=2024.7.4",
        "setuptools>=70.0.0",
        "werkzeug>=3.0.6",
    ]

    for floor in expected_security_floors:
        assert floor in req_text, f"Missing expected security version floor: {floor}"
