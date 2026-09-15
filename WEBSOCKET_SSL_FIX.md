# WebSocket SSL Fix for Windows / MacBook Bootcamp / PyCharm

## Error you are seeing

```
ERROR:websocket:[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1077) - goodbye
DEBUG:NorenRestApiPy.NorenApi:None
DEBUG:NorenRestApiPy.NorenApi:<websocket._app.WebSocketApp object at ...>
(repeats infinitely)
```

This happens when running `test_websocket_feed.py` (or any websocket test) on **Windows**, especially:
- MacBook with Windows Bootcamp
- PyCharm on Windows
- Corporate laptop with Zscaler / proxy / antivirus SSL inspection

---

## Why it happens

1. **Python on Windows** doesn't always have a complete CA bundle. `websocket-client` tries to verify `wss://api.shoonya.com` but fails.
2. **Antivirus** (Avast, Kaspersky, Bitdefender, Norton) intercepts HTTPS/WSS and injects its own self-signed certificate.
3. **Corporate proxy / Zscaler / VPN** does SSL inspection with a custom root CA that Python doesn't trust.
4. **Bootcamp drivers / outdated certifi**: The Windows cert store and Python's certifi are out of sync.

---

## Fix (3 levels)

### Level 1: Secure fix (Recommended - keep SSL verification)

Update your CA bundle and let Python use Windows cert store:

```bash
# In PyCharm terminal or CMD (as admin if needed)
pip install --upgrade certifi
pip install --upgrade websocket-client

# Windows only: make Python use Windows certificate store
pip install pip-system-certs
# OR alternative:
pip install python-certifi-win32

# Then restart PyCharm:
# File -> Invalidate Caches / Restart
```

The **patched library in this repo** now automatically uses `certifi.where()` for websocket connections, so after installing certifi it should work without code changes:

```python
from api_helper import NorenApiPy
api = NorenApiPy()  # now uses certifi automatically
api.start_websocket(...)
```

### Level 2: Quick workaround (Insecure, for testing only)

If you are blocked and need to test immediately, disable SSL verification. **Do not use in production** - it makes you vulnerable to MITM attacks.

```python
from api_helper import NorenApiPy
import ssl

# Option A: via constructor
api = NorenApiPy(disable_ssl=True)
api.start_websocket(..., disable_ssl=True)

# Option B: explicit sslopt
api.start_websocket(..., sslopt={"cert_reqs": ssl.CERT_NONE})

# Option C: secure with explicit certifi (better than disabling)
import certifi
api.start_websocket(..., sslopt={"ca_certs": certifi.where(), "cert_reqs": ssl.CERT_REQUIRED})
```

The patched `NorenRestApiPy/NorenApi.py` in this repo now supports:
```python
api.start_websocket(..., sslopt=..., disable_ssl=False)
```

### Level 3: System-level fix (Antivirus / Proxy)

1. **Antivirus**:
   - Avast: Settings -> Protection -> Core Shields -> Disable "Enable HTTPS scanning"
   - Or add exclusion: `api.shoonya.com` and `*.shoonya.com`
   - Kaspersky: Settings -> Network -> Uncheck "Scan encrypted connections"

2. **Corporate Zscaler / Proxy**:
   - Ask IT for the Zscaler root CA certificate
   - Install it to Windows cert store, then `pip install pip-system-certs` will make Python trust it
   - Or set environment variable:
     ```
     setx SSL_CERT_FILE "C:\path\to\zscaler-root-ca.pem"
     setx REQUESTS_CA_BUNDLE "C:\path\to\zscaler-root-ca.pem"
     ```

3. **PyCharm specific**:
   - File -> Settings -> Tools -> Server Certificates -> Check "Accept non-trusted certificates automatically" (temporary)
   - Use same interpreter as system terminal: File -> Settings -> Project -> Python Interpreter -> ensure it points to the env where you installed certifi

---

## What was fixed in this repo

### 1. `NorenRestApiPy/NorenApi.py` (local patched copy)

- Added `ssl` and `certifi` imports
- Added `self.__sslopt` storage
- Modified `__ws_run_forever()` to pass `sslopt` to `run_forever()`:
  ```python
  self.__websocket.run_forever(..., sslopt=self.__sslopt)
  ```
  Previously it called `run_forever()` without `sslopt`, so Python used default verification that fails on Windows.

- Modified `start_websocket()` to accept:
  ```python
  def start_websocket(..., sslopt=None, disable_ssl=False, suppress_ssl_errors=False)
  ```
  - If `disable_ssl=True`: sets `sslopt={"cert_reqs": ssl.CERT_NONE}`
  - If `sslopt` provided: uses it directly
  - Otherwise: uses `{"ca_certs": certifi.where()}` automatically (secure default)

- Added better error logging that suggests fixes when `CERTIFICATE_VERIFY_FAILED` occurs.

This local `NorenRestApiPy/` folder takes precedence over the pip-installed `NorenRestApiOAuth` package because `Tests/` adds repo root to `sys.path`.

### 2. `api_helper.py`

- Now accepts `disable_ssl`, `ssl_verify`, `sslopt` in `__init__`
- Overrides `start_websocket()` to handle SSL options and fallback to monkey-patching if parent is old unpatched version
- Uses `certifi` bundle for both websocket and requests sessions
- Provides `set_ssl_options()` helper

### 3. `requirements.txt`

- Added `certifi`, `pip-system-certs` (Windows only), `websocket-client`

### 4. Test files updated with comments showing how to use the fix

---

## How to test the fix

```bash
cd Shoonya_API_OAuth
pip install -r requirements.txt

# Test secure (should work after pip-system-certs)
python Tests/test_websocket_feed.py

# If still fails, test insecure workaround:
# Edit Tests/test_websocket_feed.py and uncomment disable_ssl=True line
```

You should see:
```
app is connected
quote event: ...
```
Instead of infinite `CERTIFICATE_VERIFY_FAILED`.

---

## Still failing?

1. Run this diagnostic in PyCharm Python Console:
```python
import ssl, certifi, websocket
print("certifi:", certifi.where())
print("ssl default:", ssl.get_default_verify_paths())
print("websocket-client:", websocket.__version__)

# Test raw connection
import websocket
ws = websocket.WebSocket()
ws.connect("wss://api.shoonya.com/NorenWSAPI/", sslopt={"ca_certs": certifi.where()})
print("certifi bundle works!")

# Test insecure (should always work)
ws2 = websocket.WebSocket()
ws2.connect("wss://api.shoonya.com/NorenWSAPI/", sslopt={"cert_reqs": ssl.CERT_NONE})
print("insecure works, so it's definitely a cert issue")
```

2. Check PyCharm is using same env:
   - PyCharm bottom right -> Interpreter -> should match `pip list` env

3. Open issue with output of diagnostic.

---

## Security note

`disable_ssl=True` or `sslopt={"cert_reqs": ssl.CERT_NONE}` disables **all** SSL verification. Use only for local testing behind trusted network. For production, always use certifi or Windows cert store integration.
