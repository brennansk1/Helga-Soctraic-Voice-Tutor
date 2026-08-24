
import pytest
from unittest.mock import MagicMock, patch, ANY, call
import sys
import os
import json

# Add project root and services/core to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/core')))

# Mock Flask
class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw):
        return lambda f: f
    def run(self, *a, **kw): pass

flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()

cb_mock = MagicMock()
cb_mock.__file__ = 'mocked_course_builder.py'

# Mock dependencies before import
core_deps = {
    'requests': MagicMock(),
    'yaml': MagicMock(),
    'psutil': MagicMock(),
    'subprocess': MagicMock(),
    'fsrs_engine': MagicMock(),
    'safety': MagicMock(),
    'service_manager': MagicMock(),
    'db_manager': MagicMock(),
    'content_provider': MagicMock(),
    'course_builder': cb_mock,
    'kuzu': MagicMock(),
    'libzim': MagicMock(),
    'sentence_transformers': MagicMock(),
    'docker': MagicMock(),
    'docker.errors': MagicMock(),
}
with patch.dict('sys.modules', core_deps):
    from services.core.fsm_logic import MnemosyneFSM

@pytest.fixture
def fsm():
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm_instance = MnemosyneFSM.__new__(MnemosyneFSM)
        fsm_instance.student_id = 'stu_test0001'
        fsm_instance.grade_band = '6-8'

    # Set all attributes that __init__ normally creates
    fsm_instance.dev_mode = True
    fsm_instance.config = {}
    fsm_instance.state = 'LOBBY'
    fsm_instance.fsrs = MagicMock()
    fsm_instance.security_token = 'mock-token'
    fsm_instance.data_root = '/tmp/test_data'
    fsm_instance.state_file = '/tmp/test_data/user_state.json'

    fsm_instance.active_course_uid = None
    fsm_instance.creation_in_progress = False
    fsm_instance._creation_thread = None
    fsm_instance.creation_status = {
        'active': False, 'topic': None, 'phase': None,
        'started_at': None, 'course_uid': None, 'progress_pct': 0, 'last_update': None,
    }

    fsm_instance.user_level = 5
    fsm_instance.current_context = ''
    fsm_instance.syllabus_queue = []
    fsm_instance.current_lesson_node = None
    fsm_instance.completed_topics = set()
    fsm_instance.last_lesson_title = None
    fsm_instance.conversation_history = []
    fsm_instance.transcript = []
    fsm_instance.last_question = ''

    fsm_instance.review_queue = []
    fsm_instance.previous_state = None
    fsm_instance.current_card = None
    fsm_instance.card_attempts = 0

    fsm_instance.current_locus_uid = None
    fsm_instance.current_locus_desc = ''
    fsm_instance.temp_anchor_concept = None

    fsm_instance.last_interaction_time = 0
    fsm_instance.question_start_time = 0
    fsm_instance.battery_level = 100

    fsm_instance.socratic_type_index = 0
    fsm_instance.socratic_retry_count = 0
    fsm_instance._last_socratic_grade = 3

    fsm_instance.concept_correct_streak = 0
    fsm_instance.concept_question_count = 0

    fsm_instance.current_bloom_level = 1
    fsm_instance.bloom_correct_streak = 0
    # Bloom progression attributes
    fsm_instance.course_bloom_floor = 1
    fsm_instance.course_bloom_ceiling = 6
    fsm_instance.concept_bloom_target = 1
    fsm_instance.passed_question_types = {"SCENARIO", "MECHANISM", "CONTRAST"}
    fsm_instance.prior_concepts_summary = []

    fsm_instance.current_misconceptions = []
    fsm_instance.current_analogies = []
    fsm_instance.current_teaching_style = ''
    fsm_instance.user_profile = None

    fsm_instance.draft_course_structure = None
    fsm_instance.draft_course_topic = ''
    fsm_instance.draft_course_depth = 3
    fsm_instance.draft_teaching_style = ''

    fsm_instance.pre_assessment_questions = []
    fsm_instance.pre_assessment_answers = {}
    fsm_instance.pre_assessment_module_depths = {}

    fsm_instance.maintenance_mode = False
    fsm_instance.maintenance_paused = False

    fsm_instance.tts_enabled = True
    fsm_instance.current_concept_uid = None
    fsm_instance.current_concept_title = None
    fsm_instance.current_resource_text = None

    # Mock external interaction methods
    fsm_instance.speak = MagicMock()
    fsm_instance.play_sound = MagicMock()
    fsm_instance.start_listening = MagicMock()
    fsm_instance.stop_audio = MagicMock()
    fsm_instance.send_status_update = MagicMock()
    fsm_instance.send_event = MagicMock()
    fsm_instance.append_session_note = MagicMock()
    fsm_instance._save_current_course_progress = MagicMock()
    fsm_instance.next_syllabus_item = MagicMock()
    fsm_instance.add_message = MagicMock()
    fsm_instance.llm_client = MagicMock()
    fsm_instance.storage = MagicMock()
    fsm_instance.conn = MagicMock()
    fsm_instance.rag_url = 'http://mock-rag:5002'
    fsm_instance.web_ui_url = 'http://mock-webui:5000'

    return fsm_instance

def test_fsm_initial_state(fsm):
    """Tests that the FSM initializes in the LOBBY state."""
    assert fsm.state == 'LOBBY'

