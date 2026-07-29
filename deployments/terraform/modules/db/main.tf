# =============================================================================
# CFI Platform — Reusable RDS PostgreSQL 16 Module
# =============================================================================

resource "aws_db_subnet_group" "cfi" {
  name        = "${var.name}-db-subnet-group"
  subnet_ids  = var.subnet_ids
  description = "Subnet group for CFI Platform PostgreSQL database"

  tags = {
    Name        = "${var.name}-db-subnet-group"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db-sg"
  description = "Security group for CFI Platform RDS PostgreSQL"
  vpc_id      = var.vpc_id

  ingress {
    description = "PostgreSQL port"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.name}-db-sg"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_db_instance" "cfi" {
  identifier                  = "${var.name}-db"
  engine                      = "postgres"
  engine_version              = "16.2"
  instance_class              = var.instance_class
  allocated_storage           = var.allocated_storage
  max_allocated_storage       = var.max_allocated_storage
  storage_type                = "gp3"
  storage_encrypted           = true
  kms_key_id                  = var.kms_key_arn
  multi_az                    = true
  publicly_accessible         = false
  deletion_protection         = true
  backup_retention_period     = 7
  backup_window               = "03:00-04:00"
  maintenance_window          = "Mon:04:00-Mon:05:00"
  auto_minor_version_upgrade  = true
  db_subnet_group_name        = aws_db_subnet_group.cfi.name
  vpc_security_group_ids      = [aws_security_group.db.id]

  db_name  = var.db_name
  username = var.username
  password = var.password

  skip_final_snapshot = false
  final_snapshot_identifier = "${var.name}-db-final-snapshot"

  tags = {
    Name        = "${var.name}-db"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

output "db_instance_endpoint" {
  value       = aws_db_instance.cfi.endpoint
  description = "PostgreSQL connection endpoint"
}

output "db_instance_arn" {
  value       = aws_db_instance.cfi.arn
  description = "PostgreSQL instance ARN"
}
