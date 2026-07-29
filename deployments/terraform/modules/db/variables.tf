variable "name" {
  description = "Identifier for the RDS DB cluster/instance"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "subnet_ids" {
  description = "List of private subnet IDs for DB subnet group"
  type        = list(string)
}

variable "allocated_storage" {
  description = "Allocated storage in GB"
  type        = number
  default     = 100
}

variable "max_allocated_storage" {
  description = "Max storage limit for autoscaling in GB"
  type        = number
  default     = 1000
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.m6i.xlarge"
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "cfi_platform"
}

variable "username" {
  description = "Master DB username"
  type        = string
  default     = "cfi_admin"
}

variable "password" {
  description = "Master DB password"
  type        = string
  sensitive   = true
}

variable "kms_key_arn" {
  description = "KMS Key ARN for storage encryption"
  type        = string
}
