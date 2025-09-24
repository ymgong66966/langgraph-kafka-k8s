from fastapi import FastAPI, HTTPException, Query
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
from contextlib import asynccontextmanager
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables - consistent with existing services
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'langgraph-system-kafka:9092')
KAFKA_RESPONSE_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')  # For health checks

# In-memory storage for messages and SSE clients
messages: List[Dict] = []
# Changed to dict to track user_id per client: {client_queue: user_id}
sse_clients: Dict[asyncio.Queue, str] = {}
kafka_consumer_running = False

class ChatMessage(BaseModel):
    content: str
    target_endpoint: Optional[str] = "task-generator"
    user_id: Optional[str] = None

class IncomingMessage(BaseModel):
    content: str
    source: str
    message_type: str = "agent"
    conversation_id: Optional[str] = None
    metadata: Optional[Dict] = None
    user_id: Optional[str] = None

class ExternalMessage(BaseModel):
    user_id: str
    messages: List[Dict[str, str]]  # List of messages with 'role' and 'text' fields

# LangGraph endpoint mapping
LANGGRAPH_ENDPOINTS = {
    "task-generator": "http://langgraph-kafka-task-generator:8001",
    "task-solver": "http://langgraph-kafka-task-solver:8002"
}

def send_navigator_message(user_id: str, messages: List[Dict[str, str]]) -> bool:
    """
    Send messages to the Navigator message delivery API (AWS S3)

    Args:
        user_id (str): The user ID to send messages to
        messages (list): List of message objects with 'text' field

    Returns:
        bool: True if successful, False otherwise
    """
    url = "https://h9d1ldlv65.execute-api.us-east-2.amazonaws.com/dev/delivernavigatormessage"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": "iwja4JC4q765W7VlfqBVx2RAYSISs9lPwEyqNvfh"
    }

    payload = {
        "user_Id": user_id,
        "messages": messages
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()

        logger.info(f"✅ AWS S3 message sent successfully to user {user_id}")
        logger.info(f"AWS Response: {response.json()}")

        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error sending AWS S3 message to user {user_id}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"AWS Response Status: {e.response.status_code}")
            logger.error(f"AWS Response Text: {e.response.text}")
        return False

async def send_aws_message_async(user_id: str, messages: List[Dict[str, str]]) -> bool:
    """Async wrapper for sending AWS S3 messages with error handling"""
    try:
        # Run the sync function in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, send_navigator_message, user_id, messages)
        if success:
            logger.info(f"✅ AWS S3 delivery successful for user {user_id}")
            return True
        else:
            logger.warning(f"⚠️ AWS S3 delivery failed for user {user_id}")
            return False
    except Exception as e:
        logger.error(f"❌ AWS S3 delivery error for user {user_id}: {e}")
        return False

async def send_aws_message_async_safe(user_id: str, messages: List[Dict[str, str]]):
    """Safe AWS S3 delivery that never raises exceptions to UI delivery"""
    try:
        success = await send_aws_message_async(user_id, messages)
        if success:
            logger.info(f"✅ Background AWS S3 delivery successful for user {user_id}")
        else:
            logger.warning(f"⚠️ Background AWS S3 delivery failed for user {user_id} - UI delivery unaffected")
    except Exception as e:
        logger.error(f"❌ Background AWS S3 delivery exception for user {user_id}: {e} - UI delivery unaffected")

