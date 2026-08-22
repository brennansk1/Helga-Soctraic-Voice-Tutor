"""Teachable MOVES mined from historical source material.

THE CONSTRAINT THIS SERVES
--------------------------
History's version of "do not make them solve" points the other way: **never ask
a learner to guess a contingent fact.** You cannot elicit that Hastings was
1066, and asking is a quiz with the answer withheld.

So the reasoning has to live somewhere else, and Wineburg's Stanford History
Education Group named the somewhere: the skills unique to the historian's work
are SOURCING, CONTEXTUALIZATION and CORROBORATION. All three are answerable
from material in front of the learner, need no recall, and have a checkable
answer in the source itself.

  SOURCE_CHECK    an extract WITH its provenance. Ask what the author's
                  position makes them likely to emphasise or leave out.
                  Sourcing is the first thing a historian does and the thing
                  students most reliably skip — Wineburg's original finding was
                  that working historians read the attribution first and
                  students read it last, if at all.

  CORROBORATE     two accounts of the same event that DIFFER. Ask where they
                  agree and where they diverge, before any question of who is
                  right. Comparison is what makes an account visible AS an
                  account rather than as the past itself.

  HISTORIOGRAPHY  two NAMED historians taking different positions. Ask what
                  turns on the disagreement — what would have to be true for
                  one reading to hold. Historical debate "is not about
                  identifying the correct answer, but about evidence,
                  interpretation, and framing".

  COUNTERFACTUAL  a cause, and what is claimed to depend on it. Ask which
                  cause, if absent, would most likely have changed the outcome.
                  This is how causal weighting is argued about, and weighting
                  is where historians actually disagree.

WHY TWO NAMED POSITIONS, AND NOT ONE HEDGE
------------------------------------------
The benchmark dimension this domain is scored on penalises BOTH flattening a
live debate AND inventing one. A module that hedges everything scores no better
than one that settles everything.

So a HISTORIOGRAPHY move requires **two attributed positions** in the source.
"Some historians argue" is not evidence of a live debate — it is a construction
that appears just as readily in front of a settled question. Two named people
disagreeing in the text is evidence; a hedge is not.

WHAT IT REFUSES
---------------
An extract with no provenance is a quotation, not a source, and cannot be
sourced — the whole move is about the attribution. Two accounts that do not
actually differ are not a corroboration exercise. And a single named historian
is not a debate.
"""
import logging
import re

logger = logging.getLogger(__name__)

SOURCE_CHECK = "SOURCE_CHECK"
CORROBORATE = "CORROBORATE"
HISTORIOGRAPHY = "HISTORIOGRAPHY"
COUNTERFACTUAL = "COUNTERFACTUAL"

#: A labelled source box, as history textbooks actually print them.
_SOURCE_HEAD = re.compile(
    r"(?:^|\n)\s*source\s*([A-Z]|\d+)\s*[.:—-]?\s*", re.I)

#: Provenance: who, when, to whom. Without this an extract cannot be sourced.
_PROVENANCE = re.compile(
    r"("
    r"\b(letter|diary|despatch|dispatch|memoir|speech|telegram|report|"
    r"editorial|proclamation|treaty|minutes)\b[^.]{0,80}\b(from|by|of)\b"
    r"|\bwritten (by|in)\b"
    r"|\bspeaking (to|in)\b"
    r"|\b(1[0-9]{3}|20[0-2][0-9])\b[^.]{0,30}\b(to|by|from)\b"
    r"|\b(by|from)\b[^.]{0,40}\b(1[0-9]{3}|20[0-2][0-9])\b"
    r")", re.I)

#: A NAMED historian taking a position. The name is the point: an attributed
#: claim can be weighed, an unattributed one can only be believed.
_HISTORIAN = re.compile(
    r"\b([A-Z][a-z]{2,})\s+"
    r"(argues?|argued|contends?|contended|maintains?|maintained|claims?|"
    r"claimed|suggests?|suggested|has argued|concluded)\b")

#: The hedge that looks like a debate and is not.
_HEDGE = re.compile(
    r"\b(some|many|most|certain)\s+(historians?|scholars?|writers?)\b", re.I)

