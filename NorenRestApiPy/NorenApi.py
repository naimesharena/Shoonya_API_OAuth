import json
import requests
import threading
import websocket
import logging
import enum
import datetime
import hashlib
import time
import urllib
import ssl
from time import sleep
from datetime import datetime as dt

logger = logging.getLogger(__name__)

# Try to use certifi for proper CA bundle on Windows/Mac
try:
    import certifi
    _DEFAULT_CA_BUNDLE = certifi.where()
except ImportError:
    _DEFAULT_CA_BUNDLE = None

class position:
    prd:str
    exch:str
    instname:str
    symname:str
    exd:int
    optt:str
    strprc:float
    buyqty:int
    sellqty:int
    netqty:int
    def encode(self):
        return self.__dict__

class ProductType:
    Delivery = 'C'
    Intraday = 'I'
    Normal   = 'M'
    CF       = 'M'

class FeedType:
    TOUCHLINE = 1    
    SNAPQUOTE = 2
    
class PriceType:
    Market = 'MKT'
    Limit = 'LMT'
    StopLossLimit = 'SL-LMT'
    StopLossMarket = 'SL-MKT'

class BuyorSell:
    Buy = 'B'
    Sell = 'S'
    
def reportmsg(msg):
    #print(msg)
    logger.debug(msg)

def reporterror(msg):
    #print(msg)
    logger.error(msg)

def reportinfo(msg):
    #print(msg)
    logger.info(msg)

