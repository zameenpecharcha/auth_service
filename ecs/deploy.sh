#!/usr/bin/env bash
# =============================================================================
#  deploy-ecs.sh  —  Deploy auth-service to AWS ECS Fargate (minimal / free-ish)
#
#  No NLB. No custom VPC. Uses AWS default VPC + public IP on the task.
#  Cost: ~$0.30/day (Fargate 0.25vCPU + 0.5GB).  Stop task when done testing.
#
#  Prerequisites:  aws-cli v2, docker, jq
#  First run:  chmod +x deploy.sh && ./deploy.sh
# =============================================================================
set -euo pipefail

# Prevent Git Bash on Windows from converting /path/... arguments to Windows paths
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

# --- CONFIG ------------------------------------------------------------------
AWS_REGION="ap-south-1"
ECS_CLUSTER="zpc-cluster"
ECS_SERVICE="auth-service"
GRPC_PORT=50052
TASK_DEF_FILE="$(dirname "$0")/task-definition.json"
# -----------------------------------------------------------------------------

log()  { echo -e "\n\033[1;32m>>> $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m"; }
die()  { echo -e "\033[1;31m[ERROR] $*\033[0m" >&2; exit 1; }

command -v aws    >/dev/null 2>&1 || die "aws cli not found"
command -v docker >/dev/null 2>&1 || die "docker not found"
command -v jq     >/dev/null 2>&1 || die "jq not found (brew install jq / apt install jq)"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/auth-service"
IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || echo latest)"
FULL_IMAGE="${ECR_URI}:${IMAGE_TAG}"

log "Account: ${AWS_ACCOUNT_ID}  Region: ${AWS_REGION}"

# --- 1. ECR repo -------------------------------------------------------------
log "1/8  ECR repository"
aws ecr describe-repositories --repository-names auth-service \
    --region "${AWS_REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name auth-service \
        --region "${AWS_REGION}" --output text --query "repository.repositoryUri"

# --- 2. Build & push ---------------------------------------------------------
log "2/8  Build & push image -> ${FULL_IMAGE}"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin \
      "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker build --platform linux/amd64 \
  -t "${FULL_IMAGE}" -t "${ECR_URI}:latest" \
  "$(dirname "$0")/.."

docker push "${FULL_IMAGE}"
docker push "${ECR_URI}:latest"

# --- 3. Task execution role --------------------------------------------------
log "3/6  Task execution role"
ROLE_EXISTS=$(aws iam get-role --role-name ecsTaskExecutionRole \
  --query "Role.RoleName" --output text 2>/dev/null || echo "")

if [[ -z "${ROLE_EXISTS}" ]]; then
  log "Creating ecsTaskExecutionRole..."
  cat > trust-policy-$$.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
  aws iam create-role \
    --role-name ecsTaskExecutionRole \
    --assume-role-policy-document "file://trust-policy-$$.json" >/dev/null
  rm -f "trust-policy-$$.json"
  aws iam attach-role-policy \
    --role-name ecsTaskExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  log "ecsTaskExecutionRole created"
else
  log "ecsTaskExecutionRole already exists"
fi

# Grant logs:CreateLogGroup (not in the managed policy by default)
cat > cw-policy-$$.json << CWEOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:DescribeLogGroups"],
      "Resource": "*"
    }
  ]
}
CWEOF
aws iam put-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name zpc-cw-logs \
  --policy-document "file://cw-policy-$$.json" >/dev/null
rm -f "cw-policy-$$.json"

# Pre-create the CloudWatch log group (avoids race condition)
aws logs create-log-group \
  --log-group-name /ecs/auth-service \
  --region "${AWS_REGION}" 2>/dev/null || true
log "CloudWatch log group ready: /ecs/auth-service"

# --- 4. Register task definition ---------------------------------------------
log "5/8  Registering task definition"
TMP_TASK_DEF="tmp-task-def-$$.json"
jq \
  --arg img  "${FULL_IMAGE}" \
  --arg acct "${AWS_ACCOUNT_ID}" \
  --arg region "${AWS_REGION}" \
  '
    .containerDefinitions[0].image = $img |
    .executionRoleArn = ("arn:aws:iam::" + $acct + ":role/ecsTaskExecutionRole") |
    .containerDefinitions[0].logConfiguration.options["awslogs-region"] = $region
  ' "${TASK_DEF_FILE}" > "${TMP_TASK_DEF}"

TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json "file://${TMP_TASK_DEF}" \
  --region "${AWS_REGION}" \
  --query "taskDefinition.taskDefinitionArn" --output text)
rm -f "${TMP_TASK_DEF}"
log "Task def: ${TASK_DEF_ARN}"

# --- 6. Cluster --------------------------------------------------------------
log "6/8  ECS cluster: ${ECS_CLUSTER}"
aws ecs create-cluster \
  --cluster-name "${ECS_CLUSTER}" \
  --region "${AWS_REGION}" >/dev/null 2>&1 || true

# Verify cluster exists
CLUSTER_STATUS=$(aws ecs describe-clusters \
  --clusters "${ECS_CLUSTER}" \
  --region "${AWS_REGION}" \
  --query "clusters[0].status" --output text 2>/dev/null || echo "")
if [[ "${CLUSTER_STATUS}" != "ACTIVE" ]]; then
  die "Cluster ${ECS_CLUSTER} not found or not ACTIVE (status: ${CLUSTER_STATUS})"
fi
log "Cluster active: ${ECS_CLUSTER}"

# --- 7. Default VPC + security group (port 50052) ----------------------------
log "7/8  Default VPC networking (free — no custom VPC, no NLB)"
DEFAULT_VPC=$(aws ec2 describe-vpcs \
  --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" --output text --region "${AWS_REGION}")

SUBNET_FIRST=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${DEFAULT_VPC}" \
            "Name=mapPublicIpOnLaunch,Values=true" \
  --query "Subnets[0].SubnetId" --output text --region "${AWS_REGION}")

if [[ -z "${SUBNET_FIRST}" || "${SUBNET_FIRST}" == "None" ]]; then
  # Fallback: any subnet in the default VPC
  SUBNET_FIRST=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=${DEFAULT_VPC}" \
    --query "Subnets[0].SubnetId" --output text --region "${AWS_REGION}")
  warn "No auto-assign-public-IP subnet found; using ${SUBNET_FIRST} (ensure it has internet gateway)"
fi
[[ -z "${SUBNET_FIRST}" || "${SUBNET_FIRST}" == "None" ]] && die "No subnet found in default VPC ${DEFAULT_VPC}"
log "Using subnet: ${SUBNET_FIRST}  VPC: ${DEFAULT_VPC}"

SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=auth-service-grpc-sg" \
            "Name=vpc-id,Values=${DEFAULT_VPC}" \
  --query "SecurityGroups[0].GroupId" --output text \
  --region "${AWS_REGION}" 2>/dev/null || echo "")

if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  SG_ID=$(aws ec2 create-security-group \
    --group-name "auth-service-grpc-sg" \
    --description "gRPC ${GRPC_PORT} for auth-service testing" \
    --vpc-id "${DEFAULT_VPC}" \
    --region "${AWS_REGION}" \
    --query "GroupId" --output text)
  aws ec2 authorize-security-group-ingress \
    --group-id "${SG_ID}" \
    --protocol tcp --port "${GRPC_PORT}" --cidr 0.0.0.0/0 \
    --region "${AWS_REGION}" >/dev/null
  log "Security group created: ${SG_ID}  (TCP ${GRPC_PORT} open to 0.0.0.0/0)"
else
  log "Reusing security group: ${SG_ID}"
fi

# --- 8. Create / update ECS service ------------------------------------------
log "8/8  ECS service: ${ECS_SERVICE}"
NET_CFG="awsvpcConfiguration={subnets=[${SUBNET_FIRST}],securityGroups=[${SG_ID}],assignPublicIp=ENABLED}"

SERVICE_ACTIVE=$(aws ecs describe-services \
  --cluster "${ECS_CLUSTER}" --services "${ECS_SERVICE}" \
  --region "${AWS_REGION}" \
  --query "services[?status=='ACTIVE'].serviceArn" --output text 2>/dev/null || echo "")

if [[ -z "${SERVICE_ACTIVE}" ]]; then
  aws ecs create-service \
    --cluster "${ECS_CLUSTER}" \
    --service-name "${ECS_SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "${NET_CFG}" \
    --region "${AWS_REGION}" >/dev/null
else
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --region "${AWS_REGION}" >/dev/null
fi

# --- Wait + fetch the task public IP -----------------------------------------
log "Waiting for task to reach RUNNING state (up to 5 min)..."

TASK_ARN=""
for i in $(seq 1 30); do
  sleep 10
  TASK_ARN=$(aws ecs list-tasks \
    --cluster "${ECS_CLUSTER}" --service-name "${ECS_SERVICE}" \
    --region "${AWS_REGION}" \
    --query "taskArns[0]" --output text 2>/dev/null || echo "")

  if [[ -n "${TASK_ARN}" && "${TASK_ARN}" != "None" ]]; then
    TASK_STATUS=$(aws ecs describe-tasks \
      --cluster "${ECS_CLUSTER}" --tasks "${TASK_ARN}" \
      --region "${AWS_REGION}" \
      --query "tasks[0].lastStatus" --output text 2>/dev/null || echo "")
    echo "  [${i}] Task: ${TASK_ARN##*/}  Status: ${TASK_STATUS}"
    [[ "${TASK_STATUS}" == "RUNNING" ]] && break
    if [[ "${TASK_STATUS}" == "STOPPED" ]]; then
      STOP_REASON=$(aws ecs describe-tasks \
        --cluster "${ECS_CLUSTER}" --tasks "${TASK_ARN}" \
        --region "${AWS_REGION}" \
        --query "tasks[0].stoppedReason" --output text 2>/dev/null || echo "unknown")
      echo ""
      echo "  Service events:"
      aws ecs describe-services \
        --cluster "${ECS_CLUSTER}" --services "${ECS_SERVICE}" \
        --region "${AWS_REGION}" \
        --query "services[0].events[:5]" --output table 2>/dev/null || true
      die "Task STOPPED: ${STOP_REASON}"
    fi
  else
    echo "  [${i}] No tasks yet — checking service events..."
    aws ecs describe-services \
      --cluster "${ECS_CLUSTER}" --services "${ECS_SERVICE}" \
      --region "${AWS_REGION}" \
      --query "services[0].events[:3]" --output table 2>/dev/null || true
  fi
done

if [[ -z "${TASK_ARN}" || "${TASK_ARN}" == "None" ]]; then
  echo ""
  echo "  Service events:"
  aws ecs describe-services \
    --cluster "${ECS_CLUSTER}" --services "${ECS_SERVICE}" \
    --region "${AWS_REGION}" \
    --query "services[0].events[:5]" --output table 2>/dev/null || true
  die "No task started after 5 minutes"
fi

ENI_ID=$(aws ecs describe-tasks \
  --cluster "${ECS_CLUSTER}" --tasks "${TASK_ARN}" \
  --region "${AWS_REGION}" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value" \
  --output text)

PUBLIC_IP=$(aws ec2 describe-network-interfaces \
  --network-interface-ids "${ENI_ID}" \
  --region "${AWS_REGION}" \
  --query "NetworkInterfaces[0].Association.PublicIp" --output text)

# --- Done --------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  auth-service is RUNNING"
echo ""
echo "  Public IP  : ${PUBLIC_IP}"
echo "  gRPC port  : ${GRPC_PORT}"
echo ""
echo "  Set this in Render -> api_gateway -> Environment:"
echo "  AUTH_SERVICE_URL=${PUBLIC_IP}:${GRPC_PORT}"
echo ""
echo "  grpc_base_client uses insecure channel for non-443 ports"
echo "  — no code change needed."
echo ""
echo "  STOP task when done (saves money):"
echo "  aws ecs update-service --cluster ${ECS_CLUSTER} \\"
echo "      --service ${ECS_SERVICE} --desired-count 0 \\"
echo "      --region ${AWS_REGION}"
echo "============================================================"

