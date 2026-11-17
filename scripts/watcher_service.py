"""The live stats watcher as a tiny Cloud Run service that Google's timer (Cloud Scheduler) pokes every 5 minutes.

Each POST runs one watcher stretch (scripts/live_watcher.py, MAX_MINUTES long, default 5.5) and then answers, so
stretches overlap by 30 s and nothing is missed while the next one starts. Cloud Run bills CPU only while a request is
running and needs no public IP of its own, which is what keeps this inside the free tier (a quarter CPU, one request
per instance). Off game windows the watcher sees nothing on and answers within seconds.

LANES (optional, e.g. "summary:10000,gamecast:20000") runs one watcher per ESPN feed side by side, for the speed
race in scoring/speed_test.py. GET answers 200 (health check). Prints counts only, like the watcher.

ALERTS=1 (the live setup, replacing the e2-micro): each POST also runs one pass of the owners' push alerts
(scripts/push_alerts.py, the e2-micro ran it every 5 minutes) and, alongside the stretch, the lock screen Live Activity
loop (scripts/live_activity_watcher.py). The Live Activity loop stops as soon as no activity is running, so a quiet
POST still answers within seconds and bills next to nothing. On calls at :00, :15, :30 and :45 it also runs the
15-minute Sleeper sync (scripts/run_sleeper_sync.py then scripts/notify_trades.py, both safe to repeat), which the
e2-micro used to run; GitHub's sleeper-sync.yml stays as a 4-hourly backup.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import live_watcher  # noqa: E402


def _alerts() -> None:
    """One pass of the owners' push alerts."""
    try:
        from scripts import push_alerts
        push_alerts.main()
    except Exception as exc:
        print(f"ALERTS_ERROR {type(exc).__name__}", flush=True)


def _sleeper_sync() -> None:
    """The 15-minute Sleeper sync, each script in its own process (they are written as standalone scripts)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("run_sleeper_sync.py", "notify_trades.py"):
        try:
            code = subprocess.run([sys.executable, os.path.join(here, name)], timeout=240).returncode
            if code:
                print(f"SYNC_ERROR {name} exit {code}", flush=True)
                return
        except Exception as exc:
            print(f"SYNC_ERROR {name} {type(exc).__name__}", flush=True)
            return


def _live_activities(stop: float) -> None:
    """Keep running lock screen Live Activities current until the stretch ends or none is left running."""
    try:
        from supabase import create_client

        from api import apns
        from scripts import live_activity_watcher as law
        if not apns.configured():
            return
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        push = apns.Client.from_env()
        watcher = law.Watcher(client, push)
        interval = float(os.getenv("LA_INTERVAL", "15"))
        passes = 0
        try:
            while time.time() < stop:
                started = time.time()
                try:
                    running = watcher.tick()
                except Exception as exc:
                    watcher.counts["errors"] += 1
                    print(f"WATCH_TICK_ERROR {type(exc).__name__}", flush=True)
                    running = 1  # keep trying this stretch; a quiet stretch still ends on time
                passes += 1
                if not running:
                    break
                wait = interval - (time.time() - started)
                if wait > 0:
                    time.sleep(min(wait, max(0.0, stop - time.time())))
        finally:
            push.close()
        if passes > 1 or any(watcher.counts.values()):
            print(f"live activities: {passes} passes, {watcher.counts}", flush=True)
    except Exception as exc:
        print(f"LA_ERROR {type(exc).__name__}", flush=True)


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
                extras = []
                if os.getenv("ALERTS") == "1":
                    stop = time.time() + float(os.getenv("MAX_MINUTES", "5.5")) * 60
                    extras = [threading.Thread(target=_alerts), threading.Thread(target=_live_activities, args=(stop,))]
                    if time.gmtime().tm_min % 15 < 5:  # the :00/:15/:30/:45 calls (timers fire every 5 minutes)
                        extras.append(threading.Thread(target=_sleeper_sync))
                    for t in extras:
                        t.start()
                code = live_watcher.main()
                for t in extras:
                    t.join()
            self._answer(200, f"done {code}")
        except Exception as exc:
            print(f"WATCH_ERROR {type(exc).__name__}", flush=True)
            self._answer(500, "error")

    def log_message(self, *args) -> None:  # keep logs to the watcher's own count lines
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
