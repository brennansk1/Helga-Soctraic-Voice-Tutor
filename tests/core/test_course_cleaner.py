import os
import json
import shutil
import pytest
import tempfile
from services.common.course_cleaner import clean_failed_courses

@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        courses_dir = os.path.join(tmp, "courses")
        os.makedirs(courses_dir)
        yield tmp

def test_clean_failed_courses(temp_data_dir):
    courses_dir = os.path.join(temp_data_dir, "courses")
    
    # 1. Create a "good" course
    good_dir = os.path.join(courses_dir, "course_good")
    os.makedirs(good_dir)
    with open(os.path.join(good_dir, "structure.json"), "w") as f:
        json.dump({"uid": "course_good", "status": "ready"}, f)
        
    # 2. Create a "failed" course
    failed_dir = os.path.join(courses_dir, "course_failed")
    os.makedirs(failed_dir)
    with open(os.path.join(failed_dir, "structure.json"), "w") as f:
        json.dump({"uid": "course_failed", "status": "failed"}, f)
        
    # 3. Create a "hydration_failed" course
    hydration_failed_dir = os.path.join(courses_dir, "course_hydration_failed")
    os.makedirs(hydration_failed_dir)
    with open(os.path.join(hydration_failed_dir, "structure.json"), "w") as f:
        json.dump({"uid": "course_hydration_failed", "status": "hydration_failed"}, f)

    # 4. Create a folder without structure.json (incomplete build — should be removed)
    other_dir = os.path.join(courses_dir, "other")
    os.makedirs(other_dir)

    # Run cleaner
    clean_failed_courses(temp_data_dir)

    # Verify
    assert os.path.exists(good_dir)
    assert not os.path.exists(failed_dir)
    assert not os.path.exists(hydration_failed_dir)
    assert not os.path.exists(other_dir)  # No structure.json = incomplete, removed
