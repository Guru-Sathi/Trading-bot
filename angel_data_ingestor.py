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

    def _get_mock_fundamentals(self, stock_name):
        """
        Simulates fundamental data since Angel One API does not provide deep fundamentals.
        In a real production environment, this would call a secondary API (e.g., Screener/TickerTape).
        Uses a hash of the stock name to generate consistent, deterministic pseudo-random values.
        """
        import hashlib
        # Create a deterministic integer based on the stock name
        hash_val = int(hashlib.md5(stock_name.encode()).hexdigest(), 16)

        # P/E Ratio: Generally between 10 and 80
        pe_ratio = 10 + (hash_val % 70) + ((hash_val % 100) / 100.0)

        # ROE: Generally between 5% and 35%
        roe = 5 + (hash_val % 30) + ((hash_val % 50) / 100.0)

        # Debt to Equity: Generally between 0.0 and 3.0
        debt_equity = (hash_val % 300) / 100.0

        # Financial Health Score (1-10) based on metrics
        score = 10
        if pe_ratio > 30: score -= 2
        if pe_ratio > 50: score -= 2
        if roe < 15: score -= 2
        if roe < 10: score -= 1
        if debt_equity > 1.0: score -= 2
        if debt_equity > 2.0: score -= 1

        return {
            "pe_ratio": round(pe_ratio, 2),
            "roe": round(roe, 2),
            "debt_equity": round(debt_equity, 2),
            "health_score": max(1, score)
        }

    def analyze_mean_reversion_batch(self, mappings):
        """Analyzes a batch of stocks for Mean Reversion (Z-Score) & Fundamental Health."""
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

                        fundamentals = self._get_mock_fundamentals(stock_name)

                        all_results.append({
                            "stock": stock_name + "-EQ",
                            "ltp": float(ltp),
                            "sma": float(sma20),
                            "std_dev": float(std_dev),
                            "z_score": float(z_score),
                            "fundamentals": fundamentals
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

    def get_nifty_options_chain(self):
        """Extracts the entire Near-Month Options Chain for NIFTY 50."""
        data = self.get_scrip_master()

        # 1. Filter strictly for NIFTY options
        opt_list = [item for item in data if item['name'] == 'NIFTY' and item['instrumenttype'] == 'OPTIDX' and item['exch_seg'] == 'NFO']

        from datetime import datetime

        # We need to find the nearest valid weekly/monthly expiry
        now = datetime.now()
        valid_expiries = []

        for opt in opt_list:
            try:
                dt = datetime.strptime(opt['expiry'], '%d%b%Y')
                # Include today as a valid expiry
                if dt.date() >= now.date():
                    valid_expiries.append((dt, opt['expiry']))
            except:
                pass

        if not valid_expiries:
            raise Exception("No upcoming NIFTY expirations found.")

        # Find the absolute nearest expiry string
        valid_expiries.sort(key=lambda x: x[0])
        nearest_expiry_str = valid_expiries[0][1]

        # Filter the chain to ONLY this nearest expiry
        chain_list = [item for item in opt_list if item['expiry'] == nearest_expiry_str]

        # Group by strike
        strikes_dict = {}
        for opt in chain_list:
            strike = float(opt['strike']) / 100.0
            opt_type = opt['symbol'][-2:]

            if opt_type not in ['CE', 'PE']: continue

            if strike not in strikes_dict:
                strikes_dict[strike] = {}

            strikes_dict[strike][opt_type] = {
                'token': opt['token'],
                'symbol': opt['symbol']
            }

        return nearest_expiry_str, strikes_dict

    def analyze_nifty_oi_chain(self):
        """Fetches NIFTY spot, and deeply analyzes +/- 10 strikes around ATM."""
        # 1. Fetch NIFTY Spot Price
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"

        # 26000 is the standard token for NIFTY 50 Spot in NSE
        payload_spot = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": ["26000"]
            }
        }

        nifty_ltp = 0.0
        nifty_pct = 0.0
        response_spot = requests.post(url, headers=self.headers, json=payload_spot)
        if response_spot.status_code == 200:
            res = response_spot.json()
            if res.get('status') is True and res.get('data', {}).get('fetched'):
                spot_data = res['data']['fetched'][0]
                nifty_ltp = float(spot_data.get('ltp', 0))
                close = float(spot_data.get('close', 0))
                if close > 0:
                    nifty_pct = ((nifty_ltp - close) / close) * 100

        if nifty_ltp == 0:
            raise Exception("Could not fetch NIFTY 50 Spot Price.")

        # 2. Get the Options Chain structure
        expiry_str, strikes_dict = self.get_nifty_options_chain()

        # 3. Find ATM and slice +/- 10 strikes
        available_strikes = sorted(list(strikes_dict.keys()))
        atm_strike = min(available_strikes, key=lambda x: abs(x - nifty_ltp))
        atm_idx = available_strikes.index(atm_strike)

        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(available_strikes), atm_idx + 11)

        target_strikes = available_strikes[start_idx:end_idx]

        # 4. Collect tokens to fetch OI and Prices
        nfo_tokens = []
        token_to_strike = {} # mapping for easy population later

        for strike in target_strikes:
            opts = strikes_dict[strike]
            if 'CE' in opts:
                ce_token = opts['CE']['token']
                nfo_tokens.append(ce_token)
                token_to_strike[ce_token] = ('CE', strike)
            if 'PE' in opts:
                pe_token = opts['PE']['token']
                nfo_tokens.append(pe_token)
                token_to_strike[pe_token] = ('PE', strike)

        # 5. Fetch Options Data
        payload_nfo = {
            "mode": "FULL",
            "exchangeTokens": {
                "NFO": nfo_tokens
            }
        }

        chain_data = {strike: {'CE_OI': 0, 'CE_LTP': 0, 'PE_OI': 0, 'PE_LTP': 0} for strike in target_strikes}

        response_nfo = requests.post(url, headers=self.headers, json=payload_nfo)
        if response_nfo.status_code == 200:
             res = response_nfo.json()
             if res.get('status') is True:
                 for item in res.get('data', {}).get('fetched', []):
                     token = item['symbolToken']
                     if token in token_to_strike:
                         opt_type, strike = token_to_strike[token]
                         oi = float(item.get('opnInterest', 0))
                         ltp = float(item.get('ltp', 0))

                         chain_data[strike][f"{opt_type}_OI"] = oi
                         chain_data[strike][f"{opt_type}_LTP"] = ltp

        # 6. Comprehensive Analysis
        total_ce_oi = 0
        total_pe_oi = 0
        max_ce_oi = 0
        max_pe_oi = 0
        resistance_strike = 0
        support_strike = 0

        max_pain_oi_sum = float('inf')
        max_pain_strike = 0

        chain_list_result = []

        for strike in sorted(chain_data.keys(), reverse=True): # Descending for UI (Calls left, Puts right, highest strike top)
            data = chain_data[strike]
            ce_oi = data['CE_OI']
            pe_oi = data['PE_OI']

            total_ce_oi += ce_oi
            total_pe_oi += pe_oi

            if ce_oi > max_ce_oi:
                max_ce_oi = ce_oi
                resistance_strike = strike

            if pe_oi > max_pe_oi:
                max_pe_oi = pe_oi
                support_strike = strike

            # Simple Max Pain estimation: Strike where intrinsic value of all options is lowest.
            # For a quick proxy, strike with the highest aggregate OI (CE + PE) often gravitates towards Max Pain in Indian markets
            # But the true max pain formula requires calculating payoff. Let's do a simplified proxy: Highest combined OI
            combined_oi = ce_oi + pe_oi
            if combined_oi > 0:
                 # Actual Max Pain logic: Find strike that causes minimum loss to option writers.
                 # We will loop all strikes later to compute this precisely.
                 pass

            # Strike-level PCR
            strike_pcr = pe_oi / ce_oi if ce_oi > 0 else 0

            chain_list_result.append({
                "strike": strike,
                "is_atm": (strike == atm_strike),
                "ce_oi": ce_oi,
                "ce_ltp": data['CE_LTP'],
                "pe_oi": pe_oi,
                "pe_ltp": data['PE_LTP'],
                "strike_pcr": strike_pcr
            })

        # Calculate actual Max Pain
        min_loss = float('inf')
        for test_strike in target_strikes:
            total_loss = 0
            for strike in target_strikes:
                 data = chain_data[strike]
                 # CE Loss: if expiry happens at test_strike, CE writer loses if test_strike > strike
                 if test_strike > strike:
                      total_loss += (test_strike - strike) * data['CE_OI']
                 # PE Loss: if expiry happens at test_strike, PE writer loses if test_strike < strike
                 if test_strike < strike:
                      total_loss += (strike - test_strike) * data['PE_OI']

            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = test_strike

        overall_pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0

        sentiment = "Neutral"
        if overall_pcr > 1.2: sentiment = "Highly Bullish"
        elif overall_pcr > 1.0: sentiment = "Bullish"
        elif overall_pcr < 0.6: sentiment = "Highly Bearish"
        elif overall_pcr < 0.8: sentiment = "Bearish"

        return {
             "underlying": "NIFTY 50",
             "spot_price": nifty_ltp,
             "spot_pct": nifty_pct,
             "expiry": expiry_str,
             "atm_strike": atm_strike,
             "overall_pcr": overall_pcr,
             "sentiment": sentiment,
             "max_pain": max_pain_strike,
             "support": support_strike,
             "resistance": resistance_strike,
             "total_ce_oi": total_ce_oi,
             "total_pe_oi": total_pe_oi,
             "chain": chain_list_result
        }

    def get_index_heavyweights(self, mappings):
        """Returns the token mapping for NIFTY 50 and NIFTY BANK heavyweights."""
        # Approximate current weights for major indices
        nifty_50_weights = {
            "HDFCBANK": 11.5,
            "RELIANCE": 9.5,
            "ICICIBANK": 8.0,
            "INFY": 6.0,
            "LT": 4.5,
            "TCS": 4.0,
            "ITC": 4.0,
            "AXISBANK": 3.0,
            "SBIN": 3.0,
            "BHARTIARTL": 2.5
        }

        nifty_bank_weights = {
            "HDFCBANK": 29.0,
            "ICICIBANK": 23.0,
            "AXISBANK": 11.5,
            "SBIN": 11.0,
            "KOTAKBANK": 10.0
        }

        # We also need to fetch the index tokens themselves
        # 26000 = NIFTY 50, 26009 = NIFTY BANK
        index_tokens = {"NIFTY 50": "26000", "NIFTY BANK": "26009"}

        name_to_token = {item['name']: item['spot_token'] for item in mappings}

        result = {
            "NIFTY 50": {"index_token": index_tokens["NIFTY 50"], "constituents": []},
            "NIFTY BANK": {"index_token": index_tokens["NIFTY BANK"], "constituents": []}
        }

        for stock, weight in nifty_50_weights.items():
            if stock in name_to_token:
                result["NIFTY 50"]["constituents"].append({
                    "stock": stock,
                    "token": name_to_token[stock],
                    "weight": weight
                })

        for stock, weight in nifty_bank_weights.items():
            if stock in name_to_token:
                result["NIFTY BANK"]["constituents"].append({
                    "stock": stock,
                    "token": name_to_token[stock],
                    "weight": weight
                })

        return result

    def analyze_index_weightage(self, mappings):
        """Fetches LTPs for indices and heavyweights to calculate divergence."""
        heavyweights_data = self.get_index_heavyweights(mappings)

        all_tokens_to_fetch = set()

        # Add index tokens
        all_tokens_to_fetch.add(heavyweights_data["NIFTY 50"]["index_token"])
        all_tokens_to_fetch.add(heavyweights_data["NIFTY BANK"]["index_token"])

        # Add constituent tokens
        for index_name, data in heavyweights_data.items():
            for c in data["constituents"]:
                all_tokens_to_fetch.add(c["token"])

        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        payload = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": list(all_tokens_to_fetch)
            }
        }

        price_dict = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            res = response.json()
            if res.get('status') is True:
                for item in res.get('data', {}).get('fetched', []):
                    token = item['symbolToken']
                    ltp = float(item.get('ltp', 0))
                    close = float(item.get('close', 0))
                    pct_change = ((ltp - close) / close * 100) if close > 0 else 0
                    price_dict[token] = {"ltp": ltp, "pct_change": pct_change}

        # Calculate divergences
        final_result = {
            "indices": {},
            "constituents": []
        }

        for index_name, data in heavyweights_data.items():
            idx_token = data["index_token"]

            if idx_token not in price_dict:
                continue

            actual_move = price_dict[idx_token]["pct_change"]

            implied_move = 0.0
            total_weight_tracked = 0.0

            for c in data["constituents"]:
                token = c["token"]
                if token in price_dict:
                    weight = c["weight"]
                    pct = price_dict[token]["pct_change"]

                    # Normalize weight since we aren't tracking 100% of the index
                    total_weight_tracked += weight
                    implied_move += (weight * pct)

                    # Prevent duplicates in constituent list if stock belongs to both indices (e.g. HDFC Bank)
                    # We just add a flag to know which index it belongs to for the table
                    final_result["constituents"].append({
                        "index": index_name,
                        "stock": c["stock"],
                        "weight": weight,
                        "ltp": price_dict[token]["ltp"],
                        "pct_change": pct
                    })

            if total_weight_tracked > 0:
                implied_move = implied_move / total_weight_tracked
                divergence = actual_move - implied_move

                final_result["indices"][index_name] = {
                    "actual_move": actual_move,
                    "implied_move": implied_move,
                    "divergence": divergence
                }

        return final_result

    def calculate_rsi(self, series, period=14):
        """Calculates the Relative Strength Index (RSI)."""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not rsi.empty else 50.0

    def analyze_momentum_batch(self, mappings):
        """Analyzes a batch of stocks for Momentum Breakouts."""
        all_results = []

        # 1. Fetch real-time LTP and Volume for the batch
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"
        nse_tokens = [pair['spot_token'] for pair in mappings]

        payload = {
            "mode": "FULL",
            "exchangeTokens": {
                "NSE": nse_tokens
            }
        }

        rt_dict = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                for item in fetched_data:
                    rt_dict[item['symbolToken']] = {
                        "ltp": float(item.get('ltp', 0)),
                        "volume": float(item.get('tradeVolume', 0))
                    }

        # 2. Fetch historical data & calculate momentum stats
        for pair in mappings:
            token = pair['spot_token']
            stock_name = pair['name']

            rt_data = rt_dict.get(token)
            if not rt_data:
                continue

            ltp = rt_data['ltp']
            today_vol = rt_data['volume']

            # Fetch 60 days to ensure we have enough data for 50-day high and 14-day RSI
            df = self.get_historical_data(token, days_back=60)

            if df is not None and len(df) >= 20:
                # Calculate 50-Day High (or max available if less than 50 but > 20)
                high_50d = df['high'].rolling(window=min(50, len(df))).max().iloc[-1]

                # Calculate 20-Day Average Volume
                avg_vol_20d = df['volume'].rolling(window=20).mean().iloc[-1]

                # Calculate 14-Day RSI
                rsi_14d = self.calculate_rsi(df['close'], period=14)

                if pd.notna(high_50d) and pd.notna(avg_vol_20d) and avg_vol_20d > 0:

                    dist_to_high = ((ltp - high_50d) / high_50d) * 100
                    vol_pct = (today_vol / avg_vol_20d) * 100 if avg_vol_20d > 0 else 0

                    # Filtering criteria to only return interesting momentum setups
                    # e.g., Volume must be at least 150% of average, RSI must be strong but not exhausted
                    if vol_pct >= 150 and rsi_14d >= 55:

                        signal = "Developing"
                        if dist_to_high > 0 and rsi_14d < 85:
                            signal = "Strong Breakout"
                        elif rsi_14d >= 85:
                            signal = "Overextended"

                        all_results.append({
                            "stock": stock_name + "-EQ",
                            "ltp": ltp,
                            "high_50d": float(high_50d),
                            "dist_to_high": dist_to_high,
                            "today_vol": today_vol,
                            "avg_vol": float(avg_vol_20d),
                            "vol_pct": vol_pct,
                            "rsi": float(rsi_14d),
                            "signal": signal
                        })

        return all_results

    def get_exchange_arb_mapping(self):
        """Finds stocks that are listed on both NSE and BSE to look for arbitrage."""
        data = self.get_scrip_master()

        # Build dictionary of all BSE stocks mapping name -> token
        bse_dict = {
            item['name']: item['token']
            for item in data
            if item['exch_seg'] == 'BSE'
        }

        # Cross reference with NSE EQ stocks
        nse_list = [
            item for item in data
            if item['exch_seg'] == 'NSE' and item['symbol'].endswith('-EQ')
        ]

        mappings = []
        for nse_item in nse_list:
            name = nse_item['name']
            if name in bse_dict:
                mappings.append({
                    'name': name,
                    'nse_token': nse_item['token'],
                    'bse_token': bse_dict[name]
                })

        return mappings

    def analyze_exchange_arb_batch(self, mappings):
        """Analyzes a batch of stocks for NSE vs BSE price differences."""
        all_results = []

        # 1. Fetch real-time LTPs for both exchanges
        url = f"{self.base_url}rest/secure/angelbroking/market/v1/quote/"

        nse_tokens = [pair['nse_token'] for pair in mappings]
        bse_tokens = [pair['bse_token'] for pair in mappings]

        payload = {
            "mode": "LTP",
            "exchangeTokens": {
                "NSE": nse_tokens,
                "BSE": bse_tokens
            }
        }

        ltp_dict = {}
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") is True:
                fetched_data = result.get("data", {}).get("fetched", [])
                for item in fetched_data:
                    # Append exchange to token just in case there's an overlap in token IDs across exchanges
                    key = f"{item['exchange']}_{item['symbolToken']}"
                    ltp_dict[key] = float(item.get('ltp', 0))

        # 2. Calculate Spreads & Fetch Intraday History if spread is significant
        for pair in mappings:
            nse_key = f"NSE_{pair['nse_token']}"
            bse_key = f"BSE_{pair['bse_token']}"

            ltp_nse = ltp_dict.get(nse_key, 0)
            ltp_bse = ltp_dict.get(bse_key, 0)

            if ltp_nse == 0 or ltp_bse == 0:
                continue

            diff = abs(ltp_nse - ltp_bse)
            min_price = min(ltp_nse, ltp_bse)
            spread_pct = (diff / min_price) * 100

            duration_mins = 0

            # If there's a meaningful spread, fetch 1-minute historical data to see how long it has lasted
            if spread_pct >= 0.5:
                df_nse = self.get_historical_data(pair['nse_token'], exchange='NSE', interval='ONE_MINUTE', days_back=1)
                df_bse = self.get_historical_data(pair['bse_token'], exchange='BSE', interval='ONE_MINUTE', days_back=1)

                if df_nse is not None and df_bse is not None and not df_nse.empty and not df_bse.empty:
                    # Merge on datetime
                    df_merged = pd.merge(df_nse[['datetime', 'close']], df_bse[['datetime', 'close']], on='datetime', suffixes=('_nse', '_bse'))

                    # Sort newest to oldest
                    df_merged = df_merged.sort_values(by='datetime', ascending=False)

                    # Count contiguous minutes where spread > 0.3%
                    for _, row in df_merged.iterrows():
                        hist_diff = abs(row['close_nse'] - row['close_bse'])
                        hist_min = min(row['close_nse'], row['close_bse'])
                        hist_spread = (hist_diff / hist_min) * 100 if hist_min > 0 else 0

                        if hist_spread >= 0.3:
                            duration_mins += 1
                        else:
                            break # Broken the contiguous streak

            all_results.append({
                "stock": pair['name'],
                "ltp_nse": ltp_nse,
                "ltp_bse": ltp_bse,
                "spread_pct": spread_pct,
                "duration_mins": duration_mins
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