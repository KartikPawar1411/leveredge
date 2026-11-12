"""Capture README screenshots of the running app with headless Edge/Chrome (DevTools protocol).

    streamlit run app/main.py --server.port 8765      (in another terminal)
    python scripts/capture_screenshots.py [http://localhost:8765]
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
SHOTS = {  # name: (query string, viewport height)
    "overview": ("", 1380), "sources_uses": ("?tab=entry", 1120), "debt": ("?tab=debt", 1500),
    "returns": ("?tab=exit", 1400), "sensitivity": ("?tab=sensitivity", 1250), "scenarios": ("?tab=scenarios", 1700),
}
PORT = 9333


async def capture(base: str) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
        ws_url = next(t for t in json.load(r) if t["type"] == "page")["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None) as ws:
        n = 0

        async def call(method: str, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    return msg.get("result", {})

        for name, (query, height) in SHOTS.items():
            await call("Emulation.setDeviceMetricsOverride", width=1600, height=height, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=f"{base}/{query}")
            await asyncio.sleep(9)   # Streamlit renders over a websocket; wait for charts
            # hide the Streamlit toolbar so the picture shows only the product
            await call("Runtime.evaluate", expression="document.querySelector('[data-testid=stToolbar]')?.remove()")
            shot = await call("Page.captureScreenshot", format="png")
            (OUT / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
            print("saved", name)


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765").rstrip("/")
    exe = next((b for b in BROWSERS if Path(b).exists()), None)
    if exe is None:
        sys.exit("No Edge or Chrome installation found.")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as prof:
        proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--remote-debugging-port={PORT}",
                                 f"--user-data-dir={prof}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(3)
            asyncio.run(capture(base))
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
