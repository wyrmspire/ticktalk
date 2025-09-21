# data/main.py
import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# Import the centralized client functions
from shared import client

# ---- CONFIG ----
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8090"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---- FLASK APP ----
app = Flask(__name__)
CORS(app)

# ---- HELPERS ----
def _normalize_symbol(s: str) -> str:
    s = (s or "").strip()
    return s[:-2] if s.endswith("=F") else s

def _to_naive_utc_str(iso_str: str) -> str:
    try:
        s = (iso_str or "").strip()
        if not s: return s
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str

def _bars_to_records(bars: list) -> list:
    return [{
        "time": _to_naive_utc_str(b.get("t")), "open": b.get("o"), "high": b.get("h"),
        "low": b.get("l"), "close": b.get("c"), "volume": b.get("v", 0),
    } for b in bars or []]

# ---- API ROUTES ----
@app.get("/api/bars")
def api_bars():
    try:
        contract = request.args.get("contract")
        symbol = request.args.get("symbol")
        tf = request.args.get("tf", "15m")
        start = request.args.get("start")
        end = request.args.get("end")
        limit = request.args.get("limit")
        live = request.args.get("live", "false").lower() == "true"

        if not (contract or symbol): return jsonify({"error": "Provide ?contract or ?symbol"}), 400
        if not (start and end): return jsonify({"error": "Provide ?start and ?end"}), 400
        if tf not in client.INTERVAL_MAP: return jsonify({"error": f"Unsupported tf '{tf}'"}), 400

        contract_id = contract.strip() if contract else client.resolve_contract_id(_normalize_symbol(symbol), live=live)
        
        bars = client.retrieve_bars(
            contract_id, start, end, tf, live=live,
            limit=int(limit) if (limit and str(limit).isdigit()) else None,
        )
        records = _bars_to_records(bars)
        # We return the raw bars list, as this is an internal API now
        return jsonify(bars)

    except requests.HTTPError as e:
        client.log(f"HTTP error in data service: {e}", "ERROR")
        return jsonify({"error": "Upstream API error", "detail": str(e)}), 502
    except Exception as e:
        client.log(f"Unhandled error in data service: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.get("/api/trades")
def api_trades():
    # This logic is simplified from your original file, using the shared client
    try:
        accounts = client.retrieve_accounts()
        default_acc = next((a for a in accounts if "PRACTICE" in str(a.get("name","")).upper()), accounts[0] if accounts else None)
        default_id = default_acc.get("id") if default_acc else 0

        account_id = int(request.args.get("accountId", default_id))
        start = request.args.get("start")
        end = request.args.get("end")
        symbol_id = request.args.get("contract") or request.args.get("symbolId")
        limit = request.args.get("limit")

        if not account_id: return jsonify({"error": "No account available."}), 400
        if not start or not end: return jsonify({"error": "Provide both ?start and ?end"}), 400
        
        trades = client.retrieve_trades(
            account_id=account_id, start_iso=start, end_iso=end,
            symbol_id=symbol_id, limit=int(limit) if (limit and str(limit).isdigit()) else None
        )
        return jsonify({"accountId": account_id, "count": len(trades), "trades": trades})
    except Exception as e:
        client.log(f"Trades error: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "data"})

if __name__ == "__main__":
    client.log(f"Starting DATA server on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=(LOG_LEVEL == "DEBUG"))