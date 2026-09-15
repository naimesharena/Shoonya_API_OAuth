"""
Fetch data only for year 2010 - based on your pasted code structure
Uses get_daily_price_series (d) with startdate/enddate for 2010
Also shows get_time_price_series for comparison
"""
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_helper import NorenApiPy
import logging
import yaml
import datetime
import timeit
import time
from datetime import timedelta

# supress debug for prod, use INFO
logging.basicConfig(level=logging.INFO)

api = NorenApiPy()

# cred.yml is one level up from Tests/
cred_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cred.yml')
with open(cred_path) as f:
    cred = yaml.load(f, Loader=yaml.FullLoader)
    print(cred)

ret = api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])

if ret != None:
    # === Your existing code for intraday ===
    lastBusDay = datetime.datetime.today()
    lastBusDay = lastBusDay.replace(hour=0, minute=0, second=0, microsecond=0)
    if datetime.date.weekday(lastBusDay) == 5:
        lastBusDay = lastBusDay - datetime.timedelta(days=1)
    elif datetime.date.weekday(lastBusDay) == 6:
        lastBusDay = lastBusDay - datetime.timedelta(days=2)

    print(f"lastBusDay timestamp: {lastBusDay.timestamp()}")

    starttime = timeit.default_timer()
    print("The start time is :", starttime)
    # get one day's data
    ret_intraday = api.get_time_price_series(exchange='NSE', token='2885')
    print("The time difference is :", timeit.default_timer() - starttime)

    if ret_intraday != None:
        print(len(ret_intraday))
        print(ret_intraday[0])
        print(ret_intraday[-1])

    print("\n" + "="*70)
    print("NEW: Fetch DAILY data only for year 2010")
    print("="*70)

    exch = 'NSE'
    tsym = 'RELIANCE-EQ'  # or 'RELIANCE-EQ', 'TCS-EQ', etc.
    token = '2885'  # RELIANCE token

    # --- Method 1: Daily data for 2010 using get_daily_price_series ---
    # Define 2010 range: 2010-01-01 to 2010-12-31
    start_2010 = datetime.datetime(2010, 1, 1, 0, 0, 0).timestamp()
    end_2010 = datetime.datetime(2010, 12, 31, 23, 59, 59).timestamp()

    print(f"\n--- Method 1: get_daily_price_series for 2010 ---")
    print(f"Fetching {exch}:{tsym} from 2010-01-01 ({start_2010}) to 2010-12-31 ({end_2010})")

    start = timeit.default_timer()
    daily_2010 = api.get_daily_price_series(exchange=exch, tradingsymbol=tsym, startdate=start_2010, enddate=end_2010)
    elapsed = timeit.default_timer() - start

    if daily_2010:
        print(f"✓ Got {len(daily_2010)} daily candles for 2010 in {elapsed:.2f}s")
        print(f"  First: {daily_2010[0]}")
        print(f"  Last: {daily_2010[-1]}")
        # DataFrame
        try:
            import pandas as pd
            df = pd.DataFrame(daily_2010)
            print("\nDataFrame head:")
            print(df.head().to_string())
            print("\nDataFrame tail:")
            print(df.tail().to_string())
            # Parse time
            try:
                df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
                print(f"\nDate range: {df['time_dt'].min()} to {df['time_dt'].max()}")
                print(f"Columns: into=open, inth=high, intl=low, intc=close, intv=volume")
            except Exception as e:
                print(f"Time parse error: {e}")
        except ImportError:
            pass
    else:
        print("✗ No daily data for 2010 (check token/symbol or credentials)")

    # --- Method 2: Using get_daily_price_series_full for 2010 (more robust) ---
    print(f"\n--- Method 2: get_daily_price_series_full for 2010 (recommended) ---")
    start = timeit.default_timer()
    daily_2010_full = api.get_daily_price_series_full(exchange=exch, tradingsymbol=tsym, startdate=start_2010, enddate=end_2010, chunk_years=1)
    elapsed = timeit.default_timer() - start
    if daily_2010_full:
        print(f"✓ Got {len(daily_2010_full)} candles via full method in {elapsed:.2f}s")
        print(f"  First: {daily_2010_full[0].get('time')} | Last: {daily_2010_full[-1].get('time')}")

    # --- Method 3: Fetch Nifty 50 all at once for 2010 (parallel) ---
    print(f"\n--- Method 3: Fetch Nifty 50 daily data for 2010 all at once (parallel) ---")
    NIFTY_50_2010 = [
        "RELIANCE-EQ", "TCS-EQ", "INFY-EQ", "HDFCBANK-EQ", "ICICIBANK-EQ",
        "SBIN-EQ", "BHARTIARTL-EQ", "ITC-EQ", "KOTAKBANK-EQ", "LT-EQ",
        "HCLTECH-EQ", "AXISBANK-EQ", "MARUTI-EQ", "WIPRO-EQ", "ULTRACEMCO-EQ"
    ]
    try:
        import concurrent.futures
        def fetch_2010(sym):
            try:
                d = api.get_daily_price_series(exchange='NSE', tradingsymbol=sym, startdate=start_2010, enddate=end_2010)
                return sym, len(d) if d else 0, d[0].get('time') if d and len(d)>0 else None, d[-1].get('time') if d and len(d)>0 else None
            except Exception as e:
                return sym, 0, None, str(e)

        start = timeit.default_timer()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(fetch_2010, s) for s in NIFTY_50_2010]
            for f in concurrent.futures.as_completed(futures):
                sym, cnt, first, last = f.result()
                print(f"  {sym}: {cnt} candles, {first} to {last}")
        print(f"  Total time for {len(NIFTY_50_2010)} symbols 2010 data: {timeit.default_timer()-start:.2f}s")
    except Exception as e:
        print(f"  Parallel fetch error: {e}")

    # --- Method 4: Intraday 1-min data for a specific day in 2010 ---
    print(f"\n--- Method 4: Intraday 1-min data for a day in 2010 (e.g., 2010-06-15) ---")
    print("Note: get_time_price_series needs token and starttime/endtime per day")
    day_2010 = datetime.datetime(2010, 6, 15, 9, 15, 0)  # market open
    day_2010_end = datetime.datetime(2010, 6, 15, 15, 30, 0)  # market close
    start_ts = day_2010.timestamp()
    end_ts = day_2010_end.timestamp()
    print(f"Fetching intraday for token 2885 (RELIANCE) on 2010-06-15 09:15 to 15:30")
    print(f"  start={start_ts} ({day_2010}), end={end_ts} ({day_2010_end})")
    start = timeit.default_timer()
    intraday_2010_day = api.get_time_price_series(exchange='NSE', token='2885', starttime=start_ts, endtime=end_ts, interval=1)
    elapsed = timeit.default_timer() - start
    if intraday_2010_day:
        print(f"✓ Got {len(intraday_2010_day)} 1-min candles for 2010-06-15 in {elapsed:.2f}s")
        print(f"  First: {intraday_2010_day[0]}")
        print(f"  Last: {intraday_2010_day[-1]}")
    else:
        print(f"✗ No intraday data for 2010-06-15 (might be holiday or data not available for old date)")

    print("\n" + "="*70)
    print("Summary for year 2010 fetch:")
    print("  Daily: api.get_daily_price_series(exch, tsym, startdate=2010-01-01 ts, enddate=2010-12-31 ts)")
    print("  Full:  api.get_daily_price_series_full(exch, tsym, startdate=2010-01-01 ts, enddate=2010-12-31 ts)")
    print("  Intraday per day: api.get_time_price_series(exch, token, starttime=day 09:15 ts, endtime=day 15:30 ts)")
    print("="*70)
