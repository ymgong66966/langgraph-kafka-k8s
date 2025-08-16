#!/bin/bash

# Test individual service build locally
# Usage: ./test-single-service.sh <service-name>

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <service-name>"
    echo "Available services: agent-comms, task-generator, task-solver, chat-interface"
    exit 1
fi

SERVICE=$1

case $SERVICE in
    "agent-comms")
        DOCKERFILE="docker/Dockerfile.agent-comms"
        REQUIREMENTS="requirements-agent-comms.txt"
        ;;
    "task-generator")
        DOCKERFILE="docker/Dockerfile.task-generator"
        REQUIREMENTS="requirements-task-generator.txt"
        ;;
    "task-solver")
        DOCKERFILE="docker/Dockerfile.task-solver"
        REQUIREMENTS="requirements-task-solver.txt"
        ;;
    "chat-interface")
        DOCKERFILE="docker/Dockerfile.chat-interface"
        REQUIREMENTS="requirements-chat-interface.txt"
        echo "🎨 Building frontend first..."
        cd frontend && npm install && npm run build && cd ..
        ;;
    *)
        echo "❌ Unknown service: $SERVICE"
        echo "Available services: agent-comms, task-generator, task-solver, chat-interface"
        exit 1
        ;;
esac

echo "🔍 Testing $SERVICE build..."
echo "📋 Using dockerfile: $DOCKERFILE"
echo "📋 Using requirements: $REQUIREMENTS"

# Verify files exist
if [ ! -f "$DOCKERFILE" ]; then
    echo "❌ Dockerfile not found: $DOCKERFILE"
    exit 1
fi

if [ ! -f "$REQUIREMENTS" ]; then
    echo "❌ Requirements file not found: $REQUIREMENTS"
    exit 1
fi

echo "📦 Dependencies:"
cat "$REQUIREMENTS"

# Build with timing
echo "🏗️ Building Docker image (this may take a few minutes)..."
START_TIME=$(date +%s)

if docker build -f "$DOCKERFILE" -t "local-test-$SERVICE" . --progress=plain; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "✅ $SERVICE build successful in ${DURATION}s!"
    
    # Clean up
    docker rmi "local-test-$SERVICE" 2>/dev/null || true
else
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "❌ $SERVICE build failed after ${DURATION}s!"
    exit 1
fi