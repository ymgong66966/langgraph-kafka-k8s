#!/usr/bin/env python3
"""
Minimal working example: Using Langfuse to log BedrockClient calls
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from langfuse import Langfuse

# Import your existing BedrockClient from task_generator (which has working credentials)
import sys
sys.path.append('src')
# from bedrock_client import BedrockClient
import boto3
import os
import logging
import json
import random
from botocore.exceptions import ClientError, NoCredentialsError
import time
logger = logging.getLogger(__name__)

class BedrockClient:
    def __init__(self):
        self.region = 'us-east-2'  # Fixed to us-east-2
        self.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"  # Use the working model
        self.client = None
        self._initialize_client()

    
    def _initialize_client(self):
        """Initialize Bedrock client using withcare-dev profile"""
        try:
            # Check if we're in Kubernetes (has service account token file)
            k8s_token_file = os.getenv('AWS_WEB_IDENTITY_TOKEN_FILE')
            
            if k8s_token_file and os.path.exists(k8s_token_file):
                # Kubernetes environment - attempt IRSA role assumption
                logger.info("Kubernetes environment detected, attempting IRSA role assumption")
                try:
                    sts_client = boto3.client('sts', region_name=self.region)

                    # Assume the role (OrganizationAccountAccessRole in account 216989110335)
                    assumed_role = sts_client.assume_role(
                        RoleArn='arn:aws:iam::216989110335:role/OrganizationAccountAccessRole',
                        RoleSessionName='bedrock-k8s-session'
                    )

                    # Extract credentials from the assumed role
                    credentials = assumed_role['Credentials']

                    # Create Bedrock client with assumed role credentials
                    self.client = boto3.client(
                        'bedrock-runtime',
                        region_name=self.region,
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken']
                    )

                    logger.info("Successfully initialized Bedrock client with assumed role")
                except Exception as irsa_error:
                    logger.warning(f"IRSA role assumption failed: {irsa_error}")
                    logger.info("Falling back to default AWS credentials")
                    self.client = boto3.client('bedrock-runtime', region_name=self.region)
                    logger.info("Successfully initialized Bedrock client with default credentials")
            else:
                # Local environment - use default AWS credentials
                logger.info("Local environment detected, using default AWS credentials")
                session = boto3.Session(profile_name="withcare_dev", region_name="us-east-2")
                self.client = session.client("bedrock-runtime")
                logger.info("Successfully initialized Bedrock client with default credentials")
            
        except NoCredentialsError:
            logger.error("No AWS credentials found. Ensure AWS profile is configured or IRSA is set up.")
            raise
        except ClientError as e:
            logger.error(f"Failed to initialize Bedrock client: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing Bedrock client: {e}")
            raise
    
    def _retry_with_backoff(self, func, max_retries=5, base_delay=1.0, max_delay=60.0, backoff_factor=2.0):
        """Retry function with exponential backoff for throttling"""
        for attempt in range(max_retries):
            try:
                return func()
            except ClientError as e:
                error_code = e.response['Error']['Code']
                
                if error_code == 'ThrottlingException' and attempt < max_retries - 1:
                    # Calculate delay with jitter
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    jitter = random.uniform(0.1, 0.3) * delay
                    total_delay = delay + jitter
                    
                    logger.warning(f"Throttling detected, retrying in {total_delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(total_delay)
                    continue
                else:
                    # Re-raise for non-throttling errors or final attempt
                    raise
            except Exception as e:
                # Re-raise non-ClientError exceptions immediately
                raise
        
        # This should never be reached, but just in case
        raise Exception(f"Max retries ({max_retries}) exceeded")
    def converse(self, messages, max_tokens=1000, temperature=0.2, top_p=0.9):
        """Send messages to Bedrock model and get response using invoke_model API with retry logic"""
        if not self.client:
            raise RuntimeError("Bedrock client not initialized")

        def _make_request():
            # Format messages for Bedrock invoke_model API (following AWS docs)
            bedrock_messages = []
            for msg in messages:
                bedrock_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]  # Simple string format, not array
                })

            # Create request body following AWS documentation format
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": bedrock_messages,
                "temperature": temperature,
                "top_p": top_p
            })

            # Use invoke_model instead of converse
            response = self.client.invoke_model(
                body=body,
                modelId=self.model_id
            )

            # Parse response body
            response_body = json.loads(response.get('body').read())

            # Extract text from response (Claude format)
            if 'content' in response_body and len(response_body['content']) > 0:
                return response_body['content'][0]['text']
            else:
                logger.error(f"Unexpected response format: {response_body}")
                return "Error: Unexpected response format"

        try:
            return self._retry_with_backoff(_make_request)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AccessDeniedException':
                logger.error(f"Access denied to Bedrock model {self.model_id}: {e}")
            else:
                logger.error(f"Bedrock API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in converse: {e}")
            raise

    
    def simple_chat(self, prompt, max_tokens=1000):
        """
        Simple chat interface for single prompts

        Args:
            prompt: User prompt string
            max_tokens: Maximum tokens to generate

        Returns:
            Response text from the model
        """
        messages = [{"role": "user", "content": prompt}]
        return self.converse(messages, max_tokens=max_tokens)

    async def async_chat(self, prompt, max_tokens=1000, temperature=0.2):
        """
        Async chat interface for single prompts (compatible with LangChain patterns)

        Args:
            prompt: User prompt string
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Response text from the model
        """
        messages = [{"role": "user", "content": prompt}]
        # Run synchronous converse in async context
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.converse(messages, max_tokens=max_tokens, temperature=temperature))

    async def async_converse(self, messages, max_tokens=1000, temperature=0.2, top_p=0.9):
        """
        Async converse interface for multiple messages (compatible with LangChain patterns)

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter

        Returns:
            Response text from the model
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.converse(messages, max_tokens=max_tokens, temperature=temperature, top_p=top_p))
# Configure Langfuse
# You can get these from https://cloud.langfuse.com or your self-hosted instance
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-a37d7746-e39e-4c5e-9f90-c911b5dbad91")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-7ee79e38-c88a-461f-a16b-9b1023a79285")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

