# API Usage Guide

This document describes how to use the endpoints in your TickTalk project, including required parameters, minimum data requirements, and example requests.

## Endpoints

### /api/indicators
Calculates indicators (EMA, SMA, RSI) for a given contract or symbol.

**Required parameters:**
- `contract` or `symbol` (e.g., `contract=CON.F.US.ES` or `symbol=ES`)
- `interval` (e.g., `5m`, `15m`, `1h`)
- Indicator length (e.g., `ema=200`, `sma=50`, `rsi=14`)
- `start` and `end` (ISO8601 UTC, e.g., `2025-09-16T19:00:00Z`)

**Minimum data requirements:**
- EMA: Needs at least N bars, where N is the EMA length (e.g., `ema=200` needs 200 bars)
- SMA: Needs at least N bars
- RSI: Needs at least N bars

**Example request:**
```
curl 'http://localhost:8080/api/indicators?interval=5m&ema=200&contract=CON.F.US.ES&start=2025-09-16T16:00:00Z&end=2025-09-17T12:00:00Z'
```

### /api/vwap
Calculates VWAP for a given contract or symbol.

**Example request:**
```
curl 'http://localhost:8080/api/vwap?interval=5m&contract=CON.F.US.ES&start=2025-09-16T09:00:00Z&end=2025-09-17T12:00:00Z'
```

## Tips
- Always provide enough bars for your indicator length.
- If you get `null` for an indicator, increase your time window.
- Use ISO8601 UTC format for `start` and `end`.

## Troubleshooting
- "Could not resolve contract": Make sure you provide a valid `contract` or `symbol`.
- Indicator returns `null`: Not enough data bars in the requested window.

---
Add more endpoint details and examples as your API evolves.
