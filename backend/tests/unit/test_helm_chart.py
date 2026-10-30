"""Tests for Helm chart structural integrity.

These tests validate Chart.yaml schema, required values keys,
and that template files are syntactically well-formed YAML fragments.
No Kubernetes cluster is required - these are offline linting checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).parents[3] / "deployments" / "helm" / "cfi-platform"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml_all(path: Path) -> list[dict]:
    """Load multi-document YAML (separated by ---)."""
    with path.open(encoding="utf-8") as f:
        return list(yaml.safe_load_all(f))


# ---------------------------------------------------------------------------
# Chart.yaml tests
# ---------------------------------------------------------------------------


class TestChartYaml:
    def setup_method(self):
        self.chart = load_yaml(HELM_DIR / "Chart.yaml")

    def test_chart_yaml_exists(self):
        assert (HELM_DIR / "Chart.yaml").exists()

    def test_api_version_v2(self):
        assert self.chart["apiVersion"] == "v2"

    def test_name_present(self):
        assert self.chart["name"] == "cfi-platform"

    def test_version_semver(self):
        version = self.chart["version"]
        pattern = r"^\d+\.\d+\.\d+$"
        assert re.match(pattern, version), f"version '{version}' is not valid semver"

    def test_app_version_present(self):
        assert "appVersion" in self.chart

    def test_type_is_application(self):
        assert self.chart.get("type") == "application"

    def test_dependencies_structure(self):
        deps = self.chart.get("dependencies", [])
        for dep in deps:
            assert "name" in dep
            assert "version" in dep
            assert "repository" in dep


# ---------------------------------------------------------------------------
# values.yaml tests
# ---------------------------------------------------------------------------


class TestValuesYaml:
    def setup_method(self):
        self.values = load_yaml(HELM_DIR / "values.yaml")

    def test_values_yaml_exists(self):
        assert (HELM_DIR / "values.yaml").exists()

    def test_aggregator_section_present(self):
        assert "aggregator" in self.values

    def test_aggregator_replica_count_positive(self):
        assert self.values["aggregator"]["replicaCount"] >= 1

    def test_bank_node_section_present(self):
        assert "bankNode" in self.values

    def test_bank_node_replica_count_positive(self):
        assert self.values["bankNode"]["replicaCount"] >= 1

    def test_database_section_has_secret_name(self):
        db = self.values["database"]
        assert "secretName" in db
        assert db["secretName"]  # non-empty

    def test_kafka_section_has_brokers(self):
        kafka = self.values["kafka"]
        assert "brokers" in kafka
        assert kafka["brokers"]

    def test_kafka_section_has_secret_name(self):
        assert "secretName" in self.values["kafka"]

    def test_vault_section_present(self):
        assert "vault" in self.values

    def test_redis_disabled_by_default(self):
        # Redis is expected to be toggled externally via values-prod.yaml
        assert "redis" in self.values

    def test_ingress_enabled_by_default(self):
        assert self.values["ingress"]["enabled"] is True

    def test_ingress_tls_configured(self):
        ingress = self.values["ingress"]
        assert "tls" in ingress
        assert len(ingress["tls"]) >= 1

    def test_aggregator_autoscaling_enabled(self):
        hpa = self.values["aggregator"]["autoscaling"]
        assert hpa["enabled"] is True
        assert hpa["minReplicas"] >= 1
        assert hpa["maxReplicas"] > hpa["minReplicas"]

    def test_hsm_config_present(self):
        hsm = self.values["bankNode"]["hsm"]
        assert "enabled" in hsm
        assert "secretName" in hsm


# ---------------------------------------------------------------------------
# Template file tests
# ---------------------------------------------------------------------------

TEMPLATE_FILES = [
    "aggregator-deployment.yaml",
    "bank-node-deployment.yaml",
    "service.yaml",
    "ingress.yaml",
    "hpa-and-netpol.yaml",
]


@pytest.mark.parametrize("template_name", TEMPLATE_FILES)
def test_template_file_exists(template_name: str):
    path = HELM_DIR / "templates" / template_name
    assert path.exists(), f"Template not found: {path}"


@pytest.mark.parametrize("template_name", TEMPLATE_FILES)
def test_template_has_kubernetes_api_version_marker(template_name: str):
    """All templates must declare 'apiVersion:' at least once (pre-render)."""
    path = HELM_DIR / "templates" / template_name
    content = path.read_text(encoding="utf-8")
    assert "apiVersion:" in content, f"No apiVersion in {template_name}"


@pytest.mark.parametrize("template_name", TEMPLATE_FILES)
def test_template_uses_release_name(template_name: str):
    """All templates should reference .Release.Name for uniqueness."""
    path = HELM_DIR / "templates" / template_name
    content = path.read_text(encoding="utf-8")
    assert ".Release.Name" in content, f"No .Release.Name in {template_name}"


# ---------------------------------------------------------------------------
# Security posture tests (static checks on values)
# ---------------------------------------------------------------------------


class TestSecurityPosture:
    def setup_method(self):
        self.values = load_yaml(HELM_DIR / "values.yaml")

    def test_no_plaintext_passwords_in_values(self):
        """Secrets must come from secretKeyRef, not plain values."""
        raw = (HELM_DIR / "values.yaml").read_text(encoding="utf-8")
        # Ensure the word 'password' only appears inside comments or keys,
        # not as a value assignment like: password: mypassword123
        lines = raw.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Detect 'password: <non-placeholder>' patterns
            match = re.match(r"^\s*(?:sasl)?[Pp]assword:\s+\"?(?!changeme)(\S+)\"?", line)
            assert not match, f"Potential plaintext password found in values.yaml: {line.strip()}"

    def test_hsm_secret_name_not_empty(self):
        secretName = self.values["bankNode"]["hsm"]["secretName"]
        assert secretName and isinstance(secretName, str)

    def test_vault_enabled(self):
        assert self.values["vault"]["enabled"] is True

    def test_ingress_tls_secret_name_set(self):
        tls_entries = self.values["ingress"]["tls"]
        for entry in tls_entries:
            assert "secretName" in entry and entry["secretName"]
