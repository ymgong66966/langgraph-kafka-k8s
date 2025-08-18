from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import uuid
import httpx
from datetime import datetime
from typing import Dict, List, Optional
import logging
import os
from kafka import KafkaConsumer
import threading

app = FastAPI(title="Chat Interface API", version="1.0.0")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables - consistent with existing services
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'langgraph-system-kafka:9092')
KAFKA_RESPONSE_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')  # For health checks

# In-memory storage for messages and SSE clients
messages: List[Dict] = []
sse_clients: List[asyncio.Queue] = []
kafka_consumer_running = False

class ChatMessage(BaseModel):
    content: str
    target_endpoint: Optional[str] = "task-generator"

class IncomingMessage(BaseModel):
    content: str
    source: str
    message_type: str = "agent"
    conversation_id: Optional[str] = None
    metadata: Optional[Dict] = None

# LangGraph endpoint mapping
LANGGRAPH_ENDPOINTS = {
    "task-generator": "http://langgraph-system-langgraph-kafka-task-generator:8001",
    "task-solver": "http://langgraph-system-langgraph-kafka-task-solver:8002", 
    "agent-comms": "http://langgraph-system-langgraph-kafka-agent-comms:8000"
}

async def broadcast_message(message: Dict):
    """Broadcast message to all SSE clients"""
    message_json = json.dumps(message)
    
    # Add to message history
    messages.append(message)
    
    # Keep only last 100 messages
    if len(messages) > 100:
        messages.pop(0)
    
    # Send to all connected SSE clients
    disconnected_clients = []
    for client_queue in sse_clients:
        try:
            await client_queue.put(message_json)
        except:
            disconnected_clients.append(client_queue)
    
    # Remove disconnected clients
    for client in disconnected_clients:
        sse_clients.remove(client)
    
    logger.info(f"Broadcasted message to {len(sse_clients)} clients")

def kafka_response_consumer():
    """Background thread to consume messages from Kafka response topic"""
    global kafka_consumer_running
    kafka_consumer_running = True
    
    # Wait for Kafka to be ready
    import time
    retry_count = 0
    max_retries = 10
    
    while retry_count < max_retries:
        try:
            consumer = KafkaConsumer(
                KAFKA_RESPONSE_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda x: json.loads(x.decode('utf-8')),
                group_id='chat-interface-consumer',
                auto_offset_reset='earliest',  # Get all messages from beginning
                # consumer_timeout_ms removed - let consumer run indefinitely
                security_protocol='PLAINTEXT',  # Disable SASL for development
                enable_auto_commit=True,
                request_timeout_ms=30000,
                connections_max_idle_ms=600000
            )
            
            logger.info(f"Kafka consumer started for topic: {KAFKA_RESPONSE_TOPIC}")
            break
            
        except Exception as e:
            retry_count += 1
            logger.warning(f"Failed to connect to Kafka (attempt {retry_count}/{max_retries}): {e}")
            if retry_count >= max_retries:
                logger.error("Failed to connect to Kafka after max retries")
                kafka_consumer_running = False
                return
            time.sleep(5)  # Wait before retry
    
    try:
        logger.info("Starting Kafka message consumption loop")
        poll_count = 0
        for message in consumer:
            poll_count += 1
            if poll_count % 10 == 0:
                logger.info(f"Consumer polling: {poll_count} polls completed")
            try:
                kafka_message = message.value
                logger.info(f"Received Kafka message: {kafka_message}")
                logger.info(f"Message offset: {message.offset}, partition: {message.partition}")
                
                # Transform Kafka message to chat message format
                chat_message = {
                    "id": kafka_message.get("task_id", str(uuid.uuid4())),
                    "type": "agent",
                    "content": kafka_message.get("content", kafka_message.get("result", str(kafka_message))),
                    "timestamp": datetime.now().isoformat(),
                    "source": kafka_message.get("source", "agent"),
                    "metadata": kafka_message.get("metadata", {})
                }
                
                # Add to message history and broadcast
                messages.append(chat_message)
                if len(messages) > 100:
                    messages.pop(0)
                
                # Broadcast to all SSE clients (need to use asyncio from thread)
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                message_json = json.dumps(chat_message)
                disconnected_clients = []
                for client_queue in sse_clients:
                    try:
                        # Put message in queue (synchronous)
                        client_queue._queue.appendleft(message_json)
                    except:
                        disconnected_clients.append(client_queue)
                
                # Clean up disconnected clients
                for client in disconnected_clients:
                    if client in sse_clients:
                        sse_clients.remove(client)
                
                logger.info(f"Broadcasted Kafka message to {len(sse_clients)} clients")
                        
            except Exception as e:
                logger.error(f"Error processing Kafka message: {e}")
                
    except Exception as e:
        logger.error(f"Kafka consumer error: {e}")
    finally:
        kafka_consumer_running = False

