#!/bin/bash

# Comprehensive local testing workflow
set -e

echo "🧪 Starting comprehensive build testing workflow..."

# Function to test individual service build
test_service_build() {
    local service=$1
    local dockerfile=$2
    local requirements_file=$3
    
    echo "🔍 Testing $service build..."
    
    # Check if requirements file exists
    if [ ! -f "$requirements_file" ]; then
        echo "❌ Requirements file not found: $requirements_file"
        return 1
    fi
    
    echo "📋 Dependencies for $service:"
    cat "$requirements_file"
    
    # Build the Docker image locally
    echo "🏗️ Building $service Docker image..."
    if docker build -f "$dockerfile" -t "local-test-$service" . --no-cache; then
        echo "✅ $service build successful!"
        
        # Clean up the test image
        docker rmi "local-test-$service" 2>/dev/null || true
        return 0
    else
        echo "❌ $service build failed!"
        return 1
    fi
}

# Test frontend build first
echo "🎨 Testing frontend build..."
cd frontend
npm install
npm run build
if [ -d "dist" ] && [ -f "dist/index.html" ]; then
    echo "✅ Frontend build successful!"
    cd ..
else
    echo "❌ Frontend build failed!"
    exit 1
fi

# Test all service builds
echo "🐳 Testing Docker builds for all services..."

# Test each service
test_service_build "agent-comms" "docker/Dockerfile.agent-comms" "requirements-agent-comms.txt"
test_service_build "task-generator" "docker/Dockerfile.task-generator" "requirements-task-generator.txt" 
test_service_build "task-solver" "docker/Dockerfile.task-solver" "requirements-task-solver.txt"
test_service_build "chat-interface" "docker/Dockerfile.chat-interface" "requirements-chat-interface.txt"

echo "✅ All builds completed successfully!"
echo "🚀 Ready to push to GitHub for CI/CD deployment"