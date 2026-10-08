"""Feature 8: health-risk priority ranking of zones.

Not a trained model: a transparent score that combines how bad the air is (and is about to be) with how many vulnerable people
live there. Exposure comes from OpenStreetMap (schools, kindergartens, colleges, hospitals, clinics, nursing homes within
1.5 km of each zone centre, plus building count as a residential-density proxy), fetched once and cached in
ml/artifacts/exposure.json. Real population/census data would replace the building proxy; if the OSM cache is missing the
ranking falls back to air quality alone and says so.

    risk = (0.7 * hazard + 0.3 * outlook) * (0.5 + 0.5 * exposure_index)
    hazard   = AQI now / 300 (capped at 2)           outlook = P(AQI crosses 'Very Poor' within 3 h) from the early-warning model
    exposure_index = 0.6 * sensitive-site score + 0.4 * building-density score (each scaled 0..1 across zones, sqrt-compressed)

    python -m ml.risk fetch        # one-off OSM download (needs internet)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ART = Path(__file__).resolve().parent / "artifacts"
EXPOSURE = ART / "exposure.json"
RADIUS_M = 1500
SENSITIVE = {"school": 1.0, "kindergarten": 1.5, "college": 0.7, "hospital": 2.0, "clinic": 1.0, "nursing_home": 2.0}


MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


def _overpass_count(query_body: str, retries: int = 8) -> int:
    """`out count` returns just a number (a few hundred bytes) instead of every feature, which is what kept timing out."""
    import time

    import requests
    q = f"[out:json][timeout:90];({query_body});out count;"
    for attempt in range(retries):
        try:
            r = requests.post(MIRRORS[attempt % len(MIRRORS)], data={"data": q}, timeout=100)
            if r.status_code == 200:
                return int(r.json()["elements"][0]["tags"]["total"])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4 * (attempt + 1))
    raise RuntimeError("Overpass unavailable")


def fetch_exposure() -> dict:
    """Resumable: zones already cached are skipped; each zone is saved as soon as it is counted."""
    from shared.zones import ZONES
    out = load_exposure() or {}
    ART.mkdir(exist_ok=True)
    for z in ZONES:
        if z.zone_id in out:
            continue
        lat, lon = z.centroid
        around = f"(around:{RADIUS_M},{lat},{lon})"
        try:
            sites = {k: _overpass_count(f'nwr["amenity"="{k}"]{around};') for k in SENSITIVE}
            buildings = _overpass_count(f'way["building"]{around};')
        except Exception as e:  # noqa: BLE001
            print(f"{z.zone_id}: {e} - will retry on the next run", flush=True)
            continue
        out[z.zone_id] = {"sites": sites, "buildings": buildings, "radius_m": RADIUS_M, "source": "OpenStreetMap (Overpass counts)"}
        EXPOSURE.write_text(json.dumps(out, indent=1))
        print(z.zone_id, out[z.zone_id], flush=True)
    return out


def load_exposure() -> dict | None:
    return json.loads(EXPOSURE.read_text()) if EXPOSURE.exists() else None


def _scaled(vals: dict[str, float]) -> dict[str, float]:
    v = {k: np.sqrt(max(0.0, x)) for k, x in vals.items() if x is not None}
    top = max(v.values()) if v else 0.0
    return {k: (x / top if top > 0 else 0.0) for k, x in v.items()}


def rank(zones: list[dict], exposure: dict | None = None) -> list[dict]:
    """zones: [{zone_id, name, aqi, p_very_poor (optional)}]. Returns ranked rows with a transparent breakdown."""
    exposure = load_exposure() if exposure is None else exposure
    weighted = {z: sum(SENSITIVE[k] * n for k, n in e["sites"].items()) for z, e in (exposure or {}).items()}
    sens = _scaled(weighted) if exposure else {}
    dens = _scaled({z: e.get("buildings") for z, e in (exposure or {}).items()}) if exposure else {}
    rows = []
    for z in zones:
        aqi = z.get("aqi")
        if aqi is None:
            continue
        hazard = min(2.0, aqi / 300.0)
        outlook = float(z.get("p_very_poor") or 0.0)
        have = exposure is not None and z["zone_id"] in (exposure or {})
        e_idx = 0.6 * sens.get(z["zone_id"], 0.0) + 0.4 * dens.get(z["zone_id"], 0.0) if have else None
        score = (0.7 * hazard + 0.3 * outlook) * (0.5 + 0.5 * (e_idx if e_idx is not None else 0.5))
        rows.append({"zone_id": z["zone_id"], "name": z.get("name"), "risk": round(float(score), 3), "aqi": aqi, "hazard": round(hazard, 3),
                     "outlook_p_very_poor": round(outlook, 3), "exposure_index": None if e_idx is None else round(float(e_idx), 3),
                     "sensitive_sites": exposure[z["zone_id"]]["sites"] if have else None,
                     "buildings": exposure[z["zone_id"]].get("buildings") if have else None,
                     "note": None if have else "no exposure data cached: ranked on air quality alone (neutral exposure assumed)"})
    rows.sort(key=lambda r: -r["risk"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        fetch_exposure()
