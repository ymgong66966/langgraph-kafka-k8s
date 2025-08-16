#!/bin/bash

# GitHub Actions Deployment Script for LangGraph Kafka K8s
# This script deploys using images built by GitHub Actions

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 LangGraph Kafka K8s Deployment (GitHub Actions)${NC}"
echo "=================================================="

# Check prerequisites
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"

# Check if we're in a git repo
if [ ! -d ".git" ]; then
    echo -e "${RED}❌ Error: Not in a git repository${NC}"
    exit 1
fi

# Check kubectl
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}❌ Error: kubectl not found${NC}"
    exit 1
fi

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ Error: aws CLI not found${NC}"
    exit 1
fi

# Check helm
if ! command -v helm &> /dev/null; then
    echo -e "${RED}❌ Error: helm not found${NC}"
    exit 1
fi

# Verify kubectl connection
echo "🔍 Checking kubectl connection..."
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}❌ Error: Cannot connect to Kubernetes cluster${NC}"
    echo "Run: aws eks update-kubeconfig --region us-east-2 --name my-small-cluster --profile personal"
    exit 1
fi

# Get GitHub username and repo name
GITHUB_USER=$(git remote get-url origin | sed -n 's#.*github\.com[:/]\([^/]*\)/.*#\1#p')
REPO_NAME=$(git remote get-url origin | sed -n 's#.*github\.com[:/][^/]*/\([^/]*\)\.git#\1#p')

if [ -z "$GITHUB_USER" ] || [ -z "$REPO_NAME" ]; then
    echo -e "${RED}❌ Error: Cannot determine GitHub username/repo from git remote${NC}"
    echo "Current remote: $(git remote get-url origin)"
    exit 1
fi

echo -e "${GREEN}✅ GitHub repo: ${GITHUB_USER}/${REPO_NAME}${NC}"

# Get current commit SHA
CURRENT_SHA=$(git rev-parse HEAD)
SHORT_SHA=$(echo $CURRENT_SHA | cut -c1-7)
BRANCH=$(git branch --show-current)

echo -e "${GREEN}✅ Current branch: ${BRANCH}${NC}"
echo -e "${GREEN}✅ Current commit: ${SHORT_SHA}${NC}"

# Check if on main/master branch
if [ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ]; then
    echo -e "${YELLOW}⚠️  Warning: Not on main/master branch. Images may not be available.${NC}"
    echo "Current branch: $BRANCH"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Set image configuration
REGISTRY="ghcr.io"
IMAGE_REPO="$REGISTRY/$GITHUB_USER/$REPO_NAME"
IMAGE_TAG="${BRANCH}-${SHORT_SHA}"

echo -e "${GREEN}📦 Using images from: ${IMAGE_REPO}/*:${IMAGE_TAG}${NC}"

# Prompt for OpenAI API key if not set
if [ -z "$OPENAI_API_KEY" ]; then
    echo -e "${YELLOW}🔑 OpenAI API key not set in environment${NC}"
    read -p "Enter your OpenAI API key: " -s OPENAI_API_KEY
    echo
    
    if [ -z "$OPENAI_API_KEY" ]; then
        echo -e "${RED}❌ Error: OpenAI API key is required${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✅ OpenAI API key configured${NC}"

# Update Helm dependencies
echo -e "${YELLOW}📦 Updating Helm dependencies...${NC}"
cd helm
helm dependency update
cd ..

# Deploy to Kubernetes
echo -e "${YELLOW}🚀 Deploying to Kubernetes...${NC}"

helm upgrade --install langgraph-kafka ./helm \
  --namespace langgraph \
  --create-namespace \
  --values helm/values-dev.yaml \
  --set image.repository="$IMAGE_REPO" \
  --set image.tag="$IMAGE_TAG" \
  --set env.openaiApiKey="$OPENAI_API_KEY" \
  --set env.kafkaBootstrapServers="langgraph-system-kafka:9092" \
  --set env.kafkaTopic="dev-langgraph-agent-events" \
  --set env.kafkaResultsTopic="dev-langgraph-task-results" \
  --set env.disableCheckpointer="true" \
  --timeout 600s

echo -e "${YELLOW}⏳ Waiting for pods to be ready...${NC}"
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=langgraph-kafka -n langgraph --timeout=300s

echo -e "${GREEN}✅ Deployment complete!${NC}"
echo
echo "📊 Pod Status:"
kubectl get pods -n langgraph

echo
echo "🌐 Services:"
kubectl get svc -n langgraph

echo
echo -e "${GREEN}🎉 Deployment successful!${NC}"
echo
echo "📝 Next steps:"
echo "1. Port forward chat interface: kubectl port-forward -n langgraph svc/langgraph-system-langgraph-kafka-chat-interface 8003:8003"
echo "2. Open browser: http://localhost:8003"
echo "3. Test the chat interface!"

echo
echo "🔍 Quick health check:"
echo "kubectl port-forward -n langgraph svc/langgraph-system-langgraph-kafka-task-generator 8001:8001 &"
echo "curl http://localhost:8001/health"