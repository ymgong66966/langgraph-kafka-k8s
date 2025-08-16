import os
import json
import logging
import asyncio
import threading
from typing import Dict, Any, Optional, TypedDict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langgraph.graph import END, StateGraph
from langgraph.constants import START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
from contextlib import asynccontextmanager
import uuid
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_INPUT_TOPIC = os.getenv('KAFKA_TOPIC', 'dev-langgraph-agent-events')
KAFKA_OUTPUT_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

TASK_SOLVER_PROMPT = """
You are a helpful task-solving agent. You will receive a task with the following information:
- Task Name: {task_name}
- Task Description: {task_description}
- Context: {context}

Your job is to provide a helpful response to complete or address this task.
Be specific, actionable, and comprehensive in your response.

Provide a detailed solution or answer for the given task.
"""

class TaskSolverState(TypedDict):
    task_id: str
    task_name: str
    task_description: str
    context: str
    solution: Optional[str]
    timestamp: str

class TaskRequest(BaseModel):
    task_id: Optional[str] = None
    task_name: str
    task_description: str
    context: str

class TaskSolution(BaseModel):
    task_id: str
    task_name: str
    task_description: str
    context: str
    solution: str
    timestamp: str
    solver_agent: str = "langgraph-task-solver"

# Global state
consumer_running = False
producer = None
consumer_thread = None
processed_tasks = {}

class TaskSolverAgent:
    def __init__(self):
        global producer
        if producer is None:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                retries=3,
                acks='all'
            )
        self.producer = producer
        self.graph = self.create_solver_graph()
        
    def send_result_to_kafka(self, result_data: dict) -> bool:
        """Send task result to Kafka output topic"""
        try:
            future = self.producer.send(KAFKA_OUTPUT_TOPIC, value=result_data)
            record_metadata = future.get(timeout=10)
            logger.info(f"Task result sent to Kafka topic {record_metadata.topic} partition {record_metadata.partition}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send result to Kafka: {e}")
            return False

    async def solve_task_node(self, state: TaskSolverState) -> Dict[str, Any]:
        """Solve the task using LLM"""
        if not OPENAI_API_KEY:
            logger.error("OpenAI API key not configured")
            return {"solution": "Error: OpenAI API key not configured"}
        
        llm = ChatOpenAI(
            temperature=0.7,
            api_key=OPENAI_API_KEY,
            model="gpt-4o-mini"
        )
        
        prompt = PromptTemplate(
            template=TASK_SOLVER_PROMPT,
            input_variables=["task_name", "task_description", "context"]
        )
        
        chain = LLMChain(llm=llm, prompt=prompt)
        
        try:
            solution = await chain.arun(
                task_name=state["task_name"],
                task_description=state["task_description"],
                context=state["context"]
            )
            
            logger.info(f"Generated solution for task {state['task_id']}: {solution[:100]}...")
            return {"solution": solution}
            
        except Exception as e:
            logger.error(f"Error generating solution: {e}")
            return {"solution": f"Error generating solution: {str(e)}"}

    def create_solver_graph(self) -> StateGraph:
        """Create and return the task solver graph"""
        workflow = StateGraph(TaskSolverState)
        
        # Add nodes
        workflow.add_node("solve_task", self.solve_task_node)
        
        # Add edges
        workflow.add_edge(START, "solve_task")
        workflow.add_edge("solve_task", END)
        
        # Add checkpointer for state persistence
        # checkpointer = MemorySaver()
        # return workflow.compile(checkpointer=checkpointer)
        return workflow.compile()

    async def process_task(self, task_data: dict) -> dict:
        """Process a task and return the solution"""
        task_id = task_data.get('task_id', str(uuid.uuid4()))
        timestamp = datetime.now().isoformat()
        
        # Prepare state
        state = TaskSolverState(
            task_id=task_id,
            task_name=task_data.get('task_name', ''),
            task_description=task_data.get('task_description', ''),
            context=task_data.get('context', ''),
            solution=None,
            timestamp=timestamp
        )
        
        try:
            # Run the LangGraph
            result = await self.graph.ainvoke(state)
            
            # Create solution response
            solution_data = TaskSolution(
                task_id=task_id,
                task_name=result["task_name"],
                task_description=result["task_description"],
                context=result["context"],
                solution=result["solution"],
                timestamp=timestamp
            ).dict()
            
            # Send result to Kafka (format for chat interface)
            chat_response = {
                "content": result["solution"],
                "source": "task-solver", 
                "task_id": task_id,
                "type": "task_solution",
                "metadata": {
                    "task_name": result["task_name"],
                    "task_description": result["task_description"],
                    "context": result["context"]
                }
            }
            self.send_result_to_kafka(chat_response)
            
            # Store in memory for API access
            processed_tasks[task_id] = solution_data
            
            return solution_data
            
        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}")
            error_result = {
                "task_id": task_id,
                "task_name": task_data.get('task_name', ''),
                "solution": f"Error: {str(e)}",
                "timestamp": timestamp
            }
            
            # Send error to chat interface
            error_chat_response = {
                "content": f"Error solving task: {str(e)}",
                "source": "task-solver",
                "task_id": task_id,
                "type": "error"
            }
            self.send_result_to_kafka(error_chat_response)
            
            processed_tasks[task_id] = error_result
            return error_result

