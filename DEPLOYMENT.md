# Deployment Guide

This guide covers deploying the LangGraph Kafka Communication System to Kubernetes using Helm.

## Prerequisites

1. **Kubernetes cluster** (local or cloud)
2. **Helm 3.x** installed
3. **kubectl** configured to access your cluster
4. **Docker** for building images
5. **Container registry** access

## Quick Start

### 1. Build and Push Images

```bash
# Set your container registry
export REGISTRY=your-registry.com

# Build and push images
make push REGISTRY=$REGISTRY TAG=v1.0.0
```

### 2. Configure Values

Create a custom values file or use environment variables:

```bash
# Create custom values file
cp helm/values.yaml my-values.yaml

# Edit with your configuration
vim my-values.yaml
```

### 3. Deploy to Kubernetes

```bash
# Deploy with default values
make deploy REGISTRY=$REGISTRY TAG=v1.0.0

# Or deploy with custom values
helm upgrade --install langgraph-kafka ./helm \
  --namespace langgraph \
  --create-namespace \
  --values my-values.yaml \
  --set env.openaiApiKey="your-openai-key" \
  --set image.repository=$REGISTRY/langgraph-kafka \
  --set image.tag=v1.0.0
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker addresses | `kafka-service:9092` |
| `KAFKA_TOPIC` | Kafka topic for agent events | `langgraph-agent-events` |
| `LANGGRAPH_API_URL` | LangGraph API endpoint | `http://langgraph-api:2024` |
| `OPENAI_API_KEY` | OpenAI API key for task generation | (required) |

### Kafka Configuration

The system includes a Kafka cluster via Bitnami Helm chart:

```yaml
kafka:
  enabled: true
  replicaCount: 3  # For production
  persistence:
    enabled: true
    size: 10Gi
  zookeeper:
    enabled: true
    persistence:
      enabled: true
      size: 5Gi
```

### Resource Configuration

Configure resource limits per component:

```yaml
agentComms:
  resources:
    limits:
      cpu: 500m
      memory: 512Mi
    requests:
      cpu: 250m
      memory: 256Mi

taskGenerator:
  resources:
    limits:
      cpu: 300m
      memory: 256Mi
    requests:
      cpu: 150m
      memory: 128Mi
```

## Production Deployment

### 1. External Kafka (Recommended)

For production, use an external Kafka cluster:

```yaml
kafka:
  enabled: false  # Disable built-in Kafka

env:
  kafkaBootstrapServers: "kafka-1.example.com:9092,kafka-2.example.com:9092"
```

### 2. High Availability

Configure multiple replicas:

```yaml
agentComms:
  replicaCount: 3
  
taskGenerator:
  replicaCount: 2

# Enable pod disruption budgets
podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

### 3. Security

Configure security contexts and network policies:

```yaml
securityContext:
  capabilities:
    drop:
    - ALL
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 1000

networkPolicy:
  enabled: true
  ingress:
    - from: []
```

### 4. Monitoring

Enable monitoring with Prometheus:

```yaml
monitoring:
  enabled: true
  serviceMonitor:
    enabled: true
    labels:
      monitoring: prometheus
```

## Troubleshooting

### Common Issues

1. **Kafka Connection Issues**
   ```bash
   # Check Kafka connectivity
   kubectl exec -it deployment/langgraph-kafka-agent-comms -- \
     python -c "from kafka import KafkaProducer; print('Connected' if KafkaProducer(bootstrap_servers=['kafka-service:9092']) else 'Failed')"
   ```

2. **OpenAI API Key Issues**
   ```bash
   # Verify secret is created
   kubectl get secret langgraph-kafka-secrets -o yaml
   
   # Update OpenAI API key
   kubectl create secret generic langgraph-kafka-secrets \
     --from-literal=openai-api-key="your-new-key" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

3. **Pod Startup Issues**
   ```bash
   # Check pod logs
   kubectl logs -l component=agent-comms --tail=100
   
   # Check pod events
   kubectl describe pod -l component=agent-comms
   ```

### Health Checks

The system includes health check endpoints:

```bash
# Check agent communication service
kubectl port-forward svc/langgraph-kafka-agent-comms 8000:8000
curl http://localhost:8000/health

# Expected response:
# {
#   "status": "healthy",
#   "consumer_running": true,
#   "kafka_servers": "kafka-service:9092",
#   "topic": "langgraph-agent-events"
# }
```

## Scaling

### Horizontal Pod Autoscaling

Enable HPA for automatic scaling:

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

### Kafka Scaling

Scale Kafka brokers for higher throughput:

```yaml
kafka:
  replicaCount: 5
  persistence:
    size: 50Gi
```

## Maintenance

### Updating the System

```bash
# Update images
make push REGISTRY=$REGISTRY TAG=v1.1.0

# Upgrade deployment
helm upgrade langgraph-kafka ./helm \
  --set image.tag=v1.1.0 \
  --reuse-values
```

### Backup and Recovery

```bash
# Backup Kafka data (if using persistent volumes)
kubectl create job kafka-backup --image=backup-tool -- backup-kafka

# Backup Helm configuration
helm get values langgraph-kafka > backup-values.yaml
```