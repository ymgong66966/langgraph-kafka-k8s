# GitHub Actions Deployment Guide

This guide explains how to deploy LangGraph Kafka K8s using GitHub Actions and GitHub Container Registry (GHCR).

## 🎯 Benefits of This Approach

✅ **No Docker Desktop needed** - Builds happen in GitHub cloud  
✅ **Automatic CI/CD** - Push code → Auto build → Auto deploy  
✅ **Cross-platform builds** - ARM64 → AMD64 handled automatically  
✅ **Free for public repos** - GitHub Actions + GHCR  
✅ **No corporate restrictions** - Uses personal GitHub account  

## 📋 Prerequisites

1. **Personal GitHub account**
2. **AWS EKS cluster** (your `my-small-cluster`)
3. **kubectl configured** with AWS credentials
4. **OpenAI API key**

## 🔧 Setup Steps

### Step 1: Create GitHub Repository

1. **Create new repo** on GitHub (public recommended for free Actions):
   ```
   Repository name: langgraph-kafka-k8s
   Visibility: Public (for free GitHub Actions)
   ```

2. **Push your code**:
   ```bash
   cd /Users/xyxg025/langgraph-kafka-k8s
   git init
   git add .
   git commit -m "Initial commit: LangGraph Kafka K8s system"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/langgraph-kafka-k8s.git
   git push -u origin main
   ```

### Step 2: Configure GitHub Secrets

Go to your GitHub repo → Settings → Secrets and variables → Actions

**Add these Repository Secrets:**

1. **OPENAI_API_KEY**: `sk-your-openai-key-here`
2. **AWS_ACCESS_KEY_ID**: Your AWS access key
3. **AWS_SECRET_ACCESS_KEY**: Your AWS secret key

**To get AWS credentials:**
```bash
# Get your current credentials
aws configure get aws_access_key_id --profile personal
aws configure get aws_secret_access_key --profile personal
```

### Step 3: Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. GitHub will automatically detect the workflow in `.github/workflows/build-and-push.yml`
3. **Enable Actions** if prompted

## 🚀 Deployment Process

### Option A: Automatic Deployment (Recommended)

**Just push to main branch:**
```bash
git add .
git commit -m "Deploy chat interface system"
git push origin main
```

GitHub Actions will:
1. ✅ Build all 4 Docker images (cross-platform)
2. ✅ Push to GHCR automatically  
3. ✅ Deploy to your K8s cluster
4. ✅ Verify deployment health

**Monitor progress:**
- Go to GitHub repo → Actions tab
- Watch the build/deploy progress live

### Option B: Manual Deployment

**If you want to deploy specific commit locally:**
```bash
# Make sure you're on the right commit
git checkout main
git pull origin main

# Run deployment script
./deploy-from-github.sh
```

## 📊 Workflow Architecture

```
GitHub Push → GitHub Actions → Build Images → Push to GHCR → Deploy to K8s
     ↓              ↓              ↓             ↓            ↓
  Your Code    Ubuntu Runner   4 Docker Images  Registry   EKS Cluster
                              (task-gen, solver              (all pods)
                               agent-comms, 
                               chat-interface)
```

## 🔍 Verification Steps

**After deployment, verify:**

1. **Check Actions status**: GitHub repo → Actions (should be ✅ green)

2. **Check K8s pods**:
   ```bash
   kubectl get pods -n langgraph
   # Should show all pods Running
   ```

3. **Test chat interface**:
   ```bash
   kubectl port-forward -n langgraph svc/langgraph-system-langgraph-kafka-chat-interface 8003:8003 &
   open http://localhost:8003
   ```

4. **Test full workflow**:
   - Send message in chat interface
   - Should see immediate user message
   - Should see agent response within 10-30 seconds

## 🐛 Troubleshooting

### GitHub Actions Failed

**Check the Actions log:**
1. Go to repo → Actions → Click failed run
2. Check which step failed
3. Common issues:
   - **Secrets not set**: Add missing AWS/OpenAI secrets
   - **Permissions**: Ensure GITHUB_TOKEN has package write permission
   - **Build errors**: Check Dockerfile syntax

### Deployment Failed

**Local debugging:**
```bash
# Check what images were built
kubectl describe pod -n langgraph langgraph-system-langgraph-kafka-chat-interface-xxx

# Check if GHCR images exist
# Go to: https://github.com/YOUR_USERNAME/langgraph-kafka-k8s/pkgs/container/langgraph-kafka-k8s%2Fchat-interface
```

### K8s Pods Not Starting

**Same issues as before:**
- Check logs: `kubectl logs -n langgraph POD_NAME`
- Check image pull: `kubectl describe pod -n langgraph POD_NAME`
- SASL auth issues: Already fixed in values-dev.yaml
- Checkpointer issues: Already disabled

## 🎯 Complete Deployment Workflow

**One-time setup:**
1. Create GitHub repo and push code
2. Add GitHub secrets (AWS + OpenAI)
3. Enable GitHub Actions

**Every deployment:**
1. Make changes to code
2. `git push origin main`
3. Watch GitHub Actions build and deploy
4. Test at `http://localhost:8003` (after port forward)

**The system automatically handles:**
- ✅ Cross-platform builds (ARM64 → AMD64)
- ✅ Registry authentication
- ✅ Multi-service deployment
- ✅ Kafka SASL authentication fixes
- ✅ LangGraph checkpointer configuration

## 🏗️ Image Registry

**Your images will be stored at:**
```
ghcr.io/YOUR_USERNAME/langgraph-kafka-k8s/task-generator:main-abc1234
ghcr.io/YOUR_USERNAME/langgraph-kafka-k8s/agent-comms:main-abc1234  
ghcr.io/YOUR_USERNAME/langgraph-kafka-k8s/task-solver:main-abc1234
ghcr.io/YOUR_USERNAME/langgraph-kafka-k8s/chat-interface:main-abc1234
```

**Tags format:**
- `latest` - Latest main branch
- `main-abc1234` - Specific commit SHA
- `feature-xyz-abc1234` - Feature branch builds