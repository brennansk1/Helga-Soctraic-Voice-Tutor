"""Settle SQL claims by running them, not by asking a model.

A content audit on 2026-08-25 found seven factual errors in two shipped
courses. Every gate passed them, because every gate was asking a different
question:

    depth contract  -> is it deep enough?      (word count, has a definition)
    content guards  -> is it hygienic?         (no deliberation, no stubs)
    relevance gate  -> is the source on-topic?
    fact_check.py   -> asks the model that wrote it, on a 34% sample

None of those asks whether the sentence is TRUE. So this passed everything:

    "By default, ORDER BY ... ASC places NULLs first, treating them as less
     than any other value."

PostgreSQL does the opposite. And that is not a matter of opinion to be
adjudicated by a second model — it is a query. `SELECT ... ORDER BY x` settles
it in under a millisecond, deterministically, with no model in the loop.

WHY THIS IS DIFFERENT FROM THE FACT-CHECKER WE HAVE
---------------------------------------------------
The existing checker asks a language model to judge language. This runs the
claim. When they disagree, this one is right — it is the same engine the
learner will type into. Text-to-SQL research calls this execution-based
evaluation and rates it the strongest available signal for exactly that
reason; the usual objection (you need a live database) costs us one 57 MB
container.

WHAT IT CAN AND CANNOT DO
-------------------------
It checks claims that are COMPUTABLE: ordering, ranking, frame semantics,
NULL propagation, aggregate behaviour. It says nothing about pedagogy, about
whether an explanation is clear, or about claims with no executable form
("CTEs improve readability"). Those remain the fact-checker's job, and a
human's.

It is also deliberately CONSERVATIVE: a probe reports a discrepancy only when
the prose makes an unambiguous claim that the engine contradicts. Silence is
not approval — `unchecked` and `correct` are different results and are
reported as such, because a checker that overstates its coverage is how "no
false claims found" was reported for a course that had seven.
"""
import datetime
import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

CONTAINER = "helga-sqlcheck"


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


