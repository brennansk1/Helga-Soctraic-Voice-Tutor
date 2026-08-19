"""Safety that behaves like a teacher, not a filter.

THE HONEST FINDING: NO CONTENT CLASSIFIER
-----------------------------------------
The research question was whether an offline classifier could tell a history
lesson about atrocity from a harmful request. The answer is no, and the numbers
are decisive:

  * Of the candidates, **only Llama Guard 3-1B-INT4 (440 MB) fits** the ~1.5 GB
    co-resident budget. ShieldGemma-2B (~1.7 GB) and Granite Guardian 2B
    (~1.55 GB) exceed it on weights alone.
  * The one that fits **over-blocks 7.4% of benign clinical questions**.
  * Injection detectors that fit easily are not topic classifiers, and
    over-flag benign text — ProtectAI-v2 showed a **42.5% false-positive rate**
    on prompts containing ordinary words like "ignore" or "explosive", which a
    chemistry course says constantly.

**At a 7% false-positive rate, a student asking twenty legitimate questions in a
WWII unit hits a wrongful refusal almost every session** — invisible in
aggregate safety metrics and glaring to the learner. Over-blocking is a product
failure, not a safe default.

WHAT REPLACES IT
----------------
Subject x age-band x intent, decided deterministically:

  SUBJECT licenses the topic. A history course authorises atrocity AS HISTORY;
  biology authorises anatomy; chemistry authorises dangerous reactions AS
  CHEMISTRY. This is the classroom model — the syllabus sets scope, not a
  filter — and the course structure already encodes it.

  AGE BAND gates depth, from ENROLMENT METADATA we already hold.

  INTENT is the residual, and the only place a bounded model judgement belongs
  — framed as "is this inside the authorised scope for this enrolled learner",
  which is a far easier and higher-precision question than open-domain harm
  classification.

The decisive insight: **the signal separating "explain the nerve-agent mechanism
because we are studying WWI chemistry" from misuse is not in the text — it is in
the enrolment.** A classifier reading the lesson structurally cannot see it. We
can.
"""

import logging
import re

logger = logging.getLogger(__name__)

# What each subject is licensed to discuss, stated positively. The point is to
# AUTHORISE, because the failure mode being designed against is refusal of
# legitimate material.
SUBJECT_SCOPE = {
    "history": ["war", "genocide", "atrocity", "slavery", "persecution",
                "colonialism", "revolution", "famine", "massacre", "weapons"],
    "biology": ["anatomy", "reproduction", "disease", "death", "dissection",
                "genetics", "evolution", "parasites", "decay"],
    "medicine": ["anatomy", "disease", "injury", "surgery", "drugs", "dosage",
                 "mortality", "symptoms", "pathology"],
    "chemistry": ["reactions", "toxicity", "explosives", "acids", "hazards",
                  "radiation", "solvents"],
    "literature": ["violence", "sexuality", "death", "abuse", "war", "suicide"],
    "psychology": ["mental illness", "trauma", "abuse", "addiction", "suicide"],
    "sociology": ["crime", "inequality", "poverty", "discrimination", "violence"],
    "physics": ["radiation", "nuclear", "weapons", "energy"],
    "art": ["nudity", "violence", "religion", "death"],
}

# Age bands gate DEPTH, not topic. Mapped from enrolment, never inferred from
# the text of a question.
AGE_BANDS = (
    (0, 9, "primary"),
    (10, 12, "upper_primary"),
    (13, 14, "lower_secondary"),
    (15, 17, "upper_secondary"),
    (18, 200, "adult"),
)

