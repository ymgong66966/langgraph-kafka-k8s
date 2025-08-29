import os
import json
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, TypedDict
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError
# from dotenv import load_dotenv
# load_dotenv()

# Configure LangSmith tracing
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", os.getenv("LANGCHAIN_TRACING", "false"))
os.environ["LANGCHAIN_ENDPOINT"] = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = "langgraph-kafka-task-generator"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'dev-langgraph-agent-events')
KAFKA_RESULTS_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
ROUTER_AGENT_PROMPT = """
You are a router agent that determines which agent to use based on the user's input. Here is the chat history: {chat_history}. 

Here are the agent names and descriptions to choose from:
- frontend_agent: an agent that can be a frontdesk kind of assistant for caregivers, it is best for keeping the conversation going, being engaging, caring, and helpful. It is not for generating well-thought-out recommendations or solutions. For example, if the user is asking for recommendations for babysitting services, the frontend agent should not be used.
- task_generator_agent: an agent that can generate well-thought-out recommendations or solutions. It has access to tool calls below: online_general_online_search_with_one_query (Search the web with Firecrawl), online_google_places_search (Find places using Google Places API), online_website_map (Map websites and find relevant URLs using vector search), online_scrape_multiple_websites_after_website_map (Scrape multiple websites concurrently). For example, if the user is asking for recommendations for in-home care services, the task generator agent should be used.

I want you to choose the agent that best fits the user's input. The output should be a JSON object with the following format: {{"agent": "frontend_agent"}} or {{"agent": "task_generator_agent"}}. IMPORTANT: Do not include any additional text such as 'json' or 'json object' or explanation in the output. Only include the JSON object.
"""

FRONTEND_AGENT_PROMPT = """
You are a compassionate and professional frontend assistant specializing in caregiver support. Your primary role is to provide emotional support, maintain engaging conversations, and offer general guidance while being warm and empathetic.

## Your Core Responsibilities:
- **Emotional Support**: Acknowledge the challenges of caregiving and provide encouragement
- **Active Listening**: Show understanding of the caregiver's situation and feelings
- **Conversation Flow**: Keep discussions natural and engaging with thoughtful follow-up questions
- **General Guidance**: Offer basic tips, reassurance, and emotional validation
- **Resource Awareness**: Know when to suggest that specialized help might be needed

## Your Communication Style:
- Use warm, caring, and professional language
- Show genuine empathy for caregiving challenges
- Ask open-ended questions to better understand their needs
- Validate their feelings and experiences
- Provide encouragement and positive reinforcement
- Be patient and non-judgmental

## What You Should NOT Do:
- Provide specific medical advice or recommendations
- Generate detailed research or complex solutions
- Make specific service recommendations (that's for the task generator agent)
- Offer professional medical, legal, or financial advice
- Overwhelm with too much information at once

## Context Information:
**Chat History**: {chat_history}
**User Information**: {user_info}

## Your Response Guidelines:
1. **Acknowledge**: Recognize their situation and any emotions expressed
2. **Empathize**: Show understanding of their caregiving challenges
3. **Engage**: Ask thoughtful questions to better understand their needs
4. **Support**: Offer emotional validation and encouragement
5. **Guide**: If they need specific recommendations or research, gently suggest they might benefit from more specialized assistance

Respond in a warm, caring manner that makes the caregiver feel heard, supported, and understood. Keep your response conversational and focused on their emotional well-being while gathering information about their needs. Most importantly, you need to talk like a human and not like a robot. Keep your response short and to the point, make sure your response is not more than 5 sentences. Try to ask meaningful and open-ended questions to better understand their needs. Try to ask questions to keep them engaged. Try to make friends with them. Remember, you are talking to caregivers. You have to ask questions they may be interested in answering and keep the conversation going. Try to make the conversation engaging and interesting. Try to make the conversation fun and enjoyable.
"""

