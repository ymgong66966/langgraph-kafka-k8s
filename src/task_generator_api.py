import os
import json
import logging
import threading
from typing import List, Dict, Any, Optional, Union
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
from contextlib import asynccontextmanager
from task_generator import (
    generate_task_from_messages, 
    TaskGenerator as TaskGen
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'dev-langgraph-agent-events')
KAFKA_RESULTS_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Global state
task_results = {}
results_consumer_running = False
results_consumer_thread = None

class TaskRequest(BaseModel):
    conversation_history: Union[str, List[Dict[str, Any]]]  # Can be string or array of message objects
    current_message: Optional[str] = None  # New field for current message
    user_id: Optional[str] = None
    user_info: Optional[str] = None
    context: Optional[str] = None
    needs_human: Optional[bool] = False  # Track if user needs human support

class TaskResponse(BaseModel):
    agent_used: str
    response: Optional[str] = None
    task_id: Optional[str] = None
    kafka_sent: bool
    status: str = "processed"
    needs_human: Optional[bool] = False  # Return updated needs_human state

# Global producer instance
producer = None

class TaskGeneratorAPI:
    def __init__(self):
        global producer
        if producer is None:
            try:
                logger.info(f"Initializing Kafka producer with servers: {KAFKA_BOOTSTRAP_SERVERS}")
                producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    retries=1,
                    acks=1,  # Only wait for leader acknowledgment (faster)
                    security_protocol='PLAINTEXT',
                    request_timeout_ms=5000,  # Reduce timeout to 5 seconds
                    connections_max_idle_ms=600000
                )
                logger.info("Kafka producer initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Kafka producer: {e}")
                raise
        self.producer = producer
        
    def send_to_kafka(self, task_data: dict) -> bool:
        """Send task data to Kafka topic"""
        try:
            future = self.producer.send(KAFKA_TOPIC, value=task_data)
            record_metadata = future.get(timeout=10)
            logger.info(f"Task sent to Kafka topic {record_metadata.topic} partition {record_metadata.partition}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send task to Kafka: {e}")
            return False
    
    def send_response_to_chat(self, response_data: dict) -> bool:
        """Send response directly to chat interface via Kafka response topic"""
        try:
            future = self.producer.send(KAFKA_RESULTS_TOPIC, value=response_data)
            record_metadata = future.get(timeout=10)
            logger.info(f"Response sent to chat via Kafka topic {record_metadata.topic}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send response to chat: {e}")
            return False

def kafka_results_consumer_loop():
    """Kafka consumer loop that processes task results"""
    global results_consumer_running
    results_consumer_running = True
    
    consumer = KafkaConsumer(
        KAFKA_RESULTS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='task-generator-results-group',
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        security_protocol='PLAINTEXT',  # Disable SASL for development
        request_timeout_ms=30000,
        connections_max_idle_ms=600000
    )
    
    try:
        logger.info(f"Starting results consumer for topic: {KAFKA_RESULTS_TOPIC}")
        for message in consumer:
            try:
                result_data = message.value
                task_id = result_data.get('task_id')
                
                if task_id:
                    task_results[task_id] = result_data
                    logger.info(f"Received result for task: {task_id}")
                else:
                    logger.warning("Received result without task_id")
                
            except Exception as e:
                logger.error(f"Error processing result message: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Results consumer error: {e}")
    finally:
        results_consumer_running = False
        consumer.close()

def convert_conversation_to_messages(conversation_history: Union[str, List[Dict[str, Any]]], current_message: Optional[str] = None) -> List[BaseMessage]:
    """Convert conversation history string or array to LangChain messages"""
    messages = []
    
    if isinstance(conversation_history, list):
        # Handle array of message objects from chat interface
        for message in conversation_history:
            # Handle different message formats
            if isinstance(message, dict):
                # Check for different possible formats
                msg_type = message.get("type") or message.get("role")
                content = message.get("content", "")
                
                if msg_type == "user" or msg_type == "human":
                    messages.append(HumanMessage(content=content))
                elif msg_type == "assistant" or msg_type == "ai" or msg_type == "agent" or message.get("source") in ["task-generator", "mcp-task-solver", "frontend_agent", "agent"]:
                    messages.append(AIMessage(content=content))
                elif content:  # If no type specified but has content, treat as user message
                    messages.append(HumanMessage(content=content))
    elif isinstance(conversation_history, str) and conversation_history.strip():
        # Simple parsing - split by lines and detect User/Assistant patterns
        lines = conversation_history.strip().split('\n')
        current_content = ""
        current_role = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("User:"):
                # Save previous message if exists
                if current_role and current_content:
                    if current_role == "user":
                        messages.append(HumanMessage(content=current_content.strip()))
                    elif current_role == "assistant":
                        messages.append(AIMessage(content=current_content.strip()))
                
                # Start new user message
                current_role = "user"
                current_content = line[5:].strip()  # Remove "User:" prefix
                
            elif line.startswith("Assistant:"):
                # Save previous message if exists
                if current_role and current_content:
                    if current_role == "user":
                        messages.append(HumanMessage(content=current_content.strip()))
                    elif current_role == "assistant":
                        messages.append(AIMessage(content=current_content.strip()))
                
                # Start new assistant message
                current_role = "assistant"
                current_content = line[10:].strip()  # Remove "Assistant:" prefix
                
            else:
                # Continue current message
                if current_content:
                    current_content += " " + line
                else:
                    current_content = line
        
        # Add final message
        if current_role and current_content:
            if current_role == "user":
                messages.append(HumanMessage(content=current_content.strip()))
            elif current_role == "assistant":
                messages.append(AIMessage(content=current_content.strip()))
        
        # If no structured conversation found, treat entire input as user message
        if not messages and conversation_history.strip():
            messages.append(HumanMessage(content=conversation_history.strip()))
    
    # Add current message if provided
    if current_message and current_message.strip():
        messages.append(HumanMessage(content=current_message.strip()))
    
    return messages

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    global producer, results_consumer_thread
    logger.info("Starting Task Generator API")
    
    # Start results consumer
    results_consumer_thread = threading.Thread(target=kafka_results_consumer_loop, daemon=True)
    results_consumer_thread.start()
    logger.info("Results consumer started")
    
    # Test Kafka connection
    try:
        test_producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            retries=3,
            request_timeout_ms=5000
        )
        test_producer.close()
        logger.info(f"Successfully connected to Kafka at {KAFKA_BOOTSTRAP_SERVERS}")
    except Exception as e:
        logger.error(f"Failed to connect to Kafka: {e}")
    
    yield
    
    # Shutdown
    global results_consumer_running
    results_consumer_running = False
    if results_consumer_thread and results_consumer_thread.is_alive():
        results_consumer_thread.join(timeout=5)
    if producer:
        producer.close()
    logger.info("Task Generator API shutdown complete")

