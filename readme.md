awesome — here’s a clean, single-path **README.md** that matches exactly what you’re doing now (Windows + venv + two servers + Caddy proxy). Then I give you two tiny copy blocks: one to **start everything**, one to **test**.

---

### 📄 Full file: `README.md` (drop-in)

````markdown
# ticktalk – local two-server stack with Caddy proxy

This repo runs **two Python services** behind a **Caddy** reverse proxy:

## API Usage Cheat-Sheet

This section provides direct terminal usage examples for the TickTalk API endpoints. For more details, see `API_USAGE.md`.

---

# 0) Set base URL

### PowerShell
```powershell
# Codespaces public proxy
$url = 'https://potential-journey-5grvjxjgpqqr27jjx-8080.app.github.dev'
```

### bash (mac/linux/Codespaces)
```bash
BASE='https://potential-journey-5grvjxjgpqqr27jjx-8080.app.github.dev'
```

---

# 1) Health

### PowerShell
```powershell
curl "$url/health"
```

### bash
```bash
curl "$BASE/health"
```

---

# 2) /api/bars — OHLCV candles

**Rules**

* Use **either** `symbol` (preferred) **or** `contract` (full ID like `CON.F.US.MES.Z25`), not both.
* `start` and `end` are **required** (ISO8601 UTC with `Z`).
* Valid `tf`: `1s,1m,5m,15m,30m,1h,4h,1d,1w,1M`.

### PowerShell examples
```powershell
# 5m bars during Friday RTH (ET 09:30–16:00 → UTC 13:30–20:00 in EDT)
curl "$url/api/bars?symbol=MES&tf=5m&start=2025-09-19T13:30:00Z&end=2025-09-19T20:00:00Z&limit=1000"

# Daily bars across a range (let server pick active contract)
curl "$url/api/bars?symbol=MES&tf=1d&start=2025-08-01T00:00:00Z&end=2025-09-21T00:00:00Z&limit=30"

# Pin a specific contract explicitly
curl "$url/api/bars?contract=CON.F.US.MES.Z25&tf=1h&start=2025-09-17T00:00:00Z&end=2025-09-18T00:00:00Z"
```

### bash equivalents
```bash
curl "$BASE/api/bars?symbol=MES&tf=5m&start=2025-09-19T13:30:00Z&end=2025-09-19T20:00:00Z&limit=1000"
curl "$BASE/api/bars?symbol=MES&tf=1d&start=2025-08-01T00:00:00Z&end=2025-09-21T00:00:00Z&limit=30"
curl "$BASE/api/bars?contract=CON.F.US.MES.Z25&tf=1h&start=2025-09-17T00:00:00Z&end=2025-09-18T00:00:00Z"
```

---

# 3) /api/vwap — session VWAP series

**Tips**

* `mode`: `daily` or `weekly`.
* On weekends/holidays, add `auto_window=true` **or** provide `start`/`end`.

### PowerShell examples
```powershell
# Weekly VWAP, weekend-safe
curl "$url/api/vwap?symbol=MES&interval=1m&mode=weekly&auto_window=true"

# VWAP for a specific day (UTC 00:00 → 24:00)
curl "$url/api/vwap?symbol=MES&interval=1h&mode=daily&start=2025-09-17T00:00:00Z&end=2025-09-18T00:00:00Z"
```

### bash
```bash
curl "$BASE/api/vwap?symbol=MES&interval=1m&mode=weekly&auto_window=true"
curl "$BASE/api/vwap?symbol=MES&interval=1h&mode=daily&start=2025-09-17T00:00:00Z&end=2025-09-18T00:00:00Z"
```

---

# 4) /api/indicators — SMA/EMA/RSI over a window

**Rules**

* Provide enough lookback for the period (e.g., **EMA 200 on 5m** needs ≥200×5m ≈ **16h40m** prior to the timestamp).
* Optional: `route=nonlive` for historical; `include_partial=false` to avoid incomplete bar.

### PowerShell examples
```powershell
# 14-period SMA/RSI on 1h with enough history
curl "$url/api/indicators?symbol=MES&interval=1h&sma=14&rsi=14&start=2025-09-16T00:00:00Z&end=2025-09-19T20:00:00Z"

# 200 EMA on 5m at noon ET on Wed (noon ET = 16:00Z during EDT)
# Lookback ~18h ending exactly at 16:00Z
curl "$url/api/indicators?symbol=MES&interval=5m&ema=200&start=2025-09-16T22:00:00Z&end=2025-09-17T16:00:00Z&route=nonlive&include_partial=false"
```

