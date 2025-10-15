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

echo -e "${YELLOW}Step 1: Checking if AWS Load Balancer Controller is installed...${NC}"
if kubectl get deployment -n kube-system aws-load-balancer-controller &> /dev/null; then
    echo -e "${GREEN}✓ AWS Load Balancer Controller already installed${NC}"
else
    echo -e "${YELLOW}Installing AWS Load Balancer Controller...${NC}"
    
    # Create IAM service account
    echo "Creating IAM service account..."
    eksctl create iamserviceaccount \
      --cluster=$CLUSTER_NAME \
      --namespace=kube-system \
      --name=aws-load-balancer-controller \
      --attach-policy-arn=arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess \
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
fi

echo ""
echo -e "${YELLOW}Step 2: Deleting old LoadBalancer service (if exists)...${NC}"
kubectl delete svc chat-interface-external -n $NAMESPACE --ignore-not-found=true
echo -e "${GREEN}✓ Old service deleted${NC}"

echo ""
echo -e "${YELLOW}Step 3: Applying ClusterIP service...${NC}"
kubectl apply -f k8s/chat-interface-service.yaml
echo -e "${GREEN}✓ ClusterIP service created${NC}"

echo ""
echo -e "${YELLOW}Step 4: Applying Ingress resource...${NC}"
kubectl apply -f k8s/loadbalancer-service.yaml
echo -e "${GREEN}✓ Ingress resource created${NC}"

echo ""
echo -e "${YELLOW}Step 5: Waiting for ALB to be provisioned (this takes 2-3 minutes)...${NC}"
echo "Checking Ingress status..."

# Wait for Ingress to get an address
COUNTER=0
MAX_WAIT=180  # 3 minutes
while [ $COUNTER -lt $MAX_WAIT ]; do
    ALB_URL=$(kubectl get ingress chat-interface-ingress -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
    
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
    echo "  kubectl describe ingress chat-interface-ingress -n $NAMESPACE"
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
