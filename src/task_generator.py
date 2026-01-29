import os
import json
import logging
import uuid
import time
import random
from datetime import datetime
from typing import List, Dict, Any, Optional, TypedDict
# from langchain.prompts import PromptTemplate  # No longer needed - using direct string formatting
# from langchain_openai import ChatOpenAI  # Replaced with BedrockClient
from langgraph.graph import END, StateGraph, START
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import requests
from kafka import KafkaProducer
from kafka.errors import KafkaError
from fastmcp import Client
import anthropic
import asyncio
from loguru import logger
class AnthropicClient:
    """Anthropic API client for Claude"""
    def __init__(self):
        self.model_id = "claude-sonnet-4-20250514"
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        self.client = anthropic.Anthropic(api_key=api_key)
        logger.info("Successfully initialized Anthropic client")

    def _retry_with_backoff(self, func, max_retries=5, base_delay=1.0, max_delay=60.0, backoff_factor=2.0):
        """Retry function with exponential backoff for throttling"""
        for attempt in range(max_retries):
            try:
                return func()
            except anthropic.RateLimitError as e:
                if attempt < max_retries - 1:
                    # Calculate delay with jitter
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    jitter = random.uniform(0.1, 0.3) * delay
                    total_delay = delay + jitter

                    logger.warning(f"Rate limit detected, retrying in {total_delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(total_delay)
                    continue
                else:
                    # Re-raise on final attempt
                    raise
            except Exception as e:
                # Re-raise non-rate-limit exceptions immediately
                raise

        # This should never be reached, but just in case
        raise Exception(f"Max retries ({max_retries}) exceeded")

    def converse(self, messages, max_tokens=1000, temperature=0.2, top_p=0.9):
        """Send messages to Anthropic API and get response with retry logic"""
        if not self.client:
            raise RuntimeError("Anthropic client not initialized")

        def _make_request():
            # Format messages for Anthropic API
            anthropic_messages = []
            for msg in messages:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

            # Make API call
            response = self.client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                messages=anthropic_messages
            )

            # Extract text from response
            if response.content and len(response.content) > 0:
                # Get first text block
                for block in response.content:
                    if block.type == 'text':
                        return block.text
                return "Error: No text content in response"
            else:
                logger.error(f"Unexpected response format: {response}")
                return "Error: Unexpected response format"

        try:
            return self._retry_with_backoff(_make_request)
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in converse: {e}")
            raise

    # def simple_chat(self, prompt, max_tokens=1000):
    #     """Simple chat interface for single prompts"""
    #     messages = [{"role": "user", "content": prompt}]
    #     return self.converse(messages, max_tokens=max_tokens)

    # async def async_chat(self, prompt, max_tokens=1000, temperature=0.2):
    #     """Async chat interface for single prompts (compatible with LangChain patterns)"""
    #     messages = [{"role": "user", "content": prompt}]
    #     # Run synchronous converse in async context
    #     import asyncio
    #     loop = asyncio.get_event_loop()
    #     return await loop.run_in_executor(None, lambda: self.converse(messages, max_tokens=max_tokens, temperature=temperature))

    # async def async_converse(self, messages, max_tokens=1000, temperature=0.2, top_p=0.9):
    #     """Async converse interface for multiple messages (compatible with LangChain patterns)"""
    #     import asyncio
    #     loop = asyncio.get_event_loop()
    #     return await loop.run_in_executor(None, lambda: self.converse(messages, max_tokens=max_tokens, temperature=temperature, top_p=top_p))





# Configure Langfuse
from langfuse import Langfuse

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")

# Initialize Langfuse client
try:
    if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_HOST:
        langfuse = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST
        )
        logger.info(f"✅ Langfuse client initialized successfully. Host: {LANGFUSE_HOST}")
        logger.info(f"Langfuse client: {LANGFUSE_PUBLIC_KEY}, {LANGFUSE_SECRET_KEY}, {LANGFUSE_HOST}")
    else:
        logger.warning(f"❌ Langfuse environment variables not set properly:")
        logger.warning(f"  LANGFUSE_PUBLIC_KEY: {'✅ set' if LANGFUSE_PUBLIC_KEY else '❌ missing'}")
        logger.warning(f"  LANGFUSE_SECRET_KEY: {'✅ set' if LANGFUSE_SECRET_KEY else '❌ missing'}")
        logger.warning(f"  LANGFUSE_HOST: {'✅ set' if LANGFUSE_HOST else '❌ missing'}")
        langfuse = None
except Exception as e:
    logger.error(f"❌ Failed to initialize Langfuse client: {e}")
    langfuse = None

