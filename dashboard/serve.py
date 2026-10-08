"""Launcher for the dashboard:  python -m dashboard.serve [--port 8501]

Why not plain `streamlit run`? On Windows the default asyncio event loop (Proactor) permanently closes the
listening socket if a browser resets a connection mid-accept ("OSError: [WinError 64]"), after which the dashboard
silently stops answering while the process stays alive. The selector loop does not have that failure mode (verified by hammering both
launch styles with reset connections), so we select it before Streamlit starts. Identical behaviour everywhere else.
"""
from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path

if sys.platform == "win32":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from streamlit.web import cli  # noqa: E402


def main() -> None:
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "8501"
    app = str(Path(__file__).with_name("app.py"))
    sys.argv = ["streamlit", "run", app, "--server.headless", "true", "--server.port", port,
                "--browser.gatherUsageStats", "false",
                "--server.baseUrlPath", "app", "--server.address", "127.0.0.1"]   # served to browsers via the backend proxy at /app/
    cli.main()


if __name__ == "__main__":
    main()
