"""
Uses the Amazon Bedrock runtime client with role assumption to test the same flow as Kubernetes pods.
This simulates: withcare-ml profile -> assume withcare-mgmt role -> access Bedrock
"""
import logging
import json
import boto3
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_bedrock_with_role_assumption():
    """Test Bedrock access using role assumption chain like in Kubernetes"""
    
    # Step 1: Use withcare-ml profile (this simulates the Kubernetes service account)
    session = boto3.Session(profile_name="withcare-ml", region_name="us-east-2")
    sts_client = session.client('sts')
    
    try:
        # Step 2: Assume the withcare-mgmt role
        logger.info("Assuming withcare-mgmt role...")
        assumed_role = sts_client.assume_role(
            RoleArn='arn:aws:iam::909817712952:role/withcare-mgmt',
            RoleSessionName='bedrock-local-test'
        )
        
        # Step 3: Extract credentials from assumed role
        credentials = assumed_role['Credentials']
        logger.info("Successfully assumed withcare-mgmt role")
        
        # Step 4: Create Bedrock client with assumed role credentials
        bedrock_client = boto3.client(
            'bedrock-runtime',
            region_name='us-east-2',
            aws_access_key_id=credentials['AccessKeyId'],
            aws_secret_access_key=credentials['SecretAccessKey'],
            aws_session_token=credentials['SessionToken']
        )
        
        # Step 5: Test Bedrock API call
        model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        
        logger.info(f"Testing Bedrock model: {model_id}")
        response = bedrock_client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Say hello in one sentence."}]}],
            inferenceConfig={"maxTokens": 128, "temperature": 0.2, "topP": 0.9}
        )
        
        result = response["output"]["message"]["content"][0]["text"]
        logger.info("Bedrock test successful!")
        print(f"Bedrock response: {result}")
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return False

def test_direct_withcare_mgmt():
    """Test direct access with withcare-mgmt profile (for comparison)"""
    try:
        logger.info("Testing direct withcare-mgmt access...")
        session = boto3.Session(profile_name="withcare-mgmt", region_name="us-east-2")
        bedrock_client = session.client("bedrock-runtime")
        
        model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        response = bedrock_client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Say hello in one sentence."}]}],
            inferenceConfig={"maxTokens": 128, "temperature": 0.2, "topP": 0.9}
        )
        
        result = response["output"]["message"]["content"][0]["text"]
        logger.info("Direct withcare-mgmt test successful!")
        print(f"Direct response: {result}")
        return True
        
    except Exception as e:
        logger.error(f"Direct test failed: {e}")
        return False

def test_direct_withcare_dev():
    """Test direct access with withcare-dev profile"""
    try:
        logger.info("Testing direct withcare-dev access...")
        session = boto3.Session(profile_name="withcare-dev", region_name="us-east-2")
        bedrock_client = session.client("bedrock-runtime")
        
        model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        response = bedrock_client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Say hello in one sentence."}]}],
            inferenceConfig={"maxTokens": 128, "temperature": 0.2, "topP": 0.9}
        )
        
        result = response["output"]["message"]["content"][0]["text"]
        logger.info("Direct withcare-dev test successful!")
        print(f"withcare-dev response: {result}")
        return True
        
    except Exception as e:
        logger.error(f"withcare-dev test failed: {e}")
        return False

if __name__ == "__main__":
    print("=== Testing Bedrock Access Patterns ===\n")
    
    print("1. Testing role assumption (withcare-ml -> withcare-mgmt):")
    role_assumption_works = test_bedrock_with_role_assumption()
    
    print("\n2. Testing direct withcare-mgmt access:")
    mgmt_works = test_direct_withcare_mgmt()
    
    print("\n3. Testing direct withcare-dev access:")
    dev_works = test_direct_withcare_dev()
    
    print(f"\n=== Results ===")
    print(f"Role assumption (K8s pattern): {'✅ PASS' if role_assumption_works else '❌ FAIL'}")
    print(f"Direct withcare-mgmt: {'✅ PASS' if mgmt_works else '❌ FAIL'}")
    print(f"Direct withcare-dev: {'✅ PASS' if dev_works else '❌ FAIL'}")
    
    if dev_works:
        print(f"\n💡 withcare-dev works! You can:")
        print(f"   Option 1: Use withcare-dev role for Kubernetes")
        print(f"   Option 2: Copy withcare-dev permissions to withcare-mgmt")
        print(f"\nRun the diagnostic script to see exact role differences:")
        print(f"   python diagnose_aws_roles.py")
    
    if role_assumption_works:
        print("\n✅ Your Kubernetes setup should work!")
    else:
        print("\n❌ Fix role assumption before deploying to Kubernetes")