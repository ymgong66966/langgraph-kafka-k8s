#!/usr/bin/env python3

import sys
import os
sys.path.append('/Users/xyxg025/langgraph-kafka-k8s/src')

# Set required environment variables
os.environ.setdefault('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
os.environ.setdefault('KAFKA_TOPIC', 'test')
os.environ.setdefault('KAFKA_RESULTS_TOPIC', 'test-results')
os.environ.setdefault('OPENAI_API_KEY', 'test-key')

import asyncio
from task_generator_api import graph

async def test_direct_graph():
    """Test the graph directly"""
    try:
        print("Testing graph directly...")
        result = await graph.ainvoke({
            "conversation_history": "User: test",
            "generated_task": None
        })
        print(f"✅ Success: {result}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    asyncio.run(test_direct_graph())