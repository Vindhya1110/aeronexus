"""Event lifecycle: detected -> confirmed -> recommended -> rechecked -> closed.

All durations are measured in DATA time (the zone's sample timestamps), so the same logic works
live and when backfilling replayed data, and re-running a pass on unchanged data is a no-op.
One step per pass, so every status is observable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Event

from .config import WorkerSettings

OPEN = ("detected", "confirmed", "recommended", "rechecked")
SEV_ORDER = {"warning": 0, "high": 1, "critical": 2}
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

ACTIONS = {
    "probable traffic/combustion signature":
        "Check congestion/idling near the node; consider a local traffic-management advisory and re-measure.",
    "probable dust-dominant signature":
        "Inspect nearby construction/road works for dust control (water sprinkling, covering); re-measure.",
    "probable smoke/combustion signature":
        "Check for open burning or industrial stack/boiler emissions nearby; re-measure.",
}
DEFAULT_ACTION = "Source unclear from sensor data: dispatch a field inspection and collect more data."


def _iso(t: datetime) -> str:
    return t.strftime(TS_FMT)


def _log(det: dict, status: str, at: datetime) -> None:
    det.setdefault("transitions", []).append({"status": status, "at": _iso(at)})


def update_events(db: Session, zone_id: str, evals: dict[str, dict], as_of: datetime,
                  source: Optional[dict], cfg: WorkerSettings) -> list[Event]:
    """Apply this pass's evaluations to the zone's anomaly events. Returns events touched."""
    touched: list[Event] = []
    open_events = list(db.scalars(select(Event).where(
        Event.zone_id == zone_id, Event.event_type == "anomaly", Event.status.in_(OPEN))))
    for ev in evals.values():
        cur = next((e for e in open_events if (e.details or {}).get("metric") == ev["metric"]), None)
        if ev["breach"]:
            if cur is None:
                det = {"metric": ev["metric"], "value": ev["value"], "baseline": ev["baseline"],
                       "baseline_method": ev["baseline_method"], "ratio": ev["ratio"], "z": ev["z"],
                       "peak_value": ev["value"],
                       "triggered_by": f"{ev['metric']} {ev['value']} vs baseline {ev['baseline']} "
                                       f"({ev['baseline_method']})",
                       "source_hypothesis_at_detection": source and {
                           "label": source["label"], "confidence": source["confidence"]},
                       "note": "anomaly = deviation from this zone's own baseline, not a standard exceedance"}
                _log(det, "detected", as_of)
                cur = Event(zone_id=zone_id, event_type="anomaly", status="detected",
                            severity=ev["severity"], detected_at=as_of, updated_at=as_of, details=det)
                db.add(cur)
            else:
                det = dict(cur.details)
                det.pop("clear_since", None)
                det.update(value=ev["value"], ratio=ev["ratio"], z=ev["z"],
                           peak_value=max(det.get("peak_value", 0), ev["value"]))
                if SEV_ORDER[ev["severity"]] > SEV_ORDER.get(cur.severity, 0):
                    cur.severity = ev["severity"]
                if cur.status == "rechecked":                        # breached again after a recheck
                    cur.status = "recommended"
                    det["recurrences"] = det.get("recurrences", 0) + 1
                    _log(det, "recommended", as_of)
                elif cur.status == "detected" and as_of - cur.detected_at >= timedelta(minutes=cfg.confirm_after_min):
                    cur.status = "confirmed"
                    det["confirmed_at"] = _iso(as_of)
                    _log(det, "confirmed", as_of)
                elif cur.status == "confirmed":
                    cur.status = "recommended"
                    label = (source or {}).get("label")
                    det["recommended_action"] = ACTIONS.get(label, DEFAULT_ACTION)
                    det["recommended_basis"] = f"source hypothesis: {label} (heuristic, not a lab measurement)"
                    _log(det, "recommended", as_of)
                cur.details, cur.updated_at = det, as_of
            touched.append(cur)
        elif cur is not None:
            det = dict(cur.details)
            if ev["clear"]:
                since = (datetime.strptime(det["clear_since"], TS_FMT).replace(tzinfo=timezone.utc)
                         if det.get("clear_since") else as_of)
                det["clear_since"] = _iso(since)
                waited = as_of - since
                if waited >= timedelta(minutes=cfg.clear_after_min):
                    if cur.status == "detected":
                        cur.status, det["outcome"] = "closed", "transient: cleared before confirmation"
                        _log(det, "closed", as_of)
                    elif cur.status in ("confirmed", "recommended"):
                        cur.status = "rechecked"
                        det["recheck"] = {"at": _iso(as_of), "value": ev["value"],
                                          "baseline": ev["baseline"], "outcome": "returned to baseline"}
                        _log(det, "rechecked", as_of)
                    elif cur.status == "rechecked" and waited >= 2 * timedelta(minutes=cfg.clear_after_min):
                        cur.status, det["outcome"] = "closed", "recovered; stayed at baseline after recheck"
                        _log(det, "closed", as_of)
            else:
                det.pop("clear_since", None)       # still elevated, just under the breach threshold
            det["last_value"] = ev["value"]
            cur.details, cur.updated_at = det, as_of
            touched.append(cur)
    return touched
