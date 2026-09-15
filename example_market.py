from api_helper import NorenApiPy, get_time
import datetime
import logging
import time
import yaml
import pandas as pd

#sample
logging.basicConfig(level=logging.DEBUG)

#flag to tell us if the websocket is open
socket_opened = False

#application callbacks
def event_handler_order_update(message):
    print("order event: " + str(message))


def event_handler_quote_update(message):
    #e   Exchange
    #tk  Token
    #lp  LTP
    #pc  Percentage change
    #v   volume
    #o   Open price
    #h   High price
    #l   Low price
    #c   Close price
    #ap  Average trade price

    print("quote event: {0}".format(time.strftime('%d-%m-%Y %H:%M:%S')) + str(message))
    

def open_callback():
    global socket_opened
    socket_opened = True
    print('app is connected')
    
    api.subscribe('NSE|11630')
    #api.subscribe(['NSE|22', 'BSE|522032'])

#end of callbacks

def get_time(time_string):
    data = time.strptime(time_string,'%d-%m-%Y %H:%M:%S')

    return time.mktime(data)

#start of our program
api = NorenApiPy()

#use following if yaml isnt used
#user    = <uid>
#pwd     = <password>
#factor2 = <2nd factor>
#vc      = <vendor code>
#apikey  = <secret key>
#imei    = <imei>

#ret = api.login(userid = user, password = pwd, twoFA=factor2, vendor_code=vc, api_secret=apikey, imei=imei)

#yaml for parameters
with open('cred.yml') as f:
    cred = yaml.load(f, Loader=yaml.FullLoader)
    print(cred)

#ret = api.login(userid = cred['user'], password = cred['pwd'], twoFA=cred['factor2'], vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])
ret = injected_headers = api.injectOAuthHeader(cred['Access_token'],cred['UID'],cred['Account_ID'])

