from gevent import monkey; monkey.patch_all()
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import time
import logging
import sys
import os
import gevent
# socketio.Client removed — no longer connecting to STT/audio services
import subprocess
from werkzeug.utils import secure_filename
import json
import hashlib
import secrets
from functools import wraps

# --- Security Helpers ---
# Per-file upload size limit (50MB per file)
MAX_FILE_SIZE = 50 * 1024 * 1024

# MIME types allowed for file uploads
ALLOWED_MIMES = {
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.pdf': 'application/pdf',
    '.epub': 'application/epub+zip',
}

_MIME_WHITELIST = {
    '.txt': ['text/plain'],
    '.md': ['text/plain', 'text/markdown'],
    '.pdf': ['application/pdf'],
    '.epub': ['application/epub+zip', 'application/octet-stream'],
}

def _validate_upload(file, allowed_extensions):
    """Common upload validation: extension, MIME type, filename sanitization, size."""
    if not file or file.filename == '':
        return None, ('No file selected', 400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        return None, (f'Unsupported file type: {ext}. Allowed: {", ".join(allowed_extensions)}', 400)
    # MIME type validation
    if file.content_type and ext in _MIME_WHITELIST:
        if file.content_type not in _MIME_WHITELIST[ext]:
            logger.warning(f"MIME mismatch: {file.filename} has type {file.content_type}, expected {_MIME_WHITELIST[ext]}")
    filename = secure_filename(file.filename)
    if not filename:
        return None, ('Invalid filename', 400)
    # Check file size by reading and seeking back
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        mb = MAX_FILE_SIZE // (1024 * 1024)
        return None, (f'File too large (max {mb}MB)', 400)
    return filename, None

# Logging Setup
log_dir = "/app/data/logs"
try:
    os.makedirs(log_dir, exist_ok=True)
except (PermissionError, OSError):
    print(f"Permission denied or read-only FS for {log_dir}, falling back to /tmp/logs")
    log_dir = "/tmp/logs"
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{log_dir}/web-ui.log")
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE + 1024  # Slightly above per-file limit for overhead
# B9.3: prefer a stable secret from the environment so sessions survive restarts.
# Treat an empty value as unset. If none is provided, fall back to a random key
# (sessions won't persist across restarts) and warn.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('FLASK_SECRET_KEY'):
    logging.getLogger(__name__).warning(
        "FLASK_SECRET_KEY not set — using an ephemeral key; sessions reset on restart. "
        "Set FLASK_SECRET_KEY in .env for a stable secret."
    )
CORS_ORIGINS = os.environ.get('CORS_ORIGINS', 'http://localhost:5050,http://127.0.0.1:5050').split(',')
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins=CORS_ORIGINS, max_http_buffer_size=10000000)

# --- CSRF Protection ---
def get_csrf_token():
    """Generate or retrieve session CSRF token."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = get_csrf_token

def csrf_protect(f):
    """Decorator to validate CSRF token on state-changing POST endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            # Skip CSRF in test mode
            if app.config.get('TESTING'):
                return f(*args, **kwargs)
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            expected = session.get('_csrf_token')
        except RuntimeError:
            # No request/session context (e.g. unit tests calling function directly)
            return f(*args, **kwargs)
        if not token or token != expected:
            logger.warning(f"CSRF validation failed for {request.path}")
            return jsonify({'error': 'CSRF token invalid or missing'}), 403
        return f(*args, **kwargs)
    return decorated

# Track initiating SID for scoped status updates (AUTO-4)
_creation_initiator_sid = None

# Service URLs — use environment variables with container name defaults
SERVICES = {
    'core': os.environ.get('CORE_LOGIC_URL', 'http://helga-core-logic:5003'),
    'rag': os.environ.get('RAG_URL', 'http://helga-rag-engine:5002'),
    'tts': os.environ.get('TTS_URL', 'http://helga-tts:5005'),
    'research': os.environ.get('RESEARCH_URL', 'http://helga-research:5006'),
    # STT runs natively on the host (MLX/ANE) by default, reached like Ollama;
    # override with STT_URL=http://helga-stt:5001 for the containerized fallback.
    'stt': os.environ.get('STT_URL', 'http://host.docker.internal:5001'),
}

# --- Background Pollers ---

