"""USB-serial bridge: Aeronex ESP32-S3 node  ->  Aeronex backend (POST /v1/telemetry).

    python -m gateway.serial_bridge --list-ports
    python -m gateway.serial_bridge --port COM5 --device-id ZONE_HITEC_90 --url http://localhost:8000
    python -m gateway.serial_bridge --simulate --interval-s 4 --url http://localhost:8000     # no hardware needed: synthetic node
    python -m gateway.serial_bridge --replay data/node_log.txt --dry-run                      # replay a captured serial log

It is a store-and-forward gateway, exactly like the simulator's (it reuses `simulator.gateway.GatewayClient`): 2xx/409 = delivered,
other 4xx = rejected (not retried), 5xx / no connection = buffered and resent in order. Sequence numbers are saved in
data/hw_state.json so a restart never reuses one. Sends the WINDOW MEAN of the readings (default every 30 s); if the node goes silent
nothing is sent (the backend then shows it offline, it never invents readings).

Hardware mode: with --mode auto (default) the bridge polls the public summary every 30 s and sends `MODE,warning` to the node while its
zone's AQI is above 300 (back to `MODE,normal` below 270). `--mode normal|warning|silent` forces a mode.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests

from shared.constants import DEVICE_ID_RE
from simulator.gateway import GatewayClient

from .hardware import NodeConfig, Window, build_payload, next_mode, parse_csv_line, parse_mode_echo

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "hw_state.json"
log = logging.getLogger("aeronex.bridge")


# ------------------------------------------------------------------------------------------------ line sources
class SerialSource:
    """Reads lines from the node's USB serial port; reconnects if the cable is unplugged. write() sends commands to the node."""

    def __init__(self, port: str, baud: int = 115200):
        self.port, self.baud, self.ser = port, baud, None

    def _open(self) -> None:
        import serial
        # serial_for_url: a normal port name (COM5, /dev/ttyACM0) or a pyserial URL (loop://, socket://host:port) for testing
        self.ser = serial.serial_for_url(self.port, baudrate=self.baud, timeout=1.0, do_not_open=True)
        self.ser.dtr = self.ser.rts = False                  # opening the port must not reset the board if we can help it
        self.ser.open()
        log.info("serial port %s open at %d baud", self.port, self.baud)

    def lines(self) -> Iterator[Optional[str]]:
        while True:
            try:
                if self.ser is None:
                    self._open()
                raw = self.ser.readline()
                yield raw.decode("utf-8", errors="replace").strip() if raw else None     # None = timeout, lets the caller do its timers
            except Exception as e:  # noqa: BLE001 - unplugged / busy port: wait and retry
                log.warning("serial problem (%s); retrying in 3 s", e.__class__.__name__)
                self.ser = None
                time.sleep(3)
                yield None

    def write(self, text: str) -> None:
        try:
            if self.ser is not None:
                self.ser.write(text.encode())
        except Exception as e:  # noqa: BLE001
            log.warning("could not send %r to the node (%s)", text.strip(), e.__class__.__name__)


class ReplaySource:
    """Lines from a captured serial log (also what `--simulate` generates). Paced at one line per `delay_s`."""

    def __init__(self, lines: list[str], delay_s: float = 0.0, loop: bool = False):
        self.src, self.delay_s, self.loop = lines, delay_s, loop

    def lines(self) -> Iterator[Optional[str]]:
        while True:
            for ln in self.src:
                yield ln
                if self.delay_s:
                    time.sleep(self.delay_s)
            if not self.loop:
                return

    def write(self, text: str) -> None:
        log.info("(no hardware) would send to node: %s", text.strip())


def synthetic_lines(seed: int = 7) -> Iterator[str]:
    """A believable node on a desk: warm-up drift in the MQ readings, DHT11 occasionally failing. Endless, 2 s of node time per line."""
    rng = random.Random(seed)
    ms, mq135, mq2, mq9 = 0, 1500.0, 1100.0, 1250.0
    while True:
        ms += 2000
        warm = max(0.0, 1.0 - ms / 120000.0)
        mq135 += rng.gauss(0, 6) + (1500 - mq135) * 0.02
        mq2 += rng.gauss(0, 5) + (1100 - mq2) * 0.02
        mq9 += rng.gauss(0, 5) + (1250 - mq9) * 0.02
        dht_ok = rng.random() > 0.05
        yield ("CSV_DATA,%d,%d,%d,%d,%s,%s,%.1f,%.2f,%.2f" % (
            ms, mq135 * (1 + 0.4 * warm), mq2 * (1 + 0.4 * warm), mq9 * (1 + 0.4 * warm),
            "%.1f" % (30.5 + rng.gauss(0, 0.2)) if dht_ok else "", "%.1f" % (52 + rng.gauss(0, 0.8)) if dht_ok else "",
            30.2 + rng.gauss(0, 0.1), 951.8 + rng.gauss(0, 0.15), 541.0 + rng.gauss(0, 1.2)))


# ------------------------------------------------------------------------------------------------ state + mode polling
def load_seq(device_id: str, fresh: bool) -> int:
    if fresh or not STATE.exists():
        return 0
    try:
        return int(json.loads(STATE.read_text()).get(device_id, 0))
    except Exception:  # noqa: BLE001
        return 0


def save_seq(device_id: str, seq: int) -> None:
    try:
        data = json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:  # noqa: BLE001
        data = {}
    data[device_id] = seq
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data))


