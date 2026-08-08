import json
import uuid
from datetime import datetime, timedelta

now = datetime.now()
trade_id = str(uuid.uuid4())

# Create a mock state with one active trade containing LTP history
state = {
    "balance": 990000.0,
    "active_positions": [],
    "trade_history": [
        {
            "id": trade_id,
            "symbol": "NIFTY25MAY22000CE",
            "type": "CE",
            "qty": 65,
            "entry_time": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "entry_price": 100.0,
            "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_price": 108.0,
            "pnl": 520.0,
            "reason": "Target Hit",
            "ltp_history": [
                {"time": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "ltp": 100.0},
                {"time": (now - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S"), "ltp": 98.0},
                {"time": (now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"), "ltp": 102.0},
                {"time": (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"), "ltp": 105.0},
                {"time": (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"), "ltp": 104.0},
                {"time": now.strftime("%Y-%m-%d %H:%M:%S"), "ltp": 108.0}
            ]
        }
    ]
}

with open('paper_trading_state.json', 'w') as f:
    json.dump(state, f)
