# AWS Load Balancer Controller + Ingress Setup Guide

This guide will help you set up a stable ALB (Application Load Balancer) for your chat-interface service using AWS Load Balancer Controller and Kubernetes Ingress.

## Prerequisites

- EKS cluster: `my-small-cluster` in `us-east-2`
- kubectl configured to access your cluster
- helm installed
- eksctl installed (optional, for IAM service account creation)

## Step 1: Install AWS Load Balancer Controller

### Check if already installed

```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
```

If you see a deployment, skip to Step 2. Otherwise, continue:

### Option A: Using eksctl (Recommended)

```bash
# Create IAM service account for AWS Load Balancer Controller
eksctl create iamserviceaccount \
  --cluster=my-small-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess \
  --override-existing-serviceaccounts \
  --approve \
  --region=us-east-2

# Add EKS chart repository
helm repo add eks https://aws.github.io/eks-charts
helm repo update

# Install AWS Load Balancer Controller
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=my-small-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

### Option B: Manual IAM Policy Creation

If eksctl doesn't work, create the IAM policy manually:

1. Download the IAM policy document:
```bash
curl -o iam_policy.json https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.7.0/docs/install/iam_policy.json
```

2. Create the IAM policy:
```bash
aws iam create-policy \
    --policy-name AWSLoadBalancerControllerIAMPolicy \
    --policy-document file://iam_policy.json \
    --region us-east-2
```

3. Create service account and install controller:
```bash
eksctl create iamserviceaccount \
  --cluster=my-small-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::216989110335:policy/AWSLoadBalancerControllerIAMPolicy \
  --override-existing-serviceaccounts \
  --approve \
  --region=us-east-2

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=my-small-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

### Verify Installation

```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
kubectl logs -n kube-system deployment/aws-load-balancer-controller
```

You should see the controller running and logs showing it's watching for Ingress resources.

## Step 2: Delete Old LoadBalancer Service

```bash
# Delete the old LoadBalancer service if it exists
kubectl delete svc chat-interface-external -n langgraph --ignore-not-found=true
```

## Step 3: Apply ClusterIP Service

```bash
# Apply the new ClusterIP service
kubectl apply -f k8s/chat-interface-service.yaml

# Verify service is created
kubectl get svc -n langgraph chat-interface-service
```

## Step 4: Apply Ingress Resource

```bash
# Apply the Ingress
kubectl apply -f k8s/loadbalancer-service.yaml

# Watch the Ingress until it gets an ADDRESS (takes 2-3 minutes)
kubectl get ingress chat-interface-ingress -n langgraph -w
```

Press `Ctrl+C` once you see an ADDRESS column populated.

## Step 5: Get Your Stable ALB URL

```bash
# Get the ALB hostname
export ALB_URL=$(kubectl get ingress chat-interface-ingress -n langgraph -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

echo "Your stable ALB URL is: http://$ALB_URL"
```

The URL will look like: `k8s-langgraph-chatinte-xxxxx-xxxxxxxxxx.us-east-2.elb.amazonaws.com`

**This URL will remain stable** across pod restarts and redeployments!

## Step 6: Test Your Endpoint

```bash
# Test health endpoint
curl http://$ALB_URL/health

# Test your API
curl -X POST http://$ALB_URL/external/send \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user",
    "messages": [
      {"role": "user", "text": "hello"}
    ]
  }'
```

## Step 7: (Optional) Add Custom Domain with Route53

If you want a custom domain like `api.yourdomain.com`:

1. Go to AWS Route53 console
2. Select your hosted zone
3. Create a new CNAME record:
   - Name: `api` (or your preferred subdomain)
   - Type: CNAME
   - Value: Your ALB hostname from Step 5
   - TTL: 300

Then you can access your API at: `http://api.yourdomain.com`

## Troubleshooting

### Ingress stuck in "Pending" state

```bash
# Check Ingress events
kubectl describe ingress chat-interface-ingress -n langgraph

# Check controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller
```

Common issues:
- IAM permissions not set correctly
- Security groups blocking traffic
- Subnets not tagged properly for ALB

### ALB not routing traffic

```bash
# Check target group health in AWS Console
# Or check pod status
kubectl get pods -n langgraph -l app.kubernetes.io/component=chat-interface

# Check service endpoints
kubectl get endpoints -n langgraph chat-interface-service
```

### Need to update Ingress

```bash
# Edit the ingress
kubectl edit ingress chat-interface-ingress -n langgraph

# Or update the YAML file and reapply
kubectl apply -f k8s/loadbalancer-service.yaml
```

## Using Helm (Alternative)

If you prefer using Helm for deployment:

```bash
# Update your Helm deployment
helm upgrade --install langgraph-kafka ./helm \
  --namespace langgraph \
  --create-namespace \
  --set chatInterface.ingress.enabled=true

# Get the ALB URL
kubectl get ingress -n langgraph
```

## Cleanup

To remove the Ingress and ALB:

```bash
# Delete Ingress (this will delete the ALB)
kubectl delete ingress chat-interface-ingress -n langgraph

# Optionally delete the service
kubectl delete svc chat-interface-service -n langgraph
```

## Next Steps

- Set up HTTPS with ACM (AWS Certificate Manager)
- Configure custom domain with Route53
- Set up WAF rules for security
- Enable access logs for the ALB
- Configure autoscaling based on ALB metrics
