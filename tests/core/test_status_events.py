"""Tests for the structured status-event contract (B6.4 / Task #6)."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/core')))

core_deps = {
    'kuzu': MagicMock(), 'libzim': MagicMock(), 'sentence_transformers': MagicMock(),
    'psutil': MagicMock(), 'yaml': MagicMock(), 'fsrs_engine': MagicMock(),
    'safety': MagicMock(), 'service_manager': MagicMock(), 'db_manager': MagicMock(),
    'content_provider': MagicMock(), 'course_builder': MagicMock(),
}
with patch.dict('sys.modules', core_deps):
    import services.core.fsm_logic as fsm_mod
FSM = fsm_mod.MnemosyneFSM


def _make_fsm():
    with patch.object(FSM, '__init__', lambda self, *a, **k: None):
        fsm = FSM.__new__(FSM)
    fsm.web_ui_url = 'http://web-ui:5000'
    fsm.student_id = 'stu_test0001'  # B15.5: every status payload is stamped
    return fsm


def test_status_update_includes_event_when_given():
    fsm = _make_fsm()
    with patch.object(fsm_mod, 'requests') as req:
        fsm.send_status_update("Hydrating", event={"type": "PIPELINE_STAGE", "stage": "HYDRATE"})
        body = req.post.call_args.kwargs['json']
        assert body['message'] == "Hydrating"
        assert body['event'] == {"type": "PIPELINE_STAGE", "stage": "HYDRATE"}


def test_status_update_omits_event_key_when_none():
    fsm = _make_fsm()
    with patch.object(fsm_mod, 'requests') as req:
        fsm.send_status_update("plain")
        assert 'event' not in req.post.call_args.kwargs['json']


def test_pipeline_stage_builds_structured_event():
    fsm = _make_fsm()
    with patch.object(fsm_mod, 'requests') as req:
        fsm.send_pipeline_stage("HYDRATE", pct=40, title="Photosynthesis", completed=2, total=5)
        body = req.post.call_args.kwargs['json']
        ev = body['event']
        assert ev['type'] == 'PIPELINE_STAGE'
        assert ev['stage'] == 'HYDRATE'
        assert ev['pct'] == 40
        assert ev['title'] == 'Photosynthesis'
        assert ev['completed'] == 2 and ev['total'] == 5
        assert body['progress'] == 40
        assert body['message']  # human-readable fallback present


def test_pipeline_stage_pct_clamped():
    fsm = _make_fsm()
    with patch.object(fsm_mod, 'requests') as req:
        fsm.send_pipeline_stage("DONE", pct=150)
        assert req.post.call_args.kwargs['json']['event']['pct'] == 100
    with patch.object(fsm_mod, 'requests') as req:
        fsm.send_pipeline_stage("PREFLIGHT", pct=-5)
        assert req.post.call_args.kwargs['json']['event']['pct'] == 0


def test_call_llm_forwards_images_to_client():
    # B13: the Socratic loop can pass an image to the multimodal model.
    fsm = _make_fsm()
    fsm.llm_client = MagicMock()
    fsm.llm_client.chat.return_value = "It's a force diagram."
    out = fsm._call_llm([{"role": "system", "content": "Discuss the image."}],
                        images=["data:image/png;base64,AAAA"])
    assert out == "It's a force diagram."
    assert fsm.llm_client.chat.call_args.kwargs['images'] == ["data:image/png;base64,AAAA"]


# ---------------------------------------------------------------------------
# THE SKELETON PHASE IS THE LONG ONE AND HAD NO MOVEMENT IN IT.
#
# creation_status walks a fixed ladder: 10 skeleton, 30 audit, 40 hydration,
# 100 complete. Skeleton is where the hours go -- an HTTP-status-codes build
# sat at "Building... 10%" for two hours while working normally, which is
# indistinguishable from a hang. The builder knows its module ordinal and
# total; these map it onto the 10-30 band.
# ---------------------------------------------------------------------------

def test_module_progress_advances_the_skeleton_bar():
    fsm = _make_fsm()
    fsm.creation_status = {"phase": "skeleton", "progress_pct": 10}
    with patch.object(fsm_mod, 'requests'):
        fsm.send_status_update("STRUCT:MODULE_PROGRESS:1:6")
        first = fsm.creation_status['progress_pct']
        fsm.send_status_update("STRUCT:MODULE_PROGRESS:4:6")
        later = fsm.creation_status['progress_pct']

    assert first == 10, "starting the first module is 0/6 done"
    assert later > first, "the bar must move as modules land"
    assert fsm.creation_status['modules_done'] == 3
    assert fsm.creation_status['modules_total'] == 6
    assert fsm.creation_status['phase'] == 'skeleton'


def test_module_progress_never_reaches_the_next_phase():
    """Audit owns 30. Skeleton must stop short of it however many modules."""
    fsm = _make_fsm()
    fsm.creation_status = {"phase": "skeleton", "progress_pct": 10}
    with patch.object(fsm_mod, 'requests'):
        fsm.send_status_update("STRUCT:MODULE_PROGRESS:6:6")
        assert fsm.creation_status['progress_pct'] < 30
        fsm.send_status_update("STRUCT:MODULE_PROGRESS:99:6")
        assert fsm.creation_status['progress_pct'] < 30


def test_a_malformed_progress_line_never_stops_a_build():
    fsm = _make_fsm()
    fsm.creation_status = {"phase": "skeleton", "progress_pct": 10}
    with patch.object(fsm_mod, 'requests'):
        for bad in ("STRUCT:MODULE_PROGRESS:",
                    "STRUCT:MODULE_PROGRESS:x:y",
                    "STRUCT:MODULE_PROGRESS:1",
                    "STRUCT:MODULE_PROGRESS:1:0"):
            fsm.send_status_update(bad)     # must not raise


def test_ordinary_status_messages_do_not_touch_progress():
    fsm = _make_fsm()
    fsm.creation_status = {"phase": "hydration", "progress_pct": 40}
    with patch.object(fsm_mod, 'requests'):
        fsm.send_status_update("STRUCT:MODULE:Redirection and Persistence")
        fsm.send_status_update("LOG: Generating Units for module: X")
    assert fsm.creation_status['progress_pct'] == 40


def test_progress_parsing_survives_an_fsm_without_creation_status():
    """send_status_update is called from every corner of the FSM, including
    contexts that never run a build. An AttributeError here would take out
    status reporting everywhere, so the hook must simply not engage."""
    fsm = _make_fsm()
    if hasattr(fsm, 'creation_status'):
        del fsm.creation_status
    with patch.object(fsm_mod, 'requests'):
        fsm.send_status_update("STRUCT:MODULE_PROGRESS:2:6")   # must not raise
