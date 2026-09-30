# A copy of an existing database, restored from a snapshot into THIS stack's VPN networking.
# The source (seracbio-prod) sits in the default VPC and is publicly accessible. The copy is
# private, reachable only over the VPN, and it uses the force_ssl parameter group.
#
# Set restore_snapshot_identifier to build it. Leave it empty and this file creates nothing.

resource "aws_db_instance" "restored" {
  count = var.restore_snapshot_identifier == "" ? 0 : 1

  identifier          = var.restore_identifier
  snapshot_identifier = var.restore_snapshot_identifier
  instance_class      = var.instance_class

  # The engine, the master user and the databases all come from the snapshot.
  # Do not set engine_version, username or db_name here; each one causes a diff or an upgrade.
  storage_type      = var.restore_storage_type
  allocated_storage = var.restore_allocated_storage
  iops              = var.restore_storage_type == "gp3" ? null : var.restore_iops
  storage_encrypted = true
  kms_key_id        = var.restore_kms_key_id

  # RDS makes a NEW password in Secrets Manager and rotates it. The copy then stops sharing
  # the password of the source. The password never enters Terraform state.
  manage_master_user_password = true

  multi_az               = var.restore_multi_az
  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.db.name
  publicly_accessible    = false

  backup_retention_period   = var.backup_retention_days
  copy_tags_to_snapshot     = true
  deletion_protection       = var.restore_deletion_protection
  final_snapshot_identifier = "${var.restore_identifier}-final"

  lifecycle {
    # A newer snapshot id must never replace a live database. Change it on purpose only.
    ignore_changes = [snapshot_identifier]
  }
}