def state_poller():
    """Periodically poll the core service for state and broadcast it to all clients."""
    while True:
        full_state = {}
        try:
            # 1. Fetch FSM state from core service
            fsm_resp = requests.get(f'{SERVICES["core"]}/state', timeout=2)
            fsm_resp.raise_for_status()
            fsm_state = fsm_resp.json()
            full_state.update(fsm_state)

            active_course_uid = fsm_state.get('active_course_uid')
            current_lesson_uid = fsm_state.get('current_lesson_uid')
            completed_topics = fsm_state.get('completed_topics', [])

            # 2. Fetch course structure from RAG service if a course is active
            # Wrapped in its own try/except so a RAG failure doesn't block state broadcasting
            try:
                if active_course_uid:
                    rag_params = {
                        'uid': active_course_uid,
                        'current_lesson_uid': current_lesson_uid,
                        'completed_topics': ','.join(completed_topics)
                    }
                    rag_resp = requests.get(f'{SERVICES["rag"]}/api/course_structure', params=rag_params, timeout=2)
                    rag_resp.raise_for_status()
                    course_structure = rag_resp.json()
                    full_state['course_structure'] = course_structure
                else:
                    full_state['course_structure'] = None
                    full_state['active_course_uid'] = None
            except Exception as rag_err:
                logger.warning(f"State Poller: RAG course_structure failed (non-fatal): {rag_err}")
                full_state['course_structure'] = None

            socketio.emit('state_update', full_state)

        except requests.exceptions.RequestException as e:
            logger.error(f"State Poller: Failed to get core state: {e}")
        except Exception as e:
            logger.error(f"State Poller: An unexpected error occurred: {e}", exc_info=True)
        socketio.sleep(2)

def health_check_poller():
    """Periodically perform health checks and broadcast to the status room."""
    while True:
        health_status = {}

        for service, url in SERVICES.items():
            try:
                start = time.time()
                resp = requests.get(f'{url}/health', timeout=3)
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    health_status[service] = {'status': 'online', 'latency': latency, 'data': resp.json()}
                else:
                    health_status[service] = {'status': 'error', 'latency': latency, 'error': f"HTTP {resp.status_code}"}
            except requests.RequestException as e:
                health_status[service] = {'status': 'offline', 'latency': None, 'error': str(e)}

        try:
            start = time.time()
            resp = requests.get("http://host.docker.internal:11434/", timeout=2)
            
            latency = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                health_status['qwen'] = {'status': 'online', 'latency': latency, 'data': {'version': 'native-ollama'}}
            else:
                health_status['qwen'] = {'status': 'error', 'latency': latency, 'error': f"HTTP {resp.status_code}"}
        except Exception as e:
            health_status['qwen'] = {'status': 'offline', 'latency': None, 'error': str(e)}

        try:
            start = time.time()
            resp = requests.get(f'{SERVICES["rag"]}/api/stats', timeout=3) 
            latency = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                health_status['vectordb'] = {
                    'status': 'online', 
                    'latency': latency, 
                    'data': {'concepts': data.get('concepts', 0), 'courses': data.get('courses', 0)}
                }
            else:
                 health_status['vectordb'] = {'status': 'degraded', 'latency': latency, 'error': "Librarian Error"}
        except Exception as e:
             health_status['vectordb'] = {'status': 'offline', 'latency': None, 'error': str(e)}

        try:
            start = time.time()
            requests.get("http://8.8.8.8", timeout=2)
            latency = int((time.time() - start) * 1000)
            health_status['internet'] = {'status': 'online', 'latency': latency, 'data': {}}
        except Exception as e:
            health_status['internet'] = {'status': 'offline', 'latency': None, 'error': "No Connection"}
        
        socketio.emit('health_update', health_status, room='status_room')
        socketio.sleep(5)

# --- Flask-SocketIO Server Event Handlers ---

@socketio.on('connect')
def handle_connect():
    logger.info(f"Browser client connected: {request.sid}")
    try:
        fsm_resp = requests.get(f'{SERVICES["core"]}/state', timeout=2)
        fsm_resp.raise_for_status()
        fsm_state = fsm_resp.json()
        socketio.emit('state_update', fsm_state)
    except Exception as e:
        logger.error(f"On Connect Error: {e}")

@socketio.on('text_input')
def handle_text_input(data):
    global _creation_initiator_sid
    logger.info(f"Socket.IO text_input: {data}")
    # AUTO-4: Track initiating SID for scoped status updates
    _creation_initiator_sid = request.sid
    try:
        # AUTO-2: Increase timeout to 60s — course creation takes minutes
        requests.post(f'{SERVICES["core"]}/event', json={'type': 'TEXT_INPUT', 'payload': data}, timeout=60)
    except Exception as e:
        logger.error(f"Failed to forward text_input to core: {e}")
        # AUTO-3: Emit error back to browser so it doesn't hang on spinner
        emit('status_update', {'message': f'Error: Failed to reach core service. {str(e)[:100]}'})

# voice_update handler removed — text-only mode

@socketio.on('join_status_room')
def handle_join_status_room():
    join_room('status_room')

@socketio.on('leave_status_room')
def handle_leave_status_room():
    leave_room('status_room')

# Audio chunk and STT handlers removed — text-only mode

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/courses')
def courses_page():
    return render_template('courses.html')

@app.route('/courses/new')
def course_wizard_page():
    return render_template('course_wizard.html')

@app.route('/api/suggest_modules', methods=['POST'])
def suggest_modules():
    """LLM suggests modules for the custom course wizard."""
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/suggest_modules', json=request.json, timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'modules': [], 'error': str(e)}), 502

