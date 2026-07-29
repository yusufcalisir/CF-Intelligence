variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Deployment environment (production, staging)"
  type        = string
  default     = "production"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "cfi-platform"
}

variable "kubernetes_version" {
  description = "Kubernetes version for EKS cluster"
  type        = string
  default     = "1.30"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
}

variable "availability_zones" {
  description = "Availability zones to deploy into"
  type        = list(string)
  default     = ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
}

variable "node_instance_types" {
  description = "EC2 instance types for EKS node group"
  type        = list(string)
  default     = ["m6i.2xlarge"]
}

variable "desired_node_count" {
  description = "Desired number of worker nodes"
  type        = number
  default     = 3
}

variable "min_node_count" {
  description = "Minimum number of worker nodes"
  type        = number
  default     = 2
}

variable "max_node_count" {
  description = "Maximum number of worker nodes"
  type        = number
  default     = 10
}

variable "db_password" {
  description = "Database master password for RDS PostgreSQL"
  type        = string
  sensitive   = true
  default     = "CHANGEME_USE_SECRETS_MANAGER"
}
