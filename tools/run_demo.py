"""Cross-platform one-command demo (Windows, macOS, Linux). Same stack as infra/run_demo.ps1, in the foreground.

    python tools/run_demo.py                       # live mode: story plays at x12
    python tools/run_demo.py --mode replay         # recorded story, ready in seconds once the snapshot exists
    python tools/run_demo.py --port 8100 --dash-port 8601 --rebuild

Starts backend + worker + (live) simulator + dashboard, waits until they answer, prints the URL and where the logins are.
Ctrl+C stops everything. Logs: data/demo/logs/. Logins: data/demo/credentials.txt (generated on FIRST run, different on every machine).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "data" / "demo"
LOGS = DEMO / "logs"
PY = sys.executable
LIVE_START = "2026-12-10T04:00:00Z"            # 09:30 IST: where the live story continues from the preloaded history


def get_json(url: str, headers: dict | None = None, timeout: float = 5.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait(test, secs: float, what: str) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            if test():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise SystemExit(f"TIMEOUT waiting for: {what} (see data/demo/logs/)")


def port_free(port: int) -> bool:
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dash-port", type=int, default=8501)
    ap.add_argument("--speedup", type=float, default=12)
    ap.add_argument("--rebuild", action="store_true", help="ignore the cached database snapshot")
    ap.add_argument("--hardware", metavar="PORT", help="also run the ESP32 node bridge: a serial port (COM5, /dev/ttyACM0) or 'sim' for a synthetic node")
    ap.add_argument("--hardware-id", default="ZONE_HITEC_90", help="device id of the node (its zone code decides the zone)")
    ap.add_argument("--hardware-interval", type=int, default=30, help="seconds per packet from the node (window mean)")
    a = ap.parse_args()

    for p in (a.port, a.dash_port):
        if not port_free(p):
            raise SystemExit(f"port {p} is already in use - stop the other Aeronex run first, or pass --port / --dash-port")

    LOGS.mkdir(parents=True, exist_ok=True)
    db = DEMO / "demo.db"
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "AIRDNA_DATABASE_URL": f"sqlite:///{db.as_posix()}", "AIRDNA_API_KEY": "dev-key",
           "AIRDNA_BACKEND_URL": f"http://127.0.0.1:{a.port}", "AIRDNA_REQUIRE_AUTH": "1", "AIRDNA_AUTOREFRESH_S": "10",
           "AIRDNA_WORKER_INTERVAL_S": "15" if a.mode == "live" else "60",
           "AIRDNA_OFFLINE_AFTER_S": "300" if a.mode == "live" else "604800",     # replayed devices must not turn "offline" after 5 min
           "AIRDNA_DASHBOARD_UPSTREAM": f"http://127.0.0.1:{a.dash_port}"}
    env.pop("AIRDNA_DASHBOARD_URL", None)

    def run(*args: str, log: str) -> None:
        with open(LOGS / f"{log}.log", "w", encoding="utf-8") as f:
            r = subprocess.run([PY, *args], cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            raise SystemExit(f"{log} failed (exit {r.returncode}); see data/demo/logs/{log}.log")

    t0 = time.time()
    print(f"[{time.time() - t0:5.1f}s] preparing seeded dataset + fresh database ({a.mode}); the first run builds it (about 2-5 min), later runs restore a snapshot")
    run("tools/demo_data.py", "prepare", log="demo_prepare")
    run("tools/demo_data.py", "load", "--mode", a.mode, *(["--rebuild"] if a.rebuild else []), log="demo_load")
    creds = json.loads((DEMO / "credentials.json").read_text())
    env["AIRDNA_AUTH_SECRET"] = creds["auth_secret"]

    procs: list[subprocess.Popen] = []

    def start(name: str, *args: str) -> None:
        f = open(LOGS / f"{name}.log", "w", encoding="utf-8")
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        procs.append(subprocess.Popen([PY, *args], cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, **flags))

    def stop_all(*_):
        for p in procs:
            if p.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
                else:
                    try:
                        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        print("\nAeronex stopped.")

    try:
        base = f"http://127.0.0.1:{a.port}"
        print(f"[{time.time() - t0:5.1f}s] starting backend")
        start("backend", "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(a.port), "--log-level", "warning")
        wait(lambda: get_json(f"{base}/healthz")["status"] == "ok", 40, "backend /healthz")
        off = creds["accounts"]["official.demo"]
        token = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"{base}/v1/auth/login", data=json.dumps({"username": "official.demo", "password": off["password"]}).encode(),
            headers={"Content-Type": "application/json"}), timeout=15).read().decode())["token"]
        auth = {"Authorization": f"Bearer {token}"}
        print(f"[{time.time() - t0:5.1f}s] starting worker")
        start("worker", "-m", "worker")
        wait(lambda: sum(1 for z in get_json(f"{base}/v1/zones", auth)["zones"] if z["aqi"] and z["coverage"]) == 6, 90, "worker output for all 6 zones")
        if a.mode == "live":
            print(f"[{time.time() - t0:5.1f}s] starting simulator (x{a.speedup:g}, scripted scenario, continues at 09:30 IST)")
            start("simulator", "-m", "simulator", "live", "--scenario", "demo", "--no-faults", "--offline-routes", "--url", base,
                  "--start", LIVE_START, "--speedup", str(a.speedup))
            wait(lambda: sum(1 for d in get_json(f"{base}/v1/devices", auth)["devices"] if d["last_age_s"] < 20) >= 8, 60, "live packets from >= 8 devices")
        if a.hardware:
            print(f"[{time.time() - t0:5.1f}s] starting ESP32 node bridge ({'synthetic node' if a.hardware == 'sim' else a.hardware} -> {a.hardware_id})")
            bridge = ["-m", "gateway.serial_bridge", "--url", base, "--device-id", a.hardware_id, "--interval-s", str(a.hardware_interval),
                      "--fresh"]                                          # the demo database was just recreated: restart the node's sequence at 1
            start("bridge", *bridge, *(["--simulate"] if a.hardware == "sim" else ["--port", a.hardware]))
        print(f"[{time.time() - t0:5.1f}s] starting dashboard")
        start("dashboard", "-m", "dashboard.serve", "--port", str(a.dash_port))
        wait(lambda: urllib.request.urlopen(f"http://127.0.0.1:{a.dash_port}/app/_stcore/health", timeout=3).read() == b"ok", 60, "dashboard health")
        print(f"\n[{time.time() - t0:5.1f}s] READY\n")
        shown = f"http://localhost:{a.port}"
        print(f"  Website  : {shown}/        (home page; the Login button leads to the dashboard at {shown}/app/)")
        print(f"  API docs : {shown}/docs")
        print(f"  Logins   : {DEMO / 'credentials.txt'}   (official.demo = official, public.demo = public; passwords are generated per machine)")
        print("  Stop     : Ctrl+C in this window\n")
        while True:
            time.sleep(2)
            dead = [p for p in procs if p.poll() is not None]
            if dead:
                print(f"a component exited (code {dead[0].returncode}); see data/demo/logs/")
                return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop_all()


if __name__ == "__main__":
    sys.exit(main())
