# context_api.py — computes untaken levels & sessions using TopstepX bars
# Run:  python context_api.py
# Env:  .env must include TOPSTEP_USER, TOPSTEP_API_KEY (and optional HOST/CONTEXT_PORT)

import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ---------- TIMEZONE (America/Chicago) ----------
# Windows often needs tzdata: pip install tzdata
try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    from datetime import timezone as _tz
    CT = _tz(timedelta(hours=-6))  # simple fallback (no DST)

# ---------- ENV ----------
load_dotenv()
API_BASE = os.getenv("API_BASE", "https://api.topstepx.com").rstrip("/")
TOPSTEP_USER = os.getenv("TOPSTEP_USER", "")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY", "")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("CONTEXT_PORT", "8092"))  # default 8092
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))

if not TOPSTEP_USER or not TOPSTEP_API_KEY:
    raise SystemExit("Missing TOPSTEP_USER or TOPSTEP_API_KEY in .env")

def log(msg, level="INFO"):
    levels = ["DEBUG", "INFO", "WARN", "ERROR"]
    try:
        if levels.index(level) >= levels.index(LOG_LEVEL):
            print(f"[{level}] {msg}")
    except Exception:
        print(f"[INFO] {msg}")

# ---------- HTTP (retry) ----------
def _http_post(url, headers=None, json_body=None, timeout=REQUEST_TIMEOUT_SECONDS):
    last_exc = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if r.status_code >= 400:
                try:
                    err = r.json()
                except Exception:
                    err = {"status": r.status_code, "text": r.text[:500]}
                raise requests.HTTPError(str(err))
            return r
        except Exception as e:
            last_exc = e
            log(f"POST {url} failed (attempt {attempt}/{RETRY_MAX}): {e}", "WARN")
            if attempt < RETRY_MAX:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc

# ---------- AUTH ----------
_session_token = None
_token_expiry_epoch = 0

def _auth_token():
    global _session_token, _token_expiry_epoch
    now = time.time()
    if _session_token and now < _token_expiry_epoch:
        return _session_token
    url = f"{API_BASE}/api/Auth/loginKey"
    payload = {"userName": TOPSTEP_USER, "apiKey": TOPSTEP_API_KEY}
    log("Authenticating with TopstepX ...")
    r = _http_post(url, headers={"Content-Type": "application/json", "accept": "text/plain"}, json_body=payload)
    js = r.json() or {}
    token = js.get("token")
    if not token:
        raise RuntimeError(f"Auth failed: {js}")
    _session_token = token
    _token_expiry_epoch = now + 23.5 * 3600  # ~24h
    log("Authenticated (token cached)")
    return _session_token

def _auth_headers():
    return {
        "Authorization": f"Bearer {_auth_token()}",
        "Content-Type": "application/json",
        "accept": "text/plain",
    }

# ---------- CONTRACT RESOLVE ----------
def _search_contract(symbol_text, live=False):
    """Return first contractId for a symbol search string (e.g., 'MES')."""
    url = f"{API_BASE}/api/Contract/search"
    body = {
        "searchText": str(symbol_text or "").strip(),
        "live": bool(live),
        "onlyTradable": False,
        "limit": 25
    }
    r = _http_post(url, headers=_auth_headers(), json_body=body)
    js = r.json() or {}
    items = js.get("contracts") or js.get("items") or js.get("results") or []
    if isinstance(js, list):
        items = js
    return (items[0] or {}).get("id") if items else None

# ---------- BARS ----------
# unit enum (per API): 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month
# (unit, unitNumber)
INTERVAL_MAP = {
    "1m":  (2, 1),
    "2m":  (2, 2),
    "5m":  (2, 5),
    "15m": (2, 15),
    "30m": (2, 30),
    "1h":  (3, 1),
    "2h":  (3, 2),
    "4h":  (3, 4),   # <— FIXED: use Hour(3) x 4 instead of "Minute"/240
    "1d":  (4, 1),
}

