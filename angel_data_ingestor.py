import os
import requests
# import time
import pandas as pd
import pyotp
# from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
from datetime import datetime, timedelta

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

        try:
            self._login()
        except Exception as e:
            print(f"Warning: Login failed during initialization. Credentials might be missing. {e}")

    def _login(self):
        if not self.totp_secret:
            raise ValueError("TOTP_SECRET environment variable is not set.")

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

    def get_scrip_master(self):
        """Downloads and returns the raw Scrip Master JSON data."""
        if not hasattr(self, '_scrip_master_cache'):
            print("Downloading Scrip Master...")
            scrip_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            response = requests.get(scrip_url)
            if response.status_code != 200:
                raise Exception("Failed to download Scrip Master file.")
            self._scrip_master_cache = response.json()
        return self._scrip_master_cache

    def get_spot_fut_mapping(self):
        """Builds a mapping of Spot tokens, Near-Month Future tokens, and Lot Sizes."""
        data = self.get_scrip_master()
        
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
        """Fetches LTPs, calculates the premium %, and Absolute Profit per Lot. Expects a batch of max 25 mappings."""
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        all_results = []
        
        nse_tokens = [pair['spot_token'] for pair in mappings]
        nfo_tokens = [pair['fut_token'] for pair in mappings]

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

                for pair in mappings:
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

    def get_historical_data(self, token, exchange='NSE', interval='ONE_DAY', days_back=30):
        url = f"{self.base_url}rest/secure/angelbroking/historical/v1/getCandleData"

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        payload = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": start_date.strftime("%Y-%m-%d %H:%M"),
            "todate": end_date.strftime("%Y-%m-%d %H:%M")
        }

        response = requests.post(url, headers=self.headers, json=payload)
        time.sleep(0.5) # rate limit

        if response.status_code == 200:
            result = response.json()
            if result.get('status') is True and result.get('data'):
                df = pd.DataFrame(result['data'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
                df['close'] = df['close'].astype(float)
                return df
        return None

    def analyze_mean_reversion_batch(self, mappings):
        """Analyzes a batch of stocks for Mean Reversion (Z-Score)."""
        all_results = []

        # 1. Fetch real-time LTPs for the batch
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        nse_tokens = [pair['spot_token'] for pair in mappings]

        payload = {
            "mode": "LTP",
            "exchangeTokens": {
                "NSE": nse_tokens
            }
        }

        ltp_dict = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                ltp_dict = {item['symbolToken']: item['ltp'] for item in fetched_data}

        # 2. Fetch historical data & calculate stats for each stock
        for pair in mappings:
            token = pair['spot_token']
            stock_name = pair['name']

            ltp = ltp_dict.get(token)
            if not ltp:
                continue

            df = self.get_historical_data(token, days_back=40) # Fetch enough days for a reliable 20-day SMA
            if df is not None and len(df) >= 20:
                # Calculate 20-Day SMA and Standard Deviation
                sma20 = df['close'].rolling(window=20).mean().iloc[-1]
                std_dev = df['close'].rolling(window=20).std().iloc[-1]

                if pd.notna(sma20) and pd.notna(std_dev) and std_dev > 0:
                    z_score = (ltp - sma20) / std_dev

                    # Only include stocks that have deviated significantly (Optional filter)
                    if abs(z_score) >= 1.0: # Filter out "boring" stocks to save frontend bandwidth
                        all_results.append({
                            "stock": stock_name + "-EQ",
                            "ltp": float(ltp),
                            "sma": float(sma20),
                            "std_dev": float(std_dev),
                            "z_score": float(z_score)
                        })

        return all_results

    def get_predefined_pairs(self, mappings):
        """Finds tokens for predefined highly correlated classic pairs."""
        target_pairs = [
            ("TCS-EQ", "INFY-EQ"),
            ("HDFCBANK-EQ", "ICICIBANK-EQ"),
            ("RELIANCE-EQ", "ONGC-EQ"),
            ("BAJFINANCE-EQ", "BAJAJFINSV-EQ"),
            ("MARUTI-EQ", "M&M-EQ"),
            ("SUNPHARMA-EQ", "CIPLA-EQ"),
            ("TATASTEEL-EQ", "JSWSTEEL-EQ"),
            ("ULTRACEMCO-EQ", "SHREECEM-EQ"),
            ("AXISBANK-EQ", "SBIN-EQ"),
            ("HINDUNILVR-EQ", "BRITANNIA-EQ"),
        ]

        name_to_token = {item['name'] + "-EQ": item['spot_token'] for item in mappings}

        pair_mappings = []
        for stock_a, stock_b in target_pairs:
            if stock_a in name_to_token and stock_b in name_to_token:
                pair_mappings.append({
                    "stock_a": stock_a,
                    "token_a": name_to_token[stock_a],
                    "stock_b": stock_b,
                    "token_b": name_to_token[stock_b]
                })

        return pair_mappings

    def analyze_stat_arb_batch(self, pair_batch):
        """Analyzes a batch of stock pairs for Statistical Arbitrage."""
        all_results = []

        # 1. Fetch real-time LTPs for the batch
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        nse_tokens = []
        for pair in pair_batch:
            nse_tokens.extend([pair['token_a'], pair['token_b']])

        # Deduplicate
        nse_tokens = list(set(nse_tokens))

        payload = {
            "mode": "LTP",
            "exchangeTokens": {
                "NSE": nse_tokens
            }
        }

        ltp_dict = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                ltp_dict = {item['symbolToken']: item['ltp'] for item in fetched_data}

        # 2. Process each pair
        for pair in pair_batch:
            token_a = pair['token_a']
            token_b = pair['token_b']

            ltp_a = ltp_dict.get(token_a)
            ltp_b = ltp_dict.get(token_b)

            if not ltp_a or not ltp_b:
                continue

            # Fetch historical data (e.g. 60 days to get a good 20-day correlation and SMA)
            df_a = self.get_historical_data(token_a, days_back=60)
            df_b = self.get_historical_data(token_b, days_back=60)

            if df_a is not None and df_b is not None and not df_a.empty and not df_b.empty:
                # Align data by datetime
                df_merged = pd.merge(df_a[['datetime', 'close']], df_b[['datetime', 'close']], on='datetime', suffixes=('_a', '_b'))

                if len(df_merged) >= 20:
                    # Calculate ratio (Price A / Price B)
                    df_merged['ratio'] = df_merged['close_a'] / df_merged['close_b']

                    # Calculate Pearson Correlation for the last 20 days
                    correlation = df_merged['close_a'].tail(20).corr(df_merged['close_b'].tail(20))

                    # Calculate SMA and Std Dev of the Ratio over the last 20 days
                    sma20 = df_merged['ratio'].rolling(window=20).mean().iloc[-1]
                    std_dev = df_merged['ratio'].rolling(window=20).std().iloc[-1]

                    # Current Ratio
                    current_ratio = ltp_a / ltp_b

                    if pd.notna(sma20) and pd.notna(std_dev) and std_dev > 0:
                        z_score = (current_ratio - sma20) / std_dev

                        all_results.append({
                            "stock_a": pair['stock_a'],
                            "stock_b": pair['stock_b'],
                            "ltp_a": float(ltp_a),
                            "ltp_b": float(ltp_b),
                            "ratio": float(current_ratio),
                            "correlation": float(correlation),
                            "z_score": float(z_score)
                        })

        return all_results

    def get_options_dict(self):
        """Parses Scrip Master for Near-Month Options (CE/PE) mapped to Underlying names."""
        data = self.get_scrip_master()

        # Get all Options
        opt_list = [item for item in data if item['exch_seg'] == 'NFO' and item['instrumenttype'] in ['OPTSTK', 'OPTIDX']]

        # Parse expiry dates properly to sort chronologically
        from datetime import datetime
        for opt in opt_list:
            try:
                # Angel One expiry format e.g. "26OCT2023"
                opt['parsed_expiry'] = datetime.strptime(opt['expiry'], '%d%b%Y')
            except ValueError:
                # Fallback far into the future if parsing fails
                opt['parsed_expiry'] = datetime(2100, 1, 1)

        opt_list.sort(key=lambda x: (x['name'], x['parsed_expiry'], float(x['strike']) if float(x['strike']) > 0 else 0))

        options_dict = {}
        for opt in opt_list:
            name = opt['name']
            strike = float(opt['strike']) / 100.0  # Angel One strikes are multiplied by 100

            # Extract CE or PE safely from the end of the symbol
            opt_type = opt['symbol'][-2:]
            if opt_type not in ['CE', 'PE']:
                 continue

            expiry = opt['expiry']

            if name not in options_dict:
                options_dict[name] = {}

            # Only keep the nearest expiry (since we sorted, the first we see is nearest, but we need to group by strike)
            if strike not in options_dict[name]:
                 options_dict[name][strike] = {}

            if opt_type not in options_dict[name][strike]:
                 options_dict[name][strike][opt_type] = {
                      'token': opt['token'],
                      'symbol': opt['symbol'],
                      'expiry': expiry
                 }
        return options_dict

    def analyze_oi_divergence_batch(self, mappings):
        """Analyzes ATM Options OI against Spot Price changes for Divergence Signals."""
        all_results = []
        options_dict = self.get_options_dict()

        # 1. Fetch FULL quote for spot tokens to get LTP and previous Close (for % change)
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        nse_tokens = [pair['spot_token'] for pair in mappings]

        payload = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": nse_tokens
            }
        }

        spot_data = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                for item in fetched_data:
                     spot_data[item['symbolToken']] = {
                         'ltp': float(item.get('ltp', 0)),
                         'close': float(item.get('close', 0))
                     }

        # 2. Identify ATM strikes and collect their option tokens to fetch OI
        nfo_tokens_to_fetch = []
        opt_requests_map = {} # Maps spot token -> { 'ce_token': .., 'pe_token': .. }

        for pair in mappings:
            spot_token = pair['spot_token']
            name = pair['name']

            if spot_token not in spot_data or name not in options_dict:
                 continue

            ltp = spot_data[spot_token]['ltp']
            close = spot_data[spot_token]['close']
            if close == 0: continue

            price_pct_change = ((ltp - close) / close) * 100

            # Find closest strike (ATM)
            available_strikes = list(options_dict[name].keys())
            if not available_strikes: continue

            atm_strike = min(available_strikes, key=lambda x: abs(x - ltp))

            opt_chain = options_dict[name][atm_strike]
            if 'CE' in opt_chain and 'PE' in opt_chain:
                 ce_token = opt_chain['CE']['token']
                 pe_token = opt_chain['PE']['token']
                 nfo_tokens_to_fetch.extend([ce_token, pe_token])
                 opt_requests_map[spot_token] = {
                      'name': name,
                      'ltp': ltp,
                      'pct_change': price_pct_change,
                      'atm_strike': atm_strike,
                      'ce_token': ce_token,
                      'pe_token': pe_token
                 }

        if not nfo_tokens_to_fetch:
             return all_results

        # 3. Fetch OI for the ATM options
        payload_nfo = {
            "mode": "FULL",
            "exchangeTokens": {
                "NFO": nfo_tokens_to_fetch
            }
        }

        oi_dict = {}
        response_nfo = requests.post(url, headers=self.headers, json=payload_nfo)
        if response_nfo.status_code == 200:
            result = response_nfo.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                for item in fetched_data:
                     # 'opnInterest' is the field returned by Angel One for OI
                     oi_dict[item['symbolToken']] = float(item.get('opnInterest', 0))

        # 4. Calculate PCR and Signals
        for spot_token, data in opt_requests_map.items():
             ce_oi = oi_dict.get(data['ce_token'], 0)
             pe_oi = oi_dict.get(data['pe_token'], 0)

             # Avoid division by zero
             if ce_oi == 0 and pe_oi == 0: continue

             # PCR = Put OI / Call OI
             pcr = pe_oi / ce_oi if ce_oi > 0 else float('inf')
             if pcr == float('inf'): continue # ignore extremes for UI clarity

             signal = "Neutral"
             pct_change = data['pct_change']

             # Divergence Logic:
             # If price is dropping sharply but PCR is high (>1.2), puts are being heavily written.
             # Smart money is providing support. Bullish Divergence.
             if pct_change < -0.5 and pcr > 1.2:
                 signal = "Bullish Divergence"

             # If price is rising sharply but PCR is low (<0.8), calls are being heavily written.
             # Smart money is creating resistance. Bearish Divergence.
             elif pct_change > 0.5 and pcr < 0.8:
                 signal = "Bearish Divergence"

             # We'll return everything to the frontend to allow users to see the raw data,
             # but the frontend can filter or highlight based on signal.
             all_results.append({
                 "underlying": data['name'],
                 "spot_price": data['ltp'],
                 "price_pct_change": pct_change,
                 "call_oi": ce_oi,
                 "put_oi": pe_oi,
                 "pcr": pcr,
                 "signal": signal
             })

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