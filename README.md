# LangGraph Kafka Communication System for Kubernetes

A Kubernetes-deployable Kafka-based communication system that enables LangGraph agents to communicate with each other asynchronously. This system provides task generation from conversation history and distributed processing through Kafka messaging.

## Architecture

This system provides:

- **Event-driven communication** between LangGraph agents via Kafka
- **Kubernetes-native deployment** with proper scalability
- **Task delegation model** where agents can generate and consume tasks
- **REST API bridge** for easy integration with external systems
- **Multi-agent workflow** with task generation and solving capabilities

## Components

### 1. Kafka Message Broker
- Handles event streaming between agents using KRaft mode (no Zookeeper)
- Topics: `dev-langgraph-agent-events`, `dev-langgraph-task-results`
- Configurable persistence (disabled for development)

### 2. Task Generator Service (`task-generator`)
- FastAPI service on port 8001
- LangGraph workflow that generates structured tasks from conversation history
- Uses OpenAI GPT-4o-mini for task generation
- Publishes tasks to Kafka and monitors results

### 3. Agent Communication Service (`agent-comms`)
- FastAPI service that bridges HTTP and Kafka
- Consumes messages and processes with LangGraph agents
- Provides health checks and message forwarding

### 4. Task Solver Agent (`task-solver`)
- LangGraph agent that processes tasks from Kafka
- Publishes results back to results topic

## ⚠️ CRITICAL DEPLOYMENT WARNINGS

### Docker Build Issues
1. **Architecture Compatibility**: Always build for the correct architecture
   ```bash
   # For AWS EKS (x86_64), use:
   docker buildx build --platform linux/amd64 -f docker/Dockerfile.task-generator -t $REGISTRY/langgraph-kafka/task-generator:$TAG .
   ```

2. **TLS Certificate Issues**: If you encounter TLS errors:
   ```bash
   # Clear Docker cache and restart
   docker system prune -af
   colima restart  # If using Colima
   # Manually pull base images if needed
   docker pull python:3.11-slim
   ```

### LangGraph Configuration
1. **Checkpointer Issues**: LangGraph requires proper config for checkpointer
   - For development: Set `disableCheckpointer: "true"` in values-dev.yaml
   - For production: Ensure graph.ainvoke() calls include config parameter:
   ```python
   config={"configurable": {"thread_id": f"task-gen-{hash(input) % 10000}"}}
   ```

2. **Import Compatibility**: Use correct LangGraph imports:
   ```python
   from langgraph.graph import END, StateGraph, START
   from langgraph.constants import START  # For newer versions
   ```

### Kafka Configuration
1. **SASL Authentication**: Disable for development to avoid auth issues
2. **Persistence**: Disable StatefulSet persistence for dev environment
3. **Topic Names**: Use prefixed topics (e.g., `dev-langgraph-agent-events`)

## Deployment Guide

### Prerequisites
- Kubernetes cluster (tested on AWS EKS)
- kubectl configured and connected to cluster
- Docker with buildx support
- Container registry (AWS ECR recommended)
- OpenAI API key

### Step-by-Step Deployment

1. **Verify Prerequisites:**
   ```bash
   # Check kubectl connection
   kubectl cluster-info
   
   # Check Docker buildx
   docker buildx version
   
   # Verify registry access
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $REGISTRY
   ```

2. **Set Environment Variables:**
   ```bash
   export REGISTRY="your-ecr-repo.amazonaws.com"
   export TAG="latest"
   export OPENAI_API_KEY="sk-your-openai-key"
   ```

3. **Build Images with Correct Architecture:**
   ```bash
   # Build for x86_64 clusters (AWS EKS)
   docker buildx build --platform linux/amd64 -f docker/Dockerfile.task-generator -t $REGISTRY/langgraph-kafka/task-generator:$TAG . --push
   docker buildx build --platform linux/amd64 -f docker/Dockerfile.agent-comms -t $REGISTRY/langgraph-kafka/agent-comms:$TAG . --push
   docker buildx build --platform linux/amd64 -f docker/Dockerfile.task-solver -t $REGISTRY/langgraph-kafka/task-solver:$TAG . --push
   ```

4. **Update Helm Dependencies:**
   ```bash
   cd helm
   helm dependency update
   cd ..
   ```

5. **Deploy to Kubernetes:**
   ```bash
   # Deploy with development configuration
   helm upgrade --install langgraph-kafka ./helm \
     --namespace langgraph \
     --create-namespace \
     --values helm/values-dev.yaml \
     --set image.repository=$REGISTRY/langgraph-kafka \
     --set image.tag=$TAG \
     --set env.openaiApiKey=$OPENAI_API_KEY
   ```