def _retrieve_bars(contract_id, start_iso, end_iso, interval, limit=None, include_partial=False, live=False):
    if interval not in INTERVAL_MAP:
        raise ValueError(f"Unsupported tf '{interval}'")
    unit, unit_number = INTERVAL_MAP[interval]
    url = f"{API_BASE}/api/History/retrieveBars"
    body = {
        "contractId": contract_id,
        "live": bool(live),
        "startTime": start_iso,
        "endTime": end_iso,
        "unit": unit,                # <— numeric enum
        "unitNumber": unit_number,   # <— integer
        "limit": int(limit) if limit else 20000,
        "includePartialBar": bool(include_partial),
    }
    r = _http_post(url, headers=_auth_headers(), json_body=body, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
    js = r.json() or {}
    bars = js.get("bars") or []
    out = []
    for b in bars:
        out.append({
            "t": b.get("t") or b.get("time") or b.get("timestamp"),
            "o": b.get("o") or b.get("open"),
            "h": b.get("h") or b.get("high"),
            "l": b.get("l") or b.get("low"),
            "c": b.get("c") or b.get("close"),
            "v": b.get("v") or b.get("volume") or 0,
        })
    return out

# ---------- LOGIC: swings, breaches, FVG ----------
def _iso_z(dt):
    if isinstance(dt, str):
        s = dt.strip()
        return s if s.endswith("Z") or (len(s) >= 6 and s[-6] in "+-") else (s + "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _swing_points(bars, left=2, right=2):
    """Return swing highs/lows indices using L/R neighbors."""
    swings = []
    n = len(bars)
    for i in range(left, n - right):
        try:
            hi = all(bars[i]["h"] >= bars[i - k]["h"] for k in range(1, left + 1)) and \
                 all(bars[i]["h"] >= bars[i + k]["h"] for k in range(1, right + 1))
            lo = all(bars[i]["l"] <= bars[i - k]["l"] for k in range(1, left + 1)) and \
                 all(bars[i]["l"] <= bars[i + k]["l"] for k in range(1, right + 1))
        except Exception:
            continue
        if hi:
            swings.append({"type": "swing_high", "idx": i, "price": bars[i]["h"], "time": bars[i]["t"]})
        if lo:
            swings.append({"type": "swing_low", "idx": i, "price": bars[i]["l"], "time": bars[i]["t"]})
    return swings

def _breached_after(bars, level_price, start_idx, breach_type):
    """Check if a level is taken out AFTER it forms."""
    for j in range(start_idx + 1, len(bars)):
        h = bars[j]["h"]; l = bars[j]["l"]
        if breach_type == "swing_high" and h is not None and h > level_price + 1e-9:
            return True, bars[j]["t"]
        if breach_type == "swing_low" and l is not None and l < level_price - 1e-9:
            return True, bars[j]["t"]
    return False, None

def _find_fvgs_15m(bars15):
    """ICT 3-candle FVGs on 15m. Up-gap: L(n) > H(n-2); Down-gap: H(n) < L(n-2)."""
    gaps = []
    for i in range(2, len(bars15)):
        a = bars15[i-2]; c = bars15[i]
        ah = a.get("h"); al = a.get("l")
        ch = c.get("h"); cl = c.get("l")
        if ah is None or al is None or ch is None or cl is None:
            continue
        # bullish FVG
        if cl > ah:
            gaps.append({
                "type": "bullish",
                "bar_index": i,
                "start": ah,
                "end": cl,
                "formed_at": c.get("t"),
                "filled": False,
                "filled_at": None
            })
        # bearish FVG
        if ch < al:
            gaps.append({
                "type": "bearish",
                "bar_index": i,
                "start": ch,
                "end": al,
                "formed_at": c.get("t"),
                "filled": False,
                "filled_at": None
            })
    return gaps

def _mark_fvg_fills(gaps, subsequent_bars):
    for g in gaps:
        for b in subsequent_bars:
            h = b.get("h"); l = b.get("l")
            if h is None or l is None:
                continue
            if l <= g["end"] and h >= g["start"]:
                g["filled"] = True
                g["filled_at"] = b.get("t")
                break
    return gaps

# ---------- APP / ROUTES ----------
app = Flask(__name__)
CORS(app)

@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "apiBase": API_BASE,
        "user": TOPSTEP_USER,
    })

