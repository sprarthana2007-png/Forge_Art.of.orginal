"""Local test server for the cloaking check (fictional data).

  python tests/fixtures/cloaking_server.py --port 8703 --mode cloak   # bots see "under maintenance",
                                                                       # Instagram-ad shoppers see the fake sale
  python tests/fixtures/cloaking_server.py --port 8704 --mode same    # everyone sees the same page

"Instagram-ad shopper" = User-Agent contains "Instagram" OR the URL has an fbclid parameter.
Images and other files are served from tests/fixtures/clone/.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CLONE = Path(__file__).resolve().parent / "clone"
MAINTENANCE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Site under maintenance</title>
<style>body{font-family:sans-serif;background:#f4f4f4;color:#333;display:flex;align-items:center;
justify-content:center;height:90vh;margin:0}div{text-align:center}h1{font-weight:300}</style></head>
<body><div><h1>We'll be back soon</h1><p>Our website is under scheduled maintenance.</p>
<p>Thank you for your patience.</p></div></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    mode = "cloak"

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            shopper = "Instagram" in self.headers.get("User-Agent", "") or "fbclid" in parse_qs(u.query)
            body = (CLONE / "index.html").read_bytes() if (self.mode == "same" or shopper) else MAINTENANCE
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mode", choices=["cloak", "same"], default="cloak")
    a = ap.parse_args()
    Handler.mode = a.mode
    ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(CLONE))).serve_forever()


if __name__ == "__main__":
    main()