# Initialize Langfuse client with specific project
langfuse = Langfuse(
    public_key=LANGFUSE_PUBLIC_KEY,
    secret_key=LANGFUSE_SECRET_KEY,
    host=LANGFUSE_HOST
)

# # Set the project for all traces
# def create_langfuse_with_project(project_name="test_graph"):
#     """Create Langfuse client configured for specific project"""
#     return Langfuse(
#         public_key=LANGFUSE_PUBLIC_KEY,
#         secret_key=LANGFUSE_SECRET_KEY,
#         host=LANGFUSE_HOST
#     )

# Fake MCP Tools for demonstration
FAKE_MCP_TOOLS = [
    {
        "name": "get_weather",
        "description": "Get current weather information for a specific location",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name or location to get weather for"
                },
                "unit": {
                    "type": "string",
                    "description": "Temperature unit (celsius or fahrenheit)",
                    "enum": ["celsius", "fahrenheit"]
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "search_restaurants",
        "description": "Search for restaurants in a specific area with filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location to search for restaurants"
                },
                "cuisine": {
                    "type": "string",
                    "description": "Type of cuisine (optional)"
                },
                "price_range": {
                    "type": "string",
                    "description": "Price range filter",
                    "enum": ["$", "$$", "$$$", "$$$$"]
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "calculate_tip",
        "description": "Calculate tip amount based on bill total and percentage",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_amount": {
                    "type": "number",
                    "description": "Total bill amount before tip"
                },
                "tip_percentage": {
                    "type": "number",
                    "description": "Tip percentage (e.g., 15, 18, 20)"
                }
            },
            "required": ["bill_amount", "tip_percentage"]
        }
    }
]

def execute_fake_tool(tool_name: str, inputs: dict) -> str:
    """Execute a fake tool and return a simulated result"""
    if tool_name == "get_weather":
        location = inputs.get("location", "Unknown")
        unit = inputs.get("unit", "celsius")
        temp = "22°C" if unit == "celsius" else "72°F"
        return f"Current weather in {location}: Sunny, {temp}, light breeze"

    elif tool_name == "search_restaurants":
        location = inputs.get("location", "Unknown")
        cuisine = inputs.get("cuisine", "various")
        price_range = inputs.get("price_range", "$$")
        return f"Found 5 {cuisine} restaurants in {location} with {price_range} price range: 1) Mario's Italian Bistro, 2) Sakura Sushi, 3) The Local Grill, 4) Spice Garden, 5) Café Luna"

    elif tool_name == "calculate_tip":
        bill = inputs.get("bill_amount", 0)
        percentage = inputs.get("tip_percentage", 15)
        tip = bill * (percentage / 100)
        total = bill + tip
        return f"Bill: ${bill:.2f}, Tip ({percentage}%): ${tip:.2f}, Total: ${total:.2f}"

    else:
        return f"Tool {tool_name} executed with inputs: {inputs}"