app = FastAPI(
    title="LangGraph Task Generator API",
    description="Generate tasks from conversation history using task_generator.py",
    version="2.0.0",
    lifespan=lifespan
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "task-generator-api",
        "results_consumer_running": results_consumer_running,
        "kafka_servers": KAFKA_BOOTSTRAP_SERVERS,
        "input_topic": KAFKA_TOPIC,
        "results_topic": KAFKA_RESULTS_TOPIC,
        "openai_configured": bool(OPENAI_API_KEY),
        "task_results_count": len(task_results),
        "using_task_generator_module": True
    }

@app.post("/generate-task")
async def generate_task(request: TaskRequest):
    """Generate a task from conversation history using task_generator.py"""
    try:
        # Log incoming request details
        history_count = len(request.conversation_history) if isinstance(request.conversation_history, list) else 1
        logger.info(f"Received request: history_count={history_count}, current_message='{request.current_message[:100] if request.current_message else 'None'}...', user_id={request.user_id}")
        
        # Convert conversation history to messages
        messages = convert_conversation_to_messages(request.conversation_history, request.current_message)
        
        logger.info(f"After conversion: {len(messages)} total messages")
        logger.info(f"Messages preview: {[f'{type(m).__name__}: {m.content[:50]}...' for m in messages[:3]]}")
        
        # Use the function from task_generator.py
        result = await generate_task_from_messages(
            messages=messages,
            user_id=request.user_id,
            user_info=request.user_info,
            mock=False,  # Set to False for production use
            needs_human=request.needs_human  # Pass needs_human state
        )

        logger.info(f"Task generator result: {result}")

        # Extract processed data
        processed_data = result.get("processed_data", {})

        if "error" in processed_data:
            raise HTTPException(status_code=500, detail=processed_data["error"])

        # Create response based on the result
        agent_used = processed_data.get("agent_used", "unknown")
        response_content = processed_data.get("response", "")
        task_id = processed_data.get("task_id", "")
        kafka_sent = processed_data.get("kafka_sent", False)
        updated_needs_human = processed_data.get("needs_human", request.needs_human)  # Get updated state

        logger.info(f"Updated needs_human state: {updated_needs_human}")

        return TaskResponse(
            agent_used=agent_used,
            response=response_content,
            task_id=task_id,
            kafka_sent=kafka_sent,
            status="processed",
            needs_human=updated_needs_human  # Return updated needs_human state
        )
        
    except Exception as e:
        logger.error(f"Task generation error: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send-task-to-kafka")
async def send_task_directly(task_data: dict):
    """Send task data directly to Kafka (for testing)"""
    try:
        task_gen = TaskGeneratorAPI()
        success = task_gen.send_to_kafka(task_data)
        
        if success:
            return {"status": "success", "message": "Task sent to Kafka", "data": task_data}
        else:
            raise HTTPException(status_code=500, detail="Failed to send task to Kafka")
            
    except Exception as e:
        logger.error(f"Error sending task to Kafka: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# @app.get("/task-result/{task_id}")
# async def get_task_result(task_id: str):
#     """Get the result of a specific task"""
#     if task_id in task_results:
#         return task_results[task_id]
#     else:
#         raise HTTPException(status_code=404, detail="Task result not found")

# @app.get("/task-results")
# async def list_task_results():
#     """List all task results"""
#     return {
#         "total_results": len(task_results),
#         "results": list(task_results.values())
#     }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "LangGraph Task Generator API",
        "status": "running",
        "version": "2.0.0",
        "using_module": "task_generator.py",
        "endpoints": {
            "/generate-task": "POST - Generate task from conversation history",
            # "/send-task-to-kafka": "POST - Send task directly to Kafka",
            "/task-result/{task_id}": "GET - Get result of specific task",
            # "/task-results": "GET - List all task results",
            "/health": "GET - Health check"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)