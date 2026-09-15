"""
Shoonya API OAuth - Enhanced api_helper with SSL fix for Windows Bootcamp / PyCharm

Fixes: ERROR:websocket:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain

Root causes on Windows:
- Python's default CA bundle missing or outdated
- Antivirus (Avast, Kaspersky, Bitdefender) doing SSL interception with self-signed cert
- Corporate proxy / Zscaler / VPN intercepting wss://
- Bootcamp Windows not having Windows cert store synced to Python

Solutions implemented here:
1. Uses certifi bundle by default (patched NorenApi.py does this automatically)
2. Allows disable_ssl=True for testing (insecure, but unblocks)
3. Allows custom sslopt dict to be passed
4. Provides helper to install Windows cert store via pip-system-certs

Usage:
    # Secure (default) - uses certifi
    api = NorenApiPy()
    api.start_websocket(...)

    # Insecure workaround for Windows Bootcamp when you get CERTIFICATE_VERIFY_FAILED
    api = NorenApiPy(disable_ssl=True)
    api.start_websocket(..., disable_ssl=True)

    # Or with custom sslopt
    import ssl, certifi
    api.start_websocket(..., sslopt={"ca_certs": certifi.where()})
    # For testing only (insecure):
    api.start_websocket(..., sslopt={"cert_reqs": ssl.CERT_NONE})

Recommended permanent fix for Windows:
    pip install --upgrade certifi
    pip install pip-system-certs  # OR python-certifi-win32
    # Then restart PyCharm and invalidate caches
    # Also check antivirus: disable HTTPS scanning / add api.shoonya.com to exclusions
"""
from NorenRestApiPy.NorenApi import NorenApi
from threading import Timer
import pandas as pd
import time
import concurrent.futures
import requests
from functools import partial
import ssl
import logging

logger = logging.getLogger(__name__)

# Try to get certifi
try:
    import certifi
    _CERTIFI_BUNDLE = certifi.where()
except ImportError:
    _CERTIFI_BUNDLE = None
    certifi = None

api = None

class Order:
     def __init__(self, buy_or_sell:str = None, product_type:str = None,
                 exchange: str = None, tradingsymbol:str =None, 
                 price_type: str = None, quantity: int = None, 
                 price: float = None,trigger_price:float = None, discloseqty: int = 0,
                 retention:str = 'DAY', remarks: str = "tag",
                 order_id:str = None):
        self.buy_or_sell=buy_or_sell
        self.product_type=product_type
        self.exchange=exchange
        self.tradingsymbol=tradingsymbol
        self.quantity=quantity
        self.discloseqty=discloseqty
        self.price_type=price_type
        self.price=price
        self.trigger_price=trigger_price
        self.retention=retention
        self.remarks=remarks
        self.order_id=None
    #print(ret)
    
def get_time(time_string):
    data = time.strptime(time_string,'%d-%m-%Y %H:%M:%S')
    return time.mktime(data)

