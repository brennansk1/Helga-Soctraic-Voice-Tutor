"""B21 tests: Health Strand 6 consent gating, output moderation, profanity /
abuse-disclosure categories, crisis escalation to the parent.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'services/core'))

import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

from services.common.storage import StorageManager
from services.common.prompts import (get_socratic_tutor_prompt,
                                     HEALTH_STRAND6_FRAMING)

core_deps = {'yaml': MagicMock(), 'kuzu': MagicMock(), 'libzim': MagicMock()}
with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM
    from safety import (check_safety_detailed, check_output_safety,
                        check_profanity, get_refusal_message)

import tempfile
import shutil


class TestSafetyCategories(unittest.TestCase):
    def test_abuse_disclosure_supportive_not_topical(self):
        r = check_safety_detailed("my uncle hits me and told me not to tell")
        assert not r.is_safe and r.category == "abuse_disclosure"
        assert "Childhelp" in r.message and "not your fault" in r.message

    def test_profanity_band_aware(self):
        assert not check_profanity("this damn lesson", "K-2").is_safe
        assert check_profanity("this damn lesson", "9-12").is_safe   # mild ok for teens
        assert not check_profanity("fuck this", "9-12").is_safe      # hard words never ok

    def test_profanity_refusal_never_shames(self):
        msg = get_refusal_message("profanity", "K-2")
        assert "kind words" in msg

    def test_output_moderation_flags_and_substitutes(self):
        r = check_output_safety("Here is explicit sexual pornography content", None, "3-5")
        assert not r.is_safe
        assert "lesson" in r.message.lower()

    def test_output_moderation_respects_educational_context(self):
        r = check_output_safety(
            "The reproduction cycle involves hormones and genetics",
            node_title="Biology: Reproduction and Genetics", grade_band="9-12")
        assert r.is_safe, f"biology false-flagged: {r.category}"

    def test_safe_output_passes(self):
        assert check_output_safety("What is 3 plus 4?", "Math", "K-2").is_safe


def _fsm_with_storage(tmpdir, grade_band="6-8"):
    storage = StorageManager(tmpdir)
    pid = storage.accounts.create_parent("c@example.com", "h", status="active")
    sid = storage.accounts.create_student(pid, "Kid", grade_band=grade_band)
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    fsm.student_id = sid
    fsm.grade_band = grade_band
    fsm.storage = storage
    fsm.current_lesson_node = None
    fsm.transcript = []
    fsm.send_status_update = MagicMock()
    fsm.active_course_uid = "course_hd1"
    return fsm, storage, pid, sid


class TestHealthStrand6Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helga_hd_")
        self.fsm, self.storage, self.pid, self.sid = _fsm_with_storage(self.tmp)
        self.storage.standards.upsert(
            "HD.1", "health", "Human Development",
            "Human development and reproduction basics.", grade_band="6-8")
        self.storage.standards.upsert(
            "6.RP", "math", "Ratios", "Ratio reasoning.", grade_band="6-8")
        self.storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_hd_body", "standard_code": "HD.1"},
                 {"concept_uid": "con_math_ok", "standard_code": "6.RP"}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hd_helper(self):
        assert self.storage.standards.concept_is_health_strand6("con_hd_body")
        assert not self.storage.standards.concept_is_health_strand6("con_math_ok")

    def test_gated_without_consent(self):
        assert self.fsm._hd_consent_blocked("con_hd_body") is True
        assert self.fsm._hd_consent_blocked("con_math_ok") is False

    def test_consent_unblocks_and_withdrawal_regates(self):
        self.storage.consent.record(self.pid, "health_strand6", True, "v1",
                                    student_id=self.sid)
        assert self.fsm._hd_consent_blocked("con_hd_body") is False
        # withdrawal re-gates instantly (latest record wins)
        self.storage.consent.record(self.pid, "health_strand6", False, "v1",
                                    student_id=self.sid)
        assert self.fsm._hd_consent_blocked("con_hd_body") is True

    def test_get_concept_details_refuses_gated(self):
        result = self.fsm.get_concept_details("con_hd_body")
        assert result is None
        event = self.fsm.send_status_update.call_args.kwargs.get("event")
        assert event and event["type"] == "CONSENT_REQUIRED"
        # the refusal itself was spoken to the child
        assert any("parent" in t["text"].lower() for t in self.fsm.transcript)

    def test_hd_framing_in_prompt_only_when_gated(self):
        with_hd = get_socratic_tutor_prompt("ctx", [("a", "b")],
                                            grade_band="6-8", health_strand6=True)
        without = get_socratic_tutor_prompt("ctx", [("a", "b")], grade_band="6-8")
        assert "abstinence before marriage" in with_hd[0]["content"]
        assert "abstinence" not in without[0]["content"]


class TestOutputModerationInFSM(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helga_om_")
        self.fsm, self.storage, self.pid, self.sid = _fsm_with_storage(self.tmp, "3-5")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flagged_output_suppressed(self):
        self.fsm.add_message("Let me tell you about explicit pornography now")
        assert self.fsm.transcript, "nothing recorded"
        text = self.fsm.transcript[-1]["text"]
        assert "porn" not in text.lower()
        assert "lesson" in text.lower()
        incidents = self.storage.audit.list(subject_student_id=self.sid)
        assert any(a["action"] == "safety_incident" for a in incidents)

    def test_safe_output_delivered_verbatim(self):
        self.fsm.add_message("What is 3 plus 4?")
        assert self.fsm.transcript[-1]["text"] == "What is 3 plus 4?"


class TestCrisisEscalation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helga_esc_")
        self.fsm, self.storage, self.pid, self.sid = _fsm_with_storage(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_escalation_creates_parent_alert_without_transcript(self):
        self.fsm._escalate_safety("self_harm")
        alerts = self.storage.notifications.list_for(self.pid, unread_only=True)
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "struggle_alert"
        assert "check in" in alerts[0]["body"].lower()
        # the child's words are never stored in the alert
        assert "self_harm" not in (alerts[0]["body"] or "")
        assert any(a["action"] == "safety_incident"
                   for a in self.storage.audit.list(subject_student_id=self.sid))

    def test_escalation_never_raises(self):
        self.fsm.storage = MagicMock()
        self.fsm.storage.accounts.get_student.side_effect = RuntimeError("db gone")
        self.fsm._escalate_safety("abuse_disclosure")   # must not raise


if __name__ == '__main__':
    unittest.main()
