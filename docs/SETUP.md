# Aeronex - set up and run on a new machine

Tested from a clean `git clone` into an empty folder on Windows (new virtual environment, fresh install, first run, both logins).

## 1. Requirements
* Python **3.11 or newer** (developed on 3.14). Git.
* About 1 GB of disk and 5-10 minutes for `pip install` (pandas, scikit-learn, statsmodels, osmnx, streamlit ...).
* No internet is needed to run it afterwards (the trained models, route cache and OpenStreetMap exposure data are in the repo).

## 2. Install (once)

**Windows (PowerShell)**
```powershell
git clone <repo-url> aeronex
cd aeronex
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```
**macOS / Linux**
```bash
git clone <repo-url> aeronex && cd aeronex
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```
The virtual environment folder must be called **`venv`** (the PowerShell scripts look for `venv\Scripts\python.exe`).

## 3. Run

Pick ONE of these (same stack: backend + analytics worker + simulator + dashboard):

| | Windows | macOS / Linux |
|---|---|---|
| any OS, foreground, Ctrl+C stops it | `.\venv\Scripts\python.exe tools\run_demo.py` | `./venv/bin/python tools/run_demo.py` |
| Windows, background + auto-restart + stop script | `powershell -ExecutionPolicy Bypass -File .\infra\run_demo.ps1` then `.\infra\stop_demo.ps1` | - |

Options (both launchers): `--mode replay` / `-Mode replay` for the recorded story (instant), `--port 8100 --dash-port 8601` / `-Port 8100 -DashPort 8601`
if 8000 or 8501 are taken, `--rebuild` / `-Rebuild` to rebuild the demo database.

**First run only:** the demo database is built from the seeded dataset, about **2-5 minutes**. Every later run restores a snapshot in seconds.
Wait for the line `READY`.

Then open **http://localhost:8000** (the home page). The dashboard is at `http://localhost:8000/app/` - the same site and port.

## 4. Logging in (the part that goes wrong)

The two demo accounts are **created on your machine the first time you run the demo, with random passwords**. Your passwords are NOT the same as
anyone else's, including the machine the repo was developed on.

```powershell
type data\demo\credentials.txt          # Windows        (macOS/Linux: cat data/demo/credentials.txt)
```
| account | role | sees |
|---|---|---|
| `public.demo` | public | Overview, Live map, City heatmap, Zone detail, Forecast, Trends |
| `official.demo` | official | all 11 screens (adds Source insight, Risk & hotspots, Node health, Events, Accounts) |

Sign in once on the **home page** (button *Login to view stats*). The session is a browser cookie shared by the home page and the dashboard, so you can
move between them freely. *Sign out* is in both top bars.

Want your own password? `python tools/create_user.py --username public.demo --reset-password` (stop the demo first; the demo database is rebuilt
on every start, so for a permanent account create it with `tools/create_user.py` against your own database, see `backend/README.md`).

### "I cannot log in" - checklist
1. **Using someone else's password.** Passwords are generated per machine: read *your* `data/demo/credentials.txt`.
2. **Opened the wrong address.** Use `http://localhost:8000` (not `:8501`, the internal dashboard port, and not `https`).
3. **Started only the backend** (`uvicorn backend.main:app ...`). Then no accounts exist and `/app/` shows "starting". Use `tools/run_demo.py`, which creates the
   accounts, starts the dashboard and sets the signing key.
4. **Port already in use** (another copy running). The launcher says so and stops. Stop the other copy, or use `--port 8100 --dash-port 8601`
   and open that port instead.
5. **Browser blocks cookies for localhost** (strict privacy setting or an extension). Allow cookies for `localhost`, or try another browser/window.
6. **Changed code or updated the repo and the login looks stale.** Stop everything and start again; delete `data/demo/credentials.json` to regenerate both passwords.
7. **Looking at the log:** `data/demo/logs/backend.log` (or `backend.err.log` for the PowerShell launcher).

### Other problems
* `running scripts is disabled on this system` (PowerShell): run with `-ExecutionPolicy Bypass` as shown, or `Set-ExecutionPolicy -Scope Process Bypass`.
* `python` not found on Windows: try `py -3.11 -m venv venv` or install Python from python.org and tick *Add to PATH*.
* `pip install` fails building a package: use Python 3.11-3.13 (very new interpreters can lack wheels for some packages).
* The dashboard shows "Sign in to continue": you are not signed in (or signed out); use the button, it goes to the home page login.
* `data/demo/` is local and git-ignored; deleting it just forces a rebuild.

## 4b. ESP32 sensor node (optional)
`tools/run_demo.py --hardware COM5` (or `--hardware sim` for a synthetic node, no hardware needed) adds the node to the running stack. Details and what the node can and
cannot contribute: `firmware/README.md`, `gateway/README.md`.

## 5. Stop
`Ctrl+C` in the `tools/run_demo.py` window, or `.\infra\stop_demo.ps1` for the PowerShell launcher.

## 6. Tests (optional, about 6 minutes)
```powershell
.\venv\Scripts\python.exe -m pytest -q
```

## 7. Retrain the ML models (optional; trained models are already in `ml/artifacts/`)
See `docs/ml.md`: `python -m ml.data generate --days 365`, then `python -m ml.train_all` (about 40 minutes).
