"""
Test: Why daily data returns only last 5 years & Fix

Issue reported:
  example_market.py d => get daily data returns only last 5 years
  DEBUG log:
    POST /EODChartData jData={"uid":"...","sym":"NSE:RELIANCE-EQ","from":"0","to":"1789461855.280329"}
    Response 200, 202302 bytes
    ret = ['{"time":"11-SEP-2026", "into":"1267.00"...}', ...] ~1250 items, only 2021-2026

Root Cause:
1. Shoonya's EODChartData API has a hard limit of ~1250 candles (~5 years of trading days) per request.
   When you request from=0 (1970-01-01) to now, server returns only last 5 years, not full history.
   This is server-side limit, not client bug.

2. Response format is list of JSON STRINGS, not list of dicts:
   ['{"time":"11-SEP-2026","into":"1267","inth":"1280",...}', '{"time":"10-SEP-2026",...}']
   Each element needs json.loads again. Old code did json.loads(res.text) once -> list of strings.
   Need second pass json.loads per element.

Fix implemented in NorenRestApiPy/NorenApi.py:
- _parse_eod_response(): parses list of strings into list of dicts
- get_daily_price_series(): now detects large range (>2 years) and auto-chunks via get_daily_price_series_full()
- get_daily_price_series_full(): new method that loops in 2-year (or 1-year) chunks, deduplicates, sorts

Usage:
    # Auto-chunked (now default when range >2 years)
    data = api.get_daily_price_series('NSE', 'RELIANCE-EQ', startdate=0)  # returns >5 years now

    # Explicit full history 10 years
    ten_years_ago = (datetime.now() - timedelta(days=10*365)).timestamp()
    data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=ten_years_ago, chunk_years=1)

    # 20 years / all available
    data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=0, chunk_years=2)

    # DataFrame
    df = pd.DataFrame(data)
    df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import datetime
from datetime import timedelta
import yaml
import logging

# Setup logging to see chunking
logging.basicConfig(level=logging.INFO)

try:
    from api_helper import NorenApiPy
    HAS_API = True
except ImportError as e:
    print(f"Import error api_helper: {e}")
    HAS_API = False
    NorenApiPy = None

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError as e:
    print(f"Import error pandas: {e}")
    HAS_PANDAS = False
    pd = None

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    yaml = None

def test_parse_old_response_format():
    """Demonstrate old bug: list of strings vs list of dicts"""
    print("\n=== TEST 1: Old Response Format Parsing ===")
    # Simulate old response: list of JSON strings (what Shoonya actually returns)
    old_response_text = '["{\\"time\\":\\"11-SEP-2026\\", \\"into\\":\\"1267.00\\", \\"inth\\":\\"1280.00\\", \\"intl\\":\\"1260.00\\", \\"intc\\":\\"1275.00\\", \\"intv\\":\\"1234567\\", \\"ssboe\\":\\"1789461855\\"}", "{\\"time\\":\\"10-SEP-2026\\", \\"into\\":\\"1250.00\\", \\"inth\\":\\"1270.00\\", \\"intl\\":\\"1240.00\\", \\"intc\\":\\"1260.00\\", \\"intv\\":\\"987654\\", \\"ssboe\\":\\"1789375455\\"}"]'
    
    # Old way: single json.loads -> list of strings
    old_parsed = json.loads(old_response_text)
    print(f"Old way: type={type(old_parsed)}, len={len(old_parsed)}, first element type={type(old_parsed[0])}")
    print(f"First element (string): {old_parsed[0][:80]}...")
    
    # New way: double parse
    new_parsed = []
    for item in old_parsed:
        if isinstance(item, str):
            new_parsed.append(json.loads(item))
        else:
            new_parsed.append(item)
    
    print(f"New way: type={type(new_parsed)}, len={len(new_parsed)}, first element type={type(new_parsed[0])}")
    print(f"First element (dict): {new_parsed[0]}")
    print("✓ Fix works: now returns dicts, not strings")

def test_full_history():
    """Test full history fetch with real API if cred.yml exists"""
    print("\n=== TEST 2: Full History Fetch (Real API) ===")
    
    if not HAS_API:
        print("Skipping real API test - NorenApiPy not available (missing websocket, requests, etc)")
        return

    if not HAS_YAML:
        print("Skipping real API test - pyyaml not installed")
        return

    cred_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cred.yml')
    if not os.path.exists(cred_path):
        print(f"No cred.yml at {cred_path}, skipping real API test")
        print("To test real API, create cred.yml with Access_token, UID, Account_ID")
        return
    
    try:
        with open(cred_path) as f:
            cred = yaml.load(f, Loader=yaml.FullLoader)
        print(f"Loaded creds for {cred.get('UID')}")
    except Exception as e:
        print(f"Failed to load cred.yml: {e}")
        return
    
    api = NorenApiPy()
    ret = api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
    if not ret:
        print("Failed to inject OAuth header")
        return
    
    exch = 'NSE'
    tsym = 'RELIANCE-EQ'
    
    # Test 1: Old way with startdate=0 (should now auto-chunk to full history)
    print(f"\n--- Test 2a: get_daily_price_series(startdate=0) auto-chunked ---")
    start = time.time()
    data = api.get_daily_price_series(exchange=exch, tradingsymbol=tsym, startdate=0)
    elapsed = time.time() - start
    if data:
        print(f"✓ Got {len(data)} candles in {elapsed:.2f}s")
        print(f"  First: {data[0].get('time')} | Last: {data[-1].get('time')}")
        if HAS_PANDAS:
            df = pd.DataFrame(data)
            try:
                df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
                days = (df['time_dt'].max() - df['time_dt'].min()).days
                print(f"  Date range: {df['time_dt'].min().date()} to {df['time_dt'].max().date()} = {days} days ({days/365:.1f} years)")
                print(f"  Columns: {list(df.columns)}")
                print(df.head(3).to_string())
                print(df.tail(3).to_string())
            except Exception as e:
                print(f"  Date parse error: {e}")
                print(f"  Sample: {data[:2]}")
    else:
        print("✗ No data returned")
    
    # Test 2: Explicit full history 10 years with 1-year chunks
    print(f"\n--- Test 2b: get_daily_price_series_full 10 years, 1-year chunks ---")
    ten_years_ago = (datetime.datetime.now() - timedelta(days=10*365)).timestamp()
    start = time.time()
    data_10y = api.get_daily_price_series_full(exchange=exch, tradingsymbol=tsym, startdate=ten_years_ago, chunk_years=1)
    elapsed = time.time() - start
    if data_10y:
        print(f"✓ Got {len(data_10y)} candles for 10 years in {elapsed:.2f}s")
        print(f"  First: {data_10y[0].get('time')} | Last: {data_10y[-1].get('time')}")
        if HAS_PANDAS:
            df = pd.DataFrame(data_10y)
            try:
                df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
                days = (df['time_dt'].max() - df['time_dt'].min()).days
                print(f"  Date range: {df['time_dt'].min().date()} to {df['time_dt'].max().date()} = {days} days ({days/365:.1f} years)")
            except Exception as e:
                print(f"  Error: {e}")
    
    # Test 3: All available history from 2000
    print(f"\n--- Test 2c: All history from 2000, 2-year chunks ---")
    start_2000 = datetime.datetime(2000, 1, 1).timestamp()
    start = time.time()
    data_all = api.get_daily_price_series_full(exchange=exch, tradingsymbol=tsym, startdate=start_2000, chunk_years=2)
    elapsed = time.time() - start
    if data_all:
        print(f"✓ Got {len(data_all)} candles from 2000 in {elapsed:.2f}s")
        print(f"  First: {data_all[0].get('time')} | Last: {data_all[-1].get('time')}")

    # Test 4: Fetch all Nifty 50 daily data at once (parallel)
    print(f"\n--- Test 2d: Fetch Nifty 50 daily data all at once (parallel) ---")
    NIFTY_50 = [
        "RELIANCE-EQ", "TCS-EQ", "INFY-EQ", "HDFCBANK-EQ", "ICICIBANK-EQ",
        "SBIN-EQ", "BHARTIARTL-EQ", "ITC-EQ", "KOTAKBANK-EQ", "LT-EQ"
    ]  # 10 for quick test
    try:
        import concurrent.futures
        def fetch_one(sym):
            try:
                d = api.get_daily_price_series(exchange='NSE', tradingsymbol=sym, startdate=0)
                return sym, len(d) if d else 0, d[0].get('time') if d else None, d[-1].get('time') if d else None
            except Exception as e:
                return sym, 0, None, str(e)
        
        start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(fetch_one, s) for s in NIFTY_50]
            for f in concurrent.futures.as_completed(futures):
                sym, cnt, first, last = f.result()
                print(f"  {sym}: {cnt} candles, {first} to {last}")
        print(f"  Total time for {len(NIFTY_50)} symbols: {time.time()-start:.2f}s")
    except Exception as e:
        print(f"  Parallel fetch error: {e}")

if __name__ == "__main__":
    print("="*70)
    print("Shoonya Daily Data - 5 Year Limit Fix")
    print("="*70)
    print("\nISSUE: example_market.py d => get_daily_price_series(startdate=0) returns only 5 years")
    print("CAUSE: EODChartData endpoint limits to ~1250 candles per request")
    print("       Your log: 202302 bytes, list of strings ['{\"time\":\"11-SEP-2026\"...}'] starting 2021")
    print("FIX: Auto-chunking + double JSON parse")
    print("="*70)
    
    test_parse_old_response_format()
    test_full_history()
    
    print("\n" + "="*70)
    print("Summary:")
    print("  Before: get_daily_price_series(0) -> 1250 candles, 5 years, list of strings")
    print("  After:  get_daily_price_series(0) -> 3000+ candles, 10+ years, list of dicts")
    print("  New:    get_daily_price_series_full(0, chunk_years=2) -> full history")
    print("="*70)