class TrackedBedrockClient(BedrockClient):
    """BedrockClient with Langfuse tracking, tool use, and extended thinking support"""

    def __init__(self, session_id: str = None):
        super().__init__()
        self.session_id = session_id or f"bedrock-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    def simple_chat(self, prompt, max_tokens=1000, **kwargs):
        """Simple chat with Langfuse logging"""
        # Create a generation trace
        generation = langfuse.generation(
            name="bedrock-claude-chat",
            model=self.model_id,
            input=prompt,
            session_id=self.session_id,
            project="test_graph",
            metadata={
                "max_tokens": max_tokens,
                "region": self.region,
                **kwargs
            }
        )

        try:
            # Call the original method
            start_time = datetime.now()
            response = super().simple_chat(prompt, max_tokens, **kwargs)
            end_time = datetime.now()

            # Calculate approximate token counts (rough estimation)
            input_tokens = len(prompt.split()) * 1.3  # Rough approximation
            output_tokens = len(response.split()) * 1.3

            # Update the generation with response
            generation.end(
                output=response,
                usage={
                    "input": int(input_tokens),
                    "output": int(output_tokens),
                    "total": int(input_tokens + output_tokens)
                },
                level="DEFAULT",
                metadata={
                    "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                    "model": self.model_id
                }
            )

            return response

        except Exception as e:
            # Log the error
            generation.end(
                level="ERROR",
                metadata={"error": str(e)}
            )
            raise

    async def async_chat(self, prompt, max_tokens=1000, temperature=0.2, **kwargs):
        """Async chat with Langfuse logging"""
        # Create a generation trace
        generation = langfuse.generation(
            name="bedrock-claude-async-chat",
            model=self.model_id,
            input=prompt,
            session_id=self.session_id,
            project="test_graph",
            metadata={
                "max_tokens": max_tokens,
                "temperature": temperature,
                "region": self.region,
                **kwargs
            }
        )

        try:
            # Call the original method
            start_time = datetime.now()
            response = await super().async_chat(prompt, max_tokens, temperature)
            end_time = datetime.now()

            # Calculate approximate token counts
            input_tokens = len(prompt.split()) * 1.3
            output_tokens = len(response.split()) * 1.3

            # Update the generation with response
            generation.end(
                output=response,
                usage={
                    "input": int(input_tokens),
                    "output": int(output_tokens),
                    "total": int(input_tokens + output_tokens)
                },
                level="DEFAULT",
                metadata={
                    "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                    "temperature": temperature,
                    "model": self.model_id
                }
            )

            return response

        except Exception as e:
            # Log the error
            generation.end(
                level="ERROR",
                metadata={"error": str(e)}
            )
            raise

    def converse_with_tools_and_thinking(self, messages, tools=None, max_tokens=4000, thinking_budget=2000, **kwargs):
        """
        Enhanced converse method with tool use and extended thinking support

        Args:
            messages: List of conversation messages
            tools: List of tool definitions (MCP format)
            max_tokens: Maximum tokens for the response
            thinking_budget: Maximum tokens for thinking (must be < max_tokens)
            **kwargs: Additional parameters
        """
        

        # Create Langfuse generation trace before making the API call
        generation = langfuse.generation(
            name="bedrock-claude-tools-thinking",
            model=self.model_id,
            input=messages,
            session_id=self.session_id,
            metadata={
                "max_tokens": max_tokens,
                "thinking_budget": thinking_budget,
                "tools_enabled": bool(tools),
                "num_tools": len(tools) if tools else 0,
                "region": self.region,
                **kwargs
            }
        )

        try:
            start_time = datetime.now()

            # Prepare the request body for Bedrock with extended thinking
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": messages
            }

            # Add extended thinking configuration
            if thinking_budget and thinking_budget < max_tokens:
                request_body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": thinking_budget
                }

            # Add tools if provided
            if tools:
                request_body["tools"] = tools
                # Don't force tool use when thinking is enabled - let Claude decide
                # request_body["tool_choice"] = {"type": "any"}  # Conflicts with thinking mode

            # Make the Bedrock API call
            response = self._retry_with_backoff(
                lambda: self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(request_body)
                )
            )
            
            end_time = datetime.now()

            # Parse response
            response_body = json.loads(response['body'].read().decode('utf-8'))
            
            # Extract thinking and content blocks
            thinking_content = []
            text_content = []
            tool_use_content = []

            # Debug: Check response structure
            if 'content' not in response_body:
                print(f"Warning: No 'content' key in response. Keys: {list(response_body.keys())}")
                print(f"Full response: {response_body}")

            for content_block in response_body.get('content', []):
                if content_block.get('type') == 'thinking':
                    thinking_content.append(content_block.get('content', ''))
                elif content_block.get('type') == 'text':
                    text_content.append(content_block.get('text', ''))
                elif content_block.get('type') == 'tool_use':
                    tool_use_content.append(content_block)
            print("thinking_content", response_body.get('content', []))
            
            # Calculate token usage
            usage = response_body.get('usage', {})

            # Log successful response
            generation.end(
                output={
                    "thinking": thinking_content,
                    "text": text_content,
                    "tool_use": tool_use_content,
                    "full_response": response_body
                },
                usage={
                    "input": usage.get('input_tokens', 0),
                    "output": usage.get('output_tokens', 0),
                    "total": usage.get('input_tokens', 0) + usage.get('output_tokens', 0)
                },
                metadata={
                    "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                    "thinking_tokens": len(' '.join(thinking_content).split()) if thinking_content else 0,
                    "text_tokens": len(' '.join(text_content).split()) if text_content else 0,
                    "has_tool_use": bool(tool_use_content),
                    "stop_reason": response_body.get('stop_reason')
                }
            )

            return response_body

        except Exception as e:
            # Log the error
            generation.end(
                level="ERROR",
                metadata={"error": str(e)}
            )
            raise

    def converse_with_tools_only(self, messages, tools, max_tokens=4000, force_tool_use=False, **kwargs):
        """
        Tool use without extended thinking for scenarios requiring forced tool use

        Args:
            messages: List of conversation messages
            tools: List of tool definitions (MCP format)
            max_tokens: Maximum tokens for the response
            force_tool_use: Whether to force tool usage
            **kwargs: Additional parameters
        """
        generation = langfuse.generation(
            name="bedrock-claude-tools-only",
            model=self.model_id,
            input=messages,
            session_id=self.session_id,
            metadata={
                "max_tokens": max_tokens,
                "tools_enabled": True,
                "num_tools": len(tools),
                "force_tool_use": force_tool_use,
                "region": self.region,
                **kwargs
            }
        )

        try:
            start_time = datetime.now()

            # Prepare the request body for Bedrock (no thinking)
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": tools
            }

            # Add tool choice if forcing tool use
            if force_tool_use:
                request_body["tool_choice"] = {"type": "any"}

            # Make the Bedrock API call
            response = self._retry_with_backoff(
                lambda: self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(request_body)
                )
            )

            end_time = datetime.now()

            # Parse response
            response_body = json.loads(response['body'].read().decode('utf-8'))

            # Extract content blocks (no thinking in this mode)
            text_content = []
            tool_use_content = []

            # Debug: Check response structure
            print(f"Response keys: {list(response_body.keys())}")
            if 'content' not in response_body:
                print(f"Warning: No 'content' key in response")
                print(f"Full response: {response_body}")

            for content_block in response_body.get('content', []):
                if content_block.get('type') == 'text':
                    text_content.append(content_block.get('text', ''))
                elif content_block.get('type') == 'tool_use':
                    tool_use_content.append(content_block)

            # Calculate token usage
            usage = response_body.get('usage', {})

            # Log successful response
            generation.end(
                output={
                    "text": text_content,
                    "tool_use": tool_use_content,
                    "full_response": response_body
                },
                usage={
                    "input": usage.get('input_tokens', 0),
                    "output": usage.get('output_tokens', 0),
                    "total": usage.get('input_tokens', 0) + usage.get('output_tokens', 0)
                },
                metadata={
                    "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                    "has_tool_use": bool(tool_use_content),
                    "stop_reason": response_body.get('stop_reason')
                }
            )

            return response_body

        except Exception as e:
            # Log the error
            generation.end(
                level="ERROR",
                metadata={"error": str(e)}
            )
            raise

    async def multi_turn_conversation_with_tools(self, initial_prompt, tools=None, max_turns=5, use_thinking=True):
        """
        Demonstrate multi-turn conversation with tool use and extended thinking

        Args:
            initial_prompt: Starting user message
            tools: List of available tools
            max_turns: Maximum number of conversation turns
            use_thinking: Whether to enable extended thinking (conflicts with forced tool use)
        """
        if not tools:
            tools = FAKE_MCP_TOOLS

        messages = [{"role": "user", "content": initial_prompt}]
        turn = 1

        mode = "thinking + tools" if use_thinking else "tools only"
        print(f"\n🤖 Starting multi-turn conversation with {len(tools)} tools available ({mode})")
        print(f"📝 Initial prompt: {initial_prompt}")

        while turn <= max_turns:
            print(f"\n--- Turn {turn} ---")

            # Generate response - choose method based on thinking preference
            if use_thinking:
                print("Thinking + tools")
                response = self.converse_with_tools_and_thinking(
                    messages=messages,
                    tools=tools,
                    max_tokens=4000,
                    thinking_budget=2000  # Already >= 1024
                )
            else:
                response = self.converse_with_tools_only(
                    messages=messages,
                    tools=tools,
                    max_tokens=4000,
                    force_tool_use=False  # Let Claude decide
                )
            print(response)
            # Display thinking process if available
            thinking_blocks = [block for block in response.get('content', []) if block['type'] == 'thinking']
            if thinking_blocks:
                print("🧠 Claude's thinking:")
                for block in thinking_blocks:
                    print(f"   {block['thinking'][:200]}...")

            # Process text responses
            text_blocks = [block for block in response.get('content', []) if block['type'] == 'text']
            if text_blocks:
                assistant_text = ' '.join([block['text'] for block in text_blocks])
                print(f"💬 Claude: {assistant_text}")

            # Process tool use
            tool_use_blocks = [block for block in response.get('content', []) if block['type'] == 'tool_use']
            if tool_use_blocks:
                print("🔧 Tool usage:")

                # Add assistant message with tool use to conversation
                # AWS Bedrock requires thinking blocks to come FIRST when thinking is enabled
                assistant_content = []
                for thinking_block in thinking_blocks:
                    assistant_content.append({
                        "type": "thinking", 
                        "thinking": thinking_block['thinking'],
                        "signature": thinking_block['signature']
                    })
                for text_block in text_blocks:
                    assistant_content.append({"type": "text", "text": text_block['text']})
                for tool_block in tool_use_blocks:
                    assistant_content.append(tool_block)

                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools and add results
                tool_results = []
                for tool_block in tool_use_blocks:
                    tool_name = tool_block['name']
                    tool_input = tool_block['input']
                    tool_id = tool_block['id']

                    print(f"   Calling {tool_name} with {tool_input}")

                    # Execute fake tool
                    result = execute_fake_tool(tool_name, tool_input)
                    print(f"   Result: {result}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result
                    })

                # Add tool results to messages
                messages.append({"role": "user", "content": tool_results})

            else:
                # No tool use, just add assistant response and break
                # AWS Bedrock requires thinking blocks to come FIRST when thinking is enabled
                assistant_content = []
                for thinking_block in thinking_blocks:
                    assistant_content.append({
                        "type": "thinking", 
                        "thinking": thinking_block['thinking'],
                        "signature": thinking_block['signature']
                    })
                for text_block in text_blocks:
                    assistant_content.append({"type": "text", "text": text_block['text']})

                messages.append({"role": "assistant", "content": assistant_content})
                break

            turn += 1

            # Small delay between turns
            await asyncio.sleep(1)

        print(f"\n✅ Conversation completed after {turn-1} turns")
        return messages

