"""Healthcheck Docker : interroge /health en HTTP ou HTTPS selon SENTINEL_TLS."""

import os
import ssl
import sys
import urllib.request

scheme = "https" if os.environ.get("SENTINEL_TLS", "on") == "on" else "http"
url = f"{scheme}://127.0.0.1:8443/health"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE  # certificat auto-signé local

try:
    with urllib.request.urlopen(url, context=ctx, timeout=5) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
