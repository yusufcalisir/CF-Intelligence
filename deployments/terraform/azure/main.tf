# =============================================================================
# CFI Platform — Azure Terraform Module
# Provisions: Resource Group, VNet/NSG, AKS, Azure Key Vault
# =============================================================================

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.0, < 5.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 2.0"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------
locals {
  common_tags = {
    project     = "cfi-platform"
    environment = var.environment
    managed_by  = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Resource Group
# ---------------------------------------------------------------------------
resource "azurerm_resource_group" "cfi" {
  name     = var.resource_group_name
  location = var.azure_location
  tags     = local.common_tags
}

# ---------------------------------------------------------------------------
# Virtual Network & Subnets
# ---------------------------------------------------------------------------
resource "azurerm_virtual_network" "cfi" {
  name                = "${var.cluster_name}-vnet"
  address_space       = [var.vnet_address_space]
  location            = azurerm_resource_group.cfi.location
  resource_group_name = azurerm_resource_group.cfi.name
  tags                = local.common_tags
}

resource "azurerm_subnet" "aks_nodes" {
  name                 = "${var.cluster_name}-aks-nodes"
  resource_group_name  = azurerm_resource_group.cfi.name
  virtual_network_name = azurerm_virtual_network.cfi.name
  address_prefixes     = [var.aks_subnet_cidr]
}

resource "azurerm_subnet" "private_endpoints" {
  name                                          = "${var.cluster_name}-private-endpoints"
  resource_group_name                           = azurerm_resource_group.cfi.name
  virtual_network_name                          = azurerm_virtual_network.cfi.name
  address_prefixes                              = [var.private_endpoint_subnet_cidr]
  private_endpoint_network_policies             = "Disabled"
}

# ---------------------------------------------------------------------------
# Network Security Group — gRPC mTLS isolation
# ---------------------------------------------------------------------------
resource "azurerm_network_security_group" "cfi" {
  name                = "${var.cluster_name}-nsg"
  location            = azurerm_resource_group.cfi.location
  resource_group_name = azurerm_resource_group.cfi.name

  # gRPC FL Aggregator — within VNet only
  security_rule {
    name                       = "AllowGRPCAggregator"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["50051", "50052"]
    source_address_prefix      = var.vnet_address_space
    destination_address_prefix = "*"
  }

  # HTTP health probes
  security_rule {
    name                       = "AllowHTTPHealthProbes"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["8080", "8081"]
    source_address_prefix      = var.vnet_address_space
    destination_address_prefix = "*"
  }

  # Block inter-bank direct gRPC traffic (bank nodes must go through aggregator)
  security_rule {
    name                       = "DenyInterBankDirect"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["50052"]
    source_address_prefix      = var.aks_subnet_cidr
    destination_address_prefix = var.aks_subnet_cidr
  }

  tags = local.common_tags
}

resource "azurerm_subnet_network_security_group_association" "aks_nodes" {
  subnet_id                 = azurerm_subnet.aks_nodes.id
  network_security_group_id = azurerm_network_security_group.cfi.id
}

# ---------------------------------------------------------------------------
# AKS Cluster
# ---------------------------------------------------------------------------
resource "azurerm_kubernetes_cluster" "cfi" {
  name                = var.cluster_name
  location            = azurerm_resource_group.cfi.location
  resource_group_name = azurerm_resource_group.cfi.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Standard"

  default_node_pool {
    name                         = "system"
    node_count                   = var.system_node_count
    vm_size                      = var.node_vm_size
    vnet_subnet_id               = azurerm_subnet.aks_nodes.id
    only_critical_addons_enabled = true
    os_disk_size_gb              = 128
    os_disk_type                 = "Ephemeral"
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    service_cidr      = "172.16.0.0/16"
    dns_service_ip    = "172.16.0.10"
  }

  # Secrets encryption via Azure Key Vault (configured post-deploy via CSI driver)
  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  tags = local.common_tags
}

# Bank node pool — isolatable for per-bank resource quotas
resource "azurerm_kubernetes_cluster_node_pool" "bank_nodes" {
  name                  = "banknodes"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.cfi.id
  vm_size               = var.bank_node_vm_size
  node_count            = var.bank_node_count
  min_count             = var.bank_node_min_count
  max_count             = var.bank_node_max_count
  auto_scaling_enabled  = true
  vnet_subnet_id        = azurerm_subnet.aks_nodes.id

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Azure Key Vault
# ---------------------------------------------------------------------------
data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "cfi" {
  name                        = "${var.cluster_name}-kv"
  location                    = azurerm_resource_group.cfi.location
  resource_group_name         = azurerm_resource_group.cfi.name
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  sku_name                    = "premium"
  soft_delete_retention_days  = 90
  purge_protection_enabled    = true
  rbac_authorization_enabled  = true

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = var.key_vault_allowed_ips
  }

  tags = local.common_tags
}
