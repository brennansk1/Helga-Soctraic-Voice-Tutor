"""The contract every domain extension implements, and the lookup for it.

A domain extension answers three questions the generic pipeline cannot:

    classify(title, text, objectives) -> kind        what sort of knowledge
    rank(kind)                        -> int         teaching order
    guidance(kind)                    -> str         how the tutor should teach

and optionally:

    example_for(title, kind, source_text, url) -> aid dict | None

`for_domain` returns None for a subject with no extension, and every caller
must treat None as "use the generic path" rather than as an error. A domain
without a specialist is the normal case, not a failure — adding one should be
opt-in, not a prerequisite for building a course.
"""
import logging

logger = logging.getLogger(__name__)

#: Domains are DISCOVERED, not listed. A package under services/domains/ that
#: exposes DOMAIN and the contract below is a domain — adding one means adding a
#: directory, not editing this file. The previous hardcoded dict meant the
#: registry was closed for extension, which is the opposite of what a plug-in
#: point is for.
_CACHE = {}


def available():
    """Domain keys discoverable on disk, cached after first scan."""
    if _CACHE.get("_scanned"):
        return [k for k in _CACHE if not k.startswith("_")]
    import os
    import pkgutil
    here = os.path.dirname(os.path.abspath(__file__))
    for mod in pkgutil.iter_modules([here]):
        if not mod.ispkg:
            continue
        try:
            import importlib
            m = importlib.import_module(f"services.domains.{mod.name}")
            if getattr(m, "DOMAIN", None):
                _CACHE[m.DOMAIN] = m
        except Exception as e:               # pragma: no cover - defensive
            logger.warning(f"[DOMAIN] {mod.name} failed to load: {e}")
    _CACHE["_scanned"] = True
    return [k for k in _CACHE if not k.startswith("_")]


#: THE CONTRACT. A domain module MUST expose these. Checked at load time so a
#: half-implemented domain is reported rather than failing later in a build.
REQUIRED = ("DOMAIN", "LABEL", "classify", "rank", "guidance", "prompt_line")
#: Optional, each independently. Absence disables that feature for the domain,
#: it does not disable the domain.
#: `pair_block` turns a build-time mined pair into a tutor instruction.
#: Optional because a domain may have no notion of a teachable pair.
#: `source_for` and `classify_concepts` are CALLED by `book_skeleton` and were
#: not declared here, so `contract_report` could not see them and a domain that
#: implemented one wrongly looked complete. That is how the mathematics domain
#: shipped a `source_for(subject)` whose signature did not match the call site
#: `source_for(subject, doc_resolver=...)`: the TypeError went into that site's
#: `except Exception`, was logged as "domain source lookup failed", and the
#: report said nothing was missing.
OPTIONAL = ("SHAPE", "example_for", "attach_to_course", "KEYWORDS",
            "pair_block", "source_for", "classify_concepts")


#: Required names that must be CALLABLE, not merely present.
#:
#: `hasattr` is not enough. The computer-science package had a submodule named
#: `classify.py`, and importing it bound `computer_science.classify` to the
#: MODULE — shadowing the contract function of the same name. `ext.classify(...)`
#: raised "module object is not callable", while this report happily said
#: nothing was missing. A contract check that passes the wrong object is worse
#: than no check, because it is trusted.
_CALLABLE_REQUIRED = ("classify", "rank", "guidance", "prompt_line")


def contract_report(module):
    """What a domain module implements and what it is missing."""
    missing = [f for f in REQUIRED if not hasattr(module, f)]
    shadowed = [f for f in _CALLABLE_REQUIRED
                if hasattr(module, f) and not callable(getattr(module, f))]
    return {
        "domain": getattr(module, "DOMAIN", None),
        "missing_required": missing + [f"{f} (not callable)" for f in shadowed],
        "has_optional": [f for f in OPTIONAL if hasattr(module, f)],
    }


def domain_for(subject, llm_json_fn=None, context=None):
    """The domain key for a subject, or None.

    Keyword matching FIRST because it is free and unambiguous when it hits, and
    an LLM classifier second when one is supplied. Keywords alone are not
    enough: matching the bare substring "api" routed "therapist" to computer
    science, and no keyword list will ever cover "Polars", "Dagster" or an
    internal tool nobody has heard of.

    Each domain owns its own KEYWORDS, so adding a domain does not mean editing
    this function.
    """
    s = (subject or "").strip().lower()
    if not s:
        return None
    for key in available():
        mod = _CACHE.get(key)
        for w in (getattr(mod, "KEYWORDS", ()) or ()):
            w = w.lower()
            # SINGLE WORDS MATCH AT A WORD BOUNDARY; PHRASES MATCH ANYWHERE.
            #
            # The rule used to be keyed on LENGTH — boundary for <=4 chars,
            # substring otherwise — and that is not the distinction that
            # matters. "force" is five characters, so it substring-matched and
            # routed "Managing your workforce" to science. The science module's
            # own docstring names that exact trap, and a comment in it claimed
            # the boundary rule handled it; the rule did not reach it.
            #
            # Leading boundary only, deliberately: `\balgebra` still matches
            # "algebraic" and `\bcell` still matches "cells", while neither
            # `\bforce` nor `\bcell` can fire inside "workforce" or "Excel".
            # A trailing \b would lose every inflected form.
            if " " in w:
                if w in s:
                    return key
            else:
                import re as _re
                if _re.search(rf"\b{_re.escape(w)}", s):
                    return key
    if llm_json_fn:
        return _classify_with_llm(s, llm_json_fn, context=context)
    return None


