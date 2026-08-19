"""Fact-checking pass for generated course content (sprint A1, top priority).

WHY THIS EXISTS
---------------
Measured on real generated content with `tools/substance_check.py`: only 3 of 6
sampled concepts were judged SUBSTANTIVE, and the failures were not "missing
worked examples" — they were **false technical claims stated as fact**. One,
verified by hand:

    "A necessary and sufficient condition for endogeneity bias is the presence
     of a collider variable in the causal pathway between treatment and outcome."

False in both directions. Not necessary (ordinary confounding comes from a
common cause); not sufficient (an unconditioned collider *blocks* a path — bias
appears only on conditioning). A student is taught this as a formal condition.

Crucially this survived every other check: the concept reads at its claimed
level, carries real citations, and is well written. Fluency is not accuracy.

APPROACH
--------
Two modes, because memory is scarce on the target box (24 GB, already swapping):

  "llm"       — reuse the model already loaded. No extra RAM. Default.
  "minicheck" — Bespoke-MiniCheck, a model trained specifically for
                document→claim entailment. Higher precision, but pulls another
                ~4-5 GB resident, so it is opt-in via HELGA_FACTCHECK_MODE.

Claims are checked as ENTAILMENT against the retrieved source text where we
have sources, and as standalone truth assessment where we do not (llm-only
generation still has to not lie).

The checker validates ITSELF before being trusted — see `self_test()`. Too many
instruments in this project turned out to measure the wrong thing.
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MODE = os.getenv("HELGA_FACTCHECK_MODE", "llm").lower()
MINICHECK_MODEL = os.getenv("HELGA_MINICHECK_MODEL", "bespoke-minicheck")
# Below this many verified-false claims we accept the concept as-is.
MAX_FALSE_CLAIMS = int(os.getenv("HELGA_MAX_FALSE_CLAIMS", "0"))

_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string",
                                "enum": ["TRUE", "FALSE", "UNVERIFIABLE"]},
                    "why": {"type": "string"},
                },
                "required": ["claim", "verdict"],
            },
        }
    },
    "required": ["claims"],
}

_SYSTEM = """You are a domain expert fact-checking teaching material. Be strict.
A confident, fluent, well-written statement can still be false — judge the
substance.

Extract the material's SPECIFIC technical claims (definitions, conditions,
formulas, causal statements, named results). Ignore pedagogical framing,
questions, and vague motivational text.

For each claim return a verdict:
  TRUE          — correct as stated
  FALSE         — incorrect, overstated, or reverses a real relationship
  UNVERIFIABLE  — cannot be judged from general knowledge

Be especially alert to these, which are the observed failure modes:
- "necessary and sufficient" applied to something that is neither
- a mechanism described backwards (e.g. conflating a mediator with a confounder,
  or a collider with a common cause)
- a real named result attached to the wrong statement or formula
- a threshold or constant asserted with false precision

Report at most 8 claims, prioritising the most load-bearing ones.
Return STRICT JSON matching the schema."""


def _field(d, *names, default=None):
    """Read a field under any of several plausible key names.

    Ollama's /v1 shim honours `format` enough to emit valid JSON but does NOT
    reliably enforce the schema's KEY names: asked for {"verdict","why"} it
    returned {"answer": "FALSE", "reasoning": "..."} — correct content, wrong
    keys. Reading only the requested key silently discarded a correct verdict
    and made the checker look broken when the model was right.

    Smaller/quantised models do this often enough that alias-tolerant reads are
    the norm here, not a workaround.
    """
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    # Case-insensitive second pass.
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] is not None:
            return low[n.lower()]
    return default


def _verdict_of(d):
    """Normalise a verdict from any key/casing the model happened to use."""
    v = _field(d, "verdict", "answer", "result", "label", "judgement",
               "judgment", "decision", default="")
    return str(v).strip().upper()


def _post(messages, schema=None, max_tokens=900, model=None):
    """Single constrained call. Returns parsed dict or None."""
    import requests
    url = os.getenv("LLM_API_URL",
                    "http://host.docker.internal:11434/v1/chat/completions")
    mdl = model or os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL", "nail-35b-a3b-ctx")
    body = {"model": mdl, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0.0, "stream": False, "reasoning_effort": "none"}
    if schema:
        body["format"] = schema
    try:
        r = requests.post(url, json=body, timeout=180)
        if r.status_code != 200:
            logger.warning(f"fact-check HTTP {r.status_code}")
            return None
        c = r.json()["choices"][0]["message"].get("content") or ""
        c = re.sub(r"<think>.*?</think>", "", c, flags=re.DOTALL)
        from services.common.llm_utils import parse_llm_json
        return parse_llm_json(c, expected_type="dict")
    except Exception as e:
        logger.warning(f"fact-check call failed: {e}")
        return None


def _strip_scaffolding(text):
    """Drop sections that carry no factual claims, to focus the checker."""
    for sec in ("Metadata", "Learning Objectives", "Mastery Criteria",
                "Socratic Hooks", "Sources", "Prerequisites"):
        text = re.sub(rf"^## {sec}\n.*?(?=\n## |\Z)", "", text,
                      flags=re.DOTALL | re.MULTILINE)
    return text.strip()


def check_content(content, source_text="", concept_title=""):
    """Fact-check one concept body.

    Returns {"claims": [...], "false": [...], "ok": bool}. Fails OPEN (ok=True,
    empty lists) if the checker itself is unavailable — a broken checker must
    not block course creation, but it also must not silently claim success, so
    "checked" is reported separately.
    """
    body = _strip_scaffolding(content or "")
    if not body.strip():
        return {"claims": [], "false": [], "ok": True, "checked": False}

    user = f"Teaching material{' on ' + concept_title if concept_title else ''}:\n\n{body[:6000]}"
    if source_text:
        user = (f"Reference sources:\n{source_text[:4000]}\n\n"
                f"Judge each claim against the sources where they are relevant, "
                f"and against your own knowledge otherwise.\n\n" + user)

    model = MINICHECK_MODEL if MODE == "minicheck" else None
    data = _post([{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": user}],
                 schema=_CLAIM_SCHEMA, model=model)
    # Read via _field: the model may return "items"/"results" instead of
    # "claims" even with a schema (see _field's docstring).
    if not data or not isinstance(
            _field(data, "claims", "items", "results"), list):
        return {"claims": [], "false": [], "ok": True, "checked": False}

    raw_claims = _field(data, "claims", "items", "results", default=[])
    claims = [c for c in raw_claims
              if isinstance(c, dict) and _field(c, "claim", "statement", "text")]
    flagged = [c for c in claims if _verdict_of(c) == "FALSE"]

    # Confirm every FALSE verdict independently before acting on it.
    #
    # The first pass is deliberately primed with known failure modes ("watch for
    # mediator/confounder confusion"), which makes it sensitive but also prone
    # to crying wolf: in self-test it flagged the TRUE statement "adjusting for
    # a mediator removes part of the effect being estimated" — textbook
    # over-adjustment bias — purely because the claim mentioned a mediator.
    #
    # That matters more than a miss. A false positive triggers regeneration of
    # CORRECT content and invites the model to replace a true statement with a
    # confident new error. So a claim is only reported false if an unprimed
    # second look agrees.
    false = [c for c in flagged
             if _confirm_false(_field(c, "claim", "statement", "text"),
                               concept_title)]
    return {
        "claims": claims,
        "flagged": flagged,
        "false": false,
        "ok": len(false) <= MAX_FALSE_CLAIMS,
        "checked": True,
    }


_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["TRUE", "FALSE", "UNSURE"]},
        "why": {"type": "string"},
    },
    "required": ["verdict"],
}

_CONFIRM_SYSTEM = """Judge whether ONE statement is technically correct.

