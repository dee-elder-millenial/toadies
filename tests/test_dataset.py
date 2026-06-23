import json

from toadies import dataset


def test_logs_only_high_scoring_examples(tmp_path):
    p = tmp_path / "dataset.jsonl"

    logged = dataset.log_example(
        p, toadie="gremlin", task_type="pytest", score=0.95,
        input_text="raw log", output_text="summary",
    )
    assert logged is True

    # a mediocre grade is not banked as a training example
    skipped = dataset.log_example(
        p, toadie="gremlin", task_type="pytest", score=0.40,
        input_text="raw log 2", output_text="summary 2",
    )
    assert skipped is False

    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["toadie"] == "gremlin"
    assert rec["task_type"] == "pytest"
    assert rec["input"] == "raw log"
    assert rec["output"] == "summary"
    assert rec["score"] == 0.95
