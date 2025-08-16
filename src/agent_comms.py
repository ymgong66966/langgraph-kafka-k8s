import threading
import json
import os
from fastapi import FastAPI, HTTPException
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
import asyncio
import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'dev-langgraph-agent-events')
LANGGRAPH_API_URL = os.getenv('LANGGRAPH_API_URL', 'http://langgraph-api:2024')

# Global state
consumer_running = False
last_message = None
consumer_thread = None

def kafka_consumer_loop():
    """Kafka consumer loop that processes messages and forwards to LangGraph agents"""
    global consumer_running, last_message
    consumer_running = True
    
    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='langgraph-agent-group',
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        security_protocol='PLAINTEXT',  # Disable SASL for development
        request_timeout_ms=30000,
        connections_max_idle_ms=600000
    )
    
    try:
        logger.info(f"Starting Kafka consumer for topic: {KAFKA_TOPIC}")
        for message in consumer:
            try:
                data = message.value
                logger.info(f"Received message: {data}")
                
                # Process message with LangGraph agent
                asyncio.run(process_with_langgraph(data))
                
                last_message = data
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Consumer error: {e}")
    finally:
        consumer_running = False
        consumer.close()

async def process_with_langgraph(task_data):
    """Process task data with LangGraph agent"""
    try:
        from langgraph_sdk import get_client
        
        client = get_client(url=LANGGRAPH_API_URL)
        
        async for chunk in client.runs.stream(
            None,  # Threadless run
            "task_agent",  # Agent name from langgraph.json
            input={
                "context": task_data.get("context", ""),
                "task": task_data.get("task_description", "")
            },
        ):
            if hasattr(chunk, 'data') and chunk.data:
                logger.info(f"Task response: {chunk.data}")
                
    except Exception as e:
        logger.error(f"LangGraph processing error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    global consumer_thread
    
    # Startup
    consumer_thread = threading.Thread(target=kafka_consumer_loop, daemon=True)
    consumer_thread.start()
    logger.info("Kafka consumer started")
    
    yield
    
    # Shutdown
    global consumer_running
    consumer_running = False
    if consumer_thread and consumer_thread.is_alive():
        consumer_thread.join(timeout=5)
    logger.info("Application shutdown complete")

app = FastAPI(lifespan=lifespan)

# Initialize Kafka producer
producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    retries=3,
    acks='all'
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "consumer_running": consumer_running,
        "kafka_servers": KAFKA_BOOTSTRAP_SERVERS,
        "topic": KAFKA_TOPIC
    }

@app.post("/send_event")
async def send_event(event: dict):
    """Send event to Kafka topic"""
    try:
        future = producer.send(KAFKA_TOPIC, value=event)
        # Wait for send to complete
        record_metadata = future.get(timeout=10)
        
        logger.info(f"Event sent to topic {record_metadata.topic} partition {record_metadata.partition}")
        return {
            "message": "Event sent successfully",
            "event": event,
            "topic": record_metadata.topic,
            "partition": record_metadata.partition
        }
    except KafkaError as e:
        logger.error(f"Failed to send event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send event: {str(e)}")

@app.get("/last_message")
async def get_last_message():
    """Get the last consumed message"""
    global last_message
    if last_message is None:
        return {"message": "No messages consumed yet"}
    return {"last_message": last_message}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "LangGraph Kafka Communication Service",
        "status": "running",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)