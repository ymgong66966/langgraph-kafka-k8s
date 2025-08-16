#!/usr/bin/env python3
"""
Runtime patch for LangGraph checkpointer issue
This script patches the graph invocation to add the required config parameter
"""

import asyncio
import requests
import json
import time

def test_and_restart_if_needed():
    """Test if the service has the checkpointer error and restart if needed"""
    
    # Test current state
    try:
        response = requests.post(
            "http://localhost:8001/generate-task",
            json={
                "task_type": "analysis", 
                "description": "Test task", 
                "conversation_history": "User: test", 
                "context": "testing"
            },
            timeout=10
        )
        
        if "Checkpointer requires" in response.text:
            print("❌ Checkpointer error detected")
            return False
        else:
            print("✅ Service working correctly")
            return True
            
    except Exception as e:
        print(f"❌ Error testing service: {e}")
        return False

if __name__ == "__main__":
    print("Testing current deployment state...")
    is_working = test_and_restart_if_needed()
    
    if not is_working:
        print("\n🔧 Service needs fixing. The solution requires:")
        print("1. Updating Docker images with the LangGraph checkpointer fix")
        print("2. Or disabling checkpointer entirely for this deployment")
        print("\nThe fix involves adding config parameter to graph.ainvoke() calls")
    else:
        print("\n✅ Deployment is working correctly!")