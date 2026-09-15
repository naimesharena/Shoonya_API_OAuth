"""
Nifty 50 Live Excel Feed - Optimized for Minimum Latency

Features:
- Nifty 50 stocks (50 symbols) with dynamic token resolution via searchscrip
- Minimum refresh interval: 100ms (configurable, down to 50ms) vs original 5s
- Event-driven Excel update: only dirty rows updated, not full sheet
- Latency measurement: feed time vs local time
- SSL fix for Windows Bootcamp (uses patched NorenApiPy)
- Supports both OAuth (cred.yml Access_token) and old login

Usage:
    pip install -r ../requirements.txt
    pip install xlwings xlsxwriter pandas
    # Update ../cred.yml with your credentials
    python test_realtime_excel_nifty50.py

Excel output: realtime_excel_feed.xlsx (Live sheet)
Columns: Symbol | Token | LTP | %Chg | Open | High | Low | Close | AvgPrice | Volume | LTQ | FeedTime | LatencyMs | LastUpdate

For lowest latency:
- Use feed_type='t' (touchline) not 'd' (depth)
- Set REFRESH_INTERVAL = 0.05 to 0.1 sec
- Keep Excel visible but close other workbooks
- Disable Excel auto-calculation if not needed: Formulas -> Calculation Options -> Manual
"""

import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_helper import NorenApiPy
import signal
import datetime
import logging
import time
import yaml
import pandas as pd
import threading
from collections import deque
import ssl

# Excel libs - try xlwings, fallback to openpyxl
try:
    import xlsxwriter
    import xlwings as xw
    HAS_XLWINGS = True
except ImportError:
    HAS_XLWINGS = False
    print("xlwings not found, will use console only. pip install xlwings")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
# Minimum refresh interval in seconds - for live latency, keep 0.05-0.2
REFRESH_INTERVAL = 0.1  # 100ms = lowest practical for xlwings, use 0.05 for 50ms if Excel can handle
FEED_TYPE = 't'  # 't' = touchline (lowest latency), 'd' = depth (more fields but heavier)

# Nifty 50 - as of Sep 2024 (Wikipedia + NSE)
NIFTY_50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",  # ETERNAL = Zomato
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]

# Fallback hardcoded tokens (approximate, from NSE - searchscrip will try to resolve correct ones)
# If searchscrip fails, these will be used
FALLBACK_TOKENS = {
    "ADANIENT": "25", "ADANIPORTS": "15083", "APOLLOHOSP": "157", "ASIANPAINT": "22", "AXISBANK": "5900",
    "BAJAJ-AUTO": "16669", "BAJFINANCE": "317", "BAJAJFINSV": "16675", "BEL": "383", "BHARTIARTL": "10604",
    "CIPLA": "694", "COALINDIA": "20374", "DRREDDY": "881", "EICHERMOT": "910", "ETERNAL": "5097", # Zomato token approx 5097
    "GRASIM": "1435", "HCLTECH": "7229", "HDFCBANK": "1333", "HDFCLIFE": "467", "HINDALCO": "1363",
    "HINDUNILVR": "1394", "ICICIBANK": "4963", "INDIGO": "11184", "INFY": "1594", "ITC": "1660",
    "JIOFIN": "18143", "JSWSTEEL": "11723", "KOTAKBANK": "1922", "LT": "11483", "M&M": "2031",
    "MARUTI": "10999", "MAXHEALTH": "19005", "NESTLEIND": "17963", "NTPC": "11630", "ONGC": "2475",
    "POWERGRID": "14977", "RELIANCE": "2885", "SBILIFE": "21808", "SHRIRAMFIN": "4306", "SBIN": "3045",
    "SUNPHARMA": "3351", "TCS": "11536", "TATACONSUM": "3432", "TATAMOTORS": "3456", "TATASTEEL": "3499",
    "TECHM": "13538", "TITAN": "3506", "TRENT": "1964", "ULTRACEMCO": "11532", "WIPRO": "3787"
}

# ================= GLOBAL STATE =================
socket_opened = False
SYMBOLDICT = {}
ROW_MAP = {}  # key -> row number
DIRTY_KEYS = set()
LOCK = threading.Lock()
LATENCY_STATS = deque(maxlen=100)  # last 100 latencies for avg

def event_handler_order_update(message):
    print(f"Order update: {message}")

