"""
Dynamic SBOM (Software Bill of Materials) Generator for CFI Platform.
Generates a genuine CycloneDX 1.5 compliant SBOM from the active Python distribution
metadata and the frontend npm manifest.

Outputs:
  storage/sbom_cyclonedx.json
"""

import datetime
import importlib.metadata
import json
from pathlib import Path
import sys
import uuid


def generate_sbom(output_path: Path = Path("storage/sbom_cyclonedx.json")) -> dict:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    components = []

    # 1. Discover all active Python packages from importlib.metadata
    dists = list(importlib.metadata.distributions())
    for dist in dists:
        name = dist.metadata.get("Name")
        version = dist.metadata.get("Version")
        if not name or not version:
            continue

        license_name = dist.metadata.get("License") or "Unknown"
        summary = dist.metadata.get("Summary") or ""

        component = {
            "type": "library",
            "bom-ref": f"pkg:pypi/{name.lower()}@{version}",
            "name": name,
            "version": version,
            "description": summary[:200] if summary else "",
            "licenses": [{"license": {"id": license_name}} if license_name in ["MIT", "Apache-2.0", "BSD-3-Clause", "MPL-2.0"] else {"license": {"name": license_name[:50]}}],
            "purl": f"pkg:pypi/{name.lower()}@{version}",
            "properties": [
                {"name": "ecosystem", "value": "pypi"}
            ]
        }
        components.append(component)

    # 2. Discover frontend packages from frontend/package.json
    frontend_pkg_path = Path("frontend/package.json")
    if frontend_pkg_path.exists():
        try:
            with open(frontend_pkg_path, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            for dep_name, dep_ver in {**deps, **dev_deps}.items():
                clean_ver = dep_ver.lstrip("^~")
                components.append({
                    "type": "library",
                    "bom-ref": f"pkg:npm/{dep_name}@{clean_ver}",
                    "name": dep_name,
                    "version": clean_ver,
                    "purl": f"pkg:npm/{dep_name}@{clean_ver}",
                    "properties": [
                        {"name": "ecosystem", "value": "npm"}
                    ]
                })
        except Exception as exc:
            print(f"Warning reading frontend package.json: {exc}", file=sys.stderr)

    # Sort components deterministically by name
    components.sort(key=lambda c: c["name"].lower())

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tools": [
                {
                    "vendor": "CF-Intelligence",
                    "name": "cfi-sbom-generator",
                    "version": "1.0.0"
                }
            ],
            "component": {
                "type": "application",
                "name": "privacy-preserving-cross-bank-fraud-detection",
                "version": "1.0.0",
                "licenses": [{"license": {"id": "MIT"}}]
            }
        },
        "components": components
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sbom, f, indent=2)

    print(f"Generated CycloneDX 1.5 SBOM with {len(components)} components -> {output_path}")
    return sbom


if __name__ == "__main__":
    generate_sbom()
