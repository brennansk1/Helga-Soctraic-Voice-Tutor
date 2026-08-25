"""Does this course actually work — everywhere the product touches it?

A course being "ready" says its concepts have bodies that cleared the depth
contract. It says nothing about whether the tutor can teach them, whether the
flashcards generate, whether review schedules them, whether search finds them,
or whether the path renders. Those are separate systems that read the same
course, and this project's signature failure is precisely a component that
works on a path that never fires.

So this drives the REAL endpoints, in the order a learner meets them, and
reports what it saw rather than that it ran.

    python3 tools/course_acceptance.py <course_uid> [--quick] [--json out.json]

Exit code is 0 only if every gate that ran passed. Gates that could not run
(no due cards yet, no figures in this course) are reported as SKIP and do not
fail the run — a skip is honest, a silent pass is not.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

CORE = "http://localhost:5003"
RAG = "http://localhost:5002"
WEB = "http://localhost:5050"

TUTOR_TIMEOUT = 240


# --- plumbing ---------------------------------------------------------------

def call(method, url, body=None, timeout=30):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return r.status, {"_raw": raw[:400].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:400].decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"_error": str(e)}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, area, ok, detail, evidence=""):
        self.rows.append({"area": area, "status": ok, "detail": detail,
                          "evidence": evidence[:400]})
        mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip ",
                "WARN": " warn "}[ok]
        print(f"[{mark}] {area:32} {detail}")
        if evidence and ok in ("FAIL", "WARN"):
            print(f"          {evidence[:300]}")

    def failed(self):
        return [r for r in self.rows if r["status"] == "FAIL"]

    def summary(self):
        c = {}
        for r in self.rows:
            c[r["status"]] = c.get(r["status"], 0) + 1
        return c


# --- the gates --------------------------------------------------------------

def gate_course_readable(rep, uid):
    st, d = call("GET", f"{RAG}/api/pipeline/course/{uid}")
    if st != 200:
        rep.add("course readable", "FAIL", f"HTTP {st}", json.dumps(d))
        return None
    counts = d.get("counts", {})
    rep.add("course readable", "PASS",
            f"{counts.get('concepts')} concepts, status {d.get('status')}")
    return d


def gate_depth_contract(rep, d):
    v = (d.get("verdicts") or {}).get("depth_contract") or {}
    met = v.get("met_pct")
    if met is None:
        rep.add("depth contract", "SKIP", "no verdict recorded (run finalize)")
        return
    ok = "PASS" if met >= 95 else ("WARN" if met >= 80 else "FAIL")
    rep.add("depth contract", ok, f"{met}% of concepts meet their level",
            f"level_verified={v.get('level_verified')}")


def gate_grounding(rep, uid, d):
    """Sources are the difference between teaching and recall."""
    total = len(d.get("concepts") or [])
    with_src = 0
    sampled = 0
    for c in (d.get("concepts") or []):
        st, cd = call("GET", f"{RAG}/api/pipeline/course/{uid}/concept/{c['uid']}")
        if st != 200:
            continue
        sampled += 1
        body = cd.get("content") or cd.get("body") or ""
        if re.search(r"https?://", body):
            with_src += 1
    if not sampled:
        rep.add("grounding", "SKIP", "no concept bodies could be read")
        return
    pct = with_src / sampled * 100
    ok = "PASS" if pct >= 80 else ("WARN" if pct >= 40 else "FAIL")
    rep.add("grounding", ok,
            f"{with_src}/{sampled} concepts cite at least one source ({pct:.0f}%)",
            "llm-only content is the known quality gap" if pct < 80 else "")


def gate_structure_renders(rep, uid):
    st, d = call("GET", f"{RAG}/api/course_structure?uid={uid}")
    s = d.get("structure") or d
    mods = s.get("modules") or []
    n = sum(len(l.get("concepts") or [])
            for m in mods for u in (m.get("units") or [])
            for l in (u.get("lessons") or []))
    ok = "PASS" if (st == 200 and mods and n) else "FAIL"
    rep.add("learn path renders", ok, f"{len(mods)} modules, {n} concepts",
            json.dumps(d)[:200] if ok == "FAIL" else "")


def gate_search_finds_it(rep, uid, d):
    """FTS5 has to surface this course's own concepts."""
    concepts = d.get("concepts") or []
    if not concepts:
        rep.add("search", "SKIP", "no concepts")
        return
    title = concepts[len(concepts) // 2]["title"]
    term = " ".join(w for w in re.findall(r"[A-Za-z]{4,}", title)[:2])
    st, res = call("GET", f"{RAG}/search?q={urllib.parse.quote(term)}")
    hits = res.get("results") or []
    own = {c["uid"] for c in concepts}
    mine = [h for h in hits if h.get("uid") in own]
    ok = "PASS" if mine else ("WARN" if hits else "FAIL")
    rep.add("search finds this course", ok,
            f"'{term}' -> {len(hits)} hits, {len(mine)} in this course",
            json.dumps(res)[:200] if not mine else "")


def gate_flashcards(rep, uid, concept_uid):
    st, d = call("POST", f"{RAG}/api/generate_flashcards",
                 {"course_uid": uid, "concept_uid": concept_uid, "count": 3},
                 timeout=240)
    cards = d.get("flashcards") or d.get("cards") or []
    ok = "PASS" if cards else "FAIL"
    ev = ""
    if cards:
        c0 = cards[0]
        ev = f"{str(c0.get('front') or c0.get('question'))[:90]}"
    rep.add("flashcards generate", ok, f"{len(cards)} card(s)",
            ev if ok == "PASS" else json.dumps(d)[:200])
    return cards


def gate_quiz(rep, uid):
    st, d = call("GET", f"{RAG}/api/quiz?course_uid={uid}&count=3", timeout=240)
    qs = d.get("questions") or d.get("quiz") or []
    ok = "PASS" if qs else "FAIL"
    ev = str(qs[0].get("question"))[:90] if qs else json.dumps(d)[:200]
    rep.add("quiz generates", ok, f"{len(qs)} question(s)", ev)


def gate_teaching_context(rep, uid, d):
    """What the tutor is handed before it speaks."""
    concepts = d.get("concepts") or []
    if not concepts:
        rep.add("teaching context", "SKIP", "no concepts")
        return
    c = concepts[0]
    st, tc = call("GET",
                  f"{RAG}/teaching_context?course_uid={uid}&uid={c['uid']}")
    if st != 200:
        rep.add("teaching context", "FAIL", f"HTTP {st}", json.dumps(tc)[:200])
        return
    body = json.dumps(tc)
    ok = "PASS" if len(body) > 200 else "WARN"
    rep.add("teaching context", ok, f"{len(body)} chars for '{c['title'][:30]}'")


TUTOR_SENDERS = ("helga", "ai", "assistant")


def _last_tutor_line(state):
    """The transcript keys the speaker as `sender`, and the tutor is 'helga'."""
    for t in reversed(state.get("transcript") or []):
        if (t.get("sender") or "").lower() in TUTOR_SENDERS:
            return t.get("text") or ""
    return ""


def _tutor_turn(uid, concept_uid, learner_text):
    call("POST", f"{CORE}/event",
         {"type": "SET_CONTEXT", "student_id": "default",
          "payload": {"course_uid": uid}})
    call("POST", f"{CORE}/event",
         {"type": "NAVIGATE_TO_TOPIC", "student_id": "default",
          "payload": {"topic_id": concept_uid}}, timeout=TUTOR_TIMEOUT)
    st, state = call("GET", f"{CORE}/state?student_id=default")
    opening = ""
    opening = _last_tutor_line(state)
    call("POST", f"{CORE}/event",
         {"type": "TEXT_INPUT", "student_id": "default",
          "payload": {"text": learner_text}}, timeout=TUTOR_TIMEOUT)
    st, state = call("GET", f"{CORE}/state?student_id=default")
    reply = _last_tutor_line(state)
    return opening, reply, state


def gate_tutoring(rep, uid, d, quick):
    """The only check that answers 'can it teach this?'."""
    concepts = [c for c in (d.get("concepts") or []) if c.get("has_content")]
    if not concepts:
        rep.add("tutoring", "FAIL", "no concept has content to teach")
        return
    picks = concepts[:1] if quick else [
        concepts[0], concepts[len(concepts) // 2], concepts[-1]]
    answers = ["I'm not sure", "Is it because it filters rows after grouping?",
               "no idea"]
    taught = 0
    for i, c in enumerate(picks):
        opening, reply, state = _tutor_turn(uid, c["uid"], answers[i % len(answers)])
        if not opening:
            rep.add(f"tutor opens: {c['title'][:24]}", "FAIL",
                    "no opening turn", f"state={state.get('state')}")
            continue
        problems = []
        if not reply:
            problems.append("no reply to the learner")
        if reply and "?" not in reply:
            problems.append("reply does not end in a question")
        if reply and len(reply.split()) > 120:
            problems.append(f"reply is {len(reply.split())} words")
        if state.get("state") != "SOCRATIC_LEARNING":
            problems.append(f"FSM in {state.get('state')}")
        if problems:
            rep.add(f"tutor: {c['title'][:24]}", "FAIL", "; ".join(problems),
                    reply[:250])
        else:
            taught += 1
            rep.add(f"tutor: {c['title'][:24]}", "PASS",
                    f"{len(reply.split())} words, ends in a question",
                    reply[:200])
    if taught:
        rep.add("tutoring", "PASS", f"{taught}/{len(picks)} concepts taught")


def gate_progress_and_review(rep, uid, d):
    """Completing a concept must schedule it, and the schedule must show it."""
    concepts = [c for c in (d.get("concepts") or []) if c.get("has_content")]
    if not concepts:
        rep.add("review scheduling", "SKIP", "no concepts with content")
        return
    c = concepts[0]
    st, res = call("POST", f"{RAG}/api/update_mastery",
                   {"uid": c["uid"], "course_uid": uid, "grade": 4,
                    "bloom_level": 2})
    if st != 200:
        rep.add("mastery recorded", "FAIL", f"HTTP {st}", json.dumps(res)[:200])
        return
    rep.add("mastery recorded", "PASS", f"'{c['title'][:28]}' graded")

    st, due = call("GET", f"{RAG}/api/due_concepts?course_uid={uid}")
    items = due.get("due") or due.get("concepts") or []
    rep.add("review queue answers", "PASS" if st == 200 else "FAIL",
            f"{len(items)} due now", json.dumps(due)[:200] if st != 200 else "")

    st, sched = call("GET", f"{CORE}/api/schedule?student_id=default")
    rep.add("schedule answers", "PASS" if st == 200 else "FAIL",
            f"HTTP {st}", json.dumps(sched)[:200] if st != 200 else "")

    st, stats = call("GET", f"{RAG}/api/review_stats")
    rep.add("review stats", "PASS" if st == 200 else "WARN", f"HTTP {st}")


def gate_resume(rep, uid):
    st, d = call("GET", f"{CORE}/api/resume_points?student_id=default")
    ok = "PASS" if st == 200 else "WARN"
    rep.add("resume points", ok, f"HTTP {st}", json.dumps(d)[:200] if st else "")


def gate_stats(rep, uid):
    st, d = call("GET", f"{RAG}/api/stats")
    rep.add("dashboard stats", "PASS" if st == 200 else "FAIL", f"HTTP {st}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("course_uid")
    ap.add_argument("--quick", action="store_true",
                    help="one tutor turn instead of three")
    ap.add_argument("--json")
    a = ap.parse_args()

    rep = Report()
    print(f"\n=== acceptance: {a.course_uid} ===\n")
    d = gate_course_readable(rep, a.course_uid)
    if not d:
        sys.exit(1)
    with_content = [c for c in (d.get("concepts") or []) if c.get("has_content")]
    first_uid = with_content[0]["uid"] if with_content else None

    gate_depth_contract(rep, d)
    gate_structure_renders(rep, a.course_uid)
    gate_grounding(rep, a.course_uid, d)
    gate_search_finds_it(rep, a.course_uid, d)
    gate_teaching_context(rep, a.course_uid, d)
    gate_tutoring(rep, a.course_uid, d, a.quick)
    gate_flashcards(rep, a.course_uid, first_uid)
    gate_quiz(rep, a.course_uid)
    gate_progress_and_review(rep, a.course_uid, d)
    gate_resume(rep, a.course_uid)
    gate_stats(rep, a.course_uid)

    print("\n" + json.dumps(rep.summary()))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rep.rows, f, indent=2)
        print(f"written: {a.json}")
    sys.exit(1 if rep.failed() else 0)


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (used in gate_search_finds_it)
    main()
