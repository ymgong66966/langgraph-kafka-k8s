#!/usr/bin/env python3
"""
Minimal working example: Using Langfuse to log BedrockClient calls
"""

import asyncio
import json
import os
from datetime import datetime
from langfuse import Langfuse

# Import your existing BedrockClient from task_generator (which has working credentials)
import sys
sys.path.append('src')
from bedrock_client import BedrockClient

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

class TrackedBedrockClient(BedrockClient):
    """BedrockClient with Langfuse tracking"""

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
    """Demo the tracked BedrockClient"""
    print("🚀 Testing Langfuse + BedrockClient Integration")

    # Create conversation trace
    conversation, session_id = create_conversation_trace("demo-user")

    # Create tracked client
    client = TrackedBedrockClient(session_id=session_id)

    # Test 1: Simple chat
    print("\n📝 Test 1: Simple Chat")
    try:
        response1 = client.simple_chat(
            "Explain what AWS Bedrock is in one sentence.",
            max_tokens=100
        )
        print(f"Response: {response1}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 2: Async chat
    print("\n📝 Test 2: Async Chat")
    try:
        response2 = await client.async_chat(
            "What are the benefits of using Claude 4 Sonnet?",
            max_tokens=200,
            temperature=0.1
        )
        print(f"Response: {response2}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 3: Router-style call (JSON response)
    print("\n📝 Test 3: Router Decision")
    try:
        router_prompt = '''You are a router agent. Based on this chat history:
User: I need help finding daycare services in Chicago
Respond with JSON: {"agent": "task_generator"} or {"agent": "frontend_agent"}'''

        router_response = await client.async_chat(
            router_prompt,
            max_tokens=50,
            temperature=0.1
        )
        print(f"Router Decision: {router_response}")
    except Exception as e:
        print(f"Error: {e}")

    # End the conversation trace
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