@app.route('/api/suggest_concepts', methods=['POST'])
def suggest_concepts():
    """LLM suggests concepts for a module in the wizard."""
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/suggest_concepts', json=request.json, timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'concepts': [], 'error': str(e)}), 502

@app.route('/api/clarify_course', methods=['POST'])
def clarify_course():
    """LLM generates clarifying questions for the wizard."""
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/clarify_course', json=request.json, timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'questions': [], 'error': str(e)}), 502

@app.route('/api/create_course_custom', methods=['POST'])
def create_course_custom():
    """Trigger custom course generation from wizard payload."""
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/create_course_custom', json=request.json, timeout=300)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/learn')
def learn_page():
    return render_template('learn.html')

@app.route('/test')
@app.route('/quiz')
def test_page():
    return render_template('quiz.html')

@app.route('/review')
def review_page():
    return render_template('review.html')

@app.route('/palace')
def palace_page():
    """Memory Palace removed — redirect to home."""
    from flask import redirect
    return redirect('/')

@app.route('/schedule')
def schedule_page():
    return render_template('schedule.html')

# --- Schedule API (direct StorageManager access via shared data volume) ---
def _get_storage():
    """Lazy-init StorageManager for schedule reads."""
    if not hasattr(app, '_storage'):
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
        from services.common.storage import StorageManager
        data_root = os.environ.get('DATA_ROOT', '/app/data')
        app._storage = StorageManager(data_root)
    return app._storage

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    try:
        storage = _get_storage()
        start = request.args.get('start')
        end = request.args.get('end')
        course_uid = request.args.get('course_uid')
        storage.schedule.mark_overdue()
        reviews = storage.schedule.get_scheduled_reviews(
            start_date=start, end_date=end, course_uid=course_uid
        )
        return jsonify({'reviews': reviews}), 200
    except Exception as e:
        logger.error(f"Schedule fetch error: {e}")
        return jsonify({'reviews': []}), 200

@app.route('/api/schedule/complete', methods=['POST'])
def complete_schedule_review():
    try:
        storage = _get_storage()
        review_id = request.json.get('review_id')
        if review_id is not None:
            try:
                review_id = int(review_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'review_id must be an integer'}), 400
            storage.schedule.complete_review(review_id)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Schedule complete error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule/stats', methods=['GET'])
def get_schedule_stats():
    try:
        storage = _get_storage()
        storage.schedule.mark_overdue()
        upcoming = storage.schedule.get_upcoming_count(days=7)
        streak = storage.settings.get('streak', 0) if hasattr(storage, 'settings') else 0
        # Count overdue
        all_reviews = storage.schedule.get_scheduled_reviews()
        overdue = sum(1 for r in all_reviews if r.get('status') == 'overdue')
        completed = sum(1 for r in all_reviews if r.get('status') == 'completed')
        total = len(all_reviews)
        retention = round((completed / total * 100) if total > 0 else 0)
        return jsonify({
            'upcoming': upcoming, 'overdue': overdue,
            'streak': streak, 'retention': retention,
            'completed': completed, 'total': total
        }), 200
    except Exception as e:
        logger.error(f"Schedule stats error: {e}")
        return jsonify({'upcoming': 0, 'overdue': 0, 'streak': 0, 'retention': 0}), 200

@app.route('/status')
def status_page():
    # Fetch health data for initial render; WebSocket updates will keep it live
    services_health = {}
    for service, url in SERVICES.items():
        try:
            start = time.time()
            resp = requests.get(f'{url}/health', timeout=3)
            latency = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                services_health[service] = {
                    'name': service,
                    'status': 'healthy',
                    'latency': latency,
                    'data': resp.json(),
                    'error': None
                }
            else:
                services_health[service] = {
                    'name': service,
                    'status': 'error',
                    'latency': latency,
                    'data': {},
                    'error': f"HTTP {resp.status_code}"
                }
        except Exception as e:
            services_health[service] = {
                'name': service,
                'status': 'offline',
                'latency': None,
                'data': {},
                'error': str(e)[:100]
            }
    return render_template('status.html', services_health=services_health)


@app.route('/settings')
def settings_page():
    return render_template('settings.html')


@app.route('/course/view')
def course_view_page():
    return render_template('course_view.html')


@app.route('/api/profile', methods=['GET'])
def get_profile():
    """Get user profile settings."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/profile', timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        # Return defaults if RAG unavailable
        return jsonify({
            "display_name": "",
            "theme": "light",
            "font_scale": 1.0,
            "default_voice": "af_heart",
            "gamification_enabled": True,
            "sound_effects": True,
            "daily_goal": 5,
        })


@app.route('/api/profile', methods=['PATCH'])
def update_profile():
    """Update user profile settings."""
    try:
        resp = requests.patch(
            f'{SERVICES["rag"]}/api/profile',
            json=request.get_json(force=True),
            timeout=5
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        return jsonify({"error": str(e)}), 502


@app.route('/api/gamification', methods=['GET'])
def get_gamification():
    """Get gamification state (XP, level, streak, achievements)."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/gamification', timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({
            "total_xp": 0, "level": 1, "streak_days": 0,
            "daily_xp": 0, "achievements_unlocked": [],
        })