TASK_DELEGATION_PROMPT = """
You are a friendly caregiver assistant who has just delegated the user's request to a specialized task solver. Your job is to:

1. **Acknowledge the delegation**: Let them know you've passed their request to a specialist who will provide detailed help
2. **Keep the conversation going**: Ask related questions or continue the natural flow of conversation
3. **Be conversational and friendly**: Talk like a caring friend, not a robot
4. **Show genuine interest**: Ask follow-up questions that show you care about their situation

## Context:
**Chat History**: {chat_history}
**User Information**: {user_info}
**Router Decision**: The system decided to delegate this to the task solver because it requires detailed research/recommendations

## Your Response Guidelines:
- Keep it short (2-3 sentences max)
- Acknowledge that you're getting them specialized help
- Ask a related question to keep them engaged
- Be warm and conversational
- If they were being casual/chatty, match that tone
- If they seem stressed, be supportive

## Examples:
- "I've sent your request to our {{research specialist agent}} who'll find you some great options! You should hear from {{the corresponding agent}} soon. While they're working on that, {{ask a follow-up question to get more information about their needs}}"
- "Getting you connected with {{an agent who can dig into the best services in your area}}! You should hear from {{the corresponding agent}} soon. In the meantime, how is {{care recipient}} doing recently? Has {{the condition}} been better?"
- "I've passed this along to get you some detailed recommendations! you should expect a reponse back from {{research specialist agent}} soon. How are you feeling about everything else going on? Did {{this difficulty}} make your life more stressful? How are you feeling?"

Respond naturally and keep the conversation flowing while they wait for detailed help.
"""

class AgentState(TypedDict):
    messages: List[BaseMessage]
    user_id: Optional[str]
    user_info: Optional[str]
    processed_data: Optional[dict]
    agent: Optional[str]
    mock: Optional[bool]

class TaskGenerator:
    def __init__(self, mock: bool = False):
        self.mock = mock
        if self.mock == False:
            self.producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                retries=3,
                acks='all'
            )
        
    def send_to_kafka(self, task_data: dict) -> bool:
        if self.mock == True:
            return True
        """Send task data to Kafka topic"""
        try:
            future = self.producer.send(KAFKA_TOPIC, value=task_data)
            record_metadata = future.get(timeout=10)
            logger.info(f"Task sent to Kafka topic {record_metadata.topic} partition {record_metadata.partition}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send task to Kafka: {e}")
            return False

    def send_result_to_kafka(self, result_data: dict) -> bool:
        """Send result data to Kafka results topic"""
        if self.mock == True:
            return True
        try:
            future = self.producer.send(KAFKA_RESULTS_TOPIC, value=result_data)
            record_metadata = future.get(timeout=10)
            logger.info(f"Result sent to Kafka topic {record_metadata.topic} partition {record_metadata.partition}")
            return True
        except KafkaError as e:
            logger.error(f"Failed to send result to Kafka: {e}")
            return False

    def get_user_info(self, user_id: str) -> str:
        """Retrieve user information from API"""
        import requests
        import json

        url = "https://h9d1ldlv65.execute-api.us-east-2.amazonaws.com/dev/getuser-mcp"

        payload = {"user_Id": user_id}
        headers1 = {
            "x-api-key": "iwja4JC4q765W7VlfqBVx2RAYSISs9lPwEyqNvfh",
            "Content-Type": "application/json"
        }

        try:
            response1 = requests.post(url, headers=headers1, data=json.dumps(payload))
            user_info_json = json.loads(response1.text)
            care_recipients = user_info_json["careRecipients"]
            user_info_filtered = {}
            for item, value in user_info_json.items():
                if item != "careRecipients" and item != "user_Id":
                    user_info_filtered[item] = value
            care_recipients_ids = []
            for care_recipient in care_recipients:
                care_recipients_ids.append((care_recipient["recipient_Id"], care_recipient["relationship"]))
            user_info_final = "User themselves information: " + json.dumps(user_info_filtered) + "\n\nUser's Care Recipients information: "

        except Exception as e:
            print(f"Error: {e}")
            return "user is a caregiver"
        for care_recipient_id, relationship in care_recipients_ids:
            payload2 = {"recipient_Id": care_recipient_id}
            url2 = "https://h9d1ldlv65.execute-api.us-east-2.amazonaws.com/dev/getrecipient-mcp"
            headers2 = {
                "x-api-key": "iwja4JC4q765W7VlfqBVx2RAYSISs9lPwEyqNvfh",
                "Content-Type": "application/json"
            }
            try:
                response2 = requests.post(url2, headers=headers2, data=json.dumps(payload2))
                user_info_final += "\nRelationship with the user: " + relationship + "\n" + response2.text
            except Exception as e:
                print(f"Error: {e}")
                return "user is a caregiver"
        return user_info_final
        # return f"User info: address: Noca Lofts, Lansing, MI. I'm taking care of my mom who is 75 years old. She has Alzheimer's disease. She is diabetic and has high blood pressure. I need help with her care."

