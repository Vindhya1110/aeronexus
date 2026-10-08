# Aeronex - demo script (Hyderabad)

A literal run-sheet. Everything below was run end to end on this repo; timings are measured, not estimated.
All commands are from the repo root in **PowerShell**.

> **Be upfront with judges:** the sensors are *simulated* (a drop-in fake gateway that speaks the real data contract).
> The demo story is a *scripted scenario* layered on seeded, physically-modelled data - it shows the pipeline
> working end to end, it is not an accuracy benchmark. Say that before they ask.

## 0. Before you go on stage

| when | do | why |
|---|---|---|
| T-30 min | `.\infra\run_demo.ps1 -Mode replay -Rebuild` once, then `.\infra\stop_demo.ps1` | builds + caches the seeded database (first build ~50 s; every later start is ~10 s) |
| T-10 min | plug in power, set Windows power plan to "never sleep", keep the screen on, close heavy apps | this laptop enters Modern Standby when idle and freezes every process (see infra/README.md); the supervisor asks Windows to stay awake but cannot undo a standby that already started |
| T-3 min | `.\infra\run_demo.ps1` (live mode) | cold start measured at **14-20 s** with a cached DB, 55 s from absolute scratch |
| T-0 | browser on http://localhost:8000 (the script opens it): one website, dashboard at /app/ | |

Deep links (use them to jump screens without hunting in the sidebar):

```
http://localhost:8000/app/?screen=Overview
http://localhost:8000/app/?screen=Live%20map
http://localhost:8000/app/?screen=Events
http://localhost:8000/app/?screen=Zone%20detail&zone=HITEC
http://localhost:8000/app/?screen=Source%20insight
http://localhost:8000/app/?screen=Forecast&zone=HITEC
http://localhost:8000/app/?screen=Node%20health
```

## 0a. Opening page

Start on the **website, http://localhost:8000** (opened by `run_demo.ps1`): hero with the live city-average gauge, the six
live zone cards, features, roles, footer. Talk for ~30 s, then click **Login to view stats** to land on the dashboard
sign-in. Particles in the hero tint with the city AQI category.

## 0b. Sign-in (two roles, one login)

Sign in **once, on the home page** (Login button -> small pop-up). The login is a browser cookie shared by the home page and the dashboard,
so you can click **Home** in the dashboard bar, look at the site, click **Dashboard** and you are still signed in. Sign out is in both bars.
Passwords are in `data\demo\credentials.txt` (run `type data\demo\credentials.txt`).
- **official.demo** (official): 11 screens, incl. Source insight, Risk & hotspots, Node health, Events, Accounts.
- **public.demo** (public citizen): 6 screens (Overview, Live map, City heatmap, Zone detail, Forecast, Trends); no source attribution,
  device internals, events or accounts. Side-by-side picture: `docs/role_comparison.png`.
- Anyone can create a *public* account from the pop-up's "Create account" tab; officials are only created by an administrator
  (Accounts screen, or `tools/create_user.py`).

## 1. The story and its clock

Live mode replays a fixed morning in Hyderabad (10 Dec 2026, winter) from 09:30 IST at x12, so **1 wall-clock
minute = 12 minutes of city time**. History before 09:30 (night calm -> rush-hour build-up, 03:00-09:30 IST) is
already loaded, so every chart has context the moment you open it.

| wall clock after READY | city time (IST) | what happens |
|---|---|---|
| 0:00 | 09:30 | morning rush at its peak: traffic-dominated zones are high, MGBSEC / CHNAM / PGPT read "probable traffic/combustion" |
| ~2:30 | 10:00 | an open-burning **smoke episode starts in Hitec City (HITEC)**: PM2.5 spikes, MQ-2 and MQ-135 proxies rise, MQ-9 barely moves |
| ~3:20-3:50 | 10:10-10:15 | worker flags an **anomaly event** at HITEC; within a pass or two the source hypothesis turns to *probable smoke/combustion signature* |
| ~4:40-5:00 | 10:30 | event is **confirmed** (breach persisted 15 data-minutes) and a **recommended action** is attached |
| ~9 | 11:15 | the scripted episode has decayed; the simulator keeps generating ordinary traffic |

