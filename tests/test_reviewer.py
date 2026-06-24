import json

import pytest

from toadies import localai, reviewer


class FakeStore:
    def __init__(self):
        self.competency = {}
        self.grades = []

    def get_competency(self, toadie, task_type):
        return self.competency.get((toadie, task_type))

    def upsert_competency(self, toadie, task_type, ema, samples, leash_level):
        self.competency[(toadie, task_type)] = {
            "ema": ema,
            "samples": samples,
            "leash_level": leash_level,
        }

    def insert_grade(self, **kw):
        self.grades.append(kw)

    def close(self):
        return None


class FailingStore:
    def __init__(self, *args, **kwargs):
        raise OSError("trust store unavailable")


def _fake_chat_response(text):
    def _fake_chat(messages, model, base_url=None, api_key=None, transport=None, **kwargs):
        return localai.ChatResult(text=text)

    return _fake_chat


def test_extract_score_parser_supports_json_ratio_and_label():
    score, rationale = reviewer._extract_score_from_text('{"score": 0.93, "rationale": "Looks good"}')
    assert score == 0.93
    assert rationale == "Looks good"

    score, rationale = reviewer._extract_score_from_text("quality: 4/5")
    assert score == 0.8
    assert rationale is None

    score, rationale = reviewer._extract_score_from_text("score: 78")
    assert score == 0.78
    assert rationale is None


def test_extract_score_prefers_embedded_json_when_present():
    score, rationale = reviewer._extract_score_from_text("model said: ok {\"score\": 0.41, \"note\": \"bad\"} done")
    assert score == 0.41
    assert rationale is None


def test_review_and_record_writes_dataset_entry_when_good(tmp_path, monkeypatch):
    fake_store = FakeStore()
    monkeypatch.setattr(reviewer, "Store", lambda *args, **kwargs: fake_store)
    monkeypatch.setattr(reviewer.localai, "chat", _fake_chat_response('{"score": 0.95}'))

    dataset_path = tmp_path / "dataset.jsonl"
    out = reviewer.review_and_record(
        "gremlin",
        "pytest",
        "input text",
        "output summary",
        model="judge-model",
        dataset_path=dataset_path,
    )

    assert out["ok"] is True
    assert out["score"] == 0.95
    assert out["leash_level"] == "probation"
    assert len(fake_store.grades) == 1
    assert fake_store.competency[("gremlin", "pytest")]["ema"] == 0.95
    assert dataset_path.exists()
    row = json.loads(dataset_path.read_text().strip())
    assert row["toadie"] == "gremlin"
    assert row["input"] == "input text"


def test_review_and_record_fails_open_if_store_is_down(monkeypatch):
    monkeypatch.setattr(reviewer, "Store", FailingStore)
    monkeypatch.setattr(reviewer.localai, "chat", _fake_chat_response('{"score": 0.75}'))

    out = reviewer.review_and_record("gremlin", "pytest", "input text", "output text")

    assert out["ok"] is False
    assert out["score"] == 0.75
    assert out["toadie"] == "gremlin"
    assert "trust store unavailable" in out["error"]


def test_review_and_record_bubbles_unparseable_judge_output(monkeypatch):
    monkeypatch.setattr(reviewer.localai, "chat", _fake_chat_response("no score content"))

    with pytest.raises(reviewer.JudgeError):
        reviewer.review_and_record("gremlin", "pytest", "", "output text")
