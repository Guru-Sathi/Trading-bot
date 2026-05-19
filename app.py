from flask import Flask, render_template, jsonify
from angel_data_ingestor import AngelDataIngestor

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/strategy/cash-fut-arb')
def cash_fut_arb():
    return render_template('strategy.html')

@app.route('/api/cash-fut-arb')
def api_cash_fut_arb():
    try:
        ingestor = AngelDataIngestor()

        # If not authenticated (e.g. no .env), we should return a clear error
        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API. Please check your .env credentials."}), 401

        stock_mappings = ingestor.get_spot_fut_mapping()
        premium_stocks = ingestor.fetch_and_calculate_premiums(stock_mappings)

        # Sort by Gross_Profit(₹) descending
        if premium_stocks:
            premium_stocks = sorted(premium_stocks, key=lambda x: x.get('Gross_Profit(₹)', 0), reverse=True)

        return jsonify({"status": "success", "data": premium_stocks})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
