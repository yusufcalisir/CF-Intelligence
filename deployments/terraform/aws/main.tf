# =============================================================================
# CFI Platform — AWS Terraform Module
# Provisions: VPC, EKS, AWS KMS, Security Groups
# =============================================================================

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------
locals {
  common_tags = {
    Project     = "cfi-platform"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# VPC
# ---------------------------------------------------------------------------
resource "aws_vpc" "cfi" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-vpc" })
}

resource "aws_internet_gateway" "cfi" {
  vpc_id = aws_vpc.cfi.id
  tags   = merge(local.common_tags, { Name = "${var.cluster_name}-igw" })
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.cfi.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.common_tags, {
    Name                              = "${var.cluster_name}-private-${count.index}"
    "kubernetes.io/role/internal-elb" = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "owned"
  })
}

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.cfi.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, {
    Name                         = "${var.cluster_name}-public-${count.index}"
    "kubernetes.io/role/elb"     = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "owned"
  })
}

resource "aws_eip" "nat" {
  count  = length(var.private_subnet_cidrs)
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${var.cluster_name}-nat-eip-${count.index}" })
}

resource "aws_nat_gateway" "cfi" {
  count         = length(var.public_subnet_cidrs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(local.common_tags, { Name = "${var.cluster_name}-nat-${count.index}" })
}

resource "aws_route_table" "private" {
  count  = length(var.private_subnet_cidrs)
  vpc_id = aws_vpc.cfi.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.cfi[count.index].id
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-private-rt-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = length(var.private_subnet_cidrs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------------------
# Security Groups — mTLS gRPC isolation
# ---------------------------------------------------------------------------
resource "aws_security_group" "eks_nodes" {
  name        = "${var.cluster_name}-node-sg"
  description = "CFI Platform EKS Node security group — mTLS gRPC isolation"
  vpc_id      = aws_vpc.cfi.id

  # gRPC FL Aggregator ingress from bank nodes only (within VPC)
  ingress {
    description = "gRPC FL Aggregator port"
    from_port   = 50051
    to_port     = 50052
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # mTLS HTTP health probes
  ingress {
    description = "HTTP health probes"
    from_port   = 8080
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Kubernetes internal kubelet communication
  ingress {
    description = "Kubernetes kubelet"
    from_port   = 10250
    to_port     = 10250
    protocol    = "tcp"
    self        = true
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-node-sg" })
}

# ---------------------------------------------------------------------------
# IAM — EKS Cluster Role
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "eks_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_cluster" {
  name               = "${var.cluster_name}-eks-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.eks_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_iam_policy_document" "node_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_node_group" {
  name               = "${var.cluster_name}-eks-node-role"
  assume_role_policy = data.aws_iam_policy_document.node_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "eks_container_registry" {
  role       = aws_iam_role.eks_node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ---------------------------------------------------------------------------
# EKS Cluster
# ---------------------------------------------------------------------------
resource "aws_eks_cluster" "cfi" {
  name     = var.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    security_group_ids      = [aws_security_group.eks_nodes.id]
    endpoint_private_access = true
    endpoint_public_access  = false
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.cfi.arn
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
  ]
}

# ---------------------------------------------------------------------------
# EKS Managed Node Group
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "cfi" {
  cluster_name    = aws_eks_cluster.cfi.name
  node_group_name = "${var.cluster_name}-nodes"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = var.node_instance_types

  scaling_config {
    desired_size = var.desired_node_count
    min_size     = var.min_node_count
    max_size     = var.max_node_count
  }

  update_config {
    max_unavailable = 1
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry,
  ]
}

# ---------------------------------------------------------------------------
# AWS KMS — Envelope Encryption Key
# ---------------------------------------------------------------------------
resource "aws_kms_key" "cfi" {
  description              = "CFI Platform — EKS secrets envelope encryption key"
  deletion_window_in_days  = 30
  enable_key_rotation      = true
  multi_region             = false

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-kms" })
}

resource "aws_kms_alias" "cfi" {
  name          = "alias/${var.cluster_name}-key"
  target_key_id = aws_kms_key.cfi.key_id
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL 16 Database
# ---------------------------------------------------------------------------
resource "aws_db_subnet_group" "cfi" {
  name       = "${var.cluster_name}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = merge(local.common_tags, { Name = "${var.cluster_name}-db-subnets" })
}

resource "aws_security_group" "rds" {
  name        = "${var.cluster_name}-rds-sg"
  description = "Security group for PostgreSQL RDS"
  vpc_id      = aws_vpc.cfi.id

  ingress {
    description = "PostgreSQL access from EKS nodes"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-rds-sg" })
}

resource "aws_db_instance" "cfi_postgresql" {
  identifier                  = "${var.cluster_name}-postgres"
  engine                      = "postgres"
  engine_version              = "16.2"
  instance_class              = "db.m6i.xlarge"
  allocated_storage           = 100
  max_allocated_storage       = 1000
  storage_type                = "gp3"
  storage_encrypted           = true
  kms_key_id                  = aws_kms_key.cfi.arn
  multi_az                    = true
  publicly_accessible         = false
  deletion_protection         = true
  backup_retention_period     = 7
  backup_window               = "03:00-04:00"
  db_subnet_group_name        = aws_db_subnet_group.cfi.name
  vpc_security_group_ids      = [aws_security_group.rds.id]

  db_name  = "cfi_platform"
  username = "cfi_admin"
  password = "SuperSecretProductionPassword123!"

  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.cluster_name}-postgres-final"

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-postgres" })
}

# ---------------------------------------------------------------------------
# ElastiCache Redis Replication Group
# ---------------------------------------------------------------------------
resource "aws_elasticache_subnet_group" "cfi" {
  name       = "${var.cluster_name}-redis-subnets"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "redis" {
  name        = "${var.cluster_name}-redis-sg"
  description = "Security group for ElastiCache Redis"
  vpc_id      = aws_vpc.cfi.id

  ingress {
    description     = "Redis port from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-redis-sg" })
}

resource "aws_elasticache_replication_group" "cfi_redis" {
  replication_group_id       = "${var.cluster_name}-redis"
  description                = "CFI Platform Feature Store Redis Cluster"
  node_type                  = "cache.m6g.large"
  num_cache_clusters         = 2
  parameter_group_name       = "default.redis7"
  port                       = 6379
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.cfi.arn
  subnet_group_name          = aws_elasticache_subnet_group.cfi.name
  security_group_ids         = [aws_security_group.redis.id]

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-redis" })
}

# ---------------------------------------------------------------------------
# AWS WAFv2 Web ACL
# ---------------------------------------------------------------------------
resource "aws_wafv2_web_acl" "cfi" {
  name        = "${var.cluster_name}-waf-acl"
  description = "WAF Web ACL for CFI Platform API"
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
    metric_name                = "${var.cluster_name}-waf-metrics"
    sampled_requests_enabled   = true
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-waf-acl" })
}

# ---------------------------------------------------------------------------
# CloudFront CDN Distribution
# ---------------------------------------------------------------------------
resource "aws_cloudfront_distribution" "cfi_cdn" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "CFI Platform CDN distribution"
  default_root_object = "index.html"

  origin {
    domain_name = "api.cfi-platform.org"
    origin_id   = "ALB-CFI-Platform"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2", "TLSv1.3"]
    }
  }

  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "ALB-CFI-Platform"

    forwarded_values {
      query_string = true
      cookies {
        forward = "all"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 3600
    max_ttl                = 86400
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = merge(local.common_tags, { Name = "${var.cluster_name}-cdn" })
}
