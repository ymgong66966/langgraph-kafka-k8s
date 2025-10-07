import requests

url = "http://ad6aee916b39d489a950ddde67340849-150473755.us-east-2.elb.amazonaws.com/external/send"

headers = {
    "Content-Type": "application/json"
}

data = {
    "user_id": "de15ac05-4fc4-42df-9dd5-0bcd676484e3",
    "messages": [
        {
            "role": "user",
            "text": "hey how is your day"
        }
    ]
}

try:
    response = requests.post(url, json=data, headers=headers, timeout=120)
    print("Status code:", response.status_code)
    print("Response body:", response.text)
except requests.exceptions.Timeout:
    print("Request timed out after 120 seconds.")
except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")