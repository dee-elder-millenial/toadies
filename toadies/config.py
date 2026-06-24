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


def _data_dir():
    env = os.environ.get("TOADIES_DATA_DIR")
    if env:
        return str(Path(env).expanduser())
    return str(Path("~/.local/share/toadies").expanduser())


def default_interjection_queue_path():
    return str(Path(_data_dir()) / "interjections.jsonl")


def _coerce_env_bool(raw, *, default=False):
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _coerce_toadie_list(raw: str | None):
    if raw is None:
        return set()
    parsed = {entry.strip().lower() for entry in raw.split(",") if entry.strip()}
    parsed = {entry for entry in parsed if entry}
    return parsed


def graduated_toadies():
    """
    Return the set of toadies explicitly marked as graduated.

    There are two knobs:
    - TOADIES_GRADUATED_TOADIES: comma-separated list.
    - TOADIES_TOADETTE_GRADUATED: explicit Toadette flag kept for clarity while onboarding.
    """
    names = _coerce_toadie_list(os.environ.get("TOADIES_GRADUATED_TOADIES", ""))
    if _coerce_env_bool(os.environ.get("TOADIES_TOADETTE_GRADUATED", None)):
        names.add("toadette")
    return names


def is_toadie_graduated(toadie: str):
    if not toadie:
        return False
    return toadie.strip().lower() in graduated_toadies()
