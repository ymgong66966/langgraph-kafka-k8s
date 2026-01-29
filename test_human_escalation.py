#!/usr/bin/env python3
"""
Test script for Human Escalation feature
Tests the three routing scenarios:
- Scenario A: needs_human=true + non-matching question → ROUTER
- Scenario B: needs_human=false + matching question → HUMAN
- Scenario C: needs_human=false + non-matching question → ROUTER
"""

import requests
import json
from typing import Dict, Any

# Configuration
TASK_GENERATOR_URL = "http://localhost:8001"  # Use port-forward or update with actual URL
TEST_USER_ID = "test-user-human-escalation"

def test_task_generator(message: str, needs_human: bool) -> Dict[str, Any]:
    """Send a test message to task generator API"""
    url = f"{TASK_GENERATOR_URL}/generate-task"
    payload = {
        "conversation_history": [],
        "current_message": message,
        "user_id": TEST_USER_ID,
        "needs_human": needs_human
    }

    print(f"\n{'='*80}")
    print(f"Testing message: '{message}'")
    print(f"Initial needs_human: {needs_human}")
    print(f"{'='*80}")

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()

        print(f"✅ Status: {response.status_code}")
        print(f"📊 Result:")
        print(f"   - Agent used: {result.get('agent_used')}")
        print(f"   - Updated needs_human: {result.get('needs_human')}")
        print(f"   - Response: {result.get('response', 'N/A')[:200]}")

        return result

    except requests.exceptions.RequestException as e:
        print(f"❌ Error: {e}")
        return {}

def main():
    """Run test scenarios"""
    print("\n" + "="*80)
    print("HUMAN ESCALATION FEATURE TEST")
    print("="*80)

    # Test Scenario B: Should trigger human escalation
    print("\n\n🧪 SCENARIO B TEST: First-time human support question")
    print("Expected: needs_human changes from False → True, routes to HUMAN")

    scenario_b_messages = [
        "Can you help me hire an in-home caregiver?",
        "I need to schedule a consultation with a neurologist",
        "Help me coordinate moving mom into assisted living",
        "Can you call my insurance to ask why they denied my claim?",
        "I need help interviewing home care agencies"
    ]

    for msg in scenario_b_messages[:2]:  # Test first 2
        result = test_task_generator(msg, needs_human=False)
        if result.get('needs_human') == True:
            print("✅ PASS: needs_human correctly set to True")
        else:
            print("❌ FAIL: needs_human should be True")

    # Test Scenario C: Should NOT trigger human escalation
    print("\n\n🧪 SCENARIO C TEST: General caregiving questions")
    print("Expected: needs_human stays False, routes to ROUTER (normal flow)")

    scenario_c_messages = [
        "What is respite care?",
        "Tell me about memory care options",
        "I'm feeling stressed about caregiving",
        "How do I handle caregiver burnout?",
        "What are some tips for caring for someone with dementia?"
    ]

    for msg in scenario_c_messages[:2]:  # Test first 2
        result = test_task_generator(msg, needs_human=False)
        if result.get('needs_human') == False:
            print("✅ PASS: needs_human correctly stays False")
        else:
            print("❌ FAIL: needs_human should be False")

    # Test Scenario A: User waiting for social worker, asks unrelated question
    print("\n\n🧪 SCENARIO A TEST: User waiting for social worker, asks different question")
    print("Expected: needs_human stays True, routes to ROUTER (agentic system)")

    scenario_a_messages = [
        "What is respite care?",  # Unrelated to human support
        "Tell me about memory care options",
        "How do I handle caregiver burnout?"
    ]

    for msg in scenario_a_messages[:1]:  # Test first 1
        result = test_task_generator(msg, needs_human=True)
        if result.get('needs_human') == True:
            print("✅ PASS: needs_human correctly stays True")
        else:
            print("❌ FAIL: needs_human should stay True")

    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print("✅ If all tests passed, human escalation is working correctly!")
    print("❌ If any tests failed, check the logs:")
    print("   kubectl logs -n langgraph -l component=task-generator | grep -i scenario")
    print("="*80 + "\n")

if __name__ == "__main__":
    # Check if task generator is reachable
    try:
        health = requests.get(f"{TASK_GENERATOR_URL}/health", timeout=5)
        if health.status_code == 200:
            print(f"✅ Task Generator is reachable at {TASK_GENERATOR_URL}")
            main()
        else:
            print(f"❌ Task Generator health check failed: {health.status_code}")
            print("Make sure to port-forward: kubectl port-forward -n langgraph svc/langgraph-system-langgraph-kafka-task-generator 8001:8001")
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot reach Task Generator at {TASK_GENERATOR_URL}")
        print(f"Error: {e}")
        print("\nMake sure to run:")
        print("kubectl port-forward -n langgraph svc/langgraph-system-langgraph-kafka-task-generator 8001:8001")
