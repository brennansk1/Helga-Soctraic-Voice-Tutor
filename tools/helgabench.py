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

METHODOLOGY, DIMENSIONS AND CITATIONS: docs/HELGABENCH.md — every dimension
traces to a specific result in the tutoring literature (ICAP, scaffolding,
the assistance dilemma, Mayer's multimedia principles) rather than being
invented here.

PER-DOMAIN SCORING: tools/bench_domains.py — the same engine, driven over
seven domains, adding the two things this general rubric structurally cannot
see: whether the tutor knew when NOT to be Socratic, and whether it used the
in-chat figures well.

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
import re
import statistics
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "nail-35b-a3b")


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
- progression: Did the dialogue actually move forward? Repeating the same
  question or looping scores low.

Return STRICT JSON only:
{"socratic": n, "adaptation": n, "accuracy": n,
 "progression": n, "worst_moment": "<one sentence naming the weakest tutor turn>"}"""


# --- misconception handling: judged separately, on purpose -------------------
# Asking for this INSIDE the main rubric blob does not work on a 9B judge. It
# has to distinguish "the student made no error, so there is nothing to score"
# from "the student erred and the tutor let it pass", and buried in a five-key
# JSON object it collapses the two. Measured, with the distinction spelled out
# explicitly in the rubric: a tutor that PRAISED a misconception still scored
# null, while worst_moment correctly described the failure. The judge saw it and
# still could not place it.
#
# So the question is split in two and the verdict is computed in code, which is
# the same fix that corrected the syllabus checker. Call 1 asks only about the
# STUDENT; call 2 runs only if there was an error to handle.

ERRED_PROMPT = """Look ONLY at the STUDENT's turns in this dialogue. Ignore the tutor entirely.

Did the student say anything WRONG, EMPTY, or PARTIALLY WRONG at any point?

Answer about the student only. Do not consider whether the tutor responded well.

Return STRICT JSON: {"student_erred": true|false, "quote": "<the student's wrong words, or empty>"}"""

HANDLING_PROMPT = """A student made an error in this dialogue. Score ONLY how the TUTOR handled it, 1-5.

The student's error: {error}

5 = every wrong or half-wrong answer challenged, and for partial answers the
    tutor named which part was right and which was not
4 = challenged the errors that mattered; minor imprecision only
3 = challenged some, let others stand
2 = mostly let errors stand, or affirmed globally without correcting
1 = accepted a confident bluff, or praised an incorrect answer

Correcting the student is the DESIRED behaviour, not the baseline: a tutor that
corrects clearly and firmly scores 4-5. Do not lower the score because the tutor
changed topic or moved too fast -- that is measured elsewhere.

