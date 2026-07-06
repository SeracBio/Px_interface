# -------------------------------------------------------
# Monitoring — CloudWatch alarms on Site-to-Site VPN tunnel state
#
# TunnelState (AWS/VPN): 1 = UP, 0 = DOWN, per tunnel. Aggregated by VpnId:
#   Maximum < 1  -> no tunnel is up          (full outage, interface unreachable)
#   Minimum < 1  -> at least one tunnel down (redundancy lost, still reachable)
#
# Set var.alarm_email to receive notifications (confirm the SNS email once).
# Alarms cost ~$0.10/each/month; SNS email is free-tier.
# -------------------------------------------------------

variable "alarm_email" {
  description = "Email address for VPN alarm notifications (leave empty to skip the subscription)"
  type        = string
  default     = ""
}

resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-alarms"
  tags = { Project = var.project_name }
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Critical — both tunnels down => the interface is unreachable
resource "aws_cloudwatch_metric_alarm" "vpn_down" {
  alarm_name          = "${var.project_name}-vpn-connection-down"
  alarm_description   = "Both Site-to-Site VPN tunnels are DOWN (interface unreachable)."
  namespace           = "AWS/VPN"
  metric_name         = "TunnelState"
  dimensions          = { VpnId = aws_vpn_connection.main.id }
  statistic           = "Maximum"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = { Project = var.project_name }
}

# Warning — at least one tunnel down => redundancy lost (still reachable via the other)
resource "aws_cloudwatch_metric_alarm" "vpn_degraded" {
  alarm_name          = "${var.project_name}-vpn-tunnel-degraded"
  alarm_description   = "At least one Site-to-Site VPN tunnel is DOWN (redundancy lost)."
  namespace           = "AWS/VPN"
  metric_name         = "TunnelState"
  dimensions          = { VpnId = aws_vpn_connection.main.id }
  statistic           = "Minimum"
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 3
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = { Project = var.project_name }
}
