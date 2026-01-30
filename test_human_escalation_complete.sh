#!/bin/bash

# Complete Human Escalation Test Script
# Tests all three routing scenarios

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

API_URL="http://localhost:8001"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Human Escalation Feature Test${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if service is accessible
echo -e "${YELLOW}Checking service health...${NC}"
if ! curl -s "${API_URL}/health" > /dev/null; then
    echo -e "${RED}❌ Cannot reach service at ${API_URL}${NC}"
    echo "Please run: kubectl port-forward -n langgraph svc/langgraph-kafka-task-generator 8001:8001"
    exit 1
fi
echo -e "${GREEN}✅ Service is accessible${NC}"
echo ""

# Test Scenario B: First-time human escalation
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Testing Scenario B: First-time Human Escalation${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=false, question matches list"
echo "Expected: needs_human → true, route to HUMAN"
echo ""

RESPONSE_B=$(curl -s -X POST "${API_URL}/generate-task" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": "",
    "current_message": "Can you help me hire an in-home caregiver?",
    "user_id": "test-scenario-b-'$(date +%s)'",
    "needs_human": false
  }')

NEEDS_HUMAN_B=$(echo "$RESPONSE_B" | jq -r '.needs_human')
RESPONSE_TEXT_B=$(echo "$RESPONSE_B" | jq -r '.response')

if [ "$NEEDS_HUMAN_B" = "true" ]; then
    echo -e "${GREEN}✅ PASS: needs_human changed to true${NC}"
    echo "Response preview: ${RESPONSE_TEXT_B:0:100}..."
else
    echo -e "${RED}❌ FAIL: needs_human is ${NEEDS_HUMAN_B}, expected true${NC}"
    echo "Full response: $RESPONSE_B"
fi
echo ""

# Test Scenario C: Normal question
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Testing Scenario C: Normal Question${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=false, non-matching question"
echo "Expected: needs_human stays false, route to agentic system"
echo ""

RESPONSE_C=$(curl -s -X POST "${API_URL}/generate-task" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": "",
    "current_message": "What is respite care?",
    "user_id": "test-scenario-c-'$(date +%s)'",
    "needs_human": false
  }')

NEEDS_HUMAN_C=$(echo "$RESPONSE_C" | jq -r '.needs_human')
AGENT_C=$(echo "$RESPONSE_C" | jq -r '.agent_used')

if [ "$NEEDS_HUMAN_C" = "false" ] && [ "$AGENT_C" = "frontend_agent" ]; then
    echo -e "${GREEN}✅ PASS: needs_human stayed false, routed to ${AGENT_C}${NC}"
    RESPONSE_TEXT_C=$(echo "$RESPONSE_C" | jq -r '.response')
    echo "Response preview: ${RESPONSE_TEXT_C:0:100}..."
else
    echo -e "${RED}❌ FAIL: needs_human=${NEEDS_HUMAN_C}, agent=${AGENT_C}${NC}"
    echo "Full response: $RESPONSE_C"
fi
echo ""

# Test Scenario A: User waiting for social worker
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Testing Scenario A: User Waiting for Social Worker${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=true, non-matching question"
echo "Expected: needs_human stays true, route to agentic system"
echo ""

RESPONSE_A=$(curl -s -X POST "${API_URL}/generate-task" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": "",
    "current_message": "What is memory care?",
    "user_id": "test-scenario-a-'$(date +%s)'",
    "needs_human": true
  }')

NEEDS_HUMAN_A=$(echo "$RESPONSE_A" | jq -r '.needs_human')
AGENT_A=$(echo "$RESPONSE_A" | jq -r '.agent_used')

if [ "$NEEDS_HUMAN_A" = "true" ] && [ "$AGENT_A" = "frontend_agent" ]; then
    echo -e "${GREEN}✅ PASS: needs_human stayed true, routed to ${AGENT_A}${NC}"
    RESPONSE_TEXT_A=$(echo "$RESPONSE_A" | jq -r '.response')
    echo "Response preview: ${RESPONSE_TEXT_A:0:100}..."
else
    echo -e "${RED}❌ FAIL: needs_human=${NEEDS_HUMAN_A}, agent=${AGENT_A}${NC}"
    echo "Full response: $RESPONSE_A"
fi
echo ""

# Summary
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Test Summary${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Scenario B (First-time escalation): $([ "$NEEDS_HUMAN_B" = "true" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo "Scenario C (Normal question): $([ "$NEEDS_HUMAN_C" = "false" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo "Scenario A (User waiting): $([ "$NEEDS_HUMAN_A" = "true" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo ""
echo -e "${GREEN}Testing complete!${NC}"