def kafka_consumer_loop():
    """Kafka consumer loop that processes tasks"""
    global consumer_running
    consumer_running = True
    
    consumer = KafkaConsumer(
        KAFKA_INPUT_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='task-solver-group',
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        security_protocol='PLAINTEXT',  # Disable SASL for development
        request_timeout_ms=30000,
        connections_max_idle_ms=600000
    )
    
    task_solver = TaskSolverAgent()
    
    try:
        logger.info(f"Starting Kafka consumer for topic: {KAFKA_INPUT_TOPIC}")
        for message in consumer:
            try:
                task_data = message.value
                logger.info(f"Received task: {task_data.get('task_name', 'Unknown')}")
                
                # Process task asynchronously
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(task_solver.process_task(task_data))
                loop.close()
                
                logger.info(f"Task processed successfully: {result['task_id']}")
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Consumer error: {e}")
    finally:
        consumer_running = False
        consumer.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    global consumer_thread
    
    # Startup
    consumer_thread = threading.Thread(target=kafka_consumer_loop, daemon=True)
    consumer_thread.start()
    logger.info("Task Solver Agent started")
    
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
    global consumer_running
    consumer_running = False
    if consumer_thread and consumer_thread.is_alive():
        consumer_thread.join(timeout=5)
    if producer:
        producer.close()
    logger.info("Task Solver Agent shutdown complete")

app = FastAPI(
    title="LangGraph Task Solver Agent",
    description="Solves tasks received from Kafka and returns results",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "task-solver-agent",
        "consumer_running": consumer_running,
        "kafka_servers": KAFKA_BOOTSTRAP_SERVERS,
        "input_topic": KAFKA_INPUT_TOPIC,
        "output_topic": KAFKA_OUTPUT_TOPIC,
        "openai_configured": bool(OPENAI_API_KEY),
        "processed_tasks_count": len(processed_tasks)
    }

@app.post("/solve-task", response_model=TaskSolution)
async def solve_task_direct(task_request: TaskRequest):
    """Solve a task directly via API (bypass Kafka)"""
    task_solver = TaskSolverAgent()
    
    task_data = {
        "task_id": task_request.task_id or str(uuid.uuid4()),
        "task_name": task_request.task_name,
        "task_description": task_request.task_description,
        "context": task_request.context
    }
    
    try:
        result = await task_solver.process_task(task_data)
        return TaskSolution(**result)
    except Exception as e:
        logger.error(f"Error solving task directly: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/task/{task_id}")
async def get_task_result(task_id: str):
    """Get result of a processed task by ID"""
    if task_id in processed_tasks:
        return processed_tasks[task_id]
    else:
        raise HTTPException(status_code=404, detail="Task not found")

@app.get("/tasks")
async def list_processed_tasks():
    """List all processed tasks"""
    return {
        "total_tasks": len(processed_tasks),
        "tasks": list(processed_tasks.keys()),
        "recent_tasks": list(processed_tasks.values())[-10:]  # Last 10 tasks
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "LangGraph Task Solver Agent",
        "status": "running",
        "version": "1.0.0",
        "endpoints": {
            "/solve-task": "POST - Solve task directly",
            "/task/{task_id}": "GET - Get task result by ID",
            "/tasks": "GET - List processed tasks",
            "/health": "GET - Health check"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)