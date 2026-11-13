"""The live stats watcher as a tiny Cloud Run service that Google's timer (Cloud Scheduler) pokes every 5 minutes.

Each POST runs one watcher stretch (scripts/live_watcher.py, MAX_MINUTES long, default 5.5) and then answers, so
stretches overlap by 30 s and nothing is missed while the next one starts. Cloud Run bills CPU only while a request is
running and needs no public IP of its own, which is what keeps this inside the free tier (a quarter CPU, one request
per instance). Off game windows the watcher sees nothing on and answers within seconds.

LANES (optional, e.g. "summary:10000,gamecast:20000") runs one watcher per ESPN feed side by side, for the speed
race in scoring/speed_test.py. GET answers 200 (health check). Prints counts only, like the watcher.
"""
from __future__ import annotations

import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import live_watcher  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _answer(self, code: int, text: str) -> None:
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._answer(200, "ok")

    def do_POST(self) -> None:  # noqa: N802
        try:
            lanes = [x.split(":") for x in os.getenv("LANES", "").split(",") if ":" in x]
            if lanes:  # race mode: one watcher process per feed, each saving under its own season offset
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_watcher.py")
                procs = [subprocess.Popen([sys.executable, script], env={**os.environ, "FEED": feed, "SEASON_OFFSET": offset})
                         for feed, offset in lanes]
                code = max(p.wait() for p in procs)
            else:
                code = live_watcher.main()
            self._answer(200, f"done {code}")
        except Exception as exc:
            print(f"WATCH_ERROR {type(exc).__name__}", flush=True)
            self._answer(500, "error")

    def log_message(self, *args) -> None:  # keep logs to the watcher's own count lines
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
