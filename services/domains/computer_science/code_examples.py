"""Build the coding examples a concept needs, once, at build time.

WHY THIS IS AN ASSET AND NOT A TUTORING-TIME DECISION
-----------------------------------------------------
Three measured reasons.

**The tutor must not compose code mid-turn.** A model call costs 8-40s on this
hardware and turn latency is already the acute defect. Worse, improvised
material is unreliable: the domain benchmark caught the tutor contradicting the
coordinates of a figure IT HAD DRAWN two turns earlier, scoring 1/5 on accuracy.
Code invented under time pressure will fail the same way, and a wrong `select`
teaches a wrong `select`.

**The source already contains correct examples.** dbt's documentation carries
264 code blocks in 45 pages; a programming book carries more. An example lifted
from the source is correct by construction. One the model writes is a guess
about a tool it may have seen a different version of.

**Duplication is a whole-course problem.** Eight concepts each independently
deciding they want a "basic select" example is the code analogue of the
duplicate-figure problem `asset_arbiter` exists to solve. Deciding once, for the
course, is the only place that can be seen.

WHY BLANKS AND NOT "WHAT WOULD YOU TYPE"
----------------------------------------
Without execution the tutor cannot check free-typed code, so asking a student to
compose a statement produces plausible-but-broken answers that get affirmed —
the confident-bluffer failure, in SQL. A blank is different: the builder removes
a token it KNOWS, so the answer is stored alongside the question and the tutor
can actually mark it. That is the closest thing to verified practice available
without a sandbox.

WHAT IT REFUSES TO DO
---------------------
Produces nothing for concepts whose kind is not code-shaped, nothing when the
source chapter carries no code, and nothing when no safe blank can be chosen.
An example the tutor cannot check is worse than no example, because it looks
like practice and is not.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: Kinds of concept that are taught THROUGH code. Others (orientation,
#: mechanism, convention) are taught by reasoning and a code block would be
#: decoration — the `visual_policy` failure in a different costume.
CODE_KINDS = ("SYNTAX", "PROCEDURE", "DEBUGGING", "TOOLING")

#: A block shorter than this is a fragment (a bare command name, a single
#: identifier) and carries no structure to blank out.
MIN_CODE_CHARS = 40
#: Longer than this and the student is reading, not answering.
MAX_CODE_LINES = 24

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)

#: Tokens worth blanking: the ones that carry the lesson. Deliberately NOT
#: punctuation or keywords every language shares — blanking `select` teaches
#: nothing, blanking `ref` teaches what dbt actually does.
_BLANKABLE = re.compile(
    r"\b(ref|source|config|materialized|table|view|incremental|unique|"
    r"not_null|accepted_values|relationships|schema|alias|tags|depends_on|"
    r"is_incremental|this|target|var|env_var|run_query|dbt_utils)\b")

#: Never blank these: too generic to teach, or structural.
_NEVER = {"select", "from", "where", "and", "or", "as", "with", "on", "by",
          "the", "a", "an", "is", "in", "for", "to", "of"}


def _language_of(text, block_start):
    """The fence's language tag, or a guess from content."""
    m = re.search(r"```([a-zA-Z0-9_+-]+)\n", text[max(0, block_start - 30):block_start + 12])
    if m:
        return m.group(1).lower()
    return ""


def _guess_language(code):
    c = code.lower()
    if re.search(r"\bselect\b.*\bfrom\b", c, re.S):
        return "sql"
    if re.search(r"^\s*[\w-]+:\s", code, re.M):
        return "yaml"
    if re.search(r"\bdef \w+\(|import \w+", code):
        return "python"
    if re.search(r"^\s*(dbt|npm|pip|git|kubectl|terraform) ", code, re.M):
        return "shell"
    return ""


def blocks_in(text):
    """Fenced code blocks in a chapter, longest first.

    Longest first because the richest block usually shows the whole shape of a
    thing, and a one-line fragment rarely has a teachable blank in it.
    """
    out = []
    for m in _FENCE.finditer(text or ""):
        code = (m.group(1) or "").strip("\n")
        if len(code.strip()) < MIN_CODE_CHARS:
            continue
        if len(code.splitlines()) > MAX_CODE_LINES:
            continue
        out.append(code)
    return sorted(out, key=len, reverse=True)


