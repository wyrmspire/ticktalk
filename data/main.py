#!/usr/bin/env python3
"""
DATA SERVER
Endpoints:
  GET /health
  GET /api/bars
  GET /api/trades

API shapes align to apiuse.txt:
- /api/bars → { contractId, tf, includePartialBar, live, requestedStart/End,
                effectiveStart/End, count, series:[{time,open,high,low,close,volume}] }
- /api/trades → unchanged shape from previous working build

Reads .env via load_dotenv(), keys:
  API_BASE=https://api.topstepx.com
  TOPSTEP_USER=...
  TOPSTEP_API_KEY=...
  HOST=127.0.0.1
  DATA_PORT=8090         # preferred (falls back to PORT)
  PORT=8090              # fallback
  LOG_LEVEL=INFO
  REQUEST_TIMEOUT_SECONDS=30
  RETRY_BACKOFF=0.5
  RETRY_MAX=3
"""

import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ---------- ENV ----------
load_dotenv()
API_BASE = os.getenv("API_BASE", "https://api.topstepx.com").rstrip("/")
TOPSTEP_USER = os.getenv("TOPSTEP_USER", "")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY", "")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("DATA_PORT", os.getenv("PORT", "8090")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))

if not TOPSTEP_USER or not TOPSTEP_API_KEY:
    raise SystemExit("Missing TOPSTEP_USER or TOPSTEP_API_KEY in .env")

# ---------- APP ----------
app = Flask("data_server")
CORS(app)

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
def log(msg: str, level: str = "INFO"):
    if _LEVELS.get(level, 20) >= _LEVELS.get(LOG_LEVEL, 20):
        print(f"[{level}] {msg}")

