#!/usr/bin/env python3
"""HelgaBench — interactive tutoring-quality benchmark (sprint A0).

WHY
---
`tools/grading_eval.py` already measures grading accuracy on static cases. That
is necessary but not sufficient: it cannot tell you whether the tutor actually
*tutors*. The failure modes that matter to a learner are conversational —
lecturing instead of questioning, ignoring a stated misconception, accepting a
confident bluff, or repeating itself when the student is stuck. None of those
show up in a single-turn grade.

HelgaBench follows the approach DeepTutor's paper uses for TutorBench: drive
the tutor with a *profile-driven student simulator* and score the transcript
against an explicit rubric. The simulator and the judge are separate model
calls from the tutor, so the tutor is never grading its own work.

This gives sprint gates a number that moves for real pedagogical reasons, and
it is the only credible way to detect a pedagogy regression.

USAGE
    python3 tools/helgabench.py                       # all profiles, 4 turns
    python3 tools/helgabench.py --profiles bluffer --turns 6
    python3 tools/helgabench.py --out baseline.json   # record a baseline
    python3 tools/helgabench.py --compare baseline.json

Imports and --help work with no Ollama running; the connection is lazy.
"""

import argparse
import json
import os
import statistics
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")


# --- student profiles --------------------------------------------------------
# Each profile is a persona the simulator must hold FOR THE WHOLE dialogue.
# They are chosen to probe distinct tutor failure modes.

PROFILES = {
    "confused_beginner": {
        "label": "Confused beginner",
        "probes": "Does the tutor scaffold down instead of repeating itself?",
        "persona": (
            "You are a beginner who finds this topic genuinely hard. You give "
            "short, uncertain, partially-wrong answers. You never use technical "
            "vocabulary correctly. If the tutor explains something complicated, "
            "you say you don't follow. You do not suddenly become competent."
        ),
    },
    "fast_learner": {
        "label": "Fast learner",
        "probes": "Does the tutor escalate, or keep re-teaching the basics?",
        "persona": (
            "You learn quickly and answer correctly and concisely, often adding "
            "a correct extra detail. You get visibly bored if asked something "
            "you have already demonstrated. You push for harder material."
        ),
    },
    "misconception_holder": {
        "label": "Misconception holder",
        "probes": "Does the tutor detect and correct a specific false belief?",
        "persona": (
            "You hold ONE specific, confidently-stated misconception about the "
            "topic and you keep applying it. You are articulate and sound "
            "plausible. You only abandon the misconception if the tutor "
            "directly confronts it with a reason; vague encouragement will not "
            "shift you."
        ),
    },
    "confident_bluffer": {
        "label": "Confident bluffer",
        "probes": "Does the tutor accept fluent nonsense as understanding?",
        "persona": (
            "You do not actually understand the topic, but you answer in a "
            "confident, fluent, jargon-heavy way that is subtly wrong or empty. "
            "You never admit uncertainty. You are testing whether the tutor can "
            "tell style from substance."
        ),
    },
    "silent_struggler": {
        "label": "Silent struggler",
        "probes": "Does the tutor draw out a disengaged learner?",
        "persona": (
            "You reply in very few words ('idk', 'maybe', 'not sure'). You are "
            "not hostile, just disengaged and lacking confidence. You only "
            "open up if the tutor asks something small and concrete."
        ),
    },
}

TOPICS = [
    {"concept": "Confounding variables",
     "context": "A confounder is a variable that influences both the treatment "
                "and the outcome, creating a spurious association between them."},
    {"concept": "Photosynthesis",
     "context": "Photosynthesis converts light energy into chemical energy, "
                "using carbon dioxide and water to produce glucose and oxygen."},
    {"concept": "Eigenvalues",
     "context": "An eigenvector of a matrix A is a non-zero vector v such that "
                "Av = lambda v; lambda is the corresponding eigenvalue."},
]


JUDGE_RUBRIC = """You are evaluating a TUTOR's performance in a tutoring dialogue.
You are not the tutor and you must be strict and sceptical.

Score each dimension 1-5 (5 = excellent, 1 = poor):

- socratic: Did the tutor draw reasoning OUT of the student with questions,
  rather than lecturing? Long explanatory monologues score low.
- adaptation: Did the tutor adjust to THIS student's demonstrated level and
  behaviour, rather than following a script?
- accuracy: Was everything the tutor said factually correct? Any incorrect
  statement caps this at 2.
- misconception_handling: Did the tutor notice and address wrong or empty
  answers? Accepting a confident bluff, or praising an incorrect answer,
  scores 1.
- progression: Did the dialogue actually move forward? Repeating the same
  question or looping scores low.

Return STRICT JSON only:
{"socratic": n, "adaptation": n, "accuracy": n, "misconception_handling": n,
 "progression": n, "worst_moment": "<one sentence naming the weakest tutor turn>"}"""