6. **Verify Deployment:**
   ```bash
   # Check pod status
   kubectl get pods -n langgraph
   
   # Check logs for errors
   kubectl logs -l app.kubernetes.io/name=langgraph-kafka -n langgraph
   
   # Verify services
   kubectl get svc -n langgraph
   ```

7. **Test the Deployment:**
   ```bash
   # Port forward to test locally
   kubectl port-forward -n langgraph svc/langgraph-kafka-task-generator 8001:8001 &
   
   # Test health endpoint
   curl http://localhost:8001/health
   
   # Test task generation
   curl -X POST http://localhost:8001/generate-task \
     -H "Content-Type: application/json" \
     -d '{"conversation_history": "User: I need help with my project"}'
   ```

## Troubleshooting Common Issues

### 1. Checkpointer Error
```
ValueError: Checkpointer requires one or more of the following 'configurable' keys: []
```
**Solution**: Ensure `disableCheckpointer: "true"` in values-dev.yaml or fix graph.ainvoke() calls

### 2. Exec Format Error
```
exec /usr/local/bin/python: exec format error
```
**Solution**: Rebuild images with `--platform linux/amd64`

### 3. Kafka Authentication Failed
```
kafka.errors.AuthenticationFailedError: Authentication failed
```
**Solution**: Disable SASL authentication in development configuration

### 4. ImagePullBackOff
**Solution**: Verify registry authentication and image names/tags

### 5. PVC Provisioning Errors
**Solution**: Set `persistence.enabled: false` for development

## Development Configuration

The `helm/values-dev.yaml` file contains optimized settings for development:
- Checkpointer disabled
- Kafka persistence disabled
- Resource limits reduced
- SASL authentication disabled

## Production Considerations

For production deployment:
1. Enable proper authentication (SASL/TLS)
2. Enable Kafka persistence with proper storage class
3. Configure resource limits based on load
4. Enable checkpointer with proper database backend
5. Set up proper monitoring and logging
6. Use secrets management for API keys

## Message Format

Tasks follow this JSON structure:
```json
{
  "task_name": "Short descriptive name",
  "task_description": "Detailed description of what needs to be done",
  "context": "Additional context information for task resolution"
}
```

## API Endpoints

### Task Generator Service (Port 8001)
- `POST /generate-task` - Generate task from conversation history
- `GET /health` - Service health check and configuration
- `GET /task-results` - List all task results
- `GET /task-result/{task_id}` - Get specific task result

### Agent Communication Service (Port 8000)
- `POST /send_event` - Send events to Kafka
- `GET /health` - Service health check
- `GET /last_message` - Get last consumed message

## Environment Variables

### Core Configuration
- `KAFKA_BOOTSTRAP_SERVERS` - Kafka broker addresses (default: kafka-service:9092)
- `KAFKA_TOPIC` - Input topic name (default: langgraph-agent-events)
- `KAFKA_RESULTS_TOPIC` - Results topic name (default: langgraph-task-results)
- `OPENAI_API_KEY` - Required for task generation
- `DISABLECHECKPOINTER` - Set to "true" to disable LangGraph checkpointer

### Service-Specific
- `LANGGRAPH_API_URL` - LangGraph API endpoint (if using external API)
- Port configurations (8000, 8001, 8002 for different services)

## File Structure
```
langgraph-kafka-k8s/
├── src/                          # Source code
│   ├── task_generator_api.py     # Task generation service
│   ├── agent_comms.py           # Agent communication service
│   └── task_solver_agent.py     # Task solving agent
├── docker/                      # Dockerfiles
├── helm/                        # Helm chart
│   ├── values-dev.yaml         # Development configuration
│   └── values.yaml             # Production template
├── requirements.txt            # Python dependencies
├── Makefile                   # Build and deployment commands
└── README.md                  # This file
```

## Quick Commands Reference

```bash
# Build and deploy everything
make build REGISTRY=$REGISTRY TAG=$TAG
make deploy-dev REGISTRY=$REGISTRY TAG=$TAG OPENAI_API_KEY=$OPENAI_API_KEY

# Check status
make status

# View logs
kubectl logs -f deployment/langgraph-kafka-task-generator -n langgraph

# Port forward for testing
kubectl port-forward -n langgraph svc/langgraph-kafka-task-generator 8001:8001

# Clean up
helm uninstall langgraph-kafka -n langgraph
```