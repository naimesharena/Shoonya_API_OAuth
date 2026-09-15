import os, sys
# Ensure repo root is on sys.path for `api_helper` import
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# FIX 1: ImportError fix
# `api_helper.py` defines `NorenApiPy`. Older code used `ShoonyaApiPy` which did not exist.
# `api_helper` now provides `ShoonyaApiPy = NorenApiPy` alias, so BOTH imports work.
# Preferred import is NorenApiPy (consistent with all other Tests/).
from api_helper import NorenApiPy
# If you have old code that does `from api_helper import ShoonyaApiPy` it will also work now
# via the alias added in api_helper.py:
#   ShoonyaApiPy = NorenApiPy
try:
    from api_helper import ShoonyaApiPy  # noqa: F401 - keep for backward compat check
except ImportError:
    ShoonyaApiPy = NorenApiPy  # fallback if running against older api_helper

import signal
import datetime
import logging
import time
import yaml
import pandas as pd
import xlsxwriter

try:
    import xlwings as xw
    HAS_XLWINGS = True
except ImportError:
    HAS_XLWINGS = False
    xw = None

# sample - DEBUG shows websocket pings
logging.basicConfig(level=logging.DEBUG)

# flag to tell us if the websocket is open
socket_opened = False
socket_error_msg = None
socket_closed = False

# application callbacks
def event_handler_order_update(message):
    print("order event: " + str(message))

def event_handler_error(message):
    global socket_error_msg
    socket_error_msg = message
    print(f"!!! WEBSOCKET ERROR/AUTH FAILED: {message}")
    logging.error(f"WS ERROR: {message}")
    # Common auth failure: {'t':'ak', 's':'NOT_OK', 'emsg': 'Invalid...'}
    # If you see this, run test_oauth.py again to refresh Access_token

def event_handler_close(code=None, reason=None):
    global socket_closed
    socket_closed = True
    print(f"!!! WEBSOCKET CLOSED: code={code} reason={reason}")
    logging.warning(f"WS CLOSED: {code} {reason}")

SYMBOLDICT = {}
def event_handler_quote_update(inmessage):
    global SYMBOLDICT
    # e   Exchange
    # tk  Token
    # lp  LTP
    # pc  Percentage change
    # v   volume
    # o   Open price
    # h   High price
    # l   Low price
    # c   Close price
    # ap  Average trade price

    # Log RAW message first - helps debug no-feed issues
    print(f"quote event RAW: {inmessage}")

    fields = ['ts', 'lp', 'pc', 'c', 'o', 'h', 'l', 'v', 'ltq', 'ltp']

    # safely filter only available fields
    message = {field: inmessage[field] for field in set(fields) & set(inmessage.keys())}

    # FIX: 'ft' may be missing in some ticks - guard with .get()
    if 'ft' in inmessage:
        try:
            feedtime = int(inmessage['ft'])
            message['ft'] = str(datetime.datetime.fromtimestamp(feedtime))
        except Exception:
            message['ft'] = str(inmessage.get('ft'))

    print(f"quote event filtered: {message}")

    # Some messages (e.g. ack) may not have e/tk - skip those for SYMBOLDICT
    if 'e' not in inmessage or 'tk' not in inmessage:
        print(f"Skipping SYMBOLDICT update - missing e/tk: {inmessage}")
        return

    key = inmessage['e'] + '|' + inmessage['tk']

    if key in SYMBOLDICT:
        symbol_info = SYMBOLDICT[key]
        symbol_info.update(message)
        SYMBOLDICT[key] = symbol_info
    else:
        SYMBOLDICT[key] = message

    # keep transpose for debugging / excel
    df_debug = pd.DataFrame.from_dict(SYMBOLDICT).transpose()
    print(f"SYMBOLDICT updated [{key}]:\n{df_debug}")


def open_callback():
    global socket_opened
    socket_opened = True
    print('*** app is connected - websocket AUTH OK, subscribing ***')
    # Subscribe after auth - you should get t='tk' ack immediately even if market closed
    # Try both feed types if one fails. 't'=touchline, 'd'=depth
    try:
        # Default TOUCHLINE
        api.subscribe(["NSE|22", "NSE|13", "BSE|522032"], feed_type='t')
        print("Subscribed to NSE|22, NSE|13, BSE|522032 with feed_type='t'")
        # Also subscribe to Nifty index as sanity check (always has data)
        # api.subscribe("NSE|26000", feed_type='t')
    except Exception as e:
        print(f"Subscribe failed: {e}")
        logging.error(f"Subscribe failed: {e}")


# end of callbacks

def get_time(time_string):
    data = time.strptime(time_string, '%d-%m-%Y %H:%M:%S')
    return time.mktime(data)


