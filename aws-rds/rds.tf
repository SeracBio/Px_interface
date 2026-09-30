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

# The px-rds placeholder proved that a database can live in the VPN VPC. It is deleted.
# The parameter group above stays: the restored copy in restore.tf uses it.
