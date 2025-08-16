#!/bin/bash

# Test Python requirements locally without Docker
set -e

echo "🧪 Testing Python requirements for all services..."

test_python_requirements() {
    local service=$1
    local requirements_file=$2
    
    echo "🔍 Testing $service requirements..."
    
    if [ ! -f "$requirements_file" ]; then
        echo "❌ Requirements file not found: $requirements_file"
        return 1
    fi
    
    # Create temporary virtual environment
    echo "📦 Creating virtual environment for $service..."
    python3 -m venv "test-env-$service"
    source "test-env-$service/bin/activate"
    
    # Upgrade pip for faster installs
    pip install --upgrade pip
    
    # Install requirements with timing
    echo "⏱️ Installing $service dependencies..."
    START_TIME=$(date +%s)
    
    if pip install -r "$requirements_file"; then
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo "✅ $service dependencies installed in ${DURATION}s"
        
        # Test imports
        echo "🔍 Testing critical imports..."
        case $service in
            "agent-comms"|"task-generator"|"task-solver")
                python -c "import fastapi, uvicorn, kafka, requests, pydantic; print('✅ Core imports successful')"
                if [[ "$service" != "agent-comms" ]]; then
                    python -c "import langchain, langgraph; print('✅ LangGraph imports successful')"
                fi
                ;;
            "chat-interface")
                python -c "import fastapi, uvicorn, kafka, requests, pydantic; print('✅ Chat interface imports successful')"
                ;;
        esac
    else
        END_TIME=$(date +%s)
        DURATION=$((END_TIME - START_TIME))
        echo "❌ $service dependencies failed after ${DURATION}s"
        deactivate
        rm -rf "test-env-$service"
        return 1
    fi
    
    deactivate
    rm -rf "test-env-$service"
    echo "🧹 Cleaned up test environment for $service"
}

# Test all services
test_python_requirements "agent-comms" "requirements-agent-comms.txt"
test_python_requirements "task-generator" "requirements-task-generator.txt"
test_python_requirements "task-solver" "requirements-task-solver.txt"
test_python_requirements "chat-interface" "requirements-chat-interface.txt"

echo "✅ All Python requirements tests passed!"
echo "🚀 Dependencies are optimized and should build faster in CI/CD"