MIN_CHARS = 60
MAX_CHARS = 1200


def _trim(text, cap=MAX_CHARS):
    return re.sub(r"\s+", " ", (text or "")).strip()[:cap]


def sources_in_text(text, limit=8):
    """Labelled sources WITH provenance, in order.

    Each is {label, provenance, extract}. A source box whose attribution is
    missing is skipped: sourcing is a question about the attribution, so an
    extract without one cannot support the move at all.
    """
    body = text or ""
    out = []
    heads = list(_SOURCE_HEAD.finditer(body))
    for i, head in enumerate(heads):
        start = head.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else min(
            len(body), start + 2 * MAX_CHARS)
        block = body[start:end].strip()
        if len(block) < MIN_CHARS:
            continue
        prov = _PROVENANCE.search(block)
        if not prov:
            continue                      # a quotation, not a source
        out.append({
            "label": head.group(1),
            "provenance": _trim(prov.group(0), 200),
            "extract": _trim(block, MAX_CHARS),
        })
        if len(out) >= limit:
            break
    return out


def historians_in_text(text, limit=6):
    """Named historians taking positions, deduplicated by name.

    A hedge ("some historians argue") is deliberately NOT counted: it appears
    just as readily in front of a settled question, so it is not evidence that
    a debate is live.
    """
    out, seen = [], set()
    for m in _HISTORIAN.finditer(text or ""):
        name = m.group(1)
        if name.lower() in ("this", "that", "there", "these", "history",
                            "britain", "germany", "france", "russia"):
            continue
        if name in seen:
            continue
        sentence = _sentence_around(text, m.start())
        if len(sentence) < 40:
            continue
        seen.add(name)
        out.append({"historian": name, "position": _trim(sentence, 400)})
        if len(out) >= limit:
            break
    return out


def _sentence_around(text, index):
    body = text or ""
    start = max(body.rfind(".", 0, index), body.rfind("\n", 0, index)) + 1
    end = body.find(".", index)
    return body[start:end + 1 if end != -1 else len(body)].strip()


def from_text(text):
    """Teachable moves found in a chapter, best first.

    Ordered by how much the move needs material that cannot be invented: a
    real attributed disagreement is the scarcest and least fakeable, a source
    with provenance next.
    """
    out = []
    sources = sources_in_text(text)
    historians = historians_in_text(text)

    if len(historians) >= 2:
        a, b = historians[0], historians[1]
        out.append({
            "kind": HISTORIOGRAPHY,
            "first": f"{a['historian']}: {a['position']}",
            "second": f"{b['historian']}: {b['position']}",
        })

    for src in sources[:2]:
        out.append({
            "kind": SOURCE_CHECK,
            "first": f"[Source {src['label']} — {src['provenance']}]\n"
                     f"{src['extract']}",
            "second": "",
        })

    if len(sources) >= 2:
        out.append({
            "kind": CORROBORATE,
            "first": f"[Source {sources[0]['label']} — "
                     f"{sources[0]['provenance']}]\n{sources[0]['extract']}",
            "second": f"[Source {sources[1]['label']} — "
                      f"{sources[1]['provenance']}]\n{sources[1]['extract']}",
        })

    rank = {HISTORIOGRAPHY: 0, SOURCE_CHECK: 1, CORROBORATE: 2,
            COUNTERFACTUAL: 3}
    out.sort(key=lambda m: rank.get(m["kind"], 9))
    return out


#: Which move suits WHICH LEARNER. Mirrors the mathematics domain, where
#: choosing material from the concept alone was found to be a script by
#: construction — the same concept producing the same turn whoever is present.
_MOVE_FOR_BEHAVIOUR = {
    "BLUFFING": (SOURCE_CHECK, CORROBORATE, HISTORIOGRAPHY),
    "GIVING_UP": (SOURCE_CHECK, COUNTERFACTUAL, HISTORIOGRAPHY),
    "TERSE": (SOURCE_CHECK, COUNTERFACTUAL, CORROBORATE),
    "HEDGING": (CORROBORATE, HISTORIOGRAPHY, SOURCE_CHECK),
    "AHEAD": (HISTORIOGRAPHY, CORROBORATE, COUNTERFACTUAL),
}


