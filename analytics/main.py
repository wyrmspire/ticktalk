# ---------- FLASK APP ----------
from flask import Flask, request, jsonify
from flask_cors import CORS
app = Flask(__name__)
CORS(app)
# ---------- TIMEZONE (America/Chicago) ----------
try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    from datetime import timezone as _tz
    CT = _tz(timedelta(hours=-6))

# ---------- LOGIC: swings, breaches, FVG ----------
def _iso_z(dt):
    if isinstance(dt, str):
        s = dt.strip()
        return s if s.endswith("Z") or (len(s) >= 6 and s[-6] in "+-") else (s + "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _swing_points(bars, left=2, right=2):
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
    for j in range(start_idx + 1, len(bars)):
        h = bars[j]["h"]; l = bars[j]["l"]
        if breach_type == "swing_high" and h is not None and h > level_price + 1e-9:
            return True, bars[j]["t"]
        if breach_type == "swing_low" and l is not None and l < level_price - 1e-9:
            return True, bars[j]["t"]
    return False, None

def _find_fvgs_15m(bars15):
    gaps = []
    for i in range(2, len(bars15)):
        a = bars15[i-2]; c = bars15[i]
        ah = a.get("h"); al = a.get("l")
        ch = c.get("h"); cl = c.get("l")
        if ah is None or al is None or ch is None or cl is None:
            continue
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

# ---------- CONTEXT LEVELS ROUTE ----------
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
        con = resolve_contract(symbol=sym, live=live_flag)
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
    bars_h4 = retrieve_bars(con, _iso_z(start_h4_utc), _iso_z(asof), 3, 4, live=live_flag)
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

    asian_start = datetime(day_ct.year, day_ct.month, day_ct.day, 18, 0, tzinfo=CT) - timedelta(days=1)
    asian_end   = datetime(day_ct.year, day_ct.month, day_ct.day, 2, 0, tzinfo=CT)
    london_start = datetime(day_ct.year, day_ct.month, day_ct.day, 2, 0, tzinfo=CT)
    london_end   = datetime(day_ct.year, day_ct.month, day_ct.day, 7, 30, tzinfo=CT)
    prev_close_ct = datetime(day_ct.year, day_ct.month, day_ct.day, 15, 0, tzinfo=CT) - timedelta(days=1)

    m15_fetch_start = asian_start - timedelta(hours=2)
    bars_15 = retrieve_bars(con, _iso_z(m15_fetch_start), _iso_z(asof), 2, 15, live=live_flag)

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

    since_prev_close = _slice(bars_15, prev_close_ct, asof_ct)
    m15_swings = _swing_points(since_prev_close, left=2, right=2)
    m15_since_prevclose_levels = [{"type": s["type"], "price": s["price"], "formed_at": s["time"]} for s in m15_swings]

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
#!/usr/bin/env python3
"""
ANALYTICS SERVER
Endpoints:
  GET /health
  GET /hours
  GET /api/vwap
  GET /api/indicators
  GET /api/context/levels

Aligned with apiuse.txt:
- weekend guard + auto-window
- response metadata: requestedStart/End, effectiveStart/End, route, includePartialBar, windowAdjustedForClosure
- indicators: default auto_window=true IF market is closed AND user did not specify guard/auto_window

Reads .env via load_dotenv(), keys:
  API_BASE=https://api.topstepx.com
  TOPSTEP_USER=...
  TOPSTEP_API_KEY=...
  HOST=127.0.0.1
  ANALYTICS_PORT=8091     # preferred (falls back to PORT)
  PORT=8091               # fallback
  LOG_LEVEL=INFO
  REQUEST_TIMEOUT_SECONDS=30
  RETRY_BACKOFF=0.5
  RETRY_MAX=3
  DEFAULT_INTERVAL=1m
  AUTO_LIVE_FRESH_MINUTES=10
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ---------- ENV ----------
load_dotenv()
API_BASE = os.getenv("API_BASE", "https://api.topstepx.com").rstrip("/")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("ANALYTICS_PORT", os.getenv("PORT", "8091")))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "1m")
TOPSTEP_USER = os.getenv("TOPSTEP_USER", "")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY", "")
AUTO_LIVE_FRESH_MINUTES = int(os.getenv("AUTO_LIVE_FRESH_MINUTES", "10"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
def log(msg: str, level: str = "INFO"):
    if _LEVELS.get(level, 20) >= _LEVELS.get(LOG_LEVEL, 20):
        print(f"[{level}] {msg}")

if not TOPSTEP_USER or not TOPSTEP_API_KEY:
    raise SystemExit("Missing TOPSTEP_USER or TOPSTEP_API_KEY in .env")

app = Flask("analytics_server")
CORS(app)

# ---------- UTIL ----------
def parse_iso_z(s: str) -> datetime:
    s = (s or "").strip()
    if not s: raise ValueError("empty time")
    if s.endswith("Z"): s = s[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    raise ValueError("Invalid ISO Z time")

def fmt_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def cme_weekend_status(dt_utc: datetime) -> Dict[str, Any]:
    """Fri 20:00Z close → Sun 22:00Z open; boundaries are OPEN."""
    weekday = dt_utc.weekday()  # Mon=0..Sun=6
    base = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    days_to_fri = (4 - weekday) % 7
    friday = base + timedelta(days=days_to_fri)
    if dt_utc < friday:
        friday -= timedelta(days=7)
    fri_close = friday.replace(hour=20, minute=0, second=0, microsecond=0)
    sun_open = fri_close + timedelta(days=2)
    sun_open = sun_open.replace(hour=22, minute=0, second=0, microsecond=0)
    closed = (dt_utc > fri_close) and (dt_utc < sun_open)
    return {"closed": closed, "fridayClose": fri_close, "sundayOpen": sun_open}

# ---------- HTTP/AUTH ----------
_session_token: Optional[str] = None
_token_expiry_epoch: float = 0.0

def _http_post(url: str, headers: Dict[str, str], json_body: Dict[str, Any],
               timeout: float = REQUEST_TIMEOUT_SECONDS) -> requests.Response:
    last_exc: Optional[Exception] = None
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
    raise last_exc or RuntimeError("HTTP POST failed with no details")

def _auth_token() -> str:
    global _session_token, _token_expiry_epoch
    now = time.time()
    if _session_token and now < _token_expiry_epoch:
        return _session_token
    payload = {"userName": TOPSTEP_USER, "apiKey": TOPSTEP_API_KEY}
    r = _http_post(f"{API_BASE}/api/Auth/loginKey",
                   headers={"Content-Type": "application/json", "accept": "text/plain"},
                   json_body=payload)
    js = r.json() or {}
    token = js.get("token")
    if not token:
        raise RuntimeError(f"Auth failed: {js}")
    _session_token = token
    _token_expiry_epoch = now + 23.5 * 3600
    log("Authenticated (token cached)")
    return _session_token

def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_auth_token()}",
            "Content-Type": "application/json",
            "accept": "text/plain"}

# ---------- Contracts / History ----------
INTERVAL_MAP: Dict[str, Tuple[int, int]] = {
    "1m": (2, 1), "2m": (2, 2), "3m": (2, 3), "5m": (2, 5),
    "15m": (2, 15), "30m": (2, 30), "1h": (3, 1), "4h": (3, 4), "1d": (4, 1),
}
def _interval_to_unit(interval: str) -> Tuple[int, int]:
    return INTERVAL_MAP.get((interval or DEFAULT_INTERVAL or "1m").lower(), (2, 1))

def _search_contracts(search_text: str, live: bool, limit: int = 50) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/api/Contract/search"
    body = {"searchText": search_text, "live": bool(live), "onlyTradable": False, "limit": int(limit)}
    r = _http_post(url, headers=_auth_headers(), json_body=body)
    js = r.json() or {}
    for k in ("contracts", "items", "results"):
        if isinstance(js.get(k), list):
            return js[k]
    if isinstance(js, list):
        return js
    return []

def _search_contract_by_id(contract_code: str) -> Optional[Dict[str, Any]]:
    url = f"{API_BASE}/api/Contract/searchById"
    body = {"contractId": contract_code}
    r = _http_post(url, headers=_auth_headers(), json_body=body)
    js = r.json() or {}
    c = js.get("contract")
    return c if isinstance(c, dict) else None

def _pick_front(contracts: List[Dict[str, Any]]) -> Optional[str]:
    if not contracts: return None
    fronts = [c for c in contracts if str(c.get("isFront")).lower() == "true"]
    if fronts:
        return fronts[0].get("id") or fronts[0].get("code")
    return contracts[0].get("id") or contracts[0].get("code")

def resolve_contract(symbol: Optional[str] = None, contract: Optional[str] = None, live: bool = False) -> Optional[str]:
    if contract and contract.upper().startswith("CON."):
        c = _search_contract_by_id(contract)
        if c and (c.get("id") or c.get("code")):
            return c.get("id") or c.get("code")
        parts = contract.split(".")
        root = parts[3] if len(parts) >= 4 else contract
        symbol = root

    st = (symbol or "").strip().upper()
    if not st: return None

    for text in (st, f"F.US.{st}", f"CON.F.US.{st}"):
        for flag in (live, not live):
            items = _search_contracts(text, live=bool(flag))
            cid = _pick_front(items)
            if cid: return cid
    return None

def retrieve_bars(contract_id: str, start_iso: str, end_iso: str, unit: int, unit_number: int,
                  include_partial: bool = False, live: bool = False, limit: int = 20000) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/api/History/retrieveBars"
    body = {
        "contractId": contract_id, "live": bool(live),
        "startTime": start_iso, "endTime": end_iso,
        "unit": int(unit), "unitNumber": int(unit_number),
        "limit": int(limit), "includePartialBar": bool(include_partial),
    }
    r = _http_post(url, headers=_auth_headers(), json_body=body, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
    js = r.json() or {}
    if isinstance(js, dict) and js.get("bars"): return js["bars"]
    if isinstance(js, list): return js
    return []

def _gx(b: Dict[str, Any], short_key: str, long_key: str):
    return b.get(short_key) if b.get(short_key) is not None else b.get(long_key)

def _f(x: Any) -> float:
    try: return float(x)
    except Exception: return 0.0

# ---------- Indicators ----------
def series_close(bars: List[Dict[str, Any]]) -> List[float]:
    return [_f(_gx(b, "c", "close")) for b in bars]

def sma(values: List[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= length: s -= values[i - length]
        out.append(s / length if i >= length - 1 else None)
    return out

def ema(values: List[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    if length <= 0: return [None for _ in values]
    k = 2.0 / (length + 1.0)
    e = None
    for i, v in enumerate(values):
        if e is None:
            if i >= length - 1:
                e = sum(values[:length]) / float(length)
            else:
                out.append(None); continue
        e = (v - e) * k + e
        out.append(e)
    return out

def rsi(values: List[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    if length <= 0: return [None for _ in values]
    gains = [0.0]; losses = [0.0]
    for i in range(1, len(values)):
        ch = values[i] - values[i-1]
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
    ag = None; al = None
    for i in range(len(values)):
        if i < length: out.append(None); continue
        if i == length:
            ag = sum(gains[1:length+1]) / float(length)
            al = sum(losses[1:length+1]) / float(length)
        else:
            ag = ((ag * (length - 1)) + gains[i]) / float(length)
            al = ((al * (length - 1)) + losses[i]) / float(length)
        if not al: out.append(100.0)
        else:
            rs = ag / al
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out

def compute_vwap(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    cum_pv = 0.0; cum_v = 0.0
    series: List[Dict[str, Any]] = []
    for b in bars:
        h = _f(_gx(b, "h", "high")); l = _f(_gx(b, "l", "low"))
        c = _f(_gx(b, "c", "close")); v = _f(_gx(b, "v", "volume"))
        tp = (h + l + c) / 3.0
        cum_pv += tp * v; cum_v += v
        vwap_val = cum_pv / cum_v if cum_v > 0 else 0.0
        series.append({"time": _gx(b, "t", "time"), "typicalPrice": tp, "vwap": vwap_val, "close": c, "volume": v})
    return {"final": series[-1]["vwap"] if series else 0.0, "series": series, "count": len(series)}

# ---------- Routes ----------
@app.get("/health")
def health():
    return {"ok": True, "service": "analytics_server"}

@app.get("/hours")
def hours():
    now = datetime.now(timezone.utc)
    st = cme_weekend_status(now)
    return {
        "now": fmt_iso_z(now),
        "closed": st["closed"],
        "fridayClose": fmt_iso_z(st["fridayClose"]),
        "sundayOpen": fmt_iso_z(st["sundayOpen"]),
    }

def _decide_route(end_dt: datetime) -> bool:
    """auto route: if end within AUTO_LIVE_FRESH_MINUTES, use live else nonlive."""
    recent_cut = datetime.now(timezone.utc) - timedelta(minutes=AUTO_LIVE_FRESH_MINUTES)
    return end_dt >= recent_cut

def _clip_for_weekend(req_start_dt: datetime, req_end_dt: datetime, status: Dict[str, Any]) -> Tuple[datetime, datetime, bool]:
    """Return (effective_start, effective_end, clipped?) — clips only if inside the gap."""
    if status["closed"]:
        new_end = min(status["fridayClose"], req_end_dt)
        new_start = max(new_end - (req_end_dt - req_start_dt), new_end - timedelta(days=7))
        return new_start, new_end, True
    return req_start_dt, req_end_dt, False

@app.get("/api/vwap")
def api_vwap():
    try:
        symbol = request.args.get("symbol")
        contract = request.args.get("contract")
        interval = request.args.get("interval", DEFAULT_INTERVAL)
        include_partial = request.args.get("include_partial", "false").lower() == "true"

        route = (request.args.get("route", "auto") or "auto").lower()  # auto|live|nonlive
        live_requested = request.args.get("live", "").lower()
        if live_requested in ("true", "false"):
            route = "live" if live_requested == "true" else "nonlive"

        mode = (request.args.get("mode") or "").lower() or None
        guard_param = request.args.get("guard")
        auto_param = request.args.get("auto_window")
        guard_on = (guard_param or "on").lower() != "off"
        # For weekly we already handled default earlier; keep that behavior:
        weekly_default_auto = (mode == "weekly")
        auto_window = (auto_param or ("true" if weekly_default_auto else "false")).lower() == "true"

        now = datetime.now(timezone.utc)
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            if mode == "weekly":
                end_dt = now; start_dt = end_dt - timedelta(days=7)
            else:
                start_dt = now - timedelta(hours=6); end_dt = now
            start = fmt_iso_z(start_dt); end = fmt_iso_z(end_dt)

        req_start_dt = parse_iso_z(start)
        req_end_dt = parse_iso_z(end)
        status = cme_weekend_status(req_end_dt)

        # route choice
        if route == "live": effective_live = True
        elif route == "nonlive": effective_live = False
        else: effective_live = _decide_route(req_end_dt)

        # weekend guard
        eff_start, eff_end = req_start_dt, req_end_dt
        windowAdjusted = False
        if status["closed"]:
            if guard_on and not auto_window:
                return jsonify({
                    "error": "Market likely closed (CME weekend gap).",
                    "requestedStart": fmt_iso_z(req_start_dt),
                    "requestedEnd": fmt_iso_z(req_end_dt),
                    "fridayClose": fmt_iso_z(status["fridayClose"]),
                    "sundayOpen": fmt_iso_z(status["sundayOpen"]),
                    "interval": interval,
                    "guard": "on",
                    "hint": "Set auto_window=true to shift the window back automatically."
                }), 409
            if auto_window:
                eff_start, eff_end, windowAdjusted = _clip_for_weekend(req_start_dt, req_end_dt, status)
                effective_live = False  # clip implies historical

        cid = resolve_contract(symbol=symbol, contract=contract, live=effective_live)
        if not cid:
            return jsonify({"error": "Could not resolve contract", "symbol": symbol, "contract": contract}), 400

        unit, unit_number = _interval_to_unit(interval)
        bars = retrieve_bars(cid, fmt_iso_z(eff_start), fmt_iso_z(eff_end),
                             unit, unit_number, include_partial=include_partial, live=effective_live)
        if not bars and effective_live:
            bars = retrieve_bars(cid, fmt_iso_z(eff_start), fmt_iso_z(eff_end),
                                 unit, unit_number, include_partial=include_partial, live=False)

        if not bars:
            return jsonify({"error": "No bars returned"}), 404

        v = compute_vwap(bars)
        return jsonify({
            "contractId": cid,
            "interval": interval,
            "route": route,
            "live": effective_live,
            "requestedStart": fmt_iso_z(req_start_dt),
            "requestedEnd": fmt_iso_z(req_end_dt),
            "effectiveStart": fmt_iso_z(eff_start),
            "effectiveEnd": fmt_iso_z(eff_end),
            "includePartialBar": include_partial,
            "windowAdjustedForClosure": windowAdjusted,
            "count": v["count"],
            "final": v["final"],
            "series": v["series"],
        })
    except requests.HTTPError as e:
        log(f"/api/vwap upstream: {e}", "ERROR")
        return jsonify({"error": "upstream_failed", "detail": str(e)}), 502
    except Exception as e:
        log(f"/api/vwap unhandled: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.get("/api/indicators")
def api_indicators():
    try:
        symbol = request.args.get("symbol")
        contract = request.args.get("contract")
        interval = request.args.get("interval", DEFAULT_INTERVAL)
        include_partial = request.args.get("include_partial", "false").lower() == "true"

        # indicators requested
        sma_len = request.args.get("sma")
        ema_len = request.args.get("ema")
        rsi_len = request.args.get("rsi")
        sma_len = int(sma_len) if sma_len else None
        ema_len = int(ema_len) if ema_len else None
        rsi_len = int(rsi_len) if rsi_len else None
        if not any([sma_len, ema_len, rsi_len]):
            return jsonify({"error": "Specify at least one indicator: sma, ema, rsi"}), 400

        route = (request.args.get("route", "auto") or "auto").lower()
        live_requested = request.args.get("live", "").lower()
        if live_requested in ("true", "false"):
            route = "live" if live_requested == "true" else "nonlive"

        guard_param = request.args.get("guard")
        auto_param = request.args.get("auto_window")
        guard_on = (guard_param or "on").lower() != "off"
        auto_window_user_specified = auto_param is not None  # user explicitly set it
        auto_window = (auto_param or "false").lower() == "true"

        now = datetime.now(timezone.utc)
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            start_dt = now - timedelta(hours=6)
            end_dt = now
            start = fmt_iso_z(start_dt); end = fmt_iso_z(end_dt)

        req_start_dt = parse_iso_z(start)
        req_end_dt = parse_iso_z(end)
        status = cme_weekend_status(req_end_dt)

        # If market closed and user didn't specify, default auto_window=true (per workaround.txt)
        if status["closed"] and not auto_window_user_specified:
            auto_window = True

        # route choice
        if route == "live": effective_live = True
        elif route == "nonlive": effective_live = False
        else: effective_live = _decide_route(req_end_dt)

        # weekend guard
        eff_start, eff_end = req_start_dt, req_end_dt
        windowAdjusted = False
        if status["closed"]:
            if guard_on and not auto_window:
                return jsonify({"error": "Market likely closed (CME weekend gap)."}), 409
            if auto_window:
                eff_start, eff_end, windowAdjusted = _clip_for_weekend(req_start_dt, req_end_dt, status)
                effective_live = False

        cid = resolve_contract(symbol=symbol, contract=contract, live=effective_live)
        if not cid:
            return jsonify({"error": "Could not resolve contract"}), 400

        unit, unit_number = _interval_to_unit(interval)
        url_start, url_end = fmt_iso_z(eff_start), fmt_iso_z(eff_end)
        bars = retrieve_bars(cid, url_start, url_end, unit, unit_number, include_partial=include_partial, live=effective_live)
        if not bars and effective_live:
            bars = retrieve_bars(cid, url_start, url_end, unit, unit_number, include_partial=include_partial, live=False)
        if not bars:
            return jsonify({"error": "No bars returned"}), 404

        closes = series_close(bars)
        result: Dict[str, Any] = {
            "contractId": cid,
            "interval": interval,
            "route": route,
            "live": effective_live,
            "requestedStart": fmt_iso_z(req_start_dt),
            "requestedEnd": fmt_iso_z(req_end_dt),
            "effectiveStart": url_start,
            "effectiveEnd": url_end,
            "includePartialBar": include_partial,
            "windowAdjustedForClosure": windowAdjusted,
            "count": len(closes),
            "series": [{"time": _gx(b, "t", "time"), "close": _f(_gx(b, "c", "close"))} for b in bars],
        }
        if sma_len:
            sma_series = sma(closes, sma_len)
            result["sma"] = sma_series[-1] if sma_series else None
        if ema_len:
            ema_series = ema(closes, ema_len)
            result["ema"] = ema_series[-1] if ema_series else None
        if rsi_len:
            rsi_series = rsi(closes, rsi_len)
            result["rsi"] = rsi_series[-1] if rsi_series else None

        return jsonify(result)
    except requests.HTTPError as e:
        log(f"/api/indicators upstream: {e}", "ERROR")
        return jsonify({"error": "upstream_failed", "detail": str(e)}), 502
    except Exception as e:
        log(f"/api/indicators unhandled: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

# --- Context levels (kept minimal; unchanged core logic) ---
@app.get("/api/context/levels")
def context_levels():
    try:
        symbol = request.args.get("symbol")
        contract = request.args.get("contract")
        live = (request.args.get("live", "false").lower() == "true")
        as_of = request.args.get("asOf")  # ISO; optional

        if not symbol and not contract:
            return jsonify({"error": "Provide ?symbol= or ?contract="}), 400

        asof = parse_iso_z(as_of) if as_of else datetime.now(timezone.utc)
        cid = resolve_contract(symbol=symbol, contract=contract, live=live)
        if not cid:
            return jsonify({"error": "Could not resolve contract"}), 400

        # very light 15m pull for quick session markers
        unit15, num15 = (2, 15)
        start_15 = asof - timedelta(hours=30)
        bars_15 = retrieve_bars(cid, fmt_iso_z(start_15), fmt_iso_z(asof), unit15, num15, include_partial=False, live=live)

        def hi_lo(bars: List[Dict[str, Any]]):
            if not bars: return None
            highs = [(i, _f(_gx(b, "h", "high"))) for i, b in enumerate(bars)]
            lows  = [(i, _f(_gx(b, "l", "low")))  for i, b in enumerate(bars)]
            h_idx, h_val = max(highs, key=lambda t: t[1])
            l_idx, l_val = min(lows,  key=lambda t: t[1])
            return {"high": h_val, "high_at": _gx(bars[h_idx], "t", "time"),
                    "low": l_val,  "low_at":  _gx(bars[l_idx], "t", "time"),
                    "count": len(bars)}

        return jsonify({
            "contractId": cid,
            "asOf": fmt_iso_z(asof),
            "m15_summary": hi_lo(bars_15)
        })
    except requests.HTTPError as e:
        log(f"/api/context/levels upstream: {e}", "ERROR")
        return jsonify({"error": "upstream_failed", "detail": str(e)}), 502
    except Exception as e:
        log(f"/api/context/levels unhandled: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    log(f"Starting ANALYTICS server on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