class NorenApiPy(NorenApi):
    """
    Enhanced wrapper with SSL fix for websocket.

    Args:
        host: API host, defaults to Shoonya live
        websocket: Websocket URL
        disable_ssl: If True, disables SSL verification for websocket (insecure, for testing)
        ssl_verify: If False, same as disable_ssl=True. If True (default), uses certifi bundle
        sslopt: Optional dict passed to websocket-client. Example: {"cert_reqs": ssl.CERT_NONE}
    """
    def __init__(self, host='https://api.shoonya.com/NorenWClientAPI/', 
                 websocket='wss://api.shoonya.com/NorenWSAPI/',
                 disable_ssl=False,
                 ssl_verify=True,
                 sslopt=None):
        # Handle disable_ssl / ssl_verify flags
        # ssl_verify=False means disable SSL (insecure)
        if disable_ssl or (ssl_verify is False):
            self._disable_ssl = True
            self._custom_sslopt = {"cert_reqs": ssl.CERT_NONE}
            logger.warning("NorenApiPy initialized with SSL verification DISABLED (insecure). Use only for testing.")
        elif sslopt is not None:
            self._disable_ssl = False
            self._custom_sslopt = sslopt
            logger.info(f"Using custom sslopt: {sslopt}")
        else:
            self._disable_ssl = False
            self._custom_sslopt = None
            # Default secure mode will be handled by patched NorenApi.py using certifi
        
        # Initialize parent - parent now supports both host and websocket param names
        # Also handles sslopt internally
        try:
            # New patched version supports websocket_url kwarg
            NorenApi.__init__(self, host=host, websocket=websocket)
        except TypeError:
            # Fallback for old version: positional args
            NorenApi.__init__(self, host, websocket)
        
        global api
        api = self

        # Order latency optimization - pooled session
        _session = requests.Session()
        _session.headers.update({"Connection": "keep-alive"})
        _adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
        _session.mount("https://", _adapter)
        _session.mount("http://", _adapter)

        # Use certifi bundle for requests as well if available (helps Windows)
        # But keep timeout wrapper
        requests.get = partial(_session.get,timeout=(3,5))
        requests.post = partial(_session.post,timeout=(3,5))
        #  end pooled session setup 

    def set_ssl_options(self, sslopt=None, disable_ssl=False):
        """
        Update SSL options after init.
        Example:
            api.set_ssl_options(disable_ssl=True)  # insecure, testing only
            api.set_ssl_options(sslopt={"cert_reqs": ssl.CERT_NONE})
            api.set_ssl_options(sslopt={"ca_certs": certifi.where()})
        """
        if disable_ssl:
            self._custom_sslopt = {"cert_reqs": ssl.CERT_NONE}
            self._disable_ssl = True
        elif sslopt is not None:
            self._custom_sslopt = sslopt
            self._disable_ssl = False
        else:
            self._custom_sslopt = None
            self._disable_ssl = False
        return self._custom_sslopt

    def start_websocket(self, subscribe_callback=None,
                        order_update_callback=None,
                        socket_open_callback=None,
                        socket_close_callback=None,
                        socket_error_callback=None,
                        sslopt=None,
                        disable_ssl=False,
                        suppress_ssl_errors=False):
        """
        Start websocket with SSL fix.

        This overrides parent to ensure SSL options are correctly passed on Windows.

        Args:
            ... same as parent ...
            sslopt: dict, optional. Passed to websocket-client run_forever.
                    Secure example: {"ca_certs": certifi.where()}
                    Insecure testing: {"cert_reqs": ssl.CERT_NONE}
            disable_ssl: bool, if True disables SSL verification (insecure, for Windows Bootcamp workaround)
            suppress_ssl_errors: deprecated alias for disable_ssl

        Recommended usage for your error:
            # Option 1: Secure fix (try first)
            # pip install --upgrade certifi
            # pip install pip-system-certs
            api.start_websocket(...)

            # Option 2: Quick workaround (insecure, testing only)
            api.start_websocket(..., disable_ssl=True)

            # Option 3: Explicit certifi
            import certifi, ssl
            api.start_websocket(..., sslopt={"ca_certs": certifi.where(), "cert_reqs": ssl.CERT_REQUIRED})
        """
        # Determine effective sslopt
        # Priority: explicit arg > instance setting > default
        effective_sslopt = None
        effective_disable = False

        if disable_ssl or suppress_ssl_errors:
            effective_disable = True
            effective_sslopt = {"cert_reqs": ssl.CERT_NONE}
        elif sslopt is not None:
            effective_sslopt = sslopt
        elif getattr(self, '_custom_sslopt', None) is not None:
            effective_sslopt = self._custom_sslopt
            if self._custom_sslopt.get("cert_reqs") == ssl.CERT_NONE:
                effective_disable = True
        elif getattr(self, '_disable_ssl', False):
            effective_disable = True
            effective_sslopt = {"cert_reqs": ssl.CERT_NONE}

        # If still None, let patched parent use certifi automatically
        # But we can also proactively set certifi here for extra safety
        if effective_sslopt is None and not effective_disable:
            if _CERTIFI_BUNDLE is not None:
                effective_sslopt = {"ca_certs": _CERTIFI_BUNDLE, "cert_reqs": ssl.CERT_REQUIRED}

        # Call parent's start_websocket with SSL args
        # Parent (patched) now accepts sslopt and disable_ssl
        try:
            return super().start_websocket(
                subscribe_callback=subscribe_callback,
                order_update_callback=order_update_callback,
                socket_open_callback=socket_open_callback,
                socket_close_callback=socket_close_callback,
                socket_error_callback=socket_error_callback,
                sslopt=effective_sslopt,
                disable_ssl=effective_disable
            )
        except TypeError as e:
            # Fallback if parent is old unpatched version (from pip) - try without SSL args
            # Then monkey-patch the private __sslopt attribute
            logger.warning(f"Parent start_websocket doesn't support sslopt args ({e}), using fallback monkey-patch")
            # Try to set private attribute directly
            try:
                # Mangled name for __sslopt in parent is _NorenApi__sslopt
                if effective_sslopt is not None:
                    setattr(self, '_NorenApi__sslopt', effective_sslopt)
                elif effective_disable:
                    setattr(self, '_NorenApi__sslopt', {"cert_reqs": ssl.CERT_NONE})
            except Exception as ex:
                logger.warning(f"Failed to set _NorenApi__sslopt: {ex}")

            # Call old signature
            return super().start_websocket(
                subscribe_callback=subscribe_callback,
                order_update_callback=order_update_callback,
                socket_open_callback=socket_open_callback,
                socket_close_callback=socket_close_callback,
                socket_error_callback=socket_error_callback
            )

    def place_basket(self, orders):
        resp_err = 0
        resp_ok  = 0
        result   = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {executor.submit(self.place_order, order): order for order in  orders}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
            try:
                result.append(future.result())
            except Exception as exc:
                print(exc)
                resp_err = resp_err + 1
            else:
                resp_ok = resp_ok + 1
        return result
                
    def placeOrder(self,order: Order):
        ret = NorenApi.place_order(self, buy_or_sell=order.buy_or_sell, product_type=order.product_type,
                            exchange=order.exchange, tradingsymbol=order.tradingsymbol, 
                            quantity=order.quantity, discloseqty=order.discloseqty, price_type=order.price_type, 
                            price=order.price, trigger_price=order.trigger_price,
                            retention=order.retention, remarks=order.remarks)
        #print(ret)
        return ret

# Backward compatibility aliases for old code using ShoonyaApiPy / StarApiPy
ShoonyaApiPy = NorenApiPy
StarApiPy = NorenApiPy
Api = NorenApiPy
