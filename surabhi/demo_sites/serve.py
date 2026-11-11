"""Serve the demo sites for a FORGE run: the official store and each case on its OWN port.

    python surabhi/demo_sites/serve.py            # ports 8000 (official), 8001-8004 (case1-case4)
    python surabhi/demo_sites/serve.py --port 9000

Why separate ports: FORGE treats the brand's own origin (host:port) as allow-listed, so cases served from the
same port as the official store would all be scored 0 as "the brand's own store". Each server serves the whole
demo_sites folder, so the pages' ../assets/ images are the very same files everywhere (same SHA-256).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITES = ("official", "case1", "case2", "case3", "case4")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start(base_port: int = 8000, host: str = "127.0.0.1") -> tuple[dict, list]:
    """Start one server per site. Returns ({site: url}, [servers]); call .shutdown() on each to stop."""
    urls, servers = {}, []
    handler = functools.partial(_Quiet, directory=str(HERE))
    for i, site in enumerate(SITES):
        srv = http.server.ThreadingHTTPServer((host, base_port + i if base_port else 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        urls[site] = f"http://localhost:{srv.server_address[1]}/{site}/"
    return urls, servers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8000, help="port of the official store; cases use the next ones")
    a = ap.parse_args()
    urls, servers = start(a.port)
    for site, url in urls.items():
        print(f"{site:9} {url}")
    cases = " ".join(f"--suspect {urls[s]}" for s in SITES[1:])
    print(f"\nRun FORGE from the repository root:\n  python main.py --store {urls['official']} {cases} --fresh\n")
    print("Serving. Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for srv in servers:
            srv.shutdown()


if __name__ == "__main__":
    main()
