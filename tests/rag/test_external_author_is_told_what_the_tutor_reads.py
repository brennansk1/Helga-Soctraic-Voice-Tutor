"""The tutor reads sections out of the markdown. Say so.

`teaching_context` extracts "## Misconceptions" and "## Analogies" from a
concept body and hands them to the tutor turn; the asset collector reads
Misconceptions too. The local generator emits those headings on every concept,
so the local pipeline gets them for free.

An external author was told the word range and the required elements and
nothing about this. Measured: a Claude-authored course that met 100% of its
depth contract returned {"misconceptions": [], "analogies": []} for every
concept — it passed every gate and taught with less than a locally built
course would have.

They are deliberately NOT required. A concept without them is stored and
teaches; it teaches worse, and a caller should be told rather than find out.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_sections_the_tutor_reads_are_published():
    from services.rag.pipeline_api import CONSUMED_SECTIONS
    assert "## Misconceptions" in CONSUMED_SECTIONS
    assert "## Analogies" in CONSUMED_SECTIONS


def test_every_section_the_code_extracts_is_listed():
    """If someone teaches the tutor to read a new section, this fails until
    the external author is told about it — the drift that produced the gap."""
    from services.rag.pipeline_api import CONSUMED_SECTIONS
    import re

    extracted = set()
    for mod in (("services", "rag", "librarian.py"),
                ("services", "core", "asset_collector.py")):
        src = _read(*mod)
        for m in re.finditer(r'_extract_section\([^,]+,\s*"([^"]+)"', src):
            extracted.add(m.group(1))

    listed = {k.replace("## ", "") for k in CONSUMED_SECTIONS}
    missing = extracted - listed
    assert not missing, (
        f"the product reads {sorted(missing)} out of concept markdown and the "
        f"pipeline contract never mentions it")


def test_the_contract_endpoint_carries_them():
    src = _read("services", "rag", "pipeline_api.py")
    i = src.find("def _writing_standard")
    assert i > 0
    assert "sections_the_product_reads" in src[i:i + 1500]


def test_ingest_still_refuses_nothing():
    """Replaces test_they_are_not_presented_as_required.

    That test asserted the contract must call these sections optional, on the
    reasoning that "requiring them would refuse content that is fine, and the
    depth contract is the thing that refuses". Half of that has been falsified
    by two real courses: "Reading a Query Plan" and "Practical Regular
    Expressions" were authored through this surface, both MET their depth
    contract, and both are unusable. The depth contract is not the thing that
    refuses — course_audit's gate is, on exactly Core Explanation,
    Misconceptions and Analogies.

    Its other half still stands and is what this keeps: nothing is rejected at
    ingest. A concept is stored whatever headings it carries; what changed is
    that finalize now says plainly that the course cannot be taught, instead of
    the author discovering it from a different subsystem at teach time.
    """
    src = _read("services", "rag", "pipeline_api.py")
    v = src.find("def _validate")
    assert v > 0
    assert "CONSUMED_SECTIONS" not in src[v:v + 1500], \
        "ingest validation must not start rejecting on section headings"
    assert "is_teachable" not in src[v:v + 1500], \
        "teachability belongs to finalize's verdict, not to ingest"


# ---------------------------------------------------------------------------
# THE CONTRACT SAID OPTIONAL; THE GATE SAYS REQUIRED.
#
# course_audit.TUTOR_SECTIONS is what is_teachable() checks, and the audit gate
# refuses a course whose concepts lack them — needs_review, "there is no lesson
# to teach", not openable. The contract told external authors those sections
# were "not required and not enforced".
#
# It cost two real courses. "Reading a Query Plan" and "Practical Regular
# Expressions" were both authored through this surface, both met their depth
# contract, and both are unusable: good prose under the wrong headings, which
# is what an author who believed the note would write.
# ---------------------------------------------------------------------------

def test_the_contract_does_not_call_the_gated_sections_optional():
    import re
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "rag" / "pipeline_api.py").read_text()
    assert "Not required and not enforced" not in src, (
        "the contract still tells external authors that the sections the "
        "audit gate enforces are optional")
    assert "sections_required" in src, (
        "the contract should name the required sections explicitly")


def test_finalize_refuses_ready_for_a_course_the_tutor_cannot_teach():
    """A depth contract met by an unteachable course is not readiness."""
    import inspect
    from services.rag import pipeline_api
    src = inspect.getsource(pipeline_api)
    assert "is_teachable(body)" in src, \
        "finalize does not check teachability"
    assert 'passing == total and not unteachable' in src, \
        "finalize can still mark an unteachable course ready"


def test_teachability_has_one_definition_shared_with_the_gate():
    """Two copies would be free to disagree, which is how the gate and the
    course list once disagreed about the same course."""
    from services.rag import pipeline_api
    from services.core import course_audit
    assert pipeline_api.is_teachable is course_audit.is_teachable
    assert pipeline_api.TUTOR_SECTIONS is course_audit.TUTOR_SECTIONS


def test_the_wizard_route_stores_what_the_learner_said_they_wanted():
    """ContentHydrator.hydrate() reads course["learner_context"] and puts it in
    front of the model for every concept. /api/custom_course/create stored the
    description only as `overview`, which nothing reads at build time.

    Measured after rewiring the wizard through this route: "I use regex daily
    but lookahead still confuses me" reached the server and the finished course
    had learner_context empty. The earlier fix set it on the FSM path — the one
    the wizard no longer takes — so the reroute quietly undid it.
    """
    import inspect
    import re
    from services.rag import librarian
    from services.core.course_builder import ContentHydrator

    src = inspect.getsource(librarian)
    i = src.index("def create_custom_course_wizard")
    j = src.index("@app.route", i)
    body = re.sub(r'#[^\n]*', ' ', src[i:j])
    assert '"learner_context": description' in body, (
        "the wizard route drops the learner's own words")

    # And the hydrator must still be reading that key.
    assert 'course.get("learner_context")' in inspect.getsource(ContentHydrator), (
        "the hydrator no longer reads learner_context off the course")
