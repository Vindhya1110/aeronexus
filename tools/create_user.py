"""Create or update an Aeronex account straight in the database (the bootstrap path: the first admin must come from here).

    python tools/create_user.py --username officer1 --role admin --name "Duty Officer"      # prompts for the password
    python tools/create_user.py --username officer1 --reset-password                        # change a password
    python tools/create_user.py --username official.demo --role admin --generate               # random password, printed once

Uses AIRDNA_DATABASE_URL like the backend. Never put a password on the command line (it lands in shell history);
use the prompt, --generate, or the AIRDNA_NEW_PASSWORD environment variable for scripts.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from backend.auth import ROLES, hash_password, validate_new_credentials  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.db import init_db, make_engine, make_session_factory  # noqa: E402
from backend.models import User  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=ROLES, default="user")
    ap.add_argument("--name", default="", help="display name")
    ap.add_argument("--generate", action="store_true", help="generate a random password and print it once")
    ap.add_argument("--reset-password", action="store_true", help="set a new password for an existing account")
    ap.add_argument("--quiet", action="store_true", help="with --generate: print only the password")
    a = ap.parse_args()

    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    init_db(engine, factory, settings.auto_create_db)

    if a.generate:
        password = secrets.token_urlsafe(12)
    else:
        password = os.getenv("AIRDNA_NEW_PASSWORD") or getpass.getpass("Password: ")
        if not os.getenv("AIRDNA_NEW_PASSWORD") and getpass.getpass("Repeat: ") != password:
            print("passwords differ")
            return 1
    try:
        validate_new_credentials(a.username, password)
    except Exception as e:  # noqa: BLE001 - HTTPException detail is the message
        print(getattr(e, "detail", e))
        return 1

    with factory() as db:
        u = db.scalar(select(User).where(func.lower(User.username) == a.username.lower()))
        if u and not a.reset_password:
            print(f"account '{a.username}' already exists (use --reset-password to change its password)")
            return 1
        if not u and a.reset_password:
            print(f"no such account '{a.username}'")
            return 1
        if u:
            u.password_hash, u.active = hash_password(password), True
            action = "password reset"
        else:
            db.add(User(username=a.username, display_name=a.name, role=a.role, password_hash=hash_password(password)))
            action = f"created ({a.role})"
        db.commit()
    if a.generate:
        print(password if a.quiet else f"{a.username}: {action}; password (shown once): {password}")
    else:
        print(f"{a.username}: {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
