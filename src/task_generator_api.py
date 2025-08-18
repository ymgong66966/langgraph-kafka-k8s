import os
import json
import logging
import threading
from typing import List, Dict, Any, Optional, TypedDict
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langgraph.graph import END, StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
from contextlib import asynccontextmanager

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

TASK_DECISION_PROMPT = """
You are a task manager for a multi-agent system. You will be given a conversation history from a user.

Your job is to decide whether you can directly answer the user's question or if you need to delegate the task to another agent.

If you CAN directly answer (simple questions, greetings, general information):
- Return JSON: {{"action": "direct_answer", "response": "your direct answer to the user"}}

If you CANNOT directly answer (complex tasks, analysis, calculations, research):
- Return JSON: {{"action": "delegate_task", "task_name": "short task name", "task_description": "detailed task description", "context": "useful context information"}}

Input:
Conversation History: {conversation_history}

IMPORTANT: Return ONLY valid JSON without any additional text, formatting, or code blocks.
"""

class AgentState(TypedDict):
    conversation_history: List[BaseMessage]
    generated_task: Optional[str]

class TaskRequest(BaseModel):
    conversation_history: str
    context: Optional[str] = None

class TaskResponse(BaseModel):
    task_name: str
    task_description: str
    context: str
    status: str = "sent_to_kafka"

# Global producer instance
producer = None

class TaskGenerator:
    def __init__(self):
        global producer
        if producer is None:
            try:
                logger.info(f"Initializing Kafka producer with servers: {KAFKA_BOOTSTRAP_SERVERS}")
                producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    retries=3,
                    acks='all',
                    security_protocol='PLAINTEXT',
                    request_timeout_ms=30000,
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

async def task_generator_node(state: AgentState) -> dict:
    """Generate tasks from conversation history"""
    conversation_history = state.get('conversation_history', '')
    
    if not OPENAI_API_KEY:
        logger.error("OpenAI API key not configured")
        return {"generated_task": "Error: OpenAI API key not configured"}
    
    llm = ChatOpenAI(
        temperature=0.7,
        api_key=OPENAI_API_KEY,
        model="gpt-4o-mini"
    )
    
    prompt = PromptTemplate(
        template=TASK_DECISION_PROMPT,
        input_variables=["conversation_history"]
    )
    
    chain = LLMChain(llm=llm, prompt=prompt)
    
    try:
        # Generate decision
        response = await chain.arun(conversation_history=conversation_history)
        logger.info(f"Generated decision: {response}")
        
        # Parse JSON response
        decision_data = json.loads(response.strip())
        
        if decision_data.get("action") == "direct_answer":
            # Direct answer - send to chat immediately
            logger.info("Providing direct answer to user")
            return {"generated_task": response, "decision_data": decision_data, "action": "direct_answer"}
        
        elif decision_data.get("action") == "delegate_task":
            # Task delegation - validate required fields
            required_fields = ["task_name", "task_description", "context"]
            if not all(field in decision_data for field in required_fields):
                raise ValueError(f"Missing required fields for task delegation. Expected: {required_fields}")
            
            logger.info("Delegating task to task solver")
            return {"generated_task": response, "decision_data": decision_data, "action": "delegate_task"}
        
        else:
            raise ValueError(f"Unknown action: {decision_data.get('action')}")
            
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response: {e}")
        return {"generated_task": f"Error: Invalid JSON response - {str(e)}"}
    except Exception as e:
        logger.error(f"Task generation error: {e}")
        return {"generated_task": f"Error: {str(e)}"}

def create_task_generator_graph() -> StateGraph:
    """Create and return the task generator graph"""
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("task_generator", task_generator_node)
    
    # Add edges
    workflow.add_edge(START, "task_generator")
    workflow.add_edge("task_generator", END)
    
    # Add checkpointer for state persistence (disabled in dev)
    disable_checkpointer = os.getenv('DISABLECHECKPOINTER', 'false').lower() == 'true'
    if disable_checkpointer:
        logger.info("Checkpointer disabled via environment variable")
        return workflow.compile()
    else:
        checkpointer = MemorySaver()
        return workflow.compile(checkpointer=checkpointer)

# Create the graph instance
graph = create_task_generator_graph()

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
    description="Generate tasks from conversation history and send to Kafka",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "task-generator",
        "results_consumer_running": results_consumer_running,
        "kafka_servers": KAFKA_BOOTSTRAP_SERVERS,
        "input_topic": KAFKA_TOPIC,
        "results_topic": KAFKA_RESULTS_TOPIC,
        "openai_configured": bool(OPENAI_API_KEY),
        "task_results_count": len(task_results)
    }