Answer only about the statement as written. Do not look for errors that are not
there — a correct statement about a subtle topic is still correct.

TRUE   — correct as written
FALSE  — incorrect, or reverses/overstates a real relationship
UNSURE — genuinely ambiguous, or depends on unstated context

If you are not confident it is wrong, answer TRUE or UNSURE, never FALSE.
Return STRICT JSON."""


def _confirm_false(claim, concept_title=""):
    """Second, unprimed opinion. Only a confident FALSE counts."""
    if not claim:
        return False
    ctx = f" (from teaching material on {concept_title})" if concept_title else ""
    d = _post([{"role": "system", "content": _CONFIRM_SYSTEM},
               {"role": "user", "content": f"Statement{ctx}:\n\n{claim}"}],
              schema=_CONFIRM_SCHEMA, max_tokens=300)
    if not d:
        # Can't confirm -> don't act. Better to miss than to corrupt good content.
        return False
    return _verdict_of(d) == "FALSE"


def correction_hint(false_claims):
    """Turn verified-false claims into a targeted regeneration instruction."""
    if not false_claims:
        return ""
    lines = []
    for c in false_claims[:5]:
        why = _field(c, "why", "reasoning", "explanation",
                     default="this is incorrect")
        claim = _field(c, "claim", "statement", "text", default="")
        lines.append(f'- "{str(claim)[:180]}" — {str(why)[:180]}')
    return ("The previous draft contained factually INCORRECT statements. Rewrite "
            "so that none of the following errors appear, and do not replace them "
            "with equally confident new errors — if you are unsure of a technical "
            "condition, state it qualitatively rather than as a formal criterion:\n"
            + "\n".join(lines))


# --- self-validation ---------------------------------------------------------

_KNOWN_FALSE = (
    "## Core Explanation\n"
    "A necessary and sufficient condition for endogeneity bias is the presence "
    "of a collider variable in the causal pathway between treatment (X) and "
    "outcome (Y). Controlling for a mediator removes confounding bias and always "
    "strengthens a causal estimate."
)

_KNOWN_TRUE = (
    "## Core Explanation\n"
    "A confounder is a variable that causally influences both the treatment and "
    "the outcome, producing an association between them that is not causal. "
    "Adjusting for a confounder can reduce bias; adjusting for a variable on the "
    "causal pathway (a mediator) instead removes part of the effect being "
    "estimated."
)


def self_test(verbose=True):
    """The checker must catch a known falsehood and not cry wolf on truth."""
    bad = check_content(_KNOWN_FALSE, concept_title="Endogeneity")
    good = check_content(_KNOWN_TRUE, concept_title="Confounding")
    caught = len(bad["false"]) > 0
    quiet = len(good["false"]) == 0
    if verbose:
        print(f"  known-FALSE content : {len(bad['false'])} false claim(s) "
              f"-> {'PASS' if caught else 'FAIL (missed the error)'}")
        for c in bad["false"][:3]:
            print(f"      caught: {str(_field(c, 'claim', 'statement', 'text'))[:90]}")
        print(f"  known-TRUE content  : {len(good['false'])} false claim(s) "
              f"-> {'PASS' if quiet else 'FAIL (false positive)'}")
        for c in good["false"][:2]:
            print(f"      wrongly flagged: {str(_field(c, 'claim', 'statement', 'text'))[:90]}")
    return caught and quiet


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    print("Fact-checker self-test (mode=%s)\n" % MODE)
    ok = self_test()
    print("\n  " + ("Checker is usable." if ok else
                    "CHECKER UNRELIABLE — do not trust its verdicts."))
    sys.exit(0 if ok else 1)