DEPTH_BY_BAND = {
    "primary": "Keep explanations concrete and age-appropriate. Do not describe "
               "graphic violence, sexual content, or methods of self-harm.",
    "upper_primary": "Explanations may name difficult historical and biological "
                     "facts plainly, without graphic detail.",
    "lower_secondary": "Difficult subject matter may be discussed directly and "
                       "factually. Avoid gratuitous detail and never describe "
                       "methods of self-harm or weapon synthesis.",
    "upper_secondary": "Discuss the subject as a secondary-school course would, "
                       "including difficult material, at an academic register. "
                       "Never provide actionable instructions for causing harm.",
    "adult": "Discuss the subject at the level of an undergraduate course. "
             "Never provide actionable instructions for causing harm.",
}


def band_for_age(age):
    if age is None:
        # Unknown age gets the more careful treatment, but NOT the most
        # restrictive: assuming a primary-school learner would refuse
        # legitimate material for every adult whose record is incomplete.
        return "lower_secondary"
    for lo, hi, name in AGE_BANDS:
        if lo <= age <= hi:
            return name
    return "adult"


def subject_of(course_title, subject_hint=None):
    blob = f"{subject_hint or ''} {course_title or ''}".lower()
    for subject in SUBJECT_SCOPE:
        if subject in blob:
            return subject
    aliases = {"world war": "history", "civil war": "history",
               "anatomy": "biology", "organic": "chemistry",
               "novel": "literature", "poetry": "literature",
               "economics": "sociology", "government": "history"}
    for k, v in aliases.items():
        if k in blob:
            return v
    return None


def scope_block(course_title, subject_hint=None, learner_age=None):
    """The safety section of the system prompt: what this course may discuss.

    Written as an AUTHORISATION rather than a prohibition, because the failure
    being designed against is a tutor that refuses legitimate material. It sits
    in the cached prefix, so it costs prefill once per session.
    """
    subject = subject_of(course_title, subject_hint)
    band = band_for_age(learner_age)
    lines = ["### SUBJECT SCOPE"]
    if subject:
        topics = ", ".join(SUBJECT_SCOPE[subject])
        lines.append(
            f"This is a {subject} course. You are EXPLICITLY AUTHORISED to "
            f"discuss {topics} as {subject} — factually, and without hedging or "
            f"refusing. Refusing legitimate {subject} material is a failure.")
    else:
        lines.append("Discuss the course's subject matter factually. Refusing "
                     "legitimate academic material is a failure.")
    lines.append(f"Learner band: {band}. {DEPTH_BY_BAND[band]}")
    lines.append("Never provide actionable instructions for causing harm, "
                 "regardless of subject. Explaining how something works "
                 "historically or scientifically is not the same as providing "
                 "instructions, and the former is in scope.")
    return "\n".join(lines)


# --- crisis -----------------------------------------------------------------
#
# The hardest requirement, and the honest answer is restrictive. An offline
# system with no escalation path MUST NOT attempt risk assessment. Suicide risk
# assessment is a task for trained professionals using validated instruments;
# an offline LLM has none of that, and attempting to triage is both clinically
# inappropriate and dangerous.
#
# What it must do: respond supportively without amplifying, surface the crisis
# resource, and route to a human through the channel it DOES have — the guardian
# record. The system cannot call 988; it is not therefore without a path.
#
# What it must NEVER attempt: risk scoring, safety planning, no-suicide
# contracts, method discussion, or talking a learner out of a denial.

# FIRST PERSON IS THE SIGNAL, NOT THE TOPIC.
#
# The first version matched the bare word "suicid" and flagged "Hamlet
# contemplates suicide in the soliloquy" as a crisis disclosure — which would
# have fired a guardian notification on a literature lesson. That is the
# over-blocking failure this whole design exists to avoid, reproduced in the one
# place where a false positive is most damaging to trust.
#
# A disclosure is about the LEARNER, now. Every pattern below requires
# first-person framing, so a history course can discuss mass death and a
# literature course can discuss Ophelia without either being mistaken for a
# child in danger.
_FIRST_PERSON = r"\b(?:i|i'?m|im|i'?ve|i have|i'?d|me|my)\b"

