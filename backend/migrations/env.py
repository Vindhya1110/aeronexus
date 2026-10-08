"""Alembic environment. Run from the repo root:  alembic -c backend/alembic.ini upgrade head"""
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.config import Settings          # noqa: E402
from backend.models import Base               # noqa: E402

url = os.getenv("AIRDNA_DATABASE_URL", Settings().database_url)


def run_migrations_online():
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


(run_migrations_offline if context.is_offline_mode() else run_migrations_online)()