The same story is what `-Mode replay` shows instantly (data frozen at 10:50 IST with the HITEC event open).

## 2. Presenting (about 6 minutes)

**Minute 0 - Overview** (`?screen=Overview`)
- "Six zones seeded from real Hyderabad pollution corridors (Charminar-Nampally, Hitec City, IDA Pashamylaram,
  MGBS-Secunderabad, Punjagutta-Patancheru, Sanathnagar). 11 devices: 7 fixed nodes and 4 buses."
- Point at one card: AQI badge (CPCB breakpoints, **PM only**, labelled indicative), the **raw** PM line underneath,
  source label with confidence, `Devices online 3/3`.
- "AQI is computed from smoothed PM2.5 / PM10 only. The gas sensors are raw proxy counts and never feed the AQI."

**Minute 1 - Live map** (`?screen=Live%20map`)
- Corridor lines and radii are coloured by AQI category; dots are devices (blue = online). Buses move along real
  OSM road geometry. Refresh happens automatically every 10 s.
- No internet at the venue? The page automatically switches to a self-contained map (no street tiles) - same data.

**Minute 2-3 - wait for the anomaly, talk through the pipeline**
- "Sensor -> gateway -> FastAPI ingest (schema-validated, duplicate-safe) -> SQLite -> worker (AQI, baselines,
  rules, forecast) -> dashboard." Mention raw data is immutable; derived values live in separate tables.

**Minute 3-4 - the anomaly** (`?screen=Events`)
- The newest HITEC event is open at the top (~3.5 min after start). Read it out: *"pm25 ... vs baseline ... (rolling_median 3h)"* -
  "anomaly means a deviation from **that zone's own** baseline, not a standards exceedance."
- Show **Source hypothesis at detection: probable smoke/combustion signature (confidence ~0.5-0.9)**.
  Say: "'probable' on purpose - it is a transparent rule-based hypothesis, not a lab measurement. Confidence is
  evidence strength, not a calibrated probability."
- Show **Recommended action** ("Check for open burning or industrial stack/boiler emissions nearby; re-measure")
  and the lifecycle line: detected -> confirmed -> recommended.
- If the event reads `insufficient evidence` at detection: that is the system being honest early in an episode;
  open Source insight - by the next worker pass the label has moved to smoke. (This happens for one of the two
  HITEC events by design of the data: PM2.5 trips a few minutes before the gas evidence is conclusive.)

**Minute 4 - why that label** (`?screen=Source%20insight`)
- HITEC card -> expand **Features**: PM10/PM2.5 ratio, MQ-2 and MQ-135 rise far above MQ-9, and the three rule scores.
  "Smoke raises MQ-2 and MQ-135 together; traffic raises MQ-9 first. Dust raises PM10 with flat gas."
- Point at a zone showing **insufficient evidence** (IDAPASH, steady industrial source): "it abstains instead of
  guessing."

**Minute 5 - Zone detail** (`?screen=Zone%20detail&zone=HITEC`)
- Raw vs smoothed PM2.5 / PM10 for HITEC: the spike and its decay; quality-flag table underneath (warm-up, GPS-invalid,
  sensor-fault counts per bucket). "Raw is never altered; smoothing is a separate field."

**Minute 5 - Forecast** (`?screen=Forecast&zone=HITEC`)
- 15 / 30 / 60 min, **persistence vs linear trend**, each labelled with its method and model version.
- "A blank means the method had too little history - it does not invent a number. Short-horizon, local data only."

**Minute 6 - Node health, then the resilience demo** (`?screen=Node%20health`)
- Online 11/11, firmware and calibration versions, faults, GPS validity.
- Now run the crash test (section 4) while the screen is up. Judges asking "what if this disconnects?" is the cue.

## 3. Fallback plan - live data not cooperating

Decide in the first 20 seconds; do not debug on stage.

