# -------------------------------------------------------
# Latest Amazon Linux 2023 AMI (used when ec2_ami_id is not set)
# -------------------------------------------------------
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  ami_id = var.ec2_ami_id != "" ? var.ec2_ami_id : data.aws_ami.amazon_linux_2023.id
}

# -------------------------------------------------------
# Security Group for the EC2 instance
# -------------------------------------------------------
resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-ec2-sg"
  description = "Allow HTTP, HTTPS and ICMP from on-premises network over VPN"
  vpc_id      = aws_vpc.main.id

  # HTTP from on-premises network (redirected to HTTPS by nginx)
  ingress {
    description = "HTTP from on-premises"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr
  }

  # HTTPS from on-premises network
  ingress {
    description = "HTTPS from on-premises"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr
  }

  # ICMP (ping) from on-premises network
  ingress {
    description = "ICMP from on-premises"
    from_port   = -1
    to_port     = -1
    protocol    = "icmp"
    cidr_blocks = var.allowed_cidr
  }

  # Allow all outbound traffic (VPC endpoints handle the actual routing)
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-ec2-sg"
    Project = var.project_name
  }
}

# -------------------------------------------------------
# IAM Role — allows EC2 to read TLS cert/key from SSM
# -------------------------------------------------------
resource "aws_iam_role" "ec2" {
  name = "${var.project_name}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy" "ssm_tls" {
  name = "${var.project_name}-ssm-tls-read"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssm:GetParameter",
        "ssm:GetParameters"
      ]
      Resource = [
        aws_ssm_parameter.tls_cert.arn,
        aws_ssm_parameter.tls_key.arn
      ]
    },
    {
      # Allow use of the default SSM KMS key to decrypt SecureString params
      Effect   = "Allow"
      Action   = ["kms:Decrypt"]
      Resource = ["arn:aws:kms:${var.aws_region}:*:alias/aws/ssm"]
    }]
  })
}

# Attach SSM Session Manager policy so you can shell in without opening port 22
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-ec2-profile"
  role = aws_iam_role.ec2.name
}

# -------------------------------------------------------
# User data — installs Nginx and configures HTTPS at boot
# -------------------------------------------------------
locals {
  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail

    REGION="${var.aws_region}"
    PROJECT="${var.project_name}"

    # Resilience: cloud-init fires ~8s into boot, sometimes before the VPC
    # endpoints/routing are ready, so retry the network-dependent steps.
    retry() {
      max="$1"; delay="$2"; shift 2; n=1
      until "$@"; do
        if [ "$n" -ge "$max" ]; then echo "FAILED after $n attempts: $*" >&2; return 1; fi
        echo "attempt $n failed; retry in $delay s: $*" >&2; n=$((n + 1)); sleep "$delay"
      done
    }

    fetch_param() { # $1 = SSM param name, $2 = destination file
      aws ssm get-parameter --region "$REGION" --name "$1" \
        --with-decryption --query Parameter.Value --output text > "$2"
    }

    # Install Nginx (retry until package repos are reachable via the S3 endpoint)
    retry 30 10 dnf install -y nginx
    command -v aws >/dev/null 2>&1 || retry 5 10 dnf install -y awscli

    # Create directories before writing files
    mkdir -p /etc/nginx/ssl
    mkdir -p /var/www/webapp

    # Pull TLS cert and key from SSM (retry until the ssm endpoint is reachable)
    retry 30 10 fetch_param "/$PROJECT/tls/cert" /etc/nginx/ssl/webapp.crt
    retry 30 10 fetch_param "/$PROJECT/tls/key"  /etc/nginx/ssl/webapp.key

    chmod 600 /etc/nginx/ssl/webapp.key
    chmod 644 /etc/nginx/ssl/webapp.crt

    # Write .htpasswd for HTTP basic auth
    # Value is injected from the webapp_htpasswd_hash variable (bcrypt hash).
    # To regenerate: htpasswd -nbB serac_user 'yourpassword'
    echo '${var.webapp_htpasswd_hash}' > /etc/nginx/.htpasswd
    chmod 640 /etc/nginx/.htpasswd
    chown root:nginx /etc/nginx/.htpasswd

    # Write Nginx config — HTTPS only, HTTP redirects to HTTPS
    cat > /etc/nginx/conf.d/webapp.conf <<'NGINX'
server {
    listen 80 default_server;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl default_server;

    ssl_certificate     /etc/nginx/ssl/webapp.crt;
    ssl_certificate_key /etc/nginx/ssl/webapp.key;

    # Modern TLS only
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security headers.
    # NOTE: HSTS is intentionally omitted while using a self-signed cert — once a
    # browser records HSTS it removes the "proceed anyway" bypass on cert warnings,
    # which would hard-lock users. Re-enable it only after distributing a trusted
    # cert to employee trust stores:
    #   add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    root /var/www/webapp;
    index Serac_Px_interface.html;

    location / {
        auth_basic           "Serac Px Interface";
        auth_basic_user_file /etc/nginx/.htpasswd;
        try_files $uri $uri/ =404;
    }
}
NGINX

    # Placeholder entry page — replace with the real Serac_Px_interface.html
    cat > /var/www/webapp/Serac_Px_interface.html <<'HTML'
<!DOCTYPE html>
<html>
  <head><title>Serac Px Interface</title></head>
  <body><h1>Serac Px Interface — VPN Access Only</h1></body>
</html>
HTML

    # Validate config before starting
    nginx -t

    systemctl enable nginx
    systemctl start nginx
  EOF
}

# -------------------------------------------------------
# EC2 Instance (in the private subnet)
# -------------------------------------------------------
resource "aws_instance" "main" {
  ami                    = local.ami_id
  instance_type          = var.ec2_instance_type
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  # Attach key pair only when one is specified
  key_name = var.key_pair_name != "" ? var.key_pair_name : null

  user_data                   = local.user_data
  user_data_replace_on_change = true

  # IMDSv2 enforced for security
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 30 # AL2023 AMI snapshot is 30 GB; volume must be >= snapshot
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name    = "${var.project_name}-instance"
    Project = var.project_name
  }

  # Ensure the SSM params AND the VPC endpoints exist before the instance boots —
  # user_data's dnf install / aws ssm calls have no route until the endpoints are up.
  depends_on = [
    aws_ssm_parameter.tls_cert,
    aws_ssm_parameter.tls_key,
    aws_vpc_endpoint.ssm,
    aws_vpc_endpoint.ssmmessages,
    aws_vpc_endpoint.ec2messages,
    aws_vpc_endpoint.s3
  ]
}
