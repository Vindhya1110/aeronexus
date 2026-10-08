"""Engine/session setup and zone seeding. Swap Postgres in by changing AIRDNA_DATABASE_URL."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from shared.zones import ZONES

from .models import Base, Zone


def make_engine(url: str) -> Engine:
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        if u.database and u.database != ":memory:":
            Path(u.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):          # SQLite-only tuning; other dialects skip this
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
        return engine
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_zones(db: Session) -> None:
    """Upsert the zones table from shared/zones.py (the same definitions the simulator fleet uses)."""
    for z in ZONES:
        row = db.get(Zone, z.zone_id) or Zone(zone_id=z.zone_id)
        lat, lon = z.centroid
        row.name, row.centroid_lat, row.centroid_lon = z.name, lat, lon
        row.radius_km, row.path = z.radius_km, [list(p) for p in z.path]
        row.source_context, row.source_tags = z.source_context, list(z.source_tags)
        db.add(row)
    db.commit()


def init_db(engine: Engine, factory: sessionmaker[Session], create_tables: bool) -> None:
    if create_tables:
        Base.metadata.create_all(engine)
    with factory() as db:
        seed_zones(db)
