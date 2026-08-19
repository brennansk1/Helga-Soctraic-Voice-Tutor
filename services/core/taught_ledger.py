"""The taught-concepts ledger — what this course has already said.

WHY THIS EXISTS
---------------
Concepts are hydrated one at a time, each blind to the rest of the course. Two
failures follow directly from that, and research on this pipeline concluded they
are ONE problem with one answer:

  * the same idea taught five times, because five lessons each reasonably
    decided the learner needed it explained before their own topic made sense
  * hollow content, because a concept cannot build on what it cannot see

A Dungeon Mastering course can carry "Probability in Combat", "Encounter
Difficulty Math", "Advantage and Disadvantage", "Skill Check Design" and
"Damage Expectation" — five entirely distinct, entirely reasonable lesson
titles, every one of which opens by explaining a d20 distribution.

**Every duplicate control in this repo is title-level and blind to that.**
`_is_duplicate` compares normalised titles, `check_filler` finds near-duplicate
titles and repeated stems, `check_uniformity` caps the duplicate rate at 5%.
The five titles above pass all of them.

STRUCTURED INDEX, NOT A ROLLING SUMMARY
---------------------------------------
The ledger stores claims and embeddings, never a prose summary of the course so
far. A rolling summary costs an LLM call per concept and accumulates error;
a structured index cannot drift, grows linearly, and stays queryable. It is also
what makes a 32k context enough: hydration receives the k nearest already-taught
concepts as titles plus one-line claims, never full bodies.

THE TARGET IS NOT ZERO REPETITION
---------------------------------
Bruner's spiral is the retention mechanism the scheduling design depends on: a
curriculum "should revisit these basic ideas repeatedly, building upon them."
So the gate is STRUCTURAL rather than similarity-based. Three moves:

    RE-TEACH   a full introduction   -- allowed exactly once, at the owning lesson
    ASSUME     "you know X, recall it" -- allowed downstream, and for a Socratic
                                          tutor often the best move
    CITE       a prerequisite link, no re-explanation -- the default downstream

What is flagged is a concept that INTRODUCES FROM SCRATCH a claim already
introduced upstream. Legitimate reinforcement references a prior claim and adds
new material. That distinction is computable from the claim graph with no model
in it, which matters because the LLM judge in this repo swings +/-1.4 out of 5
between identical runs.

NO MODEL IN THE INSTRUMENT
--------------------------
Detection is arithmetic: shingle Jaccard for near-verbatim, claim overlap for
paraphrase. Embeddings are used when an embedder is available and the ledger
degrades to lexical-only when it is not — and it records WHICH ran, so a
degraded check is never mistaken for a clean one.
"""

import json
import logging
import math
import re

logger = logging.getLogger(__name__)

# Shingle width for near-duplicate detection. 5 words is long enough that
# ordinary shared phrasing ("in this case", "we can see that") does not collide,
# short enough to survive light paraphrase.
SHINGLE_W = 5

# Jaccard at or above this is near-verbatim re-teaching rather than reference.
NEAR_DUPLICATE = 0.80

# A claim counts as re-introduced when its content words overlap this much with
# a claim already on the ledger.
CLAIM_OVERLAP = 0.70

# How many already-taught concepts to hand to a hydration prompt. Six titles
# plus one-line claims is a few hundred tokens; the whole point is that this
# stays small enough to be free at any context size.
DEFAULT_K = 6

_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "has", "have", "had", "not", "but", "its", "it's", "can", "will", "would",
    "which", "when", "what", "how", "why", "than", "then", "into", "such",
    "these", "those", "there", "their", "them", "they", "you", "your", "our",
    "his", "her", "she", "him", "all", "any", "one", "two", "may", "each",
    "also", "more", "most", "some", "other", "used", "use", "using", "about",
}


def ensure_schema(conn):
    """Create the ledger tables. Safe to call repeatedly."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS taught_concepts (
            course_uid   TEXT NOT NULL,
            concept_uid  TEXT NOT NULL,
            title        TEXT NOT NULL,
            ordinal      INTEGER NOT NULL,
            module       TEXT,
            lesson       TEXT,
            embedding    BLOB,
            embedder     TEXT,
            body_hash    TEXT,
            shingles     TEXT,
            PRIMARY KEY (course_uid, concept_uid)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS taught_claims (
            course_uid   TEXT NOT NULL,
            concept_uid  TEXT NOT NULL,
            ordinal      INTEGER NOT NULL,
            claim        TEXT NOT NULL,
            keywords     TEXT NOT NULL
        )
    """)
    # Retrieval walks the ledger by course and in teaching order on every
    # hydration call, so these two are load-bearing rather than housekeeping.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_taught_course "
                "ON taught_concepts(course_uid, ordinal)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_claims_course "
                "ON taught_claims(course_uid, ordinal)")
    conn.commit()


# --- text -> claims ----------------------------------------------------------

