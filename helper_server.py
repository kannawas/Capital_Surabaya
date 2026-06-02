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


def _validate_tickers(candidates: list[str]) -> tuple[list[str], list[str]]:
    """Use yfinance as ground truth: a ticker is valid if it returns price bars.
    Returns (valid, invalid)."""
    import yfinance as yf
    valid, invalid = [], []
    for t in candidates:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if not hist.empty and len(hist) > 0:
                valid.append(t)
            else:
                invalid.append(t)
        except Exception:
            invalid.append(t)
    return valid, invalid


def do_add_tickers(tickers: list[str]) -> dict:
    """Validate tickers via yfinance, add valid ones to watchlist.json, push to repo."""
    wl_path = REPO / "watchlist.json"
    try:
        wl = json.loads(wl_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"cannot read watchlist.json: {e}"}

    existing = {t.upper() for t in wl.get("tickers", [])}
    clean = [t.strip().upper() for t in tickers if t.strip()]
    candidates = [t for t in clean if t not in existing]

    if not candidates:
        return {"ok": True, "added": [], "rejected": [], "msg": "All tickers already in watchlist."}

    # Validate against yfinance (ground truth for tradable US-listed tickers)
    print(f"[helper] validating {len(candidates)} ticker(s) against yfinance...")
    added, rejected = _validate_tickers(candidates)
    print(f"[helper] valid: {len(added)} | rejected: {len(rejected)} {rejected[:20]}")

    if not added:
        return {"ok": True, "added": [], "rejected": rejected,
                "msg": f"No valid US-listed tickers found. Rejected: {', '.join(rejected)}"}

    wl["tickers"] = wl.get("tickers", []) + added
    groups = wl.setdefault("groups", {})
    for t in added:
        groups.setdefault(t, "uncategorized")
    wl_path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")

    # Fetch prices for the full list, regenerate dashboard, then push
    rc_f, out_f = _run([sys.executable, "data/prices_local.py"], timeout=300)
    rc_d, out_d = _run([sys.executable, "routines/export_dashboard.py"], timeout=60)

    _run(["git", "add", "watchlist.json", "prices.json", "dashboard.json"])
    _run(["git", "commit", "-m", f"Add {len(added)} tickers (validated)"])
    _run(["git", "pull", "--rebase", "--autostash", "origin", "main"], timeout=60)
    rc_p, out_p = _run(["git", "push", "origin", "HEAD:main"], timeout=60)

    return {
        "ok": rc_p == 0,
        "added": added,
        "rejected": rejected,
        "total": len(wl["tickers"]),
        "push_ok": rc_p == 0,
        "out": (out_f[-120:] + "\n" + out_p[-160:]).strip(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Allow the GitHub Pages origin to call us
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_POST(self):
        if self.path.startswith("/add-tickers"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                tickers = payload.get("tickers", [])
            except Exception as e:
                self._send(400, {"ok": False, "error": f"bad request: {e}"})
                return
            print(f"[helper] /add-tickers requested: {tickers}")
            result = do_add_tickers(tickers)
            print(f"[helper] add-tickers done: ok={result.get('ok')} added={result.get('added')}")
            self._send(200 if result.get("ok") else 500, result)
        else:
            self._send(404, {"ok": False, "error": "unknown endpoint"})

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
