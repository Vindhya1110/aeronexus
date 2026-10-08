"""Aeronex dashboard (Streamlit).  Run from the repo root:  python -m dashboard.serve

Client of the backend API (default http://localhost:8000, override with AIRDNA_BACKEND_URL).
Sign-in is required. Roles: "user" (public view) and "admin" (official: + source insight, events, node health, accounts).
"""
from __future__ import annotations

import os
import time

import altair as alt
import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

try:
    from dashboard import theme
except ImportError:          # started as a plain script: its own folder is on sys.path
    import theme

API = os.environ.get("AIRDNA_BACKEND_URL", "http://localhost:8000").rstrip("/")
HOME_URL = os.environ.get("AIRDNA_HOME_URL", "/")          # the website and this dashboard share one origin (backend proxies /app/)

AQI_COLORS = {"Good": "#00b050", "Satisfactory": "#92d050", "Moderate": "#ffc000",
              "Poor": "#ff7c00", "Very Poor": "#ff0000", "Severe": "#800000"}

st.set_page_config(page_title="Aeronex - Hyderabad", page_icon="🌬", layout="wide", initial_sidebar_state="expanded")
st.markdown(theme.CSS, unsafe_allow_html=True)


class AuthExpired(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# the token is an argument on purpose: it is part of the cache key, so one account never sees another's cached answers
@st.cache_data(ttl=10, show_spinner=False)
def _get(token: str, path: str, **params):
    r = requests.get(f"{API}{path}", params=params or None, headers=_headers(token), timeout=10)
    if r.status_code == 401:
        raise AuthExpired()
    r.raise_for_status()
    return r.json()


def sign_out(msg: str | None = None) -> None:
    """Forget the session in this tab. (The real sign-out - clearing the browser cookie - is the /v1/auth/logout link.)"""
    for k in ("token", "user"):
        st.session_state.pop(k, None)
    st.cache_data.clear()
    if msg:
        st.session_state["flash"] = msg
    st.rerun()


def _cookie_token() -> str | None:
    """The login cookie set by the website's sign-in (shared by / and /app/ because they are one origin)."""
    try:
        return st.context.cookies.get("aeronex_token")
    except Exception:  # noqa: BLE001
        return None


def sync_session() -> bool:
    """Make this dashboard session match the browser's login cookie. Returns True when signed in."""
    tok = _cookie_token()
    if not tok:
        for k in ("token", "user"):
            st.session_state.pop(k, None)
        return False
    if st.session_state.get("token") == tok and "user" in st.session_state:
        return True
    try:
        r = requests.get(f"{API}/v1/auth/me", headers=_headers(tok), timeout=10)
    except Exception:  # noqa: BLE001
        r = None
    if r is not None and r.ok:
        st.session_state["token"], st.session_state["user"] = tok, r.json()
        st.cache_data.clear()                    # a different account must never see the previous one's cached answers
        return True
    for k in ("token", "user"):
        st.session_state.pop(k, None)
    return False


def get(path: str, **params):
    try:
        return _get(st.session_state.get("token", ""), path, **params)
    except AuthExpired:
        sign_out("Your session expired - please sign in again.")


def safe(path: str, **params):
    try:
        return get(path, **params)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            st.warning("Your account does not have access to this information.")
        else:
            st.error(f"Backend request failed: {path} ({e})")
        return None
    except Exception as e:  # noqa: BLE001 - show any failure in the UI
        st.error(f"Backend request failed: {path} ({e})")
        return None


def api_call(method: str, path: str, **kw):
    """Account calls (login / sign-up / admin). Returns (ok, json_or_message)."""
    try:
        r = requests.request(method, f"{API}{path}", headers=_headers(st.session_state.get("token", "")), timeout=10, **kw)
    except Exception as e:  # noqa: BLE001
        return False, f"Cannot reach the backend ({e.__class__.__name__})"
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.ok:
        return True, body
    d = body.get("detail")
    if isinstance(d, list):                                  # pydantic validation errors
        d = "; ".join(str(x.get("msg", x)) for x in d)
    return False, d or f"error {r.status_code}"


def color(cat: str | None) -> str:
    return AQI_COLORS.get(cat or "", "#999999")


def badge(text: str, cat: str | None = None) -> str:
    return (f"<span style='background:{color(cat)};color:#fff;padding:2px 8px;"
            f"border-radius:10px;font-size:0.85em'>{text}</span>")


# ---------------------------------------------------------------- sign-in (done once, on the home page)
def signin_required() -> None:
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(theme.navbar(None, HOME_URL), unsafe_allow_html=True)
        expired = st.session_state.pop("flash", None) is not None
        st.markdown(f'<div class="ax-login">{theme.LOGO}<h1 class="ax-grad">Sign in to continue</h1>'
                    f'<p>{"Your session expired. " if expired else ""}Sign in once on the Aeronex home page &mdash; '
                    f'it also unlocks this dashboard, and you can move between the two freely.</p>'
                    f'<p style="margin-top:1.2rem"><a class="ax-btn" href="{HOME_URL}?login=1" target="_self">Sign in</a>'
                    f'<a class="ax-btn ghost" href="{HOME_URL}" target="_self">Back to home</a></p></div>', unsafe_allow_html=True)


if not sync_session():
    signin_required()
    st.stop()

ME = st.session_state["user"]
IS_ADMIN = ME["role"] == "admin"

# ---------------------------------------------------------------- sidebar
st.markdown(theme.navbar(ME, HOME_URL), unsafe_allow_html=True)
st.sidebar.markdown(f'<div class="ax-side-brand">{theme.LOGO}<span class="ax-grad">Aeronex</span></div>', unsafe_allow_html=True)
st.sidebar.caption("Hyderabad - indicative, prototype-calibrated sensors")
st.sidebar.markdown(f"**{ME['display_name']}**  \n{'Official / admin' if IS_ADMIN else 'Public user'}")
st.sidebar.markdown(f'<a class="ax-btn small" href="{HOME_URL}" target="_self">&larr; Home</a>'
                    f'<a class="ax-btn small ghost" href="{HOME_URL}v1/auth/logout?next=/" target="_self">Sign out</a>', unsafe_allow_html=True)
SCREENS = ["Overview", "Live map", "City heatmap", "Zone detail", "Forecast", "Trends"]
if IS_ADMIN:
    SCREENS += ["Source insight", "Risk & hotspots", "Node health", "Events", "Accounts"]
# deep links for the demo / screenshots:  ?screen=Events   ?screen=Zone%20detail&zone=HITEC  (only screens your role may open)
_qs = st.query_params.get("screen", "Overview")
# the screen menu lives in the page itself (a pill bar under the top bar) so it is always visible, whether or not the sidebar is open
page = st.radio("Screen", SCREENS, index=SCREENS.index(_qs) if _qs in SCREENS else 0, horizontal=True,
                label_visibility="collapsed", key="nav_screen")
st.query_params["screen"] = page
if st.sidebar.button("Refresh now"):
    st.cache_data.clear()
_every = int(os.environ.get("AIRDNA_AUTOREFRESH_S", "10"))
auto = st.sidebar.toggle(f"Auto-refresh ({_every} s)", value=_every > 0)

zdata = safe("/v1/zones")
if not zdata:
    st.stop()
zones = zdata["zones"]
zone_ids = [z["zone_id"] for z in zones]
zmap = {z["zone_id"]: z for z in zones}


def pick_zone(key: str) -> str:
    _z = st.query_params.get("zone")
    return st.selectbox("Zone", zone_ids, key=key, index=zone_ids.index(_z) if _z in zone_ids else 0,
                                format_func=lambda i: f"{i} - {zmap[i]['name']}")


# ---------------------------------------------------------------- screens
@st.cache_data(ttl=10, show_spinner=False)
def _warnings(token: str) -> dict:
    try:
        r = requests.get(f"{API}/v1/insights/early-warning", headers=_headers(token), timeout=10)
        return {z["zone_id"]: z for z in r.json().get("zones", [])} if r.ok else {}
    except Exception:  # noqa: BLE001
        return {}


def overview():
    st.header("Overview")
    warn = _warnings(st.session_state.get("token", ""))
    st.caption("AQI is a PM-only indicative CPCB index from a rolling mean; the PM line is the raw windowed mean.")
    cols = st.columns(3)
    for i, z in enumerate(zones):
        aqi, cov, cur = z.get("aqi"), z.get("coverage") or {}, z["current"]
        with cols[i % 3].container(border=True):
            st.subheader(z["name"])
            if aqi:
                st.markdown(f"## {aqi['value']} " + badge(aqi["category"], aqi["category"]),
                            unsafe_allow_html=True)
            else:
                st.markdown("## - ")
                st.caption("AQI not computed yet (run the worker)")
            st.write(f"Raw PM2.5 **{cur['pm25_ugm3']}** / PM10 **{cur['pm10_ugm3']}** ug/m3 "
                     f"({cur['n_samples']} samples, {cur['window_min']} min)")
            sh = z.get("source_hypothesis")
            w = warn.get(z["zone_id"])
            if w and w.get("aqi_now") is not None and w["aqi_now"] > 300:
                st.markdown("<span style='color:#ff4d4f;font-weight:700'>Very Poor now</span> &nbsp;(already above the warning threshold)", unsafe_allow_html=True)
            elif w and w.get("very_poor"):
                vp, pp = w["very_poor"], w.get("poor") or {}
                tone = "#ff4d4f" if vp.get("alert") else ("#ffc000" if pp.get("alert") else "#8FB5A0")
                now_aqi = w.get("aqi_now") or 0
                head = (f"<span style='color:#ffc000;font-weight:700'>Poor now</span> &nbsp;P(Very Poor in 3 h) {vp['p']:.0%}" if now_aqi > 200 else
                        f"<span style='color:{tone};font-weight:700'>Early warning</span> &nbsp;P(Poor in 3 h) {pp.get('p', 0):.0%} &middot; P(Very Poor) {vp['p']:.0%}")
                st.markdown(head
                            + (" &middot; <i>limited history</i>" if w.get("limited_history") else ""), unsafe_allow_html=True)
            if sh and IS_ADMIN:
                st.write(f"Source: *{sh['label']}* (confidence {sh['confidence']})")
            d = z["devices"]
            if IS_ADMIN:
                st.caption(f"Devices online {d['online']}/{d['total']}"
                           + (" - STALE" if z.get("stale") else "")
                           + (" - BLIND SPOT" if cov.get("blind_spot") else ""))
            elif z.get("stale") or cov.get("blind_spot"):
                st.caption("Data for this zone is currently delayed.")


@st.cache_data(ttl=60, show_spinner=False)
def tiles_reachable() -> bool:
    """The Leaflet map needs CDN scripts + OSM tiles. No internet -> use the self-contained map instead."""
    try:
        requests.head("https://tile.openstreetmap.org/0/0/0.png", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def offline_map(devs):
    """Self-contained map (Vega via Streamlit's own bundle): zone corridors, zone radii and device positions."""
    lines, circles, pts = [], [], []
    for z in zones:
        aqi = z.get("aqi")
        cat = aqi["category"] if aqi else "n/a"
        for i, (la, lo) in enumerate(z["path"]):
            lines.append({"zone": z["name"], "order": i, "lat": la, "lon": lo, "AQI category": cat})
        circles.append({"zone": z["name"], "lat": z["centroid"]["lat"], "lon": z["centroid"]["lon"],
                        "AQI category": cat, "AQI": aqi["value"] if aqi else None, "size": (z["radius_km"] * 1000) ** 1.2})
    for d in devs:
        if d.get("last_lat") is not None:
            pts.append({"device": d["device_id"], "kind": d["kind"], "status": d["status"],
                        "lat": d["last_lat"], "lon": d["last_lon"]})
    scale = alt.Scale(domain=list(AQI_COLORS), range=list(AQI_COLORS.values()))
    x = alt.X("lon:Q", scale=alt.Scale(zero=False), title="longitude")
    y = alt.Y("lat:Q", scale=alt.Scale(zero=False), title="latitude")
    col = alt.Color("AQI category:N", scale=scale)
    cdf = pd.DataFrame(circles)
    chart = (alt.Chart(cdf).mark_circle(opacity=0.28, size=3200).encode(
        x=x, y=y, color=col, tooltip=["zone", "AQI", "AQI category"])
        + alt.Chart(cdf).mark_text(dy=-26, fontSize=12, color="#cccccc").encode(x=x, y=y, text="zone:N")
        + alt.Chart(pd.DataFrame(lines)).mark_line(strokeWidth=5).encode(
            x=x, y=y, color=col, order="order:Q", detail="zone:N", tooltip=["zone", "AQI category"])
        + alt.Chart(pd.DataFrame(pts)).mark_point(filled=True, size=90, stroke="white").encode(
            x=x, y=y, shape=alt.Shape("kind:N"), color=alt.value("#1f77b4"),
            tooltip=["device", "kind", "status"]))
    st.altair_chart(chart.properties(height=540).interactive(), use_container_width=True)


def live_map():
    st.header("Live map")
    devs = (safe("/v1/devices") or {}).get("devices", []) if IS_ADMIN else []
    offline = os.environ.get("AIRDNA_OFFLINE_MAP") == "1" or not tiles_reachable()
    if offline:
        st.info("Offline map (no internet or AIRDNA_OFFLINE_MAP=1): corridors, zone radii and devices, no street tiles.")
        offline_map(devs)
        return
    c = zones[0]["centroid"]
    m = folium.Map(location=[c["lat"], c["lon"]], zoom_start=11, tiles="OpenStreetMap")
    for z in zones:
        aqi = z.get("aqi")
        col = color(aqi["category"] if aqi else None)
        folium.PolyLine(z["path"], color=col, weight=6, opacity=0.8).add_to(m)
        folium.Circle([z["centroid"]["lat"], z["centroid"]["lon"]], radius=z["radius_km"] * 1000,
                      color=col, fill=True, fill_opacity=0.15,
                      tooltip=f"{z['name']}: AQI {aqi['value'] if aqi else 'n/a'}").add_to(m)
    for d in devs:
        if d.get("last_lat") is None:
            continue
        online = d["status"] == "online"
        folium.CircleMarker([d["last_lat"], d["last_lon"]], radius=6 if d["kind"] == "mobile" else 8,
                            color="#1f77b4" if online else "#777777", fill=True, fill_opacity=0.9,
                            tooltip=f"{d['device_id']} ({d['kind']}, {d['status']}, "
                                    f"GPS {'ok' if d['gps_valid'] else 'invalid'})").add_to(m)
    st_folium(m, height=560, use_container_width=True, returned_objects=[])
    st.caption("Line/circle colour = zone AQI category. Dots = devices (blue online, grey offline); "
               "bus positions are the last received GPS fix.")


def zone_detail():
    st.header("Zone detail")
    zid = pick_zone("zd")
    hours = st.slider("History (hours)", 1, 48, 6)
    to = zmap[zid].get("last_seen_utc")
    kw = {"fields": "pm25_ugm3,pm10_ugm3", "include_smoothed": "true", "max_points": 200}
    if to:
        kw["to"] = to
        kw["from"] = (pd.Timestamp(to) - pd.Timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = safe(f"/v1/zones/{zid}/history", **kw)
    if not h or not h["points"]:
        st.info("No history in this range.")
        return
    rows = []
    for p in h["points"]:
        v, s, f = p["values"], p.get("smoothed") or {}, p["flags"]
        rows.append({"t": pd.Timestamp(p["t"]), "PM2.5 raw": v.get("pm25_ugm3"),
                     "PM2.5 smoothed": s.get("pm25_ugm3"), "PM10 raw": v.get("pm10_ugm3"),
                     "PM10 smoothed": s.get("pm10_ugm3"), "n": p["n"],
                     "warmup": f["warmup_samples"], "gps_invalid": f["gps_invalid_samples"],
                     "sensor_fault": f["sensor_fault_samples"]})
    df = pd.DataFrame(rows).set_index("t")
    st.subheader("PM2.5 - raw vs smoothed")
    st.line_chart(df[["PM2.5 raw", "PM2.5 smoothed"]])
    st.subheader("PM10 - raw vs smoothed")
    st.line_chart(df[["PM10 raw", "PM10 smoothed"]])
    st.subheader("Quality flags per bucket")
    st.dataframe(df[["n", "warmup", "gps_invalid", "sensor_fault"]], use_container_width=True)
    st.caption("Raw values are never altered; smoothed is the worker's separate rolling mean.")


def source_insight():
    st.header("Source insight")
    for z in zones:
        sh = z.get("source_hypothesis")
        with st.container(border=True):
            st.subheader(z["name"])
            st.caption(f"Context: {z['source_context']}  |  tags: {', '.join(z['source_tags'])}")
            if not sh:
                st.write("No hypothesis yet.")
                continue
            st.write(f"**{sh['label']}**")
            st.progress(min(max(float(sh["confidence"] or 0), 0.0), 1.0),
                        text=(f"confidence {sh['confidence']} that no rule fits (ordinal, not a probability)"
                              if sh["label"] == "insufficient evidence"
                              else f"confidence {sh['confidence']} (ordinal evidence strength, not a probability)"))
            ml = (sh.get("features") or {}).get("ml_attribution")
            if ml:
                rule_short = {"probable traffic/combustion signature": "traffic", "probable dust-dominant signature": "dust",
                              "probable smoke/combustion signature": "smoke", "insufficient evidence": "background"}.get(sh["label"])
                ml_short = {"industrial": "smoke"}.get(ml["label"], ml["label"])
                st.markdown(f"**Learned classifier:** {ml['label']} ({ml['probability']:.0%})")
                if rule_short and ml_short != rule_short and not (rule_short == "background" and ml_short in ("industrial", "background")):
                    st.warning("The rule set and the learned classifier disagree here. The classifier was trained only on the simulator's own episodes, so an "
                               "unusual event can fall outside what it has seen; treat disagreement as a prompt to look closer, not as a verdict.")
                st.bar_chart(pd.Series(ml["probabilities"]).sort_values(ascending=False), height=140)
                st.caption(f"Trained classifier on PM ratio, gas-proxy rises, time and zone context; {ml.get('history_hours', '?')} h of history behind this "
                           "estimate. Labels come from simulated ground truth; it needs field-labelled data on real hardware.")
            with st.expander("Features"):
                st.json(sh["features"])
            st.caption(sh["note"])


def _hlabel(m: int) -> str:
    return f"{m} min" if m < 120 else f"{m // 60} h"


def forecast():
    st.header("Forecast")
    zid = pick_zone("fc")
    f = safe(f"/v1/zones/{zid}/forecast")
    if not f:
        return
    if f.get("status") != "ok" or not f["forecasts"]:
        st.info("Forecast unavailable - run the worker.")
        return
    st.caption(f"Issued {f['issued_at']}")
    w = _warnings(st.session_state.get("token", "")).get(zid)
    if w and w.get("very_poor"):
        c1, c2, c3 = st.columns(3)
        c1.metric("AQI now", w.get("aqi_now") and round(w["aqi_now"]))
        now_aqi = w.get("aqi_now") or 0
        tag = lambda key, thr: ("already above" if now_aqi > thr else ("alert" if w[key]["alert"] else None))
        c2.metric("P(Poor, AQI>200) in 3 h", f"{w['poor']['p']:.0%}", tag("poor", 200), delta_color="inverse")
        c3.metric("P(Very Poor, AQI>300) in 3 h", f"{w['very_poor']['p']:.0%}", tag("very_poor", 300), delta_color="inverse")
        if w.get("limited_history"):
            st.caption(f"Limited history ({w.get('history_hours')} h): the 24 h baselines are missing, so these probabilities are less reliable and raise no events.")
    gbt = [r for r in f["forecasts"] if r["method"] == "gbt"]
    if gbt:
        st.subheader("AQI forecast (learned model, 80% interval)")
        g = pd.DataFrame([{"horizon": _hlabel(r["horizon_min"]), "order": r["horizon_min"], "AQI": r["aqi_pred"], "low (p10)": r["aqi_p10"],
                           "high (p90)": r["aqi_p90"], "held-out MAE": (r["basis"] or {}).get("test_mae_aqi"),
                           "skill vs best baseline": (r["basis"] or {}).get("test_skill_vs_best_baseline")} for r in gbt]).sort_values("order")
        band = alt.Chart(g).mark_area(opacity=0.25, color="#5eead4").encode(x=alt.X("horizon:N", sort=list(g["horizon"]), title=None),
                                                                            y=alt.Y("low (p10):Q", title="AQI"), y2="high (p90):Q")
        line = alt.Chart(g).mark_line(point=True, color="#5eead4").encode(x=alt.X("horizon:N", sort=list(g["horizon"])), y="AQI:Q",
                                                                          tooltip=["horizon", "AQI", "low (p10)", "high (p90)"])
        st.altair_chart(band + line, use_container_width=True)
        st.dataframe(g.drop(columns="order").round(2), use_container_width=True, hide_index=True)
        st.caption("Gradient-boosted trees on recent AQI, lags, temperature/humidity/pressure and calendar. The 6 h / 24 h / 72 h values "
                   "are mostly the daily pattern plus today's level. Trained on simulated history; no wind or traffic feed exists yet.")
    base = [r for r in f["forecasts"] if r["method"] != "gbt"]
    if base:
        with st.expander("Short-horizon baselines (persistence and linear trend, PM)"):
            df = pd.DataFrame([{"horizon_min": r["horizon_min"], "method": r["method"], "PM2.5": r["pm25_pred"], "PM10": r["pm10_pred"],
                                "confidence": r["confidence"], "note": (r.get("basis") or {}).get("reason", "")} for r in base])
            st.dataframe(df, use_container_width=True)
    st.caption(f["disclaimer"] + " A blank value means the method had too little data.")


@st.cache_data(ttl=20, show_spinner=False)
def _heat(token: str, n: int):
    r = requests.get(f"{API}/v1/heatmap", params={"n": n}, headers=_headers(token), timeout=30)
    if r.status_code == 401:
        raise AuthExpired()
    return r.json()


def heatmap():
    st.header("City heatmap")
    st.caption("Estimated AQI between sensors, interpolated from the latest readings of every located node (kriging or inverse-distance, whichever fits better right now).")
    try:
        h = _heat(st.session_state.get("token", ""), 36)
    except AuthExpired:
        sign_out("Your session expired - please sign in again.")
        return
    if h.get("status") != "ok":
        st.info(h.get("reason", "Heatmap unavailable."))
        return
    g = h["grid"]
    lats, lons = g["lats"], g["lons"]
    dlat, dlon = lats[1] - lats[0], lons[1] - lons[0]
    cells = pd.DataFrame([{"lat": la, "lon": lo, "lat2": la + dlat, "lon2": lo + dlon, "AQI": g["aqi"][i][j], "uncertainty": g["std"][i][j]}
                          for i, la in enumerate(lats) for j, lo in enumerate(lons)])
    show_unc = st.toggle("Fade cells by uncertainty", value=True)
    scale = alt.Scale(type="threshold", domain=[50, 100, 200, 300, 400], range=list(AQI_COLORS.values()))
    enc = dict(x=alt.X("lon:Q", scale=alt.Scale(zero=False), title="longitude"), x2="lon2:Q",
               y=alt.Y("lat:Q", scale=alt.Scale(zero=False), title="latitude"), y2="lat2:Q", color=alt.Color("AQI:Q", scale=scale, title="AQI"),
               tooltip=["AQI", alt.Tooltip("uncertainty:Q", title="+/- 1 sigma", format=".0f")])
    if show_unc:
        enc["opacity"] = alt.Opacity("uncertainty:Q", scale=alt.Scale(range=[1.0, 0.25]), legend=None)
    nodes = pd.DataFrame(h["nodes"])
    layer = alt.Chart(nodes).mark_point(filled=True, size=70, color="white", stroke="#06202b").encode(x="lon:Q", y="lat:Q", tooltip=["device", alt.Tooltip("aqi:Q", format=".0f")])
    st.altair_chart((alt.Chart(cells).mark_rect().encode(**enc) + layer).properties(height=520).interactive(), use_container_width=True)
    loo = g.get("leave_one_out_mae") or {}
    st.caption(f"{g['points']} nodes used (white dots). Method now: **{g.get('method_used', 'gp')}** (leave-one-out error: kriging {loo.get('gp', 0):.0f}, "
               f"inverse-distance {loo.get('idw', 0):.0f} AQI points). Data as of {h['as_of']}. {h['caveat'][0].upper() + h['caveat'][1:]}.")


def trends():
    st.header("Trends and seasonality")
    t = safe("/v1/insights/trends")
    if not t or t.get("status") != "ok":
        st.info("Trend analysis has not been computed yet.")
        return
    st.caption(t.get("note", ""))
    rows, series = [], {}
    for z, v in t["zones"].items():
        series[z] = v["months"]
        rows.append({"zone": z, "latest month AQI": round(v["latest_month_mean"], 1), "change vs previous month": None if v.get("mom_change_last") is None else v["mom_change_last"] * 100,
                     "weekly pattern strength": v["weekly_strength"],
                     "long-term trend": ("identifiable" if v["trend_identifiable"] else "needs >= 2 years of data")})
    st.subheader("Monthly mean AQI per zone")
    st.line_chart(pd.DataFrame(series))
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={"change vs previous month": st.column_config.NumberColumn(format="%.1f%%"),
                                "weekly pattern strength": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Seasonal-trend decomposition (STL). With less than two years of history the long-term trend cannot be separated from the "
               "annual cycle, so only month-over-month change and the weekly pattern are reported.")


def risk_hotspots():
    st.header("Risk and hotspots")
    r = safe("/v1/insights/risk")
    if r and r.get("status") == "ok":
        st.subheader("Which zones to prioritise first")
        df = pd.DataFrame(r["ranking"])
        if df["exposure_index"].isna().any():
            st.warning(f"Exposure data (OpenStreetMap) is missing for {int(df['exposure_index'].isna().sum())} zone(s); those are ranked on air quality alone with a neutral exposure assumption.")
        st.dataframe(df[["rank", "name", "risk", "aqi", "outlook_p_very_poor", "exposure_index", "buildings"]], use_container_width=True, hide_index=True,
                     column_config={"risk": st.column_config.ProgressColumn("risk", min_value=0, max_value=1.2, format="%.2f"),
                                    "outlook_p_very_poor": st.column_config.NumberColumn("P(Very Poor, 3 h)", format="%.0f%%")})
        with st.expander("How the ranking is computed"):
            st.write(r["method"])
            st.caption(r["caveat"])
            st.dataframe(pd.DataFrame([{"zone": x["name"], **(x.get("sensitive_sites") or {})} for x in r["ranking"]]), hide_index=True)
    h = safe("/v1/insights/hotspots")
    if h and h.get("status") == "ok":
        st.subheader("Persistent hotspots")
        st.caption(h.get("note", ""))
        cl = pd.DataFrame(h["clusters"])
        if not cl.empty:
            st.altair_chart(alt.Chart(cl).mark_circle(opacity=0.7, color="#ff7c00").encode(
                x=alt.X("lon:Q", scale=alt.Scale(zero=False)), y=alt.Y("lat:Q", scale=alt.Scale(zero=False)), size=alt.Size("score:Q", legend=None),
                tooltip=["rank", "mean_aqi", "persistence", "days_observed"]).properties(height=340).interactive(), use_container_width=True)
            st.dataframe(cl[["rank", "zone", "mean_aqi", "vs_city", "persistence", "days_observed", "samples"]].round(2), use_container_width=True, hide_index=True)
    elif h:
        st.info("Hotspot analysis needs several days of history.")


def node_health():
    st.header("Node health")
    devs = (safe("/v1/devices") or {}).get("devices", [])
    df = pd.DataFrame([{
        "device": d["device_id"], "kind": d["kind"], "zone": d["zone_id"], "status": d["status"],
        "age_s": d["last_age_s"], "last_seq": d["last_sequence"], "gps_valid": d["gps_valid"],
        "warmup": d["warmup"], "faults": ", ".join(d["sensor_faults"]) or "-",
        "firmware": d["firmware_version"], "calibration": d["calibration_version"]} for d in devs])
    online = int((df["status"] == "online").sum()) if len(df) else 0
    c1, c2 = st.columns(2)
    c1.metric("Online", f"{online}/{len(df)}")
    c2.metric("Offline", len(df) - online)
    st.dataframe(df, use_container_width=True, hide_index=True)
    cal = safe("/v1/insights/calibration")
    if cal and cal.get("status") == "ok":
        st.subheader("Sensor drift correction (mobile nodes)")
        st.caption(cal.get("note", ""))
        st.dataframe(pd.DataFrame(cal["buses"]), use_container_width=True, hide_index=True)
    st.subheader("Zone coverage")
    st.dataframe(pd.DataFrame([{
        "zone": z["zone_id"], "devices online": f"{z['devices']['online']}/{z['devices']['total']}",
        "last sample age (s)": (z.get("coverage") or {}).get("last_sample_age_s"),
        "blind spot": (z.get("coverage") or {}).get("blind_spot")} for z in zones]),
        use_container_width=True, hide_index=True)


def events():
    st.header("Events")
    c1, c2 = st.columns(2)
    zf = c1.selectbox("Zone filter", ["all"] + zone_ids)
    sf = c2.selectbox("Status", ["all", "detected", "confirmed", "recommended", "rechecked", "closed"])
    kw = {"limit": 200}
    if zf != "all":
        kw["zone_id"] = zf
    if sf != "all":
        kw["status"] = sf
    ev = (safe("/v1/events", **kw) or {}).get("events", [])
    if not ev:
        st.info("No events.")
        return
    for e in ev:
        d = e["details"]
        sh = d.get("source_hypothesis_at_detection") or {}
        with st.expander(f"#{e['id']} {e['zone_id']} - {d.get('metric', e['event_type'])} [{e['status']}] "
                         f"{e['detected_at']}", expanded=e["status"] != "closed"):
            st.write(d.get("triggered_by", ""))
            if sh:
                st.write(f"Source hypothesis at detection: **{sh.get('label')}** (confidence {sh.get('confidence')})")
            if d.get("recommended_action"):
                basis = d.get("recommended_basis")
                st.info(f"Recommended action: {d['recommended_action']}" + (f" ({basis})" if basis else ""))
            steps = " -> ".join(f"{t['status']} {t['at'][11:16]}Z" for t in d.get("transitions", []))
            st.caption(f"Severity {e['severity']}  |  lifecycle: {steps}  |  updated {e['updated_at']}")
            with st.expander("Raw event details"):
                st.json(d)


def accounts():
    st.header("Accounts")
    ok, res = api_call("GET", "/v1/auth/users")
    if not ok:
        st.error(res)
        return
    st.dataframe(pd.DataFrame(res["users"])[["username", "display_name", "role", "active", "created_at", "last_login_at"]],
                 use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.subheader("Create account")
        with st.form("newacct", clear_on_submit=True):
            u = st.text_input("Username")
            n = st.text_input("Display name")
            role = st.selectbox("Role", ["admin", "user"], format_func=lambda r: "Official / admin" if r == "admin" else "Public user")
            p = st.text_input("Initial password (min 8 chars)", type="password")
            if st.form_submit_button("Create"):
                ok, r = api_call("POST", "/v1/auth/users", json={"username": u.strip(), "password": p, "display_name": n, "role": role})
                st.success(f"Created {r['username']}") if ok else st.error(r)
    with c2.container(border=True):
        st.subheader("Manage account")
        names = [x["username"] for x in res["users"]]
        who = st.selectbox("Account", names)
        action = st.radio("Action", ["Deactivate", "Reactivate", "Reset password"], horizontal=True)
        newpw = st.text_input("New password", type="password") if action == "Reset password" else None
        if st.button("Apply"):
            body = {"active": False} if action == "Deactivate" else {"active": True} if action == "Reactivate" else {"password": newpw}
            ok, r = api_call("PATCH", f"/v1/auth/users/{who}", json=body)
            st.success(f"Updated {who}") if ok else st.error(r)


{"Overview": overview, "Live map": live_map, "Zone detail": zone_detail,
 "Source insight": source_insight, "Forecast": forecast, "Node health": node_health,
 "Events": events, "Accounts": accounts, "City heatmap": heatmap, "Trends": trends,
 "Risk & hotspots": risk_hotspots}[page]()

if auto and _every > 0:
    time.sleep(_every)
    st.cache_data.clear()
    st.rerun()