def _content_words(text):
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOP]


def extract_claims(markdown):
    """Atomic claims from a hydrated concept.

    `## Key Facts` is already a list of atomic verified bullets — the section
    template asks for exactly that — so the claims are free rather than needing
    an extraction model. `## Misconceptions` corrections are claims too: a
    correction asserts what IS true.

    Falls back to the first sentences of `## Core Explanation` when a concept
    has neither, so a malformed file still lands on the ledger rather than
    silently contributing nothing.
    """
    md = markdown or ""
    claims = []

    for header in ("Key Facts", "Edge Cases & Limitations"):
        m = re.search(rf"##\s*{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)",
                      md, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip().lstrip("-*• ").strip()
                if len(line) > 15:
                    claims.append(line)

    m = re.search(r"##\s*Misconceptions\s*\n(.*?)(?=\n##\s|\Z)", md, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            # Strip the list marker but NOT the emphasis: `lstrip("-*• ")` eats
            # the leading `**` of `**Correction**` and the label stops matching,
            # which silently drops every correction from the ledger.
            line = re.sub(r"^\s*[-•]\s*", "", line).strip()
            # The correction is the assertion; the belief is what is FALSE, and
            # putting a false statement on the ledger would be actively wrong —
            # a later concept would then be told not to contradict it.
            if re.match(r"\*\*Correction\*\*", line, re.I):
                body = re.sub(r"^\*\*Correction\*\*:?\s*", "", line, flags=re.I)
                if len(body) > 15:
                    claims.append(body.strip())

    if not claims:
        m = re.search(r"##\s*Core Explanation\s*\n(.*?)(?=\n##\s|\Z)", md, re.DOTALL)
        if m:
            for s in re.split(r"(?<=[.!?])\s+", m.group(1).strip())[:5]:
                if len(s.strip()) > 25:
                    claims.append(s.strip())

    # Dedupe within the concept before it ever reaches the ledger.
    seen, out = set(), []
    for c in claims:
        k = " ".join(sorted(set(_content_words(c))))
        if k and k not in seen:
            seen.add(k)
            out.append(c[:400])
    return out


def shingles(text, w=SHINGLE_W):
    words = _content_words(text)
    if len(words) < w:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + w]) for i in range(len(words) - w + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap(a_words, b_words):
    """Directional containment: how much of `a` appears in `b`.

    Not Jaccard here — a short claim fully contained in a longer one IS
    re-introduced, and symmetric Jaccard would score that pair low simply
    because the lengths differ.
    """
    if not a_words:
        return 0.0
    return len(a_words & b_words) / len(a_words)


# --- writing -----------------------------------------------------------------

def record_concept(conn, course_uid, concept_uid, title, markdown,
                   ordinal, module="", lesson="", embedding=None,
                   embedder=None):
    """Write a validated concept to the ledger.

    Called AFTER validation, never before: a concept that failed its depth
    contract and will be regenerated must not teach the ledger anything, or the
    regeneration would be told to avoid re-teaching its own discarded draft.
    """
    ensure_schema(conn)
    claims = extract_claims(markdown)
    sh = shingles(markdown)
    cur = conn.cursor()
    cur.execute("DELETE FROM taught_concepts WHERE course_uid=? AND concept_uid=?",
                (course_uid, concept_uid))
    cur.execute("DELETE FROM taught_claims WHERE course_uid=? AND concept_uid=?",
                (course_uid, concept_uid))
    cur.execute(
        "INSERT INTO taught_concepts (course_uid, concept_uid, title, ordinal, "
        "module, lesson, embedding, embedder, body_hash, shingles) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (course_uid, concept_uid, title, ordinal, module, lesson,
         json.dumps(embedding) if embedding else None, embedder,
         str(hash(markdown or "")), json.dumps(sorted(sh)[:400])))
    for c in claims:
        cur.execute(
            "INSERT INTO taught_claims (course_uid, concept_uid, ordinal, "
            "claim, keywords) VALUES (?,?,?,?,?)",
            (course_uid, concept_uid, ordinal, c,
             " ".join(sorted(set(_content_words(c))))))
    conn.commit()
    return len(claims)


# --- reading -----------------------------------------------------------------

