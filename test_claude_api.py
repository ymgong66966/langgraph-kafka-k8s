import anthropic
import asyncio
import os

# Set your API key as an environment variable before running this script
# export ANTHROPIC_API_KEY="your-api-key-here"

async def main():
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=40000,
        thinking={
            "type": "enabled",
            "budget_tokens": 1000
        },
        messages=[{
            "role": "user",
            "content": "Are there an infinite number of prime numbers such that n mod 4 == 3?"
        }]
    )
    
    # The response will contain summarized thinking blocks and text blocks
    for block in response.content:
        if block.type == "thinking":
            print(f"\nThinking summary: {block.thinking}")
        elif block.type == "text":
            print(f"\nResponse: {block.text}")

if __name__ == "__main__":
    asyncio.run(main())