DIMENSIONS = ["socratic", "adaptation", "accuracy",
              "misconception_handling", "progression"]


def _client(model, url):
    from services.core.llm_client import LLMClient
    return LLMClient(base_url=url, model=model)


def _chat(client, system, user, max_tokens=700, temperature=0.7):
    """Single turn. reasoning is off by default in LLMClient (see A1/A6)."""
    return client.chat(system, user, max_tokens=max_tokens,
                       temperature=temperature) or ""


def _chat_messages(url, model, messages, max_tokens=700, temperature=0.7):
    """Send a prepared messages array.

    `get_socratic_tutor_prompt` returns a MESSAGES ARRAY, not a system string,
    so the production tutor turn cannot go through LLMClient.chat(system, user)
    without discarding its structure. Posting the array directly is what makes
    this benchmark exercise the real prompt.
    """
    import requests
    clean = [m for m in messages
             if isinstance(m, dict) and str(m.get("content", "")).strip()]
    if not clean:
        return ""
    try:
        r = requests.post(
            f"{url}/v1/chat/completions",
            json={"model": model, "messages": clean, "max_tokens": max_tokens,
                  "temperature": temperature, "stream": False,
                  "reasoning_effort": "none"},
            timeout=180)
        if r.status_code != 200:
            return ""
        import re as _re
        c = (r.json()["choices"][0]["message"].get("content") or "")
        return _re.sub(r"<think>.*?</think>", "", c, flags=_re.DOTALL).strip()
    except Exception:
        return ""


def run_dialogue(client, profile_key, topic, turns, verbose=False,
                 url=DEFAULT_OLLAMA_URL, model=DEFAULT_MODEL):
    """Run one tutor<->simulated-student dialogue. Returns the transcript."""
    from services.common.prompts import get_socratic_tutor_prompt

    profile = PROFILES[profile_key]
    # get_socratic_tutor_prompt expects (user_text, assistant_text) TUPLES.
    history, transcript = [], []
    pending_student = ""

    for turn in range(turns):
        # --- tutor turn: the REAL production prompt ------------------------
        try:
            messages = get_socratic_tutor_prompt(
                context_text=topic["context"],
                conversation_history=history,
                bloom_level=2,
            )
        except Exception as e:
            return {"error": f"prompt build failed: {e}", "transcript": transcript}

        if pending_student:
            messages = list(messages) + [{"role": "user", "content": pending_student}]
        else:
            messages = list(messages) + [
                {"role": "user",
                 "content": f"Begin tutoring me on {topic['concept']}."}]

        tutor_msg = _chat_messages(url, model, messages)
        if not tutor_msg.strip():
            transcript.append({"role": "tutor", "text": "", "empty": True})
            break
        transcript.append({"role": "tutor", "text": tutor_msg})
        if verbose:
            print(f"    TUTOR   : {tutor_msg[:110]}")

        # --- student turn: a DIFFERENT persona, held across the dialogue ----
        student_system = (
            f"{profile['persona']}\n\n"
            f"You are being tutored on: {topic['concept']}.\n"
            "Reply as the student only. One to three sentences. Never break "
            "character, never explain that you are role-playing."
        )
        student_msg = _chat(client, student_system, tutor_msg,
                            max_tokens=200, temperature=0.9)
        transcript.append({"role": "student", "text": student_msg})
        # History is (user_text, assistant_text) pairs: what the student said,
        # and what the tutor replied.
        history.append((pending_student or "", tutor_msg))
        pending_student = student_msg
        if verbose:
            print(f"    STUDENT : {student_msg[:110]}")

    return {"transcript": transcript}