@app.get("/api/context/levels")
def levels():
    """
    Query params:
      - symbol or contract (one required)
      - asOf: ISO8601 UTC (default now)
      - live: true/false (optional; affects contract search & bars)
    Returns JSON with:
      - h4_untaken_levels (last 5 days of 4h bars)
      - m15_sessions (Asian & London highs/lows for the asOf CT day)
      - m15_prevclose_swings (15m swings since previous 3:00pm CT)
      - m15_open_fvgs (unfilled 15m fair value gaps)
    """
    sym = request.args.get("symbol")
    con = request.args.get("contract")
    live_flag = str(request.args.get("live", "false")).lower() in ("1", "true", "yes", "y")
    as_of = request.args.get("asOf")

    if not sym and not con:
        return jsonify({"error": "Provide ?symbol= or ?contract="}), 400

    if not con:
        con = _search_contract(sym, live=live_flag)
        if not con:
            return jsonify({"error": f"No contract found for symbol '{sym}'"}), 404

    now_utc = datetime.now(timezone.utc)
    if as_of:
        try:
            asof = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return jsonify({"error": "Bad asOf format; use ISO8601 like 2025-09-19T15:00:00Z"}), 400
    else:
        asof = now_utc

    # --- 4h bars (Hour x 4), last 5 days window ---
    start_h4_utc = asof - timedelta(days=5, hours=1)
    bars_h4 = _retrieve_bars(con, _iso_z(start_h4_utc), _iso_z(asof), "4h", live=live_flag)
    swings = _swing_points(bars_h4, left=2, right=2)
    h4_levels = []
    for s in swings:
        taken, taken_at = _breached_after(bars_h4, s["price"], s["idx"], s["type"])
        if not taken:
            h4_levels.append({
                "type": s["type"],
                "price": s["price"],
                "formed_at": s["time"],
                "untaken": True
            })

    # --- 15m bars for sessions + prev close ---
    asof_ct = asof.astimezone(CT)
    day_ct = asof_ct.date()

    # CT session windows
    asian_start = datetime(day_ct.year, day_ct.month, day_ct.day, 18, 0, tzinfo=CT) - timedelta(days=1)
    asian_end   = datetime(day_ct.year, day_ct.month, day_ct.day, 2, 0, tzinfo=CT)
    london_start = datetime(day_ct.year, day_ct.month, day_ct.day, 2, 0, tzinfo=CT)
    london_end   = datetime(day_ct.year, day_ct.month, day_ct.day, 7, 30, tzinfo=CT)
    prev_close_ct = datetime(day_ct.year, day_ct.month, day_ct.day, 15, 0, tzinfo=CT) - timedelta(days=1)

    m15_fetch_start = asian_start - timedelta(hours=2)
    bars_15 = _retrieve_bars(con, _iso_z(m15_fetch_start), _iso_z(asof), "15m", live=live_flag)

    def _slice(bars, start_ct, end_ct):
        out = []
        su = start_ct.astimezone(timezone.utc)
        eu = end_ct.astimezone(timezone.utc)
        for b in bars:
            try:
                t = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue
            if su <= t <= eu:
                out.append(b)
        return out

    def _hl(bars):
        if not bars:
            return None
        highs = [b["h"] for b in bars if b["h"] is not None]
        lows  = [b["l"] for b in bars if b["l"] is not None]
        if not highs or not lows:
            return None
        hi = max(highs); lo = min(lows)
        when_hi = next((b["t"] for b in bars if b["h"] == hi), None)
        when_lo = next((b["t"] for b in bars if b["l"] == lo), None)
        return {"high": hi, "high_at": when_hi, "low": lo, "low_at": when_lo, "count": len(bars)}

    asian_15  = _slice(bars_15, asian_start, asian_end)
    london_15 = _slice(bars_15, london_start, london_end)
    asian_hl  = _hl(asian_15)
    london_hl = _hl(london_15)

    # swings since prev close on 15m
    since_prev_close = _slice(bars_15, prev_close_ct, asof_ct)
    m15_swings = _swing_points(since_prev_close, left=2, right=2)
    m15_since_prevclose_levels = [{"type": s["type"], "price": s["price"], "formed_at": s["time"]} for s in m15_swings]

    # FVGs on all 15m up to asOf; mark fills with subsequent bars
    fvgs_all = _find_fvgs_15m(bars_15)
    for g in fvgs_all:
        tail = bars_15[g["bar_index"] + 1:] if g["bar_index"] + 1 < len(bars_15) else []
        _mark_fvg_fills([g], tail)
    open_fvgs = [g for g in fvgs_all if not g["filled"]]

    return jsonify({
        "asOf": _iso_z(asof),
        "contractId": con,
        "h4_untaken_levels": h4_levels,
        "m15_sessions": {
            "asian": asian_hl,
            "london": london_hl,
            "asian_window_ct": {"start": asian_start.isoformat(), "end": asian_end.isoformat()},
            "london_window_ct": {"start": london_start.isoformat(), "end": london_end.isoformat()},
        },
        "m15_prevclose_swings": {
            "since_prev_close_ct": {"start": prev_close_ct.isoformat(), "end": asof_ct.isoformat()},
            "levels": m15_since_prevclose_levels
        },
        "m15_open_fvgs": open_fvgs
    })

if __name__ == "__main__":
    log(f"Starting CONTEXT server on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=(LOG_LEVEL == "DEBUG"))