def event_handler_quote_update(inmessage):
    global SYMBOLDICT, DIRTY_KEYS, LATENCY_STATS
    try:
        # Calculate latency: ft is feed time (exchange time) in seconds
        now = time.time()
        ft = int(inmessage.get('ft', now))
        latency_ms = (now - ft) * 1000 if ft < now + 86400 else 0  # ignore if ft is in future (clock skew)
        if latency_ms < 0: latency_ms = 0
        if latency_ms > 10000: latency_ms = 0  # ignore unrealistic

        # Extract fields
        fields = ['ts', 'lp', 'pc', 'c', 'o', 'h', 'l', 'v', 'ltq', 'ap', 'bp1', 'sp1', 'bq1', 'sq1']
        message = {field: inmessage[field] for field in fields if field in inmessage}
        
        # Add metadata
        message['ft_str'] = str(datetime.datetime.fromtimestamp(ft)) if 'ft' in inmessage else ''
        message['latency_ms'] = round(latency_ms, 1)
        message['local_time'] = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        
        key = inmessage['e'] + '|' + inmessage['tk']
        
        with LOCK:
            if key in SYMBOLDICT:
                SYMBOLDICT[key].update(message)
            else:
                SYMBOLDICT[key] = message
            DIRTY_KEYS.add(key)
            if latency_ms > 0:
                LATENCY_STATS.append(latency_ms)

        # Optional: print only for first few to avoid spam
        # print(f"{inmessage.get('ts','')} LTP={inmessage.get('lp','')} latency={latency_ms:.1f}ms")

    except Exception as e:
        logger.error(f"Error in quote handler: {e} msg={inmessage}")

def open_callback():
    global socket_opened
    socket_opened = True
    print('WebSocket connected - subscribing to Nifty 50...')
    
    # Subscribe to all Nifty 50 - will be done in main after token resolution
    # Here we just signal connected

def get_time(time_string):
    data = time.strptime(time_string,'%d-%m-%Y %H:%M:%S')
    return time.mktime(data)

class ProgramKilled(Exception):
    pass

def signal_handler(signum, frame):
    raise ProgramKilled

def resolve_tokens(api, symbols):
    """
    Resolve NSE tokens for symbols via searchscrip.
    Returns list of NSE|token strings.
    Falls back to FALLBACK_TOKENS if search fails.
    """
    resolved = []
    failed = []
    print(f"\nResolving tokens for {len(symbols)} Nifty 50 symbols...")
    for sym in symbols:
        # Handle special chars: M&M needs URL encoding, but searchscrip handles it
        search_sym = sym
        try:
            ret = api.searchscrip(exchange='NSE', searchtext=search_sym)
            if ret and 'values' in ret and len(ret['values']) > 0:
                # Find exact match: tsym == SYM-EQ or SYM
                exact = None
                for val in ret['values']:
                    tsym = val.get('tsym','')
                    # Exact EQ match
                    if tsym == f"{sym}-EQ" or tsym == sym:
                        exact = val
                        break
                # Fallback: first result that contains symbol
                if not exact:
                    # Prefer EQ series
                    for val in ret['values']:
                        if '-EQ' in val.get('tsym','') and sym.replace('-','') in val.get('tsym','').replace('-',''):
                            exact = val
                            break
                if not exact:
                    exact = ret['values'][0]
                
                token = exact['token']
                tsym = exact['tsym']
                resolved.append(f"NSE|{token}")
                print(f"  {sym} -> {tsym} token {token}")
                time.sleep(0.1)  # avoid rate limit
            else:
                raise Exception("No values")
        except Exception as e:
            # Use fallback
            fb_token = FALLBACK_TOKENS.get(sym)
            if fb_token:
                resolved.append(f"NSE|{fb_token}")
                print(f"  {sym} -> fallback token {fb_token} (search failed: {e})")
                failed.append(sym)
            else:
                print(f"  {sym} FAILED - no token found: {e}")
                failed.append(sym)
    
    print(f"\nResolved {len(resolved)}/{len(symbols)} tokens, {len(failed)} using fallback/failed")
    if failed:
        print(f"Failed symbols: {failed}")
    return resolved

