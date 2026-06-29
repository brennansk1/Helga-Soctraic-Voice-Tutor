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
