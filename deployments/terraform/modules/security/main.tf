# =============================================================================
# CFI Platform — Reusable KMS + Secrets Manager + WAFv2 Security Module
# =============================================================================

# KMS Key
resource "aws_kms_key" "cfi" {
  description             = var.kms_description
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name        = "${var.name_prefix}-kms"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_kms_alias" "cfi" {
  name          = "alias/${var.name_prefix}-key"
  target_key_id = aws_kms_key.cfi.key_id
}

# Secrets Manager
resource "aws_secretsmanager_secret" "cfi" {
  name                    = "${var.name_prefix}-secrets"
  kms_key_id              = aws_kms_key.cfi.arn
  recovery_window_in_days = 7

  tags = {
    Name        = "${var.name_prefix}-secrets"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# AWS WAFv2 Web ACL
resource "aws_wafv2_web_acl" "main" {
  name        = "${var.name_prefix}-waf-acl"
  description = "CFI Platform Production WAF Web ACL"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSetMetric"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 20

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "SQLiRuleSetMetric"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-waf-metrics"
    sampled_requests_enabled   = true
  }

  tags = {
    Name        = "${var.name_prefix}-waf-acl"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

output "kms_key_arn" {
  value       = aws_kms_key.cfi.arn
  description = "KMS Key ARN"
}

output "secrets_manager_arn" {
  value       = aws_secretsmanager_secret.cfi.arn
  description = "Secrets Manager Secret ARN"
}

output "waf_web_acl_arn" {
  value       = aws_wafv2_web_acl.main.arn
  description = "WAFv2 Web ACL ARN"
}
