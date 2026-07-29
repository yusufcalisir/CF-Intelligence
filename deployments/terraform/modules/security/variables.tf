variable "name_prefix" {
  description = "Prefix for security resources"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "kms_description" {
  description = "Description for KMS Key"
  type        = string
  default     = "CFI Platform Encryption Key"
}
