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
from fastmcp import Client
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
        
        # Test Bedrock connectivity during initialization
        self._test_bedrock_connectivity()
    
    def _test_bedrock_connectivity(self):
        """Test Bedrock connectivity and log results for verification"""
        try:
            from bedrock_client import BedrockClient
            logger.info("🧪 BEDROCK TEST: Initializing Bedrock client...")
            
            bedrock = BedrockClient()
            test_response = bedrock.simple_chat("Say 'Bedrock test successful' in one sentence.")
            
            logger.info(f"✅ BEDROCK TEST SUCCESS: {test_response}")
            logger.info("🎉 Claude model is accessible and working in this environment!")
            
        except ImportError as e:
            logger.error(f"❌ BEDROCK TEST FAILED: BedrockClient import failed - {e}")
        except Exception as e:
            logger.error(f"❌ BEDROCK TEST FAILED: {e}")
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
    
    # Convert LangChain messages to dict format for Kafka (last 10 messages only)
    recent_messages = messages[-4:] if len(messages) > 4 else messages
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
            temperature=0.1,
            api_key=OPENAI_API_KEY,
            model="gpt-4.1"
        )
        mcp_server_url = "http://a7a09ec61615e46a7892d050e514c11e-1977986439.us-east-2.elb.amazonaws.com/mcp"
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

        prompt = PromptTemplate.from_template(PLANNER_PROMPT)
        chain = prompt | llm
        user_question = messages[-1].content
        try:
            # Generate delegation acknowledgment response
            response = await chain.ainvoke({
                "chat_history": chat_history,
                "user_info": user_info,
                "user_question": user_question,
                "mcp_tool_context": tool_information_nice_print
            })
            planner_plan = response.content
            logger.info(f"Delegation response generated: {response.content[:100]}...")
            # Generate task_id for this delegation response
            delegation_task_id = str(uuid.uuid4())
            delegate_prompt = PromptTemplate.from_template(TASK_DELEGATION_PROMPT)
            delegate_chain = delegate_prompt | llm

            delegate_response = await delegate_chain.ainvoke({
                "chat_history": chat_history,
                "user_info": user_info,
                "user_question": user_question,
                "planner_plan": planner_plan
            })
            # Format response for chat interface (send to results topic)
            if delegate_response.content.lower() != "no response needed":
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
                    "content": delegate_response.content,
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
                        "delegation_response": filler_response.content,
                        "delegation_task_id": delegation_task_id,
                        "delegation_sent": delegation_success
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
                        "delegation_response": response.content,
                        "delegation_task_id": delegation_task_id,
                        "delegation_sent": True
                    }
                }
            
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
    logger.info(f"Router received {len(messages)} messages")
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            chat_history += f"User: {msg.content}\n"
            logger.info(f"Message {i}: User - {msg.content[:50]}...")
        elif isinstance(msg, AIMessage):
            chat_history += f"Assistant: {msg.content}\n"
            logger.info(f"Message {i}: Assistant - {msg.content[:50]}...")
    
    logger.info(f"Full chat history for routing:\n{chat_history[:500]}...")
    
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