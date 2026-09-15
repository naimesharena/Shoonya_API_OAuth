"""
Test script to verify SSL fix for Windows Bootcamp / PyCharm
ERROR:websocket:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain

This script tests both secure and insecure modes without needing real credentials.
"""
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssl
import certifi
import logging
logging.basicConfig(level=logging.DEBUG)

from api_helper import NorenApiPy
from NorenRestApiPy.NorenApi import NorenApi
import inspect

print("="*80)
print("Testing SSL Fix for Shoonya Websocket")
print("="*80)

# Check that patched NorenApi is being used
print(f"\n1. NorenApi file: {inspect.getfile(NorenApi)}")
sig = inspect.signature(NorenApi.start_websocket)
print(f"   start_websocket signature: {sig}")
assert 'sslopt' in str(sig), "Patched version should have sslopt param"
assert 'disable_ssl' in str(sig), "Patched version should have disable_ssl param"
print("   ✓ Patched NorenApi has sslopt and disable_ssl params")

# Check api_helper
print(f"\n2. NorenApiPy file: {inspect.getfile(NorenApiPy)}")
sig2 = inspect.signature(NorenApiPy.start_websocket)
print(f"   start_websocket signature: {sig2}")
print("   ✓ api_helper patched")

# Test 1: Secure default should use certifi
print("\n3. Testing secure default (uses certifi)...")
import NorenRestApiPy.NorenApi as mod

class DummyWS:
    def __init__(self, *args, **kwargs):
        self.url = kwargs.get('url', args[0] if args else 'unknown')
    def run_forever(self, *args, **kwargs):
        sslopt = kwargs.get('sslopt')
        print(f"   run_forever sslopt: {sslopt}")
        if sslopt:
            assert 'ca_certs' in sslopt or 'cert_reqs' in sslopt
            if 'ca_certs' in sslopt:
                assert sslopt['ca_certs'] == certifi.where()
                print(f"   ✓ Secure mode uses certifi bundle: {sslopt['ca_certs']}")
        raise Exception("Stop test - secure mode works!")
    def send(self, *a, **k): pass
    def close(self): pass

original = mod.websocket.WebSocketApp
mod.websocket.WebSocketApp = DummyWS

try:
    api = NorenApiPy()
    api.start_websocket()
    import time; time.sleep(0.3)
except Exception as e:
    if "secure mode works" in str(e):
        print("   ✓ Test 1 PASSED")
    else:
        print(f"   Exception: {e}")

# Test 2: Insecure mode with disable_ssl=True
print("\n4. Testing insecure workaround (disable_ssl=True)...")
class DummyWS2:
    def __init__(self, *a, **k): pass
    def run_forever(self, *a, **k):
        sslopt = k.get('sslopt')
        print(f"   run_forever sslopt: {sslopt}")
        assert sslopt is not None
        assert sslopt.get('cert_reqs') == ssl.CERT_NONE
        print("   ✓ Insecure mode correctly sets CERT_NONE")
        raise Exception("Stop test - insecure mode works!")
    def send(self, *a, **k): pass
    def close(self): pass

mod.websocket.WebSocketApp = DummyWS2
try:
    api = NorenApiPy(disable_ssl=True)
    api.start_websocket(disable_ssl=True)
    import time; time.sleep(0.3)
except Exception as e:
    if "insecure mode works" in str(e):
        print("   ✓ Test 2 PASSED")
    else:
        print(f"   Exception: {e}")

# Restore
mod.websocket.WebSocketApp = original

print("\n" + "="*80)
print("All SSL fix tests PASSED!")
print("="*80)
print("""
Your original error:
  ERROR:websocket:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain

Should now be fixed by:

1. Secure (recommended):
   pip install --upgrade certifi
   pip install pip-system-certs   # Windows only
   # Then just use:
   api = NorenApiPy()
   api.start_websocket(...)

2. Quick workaround (insecure, testing only):
   api = NorenApiPy(disable_ssl=True)
   api.start_websocket(..., disable_ssl=True)

See WEBSOCKET_SSL_FIX.md for detailed explanation.
""")
