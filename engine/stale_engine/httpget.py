"""JSON fetch with retries, for feeds and runtime reports. Accepts http(s) URLs and local paths."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


def get_json(url: str, *, timeout: float = 10.0, attempts: int = 3, client: httpx.Client | None = None) -> Any:
    if url.startswith("file://"):
        return json.loads(Path(url[7:]).read_text())
    if not url.startswith(("http://", "https://")):
        return json.loads(Path(url).read_text())
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        last: Exception | None = None
        for i in range(attempts):
            try:
                r = client.get(url, headers={"Cache-Control": "no-cache"})
                if r.status_code in RETRY_STATUS:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in RETRY_STATUS:
                    break
                if i + 1 < attempts:
                    time.sleep(0.5 * 2**i)
        raise RuntimeError(f"GET {url} failed: {last}") from last
    finally:
        if own:
            client.close()
