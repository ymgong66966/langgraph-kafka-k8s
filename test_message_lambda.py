#!/usr/bin/env python3
"""
Test script to send messages to the Navigator message delivery API
"""

import requests
import json

def send_navigator_message(user_id, messages):
    """
    Send messages to the Navigator message delivery API
    
    Args:
        user_id (str): The user ID to send messages to
        messages (list): List of message objects with 'text' field
    
    Returns:
        dict: API response
    """
    url = "https://h9d1ldlv65.execute-api.us-east-2.amazonaws.com/dev/delivernavigatormessage"
    
    headers = {
        "Content-Type": "application/json",
        "x-api-key": "iwja4JC4q765W7VlfqBVx2RAYSISs9lPwEyqNvfh"
    }
    
    payload = {
        "user_Id": user_id,
        "messages": messages
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        print(f"✅ Message sent successfully!")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        return response.json()
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending message: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            print(f"Response Text: {e.response.text}")
        return None

def main():
    """Test the message delivery function"""
    user_id = "0742e9f2-502f-4e5f-92ed-ee6436bf9ca3"
    messages = [
        {"text": "Hello"}
    ]
    
    print(f"📤 Sending message to user: {user_id}")
    print(f"📝 Messages: {json.dumps(messages, indent=2)}")
    
    result = send_navigator_message(user_id, messages)
    
    if result:
        print("🎉 Test completed successfully!")
    else:
        print("💥 Test failed!")

if __name__ == "__main__":
    main()