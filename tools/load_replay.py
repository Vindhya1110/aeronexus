"""Load a simulator replay file (JSONL) straight into the backend DB through the SAME ingestion code
path as POST /v1/telemetry (validation, zone resolution, duplicate rejection) - no HTTP needed.

    python tools/load_replay.py data/replay.jsonl            # uses AIRDNA_DATABASE_URL
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import event  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.db import init_db, make_engine, make_session_factory  # noqa: E402
from backend.ingest import DuplicatePacket, store_telemetry  # noqa: E402
from shared.schema import TelemetryPayload  # noqa: E402


def main(path: str) -> None:
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    if engine.dialect.name == "sqlite":             # bulk load of a disposable replay DB: skip per-commit fsync (~10x faster)
        event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA synchronous=OFF"))
    factory = make_session_factory(engine)
    init_db(engine, factory, settings.auto_create_db)
    n: Counter = Counter()
    with open(path, encoding="utf-8") as f:
        for line in f:
            raw = json.loads(line)
            try:
                p = TelemetryPayload.model_validate(raw)
            except ValidationError:
                n["rejected_422"] += 1
                continue
            try:
                store_telemetry(factory, p, raw)
                n["accepted"] += 1
            except DuplicatePacket:
                n["duplicate_409"] += 1
    print(dict(n), "->", settings.database_url)


if __name__ == "__main__":
    main(sys.argv[1])