class NorenApi:
    __service_config = {
      'host': 'http://wsapihost/',
      'routes': {
          'authorize': '/QuickAuth',
          'logout': '/Logout',
          'forgot_password': '/ForgotPassword',
          'change_password': '/Changepwd',
          'watchlist_names': '/MWList',
          'watchlist': '/MarketWatch',
          'watchlist_add': '/AddMultiScripsToMW',
          'watchlist_delete': '/DeleteMultiMWScrips',
          'placeorder': '/PlaceOrder',
          'modifyorder': '/ModifyOrder',
          'cancelorder': '/CancelOrder',
          'exitorder': '/ExitSNOOrder',
          'product_conversion': '/ProductConversion',
          'orderbook': '/OrderBook',
          'tradebook': '/TradeBook',          
          'singleorderhistory': '/SingleOrdHist',
          'searchscrip': '/SearchScrip',
          'TPSeries' : '/TPSeries',     
          'optionchain' : '/GetOptionChain',     
          'holdings' : '/Holdings',
          'limits' : '/Limits',
          'positions': '/PositionBook',
          'scripinfo': '/GetSecurityInfo',
          'getquotes': '/GetQuotes',
          'span_calculator' :'/SpanCalc',
          'option_greek' :'/GetOptionGreek',
          'get_daily_price_series' :'/EODChartData',
          'forgot_password_OTP':'/FgtPwdOTP',
          'gen_acs_tok':'/GenAcsTok',
          'exch_msg':'/ExchMsg',
          'linked_scrips':'/GetLinkedScrips'
      },
      'websocket_endpoint': 'wss://wsendpoint/',
      #'eoddata_endpoint' : 'http://eodhost/'
    }

    def __init__(self, host, websocket_url=None, websocket=None):
        # Support both websocket_url and websocket param names for backward compat
        ws_endpoint = websocket_url if websocket_url is not None else websocket
        if ws_endpoint is None:
            ws_endpoint = self.__service_config['websocket_endpoint']
        
        # Handle host param possibly being dict? Keep simple
        if host is not None:
            self.__service_config['host'] = host
        if ws_endpoint is not None:
            self.__service_config['websocket_endpoint'] = ws_endpoint
            
        self.__access_token = None
        self.__username = None
        self.__accountid = None        
        self.__websocket = None
        self.__websocket_connected = False
        self.__ws_mutex = threading.Lock()
        self.__on_error = None
        self.__on_disconnect = None
        self.__on_open = None
        self.__subscribe_callback = None
        self.__order_update_callback = None
        self.__subscribers = {}
        self.__market_status_messages = []
        self.__exchange_messages = []
        self.__OAuthHeaders = None
        # SSL fix: store sslopt for websocket
        self.__sslopt = None
        # For Windows Bootcamp / corporate proxy scenarios, allow disabling SSL verification
        self.__disable_ssl = False


    def __ws_run_forever(self):
        while self.__stop_event.is_set() == False:
            try:
                # FIX FOR SSL CERTIFICATE_VERIFY_FAILED on Windows / Bootcamp / corporate proxies
                # Pass sslopt if set. websocket-client's run_forever accepts sslopt dict.
                if self.__sslopt is not None:
                    self.__websocket.run_forever(ping_interval=3, ping_payload='{"t":"h"}', sslopt=self.__sslopt)
                else:
                    # Default: try to use certifi bundle if available (helps Windows)
                    if _DEFAULT_CA_BUNDLE is not None:
                        try:
                            self.__websocket.run_forever(
                                ping_interval=3,
                                ping_payload='{"t":"h"}',
                                sslopt={"ca_certs": _DEFAULT_CA_BUNDLE, "cert_reqs": ssl.CERT_REQUIRED}
                            )
                            continue
                        except TypeError:
                            # Older websocket-client may not support ca_certs in sslopt? Fallback
                            pass
                    self.__websocket.run_forever(ping_interval=3, ping_payload='{"t":"h"}')
            except Exception as e:
                # Provide helpful message for SSL errors
                err_str = str(e)
                if "CERTIFICATE_VERIFY_FAILED" in err_str or "self-signed certificate" in err_str:
                    logger.error(
                        "SSL verification failed. This often happens on Windows with antivirus/corporate proxy "
                        "or outdated CA bundle. Try: 1) pip install --upgrade certifi, "
                        "2) pip install pip-system-certs or python-certifi-win32, "
                        "3) Use disable_ssl=True or sslopt={'cert_reqs': ssl.CERT_NONE} for testing (insecure), "
                        "4) Check antivirus SSL scanning."
                    )
                logger.warning(f"websocket run forever ended in exception, {e}")
            
            sleep(0.1) # Sleep for 100ms between reconnection.

    def __ws_send(self, *args, **kwargs):
        while self.__websocket_connected == False:
            sleep(0.05)  # sleep for 50ms if websocket is not connected, wait for reconnection
        with self.__ws_mutex:
            ret = self.__websocket.send(*args, **kwargs)
        return ret


    def __on_close_callback(self, wsapp, close_status_code, close_msg):
        reportmsg(close_status_code)
        reportmsg(wsapp)

        self.__websocket_connected = False
        if self.__on_disconnect:
            self.__on_disconnect()
    
    def set_credentials(self, access_token, uid, accountid):
        self.__access_token = access_token
        self.__username = uid
        self.__accountid = accountid

    def __on_open_callback(self, ws=None):
        self.__websocket_connected = True

        #prepare the data
        values              = { "t": "a" }
        values["uid"]       = self.__username        
        values["actid"]     = self.__username
        values["accesstoken"]    = self.__access_token
        values["source"]    = 'API'   
           

        payload = json.dumps(values)

        reportmsg(payload)
        self.__ws_send(payload)

        #self.__resubscribe()
        

    def __on_error_callback(self, ws=None, error=None):
        if(type(ws) is not websocket.WebSocketApp): # This workaround is to solve the websocket_client's compatiblity issue of older versions. ie.0.40.0 which is used in upstox. Now this will work in both 0.40.0 & newer version of websocket_client
            error = ws
        # Enhanced SSL error reporting
        err_str = str(error)
        if "CERTIFICATE_VERIFY_FAILED" in err_str or "self-signed certificate" in err_str or "SSL" in err_str:
            reporterror(
                f"WebSocket SSL error: {error}. "
                f"On Windows Bootcamp/PyCharm, this is usually due to antivirus or missing CA certs. "
                f"Fix: pip install --upgrade certifi && pip install pip-system-certs, "
                f"or pass disable_ssl=True to start_websocket() for testing."
            )
        if self.__on_error:
            self.__on_error(error)

    def __on_data_callback(self, ws=None, message=None, data_type=None, continue_flag=None):
        #print(ws)
        #print(message)
        #print(data_type)
        #print(continue_flag)

        res = json.loads(message)
  
        if(self.__subscribe_callback is not None):
            if res['t'] == 'tk' or res['t'] == 'tf':
                self.__subscribe_callback(res)
                return
            if res['t'] == 'dk' or res['t'] == 'df':
                self.__subscribe_callback(res)
                return

        if(self.__on_error is not None):
            if res['t'] == 'ak' and res['s'] != 'OK':
                self.__on_error(res)
                return
            
        # if(self.__on_error is not None):
        #     if res['t'] == 'ck' and res['s'] != 'OK':
        #         self.__on_error(res)
        #         return

        if(self.__order_update_callback is not None):
            if res['t'] == 'om':
                self.__order_update_callback(res)
                return

        if self.__on_open:
            if res['t'] == 'ak' and res['s'] == 'OK':
                self.__on_open()
                return
            
        if self.__on_open:
            if res['t'] == 'ck' and res['s'] == 'OK':
                self.__on_open()
                return


    def start_websocket(self, subscribe_callback = None, 
                        order_update_callback = None,
                        socket_open_callback = None,
                        socket_close_callback = None,
                        socket_error_callback = None,
                        sslopt = None,
                        disable_ssl = False,
                        suppress_ssl_errors = False):        
        """ 
        Start a websocket connection for getting live data 
        
        Parameters for SSL fix (Windows Bootcamp / PyCharm issue):
        - sslopt: dict, optional. Passed directly to websocket-client's run_forever.
                  Example: {"cert_reqs": ssl.CERT_NONE} to disable verification (insecure, for testing)
                  Example: {"ca_certs": certifi.where()} to use certifi bundle
        - disable_ssl: bool, default False. If True, disables SSL verification (equivalent to sslopt={"cert_reqs": ssl.CERT_NONE})
                       Use only for testing/debugging on Windows where you get CERTIFICATE_VERIFY_FAILED
        - suppress_ssl_errors: bool, deprecated, alias for disable_ssl
        """
        self.__on_open = socket_open_callback
        self.__on_disconnect = socket_close_callback
        self.__on_error = socket_error_callback
        self.__subscribe_callback = subscribe_callback
        self.__order_update_callback = order_update_callback
        self.__stop_event = threading.Event()
        
        # Handle SSL options
        # Priority: explicit sslopt > disable_ssl flag > suppress_ssl_errors alias > default with certifi
        if disable_ssl or suppress_ssl_errors:
            self.__disable_ssl = True
            self.__sslopt = {"cert_reqs": ssl.CERT_NONE}
            logger.warning("SSL verification disabled for websocket (insecure). Use only for testing.")
        elif sslopt is not None:
            self.__sslopt = sslopt
            logger.info(f"Using custom sslopt for websocket: {sslopt}")
        else:
            # Default secure mode: try to use certifi bundle if available
            # This fixes most Windows issues without disabling security
            if _DEFAULT_CA_BUNDLE is not None:
                self.__sslopt = {"ca_certs": _DEFAULT_CA_BUNDLE, "cert_reqs": ssl.CERT_REQUIRED}
                logger.debug(f"Using certifi CA bundle: {_DEFAULT_CA_BUNDLE}")
            else:
                self.__sslopt = None
                logger.debug("No custom sslopt, using websocket-client default SSL verification")
        
        url = self.__service_config['websocket_endpoint'].format(access_token = self.__access_token)
        reportmsg('connecting to {}'.format(url))

        self.__websocket = websocket.WebSocketApp(url,
                                                on_data=self.__on_data_callback,
                                                on_error=self.__on_error_callback,
                                                on_close=self.__on_close_callback,
                                                on_open=self.__on_open_callback)
        #th = threading.Thread(target=self.__send_heartbeat)
        #th.daemon = True
        #th.start()
        #if run_in_background is True:
        self.__ws_thread = threading.Thread(target=self.__ws_run_forever)
        self.__ws_thread.daemon = True
        self.__ws_thread.start()
        
    def close_websocket(self):
        if self.__websocket_connected == False:
            return
        self.__stop_event.set()        
        self.__websocket_connected = False
        self.__websocket.close()
        self.__ws_thread.join()

    ###### OAuth Update ###### 
    def getOAuthURL(self, oauth_url, api_key=None): 
        default_login_uri = oauth_url 
        return "%s?client_id=%s" % (default_login_uri, api_key)

    def injectOAuthHeader(self,access_token,UID,AID): 
        headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
        }
        self.__OAuthHeaders = headers
        self.__username   = UID
        self.__accountid  = AID
        return headers
    
    def getAccessToken(self, authcode, Secret_Code, client_id, UID): 
        config = NorenApi.__service_config
        AcsTokURL = f"{config['host']}{config['routes']['gen_acs_tok']}" 
        reportmsg(AcsTokURL)
        GenAcsTokURL=AcsTokURL
        data_to_hash = (client_id + Secret_Code + authcode).encode("utf-8")
        app_verifier = hashlib.sha256(data_to_hash).hexdigest()

        values = {
            "code": authcode,
            "checksum": app_verifier,
            "uid": UID
        }

        payload = 'jData=' + json.dumps(values)
        reportmsg("Req:" + payload)

        res = requests.post(GenAcsTokURL, data=payload)
        reportmsg("Response:" + res.text)
        resDict = json.loads(res.text)
        if "access_token" in resDict:
            asc_tok = resDict['access_token']
            usrid = resDict['USERID']
            ref_tok = resDict['refresh_token']
            actid = resDict['actid']
            self.__susertoken = resDict['susertoken']

            self.__username   = usrid
            self.__accountid  = actid
            self.__access_token = asc_tok
            self.injectOAuthHeader(asc_tok,usrid,actid)
            return asc_tok , usrid , ref_tok, actid

        else:        
            reportmsg(f"Error occured: {resDict}")
            return None

      
    ###### OAuth Update ###### 

    """
    def login(self, userid, password, twoFA, vendor_code, api_secret, imei,access_type=None):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['authorize']}" 
        reportmsg(url)

        #Convert to SHA 256 for password and app key
        pwd = hashlib.sha256(password.encode('utf-8')).hexdigest()
        u_appkey = '{0}|{1}'.format(userid, api_secret)
        appkey=hashlib.sha256(u_appkey.encode('utf-8')).hexdigest()
        #prepare the data
        if access_type == None:
            values = { "source": "API" , "apkversion": "1.0.0"}
        else:
            values = { "source": access_type , "apkversion": "1.0.0"}
        values["uid"]       = userid
        values["pwd"]       = pwd
        values["factor2"]   = twoFA
        values["vc"]        = vendor_code
        values["appkey"]    = appkey        
        values["imei"]      = imei        

        payload = 'jData=' + json.dumps(values)
        reportmsg("Req:" + payload)

        res = requests.post(url, data=payload)
        reportmsg("Reply:" + res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None
        
        self.__username   = userid
        self.__accountid  = userid
        self.__password   = password
        self.__susertoken = resDict['susertoken']
        #reportmsg(self.__susertoken)

        return resDict
    """
        
    def set_session(self, userid, password, usertoken,accesstoken):
        
        self.__username   = userid
        self.__accountid  = userid
        self.__password   = password
        self.__susertoken = usertoken
        self.__access_token = accesstoken

        reportmsg(f'{userid} session set to : {self.__susertoken}')

        return True

    def forgot_password(self, userid, pan, dob):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['forgot_password']}" 
        reportmsg(url)

        #prepare the data
        values              = { "source": "API" }
        values["uid"]       = userid
        values["pan"]       = pan
        values["dob"]       = dob

        payload = 'jData=' + json.dumps(values)
        reportmsg("Req:" + payload)

        res = requests.post(url, data=payload)
        reportmsg("Reply:" + res.text)

        resDict = json.loads(res.text)
        
        if resDict['stat'] != 'Ok':            
            return None
        
        return resDict

    def logout(self):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['logout']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)

        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        self.__username   = None
        self.__accountid  = None
        self.__password   = None
        self.__susertoken = None
        self.__access_token = None

        return resDict

    def subscribe(self, instrument, feed_type=FeedType.TOUCHLINE):
        values = {}

        if(feed_type == FeedType.TOUCHLINE):
            values['t'] =  't'
        elif(feed_type == FeedType.SNAPQUOTE):
            values['t'] =  'd'
        else:
            values['t'] =  str(feed_type)

        if type(instrument) == list:
            values['k'] = '#'.join(instrument)
        else :
            values['k'] = instrument

        data = json.dumps(values)

        #print(data)
        self.__ws_send(data)

    def unsubscribe(self, instrument, feed_type=FeedType.TOUCHLINE):
        values = {}

        if(feed_type == FeedType.TOUCHLINE):
            values['t'] =  'u'
        elif(feed_type == FeedType.SNAPQUOTE):
            values['t'] =  'ud'
        
        if type(instrument) == list:
            values['k'] = '#'.join(instrument)
        else :
            values['k'] = instrument

        data = json.dumps(values)

        #print(data)
        self.__ws_send(data)

    def subscribe_orders(self):
        values = {'t': 'o'}
        values['actid'] = self.__accountid        

        data = json.dumps(values)

        reportmsg(data)
        self.__ws_send(data)

    def get_watch_list_names(self):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['watchlist_names']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def get_watch_list(self, wlname):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['watchlist']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["wlname"]    = wlname
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict


    def add_watch_list_scrip(self, wlname, instrument):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['watchlist_add']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["wlname"]    = wlname

        if type(instrument) == list:
            values['scrips'] = '#'.join(instrument)
        else :
            values['scrips'] = instrument
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'   
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def delete_watch_list_scrip(self, wlname, instrument):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['watchlist_delete']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["wlname"]    = wlname

        if type(instrument) == list:
            values['scrips'] = '#'.join(instrument)
        else :
            values['scrips'] = instrument
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict


    def place_order(self, buy_or_sell, product_type,
                    exchange, tradingsymbol, quantity, discloseqty,
                    price_type, price=0.0, trigger_price=None,
                    retention='DAY', amo=None, remarks=None, bookloss_price = 0.0, bookprofit_price = 0.0, trail_price = 0.0, algo_id=None):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['placeorder']}" 
        reportmsg(url)
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid
        values["trantype"]  = buy_or_sell
        values["prd"]       = product_type
        values["exch"]      = exchange
        values["tsym"]      = urllib.parse.quote_plus(tradingsymbol)
        values["qty"]       = str(quantity)
        values["dscqty"]    = str(discloseqty)        
        values["prctyp"]    = price_type
        values["prc"]       = str(price)
        values["trgprc"]    = str(trigger_price)
        values["ret"]       = retention
        values["remarks"]   = remarks
        values["algo_id"]       = algo_id

      
        if amo is not None:
           values["amo"]       = amo
        
        #if cover order or high leverage order
        if product_type == 'H':            
            values["blprc"]       = str(bookloss_price)
            #trailing price
            if trail_price != 0.0:
                values["trailprc"] = str(trail_price)

        #bracket order
        if product_type == 'B':            
            values["blprc"]       = str(bookloss_price)
            values["bpprc"]       = str(bookprofit_price)
            #trailing price
            if trail_price != 0.0:
                values["trailprc"] = str(trail_price)

        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def modify_order(self, orderno, exchange, tradingsymbol, newquantity,
                    newprice_type, newprice=0.0, newtrigger_price=None, bookloss_price = 0.0, bookprofit_price = 0.0, trail_price = 0.0):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['modifyorder']}" 
        print(url)

        #prepare the data
        values                  = {'ordersource':'API'}
        values["uid"]           = self.__username
        values["actid"]         = self.__accountid
        values["norenordno"]    = str(orderno)
        values["exch"]          = exchange
        values["tsym"]          = urllib.parse.quote_plus(tradingsymbol)
        values["qty"]           = str(newquantity)
        values["prctyp"]        = newprice_type        
        values["prc"]           = str(newprice)

        if (newprice_type == 'SL-LMT') or (newprice_type == 'SL-MKT'):
            if (newtrigger_price != None):
                values["trgprc"] = str(newtrigger_price)
            else:
                reporterror('trigger price is missing')
                return None

        #if cover order or high leverage order
        if bookloss_price != 0.0:            
            values["blprc"]       = str(bookloss_price)
        #trailing price
        if trail_price != 0.0:
            values["trailprc"] = str(trail_price)         
        #book profit of bracket order   
        if bookprofit_price != 0.0:
            values["bpprc"]       = str(bookprofit_price)
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def cancel_order(self, orderno):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['cancelorder']}" 
        print(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["norenordno"]    = str(orderno)
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        print(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def exit_order(self, orderno, product_type):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['exitorder']}" 
        print(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["norenordno"]    = orderno
        values["prd"]           = product_type
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        if resDict['stat'] != 'Ok':            
            return None

        return resDict

    def position_product_conversion(self, exchange, tradingsymbol, quantity, new_product_type, previous_product_type, buy_or_sell, day_or_cf):
        '''
        Coverts a day or carryforward position from one product to another. 
        '''
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['product_conversion']}" 
        print(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid        
        values["exch"]      = exchange
        values["tsym"]      = urllib.parse.quote_plus(tradingsymbol)
        values["qty"]       = str(quantity)
        values["prd"]       = new_product_type
        values["prevprd"]   = previous_product_type
        values["trantype"]  = buy_or_sell
        values["postype"]   = day_or_cf
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        
        if resDict['stat'] != 'Ok':            
            return None

        return resDict


    def single_order_history(self, orderno):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['singleorderhistory']}" 
        print(url)
        
        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["norenordno"]    = orderno
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        #error is a json with stat and msg wchih we printed earlier.
        if type(resDict) != list:                            
                return None

        return resDict


    def get_order_book(self):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['orderbook']}" 
        reportmsg(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        
        #error is a json with stat and msg wchih we printed earlier.
        if type(resDict) != list:                            
                return None

        return resDict

    def get_trade_book(self):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['tradebook']}" 
        reportmsg(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        
        #error is a json with stat and msg wchih we printed earlier.
        if type(resDict) != list:                            
                return None

        return resDict

    def searchscrip(self, exchange, searchtext):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['searchscrip']}" 
        reportmsg(url)
        
        if searchtext == None:
            reporterror('search text cannot be null')
            return None
        
        values              = {}
        values["uid"]       = self.__username
        values["exch"]      = exchange
        values["stext"]     = urllib.parse.quote_plus(searchtext)       
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if resDict['stat'] != 'Ok':            
            return None        

        return resDict

    def get_option_chain(self, exchange, tradingsymbol, strikeprice, count=2):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['optionchain']}" 
        reportmsg(url)
        
        
        values              = {}
        values["uid"]       = self.__username
        values["exch"]      = exchange
        values["tsym"]      = urllib.parse.quote_plus(tradingsymbol)       
        values["strprc"]    = str(strikeprice)
        values["cnt"]       = str(count)       
        
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if resDict['stat'] != 'Ok':            
            return None        

        return resDict

    def get_security_info(self, exchange, token):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['scripinfo']}" 
        reportmsg(url)        
        
        values              = {}
        values["uid"]       = self.__username
        values["exch"]      = exchange
        values["token"]     = token       
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if resDict['stat'] != 'Ok':            
            return None        

        return resDict

    def get_quotes(self, exchange, token):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['getquotes']}" 
        reportmsg(url)        
        
        values              = {}
        values["uid"]       = self.__username
        values["exch"]      = exchange
        values["token"]     = token       
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if resDict['stat'] != 'Ok':            
            return None        

        return resDict

    def get_time_price_series(self, exchange, token, starttime=None, endtime=None, interval= None):
        '''
        gets the chart data 
        interval possible values 1, 3, 5 , 10, 15, 30, 60, 120, 240
        '''
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['TPSeries']}" 
        reportmsg(url)

        #prepare the data
        if starttime == None:
            timestring = time.strftime('%d-%m-%Y') + ' 00:00:00'
            timeobj = time.strptime(timestring,'%d-%m-%Y %H:%M:%S')
            starttime = time.mktime(timeobj)

        #
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["exch"]      = exchange
        values["token"]     = token
        values["st"] = str(starttime)
        if endtime != None:
            values["et"]   = str(endtime)
        if interval != None:
            values["intrv"] = str(interval)

        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        
        #error is a json with stat and msg wchih we printed earlier.
        if type(resDict) != list:                            
                return None

        return resDict

    def _parse_eod_response(self, res_text):
        """
        Helper to parse EOD response which is often list of JSON strings like:
        ['{"time":"11-SEP-2026", ...}', '{"time":"10-SEP-2026", ...}']
        Returns list of dicts
        """
        try:
            resDict = json.loads(res_text)
        except Exception as e:
            reporterror(f"Failed to parse EOD response: {e}")
            return None

        if type(resDict) != list:
            # If it's a dict with error, return None
            return None

        parsed = []
        for item in resDict:
            if isinstance(item, dict):
                parsed.append(item)
            elif isinstance(item, str):
                try:
                    # Each item is a JSON string
                    parsed.append(json.loads(item))
                except:
                    # Try to handle single-quoted or escaped?
                    try:
                        # Sometimes it's already a stringified dict with single quotes? Try eval fallback
                        parsed.append(json.loads(item.replace("'", '"')))
                    except:
                        continue
            else:
                continue
        return parsed

    def get_daily_price_series(self, exchange, tradingsymbol, startdate=None, enddate=None, _chunked=False):
        """
        Gets daily price series. 

        NOTE: Shoonya's EODChartData endpoint typically returns max ~5 years / ~1250 candles per request.
        If you request from=0 (1970) to now, you'll only get last 5 years (2021-2026).
        To get full history, this function now auto-chunks if range > 2 years, or you can use
        get_daily_price_series_full() for 10-20 years.

        Args:
            exchange: e.g. 'NSE'
            tradingsymbol: e.g. 'RELIANCE-EQ'
            startdate: timestamp (seconds since epoch), or 0 for epoch, or None for 1 week ago
            enddate: timestamp, or None for now
        Returns:
            list of dicts with keys: time, into, inth, intl, intc, intv, ssboe, etc.
        """
        config = NorenApi.__service_config
        url = f"{config['host']}{config['routes']['get_daily_price_series']}" 
        reportmsg(url)

        # Handle startdate / enddate conversion
        if startdate == None:  
            week_ago = datetime.date.today() - datetime.timedelta(days=7)
            startdate = dt.combine(week_ago, dt.min.time()).timestamp()
            startdate = float(startdate)
        else:
            try:
                startdate = float(startdate)
            except:
                # If it's a date string, try to parse
                try:
                    startdate = dt.strptime(str(startdate), '%d-%m-%Y').timestamp()
                except:
                    startdate = float(startdate) if str(startdate).replace('.','').isdigit() else 0

        if enddate == None:            
            enddate = dt.now().timestamp()
            enddate = float(enddate)
        else:
            try:
                enddate = float(enddate)
            except:
                try:
                    enddate = dt.strptime(str(enddate), '%d-%m-%Y').timestamp()
                except:
                    enddate = float(enddate)

        # If range is huge (>2 years) and not already in chunked mode, auto-chunk
        # 2 years = 63072000 seconds
        TWO_YEARS = 2 * 365 * 24 * 3600
        if not _chunked and (enddate - startdate) > TWO_YEARS:
            logger.info(f"Large date range detected ({(enddate-startdate)/86400:.0f} days), auto-chunking into 2-year pieces to get full history beyond 5-year limit")
            return self.get_daily_price_series_full(exchange, tradingsymbol, startdate, enddate, chunk_years=2)

        values              = {}
        values["uid"]       = self.__username
        values["sym"]      = '{0}:{1}'.format(exchange, tradingsymbol)
        values["from"]     = str(startdate)
        values["to"]       = str(enddate)
        
        payload = 'jData=' + json.dumps(values)
        reportmsg(payload)

        # Retry for single request too
        for attempt in range(3):
            try:
                res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
                reportmsg(res)

                if res.status_code != 200:
                    reporterror(f"EOD request failed with status {res.status_code}: {res.text[:500]}")
                    if attempt < 2:
                        time.sleep(0.5 + attempt)
                        continue
                    return None

                if len(res.text) == 0:
                    return []

                parsed = self._parse_eod_response(res.text)
                if parsed is None:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return None

                return parsed
            except Exception as e:
                reporterror(f"EOD request attempt {attempt+1} exception: {e}")
                if attempt < 2:
                    time.sleep(0.5 + attempt)
                    continue
                return None

        return None

    def get_daily_price_series_full(self, exchange, tradingsymbol, startdate=0, enddate=None, chunk_years=2):
        """
        Fetch FULL history beyond 5-year API limit by chunking.

        Shoonya's EODChartData returns max ~5 years per request. This helper loops
        in chunks (default 2 years) to get 10, 15, 20+ years.

        Args:
            exchange: 'NSE'
            tradingsymbol: 'RELIANCE-EQ'
            startdate: timestamp, 0 = epoch (1970), or date string '01-01-2000', or timestamp
            enddate: timestamp, None = now
            chunk_years: years per chunk, default 2 (safe). Use 1 for more reliability.

        Returns:
            list of dicts sorted by date ascending (oldest first), deduplicated

        Example:
            # Get 10 years
            ten_years_ago = datetime.now() - timedelta(days=10*365)
            data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=ten_years_ago.timestamp())

            # Get all available (from 2000)
            data = api.get_daily_price_series_full('NSE', 'RELIANCE-EQ', startdate=0)
            df = pd.DataFrame(data)
        """
        config = NorenApi.__service_config
        url = f"{config['host']}{config['routes']['get_daily_price_series']}" 

        # Normalize startdate
        if startdate is None or startdate == 0:
            startdate = 0.0
        else:
            try:
                startdate = float(startdate)
            except:
                try:
                    # Try date string formats
                    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d-%m-%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                        try:
                            startdate = dt.strptime(str(startdate), fmt).timestamp()
                            break
                        except:
                            continue
                    else:
                        startdate = float(startdate)
                except:
                    startdate = 0.0

        if enddate is None:
            enddate = dt.now().timestamp()
        else:
            try:
                enddate = float(enddate)
            except:
                try:
                    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d-%m-%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
                        try:
                            enddate = dt.strptime(str(enddate), fmt).timestamp()
                            break
                        except:
                            continue
                    else:
                        enddate = float(enddate)
                except:
                    enddate = dt.now().timestamp()

        chunk_seconds = chunk_years * 365 * 24 * 3600
        all_data = []
        seen_times = set()

        cur_from = startdate
        chunk_idx = 0

        logger.info(f"Fetching full history for {exchange}:{tradingsymbol} from {cur_from} to {enddate} in {chunk_years}-year chunks")

        # Optimize startdate=0: NSE started 1992, BSE earlier, but 1990 is safe minimum to avoid empty chunks 1970-1990
        NSE_START = dt(1990, 1, 1).timestamp()
        if cur_from < NSE_START and cur_from == 0.0:
            logger.info(f"Startdate 0 detected, optimizing to {NSE_START} (1990-01-01) to avoid empty 1970-1990 chunks")
            cur_from = NSE_START

        while cur_from < enddate:
            cur_to = min(cur_from + chunk_seconds, enddate)

            values = {}
            values["uid"] = self.__username
            values["sym"] = f'{exchange}:{tradingsymbol}'
            values["from"] = str(cur_from)
            values["to"] = str(cur_to)

            payload = 'jData=' + json.dumps(values)
            reportmsg(f"Chunk {chunk_idx}: {cur_from} to {cur_to} payload {payload}")

            # Retry logic for robustness (handles SSL EOF, 429, timeout)
            retries = 3
            success = False
            for attempt in range(retries):
                try:
                    res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
                    if res.status_code != 200:
                        reporterror(f"Chunk {chunk_idx} attempt {attempt+1} failed status {res.status_code}: {res.text[:200]}")
                        if res.status_code == 429:
                            time.sleep(1 + attempt*2)
                        else:
                            time.sleep(0.5 + attempt*0.5)
                        continue

                    if len(res.text) == 0:
                        logger.info(f"Chunk {chunk_idx} empty response")
                        success = True
                        break

                    # Check for error JSON like {"stat":"Not_Ok", "emsg":"..."}
                    if res.text.strip().startswith('{'):
                        try:
                            err_json = json.loads(res.text)
                            if isinstance(err_json, dict) and err_json.get('stat') != 'Ok':
                                # Might be "No data" - not fatal
                                if 'No data' in str(err_json.get('emsg','')) or 'No Data' in str(err_json):
                                    logger.info(f"Chunk {chunk_idx} no data: {err_json}")
                                    success = True
                                    break
                        except:
                            pass

                    parsed = self._parse_eod_response(res.text)
                    if parsed is None:
                        reporterror(f"Chunk {chunk_idx} parse failed, retrying")
                        time.sleep(0.5)
                        continue

                    if isinstance(parsed, list):
                        new_count = 0
                        for item in parsed:
                            t = item.get('time') or item.get('ssboe')
                            if t not in seen_times:
                                seen_times.add(t)
                                all_data.append(item)
                                new_count += 1
                        logger.info(f"Chunk {chunk_idx}: got {len(parsed)} candles, {new_count} new, total {len(all_data)}")
                        success = True
                        break
                    else:
                        logger.info(f"Chunk {chunk_idx}: no data or error")

                except requests.exceptions.SSLError as e:
                    reporterror(f"Chunk {chunk_idx} attempt {attempt+1} SSL error: {e} - retrying with backoff")
                    time.sleep(1 + attempt*1.5)
                    # Try to create fresh session if using pooled session
                    try:
                        # If requests.post is wrapped by api_helper's session, try to close and retry
                        pass
                    except:
                        pass
                except requests.exceptions.ConnectionError as e:
                    reporterror(f"Chunk {chunk_idx} attempt {attempt+1} Connection error: {e}")
                    time.sleep(1 + attempt)
                except Exception as e:
                    reporterror(f"Chunk {chunk_idx} attempt {attempt+1} exception: {e}")
                    time.sleep(0.5 + attempt*0.5)

            if not success:
                reporterror(f"Chunk {chunk_idx} failed after {retries} retries, skipping")

            cur_from = cur_to + 86400  # next day
            chunk_idx += 1
            time.sleep(0.35)  # rate limit to avoid 429

            # Safety break
            if chunk_idx > 60:
                reporterror("Too many chunks, breaking")
                break

        # Sort by ssboe (seconds since epoch) ascending
        try:
            all_data.sort(key=lambda x: float(x.get('ssboe', 0)))
        except:
            # Sort by time string if ssboe fails
            try:
                all_data.sort(key=lambda x: dt.strptime(x.get('time','01-JAN-2000'), '%d-%b-%Y'))
            except:
                pass

        logger.info(f"Full history fetch complete: {len(all_data)} total candles for {tradingsymbol}")
        return all_data
        
    def get_holdings(self, product_type = None):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['holdings']}" 
        reportmsg(url)
        
        if product_type == None:
            product_type = ProductType.Delivery
        
        values              = {}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid
        values["prd"]       = product_type       
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)

        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if type(resDict) != list:                            
                return None

        return resDict

    def get_limits(self, product_type = None, segment = None, exchange = None):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['limits']}" 
        reportmsg(url)        
        
        values              = {}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid
        
        if product_type != None:
            values["prd"]       = product_type       
        
        if product_type != None:
            values["seg"]       = segment       
        
        if exchange != None:
            values["exch"]       = exchange       
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)        

        return resDict

    def get_positions(self):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['positions']}" 
        reportmsg(url)        
        
        values              = {}
        values["uid"]       = self.__username
        values["actid"]     = self.__accountid
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)

        if type(resDict) != list:                            
            return None

        return resDict

    def span_calculator(self,actid,positions:list):
        config = NorenApi.__service_config
        #prepare the uri
        url = f"{config['host']}{config['routes']['span_calculator']}" 
        reportmsg(url) 

        senddata = {}
        senddata['actid'] =self.__accountid 
        senddata['pos'] = positions
        #payload = 'jData=' + json.dumps(senddata,default=lambda o: o.encode())+ f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(senddata,default=lambda o: o.encode())

        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)        

        return resDict
        
    def option_greek(self,expiredate,StrikePrice,SpotPrice,InterestRate,Volatility,OptionType):
        config = NorenApi.__service_config 

        #prepare the uri
        url = f"{config['host']}{config['routes']['option_greek']}" 
        reportmsg(url)

        #prepare the data
        values               = { "source": "API" }
        values["actid"]     = self.__accountid
        values["exd"]        = expiredate
        values["strprc"]     = StrikePrice 
        values["sptprc"]     = SpotPrice
        values["int_rate"]   = InterestRate	
        values["volatility"] = Volatility
        values["optt"]       = OptionType

        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)

        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)        

        return resDict
   
    def forgot_password_OTP(self, userid, pan):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['forgot_password_OTP']}" 
        reportmsg(url)

        #prepare the data
        values              = { "source": "API" }
        values["uid"]       = userid
        values["pan"]       = pan

        payload = 'jData=' + json.dumps(values)
        reportmsg("Req:" + payload)
        
        res = requests.post(url, data=payload)
        reportmsg(res.text)

        resDict = json.loads(res.text)        

        return resDict
    
    def exch_msg(self, exchange):
        config = NorenApi.__service_config

        #prepare the uri
        url = f"{config['host']}{config['routes']['exch_msg']}" 
        print(url)

        #prepare the data
        values              = {'ordersource':'API'}
        values["uid"]       = self.__username
        values["exch"]           = exchange
        
        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)
        
        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)
        
        return resDict

    
    def linked_scrips(self,token,exchange):
        config = NorenApi.__service_config 

        #prepare the uri
        url = f"{config['host']}{config['routes']['linked_scrips']}" 
        reportmsg(url)

        #prepare the data
        values               = { "source": "API" }
        values["uid"]       = self.__username
        values["token"]     = token
        values["exch"]  = exchange

        #payload = 'jData=' + json.dumps(values) + f'&jKey={self.__susertoken}'
        payload = 'jData=' + json.dumps(values)

        reportmsg(payload)

        res = requests.post(url, data=payload, headers=self.__OAuthHeaders)
        reportmsg(res.text)

        resDict = json.loads(res.text)        

        return resDict
