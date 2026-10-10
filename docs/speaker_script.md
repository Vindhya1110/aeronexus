# Aeronex - speaker script, architecture walkthrough and judge Q&A

Two slides: **"How Aeronex works"** (the pipeline) and **"Tech stack"**. Speaking time: about 4 minutes for both slides, then questions.
Everything numeric below comes from the measured results in `docs/ml_results.md`. Say "simulated" whenever you quote a number.

---

## Part A: Speaker script

### Slide 1 - How Aeronex works (about 2 minutes)

**Opening (15 s)**
> "Air quality is measured at one station for a whole city. Aeronex measures it street by street. We run low-cost sensors on six Hyderabad pollution corridors, and turn their readings into a zone-level picture, a forecast and an explanation of what is causing the pollution."

**Walk the flow, left to right (60 s)**
1. **Sensor nodes.** "Seven fixed nodes and four on city buses. Each sends PM2.5, PM10, gas proxies, temperature, humidity, pressure and GPS, once a minute or every 30 seconds."
2. **Gateway.** "This is the store-and-forward part. If the backend is down, the gateway keeps the packets and resends them in order. In our outage test, nothing was lost or duplicated."
3. **Ingest API.** "Every packet is checked against a strict data contract. A bad packet is rejected with the exact field that is wrong. A duplicate is detected by device and sequence number, so a resend is harmless."
4. **Database.** "Raw readings are stored unchanged and are never overwritten. Everything we calculate goes into separate tables, so you can always see the original numbers."
5. **Analytics and ML.** "This is where the insight is. I will come back to it."
6. **Website and dashboard.** "One site, one login. The public sees air quality, the map, forecasts and trends. Officials also see the source of pollution, events, risk and node health."

**The analytics band (30 s)**
> "Analytics does five things: it computes the official-style AQI from PM2.5 and PM10 using the Indian CPCB bands; it suggests the likely source; it flags spikes as events that move from detected to confirmed to a recommended action; it forecasts ahead; and it builds the heatmap, hotspots and trends."

**Close the slide (15 s)**
> "The sensors are simulated with a gateway that speaks the real data format, so swapping in hardware changes nothing downstream. That is deliberate, and I will say so clearly every time we quote a number."

### Slide 2 - Tech stack (about 2 minutes)

> "Everything is Python, from the sensor simulator to the dashboard. The data contract is Pydantic. The API is FastAPI on Uvicorn. Storage is SQLite behind SQLAlchemy, with Alembic migrations, so moving to Postgres is a configuration change. The analytics use pandas and NumPy, and the machine learning uses scikit-learn and statsmodels. The dashboard is Streamlit with Altair and Folium maps. The public website is plain HTML, CSS and JavaScript with no external libraries, so it works without internet. Security uses hashed passwords and a signed, HttpOnly session cookie, with two roles."

Then point at the screenshots: "Zone detail shows raw and smoothed readings with quality flags. Trends decomposes a year of data into a seasonal and a trend part."

---

## Part B: The predictions and insights, and why they are unique

For each one: what it is, what we measured, and the honest limit. Judges respect the limits more than the numbers.

**1. City-wide AQI between sensors (heatmap).**
- *What:* a grid of estimated AQI across the city, with uncertainty that grows away from sensors. Gaussian-process regression (kriging) or inverse-distance weighting, whichever has the lower error on the current snapshot.
- *Measured:* about 14% better than the city average on held-out nodes.
- *Unique:* most city dashboards show one station. We show the gaps between sensors and say how sure we are there.
- *Limit:* on our simulated field kriging does not clearly beat inverse-distance weighting. The map is an estimate.

**2. Multi-horizon forecast (15 min to 72 h).**
- *What:* gradient-boosted trees predict the AQI at six horizons, with an 80% range. They correct the simple baseline (persistence or same-time-of-day average), which is chosen on validation data.
- *Measured:* beats the best simple baseline by about 36% at 15 minutes, about 23% at an hour, about 10% at 24 hours, and about 5% at 72 hours.
- *Unique:* the forecast tells you when it is uncertain (the band) and which method produced it, and it is trained on a blocked time split so it cannot cheat by seeing the future.
- *Limit:* 72-hour skill is modest and mostly comes from the daily pattern. No wind or traffic feed exists yet.

**3. Early warning (3 hours ahead).**
- *What:* the probability that a zone crosses "Poor" (above 200) or "Very Poor" (above 300) in the next 3 hours.
- *Measured:* ROC-AUC about 0.96 for both thresholds. For warnings given before the crossing, F1 is about 0.64 (Poor) and 0.58 (Very Poor), against about 0.0 for the persistence rule and about 0.4 for a simple trend rule.
- *Unique:* the persistence rule is useless for a warning, because a warning that just repeats today's level comes too late. Ours fires before the crossing.
- *Limit:* not calibrated on real hardware, and it raises dashboard events, not push alerts.

