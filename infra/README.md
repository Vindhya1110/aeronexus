# infra/ - one-command demo, crash recovery, evidence (Phase 5)

Windows / PowerShell 5.1+. Run everything from the repo root. Presenting? Read `docs/demo_script.md`.

| script | what it does |
|---|---|
| `run_demo.ps1 [-Mode live\|replay] [-Speedup 12] [-Rebuild] [-NoBrowser]` | stops leftovers, loads a **fresh** seeded database, starts backend -> worker (scheduled) -> simulator (live mode) -> dashboard -> supervisor, health-checks each, prints `READY` |
| `stop_demo.ps1` | kills every demo process tree (supervisor first), sweeps strays and anything on ports 8000 (public) and 8501 (internal dashboard); exits non-zero if a port is still busy |
| `crash_test.ps1 [-Auto] [-OutageS 20]` | kills the backend mid-stream, restarts it (by hand, or `-Auto` = the supervisor does), proves buffered packets arrive with no loss and no duplicates |
| `capture_evidence.ps1 [-Shots ...] [-Zone HITEC]` | PNG screenshots of every dashboard screen via headless Edge/Chrome |
| `supervisor.ps1` | started by `run_demo.ps1`; restarts backend / worker / dashboard if they die or stop answering; keeps the PC awake |
| `_demo_lib.ps1` | shared helpers (not run directly) |

## One website, one port

Everything the browser sees is **http://localhost:8000**: the home page (`site/`, plain HTML/CSS/JS, no CDNs, works
offline) at `/`, the dashboard at **`/app/`**, the API at `/v1/*`, docs at `/docs`. The dashboard is still its own
Streamlit process, but it listens only on `127.0.0.1:8501` and the backend proxies `/app/` to it (HTTP and the live
websocket, `backend/proxy.py`), so there is no second site/port to open or share. If the dashboard is down `/app/` shows a
"starting" page and the supervisor restarts it. The home page's **Login to view stats** buttons go to `/app/`; it reads
only `GET /v1/public/summary` (zone AQI + category; no sources/events/devices), open even when login is enforced. The
dashboard uses the same palette, logo, navbar and motion (`dashboard/theme.py`, `.streamlit/config.toml`).

## Logins

`run_demo.ps1` creates two accounts in the demo database on every start and runs the demo API with sign-in enforced:
**official.demo** (official: all screens + accounts) and **public.demo** (public: overview, map, heatmap, zone detail, forecast, trends).
Their generated passwords are in `data\demo\credentials.txt` (created on first run; delete `data\demo\credentials.json` to regenerate).
Sign in once on the home page: the HttpOnly login cookie is shared by `/` and `/app/` (one origin), so moving between the site and the
dashboard never asks again; **Sign out** (either bar) clears it. Real accounts: `python tools/create_user.py ...`. Screenshots sign in through
the same pop-up (`capture_evidence.ps1 -As user` for the public view).

## Modes

* **live** (default): history 03:00-09:30 IST of the scripted morning is preloaded; the simulator then continues the
  same scenario at x12 (1 wall minute = 12 city minutes), so the HITEC smoke episode appears ~2.5 min after start.
  Worker runs every 15 s.
* **replay**: the whole scenario (to 10:50 IST, HITEC anomaly open) is preloaded, no simulator. Liveness thresholds are
  relaxed (`AIRDNA_OFFLINE_AFTER_S=604800`) so recorded devices do not turn "offline" five minutes after loading.
  Use it as the fallback and for screenshots.

## Measured (this repo, Windows 11)

| | result |
|---|---|
| cold start, cached DB, live mode | 14-20 s to READY (dashboard serving live data) |
| cold start from nothing (no cached DB; includes ingest 7,425 packets + worker backfill) | 55 s |
| reset to replay state | ~10 s (`-Mode replay`), DB restore itself < 1 s |
| determinism | two full rebuilds from JSONL and a snapshot restore give identical `tools/narrative_fingerprint.py` hashes |
| kill backend 15-25 s mid-stream | 0 missing, 0 duplicated packets; supervisor auto-restart ~10 s after the kill |

## How the seeded dataset works

* `simulator/scenario.py` overlays two scripted episodes on the seeded simulator output: a morning traffic jam at
  MGBSEC and an open-burning smoke episode at HITEC (PM2.5 + MQ-2 + MQ-135 up, MQ-9 flat). Deterministic: depends only
  on the packet's own timestamp.
* `python tools/demo_data.py prepare` writes `data/demo/demo_full.jsonl` (+ ground truth, evaluation only) and the
  pre-09:30 history. `load --mode live|replay [--rebuild]` ingests it through the real ingest path into
  `data/demo/demo.db`, runs `worker.run_once --backfill`, and caches the result as `snapshot_<mode>.db` (keyed by a hash
  of the dataset + backend/worker/shared/simulator sources, so a stale snapshot is never reused).
* The demo uses its own database (`data/demo/demo.db`); `data/airdna.db` is untouched.
* Dataset flags: `--no-faults` (no malformed / duplicate / dropout injection) so the story and the integrity check are
  exact. Fault handling is covered by the test suite and the normal `python -m simulator live`.

## Things worth knowing

* **No `--reload`** for the demo backend (the reloader orphans a child process and respawns on `data/` writes).
* The dashboard is launched by `python -m dashboard.serve`, not bare `streamlit run`: on Windows the default asyncio loop
  permanently closes the listening port if a browser resets a connection mid-accept, leaving a "running" dashboard that
  answers nothing. Reproduced and fixed (`dashboard/serve.py`).
* Starting scripts from a terminal returns when READY; the components keep running hidden. Logs: `data/demo/logs/`.
  If you close the launching terminal the components keep running, but their log files may stop updating.
* The simulator is intentionally **not** supervised: restarting it would drop its in-memory retry buffer and muddy the
  no-loss story. If it dies, rerun `run_demo.ps1`.
* **Sleep freezes everything.** On this laptop (Windows Modern Standby, event log 506/507) an idle PC suspended all demo
  processes for 10-45 min several times while we tested; on resume the simulator jumped ahead and "caught up" in a burst
  (its clock is monotonic wall time, which includes the suspended period). The supervisor asks Windows to stay awake
  (`SetThreadExecutionState`) but that cannot undo a standby that has already begun. Before presenting: plug in, power
  plan "never sleep", screen on, and start the demo only when you are at the machine.

## Manual steps that remain

1. Run `run_demo.ps1` (nothing else); optionally `-Rebuild` once after changing worker / backend code.
2. Open the dashboard (done automatically unless `-NoBrowser`).
3. Screenshots: run `capture_evidence.ps1` at the right moment (live mode: 4-8 min in). Screen *recordings* are manual
   (Win+Alt+R / OBS).
4. The Round 1 / Round 2 documents themselves.
