# -------------------------------------------------------
# Self-signed TLS certificate for the internal webapp
#
# Because this is a VPN-only internal service there is no
# public domain, so a self-signed cert is appropriate.
# Employees will need to accept the browser warning once,
# or you can distribute the CA cert to their trust stores.
# -------------------------------------------------------

resource "tls_private_key" "webapp" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "webapp" {
  private_key_pem = tls_private_key.webapp.private_key_pem

  subject {
    common_name  = "internal.${var.project_name}.local"
    organization = var.tls_cert_org
  }

  # Valid from now
  validity_period_hours = var.tls_cert_validity_days * 24

  # Standard extensions for a server certificate
  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]

  is_ca_certificate = false
}

# Store the cert and key in AWS Systems Manager Parameter Store
# so they can be pulled by the EC2 instance at boot time.
# Both are marked SecureString (encrypted at rest via KMS default key).
resource "aws_ssm_parameter" "tls_cert" {
  name        = "/${var.project_name}/tls/cert"
  description = "Self-signed TLS certificate for the internal webapp"
  type        = "SecureString"
  value       = tls_self_signed_cert.webapp.cert_pem

  tags = {
    Project = var.project_name
  }
}

resource "aws_ssm_parameter" "tls_key" {
  name        = "/${var.project_name}/tls/key"
  description = "Private key for the internal webapp TLS certificate"
  type        = "SecureString"
  value       = tls_private_key.webapp.private_key_pem

  tags = {
    Project = var.project_name
  }
}