async def task_generator_node(state: AgentState) -> dict:
    """Process messages and prepare data for task solver"""
    messages = state.get('messages', [])
    user_id = state.get('user_id')
    user_info = state.get('user_info', '')
    
    # Get user info if user_id provided but user_info is empty
    task_gen = TaskGenerator(mock=state.get('mock', False))
    if user_id and not user_info:
        user_info = task_gen.get_user_info(user_id)
    
    # Convert LangChain messages to dict format for Kafka
    messages_data = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            messages_data.append({
                "type": "human",
                "role": "user",
                "content": msg.content
            })
        elif isinstance(msg, AIMessage):
            messages_data.append({
                "type": "ai", 
                "role": "assistant",
                "content": msg.content
            })
        else:
            # Handle other message types
            messages_data.append({
                "type": "unknown",
                "content": str(msg.content) if hasattr(msg, 'content') else str(msg)
            })
    
    # Prepare task data for the new format
    task_data = {
        "messages": messages_data,
        "user_id": user_id,
        "user_info": user_info
    }
    
    try:
        # Send to Kafka
        success = task_gen.send_to_kafka(task_data)
        
        if success:
            logger.info("Task successfully sent to Kafka")
            
            # NEW: Generate conversational response after delegation
            if not state.get('mock', False) and OPENAI_API_KEY:
                # Convert messages to chat history string
                chat_history = ""
                for msg in messages:
                    if isinstance(msg, HumanMessage):
                        chat_history += f"User: {msg.content}\n"
                    elif isinstance(msg, AIMessage):
                        chat_history += f"Assistant: {msg.content}\n"
                
                # Use GPT-4o for delegation response
                llm = ChatOpenAI(
                    temperature=0.3,
                    api_key=OPENAI_API_KEY,
                    model="gpt-4o"
                )
                
                prompt = PromptTemplate.from_template(TASK_DELEGATION_PROMPT)
                chain = prompt | llm
                
                try:
                    # Generate delegation acknowledgment response
                    response = await chain.ainvoke({
                        "chat_history": chat_history,
                        "user_info": user_info
                    })
                    
                    logger.info(f"Delegation response generated: {response.content[:100]}...")
                    
                    # Generate task_id for this delegation response
                    delegation_task_id = str(uuid.uuid4())
                    
                    # Format response for chat interface (send to results topic)
                    delegation_response = {
                        "content": response.content,
                        "source": "task-generator-delegation",
                        "task_id": delegation_task_id,
                        "type": "delegation_response",
                        "user_id": user_id,
                        "metadata": {
                            "user_info": user_info,
                            "original_task_delegated": True,
                            "agent_type": "task_generator"
                        }
                    }
                    
                    # Send delegation response to Kafka results topic
                    delegation_success = task_gen.send_result_to_kafka(delegation_response)
                    
                    if delegation_success:
                        logger.info("Delegation response sent to Kafka results topic")
                    else:
                        logger.error("Failed to send delegation response to Kafka")
                    
                    return {
                        "processed_data": {
                            **task_data,
                            "delegation_response": response.content,
                            "delegation_task_id": delegation_task_id,
                            "delegation_sent": delegation_success
                        }
                    }
                    
                except Exception as e:
                    logger.error(f"Error generating delegation response: {e}")
                    # Still return success for the main task delegation
                    return {"processed_data": task_data}
            
            return {"processed_data": task_data}
        else:
            logger.error("Failed to send task to Kafka")
            return {"processed_data": {"error": "Failed to send task to Kafka"}}
            
    except Exception as e:
        logger.error(f"Task generation error: {e}")
        return {"processed_data": {"error": str(e)}}

