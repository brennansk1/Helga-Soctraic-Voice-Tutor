"""Teachable PAIRS mined from source material: error→fix, code→output, before→after.

THE CONSTRAINT THIS SERVES
--------------------------
Teach programming Socratically WITHOUT the learner typing code and WITHOUT a
sandbox. That rules out "write a function that…", because nothing can check the
answer, and an unchecked answer that sounds right is the confident-bluffer
failure in SQL.

What remains is everything the tutor can SHOW and then VERIFY from the source:

  ERROR_FIX     a real error message, and the change that resolves it.
                Ask "what would you check first?" before revealing. The skill
                is the order of investigation, and erroneous examples are a
                studied technique in their own right rather than a lesser
                version of worked examples.

  CODE_OUTPUT   a command or snippet, and what it prints. Ask the learner to
                PREDICT the output, then reveal it. This is the only technique
                that gives a verified answer with no execution, because the
                documentation already contains the result.

  BEFORE_AFTER  two versions of a config or snippet. Ask what changed and why
                it matters — comparison without composition.

WHY MINED AND NOT GENERATED
---------------------------
A model asked to invent an error message invents a plausible one, and a
plausible-but-wrong error teaches a wrong diagnosis. Documentation errors are
real: `dbt0101: no viable alternative at input '(    )'` came out of dbt's own
static-analysis page, next to the YAML that fixes it. Mined pairs are correct by
construction; generated ones are a guess about a version of the tool the model
may never have seen.

WHAT IT REFUSES
---------------
Returns nothing rather than a weak pair. A "pair" that is really two unrelated
config blocks teaches nothing and costs a turn, and the tutor cannot tell the
difference once it is in the prompt.
"""
import logging
import re

logger = logging.getLogger(__name__)

ERROR_FIX = "ERROR_FIX"
CODE_OUTPUT = "CODE_OUTPUT"
BEFORE_AFTER = "BEFORE_AFTER"

_FENCE = re.compile(r"```([a-zA-Z0-9_+#.-]*)\n(.*?)\n```", re.S)

#: A block that IS an error. Deliberately strict: an error is the most
#: valuable pair and the most damaging to fake, so a maybe is a no.
_ERROR = re.compile(
    r"("
    r"^\s*\w+\d{3,}\b"                       # dbt0101, TS2345, E0602
    r"|\berror\b\s*[:\[]"                    # Error: / Error [
    r"|\bexception\b\s*[:\[]"
    r"|\btraceback \(most recent call last\)"
    r"|\bfatal\b\s*[:\[]"
    r"|\bcompilation error\b"
    r"|\bsyntax error\b"
    r"|\bfailed\b.{0,40}\bbecause\b"
    r"|^\s*[Ee]rror\s"
    r")", re.M)

#: A block that is program OUTPUT rather than program SOURCE.
_OUTPUT_HINT = re.compile(
    r"("
    r"^\s*\d{2}:\d{2}:\d{2}\b"               # 14:03:22  log timestamps
    r"|\bcompleted successfully\b"
    r"|\b(PASS|FAIL|OK|WARN|ERROR)=\d+"      # dbt run summaries
    r"|^\s*Finished running\b"
    r"|^\s*Done\.\s*$"
    r"|^\s*\|.*\|\s*$"                       # ascii tables
    r"|^\s*\+[-+]+\+\s*$"
    r"|\brows? (in set|affected)\b"
    r")", re.M | re.I)

#: A block that is clearly SOURCE, not output. Used to reject false pairs.
_SOURCE_HINT = re.compile(
    r"\b(select\s|def\s|class\s|import\s|from\s+\w+\s+import|function\s|"
    r"const\s|let\s|var\s|public\s|private\s|#include|package\s|module\s|"
    r"CREATE\s+(TABLE|VIEW)|WITH\s+\w+\s+AS)\b", re.I)

#: A command invocation — the left half of a code→output pair.
_COMMAND = re.compile(
    r"^\s*\$?\s*(dbt|npm|npx|pip|python|git|docker|kubectl|terraform|cargo|"
    r"go|yarn|pnpm|make|psql|mysql|curl)\b", re.M)

MIN_BLOCK = 20
MAX_BLOCK = 1400


def _blocks(text):
    out = []
    for m in _FENCE.finditer(text or ""):
        lang, body = m.group(1).lower(), m.group(2)
        if MIN_BLOCK <= len(body) <= MAX_BLOCK:
            out.append({"lang": lang, "code": body, "at": m.start()})
    return out


def _is_error(b):
    return bool(_ERROR.search(b["code"]))


def _is_output(b):
    if _SOURCE_HINT.search(b["code"]):
        return False
    return bool(_OUTPUT_HINT.search(b["code"]))


def _similar(a, b):
    """Are two blocks two versions of the same thing?

    Line-set overlap rather than a diff: a before/after pair shares most of its
    lines and differs in a few, which is exactly what makes it teachable.
    """
    la = {l.strip() for l in a.splitlines() if l.strip()}
    lb = {l.strip() for l in b.splitlines() if l.strip()}
    if not la or not lb:
        return 0.0
    return len(la & lb) / max(len(la), len(lb))


