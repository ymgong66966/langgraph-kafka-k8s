# Usage Guide

This document explains how to use the LangGraph Kafka Communication System.

## Architecture Overview

```
[Task Generator API] → [Kafka Input Topic] → [Task Solver Agent] → [Kafka Results Topic] → [Task Generator API]
     (Port 8001)       langgraph-agent-events    (Port 8002)      langgraph-task-results        (consumes results)
                                 ↓
                       [Agent Comms Service] → [External LangGraph Agents]
                            (Port 8000)
```

### Component Roles:

1. **Task Generator API** (Port 8001)
   - Receives conversation history via REST API
   - Uses OpenAI to generate structured tasks
   - Publishes tasks to Kafka input topic
   - Consumes results from Kafka results topic
   - Provides access to task results via API

2. **Task Solver Agent** (Port 8002) **[NEW]**
   - LangGraph agent that processes tasks from Kafka
   - Uses OpenAI to generate solutions for tasks
   - Publishes results back to Kafka results topic
   - Provides direct task solving API

3. **Kafka Broker**
   - Message queue for asynchronous communication
   - Input Topic: `langgraph-agent-events`
   - Results Topic: `langgraph-task-results`
   - Bootstrap servers: `langgraph-kafka-kafka:9092`

4. **Agent Communications Service** (Port 8000)
   - Alternative consumer for tasks from Kafka
   - Forwards tasks to external LangGraph agents
   - Provides health checks and debugging endpoints

## API Endpoints

### Task Generator API (`http://task-generator-service:8001`)

#### Generate Task from Conversation
```bash
POST /generate-task
Content-Type: application/json

{
  "conversation_history": "User: I need help finding a babysitting service near my home in Chicago. Assistant: I'd be happy to help you find babysitting services in Chicago. User: I live at 222 East Pearson Street and need someone for weekends.",
  "context": "Optional additional context"
}
```

**Response:**
```json
{
  "task_name": "find near-home babysitting service",
  "task_description": "find a babysitting service that is close to the user's home in Chicago for weekend childcare",
  "context": "the user lives at 222 East Pearson Street, Chicago and needs weekend babysitting services",
  "status": "sent_to_kafka"
}
```

#### Send Task Directly to Kafka
```bash
POST /send-task-to-kafka
Content-Type: application/json

{
  "task_name": "research restaurants",
  "task_description": "find Italian restaurants in downtown Chicago",
  "context": "user prefers authentic Italian cuisine, budget around $50 per person"
}
```

#### Get Task Result
```bash
GET /task-result/{task_id}
```

#### List All Task Results
```bash
GET /task-results
```

#### Health Check
```bash
GET /health
```

### Task Solver Agent API (`http://task-solver-service:8002`) **[NEW]**

#### Solve Task Directly
```bash
POST /solve-task
Content-Type: application/json

{
  "task_name": "research restaurants",
  "task_description": "find Italian restaurants in downtown Chicago",
  "context": "user prefers authentic Italian cuisine, budget around $50 per person"
}
```

**Response:**
```json
{
  "task_id": "uuid-generated-id",
  "task_name": "research restaurants",
  "task_description": "find Italian restaurants in downtown Chicago",
  "context": "user prefers authentic Italian cuisine, budget around $50 per person",
  "solution": "Here are some excellent Italian restaurants in downtown Chicago that match your criteria...",
  "timestamp": "2024-01-15T10:30:00",
  "solver_agent": "langgraph-task-solver"
}
```

#### Get Task Result by ID
```bash
GET /task/{task_id}
```

#### List All Processed Tasks
```bash
GET /tasks
```

#### Health Check
```bash
GET /health
```

### Agent Communications Service (`http://agent-comms-service:8000`)

#### Send Event to Kafka
```bash
POST /send_event
Content-Type: application/json

{
  "task_name": "book appointment",
  "task_description": "schedule a doctor's appointment",
  "context": "user needs a general checkup next week"
}
```

#### Get Last Consumed Message
```bash
GET /last_message
```

#### Health Check
```bash
GET /health
```

## Usage Examples

### Example 1: Complete Task Flow - Generate, Process, and Get Result

