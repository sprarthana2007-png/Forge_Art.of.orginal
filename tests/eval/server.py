"""Serves the evaluation sites: http://<site>.localhost:<port>/ -> tests/eval/sites/<site>/index.html,
/shared/<file> -> tests/eval/shared/<file>. Sites marked "cloaking" in labels.json show a maintenance page unless
the visitor looks like an Instagram-ad shopper (User-Agent contains "Instagram" or the URL has fbclid).

    python tests/eval/server.py --port 8800
"""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CLOAKED = {p["site"] for p in json.loads((HERE / "labels.json").read_text())["pages"] if p.get("cloaking")}
MAINTENANCE = (b"<!doctype html><html><head><meta charset='utf-8'><title>Under maintenance</title></head><body>"
               b"<h1>We'll be back soon</h1><p>Our website is under scheduled maintenance.</p></body></html>")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        site = (self.headers.get("Host") or "").split(":")[0].removesuffix(".localhost")
        u = urlparse(self.path)
        if u.path.startswith("/shared/"):
            f = (HERE / "shared" / Path(u.path).name)
            if f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            return self._send(404, b"not found")
        page = HERE / "sites" / site / "index.html"
        if u.path not in ("/", "/index.html") or not page.is_file():
            return self._send(404, b"not found")
        shopper = "Instagram" in self.headers.get("User-Agent", "") or "fbclid" in parse_qs(u.query)
        if site in CLOAKED and not shopper:
            return self._send(200, MAINTENANCE)
        return self._send(200, page.read_bytes())

    def log_message(self, *a):
        pass


def serve(port: int):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    serve(ap.parse_args().port).serve_forever()
