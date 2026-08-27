"""Turn a concept's markdown into scheduled review items.

WHY THIS IS EXTRACTION AND NOT GENERATION
-----------------------------------------
The hydrator already writes, for every concept, a Key Facts list, a
Misconceptions list as Belief/Correction pairs, Edge Cases, and Socratic Hooks
banded by Bloom level. Those are an item bank in all but name: 2,091 items came
out of the 186 concepts on disk here without a single model call. Review-time
latency on this hardware is ~47s a turn, so anything that needs a model at
review time cannot be in the daily queue at all; keeping extraction mechanical
also means the item bank is reproducible and diffable.

WHY THE MIX MATTERS MORE THAN THE ROUTING
-----------------------------------------
The obvious design is to route by the concept's Bloom target: low-Bloom concepts
get flashcards, high-Bloom concepts get Socratic dialogue. The evidence says
that is the wrong shape. Agarwal found that practising factual questions and
then testing higher-order understanding performs no better than no practice at
all, and — the part that decides this design — that quizzes mixing factual AND
higher-order questions beat quizzes that are purely one or the other, measured
on higher-order outcomes. So every concept yields items at several tiers, and
the concept's Bloom target shifts the RATIO rather than picking a lane.

  https://files.eric.ed.gov/fulltext/EJ1327865.pdf
  https://notes.andymatuschak.org/Retrieval_practice_and_transfer_learning
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional

# Item kinds, cheapest first. The daily queue is mostly the cheap ones by TIME,
# but must not be mostly cheap ones by CONTENT — see the module docstring.
RECALL = "recall"            # cloze / short definition, self-rated
DISCRIMINATE = "discriminate"  # true-or-false against a known misconception, objective
APPLY = "apply"              # predict-the-behaviour, self-checked against criteria
SOCRATIC = "socratic"        # open question, graded by the tutor against a rubric

KINDS = (RECALL, DISCRIMINATE, APPLY, SOCRATIC)

_CODE = re.compile(r"`([^`]+)`")
_LABELLED = re.compile(r"^\*\*(?P<label>[^*]+?)\*\*\s*:\s*(?P<body>.+)$", re.S)
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_HOOK_BAND = re.compile(
    r"^[-*]\s*\*{0,2}Bloom\s*(?P<lo>\d)\s*[-–]\s*(?P<hi>\d)\*{0,2}\s*:?\s*(?P<q>.+)$",
    re.I | re.S)
_BELIEF = re.compile(
    r"\*\*Belief\*\*\s*:\s*(?P<belief>.*?)\s*\*\*Correction\*\*\s*:\s*(?P<fix>.*?)"
    r"(?=\n\s*[-*]\s*\*\*Belief\*\*|\Z)", re.S)


@dataclass
class ReviewItem:
    """One thing FSRS will schedule. `uid` is derived from the content so that
    re-extracting a concept keeps a learner's history instead of orphaning it."""
    uid: str
    concept_uid: str
    course_uid: str
    kind: str
    front: str
    back: str
    bloom: int = 2
    source_section: str = ""
    payload: Dict = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return asdict(self)