**4. Pollution source attribution.**
- *What:* traffic, dust, smoke, industrial or background, with a confidence score. It says "insufficient evidence" when it is not sure.
- *Measured:* 98.9% accuracy on held-out simulated data, against 42.9% for the hand-written rules.
- *Unique:* it combines the PM2.5 to PM10 ratio with the rise of gas proxies over each device's own quiet baseline, and it stays transparent: the rule set runs alongside the learned model, and the dashboard flags any disagreement.
- *Limit, say this first if asked:* the labels come from the simulator, and about 98% accuracy survives with only PM data. So the classifier largely learns the simulator's patterns. It also disagrees with the rules on the demo's scripted smoke episode. Real use needs labelled field episodes.

**5. Hotspots and spike detection.**
- *What:* DBSCAN finds persistent problem areas from 12 months of located readings. An Isolation Forest flags unusual combinations, alongside the older statistical detector.
- *Measured:* the two most polluted zones are found exactly. The Isolation Forest catches 85% of smoke episodes, against 72% for the statistical detector, with fewer false alerts.
- *Unique:* a hotspot is a place that is bad *repeatedly* at the same hour, not a single bad reading.

**6. Sensor drift correction.**
- *What:* each mobile bus is compared with the fixed nodes whenever they pass within 400 m, and a robust fit corrects its gain every day from the previous 30 days.
- *Measured:* on the two buses where we injected drift, error fell from 65% to 10% and from 28% to 10%. The healthy buses were left alone.
- *Unique:* low-cost sensors drift. Most projects assume they do not. We keep the network consistent without a reference station.
- *Limit, important:* the drift was injected into the simulator, because simulated sensors do not drift. The fixed nodes are low-cost too. This gives consistency, not absolute accuracy.

**7. Risk ranking.**
- *What:* zones ranked by air quality now plus the early-warning outlook, weighted by schools, hospitals, clinics and building density within 1.5 km (OpenStreetMap).
- *Unique:* it tells officials which zone to fix first, not only which is worst.
- *Limit:* not a trained model. Building counts stand in for population; there is no census data. Exposure data currently covers three of the six zones.

**8. Trends and year-over-year impact.**
- *What:* decomposition of daily AQI into a seasonal, weekly and trend part, plus a year-over-year before-and-after comparison with a confidence interval.
- *Measured:* on an injected 27% decline, the year-over-year method recovered −23%, with a 95% range of −30% to −17%.
- *Unique:* it is the tool for "did the intervention work?", the question a judge will ask.
- *Limit:* with under two years of data, the long-term trend cannot be separated from the seasonal cycle, so the dashboard says so.

**Not built, if asked:** scenario simulation ("what if we re-time this junction?") and a cleanest-route planner. Say they are next, not that they exist.

---

## Part C: Architecture walkthrough (for your own understanding)

