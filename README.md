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

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
python -m simulator sample
```
Target Python 3.11 (developed on 3.14; code avoids 3.12+ features).

## Run the demo (Windows / PowerShell)

```powershell
.\infra
un_demo.ps1                  # backend + worker + simulator + dashboard, health-checked (~15-55 s)
.\infra
un_demo.ps1 -Mode replay     # fallback: recorded scenario, ~10 s
.\infra\crash_test.ps1 -Auto          # kill the backend mid-stream; prove no data loss
.\infra\stop_demo.ps1
```
Presenting? `docs/demo_script.md`.