| symptom | action | time |
|---|---|---|
| Dashboard blank / "Backend request failed" | wait 10 s (the supervisor restarts crashed components every ~6 s), refresh the page | 10 s |
| Still wrong, or the simulator stalled, or story timing is off | `.\infra\run_demo.ps1 -Mode replay` | **~10 s** |
| Anything weirder (stuck ports, stale processes) | `.\infra\stop_demo.ps1` then `.\infra\run_demo.ps1 -Mode replay` | ~15 s |
| Cached DB broken | `.\infra\run_demo.ps1 -Mode replay -Rebuild` | ~55 s |
| No internet | nothing to do: everything is local; the map falls back by itself (force it with `$env:AIRDNA_OFFLINE_MAP='1'`) | - |

Replay mode is the *same seeded dataset* the live story plays from (byte-identical every time, verified by
`tools/narrative_fingerprint.py`), loaded as a finished recording: the HITEC smoke event is already open, the
dashboard looks exactly like minute 4 of the live run. Tell the judges: "this is the recorded run of the same
scenario" - never present replay as live.

## 4. "What happens if it crashes?" - the live proof (about 40 seconds)

Live mode must be running. Open Node health first.

```powershell
.\infra\crash_test.ps1 -Auto -OutageS 20
```

What it does and what to say as it prints:

1. baseline integrity check (no gaps, no duplicates in the database);
2. **kills the backend process** - "that's a crash or a cut network link";
3. the simulator (acting as the gateway) logs `backend unreachable; buffering` and keeps generating;
4. **the supervisor notices and restarts the backend by itself** (about 6-10 s) - or pass no `-Auto` to restart by hand;
5. the buffered packets drain oldest-first; the script waits for the gateway to report `buffered=0`;
6. integrity check: every device's sequence numbers 1..N are all present, zero duplicates, DB row count >= history +
   everything the gateway reports delivered. Ends with `CRASH TEST: PASS - nothing lost, nothing duplicated`.

Why it works (one sentence for judges): the gateway only drops a packet after the backend acknowledges it, and the
backend stores `(device_id, sequence)` uniquely, so a resend is idempotent.

Honest limit to volunteer if asked: the retry buffer lives in the gateway's memory (10,000 packets). A gateway
*process* crash loses what is buffered; a real LoRa gateway would persist it to disk.

## 5. Evidence capture (slides / report)

While the HITEC event is open (live mode: 4-8 min after start; replay mode: any time):

```powershell
.\infra\capture_evidence.ps1                   # all 7 screens -> data\demo\evidence\<timestamp>\*.png
.\infra\capture_evidence.ps1 -Shots "Events,Source insight" -Zone HITEC
```

Uses headless Edge/Chrome already on the PC; shots are full-page PNGs at 1500 px wide.
Good set for a slide: `overview.png` (six zone cards), `events.png` (the open HITEC anomaly with source + action),
`source_insight.png` (features behind the label), `live_map.png`.
For a **screen recording**: `Win + Alt + R` (Xbox Game Bar) on the browser window, or OBS. Record Events ->
Source insight -> Forecast, then the crash test in a second take.

## 6. Questions you will get

- *Is this real data?* No: simulator, physically-modelled, Hyderabad geography and seasons, hidden ground truth. The
  hardware path (LoRa gateway) posts to the same endpoint, nothing downstream changes.
- *How accurate is the source attribution?* Measured against the simulator's hidden truth (48 h, `worker/README.md`):
  overall 50% (62% on clear events); traffic recall 68% / precision 90%; dust and steady-industrial are weak; when
  unsure it says `insufficient evidence` (wrong source only 4% of the time). Confidence is ordinal, not calibrated.
- *Why PM-only AQI?* The MQ sensors give uncalibrated proxy counts, so they only help *attribute* a source, never
  *measure* a pollutant. AQI here is indicative (rolling mean, not the 24-h official average).
- *Is the demo scenario cherry-picked?* The smoke episode and the MGBSEC jam are scripted on purpose
  (`simulator/scenario.py`) so the story is repeatable; everything else is the seeded generator.
