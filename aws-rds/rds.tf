# The server refuses connections without TLS. The client adds sslmode=verify-full with global-bundle.pem.
resource "aws_db_parameter_group" "db" {
  name   = "${var.project_name}-pg${var.engine_version}"
  family = "postgres${var.engine_version}"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot" # AWS reports this method; "immediate" gives a diff on every plan
  }
}

resource "aws_db_instance" "db" {
  identifier     = var.project_name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.master_username
  # RDS creates and rotates the password in Secrets Manager. It never goes into Terraform state.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.db.name
  publicly_accessible    = false

  backup_retention_period   = var.backup_retention_days
  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  final_snapshot_identifier = "${var.project_name}-final"
}
