"""Claim verification — does a retained passage actually support this claim?

THE GAP THIS FILLS
------------------
Every instrument that passes today is structural. `skeleton_qa` reads titles and
counts; the depth contract checks that rigour is PRESENT; `check_filler` finds
padding. Not one of them reads content for truth — and the measured failure is
that generated courses state verified false claims while passing all of them.
Fluency is not accuracy.

WHY MiniCheck AND NOT THE TUTOR'S OWN MODEL
--------------------------------------------
The existing `fact_check` asks the generating model whether it was right, which
is grading its own homework, and the LLM judge in this repo swings +/-1.4 out of
5 between identical runs.

MiniCheck-Flan-T5-Large is a 770M NLI model trained for exactly one job:
sentence-level "is this claim supported by this document". It reaches ~74.7%
balanced accuracy on LLM-AggreFact against GPT-4's 75.3%, at roughly 400x lower
cost, and runs on CPU.

It does NOT eliminate the self-grading problem — it is still a model, and one
whose training distribution is not ours. **Validate it on a seeded false-claim
set before gating anything on its verdict.** `seeded_check()` below exists for
that and is the thing to run first.

MEASURED ON THE SEED SET, 2026-08-19 — READ THIS BEFORE GATING
--------------------------------------------------------------
    accuracy             4/6  (0.667)
    false claims caught  3/3      <- the direction that matters
    true claims flagged  2/3      <- the direction that makes it unusable as a gate

It caught **every** falsehood, and it also rejected two *true* claims that need
one step of inference from the passage:

    claim   "The expected value of a fair twenty-sided die is 10.5."
    passage "A fair d20 is uniform over 1 to 20, so its mean is (1+20)/2 = 10.5."
    verdict UNSUPPORTED

    claim   "Rank plus nullity equals the number of columns."
    passage "...rank plus nullity equals the dimension of the domain."
    verdict UNSUPPORTED

MiniCheck wants near-verbatim support and does not carry an inference. Teaching
material is *written* to rephrase and generalise its sources, so this failure
mode is not incidental here — it is the norm.

**Consequence: this is a FLAGGING instrument, not a gate.** A high unsupported
rate means "a human should look", never "the course is wrong". `hydration_qa`
therefore reports `truth` as advisory rather than failing a course on it, and
that stays true until the false-positive rate is measured on real content and
found acceptable.

The coarse claim-to-source attribution in `_retain_sources` makes this worse and
is the first thing to fix if the rate is to improve: claims are currently linked
to a concept's source SET rather than to the passage each was drawn from, so
some pairs the verifier sees were never meant to match.

MEMORY
------
~0.73 GB int8 / ~1.46 GB fp16 against a measured ~15.0 GB ceiling on this
machine (docs/MEMORY_ALLOCATION_PLAN.md). At the 32k context the tutor model is
13.51 GB, so fp16 co-resident lands at 14.97 GB — technically under, but 0.03 GB
is not a margin. Either run this in its own phase with the LLM unloaded, or use
int8, which leaves ~0.8 GB. `unload()` exists so a caller can hand the memory
back rather than holding it for the rest of a build.
"""

import logging
import os

logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("MINICHECK_MODEL", "lytang/MiniCheck-Flan-T5-Large")

# The model answers a yes/no entailment question; these are its label tokens.
_YES, _NO = "1", "0"

_STATE = {"model": None, "tok": None, "failed": False}


def available():
    """Is a verifier usable right now? Never raises."""
    if _STATE["failed"]:
        return False
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    if _STATE["model"] is not None:
        return True
    if _STATE["failed"] or not available():
        return False
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        _STATE["tok"] = AutoTokenizer.from_pretrained(MODEL_ID)
        _STATE["model"] = AutoModelForSeq2SeqLM.from_pretrained(
            MODEL_ID, torch_dtype=torch.float32)
        _STATE["model"].eval()
        logger.info(f"[VERIFY] loaded {MODEL_ID}")
        return True
    except Exception as e:
        # A missing verifier must never fail a build. It makes the truth check
        # report NOT RUN, which the QA harness never counts as a pass.
        logger.warning(f"[VERIFY] unavailable, truth will report NOT RUN: {e}")
        _STATE["failed"] = True
        return False