def choose_blank(code):
    """One (line_index, token) worth removing, or None.

    Picks a token that carries the lesson rather than one every language shares.
    Returns None when nothing safe is found — an arbitrary blank produces a
    question with no teaching in it.
    """
    lines = code.splitlines()
    for i, line in enumerate(lines):
        for m in _BLANKABLE.finditer(line):
            tok = m.group(1)
            if tok.lower() in _NEVER:
                continue
            # Only blank a token that appears ONCE in the block, or the answer
            # is visible elsewhere and the question is free.
            if code.count(tok) == 1:
                return i, tok
    return None


#: The fading ladder. Evidence: faded worked examples beat complete ones on
#: both performance and instructional efficiency, and the effect REVERSES for
#: competent learners (expertise reversal) — so the rung must climb with
#: demonstrated competence rather than being fixed.
WORKED, FADED, INDEPENDENT = 0, 1, 2

#: Blanks per rung. Rung 0 shows the whole thing; rung 2 removes most of the
#: body and leaves a skeleton.
_BLANKS_AT = {WORKED: 0, FADED: 2, INDEPENDENT: 5}


def fade_level(established=0, correct_streak=0, bloom_level=1):
    """Which rung this learner is on, from what the FSM already tracks.

    Deliberately reads existing signals — `TurnState.established`, the FSM's
    correct-streak, Bloom — rather than inventing a new competence model. A
    second, disagreeing measure of the same thing is worse than none.
    """
    try:
        if correct_streak >= 3 or (bloom_level or 1) >= 4:
            return INDEPENDENT
        if established >= 1 or correct_streak >= 1:
            return FADED
        return WORKED
    except Exception:                        # pragma: no cover - defensive
        return WORKED


def choose_blanks(code, n):
    """Up to `n` (line_index, token) pairs worth removing, in line order."""
    if n <= 0:
        return []
    out, used = [], set()
    for i, line in enumerate(code.splitlines()):
        for m in _BLANKABLE.finditer(line):
            tok = m.group(1)
            if tok.lower() in _NEVER or tok in used:
                continue
            if code.count(tok) != 1:
                continue          # answer visible elsewhere; the blank is free
            out.append((i, tok))
            used.add(tok)
            if len(out) >= n:
                return out
    return out


def example_for(concept_title, kind, chapter_text, source_url=None,
                established=0, correct_streak=0, bloom_level=1, nth=0):
    """A `code` aid for this concept, or None.

    The returned dict is the aid the tutor renders, plus the ANSWER — which
    stays server-side so the tutor can mark the student rather than guess.
    """
    try:
        if kind not in CODE_KINDS:
            return None
        blocks = blocks_in(chapter_text)
        if not blocks:
            return None
        # DIFFERENT CONCEPTS GET DIFFERENT BLOCKS.
        #
        # This always took blocks[0], so every concept in a lesson received the
        # SAME example and the whole-course dedup then kept one and dropped the
        # rest. Measured: 189 concepts yielded 12 examples, and the cause was
        # this line rather than the classifier, which had already typed every
        # concept. A chapter with three code blocks can teach three concepts.
        code = blocks[nth % len(blocks)]
        lang = _guess_language(code)
        aid = {
            "kind": "code",
            "language": lang or "text",
            "title": concept_title[:80],
            "code": code,
            "source": source_url,
            # DEBUGGING wants the eye drawn to the suspect line rather than a
            # blank: the skill is knowing where to look, not recalling a token.
            "highlight": [],
            "blanks": [],
        }
        if kind == "DEBUGGING":
            aid["highlight"] = [0]
            aid["teaching_note"] = ("Show this as the BROKEN case. Ask what the "
                                    "student would check first, and why.")
            return aid
        rung = fade_level(established, correct_streak, bloom_level)
        chosen = choose_blanks(code, _BLANKS_AT[rung])
        aid["fade_level"] = rung
        if chosen:
            aid["blanks"] = [{"id": n, "line": i, "hint": "what goes here?",
                              "stage": 1}
                             for n, (i, _) in enumerate(chosen)]
            # ANSWERS STAY SERVER-SIDE. The builder removed tokens it read from
            # the source, so the tutor can MARK the reply instead of accepting
            # anything plausible — the only checkable practice available
            # without a sandbox.
            # Keyed by BLANK ID, not line number: two blanks on one line
            # collapsed into a single entry, so a two-blank question shipped
            # with one answer and the tutor could not mark the other.
            aid["answers"] = {str(n): tok for n, (_, tok) in enumerate(chosen)}
            aid["teaching_note"] = (
                f"Rung {rung}: show this with {len(chosen)} blank(s). You hold "
                f"the answers, so mark the student's reply rather than "
                f"accepting anything that sounds right.")
            return aid
        if rung == WORKED:
            aid["teaching_note"] = ("Rung 0: show this complete worked example "
                                    "and ask what ONE line of it does.")
            return aid
        # A correct example with no safe blank is still worth showing; it just
        # cannot be practice. Say so rather than inventing a blank.
        aid["teaching_note"] = ("Show this worked example and ask what one line "
                               "of it does. No blank was safe to remove here.")
        return aid
    except Exception:                        # pragma: no cover - defensive
        return None


