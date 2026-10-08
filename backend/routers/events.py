"""GET /v1/events - hotspot/anomaly events and lifecycle status (written by the Phase-3 worker)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import read_access
from ..deps import get_db
from ..models import Event
from .devices import iso

router = APIRouter(prefix="/v1", tags=["events"], dependencies=[Depends(read_access("admin"))])


@router.get("/events")
def list_events(zone_id: Optional[str] = None, status: Optional[str] = None,
                event_type: Optional[str] = None, since: Optional[datetime] = None,
                limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    q = select(Event).order_by(Event.detected_at.desc()).limit(limit)
    if zone_id:
        q = q.where(Event.zone_id == zone_id)
    if status:
        q = q.where(Event.status == status)
    if event_type:
        q = q.where(Event.event_type == event_type)
    if since:
        from datetime import timezone
        q = q.where(Event.detected_at >= (since if since.tzinfo else since.replace(tzinfo=timezone.utc)))
    events = [{
        "id": e.id, "zone_id": e.zone_id, "device_id": e.device_id, "event_type": e.event_type,
        "status": e.status, "severity": e.severity, "detected_at": iso(e.detected_at),
        "updated_at": iso(e.updated_at), "details": e.details,
    } for e in db.scalars(q)]
    return {"count": len(events), "events": events}
