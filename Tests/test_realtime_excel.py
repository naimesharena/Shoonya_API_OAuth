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

# sample
logging.basicConfig(level=logging.DEBUG)

# flag to tell us if the websocket is open
socket_opened = False

# application callbacks
def event_handler_order_update(message):
    print("order event: " + str(message))


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
    elif 'ft' in message:
        pass

    print("quote event: {0}".format(time.strftime('%d-%m-%Y %H:%M:%S')) + str(inmessage))
    print(message)

    key = inmessage['e'] + '|' + inmessage['tk']

    if key in SYMBOLDICT:
        symbol_info = SYMBOLDICT[key]
        symbol_info.update(message)
        SYMBOLDICT[key] = symbol_info
    else:
        SYMBOLDICT[key] = message

    # keep transpose for debugging / excel
    pd.DataFrame.from_dict(SYMBOLDICT).transpose()
    # print(SYMBOLDICT[key])


def open_callback():
    global socket_opened
    socket_opened = True
    print('app is connected')
    api.subscribe(["NSE|22", "NSE|13", "BSE|522032"], feed_type='t')
    # api.subscribe(['NSE|22', 'BSE|522032'])


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
    with open(cred_path) as f:
        cred = yaml.load(f, Loader=yaml.FullLoader)
        print(cred)

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
        except Exception as e:
            logging.warning(f"xlwings could not open workbook (Excel may not be installed): {e}")
            logging.warning("Falling back to openpyxl/xlsxwriter mode - live Excel update disabled, data will be printed to console and saved via xlsxwriter on exit.")
            sht = None
    else:
        logging.warning("xlwings not installed - live Excel update disabled. Install with `pip install xlwings` if you need live Excel. Data will be printed to console.")

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
        # also handle alternative key names (userid, password, etc.)
        legacy_keys = [
            ('user', 'userid', 'UID', 'uid'),
            ('pwd', 'password', 'passwd'),
            ('factor2', 'twoFA', 'otp', 'totp'),
            ('vc', 'vendor_code'),
            ('apikey', 'api_secret', 'Secret_Code', 'secret_code'),
            ('imei',),
        ]
        # check at least 4 of the groups have a value
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
        ret = api.injectOAuthHeader(cred['Access_token'], cred['UID'], cred['Account_ID'])
        print(f"injectOAuthHeader ret: {ret}")
        if ret is not None:
            # Set credentials for websocket (required before start_websocket)
            api.set_credentials(
                cred['Access_token'],
                cred['UID'],
                cred['Account_ID']
            )
    elif has_legacy_creds(cred):
        login_mode = 'legacy'
        logging.warning("OAuth credentials not found, falling back to legacy api.login() - consider migrating to OAuth via test_oauth.py")
        # map legacy keys flexibly
        userid = cred.get('user') or cred.get('userid') or cred.get('UID') or cred.get('uid')
        password = cred.get('pwd') or cred.get('password') or cred.get('passwd')
        twoFA = cred.get('factor2') or cred.get('twoFA') or cred.get('otp') or cred.get('totp')
        vc = cred.get('vc') or cred.get('vendor_code')
        api_secret = cred.get('apikey') or cred.get('api_secret') or cred.get('Secret_Code') or cred.get('secret_code')
        imei = cred.get('imei') or 'abc123'
        print(f"Attempting legacy login with userid={userid}, vc={vc}")
        ret = api.login(userid=userid, password=password, twoFA=twoFA, vendor_code=vc, api_secret=api_secret, imei=imei)
        print(f"login ret: {ret}")
    else:
        logging.error("Missing credentials in cred.yml")
        logging.error("OAuth requires: Access_token, UID, Account_ID (run test_oauth.py to generate)")
        logging.error("Legacy requires: user, pwd, factor2, vc, apikey, imei")
        logging.error(f"Current cred keys: {list(cred.keys())}")
        logging.error("See README 'getOAuthURL' / 'getAccessToken' / 'injectOAuthHeader' flow.")
        sys.exit(1)

    if ret is not None:
        # For OAuth, set_credentials already done above. Ensure websocket can start.
        # For legacy login, no extra header needed - login already set session.
        # (If you use OAuth but hit this path via fallback, ensure credentials are set)
        if login_mode == 'oauth':
            # already set, but keep for safety if ret was set before set_credentials
            try:
                api.set_credentials(cred['Access_token'], cred['UID'], cred['Account_ID'])
            except Exception:
                pass

        ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback)
        print(f"start_websocket ret: {ret}")

        while True:
            try:
                if socket_opened is True:
                    time.sleep(5)
                    if not SYMBOLDICT:
                        continue
                    df = pd.DataFrame.from_dict(SYMBOLDICT).transpose()
                    # print(df)
                    if sht is not None:
                        try:
                            sht.range('A1').value = df
                        except Exception as e:
                            print(f"Excel write error: {e}")
                    else:
                        # Fallback: print to console and optionally append to file via xlsxwriter interval
                        print(df)
                    continue
                else:
                    # wait for socket to open
                    time.sleep(0.5)
                    continue

            except ProgramKilled:
                print("Program killed: running cleanup code")
                # Optional: save final snapshot if xlwings not used
                if sht is None and SYMBOLDICT:
                    try:
                        df = pd.DataFrame.from_dict(SYMBOLDICT).transpose()
                        # try append mode first, fallback to write mode if file missing
                        try:
                            with pd.ExcelWriter(excel_file, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
                                df.to_excel(writer, sheet_name='Live')
                        except FileNotFoundError:
                            with pd.ExcelWriter(excel_file, engine='openpyxl', mode='w') as writer:
                                df.to_excel(writer, sheet_name='Live')
                        print(f"Saved snapshot to {excel_file}")
                    except Exception as e:
                        print(f"Could not save snapshot: {e}")
                # close workbook if opened
                if wb1 is not None:
                    try:
                        wb1.save()
                        # wb1.close()  # uncomment if you want to close Excel
                    except Exception:
                        pass
                break
            except KeyboardInterrupt:
                print("KeyboardInterrupt: exiting")
                break
    else:
        print("OAuth header injection failed - check cred.yml Access_token / UID / Account_ID")
