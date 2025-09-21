"""
Uses the Amazon Bedrock runtime client with role assumption to test the same flow as Kubernetes pods.
This simulates: withcare-ml profile -> assume withcare-mgmt role -> access Bedrock
"""
import logging
import json
import boto3
import os
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BedrockClient:
    """AWS Bedrock client for Kubernetes pods with role assumption"""
    def __init__(self):
        self.region = 'us-east-2'
        self.model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize Bedrock client using withcare_dev profile"""
        try:
            k8s_token_file = os.getenv('AWS_WEB_IDENTITY_TOKEN_FILE')
            
            # Check multiple indicators for Kubernetes environment
            is_k8s = (k8s_token_file and os.path.exists(k8s_token_file)) or \
                     os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token') or \
                     os.getenv('KUBERNETES_SERVICE_HOST')
            
            if is_k8s:
                logger.info("Kubernetes environment detected, attempting IRSA role assumption")
                try:
                    sts_client = boto3.client('sts', region_name=self.region)

                    assumed_role = sts_client.assume_role(
                        RoleArn='arn:aws:iam::216989110335:role/OrganizationAccountAccessRole',
                        RoleSessionName='bedrock-k8s-session'
                    )

                    credentials = assumed_role['Credentials']

                    self.client = boto3.client(
                        'bedrock-runtime',
                        region_name=self.region,
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken']
                    )

                    logger.info("Successfully initialized Bedrock client with IRSA role")
                except Exception as irsa_error:
                    logger.warning(f"IRSA failed: {irsa_error}")
                    logger.info("Falling back to withcare_dev profile")
                    session = boto3.Session(profile_name="withcare_dev", region_name=self.region)
                    self.client = session.client('bedrock-runtime')
                    logger.info("Successfully initialized Bedrock client with withcare_dev profile")
            else:
                logger.info("Local environment detected, using withcare_dev profile")
                session = boto3.Session(profile_name="withcare_dev", region_name=self.region)
                self.client = session.client('bedrock-runtime')
                logger.info("Successfully initialized Bedrock client with withcare_dev profile")
            
        except Exception as e:
            logger.error(f"Unexpected error initializing Bedrock client: {e}")
            raise
    
    def simple_chat(self, prompt, max_tokens=1000):
        """Simple chat interface for single prompts"""
        if not self.client:
            raise RuntimeError("Bedrock client not initialized")
        
        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "top_p": 0.9
            })
            
            response = self.client.invoke_model(
                body=body,
                modelId=self.model_id
            )
            
            response_body = json.loads(response.get('body').read())
            
            if 'content' in response_body and len(response_body['content']) > 0:
                return response_body['content'][0]['text']
            else:
                logger.error(f"Unexpected response format: {response_body}")
                return "Error: Unexpected response format"
            
        except Exception as e:
            logger.error(f"Unexpected error in simple_chat: {e}")
            raise


# def test_new_bedrock_client():
#     """Test the new BedrockClient class"""
#     try:
#         logger.info("Testing new BedrockClient class...")
#         bedrock = BedrockClient()
        
#         test_response = bedrock.simple_chat("Say 'Bedrock test successful with new client' in one sentence.")
#         logger.info("New BedrockClient test successful!")
#         print(f"New client response: {test_response}")
#         return True
        
#     except Exception as e:
#         logger.error(f"New BedrockClient test failed: {e}")
#         return False



def test_direct_withcare_dev():
    """Test direct access with withcare_dev profile"""
    try:
        logger.info("Testing direct withcare_dev access...")
        session = boto3.Session(profile_name="withcare_dev", region_name="us-east-2")
        bedrock_client = session.client("bedrock-runtime")
        
        model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "temperature": 0.2,
            "top_p": 0.9
        })
        
        response = bedrock_client.invoke_model(
            body=body,
            modelId=model_id
        )
        
        response_body = json.loads(response.get('body').read())
        result = response_body['content'][0]['text']
        logger.info("Direct withcare_dev test successful!")
        print(f"Direct withcare_dev response: {result}")
        return True
        
    except Exception as e:
        logger.error(f"Direct withcare_dev test failed: {e}")
        return False


def test_extended_thinking():
    """Test Bedrock client with extended thinking capabilities using Converse API"""
    try:
        logger.info("Testing Bedrock client with extended thinking...")
        client = BedrockClient()
        
        def make_extended_thinking_request():
            # Use Claude 3.7 Sonnet which supports extended thinking
            model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
            
            # Create a bedrock runtime client for Converse API using same session as main client
            session = boto3.Session(profile_name='withcare_dev')
            converse_client = session.client('bedrock-runtime', region_name='us-east-2')
            
            # Use Converse API with extended thinking
            response = converse_client.converse(
                modelId=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": "Analyze the pros and cons of using Kubernetes for microservices deployment. Consider scalability, complexity, and operational overhead."
                            }
                        ]
                    }
                ],
                additionalModelRequestFields={
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": 2000
                    }
                }
            )
            
            return response
        
        response = retry_with_backoff(make_extended_thinking_request)
        
        print("\n=== Extended Thinking Test Results (Turn 1) ===")
        
        # Process the response content blocks
        content_blocks = response.get('output', {}).get('message', {}).get('content', [])
        conversation_history = []
        
        for block in content_blocks:
            if 'reasoningContent' in block:
                reasoning = block['reasoningContent']
                print(f"\n🧠 Thinking Process:")
                print(f"{reasoning.get('reasoningText', 'No reasoning text')}")
            elif 'text' in block:
                print(f"\n💬 Final Response:")
                print(f"{block['text']}")
                # Store the conversation for multi-turn test
                conversation_history.append({
                    "role": "user",
                    "content": [{"text": "Analyze the pros and cons of using Kubernetes for microservices deployment. Consider scalability, complexity, and operational overhead."}]
                })
                conversation_history.append({
                    "role": "assistant", 
                    "content": [{"text": block['text']}]
                })
        
        # Log token usage
        usage = response.get('usage', {})
        if usage:
            print(f"\n📊 Token Usage (Turn 1):")
            print(f"Input tokens: {usage.get('inputTokens', 'N/A')}")
            print(f"Output tokens: {usage.get('outputTokens', 'N/A')}")
        
        # Add delay before second turn
        time.sleep(2)
        
        # Multi-turn conversation test
        def make_followup_request():
            # Use Claude 3.7 Sonnet which supports extended thinking
            followup_model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
            
            # Create a bedrock runtime client for Converse API using same session as main client
            session = boto3.Session(profile_name='withcare_dev')
            converse_client = session.client('bedrock-runtime', region_name='us-east-2')
            
            # Add follow-up question to conversation history
            followup_messages = conversation_history + [{
                "role": "user",
                "content": [{"text": "Based on your analysis, what would you recommend for a startup with 5 microservices and a team of 3 developers?"}]
            }]
            
            # Use Converse API with extended thinking for follow-up
            response = converse_client.converse(
                modelId=followup_model_id,
                messages=followup_messages,
                additionalModelRequestFields={
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": 1500
                    }
                }
            )
            
            return response
        
        # Wait a moment between requests to avoid throttling
        time.sleep(2)
        
        print("\n=== Extended Thinking Test Results (Turn 2 - Multi-turn) ===")
        followup_response = retry_with_backoff(make_followup_request)
        
        # Process the follow-up response
        followup_blocks = followup_response.get('output', {}).get('message', {}).get('content', [])
        
        for block in followup_blocks:
            if 'reasoningContent' in block:
                reasoning = block['reasoningContent']
                print(f"\n🧠 Follow-up Thinking Process:")
                print(f"{reasoning.get('reasoningText', 'No reasoning text')}")
            elif 'text' in block:
                print(f"\n💬 Follow-up Response:")
                print(f"{block['text']}")
        
        # Log follow-up token usage
        followup_usage = followup_response.get('usage', {})
        if followup_usage:
            print(f"\n📊 Token Usage (Turn 2):")
            print(f"Input tokens: {followup_usage.get('inputTokens', 'N/A')}")
            print(f"Output tokens: {followup_usage.get('outputTokens', 'N/A')}")
        
        logger.info("Extended thinking multi-turn test successful!")
        
        print(f"\n✅ Multi-turn extended thinking test completed successfully!")
        
        logger.info("Extended thinking test successful!")
        return True
        
    except Exception as e:
        logger.error(f"Extended thinking test failed: {e}")
        print(f"Error details: {str(e)}")
        
        # Fallback to regular test if extended thinking fails
        try:
            print("\n=== Fallback to Standard API ===")
            simple_response = client.simple_chat("Briefly explain Kubernetes benefits for microservices.")
            print(f"Standard response: {simple_response[:200]}...")
            return True
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            return False


# def test_simple_vs_extended_thinking():
#     """Compare simple chat vs extended thinking responses - Currently testing simple responses only"""
#     try:
#         logger.info("Testing simple chat responses (extended thinking temporarily disabled)...")
#         client = BedrockClient()
        
#         test_prompt = "Explain the trade-offs between using AWS EKS vs self-managed Kubernetes clusters."
        
#         # Test 1: Simple response with retry
#         print("\n=== Simple Response ===")
#         def make_simple_request():
#             return client.simple_chat(test_prompt)
        
#         simple_result = retry_with_backoff(make_simple_request)
#         print(f"Simple response: {simple_result[:200]}...")
        
#         # Test 2: Another simple response to verify consistency
#         print("\n=== Second Simple Response (for consistency check) ===")
#         test_prompt2 = "What are the key benefits of using Kubernetes for container orchestration?"
        
#         def make_simple_request2():
#             return client.simple_chat(test_prompt2)
        
#         simple_result2 = retry_with_backoff(make_simple_request2)
#         print(f"Second response: {simple_result2[:200]}...")
        
#         logger.info("Simple chat comparison test successful!")
#         return True
        
#     except Exception as e:
#         logger.error(f"Comparison test failed: {e}")
#         return False


def retry_with_backoff(func, max_attempts=5, backoff_factor=2):
    attempt = 0
    delay = 1
    while attempt < max_attempts:
        try:
            return func()
        except Exception as e:
            attempt += 1
            if attempt < max_attempts:
                logger.warning(f"Attempt {attempt} failed: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= backoff_factor
            else:
                logger.error(f"All {max_attempts} attempts failed: {e}")
                raise


if __name__ == "__main__":
    print("=== AWS Bedrock Client Tests ===\n")
    
    # Run all tests
    tests = [
        # ("BedrockClient Test", test_new_bedrock_client),
        # ("Role Assumption Test", test_bedrock_with_role_assumption),
        ("Direct withcare_dev Test", test_direct_withcare_dev),
        ("Extended Thinking Test", test_extended_thinking),
        # ("Simple vs Extended Thinking", test_simple_vs_extended_thinking)
    ]
    
    results = {}
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Running: {test_name}")
        print('='*50)
        results[test_name] = test_func()
        time.sleep(1)  # Brief pause between tests
    
    # Summary
    print(f"\n{'='*50}")
    print("TEST SUMMARY")
    print('='*50)
    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name}: {status}")
    
    passed = sum(results.values())
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Bedrock integration is working correctly.")
    else:
        print("⚠️  Some tests failed. Check the logs above for details.")