def _classify_with_llm(subject, llm_json_fn, context=None):
    """Ask the model which domain a course belongs to, or None.

    THE TAIL NO KEYWORD LIST REACHES. Keywords are free and exact when they
    hit, and they cannot cover everything: measured on realistic topics,
    "the pythagorean theorem", "cell division", "recursion" and "how does TCP
    work" all fell through to no domain at all — eight of sixteen. Widening the
    lists fixed those specific ones and will not fix the next eight.

    `context` is what else is known about the course — its description and the
    titles it generated. A bare title can be genuinely ambiguous ("Vectors" is
    mathematics or biology; "Trees" is computer science or botany) where the
    module list settles it immediately.

    Returns None rather than guessing when the model is unsure: a subject
    routed to the WRONG domain gets actively wrong teaching instructions, which
    is worse than getting generic ones. That asymmetry is why the prompt pushes
    toward "none".
    """
    keys = available()
    if not keys:
        return None
    labels = {k: getattr(_CACHE[k], "LABEL", k) for k in keys}
    # Show what each domain actually COVERS, not just its name. "Science" and
    # "Mathematics" are not self-explanatory at the boundary — statistics,
    # say — and a sample of each domain's own keywords describes the boundary
    # far better than the label does, without this file having to know it.
    covers = {}
    for k in keys:
        kw = list(getattr(_CACHE[k], "KEYWORDS", ()) or ())[:14]
        covers[k] = ", ".join(kw)
    ctx = f"\nWHAT ELSE IS KNOWN:\n{context}\n" if context else ""
    prompt = (
        f"Which of these subject domains does this course belong to?\n\n"
        f"COURSE: {subject}\n{ctx}\n"
        f"DOMAINS:\n"
        + "\n".join(f"- {k} ({labels[k]}) — covers: {covers[k]}"
                     for k in keys)
        + "\n- none: it belongs to none of them\n\n"
        f"Answer 'none' unless the course clearly belongs to one. A wrong "
        f"domain gives the tutor actively wrong teaching instructions — a "
        f"history course told never to ask for a solved answer, or a maths "
        f"course told never to state a date. Generic teaching is the safe "
        f"failure; a confident wrong answer is not.\n\n"
        f'Return STRICT JSON: {{"domain": "<key or none>", "why": "<8 words>"}}'
    )
    try:
        raw = llm_json_fn(prompt=prompt, expected_type="dict", max_tokens=120,
                          schema={"type": "object",
                                  "properties": {"domain": {"type": "string"},
                                                 "why": {"type": "string"}},
                                  "required": ["domain"]})
        got = (raw or {}).get("domain", "").strip().lower()
        return got if got in keys else None
    except Exception as e:
        logger.debug(f"[DOMAIN] llm classification failed: {e}")
        return None


def for_domain(domain_key):
    """The extension module for a domain key, or None."""
    if not domain_key:
        return None
    available()
    return _CACHE.get(domain_key)


def for_subject(subject, llm_json_fn=None, context=None):
    """Convenience: subject string straight to an extension module, or None."""
    return for_domain(domain_for(subject, llm_json_fn=llm_json_fn,
                                 context=context))


#: The key a course records its domain under. ONE name, used by every writer
#: and every reader.
#
# `course_builder` already stores `depth_contract.infer_domain` — "formal" vs
# "narrative" — under the key `domain`. That is a different axis entirely, and
# two meanings behind one key is how a reader ends up asking a course whether
# it is "narrative" and being told "computer_science". So this one is
# explicitly namespaced.
DOMAIN_KEY = "teaching_domain"


def classify_course(course, subject=None, llm_json_fn=None):
    """Stamp a course with its teaching domain. Returns the key, or None.

    THE SINGLE PLACE A COURSE'S DOMAIN IS DECIDED. Called once at build time by
    every build path, so the tutoring loop can read the answer instead of
    re-deriving it per turn from the title — which would be both slower and
    liable to disagree with itself between turns.

    Idempotent: a course already stamped is returned unchanged, so a rebuild or
    a partial re-run cannot flip a course's domain underneath its content.
    """
    if not isinstance(course, dict):
        return None
    existing = course.get(DOMAIN_KEY)
    if existing:
        return existing
    subject = subject or course.get("title") or ""
    key = domain_for(subject, llm_json_fn=llm_json_fn)
    if key:
        course[DOMAIN_KEY] = key
        logger.info(f"[DOMAIN] {subject!r} -> {key}")
    return key


def domain_of(course):
    """The teaching domain recorded on a course, or None. For readers."""
    if not isinstance(course, dict):
        return None
    return course.get(DOMAIN_KEY)
