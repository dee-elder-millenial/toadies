"""Structured interjection channel for high-competency toadies.

When a toadie finds a likely-impacting observation, it can submit an interjection
into a shared queue. Trusted toadies can request interrupt-style handoff;
lower levels can append-only notes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config, trust
from .store import Store


INTERJECTION_EVENT = "toadie_interjection"
MIN_LEVEL_FOR_APPEND = "spot_check"
MIN_APPEND_SCORE = 0.70
MIN_INTERRUPT_SCORE = 0.90
INTERJECTION_URGENCY = {"low", "medium", "high", "critical"}
DELIVERY_INTERRUPT = "interrupt"
DELIVERY_APPEND = "append"
LEVEL_ORDER = {"probation": 0, "spot_check": 1, "trusted": 2}
TOADETTE_NAME = "toadette"

INTERRUPT_COOLDOWN_SECONDS = 90
INTERRUPT_WINDOW_SECONDS = 300
INTERRUPT_BURST_LIMIT = 5


class InterjectionDenied(ValueError):
    """Raised when a toadie is not allowed on a requested interjection path."""


@dataclass
class InterjectionRecord:
    toadie: str
    task_type: str
    delivery: str
    message: str
    urgency: str
    details: str | None = None
    task_context: dict[str, Any] | None = None
    requested_delivery: str | None = None

    def as_payload(self) -> dict:
        return {
            "toadie": self.toadie,
            "task_type": self.task_type,
            "delivery": self.delivery,
            "urgency": self.urgency,
            "message": self.message,
            "details": self.details,
            "task_context": self.task_context,
            "requested_delivery": self.requested_delivery,
        }


def _coerce_urgency(urgency):
    if urgency not in INTERJECTION_URGENCY:
        raise InterjectionDenied(
            f"urgency must be one of {sorted(INTERJECTION_URGENCY)!r}, got {urgency!r}"
        )
    return urgency


def _coerce_delivery(requested):
    if requested is None:
        return None
    if requested not in {"auto", "interrupt", "append"}:
        raise InterjectionDenied(
            f"requested_delivery must be one of ['auto', 'interrupt', 'append'], got {requested!r}"
        )
    return requested


def _normalize_delivery(state, requested):
    can_append = LEVEL_ORDER.get(state.leash_level, 0) >= LEVEL_ORDER[MIN_LEVEL_FOR_APPEND]
    can_interrupt = state.leash_level == "trusted" and state.ema >= MIN_INTERRUPT_SCORE

    requires_graduation = (
        state.toadie.lower() == TOADETTE_NAME and not config.is_toadie_graduated(state.toadie)
    )
    graduation_reason = None
    if requires_graduation:
        can_interrupt = False
        graduation_reason = f"{state.toadie} requires explicit graduation before interrupt-style interjections"

    def _with_reason(delivery):
        return delivery, graduation_reason

    if requested == "append":
        if can_append and state.ema >= MIN_APPEND_SCORE:
            return _with_reason(DELIVERY_APPEND)
        return None, "append delivery requires spot_check trust and ema >= 0.70"

    if requested == "interrupt":
        if can_interrupt:
            return _with_reason(DELIVERY_INTERRUPT)
        if can_append and state.ema >= MIN_APPEND_SCORE:
            return _with_reason(DELIVERY_APPEND)
        return None, "interrupt delivery requires trusted level with ema >= 0.90"

    # auto
    if can_interrupt:
        return _with_reason(DELIVERY_INTERRUPT)
    if can_append and state.ema >= MIN_APPEND_SCORE:
        return _with_reason(DELIVERY_APPEND)
    return None, "interjection trust threshold not met for any delivery mode"


def _append_to_fallback_file(payload, path):
    queue_path = Path(path)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def _parse_created_at(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str):
        value = str(value)

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")
        parsed = None
        for fmt in formats:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_interjection_payload(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        if isinstance(raw, dict):
            return raw
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _guard_interrupt_delivery(
    *,
    store,
    toadie,
    urgency,
    now=None,
    cooldown_seconds=INTERRUPT_COOLDOWN_SECONDS,
    window_seconds=INTERRUPT_WINDOW_SECONDS,
    burst_limit=INTERRUPT_BURST_LIMIT,
):
    if now is None:
        now = datetime.now(timezone.utc)

    if urgency == "critical":
        return {
            "allowed": True,
            "delivery": DELIVERY_INTERRUPT,
            "reason": None,
        }

    rows = store.list_events(INTERJECTION_EVENT, limit=200)
    cutoff = now - timedelta(seconds=window_seconds)
    recent_interrupts = []
    toadie_interrupts = []

    for row in rows:
        payload = _parse_interjection_payload(row.get("metadata_json", "")) or {}
        if payload.get("delivery") != DELIVERY_INTERRUPT:
            continue

        event_created = _parse_created_at(row.get("created_at"))
        if event_created is None:
            continue

        if event_created < cutoff:
            # rows are ordered newest first.
            break

        recent_interrupts.append((event_created, row))
        if row.get("toadie") == toadie:
            toadie_interrupts.append((event_created, row))

    if toadie_interrupts:
        latest = toadie_interrupts[0][0]
        if now - latest < timedelta(seconds=cooldown_seconds):
            return {
                "allowed": False,
                "delivery": DELIVERY_APPEND,
                "reason": (
                    f"Toadie {toadie} exceeded interrupt cooldown ({cooldown_seconds}s)"
                ),
            }

    if burst_limit and len(recent_interrupts) >= burst_limit:
        return {
            "allowed": False,
            "delivery": DELIVERY_APPEND,
            "reason": (
                f"interrupt burst limit reached ({len(recent_interrupts)} in {window_seconds}s)"
            ),
        }

    return {
        "allowed": True,
        "delivery": DELIVERY_INTERRUPT,
        "reason": None,
    }


def post_interjection(
    toadie,
    task_type,
    *,
    message,
    details=None,
    urgency="medium",
    requested_delivery="auto",
    task_context=None,
    db_path=None,
    store_cls=Store,
    session_id=None,
    turn_id=None,
    now=None,
    interrupt_guard=True,
    interrupt_cooldown_seconds=INTERRUPT_COOLDOWN_SECONDS,
    interrupt_burst_window_seconds=INTERRUPT_WINDOW_SECONDS,
    interrupt_burst_limit=INTERRUPT_BURST_LIMIT,
):
    urgency = _coerce_urgency(urgency)
    requested_delivery = _coerce_delivery(requested_delivery)

    if now is None:
        now = datetime.now(timezone.utc)

    record = InterjectionRecord(
        toadie=toadie,
        task_type=task_type,
        delivery="append",
        message=message,
        urgency=urgency,
        details=details,
        task_context=task_context,
        requested_delivery=requested_delivery,
    )

    db_path = db_path or config.default_db_path()
    try:
        store = store_cls(db_path)
        try:
            state = trust.competency(store, toadie, task_type)
            delivery, delivery_reason = _normalize_delivery(state, requested_delivery)
            if delivery is None:
                return {
                    "ok": False,
                    "toadie": toadie,
                    "task_type": task_type,
                    "delivery": None,
                    "reason": "competency too low for this interjection",
                    "trust": {
                        "leash_level": state.leash_level,
                        "ema": state.ema,
                        "samples": state.samples,
                    },
                }

            guard = None
            if delivery == DELIVERY_INTERRUPT and interrupt_guard:
                guard = _guard_interrupt_delivery(
                    store=store,
                    toadie=toadie,
                    urgency=urgency,
                    now=now,
                    cooldown_seconds=interrupt_cooldown_seconds,
                    window_seconds=interrupt_burst_window_seconds,
                    burst_limit=interrupt_burst_limit,
                )
                if not guard["allowed"]:
                    delivery = guard["delivery"]
                    delivery_reason = guard["reason"]

            record.delivery = delivery
            payload = {
                **record.as_payload(),
                "created_at": now.isoformat(),
                "trust": {
                    "leash_level": state.leash_level,
                    "ema": state.ema,
                    "samples": state.samples,
                },
            }
            event_id = str(uuid.uuid4())
            store.insert_event(
                id=event_id,
                event_type=INTERJECTION_EVENT,
                toadie=toadie,
                session_id=session_id,
                turn_id=turn_id,
                metadata_json=json.dumps(payload),
            )

            response = {
                "ok": True,
                "toadie": toadie,
                "task_type": task_type,
                "event_id": event_id,
                "delivery": delivery,
                "trust": {
                    "leash_level": state.leash_level,
                    "ema": state.ema,
                    "samples": state.samples,
                },
                "message": message,
                "details": details,
                "urgency": urgency,
            }
            if delivery_reason and requested_delivery != DELIVERY_APPEND:
                response["guarded"] = True
                response["reason"] = delivery_reason
            if guard is not None and not guard["allowed"]:
                response["guarded"] = True
                response["reason"] = guard["reason"]
                response["delivery"] = guard["delivery"]
            return response
        finally:
            store.close()
    except Exception as exc:
        # DB outage fallback: keep the observation in a plain JSONL queue.
        payload = {
            "ok": True,
            "toadie": toadie,
            "task_type": task_type,
            "delivery": "append",
            "message": message,
            "details": details,
            "urgency": urgency,
            "requested_delivery": requested_delivery,
            "task_context": task_context,
            "queue_reason": f"interjection db write failed: {exc}",
            "created_at": now.isoformat(),
        }
        try:
            fallback_path = config.default_interjection_queue_path()
            _append_to_fallback_file(payload, fallback_path)
            payload["ok"] = False if requested_delivery == "interrupt" else True
            payload["event_id"] = None
            payload["persisted_to"] = "fallback_file"
            payload["persisted_path"] = fallback_path
            return payload
        except Exception as fallback_exc:
            return {
                "ok": False,
                "toadie": toadie,
                "task_type": task_type,
                "delivery": None,
                "error": f"{exc}; fallback write failed: {fallback_exc}",
            }

def list_interjections(db_path=None, *, limit=50, since_created_at=None, toadie=None, task_type=None,
                      urgency=None, delivery=None):
    """List recent interjections with decoded payload for easy Robot consumption."""
    db_path = db_path or config.default_db_path()
    try:
        store = Store(db_path)
        try:
            scan_limit = max(limit * 4, 200)
            rows = store.list_events(
                "toadie_interjection",
                limit=scan_limit,
                since_created_at=since_created_at,
            )

            parsed = []
            for row in rows:
                payload = _parse_interjection_payload(row.get("metadata_json", "")) or {}
                candidate = {
                    "event_id": row.get("id"),
                    "created_at": row.get("created_at"),
                    "toadie": payload.get("toadie") or row.get("toadie"),
                    "task_type": payload.get("task_type"),
                    "delivery": payload.get("delivery"),
                    "urgency": payload.get("urgency"),
                    "message": payload.get("message"),
                    "details": payload.get("details"),
                    "task_context": payload.get("task_context"),
                    "requested_delivery": payload.get("requested_delivery"),
                    "trust": payload.get("trust"),
                    "raw": payload,
                }

                if toadie is not None and candidate["toadie"] != toadie:
                    continue
                if task_type is not None and candidate["task_type"] != task_type:
                    continue
                if urgency is not None and candidate["urgency"] != urgency:
                    continue
                if delivery is not None and candidate["delivery"] != delivery:
                    continue

                parsed.append(candidate)
                if len(parsed) >= limit:
                    break

            return parsed
        finally:
            store.close()
    except Exception:
        return []