```bash
# Port forward to access services locally
kubectl port-forward svc/langgraph-kafka-task-generator 8001:8001 &
kubectl port-forward svc/langgraph-kafka-task-solver 8002:8002 &
kubectl port-forward svc/langgraph-kafka-agent-comms 8000:8000 &

# Step 1: Generate a task
TASK_RESPONSE=$(curl -X POST http://localhost:8001/generate-task \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": "User: I need help finding a good Italian restaurant in Chicago for my anniversary. Assistant: I can help you find romantic Italian restaurants in Chicago. User: We prefer something in the Loop area with a nice atmosphere."
  }')

echo "Generated Task: $TASK_RESPONSE"

# Step 2: Task gets automatically processed by Task Solver Agent via Kafka
# Wait a few seconds for processing
sleep 5

# Step 3: Get the result
curl http://localhost:8001/task-results
```

### Example 2: Direct Task Solving (Bypass Kafka)

```bash
# Solve a task directly via Task Solver API
curl -X POST http://localhost:8002/solve-task \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "weather check",
    "task_description": "get current weather conditions for Chicago",
    "context": "user is planning outdoor activities for tomorrow"
  }'
```

### Example 3: Monitor System Health

```bash
# Check health of all services
curl http://localhost:8001/health  # Task Generator
curl http://localhost:8002/health  # Task Solver  
curl http://localhost:8000/health  # Agent Comms

# Check processed tasks
curl http://localhost:8002/tasks   # Tasks processed by solver
curl http://localhost:8001/task-results  # Results received by generator
```

## Integration with LangGraph Agents

The Agent Communications Service automatically forwards received tasks to your LangGraph agents:

```python
# Your LangGraph agent will receive:
{
    "context": "user is planning outdoor activities for tomorrow",
    "task": "get current weather conditions for Chicago"
}
```

## Kafka Bootstrap Server Configuration

The **bootstrap server** (`langgraph-kafka-kafka:9092`) is your entry point to the Kafka cluster:

- **Service Name**: `langgraph-kafka-kafka` (created by Helm)
- **Port**: `9092` (standard Kafka port)
- **Purpose**: Initial connection point that provides cluster metadata

### How Bootstrap Servers Work:

1. **Client connects** to `langgraph-kafka-kafka:9092`
2. **Kafka responds** with information about all brokers in the cluster
3. **Client then connects** directly to the appropriate brokers

### Configuration in Code:

```python
# Both services use this configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'langgraph-kafka-kafka:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'langgraph-agent-events')
```

## Monitoring and Debugging

### Check Kafka Connection
```bash
kubectl exec -it deployment/langgraph-kafka-agent-comms -- \
  python -c "
from kafka import KafkaProducer
try:
    producer = KafkaProducer(bootstrap_servers=['langgraph-kafka-kafka:9092'])
    print('✅ Kafka connection successful')
    producer.close()
except Exception as e:
    print(f'❌ Kafka connection failed: {e}')
"
```

### View Logs
```bash
# Task Generator logs
kubectl logs -l component=task-generator -f

# Agent Communications logs  
kubectl logs -l component=agent-comms -f
```

### Scale Services
```bash
# Scale task generator
kubectl scale deployment langgraph-kafka-task-generator --replicas=3

# Scale agent communications
kubectl scale deployment langgraph-kafka-agent-comms --replicas=5
```

## Message Flow

### Complete Task Processing Flow:

```
1. POST /generate-task → Task Generator API
2. OpenAI generates structured task → Task Generator  
3. Task sent to Kafka input topic → Kafka Broker (langgraph-agent-events)
4. Task Solver consumes task → Task Solver Agent
5. LangGraph processes task with OpenAI → Task Solver Agent
6. Solution sent to Kafka results topic → Kafka Broker (langgraph-task-results)
7. Task Generator consumes result → Task Generator API
8. GET /task-results shows solution → Client
```

### Alternative Flow (External Agents):

```
1. POST /generate-task → Task Generator API
2. Task sent to Kafka input topic → Kafka Broker
3. Agent Comms consumes task → Agent Communications Service
4. Task forwarded to external agents → Your LangGraph Agents
5. Results handled by your agents → Custom Logic
```

### Key Features:

✅ **End-to-end task processing** with automatic result delivery  
✅ **Scalable architecture** with multiple consumer groups  
✅ **Dual processing paths** (internal solver + external agents)  
✅ **Full API access** to tasks and results  
✅ **Health monitoring** across all services  

This creates a complete, production-ready task processing system where LangGraph agents can generate, process, and return results asynchronously!