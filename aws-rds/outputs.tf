output "db_address" {
  description = "RDS endpoint name. It resolves to the private IP, which only the VPN can reach."
  value       = aws_db_instance.db.address
}

output "db_port" {
  description = "PostgreSQL port"
  value       = aws_db_instance.db.port
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.db.db_name
}

output "master_secret_arn" {
  description = "Secrets Manager secret with the master user name and password"
  value       = aws_db_instance.db.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "Security group of the database. A later client inside the VPC needs an ingress rule here."
  value       = aws_security_group.db.id
}
