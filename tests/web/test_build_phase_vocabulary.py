"""Every phase the server reports must map to a stage the build page can show.

These are two hand-maintained vocabularies in two languages, and they drifted:
the server reports `skeleton -> audit -> hydration -> complete`, while the
stage rail is named `preflight, research, skeleton, coverage, hydrate, assets`.
"audit" and "hydration" appear in neither the rail nor its ORDER list, so
`ORDER.indexOf(phase)` returned -1 for both and the rail sat on "Structure" for
an entire multi-hour build while the server reported progress the whole time.

Nothing failed. The page just quietly stopped telling the truth — which is the
same failure mode as the silent ImportError behind "Scope check unavailable"
and the CORS 400 behind "Warming up...". A vocabulary shared across a process
boundary needs a test, not a comment.
"""
import os
import re

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
_FSM = os.path.join(_ROOT, "services/core/fsm_logic.py")
_VIEW = os.path.join(_ROOT, "services/web-ui/static/js/build-view.js")

#: Phases that end the build rather than advancing the rail. They are handled
#: explicitly by the poller and deliberately have no stage of their own.
_TERMINAL = {"complete", "error", "aborted", "cancelled"}


def _phases_the_server_reports():
    src = open(_FSM, encoding="utf-8").read()
    return {m.group(1) for m in
            re.finditer(r'creation_status(?:\[|\.update\(\{\s*)?["\']?phase["\']?'
                        r'\s*[:=]\s*["\'](\w+)["\']', src)} | {
        m.group(1) for m in
        re.finditer(r'"phase":\s*"(\w+)"', src)}


def _stages_the_page_can_show():
    src = open(_VIEW, encoding="utf-8").read()
    m = re.search(r'PHASE_TO_STAGE\s*=\s*\{(.*?)\}', src, re.S)
    assert m, "PHASE_TO_STAGE table not found — the mapping was removed"
    return {k for k in re.findall(r'(\w+)\s*:', m.group(1))}


def test_every_reported_phase_can_be_displayed():
    reported = _phases_the_server_reports()
    assert reported, "found no phases in fsm_logic — the scraper is broken"
    mappable = _stages_the_page_can_show() | _TERMINAL
    missing = sorted(reported - mappable)
    assert not missing, (
        f"the server reports {missing} and the build page maps none of them; "
        f"the stage rail will freeze on whatever came before")


def test_the_rail_order_contains_every_mapped_stage():
    """A stage the mapping points at must actually exist in ORDER, or
    setStage() looks it up, gets -1, and silently does nothing."""
    src = open(_VIEW, encoding="utf-8").read()
    order = re.search(r"var ORDER = \[(.*?)\]", src, re.S)
    assert order
    names = set(re.findall(r"'(\w+)'", order.group(1)))
    m = re.search(r'PHASE_TO_STAGE\s*=\s*\{(.*?)\}', src, re.S)
    targets = set(re.findall(r":\s*'(\w+)'", m.group(1)))
    assert targets <= names, f"mapped to stages the rail has no element for: {sorted(targets - names)}"
