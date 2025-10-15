# Manual Deployment Commands with AWS Profile

Use these commands if you prefer to run step-by-step instead of using the automated script.

## Prerequisites

Set your AWS profile, assume role, and update kubeconfig:

```bash
export AWS_PROFILE=withcare-mgmt

# Verify base credentials
aws sts get-caller-identity --profile withcare-mgmt

# Assume role to access WithCare Dev account (216989110335)
CREDENTIALS=$(aws sts assume-role \
  --role-arn arn:aws:iam::216989110335:role/OrganizationAccountAccessRole \
  --role-session-name "eks-deployment-session" \
  --profile withcare-mgmt \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
  --output text)

# Export temporary credentials
export AWS_ACCESS_KEY_ID=$(echo $CREDENTIALS | awk '{print $1}')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDENTIALS | awk '{print $2}')
export AWS_SESSION_TOKEN=$(echo $CREDENTIALS | awk '{print $3}')

# Verify assumed role (should show account 216989110335)
aws sts get-caller-identity

# Update kubeconfig for EKS access
aws eks update-kubeconfig --name my-small-cluster --region us-east-2

# Test kubectl access
kubectl get nodes
```

## Step 1: Check if AWS Load Balancer Controller is installed

```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
```

If you see a deployment, **skip to Step 3**. Otherwise, continue to Step 2.

## Step 2: Install AWS Load Balancer Controller

### Option A: If you have eksctl installed

```bash
# Create IAM service account
eksctl create iamserviceaccount \
  --cluster=my-small-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess \
  --override-existing-serviceaccounts \
  --approve \
  --region=us-east-2 \
  --profile=withcare-mgmt

# Add Helm repo
helm repo add eks https://aws.github.io/eks-charts
helm repo update

# Install controller
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=my-small-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

# Verify installation
kubectl get deployment -n kube-system aws-load-balancer-controller
kubectl logs -n kube-system deployment/aws-load-balancer-controller
```

### Option B: Manual IAM setup (if eksctl doesn't work)

```bash
# 1. Download IAM policy
curl -o iam_policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.0/docs/install/iam_policy.json

# 2. Create IAM policy (using withcare-mgmt profile)
aws iam create-policy \
    --policy-name AWSLoadBalancerControllerIAMPolicy \
    --policy-document file://iam_policy.json \
    --profile withcare-mgmt

# 3. Create service account with IRSA
eksctl create iamserviceaccount \
  --cluster=my-small-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::216989110335:policy/AWSLoadBalancerControllerIAMPolicy \
  --override-existing-serviceaccounts \
  --approve \
  --region=us-east-2 \
  --profile=withcare-mgmt

# 4. Install controller via Helm
helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=my-small-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

## Step 3: Delete old LoadBalancer service

```bash
kubectl delete svc chat-interface-external -n langgraph --ignore-not-found=true
```

## Step 4: Apply ClusterIP service

```bash
kubectl apply -f k8s/chat-interface-service.yaml

# Verify
kubectl get svc -n langgraph chat-interface-service
```

## Step 5: Apply Ingress resource

```bash
kubectl apply -f k8s/loadbalancer-service.yaml

# Watch for ALB provisioning (takes 2-3 minutes)
kubectl get ingress chat-interface-ingress -n langgraph -w
```

Press `Ctrl+C` once you see an ADDRESS populated.

## Step 6: Get your stable ALB URL

```bash
export ALB_URL=$(kubectl get ingress chat-interface-ingress -n langgraph -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

echo "Your stable ALB URL: http://$ALB_URL"
```

## Step 7: Test your endpoint

```bash
# Health check
curl http://$ALB_URL/health

# API test
curl -X POST http://$ALB_URL/external/send \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user",
    "messages": [
      {"role": "user", "text": "hello"}
    ]
  }'
```

## Troubleshooting

### If eksctl fails with AWS credentials

Make sure you're using the correct profile:

```bash
# Check current identity
aws sts get-caller-identity --profile withcare-mgmt

# Should show:
# Account: 975050127340 (base account)
# User: ygong

# The eksctl command will automatically assume the role to access
# the WithCare Dev account (216989110335) where the EKS cluster is
```

### If kubectl commands fail

```bash
# Re-update kubeconfig
aws eks update-kubeconfig --name my-small-cluster --region us-east-2 --profile withcare-mgmt

# Test access
kubectl get nodes
kubectl get pods -n langgraph
```

### If Ingress doesn't get an ADDRESS

```bash
# Check Ingress events
kubectl describe ingress chat-interface-ingress -n langgraph

# Check controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller --tail=50

# Check controller status
kubectl get deployment -n kube-system aws-load-balancer-controller
```

### If ALB is created but not routing traffic

```bash
# Check target group health in AWS Console
# Navigate to: EC2 → Load Balancers → Your ALB → Target Groups

# Check pods
kubectl get pods -n langgraph -l app.kubernetes.io/component=chat-interface

# Check service endpoints
kubectl get endpoints -n langgraph chat-interface-service

# Check pod logs
kubectl logs -n langgraph -l app.kubernetes.io/component=chat-interface
```

## Quick Reference

```bash
# Set profile
export AWS_PROFILE=withcare-mgmt

# Update kubeconfig
aws eks update-kubeconfig --name my-small-cluster --region us-east-2 --profile withcare-mgmt

# Check Ingress status
kubectl get ingress -n langgraph

# Get ALB URL
kubectl get ingress chat-interface-ingress -n langgraph -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'

# Check all resources
kubectl get all -n langgraph
```
