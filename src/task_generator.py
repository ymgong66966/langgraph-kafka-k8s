import os
import json
import logging
from typing import List, Dict, Any, Optional, TypedDict
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langgraph.graph import END, StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage
import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'langgraph-agent-events')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

TASK_GENERATOR_PROMPT = """
You are a task generator for a multi-agent system. You will be given a conversation history. 
Your job is to generate a task name, a task description and a context based on the conversation history. 

The context should contain useful information that can help solve the task. 
The task name should be a short description of the task and the task description should be a more detailed description.

Input:
Conversation History: {conversation_history}

IMPORTANT: Return your response as a JSON object with the following format:
{{"task_name": "short task name", "task_description": "detailed task description", "context": "useful context information"}}

Make sure to return only valid JSON without any additional text or formatting such as json, JSON or output.
"""

class AgentState(TypedDict):
    conversation_history: List[BaseMessage]
    generated_task: Optional[str]

class TaskGenerator:
    def __init__(self):
        self.producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            retries=3,
            acks='all'
        )
        
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
        template=TASK_GENERATOR_PROMPT,
        input_variables=["conversation_history"]
    )
    
    chain = LLMChain(llm=llm, prompt=prompt)
    
    try:
        # Generate task
        response = await chain.arun(conversation_history=conversation_history)
        logger.info(f"Generated task: {response}")
        
        # Parse JSON response
        task_data = json.loads(response.strip())
        
        # Validate required fields
        required_fields = ["task_name", "task_description", "context"]
        if not all(field in task_data for field in required_fields):
            raise ValueError(f"Missing required fields. Expected: {required_fields}")
        
        # Send to Kafka
        task_gen = TaskGenerator()
        success = task_gen.send_to_kafka(task_data)
        
        if success:
            logger.info("Task successfully sent to Kafka")
            return {"generated_task": response}
        else:
            logger.error("Failed to send task to Kafka")
            return {"generated_task": "Error: Failed to send task to Kafka"}
            
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
    
    # Add checkpointer for state persistence
    # checkpointer = MemorySaver()
    # return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()

# Create the graph instance
graph = create_task_generator_graph()

async def generate_task_from_history(conversation_history: str) -> dict:
    """Convenience function to generate task from conversation history"""
    result = await graph.ainvoke({
        "conversation_history": conversation_history,
        "generated_task": None
    })
    return result

if __name__ == "__main__":
    # Example usage
    import asyncio
    
    async def main():
        test_conversation = """
        User: I need help finding a babysitting service near my home in Chicago.
        Assistant: I'd be happy to help you find babysitting services in Chicago. 
        User: I live at 222 East Pearson Street and need someone for weekends.
        """
        
        result = await generate_task_from_history(test_conversation)
        print(f"Generated task: {result}")
    
    asyncio.run(main())