import os
import json
import logging
import asyncio
import threading
import requests
import uuid
from typing import Dict, Any, Optional, TypedDict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
from contextlib import asynccontextmanager
from datetime import datetime
import uuid
# Import MCP LangGraph Agent
from fastmcp import Client
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import ToolMessage
from typing import Literal, Annotated
from operator import add

# Configure LangSmith tracing
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", os.getenv("LANGCHAIN_TRACING", "false"))
os.environ["LANGCHAIN_ENDPOINT"] = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = "langgraph-kafka-task-solver"

# Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka-service:9092')
KAFKA_INPUT_TOPIC = os.getenv('KAFKA_TOPIC', 'dev-langgraph-agent-events')
KAFKA_OUTPUT_TOPIC = os.getenv('KAFKA_RESULTS_TOPIC', 'dev-langgraph-task-results')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Updated State for MCP integration
# class TaskSolverState(TypedDict):
#     task_id: str
#     messages: List[BaseMessage]
#     user_info: str
#     solution: Optional[str]
#     timestamp: str

class TaskRequest(BaseModel):
    task_id: Optional[str] = None
    messages: List[Dict[str, Any]]  # Changed from task_name/description
    user_id: Optional[str] = None
    user_info: Optional[str] = None

class TaskSolution(BaseModel):
    task_id: str
    messages: List[Dict[str, Any]]
    user_info: Optional[str] = None
    solution: str
    timestamp: str
    solver_agent: str = "langgraph-mcp-task-solver"

