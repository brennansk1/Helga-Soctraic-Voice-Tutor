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


def test_they_are_not_presented_as_required():
    """Requiring them would refuse content that is fine, and the depth
    contract is the thing that refuses."""
    from services.rag.pipeline_api import CONSUMED_SECTIONS
    src = _read("services", "rag", "pipeline_api.py")
    i = src.find("sections_note")
    note = src[i:i + 500]
    assert "Not required" in note or "not required" in note
    # and no validator gates on them
    assert "CONSUMED_SECTIONS" not in src[src.find("def _validate"):
                                          src.find("def _validate") + 1500]
