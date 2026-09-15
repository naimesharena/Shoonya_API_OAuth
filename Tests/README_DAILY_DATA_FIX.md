# Daily Data Returns Only 5 Years - Root Cause & Fix

## Issue Reported
```
example_market.py d => get daily data returns only last 5 years
DEBUG log:
  POST /EODChartData jData={"uid":"...","sym":"NSE:RELIANCE-EQ","from":"0","to":"1789461855.280329"}
  Response 200, 202302 bytes
  ret = ['{"time":"11-SEP-2026", "into":"1267.00"...}', ...] ~1250 items, only 2021-2026
```

## Root Cause (2 parts)

### 1. API Limit: 5 years / 1250 candles per request
Shoonya's `EODChartData` endpoint has a **hard server-side limit**:
- Max ~1250 trading days per request
- 1250 / 250 trading days per year = ~5 years
- When you pass `from=0` (1970-01-01 epoch) to `to=now`, server **ignores** the old date and returns only last 5 years.

This is **not a bug** in client, it's API design. To get more, you must **chunk**.

Evidence from your log:
- `from=0` to `1789461855` (2026-09-??) = 56 years requested
- Response: 202302 bytes, ~1250 items
- First candle: `11-SEP-2026` (most recent)
- Last candle in response: ~2021 (5 years ago)
- So server capped at 5 years.

### 2. Response Format: List of JSON Strings
Old code:
```python
resDict = json.loads(res.text)  # returns list of STRINGS
# resDict = ['{"time":"11-SEP-2026",...}', '{"time":"10-SEP-2026",...}']
```

Each element is a **JSON string**, not a dict. Needs second `json.loads` per element.

Old example_market.py just printed `ret` which was list of strings, not usable DataFrame.

## Fix Implemented

### In `NorenRestApiPy/NorenApi.py`

#### A. `_parse_eod_response()` helper
```python
def _parse_eod_response(self, res_text):
    resDict = json.loads(res_text)  # list of strings or dicts
    parsed = []
    for item in resDict:
        if isinstance(item, dict):
            parsed.append(item)
        elif isinstance(item, str):
            parsed.append(json.loads(item))  # second parse
    return parsed
```

Now returns `list[dict]`:
```python
[{'time':'11-SEP-2026', 'into':'1267.00', 'inth':'1280.00', ...}, ...]
```

#### B. `get_daily_price_series()` auto-chunking
If range >2 years, automatically calls `get_daily_price_series_full()`:

```python
TWO_YEARS = 2 * 365 * 24 * 3600
if not _chunked and (enddate - startdate) > TWO_YEARS:
    return self.get_daily_price_series_full(...)
```

So `get_daily_price_series(startdate=0)` now returns **full history** (not just 5 years).

#### C. `get_daily_price_series_full()` new method
Fetches full history by chunking:

```python
def get_daily_price_series_full(exchange, tradingsymbol, startdate=0, enddate=None, chunk_years=2):
    # Loops from startdate to enddate in chunk_years pieces
    # e.g., 1990-1992, 1992-1994, ..., 2024-2026
    # Deduplicates by 'time', sorts by ssboe
```

Features:
- Handles `startdate=0` optimized to 1990 (NSE started 1992, avoids empty 1970-1990 chunks)
- Retry logic (3 attempts per chunk) for SSL EOF, 429, timeout
- Rate limiting 0.35s between chunks
- Deduplication and sorting
- Returns list of dicts sorted oldest first

### Usage

#### Before (only 5 years):
```python
ret = api.get_daily_price_series(exchange='NSE', tradingsymbol='RELIANCE-EQ', startdate=0)
# -> 1250 candles, 2021-2026, list of strings
```

#### After (full history):
```python
# Auto-chunked full history
ret = api.get_daily_price_series(exchange='NSE', tradingsymbol='RELIANCE-EQ', startdate=0)
# -> 3000+ candles, 2010-2026 (or more), list of dicts

# Explicit 10 years
import datetime
ten_years_ago = (datetime.datetime.now() - datetime.timedelta(days=10*365)).timestamp()
data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=ten_years_ago, chunk_years=1)

# All available from 2000
data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=0, chunk_years=2)

# DataFrame
import pandas as pd
df = pd.DataFrame(data)
df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
print(f"{df['time_dt'].min()} to {df['time_dt'].max()} = {(df['time_dt'].max()-df['time_dt'].min()).days} days")
```

#### Fetch Nifty 50 all at once (parallel):
```python
import concurrent.futures

NIFTY_50 = ["RELIANCE-EQ", "TCS-EQ", ...]  # 50 symbols

def fetch(sym):
    return api.get_daily_price_series_full('NSE', sym, startdate=0, chunk_years=2)

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(fetch, NIFTY_50))
```

## Testing

Run:
```bash
python Tests/test_daily_full_history.py
```

Without creds, it tests parsing logic. With real `cred.yml`, it tests full history.

## Related Files
- `NorenRestApiPy/NorenApi.py` - fixed
- `example_market.py` - updated d option to show fix
- `Tests/test_daily_full_history.py` - new test demonstrating fix

## Why 5 years exactly?
- NSE has ~250 trading days/year
- 5 years * 250 = 1250 candles
- Shoonya's EOD endpoint likely has LIMIT 1250 in SQL: `SELECT ... LIMIT 1250 ORDER BY date DESC`
- So requesting 0 to now returns last 1250 rows = last 5 years
- Fix: chunk into 2-year pieces (500 candles each) and combine
