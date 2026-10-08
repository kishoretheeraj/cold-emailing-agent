"""
Serves a local PostgREST under Supabase's /rest/v1 prefix, so the real supabase-py client (and so
the real db.py) can run against a disposable Postgres. Local stress tests only.

  python scripts/stress/rest_proxy.py <listen_port> <postgrest_port>
"""

import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}


def make_handler(upstream_port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _forward(self):
            path = self.path
            if path.startswith("/rest/v1"):
                path = path[len("/rest/v1"):] or "/"
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            conn = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=60)
            try:
                conn.request(self.command, path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            finally:
                conn.close()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_HEAD = _forward

        def log_message(self, *args):
            pass

    return Handler


if __name__ == "__main__":
    listen, upstream = int(sys.argv[1]), int(sys.argv[2])
    ThreadingHTTPServer(("127.0.0.1", listen), make_handler(upstream)).serve_forever()
