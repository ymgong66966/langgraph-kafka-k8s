#!/bin/bash

# Human Escalation Test Script - External API Version
# Tests via ALB external endpoint (no port-forward needed)

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ALB URL
ALB_URL="http://k8s-langgrap-langgrap-42786a5662-1419563647.us-east-2.elb.amazonaws.com"

# Generate unique user IDs for this test run
TIMESTAMP=$(date +%s)
USER_ID_B="ext-test-b-${TIMESTAMP}"
USER_ID_C="ext-test-c-${TIMESTAMP}"
USER_ID_A="ext-test-a-${TIMESTAMP}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Human Escalation Test (External API)${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "ALB URL: ${ALB_URL}"
echo ""

# Check if ALB is accessible
echo -e "${YELLOW}Checking ALB health...${NC}"
HEALTH_RESPONSE=$(curl -s "${ALB_URL}/health" 2>/dev/null || echo "FAILED")
if echo "$HEALTH_RESPONSE" | jq -e '.status' > /dev/null 2>&1; then
    echo -e "${GREEN}✅ ALB is accessible${NC}"
else
    echo -e "${RED}❌ Cannot reach ALB at ${ALB_URL}${NC}"
    exit 1
fi
echo ""

# Test Scenario B: First-time human escalation
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Test B: First-time Human Escalation${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=false, question matches escalation list"
echo "Expected: needs_human → true"
echo ""

RESPONSE_B=$(curl -s -X POST "${ALB_URL}/external/send" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"${USER_ID_B}\",
    \"messages\": [{\"role\": \"user\", \"text\": \"Can you help me hire an in-home caregiver?\"}],
    \"needs_human\": false
  }")

echo "Response: $RESPONSE_B"
NEEDS_HUMAN_B=$(echo "$RESPONSE_B" | jq -r '.needs_human | tostring')
STATUS_B=$(echo "$RESPONSE_B" | jq -r '.status // "error"')

if [ "$STATUS_B" = "success" ] && [ "$NEEDS_HUMAN_B" = "true" ]; then
    echo -e "${GREEN}✅ PASS: needs_human changed to true${NC}"
else
    echo -e "${RED}❌ FAIL: status=${STATUS_B}, needs_human=${NEEDS_HUMAN_B}${NC}"
fi
echo ""

# Test Scenario C: Normal question
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Test C: Normal Question${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=false, non-matching question"
echo "Expected: needs_human stays false"
echo ""

RESPONSE_C=$(curl -s -X POST "${ALB_URL}/external/send" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"${USER_ID_C}\",
    \"messages\": [{\"role\": \"user\", \"text\": \"What is respite care?\"}],
    \"needs_human\": false
  }")

echo "Response: $RESPONSE_C"
NEEDS_HUMAN_C=$(echo "$RESPONSE_C" | jq -r '.needs_human | tostring')
STATUS_C=$(echo "$RESPONSE_C" | jq -r '.status // "error"')

if [ "$STATUS_C" = "success" ] && [ "$NEEDS_HUMAN_C" = "false" ]; then
    echo -e "${GREEN}✅ PASS: needs_human stayed false${NC}"
else
    echo -e "${RED}❌ FAIL: status=${STATUS_C}, needs_human=${NEEDS_HUMAN_C}${NC}"
fi
echo ""

# Test Scenario A: User already escalated
echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Test A: User Already Escalated${NC}"
echo -e "${YELLOW}========================================${NC}"
echo "Input: needs_human=true, non-matching question"
echo "Expected: needs_human stays true"
echo ""

RESPONSE_A=$(curl -s -X POST "${ALB_URL}/external/send" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"${USER_ID_A}\",
    \"messages\": [{\"role\": \"user\", \"text\": \"What is memory care?\"}],
    \"needs_human\": true
  }")

echo "Response: $RESPONSE_A"
NEEDS_HUMAN_A=$(echo "$RESPONSE_A" | jq -r '.needs_human | tostring')
STATUS_A=$(echo "$RESPONSE_A" | jq -r '.status // "error"')

if [ "$STATUS_A" = "success" ] && [ "$NEEDS_HUMAN_A" = "true" ]; then
    echo -e "${GREEN}✅ PASS: needs_human stayed true${NC}"
else
    echo -e "${RED}❌ FAIL: status=${STATUS_A}, needs_human=${NEEDS_HUMAN_A}${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Test B (First-time escalation): $([ "$NEEDS_HUMAN_B" = "true" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo -e "Test C (Normal question):       $([ "$NEEDS_HUMAN_C" = "false" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo -e "Test A (Already escalated):     $([ "$NEEDS_HUMAN_A" = "true" ] && echo -e "${GREEN}PASS${NC}" || echo -e "${RED}FAIL${NC}")"
echo ""
echo -e "${BLUE}Testing complete!${NC}"
