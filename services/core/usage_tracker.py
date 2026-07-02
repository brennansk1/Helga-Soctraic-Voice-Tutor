"""Per-student token / GPU-second usage tracking (B27.4).

llm_client records every generation's token usage (Ollama's OpenAI-compat
responses carry `usage`) and wall-clock generation time against the calling
LLMContext's student_id. In-process counters back the /metrics endpoint and
/api/usage — the unit-economics input for capacity planning (spec 10 §2).
"""

import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_usage = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0,
                              "completion_tokens": 0, "gpu_seconds": 0.0})


def record(student_id, prompt_tokens=0, completion_tokens=0, gpu_seconds=0.0):
    sid = student_id or "_anon"
    with _lock:
        u = _usage[sid]
        u["calls"] += 1
        u["prompt_tokens"] += int(prompt_tokens or 0)
        u["completion_tokens"] += int(completion_tokens or 0)
        u["gpu_seconds"] += float(gpu_seconds or 0.0)


def snapshot():
    with _lock:
        return {sid: dict(u) for sid, u in _usage.items()}


def totals():
    with _lock:
        agg = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
               "gpu_seconds": 0.0, "students": len(_usage)}
        for u in _usage.values():
            agg["calls"] += u["calls"]
            agg["prompt_tokens"] += u["prompt_tokens"]
            agg["completion_tokens"] += u["completion_tokens"]
            agg["gpu_seconds"] += u["gpu_seconds"]
        return agg


def reset():
    with _lock:
        _usage.clear()
