"""_pipeline_server.py - HTTP opener-server mixin for PipelineFrame."""
import http.server
from _pipeline_constants import _SRC_DIR, _ROOT_DIR, _FROZEN, _LOADER, SICC_UPM_SCRIPT, SICC_CDYN_UPM_SCRIPT
from _pipeline_constants import _SRC_DIR, _ROOT_DIR, _FROZEN, _LOADER, SICC_UPM_SCRIPT, SICC_CDYN_UPM_SCRIPT
import os
import queue
import socketserver
import subprocess
import threading
import urllib.parse


class OpenerServerMixin:
    def _poll_open_queue(self):
        """Called on the Tkinter main thread every 200 ms.  Opens any files
        queued by the HTTP opener server.  Running on the foreground thread
        lets AllowSetForegroundWindow actually work."""
        try:
            while True:
                path = self._open_queue.get_nowait()
                try:
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
                except Exception:
                    pass
                try:
                    os.startfile(path)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(200, self._poll_open_queue)

    def _start_opener_server(self):
        """Start a persistent local HTTP server on a fixed port (56947) that
        opens local files/folders via the OS shell.  Returns the port."""
        FIXED_PORT = 56947
        _open_q = queue.Queue()
        self._open_queue = _open_q

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                path   = params.get('path', [''])[0]
                if not path:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b'no path'); return
                try:
                    if parsed.path == '/folder':
                        folder = path if os.path.isdir(path) else os.path.dirname(path)
                        subprocess.Popen(['explorer', folder])
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/plain')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(b'OK')
                    else:
                        # Open the file via the OS shell directly from the HTTP
                        # handler thread.  Because the browser navigated here
                        # (target=_blank), Windows treats this as user-initiated
                        # and grants Excel foreground focus immediately.
                        os.startfile(path)
                        # Respond with a self-closing page so the new tab disappears
                        body = b'<html><body><script>window.close();</script></body></html>'
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html')
                        self.send_header('Content-Length', str(len(body)))
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(body)
                except Exception as exc:
                    self.send_response(500)
                    self.send_header('Content-Type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(str(exc).encode())
                except Exception as exc:
                    self.wfile.write(str(exc).encode())
            def log_message(self, *args): pass
        try:
            server = socketserver.TCPServer(('127.0.0.1', FIXED_PORT), _Handler)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return port
        except OSError:
            # Port already in use (another GUI instance running) – reuse it
            return FIXED_PORT
        except Exception:
            return None

