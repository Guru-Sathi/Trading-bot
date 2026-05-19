from flask import Flask, render_template, jsonify
from angel_data_ingestor import AngelDataIngestor

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/strategy/cash-fut-arb')
def cash_fut_arb():
    return render_template('strategy.html')

@app.route('/strategy/mean-reversion')
def mean_reversion():
    return render_template('mean_reversion.html')

@app.route('/api/mean-reversion')
def api_mean_reversion():
    # Mock data for frontend visualization
    mock_data = [
        {"stock": "RELIANCE-EQ", "ltp": 2450.50, "sma": 2550.00, "std_dev": 35.5, "z_score": -2.80},
        {"stock": "TCS-EQ", "ltp": 3800.00, "sma": 3650.00, "std_dev": 60.0, "z_score": 2.50},
        {"stock": "HDFCBANK-EQ", "ltp": 1600.00, "sma": 1580.00, "std_dev": 25.0, "z_score": 0.80},
        {"stock": "INFY-EQ", "ltp": 1420.00, "sma": 1500.00, "std_dev": 20.0, "z_score": -4.00},
        {"stock": "ITC-EQ", "ltp": 450.00, "sma": 445.00, "std_dev": 10.0, "z_score": 0.50},
        {"stock": "SBIN-EQ", "ltp": 620.00, "sma": 590.00, "std_dev": 15.0, "z_score": 2.00},
        {"stock": "ICICIBANK-EQ", "ltp": 980.00, "sma": 1010.00, "std_dev": 12.0, "z_score": -2.50},
        {"stock": "BHARTIARTL-EQ", "ltp": 1150.00, "sma": 1100.00, "std_dev": 22.0, "z_score": 2.27},
    ]
    return jsonify({"status": "success", "data": mock_data})

# Global state for caching
_ingestor = None
_stock_mappings = None

def get_ingestor_and_mappings():
    global _ingestor, _stock_mappings
    if _ingestor is None:
        _ingestor = AngelDataIngestor()
    if _stock_mappings is None and _ingestor.auth_token:
        _stock_mappings = _ingestor.get_spot_fut_mapping()
    return _ingestor, _stock_mappings

@app.route('/api/cash-fut-arb/init')
def api_cash_fut_arb_init():
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API. Please check your .env credentials."}), 401

        import math
        total_batches = math.ceil(len(mappings) / 25.0)
        return jsonify({"status": "success", "total_batches": total_batches})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cash-fut-arb/batch/<int:batch_id>')
def api_cash_fut_arb_batch(batch_id):
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        start_idx = batch_id * 25
        batch_mappings = mappings[start_idx:start_idx+25]

        premium_stocks = ingestor.fetch_and_calculate_premiums(batch_mappings)
        return jsonify({"status": "success", "data": premium_stocks})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