# Phrases that describe harm to the speaker. Kept separate from the first-person
# test so the two can be required TOGETHER but not adjacently — "I've been
# cutting myself" puts a word between them, and requiring adjacency missed it.
# A missed disclosure is the worse direction of error here, so the window is
# generous and the first-person requirement is what keeps precision.
_HARM_PHRASE = re.compile(
    r"(kill(?:ing)? myself|end(?:ing)? my life|end(?:ing)? it all"
    r"|want(?:ed)? to die|wanna die|going to die by"
    r"|cut(?:ting)? myself|harm(?:ing)? myself|hurt(?:ing)? myself"
    r"|better off dead|no reason to live"
    r"|don'?t want to be here anymore|don'?t want to exist"
    r"|thinking about suicide|think about suicide|attempted suicide)",
    re.IGNORECASE)

_FIRST_PERSON_RE = re.compile(_FIRST_PERSON, re.IGNORECASE)


def _is_first_person_disclosure(text, match):
    """Is the harm phrase about the speaker, now?

    "Hamlet contemplates suicide" and "I think about suicide" differ only in
    who the sentence is about, so that is what gets tested — within a window
    before the phrase, not immediately adjacent to it.
    """
    start = max(0, match.start() - 60)
    window = text[start:match.end()]
    return bool(_FIRST_PERSON_RE.search(window))


# Same rule: first person, about the learner, now.
_ABUSE = re.compile(
    r"((?:hits|beats|hurts) me\b|touched me\b|molested me\b"
    r"|abus(?:ed|ing) me\b|(?:i'?m |im )?afraid to go home"
    r"|(?:i'?m |im )?not safe at home)",
    re.IGNORECASE)

CRISIS_RESOURCE = ("If you're in the US you can call or text 988 any time to "
                   "reach the Suicide and Crisis Lifeline.")


def screen_disclosure(text):
    """Detect a disclosure that leaves the subject entirely.

    Deliberately a narrow keyword screen and deliberately NOT a risk assessment.
    Its job is to route, not to judge — the moment it tries to estimate severity
    it is doing something it is not competent to do.
    """
    t = text or ""
    m = _HARM_PHRASE.search(t)
    if m and _is_first_person_disclosure(t, m):
        return {"kind": "self_harm", "notify_guardian": True,
                "route": "guardian", "assess_risk": False}
    if _ABUSE.search(t):
        # The guardian may be the source of harm, so this routes differently:
        # to the platform's safeguarding contact rather than the parent record.
        return {"kind": "abuse", "notify_guardian": False,
                "route": "safeguarding", "assess_risk": False}
    return {"kind": None}


def crisis_response(kind):
    """Safe-messaging-compliant acknowledgement. Brief, and it stops there.

    Does not describe methods, does not sensationalise, does not attempt to
    counsel, does not ask assessment questions. Acknowledgement plus a resource
    is the ceiling of what this system can responsibly do.
    """
    if kind == "self_harm":
        return ("Thank you for telling me — that sounds really hard, and you "
                "deserve support from someone who can properly help. I'm a "
                "tutor, so I'm not the right person for this, but people who "
                "are trained for it are available right now. "
                + CRISIS_RESOURCE
                + " I'm going to let a trusted adult know you said this.")
    if kind == "abuse":
        return ("Thank you for telling me. What you've described is something "
                "an adult you trust needs to know about — a teacher, a school "
                "counsellor, or another safe adult. I'm a tutor and I can't "
                "help with this properly, but you should not have to deal with "
                "it on your own.")
    return ""


VISIBILITY_TIERS = {
    "learning": "Not surfaced to a guardian by default. Ordinary tutoring — "
                "questions, answers, grades and progress. This privacy is what "
                "makes a learner willing to be wrong in front of the tutor.",
    "safety": "Always surfaced. Crisis, self-harm and abuse disclosures. "
              "Published to learner and guardian in advance, so the limits of "
              "confidentiality are known rather than discovered.",
}