def attach_to_course(course, book, classify_fn=None, status_callback=None):
    """Give every code-shaped concept a real example from its own chapter.

    Whole-course pass, like the figure phase, so the same example is not
    attached to eight concepts. Returns a tally.
    """
    from services.domains.computer_science.concept_kind import (
        classify as _default_classify)
    classify_fn = classify_fn or _default_classify

    seen_code, made, skipped, pairs_made = set(), 0, 0, 0
    for m in (course.get("modules") or []):
        for u in (m.get("units") or []):
            for lesson in (u.get("lessons") or []):
                ch_order = lesson.get("book_chapter")
                ch = book.chapter(ch_order) if ch_order is not None else None
                if ch is None:
                    continue
                for nth, concept in enumerate(lesson.get("concepts") or []):
                    title = (concept.get("title") or "").strip()
                    if not title:
                        skipped += 1
                        continue
                    # A kind set at build time by READING (classify.py) wins
                    # over a pattern guess made here: it saw the source text,
                    # this only sees the title.
                    kind = (concept.get("concept_kind")
                            or classify_fn(title, ch.text,
                                           concept.get("learning_objectives")))
                    aid = example_for(title, kind, ch.text,
                                      source_url=course.get("source_path"),
                                      nth=nth)
                    if not aid:
                        skipped += 1
                        continue
                    fingerprint = aid["code"][:200]
                    if fingerprint in seen_code:
                        skipped += 1      # already taught with this example
                        continue
                    seen_code.add(fingerprint)
                    concept["code_example"] = aid
                    concept["concept_kind"] = kind
                    made += 1

                    # ATTACH THE MINED PAIR TOO, AT BUILD TIME.
                    #
                    # A pair (a real error and its fix, code and its output) is
                    # the strongest Socratic move available without a sandbox,
                    # and it can only be mined from the SOURCE TEXT. A
                    # doc-sourced lesson stores page URLs, not page text — so
                    # at teaching time the tutor would have to re-fetch a page
                    # mid-turn: a network call on a machine where latency is
                    # already the defect, and impossible for a product that is
                    # meant to work offline.
                    #
                    # Mining here costs nothing extra (the chapter text is
                    # already in hand) and makes the pair available for every
                    # later turn of that concept, forever.
                    try:
                        from services.domains.computer_science import code_pairs
                        pair = code_pairs.best_pair(ch.text)
                        if pair:
                            concept["teaching_pair"] = {
                                "kind": pair["kind"],
                                "first": pair["first"][:900],
                                "second": pair["second"][:500],
                                "lang": pair["lang"],
                            }
                            pairs_made += 1
                    except Exception:        # a pair is a bonus, never a cost
                        pass
    tally = {"examples": made, "skipped": skipped,
             "teaching_pairs": pairs_made}
    if status_callback:
        try:
            status_callback(f"CODE:EXAMPLES:{made}:{skipped}")
        except Exception:
            pass
    logger.info(f"[CODE] attached {made} example(s) and {pairs_made} teaching pair(s), skipped {skipped}")
    return tally
