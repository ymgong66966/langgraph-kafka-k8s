"""
Uses the Amazon Bedrock runtime client InvokeModel operation to send a prompt to a model.
"""
import logging
import json
import boto3
import os


import boto3

# Uses your configured profile/role; or omit profile_name if not needed.
session = boto3.Session(profile_name="withcare-dev", region_name="us-east-2")
brt = session.client("bedrock-runtime")

INF_PROFILE_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

resp = brt.converse(
    modelId=INF_PROFILE_ID,
    messages=[{"role": "user", "content": [{"text": "Say hello in one sentence."}]}],
    inferenceConfig={"maxTokens": 128, "temperature": 0.2, "topP": 0.9},
)

print(resp["output"]["message"]["content"][0]["text"])