if __name__=="__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Load credentials - support both Windows and Linux paths
    cred_path = '../cred.yml'
    if not os.path.exists(cred_path):
        cred_path = '..\\cred.yml'
    if not os.path.exists(cred_path):
        cred_path = os.path.join(os.path.dirname(__file__), '..', 'cred.yml')
    
    if not os.path.exists(cred_path):
        print(f"cred.yml not found at {cred_path}, please create it from cred.yml template")
        sys.exit(1)

    with open(cred_path) as f:
        cred = yaml.load(f, Loader=yaml.FullLoader)
        print(f"Loaded creds for UID: {cred.get('UID','')}")

    # Excel setup
    excel_file = 'realtime_excel_feed.xlsx'
    # Also try absolute path in Tests folder
    if not os.path.isabs(excel_file):
        excel_file = os.path.join(os.path.dirname(__file__), excel_file)
    
    wb = None
    sht = None
    app = None

    if HAS_XLWINGS:
        try:
            # Create file if not exists
            if not os.path.exists(excel_file):
                print(f"Creating {excel_file}")
                workbook = xlsxwriter.Workbook(excel_file)
                ws = workbook.add_worksheet('Live')
                # Write header
                headers = ['Symbol','Token','LTP','%Chg','Open','High','Low','Close','AvgPrice','Volume','LTQ','FeedTime','LatencyMs','LocalTime','BQty','BPrice','SQty','SPrice']
                for col, h in enumerate(headers):
                    ws.write(0, col, h)
                workbook.close()
            
            print(f"Opening Excel file: {excel_file}")
            # Use xlwings
            wb = xw.Book(excel_file)
            # Try to get Live sheet, create if not exists
            try:
                sht = wb.sheets['Live']
            except:
                sht = wb.sheets.add('Live')
            
            app = wb.app
            # Optimize for speed
            app.screen_updating = True  # Keep True to see live, but we can toggle for batch
            # app.calculation = 'manual'  # Optional: manual calc for speed
            
            # Write header
            headers = ['Symbol','Token','LTP','%Chg','Open','High','Low','Close','AvgPrice','Volume','LTQ','FeedTime','LatencyMs','LocalTime','BQty','BPrice','SQty','SPrice']
            sht.range('A1').value = [headers]
            print("Excel sheet ready")
            
            # Pre-populate ROW_MAP for Nifty 50
            for idx, sym in enumerate(NIFTY_50_SYMBOLS, start=2):
                key = f"NSE|{FALLBACK_TOKENS.get(sym, '')}"
                ROW_MAP[key] = idx
                # Also map by symbol
                ROW_MAP[sym] = idx
            
        except Exception as e:
            print(f"Excel setup failed: {e}, will run in console mode")
            HAS_XLWINGS = False
    else:
        print("xlwings not available, running in console mode")

    # API setup - with SSL fix for Windows
    # For Windows Bootcamp if you get CERTIFICATE_VERIFY_FAILED, use disable_ssl=True
    try:
        api = NorenApiPy()  # secure default uses certifi
    except Exception as e:
        print(f"Failed to init with secure mode, trying insecure: {e}")
        api = NorenApiPy(disable_ssl=True)

    # Login - support both OAuth and old password login
    ret = None
    if 'Access_token' in cred and cred['Access_token']:
        print("Using OAuth login...")
        try:
            injected = api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
            api.set_credentials(cred['Access_token'], cred['UID'], cred['Account_ID'])
            ret = injected
        except Exception as e:
            print(f"OAuth inject failed: {e}")
            ret = None
    else:
        print("Using password login...")
        try:
            ret = api.login(userid=cred['user'], password=cred['pwd'], twoFA=cred['factor2'], 
                            vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])
        except Exception as e:
            print(f"Login failed: {e}")
            ret = None

    if ret is None:
        print("Login failed, check cred.yml")
        sys.exit(1)

    print("Login success")

    # Resolve tokens for Nifty 50
    # First try dynamic resolution if api has searchscrip working
    try:
        instruments = resolve_tokens(api, NIFTY_50_SYMBOLS)
    except Exception as e:
        print(f"Token resolution failed: {e}, using fallback tokens")
        instruments = [f"NSE|{FALLBACK_TOKENS[s]}" for s in NIFTY_50_SYMBOLS if s in FALLBACK_TOKENS]

    print(f"\nSubscribing to {len(instruments)} instruments with feed_type='{FEED_TYPE}'...")
    print(f"Instruments: {instruments[:5]}... (showing first 5)")

    # Start websocket with SSL fix
    # If you are on Windows Bootcamp and get CERTIFICATE_VERIFY_FAILED, uncomment disable_ssl=True
    try:
        api.start_websocket(
            order_update_callback=event_handler_order_update,
            subscribe_callback=event_handler_quote_update,
            socket_open_callback=open_callback
            # , disable_ssl=True  # uncomment for Windows Bootcamp workaround (insecure)
        )
    except Exception as e:
        print(f"Secure websocket failed: {e}, trying insecure...")
        api.start_websocket(
            order_update_callback=event_handler_order_update,
            subscribe_callback=event_handler_quote_update,
            socket_open_callback=open_callback,
            disable_ssl=True
        )

    # Wait for connection
    print("Waiting for websocket connection...")
    timeout = 10
    start = time.time()
    while not socket_opened and (time.time() - start) < timeout:
        time.sleep(0.1)
    
    if not socket_opened:
        print("Websocket not opened within timeout, continuing anyway...")

    # Subscribe
    try:
        # Subscribe in batches to avoid payload too large (some APIs limit to 25 per request)
        batch_size = 25
        for i in range(0, len(instruments), batch_size):
            batch = instruments[i:i+batch_size]
            api.subscribe(batch, feed_type=FEED_TYPE)
            print(f"Subscribed batch {i//batch_size+1}: {len(batch)} symbols")
            time.sleep(0.2)
    except Exception as e:
        print(f"Subscribe failed: {e}")

    print(f"\n=== LIVE FEED STARTED ===")
    print(f"Refresh interval: {REFRESH_INTERVAL}s ({int(REFRESH_INTERVAL*1000)}ms)")
    print(f"Press Ctrl+C to stop")
    print(f"Excel file: {excel_file}")

    # Build initial ROW_MAP from instruments
    with LOCK:
        for idx, inst in enumerate(instruments, start=2):
            ROW_MAP[inst] = idx
            # Also try to map symbol
            try:
                token = inst.split('|')[1]
                # Find symbol for token
                for sym, tok in FALLBACK_TOKENS.items():
                    if tok == token:
                        ROW_MAP[sym] = idx
            except:
                pass

    last_print = time.time()
    update_count = 0

    try:
        while True:
            if socket_opened:
                time.sleep(REFRESH_INTERVAL)
                
                with LOCK:
                    if not SYMBOLDICT:
                        continue
                    # Copy dirty keys and clear
                    dirty = list(DIRTY_KEYS)
                    DIRTY_KEYS.clear()
                    data = dict(SYMBOLDICT)  # shallow copy
                    latency_copy = list(LATENCY_STATS)

                if not dirty and HAS_XLWINGS:
                    # No new data, skip Excel update
                    continue

                # Excel update - only dirty rows for low latency
                if HAS_XLWINGS and sht and dirty:
                    try:
                        # For lowest latency, update only dirty rows
                        for key in dirty:
                            if key not in data:
                                continue
                            msg = data[key]
                            row = ROW_MAP.get(key)
                            if not row:
                                # Find new row
                                row = len(ROW_MAP) + 2
                                ROW_MAP[key] = row
                            
                            # Prepare row values in header order
                            # ['Symbol','Token','LTP','%Chg','Open','High','Low','Close','AvgPrice','Volume','LTQ','FeedTime','LatencyMs','LocalTime','BQty','BPrice','SQty','SPrice']
                            symbol = msg.get('ts', key)
                            token = key.split('|')[1] if '|' in key else ''
                            row_values = [
                                symbol,
                                token,
                                msg.get('lp',''),
                                msg.get('pc',''),
                                msg.get('o',''),
                                msg.get('h',''),
                                msg.get('l',''),
                                msg.get('c',''),
                                msg.get('ap',''),
                                msg.get('v',''),
                                msg.get('ltq',''),
                                msg.get('ft_str',''),
                                msg.get('latency_ms',''),
                                msg.get('local_time',''),
                                msg.get('bq1',''),
                                msg.get('bp1',''),
                                msg.get('sq1',''),
                                msg.get('sp1','')
                            ]
                            # Write single row - fastest for live
                            sht.range(f'A{row}:R{row}').value = [row_values]
                        
                        update_count += len(dirty)
                    except Exception as e:
                        print(f"Excel write error: {e}")
                        # Fallback to full DataFrame write
                        try:
                            df = pd.DataFrame.from_dict(data).transpose()
                            sht.range('A2').value = df
                        except Exception as e2:
                            print(f"Fallback Excel write also failed: {e2}")

                # Console stats every 2 sec
                if time.time() - last_print > 2:
                    avg_lat = sum(latency_copy)/len(latency_copy) if latency_copy else 0
                    max_lat = max(latency_copy) if latency_copy else 0
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Updates: {update_count} | Symbols: {len(data)} | Dirty: {len(dirty)} | Avg Latency: {avg_lat:.1f}ms | Max: {max_lat:.1f}ms | Refresh: {REFRESH_INTERVAL*1000:.0f}ms")
                    last_print = time.time()

            else:
                time.sleep(0.1)

    except ProgramKilled:
        print("\nProgram killed, cleaning up...")
    except KeyboardInterrupt:
        print("\nCtrl+C pressed, stopping...")
    finally:
        print(f"\nFinal stats: Total updates {update_count}, Avg latency {sum(LATENCY_STATS)/len(LATENCY_STATS) if LATENCY_STATS else 0:.1f}ms")
        if HAS_XLWINGS and wb:
            try:
                wb.save()
                print(f"Excel saved: {excel_file}")
            except Exception as e:
                print(f"Excel save failed: {e}")
        print("Done")
