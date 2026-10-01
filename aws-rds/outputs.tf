output "db_address" {
  description = "RDS endpoint name. It resolves to the private IP, which only the VPN can reach."
  value       = try(aws_db_instance.restored[0].address, "")
}

output "db_port" {
  description = "PostgreSQL port"
  value       = try(aws_db_instance.restored[0].port, 0)
}

output "db_name" {
  description = "Database name"
  value       = try(aws_db_instance.restored[0].db_name, "")
}

output "security_group_id" {
  description = "Security group of the database. A later client inside the VPC needs an ingress rule here."
  value       = aws_security_group.db.id
}

output "restored_endpoint" {
  description = "Address of the restored copy. Empty when restore.tf creates nothing."
  value       = try(aws_db_instance.restored[0].address, "")
}

output "dev_endpoint" {
  description = "Address of the development database. Empty when create_dev is false."
  value       = try(aws_db_instance.dev[0].address, "")
}
