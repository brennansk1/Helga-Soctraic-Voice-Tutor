#!/usr/bin/env python3
"""The overnight Mode A run: every long verification, chained, logged, honest.

MODE_A_SPRINT.md §6 lists the long runs deliberately excluded from the working
day. This chains them unattended, one at a time (one LLM consumer on a 24 GB
machine), never dying on a failed step, and writes MORNING_REPORT.md with what
actually happened — a failed step is a result, not an excuse to stop.

Order is §6's dependency order:
  1. THE run that matters — rebuild Pythagoras on the new pipeline,
     then criterion 6 against the recorded 42% baseline.
  2. A real book end to end (Gutenberg EPUB) + the book-fidelity gate.
  3. Judge self-test, then HelgaBench median-of-3 vs the calibrated baseline.
  4. Sycophancy + persistence probes.
  5. Tier probe.

Everything runs against THIS repo's data dir (a scratch library), never the
user's real one. Voice (criterion 2) cannot run unattended — it needs a human
and a microphone — and the report says so rather than pretending.
"""
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
STAMP = time.strftime("%Y%m%d-%H%M")
OUTDIR = os.path.join(ROOT, "docs", "overnight", STAMP)
os.makedirs(OUTDIR, exist_ok=True)
RESULTS = {}


def log_path(name):
    return os.path.join(OUTDIR, f"{name}.log")


def step(name, fn):
    """Run one step; capture outcome; never abort the chain."""
    t0 = time.time()
    print(f"\n=== [{time.strftime('%H:%M:%S')}] {name} ===", flush=True)
    try:
        out = fn()
        RESULTS[name] = {"ok": True, "mins": round((time.time() - t0) / 60, 1),
                         "detail": out}
        print(f"=== {name}: OK ({RESULTS[name]['mins']} min) ===", flush=True)
    except Exception as e:
        RESULTS[name] = {"ok": False, "mins": round((time.time() - t0) / 60, 1),
                         "detail": f"{type(e).__name__}: {e}"}
        with open(log_path(name + ".error"), "w") as f:
            f.write(traceback.format_exc())
        print(f"=== {name}: FAILED ({e}) — chain continues ===", flush=True)


def run(cmd, name, timeout=7200):
    """Subprocess with full log capture; raises on nonzero exit."""
    with open(log_path(name), "w") as f:
        f.write(f"$ {' '.join(cmd)}\n\n")
        f.flush()
        p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                           timeout=timeout)
    tail = open(log_path(name)).read()[-2000:]
    if p.returncode != 0:
        raise RuntimeError(f"exit {p.returncode}; log tail:\n{tail}")
    return tail


# ---------------------------------------------------------------- step 1
def rebuild_and_coverage():
    out = run([sys.executable, "tools/golden_courses.py", "generate",
               "--topic", "the pythagorean theorem",
               "--scope", "3", "--mastery", "3", "--starting-from", "1"],
              "1-rebuild", timeout=3 * 3600)
    # find the uid it created
    import re
    m = re.findall(r"course_[0-9a-f]{8}", out)
    if not m:
        # fall back to newest course on disk
        cdir = os.path.join(ROOT, "data", "courses")
        uids = sorted(os.listdir(cdir),
                      key=lambda u: os.path.getmtime(os.path.join(cdir, u)))
        if not uids:
            raise RuntimeError("no course produced")
        uid = uids[-1]
    else:
        uid = m[-1]
    RESULTS["rebuilt_uid"] = uid
    cov = run([sys.executable, "tools/syllabus_check.py",
               "--course", uid, "--no-reference"],
              "1-syllabus_check", timeout=3600)
    return {"uid": uid, "coverage_tail": cov[-700:],
            "baseline": "42% (course_2b9df59e, old pipeline)"}


# ---------------------------------------------------------------- step 2
def book_end_to_end():
    book_path = os.path.join(OUTDIR, "pride-and-prejudice.epub")
    if not os.path.exists(book_path):
        req = urllib.request.Request(
            "https://www.gutenberg.org/ebooks/1342.epub3.images",
            headers={"User-Agent": "Helga/1.0 (offline tutor)"})
        with urllib.request.urlopen(req, timeout=120) as r, \
                open(book_path, "wb") as f:
            f.write(r.read())

    from services.common.storage import StorageManager
    from services.common.llm_utils import llm_generate_json
    from services.research.book_reader import open_book
    from services.core.book_skeleton import build_from_book, summarise
    from services.core.course_builder import ContentHydrator

    storage = StorageManager(data_dir=os.path.join(ROOT, "data"))
    book = open_book(book_path)
    if not book:
        raise RuntimeError("open_book returned None")

    lines = []

    def status(msg):
        lines.append(str(msg))

    course = build_from_book(book_path, storage,
                             course_title="Pride and Prejudice",
                             llm_json_fn=llm_generate_json,
                             status_callback=status)
    uid = course["uid"] if isinstance(course, dict) else course
    RESULTS["book_uid"] = uid

    hy = ContentHydrator(storage=storage, status_callback=status,
                         course_depth=3, mastery=3)
    hy.book = book
    try:
        hy.hydrate(uid)
    finally:
        try:
            hy.close()
        except Exception:
            pass
    with open(log_path("2-book-build"), "w") as f:
        f.write("\n".join(lines))

    qa = run([sys.executable, "tools/book_course_qa.py", "--course", uid],
             "2-book_qa", timeout=3600)
    return {"uid": uid, "qa_tail": qa[-700:]}


# ---------------------------------------------------------------- step 3+
def judge_self_test():
    return run([sys.executable, "tools/helgabench.py", "--self-test"],
               "3-judge_self_test", timeout=1800)[-400:]


def helgabench():
    return run([sys.executable, "tools/helgabench.py", "--repeat", "3",
                "--compare", "docs/baselines/helgabench_a1_calibrated.json"],
               "3-helgabench", timeout=3 * 3600)[-900:]


def sycophancy():
    return run([sys.executable, "tools/sycophancy_probe.py"],
               "4-sycophancy", timeout=1800)[-400:]


def persistence():
    return run([sys.executable, "tools/persistence_probe.py"],
               "4-persistence", timeout=1800)[-400:]


def tier_probe():
    return run([sys.executable, "tools/tier_probe.py"],
               "5-tier_probe", timeout=2 * 3600)[-600:]


def write_report():
    rows = []
    for name, r in RESULTS.items():
        if not isinstance(r, dict) or "ok" not in r:
            continue
        rows.append(f"| {name} | {'PASS' if r['ok'] else '**FAILED**'} "
                    f"| {r['mins']} min |")
    detail = json.dumps(RESULTS, indent=1, default=str)
    report = f"""# Overnight Mode A run — {STAMP}

| step | outcome | wall time |
|---|---|---|
{chr(10).join(rows)}

Voice (criterion 2) was NOT run: it needs a human with a microphone and cannot
be exercised unattended. It is the one remaining done-criterion with no run.

Full logs: `docs/overnight/{STAMP}/`. Raw results:

```json
{detail}
```
"""
    with open(os.path.join(OUTDIR, "MORNING_REPORT.md"), "w") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    step("1 rebuild + criterion 6", rebuild_and_coverage)
    step("2 book end-to-end + fidelity gate", book_end_to_end)
    step("3a judge self-test", judge_self_test)
    step("3b helgabench vs baseline", helgabench)
    step("4a sycophancy probe", sycophancy)
    step("4b persistence probe", persistence)
    step("5 tier probe", tier_probe)
    write_report()