@app.post("/generate-task")
async def generate_task(request: TaskRequest, background_tasks: BackgroundTasks):
    """Generate a task from conversation history and send to Kafka"""
    try:
        # Generate task using LangGraph
        # Use config only if checkpointer is enabled
        disable_checkpointer = os.getenv('DISABLECHECKPOINTER', 'false').lower() == 'true'
        if disable_checkpointer:
            result = await graph.ainvoke({
                "conversation_history": request.conversation_history,
                "generated_task": None
            })
        else:
            result = await graph.ainvoke({
                "conversation_history": request.conversation_history,
                "generated_task": None
            }, config={"configurable": {"thread_id": f"task-gen-{hash(request.conversation_history) % 10000}"}})
        
        if "Error:" in result.get("generated_task", ""):
            raise HTTPException(status_code=500, detail=result["generated_task"])
        
        # Parse the generated_task JSON to get action and decision data
        logger.info(f"llm response for logging: {result}")
        generated_task_json = result.get("generated_task", "")
        logger.info(f"Generated decision data for logging: {generated_task_json}")
        if not generated_task_json:
            raise HTTPException(status_code=500, detail="No generated task found")
        
        try:
            decision_data = json.loads(generated_task_json)
            action = decision_data.get("action")
            logger.info(f"Parsed decision: action={action}, decision_data={decision_data}")
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail=f"Invalid generated task JSON: {e}")
        
        if not action:
            raise HTTPException(status_code=500, detail=f"No action found in decision data: {decision_data}")
        
        task_gen = TaskGenerator()
        
        if action == "direct_answer":
            # Send direct response to chat interface
            response_data = {
                "content": decision_data["response"],
                "source": "task-generator",
                "task_id": f"direct-{hash(request.conversation_history) % 10000}",
                "type": "direct_answer"
            }
            
            def send_response_bg():
                try:
                    logger.info(f"Background task: Attempting to send response: {response_data}")
                    logger.info(f"Using KAFKA_RESULTS_TOPIC: {KAFKA_RESULTS_TOPIC}")
                    success = task_gen.send_response_to_chat(response_data)
                    if not success:
                        logger.error("Failed to send direct response to chat")
                    else:
                        logger.info("Successfully sent direct response to chat via Kafka")
                except Exception as e:
                    logger.error(f"Background task exception: {e}")
                    raise
            
            background_tasks.add_task(send_response_bg)
            
            return {"status": "direct_answer", "response": decision_data["response"]}
            
        elif action == "delegate_task":
            # Send task to task solver via Kafka
            task_data = {
                "task_name": decision_data["task_name"],
                "task_description": decision_data["task_description"],
                "context": decision_data["context"],
                "task_id": f"task-{hash(request.conversation_history) % 10000}",
                "source": "task-generator"
            }
            
            def send_to_kafka_bg():
                success = task_gen.send_to_kafka(task_data)
                if not success:
                    logger.error("Background task failed to send to Kafka")
            
            background_tasks.add_task(send_to_kafka_bg)
            
            return TaskResponse(**{
                "task_name": decision_data["task_name"],
                "task_description": decision_data["task_description"], 
                "context": decision_data["context"]
            })
        
        else:
            raise HTTPException(status_code=500, detail=f"Unknown action: {action}")
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse generated task")
    except Exception as e:
        logger.error(f"Task generation error: {e}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error args: {e.args}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send-task-to-kafka")
async def send_task_directly(task_data: dict):
    """Send task data directly to Kafka (for testing)"""
    try:
        required_fields = ["task_name", "task_description", "context"]
        if not all(field in task_data for field in required_fields):
            raise HTTPException(
                status_code=400, 
                detail=f"Missing required fields: {required_fields}"
            )
        
        task_gen = TaskGenerator()
        success = task_gen.send_to_kafka(task_data)
        
        if success:
            return {"status": "success", "message": "Task sent to Kafka", "data": task_data}
        else:
            raise HTTPException(status_code=500, detail="Failed to send task to Kafka")
            
    except Exception as e:
        logger.error(f"Error sending task to Kafka: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/task-result/{task_id}")
async def get_task_result(task_id: str):
    """Get the result of a specific task"""
    if task_id in task_results:
        return task_results[task_id]
    else:
        raise HTTPException(status_code=404, detail="Task result not found")

@app.get("/task-results")
async def list_task_results():
    """List all task results"""
    return {
        "total_results": len(task_results),
        "results": list(task_results.values())
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "LangGraph Task Generator API",
        "status": "running",
        "version": "1.0.0",
        "endpoints": {
            "/generate-task": "POST - Generate task from conversation history",
            "/send-task-to-kafka": "POST - Send task directly to Kafka",
            "/task-result/{task_id}": "GET - Get result of specific task",
            "/task-results": "GET - List all task results",
            "/health": "GET - Health check"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)