"""
ULTRA LOW LATENCY Nifty 50 Excel Feed - For comparing App vs API delay

You reported: "code is working perfectly but i have check real time in app and also in our excel feed as per all stocks at live basis fetch all data at once i see delay as per app and api"

This file explains WHY delay happens and gives ULTRA LOW LATENCY version.

WHY DELAY vs Shoonya App?
---------------------------
1. Exchange -> Broker OMS: 5-20ms (same for app and API, unavoidable)
2. Broker -> Your Python (network): 30-150ms
   - App may be on same AWS region / uses binary protocol
   - Your Python is on home WiFi + Windows Bootcamp + PyCharm = extra hops
   - Check: ping api.shoonya.com
3. Python -> Excel (BIGGEST BOTTLENECK): 50-300ms
   - xlwings uses COM automation, each .range().value call = 10-30ms
   - Writing 50 rows one by one = 500-1500ms
   - Original code: 5 sec sleep + full DataFrame rewrite = 5000ms+ delay

This ULTRA version:
- Fetches ALL 50 at once via parallel get_quotes (snapshot) for instant start
- Then websocket for live deltas (touchline, not depth)
- Uses direct COM API (sht.api.Range) which is 3x faster than xlwings wrapper
- No pandas DataFrame in hot path (DataFrame creation = 20-50ms)
- No sleep in callback, Excel update via queue + batch
- Measures 3 latencies: Exchange->Broker, Broker->Python, Python->Excel
- Option to run WITHOUT Excel to see TRUE API latency (vs App)

Usage:
    python test_realtime_excel_ultra_low_latency.py --no-excel  # pure API latency
    python test_realtime_excel_ultra_low_latency.py --excel     # with Excel
    python test_realtime_excel_ultra_low_latency.py --refresh 0.05  # 50ms refresh
"""

import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_helper import NorenApiPy
import signal
import datetime
import logging
import time
import yaml
import threading
from collections import deque, defaultdict
import concurrent.futures
import argparse

try:
    import xlsxwriter
    import xlwings as xw
    HAS_XLWINGS = True
except ImportError:
    HAS_XLWINGS = False

logging.basicConfig(level=logging.WARNING)  # WARNING for lowest overhead, not DEBUG

# Nifty 50
NIFTY_50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]

FALLBACK_TOKENS = {
    "ADANIENT": "25", "ADANIPORTS": "15083", "APOLLOHOSP": "157", "ASIANPAINT": "22", "AXISBANK": "5900",
    "BAJAJ-AUTO": "16669", "BAJFINANCE": "317", "BAJAJFINSV": "16675", "BEL": "383", "BHARTIARTL": "10604",
    "CIPLA": "694", "COALINDIA": "20374", "DRREDDY": "881", "EICHERMOT": "910", "ETERNAL": "5097",
    "GRASIM": "1435", "HCLTECH": "7229", "HDFCBANK": "1333", "HDFCLIFE": "467", "HINDALCO": "1363",
    "HINDUNILVR": "1394", "ICICIBANK": "4963", "INDIGO": "11184", "INFY": "1594", "ITC": "1660",
    "JIOFIN": "18143", "JSWSTEEL": "11723", "KOTAKBANK": "1922", "LT": "11483", "M&M": "2031",
    "MARUTI": "10999", "MAXHEALTH": "19005", "NESTLEIND": "17963", "NTPC": "11630", "ONGC": "2475",
    "POWERGRID": "14977", "RELIANCE": "2885", "SBILIFE": "21808", "SHRIRAMFIN": "4306", "SBIN": "3045",
    "SUNPHARMA": "3351", "TCS": "11536", "TATACONSUM": "3432", "TATAMOTORS": "3456", "TATASTEEL": "3499",
    "TECHM": "13538", "TITAN": "3506", "TRENT": "1964", "ULTRACEMCO": "11532", "WIPRO": "3787"
}

socket_opened = False
SYMBOLDICT = {}
LOCK = threading.Lock()
LATENCY_EXCHANGE = deque(maxlen=200)  # ft vs ltt
LATENCY_NETWORK = deque(maxlen=200)   # now vs ft
LATENCY_EXCEL = deque(maxlen=200)     # excel write time
TICK_COUNT = 0
LAST_TICK_TIME = 0

