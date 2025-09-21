# shared/client.py
import os
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ---- CONFIG ----
API_BASE = os.getenv("API_BASE", "https://api.topstepx.com").rstrip("/")
TOPSTEP_USER = os.getenv("TOPSTEP_USER", "")
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY", "")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))

# ---- In-memory session cache ----
_session_token: Optional[str] = None
_token_expiry_epoch: float = 0.0

# ---- LOGGING ----
def log(msg: str, level: str = "INFO"):
    print(f"[{level}] {msg}")

# ---- HTTP with RETRY ----
def _http_post(url: str, headers: Dict[str, str], json_body: Dict[str, Any], timeout: float = REQUEST_TIMEOUT_SECONDS) -> requests.Response:
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

# ---- AUTH ----
def _auth_token() -> str:
    global _session_token, _token_expiry_epoch
    now = time.time()
    if _session_token and now < _token_expiry_epoch:
        return _session_token
    if not TOPSTEP_USER or not TOPSTEP_API_KEY:
        raise SystemExit("Missing TOPSTEP_USER or TOPSTEP_API_KEY in .env")
    url = f"{API_BASE}/api/Auth/loginKey"
    payload = {"userName": TOPSTEP_USER, "apiKey": TOPSTEP_API_KEY}
    log("Authenticating with TopstepX ...")
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
    return {"Authorization": f"Bearer {_auth_token()}", "Content-Type": "application/json", "accept": "text/plain"}

# ---- CONTRACTS ----
@lru_cache(maxsize=256)
def resolve_contract_id(search_text: str, live: bool = False) -> str:
    if not search_text:
        raise ValueError("Empty symbol/contract")
    st = search_text.strip()
    if st.upper().startswith("CON."):
        return st

    url = f"{API_BASE}/api/Contract/search"
    payload = {"live": bool(live), "searchText": st}
    r = _http_post(url, headers=_auth_headers(), json_body=payload)
    data = r.json() or {}
    items = data.get("contracts") or data.get("items") or []
    for c in items:
        if c.get("activeContract") and c.get("id"):
            return c["id"]
    if items:
        return items[0].get("id") or st
    raise ValueError(f"No contract found for '{st}'")

# ---- BARS ----
INTERVAL_MAP = {
    "1s": (1, 1), "5s": (1, 5), "15s": (1, 15), "30s": (1, 30),
    "1m": (2, 1), "2m": (2, 2), "3m": (2, 3), "5m": (2, 5), "15m": (2, 15), "30m": (2, 30),
    "1h": (3, 1), "2h": (3, 2), "4h": (3, 4),
    "1d": (4, 1), "1w": (5, 1), "1M": (6, 1),
}

def retrieve_bars(contract_id: str, start_iso: str, end_iso: str, interval: str,
                   live: bool = False, limit: int | None = None) -> List[Dict[str, any]]:
    if interval not in INTERVAL_MAP:
        raise ValueError(f"Unsupported interval '{interval}'")
    unit, unit_number = INTERVAL_MAP[interval]
    limit = limit if limit is not None else 20000

    url = f"{API_BASE}/api/History/retrieveBars"
    payload = {
        "contractId": contract_id, "live": bool(live), "startTime": start_iso,
        "endTime": end_iso, "unit": unit, "unitNumber": unit_number,
        "limit": int(limit), "includePartialBar": False,
    }
    headers = {**_auth_headers(), "accept": "text/plain"}
    r = _http_post(url, headers=headers, json_body=payload, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
    data = r.json() or {}
    return data.get("bars") or []

# ---- TRADES ----
def retrieve_trades(account_id: int, start_iso: str, end_iso: str, symbol_id: Optional[str], limit: Optional[int]) -> List[Dict[str, any]]:
    url = f"{API_BASE}/api/Trade/search"
    body = {"accountId": account_id, "startTimestamp": start_iso, "endTimestamp": end_iso}
    if symbol_id:
        body["symbolId"] = symbol_id
    if limit is not None:
        body["limit"] = int(limit)
    
    r = _http_post(url, headers=_auth_headers(), json_body=body, timeout=max(REQUEST_TIMEOUT_SECONDS, 60))
    js = r.json() or {}
    trades = js.get("trades")
    return trades if isinstance(trades, list) else []

@lru_cache(maxsize=1)
def retrieve_accounts() -> List[Dict[str, any]]:
    url = f"{API_BASE}/api/Account/search"
    body = {"onlyActiveAccounts": True}
    r = _http_post(url, headers=_auth_headers(), json_body=body)
    js = r.json() or {}
    return js.get("accounts") or []