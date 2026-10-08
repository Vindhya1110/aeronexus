"""Screenshot the running dashboard with headless Edge/Chrome driven over the DevTools protocol (no extra installs).

    python tools/capture_screens.py                                   # all key screens -> data/demo/evidence/<timestamp>/
    python tools/capture_screens.py --shots "Events,Source insight" --zone HITEC --wait 12

Why not `msedge --headless --screenshot`? That flag shoots on a *virtual* clock, so Streamlit (which renders over a
websocket) is captured still on its loading skeleton. Here we wait real seconds, then Page.captureScreenshot.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
DEBUG_PORT = 9333


class Cdp:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})


async def sign_in(cdp: Cdp, port: int, username: str, password: str) -> None:
    """Sign in through the website's login modal (the same way a visitor does). The login cookie then covers every dashboard shot."""
    await cdp.call("Page.navigate", url=f"http://localhost:{port}/?login=1")
    async def focus(sel: str) -> bool:
        r = await cdp.call("Runtime.evaluate", returnByValue=True, expression=(
            f"(()=>{{const e=document.querySelector('{sel}');if(!e||e.offsetParent===null)return false;e.focus();e.select();e.value='';return true}})()"))
        return bool(r["result"].get("value"))
    for _ in range(40):                                  # wait for the modal to open
        if await focus("#inUser"):
            break
        await asyncio.sleep(0.5)
    else:
        raise RuntimeError("login modal did not appear")
    await cdp.call("Input.insertText", text=username)
    await focus("#inPass")
    await cdp.call("Input.insertText", text=password)
    for t in ("keyDown", "keyUp"):
        await cdp.call("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter", windowsVirtualKeyCode=13, text=chr(13))
    for _ in range(40):                                  # redirected to the dashboard once the cookie is set
        url = (await cdp.call("Runtime.evaluate", returnByValue=True, expression="location.pathname"))["result"].get("value", "")
        if str(url).startswith("/app"):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("sign-in did not reach the dashboard")


async def shoot(shots: list[str], zone: str, dash_port: int, width: int, height: int, wait: float, out: Path,
                login: tuple[str, str]) -> list[Path]:
    page = json.load(urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{DEBUG_PORT}/json/new?about:blank", method="PUT"), timeout=10))
    ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
    cdp = Cdp(ws)
    await cdp.call("Page.enable")
    await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
    saved = []
    await sign_in(cdp, dash_port, *login)                 # once: the cookie is shared by every page that follows
    for s in shots:
        slug = "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")
        url = f"http://localhost:{dash_port}/app/?screen={urllib.parse.quote(s)}&zone={zone}"
        await cdp.call("Page.navigate", url=url)
        await asyncio.sleep(wait)
        # Streamlit renders below the fold: grow the viewport to the content height so the whole screen is captured
        h = (await cdp.call("Runtime.evaluate", expression=(
            "(document.querySelector('[data-testid=\"stMain\"]')||document.body).scrollHeight"),
            returnByValue=True))["result"]["value"]
        await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=min(max(height, int(h) + 40), 3000),
                       deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(1.5)
        data = (await cdp.call("Page.captureScreenshot", format="png"))["data"]
        png = out / f"{slug}.png"
        png.write_bytes(base64.b64decode(data))
        await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        print(f"captured {s:15} -> {png}  ({png.stat().st_size / 1024:.0f} KB)")
        saved.append(png)
    await ws.close()
    return saved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="Overview,Live map,Zone detail,Source insight,Forecast,Events,Node health")
    ap.add_argument("--zone", default="HITEC")
    ap.add_argument("--dash-port", type=int, default=8000, help="the website/backend port (the dashboard is served under /app/)")
    ap.add_argument("--width", type=int, default=1500)
    ap.add_argument("--height", type=int, default=950)
    ap.add_argument("--wait", type=float, default=15, help="real seconds to let each screen render")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--as", dest="role", choices=["admin", "user"], default="admin",
                    help="demo account to sign in with (credentials from data/demo/credentials.json)")
    a = ap.parse_args()
    browser = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not browser:
        print("No Edge/Chrome found; take screenshots manually (Win+Shift+S) using the ?screen= links in docs/demo_script.md")
        return 1
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{a.dash_port}/healthz", timeout=3)
    except Exception:
        print(f"dashboard is not running on :{a.dash_port} (run infra/run_demo.ps1)")
        return 1
    creds = ROOT / "data" / "demo" / "credentials.json"
    if not creds.exists():
        print("no data/demo/credentials.json - start the demo once (infra/run_demo.ps1) to create the demo logins")
        return 1
    name = "official.demo" if a.role == "admin" else "public.demo"
    acct = json.loads(creds.read_text())["accounts"][name]
    login = (name, acct["password"])
    out = a.out or ROOT / "data" / "demo" / "evidence" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="airdna_cdp_")
    proc = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        saved = asyncio.run(shoot([s.strip() for s in a.shots.split(",")], a.zone, a.dash_port,
                                  a.width, a.height, a.wait, out, login))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
    print(f"\n{len(saved)} screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