### bash
```bash
curl "$BASE/api/indicators?symbol=MES&interval=1h&sma=14&rsi=14&start=2025-09-16T00:00:00Z&end=2025-09-19T20:00:00Z"
curl "$BASE/api/indicators?symbol=MES&interval=5m&ema=200&start=2025-09-16T22:00:00Z&end=2025-09-17T16:00:00Z&route=nonlive&include_partial=false"
```

---

# 5) /api/context/levels — quick session/swing context

**Tip**

* Use `asOf` anchored to a session time (e.g., **Friday close**) to avoid weekend gaps.

### PowerShell
```powershell
# Context anchored near Friday close (19:59Z in EDT season)
curl "$url/api/context/levels?symbol=MES&asOf=2025-09-19T19:59:00Z"
```

### bash
```bash
curl "$BASE/api/context/levels?symbol=MES&asOf=2025-09-19T19:59:00Z"
```

---

# 6) Common pitfalls (and quick fixes)

* **“Could not resolve contract”**
  * Use **futures** symbols like `MES`, `ES`, `NQ`. (Equities like `AAPL` won’t work.)
  * Don’t send `contract=MES`. If you use `contract`, send the **full** ID (e.g., `CON.F.US.MES.Z25`).
  * Best: just send `symbol=MES`.

* **Indicators return `null`**
  * Not enough history. Expand your `start` earlier, or lower the period.

* **Weekend block on VWAP**
  * Add `auto_window=true`, or specify `start`/`end` for a weekday.

* **PowerShell gotcha**
  * Always wrap the URL in **quotes** so `&` in query strings doesn’t become a shell operator.

---

# 7) Optional: pretty JSON output

### PowerShell (Invoke-RestMethod → object)
```powershell
irm "$url/api/bars?symbol=MES&tf=1h&start=2025-09-18T00:00:00Z&end=2025-09-21T00:00:00Z" | ConvertTo-Json -Depth 6
```

### bash (jq)
```bash
curl -s "$BASE/api/bars?symbol=MES&tf=1h&start=2025-09-18T00:00:00Z&end=2025-09-21T00:00:00Z" | jq .
```

---

If you want, I can also drop **tiny helper snippets** (PowerShell or bash) that:

* convert **ET → UTC** for a date/time (e.g., “noon ET on Wednesday”),
* compute an **EMA lookback window** (period × timeframe).
- **DATA** (`data/main.py`) — `/api/bars`, `/api/trades`
- **ANALYTICS** (`analytics/main.py`) — `/api/vwap`, `/api/indicators`, `/api/context/*`
- **CADDY** (`Caddyfile`) — single entrypoint on **http://localhost:8080**

Weekend handling is built in: VWAP and Indicators will auto-window when CME is closed, unless you explicitly override via query params.

---

## Prereqs (Windows)

- Python 3.11+ and a venv created at `.venv`
- Dependencies installed: `pip install -r requirements.txt`
- A `.env` in repo root containing your credentials and ports. Example:

```dotenv
API_BASE=https://api.topstepx.com
TOPSTEP_USER=your_email_or_username_here
TOPSTEP_API_KEY=your_key_here

# Logging / retries
LOG_LEVEL=INFO
REQUEST_TIMEOUT_SECONDS=30
RETRY_BACKOFF=0.5
RETRY_MAX=3
AUTO_LIVE_FRESH_MINUTES=10
DEFAULT_INTERVAL=1m

# Distinct ports per service
HOST=127.0.0.1
DATA_PORT=8090
ANALYTICS_PORT=8091
````

* **Caddy** installed (one-time):

```powershell
winget install --id=CaddyServer.Caddy -e
```

---

## Run (three terminals)

**Terminal A — DATA**

```powershell
cd C:\ticktalk
.\.venv\Scripts\Activate.ps1
python .\data\main.py
```

**Terminal B — ANALYTICS**

```powershell
cd C:\ticktalk
.\.venv\Scripts\Activate.ps1
python .\analytics\main.py
```

**Terminal C — CADDY proxy**

```powershell
cd C:\ticktalk
caddy run --config Caddyfile
```

Caddyfile (already included) proxies:

* `/api/bars*`, `/api/trades*` → `http://127.0.0.1:8090`
* `/api/vwap*`, `/api/indicators*`, `/api/context*` → `http://127.0.0.1:8091`
* `/health` served by Caddy itself

