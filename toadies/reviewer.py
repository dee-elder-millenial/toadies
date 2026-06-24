"""Automated grading path for the trust loop.

Sends a candidate output plus optional input/task context to an OpenAI-compatible
judge model, extracts a 0..1 score, and records it through the trust loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, dataset, localai, trust
from .store import Store


DEFAULT_JUDGE_MODEL = os.environ.get(
    "TOADIES_JUDGE_MODEL", "gemma-4-e4b-it"
)
DEFAULT_JUDGE_TIMEOUT = 120
DEFAULT_JUDGE_MAX_TOKENS = 64
DEFAULT_JUDGE_RETRIES = max(1, int(os.environ.get("TOADIES_JUDGE_RETRIES", "2")))
DEFAULT_JUDGE_RETRY_DELAY_SECONDS = float(
    os.environ.get("TOADIES_JUDGE_RETRY_DELAY_SECONDS", "1.5")
)

_SCORE_RE = re.compile(r"\bscore\b[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RATIO_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)\b")


def _judge_model_candidates(requested_model: str) -> list[str]:
    candidates: list[str] = []
    if requested_model:
        candidates.append(requested_model)

    fallback_raw = os.environ.get("TOADIES_JUDGE_FALLBACK_MODELS", "")
    for model in [entry.strip() for entry in fallback_raw.split(",") if entry.strip()]:
        if model not in candidates:
            candidates.append(model)

    if DEFAULT_JUDGE_MODEL not in candidates:
        candidates.append(DEFAULT_JUDGE_MODEL)

    return candidates


def _judge_timeout_for_model(model: str, default_timeout: int) -> int:
    profile_raw = os.environ.get("TOADIES_JUDGE_TIMEOUT_OVERRIDES", "")
    if not profile_raw:
        return default_timeout

    for entry in profile_raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, sep, timeout_raw = entry.partition(":")
        if not name.strip() or not timeout_raw.strip():
            continue
        if name.strip() == model:
            try:
                return max(1, int(timeout_raw.strip()))
            except ValueError:
                return default_timeout

    return default_timeout


def _judge_backoff(attempt: int) -> float:
    return attempt * DEFAULT_JUDGE_RETRY_DELAY_SECONDS


class JudgeError(ValueError):
    """Raised when the judge model output cannot be interpreted as a score."""


@dataclass
class JudgeResult:
    score: float
    raw: str
    rationale: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _normalize_score(value: float) -> float:
    if 0.0 <= value <= 1.0:
        return value
    if 0.0 <= value <= 100.0:
        return value / 100.0
    raise ValueError(f"judge score is outside supported ranges: {value!r}")


def _extract_score_from_text(text: str) -> tuple[float, str | None]:
    # 1) attempt strict JSON first; we expect {"score": 0.0..1.0, "rationale": "..."}
    maybe_json = text.strip()
    if not maybe_json:
        raise JudgeError("judge returned empty output")
    for candidate in [maybe_json, _find_json_object(maybe_json)]:
        if candidate is None:
            continue
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict) and "score" in payload:
            rationale = payload.get("rationale")
            rationale = rationale if isinstance(rationale, str) else None
            raw_score = payload["score"]
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                break
            return _normalize_score(score), rationale

    # 2) ratio forms like "4/5", "78/100"
    ratio = _RATIO_RE.search(text)
    if ratio:
        num = float(ratio.group(1))
        den = float(ratio.group(2))
        if den > 0:
            return _normalize_score((num / den) * 100), None

    # 3) labeled scalar forms like "score: 0.93"
    m = _SCORE_RE.search(text)
    if not m:
        raise JudgeError("judge response did not contain a score")
    score = float(m.group(1))
    return _normalize_score(score), None


def _find_json_object(text: str):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _judge_prompt(toadie, task_type, input_text, output_text):
    return (
        "You are a deterministic rubric judge.\n"
        "Return strict JSON only: "
        "{\"score\": <float>, \"rationale\": \"short explanation\"}\n"
        "where score is 0..1 (higher is better).\n"
        f"Toadie: {toadie}\n"
        f"Task type: {task_type}\n"
        f"Input reference:\n{input_text or '<no input provided>'}\n"
        f"Output:\n{output_text}\n"
    )


def judge_output(
    toadie,
    task_type,
    input_text,
    output_text,
    *,
    model=DEFAULT_JUDGE_MODEL,
    base_url=None,
    api_key=None,
    transport=None,
    timeout=DEFAULT_JUDGE_TIMEOUT,
    max_tokens=DEFAULT_JUDGE_MAX_TOKENS,
) -> JudgeResult:
    base_url = base_url or localai.DEFAULT_BASE_URL
    messages = [
        {
            "role": "system",
            "content": "You are a strict grading agent that returns JSON scores for output quality.",
        },
        {"role": "user", "content": _judge_prompt(toadie, task_type, input_text, output_text)},
    ]

    candidates = _judge_model_candidates(model)
    last_error: Exception | None = None
    response = None

    for model_name in candidates:
        model_timeout = _judge_timeout_for_model(model_name, timeout)
        for attempt in range(1, DEFAULT_JUDGE_RETRIES + 1):
            try:
                response = localai.chat(
                    messages,
                    model=model_name,
                    base_url=base_url,
                    api_key=api_key,
                    transport=transport,
                    timeout=model_timeout,
                    max_tokens=max_tokens,
                )
                break
            except localai.LocalAIError as exc:
                last_error = exc
                if attempt < DEFAULT_JUDGE_RETRIES:
                    time.sleep(_judge_backoff(attempt))
                    continue
                break
        if response is not None:
            break

    if response is None:
        raise localai.LocalAIError(
            f"judge call failed for models={candidates!r}: {last_error!r}"
        ) from last_error

    score, rationale = _extract_score_from_text(response.text)
    return JudgeResult(
        score=score,
        raw=response.text,
        rationale=rationale,
        usage=response.usage,
        finish_reason=response.finish_reason,
    )


def review_and_record(
    toadie,
    task_type,
    input_text,
    output_text,
    *,
    model=DEFAULT_JUDGE_MODEL,
    base_url=None,
    api_key=None,
    transport=None,
    timeout=DEFAULT_JUDGE_TIMEOUT,
    max_tokens=DEFAULT_JUDGE_MAX_TOKENS,
    source="rubric",
    db_path=None,
    dataset_path=None,
) -> dict:
    judge = judge_output(
        toadie,
        task_type,
        input_text,
        output_text,
        model=model,
        base_url=base_url,
        api_key=api_key,
        transport=transport,
        timeout=timeout,
        max_tokens=max_tokens,
    )

    db_path = db_path or config.default_db_path()
    try:
        s = Store(db_path)
        try:
            state = trust.record_grade(
                s,
                toadie,
                task_type,
                judge.score,
                source=source,
                prompt_hash=_hash_text(input_text),
                output_hash=_hash_text(output_text),
            )
        finally:
            s.close()
    except Exception as exc:
        return {
            "ok": False,
            "toadie": toadie,
            "task_type": task_type,
            "score": judge.score,
            "rationale": judge.rationale,
            "judge_raw": judge.raw,
            "error": str(exc),
        }

    if dataset_path:
        path = Path(dataset_path)
        dataset.log_example(
            path,
            toadie=toadie,
            task_type=task_type,
            score=judge.score,
            input_text=input_text,
            output_text=output_text,
        )

    return {
        "ok": True,
        "toadie": toadie,
        "task_type": task_type,
        "score": judge.score,
        "rationale": judge.rationale,
        "judge_raw": judge.raw,
        "finish_reason": judge.finish_reason,
        "usage": judge.usage,
        "leash_level": state.leash_level,
        "ema": state.ema,
        "samples": state.samples,
    }