async def broadcast_message(message: Dict):
    """Broadcast message to SSE clients, filtered by user_id, and send to AWS S3 if applicable"""
    message_json = json.dumps(message)
    message_user_id = message.get('user_id')

    # Add to message history
    messages.append(message)

    # Keep only last 100 messages
    if len(messages) > 100:
        messages.pop(0)

    # Send to connected SSE clients, filtered by user_id
    disconnected_clients = []
    sent_count = 0
    for client_queue, client_user_id in sse_clients.items():
        try:
            # Only send message if:
            # 1. Message has no user_id (system messages), OR
            # 2. Client has no user_id (receives all), OR
            # 3. Message user_id matches client user_id
            if not message_user_id or not client_user_id or message_user_id == client_user_id:
                await client_queue.put(message_json)
                sent_count += 1
        except:
            disconnected_clients.append(client_queue)

    # Remove disconnected clients
    for client in disconnected_clients:
        if client in sse_clients:
            del sse_clients[client]

    logger.info(f"Broadcasted message to {sent_count}/{len(sse_clients)} clients (filtered by user_id: {message_user_id})")

    # Send to AWS S3 if message has user_id and is from agent/system (not user messages)
    if message_user_id and message.get('type') in ['agent', 'ai', 'assistant', 'system']:
        aws_messages = [{"text": message.get('content', '')}]

        # Run AWS S3 delivery synchronously from thread context
        try:
            success = send_navigator_message(message_user_id, aws_messages)
            if success:
                logger.info(f"✅ AWS S3 delivery successful for user {message_user_id}")
            else:
                logger.warning(f"⚠️ AWS S3 delivery failed for user {message_user_id} - UI delivery unaffected")
        except Exception as e:
            logger.error(f"❌ AWS S3 delivery exception for user {message_user_id}: {e} - UI delivery unaffected")

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
                    "metadata": kafka_message.get("metadata", {}),
                    "user_id": kafka_message.get("user_id")  # Extract user_id from Kafka message
                }
                
                # Use the broadcast_message function with asyncio from thread
                import asyncio
                
                # Check if event loop exists, if not create one  
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        raise RuntimeError("Event loop is closed")
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                # Run broadcast_message in the event loop
                loop.run_until_complete(broadcast_message(chat_message))
                
                logger.info(f"Broadcasted Kafka message: {chat_message.get('user_id', 'no-user-id')}")
                        
            except Exception as e:
                logger.error(f"Error processing Kafka message: {e}")
                
    except Exception as e:
        logger.error(f"Kafka consumer error: {e}")
    finally:
        kafka_consumer_running = False

def start_kafka_consumer():
    if not kafka_consumer_running:
        thread = threading.Thread(target=kafka_response_consumer, daemon=True)
        thread.start()
        logger.info("Started Kafka consumer thread")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # Startup
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
    
    yield
    
    # Shutdown (if needed)
    logger.info("Chat Interface API shutting down")

app = FastAPI(title="Chat Interface API", version="1.0.0", lifespan=lifespan)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        "target_endpoint": message.target_endpoint,
        "user_id": message.user_id
    }
    
    # Broadcast user message immediately
    await broadcast_message(user_message)
    
    # Always send to task generator (as per your workflow)
    try:
        task_generator_url = "http://langgraph-kafka-task-generator:8001"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get conversation history BEFORE the current message 
            # (broadcast_message already added it to messages list)
            # Filter for user's messages and agent responses only (no system messages)
            user_conversation = [
                msg for msg in messages 
                if msg.get('user_id') == message.user_id 
                and msg.get('type') in ['user', 'agent', 'ai', 'assistant']
                and msg.get('id') != user_message['id']  # Exclude the just-added message
            ]
            
            # Get last 10 messages for better context (5 exchanges)
            recent_conversation = user_conversation[-10:] if len(user_conversation) > 0 else []
            
            # Log conversation history details for debugging
            logger.info(f"Conversation history: {len(recent_conversation)} previous messages for user {message.user_id}")
            if recent_conversation:
                logger.info(f"History preview: First={recent_conversation[0].get('content', '')[:50]}... Last={recent_conversation[-1].get('content', '')[:50]}...")
            
            # Send history WITHOUT current message (since we send it separately)
            payload = {
                "conversation_history": recent_conversation,  # Previous messages only
                "current_message": message.content,         # Current message separately
                "user_id": message.user_id
            }
            
            logger.info(f"Sending to task generator: current_message='{message.content[:100]}...', history_count={len(recent_conversation)}")
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
        "metadata": message.metadata or {},
        "user_id": message.user_id
    }
    
    # Broadcast to all chat clients
    await broadcast_message(incoming_message)
    
    logger.info(f"Received message from {message.source}: {message.content[:50]}...")
    
    return {"status": "message_received", "message_id": incoming_message["id"]}