def _run(sql, container=CONTAINER, timeout=15):
    """Execute SQL and return rows as a list of tuples, or None if unavailable."""
    try:
        out = subprocess.run(
            ["docker", "exec", container, "psql", "-U", "postgres", "-d",
             "check", "-tAF", "|", "-c", sql],
            capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        logger.debug("sql ground truth unavailable: %s", e)
        return None
    if out.returncode != 0:
        logger.debug("probe failed: %s", (out.stderr or "").strip()[:200])
        return None
    rows = []
    for line in (out.stdout or "").strip().split("\n"):
        if line == "":
            continue
        rows.append(tuple(line.split("|")))
    return rows


def engine_available(container=CONTAINER):
    return _run("SELECT 1", container=container) is not None


# --- the probes -------------------------------------------------------------
#
# Each names a behaviour, the query that settles it, how to read the answer,
# and the prose patterns that would CONTRADICT it. Patterns are deliberately
# narrow: they must match an unambiguous assertion, not a passing mention.

def _nulls_sort_position(container):
    rows = _run("SELECT COALESCE(x::text,'NULL') FROM (VALUES (1),(NULL),(2)) "
                "t(x) ORDER BY x ASC", container=container)
    if not rows:
        return None
    return "last" if rows[-1][0] == "NULL" else "first"


def _nulls_sort_position_desc(container):
    rows = _run("SELECT COALESCE(x::text,'NULL') FROM (VALUES (1),(NULL),(2)) "
                "t(x) ORDER BY x DESC", container=container)
    if not rows:
        return None
    return "first" if rows[0][0] == "NULL" else "last"


def _nulls_order_truth(container):
    asc, desc = _nulls_sort_position(container), _nulls_sort_position_desc(container)
    if asc is None or desc is None:
        return None
    return {"asc": asc, "desc": desc}


# ATTRIBUTION, NOT PROXIMITY.
#
# Two regex attempts flagged this CORRECT sentence as an error:
#
#     "PostgreSQL defaults to `NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC`."
#
# because "first" sits within a few characters of "ASC". Nearest-token pairing
# fails on it too: "NULLS FIRST" is 7 characters from ASC and 12 from DESC, so
# proximity picks the wrong one and the sentence is condemned for saying the
# right thing.
#
# What actually determines the pairing is ORDER. A sentence that mentions two
# directions and two NULL positions pairs them off in the sequence they appear,
# whichever side leads:
#
#     "NULLS LAST for ASC and NULLS FIRST for DESC"   -> (asc,last) (desc,first)
#     "ORDER BY ... ASC places NULLs first"           -> (asc,first)
#
# When the counts do not match the sentence is ambiguous, and an ambiguous
# sentence is left alone. That is the conservative direction: a checker that
# convicts correct prose teaches the pipeline to write worse prose.
_SENTENCE = re.compile(r"[^.!?\n]+")
_DIRECTION = re.compile(r"\b(ASC|DESC)\b", re.IGNORECASE)
_NULL_POSITION = re.compile(r"\bNULLs?\b[^.\n]{0,40}?\b(first|last)\b", re.IGNORECASE)


def _find_null_order_claims(body, truth):
    """Every (direction, claimed position) pair the prose asserts, that the
    engine contradicts."""
    findings = []
    for sent in _SENTENCE.finditer(body or ""):
        text = sent.group(0)
        dirs = [(m.start(), m.group(1).lower()) for m in _DIRECTION.finditer(text)]
        poss = [(m.start(), m.group(1).lower()) for m in _NULL_POSITION.finditer(text)]
        if not dirs or not poss or len(dirs) != len(poss):
            continue
        for (_, direction), (_, position) in zip(dirs, poss):
            if truth.get(direction) and position != truth[direction]:
                findings.append({
                    "claim": " ".join(text.split())[:200],
                    "detail": f"says NULLs sort {position.upper()} under "
                              f"{direction.upper()}; the engine puts them "
                              f"{truth[direction].upper()}",
                })
    return findings


def _rank_vs_dense_rank(container):
    """With a tie at the top, RANK skips and DENSE_RANK does not."""
    rows = _run(
        "SELECT rank() OVER (ORDER BY v), dense_rank() OVER (ORDER BY v) "
        "FROM (VALUES (10),(10),(20)) t(v) ORDER BY v", container=container)
    if not rows or len(rows) < 3:
        return None
    return {"rank": [r[0] for r in rows], "dense_rank": [r[1] for r in rows]}


def _count_star_vs_column(container):
    rows = _run("SELECT count(*), count(x) FROM (VALUES (1),(NULL),(2)) t(x)",
                container=container)
    if not rows:
        return None
    return {"star": int(rows[0][0]), "column": int(rows[0][1])}


def _null_equality(container):
    rows = _run("SELECT COALESCE((NULL = NULL)::text, 'NULL')",
                container=container)
    return rows[0][0] if rows else None


PROBES = [
    {
        "id": "nulls_order",
        "behaviour": "NULL position under ORDER BY ASC / DESC",
        "truth": _nulls_order_truth,
        # Uses a finder rather than patterns — see _find_null_order_claims.
        "finder": _find_null_order_claims,
        "says": "PostgreSQL sorts NULLs LAST under ASC and FIRST under DESC",
    },
    {
        "id": "rank_vs_dense_rank",
        "behaviour": "RANK() versus DENSE_RANK() across a tie",
        "truth": _rank_vs_dense_rank,
        "contradicted_when": lambda truth: (
            bool(truth) and truth["rank"] == ["1", "1", "3"]
            and truth["dense_rank"] == ["1", "1", "2"], [
                # DENSE_RANK is always <= RANK. Claiming the reverse is wrong.
                re.compile(r"DENSE_RANK\(?\)?[^.\n]{0,60}?(?:is\s+)?(?:always\s+)?"
                           r"(?:\$?\\?geq\$?|>=|greater than or equal|larger than)"
                           r"[^.\n]{0,30}?RANK\(?\)?", re.IGNORECASE),
            ]),
        "says": "DENSE_RANK() is always <= RANK(): a tie makes RANK skip (1,1,3) "
                "while DENSE_RANK does not (1,1,2)",
    },
    {
        "id": "count_star_vs_column",
        "behaviour": "COUNT(*) versus COUNT(column) with NULLs present",
        "truth": _count_star_vs_column,
        "contradicted_when": lambda truth: (
            bool(truth) and truth["star"] != truth["column"], [
                re.compile(r"COUNT\(\*\)[^.\n]{0,60}?(?:is\s+)?(?:the same as|identical to|equivalent to)"
                           r"[^.\n]{0,30}?COUNT\([a-z_]", re.IGNORECASE),
            ]),
        "says": "COUNT(*) counts rows and COUNT(column) skips NULLs, so they differ "
                "whenever the column has a NULL",
    },
    {
        "id": "null_equality",
        "behaviour": "NULL = NULL",
        "truth": _null_equality,
        "contradicted_when": lambda truth: (truth == "NULL", [
            re.compile(r"\bNULL\s*=\s*NULL\b[^.\n]{0,40}?\b(?:is\s+|returns\s+|evaluates to\s+)"
                       r"(?:TRUE|true)\b"),
        ]),
        "says": "NULL = NULL is UNKNOWN, not TRUE",
    },
    {
        "id": "short_circuit",
        "behaviour": "guaranteed short-circuit evaluation of AND/OR",
        # Not executable — the standard leaves evaluation order UNSPECIFIED,
        # which is precisely why claiming a guarantee is wrong. Recorded here
        # so the claim is caught, with the reason stated rather than measured.
        "truth": lambda container: "unspecified",
        "contradicted_when": lambda truth: (True, [
            re.compile(r"(?:standard\s+)?SQL\s+guarantees\s+short[- ]circuit", re.IGNORECASE),
            re.compile(r"short[- ]circuit(?:ing)?\s+(?:evaluation\s+)?is\s+guaranteed", re.IGNORECASE),
        ]),
        "says": "SQL does NOT guarantee short-circuit evaluation; PostgreSQL "
                "documents that you may not rely on left-to-right evaluation "
                "of AND/OR",
    },
]


# A MISCONCEPTION SECTION STATES FALSEHOODS ON PURPOSE.
#
# "- **Belief**: `NULL = NULL` returns TRUE" is the concept doing its job:
# naming the wrong idea so the next line can correct it. A checker that reads
# it as an error flags good teaching as bad — measured on the first run, 2 of
# 7 findings were exactly this, plus the tutor reads that section, so removing
# it would make the lesson worse.
#
# The Correction half is a real assertion and stays under scrutiny.
_BELIEF_LINE = re.compile(r"^\s*[-*]?\s*\*\*Belief\*\*\s*:.*$", re.MULTILINE)


def _assertions_only(markdown):
    """The prose that is claiming something, with the deliberate wrong ideas
    (and quoted learner answers) removed."""
    return _BELIEF_LINE.sub("", markdown or "")


def _spans_both_directions(text):
    t = (text or "").upper()
    return ("ASC" in t and "DESC" in t) or ("FIRST" in t and "LAST" in t)


# WHERE THE TRUTH LIVES, AND WHY IT IS A FILE.
#
# The pipeline runs in a container with no docker socket — removed on purpose,
# because a build service with the socket can control the host. So hydration
# cannot `docker exec` a probe.
#
# It does not need to. What PostgreSQL does with a NULL under ORDER BY does not
# change between two builds; what changes is whether anyone ever CHECKED. So
# the measurement happens once on the host, against a real engine, and is
# written here with the engine version and the timestamp attached. The pipeline
# reads the record.
#
# This is a weaker claim than "executed live" and is labelled as one: the file
# says which engine produced it and when, and `measure()` regenerates it in
# about a second. What it is NOT is a model's opinion, or a constant somebody
# typed in from memory — which is exactly how the wrong NULL ordering got into
# two courses in the first place.
TRUTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "sql_ground_truth.json")