if ret != None:   
    while True:
        print('f => find symbol')    
        print('m => get quotes')
        print('p => contract info n properties')    
        print('v => get 1 min market data')
        print('t => get today 1 min market data')
        print('d => get daily data (full history)')
        print('y => get daily data for YEAR 2010 only')
        print('o => get option chain')
        print('s => start_websocket')
        print('q => quit')

        prompt1=input('what shall we do? ').lower()                    
        
        if prompt1 == 'v':
            start_time = "13-07-2021 09:10:00"
            #end_time = time.time()
            
            start_secs = get_time(start_time)

            end_time = get_time("13-07-2021 09:20:00")
            ret = api.get_time_price_series(exchange='NSE', token='22', starttime=start_secs, endtime=end_time)
            
            df = pd.DataFrame.from_dict(ret)
            print(df)            
            print(f'{start_secs} to {end_time}')

        elif prompt1 == 't':
            ret = api.get_time_price_series(exchange='NSE', token='22')
            
            df = pd.DataFrame.from_dict(ret)
            print(df)            
            

        elif prompt1 == 'f':
            exch  = 'NFO'
            query = 'BANKNIFTY 30DEC CE'
            ret = api.searchscrip(exchange=exch, searchtext=query)
            print(ret)

            if ret != None:
                symbols = ret['values']
                for symbol in symbols:
                    print('{0} token is {1}'.format(symbol['tsym'], symbol['token']))

        elif prompt1 == 'd':
            exch  = 'NSE'
            tsym = 'RELIANCE-EQ'
            print("\n=== Daily Data - Why only 5 years? ===")
            print("Shoonya's EODChartData endpoint has a limit of ~1250 candles / ~5 years per request.")
            print("When you pass startdate=0 (1970), it returns only last 5 years (2021-2026 in your log).")
            print("Your log shows: list of JSON strings like '{\"time\":\"11-SEP-2026\",...}'")
            print("This is fixed now - auto-parses stringified JSON and auto-chunks for full history.\n")

            # Option 1: Old way - now auto-chunked and parsed (returns list of dicts)
            print("Option 1: api.get_daily_price_series(startdate=0) - now auto-chunked for full history")
            ret = api.get_daily_price_series(exchange=exch, tradingsymbol=tsym, startdate=0)
            print(f"Got {len(ret) if ret else 0} candles (should be >1250 if full history)")
            if ret:
                print(f"First: {ret[0]}")
                print(f"Last: {ret[-1]}")
                df = pd.DataFrame.from_dict(ret)
                print(df.head())
                print(df.tail())
                # Convert time to datetime for analysis
                try:
                    df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
                    print(f"\nDate range: {df['time_dt'].min()} to {df['time_dt'].max()} = {(df['time_dt'].max()-df['time_dt'].min()).days} days")
                except Exception as e:
                    print(f"Date parse error: {e}")

            # Option 2: Explicit full history with 1-year chunks for 10+ years
            print("\n\nOption 2: api.get_daily_price_series_full() - explicit full history (recommended for >5 years)")
            print("Fetching 10 years in 1-year chunks...")
            import datetime as dt
            ten_years_ago = (dt.datetime.now() - dt.timedelta(days=10*365)).timestamp()
            ret_full = api.get_daily_price_series_full(exchange=exch, tradingsymbol=tsym, startdate=ten_years_ago, chunk_years=1)
            print(f"Got {len(ret_full) if ret_full else 0} candles for 10 years")
            if ret_full:
                df_full = pd.DataFrame.from_dict(ret_full)
                print(df_full.head())
                print(df_full.tail())
                print(f"\nTo get 20 years: api.get_daily_price_series_full(exch, tsym, startdate=0, chunk_years=2)")

            # Option 3: Show raw vs parsed difference
            print("\n\nDebug: Old response was list of strings, new is list of dicts")
            print("Old: ['{\"time\":\"11-SEP-2026\", \"into\":\"1267.00\",...}', ...] (200k bytes, ~1250 items)")
            print("New: [{'time':'11-SEP-2026', 'into':'1267.00', ...}, ...] parsed dicts")
            print("Fix: _parse_eod_response() does json.loads on each string element")

        elif prompt1 == 'y':
            # NEW: Fetch data only for year 2010
            exch = 'NSE'
            tsym = 'RELIANCE-EQ'
            print("\n=== Daily Data for YEAR 2010 Only ===")
            import datetime as dt
            start_2010 = dt.datetime(2010, 1, 1, 0, 0, 0).timestamp()
            end_2010 = dt.datetime(2010, 12, 31, 23, 59, 59).timestamp()
            print(f"Fetching {exch}:{tsym} from 2010-01-01 to 2010-12-31")
            print(f"  start={start_2010} (2010-01-01), end={end_2010} (2010-12-31)")

            # Method 1: simple
            print("\nMethod 1: get_daily_price_series(startdate=2010-01-01, enddate=2010-12-31)")
            ret_2010 = api.get_daily_price_series(exchange=exch, tradingsymbol=tsym, startdate=start_2010, enddate=end_2010)
            print(f"Got {len(ret_2010) if ret_2010 else 0} candles for 2010")
            if ret_2010:
                print(f"First: {ret_2010[0]}")
                print(f"Last: {ret_2010[-1]}")
                df = pd.DataFrame.from_dict(ret_2010)
                print(df.head())
                print(df.tail())
                try:
                    df['time_dt'] = pd.to_datetime(df['time'], format='%d-%b-%Y')
                    print(f"Date range: {df['time_dt'].min()} to {df['time_dt'].max()}")
                except Exception as e:
                    print(f"Date parse: {e}")

            # Method 2: full method (more robust)
            print("\nMethod 2: get_daily_price_series_full for 2010")
            ret_2010_full = api.get_daily_price_series_full(exchange=exch, tradingsymbol=tsym, startdate=start_2010, enddate=end_2010, chunk_years=1)
            print(f"Got {len(ret_2010_full) if ret_2010_full else 0} candles via full method")
            if ret_2010_full:
                print(f"First: {ret_2010_full[0].get('time')} to Last: {ret_2010_full[-1].get('time')}")

            # Method 3: Nifty 50 for 2010 parallel
            print("\nMethod 3: Nifty 50 for 2010 all at once (parallel, 5 workers)")
            NIFTY_10 = ["RELIANCE-EQ", "TCS-EQ", "INFY-EQ", "HDFCBANK-EQ", "ICICIBANK-EQ"]
            try:
                import concurrent.futures
                def fetch_2010(sym):
                    d = api.get_daily_price_series(exchange='NSE', tradingsymbol=sym, startdate=start_2010, enddate=end_2010)
                    return sym, len(d) if d else 0, d[0].get('time') if d else None, d[-1].get('time') if d else None
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                    for f in concurrent.futures.as_completed([ex.submit(fetch_2010, s) for s in NIFTY_10]):
                        sym, cnt, first, last = f.result()
                        print(f"  {sym}: {cnt} candles, {first} to {last}")
            except Exception as e:
                print(f"Parallel error: {e}")

            print("\nTo fetch any year, change:")
            print("  start = datetime(YYYY,1,1).timestamp()")
            print("  end = datetime(YYYY,12,31,23,59,59).timestamp()")
            print("  api.get_daily_price_series(exch, tsym, startdate=start, enddate=end)")

        elif prompt1 == 'p':
            exch  = 'NSE'
            token = '22'
            ret = api.get_security_info(exchange=exch, token=token)
            print(ret)

        elif prompt1 == 'm':
            exch  = 'NSE'
            token = '22'
            ret = api.get_quotes(exchange=exch, token=token)
            print(ret)
        elif prompt1 == 'o':
            exch  = 'NFO'
            tsym = 'COFORGE30DEC21F'
            chain = api.get_option_chain(exchange=exch, tradingsymbol=tsym, strikeprice=3500, count=2)

            chainscrips = []
            for scrip in chain['values']:
                scripdata = api.get_quotes(exchange=scrip['exch'], token=scrip['token'])
                chainscrips.append(scripdata)

            print(chainscrips)

        elif prompt1 == 's':
            if socket_opened == True:
                print('websocket already opened')
                continue

            # Set credentials safely
            api.set_credentials(
                cred['Access_token'],
                cred['UID'],
                cred['Account_ID']
            )
                
            ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback)
            print(ret)

        else:
            ret = api.logout()
            print(ret)
            print('Fin') #an answer that wouldn't be yes or no
            break

    