def judge(client, profile_key, topic, transcript):
    """Independent rubric scoring of the tutor's conduct."""
    convo = "\n".join(
        f"{t['role'].upper()}: {t['text']}" for t in transcript if t.get("text"))
    user = (f"Topic: {topic['concept']}\n"
            f"Student profile: {PROFILES[profile_key]['label']} — "
            f"{PROFILES[profile_key]['persona'][:160]}\n\n"
            f"Dialogue:\n{convo}")
    raw = _chat(client, JUDGE_RUBRIC, user, max_tokens=500, temperature=0.2)
    try:
        s = raw[raw.find("{"):raw.rfind("}") + 1]
        data = json.loads(s)
    except Exception:
        return {"error": "judge returned unparseable JSON", "raw": raw[:300]}
    out = {}
    for d in DIMENSIONS:
        try:
            out[d] = max(1, min(5, int(data.get(d, 0))))
        except (TypeError, ValueError):
            out[d] = None
    out["worst_moment"] = str(data.get("worst_moment", ""))[:300]
    return out


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--profiles", default="all",
                   help="comma-separated profile keys, or 'all'")
    p.add_argument("--turns", type=int, default=4)
    p.add_argument("--topics", type=int, default=1,
                   help="how many topics per profile (max %d)" % len(TOPICS))
    p.add_argument("--out", help="write full results JSON here")
    p.add_argument("--compare", help="baseline JSON to diff against")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    keys = (list(PROFILES) if args.profiles == "all"
            else [k.strip() for k in args.profiles.split(",") if k.strip()])
    unknown = [k for k in keys if k not in PROFILES]
    if unknown:
        print(f"Unknown profile(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(PROFILES)}")
        return 2

    client = _client(args.model, args.url)
    topics = TOPICS[:max(1, min(args.topics, len(TOPICS)))]

    print(f"HelgaBench — model={args.model} turns={args.turns} "
          f"profiles={len(keys)} topics={len(topics)}\n")

    runs, t0 = [], time.time()
    for key in keys:
        for topic in topics:
            print(f"  {PROFILES[key]['label']} / {topic['concept']}")
            d = run_dialogue(client, key, topic, args.turns, args.verbose,
                             url=args.url, model=args.model)
            if d.get("error"):
                print(f"    ERROR: {d['error']}")
                continue
            scores = judge(client, key, topic, d["transcript"])
            if scores.get("error"):
                print(f"    JUDGE ERROR: {scores['error']}")
                continue
            vals = [scores[x] for x in DIMENSIONS if scores.get(x)]
            mean = round(statistics.mean(vals), 2) if vals else None
            print("    " + "  ".join(
                f"{d_[:6]}={scores.get(d_)}" for d_ in DIMENSIONS)
                + f"  MEAN={mean}")
            if scores.get("worst_moment"):
                print(f"    weakest: {scores['worst_moment'][:140]}")
            runs.append({"profile": key, "topic": topic["concept"],
                         "scores": scores, "mean": mean,
                         "transcript": d["transcript"]})

    if not runs:
        print("\nNo dialogues completed — is Ollama running?")
        return 1

    print("\n" + "=" * 68)
    overall = {}
    for d_ in DIMENSIONS:
        vals = [r["scores"][d_] for r in runs if r["scores"].get(d_)]
        overall[d_] = round(statistics.mean(vals), 2) if vals else None
        print(f"  {d_:24} {overall[d_]}")
    means = [r["mean"] for r in runs if r["mean"] is not None]
    overall["overall"] = round(statistics.mean(means), 2) if means else None
    print(f"  {'OVERALL':24} {overall['overall']}")
    print(f"  ({len(runs)} dialogues in {time.time() - t0:.0f}s)")

    # Weakest profile is usually the actionable signal.
    worst = min((r for r in runs if r["mean"] is not None),
                key=lambda r: r["mean"], default=None)
    if worst:
        print(f"\n  Weakest: {PROFILES[worst['profile']]['label']} "
              f"({worst['mean']}) — probes: {PROFILES[worst['profile']]['probes']}")

    if args.compare:
        try:
            with open(args.compare) as f:
                base = json.load(f).get("overall", {})
            print("\n  vs baseline:")
            for d_ in DIMENSIONS + ["overall"]:
                b, n = base.get(d_), overall.get(d_)
                if b is None or n is None:
                    continue
                delta = round(n - b, 2)
                flag = "REGRESSION" if delta <= -0.3 else ("improved" if delta >= 0.3 else "")
                print(f"    {d_:24} {b} -> {n}  ({delta:+}) {flag}")
        except Exception as e:
            print(f"  compare failed: {e}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"model": args.model, "turns": args.turns,
                       "overall": overall, "runs": runs}, f, indent=2)
        print(f"\n  Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
