"""A model tag that drifts once stays wrong forever.

`deploy.sh` builds the -ctx tag at 16384 and `docs/MODEL.md` documents 16384.
But deploy.sh only CREATED that tag when it was missing, and preflight only
refused a context below the hard floor — so a tag built once at 8192 passed
both, and every build silently truncated the research and prior-concept
material injected per concept while reporting green.

Found on 2026-08-25: nail-35b-a3b-ctx was serving 8192, and preflight had been
printing "✓ Context window 8192 tokens" for it.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_floor_and_the_target_are_different_numbers():
    from services.core.course_builder import (MIN_CONTEXT_TOKENS,
                                              WANTED_CONTEXT_TOKENS)
    assert WANTED_CONTEXT_TOKENS > MIN_CONTEXT_TOKENS, (
        "if the target equals the floor, a half-context model cannot be "
        "distinguished from a correct one")
    assert WANTED_CONTEXT_TOKENS == 16384


def test_preflight_flags_a_context_between_the_floor_and_the_target():
    src = _read("services", "core", "course_builder.py")
    i = src.find("ctx = _detect_context_window()")
    assert i > 0
    block = src[i:i + 2500]
    assert "WANTED_CONTEXT_TOKENS" in block, \
        "preflight still gives a tick to any context above the hard floor"
    assert "truncat" in block.lower()


def test_deploy_verifies_the_tag_it_already_has():
    deploy = _read("deploy.sh")
    i = deploy.find('if have "$MODEL"; then')
    assert i > 0
    block = deploy[i:i + 1600]
    assert "ollama show" in block, \
        "deploy.sh trusts that the tag exists without checking what it serves"
    assert "16384" in block


def test_deploy_and_the_docs_agree_on_the_number():
    deploy = _read("deploy.sh")
    doc = _read("docs", "MODEL.md")
    built = set(re.findall(r"num_ctx\s+(\d+)", deploy))
    documented = set(re.findall(r"num_ctx\s+(\d+)", doc))
    assert built == {"16384"}, f"deploy.sh builds {built}"
    assert "16384" in documented
