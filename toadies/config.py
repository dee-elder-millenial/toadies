"""Minimal config resolution for the MVP.

Only what the CLI needs today: where the database lives. The richer config.toml
loader described in the handoff backlog can layer on later.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_db_path():
    env = os.environ.get("TOADIES_DB")
    if env:
        return env
    return str(Path("~/.local/share/toadies/toadies.db").expanduser())
