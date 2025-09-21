# analytics/main.py
import os
import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from flask import Flask, request, jsonify
from flask_cors import CORS

# ---- CONFIG ----
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8091"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DATA_API_BASE = os.getenv("DATA_API_BASE", "http://localhost:8090")
AUTO_LIVE_FRESH_MINUTES = int(os.getenv("AUTO_LIVE_FRESH_MINUTES", "10"))

# ---- TIMEZONE for Context API ----
try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone(timedelta(hours=-6)) # Fallback without DST

# ---- FLASK APP ----
app = Flask(__name__)
CORS(app)

# ---- INTERNAL DATA FETCHING ----
def get_bars_from_data_service(params: dict) -> list:
    """Fetches bars from our internal data service, forwarding user params."""
    try:
        url = f"{DATA_API_BASE}/api/bars"
        # Forward the original query parameters directly to the data service
        response = requests.get(url, params=params, timeout=60)
        
        # If the data service returns an error (like 404), return an empty list
        if response.status_code >= 400:
            print(f"[WARN] Data service returned status {response.status_code}: {response.text[:200]}")
            return []
            
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not fetch bars from data service: {e}")
        return []

# ---- SHARED HELPERS (from all original files) ----
def parse_iso_z(s: str) -> datetime:
    if s.endswith("Z"): s = s[:-1]
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

def fmt_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def cme_weekend_status(dt_utc: datetime) -> Dict[str, Any]:
    weekday = dt_utc.weekday()
    base = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    days_to_fri = (4 - weekday) % 7
    friday = base + timedelta(days=days_to_fri)
    if dt_utc < friday: friday -= timedelta(days=7)
    fri_close = friday.replace(hour=20, minute=0)
    sun_open = fri_close + timedelta(days=2, hours=2)
    closed = (dt_utc > fri_close) and (dt_utc < sun_open)
    return {"closed": closed, "fridayClose": fri_close, "sundayOpen": sun_open}

def _gx(b: Dict, short: str, long: str): return b.get(short, b.get(long))
def _f(x: Any) -> float: return float(x) if x is not None else 0.0

# ---- VWAP LOGIC ----
def compute_vwap(bars: List[Dict]) -> Dict:
    cum_pv = cum_v = 0.0
    series = []
    for b in bars:
        h, l, c, v = _f(_gx(b,"h","high")), _f(_gx(b,"l","low")), _f(_gx(b,"c","close")), _f(_gx(b,"v","volume"))
        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_v += v
        vwap_val = (cum_pv / cum_v) if cum_v > 0 else 0.0
        series.append({"time": _gx(b,"t","time"), "vwap": vwap_val, "typicalPrice": tp, "volume": v, "close": c})
    return {"final": series[-1]["vwap"] if series else 0.0, "series": series, "count": len(series)}

# ---- INDICATORS LOGIC ----
def series_close(bars: List[Dict]) -> List[float]: return [_f(_gx(b, "c", "close")) for b in bars]

def sma(values: List[float], length: int) -> List[float | None]:
    out, window_sum = [], 0.0
    for i, v in enumerate(values):
        window_sum += v
        if i >= length: window_sum -= values[i - length]
        out.append(window_sum / length if i >= length - 1 else None)
    return out

def ema(values: List[float], length: int) -> List[float | None]:
    if not values or length <= 0: return [None] * len(values)
    out, k, ema_val = [], 2.0 / (length + 1.0), None
    for i, v in enumerate(values):
        if ema_val is None:
            if i >= length - 1: ema_val = sum(values[:length]) / float(length)
            else: out.append(None); continue
        ema_val = (v - ema_val) * k + ema_val
        out.append(ema_val)
    return out

def rsi(values: List[float], length: int) -> List[float | None]:
    if not values or length <= 0: return [None] * len(values)
    deltas = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    out, avg_gain, avg_loss = [None] * (length), 0.0, 0.0
    for i in range(len(deltas) - length + 1):
        if i == 0:
            avg_gain = sum(gains[:length]) / length
            avg_loss = sum(losses[:length]) / length
        else:
            avg_gain = (avg_gain * (length - 1) + gains[i + length - 1]) / length
            avg_loss = (avg_loss * (length - 1) + losses[i + length - 1]) / length
        rs = avg_gain / avg_loss if avg_loss != 0 else float('inf')
        out.append(100 - (100 / (1 + rs)))
    return out

# ---- CONTEXT LOGIC ----
def _swing_points(bars, left=2, right=2):
    swings, n = [], len(bars)
    for i in range(left, n - right):
        try:
            is_high = all(bars[i]["h"] >= bars[i-k]["h"] for k in range(1,left+1)) and all(bars[i]["h"] > bars[i+k]["h"] for k in range(1,right+1))
            is_low = all(bars[i]["l"] <= bars[i-k]["l"] for k in range(1,left+1)) and all(bars[i]["l"] < bars[i+k]["l"] for k in range(1,right+1))
            if is_high: swings.append({"type": "swing_high", "idx": i, "price": bars[i]["h"], "time": bars[i]["t"]})
            if is_low: swings.append({"type": "swing_low", "idx": i, "price": bars[i]["l"], "time": bars[i]["t"]})
        except (TypeError, KeyError): continue # Skip if bars have missing h/l
    return swings

