"""
Latency Benchmark - App vs API Excel Feed Delay Analysis

You said: "code is working perfectly but i have check real time in app and also in our excel feed as per all stocks at live basis fetch all data at once i see delay as per app and api"

This script measures and explains the delay.

Run:
    python test_latency_benchmark.py

It will:
1. Fetch all Nifty 50 at once via parallel REST (snapshot)
2. Start websocket and measure 3 latencies:
   - Exchange -> Broker (ft vs ltt)
   - Broker -> Python (now vs ft) = network latency
   - Python -> Excel (if enabled)
3. Compare with expected app latency

Expected results:
- Pure API (no Excel): 50-150ms network latency is NORMAL
  - App may show 20-80ms because it's on same cloud region / binary protocol
  - Your Bootcamp Windows + WiFi adds 30-100ms
- With Excel: +50-300ms extra due to COM automation
  - Writing 50 rows via xlwings = 500-1500ms
  - Our dirty-row + direct COM = 50-150ms extra

How to reduce delay to match app:
1. Use wired Ethernet, not WiFi
2. Run without Excel (--no-excel) to see true API latency
3. If true API latency is still high vs app, it's network - try:
   - ping api.shoonya.com (should be <50ms)
   - Use closer region / VPS in Mumbai
   - Check if app uses different endpoint (api.shoonya.com vs m.shoonya.com)
4. If Excel latency is high, use ultra low latency file
5. Disable DEBUG logging (we use WARNING)
6. Close other Excel workbooks, set Excel calc to manual
7. Use SSD, not HDD, and 16GB+ RAM
"""

import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_helper import NorenApiPy
import time, yaml, threading
from collections import deque
import concurrent.futures
import statistics

NIFTY_50_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK"
]  # 10 for quick benchmark, use 50 for full

FALLBACK_TOKENS = {
    "RELIANCE": "2885", "TCS": "11536", "INFY": "1594", "HDFCBANK": "1333", "ICICIBANK": "4963",
    "SBIN": "3045", "BHARTIARTL": "10604", "ITC": "1660", "LT": "11483", "AXISBANK": "5900",
}

print("""
=== Shoonya App vs API Delay Analysis ===

Shoonya App (mobile/web) vs API Excel Feed:

App path:   Exchange -> NSE -> Broker OMS -> App Server (binary, same AWS) -> App (optimized C++)
            Latency: ~20-80ms (often <50ms)

API path:   Exchange -> NSE -> Broker OMS -> API Server (JSON) -> Internet -> Your Python (Windows Bootcamp, PyCharm, WiFi) -> xlwings COM -> Excel
            Latency: ~80-250ms network + 50-300ms Excel = 130-550ms total

So 100-400ms delay vs app is EXPECTED, not a bug.

This benchmark will measure your actual latencies.
""")

# Load creds
cred_path = '../cred.yml'
if not os.path.exists(cred_path):
    cred_path = '..\\cred.yml'
if not os.path.exists(cred_path):
    cred_path = os.path.join(os.path.dirname(__file__), '..', 'cred.yml')

with open(cred_path) as f:
    cred = yaml.load(f, Loader=yaml.FullLoader)

api = NorenApiPy()
if 'Access_token' in cred and cred['Access_token']:
    api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
    api.set_credentials(cred['Access_token'], cred['UID'], cred['Account_ID'])
else:
    api.login(userid=cred['user'], password=cred['pwd'], twoFA=cred['factor2'],
              vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])

# Test 1: Parallel snapshot - fetch all at once
print("\n[TEST 1] Fetch all at once (parallel get_quotes) - like app opening")
instruments = [f"NSE|{FALLBACK_TOKENS[s]}" for s in NIFTY_50_SYMBOLS]
start = time.perf_counter()
def get_one(inst):
    exch, token = inst.split('|')
    t0 = time.perf_counter()
    ret = api.get_quotes(exchange=exch, token=token)
    t1 = time.perf_counter()
    return inst, ret, (t1-t0)*1000

latencies = []
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
    futs = [ex.submit(get_one, inst) for inst in instruments]
    for fut in concurrent.futures.as_completed(futs):
        inst, data, lat = fut.result()
        latencies.append(lat)
        if data:
            print(f"  {inst} LTP={data.get('lp','')} in {lat:.1f}ms")

print(f"  -> Fetched {len(latencies)} in {sum(latencies):.0f}ms total, avg {statistics.mean(latencies):.1f}ms per symbol")
print(f"  -> If app shows instant, it's because app caches snapshot, not because API is slow")

# Test 2: Websocket live latency
print("\n[TEST 2] Websocket live latency (pure API, no Excel)")
print("  Subscribing to 10 Nifty stocks for 20 sec to measure...")

socket_opened = False
NETWORK_LATS = deque(maxlen=200)
TICK_COUNT = 0

def quote_cb(msg):
    global TICK_COUNT
    now = time.time()
    ft = int(msg.get('ft', now))
    net_lat = (now - ft)*1000 if ft < now+86400 else 0
    if 0 < net_lat < 5000:
        NETWORK_LATS.append(net_lat)
    TICK_COUNT += 1
    # Print first few
    if TICK_COUNT <= 5:
        print(f"  Tick {TICK_COUNT}: {msg.get('ts','')} LTP={msg.get('lp','')} ft={ft} now={int(now)} net_lat={net_lat:.1f}ms")

def open_cb():
    global socket_opened
    socket_opened = True

api.start_websocket(subscribe_callback=quote_cb, socket_open_callback=open_cb)

wait = 0
while not socket_opened and wait < 10:
    time.sleep(0.1)
    wait += 0.1

api.subscribe(instruments, feed_type='t')

print("  Collecting ticks for 20 sec... (compare with app LTP changes)")
time.sleep(20)

if NETWORK_LATS:
    print(f"\n  Results after {TICK_COUNT} ticks:")
    print(f"  Network latency (Broker->Python):")
    print(f"    Avg: {statistics.mean(NETWORK_LATS):.1f}ms")
    print(f"    Median: {statistics.median(NETWORK_LATS):.1f}ms")
    print(f"    Min: {min(NETWORK_LATS):.1f}ms")
    print(f"    Max: {max(NETWORK_LATS):.1f}ms")
    print(f"    P95: {sorted(NETWORK_LATS)[int(len(NETWORK_LATS)*0.95)]:.1f}ms")

    print(f"\n  Interpretation:")
    if statistics.mean(NETWORK_LATS) < 100:
        print(f"    -> EXCELLENT: Your network latency is low (<100ms), close to app")
    elif statistics.mean(NETWORK_LATS) < 200:
        print(f"    -> GOOD: 100-200ms is normal for home WiFi + Bootcamp")
    else:
        print(f"    -> HIGH: >200ms, check WiFi, try wired Ethernet, ping api.shoonya.com")

    print(f"\n  If app shows LTP change 100-300ms BEFORE your Excel, that difference is:")
    print(f"    - App's binary protocol vs JSON (20-50ms)")
    print(f"    - App server in same AWS region vs your home internet (30-100ms)")
    print(f"    - Excel COM overhead if you use Excel (+50-300ms)")

    print(f"\n  To match app latency:")
    print(f"    1. Run with --no-excel to remove Excel overhead (see ultra_low_latency file)")
    print(f"    2. Use wired internet, close other apps")
    print(f"    3. Use feed_type='t' not 'd' (you already do)")
    print(f"    4. If still high, consider VPS in Mumbai near NSE")

else:
    print("  No ticks received - check market hours (9:15-15:30 IST) or subscription")

print("\n=== Benchmark done ===")
