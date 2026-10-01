# An EMPTY development database, in the same VPN networking as the prod copy. It shares the
# subnet group, the security group and the force_ssl parameter group, so it needs no new network.
# The password comes from ~/.px_db_dev_password, one line "user:password", like prod.

locals {
  _dev_pw_file = pathexpand("~/.px_db_dev_password")
  _dev_pw_raw  = fileexists(local._dev_pw_file) ? trimspace(file(local._dev_pw_file)) : var.dev_master_password
  dev_master_password = sensitive(
    can(regex("^[^:]+:(.+)$", local._dev_pw_raw)) ? regex("^[^:]+:(.+)$", local._dev_pw_raw)[0] : local._dev_pw_raw
  )
}

resource "aws_db_instance" "dev" {
  count = var.create_dev ? 1 : 0

  identifier     = var.dev_identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.dev_instance_class

  allocated_storage = var.dev_allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  # No db_name, so the instance holds only `postgres`. This matches the prod copy.
  username = var.dev_master_username
  password = local.dev_master_password

  multi_az               = false
  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.db.name
  publicly_accessible    = false

  backup_retention_period   = var.backup_retention_days
  copy_tags_to_snapshot     = true
  deletion_protection       = var.dev_deletion_protection
  final_snapshot_identifier = "${var.dev_identifier}-final"
}