def create_conversation_trace(user_id: str = "test-user"):
    """Create a conversation-level trace for grouping related calls"""
    session_id = f"conversation-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace = langfuse.trace(
        name="task-generator-conversation",
        user_id=user_id,
        session_id=session_id,
        project="test_graph",
        metadata={
            "system": "langgraph-kafka",
            "component": "task-generator"
        }
    )
    return trace, session_id

async def main():
    """Demo the enhanced TrackedBedrockClient with tools and thinking"""
    print("🚀 Testing Enhanced Langfuse + BedrockClient Integration")
    print("📋 Features: Tool Use + Extended Thinking + Multi-turn Conversations")

    # Create conversation trace
    conversation, session_id = create_conversation_trace("demo-user")

    # Create tracked client
    client = TrackedBedrockClient(session_id=session_id)

    # # Test 1: Simple chat (backward compatibility)
    # print("\n📝 Test 1: Simple Chat (Original)")
    # try:
    #     response1 = client.simple_chat(
    #         "Explain what AWS Bedrock is in one sentence.",
    #         max_tokens=100
    #     )
    #     print(f"Response: {response1}")
    # except Exception as e:
    #     print(f"Error: {e}")

    # Test 2: Extended thinking without tools
    # print("\n🧠 Test 2: Extended Thinking Mode")
    # try:
    #     messages = [{"role": "user", "content": "Solve this step by step: If I have 3 apples and give away 1/3 of them, how many do I have left?"}]

    #     response2 = client.converse_with_tools_and_thinking(
    #         messages=messages,
    #         tools=None,
    #         max_tokens=2000,
    #         thinking_budget=1024  # Minimum required by Bedrock
    #     )
    #     # print(type(response2))
    #     # Display thinking process
    #     for content in response2.get('content', []):
    #         if content['type'] == 'thinking':
    #             print(f"🧠 Thinking: {content['thinking'][:200]}...")
    #         elif content['type'] == 'text':
    #             print(f"💬 Response: {content['text']}")

    # except Exception as e:
    #     print(f"Error: {e}")

    # # Test 3: Single tool use with thinking
    # print("\n🔧 Test 3: Single Tool Use with Thinking")
    # try:
    #     messages = [{"role": "user", "content": "What's the weather like in San Francisco right now?"}]

    #     response3 = client.converse_with_tools_and_thinking(
    #         messages=messages,
    #         tools=FAKE_MCP_TOOLS,
    #         max_tokens=3000,
    #         thinking_budget=1500  # Must be >= 1024
    #     )

    #     # Display results
    #     for content in response3.get('content', []):
    #         if content['type'] == 'thinking':
    #             print(f"🧠 Thinking: {content['thinking'][:200]}...")
    #         elif content['type'] == 'text':
    #             print(f"💬 Text: {content['text']}")
    #         elif content['type'] == 'tool_use':
    #             print(f"🔧 Tool Use: {content['name']} with {content['input']}")

    # except Exception as e:
    #     print(f"Error: {e}")

    # Test 4: Multi-turn conversation with complex task