class TrackedAnthropicClient(AnthropicClient):
    """AnthropicClient with Langfuse tracking"""

    def __init__(self, session_id: str = None, agent_role: str = None, user_id: str = None):
        super().__init__()
        self.session_id = session_id or f"anthropic-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.agent_role = agent_role or "unknown-agent"
        self.user_id = user_id or "anonymous"

    def simple_chat(self, prompt, max_tokens=1000, **kwargs):
        """Simple chat with Langfuse logging"""
        # Create a generation trace with error handling
        generation = None
        if langfuse:
            try:
                generation = langfuse.generation(
                    name=f"anthropic-{self.agent_role}-simple-chat",
                    model=self.model_id,
                    input=prompt,
                    session_id=self.session_id,
                    user_id=self.user_id,
                    metadata={
                        "max_tokens": max_tokens,
                        "system": "langgraph-kafka",
                        "component": "task-generator",
                        **kwargs
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to create Langfuse generation: {e}")
                generation = None

        try:
            # Call the parent converse method directly
            start_time = datetime.now()
            messages = [{"role": "user", "content": prompt}]
            response = super(TrackedAnthropicClient, self).converse(messages, max_tokens=max_tokens)
            end_time = datetime.now()

            # Calculate approximate token counts (rough estimation)
            input_tokens = len(prompt.split()) * 1.3  # Rough approximation
            output_tokens = len(response.split()) * 1.3

            # Update the generation with response
            if generation:
                try:
                    generation.end(
                        output=response,
                        usage={
                            "input": int(input_tokens),
                            "output": int(output_tokens),
                            "total": int(input_tokens + output_tokens)
                        },
                        metadata={
                            "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                            "model": self.model_id
                        }
                    )
                except Exception as langfuse_error:
                    logger.warning(f"Failed to end Langfuse generation: {langfuse_error}")

            return response

        except Exception as e:
            # Log the error
            if generation:
                try:
                    generation.end(
                        level="ERROR",
                        metadata={"error": str(e)}
                    )
                except Exception as langfuse_error:
                    logger.warning(f"Failed to log error to Langfuse: {langfuse_error}")
            raise

    async def async_chat(self, prompt, max_tokens=1000, temperature=0.2, **kwargs):
        """Async chat with Langfuse logging"""
        # Create a generation trace with error handling
        generation = None
        if langfuse:
            try:
                generation = langfuse.generation(
                    name=f"anthropic-{self.agent_role}-async-chat",
                    model=self.model_id,
                    input=prompt,
                    session_id=self.session_id,
                    user_id=self.user_id,
                    metadata={
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "system": "langgraph-kafka",
                        "component": "task-generator",
                        **kwargs
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to create Langfuse generation: {e}")
                generation = None

        try:
            # Call the parent converse method in async context
            start_time = datetime.now()
            messages = [{"role": "user", "content": prompt}]
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: super(TrackedAnthropicClient, self).converse(messages, max_tokens=max_tokens, temperature=temperature))
            end_time = datetime.now()

            # Calculate approximate token counts
            input_tokens = len(prompt.split()) * 1.3
            output_tokens = len(response.split()) * 1.3

            # Update the generation with response
            if generation:
                try:
                    generation.end(
                        output=response,
                        usage={
                            "input": int(input_tokens),
                            "output": int(output_tokens),
                            "total": int(input_tokens + output_tokens)
                        },
                        metadata={
                            "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                            "temperature": temperature,
                            "model": self.model_id
                        }
                    )
                except Exception as langfuse_error:
                    logger.warning(f"Failed to end Langfuse generation: {langfuse_error}")

            return response

        except Exception as e:
            # Log the error
            if generation:
                try:
                    generation.end(
                        level="ERROR",
                        metadata={"error": str(e)}
                    )
                except Exception as langfuse_error:
                    logger.warning(f"Failed to log error to Langfuse: {langfuse_error}")
            raise

def create_conversation_trace(user_id: str = "anonymous", conversation_type: str = "task-generation"):
    """Create a conversation-level trace for grouping related calls"""
    session_id = f"conversation-{user_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace = langfuse.trace(
        name=f"langgraph-{conversation_type}",
        user_id=user_id,
        session_id=session_id,
        project="langgraph-kafka-task-generator",
        metadata={
            "system": "langgraph-kafka",
            "component": "task-generator",
            "conversation_type": conversation_type,
            "timestamp": datetime.now().isoformat()
        }
    )
    return trace, session_id




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
# OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')  # No longer needed - using BedrockClient instead
FILTER_AGENT_PROMPT = """
You are a filter agent that determines if a user's message requires human support (social worker intervention).

Your task: Output ONLY one of these two strings: "ROUTER" or "HUMAN"

Output "HUMAN" if the user's message is semantically similar to ANY of the following types of requests:

**Scheduling & Appointments:**
- Schedule consultation with neurologist/specialist
- Book appointment with memory care specialist/primary care doctor
- Set up follow-up visits
- Schedule rides for therapy/appointments
- Coordinate ongoing medical transportation

**Hiring & Interviewing:**
- Help hire in-home caregiver
- Interview home care agencies
- Screen caregivers and set up interviews

**Phone Calls & Advocacy:**
- Call provider/pharmacy on behalf
- Speak to insurance company on behalf
- Follow up with agencies/providers
- Advocate with providers

**Reminders & Follow-ups:**
- Remind to call Medicaid/insurance
- Call Medicaid on behalf
- Follow up with insurance about paperwork
- Track down missing test results

**Housing & Moving:**
- Coordinate moving into assisted living
- Set up tours at facilities
- Finalize housing placement
- Coordinate facility tours
- Prepare for housing interviews
- Organize moving company/logistics

**Respite Care:**
- Find and book respite care
- Apply for respite care grants
- Compare short-term respite options

**Medication Management:**
- Tell which medications shouldn't be taken together
- Call pharmacy to check prescription
- Clarify medication instructions with doctor

**Family Coordination:**
- Organize family meeting about care plans
- Coordinate schedules with siblings
- Help align family on next steps

**Medical Records & Documentation:**
- Request medical records from hospital
- Transfer records to new provider

**Insurance & Claims:**
- Call insurance about denied claims
- Help appeal denied procedures
- Review bills and flag errors

**Paperwork & Applications:**
- Help fill out FMLA paperwork
- Submit FMLA forms to HR
- Track FMLA claim deadlines
- Gather documents for applications
- Complete and review applications
- Check if anything missing from paperwork

**Financial Assistance:**
- Help apply for financial assistance programs
- Identify qualifying grants/subsidies
- Review expenses for cost-saving programs
- Compare insurance policies
- Understand coverage differences
- Assist with application process

**Disability & VA Benefits:**
- Help with Social Security disability paperwork
- Follow up on disability claims
- Prepare for disability appeals
- Apply for VA caregiver benefits
- Connect with veteran support officer
- Assist with VA insurance appeals

**Home Modifications & Services:**
- Arrange home safety assessment
- Monitor progress on home modifications
- Choose meal delivery service
- Arrange grocery delivery
- Request medical equipment
- Manage referrals and approvals

**Regular Check-ins & Monitoring:**
- Check in monthly and report back
- Assess caregiver/recipient status regularly
- Update care circle on progress

**End-of-Life & Grief:**
- Plan for discharge from rehab
- Plan end-of-life care
- Assist with funeral arrangements
- Find grief counseling resources

**Authorization & Representation:**
- Act as authorized unpaid representative for claims
- Be listed as representative on cases
- Communicate with Social Security on behalf

**Employee Benefits & Mental Health:**
- Understand employee benefits for caregiving
- Verify dependent eligibility with employer
- Find and book therapist covered by insurance
- Coordinate mental health care
- Follow up with mental health providers

**Task Management:**
- Add appointments to calendar
- Track deadlines and reminders
- Organize tasks to prevent things falling through

IMPORTANT: Match based on SEMANTIC SIMILARITY, not exact wording. If the user's intent matches any category above, output "HUMAN".

If the user's message does NOT match any of the above categories, output "ROUTER".

CRITICAL: Output ONLY "ROUTER" or "HUMAN" - no other text, no explanation, no quotes, no formatting.

User message: {user_message}

Your output:"""

ROUTER_AGENT_PROMPT = """
You are a router agent that determines which agent to use based on the user's input. Here is the chat history: {chat_history}. 

Here are the agent names and descriptions to choose from:
- frontend_agent: an agent that can be a frontdesk kind of assistant for caregivers, it is best for keeping the conversation going, being engaging, caring, and helpful. It is not for generating well-thought-out recommendations or solutions. For example, if the user is asking for recommendations for babysitting services, the frontend agent should not be used.
- task_generator_agent: an agent that can generate well-thought-out recommendations or solutions. It has access to tool calls below: online_general_online_search_with_one_query (Search the web with Firecrawl), online_google_places_search (Find places using Google Places API), online_website_map (Map websites and find relevant URLs using vector search), online_scrape_multiple_websites_after_website_map (Scrape multiple websites concurrently). For example, if the user is asking for recommendations for in-home care services, the task generator agent should be used.

If a caregiver makes a comment or question about a topic fully unrelated to health and caregiving, the frontend agent should be used. Example questions:  what is the weather like today, what is the stock price of Apple, What's the capital of Iceland? How do I make sourdough bread? Can you tell me a joke? What's the price of Bitcoin today? What time is it in Tokyo? When was the Eiffel Tower built? What's the best pizza topping? There are some questions very deceiving, becasue they sounds like a question that requires in-depth search or recommendations but in fact they have nothing to do with caregiving, for example, if the user asks for recommendations for restaurants, the frontend agent should be used. If the user asks oh can you compare the prices of stocks, the frontend agent should be used.

If the user's latest response is answering a filler question and does not have a lot of information, the frontend agent should be used. For example, if the chat history looks like this:

/////////
User:
i specifically want to know if the services can be covered by the insurance of veteran
AI:
I’ll be checking each agency’s website for details about whether their services can be covered by veteran insurance or VA benefits. This involves looking through several sources for each agency, so it may take a little while. I know navigating insurance and benefits can be overwhelming—are you helping a specific veteran, or just exploring options for now? If you have any other questions or concerns, feel free to share!
User:
just exploring
//////////
you should route to the frontend agent because the user is simply providing you some more context about a previous request, not asking you to complete a task 

I want you to choose the agent that best fits the user's input. The output should be a JSON object with the following format: {{"agent": "frontend_agent"}} or {{"agent": "task_generator_agent"}}. IMPORTANT: Do not include any additional text such as 'json' or 'json object' or explanation in the output. Only include the JSON object.
"""

FOLLOWUP_QUESTION_PROMPT = """
You are a expert social worker in health care and caregiving. You are given a context and a in-depth user question. Your input includes: 1. user info. 2. user question. 3. chat history. 

  Your task is to acknowledge that what they need and ask related follow-up questions to the user to get more information. The questions can be related to both the user's information, like where their care recipient lives, or their care recipient's condition, confirming who this is for, etc. or can be asking what specific information about that thing do they need, etc. IMPORTANT: You need to limit the number of questions to 1. That one question should be short and concise and there should only be one question mark in the output. The output should be a JSON object with the following format: {{"followup_questions": "your response and follow-up question"}}. IMPORTANT: Do not include any additional text such as 'json' or 'json object' or explanation in the output. Only include the JSON object.

  Here are your inputs:

  User info: {user_info}

  User question: {user_question}

  Chat history: {chat_history}

  Now give me your output:
"""


FRONTEND_AGENT_PROMPT = """
You are a compassionate and professional frontend assistant specializing in caregiver support. Your primary role is to provide emotional support, maintain engaging conversations, and offer general guidance while being warm and empathetic. 

IMPORTANT: If a caregiver makes a comment or question about a topic fully unrelated to health and caregiving, you should provide a response that they'll only be able to discuss things related to health and caregiving. Example questions: what is the weather like today, what is the stock price of Apple, What's the capital of Iceland? How do I make sourdough bread? Can you tell me a joke? What's the price of Bitcoin today? What time is it in Tokyo? When was the Eiffel Tower built? What's the best pizza topping? etc. There are some questions very deceiving, becasue they sounds like a question that requires in-depth search or recommendations but in fact they have nothing to do with caregiving, for example, if the user asks for recommendations for restaurants. or if the user asks oh can you compare the prices of stocks, etc.

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

# TASK_DELEGATION_PROMPT = """
# You are a friendly caregiver assistant who has just delegated the user's request to a specialized task solver. Your job now is to decide what you are gonna say next based on the context you have. You have two options, you can either only respond "no response needed" or you give them a filler response to keep the conversation going. Your context is below:

# ## Context:
# **Chat History**: {chat_history}
# **User Information**: {user_info}
# **Router Decision**: The system decided to delegate this to the task solver because it requires detailed research/recommendations

# There are only two scenarios you will encounter: 
# Scenario 1: in the chat history, there is a clear indication that the user is asking a follow-up question regarding a previous response from the task solver, not the frontend agent. how can you tell if this is the case? for example, if there is a response from the task solver agent one or two messages before the current user response, and the user is asking a question related to the previous response from the task solver agent, then this is a follow-up question.

# ## Your Response Guidelines for Scenario 1:

# Only respond with "no response needed". All lower case, no additional text. Do not include any other text like "response", "answer", "json".

# Scenario 2: in the chat history, there is no clear indication that the user is asking a follow-up question regarding a previous response from the task solver, but the user is asking a question that requires detailed research/recommendations. In this case you should give them a filler response to keep the conversation going.


# ## Your Response Guidelines for Scenario 2:
# - Keep it short (2-3 sentences max)
# - Acknowledge that you're getting them specialized help
# - Ask a related question to keep them engaged
# - Be warm and conversational
# - If they were being casual/chatty, match that tone
# - If they seem stressed, be supportive
# - Acknowledge the delegation: Let them know you've passed their request to a specialist who will provide detailed help
# - Keep the conversation going: Ask related questions or continue the natural flow of conversation
# - Be conversational and friendly: Talk like a caring friend, not a robot
# - Show genuine interest: Ask follow-up questions that show you care about their situation

# ## Examples:
# - "I've sent your request to our {{research specialist agent}} who'll find you some great options! You should hear from {{the corresponding agent}} soon. While they're working on that, {{ask a follow-up question to get more information about their needs}}"
# - "Getting you connected with {{an agent who can dig into the best services in your area}}! You should hear from {{the corresponding agent}} soon. In the meantime, how is {{care recipient}} doing recently? Has {{the condition}} been better?"
# - "I've passed this along to get you some detailed recommendations! you should expect a reponse back from {{research specialist agent}} soon. How are you feeling about everything else going on? Did {{this difficulty}} make your life more stressful? How are you feeling?"

# Respond naturally and keep the conversation flowing while they wait for detailed help.
# """

PLANNER_PROMPT = """
You are a planner agent who is responsible for using the mcp tools to use and the steps to solve a task/answer a question. You know how the mcp tools work and you will use them in all the steps you generate. As the BACKGROUND CONTEXT, you will be given the chat history, where the last message is the user's question, and the user information. As the MCP TOOL CONTEXT, you will be given the mcp tools available to use and their usage instructions. 
_#######################
What you have to do:
1. You need to understand the BACKGROUND CONTEXT and the MCP TOOL CONTEXT. You need to deeply understand the relationship of these mcp tools and hwo they can work as a sequence of actionables. You should be capable of using the useful information from the chat history and the user information for your reasoning. Do not define or specify the input of each tool call. Only tell me which tools to use at each step. IMPORTANT, you have to give me an estimate of how many tool calls are made in each step. You will be harshly punished if you don't do so!!!!
2. You could use any of the mcp tools to solve the task/answer the question. In each one of your steps, you have to use at least one mcp tool. You will be punished if you don't do so. If you need to use multiple mcp tools, describe the order of the mcp tools you are going to use. For example, you could decide to do four web_map tool calls as the first step and then use the results of the web_map tool calls for a scrape_multiple_websites_after_website_map tool call. Or you could decide to do a single general_online_search_with_one_query tool call and see where it takes. 
You need to understand the usage instructions of each mcp tool.
3. When you are generating a plan, I require you to only output the steps as your results. These steps should contain at least one mcp tool. Try to make as less steps as possible. If you can make only one step to get soem info, then only make one step!!!!!
4. Very important note: the /online_website_map call and /online_scrape_multiple_websites_after_website_map call are expensive. You should still make the calls when you need to but you should be a little cautious of making too many these calls if possible. For example, sometimes 
/online_google_places_search alone can provide good amount of info already. although it will be better to follow it with a website_map + 
scrape_multiple_websites_after_website_map combo. You should not use these tools if user is just asking like "oh what are the good options about legal offices around me"
5. Very important note: the /online_website_map call and /online_scrape_multiple_websites_after_website_map calls are expensive. You should still make the calls when you need to but you should be a little cautious of making too many these calls if possible. For example, sometimes 
/online_google_places_search alone can provide good amount of info already. although it will be better to follow it with a website_map + 
scrape_multiple_websites_after_website_map combo. You should not use these tools if user is just asking like "oh what are the good options about legal offices around me"
##########################

################## Example. #############
BACKGROUND CONTEXT:
## Chat History:
AI:
Here are some recommended stroke rehabilitation centers in Chicago that can provide the support your mom needs:

1. **Shirley Ryan AbilityLab**
   - **Address:** 355 E Erie St, Chicago, IL 60611, USA
   - **Rating:** 4.3
   - **Website:** [sralab.org](https://www.sralab.org/)
   - This facility is known for its comprehensive rehabilitation services and innovative approaches to recovery.

2. **Comprehensive Stroke Center at Northwestern Memorial Hospital**
   - **Address:** 259 E Erie St, 19TH FLOOR, SUITE 1900, Chicago, IL 60611, USA
   - **Rating:** 5.0
   - **Website:** [nm.org](https://www.nm.org/locations/comprehensive-stroke-center?utm_source=yext&utm_medium=other+location&utm_campaign=online+listings&y_source=1_ODIyOTgyOS03MTUtbG9jYXRpb24ud2Vic2l0ZQ%3D%3D)
   - This center specializes in stroke care and rehabilitation, offering tailored programs to help patients recover.

3. **RUSH Specialty Hospital - Inpatient Rehabilitation**
   - **Address:** 516 S Loomis St, Chicago, IL 60607, USA
   - **Rating:** 4.4
   - **Website:** [rushspecialtyhospital.com](https://www.rushspecialtyhospital.com/locations-and-tours/il/chicago/inpatient-rehabilitation/?ty=xt)
   - RUSH provides a range of rehabilitation services, including specialized stroke recovery programs.


These facilities offer a range of services tailored to stroke recovery, and I recommend contacting them directly to discuss your mom's specific needs and to inquire about their programs, availability, and any necessary referrals. If you need further assistance or specific information about any of these centers, feel free to ask!
USER:
in the options you provided above, which one is better for my mom? she is a vetera
## User Question:
in the options you provided above, which one is better for my mom? 
## User Information:
my mom is a veteran.

MCP TOOL CONTEXT:
Tool Name: /online_general_online_search_with_one_query
Description: get general information from the internet about something asked by user. Best for: quick one-off Q&A about something. It is like someone wants you to search on google for them about something they don't have knowledge of. For exmaple: "What is respite care?", "What is a power of attorney?", "What is Medicaid?", "Define hospice care." etc. You can use this tool to get general information about something.

Not recommended for: when the user is asking for some specific information about a website or company. In this case you should use website_map tool first to get the urls of interest. The only exception is when the website_map or scrape_multiple_websites_after_website_map tools didn't return meaningful results. This tool can be used as the last resort in that case.



Tool Name: /online_scrape_multiple_websites_after_website_map
Description: Get information from a list of urls about a list of queries. The input: a list of urls (strings) and a list of queries (strings). The output: a list of dictionaries (each dictionary contains the "queries" (string), the "answer" (string)). Best for: When you know which websites/urls you are interested in and want to dive deep into these websites and scrape information about a certain topic. When you are using this tool, you should input a list of the urls you think are of interest from the context or previous tools, and a list of queries you want to scrape information about. Make sure your queries are super relevant to the user intent and concise, otherwise you will be punished harshly. 

Not recommended for: when you only have a web domain or company front web page and still don't know which exact urls are of interest to you. In this case you should use website_map tool first to get the urls of interest.

Tool Name: /online_website_map
Description: Get relevant urls of a web domain about something asked by user. Best for: Finding specific information across multiple websites, when you don't know which website has the information. When you need the most relevant content for a query. 
    Example use case: a user asks follow-up questions about an in-home care agency which we have the website of. The input of the tool are the domain of that website as a string and a list of short queries that contain the user's intention and the city/area of interest. The output of the tool is a list of dictionaries (each dictionary contains the url, the title of the page, and the description of the page). If you think the description of the page is not informative enough, you can then use the urls as the input of the scrape_multiple_websites_after_website_map tool.
    
    IMPORTANT NOTES about the list of queries: 
    the list cannot have more than 3 query strings. Each query string should be short and concise. For example, if user asks about "can you check if this company has services for older adults with dementia?" and from the previous messages or context, we know that the user is interested in the city of oak park, Chicago, then the list of queries should be ["dementia services, Oak Park, Chicago", "Alzheimer services, Oak Park, Chicago"]. You will be punished if the list has more than 3 query strings or if the query strings are not short and informative.

Not recommended for: When you already know which urls to scrape and need comprehensive coverage of these urls (use scrape_multiple_websites_after_website_map tool)


Tool Name: /online_google_places_search
Description: None


Tool Name: add
Description: Adds two integer numbers together.


Tool Name: find_products
Description: Search the product catalog with optional category filtering.

#####################################

 ########################### Example Output ###########################
Step 1: call web_map tool for these agencies's web domains (not the landing pages). Need to make probably 3 web_map tool calls. One for each domain.
Step 2: call scrape_multiple_websites_after_website_map multiple times to with the relevant urls from step 1 results. Need to make probably 3 
scrape_multiple_websites_after_website_map tool calls as well. One for each result from step 1. 
###########################


############### Actual input################################
BACKGROUND CONTEXT:
## Chat History:
{chat_history}

## User Question:
{user_question}

## User Information:
{user_info}

MCP TOOL CONTEXT:
{mcp_tool_context}


##############################################
Very important note: the /online_website_map call and /online_scrape_multiple_websites_after_website_map call are expensive. You should still make the calls when you need to but you should be a little cautious of making too many these calls if possible. For example, sometimes 
/online_google_places_search alone can provide good amount of info already. although it will be better to follow it with a website_map + 
scrape_multiple_websites_after_website_map combo. You should not use these tools if user is just asking like "oh what are the good options about legal offices around me"
Very important note: the /online_website_map call and /online_scrape_multiple_websites_after_website_map calls are expensive. You should still make the calls when you need to but you should be a little cautious of making too many these calls if possible. For example, sometimes 
/online_google_places_search alone can provide good amount of info already. although it will be better to follow it with a website_map + 
scrape_multiple_websites_after_website_map combo. You should not use these tools if user is just asking like "oh what are the good options about legal offices around me"
 ##########################

Your Output:
"""

TASK_DELEGATION_PROMPT = """
You are a friendly caregiver assistant who knows the chat history, user information and the task planner's plan/steps. This is how you should function: First thing, you need to read through the task planner's plan/steps which is about how to solve the user's request. And estimate the time span of the proposed plan/steps. You have to follow this guidline below: Calculate the number of tool calls for all the steps. if there are in total more than 3 tool calls that will be made, then you should rate it as long time span. If there are in total less than 3 tool calls that will be made, then you should rate it as short time span. 
#####################
For example, if the planner's plan is: 
Step 1: Call the /online_website_map tool for each facility's website (schwab.sinaichicago.org and harmonychicago.com) using concise queries like "wheelchair support", "wheelchair accessibility", and "accessible facilities". This will require 2 tool calls, one for each web domain.


Step 2: Use /online_scrape_multiple_websites_after_website_map on the most relevant urls from each website retrieved in step 1 to extract specific information about wheelchair support and accessibility. This will likely require 2 tool calls, one for each set of relevant urls from the respective centers.

Then the number of tool calls is 4. It is a long time span.
####################




Second thing, your job is to decide what you are gonna say next based on the the time span. You only have two options: 1. for short time span, only respond "no response needed".  All lower case, no additional text. Do not include any other text like "response", "answer", "json". 2. For long time span, you generate a very short summary of the plan/steps, letting them know that you will need some time to complete the task. For the summary, don't say the exact tool names, just say the general idea of what you are gonna do. Also add one or two sentences to keep the conversation going. For these sentences, you need to show your empathy, be thoughtful and maybe also ask relevant questions. 
###################
For example, if in the recent user messages, user mentioned that their grandpa got dementia recently. You could say things among: it could be hard to take care of dementia patients, how has it been taking care of your grandpa? is it stressful? Or a good thing to do with dementia people is to do memory practice. Do you want me to give you some ways to do that? //////// You can be creative about what you say.
#####################

Your context is below:

## Context:
##Chat History: 
{chat_history}

## User Question:
{user_question}

## User Information:
{user_info}

## Planner's plan/steps:
{planner_plan}

Your output:
"""

class AgentState(TypedDict):
    messages: List[BaseMessage]
    user_id: Optional[str]
    user_info: Optional[str]
    processed_data: Optional[dict]
    agent: Optional[str]
    mock: Optional[bool]
    filter_result: Optional[str]
    needs_human: Optional[bool]  # Track if user needs human support

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
        
        # Test Anthropic connectivity during initialization
        # self._test_anthropic_connectivity()

    def _test_anthropic_connectivity(self):
        """Test Anthropic API connectivity and log results for verification"""
        try:
            logger.info("🧪 ANTHROPIC TEST: Initializing TrackedAnthropicClient...")
            anthropic_client = TrackedAnthropicClient(
                session_id="anthropic-connectivity-test",
                agent_role="connectivity-test",
                user_id="system"
            )
            test_response = anthropic_client.simple_chat("Say 'Anthropic test successful' in one sentence.")

            logger.info(f"✅ ANTHROPIC TEST SUCCESS: {test_response}")
            logger.info("🎉 Claude model is accessible and working with Langfuse tracking!")

        except Exception as e:
            logger.error(f"❌ ANTHROPIC TEST FAILED: {e}")
            logger.error("🚨 Claude model is NOT accessible in this environment!")

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

        url = "https://tarz0uu2n5.execute-api.us-east-2.amazonaws.com/prod/getuser-mcp"

        payload = {"user_Id": user_id}
        headers1 = {
            "x-api-key": "0h5c9wlW2L8Joqk6fpJUz6NnsIjLB6Su7V62ozcd",
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
            url2 = "https://tarz0uu2n5.execute-api.us-east-2.amazonaws.com/prod/getrecipient-mcp"
            headers2 = {
                "x-api-key": "0h5c9wlW2L8Joqk6fpJUz6NnsIjLB6Su7V62ozcd",
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
    
    # Convert LangChain messages to dict format for Kafka (last 10 messages only)
    recent_messages = messages[-6:] if len(messages) > 4 else messages
    messages_data = []
    for msg in recent_messages:
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
    
    
            
    # NEW: Generate conversational response after delegation
    if not state.get('mock', False):
        # Convert messages to chat history string
        chat_history = ""
        for msg in messages:
            if isinstance(msg, HumanMessage):
                chat_history += f"User: {msg.content}\n"
            elif isinstance(msg, AIMessage):
                chat_history += f"Assistant: {msg.content}\n"
        
        # Use Claude 4 Sonnet via TrackedAnthropicClient for delegation response with Langfuse tracking
        anthropic_client = TrackedAnthropicClient(
            session_id=f"task-gen-{user_id or 'anonymous'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            agent_role="task-generator",
            user_id=user_id or "anonymous"
        )
        mcp_server_url = "http://a3490b37dcce24b6aa32c67158ce1c5b-285867456.us-east-2.elb.amazonaws.com/mcp"
        client = Client({
            "enhanced_mcp": {
                "url": mcp_server_url,
                "transport": "streamable-http"
            }
        })
        
        async with client:
            fastmcp_tools = await client.list_tools()
            tool_information_nice_print = ""
            for tool in fastmcp_tools:
                tool_information_nice_print += f"Tool Name: {tool.name}\n"
                tool_information_nice_print += f"Description: {tool.description}\n"
                tool_information_nice_print += f"Input Schema: {tool.inputSchema}\n"
                tool_information_nice_print += f"Output Schema: {tool.outputSchema}\n"
                tool_information_nice_print += "\n"

        user_question = messages[-1].content
        try:
            # Generate planner response using Bedrock
            planner_prompt = PLANNER_PROMPT.format(
                chat_history=chat_history,
                user_info=user_info,
                user_question=user_question,
                mcp_tool_context=tool_information_nice_print
            )
            planner_plan = await anthropic_client.async_chat(planner_prompt, max_tokens=2000, temperature=0.1)
            logger.info(f"Planner response generated: {planner_plan[:100]}...")

            # Generate task_id for this delegation response
            delegation_task_id = str(uuid.uuid4())

            # Generate delegation response using Bedrock
            delegation_prompt = TASK_DELEGATION_PROMPT.format(
                chat_history=chat_history,
                user_info=user_info,
                user_question=user_question,
                planner_plan=planner_plan
            )
            delegate_response_content = await anthropic_client.async_chat(delegation_prompt, max_tokens=1000, temperature=0.1)
            # Format response for chat interface (send to results topic)
            if delegate_response_content.lower() != "no response needed":
                # Prepare task data for the new format
                task_data = {
                    "messages": messages_data,
                    "user_id": user_id,
                    "user_info": user_info,
                    "planner_plan": planner_plan
                }
    
                success = task_gen.send_to_kafka(task_data)
                
                if success:
                    logger.info("Task successfully sent to Kafka")
                else:
                    logger.error("Failed to send task to Kafka")
                filler_response = {
                    "content": delegate_response_content,
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
                delegation_success = task_gen.send_result_to_kafka(filler_response)
            
                if delegation_success:
                    logger.info("Delegation response sent to Kafka results topic")
                else:
                    logger.error("Failed to send delegation response to Kafka")
            
                return {
                    "processed_data": {
                        **task_data,
                        "delegation_response": filler_response["content"],
                        "delegation_task_id": delegation_task_id,
                        "delegation_sent": delegation_success,
                        "needs_human": state.get('needs_human', False)  # Preserve needs_human state
                    }
                }
            else:
                task_data = {
                    "messages": messages_data,
                    "user_id": user_id,
                    "user_info": user_info,
                    "planner_plan": planner_plan
                }
    
                success = task_gen.send_to_kafka(task_data)
                
                if success:
                    logger.info("Task successfully sent to Kafka")
                else:
                    logger.error("Failed to send task to Kafka")
                return {
                    "processed_data": {
                        **task_data,
                        "delegation_response": planner_plan,
                        "delegation_task_id": delegation_task_id,
                        "delegation_sent": True,
                        "needs_human": state.get('needs_human', False)  # Preserve needs_human state
                    }
                }
            
        except Exception as e:
            logger.error(f"Task generation error: {e}")
            return {"processed_data": {"error": str(e), "needs_human": state.get('needs_human', False)}}

async def filter_node(state: AgentState) -> dict:
    """Filter node to determine if user needs human support based on needs_human state and question matching"""
    messages = state.get('messages', [])
    user_id = state.get('user_id', 'anonymous')
    current_needs_human = state.get('needs_human', False)  # Get current needs_human state

    anthropic_client = TrackedAnthropicClient(
            session_id=f"filter-{user_id or 'anonymous'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            agent_role="filter-agent",
            user_id=user_id or "anonymous"
        )
    user_message = messages[-1].content

    try:
        # Use LLM to determine if message matches human support question list
        filter_prompt = FILTER_AGENT_PROMPT.format(user_message=user_message)
        llm_response = await anthropic_client.async_chat(filter_prompt, max_tokens=100, temperature=0.1)
        logger.info(f"Filter LLM response: {llm_response}")

        # Extract HUMAN or ROUTER from response (handle extra text)
        matches_human_questions = "HUMAN" in llm_response.upper()

        # Implement routing logic based on three scenarios:
        # Scenario A: needs_human=true + user asks new question NOT in list → route to ROUTER (agentic system)
        # Scenario B: needs_human=false + question matches list → set needs_human=true, route to HUMAN
        # Scenario C: needs_human=false + question doesn't match → route to ROUTER (normal flow)

        if current_needs_human and not matches_human_questions:
            # Scenario A: User waiting for social worker but asks unrelated question
            logger.info("Scenario A: needs_human=true, question NOT in list → routing to ROUTER (agentic system)")
            return {"filter_result": "ROUTER", "needs_human": True}  # Keep needs_human=true

        elif not current_needs_human and matches_human_questions:
            # Scenario B: First time matching human support questions
            logger.info("Scenario B: needs_human=false, question matches list → routing to HUMAN, setting needs_human=true")
            return {"filter_result": "HUMAN", "needs_human": True}  # Set needs_human=true

        else:
            # Scenario C: Normal flow - question doesn't match, proceed to router
            logger.info(f"Scenario C: needs_human={current_needs_human}, question doesn't match → routing to ROUTER")
            return {"filter_result": "ROUTER", "needs_human": current_needs_human}  # Keep current state

    except Exception as e:
        logger.error(f"Filter error: {e}")
        # On error, default to ROUTER to avoid blocking users
        return {"filter_result": "ROUTER", "needs_human": current_needs_human}

async def followup_question_node(state: AgentState) -> dict:
    messages = state.get('messages', [])
    user_id = state.get('user_id')
    user_info = state.get('user_info', '')
    
    # Get user info if user_id provided but user_info is empty
    task_gen = TaskGenerator(mock=state.get('mock', False))
    if user_id and not user_info:
        user_info = task_gen.get_user_info(user_id)

    chat_history = ""
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            chat_history += f"Assistant: {msg.content}\n"

    user_question = messages[-1].content

    followup_question_prompt = FOLLOWUP_QUESTION_PROMPT.format(user_info=user_info, user_question=user_question, chat_history=chat_history)
    anthropic_client = TrackedAnthropicClient(
            session_id=f"followup-question-{user_id or 'anonymous'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            agent_role="followup-question",
            user_id=user_id or "anonymous"
        )
    try:
        response_content = await anthropic_client.async_chat(followup_question_prompt, max_tokens=500, temperature=0.1)
        response_content_json = json.loads(response_content)
        logger.info(f"Follow-up question response: {response_content}")
        followup_response = {
                    "content": "*** Follow-up Question ***\n" + response_content_json.get("followup_questions", "Is this care needed for yourself or someone else, and do you have any specific requirements like specialized medical needs or preferred scheduling?"),
                    "source": "followup-question",
                    "task_id": "followup-question",
                    "type": "followup_question",
                    "user_id": user_id,
                    "metadata": {
                        "user_info": user_info,
                        "original_task_delegated": True,
                        "agent_type": "task_generator"
                    }
                }
        task_data =   {
                    "messages": "",
                    "user_id": "",
                    "user_info": "",
                    "planner_plan": ""
                }
        # Send follow-up question response to Kafka results topic
        followup_success = task_gen.send_result_to_kafka(followup_response)
        if followup_success:
            logger.info("Follow-up question response sent to Kafka results topic")
        else:
            logger.error("Failed to send follow-up question response to Kafka")
        return {
            "processed_data": {
                **task_data,
                "delegation_response": followup_response["content"],
                "delegation_task_id": "followup",
                "delegation_sent": followup_success,
                "needs_human": state.get('needs_human', False)  # Preserve needs_human state
            }
        }

    except Exception as e:
        logger.error(f"Follow-up question error: {e}")
        task_data =   {
                    "messages": "",
                    "user_id": "",
                    "user_info": "",
                    "planner_plan": ""
                }
        return {
            "processed_data": {
                **task_data,
                "delegation_response": f"Follow-up question error: {e}",
                "delegation_task_id": "followup",
                "delegation_sent": False,
                "needs_human": state.get('needs_human', False)  # Preserve needs_human state
            }
        }

    

async def router_node(state: AgentState) -> dict:

    """Router node to determine which agent to use"""
    messages = state.get('messages', [])

    # Convert messages to chat history string
    chat_history = ""
    logger.info(f"Router received {len(messages)} messages")
    for i, msg in enumerate(messages):
        
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
            logger.info(f"Message {i}: User - {msg.content[:50]}...")
        elif isinstance(msg, AIMessage):
            if len(msg.content) > 26:
                if msg.content[0:26] == "*** Follow-up Question ***" and len(messages) - i < 3:
                    return {"agent": "task_generator"}
            chat_history += f"Assistant: {msg.content}\n"
            logger.info(f"Message {i}: Assistant - {msg.content[:50]}...")

    logger.info(f"Full chat history for routing:\n{chat_history[:500]}...")

    # Use Claude 4 Sonnet via TrackedAnthropicClient for routing decision with Langfuse tracking
    # Extract user_id from state if available
    user_id = state.get('user_id', 'anonymous')
    anthropic_client = TrackedAnthropicClient(
        session_id=f"router-{user_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        agent_role="router",
        user_id=user_id
    )

    try:
        # Get routing decision from Claude 4 Sonnet
        router_prompt = ROUTER_AGENT_PROMPT.format(chat_history=chat_history)
        response_content = await anthropic_client.async_chat(router_prompt, max_tokens=500, temperature=0.1)
        logger.info(f"Router response: {response_content}")

        # Parse JSON response
        routing_data = json.loads(response_content.strip())
        agent_choice = routing_data.get("agent", "frontend_agent")

        # Map task_generator_agent to task_generator for consistency
        if agent_choice == "task_generator_agent":
            agent_choice = "followup_question_node" # actually route it to followup question node when the in-depth question first pops up. If there was already a followup question asked, then go to task generator

        logger.info(f"Routing to: {agent_choice}")
        return {"agent": agent_choice}

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from router: {e}")
        return {"agent": "frontend_agent"}  # Default fallback
    except Exception as e:
        logger.error(f"Router error: {e}")
        return {"agent": "frontend_agent"}  # Default fallback
def filter_to_route(state: AgentState) -> str:
    filter_result = state.get("filter_result", "HUMAN")
    return filter_result

async def human_support_node(state: AgentState) -> dict:
    """Human support node - escalates to social worker"""
    user_id = state.get('user_id', 'anonymous')
    user_info = state.get('user_info', '')
    messages = state.get('messages', [])
    task_gen = TaskGenerator(mock=state.get('mock', False))
    task_id = str(uuid.uuid4())

    # Get user's latest message for context
    user_message = messages[-1].content if messages else ""

    # Generate a brief summary of what the user needs
    anthropic_client = TrackedAnthropicClient(
        session_id=f"human-support-{user_id or 'anonymous'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        agent_role="human-support",
        user_id=user_id or "anonymous"
    )

    try:
        # Generate concise summary
        summary_prompt = f"""Based on this user request, provide a very brief 1-sentence summary of what they need:

User request: {user_message}

Output only the summary, nothing else. Example format: "Help scheduling a neurologist appointment" or "Assistance hiring an in-home caregiver"."""

        summary = await anthropic_client.async_chat(summary_prompt, max_tokens=100, temperature=0.1)
        summary = summary.strip()

        # Construct human escalation message
        content = f"I understand you need: {summary}. A social worker will reach back out to you to support you with this."

    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        # Fallback message if summary generation fails
        content = "A social worker will reach back out to you to support you with your request."

    human_support_response = {
        "content": content,
        "source": "human-support",
        "task_id": task_id,
        "type": "human_escalation",
        "user_id": user_id,
        "metadata": {
            "user_info": user_info,
            "needs_human": True,
            "escalated_to_human": True,
            "agent_type": "human_support"
        }
    }

    # Send response to Kafka results topic
    delegation_success = task_gen.send_result_to_kafka(human_support_response)

    if delegation_success:
        logger.info("Human support escalation sent to Kafka results topic")
    else:
        logger.error("Failed to send human support escalation to Kafka")

    task_data = {
        "messages": "",
        "user_id": user_id,
        "user_info": user_info,
        "planner_plan": ""
    }

    return {
        "processed_data": {
            **task_data,
            "delegation_response": content,
            "delegation_task_id": task_id,
            "delegation_sent": delegation_success,
            "needs_human": True  # Mark that this user needs human support
        }
    }


def route_to_agent(state: AgentState) -> str:
    """Route function for conditional edges"""
    # The router_node adds 'agent' to the state, we need to return it for routing
    agent_choice = state.get("agent", "frontend_agent")
    logger.info(f"Routing decision: {agent_choice}")
    return agent_choice

async def frontend_agent_node(state: AgentState) -> dict:
    """Frontend agent node with Claude 4 Sonnet via Bedrock"""
    messages = state.get('messages', [])
    user_id = state.get('user_id')
    user_info = state.get('user_info', '')
    print("test_messages", messages)

    # Get user info if needed
    task_gen = TaskGenerator(mock=state.get('mock', False))
    if user_id and not user_info:
        user_info = task_gen.get_user_info(user_id)

    # Convert messages to chat history string
    chat_history = ""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
        elif isinstance(msg, AIMessage):
            chat_history += f"Assistant: {msg.content}\n"

    # Use Claude 4 Sonnet via TrackedAnthropicClient for frontend response with Langfuse tracking
    anthropic_client = TrackedAnthropicClient(
        session_id=f"frontend-{user_id or 'anonymous'}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        agent_role="frontend-agent",
        user_id=user_id or "anonymous"
    )

    try:
        # Generate frontend response
        frontend_prompt = FRONTEND_AGENT_PROMPT.format(
            chat_history=chat_history,
            user_info=user_info
        )
        response_content = await anthropic_client.async_chat(frontend_prompt, max_tokens=1000, temperature=0.3)

        logger.info(f"Frontend agent response: {response_content[:100]}...")

        # Generate task_id for consistency
        task_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        # Format response to match task_solver_agent.py KAFKA_RESULTS_TOPIC format
        chat_response = {
            "content": response_content,
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
                    "response": response_content,
                    "task_id": task_id,
                    "kafka_sent": True,
                    "needs_human": state.get('needs_human', False)  # Preserve needs_human state
                }
            }
        else:
            logger.error("Failed to send frontend response to Kafka")
            return {
                "processed_data": {
                    "agent_used": "frontend_agent",
                    "response": response_content,
                    "task_id": task_id,
                    "kafka_sent": False,
                    "error": "Failed to send to Kafka",
                    "needs_human": state.get('needs_human', False)  # Preserve needs_human state
                }
            }

    except Exception as e:
        logger.error(f"Frontend agent error: {e}")
        return {"processed_data": {"error": str(e), "agent_used": "frontend_agent", "needs_human": state.get('needs_human', False)}}

def create_task_generator_graph() -> StateGraph:
    """Create and return the task generator graph"""
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("task_generator", task_generator_node)
    workflow.add_node("router", router_node)
    workflow.add_node("frontend_agent", frontend_agent_node)
    workflow.add_node("filter", filter_node)
    workflow.add_node("human_support_node", human_support_node)
    workflow.add_node("followup_question_node", followup_question_node)
    # Add edges
    workflow.add_edge(START, "filter")
    workflow.add_conditional_edges(
        "filter",
        filter_to_route,
        {
            "HUMAN": "human_support_node",
            "ROUTER": "router"
        }
    )

    workflow.add_conditional_edges(
        "router",
        route_to_agent,
        {
            "frontend_agent": "frontend_agent",
            "task_generator": "task_generator",
            "followup_question_node": "followup_question_node"
        }
    )
    workflow.add_edge("task_generator", END)
    workflow.add_edge("frontend_agent", END)
    workflow.add_edge("human_support_node", END)
    workflow.add_edge("followup_question_node", END)
    
    return workflow.compile()

# Create the graph instance
graph = create_task_generator_graph()

async def generate_task_from_messages(messages: List[BaseMessage], user_id: str = None, user_info: str = None, mock: bool = False, needs_human: bool = False) -> dict:
    """Generate task from messages and send to Kafka"""
    # print("messages", messages)
    # print("user_id", user_id)
    # print("user_info", user_info)
    # print("needs_human", needs_human)
    result = await graph.ainvoke({
        "messages": messages,
        "user_id": user_id,
        "user_info": user_info,
        "processed_data": None,
        "mock": mock,
        "needs_human": needs_human  # Pass needs_human state to the graph
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