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
    import re

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

    permissive_exceptions = (
        "gpl-compatible",
        "gpl compatible",
        "psf",
        "python software foundation",
        "beopen",
    )

    for pkg in direct_pkgs:
        try:
            dist = importlib.metadata.distribution(pkg)
            lic = (dist.metadata.get("License") or "").lower()
            classifiers = dist.metadata.get_all("Classifier") or []

            # Permissive and Python Software Foundation licenses are not copyleft
            if any(exc in lic for exc in permissive_exceptions):
                continue

            is_copyleft = False
            for c in classifiers:
                c_lower = c.lower()
                if (
                    "general public license" in c_lower
                    or "affero" in c_lower
                    or "gpl" in c_lower
                ) and "lgpl" not in c_lower and "lesser" not in c_lower:
                    is_copyleft = True

            if "lesser" not in lic and "lgpl" not in lic and (
                any(ind in lic for ind in ("gnu general public license", "gnu affero", "agpl", "gplv2", "gplv3"))
                or (re.search(r"\bgpl\b", lic) and not re.search(r"\bgpl[- ]compatible\b", lic))
            ):
                is_copyleft = True

            if is_copyleft:
                flagged.append((pkg, lic))
        except Exception:
            pass

    assert flagged == [], f"Found copyleft-licensed dependencies conflicting with MIT: {flagged}"


def _enforces_security_floor(specifier, min_version) -> bool:
    """Return True if a PEP 440 SpecifierSet enforces all allowed versions are >= min_version."""
    from packaging.version import InvalidVersion, Version

    has_valid_lower_bound = False
    for spec in specifier:
        try:
            spec_ver = Version(spec.version)
        except InvalidVersion:
            return False

        if spec.operator in {">=", ">", "==", "~="}:
            if spec_ver < min_version:
                return False
            has_valid_lower_bound = True
        elif spec.operator == "<":
            if spec_ver <= min_version:
                return False
        elif spec.operator == "<=":
            if spec_ver < min_version:
                return False

    return has_valid_lower_bound


def test_cve_mitigation_pins_in_requirements():
    """Ensure critical libraries meet or exceed explicit security version floors in requirements.txt."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    req_lines = (REPO_ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    parsed_requirements: dict[str, list[Requirement]] = {}
    for raw_line in req_lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        if req.marker is None:
            parsed_requirements.setdefault(canonicalize_name(req.name), []).append(req)

    expected_security_floors = {
        "starlette": "1.3.1",
        "mlflow": "3.11.0",
        "pytest": "9.0.3",
        "urllib3": "2.2.2",
        "jinja2": "3.1.4",
        "aiohttp": "3.10.11",
        "tornado": "6.4.2",
        "certifi": "2024.7.4",
        "setuptools": "70.0.0",
        "werkzeug": "3.0.6",
    }

    violations = []
    for pkg, min_ver_str in expected_security_floors.items():
        min_ver = Version(min_ver_str)
        reqs = parsed_requirements.get(canonicalize_name(pkg), [])
        if not reqs or not all(_enforces_security_floor(req.specifier, min_ver) for req in reqs):
            found = [str(req.specifier) or "<unpinned>" for req in reqs]
            violations.append(f"{pkg}>={min_ver_str} (found: {found or 'missing'})")

    assert violations == [], "Requirements below expected security version floor(s): " + "; ".join(violations)