def test_transition_to_socratic_learning_success(fsm):
    """Tests the transition from LOBBY to SOCRATIC_LEARNING when a course exists."""
    # Arrange: Mock local storage
    mock_course = {'uid': 'course1', 'title': 'Test Course'}
    fsm.storage.courses.list_courses = MagicMock(return_value=[mock_course])
    fsm.storage.courses.get_flat_concepts = MagicMock(return_value=[{'uid': 'c1', 'title': 'First Concept'}])
    fsm.storage.courses.get_concept_content = MagicMock(return_value='Concept content.')
    fsm._load_course_progress = MagicMock(return_value=False)

    # Mock the LLM client that ask_socratic_question uses via _call_llm/_call_llm_stream
    fsm.llm_client.chat_stream = MagicMock(return_value=iter(["What is the first lesson about?"]))
    fsm.llm_client.chat = MagicMock(return_value="What is the first lesson about?")

    # Act
    fsm.transition({
        'type': 'TEXT_INPUT',
        'payload': {'text': 'open course Test Course'}
    })

    # Assert
    assert fsm.state == 'SOCRATIC_LEARNING'
    fsm.play_sound.assert_any_call("MODE_SWITCH_CLICK")

    calls = [str(c) for c in fsm.speak.call_args_list]
    assert any("Opening" in c or "Loaded" in c for c in calls)

def test_transition_to_socratic_learning_no_course(fsm):
    """Tests that the FSM returns to LOBBY if the course doesn't exist."""
    fsm.storage.courses.list_courses = MagicMock(return_value=[])

    # Act
    fsm.transition({
        'type': 'TEXT_INPUT',
        'payload': {'text': 'open course Nonexistent Course'}
    })

    # Assert
    assert fsm.state == 'LOBBY'
    # CASE IS PRESERVED. This assertion used to expect "nonexistent course",
    # lower-cased, because `transition` folded ALL learner text to lower before
    # doing anything with it — which also destroyed case-bearing ANSWERS (pH,
    # chemical symbols, identifiers, code) before the grader saw them, and
    # echoed the learner's own messages back to them in lower case. The folded
    # copy now exists only for command matching.
    fsm.speak.assert_called_with("I couldn't find a course on Nonexistent Course. You can say 'create a course on Nonexistent Course' to build one, or say 'list courses' to see what's available.")

def test_course_creation_call(fsm):
    """Tests that the course creation process is called correctly."""
    topic = "quantum computing"

    fsm.transition({
        'type': 'TEXT_INPUT',
        'payload': {'text': f'create course on {topic}'}
    })

    # The last speak call is "I am researching..."
    fsm.speak.assert_called_with(f"I am researching {topic}. This may take a moment.")

def test_socratic_answer_handling_correct(fsm):
    """Tests handling of a correct answer in SOCRATIC_LEARNING mode."""
    # Arrange
    fsm.state = 'SOCRATIC_LEARNING'
    fsm.current_lesson_node = {'uid': 'lesson1', 'title': 'First Lesson', 'text': '...'}
    fsm.current_context = 'Some teaching context'
    fsm.last_question = "What is 1+1?"
    fsm.syllabus_queue = [{'uid': 'lesson2', 'title': 'Second Lesson', 'text': '...'}]
    fsm.conversation_history = [("Initiate", "What is 1+1?")]

    # Mock _call_llm to return grade 5 JSON for grading
    fsm._call_llm = MagicMock(return_value='{"grade": 5, "feedback": "Excellent!"}')
    # Mock _call_llm_stream for the follow-up question
    fsm._call_llm_stream = MagicMock(return_value="Great follow-up question?")

    # Act
    fsm.transition({
        'type': 'TEXT_INPUT',
        'payload': {'text': '2'}
    })

    # Assert: Grade 5 -> SUCCESS_CHORD
    fsm.play_sound.assert_any_call("SUCCESS_CHORD")

def test_return_to_lobby(fsm):
    """Tests the command to return to the LOBBY."""
    # Arrange
    fsm.state = 'SOCRATIC_LEARNING'
    fsm.current_lesson_node = {'uid': 'l1', 'title': 'Test Lesson'}

    # Act: 'stop' is handled by handle_global_commands
    fsm.transition({
        'type': 'TEXT_INPUT',
        'payload': {'text': 'stop'}
    })

    # Assert: handle_global_commands sets state to LOBBY and speaks
    assert fsm.state == 'LOBBY'
    fsm.speak.assert_called_with("Returned to lobby.")

def test_get_state_preserves_visuals(fsm):
    """Tests that get_state includes necessary data for the UI."""
    fsm.state = 'SOCRATIC_LEARNING'
    fsm.current_lesson_node = {'uid': 'lesson1', 'title': 'Lesson 1'}
    fsm.current_context = "This is the context."
    fsm.conversation_history = [('ai', 'Question 1'), ('user', 'Answer 1')]
    fsm.transcript = [{"sender": "helga", "text": "Question 1"}, {"sender": "user", "text": "Answer 1"}]

    state_data = fsm.get_state()

    assert state_data['state'] == 'SOCRATIC_LEARNING'
    assert state_data['current_lesson_uid'] == 'lesson1'
    # `current_context` IS DELIBERATELY NOT SENT.
    #
    # /state is polled every 2 seconds per connected student, and this shipped
    # up to 10,000 characters of the concept document — plus the same text
    # again inside `graph_node` — to a browser where NOTHING reads either
    # field: grepping all of services/web-ui/ for both names returns no
    # consumer. It also put the concept's worked examples and answer key into
    # the page for anyone who opened devtools, on a tutor whose whole method is
    # withholding the answer until the learner has committed to one.
    #
    # The identifying fields the UI actually uses are asserted around this.
    assert 'current_context' not in state_data
    assert 'text' not in (state_data.get('graph_node') or {})
    assert len(state_data['transcript']) > 0
