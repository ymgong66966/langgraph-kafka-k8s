# Deployment Testing Guide

This directory contains a comprehensive test script to validate your LangGraph Kafka K8s deployment.

## 🚀 Quick Start

### 1. Install Test Dependencies
```bash
pip install -r test_requirements.txt
```

### 2. Run the Test Suite
```bash
python test_deployment.py
```

## 🧪 What the Test Script Does

The test script performs a comprehensive validation of your deployment:

### ✅ **Pod Health Checks**
- Verifies all pods are running (`1/1 Ready`)
- Displays pod status in a formatted table
- Checks resource allocation and node placement

### 🔗 **Service Connectivity Tests**
- Sets up automatic `kubectl port-forward` for all services
- Tests HTTP health endpoints (`/health`)
- Validates Kafka connectivity (producer/consumer)

### 🤖 **API Functionality Tests**
- **Agent Communication API** (`localhost:8000`)
  - Health check: `GET /health`
  - Message sending: `POST /send_message`
  - Metrics (if available): `GET /metrics`

- **Task Generator API** (`localhost:8001`)
  - Health check: `GET /health`
  - Task creation: `POST /generate_task`

- **Task Solver API** (`localhost:8002`)
  - Health check: `GET /health`
  - Task processing: `POST /solve_task`

### 📨 **Message Flow Monitoring**
- Monitors Kafka topics: `dev-langgraph-agent-events`, `dev-langgraph-task-results`
- Displays real-time message flow
- Validates end-to-end communication

### 📊 **Comprehensive Reporting**
- Real-time status updates with rich formatting
- Final summary report with health scores
- Detailed JSON report saved to file
- Color-coded status indicators

## 🔧 **Test Scenarios**

### Basic Health Test
```python
# The script automatically tests:
# ✅ Pod readiness and liveness
# ✅ Service accessibility
# ✅ Kafka broker connectivity
# ✅ API endpoint responses
```

### Message Flow Test
```python
# Tests end-to-end message processing:
# 1. Send test message via Agent Communication API
# 2. Monitor Kafka topics for message propagation
# 3. Verify task generation and solving
# 4. Check result delivery
```

### Load Test (Optional)
You can modify the script to run multiple concurrent tests:

```python
# Example: Test concurrent message processing
test_messages = [
    {"task": "math", "data": "What is 5+3?"},
    {"task": "text", "data": "Generate a greeting"},
    {"task": "logic", "data": "Is the sky blue?"}
]
```

## 📈 **Understanding Results**

### Status Indicators
- 🟢 **GREEN (✅)**: Service healthy and responding correctly
- 🟡 **YELLOW (⚠️)**: Service partially working or degraded performance  
- 🔴 **RED (❌)**: Service error or not accessible

### Success Criteria
- **100% Success**: All services healthy, full functionality
- **>75% Success**: Core functionality working, some optional features may be down
- **<75% Success**: Critical issues that need attention

## 🐛 **Troubleshooting**

### Common Issues

**Port-forward Failures:**
```bash
# Check if services exist
kubectl get svc -n langgraph

# Check if pods are running
kubectl get pods -n langgraph
```

**API Connection Errors:**
```bash
# Check pod logs
kubectl logs -l component=agent-comms -n langgraph --tail=20
kubectl logs -l component=task-generator -n langgraph --tail=20
kubectl logs -l component=task-solver -n langgraph --tail=20
```

**Kafka Connection Issues:**
```bash
# Check Kafka controller
kubectl logs langgraph-system-kafka-controller-0 -n langgraph --tail=20

# Test Kafka connectivity
kubectl port-forward svc/langgraph-system-kafka 9092:9092 -n langgraph
```

## 🔄 **Running Tests in CI/CD**

For automated testing, you can run the script in headless mode:

```bash
# Run with JSON output only
python test_deployment.py --format json --output results.json

# Run with exit codes for CI/CD
python test_deployment.py --fail-fast --exit-code
```

## 📝 **Test Reports**

The script generates detailed reports in JSON format:
- `deployment_test_report_YYYYMMDD_HHMMSS.json`

Example report structure:
```json
{
  "timestamp": "2025-08-09T01:45:00",
  "summary": {
    "total_services": 5,
    "healthy_services": 5,
    "success_rate": 1.0
  },
  "results": {
    "agent_communication": {"status": "healthy"},
    "task_generator": {"status": "healthy"},
    "task_solver": {"status": "healthy"},
    "kafka": {"status": "healthy"}
  }
}
```

## 🎯 **Next Steps**

After successful testing:

1. **Production Deployment**: Update configurations for production
2. **Monitoring Setup**: Implement comprehensive monitoring  
3. **Load Testing**: Test with realistic workloads
4. **Security Review**: Ensure security best practices
5. **Documentation**: Update deployment docs with test results

---

## 💡 **Tips**

- Run tests after each deployment update
- Use the script for health checks before production traffic
- Modify test scenarios based on your specific use cases
- Set up automated testing in your CI/CD pipeline
- Monitor test results over time to identify trends