def pairs_in(text, limit=4):
    """Teachable pairs found in `text`, best kind first.

    Each pair is {kind, first, second, lang, prompt} where `prompt` is the
    question the tutor should ask BEFORE revealing `second`.
    """
    blocks = _blocks(text)
    out = []
    for i in range(len(blocks) - 1):
        a, b = blocks[i], blocks[i + 1]
        gap = b["at"] - a["at"]
        if gap > 3000:
            continue                          # too far apart to be a pair

        if _is_error(a) and not _is_error(b):
            out.append({
                "kind": ERROR_FIX, "first": a["code"], "second": b["code"],
                "lang": b["lang"] or a["lang"],
                "prompt": ("Show the error and ask what the learner would "
                           "CHECK FIRST, and why. Reveal the fix only after "
                           "they commit to somewhere to look."),
            })
        elif _is_output(b) and not _is_output(a):
            out.append({
                "kind": CODE_OUTPUT, "first": a["code"], "second": b["code"],
                "lang": a["lang"],
                "prompt": ("Show the code and ask the learner to PREDICT what "
                           "it prints, in their own words. Then reveal the "
                           "real output and ask what surprised them."),
            })
        elif 0.45 <= _similar(a["code"], b["code"]) < 0.98:
            out.append({
                "kind": BEFORE_AFTER, "first": a["code"], "second": b["code"],
                "lang": a["lang"] or b["lang"],
                "prompt": ("Show both versions and ask what CHANGED and why "
                           "that change matters. Do not name the difference."),
            })
        if len(out) >= limit:
            break

    # Errors first: a real error is the scarcest and most teachable material
    # here, and the one a learner cannot get from reading alone.
    rank = {ERROR_FIX: 0, CODE_OUTPUT: 1, BEFORE_AFTER: 2}
    out.sort(key=lambda p: rank.get(p["kind"], 9))
    return out[:limit]


def best_pair(text, kind=None):
    """The single most teachable pair, or None."""
    found = pairs_in(text)
    if kind:
        found = [p for p in found if p["kind"] == kind]
    return found[0] if found else None


def prompt_block(pair):
    """The tutor instruction for a mined pair, or "".

    WHY THIS IS AN INSTRUCTION AND NOT AN OFFER.
    Measured: four of five generated turns had a real ERROR_FIX pair in the
    prompt and NONE of them showed the error. The material was described to the
    model ("use this, do not invent code") and the model ignored it, because a
    described resource competes with everything else in a 2000-token prompt and
    loses.

    This repository has measured instruction at 5/5 against description at 0/5
    before, on grade-band register. Same fix: say what to DO, first, in the
    imperative, with the material inline and the turn's shape spelled out —
    rather than supplying material and hoping.
    """
    if not pair:
        return ""
    first = (pair.get("first") or "")[:600]
    second = (pair.get("second") or "")[:300]
    lang = pair.get("lang") or ""
    kind = pair.get("kind")

    if kind == ERROR_FIX:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE. You have a REAL "
            "error from the source material, so use it rather than describing "
            "a broken case in your own words.\n"
            "THIS TURN: SHOW THE ERROR BELOW, THEN ASK WHERE THEY WOULD LOOK.\n"
            "Open by putting this exact error in a ```" + lang + " code block. "
            "Do not paraphrase it and do not explain it yet.\n"
            "```" + lang + "\n" + first + "\n```\n"
            "Then ask ONE question: what would they check FIRST, and why. "
            "Do not reveal the cause or the fix in this turn — the skill being "
            "taught is the order of investigation, and telling them removes the "
            "whole lesson. The fix, for your reference only, is:\n"
            "```\n" + second + "\n```")

    if kind == CODE_OUTPUT:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you "
            "have real material from the source, so use it.\\n"
            "THIS TURN: SHOW THE CODE BELOW AND ASK THEM TO PREDICT ITS "
            "OUTPUT.\n"
            "Open by putting this exact code in a ```" + lang + " code block.\n"
            "```" + lang + "\n" + first + "\n```\n"
            "Then ask ONE question: what do they expect this to print or do, "
            "in their own words. Do NOT ask them to write or type any code. "
            "Do not show the output this turn — it is:\n"
            "```\n" + second + "\n```")

    if kind == BEFORE_AFTER:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you "
            "have real material from the source, so use it.\\n"
            "THIS TURN: SHOW BOTH VERSIONS AND ASK WHAT CHANGED.\n"
            "Put both in ```" + lang + " code blocks, labelled BEFORE and "
            "AFTER.\n"
            "BEFORE:\n```" + lang + "\n" + first + "\n```\n"
            "AFTER:\n```" + lang + "\n" + second + "\n```\n"
            "Then ask ONE question: what changed, and why does that change "
            "matter. Do not name the difference yourself.")
    return ""
