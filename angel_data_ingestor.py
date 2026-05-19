import os
import requests
# import time
import pandas as pd
import pyotp
# from datetime import datetime, timedelta
from dotenv import load_dotenv

class AngelDataIngestor:
    def __init__(self):
        load_dotenv()

        self.base_url = os.getenv("BASE_URL")
        self.login_url = os.getenv("LOGIN_URL")
        self.api_key = os.getenv("API_KEY")
        self.client_code = os.getenv("CLIENT_CODE")
        self.pin = os.getenv("PIN")
        self.totp_secret = os.getenv("TOTP_SECRET")
        self.client_local_ip = os.getenv("CLIENT_LOCAL_IP")
        self.client_public_ip = os.getenv("CLIENT_PUBLIC_IP")
        self.mac_address = os.getenv("MAC_ADDRESS")

        self.auth_token = None

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

        self._login()

    def _login(self):
        totp = pyotp.TOTP(self.totp_secret).now()

        payload = {
            "clientcode": self.client_code,
            "password": self.pin,
            "totp": totp
        }

        url = f"{self.base_url}{self.login_url}"

        response = requests.post(url, headers=self.headers, json=payload)

        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                self.auth_token = result.get("data", {}).get("jwtToken")
                if self.auth_token:
                    self.headers["Authorization"] = f"Bearer {self.auth_token}"
                    res = self.get_profile()  # Fetch profile to confirm login success
                    print(res)

                else:
                    raise Exception("Login succeeded but jwtToken not found in response data.")
            else:
                error_msg = result.get("message", "Unknown error")
                raise Exception(f"Login failed: {error_msg}")
        else:
            raise Exception(f"Login request failed with status code: {response.status_code}, response: {response.text}")

    def get_profile(self):
        url = f"{self.base_url}rest/secure/angelbroking/user/v1/getProfile"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get profile: {response.status_code}, {response.text}")

    def get_spot_fut_mapping(self):
        """Builds a mapping of Spot tokens, Near-Month Future tokens, and Lot Sizes."""
        print("Downloading Scrip Master for mapping...")
        scrip_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        response = requests.get(scrip_url)
        
        if response.status_code != 200:
            raise Exception("Failed to download Scrip Master file.")
            
        data = response.json()
        
        # 1. Create a fast lookup dictionary for NSE Spot tokens (Cash market)
        nse_spot_dict = {
            item['name']: item['token'] 
            for item in data 
            if item['exch_seg'] == 'NSE' and item['symbol'].endswith('-EQ')
        }
        
        # 2. Get all Futures and sort them by name and expiry to easily grab the near-month
        fut_list = [item for item in data if item['exch_seg'] == 'NFO' and item['instrumenttype'] == 'FUTSTK']
        fut_list.sort(key=lambda x: (x['name'], x['expiry']))
        
        mapping = []
        seen_names = set()
        
        # 3. Match them up and extract the Lot Size
        for fut in fut_list:
            name = fut['name']
            if name not in seen_names and name in nse_spot_dict:
                mapping.append({
                    'name': name,
                    'fut_symbol': fut['symbol'],
                    'fut_token': fut['token'],
                    'spot_token': nse_spot_dict[name],
                    'lotsize': int(fut['lotsize'])  # <--- Grabbing the Lot Size here
                })
                seen_names.add(name)
                
        print(f"Mapped {len(mapping)} Stock Futures to their underlying Spot assets.")
        return mapping

    def fetch_and_calculate_premiums(self, mappings):
        """Fetches LTPs, calculates the premium %, and Absolute Profit per Lot."""
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        all_results = []
        
        for i in range(0, len(mappings), 25):
            batch = mappings[i:i + 25]
            
            nse_tokens = [pair['spot_token'] for pair in batch]
            nfo_tokens = [pair['fut_token'] for pair in batch]
            
            payload = {
                "mode": "LTP",
                "exchangeTokens": {
                    "NSE": nse_tokens,
                    "NFO": nfo_tokens
                }
            }
            
            response = requests.post(url, headers=self.headers, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") is True:
                    fetched_data = result.get("data", {}).get("fetched", [])
                    ltp_dict = {item['symbolToken']: item['ltp'] for item in fetched_data}
                    
                    for pair in batch:
                        spot_ltp = ltp_dict.get(pair['spot_token'])
                        fut_ltp = ltp_dict.get(pair['fut_token'])
                        
                        if spot_ltp and fut_ltp:
                            diff = fut_ltp - spot_ltp
                            
                            # Filter: Only keep if Futures price is HIGHER than Spot
                            if diff > 0:
                                lotsize = pair['lotsize']
                                pct_diff = (diff / spot_ltp) * 100
                                
                                # Absolute calculations
                                gross_profit_per_lot = diff * lotsize
                                spot_capital_required = spot_ltp * lotsize
                                
                                all_results.append({
                                    "Stock": pair['name'],
                                    "Spot": spot_ltp,
                                    "Future": fut_ltp,
                                    "Premium_%": round(pct_diff, 2),
                                    "Lot_Size": lotsize,
                                    "Gross_Profit(₹)": round(gross_profit_per_lot, 2),
                                    "Spot_Capital(₹)": round(spot_capital_required, 2)
                                })
            else:
                print(f"Batch failed. Status: {response.status_code}")
                
        return all_results

if __name__ == "__main__":
    import pandas as pd
    
    # Optional: formatting Pandas to display large numbers cleanly without scientific notation
    pd.options.display.float_format = '{:,.2f}'.format
    
    try:
        print("Initializing AngelDataIngestor...")
        ingestor = AngelDataIngestor() 
        
        print("\n--- Scanning for Cash-Future Arbitrage Opportunities ---")
        
        stock_mappings = ingestor.get_spot_fut_mapping()
        print("Fetching live prices and calculating Absolute Profit...\n")
        
        premium_stocks = ingestor.fetch_and_calculate_premiums(stock_mappings)
        
        if premium_stocks:
            df = pd.DataFrame(premium_stocks)
            
            # Sort by the Highest Absolute Profit per Lot
            df_sorted = df.sort_values(by="Gross_Profit(₹)", ascending=False)
            df_sorted = df_sorted.reset_index(drop=True)
            
            print(df_sorted.to_string())
            print(f"\nTotal stocks trading at a premium: {len(df_sorted)}")
        else:
            print("No stocks are currently trading at a premium.")
            
    except Exception as e:
        print(f"\nExecution Failed: {e}")