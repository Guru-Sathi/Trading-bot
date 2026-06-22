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

@app.route('/strategy/stat-arb')
def stat_arb():
    return render_template('stat_arb.html')

@app.route('/strategy/oi-divergence')
def oi_divergence():
    return render_template('oi_divergence.html')

@app.route('/strategy/index-weightage')
def index_weightage():
    return render_template('index_weightage.html')

@app.route('/watchlist')
def watchlist():
    return render_template('watchlist.html')

@app.route('/paper-trading')
def paper_trading():
    return render_template('paper_trading.html')

from paper_trading_bot import bot_instance

@app.route('/api/bot/start', methods=['POST'])
def api_bot_start():
    success = bot_instance.start()
    return jsonify({"status": "success" if success else "error", "message": "Bot started" if success else "Bot is already running"})

@app.route('/api/bot/stop', methods=['POST'])
def api_bot_stop():
    success = bot_instance.stop()
    return jsonify({"status": "success", "message": "Bot stopped"})

@app.route('/api/bot/status')
def api_bot_status():
    status = bot_instance.get_status()
    return jsonify({"status": "success", "data": status})

@app.route('/strategy/momentum')
def momentum():
    return render_template('momentum.html')

@app.route('/strategy/exchange-arb')
def exchange_arb():
    return render_template('exchange_arb.html')

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

@app.route('/api/mean-reversion/init')
def api_mean_reversion_init():
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API. Please check your .env credentials."}), 401

        import math
        # Mean reversion takes ~0.5s per stock for historical data.
        # We process in smaller batches (e.g., 5 stocks per batch) to keep UI responsive.
        total_batches = math.ceil(len(mappings) / 5.0)
        return jsonify({"status": "success", "total_batches": total_batches})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/mean-reversion/batch/<int:batch_id>')
def api_mean_reversion_batch(batch_id):
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        start_idx = batch_id * 5
        batch_mappings = mappings[start_idx:start_idx+5]

        reversion_stocks = ingestor.analyze_mean_reversion_batch(batch_mappings)
        return jsonify({"status": "success", "data": reversion_stocks})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

_stat_arb_pairs_cache = None

def get_stat_arb_pairs():
    global _stat_arb_pairs_cache
    if _stat_arb_pairs_cache is None:
        ingestor, mappings = get_ingestor_and_mappings()
        if ingestor and mappings:
            _stat_arb_pairs_cache = ingestor.get_predefined_pairs(mappings)
    return _stat_arb_pairs_cache

@app.route('/api/stat-arb/init')
def api_stat_arb_init():
    try:
        ingestor, _ = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API."}), 401

        pairs = get_stat_arb_pairs()
        import math
        # Stat arb is heavy (2 historical calls per pair), so batch size of 2 is safe
        total_batches = math.ceil(len(pairs) / 2.0)
        return jsonify({"status": "success", "total_batches": total_batches})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stat-arb/batch/<int:batch_id>')
def api_stat_arb_batch(batch_id):
    try:
        ingestor, _ = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        pairs = get_stat_arb_pairs()
        start_idx = batch_id * 2
        batch_pairs = pairs[start_idx:start_idx+2]

        arb_results = ingestor.analyze_stat_arb_batch(batch_pairs)
        return jsonify({"status": "success", "data": arb_results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/oi-divergence/analyze')
def api_oi_divergence_analyze():
    try:
        ingestor, _ = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API."}), 401

        # We perform one deep analysis of the NIFTY chain
        divergence_results = ingestor.analyze_nifty_oi_chain()
        return jsonify({"status": "success", "data": divergence_results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/index-weightage/analyze')
def api_index_weightage_analyze():
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        results = ingestor.analyze_index_weightage(mappings)
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/momentum/init')
def api_momentum_init():
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate with Angel One API."}), 401

        import math
        # Momentum requires historical data per stock (~0.5s per stock), so batch small
        total_batches = math.ceil(len(mappings) / 5.0)
        return jsonify({"status": "success", "total_batches": total_batches})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/momentum/batch/<int:batch_id>')
def api_momentum_batch(batch_id):
    try:
        ingestor, mappings = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        start_idx = batch_id * 5
        batch_mappings = mappings[start_idx:start_idx+5]

        results = ingestor.analyze_momentum_batch(batch_mappings)
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

_exchange_arb_cache = None

def get_exchange_arb_mappings():
    global _exchange_arb_cache
    if _exchange_arb_cache is None:
        ingestor, _ = get_ingestor_and_mappings()
        if ingestor:
            _exchange_arb_cache = ingestor.get_exchange_arb_mapping()
    return _exchange_arb_cache

@app.route('/api/exchange-arb/init')
def api_exchange_arb_init():
    try:
        ingestor, _ = get_ingestor_and_mappings()

        if not ingestor.auth_token:
            return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        mappings = get_exchange_arb_mappings()
        import math
        # We fetch 2 historical queries per stock if spread is detected, batching 25 is safe enough
        total_batches = math.ceil(len(mappings) / 25.0)
        return jsonify({"status": "success", "total_batches": total_batches})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/exchange-arb/batch/<int:batch_id>')
def api_exchange_arb_batch(batch_id):
    try:
        ingestor, _ = get_ingestor_and_mappings()

        if not ingestor.auth_token:
             return jsonify({"status": "error", "message": "Failed to authenticate."}), 401

        mappings = get_exchange_arb_mappings()
        start_idx = batch_id * 25
        batch_mappings = mappings[start_idx:start_idx+25]

        results = ingestor.analyze_exchange_arb_batch(batch_mappings)
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