def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def neighbours(conn, course_uid, title, objectives="", k=DEFAULT_K,
               embedding=None, before_ordinal=None):
    """The k most related already-taught concepts, as a compact context block.

    Returns dicts with `title`, `claims` (at most two, one line each) and
    `concept_uid`, so a prompt can say "these are already taught, cite them by
    name rather than re-explaining."

    Deliberately NOT full bodies. The point of a ledger over stuffing the course
    into the window is that this stays small at any course size.
    """
    ensure_schema(conn)
    cur = conn.cursor()
    q = "SELECT concept_uid, title, ordinal, embedding FROM taught_concepts WHERE course_uid=?"
    args = [course_uid]
    if before_ordinal is not None:
        q += " AND ordinal < ?"
        args.append(before_ordinal)
    rows = cur.execute(q, args).fetchall()
    if not rows:
        return []

    want = set(_content_words(f"{title} {objectives}"))
    scored = []
    for uid, t, ordinal, emb in rows:
        lex = _overlap(want, set(_content_words(t))) if want else 0.0
        dense = 0.0
        if embedding and emb:
            try:
                dense = _cosine(embedding, json.loads(emb))
            except Exception:
                dense = 0.0
        # Hybrid without a fusion library: lexical catches exact terms and named
        # results, dense catches paraphrase. Taking the max rather than a
        # weighted sum avoids inventing a weight we have not measured.
        scored.append((max(lex, dense), ordinal, uid, t))

    scored.sort(key=lambda r: (-r[0], -r[1]))
    out = []
    for score, ordinal, uid, t in scored[:k]:
        claims = [r[0] for r in cur.execute(
            "SELECT claim FROM taught_claims WHERE course_uid=? AND concept_uid=? "
            "LIMIT 2", (course_uid, uid)).fetchall()]
        out.append({"concept_uid": uid, "title": t, "ordinal": ordinal,
                    "claims": claims, "score": round(score, 3)})
    return out


def format_context(neigh):
    """Render neighbours for a prompt. Empty string when there are none."""
    if not neigh:
        return ""
    lines = ["### ALREADY TAUGHT IN THIS COURSE",
             "These are covered. Refer to them by name or ask the learner to "
             "recall them — do NOT explain them again from scratch.", ""]
    for n in neigh:
        lines.append(f"- {n['title']}")
        for c in n["claims"][:2]:
            lines.append(f"    · {c[:160]}")
    return "\n".join(lines)


# --- the gate ----------------------------------------------------------------

def check_redundancy(conn, course_uid, concept_uid, markdown, ordinal):
    """Does this concept re-introduce something already taught upstream?

    Two independent detectors, neither containing a model:

      * near-verbatim   shingle Jaccard against prior bodies
      * re-introduction claim overlap against prior claims

    The second is the one that matters. Five differently-titled lessons each
    explaining a d20 distribution share few exact phrases and every underlying
    claim.
    """
    ensure_schema(conn)
    cur = conn.cursor()
    mine_sh = shingles(markdown)
    mine_claims = extract_claims(markdown)

    verbatim = []
    for uid, t, sh in cur.execute(
            "SELECT concept_uid, title, shingles FROM taught_concepts "
            "WHERE course_uid=? AND ordinal < ?", (course_uid, ordinal)):
        if uid == concept_uid or not sh:
            continue
        try:
            j = jaccard(mine_sh, set(json.loads(sh)))
        except Exception:
            continue
        if j >= NEAR_DUPLICATE:
            verbatim.append({"concept_uid": uid, "title": t, "jaccard": round(j, 2)})

    prior = cur.execute(
        "SELECT concept_uid, claim, keywords FROM taught_claims "
        "WHERE course_uid=? AND ordinal < ?", (course_uid, ordinal)).fetchall()

    reintroduced = []
    for c in mine_claims:
        cw = set(_content_words(c))
        for uid, claim, kw in prior:
            if _overlap(cw, set(kw.split())) >= CLAIM_OVERLAP:
                reintroduced.append({"claim": c[:160], "already_in": uid,
                                     "prior_claim": claim[:160]})
                break

    return {
        "checked": True,
        "instrument": "shingle Jaccard + claim overlap (no model)",
        "claims": len(mine_claims),
        "near_duplicate_of": verbatim,
        "reintroduced": reintroduced,
        # A concept that adds nothing new is the real defect. One repeated claim
        # inside a concept that otherwise advances the subject is the spiral
        # working, so the gate is a SHARE, not a count.
        "reintroduced_share": (round(len(reintroduced) / len(mine_claims), 2)
                               if mine_claims else 0.0),
        "ok": not verbatim and (
            not mine_claims or len(reintroduced) / len(mine_claims) < 0.5),
    }


def correction_hint(result):
    """Name the specific offender — the only enforcement shape measured to work.

    Prompt-only instruction changed nothing 5/5 in this pipeline; a correction
    round naming the exact offending item fixed it 5/5.
    """
    if not result or result.get("ok"):
        return ""
    bits = []
    for d in (result.get("near_duplicate_of") or [])[:2]:
        bits.append(f"This is nearly identical to \"{d['title']}\", which the "
                    f"course already teaches.")
    for r in (result.get("reintroduced") or [])[:3]:
        bits.append(f"You explain \"{r['claim']}\" from scratch, but the course "
                    f"already established it. Refer to it instead, or ask the "
                    f"learner to recall it.")
    if not bits:
        return ""
    return ("### CORRECTION — REDUNDANCY\n" + "\n".join(bits)
            + "\nTeach what is NEW about this concept. Reference prior material "
              "rather than re-deriving it.")