async def router_node(state: AgentState) -> dict:
    """Router node to determine which agent to use"""
    messages = state.get('messages', [])
    
    if not OPENAI_API_KEY:
        logger.error("OpenAI API key not configured")
        return {"agent": "frontend_agent"}  # Default fallback
    
    # Convert messages to chat history string
    chat_history = ""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            chat_history += f"Assistant: {msg.content}\n"
    
    # Use GPT-4o to determine routing
    llm = ChatOpenAI(
        temperature=0.1,
        api_key=OPENAI_API_KEY,
        model="gpt-4o"
    )
    
    # Use new LangChain syntax
    prompt = PromptTemplate.from_template(ROUTER_AGENT_PROMPT)
    chain = prompt | llm
    
    try:
        # Get routing decision from GPT-4o
        response = await chain.ainvoke({"chat_history": chat_history})
        logger.info(f"Router response: {response.content}")
        
        # Parse JSON response
        routing_data = json.loads(response.content.strip())
        agent_choice = routing_data.get("agent", "frontend_agent")
        
        # Map task_generator_agent to task_generator for consistency
        if agent_choice == "task_generator_agent":
            agent_choice = "task_generator"
        
        logger.info(f"Routing to: {agent_choice}")
        return {"agent": agent_choice}
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from router: {e}")
        return {"agent": "frontend_agent"}  # Default fallback
    except Exception as e:
        logger.error(f"Router error: {e}")
        return {"agent": "frontend_agent"}  # Default fallback

def route_to_agent(state: AgentState) -> str:
    """Route function for conditional edges"""
    # The router_node adds 'agent' to the state, we need to return it for routing
    agent_choice = state.get("agent", "frontend_agent")
    logger.info(f"Routing decision: {agent_choice}")
    return agent_choice

async def frontend_agent_node(state: AgentState) -> dict:
    """Frontend agent node with GPT-4o call"""
    messages = state.get('messages', [])
    user_id = state.get('user_id')
    user_info = state.get('user_info', '')
    print("test_messages", messages)

    # Get user info if needed
    task_gen = TaskGenerator(mock=state.get('mock', False))
    if user_id and not user_info:
        user_info = task_gen.get_user_info(user_id)
    
    if not OPENAI_API_KEY:
        logger.error("OpenAI API key not configured")
        return {"processed_data": {"error": "OpenAI API key not configured"}}
    
    # Convert messages to chat history string
    chat_history = ""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            chat_history += f"Assistant: {msg.content}\n"
    
    # Use GPT-4o for frontend response
    llm = ChatOpenAI(
        temperature=0.3,
        api_key=OPENAI_API_KEY,
        model="gpt-4o"
    )
    
    # Use new LangChain syntax
    prompt = PromptTemplate.from_template(FRONTEND_AGENT_PROMPT)
    chain = prompt | llm
    
    try:
        # Generate frontend response
        response = await chain.ainvoke({
            "chat_history": chat_history,
            "user_info": user_info
        })
        
        logger.info(f"Frontend agent response: {response.content[:100]}...")
        
        # Generate task_id for consistency
        task_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        # Format response to match task_solver_agent.py KAFKA_RESULTS_TOPIC format
        chat_response = {
            "content": response.content,
            "source": "frontend-agent",
            "task_id": task_id,
            "type": "frontend_response",
            "user_id": user_id,  # Include user_id for proper message filtering
            "metadata": {
                "user_info": user_info,
                "agent_type": "frontend"
            }
        }
        print("chat_response", chat_response)
        # Send to Kafka RESULTS topic (same as task_solver_agent)
        success = task_gen.send_result_to_kafka(chat_response)
        
        if success:
            logger.info("Frontend response sent to Kafka results topic")
            return {
                "processed_data": {
                    "agent_used": "frontend_agent",
                    "response": response.content,
                    "task_id": task_id,
                    "kafka_sent": True
                }
            }
        else:
            logger.error("Failed to send frontend response to Kafka")
            return {
                "processed_data": {
                    "agent_used": "frontend_agent",
                    "response": response.content,
                    "task_id": task_id,
                    "kafka_sent": False,
                    "error": "Failed to send to Kafka"
                }
            }
            
    except Exception as e:
        logger.error(f"Frontend agent error: {e}")
        return {"processed_data": {"error": str(e), "agent_used": "frontend_agent"}}

