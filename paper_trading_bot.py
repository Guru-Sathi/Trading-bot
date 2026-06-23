import time
import json
import os
import threading
from datetime import datetime
from angel_data_ingestor import AngelDataIngestor
import requests

STATE_FILE = "paper_trading_state.json"
INITIAL_BALANCE = 1000000.0  # 10 Lakhs

class PaperTradingBot:
    def __init__(self):
        self.ingestor = AngelDataIngestor()
        self.state = self.load_state()
        self.running = False
        self.thread = None

    def load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        return {
            "balance": INITIAL_BALANCE,
            "active_positions": [], # [{ "id", "symbol", "token", "entry_price", "qty", "entry_time", "type", "ltp_history": [] }]
            "trade_history": []
        }

    def save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=4)

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            return True
        return False

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        return True

    def get_status(self):
        # Calculate MTM (Mark to Market) for active positions
        mtm = 0.0
        positions_with_ltp = []
        if self.state["active_positions"]:
             tokens = [p["token"] for p in self.state["active_positions"]]
             ltp_data = self._fetch_ltps(tokens)
             for p in self.state["active_positions"]:
                 ltp = ltp_data.get(p["token"], p["entry_price"])
                 p_copy = dict(p)
                 p_copy["ltp"] = ltp
                 pnl = (ltp - p["entry_price"]) * p["qty"]
                 p_copy["unrealized_pnl"] = pnl
                 # Don't send full history in polling
                 p_copy.pop("ltp_history", None)
                 mtm += pnl
                 positions_with_ltp.append(p_copy)

        # Clean history for polling (remove big arrays)
        clean_history = []
        for h in self.state["trade_history"][-50:]:
            h_copy = dict(h)
            h_copy.pop("ltp_history", None)
            clean_history.append(h_copy)

        return {
            "running": self.running,
            "balance": self.state["balance"],
            "mtm": mtm,
            "total_value": self.state["balance"] + mtm,
            "active_positions": positions_with_ltp,
            "trade_history": clean_history
        }

    def get_trade_details(self, trade_id):
        # Search in active positions
        for p in self.state["active_positions"]:
            if p.get("id") == trade_id:
                return p
        # Search in history
        for h in self.state["trade_history"]:
            if h.get("id") == trade_id:
                return h
        return None

    def _fetch_ltps(self, tokens):
        # Fetch LTP for given NFO tokens
        if not self.ingestor.auth_token:
            return {}

        url = f"{self.ingestor.base_url}rest/secure/angelbroking/market/v1/quote/"
        payload = {
            "mode": "LTP",
            "exchangeTokens": {
                "NFO": tokens
            }
        }
        try:
            res = requests.post(url, headers=self.ingestor.headers, json=payload).json()
            ltp_map = {}
            if res.get("status") and res.get("data", {}).get("fetched"):
                for item in res["data"]["fetched"]:
                    ltp_map[item["symbolToken"]] = float(item["ltp"])
            return ltp_map
        except Exception:
            return {}

    def _run_loop(self):
        print("Bot started.")
        while self.running:
            try:
                # 1. Manage existing positions
                self._manage_positions()

                # 2. Look for new entry if no active positions (to keep it simple, 1 trade at a time)
                if len(self.state["active_positions"]) == 0:
                    self._scan_for_entry()

            except Exception as e:
                print(f"Bot error: {e}")

            # Sleep to avoid hitting API too hard
            time.sleep(30)

    def _manage_positions(self):
        if not self.state["active_positions"]:
            return

        tokens = [p["token"] for p in self.state["active_positions"]]
        ltps = self._fetch_ltps(tokens)

        to_remove = []
        state_changed = False
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for p in self.state["active_positions"]:
            ltp = ltps.get(p["token"])
            if not ltp:
                continue

            # Store the tick in history
            if "ltp_history" not in p:
                p["ltp_history"] = []
            p["ltp_history"].append({"time": current_time, "ltp": ltp})
            state_changed = True

            entry = p["entry_price"]
            # Simple Target: 10%, Stop Loss: 5%
            target = entry * 1.10
            sl = entry * 0.95

            if ltp >= target or ltp <= sl:
                # Close position
                profit = (ltp - entry) * p["qty"]
                self.state["balance"] += (entry * p["qty"]) + profit # Return capital + profit

                history_record = {
                    "id": p.get("id"),
                    "symbol": p["symbol"],
                    "type": p["type"],
                    "qty": p["qty"],
                    "entry_time": p["entry_time"],
                    "entry_price": entry,
                    "exit_time": current_time,
                    "exit_price": ltp,
                    "pnl": profit,
                    "reason": "Target Hit" if ltp >= target else "Stop Loss Hit",
                    "ltp_history": p["ltp_history"]
                }
                self.state["trade_history"].append(history_record)
                to_remove.append(p)
                print(f"Closed {p['symbol']} at {ltp}. PnL: {profit}")

        for p in to_remove:
            self.state["active_positions"].remove(p)

        if state_changed or to_remove:
            self.save_state()

    def _scan_for_entry(self):
        if not self.ingestor.auth_token:
            return

        print("Scanning for new entry...")
        # Deep analysis of NIFTY OI Chain
        try:
            data = self.ingestor.analyze_nifty_oi_chain()

            # Look for strong signals
            buy_ce = False
            buy_pe = False
            target_strike = None

            # For paper trading simulation, let's use the extreme signals or strong divergence
            if "🔥" in data.get("sentiment", "") or "Bullish Breakout" in data.get("sentiment", ""):
                buy_ce = True
                target_strike = data["atm_strike"] # Buy ATM
            elif "⚠️" in data.get("sentiment", "") or "Bearish Breakdown" in data.get("sentiment", ""):
                buy_pe = True
                target_strike = data["atm_strike"]

            if not buy_ce and not buy_pe:
                return

            # Find the token for the target option
            # We need to re-fetch the chain mappings to find the specific token
            expiry_str, strikes_dict = self.ingestor.get_nifty_options_chain()

            if target_strike not in strikes_dict:
                return

            opt_data = strikes_dict[target_strike]
            if buy_ce and opt_data["CE"]:
                token = opt_data["CE"]["token"]
                symbol = opt_data["CE"]["symbol"]
                opt_type = "CE"
            elif buy_pe and opt_data["PE"]:
                token = opt_data["PE"]["token"]
                symbol = opt_data["PE"]["symbol"]
                opt_type = "PE"
            else:
                return

            # Fetch LTP of this option to buy
            ltps = self._fetch_ltps([token])
            if token not in ltps:
                return

            ltp = ltps[token]
            if ltp <= 0: return

            # Buy 1 lot (Nifty lot size is 65)
            qty = 65
            cost = ltp * qty

            if self.state["balance"] >= cost:
                import uuid
                self.state["balance"] -= cost
                new_pos = {
                    "id": str(uuid.uuid4()),
                    "symbol": symbol,
                    "token": token,
                    "entry_price": ltp,
                    "qty": qty,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "type": opt_type,
                    "ltp_history": [{"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ltp": ltp}]
                }
                self.state["active_positions"].append(new_pos)
                self.save_state()
                print(f"Opened {opt_type} position: {symbol} at {ltp}")

        except Exception as e:
            print(f"Scan error: {e}")

bot_instance = PaperTradingBot()
