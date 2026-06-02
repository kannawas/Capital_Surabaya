"""
Local helper server for the Capital Surabaya UI.

The web UI (GitHub Pages) cannot fetch yfinance (CORS) or push to git (no creds).
This tiny local server bridges that gap: the "Update Prices" button on the
Watchlist page calls http://localhost:8770/update, which fetches fresh OHLC via
yfinance and git-pushes prices.json to the repo. CCR then uses it next run.

Start it by double-clicking start_helper.bat (or: python helper_server.py).
Leave it running while you use the dashboard; close the window to stop.

Endpoints:
    GET /update   -> fetch prices + git push, returns JSON status
    GET /status   -> health check
"""

from __future__ import annotations
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).parent
PORT = 8770


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def do_update() -> dict:
    steps = {}

    # 1. Fetch prices -> prices.json
    rc, out = _run([sys.executable, "data/prices_local.py"], timeout=180)
    steps["fetch"] = {"ok": rc == 0, "out": out.strip()[-300:]}
    if rc != 0:
        return {"ok": False, "step": "fetch", "detail": steps}

    # 2. git add + commit
    _run(["git", "add", "prices.json"])
    rc_c, out_c = _run(["git", "commit", "-m", "Update prices.json (manual)"])
    # commit returns nonzero if nothing changed — that's fine

    # 3. pull --rebase (CCR may have pushed since our last sync), then push
    rc_pull, out_pull = _run(["git", "pull", "--rebase", "--autostash", "origin", "main"], timeout=60)
    rc_p, out_p = _run(["git", "push", "origin", "HEAD:main"], timeout=60)
    steps["push"] = {"ok": rc_p == 0, "out": (out_c + "\n" + out_pull + "\n" + out_p).strip()[-400:]}

    # 3. read back summary
    try:
        pj = json.loads((REPO / "prices.json").read_text(encoding="utf-8"))
        ok = sum(1 for v in pj["prices"].values() if "error" not in v)
        summary = {
            "fetched_at": pj.get("fetched_at"),
            "ok_tickers": ok,
            "total": len(pj.get("tickers", [])),
            "sample": {t: pj["prices"][t].get("last_close")
                       for t in list(pj["prices"])[:3]},
        }
    except Exception as e:
        summary = {"read_error": str(e)}

    return {"ok": rc_p == 0, "summary": summary, "steps": steps}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Allow the GitHub Pages origin to call us
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path.startswith("/update"):
            print("[helper] /update requested — fetching prices...")
            result = do_update()
            print(f"[helper] update done: ok={result.get('ok')}")
            self._send(200 if result.get("ok") else 500, result)
        elif self.path.startswith("/status"):
            self._send(200, {"ok": True, "service": "capital-surabaya-helper", "port": PORT})
        else:
            self._send(404, {"ok": False, "error": "unknown endpoint"})

    def log_message(self, *args):
        pass  # quiet default logging


def main():
    print(f"Capital Surabaya helper running on http://localhost:{PORT}")
    print("Leave this window open. Press Ctrl+C (or close window) to stop.")
    print("The 'Update Prices' button on the dashboard calls /update here.\n")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHelper stopped.")


if __name__ == "__main__":
    main()
