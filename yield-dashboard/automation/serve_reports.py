"""
serve_reports.py
================
Lightweight HTTP server that exposes the reports/ folder so that the
SharePoint index.html can fetch the report list and files dynamically.

Endpoints
---------
GET /api/reports          → JSON list of all Yield_Report_*.html files
GET /reports/<filename>   → serve the HTML file directly
GET /                     → serve reports/index.html

All responses include CORS headers so browsers on SharePoint can fetch them.

Usage
-----
    python serve_reports.py                          # default port 8765
    python serve_reports.py --port 9000
    python serve_reports.py --base-dir "\\\\server\\share\\auto\\yield"

Add to Windows Task Scheduler to auto-start, or run from manage_automation.py
Schedule tab.
"""
from __future__ import annotations

import argparse
import datetime
import http.server
import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_HERE      = Path(__file__).resolve().parent.parent   # yield-dashboard/
_BASE_DIR  = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield")
_DEFAULT_PORT = 8765


def _fmt_size(n: int) -> str:
    if n < 1024:       return f"{n} B"
    if n < 1024 ** 2:  return f"{n / 1024:.0f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


class ReportHandler(http.server.BaseHTTPRequestHandler):
    base_dir: Path = _BASE_DIR   # set by factory

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:   # preflight
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")

        if path == "/api/reports":
            self._serve_manifest()
        elif path.startswith("/reports/"):
            fname = Path(path[len("/reports/"):]).name
            self._serve_file(fname)
        elif path in ("", "/"):
            self._serve_file("index.html")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_manifest(self) -> None:
        reports_dir = self.base_dir / "reports"
        files = sorted(
            [f for f in reports_dir.glob("Yield_Report_*.html")
             if f.name != "index.html"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        data = [
            {
                "name":     f.name,
                "size":     _fmt_size(f.stat().st_size),
                "mtime":    datetime.datetime.fromtimestamp(
                                f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "url":      f"/reports/{f.name}",
            }
            for f in files
        ]
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, fname: str) -> None:
        # Safety: strip any path traversal
        fname = Path(fname).name
        fpath = self.base_dir / "reports" / fname
        if not fpath.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = fpath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # suppress default stdout noise
        log.debug(fmt % args)


def _ensure_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Return (certfile, keyfile), generating a self-signed cert if needed.
    Uses the built-in `ssl` + `cryptography` package if available,
    falls back to `openssl` CLI, or skips HTTPS if neither is available.
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_file = cert_dir / "server.crt"
    key_file  = cert_dir / "server.key"
    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    # Try cryptography package first (pure Python, no external tools)
    try:
        import datetime as _dt
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        hostname = __import__("socket").gethostname()
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.utcnow())
            .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName(hostname),
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256())
        )
        key_file.write_bytes(
            key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        log.info(f"Self-signed cert generated → {cert_file}")
        return cert_file, key_file
    except ImportError:
        pass

    # Fallback: openssl CLI
    import subprocess as _sp
    hostname = __import__("socket").gethostname()
    r = _sp.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_file), "-out", str(cert_file),
        "-days", "3650", "-nodes",
        "-subj", f"/CN={hostname}",
    ], capture_output=True)
    if r.returncode == 0:
        log.info(f"Self-signed cert generated via openssl → {cert_file}")
        return cert_file, key_file

    raise RuntimeError(
        "Cannot generate TLS cert. Install 'cryptography':\n"
        "  pip install cryptography"
    )


def make_server(base_dir: Path, port: int,
                https: bool = True) -> http.server.HTTPServer:
    handler = type("Handler", (ReportHandler,), {"base_dir": base_dir})
    server  = http.server.HTTPServer(("", port), handler)
    if https:
        try:
            import ssl
            cert_dir  = Path(__file__).parent / ".tls"
            cert_file, key_file = _ensure_cert(cert_dir)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_file), str(key_file))
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            server._is_https = True
        except Exception as e:
            log.warning(f"HTTPS setup failed ({e}), falling back to HTTP.")
            server._is_https = False
    else:
        server._is_https = False
    return server


def run(base_dir: Path = _BASE_DIR, port: int = _DEFAULT_PORT,
        daemon: bool = False) -> http.server.HTTPServer:
    """Start the server (optionally in a daemon thread) and return it."""
    server = make_server(base_dir, port)
    proto  = "https" if getattr(server, "_is_https", False) else "http"
    t = threading.Thread(target=server.serve_forever, daemon=daemon)
    t.start()
    log.info(f"Reports server listening on {proto}://localhost:{port}")
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port",       type=int, default=_DEFAULT_PORT)
    ap.add_argument("--base-dir",   default=None)
    ap.add_argument("--no-https",   action="store_true",
                    help="Disable HTTPS (use plain HTTP)")
    args = ap.parse_args()

    base  = Path(args.base_dir) if args.base_dir else _BASE_DIR
    https = not args.no_https
    srv   = make_server(base, args.port, https=https)
    proto = "https" if getattr(srv, "_is_https", False) else "http"
    print(f"Starting reports server on {proto}://0.0.0.0:{args.port}")
    print(f"  Reports dir : {base / 'reports'}")
    print(f"  API         : {proto}://localhost:{args.port}/api/reports")
    if getattr(srv, "_is_https", False):
        print("  NOTE: self-signed cert — browser will show a security warning.")
        print("  Open the URL once and click 'Advanced -> Proceed' to trust it.")
    print("Press Ctrl+C to stop.")
    srv.serve_forever()
