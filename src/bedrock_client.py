"""
AWS Bedrock client for Kubernetes pods with role assumption
"""
import boto3
import os
import logging
import json
from botocore.exceptions import ClientError, NoCredentialsError

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
                # Kubernetes environment - use withcare-dev role ARN directly
                logger.info("Kubernetes environment detected, using withcare-dev role")
                sts_client = boto3.client('sts', region_name=self.region)
                
                # Assume the withcare-dev role (OrganizationAccountAccessRole in account 216989110335)
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
                
                logger.info("Successfully initialized Bedrock client with withcare-dev role")
            else:
                # Local environment - use withcare-dev profile directly
                logger.info("Local environment detected, using withcare-dev profile")
                session = boto3.Session(profile_name="withcare-dev", region_name=self.region)
                self.client = session.client('bedrock-runtime')
                logger.info("Successfully initialized Bedrock client with withcare-dev profile")
            
        except NoCredentialsError:
            logger.error("No AWS credentials found. Ensure AWS profile is configured or IRSA is set up.")
            raise
        except ClientError as e:
            logger.error(f"Failed to initialize Bedrock client: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing Bedrock client: {e}")
            raise
    
    def converse(self, messages, max_tokens=1000, temperature=0.2, top_p=0.9):
        """
        Send messages to Bedrock model and get response using invoke_model API
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            
        Returns:
            Response text from the model
        """
        if not self.client:
            raise RuntimeError("Bedrock client not initialized")
        
        try:
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
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'AccessDeniedException':
                logger.error(f"Access denied to model {self.model_id}. Check model permissions.")
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

# Example usage for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    try:
        bedrock = BedrockClient()
        response = bedrock.simple_chat("Say hello in one sentence.")
        print(f"Bedrock response: {response}")
    except Exception as e:
        print(f"Error: {e}")
