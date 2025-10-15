#!/bin/bash
# Quick deployment script for AWS Load Balancer Controller + Ingress setup

set -e

echo "🚀 Starting AWS Load Balancer Controller + Ingress Setup"
echo "=================================================="

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
CLUSTER_NAME="my-small-cluster"
REGION="us-east-2"
NAMESPACE="langgraph"
AWS_ACCOUNT_ID="216989110335"
AWS_PROFILE="withcare-mgmt"
ROLE_ARN="arn:aws:iam::216989110335:role/OrganizationAccountAccessRole"

# Set AWS profile
export AWS_PROFILE=$AWS_PROFILE

echo -e "${YELLOW}Using AWS Profile: $AWS_PROFILE${NC}"
echo -e "${YELLOW}Target Account: $AWS_ACCOUNT_ID (WithCare Dev)${NC}"

# Verify AWS credentials
echo -e "${YELLOW}Verifying base AWS credentials...${NC}"
aws sts get-caller-identity --profile $AWS_PROFILE
echo ""

# Assume role to access WithCare Dev account
echo -e "${YELLOW}Assuming role to access WithCare Dev account...${NC}"
CREDENTIALS=$(aws sts assume-role \
  --role-arn $ROLE_ARN \
  --role-session-name "eks-deployment-session" \
  --profile $AWS_PROFILE \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
  --output text)

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Failed to assume role${NC}"
    exit 1
fi

# Export temporary credentials
export AWS_ACCESS_KEY_ID=$(echo $CREDENTIALS | awk '{print $1}')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDENTIALS | awk '{print $2}')
export AWS_SESSION_TOKEN=$(echo $CREDENTIALS | awk '{print $3}')

# Verify assumed role
echo -e "${YELLOW}Verifying assumed role identity...${NC}"
aws sts get-caller-identity
echo -e "${GREEN}✓ Successfully assumed role${NC}"
echo ""

# Update kubeconfig to use the assumed role credentials
echo -e "${YELLOW}Updating kubeconfig for EKS cluster...${NC}"
aws eks update-kubeconfig --name $CLUSTER_NAME --region $REGION
echo -e "${GREEN}✓ Kubeconfig updated${NC}"
echo ""

echo -e "${YELLOW}Step 1: Checking if AWS Load Balancer Controller is installed...${NC}"
if kubectl get deployment -n kube-system aws-load-balancer-controller &> /dev/null; then
    echo -e "${GREEN}✓ AWS Load Balancer Controller already installed${NC}"
else
    echo -e "${YELLOW}Installing AWS Load Balancer Controller...${NC}"
    
    # Download the official IAM policy if not exists
    if [ ! -f iam_policy.json ]; then
        echo "Downloading official AWS Load Balancer Controller IAM policy..."
        curl -o iam_policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.0/docs/install/iam_policy.json
    fi
    
    # Create the IAM policy (ignore error if it already exists)
    echo "Creating IAM policy in AWS account..."
    aws iam create-policy \
        --policy-name AWSLoadBalancerControllerIAMPolicy \
        --policy-document file://iam_policy.json 2>/dev/null || echo "Policy already exists, continuing..."
    
    # Create IAM service account (using assumed role credentials from env vars)
    echo "Creating IAM service account with proper permissions..."
    eksctl create iamserviceaccount \
      --cluster=$CLUSTER_NAME \
      --namespace=kube-system \
      --name=aws-load-balancer-controller \
      --attach-policy-arn=arn:aws:iam::$AWS_ACCOUNT_ID:policy/AWSLoadBalancerControllerIAMPolicy \
      --override-existing-serviceaccounts \
      --approve \
      --region=$REGION
    
    # Add EKS chart repo
    echo "Adding EKS Helm repository..."
    helm repo add eks https://aws.github.io/eks-charts
    helm repo update
    
    # Install controller
    echo "Installing AWS Load Balancer Controller via Helm..."
    helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
      -n kube-system \
      --set clusterName=$CLUSTER_NAME \
      --set serviceAccount.create=false \
      --set serviceAccount.name=aws-load-balancer-controller
    
    echo -e "${GREEN}✓ AWS Load Balancer Controller installed${NC}"
    
    # Wait for controller to be ready
    echo -e "${YELLOW}Waiting for AWS Load Balancer Controller to be ready...${NC}"
    kubectl wait --for=condition=available --timeout=120s deployment/aws-load-balancer-controller -n kube-system
    
    # Wait for webhook service to have endpoints
    echo -e "${YELLOW}Waiting for webhook service endpoints...${NC}"
    sleep 10
    echo -e "${GREEN}✓ Controller is ready${NC}"
fi

echo ""
echo -e "${YELLOW}Step 2: Checking for existing Helm-deployed Ingress...${NC}"
INGRESS_NAME=$(kubectl get ingress -n $NAMESPACE -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$INGRESS_NAME" ]; then
    echo "No Ingress found. Creating one..."
    echo -e "${YELLOW}Step 3: Deleting old LoadBalancer service (if exists)...${NC}"
    kubectl delete svc chat-interface-external -n $NAMESPACE --ignore-not-found=true
    echo -e "${GREEN}✓ Old service deleted${NC}"
    
    echo ""
    echo -e "${YELLOW}Step 4: Applying ClusterIP service...${NC}"
    kubectl apply -f k8s/chat-interface-service.yaml
    echo -e "${GREEN}✓ ClusterIP service created${NC}"
    
    echo ""
    echo -e "${YELLOW}Step 5: Applying Ingress resource...${NC}"
    kubectl apply -f k8s/loadbalancer-service.yaml
    echo -e "${GREEN}✓ Ingress resource created${NC}"
    INGRESS_NAME="chat-interface-ingress"
else
    echo -e "${GREEN}✓ Found existing Ingress: $INGRESS_NAME${NC}"
    echo "Using Helm-deployed Ingress (no need to create new resources)"
fi

echo ""
echo -e "${YELLOW}Step 6: Waiting for ALB to be provisioned (this takes 2-3 minutes)...${NC}"
echo "Checking Ingress status for: $INGRESS_NAME"

# Wait for Ingress to get an address
COUNTER=0
MAX_WAIT=180  # 3 minutes
while [ $COUNTER -lt $MAX_WAIT ]; do
    ALB_URL=$(kubectl get ingress $INGRESS_NAME -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
    
    if [ ! -z "$ALB_URL" ]; then
        echo -e "${GREEN}✓ ALB provisioned successfully!${NC}"
        break
    fi
    
    echo -n "."
    sleep 5
    COUNTER=$((COUNTER + 5))
done

echo ""

if [ -z "$ALB_URL" ]; then
    echo -e "${RED}✗ Timeout waiting for ALB. Check the Ingress status:${NC}"
    echo "  kubectl describe ingress $INGRESS_NAME -n $NAMESPACE"
    exit 1
fi

echo ""
echo "=================================================="
echo -e "${GREEN}🎉 Setup Complete!${NC}"
echo "=================================================="
echo ""
echo -e "${GREEN}Your stable ALB URL is:${NC}"
echo -e "${YELLOW}http://$ALB_URL${NC}"
echo ""
echo "Test your endpoint:"
echo -e "${YELLOW}curl http://$ALB_URL/health${NC}"
echo ""
echo "Or test the API:"
echo -e "${YELLOW}curl -X POST http://$ALB_URL/external/send \\
  -H 'Content-Type: application/json' \\
  -d '{\"user_id\": \"test\", \"messages\": [{\"role\": \"user\", \"text\": \"hello\"}]}'${NC}"
echo ""
echo "=================================================="
echo "This URL will remain stable across deployments!"
echo "=================================================="