def zone_aqi(base_url: str, zone_code: str) -> Optional[float]:
    """AQI of the node's zone from the PUBLIC summary endpoint (no login needed)."""
    try:
        r = requests.get(base_url.rstrip("/") + "/v1/public/summary", timeout=5)
        r.raise_for_status()
        for z in r.json()["zones"]:
            if z["zone_id"] == zone_code:
                return z["aqi"]
    except Exception:  # noqa: BLE001
        pass
    return None


def run(args, source, client: Optional[GatewayClient]) -> int:
    cfg = NodeConfig(device_id=args.device_id, warmup_s=args.warmup_s, lat=args.lat, lon=args.lon)
    zone_code = DEVICE_ID_RE.match(cfg.device_id).group("code")
    seq = load_seq(cfg.device_id, args.fresh)
    window = Window()
    mode, mode_checked, sent, last_line = "normal", 0.0, 0, time.monotonic()
    span_ms = args.interval_s * 1000
    log.info("node %s -> %s  (window %ds, resuming at sequence %d)", cfg.device_id, args.url if client else "DRY RUN", args.interval_s, seq + 1)

    def flush() -> None:
        nonlocal window, seq, sent
        wire = build_payload(window, cfg, seq + 1)
        window = Window()
        if wire is None:
            return
        seq += 1
        sent += 1
        if client is None:                       # dry run: print only (and do not save the sequence number)
            print(json.dumps(wire), flush=True)
            return
        save_seq(cfg.device_id, seq)
        client.send(wire)
        log.info("sent seq=%d  mq2=%s mq9=%s mq135=%s T=%s RH=%s P=%s  flags=%d%s  [delivered %d, buffered %d]", seq,
                 wire["gas_proxy"]["mq2_adc"], wire["gas_proxy"]["mq9_adc"], wire["gas_proxy"]["mq135_adc"],
                 wire["env"]["temperature_c"], wire["env"]["humidity_pct"], wire["env"]["pressure_hpa"],
                 wire["quality"]["sensor_flags"], "  (warm-up)" if wire["quality"]["warmup"] else "",
                 client.stats["delivered"], len(client.buffer))

    try:
        for line in source.lines():
            now = time.monotonic()
            if line:
                last_line = now
                r = parse_csv_line(line)
                if r is not None:
                    if len(window) and r.uptime_ms < window.readings[-1].uptime_ms:
                        flush()                                   # the node rebooted: close what we have, new window (and warm-up) starts
                    window.add(r)
                    # the window is measured in NODE time (its millis), so it closes the same way live, on replay and in tests
                    if r.uptime_ms - window.readings[0].uptime_ms >= span_ms:
                        flush()
                elif (m := parse_mode_echo(line)) is not None:
                    log.info("node confirmed mode: %s", m)
            elif now - last_line > 15 and int(now - last_line) % 15 == 0:
                log.warning("no data from the node for %d s (is it powered and flashed?)", now - last_line)

            # hardware mode: forced, or auto from the node's zone AQI (polled every 30 s; the firmware shows it on the OLED header)
            if args.mode == "auto":
                if now - mode_checked > 30:
                    mode_checked = now
                    wanted = next_mode(mode, zone_aqi(args.url, zone_code) if client else None, "auto")
                else:
                    wanted = mode
            else:
                wanted = args.mode
            if wanted != mode:
                mode = wanted
                source.write(f"MODE,{mode}\n")
                log.info("hardware mode -> %s", mode)
            if args.max_packets and sent >= args.max_packets:
                break
    except KeyboardInterrupt:
        pass
    log.info("stopped after %d packets", sent)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--port", help="serial port of the node, e.g. COM5 or /dev/ttyACM0")
    src.add_argument("--simulate", action="store_true", help="no hardware: generate a synthetic node (for testing the pipeline)")
    src.add_argument("--replay", type=Path, help="replay a captured serial log file")
    src.add_argument("--list-ports", action="store_true")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--device-id", default="ZONE_HITEC_90", help="ZONE_<ZONECODE>_<NN>; the zone code decides which zone the node belongs to")
    ap.add_argument("--url", default=os.getenv("AIRDNA_BACKEND_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.getenv("AIRDNA_API_KEY", "dev-key"))
    ap.add_argument("--interval-s", type=int, default=30, help="send the mean of the readings every N seconds")
    ap.add_argument("--warmup-s", type=int, default=300, help="node uptime below which packets carry quality.warmup=true")
    ap.add_argument("--lat", type=float, help="surveyed install latitude (optional; the node has no GPS module)")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--mode", choices=["auto", "normal", "warning", "silent"], default="auto")
    ap.add_argument("--dry-run", action="store_true", help="print payloads instead of sending them")
    ap.add_argument("--fresh", action="store_true", help="ignore the saved sequence number (use after wiping the database)")
    ap.add_argument("--max-packets", type=int, default=0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.list_ports:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        print("\n".join(f"{p.device}\t{p.description}" for p in ports) or "no serial ports found")
        return 0
    if a.simulate:
        source = ReplaySource([], 0)
        source.lines = lambda: _paced(synthetic_lines(), 2.0 if a.interval_s > 2 else 0.2)       # type: ignore[method-assign]
    elif a.replay:
        source = ReplaySource(a.replay.read_text(encoding="utf-8", errors="replace").splitlines(), 0.0)
    elif a.port:
        source = SerialSource(a.port, a.baud)
    else:
        ap.error("choose one of --port, --simulate, --replay, --list-ports")
    client = None if a.dry_run else GatewayClient(a.url, a.api_key)
    return run(a, source, client)


def _paced(gen: Iterator[str], delay_s: float) -> Iterator[Optional[str]]:
    for ln in gen:
        yield ln
        time.sleep(delay_s)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