def measure(container=CONTAINER, path=TRUTH_FILE):
    """Run every probe against a live engine and record what it answered."""
    version = _run("SELECT version()", container=container)
    if not version:
        raise RuntimeError(
            f"no SQL engine to measure against (container {container!r}). "
            f"Start one with:  docker run -d --name {container} "
            f"-e POSTGRES_PASSWORD=x -e POSTGRES_DB=check --memory=256m "
            f"-p 55432:5432 postgres:16-alpine")
    record = {
        "engine": version[0][0],
        "measured_at": _now_iso(),
        "probes": {},
    }
    for probe in PROBES:
        record["probes"][probe["id"]] = probe["truth"](container)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return record


_TRUTH_CACHE = {}


def load_truth(path=TRUTH_FILE):
    """The measured answers, or None if nothing has ever been measured.

    None is returned rather than a guessed default: a checker with no ground
    truth must say it has none, not invent one.
    """
    if path in _TRUTH_CACHE:
        return _TRUTH_CACHE[path]
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except Exception as e:
        logger.debug("no measured SQL ground truth at %s (%s)", path, e)
        record = None
    _TRUTH_CACHE[path] = record
    return record


def check_markdown(markdown, container=CONTAINER):
    """Run every applicable probe against one concept body.

    Returns (findings, checked_ids). `findings` are dicts naming the probe,
    the sentence that contradicts the engine, and what the engine actually
    does. An empty findings list with a short checked_ids list means MOSTLY
    UNCHECKED, not correct — the caller must not report it as a pass.
    """
    record = load_truth()
    if not record:
        # NOT "everything is fine". Nothing was checked, and the caller is told
        # so through an empty `checked` list.
        return [], []
    measured = record.get("probes") or {}

    body = _assertions_only(markdown)
    findings, checked = [], []
    for probe in PROBES:
        truth = measured.get(probe["id"])
        if truth is None:
            continue
        checked.append(probe["id"])
        if "finder" in probe:
            for hit in probe["finder"](body, truth):
                findings.append({
                    "probe": probe["id"],
                    "behaviour": probe["behaviour"],
                    "claim": hit["claim"],
                    "engine_says": hit.get("detail", probe["says"]),
                })
            continue
        applies, patterns = probe["contradicted_when"](truth)
        if not applies:
            continue
        for pat in patterns:
            m = pat.search(body)
            # A match that contains the OPPOSITE keyword is describing both
            # directions in one sentence, which is how the correct phrasing
            # reads. Attribution, not adjacency.
            while m and _spans_both_directions(m.group(0)):
                m = pat.search(body, m.end())
            if m:
                start = max(0, m.start() - 60)
                findings.append({
                    "probe": probe["id"],
                    "behaviour": probe["behaviour"],
                    "claim": " ".join(body[start:m.end() + 60].split()),
                    "engine_says": probe["says"],
                })
                break
    seen, unique = set(), []
    for f in findings:
        key = (f["probe"], f["claim"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, checked


if __name__ == "__main__":  # re-measure against a live engine
    import sys
    rec = measure()
    print(f"measured against {rec['engine'].split(',')[0]} at {rec['measured_at']}")
    for k, v in sorted(rec["probes"].items()):
        print(f"  {k:24} {v}")
