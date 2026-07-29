variable "cluster_name" {
  description = "EKS Cluster Name"
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes Version"
  type        = string
  default     = "1.30"
}

variable "environment" {
  description = "Deployment Environment"
  type        = string
  default     = "production"
}

variable "subnet_ids" {
  description = "Private Subnet IDs"
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security Group IDs"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS Key ARN for EKS secret envelope encryption"
  type        = string
}

variable "desired_size" {
  description = "Desired Node Count"
  type        = number
  default     = 3
}

variable "min_size" {
  description = "Min Node Count"
  type        = number
  default     = 2
}

variable "max_size" {
  description = "Max Node Count"
  type        = number
  default     = 10
}

variable "instance_types" {
  description = "EC2 Instance Types"
  type        = list(string)
  default     = ["c6i.xlarge"]
}