def best_move(moves, kind=None, behaviour=None):
    """The most teachable move for THIS learner, or None."""
    found = [m for m in (moves or []) if isinstance(m, dict)]
    if kind:
        found = [m for m in found if m.get("kind") == kind]
    if not found:
        return None
    order = _MOVE_FOR_BEHAVIOUR.get((behaviour or "").upper())
    if order:
        for want in order:
            for m in found:
                if m.get("kind") == want:
                    return m
    return found[0]


def choose_move(stored, behaviour=None):
    """Pick the stored move that suits this learner, or the default.

    Never raises: a choice failure falls back to the stored default rather than
    costing the turn its material.
    """
    try:
        if not isinstance(stored, dict):
            return stored
        alts = [a for a in (stored.get("alternatives") or [])
                if isinstance(a, dict)]
        if not alts or not behaviour:
            return stored
        return best_move([stored] + alts, behaviour=behaviour) or stored
    except Exception:                    # pragma: no cover - defensive
        return stored


def prompt_block(move, beginner=False):
    """The tutor instruction for a mined move, or "".

    Imperative, with the material inline. Measured on the computer-science
    domain: DESCRIBED material was used in 0 of 4 turns and INSTRUCTED material
    in 4 of 4.
    """
    if not move or not isinstance(move, dict):
        return ""
    kind = move.get("kind")
    first = move.get("first") or ""
    second = move.get("second") or ""

    if kind == HISTORIOGRAPHY:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE. You have TWO "
            "NAMED historians disagreeing in the source, which is real "
            "evidence that this question is live — use them rather than "
            "describing a debate in your own words.\n"
            "THIS TURN: SET OUT BOTH POSITIONS, THEN ASK WHAT TURNS ON THE "
            "DISAGREEMENT.\n"
            f"POSITION 1:\n{first}\n"
            f"POSITION 2:\n{second}\n"
            "Then ask ONE question: what would have to be true for one of "
            "these readings to be the better one. Do NOT resolve it, do NOT "
            "give your own verdict, and do NOT ask the learner which is "
            "correct — there is no settled answer and implying there is, is "
            "the failure this move exists to prevent.")

    if kind == SOURCE_CHECK:
        hint = ("The learner is new to this, so point at the attribution "
                "explicitly before asking — say who wrote it and when, in "
                "your own words, so the question has something to bite on.\n"
                if beginner else "")
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have a REAL "
            "source with its provenance, so use it rather than inventing one.\n"
            "THIS TURN: SHOW THE SOURCE WITH ITS ATTRIBUTION, THEN ASK WHAT "
            "THE AUTHOR'S POSITION MAKES THEM EMPHASISE OR OMIT.\n"
            + hint +
            f"{first}\n"
            "Then ask ONE question about the AUTHOR rather than the events: "
            "given who wrote this, when, and for whom, what would you expect "
            "them to stress and what to leave out? That is answerable from "
            "the attribution alone — the learner needs no outside knowledge "
            "and must not be asked for any.")

    if kind == CORROBORATE:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have TWO "
            "real sources on the same events, so use them.\n"
            "THIS TURN: SHOW BOTH, THEN ASK WHERE THEY AGREE AND WHERE THEY "
            "DIVERGE.\n"
            f"{first}\n\n{second}\n"
            "Then ask ONE question about the DIFFERENCE, not about which is "
            "true: what do both accept, and what does one say that the other "
            "does not. Whether either is right comes later and is not this "
            "turn's question.")

    if kind == COUNTERFACTUAL:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have real "
            "material from the source, so use it.\n"
            "THIS TURN: SET OUT THE CAUSES AS THE SOURCE GIVES THEM, THEN ASK "
            "WHICH ONE MATTERS MOST.\n"
            f"{first}\n"
            "Then ask ONE question: which of these, if it had been absent, "
            "would most likely have changed the outcome — and why. Do not "
            "supply your own ranking first.")
    return ""