# ---------- HTTP (retry) ----------
def _http_post(url: str, headers: Dict[str, str] | None, json_body: Dict[str, Any] | None,
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

# ---------- AUTH ----------
_session_token: Optional[str] = None
_token_expiry_epoch: float = 0.0

def _auth_token() -> str:
    global _session_token, _token_expiry_epoch
    now = time.time()
    if _session_token and now < _token_expiry_epoch:
        return _session_token
    payload = {"userName": TOPSTEP_USER, "apiKey": TOPSTEP_API_KEY}
    url = f"{API_BASE}/api/Auth/loginKey"
    r = _http_post(url, headers={"Content-Type": "application/json", "accept": "text/plain"}, json_body=payload)
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

# ---------- Contract resolution ----------
def _search_contracts(search_text: str, live: bool) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/api/Contract/search"
    body = {"searchText": search_text, "live": bool(live), "onlyTradable": False, "limit": 50}
    r = _http_post(url, headers=_auth_headers(), json_body=body)
    js = r.json() or {}
    for k in ("contracts", "items", "results"):
        if isinstance(js.get(k), list):
            return js[k]
    if isinstance(js, list):
        return js
    return []

def _search_by_id(contract_id: str) -> Optional[Dict[str, Any]]:
    url = f"{API_BASE}/api/Contract/searchById"
    r = _http_post(url, headers=_auth_headers(), json_body={"contractId": contract_id})
    js = r.json() or {}
    c = js.get("contract")
    return c if isinstance(c, dict) else None

def _pick_front(contracts: List[Dict[str, Any]]) -> Optional[str]:
    if not contracts: return None
    fronts = [c for c in contracts if str(c.get("isFront")).lower() == "true"]
    if fronts:
        return fronts[0].get("id") or fronts[0].get("code")
    return contracts[0].get("id") or contracts[0].get("code")

@lru_cache(maxsize=256)
def _resolve_contract_id_from_symbol(symbol: str, live: bool) -> Optional[str]:
    st = (symbol or "").strip().upper()
    if not st: return None
    for flag in (live, not live):
        items = _search_contracts(st, live=bool(flag))
        cid = _pick_front(items)
        if cid: return cid
    for flag in (live, not live):
        items = _search_contracts(f"F.US.{st}", live=bool(flag))
        cid = _pick_front(items)
        if cid: return cid
    for flag in (live, not live):
        items = _search_contracts(f"CON.F.US.{st}", live=bool(flag))
        cid = _pick_front(items)
        if cid: return cid
    return None

@lru_cache(maxsize=256)
def _resolve_contract_id(search_text: str, live: bool = False) -> Optional[str]:
    st = (search_text or "").strip()
    if not st: return None
    if st.upper().startswith("CON."):
        c = _search_by_id(st)
        if c and (c.get("id") or c.get("code")):
            return c.get("id") or c.get("code")
        parts = st.split(".")
        root = parts[3] if len(parts) >= 4 else st
        return _resolve_contract_id_from_symbol(root, live=live)
    return _resolve_contract_id_from_symbol(st, live=live)

# ---------- Intervals ----------
# 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month
INTERVAL_MAP: Dict[str, Tuple[int, int]] = {
    "1s": (1, 1), "5s": (1, 5), "15s": (1, 15), "30s": (1, 30),
    "1m": (2, 1), "2m": (2, 2), "3m": (2, 3), "5m": (2, 5), "15m": (2, 15), "30m": (2, 30),
    "1h": (3, 1), "2h": (3, 2), "4h": (3, 4),
    "1d": (4, 1), "1w": (5, 1), "1M": (6, 1)
}

def _retrieve_bars(contract_id: str, start_iso: str, end_iso: str, interval: str,
                   live: bool = False, include_partial: bool = False, limit: int = 20000) -> List[Dict[str, Any]]:
    if interval not in INTERVAL_MAP:
        raise ValueError(f"Unsupported interval '{interval}'")
    unit, unit_number = INTERVAL_MAP[interval]
    url = f"{API_BASE}/api/History/retrieveBars"
    body = {
        "contractId": contract_id,
        "live": bool(live),
        "startTime": start_iso,
        "endTime": end_iso,
        "unit": unit,
        "unitNumber": unit_number,
        "limit": int(limit),
        "includePartialBar": bool(include_partial),
    }
    r = _http_post(url, headers=_auth_headers(), json_body=body, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
    js = r.json() or {}
    return js.get("bars") or (js if isinstance(js, list) else [])

# ---------- Utils ----------
def _norm_iso_z(s: str) -> str:
    s = (s or "").strip()
    if not s: return s
    return s if s.endswith("Z") or (len(s) >= 6 and s[-6] in "+-") else (s + "Z")

def _bars_to_series(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for b in bars or []:
        out.append({
            "time": (b.get("t") or b.get("time")),
            "open": b.get("o") or b.get("open"),
            "high": b.get("h") or b.get("high"),
            "low":  b.get("l") or b.get("low"),
            "close": b.get("c") or b.get("close"),
            "volume": b.get("v") or b.get("volume") or 0,
        })
    return out

# ---------- Routes ----------
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "data_server",
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "apiBase": API_BASE,
        "user": TOPSTEP_USER,
    })

@app.get("/api/bars")
def api_bars():
    try:
        contract = request.args.get("contract")
        symbol = request.args.get("symbol")
        tf = request.args.get("tf", "15m")
        start = request.args.get("start")
        end = request.args.get("end")
        limit = request.args.get("limit")
        live = (request.args.get("live", "false").lower() == "true")
        include_partial = (request.args.get("include_partial", "false").lower() == "true")

        if not contract and not symbol:
            return jsonify({"error": "Provide either ?contract= or ?symbol="}), 400
        if not start or not end:
            return jsonify({"error": "Provide both ?start= and ?end= (ISO8601)"}), 400
        if tf not in INTERVAL_MAP:
            return jsonify({"error": f"Unsupported tf '{tf}'"}), 400

        cid = (contract.strip() if contract else None) or _resolve_contract_id(symbol.strip(), live=live)
        if not cid:
            return jsonify({"error": "Could not resolve contract", "symbol": symbol, "contract": contract}), 404

        req_start = _norm_iso_z(start)
        req_end = _norm_iso_z(end)

        bars = _retrieve_bars(
            cid, req_start, req_end, tf,
            live=live, include_partial=include_partial,
            limit=int(limit) if (limit and str(limit).isdigit()) else 20000,
        )
        series = _bars_to_series(bars)
        return jsonify({
            "contractId": cid,
            "tf": tf,
            "includePartialBar": include_partial,
            "live": live,
            "requestedStart": req_start,
            "requestedEnd": req_end,
            "effectiveStart": req_start,
            "effectiveEnd": req_end,
            "count": len(series),
            "series": series
        })
    except requests.HTTPError as e:
        log(f"/api/bars HTTP error: {e}", "ERROR")
        return jsonify({"error": "upstream_failed", "detail": str(e)}), 502
    except Exception as e:
        log(f"/api/bars unhandled: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.get("/api/trades")
def api_trades():
    try:
        # You can add ?accountId=, ?contract=, ?symbolId=, ?limit=
        account_id_q = request.args.get("accountId")

        # For simplicity we’ll let upstream filter by symbolId/contract if provided.
        start = request.args.get("start")
        end = request.args.get("end")
        symbol_id = request.args.get("contract") or request.args.get("symbolId")
        limit = request.args.get("limit")

        if not start or not end:
            return jsonify({"error": "Provide both ?start= and ?end= (ISO8601)"}), 400

        # Accounts fetch & default
        @lru_cache(maxsize=1)
        def _cached_accounts():
            url = f"{API_BASE}/api/Account/search"
            body = {"onlyActiveAccounts": True}
            r = _http_post(url, headers=_auth_headers(), json_body=body)
            js = r.json() or {}
            accs = js.get("accounts")
            if accs is None and isinstance(js, list):
                accs = js
            return accs or []

        def _pick_default_account(accounts: List[Dict[str, Any]]) -> Optional[int]:
            sel = None
            for a in accounts:
                if a.get("canTrade") and a.get("isVisible"):
                    sel = a; break
            if sel is None:
                for a in accounts:
                    nm = str(a.get("name", "")).upper()
                    if "PRACTICE" in nm:
                        sel = a; break
            if sel is None and accounts:
                sel = accounts[0]
            return int(sel.get("id")) if sel and sel.get("id") is not None else None

        accounts = _cached_accounts()
        default_id = _pick_default_account(accounts)
        try:
            account_id = int(account_id_q) if account_id_q else (default_id or 0)
        except Exception:
            account_id = 0

        if not account_id:
            return jsonify({"error": "No account available; supply ?accountId= explicitly."}), 400

        url = f"{API_BASE}/api/Trade/search"
        body: Dict[str, Any] = {
            "accountId": int(account_id),
            "startTimestamp": _norm_iso_z(start),
            "endTimestamp": _norm_iso_z(end),
        }
        if symbol_id:
            body["symbolId"] = symbol_id
        if limit and str(limit).isdigit():
            body["limit"] = int(limit)

        r = _http_post(url, headers=_auth_headers(), json_body=body, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
        js = r.json() or {}
        trades = js.get("trades")
        if trades is None and isinstance(js, list):
            trades = js
        trades = trades or []

        def _norm_trade(t: Dict[str, Any]) -> Dict[str, Any]:
            # side (your logic is fine)
            side = t.get("side") or t.get("action") or t.get("type")
            if isinstance(side, str):
                s = side.upper()
                side = "BUY" if s in ("B", "BUY", "LONG", "0") else ("SELL" if s in ("S", "SELL", "SHORT", "1") else None)
            elif isinstance(side, (int, float, bool)):
                side = "BUY" if int(side) == 0 else "SELL"

            # timestamp: include creationTimestamp + normalize ISO strings
            ts = (t.get("createdAt") or
                  t.get("creationTimestamp") or
                  t.get("timestamp") or
                  t.get("time"))
            if isinstance(ts, (int, float)):
                epoch = float(ts)
                if epoch > 1000000000000:  # ms -> s
                    epoch /= 1000.0
                ts = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            elif isinstance(ts, str):
                ts = _norm_iso_z(ts)

            return {
                "id": t.get("id") or t.get("tradeId") or t.get("fillId") or t.get("orderId"),
                "accountId": t.get("accountId"),
                "symbolId": t.get("symbolId") or t.get("contractId") or t.get("contract"),
                "side": side,
                # qty: include size as first-choice (per docs), then fallbacks
                "qty": (t.get("size") or t.get("quantity") or t.get("qty") or t.get("q")),
                "price": t.get("price") or t.get("avgPrice") or t.get("fillPrice") or t.get("p"),
                "time": ts,  # keep your field name
                # optional extras you might want:
                # "pnl": t.get("profitAndLoss"),
                # "fees": t.get("fees"),
                # "orderId": t.get("orderId"),
            }


        out = [_norm_trade(t) for t in trades]
        return jsonify({"accountId": account_id, "count": len(out), "trades": out})
    except requests.HTTPError as e:
        log(f"/api/trades HTTP error: {e}", "ERROR")
        return jsonify({"error": "upstream_failed", "detail": str(e)}), 502
    except Exception as e:
        log(f"/api/trades unhandled: {e}", "ERROR")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    log(f"Starting DATA server on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
