# Deployment Summary: Stable ALB with Ingress

## What Was Created

### 1. Kubernetes Resources
- **`k8s/chat-interface-service.yaml`** - ClusterIP service for internal routing
- **`k8s/loadbalancer-service.yaml`** - Ingress resource (already updated by you)

### 2. Helm Templates
- **`helm/templates/chat-interface-service.yaml`** - Already existed, no changes needed
- **`helm/templates/chat-interface-ingress.yaml`** - NEW: Ingress template for Helm deployments

### 3. Configuration
- **`helm/values.yaml`** - Updated with ingress configuration

### 4. Documentation
- **`INGRESS_SETUP_GUIDE.md`** - Comprehensive setup guide
- **`QUICK_DEPLOY.sh`** - Automated deployment script
- **`DEPLOYMENT_SUMMARY.md`** - This file

## Quick Start (Choose One Method)

### Method 1: Automated Script (Easiest)

```bash
cd /Users/xyxg025/langgraph-kafka-k8s
./QUICK_DEPLOY.sh
```

This script will:
1. Check/install AWS Load Balancer Controller
2. Delete old LoadBalancer service
3. Create ClusterIP service
4. Create Ingress resource
5. Wait for ALB provisioning
6. Display your stable URL

### Method 2: Manual Steps

```bash
# 1. Install AWS Load Balancer Controller (if not installed)
eksctl create iamserviceaccount \
  --cluster=my-small-cluster \
  --namespace=kube-system \
  --name=aws-load-balancer-controller \
  --attach-policy-arn=arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess \
  --override-existing-serviceaccounts \
  --approve \
  --region=us-east-2

helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=my-small-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

# 2. Delete old service
kubectl delete svc chat-interface-external -n langgraph --ignore-not-found=true

# 3. Apply new resources
kubectl apply -f k8s/chat-interface-service.yaml
kubectl apply -f k8s/loadbalancer-service.yaml

# 4. Get your stable URL
kubectl get ingress chat-interface-ingress -n langgraph
```

### Method 3: Using Helm

```bash
# Deploy/upgrade with Helm
helm upgrade --install langgraph-kafka ./helm \
  --namespace langgraph \
  --create-namespace \
  --set chatInterface.ingress.enabled=true

# Get the ALB URL
kubectl get ingress -n langgraph
```

## What Changed

### Before (LoadBalancer Service)
- ❌ New random URL every time you redeploy
- ❌ Classic Load Balancer (older, less features)
- ❌ URL: `a1b2c3d4-123456789.us-east-2.elb.amazonaws.com` (changes)

### After (Ingress with ALB)
- ✅ **Stable URL that persists across deployments**
- ✅ Application Load Balancer (modern, more features)
- ✅ URL: `k8s-langgraph-chatinte-xxxxx-xxxxxxxxxx.us-east-2.elb.amazonaws.com` (stable)
- ✅ Better health checks
- ✅ Path-based routing support
- ✅ Easy to add HTTPS/SSL
- ✅ Easy to add custom domains

## Architecture

```
Internet
   ↓
AWS ALB (stable URL)
   ↓
Kubernetes Ingress (chat-interface-ingress)
   ↓
ClusterIP Service (chat-interface-service)
   ↓
Pods (chat-interface deployment)
```

## Getting Your Stable URL

After deployment, get your URL with:

```bash
kubectl get ingress chat-interface-ingress -n langgraph -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Or with a formatted output:

```bash
export ALB_URL=$(kubectl get ingress chat-interface-ingress -n langgraph -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "Your API endpoint: http://$ALB_URL"
```

## Testing Your Deployment

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

## Monitoring

```bash
# Check Ingress status
kubectl get ingress -n langgraph

# Check service endpoints
kubectl get endpoints -n langgraph chat-interface-service

# Check pods
kubectl get pods -n langgraph -l app.kubernetes.io/component=chat-interface

# View Ingress details
kubectl describe ingress chat-interface-ingress -n langgraph

# Check ALB controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller
```

## Troubleshooting

### Ingress has no ADDRESS

```bash
# Check Ingress events
kubectl describe ingress chat-interface-ingress -n langgraph

# Check controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller --tail=50
```

### ALB created but not routing traffic

```bash
# Check target group health in AWS Console
# Navigate to: EC2 → Load Balancers → Your ALB → Target Groups

# Check pod health
kubectl get pods -n langgraph -l app.kubernetes.io/component=chat-interface

# Check service endpoints
kubectl get endpoints -n langgraph chat-interface-service
```

### Need to update configuration

```bash
# Edit Ingress directly
kubectl edit ingress chat-interface-ingress -n langgraph

# Or update YAML and reapply
kubectl apply -f k8s/loadbalancer-service.yaml
```

## Next Steps

1. **Add HTTPS/SSL**
   - Request ACM certificate
   - Update Ingress annotations
   - See: INGRESS_SETUP_GUIDE.md

2. **Add Custom Domain**
   - Create Route53 CNAME record
   - Point to your ALB hostname
   - Example: `api.yourdomain.com` → ALB URL

3. **Enable Access Logs**
   - Configure S3 bucket
   - Add ALB logging annotations

4. **Set up WAF**
   - Create WAF rules
   - Attach to ALB

## Files Reference

- **Setup Guide**: `INGRESS_SETUP_GUIDE.md`
- **Quick Deploy**: `./QUICK_DEPLOY.sh`
- **Service**: `k8s/chat-interface-service.yaml`
- **Ingress**: `k8s/loadbalancer-service.yaml`
- **Helm Values**: `helm/values.yaml`
- **Helm Service**: `helm/templates/chat-interface-service.yaml`
- **Helm Ingress**: `helm/templates/chat-interface-ingress.yaml`

## Support

For issues or questions:
1. Check `INGRESS_SETUP_GUIDE.md` for detailed troubleshooting
2. Review AWS Load Balancer Controller logs
3. Check AWS Console for ALB/Target Group status
