import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_helper import NorenApiPy
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


SYMBOLDICT = {}
def event_handler_quote_update(message):
    global SYMBOLDICT
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
    
    key = message['e'] + '|' + message['tk']

    if key in SYMBOLDICT:
        symbol_info =  SYMBOLDICT[key]
        symbol_info.update(message)
        SYMBOLDICT[key] = symbol_info
    else:
        SYMBOLDICT[key] = message

    print(SYMBOLDICT[key])

def open_callback():
    global socket_opened
    socket_opened = True
    print('app is connected')
    
    api.subscribe('NSE|11630', feed_type='d')
    #api.subscribe(['NSE|22', 'BSE|522032'])

#end of callbacks

def get_time(time_string):
    data = time.strptime(time_string,'%d-%m-%Y %H:%M:%S')

    return time.mktime(data)

#start of our program
# FIX FOR Windows Bootcamp / PyCharm SSL error:
# ERROR:websocket:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
# The patched api_helper now uses certifi by default. If you still get SSL error, try:
# 1) pip install --upgrade certifi
# 2) pip install pip-system-certs  OR  pip install python-certifi-win32
# 3) Disable SSL verification for testing only (insecure):
#    api = NorenApiPy(disable_ssl=True)
#    and pass disable_ssl=True to start_websocket
# 4) Check antivirus (Avast, Kaspersky, etc) - disable HTTPS scanning or add api.shoonya.com to exclusions

api = NorenApiPy()  # Default now uses certifi CA bundle automatically
# For Windows Bootcamp quick workaround (insecure, use only for testing):
# api = NorenApiPy(disable_ssl=True)

#yaml for parameters
with open('../cred.yml') as f:
    cred = yaml.load(f, Loader=yaml.FullLoader)
    print(cred)

#ret = api.login(userid = cred['user'], password = cred['pwd'], twoFA=cred['factor2'], vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])
ret = injected_headers = api.injectOAuthHeader(cred['Access_token'],cred['UID'],cred['Account_ID'])

if ret != None:
    # Set credentials safely
    api.set_credentials(
        cred['Access_token'],
        cred['UID'],
        cred['Account_ID']
    )
   
    # Secure (default) - patched library uses certifi automatically
    ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback)
    
    # If you still get CERTIFICATE_VERIFY_FAILED on Windows, uncomment one of these:
    # Option A: Quick insecure workaround for testing (not recommended for production)
    # ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback, disable_ssl=True)
    
    # Option B: Explicit certifi bundle (secure)
    # import ssl, certifi
    # ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback, sslopt={"ca_certs": certifi.where(), "cert_reqs": ssl.CERT_REQUIRED})
    
    while True:
        if socket_opened == True:
            print('q => quit')
            prompt1=input('what shall we do? ').lower()    

            print('Fin') #an answer that wouldn't be yes or no
            break   

        else:
            continue

    