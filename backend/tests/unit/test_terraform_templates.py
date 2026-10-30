"""Tests for Terraform IaC template structural integrity.

These tests validate that all three cloud provider modules (AWS, Azure, GCP)
have the required HCL file structure, declare necessary resource types,
enforce encryption key vault resources, and define expected output values.

No Terraform binary or cloud credentials are required - all checks are
pure offline text / regex analysis of the .tf source files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TERRAFORM_DIR = Path(__file__).parents[3] / "deployments" / "terraform"
AWS_DIR = TERRAFORM_DIR / "aws"
AZURE_DIR = TERRAFORM_DIR / "azure"
GCP_DIR = TERRAFORM_DIR / "gcp"

PROVIDERS = ["aws", "azure", "gcp"]
DIRS = {"aws": AWS_DIR, "azure": AZURE_DIR, "gcp": GCP_DIR}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def hcl_blocks(content: str, block_type: str) -> list[str]:
    """Return all top-level HCL block labels of a given type.

    Matches patterns like:
      resource "aws_eks_cluster" "cfi" {
      variable "aws_region" {
      output "eks_cluster_name" {
      provider "aws" {
    """
    pattern = rf'^{re.escape(block_type)}\s+"([^"]+)"'
    return re.findall(pattern, content, re.MULTILINE)


# ---------------------------------------------------------------------------
# 1. File structure tests
# ---------------------------------------------------------------------------


class TestFileStructure:
    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_main_tf_exists(self, provider: str):
        assert (DIRS[provider] / "main.tf").exists(), f"main.tf missing for {provider}"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_variables_tf_exists(self, provider: str):
        assert (DIRS[provider] / "variables.tf").exists(), f"variables.tf missing for {provider}"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_outputs_tf_exists(self, provider: str):
        assert (DIRS[provider] / "outputs.tf").exists(), f"outputs.tf missing for {provider}"


# ---------------------------------------------------------------------------
# 2. HCL block structural tests
# ---------------------------------------------------------------------------


class TestHCLSyntaxAndBlocks:
    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_terraform_block_present(self, provider: str):
        content = read(DIRS[provider] / "main.tf")
        assert "terraform {" in content, f"No 'terraform {{' block in {provider}/main.tf"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_required_version_declared(self, provider: str):
        content = read(DIRS[provider] / "main.tf")
        assert "required_version" in content, f"required_version not set in {provider}/main.tf"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_provider_block_present(self, provider: str):
        content = read(DIRS[provider] / "main.tf")
        assert "provider " in content, f"No provider block in {provider}/main.tf"

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_resource_blocks_exist(self, provider: str):
        content = read(DIRS[provider] / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert len(resources) >= 3, (
            f"Expected >= 3 resource blocks in {provider}/main.tf, found {len(resources)}"
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_variable_blocks_exist(self, provider: str):
        content = read(DIRS[provider] / "variables.tf")
        variables = hcl_blocks(content, "variable")
        assert len(variables) >= 5, (
            f"Expected >= 5 variable blocks in {provider}/variables.tf, found {len(variables)}"
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_output_blocks_exist(self, provider: str):
        content = read(DIRS[provider] / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert len(outputs) >= 3, (
            f"Expected >= 3 output blocks in {provider}/outputs.tf, found {len(outputs)}"
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_no_hardcoded_secrets(self, provider: str):
        """Sensitive values must use variables or data sources, not inline strings."""
        content = read(DIRS[provider] / "main.tf")
        # Look for literal credentials patterns
        bad_patterns = [
            r'password\s*=\s*"[^"]{4,}"',
            r'secret_key\s*=\s*"[^"]{4,}"',
            r'access_key\s*=\s*"AKIA[A-Z0-9]{16}"',
        ]
        for pattern in bad_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            assert not match, (
                f"Potential hardcoded secret in {provider}/main.tf: {match.group() if match else ''}"
            )


# ---------------------------------------------------------------------------
# 3. KMS / Key Vault encryption resource tests
# ---------------------------------------------------------------------------


class TestEncryptionKeyVaultResources:
    def test_aws_kms_key_resource(self):
        content = read(AWS_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "aws_kms_key" in resources, "aws_kms_key resource missing from AWS main.tf"

    def test_aws_kms_key_rotation_enabled(self):
        content = read(AWS_DIR / "main.tf")
        assert "enable_key_rotation" in content, "KMS key rotation not enabled in AWS main.tf"

    def test_aws_kms_deletion_window_set(self):
        content = read(AWS_DIR / "main.tf")
        assert "deletion_window_in_days" in content, (
            "KMS deletion window not configured in AWS main.tf"
        )

    def test_azure_key_vault_resource(self):
        content = read(AZURE_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "azurerm_key_vault" in resources, (
            "azurerm_key_vault resource missing from Azure main.tf"
        )

    def test_azure_key_vault_purge_protection(self):
        content = read(AZURE_DIR / "main.tf")
        assert "purge_protection_enabled" in content, (
            "Key Vault purge protection not configured in Azure main.tf"
        )

    def test_azure_key_vault_premium_sku(self):
        content = read(AZURE_DIR / "main.tf")
        assert "sku_name" in content and "premium" in content, (
            "Key Vault premium SKU not set in Azure main.tf"
        )

    def test_gcp_kms_keyring_resource(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_kms_key_ring" in resources, (
            "google_kms_key_ring resource missing from GCP main.tf"
        )

    def test_gcp_kms_crypto_key_resource(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_kms_crypto_key" in resources, (
            "google_kms_crypto_key resource missing from GCP main.tf"
        )

    def test_gcp_kms_rotation_period(self):
        content = read(GCP_DIR / "main.tf")
        assert "rotation_period" in content, (
            "Cloud KMS key rotation_period not configured in GCP main.tf"
        )


# ---------------------------------------------------------------------------
# 4. Kubernetes cluster resource tests
# ---------------------------------------------------------------------------


class TestKubernetesClusterResources:
    def test_aws_eks_cluster_resource(self):
        content = read(AWS_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "aws_eks_cluster" in resources, "aws_eks_cluster resource missing from AWS main.tf"

    def test_aws_eks_node_group_resource(self):
        content = read(AWS_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "aws_eks_node_group" in resources, (
            "aws_eks_node_group resource missing from AWS main.tf"
        )

    def test_aws_eks_encryption_config(self):
        content = read(AWS_DIR / "main.tf")
        assert "encryption_config" in content, (
            "EKS encryption_config block missing from AWS main.tf"
        )

    def test_azure_aks_cluster_resource(self):
        content = read(AZURE_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "azurerm_kubernetes_cluster" in resources, (
            "azurerm_kubernetes_cluster resource missing from Azure main.tf"
        )

    def test_azure_aks_calico_network_policy(self):
        content = read(AZURE_DIR / "main.tf")
        assert "calico" in content, "Calico network policy not configured in Azure AKS"

    def test_azure_aks_bank_node_pool(self):
        content = read(AZURE_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "azurerm_kubernetes_cluster_node_pool" in resources, (
            "Bank node pool (azurerm_kubernetes_cluster_node_pool) missing from Azure main.tf"
        )

    def test_gcp_gke_cluster_resource(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_container_cluster" in resources, (
            "google_container_cluster resource missing from GCP main.tf"
        )

    def test_gcp_gke_node_pool_resource(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_container_node_pool" in resources, (
            "google_container_node_pool resource missing from GCP main.tf"
        )

    def test_gcp_gke_private_cluster(self):
        content = read(GCP_DIR / "main.tf")
        assert "private_cluster_config" in content, (
            "GKE private_cluster_config not configured in GCP main.tf"
        )

    def test_gcp_workload_identity(self):
        content = read(GCP_DIR / "main.tf")
        assert "workload_identity_config" in content, (
            "GKE workload_identity_config not configured in GCP main.tf"
        )


# ---------------------------------------------------------------------------
# 5. Network isolation & security tests
# ---------------------------------------------------------------------------


class TestNetworkIsolationAndSecurity:
    def test_aws_vpc_resource(self):
        content = read(AWS_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "aws_vpc" in resources, "aws_vpc resource missing from AWS main.tf"

    def test_aws_private_subnets(self):
        content = read(AWS_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "aws_subnet" in resources, "aws_subnet resource missing from AWS main.tf"

    def test_aws_security_group_grpc_port(self):
        content = read(AWS_DIR / "main.tf")
        # gRPC port must be referenced in security group rules
        assert "50051" in content and "50052" in content, (
            "gRPC ports 50051/50052 not referenced in AWS security groups"
        )

    def test_azure_vnet_resource(self):
        content = read(AZURE_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "azurerm_virtual_network" in resources, (
            "azurerm_virtual_network resource missing from Azure main.tf"
        )

    def test_azure_nsg_resource(self):
        content = read(AZURE_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "azurerm_network_security_group" in resources, (
            "azurerm_network_security_group resource missing from Azure main.tf"
        )

    def test_azure_nsg_deny_inter_bank(self):
        content = read(AZURE_DIR / "main.tf")
        # Must have an explicit Deny rule in NSG
        assert '"Deny"' in content or "'Deny'" in content, (
            "No Deny rule found in Azure NSG (inter-bank isolation missing)"
        )

    def test_gcp_firewall_deny_inter_bank(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_compute_firewall" in resources, (
            "google_compute_firewall resource missing from GCP main.tf"
        )
        assert "deny_inter_bank" in content or "deny {" in content, (
            "No deny firewall rule for inter-bank isolation in GCP main.tf"
        )

    def test_gcp_nat_gateway_for_private_nodes(self):
        content = read(GCP_DIR / "main.tf")
        resources = hcl_blocks(content, "resource")
        assert "google_compute_router_nat" in resources, (
            "Cloud NAT (google_compute_router_nat) missing - private nodes cannot pull images"
        )


# ---------------------------------------------------------------------------
# 6. Output schema tests
# ---------------------------------------------------------------------------


class TestOutputSchema:
    def test_aws_outputs_cluster_endpoint(self):
        content = read(AWS_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "eks_cluster_endpoint" in outputs, (
            "eks_cluster_endpoint output missing from AWS outputs.tf"
        )

    def test_aws_outputs_kms_key_arn(self):
        content = read(AWS_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "kms_key_arn" in outputs, "kms_key_arn output missing from AWS outputs.tf"

    def test_aws_outputs_vpc_id(self):
        content = read(AWS_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "vpc_id" in outputs, "vpc_id output missing from AWS outputs.tf"

    def test_azure_outputs_cluster_endpoint(self):
        content = read(AZURE_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "aks_cluster_endpoint" in outputs, (
            "aks_cluster_endpoint output missing from Azure outputs.tf"
        )

    def test_azure_outputs_key_vault_id(self):
        content = read(AZURE_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "key_vault_id" in outputs, "key_vault_id output missing from Azure outputs.tf"

    def test_azure_outputs_resource_group_name(self):
        content = read(AZURE_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "resource_group_name" in outputs, (
            "resource_group_name output missing from Azure outputs.tf"
        )

    def test_gcp_outputs_cluster_endpoint(self):
        content = read(GCP_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "gke_cluster_endpoint" in outputs, (
            "gke_cluster_endpoint output missing from GCP outputs.tf"
        )

    def test_gcp_outputs_kms_keyring_id(self):
        content = read(GCP_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "kms_keyring_id" in outputs, "kms_keyring_id output missing from GCP outputs.tf"

    def test_gcp_outputs_vpc_network_name(self):
        content = read(GCP_DIR / "outputs.tf")
        outputs = hcl_blocks(content, "output")
        assert "vpc_network_name" in outputs, "vpc_network_name output missing from GCP outputs.tf"