@app.post("/external/send")
async def send_external_message(request: ExternalMessage):
    """
    External API endpoint for sending messages with user_id.

    Always processes through task generator. Results are delivered to UI (if connected)
    and AWS S3. UI delivery failures are caught gracefully and don't affect AWS S3 delivery.
    """

    # Validate input
    if not request.user_id or not request.messages:
        raise HTTPException(status_code=400, detail="user_id and messages are required")

    # Find the latest user message to use as current_message
    user_messages = [msg for msg in request.messages if msg.get('role') == 'user']
    if not user_messages:
        raise HTTPException(status_code=400, detail="At least one user message is required")

    current_message = user_messages[-1].get('text', '')
    if not current_message:
        raise HTTPException(status_code=400, detail="Latest user message must have text")

    logger.info(f"External API: Received request for user {request.user_id} with {len(request.messages)} messages")

    # Convert messages to conversation history format for task generator
    conversation_history = []
    for msg in request.messages[:-1]:  # All except the last message
        role = msg.get('role', 'user')
        text = msg.get('text', '')

        conversation_history.append({
            "id": str(uuid.uuid4()),
            "type": "user" if role == "user" else "agent",
            "content": text,
            "timestamp": datetime.now().isoformat(),
            "source": "external-api",
            "user_id": request.user_id
        })

    # Always forward to task generator (don't check UI connectivity)
    try:
        task_generator_url = "http://langgraph-kafka-task-generator:8001"

        async with httpx.AsyncClient(timeout=90.0) as client:
            payload = {
                "conversation_history": conversation_history,
                "current_message": current_message,
                "user_id": request.user_id
            }

            logger.info(f"External API: Forwarding to task generator for user {request.user_id}")
            response = await client.post(f"{task_generator_url}/generate-task", json=payload)

            if response.status_code == 200:
                logger.info(f"External API: Successfully processed request for user {request.user_id}")
                return {
                    "status": "success",
                    "message": "Request processed through task generator",
                    "user_id": request.user_id,
                    "message_count": len(request.messages),
                    "delivery": "task_generator"
                }
            else:
                logger.error(f"External API: Task generator error for user {request.user_id}: {response.status_code}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Task generator error: {response.status_code}"
                )

    except httpx.RequestError as e:
        logger.error(f"External API: Network error for user {request.user_id}: {str(e)}")
        logger.error(f"External API: Error type: {type(e).__name__}")
        raise HTTPException(
            status_code=503,
            detail=f"Unable to connect to task generator service: {str(e)}"
        )
    except Exception as e:
        logger.error(f"External API: Unexpected error for user {request.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/chat/history")
async def get_chat_history(user_id: Optional[str] = Query(None)):
    """Get recent chat history, optionally filtered by user_id"""
    if user_id:
        # Filter messages for specific user
        user_messages = [msg for msg in messages if msg.get('user_id') == user_id]
        return {"messages": user_messages[-50:]}  # Last 50 messages for this user
    else:
        return {"messages": messages[-50:]}  # Last 50 messages (all users)

@app.get("/chat/stream")
async def chat_stream(user_id: Optional[str] = Query(None)):
    """SSE endpoint for real-time chat updates (fed by Kafka response topic)"""
    
    # Start Kafka consumer if not already running
    start_kafka_consumer()
    
    async def event_generator():
        # Create a queue for this client
        client_queue = asyncio.Queue()
        sse_clients[client_queue] = user_id  # Track user_id for this client
        
        try:
            # Send recent messages to new client, filtered by user_id
            recent_messages = messages[-50:]  # Get more recent messages to filter from
            if user_id:
                # Filter messages for this specific user
                user_recent_messages = [msg for msg in recent_messages if msg.get('user_id') == user_id][-10:]
                for message in user_recent_messages:
                    yield f"data: {json.dumps(message)}\n\n"
            else:
                # Send all recent messages if no user_id specified
                for message in recent_messages[-10:]:
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
                del sse_clients[client_queue]
    
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

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)