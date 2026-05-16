import requests
import json

def token():
    url = 'https://mersintransfer.turpakmonitor.com/api/Login/Login'
    headers = {'accept': 'text/plain','content-type': 'application/json'}
    body = """{
        "companyName": "mersinnukleer",
        "password": "bW9JCveXDkkr"
    }"""

    req = requests.post(url, headers=headers, data=body)

    #print(req.status_code)
    data = json.loads(req.text)
    #print(data["token"])
    token = data["token"]
    return token

