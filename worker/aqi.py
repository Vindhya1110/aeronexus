"""PM-only AQI from CPCB (Indian National AQI) breakpoints.

Never uses MQ gas proxies. The official index averages PM over 24 h and needs >=3 pollutants;
here it is a PM-only *indicative* AQI computed from a short rolling mean of low-cost-sensor PM
(calibration_version 'prototype-*'), and is labelled as such everywhere.
"""
from __future__ import annotations

from typing import Optional

# (conc_lo, conc_hi, aqi_lo, aqi_hi) in ug/m3: CPCB National AQI (2014) bands exactly as published,
# e.g. PM2.5 "31-60" <-> AQI "51-100", so the band's own lower bound is 31 (not the previous band's 30).
# The open-ended top bands ("250+", "430+") need an upper anchor for interpolation: ASSUMED 500 / 600.
PM25_BREAKPOINTS = [(0, 30, 0, 50), (31, 60, 51, 100), (61, 90, 101, 200),
                    (91, 120, 201, 300), (121, 250, 301, 400), (251, 500, 401, 500)]
PM10_BREAKPOINTS = [(0, 50, 0, 50), (51, 100, 51, 100), (101, 250, 101, 200),
                    (251, 350, 201, 300), (351, 430, 301, 400), (431, 600, 401, 500)]
CATEGORIES = [(50, "Good"), (100, "Satisfactory"), (200, "Moderate"),
              (300, "Poor"), (400, "Very Poor"), (500, "Severe")]


def sub_index(conc: Optional[float], table) -> Optional[int]:
    """CPCB: Ip = (IHi - ILo) / (BPHi - BPLo) * (Cp - BPLo) + ILo within the band containing Cp.
    Values in the sliver between two published bands (e.g. 30.4) take the next band's ILo."""
    if conc is None or conc < 0:
        return None
    for lo, hi, ilo, ihi in table:
        if conc <= hi:
            return round(ilo + max(0.0, conc - lo) * (ihi - ilo) / (hi - lo))
    return 500                              # beyond the top anchor: capped at 500


def category(aqi: Optional[int]) -> Optional[str]:
    if aqi is None:
        return None
    return next(name for top, name in CATEGORIES if aqi <= top)


def compute_aqi(pm25: Optional[float], pm10: Optional[float]) -> dict:
    """Overall AQI = max sub-index. Returns {aqi, category, detail}; aqi None if no PM available."""
    s25, s10 = sub_index(pm25, PM25_BREAKPOINTS), sub_index(pm10, PM10_BREAKPOINTS)
    subs = {k: v for k, v in (("pm25", s25), ("pm10", s10)) if v is not None}
    if not subs:
        return {"aqi": None, "category": None, "detail": None}
    dominant = max(subs, key=subs.get)
    aqi = subs[dominant]
    return {"aqi": aqi, "category": category(aqi), "detail": {
        "sub_index": {"pm25": s25, "pm10": s10}, "dominant_pollutant": dominant,
        "inputs_ugm3": {"pm25": pm25, "pm10": pm10},
        "standard": "CPCB National AQI breakpoints, PM2.5 and PM10 only",
        "caveat": "indicative: rolling-mean of prototype-calibrated sensor PM, not the 24-h official average"}}