def _breached_after(bars, level_price, start_idx, breach_type):
    for j in range(start_idx + 1, len(bars)):
        try:
            if breach_type == "swing_high" and bars[j]["h"] > level_price: return True
            if breach_type == "swing_low" and bars[j]["l"] < level_price: return True
        except (TypeError, KeyError): continue
    return False

# ---- API ROUTES ----
@app.get("/health")
def health(): return jsonify({"status": "ok", "service": "analytics"})

@app.get("/api/vwap")
@app.get("/api/indicators") # Both routes share much of the same initial logic
def api_analytics():
    params = request.args.to_dict()
    if not (params.get("symbol") or params.get("contract")):
        return jsonify({"error": "Symbol or contract is required"}), 400
    
    # --- Time Window & Weekend Guard Logic ---
    start, end, mode = params.get("start"), params.get("end"), params.get("mode")
    now = datetime.now(timezone.utc)
    if not start or not end:
        end_dt = now
        start_dt = end_dt - timedelta(days=7) if mode == "weekly" else now.replace(hour=13, minute=30, second=0) - timedelta(days=1 if now.hour < 13 else 0)
        params["start"], params["end"] = fmt_iso_z(start_dt), fmt_iso_z(end_dt)
    
    req_start_dt, req_end_dt = parse_iso_z(params["start"]), parse_iso_z(params["end"])
    status = cme_weekend_status(req_end_dt)
    window_adjusted = False
    
    guard_on = params.get("guard", "on").lower() != "off"
    auto_window = params.get("auto_window", "false").lower() == "true"
    
    if status["closed"] and guard_on and not auto_window:
        return jsonify({"error": "Market likely closed (CME weekend gap).", "hint": "Set auto_window=true or guard=off."}), 409
    
    if auto_window and status["closed"]:
        duration = req_end_dt - req_start_dt
        new_end = min(status["fridayClose"], req_end_dt)
        new_start = new_end - duration
        params["start"], params["end"] = fmt_iso_z(new_start), fmt_iso_z(new_end)
        window_adjusted = True

    # --- Fetch Data ---
    bars = get_bars_from_data_service(params)
    if not bars: return jsonify({"error": "No bars returned from data service", "params_sent": params}), 404

    # --- Process based on which endpoint was called ---
    payload = {
        "requested_params": request.args.to_dict(),
        "effective_params": params,
        "windowAdjustedForClosure": window_adjusted,
        "bars_count": len(bars)
    }

    if "/api/vwap" in request.path:
        vwap_results = compute_vwap(bars)
        payload.update(vwap_results)

    if "/api/indicators" in request.path:
        closes = series_close(bars)
        for key in ["sma", "ema", "rsi"]:
            if val := params.get(key):
                try:
                    length = int(val)
                    if length > 0 and length < len(closes):
                        if key == 'sma': series = sma(closes, length)
                        if key == 'ema': series = ema(closes, length)
                        if key == 'rsi': series = rsi(closes, length)
                        # Find the last valid (non-None) value in the series
                        payload[key] = next((item for item in reversed(series) if item is not None), None)
                except (ValueError, TypeError):
                    continue

    return jsonify(payload)

@app.get("/api/context")
def api_context():
    params = request.args.to_dict()
    if not (params.get("symbol") or params.get("contract")):
        return jsonify({"error": "Symbol or contract is required"}), 400

    now_utc = datetime.now(timezone.utc)
    asof = parse_iso_z(params.get("asOf", fmt_iso_z(now_utc)))

    # Fetch 4h bars for the last 5 days for swing levels
    h4_params = params.copy()
    h4_params["tf"] = "4h"
    h4_params["start"] = fmt_iso_z(asof - timedelta(days=5))
    h4_params["end"] = fmt_iso_z(asof)
    bars_h4 = get_bars_from_data_service(h4_params)
    
    h4_levels = []
    if bars_h4:
        swings = _swing_points(bars_h4)
        for s in swings:
            if not _breached_after(bars_h4, s["price"], s["idx"], s["type"]):
                h4_levels.append({"type": s["type"], "price": s["price"], "formed_at": s["time"], "untaken": True})

    # You can add the 15m session logic here in the same way,
    # by making another call to get_bars_from_data_service with tf=15m and the right window.
    
    return jsonify({
        "asOf": fmt_iso_z(asof),
        "params_sent": params,
        "h4_untaken_levels": h4_levels
    })

if __name__ == "__main__":
    print(f"Starting ANALYTICS server on http://{HOST}:{PORT}")
    print(f"--> Internal Data Service is at: {DATA_API_BASE}")
    app.run(host=HOST, port=PORT, debug=(LOG_LEVEL == "DEBUG"))