"""Dataset logger — banks graded-good (input → output) pairs for an optional future fine-tune.

This implements the "collect now, fine-tune later" decision: no training happens here, but
every example the strong judge rated highly is appended to a JSONL file so a LoRA/fine-tune
stays on the table down the road. Only high-scoring examples are kept (we don't want to teach
a model from its own mediocre output).
"""

from __future__ import annotations

import json
from pathlib import Path

GOOD_THRESHOLD = 0.85


def log_example(path, *, toadie, task_type, score, input_text, output_text,
                threshold=GOOD_THRESHOLD):
    """Append a training example iff score >= threshold. Returns whether it was logged."""
    if score < threshold:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "toadie": toadie,
        "task_type": task_type,
        "score": score,
        "input": input_text,
        "output": output_text,
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return True