class ProgramKilled(Exception):
    pass


def signal_handler(signum, frame):
    raise ProgramKilled


def find_cred_file():
    """Cross-platform cred.yml discovery - works from Tests/ or repo root."""
    candidates = [
        os.path.join(os.path.dirname(__file__), '..', 'cred.yml'),
        os.path.join(os.path.dirname(__file__), 'cred.yml'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cred.yml'),
        'cred.yml',
        '../cred.yml',
        '..\\cred.yml',  # legacy Windows path - kept for reference
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    # default to repo root
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'cred.yml'))


if __name__ == "__main__":

    # Register our signal handler with `SIGINT`(CTRL + C)
    signal.signal(signal.SIGINT, signal_handler)
    # Register the exit handler with `SIGTERM` (Ctrl + Z)
    signal.signal(signal.SIGTERM, signal_handler)

    # start of our program
    api = NorenApiPy()  # or ShoonyaApiPy() - both now work via alias

    # FIX 2: cross-platform cred path (old code used '..\\cred.yml' which fails on Linux/Mac)
    cred_path = find_cred_file()
    print(f"Loading credentials from: {cred_path}")
    try:
        with open(cred_path) as f:
            cred = yaml.load(f, Loader=yaml.FullLoader)
            print(cred)
    except FileNotFoundError:
        print(f"ERROR: cred.yml not found at {cred_path}")
        print("Create cred.yml from test_oauth.py flow or copy from cred.yml.example")
        sys.exit(1)

    # FIX 3: excel file path - make it relative to this file, not cwd
    excel_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'realtime_excel_feed.xlsx')

    # FIX 4: inverted logic - old code created file only IF it exists.
    # Correct is to create when it does NOT exist (or reset if needed).
    if not os.path.exists(excel_file):
        print(f"Creating new excel file: {excel_file}")
        workbook = xlsxwriter.Workbook(excel_file)
        workbook.add_worksheet('Live')
        workbook.close()
    else:
        print(f"Using existing excel file: {excel_file}")

    # Try to open with xlwings (requires Excel). Fallback gracefully if not available.
    sht = None
    wb1 = None
    if HAS_XLWINGS:
        try:
            wb1 = xw.Book(excel_file)
            # ensure sheet 'Live' exists
            try:
                sht = wb1.sheets('Live')
            except Exception:
                wb1.sheets.add('Live')
                sht = wb1.sheets('Live')
            print(f"xlwings opened {excel_file} -> sheet Live ready")
        except Exception as e:
            logging.warning(f"xlwings could not open workbook (Excel may not be installed/running): {e}")
            logging.warning("Falling back to openpyxl/xlsxwriter mode - live Excel update disabled, data will be printed to console and saved via xlsxwriter on exit.")
            print("Tip: On Linux without Excel, the file will be updated on exit, not live. Open with LibreOffice/Excel manually.")
            sht = None
    else:
        logging.warning("xlwings not installed - live Excel update disabled. Install with `pip install xlwings` if you need live Excel. Data will be printed to console.")
        print("xlwings not found - Excel will not update live. Data will print to console and snapshot saved on exit.")

    # FIX 5: OAuth login - old code used api.login() with user/pwd/factor2/vc/apikey/imei
    # This project now uses OAuth (see test_oauth.py, example_market.py, cred.yml)
    # cred.yml now contains Access_token, UID, Account_ID, Secret_Code, client_id, oauth_url
    # Use injectOAuthHeader + set_credentials
    # We support BOTH flows: OAuth (preferred) and legacy login (fallback)

    def has_valid_oauth(cred):
        required_oauth = ['Access_token', 'UID', 'Account_ID']
        for k in required_oauth:
            v = cred.get(k)
            if not v or str(v).strip() in ('', 'Your account id', 'Your client_id', 'None'):
                return False
            # Access_token placeholder check - if it still contains '#it will be auto picked' skip
            if k == 'Access_token' and 'auto picked' in str(v):
                return False
        return True

    def has_legacy_creds(cred):
        # legacy login expects user / pwd / factor2 / vc / apikey / imei
        legacy_keys = [
            ('user', 'userid', 'UID', 'uid'),
            ('pwd', 'password', 'passwd'),
            ('factor2', 'twoFA', 'otp', 'totp'),
            ('vc', 'vendor_code'),
            ('apikey', 'api_secret', 'Secret_Code', 'secret_code'),
            ('imei',),
        ]
        found = 0
        for group in legacy_keys:
            if any(cred.get(k) and str(cred.get(k)).strip() not in ('', 'None') for k in group):
                found += 1
        return found >= 4

    ret = None
    login_mode = None

    if has_valid_oauth(cred):
        login_mode = 'oauth'
        print("Using OAuth flow (injectOAuthHeader)")
        # Debug: mask token
        tok = cred['Access_token']
        print(f"  UID={cred['UID']} Account_ID={cred['Account_ID']} Token={tok[:8]}...{tok[-4:]} (len={len(tok)})")
        ret = api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
        print(f"injectOAuthHeader ret: {ret}")
        if ret is not None:
            # Set credentials for websocket (required before start_websocket)
            api.set_credentials(
                cred['Access_token'],
                cred['UID'],
                cred['Account_ID']
            )
            print("set_credentials done")
        else:
            print("injectOAuthHeader FAILED - check cred.yml")
    elif has_legacy_creds(cred):
        login_mode = 'legacy'
        logging.warning("OAuth credentials not found, falling back to legacy api.login() - consider migrating to OAuth via test_oauth.py")
        userid = cred.get('user') or cred.get('userid') or cred.get('UID') or cred.get('uid')
        password = cred.get('pwd') or cred.get('password') or cred.get('passwd')
        twoFA = cred.get('factor2') or cred.get('twoFA') or cred.get('otp') or cred.get('totp')
        vc = cred.get('vc') or cred.get('vendor_code')
        api_secret = cred.get('apikey') or cred.get('api_secret') or cred.get('Secret_Code') or cred.get('secret_code')
        imei = cred.get('imei') or 'abc123'
        print(f"Attempting legacy login with userid={userid}, vc={vc}")
        # Note: login is commented out in NorenRestApiPy OAuth version - would need custom handling
        print("Legacy login not supported in OAuth build - please use OAuth (test_oauth.py)")
        sys.exit(1)
    else:
        logging.error("Missing credentials in cred.yml")
        logging.error("OAuth requires: Access_token, UID, Account_ID (run test_oauth.py to generate)")
        print("\n=== CRED.YML DIAGNOSTICS ===")
        print(f"Current cred keys: {list(cred.keys())}")
        print(f"Values: {cred}")
        print("\nTo fix:")
        print("1. Run: python test_oauth.py")
        print("   - It will print OAuth URL -> login -> copy auth_code -> generates Access_token")
        print("2. Check cred.yml has Access_token, UID, Account_ID filled (not placeholder)")
        print("3. Re-run this script")
        print("See README 'getOAuthURL' / 'getAccessToken' / 'injectOAuthHeader' flow.")
        sys.exit(1)

    if ret is None:
        print("OAuth header injection failed - check cred.yml Access_token / UID / Account_ID")
        sys.exit(1)

    # Start websocket with error/close callbacks for diagnostics
    # start_websocket returns None (runs in background thread) - that's NORMAL
    print("\n=== Starting websocket ===")
    print("Note: start_websocket ret: None is NORMAL - websocket runs in background thread")
    ws_ret = api.start_websocket(
        order_update_callback=event_handler_order_update,
        subscribe_callback=event_handler_quote_update,
        socket_open_callback=open_callback,
        socket_close_callback=event_handler_close,
        socket_error_callback=event_handler_error
    )
    print(f"start_websocket ret: {ws_ret} (None is expected)")

    # Wait for auth with timeout - diagnose pings-with-no-feed issue
    print("\nWaiting for websocket auth (expect 'app is connected' within 5-10 sec)...")
    print("DEBUG log 'Sending ping' is heartbeat - auth should happen before pings")
    wait_start = time.time()
    timeout = 15  # seconds to wait for open_callback
    printed_diagnostics = False
    while not socket_opened and not socket_error_msg and not socket_closed:
        time.sleep(0.5)
        elapsed = time.time() - wait_start
        if elapsed > timeout:
            print("\n!!! TIMEOUT: websocket connected but NO auth ack (no 'app is connected') !!!")
            print("This means Access_token / UID / Account_ID is INVALID or EXPIRED")
            print(f"  Used: UID={cred['UID']} Account_ID={cred['Account_ID']} Token len={len(cred['Access_token'])}")
            print("  Common fixes:")
            print("  1. Token expired? Re-run test_oauth.py to get fresh Access_token")
            print("  2. Wrong UID/Account_ID? Check cred.yml matches token response")
            print("  3. Check logs for 't':'ak','s':'NOT_OK' error above")
            print("  4. Try REST test: api.get_limits() should return dict, not None")
            # Test REST as diagnostic
            try:
                limits = api.get_limits()
                print(f"  REST diagnostic get_limits(): {limits}")
                if limits is None:
                    print("  -> REST also failed -> token definitely invalid")
                else:
                    print("  -> REST OK but WS auth failed -> check websocket URL / set_credentials")
            except Exception as e:
                print(f"  REST diagnostic error: {e}")
            printed_diagnostics = True
            break
        if int(elapsed) % 5 == 0 and elapsed > 1:
            print(f"  ... still waiting for auth ({int(elapsed)}s) - pings are normal heartbeat")

    if socket_error_msg:
        print(f"\nWebsocket error detected: {socket_error_msg}")
        print("Fix token and re-run.")
        # Don't exit immediately, let loop try to handle
    elif socket_closed:
        print("\nWebsocket closed before auth - check credentials")
        sys.exit(1)
    elif socket_opened:
        print(">> Auth OK - now entering live update loop. You should see 'quote event RAW' within seconds.")
        print("   If still no feed after 10 sec, market may be closed (you should still get tk ack).")
        print("   Excel should update every 5 sec if xlwings available, else console prints.")
    elif printed_diagnostics:
        print("Continuing anyway to see if feed arrives... (if not, Ctrl+C and fix token)")

    # Main live loop - now that websocket is (hopefully) authenticated
    try:
        loop_count = 0
        while True:
            try:
                if socket_opened:
                    time.sleep(5)
                    loop_count += 1
                    # Always show loop status even if no data yet
                    print(f"\n--- Loop {loop_count} @ {time.strftime('%d-%m-%Y %H:%M:%S')} | SYMBOLDICT keys: {list(SYMBOLDICT.keys())} | socket_opened={socket_opened} ---")
                    if not SYMBOLDICT:
                        print("No ticks yet - waiting for t='tk'/'tf' ack...")
                        print("If market is closed, you should still get tk ack with last price. If no ack for 30s, subscription failed.")
                        # Try re-subscribe as diagnostic after 3 loops
                        if loop_count == 3:
                            print("Retrying subscribe diagnostic...")
                            try:
                                api.subscribe(["NSE|22"], feed_type='t')
                                print("Re-subscribe sent for NSE|22")
                            except Exception as e:
                                print(f"Re-subscribe failed: {e}")
                        continue
                    df = pd.DataFrame.from_dict(SYMBOLDICT).transpose()
                    print(f"DataFrame to Excel/console:\n{df}")
                    if sht is not None:
                        try:
                            sht.range('A1').value = df
                            print("Excel updated via xlwings")
                        except Exception as e:
                            print(f"Excel write error: {e}")
                            # Fallback to openpyxl periodically
                            try:
                                with pd.ExcelWriter(excel_file, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                                    df.to_excel(writer, sheet_name='Live')
                                print("Fallback openpyxl write succeeded")
                            except Exception as e2:
                                print(f"Fallback also failed: {e2}")
                    else:
                        # Fallback: print to console and also save snapshot via openpyxl
                        print(df)
                        try:
                            # Save snapshot each loop if no xlwings
                            with pd.ExcelWriter(excel_file, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                                df.to_excel(writer, sheet_name='Live')
                        except FileNotFoundError:
                            with pd.ExcelWriter(excel_file, engine='openpyxl', mode='w') as writer:
                                df.to_excel(writer, sheet_name='Live')
                        except Exception as e:
                            print(f"Snapshot save failed: {e}")
                    continue
                else:
                    # Wait for socket to open (with error check)
                    if socket_error_msg:
                        print(f"Socket error prevents feed: {socket_error_msg}")
                        time.sleep(1)
                    elif socket_closed:
                        print("Socket closed - exiting loop")
                        break
                    else:
                        time.sleep(0.5)
                    continue

            except ProgramKilled:
                print("Program killed: running cleanup code")
                break
            except KeyboardInterrupt:
                print("KeyboardInterrupt: exiting")
                break
    finally:
        # Cleanup - save final snapshot if needed
        print("\n=== Cleanup ===")
        if SYMBOLDICT:
            try:
                df = pd.DataFrame.from_dict(SYMBOLDICT).transpose()
                try:
                    with pd.ExcelWriter(excel_file, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                        df.to_excel(writer, sheet_name='Live')
                except FileNotFoundError:
                    with pd.ExcelWriter(excel_file, engine='openpyxl', mode='w') as writer:
                        df.to_excel(writer, sheet_name='Live')
                print(f"Saved final snapshot to {excel_file} with {len(df)} rows")
            except Exception as e:
                print(f"Could not save final snapshot: {e}")
        if wb1 is not None:
            try:
                wb1.save()
                print("Workbook saved via xlwings")
            except Exception as e:
                print(f"xlwings save failed: {e}")
        print("Done. If feed was empty, check diagnostics above (token, subscription, market hours).")
