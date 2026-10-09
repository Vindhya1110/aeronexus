# Aeronex - Zonal AQI Intelligence Prototype (Hyderabad)

Rising-AQI prototype for Hyderabad. Pipeline:
`sensors -> [out of scope] -> ingestion API -> DB + analytics worker -> dashboard + alerts`.

| folder | status |
|---|---|
| `shared/` | done (Phase 0): schema, zones, constants, fixtures |
| `simulator/` | done (Phase 1): fake gateway, live POST + replay dump |
| `backend/` | done (Phase 2): FastAPI ingest + read API, SQLite, alembic |
| `worker/` | done (Phase 3): AQI, anomaly events, source hypothesis, forecast, coverage, scheduler |
| `site/` | done (Phase 6): animated landing website (navbar, hero, live snapshot, features, footer, Login button), served by the backend at `/` |
| `dashboard/` | done (Phase 4): Streamlit, 7 screens; offline-safe map, deep links (`?screen=Events`), `python -m dashboard.serve` |
| `ml/` | done (Phase 7): 8 ML/analytics features (forecast, early warning, source classifier, hotspots + anomaly, kriging heatmap, drift correction, risk ranking, trends) - see `docs/ml.md`, measured results in `docs/ml_results.md` |
| `tools/` | `load_replay.py`, `evaluate_source_hypothesis.py`, demo tooling: `demo_data.py`, `check_integrity.py`, `narrative_fingerprint.py`, `capture_screens.py` |
| `firmware/`, `gateway/` | out of scope placeholders |
| `infra/` | done (Phase 5): one-command demo (`run_demo.ps1`), `stop_demo.ps1`, `crash_test.ps1`, `capture_evidence.ps1`, supervisor - see `infra/README.md` |
| `docs/`, `data/` | `docs/demo_script.md` (presenter run-sheet), `docs/device_ids.md`; `data/` is gitignored except `sample_*` |


## Quick start (fresh clone)

Full guide, troubleshooting and the login explanation: **[docs/SETUP.md](docs/SETUP.md)**. Short version (Python 3.11+):

```powershell
# Windows PowerShell
git clone <repo-url> aeronex ; cd aeronex
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe tools\run_demo.py        # or: powershell -ExecutionPolicy Bypass -File .\infra\run_demo.ps1
type data\demo\credentials.txt                     # YOUR logins (generated on first run, different on every machine)
```
```bash
# macOS / Linux
git clone <repo-url> aeronex && cd aeronex
python3 -m venv venv && ./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python tools/run_demo.py
cat data/demo/credentials.txt
```
Open **http://localhost:8000** and click *Login to view stats*. `python -m pytest` runs the tests.

## More demo commands (Windows / PowerShell)

```powershell
.\infra\run_demo.ps1                  # backend + worker + simulator + dashboard, health-checked
.\infra\run_demo.ps1 -Mode replay     # fallback: recorded scenario
.\infra\crash_test.ps1 -Auto          # kill the backend mid-stream; prove no data loss
.\infra\stop_demo.ps1
```
Presenting? `docs/demo_script.md`.
