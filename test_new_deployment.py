# import requests
# import json

# url = "http://ab790c9dc7db9479b8f6cee7c3fe8c97-479113026.us-east-2.elb.amazonaws.com/external/send"

# headers = {
#     "Content-Type": "application/json"
# }

# payload = {
#     "user_id": "0742e9f2-502f-4e5f-92ed-ee6436bf9ca3",
#     "messages": [
#         {"role": "user", "text": "hey how is your day"}
#     ]
# }

# response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)

# print("Status code:", response.status_code)
# print("Response body:", response.text)

import requests
import json

url = "https://tarz0uu2n5.execute-api.us-east-2.amazonaws.com/prod/getuser-mcp"

payload = {"user_Id": "de15ac05-4fc4-42df-9dd5-0bcd676484e3"}
headers1 = {
    "x-api-key": "0h5c9wlW2L8Joqk6fpJUz6NnsIjLB6Su7V62ozcd",
    "Content-Type": "application/json"
}


response1 = requests.post(url, headers=headers1, data=json.dumps(payload))
user_info_json = json.loads(response1.text)
print(user_info_json)

import requests
import json

url = "https://tarz0uu2n5.execute-api.us-east-2.amazonaws.com/prod/getrecipient-mcp"

payload = {"recipient_Id": "409781c6-f9f9-43ef-a5ec-d1a742a47f27"}
headers1 = {
    "x-api-key": "0h5c9wlW2L8Joqk6fpJUz6NnsIjLB6Su7V62ozcd",
    "Content-Type": "application/json"
}


response1 = requests.post(url, headers=headers1, data=json.dumps(payload))
user_info_json = json.loads(response1.text)
print(user_info_json)