# Start Kafka consumer in background thread
def start_kafka_consumer():
    if not kafka_consumer_running:
        thread = threading.Thread(target=kafka_response_consumer, daemon=True)
        thread.start()
        logger.info("Started Kafka consumer thread")

@app.get("/")
async def root():
    return {"message": "Chat Interface API", "status": "running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "connected_clients": len(sse_clients),
        "message_count": len(messages),
        "available_endpoints": list(LANGGRAPH_ENDPOINTS.keys()),
        "kafka_consumer_running": kafka_consumer_running,
        "kafka_response_topic": KAFKA_RESPONSE_TOPIC,
        "kafka_bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "openai_configured": bool(OPENAI_API_KEY)
    }

@app.post("/chat/send")
async def send_message(message: ChatMessage):
    """Send user message and forward to task generator"""
    
    # Create user message
    user_message = {
        "id": str(uuid.uuid4()),
        "type": "user",
        "content": message.content,
        "timestamp": datetime.now().isoformat(),
        "source": "user",
        "target_endpoint": message.target_endpoint
    }
    
    # Broadcast user message immediately
    await broadcast_message(user_message)
    
    # Always send to task generator (as per your workflow)
    try:
        task_generator_url = "http://langgraph-kafka-task-generator:8001"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Send to task generator with conversation history format
            payload = {
                "conversation_history": message.content,
                "message_id": user_message["id"]
            }
            
            logger.info(f"Sending message to task generator: {payload}")
            response = await client.post(f"{task_generator_url}/generate-task", json=payload)
            
            if response.status_code == 200:
                logger.info("Successfully sent message to task generator")
            else:
                logger.error(f"Task generator returned status {response.status_code}: {response.text}")
                
    except Exception as e:
        logger.error(f"Error forwarding to task generator: {e}")
        
        # Send error message to chat via Kafka response topic (will be picked up by SSE)
        error_message = {
            "content": f"Error connecting to task generator: {str(e)}",
            "source": "system",
            "task_id": user_message["id"]
        }
        await broadcast_message({
            "id": str(uuid.uuid4()),
            "type": "system",
            "content": error_message["content"],
            "timestamp": datetime.now().isoformat(),
            "source": "system"
        })
    
    return {"status": "message_sent", "message_id": user_message["id"]}

@app.post("/chat/incoming")
async def receive_incoming_message(message: IncomingMessage):
    """Endpoint for LangGraph pods to send messages to chat"""
    
    incoming_message = {
        "id": str(uuid.uuid4()),
        "type": message.message_type,
        "content": message.content,
        "timestamp": datetime.now().isoformat(),
        "source": message.source,
        "conversation_id": message.conversation_id,
        "metadata": message.metadata or {}
    }
    
    # Broadcast to all chat clients
    await broadcast_message(incoming_message)
    
    logger.info(f"Received message from {message.source}: {message.content[:50]}...")
    
    return {"status": "message_received", "message_id": incoming_message["id"]}

@app.get("/chat/history")
async def get_chat_history():
    """Get recent chat history"""
    return {"messages": messages[-50:]}  # Last 50 messages

@app.get("/chat/stream")
async def chat_stream():
    """SSE endpoint for real-time chat updates (fed by Kafka response topic)"""
    
    # Start Kafka consumer if not already running
    start_kafka_consumer()
    
    async def event_generator():
        # Create a queue for this client
        client_queue = asyncio.Queue()
        sse_clients.append(client_queue)
        
        try:
            # Send recent messages to new client
            for message in messages[-10:]:  # Last 10 messages
                yield f"data: {json.dumps(message)}\n\n"
            
            # Send new messages as they arrive (from Kafka or direct broadcast)
            while True:
                try:
                    # Wait for new message
                    message_json = await asyncio.wait_for(client_queue.get(), timeout=30.0)
                    yield f"data: {message_json}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'keepalive', 'timestamp': datetime.now().isoformat()})}\n\n"
                    
        except Exception as e:
            logger.error(f"SSE client error: {e}")
        finally:
            # Clean up
            if client_queue in sse_clients:
                sse_clients.remove(client_queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.on_event("startup")
async def startup_event():
    """Start Kafka consumer when FastAPI starts"""
    logger.info("Starting Chat Interface API...")
    
    # Validate environment variables
    if not KAFKA_BOOTSTRAP_SERVERS:
        logger.error("KAFKA_BOOTSTRAP_SERVERS not configured")
    if not KAFKA_RESPONSE_TOPIC:
        logger.error("KAFKA_RESPONSE_TOPIC not configured")
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not configured")
    
    # Start Kafka consumer with delay to ensure Kafka is ready
    import asyncio
    await asyncio.sleep(10)  # Wait for Kafka to be ready
    start_kafka_consumer()
    logger.info("Chat Interface API started with Kafka consumer")

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)