def create_task_generator_graph() -> StateGraph:
    """Create and return the task generator graph"""
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("task_generator", task_generator_node)
    workflow.add_node("router", router_node)
    workflow.add_node("frontend_agent", frontend_agent_node)
    
    # Add edges
    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router",
        route_to_agent,
        {
            "frontend_agent": "frontend_agent",
            "task_generator": "task_generator"
        }
    )
    workflow.add_edge("task_generator", END)
    workflow.add_edge("frontend_agent", END)
    
    return workflow.compile()

# Create the graph instance
graph = create_task_generator_graph()

async def generate_task_from_messages(messages: List[BaseMessage], user_id: str = None, user_info: str = None, mock: bool = False) -> dict:
    """Generate task from messages and send to Kafka"""
    print("messages", messages)
    print("user_id", user_id)
    print("user_info", user_info)
    result = await graph.ainvoke({
        "messages": messages,
        "user_id": user_id,
        "user_info": user_info,
        "processed_data": None,
        "mock": mock
    })
    return result

# Backward compatibility function
async def generate_task_from_history(conversation_history: List[BaseMessage], user_id: str = None, mock: bool = False) -> dict:
    """Convenience function for backward compatibility - converts string to messages"""
    # Convert string conversation to messages
    messages = conversation_history
    return await generate_task_from_messages(messages, user_id, mock=mock)

if __name__ == "__main__":
    # Example usage
    import asyncio
    
    async def main():
        # Test with new message format
        # test_messages = [
        #     HumanMessage(content="I need help finding a babysitting service near my home in Chicago."),
        #     AIMessage(content="I'd be happy to help you find babysitting services in Chicago."),
        #     HumanMessage(content="I live at 222 East Pearson Street and need someone for weekends.")
        # ]
        
        # result = await generate_task_from_messages(
        #     messages=test_messages,
        #     user_id="0742e9f2-502f-4e5f-92ed-ee6436bf9ca3"
        # )
        # print(f"Generated task: {result}")
        
        # Test backward compatibility
        # test_conversation = """
        # User: I need help finding a babysitting service near my home in Chicago.
        # Assistant: I'd be happy to help you find babysitting services in Chicago. 
        # User: I live at 222 East Pearson Street and need someone for weekends.
        # """
        test_conversation = [
            HumanMessage(content="I need help finding a babysitting service near my home in Chicago."),
            AIMessage(content="I'd be happy to help you find babysitting services in Chicago."),
            HumanMessage(content="I live at 222 East Pearson Street and need someone for weekends.")
        ]
        
        result2 = await generate_task_from_history(test_conversation, user_id="123456", mock=True)
        print(f"Generated task (legacy): {result2}")
    
    asyncio.run(main())