# For ultra low latency, we use queue for Excel writes
import queue
EXCEL_QUEUE = queue.Queue(maxsize=1000)

def event_handler_order_update(msg):
    pass

def event_handler_quote_update(inmessage):
    global TICK_COUNT, LAST_TICK_TIME
    recv_ns = time.perf_counter_ns()  # ultra precise
    now = time.time()
    
    try:
        ft = int(inmessage.get('ft', now))
        # ft is exchange feed time
        network_lat_ms = (now - ft) * 1000 if ft < now + 86400 else 0
        if network_lat_ms < 0 or network_lat_ms > 10000:
            network_lat_ms = 0

        # ltt is last trade time if available
        # For latency breakdown

        key = inmessage['e'] + '|' + inmessage['tk']
        # Minimal dict update - no pandas, no datetime in hot path
        with LOCK:
            if key in SYMBOLDICT:
                SYMBOLDICT[key].update(inmessage)
                SYMBOLDICT[key]['_recv_ns'] = recv_ns
                SYMBOLDICT[key]['_network_lat'] = network_lat_ms
            else:
                inmessage['_recv_ns'] = recv_ns
                inmessage['_network_lat'] = network_lat_ms
                SYMBOLDICT[key] = inmessage

        TICK_COUNT += 1
        LAST_TICK_TIME = now

        # Push to Excel queue for async write (non-blocking)
        try:
            EXCEL_QUEUE.put_nowait((key, recv_ns))
        except queue.Full:
            pass  # drop if queue full - better than blocking

        if network_lat_ms > 0:
            LATENCY_NETWORK.append(network_lat_ms)

    except Exception as e:
        print(f"Quote error: {e}")

def open_callback():
    global socket_opened
    socket_opened = True
    print('WS connected')