#     print("\n🔄 Test 4: Multi-turn Conversation with Tools")
#     try:
#         complex_prompt = """I'm planning a dinner tonight in Chicago. I need to:
# 1. Check the weather to see if it's good for outdoor dining
# 2. Find some Italian restaurants in the downtown area
# 3. Calculate a 20% tip for a $85 bill

# Can you help me with all of these? i need you use extended thinking to think step by step to solve this!"""

#         await client.multi_turn_conversation_with_tools(
#             initial_prompt=complex_prompt,
#             tools=FAKE_MCP_TOOLS,
#             max_turns=5,
#             use_thinking=True  # Disable thinking for reliable tool use
#         )

#     except Exception as e:
#         print(f"Multi-turn error: {e}")

    # Test 5: Advanced scenario - Restaurant planning
    print("\n🍽️ Test 5: Restaurant Planning Scenario")
    try:
        restaurant_prompt = """I'm visiting New York City tomorrow and want to plan a perfect dinner.
I need to check the weather first, then find upscale restaurants, and finally calculate tips for different scenarios."""

        await client.multi_turn_conversation_with_tools(
            initial_prompt=restaurant_prompt,
            tools=FAKE_MCP_TOOLS,
            max_turns=4,
            use_thinking=True  # Disable thinking for reliable tool use
        )

    except Exception as e:
        print(f"Restaurant planning error: {e}")

    # # End the conversation trace
    # conversation.update(
    #     output="Langfuse + BedrockClient integration test completed"
    # )

    # # Force flush all pending traces to Langfuse
    # print("\n🔄 Flushing traces to Langfuse...")
    # try:
    #     langfuse.flush()
    #     print("✅ Flush completed")
    # except Exception as e:
    #     print(f"❌ Flush failed: {e}")
    
    # # Wait a moment for async operations to complete
    # await asyncio.sleep(3)
    
    # # Final flush attempt
    # try:
    #     langfuse.flush()
    #     print("🔄 Final flush completed")
    # except Exception as e:
    #     print(f"❌ Final flush failed: {e}")
    

if __name__ == "__main__":
    asyncio.run(main())