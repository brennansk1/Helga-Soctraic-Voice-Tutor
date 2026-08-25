#!/usr/bin/env python3
"""MiniCheck as a host-native service, on the pattern Ollama and STT use.

WHY THIS IS NOT A CONTAINER
---------------------------
The checker needs torch and transformers. Both were deliberately removed from
the core and rag images — their requirements files say so directly, because the
stack was being pulled in for nothing once embeddings moved to Ollama. Putting
them back to run one check would re-add gigabytes to an image that otherwise
does not want them.

So the model stays on the host, exactly like the two dependencies this project
already reaches through `host.docker.internal`: Ollama at 11434 and the
Nemotron STT port at 5001. This is the third of the same shape.

WHAT IT ANSWERS, AND WHAT THAT IS WORTH
---------------------------------------
One question: is this claim supported by this passage. Not "is it true" — it
has no knowledge beyond the text it is handed, which is the point. A claim the
passage does not mention is unsupported, and that is different from false.

Measured on the seeded set, reproduced 2026-08-25:

    accuracy             4/6
    false claims caught  3/3      <- the direction that matters
    true claims flagged  2/3      <- why this is ADVISORY and cannot gate

Both false flags needed one inference step from the passage ("its mean is
(1+20)/2 = 10.5" judged not to support "the expected value is 10.5"). Both are
also COMPUTABLE, which is why the audit routes computable claims to execution
before they ever reach this model.

    python3 services/verifier/verifier_server.py [--port 5007]
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, jsonify, request

from services.core import claim_verifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Degraded is not dead. A missing model means truth goes unmeasured;
    it does not mean the service is broken, and the caller acts differently
    on those two."""
    ok = claim_verifier.available()
    loaded = claim_verifier._STATE.get("model") is not None
    return jsonify({
        "status": "ok" if ok else "degraded",
        "model": claim_verifier.MODEL_ID,
        "loaded": loaded,
        "reason": None if ok else "torch/transformers not importable here",
    }), (200 if ok else 503)


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    claim = (data.get("claim") or "").strip()
    passage = (data.get("passage") or "").strip()
    if not claim or not passage:
        return jsonify({"error": "claim and passage are both required"}), 400
    try:
        return jsonify({"supported": bool(
            claim_verifier.supported(claim, passage))}), 200
    except Exception as e:
        # Never guess. An error here is "not measured", and the caller must be
        # able to tell that from "measured and unsupported".
        logger.warning("verify failed: %s", e)
        return jsonify({"error": str(e)[:200]}), 503


@app.route("/verify_batch", methods=["POST"])
def verify_batch():
    """Many claims against ONE passage.

    This is the shape the audit actually has — a concept's claims all rest on
    the same few sources — and checking per-passage rather than per-claim is
    what makes the document prefix reusable instead of re-encoded per claim.
    """
    data = request.get_json(silent=True) or {}
    passage = (data.get("passage") or "").strip()
    claims = data.get("claims") or []
    if not passage or not isinstance(claims, list):
        return jsonify({"error": "passage and claims[] are required"}), 400
    out = []
    for c in claims[:200]:
        c = (c or "").strip()
        if not c:
            continue
        try:
            out.append({"claim": c,
                        "supported": bool(claim_verifier.supported(c, passage))})
        except Exception as e:
            out.append({"claim": c, "error": str(e)[:120]})
    return jsonify({"results": out}), 200


@app.route("/seeded_check", methods=["GET"])
def seeded():
    """The validation set, so the number is checkable rather than quoted."""
    return jsonify(claim_verifier.seeded_check()), 200


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int,
                   default=int(os.getenv("VERIFIER_PORT", "5007")))
    # Localhost by default. This answers questions about course content and has
    # no auth; binding it to every interface would be a decision nobody made.
    p.add_argument("--host", default=os.getenv("VERIFIER_HOST", "127.0.0.1"))
    a = p.parse_args()

    if not claim_verifier.available():
        logger.error("torch/transformers not importable — install them in the "
                     "interpreter running this service")
        return 1
    logger.info("loading %s …", claim_verifier.MODEL_ID)
    if claim_verifier.get_verifier() is None:
        logger.error("model failed to load")
        return 1
    logger.info("verifier ready on %s:%d", a.host, a.port)
    app.run(host=a.host, port=a.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
