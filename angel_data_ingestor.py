import os
import requests
import time
import pandas as pd
import pyotp
from datetime import datetime, timedelta
from dotenv import load_dotenv

class AngelDataIngestor:
    def __init__(self):
        load_dotenv()

        self.api_key = os.getenv("API_KEY")
        self.client_code = os.getenv("CLIENT_CODE")
        self.pin = os.getenv("PIN")
        self.totp_secret = os.getenv("TOTP_SECRET")
        self.client_local_ip = os.getenv("CLIENT_LOCAL_IP")
        self.client_public_ip = os.getenv("CLIENT_PUBLIC_IP")
        self.mac_address = os.getenv("MAC_ADDRESS")

        self.headers = {
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

    def fetch_data(self, symboltoken, interval, start_date_str, end_date_str):
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d %H:%M")

        master_data = []
        current_start = start_date

        # Define max days per chunk. Assuming 30 days for ONE_MINUTE.
        # Extend logic here if other intervals have different limits.
        max_days = 30 if interval == "ONE_MINUTE" else 30

        while current_start < end_date:
            current_end = current_start + timedelta(days=max_days)
            if current_end > end_date:
                current_end = end_date

            payload = {
                "exchange": "NSE",
                "symboltoken": symboltoken,
                "interval": interval,
                "fromdate": current_start.strftime("%Y-%m-%d %H:%M"),
                "todate": current_end.strftime("%Y-%m-%d %H:%M")
            }

            response_data = self._make_api_request(payload)
            if response_data and "data" in response_data and response_data["data"]:
                master_data.extend(response_data["data"])

            current_start = current_end

        return master_data

    def format_to_dataframe(self, raw_candle_list):
        if not raw_candle_list:
            return pd.DataFrame()

        df = pd.DataFrame(raw_candle_list, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])

        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)

        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(int)

        return df

    def save_to_csv(self, df, filename):
        df.to_csv(filename)
