#!/usr/bin/env bash
# Health check for the Px interface AWS stack (vpn-project).
# Read-only: AWS describe calls + one SSM command on the box (systemctl is-active nginx).
# Usage:  bash healthcheck.sh
set -uo pipefail

REGION="eu-north-1"
TFDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0; WARN=0
ok()   { echo "  [OK]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }

echo "== Px interface AWS health check (region $REGION) =="

# Resolve resource IDs from Terraform outputs
IID=$(terraform -chdir="$TFDIR"   output -raw ec2_instance_id   2>/dev/null)
VPNID=$(terraform -chdir="$TFDIR" output -raw vpn_connection_id 2>/dev/null)
VPCID=$(terraform -chdir="$TFDIR" output -raw vpc_id            2>/dev/null)
echo "  instance=$IID  vpn=$VPNID  vpc=$VPCID"

echo "[1] EC2 instance"
read -r STATE SYS INS < <(aws ec2 describe-instance-status --region "$REGION" \
  --instance-ids "$IID" --query 'InstanceStatuses[0].[InstanceState.Name,SystemStatus.Status,InstanceStatus.Status]' \
  --output text 2>/dev/null)
[ "$STATE" = "running" ] && ok "state: running" || bad "state: ${STATE:-unknown}"
[ "$SYS" = "ok" ] && [ "$INS" = "ok" ] && ok "status checks: system=$SYS instance=$INS" \
  || warn "status checks: system=${SYS:-?} instance=${INS:-?} (may still be initialising)"

echo "[2] SSM agent"
PING=$(aws ssm describe-instance-information --region "$REGION" \
  --filters "Key=InstanceIds,Values=$IID" --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)
[ "$PING" = "Online" ] && ok "Session Manager reachable (Online)" || bad "SSM ping: ${PING:-not registered}"

echo "[3] VPC endpoints (expect 4 available: ssm, ssmmessages, ec2messages, s3)"
AVAIL=$(aws ec2 describe-vpc-endpoints --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPCID" --query 'VpcEndpoints[?State==`available`] | length(@)' --output text 2>/dev/null)
[ "$AVAIL" = "4" ] && ok "$AVAIL/4 endpoints available" || warn "$AVAIL/4 endpoints available"

echo "[4] Site-to-Site VPN"
VSTATE=$(aws ec2 describe-vpn-connections --region "$REGION" --vpn-connection-ids "$VPNID" \
  --query 'VpnConnections[0].State' --output text 2>/dev/null)
[ "$VSTATE" = "available" ] && ok "connection: available" || bad "connection: ${VSTATE:-unknown}"
UPCOUNT=$(aws ec2 describe-vpn-connections --region "$REGION" --vpn-connection-ids "$VPNID" \
  --query 'VpnConnections[0].VgwTelemetry[?Status==`UP`] | length(@)' --output text 2>/dev/null)
if [ "$UPCOUNT" -ge 1 ] 2>/dev/null; then ok "tunnels UP: $UPCOUNT/2"
else warn "tunnels UP: 0/2 (expected until IT finishes the FortiGate side)"; fi

echo "[5] nginx on the box (via SSM)"
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl is-active nginx"]' \
  --query 'Command.CommandId' --output text 2>/dev/null)
NGINX=""
if [ -n "${CID:-}" ]; then
  for _ in $(seq 1 15); do
    sleep 2
    S=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" --query 'Status' --output text 2>/dev/null)
    [ "$S" = "Success" ] && { NGINX=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" --instance-id "$IID" --query 'StandardOutputContent' --output text 2>/dev/null | tr -d '[:space:]'); break; }
    case "$S" in Failed|Cancelled|TimedOut) break;; esac
  done
fi
[ "$NGINX" = "active" ] && ok "nginx: active" || bad "nginx: ${NGINX:-unknown}"

echo "== summary: $PASS ok, $WARN warn, $FAIL fail =="
[ "$FAIL" -eq 0 ]
