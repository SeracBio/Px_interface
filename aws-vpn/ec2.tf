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

# Managed prefix list for S3 in this region — used to scope egress to the S3 gateway
# endpoint (dnf) instead of opening 0.0.0.0/0.
data "aws_ec2_managed_prefix_list" "s3" {
  name = "com.amazonaws.${var.aws_region}.s3"
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

  # Egress is locked to only what user_data / the SSM agent actually initiate.
  # SGs are stateful, so inbound webapp requests are answered WITHOUT an egress rule;
  # removing 0.0.0.0/0 closes the path a compromised instance would use to pivot back
  # into the office LAN / VPN client pool over the VPN-propagated routes.
  egress {
    description = "HTTPS to interface VPC endpoints (SSM/ssmmessages/ec2messages) in-VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description     = "HTTPS to S3 via the gateway endpoint (AL2023 dnf repos)"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3.id]
  }
  egress {
    description = "DNS (UDP) to the VPC resolver: resolve endpoint private hostnames"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description = "DNS (TCP) to the VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description = "NTP to Amazon Time Sync (avoid clock skew for SigV4)"
    from_port   = 123
    to_port     = 123
    protocol    = "udp"
    cidr_blocks = ["169.254.169.123/32"]
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
        aws_ssm_parameter.tls_key.arn,
        aws_ssm_parameter.htpasswd.arn
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
# Basic-Auth hash — prefer a local ~/.serac_aws file (one line: "serac_user:$2y$...") so you
# don't have to export TF_VAR_webapp_htpasswd_hash on every apply; fall back to the variable
# if the file is absent. sensitive() keeps it out of plan output.
# -------------------------------------------------------
locals {
  _htpasswd_file       = pathexpand("~/.serac_aws")
  webapp_htpasswd_hash = sensitive(fileexists(local._htpasswd_file) ? trimspace(file(local._htpasswd_file)) : var.webapp_htpasswd_hash)
}

# Basic-Auth hash kept in SSM (like the TLS cert/key) rather than baked into user_data —
# user_data is readable by anything on the box via IMDS, so secrets don't belong there.
# The instance pulls it at boot via the same fetch_param path as the cert/key.
resource "aws_ssm_parameter" "htpasswd" {
  name        = "/${var.project_name}/webapp/htpasswd"
  description = "nginx basic-auth hash (serac_user:bcrypt) for the Px interface"
  type        = "SecureString"
  value       = local.webapp_htpasswd_hash

  tags = {
    Project = var.project_name
  }
}

# -------------------------------------------------------
# User data — drops a systemd-driven provisioner that waits for network-online and
# retries until the VPC endpoints answer, so boot never races cloud-init (see the .tftpl).
# -------------------------------------------------------
locals {
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region  = var.aws_region
    project = var.project_name
    bucket  = aws_s3_bucket.interface.bucket
  })
}

# -------------------------------------------------------
# EC2 Instance (in the private subnet)
# -------------------------------------------------------
resource "aws_instance" "main" {
  ami                    = local.ami_id
  instance_type          = var.ec2_instance_type
  subnet_id              = aws_subnet.private.id
  private_ip             = var.ec2_private_ip # stable IP so the DNS A record survives replacements
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
  # user_data's dnf install / aws ssm / s3 sync calls have no route until the endpoints are
  # up, and the S3 read grant must exist before the boot-time interface pull.
  depends_on = [
    aws_ssm_parameter.tls_cert,
    aws_ssm_parameter.tls_key,
    aws_ssm_parameter.htpasswd,
    aws_vpc_endpoint.ssm,
    aws_vpc_endpoint.ssmmessages,
    aws_vpc_endpoint.ec2messages,
    aws_vpc_endpoint.s3,
    aws_iam_role_policy.s3_interface_read
  ]
}