def unload():
    """Hand the memory back. Matters on a 24 GB box with a 12.7 GB model."""
    _STATE["model"] = None
    _STATE["tok"] = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def supported(claim, passage):
    """True if `passage` supports `claim`. None when it cannot be judged.

    None rather than False on failure, deliberately: an unjudgeable claim is not
    a false one, and collapsing those two is how a broken verifier starts
    reporting a course as full of errors — or, worse, how an unrun check gets
    counted as a pass.
    """
    if not claim or not passage:
        return None
    if not _load():
        return None
    try:
        import torch
        tok, model = _STATE["tok"], _STATE["model"]
        prompt = (f"predict: {passage[:3500]}\n\nclaim: {claim[:500]}")
        inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=2048)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=2)
        answer = tok.decode(out[0], skip_special_tokens=True).strip()
        if answer.startswith(_YES):
            return True
        if answer.startswith(_NO):
            return False
        # An answer in neither label is not evidence either way.
        logger.debug(f"[VERIFY] unparsed verdict {answer!r}")
        return None
    except Exception as e:
        logger.debug(f"[VERIFY] check failed: {e}")
        return None


def get_verifier():
    """A callable for the QA harness. Unsupported-or-unknown -> False is WRONG,
    so an unjudgeable pair is treated as supported and excluded from the count
    by the caller filtering on None."""
    if not _load():
        raise RuntimeError("verifier unavailable")

    def _v(claim, passage):
        r = supported(claim, passage)
        # Only an affirmative NO counts against a course. Unknown is not a
        # defect, and treating it as one would make a slow or truncated check
        # look like a quality collapse.
        return r is not False
    return _v


# --- validation before trusting it -------------------------------------------

SEED_SET = [
    # (claim, passage, expected_supported)
    ("Water boils at 100 degrees Celsius at sea level.",
     "At standard atmospheric pressure water boils at 100 degrees Celsius.", True),
    ("Water boils at 50 degrees Celsius at sea level.",
     "At standard atmospheric pressure water boils at 100 degrees Celsius.", False),
    ("The expected value of a fair twenty-sided die is 10.5.",
     "A fair d20 is uniform over 1 to 20, so its mean is (1+20)/2 = 10.5.", True),
    ("The expected value of a fair twenty-sided die is 20.",
     "A fair d20 is uniform over 1 to 20, so its mean is (1+20)/2 = 10.5.", False),
    ("Rank plus nullity equals the number of columns.",
     "The rank-nullity theorem states that for a linear map, rank plus nullity "
     "equals the dimension of the domain.", True),
    ("Rank plus nullity equals the number of rows.",
     "The rank-nullity theorem states that for a linear map, rank plus nullity "
     "equals the dimension of the domain.", False),
]


def seeded_check():
    """Run the verifier against known-true and known-false pairs.

    **Run this before gating anything on the verdicts.** The plan is explicit
    that MiniCheck reduces rather than eliminates the shared-blind-spot risk,
    and a verifier that says "supported" to everything is indistinguishable from
    a working one until you feed it something false.
    """
    if not _load():
        return {"ran": False, "reason": "verifier unavailable"}
    results, correct = [], 0
    for claim, passage, expected in SEED_SET:
        got = supported(claim, passage)
        ok = (got == expected)
        correct += int(ok)
        results.append({"claim": claim[:60], "expected": expected,
                        "got": got, "ok": ok})
    # False negatives are the ones that matter: a verifier that never says NO
    # provides no protection at all.
    caught = sum(1 for r, (_, _, e) in zip(results, SEED_SET)
                 if not e and r["got"] is False)
    false_total = sum(1 for _, _, e in SEED_SET if not e)
    return {
        "ran": True, "cases": len(SEED_SET), "correct": correct,
        "accuracy": round(correct / len(SEED_SET), 3),
        "false_claims_caught": f"{caught}/{false_total}",
        "results": results,
        # The decisive number. Accuracy can look fine while every miss is on
        # the falsehoods, which is the only direction that matters here.
        "usable": caught == false_total,
    }
