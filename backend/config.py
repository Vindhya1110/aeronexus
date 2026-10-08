"""Runtime settings, read from environment variables (see backend/README.md)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_KEY = "dev-key"          # same default the simulator gateway uses


@dataclass(frozen=True)
class Settings:
    api_key: str = DEFAULT_API_KEY
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'airdna.db').as_posix()}"
    offline_after_s: int = 300       # device with no packet received for this long => offline/stale
    current_window_min: int = 10     # "current" zone PM = mean of samples in the last N min of data
    auto_create_db: bool = True      # create tables at startup (dev). Use alembic for Postgres.
    auth_secret: str = ""            # signs login tokens; empty => random per process (sessions end on restart)
    token_ttl_s: int = 8 * 3600
    require_auth: bool = False       # True: read API needs a bearer token (zones: any account; devices/events: admin)
    allow_signup: bool = True        # public /v1/auth/register (creates role 'user' only)
    dashboard_url: str = "/app/"                   # where the website's Login button points (same origin, via the proxy)
    dashboard_upstream: str = "http://127.0.0.1:8501"   # the Streamlit process the backend proxies /app/ to

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            api_key=os.getenv("AIRDNA_API_KEY", d.api_key),
            database_url=os.getenv("AIRDNA_DATABASE_URL", d.database_url),
            offline_after_s=int(os.getenv("AIRDNA_OFFLINE_AFTER_S", d.offline_after_s)),
            current_window_min=int(os.getenv("AIRDNA_CURRENT_WINDOW_MIN", d.current_window_min)),
            auto_create_db=os.getenv("AIRDNA_AUTO_CREATE_DB", "1") not in ("0", "false", "False"),
            auth_secret=os.getenv("AIRDNA_AUTH_SECRET") or secrets.token_urlsafe(32),
            token_ttl_s=int(os.getenv("AIRDNA_TOKEN_TTL_S", d.token_ttl_s)),
            require_auth=os.getenv("AIRDNA_REQUIRE_AUTH", "0") in ("1", "true", "True"),
            allow_signup=os.getenv("AIRDNA_ALLOW_SIGNUP", "1") not in ("0", "false", "False"),
            dashboard_url=os.getenv("AIRDNA_DASHBOARD_URL", d.dashboard_url),
            dashboard_upstream=os.getenv("AIRDNA_DASHBOARD_UPSTREAM", d.dashboard_upstream),
        )