def _uid(concept_uid: str, kind: str, seed: str) -> str:
    """Stable across re-extraction: same concept + kind + source text -> same id.

    Editing a fact's wording DOES mint a new item, which is correct — the
    question changed, so its recall history no longer describes it."""
    h = hashlib.sha1(f"{concept_uid}|{kind}|{seed}".encode("utf-8")).hexdigest()
    return f"itm_{h[:12]}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def section(md: str, name: str) -> str:
    """The body of one '## Section', or '' when the hydrator omitted it."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)", md or "",
                  re.M | re.S)
    return m.group(1) if m else ""


def bullets(body: str) -> List[str]:
    """Bullets, joining hanging continuation lines onto their bullet."""
    out: List[str] = []
    for raw in (body or "").splitlines():
        m = _BULLET.match(raw.strip())
        if m:
            out.append(m.group(1).strip())
        elif out and raw.strip() and not raw.startswith("#"):
            out[-1] += " " + raw.strip()
    return [_clean(b) for b in out if _clean(b)]


def title_of(md: str) -> str:
    m = re.search(r"^#\s+(.+)$", md or "", re.M)
    return _clean(m.group(1)) if m else ""


def bloom_of(md: str, default: int = 2) -> int:
    m = re.search(r"\*\*Bloom Target\*\*\s*:\s*(\d)", md or "")
    return int(m.group(1)) if m else default


# --------------------------------------------------------------------------
# Key Facts -> recall items
# --------------------------------------------------------------------------

def _cloze(fact: str) -> Optional[tuple]:
    """Blank the most informative inline `code` span.

    Code spans are the right blank for this corpus: 70% of facts carry one, and
    they are exactly the token whose recall is worth testing (`NULLIF(a, b)`,
    `(?=...)`). All occurrences of the chosen span are blanked together, as in
    a standard cloze, so the answer is not visible elsewhere in the sentence.
    """
    spans = _CODE.findall(fact)
    if not spans:
        return None
    answer = max(spans, key=len)
    if len(answer) < 2:
        return None
    front = fact.replace(f"`{answer}`", "`[ ... ]`")
    if front == fact:
        return None
    return front, answer


def facts_to_items(md: str, concept_uid: str, course_uid: str,
                   title: str, bloom: int) -> List[ReviewItem]:
    items: List[ReviewItem] = []
    for fact in bullets(section(md, "Key Facts")):
        lab = _LABELLED.match(fact)
        if lab:
            # "**Grouping**: Quantifiers apply to the preceding atom." asks
            # itself: the label is the question, the body is the answer.
            label, body = _clean(lab.group("label")), _clean(lab.group("body"))
            front = f"{title} — what is the rule for **{label}**?"
            back = body
        else:
            cz = _cloze(fact)
            if not cz:
                # A plain sentence with nothing safe to blank still works as a
                # prompted recall: state it, then check yourself.
                front = f"{title} — recall this point:"
                back = fact
            else:
                front, answer = cz
                back = f"`{answer}`"
        items.append(ReviewItem(
            uid=_uid(concept_uid, RECALL, fact), concept_uid=concept_uid,
            course_uid=course_uid, kind=RECALL, front=front, back=back,
            bloom=min(bloom, 2), source_section="Key Facts",
            payload={"fact": fact}))
    return items


# --------------------------------------------------------------------------
# Misconceptions (+ Key Facts) -> objective true/false discrimination
# --------------------------------------------------------------------------

def discrimination_items(md: str, concept_uid: str, course_uid: str,
                         title: str, bloom: int) -> List[ReviewItem]:
    """True-or-false against the misconceptions the author wrote down.

    Every Belief is false by construction, so a queue built only from them
    teaches the pattern "always false" rather than the content. Each concept's
    own Key Facts supply the true statements, giving a balanced two-alternative
    choice over exactly the confusions the author thought were likely.
    """
    pairs = [(_clean(m.group("belief")), _clean(m.group("fix")))
             for m in _BELIEF.finditer(section(md, "Misconceptions"))]
    items: List[ReviewItem] = []
    for belief, fix in pairs:
        if not belief or not fix:
            continue
        items.append(ReviewItem(
            uid=_uid(concept_uid, DISCRIMINATE, belief), concept_uid=concept_uid,
            course_uid=course_uid, kind=DISCRIMINATE,
            front=belief, back=fix, bloom=max(bloom, 3),
            source_section="Misconceptions",
            payload={"truth": False, "statement": belief, "explanation": fix}))

    # Balance the class with true statements from the same concept.
    facts = [f for f in bullets(section(md, "Key Facts")) if len(f) > 30]
    for fact in facts[:len(pairs)]:
        statement = _clean(_LABELLED.sub(lambda m: m.group("body"), fact))
        items.append(ReviewItem(
            uid=_uid(concept_uid, DISCRIMINATE, "T:" + fact),
            concept_uid=concept_uid, course_uid=course_uid, kind=DISCRIMINATE,
            front=statement, back="This one is accurate as stated.",
            bloom=max(bloom, 3), source_section="Key Facts",
            payload={"truth": True, "statement": statement,
                     "explanation": "This one is accurate as stated."}))
    return items


# --------------------------------------------------------------------------
# Socratic Hooks + Edge Cases -> apply / socratic
# --------------------------------------------------------------------------

def _self_check(md: str) -> str:
    """What an open question is marked against when no tutor is in the loop.

    Hooks are questions with no written answer, so an honest open item reveals
    the author's own bar — the Mastery Criteria — instead of pretending to
    have a key."""
    # Keep the bullet structure. _clean collapses every newline, which turned
    # a criteria list into one run-on sentence where the learner most needs to
    # scan it point by point.
    crit = (section(md, "Mastery Criteria") or "").strip()
    if crit:
        lines = [ln.rstrip() for ln in crit.splitlines() if ln.strip()]
        return "\n".join(lines)
    facts = bullets(section(md, "Key Facts"))[:3]
    return " ".join(facts) if facts else ""


def hook_items(md: str, concept_uid: str, course_uid: str,
               title: str, bloom: int) -> List[ReviewItem]:
    items: List[ReviewItem] = []
    check = _self_check(md)
    for raw in (section(md, "Socratic Hooks") or "").splitlines():
        m = _HOOK_BAND.match(raw.strip())
        if not m:
            continue
        lo, hi, q = int(m.group("lo")), int(m.group("hi")), _clean(m.group("q"))
        if not q:
            continue
        if hi <= 2:
            kind, k_bloom = RECALL, 2
        elif hi <= 4:
            kind, k_bloom = APPLY, 4
        else:
            kind, k_bloom = SOCRATIC, 5
        items.append(ReviewItem(
            uid=_uid(concept_uid, kind, q), concept_uid=concept_uid,
            course_uid=course_uid, kind=kind, front=q,
            back=check or "Compare your answer with the concept notes.",
            bloom=k_bloom, source_section="Socratic Hooks",
            payload={"band": [lo, hi], "rubric": check}))
    return items


def edge_case_items(md: str, concept_uid: str, course_uid: str,
                    title: str, bloom: int) -> List[ReviewItem]:
    """Edge cases are elaborated retrieval: the same knowledge on a different
    example, which is the form the transfer literature actually supports."""
    items: List[ReviewItem] = []
    for edge in bullets(section(md, "Edge Cases & Limitations")):
        lab = _LABELLED.match(edge)
        if lab:
            label, body = _clean(lab.group("label")), _clean(lab.group("body"))
            front = f"{title} — what happens in this case: **{label}**?"
            back = body
        else:
            front = f"{title} — what is the limitation here?"
            back = edge
        items.append(ReviewItem(
            uid=_uid(concept_uid, APPLY, edge), concept_uid=concept_uid,
            course_uid=course_uid, kind=APPLY, front=front, back=back,
            bloom=max(bloom, 3), source_section="Edge Cases & Limitations",
            payload={}))
    return items


# --------------------------------------------------------------------------

def prerequisite_titles(md: str) -> List[str]:
    """The concepts this one is built on, as the hydrator recorded them.

    Queue priority uses this: re-drilling a dependent while the thing it rests
    on is lapsed spends the learner's attention on the wrong concept.
    """
    body = section(md, "Prerequisites")
    m = re.search(r"Prior concepts?\s*:\s*(.+)", body, re.S)
    if not m:
        return []
    raw = _clean(m.group(1))
    return [t.strip(" .") for t in raw.split(",") if t.strip(" .")]


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`])")
_DEFINITION = re.compile(r"\*\*Definitions?\.?\*\*\s*(?P<body>.+?)(?=\n\n|\Z)", re.S)
_MAX_PROSE_ITEMS = 8


def prose_items(md: str, concept_uid: str, course_uid: str,
                title: str, bloom: int) -> List[ReviewItem]:
    """Items from a concept written as prose, with none of the usual sections.

    Not every hydrator writes Key Facts and Socratic Hooks — one course on disk
    here is well-written prose with a single '## Worked Example', and the
    section-driven extractor returned nothing at all for it. A concept with real
    teaching in it must not yield an empty item bank just because it is shaped
    differently, so this falls back to the two things prose reliably has: a
    bolded definition, and sentences whose load-bearing token is in `code`.
    """
    items: List[ReviewItem] = []

    d = _DEFINITION.search(md)
    if d:
        body = _clean(d.group("body"))
        if len(body) > 40:
            items.append(ReviewItem(
                uid=_uid(concept_uid, RECALL, "def:" + body),
                concept_uid=concept_uid, course_uid=course_uid, kind=RECALL,
                front=f"{title} — state the definition.", back=body,
                bloom=min(bloom, 2), source_section="Definition",
                payload={"fallback": True}))

    body = re.sub(r"^#.*$", "", md, flags=re.M)
    body = re.sub(r"```.*?```", " ", body, flags=re.S)      # never cloze a code block
    seen = set()
    for sentence in _SENTENCE.split(body):
        if len(items) >= _MAX_PROSE_ITEMS:
            break
        sentence = _clean(sentence)
        if not (60 <= len(sentence) <= 320):
            continue
        if sentence.startswith(("-", "*", ">", "|")):
            continue
        cz = _cloze(sentence)
        if not cz:
            continue
        front, answer = cz
        if answer in seen:
            continue
        seen.add(answer)
        items.append(ReviewItem(
            uid=_uid(concept_uid, RECALL, sentence), concept_uid=concept_uid,
            course_uid=course_uid, kind=RECALL, front=front, back=f"`{answer}`",
            bloom=min(bloom, 2), source_section="Prose",
            payload={"fallback": True}))
    return items


def extract(md: str, concept_uid: str, course_uid: str) -> List[ReviewItem]:
    """Every item a single concept yields. Deterministic and model-free."""
    if not md or not md.strip():
        return []
    title = title_of(md) or "This concept"
    bloom = bloom_of(md)
    out: List[ReviewItem] = []
    out += facts_to_items(md, concept_uid, course_uid, title, bloom)
    out += discrimination_items(md, concept_uid, course_uid, title, bloom)
    out += hook_items(md, concept_uid, course_uid, title, bloom)
    out += edge_case_items(md, concept_uid, course_uid, title, bloom)

    # Only when the structured sections yielded nothing: the fallback is a
    # weaker item source and should never dilute a properly hydrated concept.
    if not out:
        out += prose_items(md, concept_uid, course_uid, title, bloom)

    seen, unique = set(), []
    for it in out:
        if it.uid in seen:
            continue
        seen.add(it.uid)
        unique.append(it)
    return unique


def mix_summary(items: Iterable[ReviewItem]) -> Dict[str, int]:
    counts = {k: 0 for k in KINDS}
    for it in items:
        counts[it.kind] = counts.get(it.kind, 0) + 1
    return counts