**The path of one reading**
1. A sensor node creates a payload with the device ID, a sequence number, a timestamp, GPS, PM, gas proxies, environment and quality flags.
2. The gateway posts it to `POST /v1/telemetry` with an API key. A 202 means accepted, a 409 means "already have it" (treated as delivered), a 422 means malformed (not retried), and a connection error means buffer and retry, in order.
3. The backend validates it with the Pydantic contract, resolves the zone (GPS inside a zone's radius; a fixed node uses its home zone; a bus uses its last known zone), writes the raw row, then writes a typed row, and pushes a summary over the WebSocket.
4. The **worker** runs every 15 seconds (live) or 60 seconds (default). For each zone it reads the last few hours, computes the 30-minute smoothed PM, the AQI, the anomaly check, the source attribution, the events and the rule-based forecast, and then the ML outputs (learned forecast, early warning, source classifier, anomaly score). Everything it writes is a derived row; raw data is never touched.
5. The **backend** serves those results through the read API. The **website** (`/`) calls only the public summary. The **dashboard** (`/app/`) is a separate Streamlit process, reached through the backend's proxy, so there is one origin and one login cookie.

**The login**
- Passwords are scrypt hashes with salts. Logging in returns a signed token (HMAC-SHA256, 8 hours) that is stored in an HttpOnly, SameSite cookie. The website and the dashboard share that cookie because they share an origin.
- The read API is open to anyone in local development. In the demo it is enforced: zones need any account, devices and events need the official role.
- Five wrong passwords lock a username for five minutes; wrong username and wrong password return the same message.

**The demo**
- `tools/demo_data.py` generates a scripted morning: traffic at MGBSEC, a smoke episode at HITEC. The database is built once and snapshotted; replay mode restores it in seconds.
- `tools/run_demo.py` starts the backend, the worker, the simulator and the dashboard, waits until each one answers, and prints the URL.

**Where things live**
- `shared/` the data contract and zone definitions.
- `simulator/` the fake gateway and scenario.
- `backend/` the API, auth, database models, insights.
- `worker/` the analytics and `ml_runtime.py`.
- `ml/` the eight models, their training and their evaluation.
- `dashboard/` the Streamlit screens, `site/` the public website.
- `docs/ml_results.md` every number quoted here.

---

## Part D: Questions the judges may ask, with answers

**Is this real data?**
No. The sensors are simulated by a gateway that uses the real data format, with hidden ground truth for evaluation. We say so on the slides. The hardware path posts to the same endpoint, so nothing downstream changes.

**How accurate is it?**
Each number is on held-out simulated data. Quote the headline result for the feature being discussed, then give the limit. Do not say "accurate" without a comparison.

**Why is source attribution 98.9% when the rules get 43%?**
Because the labels come from the simulator, and the simulator is very regular. A PM-only model also gets about 98%. So the high number shows the method works on our data, not that it will reach 98% in Hyderabad. Real use needs labelled field episodes.

**Why did the classifier disagree with the rules on the demo smoke event?**
That event is a scripted overlay that the generator never produced, so the classifier had not seen that pattern. The dashboard shows the disagreement on purpose, rather than hiding it.

**Why not just use the official CPCB 24-hour AQI?**
Ours is an indicative AQI from a short rolling mean of PM only, using the same breakpoints. The official index averages 24 hours and needs at least three pollutants. We do not claim it is the official figure, and the dashboard says so.

**Why are gas sensors called proxies, not concentrations?**
MQ-series sensors give uncalibrated, temperature- and humidity-sensitive readings. They are useful for telling sources apart, not for measuring concentration. We never convert them to ppm.

**Does kriging beat a simple method?**
Not clearly, on our simulated field. We report that, and the service picks whichever method has the lower error on the current snapshot. The uncertainty band grows away from sensors, so the map shows where it is guessing.

**How do you know the forecast is not just copying the present?**
We compare it with persistence (the present), with a linear trend and with a time-of-day average. It beats the best of them at every horizon up to 24 hours, and at 72 hours by a small margin. The split is blocked in time, so the model never trains on the future.

**Why is early warning better than the simple rule?**
A persistence rule only fires once the air is already bad. Our model gives a probability before the crossing, which is when a warning is useful. We measure only the moments before the crossing, not the moments after it.

**What happens when a sensor fails or the network drops?**
The gateway buffers and resends in order. The database rejects duplicates by device and sequence, so resends are safe. In our crash test (15 to 25 second outages) nothing was lost or duplicated. The worker keeps running on whatever arrives, and a zone with no fresh data is flagged as stale.

**How do you handle a sensor that drifts?**
We compare each mobile sensor with nearby fixed nodes and correct its gain daily from the previous 30 days. We tested it by injecting drift, because the simulator's sensors do not drift. Be clear that this gives network consistency, not absolute accuracy.

**Is the login secure?**
Passwords are salted scrypt hashes. The session is a signed, short-lived token in an HttpOnly cookie. Brute-force attempts are limited per username. It is a prototype: lockout counters reset on restart and there is no password-strength policy beyond length. A production system would add those and HTTPS.

**Can it scale?**
The database is SQLite today and the code uses portable types, so Postgres is a configuration change. The worker processes zones one by one, and ML inference is cached per zone. We have not load-tested beyond the simulated fleet.

**Why Streamlit for the dashboard?**
It lets us build the analytics screens quickly in Python, next to the models. The public site is plain HTML, so it is not limited by Streamlit's layout.

**Why two roles?**
Citizens need the air quality and forecasts. Officials need the source, the events, the risk ranking and the node health to act. The same data serves both, filtered by role.

**What would you do with real data?**
Calibrate the sensors against a reference station, label a set of real pollution episodes to retrain the source classifier, add wind and traffic feeds to the forecast, and replace the building-count proxy with census population. Those are the honest next steps.

**What is not built yet?**
Scenario simulation ("what if we change this junction?") and a route planner that avoids polluted corridors. We have not built them, and they are the next features.

**Why is the heatmap only 36 by 36?**
It is a display resolution, and the interpolation runs in milliseconds at that size. A finer grid is a parameter change, but it would show more detail than eleven sensors can support.

**What does "uncertainty" mean on the heatmap?**
It is the expected error at each point. Near a sensor it is small, and far from every sensor it grows, which is the honest answer when nothing measures that spot.

**Why not deep learning?**
Our data is small and simulated, and gradient-boosted trees match or beat simpler methods on this kind of tabular data with fewer assumptions. They also train in minutes on a laptop, and they explain their predictions more easily.

**How long does a run take?**
The live stack starts in about 15 seconds once the demo database exists. The first build takes a few minutes. Retraining all the models takes about 40 minutes.

---

## Part E: Quick reference numbers

| Result | Value | Caveat |
|---|---|---|
| Interpolation vs city mean | about 14% better | simulated field, no clear win over IDW |
| Forecast skill vs best baseline | +36% (15 min), +23% (1 h), +10% (24 h), +5% (72 h) | simulated |
| Early-warning ROC-AUC | 0.96 (Poor), 0.96 (Very Poor) | simulated, not calibrated |
| Early-warning F1 before crossing | 0.64 / 0.58 | persistence rule 0.00 |
| Source classifier accuracy | 98.9% (rules 42.9%) | simulator labels; PM-only still 98% |
| Isolation Forest smoke recall | 85% (statistical 72%) | simulated episodes |
| Drift correction | 65% to 10%, 28% to 10% | drift injected on purpose |
| Year-over-year impact recovered | −23% (true −27%) | injected change |
| Crash test | 0 lost, 0 duplicated | 15 to 25 s outages |
| Automated tests | 156 | |