---

## Test (new terminal)

**Caddy health**

```powershell
curl "http://localhost:8080/health"
```

**DATA via proxy**

```powershell
curl "http://localhost:8080/api/bars?symbol=MES&tf=1m&start=2025-09-18T13:30:00Z&end=2025-09-18T20:00:00Z&live=false&limit=2000"
```

**ANALYTICS via proxy**

```powershell
# VWAP (weekly auto-window during CME weekend)
curl "http://localhost:8080/api/vwap?symbol=MES&interval=1m&mode=weekly"

# Indicators (defaults auto_window=true when market closed unless overridden)
curl "http://localhost:8080/api/indicators?symbol=MES&interval=1m&sma=20&rsi=14"
```

---

## Endpoint reference

### DATA

* `GET /api/bars`

  * **Query**: `symbol` or `contract`, `tf`, `start`, `end`, `live`, `limit`, `include_partial`
  * **Response**:

    ```json
    {
      "contractId": "...",
      "tf": "1m",
      "includePartialBar": false,
      "live": false,
      "requestedStart": "2025-09-18T13:30:00Z",
      "requestedEnd": "2025-09-18T20:00:00Z",
      "effectiveStart": "2025-09-18T13:30:00Z",
      "effectiveEnd": "2025-09-18T20:00:00Z",
      "count": 390,
      "series": [{ "time":"...", "open":0, "high":0, "low":0, "close":0, "volume":0 }]
    }
    ```
* `GET /api/trades`

  * **Query**: `accountId?`, `start`, `end`, `contract?` (or `symbolId`), `limit?`

### ANALYTICS

* `GET /api/vwap`

  * **Query**: `symbol` or `contract`, `interval`, `start?`, `end?`, `mode?`, `route?`, `live?`, `guard?`, `auto_window?`, `include_partial?`
  * Weekend: if `mode=weekly` and no override, `auto_window=true` by default.
* `GET /api/indicators`

  * **Query**: `symbol` or `contract`, `interval`, one or more of `sma`, `ema`, `rsi`, plus same windowing args as VWAP.
  * Weekend: defaults `auto_window=true` when closed unless the client explicitly sets `auto_window` or `guard`.
* `GET /api/context/levels` (lightweight session/swing summary)

---

## Troubleshooting

* **502 from Caddy** → The corresponding backend isn’t running:

  * Bars/Trades need DATA (`:8090`)
  * VWAP/Indicators/Context need ANALYTICS (`:8091`)
* **401/403 upstream** → Check `TOPSTEP_USER` / `TOPSTEP_API_KEY` in `.env`.
* **Weekend 409** → Add `auto_window=true` or `guard=off` (Indicators auto-defaults if not specified).

---

## Commit the working baseline

```powershell
cd C:\ticktalk
.\.venv\Scripts\Activate.ps1
git add Caddyfile README.md analytics\main.py data\main.py
git commit -m "Docs: stable Windows local flow with Caddy; weekend-safe analytics"
git push
```

````

---

### ▶️ Commands to start everything (copy this)
```powershell
# Terminal A — DATA
cd C:\ticktalk
.\.venv\Scripts\Activate.ps1
python .\data\main.py
````

```powershell
# Terminal B — ANALYTICS
cd C:\ticktalk
.\.venv\Scripts\Activate.ps1
python .\analytics\main.py
```

```powershell
# Terminal C — CADDY
cd C:\ticktalk
caddy run --config Caddyfile
```

---

### 🧪 Commands to test (copy this)

```powershell
# Caddy health
curl "http://localhost:8080/health"

# Data via Caddy
curl "http://localhost:8080/api/bars?symbol=MES&tf=1m&start=2025-09-18T13:30:00Z&end=2025-09-18T20:00:00Z&live=false&limit=2000"

# Analytics via Caddy
curl "http://localhost:8080/api/vwap?symbol=MES&interval=1m&mode=weekly"
curl "http://localhost:8080/api/indicators?symbol=MES&interval=1m&sma=20&rsi=14"
```