@app.route('/api/gamification/award_xp', methods=['POST'])
def award_xp():
    """Award XP after a graded interaction."""
    try:
        resp = requests.post(
            f'{SERVICES["rag"]}/api/gamification/award_xp',
            json=request.get_json(force=True),
            timeout=5
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Award XP error: {e}")
        return jsonify({"error": str(e)}), 502


@app.route('/api/gamification/check_streak', methods=['POST'])
def check_streak():
    """Check and update daily streak."""
    try:
        resp = requests.post(
            f'{SERVICES["rag"]}/api/gamification/check_streak',
            json=request.get_json(force=True) if request.data else {},
            timeout=5
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Check streak error: {e}")
        return jsonify({"streak_days": 0, "incremented": False}), 502


@app.route('/api/fsm_state', methods=['GET'])
def proxy_fsm_state():
    try:
        resp = requests.get(f'{SERVICES["core"]}/state', timeout=2)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/stats', methods=['GET'])
def proxy_stats():
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/stats', timeout=2)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.warning(f"Stats proxy failed: {e}")
        return jsonify({'courses': 0, 'concepts': 0, 'streak': 0}), 200

@app.route('/api/event', methods=['POST'])
@csrf_protect
def post_event():
    try:
        resp = requests.post(f'{SERVICES["core"]}/event', json=request.json, timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# NOTE: Schedule routes are defined above (lines 305-355) using direct StorageManager access.
# Duplicate proxy routes removed to prevent Flask route conflicts.

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': 'web-ui'}), 200


@app.route('/api/update_thinking_status', methods=['POST'])
def update_thinking_status():
    data = request.json
    # Stream tokens get their own Socket.IO event so the browser can render
    # them incrementally without conflating with thinking/status updates.
    if data and data.get('type') == 'stream_token':
        socketio.emit('stream_token', {
            'token': data.get('token', ''),
            'done': data.get('done', False),
        })
        return jsonify({'status': 'ok'}), 200
    # AUTO-4: Scope status_update to originating client SID if available
    if _creation_initiator_sid:
        socketio.emit('status_update', data, room=_creation_initiator_sid)
    else:
        socketio.emit('status_update', data)
    return jsonify({'status': 'ok'}), 200

@app.route('/api/search', methods=['GET'])
def proxy_search():
    """Proxy global search to the RAG FTS5 /search (used by the header search UI)."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/search', params=request.args, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException:
        return jsonify({'results': []}), 200

@app.route('/api/courses', methods=['GET'])
def get_courses():
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/courses', timeout=2)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Error fetching courses: {e}")
        return jsonify({'courses': []}), 200

@app.route('/api/delete_course', methods=['DELETE'])
@csrf_protect
def delete_course():
    uid = request.args.get('uid')
    try:
        # 1. Notify RAG (Deletes DB nodes and Filesystem)
        resp_rag = requests.delete(f'{SERVICES["rag"]}/api/courses', params={'uid': uid}, timeout=5)
        
        # 2. Notify CORE (Clears runtime state/active course)
        event = {'type': 'DELETE_COURSE', 'payload': {'uid': uid}}
        requests.post(f'{SERVICES["core"]}/event', json=event, timeout=2)
        
        return jsonify(resp_rag.json()), resp_rag.status_code
    except Exception as e:
        logger.error(f"Error in delete_course: {e}")
        return jsonify({'error': str(e)}), 502

@app.route('/api/set_active_course', methods=['POST'])
def set_active_course():
    """Set the FSM's active course WITHOUT triggering a slow resume flow.

    The old implementation emitted RESUME_COURSE which calls the LLM to
    generate a resume-discussion question for the last saved concept. When
    called from the courses page's Start button (fire-and-forget), that LLM
    call raced with the learn page's own NAVIGATE_TO_TOPIC → two concurrent
    streams would append tokens from two different concepts into the same
    transcript. SET_CONTEXT is the right event here: it just swaps
    active_course_uid and clears stale transcript/queue state if the course
    changed. No LLM call, no race.
    """
    data = request.json
    try:
        event = {
            'type': 'SET_CONTEXT',
            'payload': {
                'course_uid': data.get('uid'),
                'title': data.get('title')
            }
        }
        resp = requests.post(f'{SERVICES["core"]}/event', json=event, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/course_structure', methods=['GET'])
def get_course_structure():
    uid = request.args.get('uid')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/course_structure', params={'uid': uid}, timeout=10)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/course_details', methods=['GET'])
def proxy_course_details():
    uid = request.args.get('uid')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/course_details', params={'uid': uid}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/concept_details', methods=['GET'])
def get_concept_details():
    uid = request.args.get('uid')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/concept_details', params={'uid': uid}, timeout=3)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/check_sudo', methods=['GET'])
def check_sudo():
    # Sudo no longer required as per README updates, return available: true to bypass UI prompt
    return jsonify({'available': True}), 200

@app.route('/api/set_sudo', methods=['POST'])
def set_sudo():
    return jsonify({'status': 'ok'}), 200

@app.route('/api/voices', methods=['GET'])
def get_voices():
    """Proxy to Kokoro TTS service for voice list."""
    try:
        resp = requests.get(f'{SERVICES["tts"]}/api/voices', timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception:
        return jsonify({'voices': ['af_heart']}), 200

@app.route('/api/tts', methods=['POST'])
def proxy_tts():
    """Proxy to Kokoro TTS service for audio generation."""
    try:
        resp = requests.post(f'{SERVICES["tts"]}/api/tts', json=request.json, timeout=30)
        if resp.status_code == 200:
            from flask import Response
            return Response(resp.content, mimetype='audio/wav')
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/stt', methods=['POST'])
def proxy_stt():
    """Proxy recorded audio to the STT service and return the transcript.

    The browser POSTs the raw audio blob as the request body (Content-Type e.g.
    audio/webm); we forward body + content-type to the STT service unchanged.
    """
    try:
        audio = request.get_data()
        if not audio:
            return jsonify({'error': 'No audio provided'}), 400
        resp = requests.post(
            f'{SERVICES["stt"]}/api/stt',
            data=audio,
            headers={'Content-Type': request.headers.get('Content-Type', 'application/octet-stream')},
            timeout=30,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException as e:
        logger.warning(f"STT service unreachable: {e}")
        return jsonify({'error': 'Speech recognition is unavailable'}), 502

@app.route('/api/upload_epub', methods=['POST'])
@csrf_protect
def upload_epub():
    """Upload and ingest an EPUB file into a course."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # EPUB-2: Validate file type
    if not file.filename.lower().endswith('.epub'):
        return jsonify({'error': 'Only .epub files are accepted'}), 400
    
    try:
        upload_dir = '/app/data/uploads'
        os.makedirs(upload_dir, exist_ok=True)
        filename = secure_filename(file.filename)
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        logger.info(f"EPUB uploaded: {filepath}")
        
        # Forward to core for course creation from EPUB
        event = {
            'type': 'TEXT_INPUT',
            'payload': {
                'text': f'create course from epub {filepath}',
                'source': 'epub_upload',
                'filepath': filepath
            }
        }
        try:
            resp = requests.post(f'{SERVICES["core"]}/event', json=event, timeout=60)
            return jsonify({'status': 'processing', 'message': 'EPUB uploaded and course creation started. Check progress in the status bar.'}), 202
        except Exception as e:
            logger.error(f"Failed to forward EPUB to core: {e}")
            return jsonify({'error': 'Upload succeeded but course creation service is unavailable. Try again later.'}), 503
    except Exception as e:
        logger.error(f"EPUB upload failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/course_status/<uid>', methods=['GET'])
def get_course_status(uid):
    """WIZ-6: Poll course creation/hydration status."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/course_status/{uid}', timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Course status check failed: {e}")
        return jsonify({'status': 'unknown', 'error': str(e)}), 502

@app.route('/api/upload_source', methods=['POST'])
@csrf_protect
def upload_source():
    """Upload a source document for Source Material Injection.
    The file is saved to /app/data/uploads/ and its path is returned.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    allowed_extensions = {'.txt', '.md', '.pdf', '.epub'}
    filename, err = _validate_upload(file, allowed_extensions)
    if err:
        return jsonify({'error': err[0]}), err[1]
    
    try:
        upload_dir = '/app/data/uploads'
        os.makedirs(upload_dir, exist_ok=True)
        filepath = os.path.join(upload_dir, filename)
        file.save(filepath)
        logger.info(f"Source file uploaded: {filepath}")
        return jsonify({'status': 'ok', 'filepath': filepath, 'filename': filename})
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/create_custom_course', methods=['POST'])
def create_custom_course():
    """Handle custom course creation with node-by-node specification."""
    try:
        title = request.form.get('title')
        teaching_style = request.form.get('teaching_style', '')
        content_source = request.form.get('content_source', 'zim')
        modules_json = request.form.get('modules')
        
        if not title or not modules_json:
            return jsonify({'error': 'Missing title or modules'}), 400
        
        try:
            modules = json.loads(modules_json)
        except (json.JSONDecodeError, ValueError):
            return jsonify({'error': 'Invalid modules JSON'}), 400
        
        if not isinstance(modules, list):
            return jsonify({'error': 'Modules must be a list'}), 400
        
        if len(modules) == 0:
            return jsonify({'error': 'At least one module is required'}), 400
        
        # Save uploaded source files
        upload_dir = '/app/data/uploads'
        os.makedirs(upload_dir, exist_ok=True)
        
        for idx, module in enumerate(modules):
            source_key = f'source_{idx}'
            if source_key in request.files:
                file = request.files[source_key]
                if file.filename:
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(upload_dir, filename)
                    file.save(filepath)
                    module['source_file'] = filepath
                    logger.info(f"Saved source file for module '{module['title']}': {filepath}")
        
        # Forward to RAG service for course creation
        payload = {
            'title': title,
            'teaching_style': teaching_style,
            'content_source': content_source,
            'modules': modules
        }
        
        resp = requests.post(f'{SERVICES["rag"]}/api/create_custom_course', json=payload, timeout=120)
        return jsonify(resp.json()), resp.status_code
        
    except Exception as e:
        logger.error(f"Custom course creation failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/draft/reorder', methods=['POST'])
def draft_reorder():
    """Proxy for module reordering to RAG service."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/draft/reorder', json=request.json, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/course_modules', methods=['GET'])
def get_course_modules():
    """Proxy for fetching course modules from RAG service."""
    uid = request.args.get('uid')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/course_modules', params={'uid': uid}, timeout=3)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/course_meta', methods=['GET'])
def get_course_meta():
    """Proxy for fetching course metadata from RAG service."""
    uid = request.args.get('uid')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/course_meta', params={'uid': uid}, timeout=3)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# --- Quiz/Test API Proxies ---
@app.route('/api/quiz', methods=['GET'])
def get_quiz():
    """Generate a quiz question from a random concept."""
    try:
        params = {}
        course_uid = request.args.get('course_uid')
        if course_uid:
            params['course_uid'] = course_uid
        resp = requests.get(f'{SERVICES["rag"]}/api/quiz', params=params, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/quiz/grade', methods=['POST'])
def grade_quiz():
    """Grade a quiz answer."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/quiz/grade', json=request.json, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# --- Review/Spaced Repetition API Proxies ---
@app.route('/api/due_cards', methods=['GET'])
def get_due_cards():
    """Get cards due for review."""
    topic = request.args.get('topic', '')
    course_uid = request.args.get('course_uid', '')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/due_cards', params={'topic': topic, 'course_uid': course_uid}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'cards': []}), 200

@app.route('/api/generate_flashcards', methods=['POST'])
def generate_flashcards():
    """Generate new flashcards for a concept."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/generate_flashcards', json=request.json, timeout=30)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/update_card', methods=['POST'])
def update_card():
    """Update card stability after review (legacy SM-2 endpoint)."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/update_card', json=request.json, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/grade_card_fsrs', methods=['POST'])
def grade_card_fsrs():
    """Grade a flashcard using FSRS algorithm (server-side)."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/grade_card_fsrs', json=request.json, timeout=15)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/review_stats', methods=['GET'])
def review_stats():
    """Unified review statistics — flashcard due dates + calendar data."""
    try:
        params = {}
        course_uid = request.args.get('course_uid')
        if course_uid:
            params['course_uid'] = course_uid
        resp = requests.get(f'{SERVICES["rag"]}/api/review_stats', params=params, timeout=10)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/auto_generate_flashcards', methods=['POST'])
def auto_generate_flashcards():
    """Auto-generate flashcards for a concept on completion."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/auto_generate_flashcards', json=request.json, timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.warning(f"Auto flashcard generation failed: {e}")
        return jsonify({'error': str(e)}), 502

# --- Memory Palace API Proxies ---
@app.route('/palace/start', methods=['GET'])
def palace_start():
    """Start memory palace session."""
    course_uid = request.args.get('course_uid', '')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/palace/start', params={'course_uid': course_uid}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/locus/next', methods=['GET'])
def locus_next():
    """Get next locus in memory palace."""
    current = request.args.get('current', '')
    try:
        resp = requests.get(f'{SERVICES["rag"]}/locus/next', params={'current': current}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/anchor', methods=['POST'])
def anchor_concept():
    """Anchor a concept to a locus."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/anchor', json=request.json, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# --- Custom Course Wizard API Proxies ---
@app.route('/api/custom_course/preview', methods=['POST'])
@csrf_protect
def preview_custom_course():
    """Proxy for custom course structure preview generation."""
    try:
        # Validate request has JSON data
        if not request.json:
            logger.error("Preview request missing JSON data")
            return jsonify({'error': 'Request must contain JSON data'}), 400
        
        # Log preview request
        title = request.json.get('title', 'Unknown')
        module_count = len(request.json.get('modules', []))
        logger.info(f"Proxying preview request for '{title}' with {module_count} modules")
        
        # Forward to RAG service with extended timeout for LLM generation
        # Timeout: 10 minutes to handle large courses (per-module timeout in backend)
        resp = requests.post(
            f'{SERVICES["rag"]}/api/custom_course/preview',
            json=request.json,
            timeout=600
        )
        
        # Log response status
        if resp.status_code == 200:
            logger.info(f"Preview generated successfully for '{title}'")
        else:
            logger.error(f"Preview generation failed with status {resp.status_code}: {resp.text[:200]}")
        
        return jsonify(resp.json()), resp.status_code
        
    except requests.exceptions.Timeout:
        logger.error("Preview generation timed out after 120 seconds")
        return jsonify({'error': 'Preview generation timed out. Please try with fewer modules or lower depth.'}), 504
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to RAG service: {e}")
        return jsonify({'error': 'Unable to connect to course generation service'}), 503
    except Exception as e:
        logger.error(f"Preview generation failed: {e}", exc_info=True)
        return jsonify({'error': f'Preview generation error: {str(e)}'}), 502

@app.route('/api/custom_course/create', methods=['POST'])
@csrf_protect
def create_custom_course_wizard():
    """Handle custom course creation from wizard with file uploads."""
    try:
        # Validate form data
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        teaching_style = request.form.get('teaching_style', '').strip()
        content_source = request.form.get('content_source', 'zim')
        modules_json = request.form.get('modules')
        structure_json = request.form.get('structure')
        
        if not title:
            logger.error("Create request missing title")
            return jsonify({'error': 'Course title is required'}), 400
        
        if not modules_json:
            logger.error("Create request missing modules")
            return jsonify({'error': 'Modules data is required'}), 400
        
        if not structure_json:
            logger.error("Create request missing structure")
            return jsonify({'error': 'Course structure is required'}), 400
        
        # Parse JSON data
        try:
            modules = json.loads(modules_json)
            structure = json.loads(structure_json)
        except json.JSONDecodeError as json_err:
            logger.error(f"Failed to parse JSON data: {json_err}")
            return jsonify({'error': f'Invalid JSON data: {str(json_err)}'}), 400
        
        if not isinstance(modules, list) or not isinstance(structure, dict):
            logger.error(f"Invalid data types: modules={type(modules)}, structure={type(structure)}")
            return jsonify({'error': 'Invalid data format'}), 400
        
        logger.info(f"Creating custom course '{title}' with {len(modules)} modules")
        
        # Save uploaded source files
        upload_dir = '/app/data/uploads'
        try:
            os.makedirs(upload_dir, exist_ok=True)
        except Exception as dir_err:
            logger.error(f"Failed to create upload directory: {dir_err}")
            return jsonify({'error': 'Server configuration error'}), 500
        
        files_saved = 0
        for idx, module in enumerate(modules):
            source_key = f'source_{idx}'
            if source_key in request.files:
                file = request.files[source_key]
                if file and file.filename:
                    try:
                        # Validate file extension
                        allowed_extensions = {'.txt', '.md', '.pdf', '.epub'}
                        ext = os.path.splitext(file.filename)[1].lower()
                        if ext not in allowed_extensions:
                            logger.warning(f"Skipping unsupported file type: {file.filename}")
                            continue
                        
                        # Save file with secure filename
                        filename = secure_filename(file.filename)
                        # Add timestamp to avoid collisions
                        import time
                        timestamp = int(time.time())
                        filename = f"{timestamp}_{filename}"
                        filepath = os.path.join(upload_dir, filename)
                        
                        file.save(filepath)
                        module['source_file'] = filepath
                        files_saved += 1
                        logger.info(f"Saved source file for module '{module.get('title', 'Unknown')}': {filepath}")
                        
                    except Exception as file_err:
                        logger.error(f"Failed to save file for module {idx}: {file_err}")
                        # Continue with other files, don't fail the entire request
        
        logger.info(f"Saved {files_saved} source files for course '{title}'")

        # WIZ-8: Validate source file paths exist before forwarding to RAG
        for module in modules:
            src = module.get('source_file')
            if src and not os.path.isfile(src):
                logger.warning(f"Source file missing for module '{module.get('title', '?')}': {src}")
                module.pop('source_file', None)

        # Forward to RAG service for course creation
        payload = {
            'title': title,
            'description': description,
            'teaching_style': teaching_style,
            'content_source': content_source,
            'modules': modules,
            'structure': structure
        }
        
        # WIZ-5: Track saved files for cleanup on failure
        saved_files = [m.get('source_file') for m in modules if m.get('source_file')]

        def _cleanup_uploads():
            for fpath in saved_files:
                try:
                    if fpath and os.path.exists(fpath):
                        os.remove(fpath)
                        logger.info(f"Cleaned up upload: {fpath}")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to clean up {fpath}: {cleanup_err}")

        try:
            # Extended timeout for course creation (5 minutes)
            resp = requests.post(
                f'{SERVICES["rag"]}/api/custom_course/create',
                json=payload,
                timeout=300
            )

            # Log response
            if resp.status_code == 200:
                result = resp.json()
                course_uid = result.get('course_uid', 'unknown')
                logger.info(f"Course '{title}' created successfully with UID: {course_uid}")
            else:
                logger.error(f"Course creation failed with status {resp.status_code}: {resp.text[:200]}")
                _cleanup_uploads()

            return jsonify(resp.json()), resp.status_code

        except requests.exceptions.Timeout:
            logger.error(f"Course creation timed out after 300 seconds for '{title}'")
            # Don't cleanup on timeout — course may still be processing
            return jsonify({'error': 'Course creation timed out. The course may still be processing in the background.'}), 504
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Failed to connect to RAG service for course creation: {e}")
            _cleanup_uploads()
            return jsonify({'error': 'Unable to connect to course creation service'}), 503

    except Exception as e:
        logger.error(f"Custom course wizard creation failed: {e}", exc_info=True)
        return jsonify({'error': f'Course creation error: {str(e)}'}), 500

# --- Account Tab ---
PROFILE_PATH = os.getenv('USER_PROFILE_PATH', '/app/data/user_space/profile.json')

@app.route('/account')
def account_page():
    from flask import redirect
    return redirect('/')

@app.route('/api/user_profile', methods=['GET'])
def get_user_profile():
    """Get the user profile."""
    try:
        if os.path.exists(PROFILE_PATH):
            with open(PROFILE_PATH, 'r') as f:
                profile = json.load(f)
            return jsonify(profile)
        return jsonify({'name': '', 'level': 'intermediate', 'interests': [], 'goals': ''})
    except Exception as e:
        logger.error(f"Failed to load user profile: {e}")
        return jsonify({'name': '', 'level': 'intermediate', 'interests': [], 'goals': ''}), 200

@app.route('/api/user_profile', methods=['POST'])
def save_user_profile():
    """Save the user profile."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Sanitize and validate
        profile = {
            'name': str(data.get('name', ''))[:50].strip(),
            'level': data.get('level', 'intermediate') if data.get('level') in ['beginner', 'intermediate', 'advanced', 'expert'] else 'intermediate',
            'interests': [str(i)[:40].strip() for i in data.get('interests', []) if isinstance(i, str)][:20],
            'goals': str(data.get('goals', ''))[:500].strip()
        }
        
        os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
        with open(PROFILE_PATH, 'w') as f:
            json.dump(profile, f, indent=2)
        
        logger.info(f"User profile saved: {profile.get('name', 'anonymous')}")
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Failed to save user profile: {e}")
        return jsonify({'error': str(e)}), 500

# --- VG-09: Aggregated health endpoint ---
@app.route('/api/health/all', methods=['GET'])
def health_all():
    """Return health of all services in one call."""
    results = {}
    for name, url in SERVICES.items():
        try:
            resp = requests.get(f'{url}/health', timeout=3)
            results[name] = {'status': 'healthy' if resp.status_code == 200 else 'unhealthy',
                             'code': resp.status_code}
        except Exception as e:
            results[name] = {'status': 'offline', 'error': str(e)[:80]}
    # Check Ollama
    try:
        resp = requests.get('http://host.docker.internal:11434/api/tags', timeout=3)
        results['ollama'] = {'status': 'healthy' if resp.status_code == 200 else 'unhealthy'}
    except Exception:
        results['ollama'] = {'status': 'offline'}
    return jsonify(results)


# --- VG-01: Proxy /api/create_course to core-logic ---
@app.route('/api/create_course', methods=['POST'])
def proxy_create_course():
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/create_course',
                             json=request.get_json(force=True), timeout=60)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Create course proxy error: {e}")
        return jsonify({'error': str(e)}), 502


@app.route('/api/creation_status', methods=['GET'])
def proxy_creation_status():
    """Monitor course creation progress — phase, topic, progress %."""
    try:
        resp = requests.get(f'{SERVICES["core"]}/api/creation_status', timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'active': False, 'phase': None, 'error': str(e)}), 200


@app.route('/api/cancel_creation', methods=['POST'])
def proxy_cancel_creation():
    """Cancel in-progress course creation."""
    try:
        resp = requests.post(f'{SERVICES["core"]}/api/cancel_creation', json={}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# --- VG-02: Proxy /api/due_concepts to RAG ---
@app.route('/api/due_concepts', methods=['GET'])
def proxy_due_concepts():
    try:
        params = dict(request.args)
        resp = requests.get(f'{SERVICES["rag"]}/api/due_concepts', params=params, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'concepts': []}), 200


def _monitored_spawn(fn, name):
    """PERF-4: Wrap gevent.spawn with auto-restart on crash."""
    def _wrapper():
        while True:
            try:
                fn()
            except Exception as e:
                logger.error(f"Greenlet '{name}' crashed: {e}. Restarting in 5s...")
                import gevent as _g
                _g.sleep(5)
    return gevent.spawn(_wrapper)


if __name__ == '__main__':
    _monitored_spawn(state_poller, "state_poller")
    _monitored_spawn(health_check_poller, "health_check_poller")
    # STT/Audio service connections removed — text-only mode
    socketio.run(app, host='0.0.0.0', port=5000)