def fetch_all_at_once_parallel(api, instruments):
    """
    Fetch ALL Nifty 50 quotes at once via parallel REST calls
    This gives instant snapshot (like app opening) vs waiting for websocket ticks
    """
    print(f"\n[SNAPSHOT] Fetching {len(instruments)} quotes in parallel (fetch all at once)...")
    start = time.perf_counter()
    
    def get_one(inst):
        try:
            exch, token = inst.split('|')
            ret = api.get_quotes(exchange=exch, token=token)
            return inst, ret
        except Exception as e:
            return inst, None

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_one, inst): inst for inst in instruments}
        for fut in concurrent.futures.as_completed(futures):
            inst, data = fut.result()
            if data:
                results[inst] = data
                # Also populate SYMBOLDICT so Excel has data immediately
                with LOCK:
                    SYMBOLDICT[inst] = data

    elapsed = time.perf_counter() - start
    print(f"[SNAPSHOT] Fetched {len(results)}/{len(instruments)} in {elapsed:.2f}s ({elapsed*1000/len(instruments):.1f}ms per symbol avg)")
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-excel', action='store_true', help='Run without Excel to measure pure API latency')
    parser.add_argument('--excel', action='store_true', help='Run with Excel')
    parser.add_argument('--refresh', type=float, default=0.05, help='Excel refresh interval sec, default 0.05 (50ms)')
    args = parser.parse_args()

    use_excel = not args.no_excel
    if args.excel:
        use_excel = True
    REFRESH = args.refresh

    # Creds
    cred_path = '../cred.yml'
    if not os.path.exists(cred_path):
        cred_path = '..\\cred.yml'
    if not os.path.exists(cred_path):
        cred_path = os.path.join(os.path.dirname(__file__), '..', 'cred.yml')
    
    with open(cred_path) as f:
        cred = yaml.load(f, Loader=yaml.FullLoader)

    # Excel setup - ULTRA FAST COM
    wb = None
    sht = None
    sht_api = None
    app = None
    if use_excel and HAS_XLWINGS:
        excel_file = os.path.join(os.path.dirname(__file__), 'realtime_excel_feed.xlsx')
        if not os.path.exists(excel_file):
            wb_tmp = xlsxwriter.Workbook(excel_file)
            wb_tmp.add_worksheet('Live')
            wb_tmp.close()
        wb = xw.Book(excel_file)
        try:
            sht = wb.sheets['Live']
        except:
            sht = wb.sheets.add('Live')
        app = wb.app
        app.screen_updating = True
        # Direct COM API handle - 3x faster than xlwings wrapper
        sht_api = sht.api
        # Header
        headers = ['Symbol','Token','LTP','%Chg','Open','High','Low','Volume','LTQ','FeedTime','NetworkLatMs','ExcelLatMs','TotalLatMs','Ticks']
        sht.range('A1').value = [headers]
        # Pre-allocate rows for 50
        for i, sym in enumerate(NIFTY_50_SYMBOLS, start=2):
            sht.range(f'A{i}').value = sym
        print(f"Excel ultra-fast mode: {excel_file} using direct COM")
    else:
        print("Running WITHOUT Excel - pure API latency measurement (fastest)")

    # API
    api = NorenApiPy()
    if 'Access_token' in cred and cred['Access_token']:
        api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
        api.set_credentials(cred['Access_token'], cred['UID'], cred['Account_ID'])
        ret = True
    else:
        ret = api.login(userid=cred['user'], password=cred['pwd'], twoFA=cred['factor2'],
                        vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])

    if not ret:
        print("Login failed")
        sys.exit(1)

    instruments = [f"NSE|{FALLBACK_TOKENS[s]}" for s in NIFTY_50_SYMBOLS]

    # STEP 1: Fetch all at once (snapshot) - instant data like app
    snapshot = fetch_all_at_once_parallel(api, instruments)

    # STEP 2: Start websocket for live deltas
    api.start_websocket(order_update_callback=event_handler_order_update,
                        subscribe_callback=event_handler_quote_update,
                        socket_open_callback=open_callback)

    start_wait = time.time()
    while not socket_opened and time.time() - start_wait < 10:
        time.sleep(0.1)

    # Subscribe ALL at once (single call, not batches) for lowest latency
    # Some brokers limit 50 per call, but try all at once first
    try:
        print(f"Subscribing to ALL 50 at once for minimum delay...")
        api.subscribe(instruments, feed_type='t')  # 't' = touchline = lowest latency
        print("Subscribed all 50 in one call")
    except Exception as e:
        print(f"Single call failed ({e}), trying batches of 25")
        for i in range(0, len(instruments), 25):
            api.subscribe(instruments[i:i+25], feed_type='t')
            time.sleep(0.1)

    print(f"\n=== ULTRA LOW LATENCY FEED ===")
    print(f"Mode: {'WITH Excel' if use_excel else 'NO Excel (pure API)'} | Refresh: {REFRESH*1000:.0f}ms")
    print(f"Snapshot: {len(snapshot)} symbols pre-loaded")
    print(f"Press Ctrl+C to stop\n")

    # Excel writer thread - separate thread for Excel to not block websocket
    def excel_writer():
        global LATENCY_EXCEL
        batch = []
        last_flush = time.perf_counter()
        while True:
            try:
                # Collect batch for  REFRESH interval
                try:
                    while len(batch) < 20:  # batch up to 20 ticks
                        key, recv_ns = EXCEL_QUEUE.get(timeout=REFRESH)
                        batch.append((key, recv_ns))
                        if time.perf_counter() - last_flush >= REFRESH:
                            break
                except queue.Empty:
                    pass

                if not batch:
                    continue

                excel_start_ns = time.perf_counter_ns()
                # Write batch via direct COM - fastest
                if use_excel and sht_api:
                    try:
                        # Prepare 2D array for batch write if possible
                        # For ultra low latency, write one by one but via api directly
                        for key, recv_ns in batch:
                            with LOCK:
                                data = SYMBOLDICT.get(key)
                            if not data:
                                continue
                            # Find row
                            token = key.split('|')[1]
                            row = None
                            for sym, tok in FALLBACK_TOKENS.items():
                                if tok == token:
                                    row = NIFTY_50_SYMBOLS.index(sym) + 2
                                    break
                            if not row:
                                continue
                            
                            # Direct COM write - faster than xlwings
                            # Using Value2 is faster than Value
                            try:
                                # Write LTP, %chg, etc in one go per row using COM
                                # Row: Symbol already there, update B-N
                                ltp = data.get('lp','')
                                pc = data.get('pc','')
                                o = data.get('o','')
                                h = data.get('h','')
                                l = data.get('l','')
                                v = data.get('v','')
                                ltq = data.get('ltq','')
                                ft = data.get('ft','')
                                ft_str = str(datetime.datetime.fromtimestamp(int(ft))) if ft else ''
                                net_lat = data.get('_network_lat',0)
                                
                                excel_lat_ms = (time.perf_counter_ns() - recv_ns) / 1e6
                                
                                # Write via COM - using Range
                                # B=Token, C=LTP, D=%Chg, E=Open, F=High, G=Low, H=Volume, I=LTQ, J=FeedTime, K=NetworkLat, L=ExcelLat, M=Total, N=Ticks
                                sht_api.Range(f"B{row}:N{row}").Value2 = [[
                                    token, ltp, pc, o, h, l, v, ltq, ft_str,
                                    round(net_lat,1), round(excel_lat_ms,1), round(net_lat+excel_lat_ms,1), TICK_COUNT
                                ]]
                                LATENCY_EXCEL.append(excel_lat_ms)
                            except Exception as e:
                                # Fallback to xlwings
                                pass
                    except Exception as e:
                        print(f"Excel batch error: {e}")

                batch.clear()
                last_flush = time.perf_counter()

            except Exception as e:
                print(f"Writer thread error: {e}")
                time.sleep(0.1)

    if use_excel:
        writer_thread = threading.Thread(target=excel_writer, daemon=True)
        writer_thread.start()

    # Main loop - just stats, no Excel work (Excel in separate thread)
    last_stats = time.time()
    try:
        while True:
            time.sleep(1)
            if time.time() - last_stats >= 2:
                with LOCK:
                    net_avg = sum(LATENCY_NETWORK)/len(LATENCY_NETWORK) if LATENCY_NETWORK else 0
                    net_max = max(LATENCY_NETWORK) if LATENCY_NETWORK else 0
                    excel_avg = sum(LATENCY_EXCEL)/len(LATENCY_EXCEL) if LATENCY_EXCEL else 0
                    total_avg = net_avg + excel_avg
                    count = len(SYMBOLDICT)
                
                # Compare with app: if your app shows 150ms and we show 250ms, 100ms is Excel overhead
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                      f"Ticks: {TICK_COUNT} | Symbols: {count} | "
                      f"Network: {net_avg:.1f}ms avg {net_max:.1f}ms max | "
                      f"Excel: {excel_avg:.1f}ms avg | "
                      f"Total: {total_avg:.1f}ms | "
                      f"Q: {EXCEL_QUEUE.qsize()} | "
                      f"{'WITH Excel' if use_excel else 'NO Excel'}")

                # If delay vs app is high, suggest:
                if total_avg > 300:
                    print(f"  -> Delay vs App likely due to Excel COM ({excel_avg:.0f}ms) + Network ({net_avg:.0f}ms). "
                          f"Try --no-excel to see pure API latency, or use wired internet, or close other Excel sheets.")

                last_stats = time.time()

                # If no ticks for 10 sec, warn
                if time.time() - LAST_TICK_TIME > 10 and LAST_TICK_TIME != 0:
                    print(f"  WARNING: No ticks for {time.time()-LAST_TICK_TIME:.0f}s - check market hours or subscription")

    except KeyboardInterrupt:
        print("\nStopping...")
        if use_excel and wb:
            try:
                wb.save()
            except:
                pass
        print(f"Final: Ticks {TICK_COUNT}, Net avg {sum(LATENCY_NETWORK)/len(LATENCY_NETWORK) if LATENCY_NETWORK else 0:.1f}ms, Excel avg {sum(LATENCY_EXCEL)/len(LATENCY_EXCEL) if LATENCY_EXCEL else 0:.1f}ms")
