"""B27.1/B27.2-lite/B22.5/B26.5 smoke tests."""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.logging_utils import JsonFormatter, with_student


def test_json_formatter_carries_correlation():
    fmt = JsonFormatter("core-logic")
    rec = logging.LogRecord("helga", logging.INFO, "f.py", 1, "graded answer",
                            None, None)
    rec.student_id = "stu_abc12345"
    out = json.loads(fmt.format(rec))
    assert out["service"] == "core-logic"
    assert out["message"] == "graded answer"
    assert out["student_id"] == "stu_abc12345"


def test_with_student_adapter_binds_id(caplog):
    logger = with_student(logging.getLogger("helga.test"), "stu_xyz")
    with caplog.at_level(logging.INFO, logger="helga.test"):
        logger.info("turn complete")
    assert caplog.records[0].student_id == "stu_xyz"


def test_provenance_table_written(tmp_path):
    from services.common.storage import StorageManager
    st = StorageManager(str(tmp_path))
    conn = st.progress._get_db()
    conn.execute(
        "INSERT INTO hydration_provenance (course_uid, concept_uid, sources, model) "
        "VALUES ('course_x', 'con_1', '[]', 'qwen3.5:9b')")
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM hydration_provenance WHERE course_uid='course_x'").fetchall()
    assert len(rows) == 1