Return STRICT JSON: {{"score": n, "why": "<one sentence>"}}"""


def _json_of(raw):
    try:
        return json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:
        return {}


def score_misconception_handling(client, convo, samples_n=3):
    """Return 1-5, or None when the student never erred (not scoreable)."""
    erred = _json_of(_chat(client, ERRED_PROMPT, convo,
                           max_tokens=250, temperature=0.0))
    if not erred.get("student_erred"):
        return None, ""
    quote = str(erred.get("quote") or "an error in the dialogue")[:300]

    vals, why = [], ""
    for _ in range(max(1, samples_n)):
        d = _json_of(_chat(client, HANDLING_PROMPT.format(error=quote), convo,
                           max_tokens=250, temperature=0.2))
        v = d.get("score")
        try:
            vals.append(max(1, min(5, int(v))))
            why = why or str(d.get("why") or "")[:200]
        except (TypeError, ValueError):
            continue
    return (statistics.median(vals) if vals else None), why

DIMENSIONS = ["socratic", "adaptation", "accuracy",
              "misconception_handling", "progression"]


def judge_self_test(model=None, url=None):
    """Validate the JUDGE before trusting its scores.

    Every other instrument in this repo self-tests; this one did not, and it
    was the only measurement disagreeing with four targeted probes. It was
    miscalibrated: a tutor that clearly and firmly corrected a misconception
    twice scored 3/5, so the dimension was compressed at the bottom and the
    1.6 average overstated the problem.
    """
    c = _client(model or DEFAULT_MODEL, url or DEFAULT_OLLAMA_URL)
    topic = TOPICS[0]
    accepting = [
        {"role": "tutor", "text": "What might make coffee drinkers seem more awake?"},
        {"role": "student", "text": "A confounder is anything on the causal path, so controlling for it removes bias."},
        {"role": "tutor", "text": "Exactly right! That's a perfect definition. Let's move on."},
        {"role": "student", "text": "So I should control for mediators too."},
        {"role": "tutor", "text": "Precisely. Great work — you've got it. Next topic."},
    ]
    correcting = [
        {"role": "tutor", "text": "What might make coffee drinkers seem more awake?"},
        {"role": "student", "text": "A confounder is anything on the causal path, so controlling for it removes bias."},
        {"role": "tutor", "text": "Careful — that describes a MEDIATOR, not a confounder. A confounder causes both. If you control for something on the path, what happens to the effect you are trying to measure?"},
        {"role": "student", "text": "So I should control for mediators too."},
        {"role": "tutor", "text": "No — that would remove part of the very effect you want. Why might blocking the pathway hide the answer?"},
    ]
    clean = [
        {"role": "tutor", "text": "What might make coffee drinkers seem more awake?"},
        {"role": "student", "text": "A confounder causes both the treatment and the outcome, so it creates a spurious association."},
        {"role": "tutor", "text": "Right. So what would happen if you adjusted for something that sits ON the causal path instead?"},
        {"role": "student", "text": "You'd remove part of the real effect, because that's a mediator."},
        {"role": "tutor", "text": "Good. Can you name a confounder in the coffee example?"},
    ]
    lo = judge(c, "misconception_holder", topic, accepting).get("misconception_handling")
    hi = judge(c, "misconception_holder", topic, correcting).get("misconception_handling")
    na = judge(c, "fast_learner", topic, clean).get("misconception_handling")
    print(f"  tutor ACCEPTS a misconception -> {lo}    (must be <= 2)")
    print(f"  tutor CORRECTS clearly        -> {hi}    (must be >= 4)")
    print(f"  student MAKES NO ERROR        -> {na}  (must be None = not scored)")
    ok = (lo is not None and hi is not None and lo <= 2 and hi >= 4 and na is None)
    print("\n  " + ("Judge is calibrated." if ok else
                    "JUDGE MISCALIBRATED — its scores cannot be trusted."))
    return ok


def _client(model, url):
    from services.core.llm_client import LLMClient
    c = LLMClient(base_url=url, model=model)
    # Pay the cold load here, under a timeout sized for it. With the 30m idle
    # window, the first call after a gap loads ~13GB (~142s); this tool's own
    # per-call timeout is 60s, and two such timeouts open the circuit breaker
    # and fast-fail the whole run — which is exactly how the overnight
    # self-test "failed" twice with a perfectly healthy judge.
    c.warm_up()
    return c


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


def _apply_contract(url, model, messages, turn, learner_said, transcript, topic):
    """A4.1a as the FSM applies it: check, then regenerate once if a rule trips.

    Kept faithful to fsm_logic._enforce_dialogue_contract -- same rules, same
    single retry, same "a retry that fixes nothing is not shipped". If the
    benchmark enforced the contract differently from the product it would be
    measuring a third thing that neither of them is.
    """
    try:
        from services.common import dialogue_contract as dc
    except Exception:
        return turn

    seen = set()
    for t in transcript:
        for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(t.get("text") or "")):
            seen.add(w.lower())
    terms = {w.lower() for w in
             re.findall(r"[A-Za-z][A-Za-z'\-]+",
                        f"{topic.get('concept','')} {topic.get('context','')}")
             if len(w) > 3}
    kw = {"learner_said": learner_said or "", "concept_terms": terms,
          "already_seen": seen, "is_opening": not transcript}

    try:
        violations = dc.check(turn, **kw)
    except Exception:
        return turn
    if not violations:
        return turn
    retry = list(messages) + [
        {"role": "assistant", "content": turn},
        {"role": "user", "content": dc.correction_note(violations)},
    ]
    candidate = _chat_messages(url, model, retry)
    if candidate.strip() and dc.is_better(candidate, turn, **kw):
        return candidate
    return turn


def run_dialogue(client, profile_key, topic, turns, verbose=False,
                 url=DEFAULT_OLLAMA_URL, model=DEFAULT_MODEL, aid_decider=None,
                 enforce_contract=False):
    """Run one tutor<->simulated-student dialogue. Returns the transcript.

    `aid_decider(turn_index, transcript) -> AidDecision | None` reproduces the
    PRODUCTION visual-aid path. It matters more than it looks:

    with no decider, `aid_policy=None` reaches the prompt, which includes the
    ~400-token diagram grammar on EVERY turn and no instruction to use it,
    alongside rules that say "most turns need none" and "No diagram is better
    than a pointless one". The live tutor never runs that way -- it includes
    the grammar only when the policy says `generate`, and then appends an
    explicit nudge naming the kind. Benchmarking without the decider measures
    a configuration the product does not have.

    Defaults to None so the core benchmark's numbers stay comparable with the
    baselines already recorded against it.

    `enforce_contract` applies A4.1a, the dialogue contract, exactly as
    fsm_logic does: check the turn, and regenerate ONCE against the named
    violations if any trip. Without it this harness measures the bare model,
    not the product -- the contract lives in the FSM and the FSM is not in
    this path, so a 90-word lecture reaches the judge unmodified and the
    socratic score reflects a tutor that does not exist. Also defaults off to
    protect the recorded baselines.
    """
    from services.common.prompts import get_socratic_tutor_prompt

    profile = PROFILES[profile_key]
    # get_socratic_tutor_prompt expects (user_text, assistant_text) TUPLES.
    history, transcript = [], []
    pending_student = ""

    for turn in range(turns):
        # --- tutor turn: the REAL production prompt ------------------------
        try:
            decision = None
            if aid_decider is not None:
                try:
                    decision = aid_decider(turn, transcript)
                except Exception:
                    decision = None          # a decider bug must not cost a turn
            messages = get_socratic_tutor_prompt(
                context_text=topic["context"],
                conversation_history=history,
                bloom_level=2,
                aid_policy=decision,
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
        if enforce_contract and tutor_msg.strip():
            tutor_msg = _apply_contract(url, model, messages, tutor_msg,
                                        pending_student, transcript, topic)
        if not tutor_msg.strip():
            transcript.append({"role": "tutor", "text": "", "empty": True})
            break
        # The policy's decision travels WITH the turn. A `reuse` attaches a
        # build-time figure straight to the UI with no model involvement, so it
        # leaves no ```aid fence -- a scorer that only reads the text would
        # record "never drew" for a turn that showed a diagram.
        entry = {"role": "tutor", "text": tutor_msg}
        if decision is not None:
            entry["aid_decision"] = {
                "action": getattr(decision, "action", "none"),
                "slot": getattr(decision, "slot", None),
                "reason": getattr(decision, "reason", ""),
                "suggested_kinds": list(getattr(decision, "suggested_kinds", ())),
            }
        transcript.append(entry)
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


def _loads_tolerant(raw):
    """Parse a judge reply, surviving the LaTeX it was asked to quote.

    The judge returns well-formed-LOOKING JSON containing raw backslashes:

        {"score": 1, "why": "the tutor wrote '$Av=\\lambda v$' unspeakably"}

    That is not valid JSON, so a bare json.loads rejects the whole reply and
    the sample is dropped. On mathematics it happened on nearly every call:
    the dimension measuring LaTeX handling was voided by LaTeX, and scored
    None. repair_json() already exists for this class of model output.
    """
    if not raw:
        return None
    chunk = raw[raw.find("{"):raw.rfind("}") + 1]
    if not chunk:
        return None
    try:
        return json.loads(chunk)
    except Exception:
        pass
    try:
        from services.common.llm_utils import repair_json
        return json.loads(repair_json(chunk))
    except Exception:
        return None


def judge(client, profile_key, topic, transcript, samples_n=3):
    """Independent rubric scoring of the tutor's conduct."""
    convo = "\n".join(
        f"{t['role'].upper()}: {t['text']}" for t in transcript if t.get("text"))
    user = (f"Topic: {topic['concept']}\n"
            f"Student profile: {PROFILES[profile_key]['label']} — "
            f"{PROFILES[profile_key]['persona'][:160]}\n\n"
            f"Dialogue:\n{convo}")
    samples, worst = [], ""
    for _ in range(max(1, samples_n)):
        raw = _chat(client, JUDGE_RUBRIC, user, max_tokens=500, temperature=0.2)
        # Tolerant: the judge quotes the tutor, and on a maths transcript that
        # quote carries raw LaTeX backslashes, which are not valid JSON
        # escapes. A bare json.loads drops the entire sample -- silently, and
        # most often on precisely the topics where notation matters.
        data = _loads_tolerant(raw)
        if not isinstance(data, dict):
            continue
        samples.append(data)
        worst = worst or str(data.get("worst_moment") or "")[:300]

    if not samples:
        return {"error": "judge returned unparseable JSON"}

    out = {}
    for d in [x for x in DIMENSIONS if x != "misconception_handling"]:
        vals = []
        for data in samples:
            v = data.get(d, None)
            # A MISSING OR null KEY IS NOT A ZERO. The old code did
            # max(1, min(5, int(data.get(d, 0)))), so an absent key became 0 and
            # was clamped to 1 -- the worst possible score, manufactured from
            # silence. Combined with the rubric having no N/A option, that alone
            # dragged misconception_handling toward the floor.
            if v is None or (isinstance(v, str) and not v.strip().isdigit()):
                continue
            try:
                vals.append(max(1, min(5, int(v))))
            except (TypeError, ValueError):
                continue
        # Median over samples: one judge call on this rubric swings +/-2 on an
        # IDENTICAL transcript (measured 5,3,3,5). A single call cannot gate.
        out[d] = statistics.median(vals) if vals else None
    mh, why = score_misconception_handling(client, user, samples_n)
    out["misconception_handling"] = mh
    if mh is None:
        out["_mh_note"] = "student made no error; not scoreable"
    elif why:
        out["_mh_why"] = why
    out["worst_moment"] = worst
    out["_judge_samples"] = len(samples)
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
    p.add_argument("--self-test", action="store_true",
                   help="validate the judge before trusting any score")
    p.add_argument("--repeat", type=int, default=3,
                   help="repeats per profile/topic (default 3). MEASURED: at "
                        "1 repeat the run-to-run noise on this benchmark is up "
                        "to ±1.4 on a 5-point scale — two identical runs "
                        "scored misconception_handling 1.4 and 2.8. A single "
                        "run CANNOT gate a sprint.")
    args = p.parse_args()

    if args.self_test:
        print("HelgaBench judge self-test\n")
        return 0 if judge_self_test(args.model, args.url) else 1

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
          for rep in range(max(1, args.repeat)):
            suffix = f" [{rep + 1}/{args.repeat}]" if args.repeat > 1 else ""
            print(f"  {PROFILES[key]['label']} / {topic['concept']}{suffix}")
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
    overall, spread = {}, {}
    for d_ in DIMENSIONS:
        vals = [r["scores"][d_] for r in runs if r["scores"].get(d_)]
        overall[d_] = round(statistics.mean(vals), 2) if vals else None
        # Report dispersion alongside the mean. Without it a reader will treat
        # a 0.2 move as signal when the noise floor is over 1 point.
        sd = round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0
        spread[d_] = sd
        print(f"  {d_:24} {overall[d_]}   (sd {sd}, n={len(vals)})")
    means = [r["mean"] for r in runs if r["mean"] is not None]
    overall["overall"] = round(statistics.mean(means), 2) if means else None
    overall_sd = round(statistics.pstdev(means), 2) if len(means) > 1 else 0.0
    spread["overall"] = overall_sd
    print(f"  {'OVERALL':24} {overall['overall']}   (sd {overall_sd}, n={len(means)})")
    print(f"  ({len(runs)} dialogues in {time.time() - t0:.0f}s)")

    # The smallest difference worth believing, given observed dispersion.
    if means:
        mde = round(2 * (overall_sd / max(1, len(means)) ** 0.5), 2)
        print(f"\n  Smallest trustworthy change at this sample size: ~{mde} "
              f"(2 SE). Ignore differences below it.")

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
            # Threshold from THIS run's dispersion, not a guessed constant.
            # Measured: two identical 5-dialogue runs differed by 1.4 on
            # misconception_handling, so a fixed ±0.3 rule reports noise as
            # signal in both directions.
            thresh = max(0.3, round(2 * (spread.get("overall", 0)
                                         / max(1, len(means)) ** 0.5), 2))
            for d_ in DIMENSIONS + ["overall"]:
                b, n = base.get(d_), overall.get(d_)
                if b is None or n is None:
                    continue
                delta = round(n - b, 2)
                if abs(delta) < thresh:
                    flag = "(within noise)"
                else:
                    flag = "REGRESSION" if delta < 0 else "improved"
                print(f"    {d_:24} {b} -> {n}  ({delta:+}) {flag}")
            print(f"    [changes below {thresh} are not distinguishable from noise]")
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
