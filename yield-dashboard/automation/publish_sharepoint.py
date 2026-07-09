"""
publish_sharepoint.py
=====================
Upload yield-report HTML files to a SharePoint document library via
Microsoft Graph API + MSAL device-code authentication.

Requires:
    pip install msal requests

Proxy (Intel corporate network)
--------------------------------
Set the standard env vars before running, or add to your .env / shell profile:

    set HTTPS_PROXY=http://proxy-chain.intel.com:912
    set HTTP_PROXY=http://proxy-chain.intel.com:912
    set NO_PROXY=intel.com,localhost,127.0.0.1

Or hard-code them here by setting _PROXY below.

Usage (standalone):
    python publish_sharepoint.py path/to/Yield_Report_20250101_070000.html

Usage (from code):
    from publish_sharepoint import upload_report
    upload_report(Path("Yield_Report_20250101_070000.html"))
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

# ── SharePoint target ────────────────────────────────────────────────────────
_SHAREPOINT_HOST   = "intel.sharepoint.com"
_SITE_PATH         = "/sites/ftesdsexecution"
_DEST_FOLDER       = "General/NVL_CDIE/NVL-N2P_trackers/NVL816-BLLC"

# ── Azure AD / MSAL settings ─────────────────────────────────────────────────
_TENANT_ID         = "46c98d88-e344-4ed4-8496-4ed7712e255d"   # Intel tenant
_CLIENT_ID         = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"   # Azure CLI (public, works for delegated Graph API)
_SCOPES            = ["https://graph.microsoft.com/Files.ReadWrite",
                      "https://graph.microsoft.com/Sites.ReadWrite.All"]
_TOKEN_CACHE_FILE  = Path(__file__).parent / ".sp_token_cache.bin"

# ── Proxy settings ───────────────────────────────────────────────────────────
# Override here OR set HTTPS_PROXY / HTTP_PROXY env vars.
# Leave as empty string to use env vars (recommended).
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
# Intel corporate proxy (uncomment if env vars are not set):
# _PROXY = "http://proxy-chain.intel.com:912"

def _proxies() -> dict | None:
    """Return a requests-compatible proxies dict, or None if no proxy set."""
    p = _PROXY.strip()
    return {"http": p, "https": p} if p else None

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_msal_app():
    try:
        import msal
    except ImportError:
        raise RuntimeError(
            "The 'msal' package is required.\n"
            "Install it with:  pip install msal requests"
        )
    cache = msal.SerializableTokenCache()
    if _TOKEN_CACHE_FILE.exists():
        cache.deserialize(_TOKEN_CACHE_FILE.read_text(encoding="utf-8"))

    # Pass a requests Session with proxy to MSAL
    proxies = _proxies()
    http_client = None
    if proxies:
        import requests
        session = requests.Session()
        session.proxies = proxies
        http_client = session

    app = msal.PublicClientApplication(
        _CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
        token_cache=cache,
        http_client=http_client,
    )
    return app, cache


def _acquire_token(progress_cb=None) -> str:
    """Return a valid access token, using cache or device-code flow."""
    app, cache = _get_msal_app()

    # Try silent first (cached token / refresh token)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _persist_cache(cache)
            return result["access_token"]

    # Fall back to device-code flow
    flow = app.initiate_device_flow(scopes=_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow initiation failed: {flow}")

    msg = (
        f"\nTo authenticate with SharePoint, open a browser and go to:\n"
        f"  {flow['verification_uri']}\n"
        f"Enter code: {flow['user_code']}\n"
    )
    log.info(msg)
    if progress_cb:
        progress_cb(msg)

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result)}"
        )
    _persist_cache(cache)
    return result["access_token"]


def _persist_cache(cache) -> None:
    if cache.has_state_changed:
        _TOKEN_CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Graph API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _graph_put(token: str, url: str, data: bytes, content_type: str) -> dict:
    import requests
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
        data=data,
        timeout=60,
        proxies=_proxies(),
    )
    resp.raise_for_status()
    return resp.json()


def _upload_small(token: str, site_rel_path: str, filename: str,
                  content: bytes) -> dict:
    """Upload a file ≤4 MB via simple PUT."""
    import requests
    # Encode path components (spaces → %20, etc.) but keep slashes
    from urllib.parse import quote
    encoded_path = quote(f"{site_rel_path}/{filename}", safe="/")
    url = (
        f"https://graph.microsoft.com/v1.0"
        f"/sites/{_SHAREPOINT_HOST}:{_SITE_PATH}"
        f":/drive/root:/{encoded_path}:/content"
    )
    return _graph_put(token, url, content, "text/html; charset=utf-8")


def _upload_large(token: str, site_rel_path: str, filename: str,
                  content: bytes, progress_cb=None) -> dict:
    """Upload a file >4 MB via upload session."""
    import requests
    from urllib.parse import quote

    encoded_path = quote(f"{site_rel_path}/{filename}", safe="/")
    session_url = (
        f"https://graph.microsoft.com/v1.0"
        f"/sites/{_SHAREPOINT_HOST}:{_SITE_PATH}"
        f":/drive/root:/{encoded_path}:/createUploadSession"
    )
    sess_resp = requests.post(
        session_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        timeout=30,
        proxies=_proxies(),
    )
    sess_resp.raise_for_status()
    upload_url = sess_resp.json()["uploadUrl"]

    chunk_size = 5 * 1024 * 1024  # 5 MB chunks
    total = len(content)
    offset = 0
    result: dict = {}
    while offset < total:
        end = min(offset + chunk_size, total) - 1
        chunk = content[offset: end + 1]
        headers = {
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {offset}-{end}/{total}",
        }
        r = requests.put(upload_url, headers=headers, data=chunk, timeout=120,
                         proxies=_proxies())
        r.raise_for_status()
        if r.status_code in (200, 201):
            result = r.json()
        if progress_cb:
            progress_cb(f"Uploading… {min(end + 1, total) / total * 100:.0f}%")
        offset = end + 1
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def upload_report(
    file_path: Path,
    dest_folder: str = _DEST_FOLDER,
    progress_cb=None,
) -> str:
    """Upload *file_path* to SharePoint and return the web URL.

    Parameters
    ----------
    file_path   : local path to the HTML report file
    dest_folder : relative folder inside the SharePoint document library
    progress_cb : optional callable(message: str) for status updates
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Report file not found: {file_path}")

    token = _acquire_token(progress_cb)

    content = file_path.read_bytes()
    filename = file_path.name

    if progress_cb:
        progress_cb(f"Uploading {filename} ({len(content) / 1024:.0f} KB)…")

    if len(content) <= 4 * 1024 * 1024:
        result = _upload_small(token, dest_folder, filename, content)
    else:
        result = _upload_large(token, dest_folder, filename, content, progress_cb)

    web_url = result.get("webUrl", "")
    if progress_cb:
        progress_cb(f"Upload complete → {web_url}")
    return web_url


def list_reports(dest_folder: str = _DEST_FOLDER) -> list[dict]:
    """Return list of {name, webUrl, size, lastModified} dicts from SharePoint."""
    import requests
    from urllib.parse import quote
    token = _acquire_token()
    encoded_path = quote(dest_folder, safe="/")
    url = (
        f"https://graph.microsoft.com/v1.0"
        f"/sites/{_SHAREPOINT_HOST}:{_SITE_PATH}"
        f":/drive/root:/{encoded_path}:/children"
        f"?$select=name,webUrl,size,lastModifiedDateTime,file"
        f"&$orderby=lastModifiedDateTime desc"
        f"&$top=50"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        proxies=_proxies(),
    )
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return [
        {
            "name": i["name"],
            "webUrl": i.get("webUrl", ""),
            "size": i.get("size", 0),
            "lastModified": i.get("lastModifiedDateTime", ""),
        }
        for i in items
        if "file" in i
    ]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        print("Usage: python publish_sharepoint.py <report.html>")
        sys.exit(1)
    url = upload_report(Path(sys.argv[1]), progress_cb=print)
    print(f"\nSharePoint URL: {url}")
