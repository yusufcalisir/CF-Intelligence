"""Unit tests verifying structure and configuration of enterprise_security_ci.yml workflow (Section 14.2).

Covers:
- YAML parsing and top-level trigger definitions
- SAST job definitions (Ruff, Mypy, Bandit)
- Dependency vulnerability audit job definition (pip-audit)
- Container security scanning job definition (Trivy)
- Infrastructure security audit job definition (Helm lint, Terraform validate)
- Full pytest security suite execution job definition
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def get_workflow_path() -> str:
    """Returns absolute path to enterprise_security_ci.yml workflow file."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    return os.path.join(repo_root, ".github", "workflows", "enterprise_security_ci.yml")


@pytest.fixture()
def workflow_yaml() -> dict[str, Any]:
    """Parses and returns workflow YAML content as dictionary."""
    path = get_workflow_path()
    assert os.path.isfile(path), f"Workflow file not found at: {path}"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Workflow YAML parsed to non-dict root"
    return data


# ---------------------------------------------------------------------------
# 1. TestEnterpriseSecurityCIWorkflow
# ---------------------------------------------------------------------------


class TestEnterpriseSecurityCIWorkflow:
    def test_workflow_file_exists_and_parses_cleanly(self, workflow_yaml: dict[str, Any]):
        """Workflow file must exist and parse cleanly as valid YAML."""
        assert "name" in workflow_yaml
        assert workflow_yaml["name"] == "Enterprise Security & Vulnerability Audit Pipeline"

    def test_workflow_defines_triggers(self, workflow_yaml: dict[str, Any]):
        """Workflow must define push, pull_request, and schedule triggers."""
        triggers = workflow_yaml.get("on")
        if triggers is None:
            for k, v in workflow_yaml.items():
                if k in ("on", "true", "True") or k is True:
                    triggers = v
                    break
        assert triggers is not None, "Workflow missing 'on' trigger configuration"
        assert "push" in triggers
        assert "pull_request" in triggers
        assert "schedule" in triggers

    def test_workflow_defines_all_security_jobs(self, workflow_yaml: dict[str, Any]):
        """Workflow must contain all core security jobs, including backend, frontend & worker Trivy jobs."""
        jobs = workflow_yaml.get("jobs", {})
        expected_jobs = [
            "sast-static-analysis",
            "dependency-security-audit",
            "trivy-container-security",
            "trivy-frontend-container-security",
            "trivy-institutional-worker-security",
            "helm-and-terraform-security-audit",
            "pytest-security-and-compliance-suites",
        ]
        for job_name in expected_jobs:
            assert job_name in jobs, f"Missing required job: {job_name}"

    def test_sast_job_includes_ruff_mypy_and_bandit(self, workflow_yaml: dict[str, Any]):
        """SAST job must contain steps for Ruff, Mypy, and Bandit security scanner."""
        sast_job = workflow_yaml["jobs"]["sast-static-analysis"]
        steps = sast_job.get("steps", [])
        step_names = [s.get("name", "") for s in steps]

        assert any("Ruff" in name for name in step_names), "Missing Ruff step"
        assert any("Mypy" in name for name in step_names), "Missing Mypy step"
        assert any("Bandit" in name for name in step_names), "Missing Bandit SAST step"

    def test_container_job_uses_trivy_action(self, workflow_yaml: dict[str, Any]):
        """Backend, frontend & worker Trivy jobs run independently, pin scanners, and block ALL CRITICAL/HIGH."""
        jobs = workflow_yaml["jobs"]
        backend_job = jobs["trivy-container-security"]
        frontend_job = jobs["trivy-frontend-container-security"]
        worker_job = jobs["trivy-institutional-worker-security"]

        # Independent jobs: none wait on or depend on another
        assert "needs" not in backend_job
        assert "needs" not in frontend_job
        assert "needs" not in worker_job

        for job_name, job in (
            ("trivy-container-security", backend_job),
            ("trivy-frontend-container-security", frontend_job),
            ("trivy-institutional-worker-security", worker_job),
        ):
            trivy_steps = [s for s in job.get("steps", []) if "trivy-action" in s.get("uses", "")]
            vuln_steps = [s for s in trivy_steps if s.get("with", {}).get("exit-code") == "1"]
            sbom_steps = [s for s in trivy_steps if s.get("with", {}).get("format") == "cyclonedx"]
            assert len(vuln_steps) == 1, f"{job_name} must execute a blocking Trivy vuln scan step"
            assert len(sbom_steps) == 1, f"{job_name} must generate a CycloneDX SBOM via Trivy"
            step = vuln_steps[0]
            uses_ref = step["uses"]
            assert "@master" not in uses_ref and "@main" not in uses_ref and "@latest" not in uses_ref
            with_cfg = step.get("with", {})
            assert with_cfg.get("exit-code") == "1"
            assert with_cfg.get("severity") == "CRITICAL,HIGH"
            assert with_cfg.get("ignore-unfixed") is False, (
                f"{job_name} must keep ignore-unfixed=false so ALL CRITICAL/HIGH findings block the gate"
            )
            assert with_cfg.get("version") and with_cfg["version"] != "latest"

        # Backend & worker jobs enforce multi-env runtime pip-audit, SBOM preservation, and recursive FS checks
        backend_runs = "\n".join(s.get("run", "") for s in backend_job.get("steps", []))
        assert "run_pip_audit.py" in backend_runs and "--path" in backend_runs
        assert "/usr/local/bin/python3" in backend_runs
        assert "test_ray_runtime_functional.py" in backend_runs
        assert "APP_ENV=test" in backend_runs
        assert "CFI_EXPECT_RAY_JARS_ABSENT=1" in backend_runs
        assert "ray/jars" in backend_runs
        assert "bom.cdx.json" not in backend_runs

        worker_runs = "\n".join(s.get("run", "") for s in worker_job.get("steps", []))
        assert "run_pip_audit.py" in worker_runs and "--path" in worker_runs
        assert "/usr/local/bin/python3" in worker_runs
        assert "private_set_intersection" in worker_runs

        # Dependency audit verifies uv.lock + exported frozen lockfiles
        dep_runs = "\n".join(
            s.get("run", "") for s in jobs["dependency-security-audit"].get("steps", [])
        )
        assert "uv lock --check" in dep_runs
        assert "requirements.lock" in dep_runs
        assert "worker-requirements.lock" in dep_runs

        # No setup-uv step may float on "latest"
        for job_name, job in jobs.items():
            for step in job.get("steps", []):
                if "setup-uv" in step.get("uses", ""):
                    uv_ver = step.get("with", {}).get("version", "")
                    assert uv_ver and uv_ver != "latest", (
                        f"{job_name} must pin setup-uv version instead of '{uv_ver}'"
                    )

    def test_iac_job_includes_helm_and_terraform(self, workflow_yaml: dict[str, Any]):
        """Infrastructure job must contain Helm linting and Terraform validation steps."""
        iac_job = workflow_yaml["jobs"]["helm-and-terraform-security-audit"]
        steps = iac_job.get("steps", [])
        step_names = [s.get("name", "") for s in steps]

        assert any("Helm" in name for name in step_names), "Missing Helm lint step"
        assert any("Terraform" in name for name in step_names), "Missing Terraform validation step"

    def test_pytest_job_runs_unit_and_compliance_suites(self, workflow_yaml: dict[str, Any]):
        """Pytest job must execute pytest across unit test directory."""
        pytest_job = workflow_yaml["jobs"]["pytest-security-and-compliance-suites"]
        steps = pytest_job.get("steps", [])
        runs = [s.get("run", "") for s in steps]

        assert any("pytest" in run for run in runs), "Missing pytest execution step"