# MCP Agent State (copied from firecrawl_mcp_graph.py)
class AgentState(TypedDict):
    """State for the MCP-powered LangGraph agent"""
    messages: Annotated[List[BaseMessage], add]
    user_info: str
    user_id: str
    user_question: str  # Original user question to keep context clear
    tool_call_count: int
    max_tool_calls: int

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
        
        # MCP Agent configuration
        self.openai_api_key = OPENAI_API_KEY
        self.max_tool_calls = 4
        self.mcp_server_url = "http://a7a09ec61615e46a7892d050e514c11e-1977986439.us-east-2.elb.amazonaws.com/mcp"
        
        # Initialize LLM
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=self.openai_api_key,
            temperature=0.1
        )
        self.memory = MemorySaver()
        self.graph = None
        
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
    
    def sanitize_tool_name(self, name: str) -> str:
        """Sanitize tool name to match OpenAI pattern ^[a-zA-Z0-9_-]+$"""
        import re
        sanitized = name.replace('/', '_').replace(' ', '_')
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', sanitized)
        sanitized = re.sub(r'_+', '_', sanitized).strip('_')
        return sanitized
    
    def _format_tool_result(self, observation, tool_name: str = "unknown") -> str:
        """Format tool results for better LLM understanding"""
        try:
            if isinstance(observation, dict):
                # Structure JSON results with better formatting
                if len(str(observation)) > 2000:
                    # For very large results, provide summary
                    keys = list(observation.keys())[:5]
                    preview = {k: observation[k] for k in keys if k in observation}
                    return f"📊 Large JSON result ({len(observation)} keys):\n{json.dumps(preview, indent=2)}\n... (truncated - use data from {tool_name})"
                else:
                    return f"📊 JSON data:\n{json.dumps(observation, indent=2)}"
            elif isinstance(observation, list):
                # Format list results with bullets for better readability
                if len(observation) > 20:
                    preview_items = observation[:15]
                    formatted = "\n".join([f"• {item}" for item in preview_items])
                    return f"📋 List results ({len(observation)} total):\n{formatted}\n... (showing first 15 items)"
                else:
                    return f"📋 List results:\n" + "\n".join([f"• {item}" for item in observation])
            elif isinstance(observation, str):
                # Handle string results with proper truncation
                if len(observation) > 1500:
                    return f"📄 Text result ({len(observation)} chars):\n{observation[:1200]}...\n\n[Content truncated - full data available for analysis]"
                else:
                    return f"📄 Text result:\n{observation}"
            else:
                # Handle other types
                result_str = str(observation)
                if len(result_str) > 1000:
                    return f"📊 Result ({type(observation).__name__}):\n{result_str[:800]}...\n[Truncated - {len(result_str)} total characters]"
                else:
                    return f"📊 Result ({type(observation).__name__}):\n{result_str}"
        except Exception as e:
            # Fallback for any formatting errors
            return f"📊 Raw result (formatting error: {e}):\n{str(observation)[:1000]}..."
    
    def convert_fastmcp_tool_to_openai_format(self, tool):
        """Convert FastMCP tool to OpenAI function format"""
        sanitized_name = self.sanitize_tool_name(tool.name)
        return {
            "type": "function",
            "function": {
                "name": sanitized_name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        }
        
    async def should_continue(self, state: AgentState) -> Literal["agent", "final_answer"]:
        """Use LLM to intelligently decide whether to continue research or provide final answer"""
        messages = state["messages"]
        user_question = state["user_question"] 
        user_info = state["user_info"]
        tool_call_count = state["tool_call_count"]
        max_calls = state["max_tool_calls"]
        
        # Force final answer if at tool limit
        if tool_call_count >= max_calls:
            logger.info("🚫 Tool limit reached - routing to final_answer")
            return "final_answer"
        
        # CRITICAL: Check if the last agent message has tool calls
        # If not, the agent decided not to use tools, so go to final_answer
        last_ai_message = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_message = msg
                break
        
        if last_ai_message and hasattr(last_ai_message, 'tool_calls') and last_ai_message.tool_calls:
            # Agent made tool calls, continue with agent after tools execute
            logger.info(f"🔧 Agent made {len(last_ai_message.tool_calls)} tool calls - routing to agent")
            return "agent"
        else:
            # Agent didn't make tool calls, provide final answer
            logger.info("✅ No tool calls from agent - routing to final_answer")
            return "final_answer"
        
        # Create LLM routing prompt to analyze tool results
        routing_prompt = f"""You are a research completion analyzer. Your job is to determine if enough information has been gathered to fully answer the user's question.

🎯 ORIGINAL USER QUESTION: "{user_question}"

👤 USER CONTEXT: {user_info}

🔍 RESEARCH STATUS:
• Tools used: {tool_call_count}/{max_calls}
• Tool results gathered: {len(tool_messages)} total

📊 RECENT TOOL RESULTS TO ANALYZE:
{self._format_recent_tools_for_routing(recent_tools)}

🤔 ROUTING DECISION REQUIRED:
Analyze the tool results above and decide:

**Route to "CONTINUE_RESEARCH"** if:
- Tool results are incomplete or insufficient for the user's question
- Missing key information (specific details, contact info, locations, etc.)
- User question has multiple parts that aren't fully addressed
- Tool results contain errors or need verification from different sources

**Route to "FINAL_ANSWER"** if:
- Tool results provide comprehensive information to fully answer the question
- You have sufficient specific details (names, addresses, contacts, services, etc.)
- The gathered data directly addresses all parts of the user's question
- Additional tools would likely provide redundant information

RESPOND WITH ONLY: "CONTINUE_RESEARCH" or "FINAL_ANSWER". Do not provide any additional text such as "answer" or "decision".

Decision:"""

        # Use LLM to make routing decision
        try:
            routing_llm = ChatOpenAI(
                model="gpt-4o-mini",  # Use faster model for routing decisions
                temperature=0.1,
                api_key=OPENAI_API_KEY
            )
            
            response = await routing_llm.ainvoke([HumanMessage(content=routing_prompt)])
            decision = response.content.strip().upper()
            
            logger.info(f"🤖 LLM routing decision: {decision}")
            
            if "CONTINUE_RESEARCH" in decision:
                return "agent"
            elif "FINAL_ANSWER" in decision:
                return "final_answer"
            else:
                # Fallback: if unclear response, continue research if tools available
                logger.warning(f"⚠️ Unclear LLM routing response: {decision}, defaulting to agent")
                return "agent"
                print("test")
        except Exception as e:
            logger.error(f"❌ Error in LLM routing decision: {e}")
            # Fallback: continue if we have tools left, otherwise final answer
            return "agent" if tool_call_count < max_calls else "final_answer"
    
    def _format_recent_tools_for_routing(self, tool_messages: List[ToolMessage]) -> str:
        """Format recent tool messages for routing decision prompt"""
        if not tool_messages:
            return "No tool results available."
        
        formatted = []
        for i, tool_msg in enumerate(tool_messages, 1):
            # Extract tool name from the formatted content
            content_lines = tool_msg.content.split('\n')
            tool_name = "Unknown Tool"
            if content_lines and "TOOL EXECUTED:" in content_lines[0]:
                tool_name = content_lines[0].replace("🔧 TOOL EXECUTED:", "").strip()
            
            result_summary = tool_msg.content
            formatted.append(f"Tool {i}: {tool_name}\nResult: {result_summary}")
        
        return "\n\n".join(formatted)
    
    async def agent_node(self, state: AgentState) -> Dict[str, Any]:
        """Main agent node that decides whether to use tools or provide answer"""
        messages = state["messages"]
        user_info = state["user_info"]
        tool_call_count = state["tool_call_count"]
        max_calls = state["max_tool_calls"]
        
        # Use MCP client to get tools and bind to LLM
        client = Client({
            "enhanced_mcp": {
                "url": self.mcp_server_url,
                "transport": "streamable-http"
            }
        })
        
        async with client:
            fastmcp_tools = await client.list_tools()
            tool_descriptions = [tool.description for tool in fastmcp_tools]
            tools = [self.convert_fastmcp_tool_to_openai_format(tool) for tool in fastmcp_tools]
            self.tool_name_mapping = {
                self.sanitize_tool_name(tool.name): tool.name 
                for tool in fastmcp_tools
            }
            tool_informations = [{"tool_name": self.sanitize_tool_name(tool.name), "description": tool.description, "input_schema": tool.inputSchema, "output_schema": tool.outputSchema} for tool in fastmcp_tools]
        
        # Only add system message at the very beginning
        has_ai_messages = any(isinstance(msg, AIMessage) for msg in messages)
        
        if not has_ai_messages and len(messages) == 1 and isinstance(messages[0], HumanMessage):
            system_prompt = f"""You are an intelligent task-solving assistant with access to powerful web intelligence tools.

User Information: {user_info}

Available MCP Tools:
{str(tool_informations)}

Tool Call Status: {tool_call_count}/{max_calls} calls used.

Instructions:
1. If you need more information to answer the user's question and haven't reached the tool limit, use the appropriate tools.
2. If you've used {max_calls} tools or have enough information, provide a comprehensive final answer.
3. Choose tools strategically - use search for general info, places for locations, website mapping for specific domains.
4. RATE LIMITING AWARENESS: Web scraping tools are subject to API rate limits. Use them judiciously and avoid excessive scraping.
5. CRITICAL: Only make ONE tool call at a time. Tools execute sequentially with delays to prevent rate limiting.
   - Making multiple simultaneous tool calls will overwhelm external APIs
   - Each tool call waits for completion before the next begins

For example, for questions like: What Medicaid benefits are available in my state? Which local agencies provide in-home care? Are there adult daycare centers near me? Where can I rent a hospital bed for home use? What local resources help with incontinence supplies? 

Useful route #1 to consider is:
You need to get the location info from user_info first, either the user's location or the care recipient's location. And then you can use google maps to search for relevant places and get their website domain. And then, you can use online_website_map tool to get the website urls that may contain the answer. And then you can use online_scrape_multiple_websites_after_website_map tool to scrape the websites and generate the answer based on the scraped content.


Useful route #2 to consider is:

Use online_general_online_search_with_one_query tool to search for relevant information on the internet with a query contain location info and the user's need. If the returned websites have useful info, then answer based on the scraped content. Otherwise, you can either choose to take useful route #2 or modify your search query and do online_general_online_search_with_one_query again.


Be flexible on the strategy. Because you could be handling a new question or a follow-up question of your previous answer. If it is a follow-up, you don't have to follow the routes from the beginning. For example, maybe you can jump to online_website_map with a known domain from the conversational context and just provide the urls as your answer. Maybe there are previous previous website scrapes in the context that you can directly use without calling a tool, etc.
But note that typically online_scrape_multiple_websites_after_website_map tool should be used after online_website_map tool, if you ever decide to use it.


Important, you should generate your tool calls following the inputSchema of the tools. For example, if inputSchema requires an array, you have to output an array in the tool call. you will be penalized if you don't follow the inputSchema. For example, if the inputSchema is 'query': {{'items': {{'type': 'string'}}, 'title': 'Query', 'type': 'array'}}, then you need to output a list of strings in the tool call for query such as: domain: angelshomecare.com
'query': ['dementia care', 'services for elderly', 'senior care', 'dementia support']

"""
            messages_with_system = [HumanMessage(content=system_prompt)] + messages
        else:
            # For subsequent interactions, add context guidance to help LLM use tool results
            tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage)]
            
            if tool_messages and tool_call_count < max_calls:
                # Add guidance prompt that instructs LLM to analyze tool results for next decision
                guidance_prompt = f"""
Based on the tool results above and the user's request, decide your next action:
User Information: {user_info}

Available MCP Tools:
{str(tool_informations)}

CURRENT STATUS: {tool_call_count}/{max_calls} tools used, {max_calls - tool_call_count} remaining

DECISION PROCESS:
1. **ANALYZE TOOL RESULTS**: Review the specific data gathered from previous tool calls above
2. **ASSESS COMPLETENESS**: Do you have sufficient information to fully answer the user's question?
3. **STRATEGIC NEXT STEP**: 
   - If you have comprehensive data → Stop using tools, provide final answer
   - If you need more specific details → Use remaining tools strategically  
   - If user asked follow-up → Focus tools on the new specific requirement

IMPORTANT: First of all, only make one tool call at a time. You will be annihilated if you make multiple tool calls. Second, Base your decision on the actual tool results visible in this conversation, not assumptions.
"""
                messages_with_system = messages + [HumanMessage(content=guidance_prompt)]
            else:
                messages_with_system = messages
        
        # Add debugging for agent decision context
        logger.info(f"🤔 Agent deciding: {tool_call_count}/{max_calls} tools used, {len(messages)} messages in context")
        
        # Bind tools to the model if we haven't hit the limit
        if tool_call_count < max_calls and tools:
            model_with_tools = self.llm.bind(tools=tools)
            logger.info("🔧 Agent has access to tools for this decision")
        else:
            model_with_tools = self.llm
            logger.info("🚫 Agent cannot use tools (limit reached or no tools available)")
        
        response = await model_with_tools.ainvoke(messages_with_system)
        logger.info(f"🎭 Agent response: {'has tool calls' if hasattr(response, 'tool_calls') and response.tool_calls else 'no tool calls'}")
        return {"messages": [response]}
    
    async def tool_node(self, state: AgentState) -> Dict[str, Any]:
        """Execute MCP tools sequentially with rate limiting and robust error handling"""
        import asyncio
        import time

        client = Client({
            "enhanced_mcp": {
                "url": self.mcp_server_url,
                "transport": "streamable-http"
            }
        })

        async with client:
            last_message = state["messages"][-1]
            tool_messages = []
            tools_used = 0

            logger.info(f"🔧 Executing {len(last_message.tool_calls)} tool calls SEQUENTIALLY")

            # Sequential execution with delays and retries
            for i, tool_call in enumerate(last_message.tool_calls):
                sanitized_name = tool_call["name"]
                original_name = getattr(self, 'tool_name_mapping', {}).get(sanitized_name, sanitized_name)

                logger.info(f"🛠️ [{i+1}/{len(last_message.tool_calls)}] Calling tool: {original_name}")

                # Add delay between tool calls to prevent rate limiting
                if i > 0:  # No delay before first tool
                    delay = 1.5  # 1.5 second delay between tools
                    logger.info(f"⏱️ Waiting {delay}s between tool calls...")
                    await asyncio.sleep(delay)

                # Execute tool with retry logic
                success = False
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        if attempt > 0:
                            # Exponential backoff: 2^attempt seconds
                            backoff_delay = 2 ** attempt
                            logger.info(f"🔄 Retry {attempt}/{max_retries-1} after {backoff_delay}s delay...")
                            await asyncio.sleep(backoff_delay)

                        observation = await client.call_tool(original_name, tool_call["args"])
                        logger.info(f"✅ Tool {original_name} returned {len(str(observation))} chars of data")

                        # Create enhanced, contextual tool message
                        formatted_result = self._format_tool_result(observation, original_name)
                        tool_message = ToolMessage(
                            content=f"""🔧 TOOL EXECUTED: {original_name}
📝 Called with parameters: {json.dumps(tool_call["args"], indent=2)}

{formatted_result}

---""",
                            tool_call_id=tool_call["id"]
                        )
                        tool_messages.append(tool_message)
                        tools_used += 1
                        success = True
                        break

                    except Exception as e:
                        error_str = str(e)
                        is_rate_limit = "429" in error_str or "rate limit" in error_str.lower() or "too many requests" in error_str.lower()

                        if is_rate_limit and attempt < max_retries - 1:
                            logger.warning(f"🚫 Rate limit hit for {original_name}, retrying in {2**(attempt+1)}s...")
                            continue  # Retry with exponential backoff
                        else:
                            # Final attempt failed or non-rate-limit error
                            logger.error(f"❌ Tool {original_name} failed after {attempt+1} attempts: {error_str}")

                            if is_rate_limit:
                                error_message = ToolMessage(
                                    content=f"""🚫 RATE LIMIT EXCEEDED: {original_name}
📝 Called with parameters: {json.dumps(tool_call.get("args", {}), indent=2)}
⚠️  API rate limit persistently hit after {max_retries} attempts with exponential backoff
🔄 The external API (Firecrawl) is currently overwhelmed
💡 Suggestion: Try fewer web scraping requests or wait before making more calls

This tool call has been counted toward your limit to prevent further rate limiting.
---""",
                                    tool_call_id=tool_call["id"]
                                )
                            else:
                                error_message = ToolMessage(
                                    content=f"""❌ TOOL ERROR: {original_name}
📝 Called with parameters: {json.dumps(tool_call.get("args", {}), indent=2)}
🚨 Error: {error_str}
🔄 Attempted {attempt+1} times with backoff

Please try a different approach or tool.
---""",
                                    tool_call_id=tool_call["id"]
                                )

                            tool_messages.append(error_message)
                            tools_used += 1
                            break

                if not success:
                    logger.warning(f"⚠️ Tool {original_name} ultimately failed after all retries")

            logger.info(f"🏁 Sequential tool execution complete: {tools_used} tools processed")
            return {
                "messages": tool_messages,
                "tool_call_count": state["tool_call_count"] + tools_used
            }
    
    async def final_answer_node(self, state: AgentState) -> Dict[str, Any]:
        """Generate final answer when tool limit is reached or no tools needed"""
        messages = state["messages"]
        user_info = state["user_info"]
        tool_call_count = state["tool_call_count"]
        max_tools = state["max_tool_calls"]
        
        # Count and analyze tool results for context
        tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage)]
        tool_summary = ""
        
        if tool_messages:
            successful_tools = [msg for msg in tool_messages if not msg.content.startswith("❌")]
            failed_tools = [msg for msg in tool_messages if msg.content.startswith("❌")]
            
            tool_summary = f"""
🛠️ TOOL EXECUTION SUMMARY:
• Total tools used: {tool_call_count}/{max_tools}
• Successful: {len(successful_tools)}
• Failed: {len(failed_tools)}
• Tool results are available in the conversation above"""
            
            if failed_tools:
                tool_summary += f"\n• Note: {len(failed_tools)} tools encountered errors - work with available data"
        
        # Add debugging and validation logging
        logger.info(f"🎯 Final answer processing: {len(messages)} total messages, {len(tool_messages)} tool results")
        
        # Log tool results for debugging
        for i, tool_msg in enumerate(tool_messages):
            content_preview = tool_msg.content[:150].replace('\n', ' ')
            logger.info(f"Tool result {i+1}: {content_preview}...")
        
        # Enhanced final prompt that explicitly instructs LLM to use tool results
        final_prompt = f"""🎯 FINAL ANSWER GENERATION

You are now ready to provide the comprehensive final answer based on ALL information gathered.

{tool_summary}

📋 CRITICAL INSTRUCTIONS:
1. **CAREFULLY REVIEW** all tool results above in the conversation
2. **SYNTHESIZE AND USE** the specific data, names, addresses, phone numbers, and details from tool results
3. **DO NOT** provide generic responses - use the actual research data gathered
4. **STRUCTURE** your response with specific recommendations based on tool findings
5. **CITE SPECIFIC** businesses, services, locations, and contact information found by the tools
6. **ACKNOWLEDGE** if tool results were insufficient and explain what information is missing

👤 User Context: {user_info}
🔧 Research Completed: {tool_call_count}/{max_tools} tools executed

Now provide a detailed, actionable response that directly incorporates the tool research results:"""
        
        messages_with_prompt = messages + [HumanMessage(content=final_prompt)]
        
        # Log final message count for debugging
        logger.info(f"🔀 Sending {len(messages_with_prompt)} messages to LLM for final answer")
        
        response = await self.llm.ainvoke(messages_with_prompt)
        
        # Log response generation success
        logger.info(f"✅ Final answer generated: {len(response.content)} characters")
        
        return {"messages": [response]}

    async def create_solver_graph(self) -> StateGraph:
        """Create and return the MCP-powered task solver graph"""
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("agent", self.agent_node)
        workflow.add_node("tools", self.tool_node)
        workflow.add_node("final_answer", self.final_answer_node)
        
        # Add edges
        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "tools",
            self.should_continue,
            {
                "agent": "agent",
                "final_answer": "final_answer"
            }
        )
        workflow.add_edge("agent", "tools")
        workflow.add_edge("final_answer", END)
        
        return workflow.compile(checkpointer=self.memory)

    async def process_task(self, task_data: dict) -> dict:
        """Process a task using MCP LangGraph agent and return the solution"""
        task_id = task_data.get('task_id') or str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        # Extract messages and user info from task data
        messages_data = task_data.get('messages', [])
        user_id = task_data.get('user_id')
        user_info = task_data.get('user_info') or None
        
        # Get user info if user_id provided but user_info is empty
        if user_id and not user_info:
            user_info = self.get_user_info(user_id)
        
        # Convert message dicts to LangChain message objects
        messages = []
        for msg_data in messages_data:
            if msg_data.get('type') == 'human' or msg_data.get('role') == 'user':
                messages.append(HumanMessage(content=msg_data.get('content', '')))
            elif msg_data.get('type') == 'ai' or msg_data.get('role') == 'assistant':
                messages.append(AIMessage(content=msg_data.get('content', '')))
        
        # If no messages, create a default one
        if not messages:
            content = task_data.get('task_description', task_data.get('content', 'Please help me with this task.'))
            messages = [HumanMessage(content=content)]
        
        # Extract original user question from first human message for routing context
        user_question = ""
        for msg in messages:
            if isinstance(msg, HumanMessage):
                user_question = msg.content
                break
        
        # Fallback if no HumanMessage found
        if not user_question:
            user_question = task_data.get('task_description', task_data.get('content', 'Please help me with this task.'))
        
        try:
            # Initialize graph if not already done
            if not self.graph:
                self.graph = await self.create_solver_graph()
            
            # Create initial state for MCP agent
            initial_state = {
                "messages": messages,
                "user_info": user_info,
                "user_id": user_id,
                "user_question": user_question,  # Original user question for routing context
                "tool_call_count": 0,
                "max_tool_calls": self.max_tool_calls
            }
            random_id = str(uuid.uuid4())
            # Run the MCP LangGraph with session-specific config
            config = {"configurable": {"thread_id": random_id}}
            result = await self.graph.ainvoke(initial_state, config)
            
            # Extract final response
            final_message = result["messages"][-1]
            solution = final_message.content if hasattr(final_message, 'content') else str(final_message)
            
            # Create solution response
            solution_data = TaskSolution(
                task_id=task_id,
                messages=[{"type": "ai", "content": solution}],
                user_info=user_info,
                solution=solution,
                timestamp=timestamp
            ).dict()
            
            # Send result to Kafka (format for chat interface)
            chat_response = {
                "content": solution,
                "source": "mcp-task-solver", 
                "task_id": task_id,
                "type": "task_solution",
                "user_id": user_id,  # Include user_id for proper message filtering
                "metadata": {
                    "user_info": user_info,
                    "tool_calls_used": result.get("tool_call_count", 0)
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
                "messages": messages_data,
                "solution": f"Error: {str(e)}",
                "timestamp": timestamp
            }
            
            # Send error to chat interface
            error_chat_response = {
                "content": f"Error solving task: {str(e)}",
                "source": "mcp-task-solver",
                "task_id": task_id,
                "type": "error",
                "user_id": user_id  # Include user_id for proper message filtering
            }
            self.send_result_to_kafka(error_chat_response)
            
            processed_tasks[task_id] = error_result
            return error_result

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

# Global state
consumer_running = False
producer = None
consumer_thread = None
processed_tasks = {}

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
        "messages": task_request.messages,
        "user_id": task_request.user_id,
        "user_info": task_request.user_info or ""
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