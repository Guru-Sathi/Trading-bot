import os
import requests
import time
from dotenv import load_dotenv

class AngelDataIngestor:
    def __init__(self):
        load_dotenv()

        self.api_key = os.getenv("API_KEY")
        self.client_local_ip = os.getenv("CLIENT_LOCAL_IP")
        self.client_public_ip = os.getenv("CLIENT_PUBLIC_IP")
        self.mac_address = os.getenv("MAC_ADDRESS")
        self.auth_token = os.getenv("AUTH_TOKEN")

        self.headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self.client_local_ip,
            "X-ClientPublicIP": self.client_public_ip,
            "X-MACAddress": self.mac_address,
            "X-PrivateKey": self.api_key
        }

    def _make_api_request(self, payload_dict):
        url = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"

        try:
            response = requests.post(url, headers=self.headers, json=payload_dict)
            if response.status_code == 200:
                result = response.json()
            else:
                print(f"Error: Received status code {response.status_code}")
                result = None
        except Exception as e:
            print(f"Error making API request: {e}")
            result = None

        time.sleep(0.5)
        return result
