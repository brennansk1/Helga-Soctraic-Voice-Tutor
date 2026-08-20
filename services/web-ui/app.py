import sys as _sys
if "pytest" not in _sys.modules:
    # Server runtime only: gevent monkey-patching mid-pytest-run would hook
    # threading/ssl after real threads exist and deadlock the suite.
    from gevent import monkey
    monkey.patch_all()
from flask import (Flask, render_template, request, jsonify, session, abort,
                   redirect, url_for, send_from_directory)
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import time
import logging
import sys
import os
import re
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

# B27.1: opt-in structured JSON logs (HELGA_JSON_LOGS=true)
try:
    from services.common.logging_utils import configure_json_logging
    configure_json_logging("web-ui")
except Exception:
    pass


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

# B15.4: auth module (session-key contract, spec 03 §1). init_auth is called
# after _get_storage is defined below.
import auth as helga_auth
from auth import (
    current_parent_id, current_student_id, owns_student,
    parent_required, student_session_required, owns_student_required,
    hash_secret, verify_secret,
)

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

def _fetch_student_state(student_id):
    """Fetch one student's FSM state + course structure enrichment."""
    fsm_resp = requests.get(f'{SERVICES["core"]}/state',
                            params={'student_id': student_id}, timeout=2)
    fsm_resp.raise_for_status()
    full_state = dict(fsm_resp.json())

    active_course_uid = full_state.get('active_course_uid')
    try:
        if active_course_uid:
            rag_params = {
                'uid': active_course_uid,
                'current_lesson_uid': full_state.get('current_lesson_uid'),
                'completed_topics': ','.join(full_state.get('completed_topics', [])),
                'student_id': student_id,
            }
            rag_resp = requests.get(f'{SERVICES["rag"]}/api/course_structure',
                                    params=rag_params, timeout=2)
            rag_resp.raise_for_status()
            full_state['course_structure'] = rag_resp.json()
        else:
            full_state['course_structure'] = None
            full_state['active_course_uid'] = None
    except Exception as rag_err:
        logger.warning(f"course_structure enrichment failed (non-fatal): {rag_err}")
        full_state['course_structure'] = None
    return full_state


def state_poller():
    """B15.5: poll per CONNECTED student and emit to that student's room only
    (replaces the single-tenant global broadcast). Event-driven push also
    happens on /api/event completion; this poller is the transitional
    keep-fresh loop and scales with connected students, not sockets."""
    while True:
        for sid_student in list(_connected_students.keys()):
            try:
                state = _fetch_student_state(sid_student)
                socketio.emit('state_update', state, room=f"student:{sid_student}")
            except requests.exceptions.RequestException as e:
                logger.error(f"State Poller: core state failed for {sid_student}: {e}")
            except Exception as e:
                logger.error(f"State Poller: unexpected error: {e}", exc_info=True)
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

# B15.5: which students have connected sockets (drives the scoped poller)
_connected_students = {}   # student_id -> live socket count
_sid_student = {}          # socket sid -> student_id


@socketio.on('connect')
def handle_connect():
    # B15.5: every browser joins exactly ONE student room, derived from the
    # same session cookie HTTP sees. All student-directed emits are
    # room-scoped; nothing is broadcast.
    sid_student = current_student_id()
    if not sid_student:
        logger.info("Rejecting socket: no student session (bare parent/anon)")
        return False
    join_room(f"student:{sid_student}")
    _sid_student[request.sid] = sid_student
    _connected_students[sid_student] = _connected_students.get(sid_student, 0) + 1
    logger.info(f"Browser client connected: {request.sid} → room student:{sid_student}")
    try:
        fsm_resp = requests.get(f'{SERVICES["core"]}/state',
                                params={'student_id': sid_student}, timeout=2)
        fsm_resp.raise_for_status()
        # initial per-connection snapshot only (replaces the old global emit)
        emit('state_update', fsm_resp.json())
    except Exception as e:
        logger.error(f"On Connect Error: {e}")


@socketio.on('disconnect')
def handle_disconnect():
    sid_student = _sid_student.pop(request.sid, None)
    if sid_student:
        n = _connected_students.get(sid_student, 1) - 1
        if n <= 0:
            _connected_students.pop(sid_student, None)
        else:
            _connected_students[sid_student] = n

@socketio.on('text_input')
def handle_text_input(data):
    logger.info(f"Socket.IO text_input: {data}")
    sid_student = _sid_student.get(request.sid) or current_student_id()
    try:
        # AUTO-2: 60s — course creation takes minutes. B15.5: student_id is
        # injected from the session, never taken from the client payload.
        requests.post(f'{SERVICES["core"]}/event',
                      json={'type': 'TEXT_INPUT', 'payload': data,
                            'student_id': sid_student},
                      timeout=60)
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

@app.route('/degree')
def degree_page():
    """The programme map — pannable, zoomable prerequisite DAG."""
    return render_template('degree.html')

@app.route('/create')
def create_page():
    """The course-creation carousel — one decision per page.

    Replaces the wizard as the primary entry; the wizard remains at
    /courses/new for the custom-module flow until the carousel absorbs it.
    """
    return render_template('create.html')

@app.route('/api/scope_check', methods=['POST'])
def scope_check():
    """Advisory pre-build scope check for the creation flow.

    Runs the same evidence sweep the build itself uses, so the warning shown
    on the carousel's scope page is the REAL verdict, not a mock. Advisory by
    design: the build re-checks before generating, so a failure here degrades
    to 'unavailable' rather than blocking creation.
    """
    data = request.get_json(silent=True) or {}
    topic = (data.get('topic') or '').strip()
    if len(topic) < 3:
        return jsonify({'available': False, 'error': 'topic too short'}), 400
    try:
        resp = requests.post(f'{SERVICES["core"]}/scope_check',
                             json={'topic': topic,
                                   'template': data.get('template')},
                             timeout=90)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.warning(f"scope_check unavailable: {e}")
        return jsonify({'available': False}), 200

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

# --- A5.1: Practice — one surface, three states -----------------------------
# Quiz, Review and Schedule were three top-level tabs for the same activity at
# three different moments. Choosing between them required the learner to
# understand our FSRS model, which is our problem, not theirs.
#
# The old URLs are kept as redirects rather than deleted: they are bookmarked,
# linked from other templates, and asserted in tests. A dead link is a worse
# outcome than a redirect.

PRACTICE_TABS = ('due', 'quiz', 'upcoming')


@app.route('/practice')
def practice_page():
    tab = request.args.get('tab', 'due')
    if tab not in PRACTICE_TABS:
        tab = 'due'
    return render_template('practice.html', active_tab=tab)


@app.route('/progress')
def progress_page():
    """What do I actually know, what is due, and where are my gaps.

    Every field behind this is already stored -- with one exception found when
    it was checked rather than assumed: times_correct (accuracy) was never
    written by anything, so this surface would have shown a flat zero for every
    learner. Fixed at the scheduler before this page was built.
    """
    return render_template('progress.html')


@app.route('/api/ask', methods=['POST'])
def ask_proxy():
    """A5.2 — Ask. Generation can take a while on a local model, so the timeout
    is generous; a 5s default would turn every real answer into an error."""
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/ask',
                             json=request.get_json(silent=True) or {},
                             timeout=120)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.Timeout:
        return jsonify({'error': 'the tutor took too long to answer'}), 504
    except Exception as e:
        logger.error(f"ask proxy failed: {e}")
        return jsonify({'error': 'could not reach the tutor service'}), 502


@app.route('/api/build/status', methods=['GET'])
def build_status():
    """Is a course being built right now, and how far has it got?

    Read on every page load. Two things depend on it:
      - the learner can navigate away from a build and come back to it;
      - the UI can stop offering actions the system will refuse, instead of
        letting them fill in a form and then rejecting it.
    """
    try:
        from services.common import build_state
        state = build_state.current()
    except Exception as e:
        logger.debug(f"build status unavailable: {e}")
        return jsonify({'active': False})
    if not state:
        return jsonify({'active': False})
    return jsonify({
        'active': bool(state.get('active')),
        'topic': state.get('topic'),
        'source': state.get('source'),
        'modules': state.get('modules', 0),
        'started_at': state.get('started_at'),
        'course_uid': state.get('course_uid'),
        'stale': state.get('stale', False),
        'messages': (state.get('messages') or [])[-120:],
    })


@app.route('/api/books/build', methods=['POST'])
def books_build():
    """Start a course from a public-domain book.

    Availability is re-checked server-side. A client that skipped the check, or
    a book whose status changed since it was listed, must not slip through —
    the whole point of the badge is that we do not build from a blurb.
    """
    data = request.get_json(silent=True) or {}
    ident = (data.get('identifier') or '').strip()
    if not ident:
        return jsonify({'error': 'identifier required'}), 400
    try:
        from services.common import build_state
        if build_state.is_building():
            return jsonify({'error': 'a course is already being built'}), 409
    except Exception:
        pass
    try:
        meta = requests.get(f'https://archive.org/metadata/{ident}',
                            headers={'User-Agent': 'Helga/1.0'},
                            timeout=25).json()
    except Exception:
        return jsonify({'error': 'could not reach the archive'}), 502

    restricted = str((meta.get('metadata') or {}).get(
        'access-restricted-item', '')).lower() == 'true'
    files = [f.get('name', '') for f in (meta.get('files') or [])]
    fulltext = [f for f in files if f.endswith('_djvu.txt')]
    if restricted or not fulltext:
        return jsonify({
            'error': 'This book is lending-only, so Helga cannot read its full '
                     'text. It will not build a course from a description.'}), 422

    title = (meta.get('metadata') or {}).get('title') or ident
    if isinstance(title, list):
        title = title[0]

    # This used to end here: validate the book, answer
    # {'status':'started'} 202, and start NOTHING — no thread, no core POST.
    # The 202 made it read as queued, so the user was sent to /build to watch
    # an elapsed counter for a build that never existed. It now does what the
    # upload path does: fetch the text, save it where the pipeline reads
    # uploads from, and hand core the same event an uploaded book sends —
    # one build path, not two.
    try:
        text_url = f'https://archive.org/download/{ident}/{fulltext[0]}'
        r = requests.get(text_url, headers={'User-Agent': 'Helga/1.0'},
                         timeout=120, stream=True)
        r.raise_for_status()
        upload_dir = '/app/data/uploads'
        os.makedirs(upload_dir, exist_ok=True)
        safe = secure_filename(f'{ident}.txt')
        filepath = os.path.join(upload_dir, safe)
        size = 0
        # 80 MB cap: the largest full-text scans are tens of MB; anything past
        # this is a mis-tagged item and would only stall the build.
        with open(filepath, 'wb') as fh:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > 80 * 1024 * 1024:
                    fh.close()
                    os.unlink(filepath)
                    return jsonify({'error': 'This text is implausibly large '
                                    'for a book and was not downloaded.'}), 422
                fh.write(chunk)
    except requests.RequestException as e:
        app.logger.warning("book text download failed for %s: %s", ident, e)
        return jsonify({'error': 'The archive did not deliver the book text. '
                        'Nothing was started — try again.'}), 502

    event = {
        'type': 'TEXT_INPUT',
        'payload': {
            'text': f'create course from epub {filepath}',
            'source': 'library_archive',
            'filepath': filepath,
            'book_title': str(title)[:180],
        },
    }
    try:
        resp = requests.post(f'{SERVICES["core"]}/event', json=event, timeout=60)
        if resp.status_code >= 400:
            raise requests.RequestException(f'core answered {resp.status_code}')
    except requests.RequestException as e:
        app.logger.error("book build handoff failed: %s", e)
        try:
            os.unlink(filepath)
        except OSError:
            pass
        return jsonify({'error': 'The book downloaded but the build service '
                        'did not accept it. Nothing was started.'}), 502

    return jsonify({'status': 'started', 'title': title, 'identifier': ident,
                    'text_file': fulltext[0]}), 202


@app.route('/build')
def build_page():
    """Live visualisation of a course build.

    A build takes tens of minutes on this hardware and previously showed a
    spinner and one line of text. The builder already emits a structured status
    stream; this renders it.
    """
    return render_template('build.html')


@app.route('/library')
def library_page():
    """Find a book to build a course from, or upload your own."""
    return render_template('library.html')


@app.route('/api/books/search', methods=['GET'])
def books_search():
    """Search Internet Archive for texts, with an HONEST availability state.

    Availability is THREE different answers and the UI must not present them as
    one: full public-domain text, borrow-only, or metadata alone. A learner who
    picks a borrow-only book and receives a course generated from a catalogue
    blurb has been misled by the interface, not by the model.

    Open Library's search.json is deliberately not used: openlibrary.org
    answers in 0.18s but the search endpoint did not respond within 45s across
    repeated attempts. Internet Archive is the same corpus through a door that
    works.
    """
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'query required'}), 400
    try:
        resp = requests.get(
            'https://archive.org/advancedsearch.php',
            params={'q': f'title:({q}) AND mediatype:texts',
                    'fl[]': ['identifier', 'title', 'creator', 'year',
                             'licenseurl'],
                    'rows': 12, 'page': 1, 'output': 'json'},
            headers={'User-Agent': 'Helga/1.0 (offline tutor)'}, timeout=25)
        docs = (resp.json().get('response') or {}).get('docs', [])
    except Exception as e:
        logger.warning(f"book search failed: {e}")
        return jsonify({'error': 'book search is unavailable right now',
                        'results': []}), 502

    def _one(v):
        return v[0] if isinstance(v, list) and v else v

    out, seen = [], set()
    for d in docs:
        title = _one(d.get('title'))
        ident = d.get('identifier')
        if not title or not ident:
            continue
        key = re.sub(r'[^a-z0-9]+', '', str(title).lower())[:60]
        if key in seen:          # the Archive holds many scans of one work
            continue
        seen.add(key)
        out.append({
            'identifier': ident,
            'title': str(title)[:180],
            'author': str(_one(d.get('creator')) or '')[:120],
            'year': d.get('year'),
            'open_license': bool(d.get('licenseurl')),
            'availability': 'unknown',      # resolved on demand, it costs a call
        })
    return jsonify({'results': out})


@app.route('/api/books/availability', methods=['GET'])
def book_availability():
    """Can we actually READ this book, or only see that it exists?"""
    ident = (request.args.get('identifier') or '').strip()
    if not ident:
        return jsonify({'error': 'identifier required'}), 400
    try:
        r = requests.get(f'https://archive.org/metadata/{ident}',
                         headers={'User-Agent': 'Helga/1.0 (offline tutor)'},
                         timeout=25)
        meta = r.json()
    except Exception as e:
        logger.warning(f"availability check failed for {ident}: {e}")
        return jsonify({'availability': 'unknown',
                        'reason': 'could not reach the archive'}), 502

    restricted = str((meta.get('metadata') or {}).get(
        'access-restricted-item', '')).lower() == 'true'
    files = [f.get('name', '') for f in (meta.get('files') or [])]
    fulltext = [f for f in files if f.endswith('_djvu.txt')]  # must match the build gate: *_meta.txt sidecars are not text

    if restricted or not fulltext:
        return jsonify({
            'availability': 'restricted',
            'can_build': False,
            'reason': 'This book is lending-only, so its full text cannot be '
                      'read. Helga will not build a course from a catalogue '
                      'description.',
        })
    return jsonify({
        'availability': 'full_text',
        'can_build': True,
        'text_file': fulltext[0],
        'size_hint': len(fulltext),
    })


@app.route('/api/progress/overview', methods=['GET'])
def progress_overview():
    """Backs the Progress surface. Reads local storage directly, like
    /api/schedule — this is derived state, not something RAG owns."""
    try:
        storage = _get_storage()
        return jsonify(storage.mastery_overview(
            course_uid=request.args.get('course_uid'),
            student_id=current_student_id())), 200
    except Exception as e:
        logger.error(f"Progress overview failed: {e}", exc_info=True)
        # An empty shape, not a fake one: the page renders its own "no data
        # yet" state rather than showing invented zeros as though measured.
        return jsonify({'error': str(e), 'courses': [], 'concepts': [],
                        'gaps': [], 'totals': {}}), 502


@app.route('/test')
@app.route('/quiz')
def test_page():
    return redirect(url_for('practice_page', tab='quiz'))


@app.route('/review')
def review_page():
    return redirect(url_for('practice_page', tab='due'))

@app.route('/palace')
def palace_page():
    """Memory Palace — the third advertised learning mode.

    A3: this previously redirected to home ("removed"), while the FSM kept the
    full MEMORY_PALACE state and five handlers and librarian kept /palace/start,
    /locus/next and /anchor. The feature was half-present: advertised, backed by
    working services, and unreachable. Restored rather than left in that state.
    """
    return render_template('palace.html')

@app.route('/schedule')
def schedule_page():
    return redirect(url_for('practice_page', tab='upcoming'))

# --- Schedule API (direct StorageManager access via shared data volume) ---
def _get_storage():
    """Lazy-init StorageManager for schedule reads."""
    if not hasattr(app, '_storage'):
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
        from services.common.storage import StorageManager
        data_root = os.environ.get('DATA_ROOT', '/app/data')
        app._storage = StorageManager(data_root)
    return app._storage

helga_auth.init_auth(_get_storage)

# B19/FE6: parent dashboard blueprint (role-gated under /parent/*)
from parent_api import create_parent_blueprint
app.register_blueprint(create_parent_blueprint(_get_storage))

# Course export/import proxy — the buttons on the courses page.
try:
    from share_api import share_api
    app.register_blueprint(share_api)
except Exception as _e:
    logger.error(f"share_api blueprint unavailable: {_e}")

# The multi-source library (IA + Gutenberg + Wikibooks + Wikiversity +
# OpenStax, with proxied disk-cached covers). Lives in its own /api/library/*
# namespace on purpose: registering a second rule on an existing URL is not an
# error in Flask, it is silent shadowing.
try:
    from library_api import library_api
    app.register_blueprint(library_api)
except Exception as _e:
    # The library search degrading must not take the whole UI down with it.
    logger.error(f"library_api blueprint unavailable: {_e}")

# Notebook page + print routes + proxies.
try:
    from notes_api import notes_api
    app.register_blueprint(notes_api)
except Exception as _e:
    logger.error(f"notes_api blueprint unavailable: {_e}")

# First-run setup: hardware check, Ollama, the model pull, voice, containers.
try:
    from setup_api import setup_api
    app.register_blueprint(setup_api)
except Exception as _e:
    logger.error(f"setup_api blueprint unavailable: {_e}")

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    try:
        storage = _get_storage()
        start = request.args.get('start')
        end = request.args.get('end')
        course_uid = request.args.get('course_uid')
        sid_student = current_student_id()
        storage.schedule.mark_overdue(student_id=sid_student)
        reviews = storage.schedule.get_scheduled_reviews(
            start_date=start, end_date=end, course_uid=course_uid,
            student_id=sid_student,
        )
        return jsonify({'reviews': reviews}), 200
    except Exception as e:
        # An unreadable schedule is not an empty month. The calendar renders
        # {'reviews': []} as "nothing scheduled", which tells a learner they
        # are free on days they may not be.
        logger.error(f"Schedule fetch error: {e}")
        return jsonify({'error': 'schedule unavailable'}), 503

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
            storage.schedule.complete_review(review_id, student_id=current_student_id())
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Schedule complete error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule/stats', methods=['GET'])
def get_schedule_stats():
    try:
        storage = _get_storage()
        sid_student = current_student_id()
        storage.schedule.mark_overdue(student_id=sid_student)
        upcoming = storage.schedule.get_upcoming_count(days=7, student_id=sid_student)
        streak = storage.settings.get('streak', 0) if hasattr(storage, 'settings') else 0
        # Count overdue
        all_reviews = storage.schedule.get_scheduled_reviews(student_id=sid_student)
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


@app.route('/api/profile/reset', methods=['POST'])
def reset_profile():
    """Reset learning progress (keeps course content).

    A3: the Settings "Reset Progress" button has always POSTed here, but no
    proxy existed on web-ui — only the implementation in librarian — so the
    control 404'd. Mirrors the GET/PATCH proxies above.
    """
    try:
        resp = requests.post(f'{SERVICES["rag"]}/api/profile/reset', timeout=10)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Profile reset error: {e}")
        return jsonify({"error": str(e)}), 502


@app.route('/api/gamification', methods=['GET'])
def get_gamification():
    """Get gamification state (XP, level, streak, achievements)."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/gamification',
                            params={'student_id': current_student_id()}, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        # Fabricated zeros are worse than an error here: a fake 0-day streak
        # reads as "you lost your streak", which is the opposite of motivating
        # and not even true.
        app.logger.warning("gamification proxy failed: %s", e)
        return jsonify({'error': 'gamification unavailable'}), 503


@app.route('/api/gamification/award_xp', methods=['POST'])
def award_xp():
    """Award XP after a graded interaction (student from session, never body)."""
    try:
        body = {**request.get_json(force=True), 'student_id': current_student_id()}
        resp = requests.post(
            f'{SERVICES["rag"]}/api/gamification/award_xp',
            json=body,
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
        body = request.get_json(force=True) if request.data else {}
        body['student_id'] = current_student_id()
        resp = requests.post(
            f'{SERVICES["rag"]}/api/gamification/check_streak',
            json=body,
            timeout=5
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Check streak error: {e}")
        return jsonify({"streak_days": 0, "incremented": False}), 502


@app.route('/api/fsm_state', methods=['GET'])
def proxy_fsm_state():
    try:
        resp = requests.get(f'{SERVICES["core"]}/state',
                            params={'student_id': current_student_id()}, timeout=2)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/media/<name>', methods=['GET'])
def serve_cached_media(name):
    """Serve an image cached at course-build time (B13.5).

    The filename comes from a URL, so it is validated against a whitelist of
    exactly the pattern we ourselves write (16 hex chars + a known extension)
    before touching the filesystem — that is what stops `../../helga.db`.
    send_from_directory is given an absolute root for the same reason.

    Served from here rather than /static so the path matches what
    visual_aids._SAFE_SRC permits, and so attribution stays queryable alongside.
    """
    from services.common.media_cache import media_root, safe_media_name
    if not safe_media_name(name):
        return jsonify({'error': 'bad media name'}), 400
    root = os.path.abspath(media_root())
    if not os.path.exists(os.path.join(root, name)):
        return jsonify({'error': 'not cached'}), 404
    resp = send_from_directory(root, name)
    # Content-addressed by URL hash, so the bytes for a given name never change.
    resp.headers['Cache-Control'] = 'public, max-age=604800, immutable'
    return resp


@app.route('/api/media/<name>/attribution', methods=['GET'])
def media_attribution(name):
    """Who made this and under what licence. Kept queryable because CC BY
    without the BY is infringement with extra steps."""
    from services.common.media_cache import attribution
    record = attribution(name)
    return (jsonify(record), 200) if record else (jsonify({'error': 'unknown'}), 404)


@app.route('/api/aid/<aid_id>', methods=['GET'])
def proxy_visual_aid(aid_id):
    """Full spec for one visual teaching aid (B13).

    Deliberately NOT part of /api/fsm_state: the transcript carries a ~200-byte
    descriptor per aid, and the spec is fetched once here and cached in the
    browser. Folding specs into the state payload would put every diagram in the
    session on a 2-second poll.

    A 404 is an ordinary outcome — the core's aid store is a bounded LRU, so an
    old diagram can be evicted while its message is still on screen. The client
    falls back to the description it already holds.
    """
    try:
        resp = requests.get(f'{SERVICES["core"]}/api/aid/{aid_id}',
                            params={'student_id': current_student_id()}, timeout=5)
        out = jsonify(resp.json())
        if resp.status_code == 200:
            out.headers['Cache-Control'] = 'private, max-age=600'
        return out, resp.status_code
    except Exception as e:
        logger.warning(f"Visual aid proxy failed for {aid_id}: {e}")
        return jsonify({'error': 'aid unavailable'}), 502


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
    # B15.5: student_id comes from the authenticated session — a forged value
    # in the client body is overwritten, never trusted (spec 03 §5).
    sid_student = current_student_id()
    if not sid_student:
        return jsonify({'error': 'student session required'}), 401
    body = {**(request.json or {}), 'student_id': sid_student}
    try:
        resp = requests.post(f'{SERVICES["core"]}/event', json=body, timeout=60)
        # push-on-completion: this student's fresh state to their room
        try:
            state = _fetch_student_state(sid_student)
            socketio.emit('state_update', state, room=f"student:{sid_student}")
        except Exception as push_err:
            logger.warning(f"post-event state push failed (non-fatal): {push_err}")
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
    # B15.5: the FSM stamps every payload with its owner; web-ui emits to that
    # student's room only. Missing student_id → drop (fail closed, never
    # broadcast another student's tokens).
    owner = (data or {}).get('student_id')
    if not owner:
        logger.warning("Dropping unowned status payload (no student_id)")
        return jsonify({'status': 'dropped'}), 202
    room = f"student:{owner}"
    if data.get('type') == 'stream_token':
        socketio.emit('stream_token', {
            'token': data.get('token', ''),
            'done': data.get('done', False),
        }, room=room)
        return jsonify({'status': 'ok'}), 200
    socketio.emit('status_update', data, room=room)
    return jsonify({'status': 'ok'}), 200

@app.route('/api/search', methods=['GET'])
def proxy_search():
    """Proxy global search to the RAG FTS5 /search (used by the header search UI)."""
    try:
        resp = requests.get(f'{SERVICES["rag"]}/search', params=request.args, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException:
        # "No matches" and "search is down" are different sentences, and the
        # header search box was showing the first whenever RAG was the second.
        return jsonify({'error': 'search unavailable'}), 503

@app.route('/api/courses', methods=['GET'])
def get_courses():
    try:
        resp = requests.get(f'{SERVICES["rag"]}/api/courses', timeout=2)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        # The courses page has a real error state with a Retry button; this
        # catch was routing around it, drawing an empty shelf over a learner's
        # actual library whenever RAG was unreachable.
        logger.error(f"Error fetching courses: {e}")
        return jsonify({'error': 'course service unavailable'}), 503

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
            # Without this the event lands on the DEFAULT student's FSM: on a
            # multi-profile install, profile B's course switch would mutate
            # someone else's session. /api/event already injects it; these two
            # side doors did not.
            'student_id': current_student_id(),
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

@app.route('/api/system/resources', methods=['GET'])
def system_resources():
    """Feeds the Settings storage panel and the memory safeguard card.

    Short timeout: this is polled while the app is open, and a slow answer is
    worse than no answer for a card whose entire job is to disappear promptly
    once there is room again.
    """
    try:
        r = requests.get(f'{SERVICES["core"]}/api/system/resources', timeout=8)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        app.logger.warning("system resources proxy failed: %s", e)
        return jsonify({'error': 'unavailable'}), 200


def _load_startup_preflight():
    """Import services.common.startup_preflight, or return None.

    It is not always importable from here. The web-ui image is built with
    `context: services/web-ui`, so `services/common` is not in it — unlike
    core-logic, whose Dockerfile copies both. Every other cross-service import
    in this file is written the same defensive way for the same reason.

    Failing to load is reported by name to the caller rather than being turned
    into a clean-looking "everything is fine", which is the failure mode a
    preflight exists to prevent.
    """
    if not hasattr(app, '_preflight_mod'):
        mod = None
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        for candidate in (root, '/app'):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
        try:
            from services.common import startup_preflight as mod  # noqa: F401
        except Exception as e:  # ImportError, and anything the module does at import
            app.logger.warning("startup preflight unavailable: %s", e)
            app._preflight_reason = str(e)
            mod = None
        app._preflight_mod = mod
    return app._preflight_mod


@app.route('/api/system/preflight', methods=['GET'])
def system_preflight():
    """Can this machine run Helga right now?

    The readings come from core's /api/system/resources when core is up,
    because core is where memory is already measured and two services
    measuring separately would disagree in public. When core is unreachable the
    module measures locally instead — a worse reading, said out loud in the
    payload rather than passed off as the real one.
    """
    resources = None
    core_note = None
    try:
        r = requests.get(f'{SERVICES["core"]}/api/system/resources', timeout=8)
        if r.ok:
            resources = r.json()
    except Exception as e:
        app.logger.warning("preflight could not reach core: %s", e)
        core_note = f"the core service did not answer ({e}); measured locally"

    mod = _load_startup_preflight()
    if mod is None:
        reason = getattr(app, '_preflight_reason', 'module not found')
        return jsonify({
            'state': 'degraded',
            'summary': 'The startup check is not installed in this build.',
            'checks': [{
                'id': 'preflight', 'label': 'Startup preflight',
                'state': 'unknown',
                'reason': f'services.common.startup_preflight could not be '
                          f'imported here: {reason}',
                'remedy': 'This says nothing about the machine — only that it '
                          'was not measured.',
                'measured': {},
            }],
            'blocking': [], 'advisory': False, 'scope': 'unknown',
            'notes': [n for n in (core_note,) if n],
            'checked_at': time.time(),
        }), 200

    verdict = mod.preflight(resources=resources)
    if core_note:
        verdict.setdefault('notes', []).append(core_note)
    return jsonify(verdict), 200


# --- Degree programmes -------------------------------------------------------
# degree.js and home.js have called these three since they were written; there
# was nothing on the other end, so the flagship surface has been rendering
# example data. These connect it to the planner.

@app.route('/api/programs', methods=['GET'])
def list_programs():
    try:
        r = requests.get(f'{SERVICES["core"]}/api/programs', timeout=6)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        # 503, never 200-with-empty: an empty 200 is indistinguishable from
        # "you have no programmes", and the degree page draws its example plan
        # over a learner's real data on exactly that misreading.
        app.logger.warning("list_programs proxy failed: %s", e)
        return jsonify({'error': 'programme service unavailable'}), 503


@app.route('/api/program/<uid>', methods=['GET'])
def get_program(uid):
    try:
        r = requests.get(f'{SERVICES["core"]}/api/program/{uid}', timeout=8)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/program', methods=['POST'])
def create_program():
    """Planning is fast relative to a build but not instant, so this gets a
    long timeout — it consults curriculum sources and the model."""
    try:
        r = requests.post(f'{SERVICES["core"]}/api/program',
                          json=request.get_json(silent=True) or {}, timeout=180)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/program/<uid>/choose', methods=['POST'])
def choose_program_elective(uid):
    try:
        r = requests.post(f'{SERVICES["core"]}/api/program/{uid}/choose',
                          json=request.get_json(silent=True) or {}, timeout=8)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/concept_sources', methods=['GET'])
def get_concept_sources():
    """Proxy for the trust panel in the session view.

    A source list is supporting detail, never the lesson itself, so a failure
    here answers available:false with the reason attached rather than a 502 —
    the panel says it could not load and the session carries on.
    """
    uid = request.args.get('uid')
    course_uid = request.args.get('course_uid')
    if not uid or not course_uid:
        return jsonify({'error': 'uid and course_uid are required'}), 400
    try:
        resp = requests.get(f'{SERVICES["rag"]}/concept_sources',
                            params={'uid': uid, 'course_uid': course_uid},
                            timeout=4)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        app.logger.warning("concept_sources proxy failed: %s", e)
        return jsonify({'available': False, 'sources': [],
                        'error': 'sources unavailable'}), 200


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
    
    # Accept exactly what services/common/document_extract.py can actually
    # parse — no more, no less. PDF is now genuinely read (pypdf), so it moves
    # from the refusal list to the accepted list; the formats still without a
    # parser are refused with a reason rather than accepted-then-ignored, which
    # is how this feature used to behave.
    name = file.filename.lower()
    ACCEPTED = ('.epub', '.pdf', '.md', '.markdown', '.txt')
    if not name.endswith(ACCEPTED):
        if name.endswith(('.doc', '.docx', '.mobi', '.azw', '.azw3')):
            return jsonify({'error':
                f'{os.path.splitext(name)[1]} is not supported — no parser is '
                f'installed for it. Convert to EPUB, PDF, Markdown or plain text first.'
            }), 400
        return jsonify({'error': f'Accepted formats: {", ".join(ACCEPTED)}'}), 400
    
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
            # Same student routing as /api/event — an upload from profile B
            # must not build on the default profile's FSM.
            'student_id': current_student_id(),
            'payload': {
                'text': f'create course from epub {filepath}',
                'source': 'epub_upload',
                'filepath': filepath
            }
        }
        try:
            resp = requests.post(f'{SERVICES["core"]}/event', json=event, timeout=60)
            # The message states what the feature ACTUALLY does now: the book's
            # own structure becomes the course (a textbook's chapters become
            # modules and its sections lessons; a novel's chapters become
            # lessons), and every concept is written from the chapter it came
            # from. Advertising must match the machinery.
            return jsonify({'status': 'processing', 'message':
                'Book uploaded. The course will follow the book\'s own '
                'structure — chapters become lessons — and every concept is '
                'read from the chapter it belongs to. Progress appears in the '
                'status bar chapter by chapter.'}), 202
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
        params = {'topic': topic, 'course_uid': course_uid}
        # The schedule calendar sends target_date for "what is due on THIS
        # day"; the proxy dropped it, so every future day listed today's cards
        # under that day's heading.
        if request.args.get('target_date'):
            params['target_date'] = request.args['target_date']
        resp = requests.get(f'{SERVICES["rag"]}/api/due_cards', params=params, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        # A failed request is not an empty schedule. The librarian side of
        # this was fixed today; answering {'cards': []} 200 here re-introduced
        # the identical lie one layer up whenever RAG itself was unreachable.
        return jsonify({'error': 'review service unavailable'}), 503

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
    """Get next locus in memory palace.

    A3: previously dropped course_uid, which librarian's /locus/next requires —
    the call always 400'd, so walking the palace could never work.
    """
    current = request.args.get('current', '')
    course_uid = request.args.get('course_uid', '')
    try:
        resp = requests.get(
            f'{SERVICES["rag"]}/locus/next',
            params={'current': current, 'course_uid': course_uid},
            timeout=5)
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
        # No file at all is a fresh install and defaults are honest (handled
        # above). A file that EXISTS but cannot be read is corruption, and
        # silently substituting level='intermediate' would quietly steer the
        # tutor while the learner's real settings sit unreadable on disk.
        logger.error(f"Failed to load user profile: {e}")
        return jsonify({'error': 'profile unreadable'}), 503

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
@app.route('/api/course_presets', methods=['GET'])
def course_presets():
    """Named starting points for course creation.

    Served from the SAME definitions the builder uses, so the UI cannot drift
    from what actually gets enforced — the `requires` list is the depth
    contract for that mastery level, not marketing copy.
    """
    try:
        from services.core.course_builder import list_presets
        return jsonify({"presets": list_presets()}), 200
    except Exception as e:
        logger.error(f"preset listing failed: {e}")
        # Empty rather than invented: a fabricated preset would promise a level
        # nothing enforces.
        return jsonify({"presets": [], "error": str(e)}), 200


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
        resp = requests.get(f'{SERVICES["core"]}/api/creation_status',
                            # Without this, core falls back to the DEFAULT
                            # student's FSM and a multi-profile install
                            # reads someone else's build phase.
                            params={'student_id': current_student_id()}, timeout=5)
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
        # Same class as due_cards above — and this one silently defeated the
        # librarian's own 503, because practice.js only ever sees this proxy.
        app.logger.warning("due_concepts proxy failed: %s", e)
        return jsonify({'error': 'review service unavailable'}), 503


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



# --- B20.2/B20.3: Stripe webhook → subscriptions mirror -----------------------

def _billing_parse_event():
    """Verify + parse the webhook. Real deployments verify the Stripe
    signature; HELGA_BILLING_TEST=true accepts unsigned JSON for tests/dev."""
    payload = request.get_data()
    if os.environ.get('HELGA_BILLING_TEST', '').lower() == 'true':
        return request.get_json(force=True)
    try:
        import stripe
        return stripe.Webhook.construct_event(
            payload, request.headers.get('Stripe-Signature', ''),
            os.environ['STRIPE_WEBHOOK_SECRET'])
    except KeyError:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return None
    except Exception as e:
        logger.warning(f"webhook signature rejected: {e}")
        return None


def _billing_apply(st, event):
    """Handler table (spec 09 §3.2). Mirror writes are idempotent upserts
    keyed on parent_id; seat decreases archive over-limit students (never
    delete — records retained per spec 08)."""
    from datetime import datetime as _dt
    etype = event.get('type', '')
    obj = (event.get('data') or {}).get('object') or {}
    meta = obj.get('metadata') or {}
    parent_id = meta.get('parent_id') or obj.get('client_reference_id')

    def _period_end(o):
        ts = o.get('current_period_end')
        return _dt.utcfromtimestamp(ts).isoformat() if ts else None

    if etype == 'checkout.session.completed':
        if not parent_id:
            raise ValueError('checkout event missing parent_id reference')
        st.subscriptions.upsert(
            parent_id,
            provider_customer_id=obj.get('customer'),
            provider_sub_id=obj.get('subscription'),
            plan=meta.get('plan'),
            seats=int(meta.get('seats') or 1),
            status='trialing' if obj.get('trial') else 'active',
            current_period_end=_period_end(obj))
    elif etype == 'customer.subscription.updated':
        if not parent_id:
            return
        seats = 1
        items = ((obj.get('items') or {}).get('data') or [])
        if items:
            seats = int(items[0].get('quantity') or 1)
        st.subscriptions.upsert(parent_id, status=obj.get('status', 'active'),
                                seats=seats, current_period_end=_period_end(obj))
        # downgrade: archive newest over-limit students (spec 09 §4.2);
        # list_students returns insertion order, so everything beyond the
        # first `seats` is the newest overflow
        active = st.accounts.list_students(parent_id)
        for s in active[seats:]:
            st.accounts.update_student(s['id'], status='archived')
            logger.info(f"seat downgrade archived student {s['id']}")
    elif etype == 'customer.subscription.deleted':
        if parent_id:
            st.subscriptions.upsert(parent_id, status='canceled')
    elif etype == 'invoice.paid':
        if parent_id:
            st.subscriptions.upsert(parent_id, status='active',
                                    current_period_end=_period_end(obj))
    elif etype == 'invoice.payment_failed':
        if parent_id:
            st.subscriptions.upsert(parent_id, status='past_due')
            st.notifications.create(parent_id, 'parent', 'system',
                                    title='Payment issue',
                                    body='Your last payment did not go through. '
                                         'Please update your payment method.')
    else:
        logger.debug(f"unhandled billing event {etype}")


@app.route('/api/billing/webhook', methods=['POST'])
def stripe_webhook():
    """No CSRF (Stripe is not a browser); authenticated by signature; fast
    200; idempotent via the billing_events ledger (at-least-once delivery)."""
    event = _billing_parse_event()
    if event is None:
        return jsonify({'error': 'bad signature'}), 400
    st = _get_storage()
    event_id = event.get('id') or ''
    if event_id and st.subscriptions.event_seen(event_id):
        return ('', 200)
    try:
        _billing_apply(st, event)
        if event_id:
            st.subscriptions.mark_event(event_id, event.get('type'))
    except Exception:
        logger.exception(f"billing webhook handler failed: {event.get('type')}")
        return ('', 500)   # Stripe retries with backoff
    return ('', 200)


# --- B24.3: in-app notifications (bell) --------------------------------------

@app.route('/api/notifications', methods=['GET'])
def api_notifications():
    """Notifications for the current principal — the launched student, or the
    parent on a bare parent session."""
    recipient = session.get('student_id') or current_parent_id() or current_student_id()
    if not recipient:
        return jsonify({'notifications': [], 'unread': 0})
    st = _get_storage()
    unread_only = request.args.get('unread') == '1'
    return jsonify({
        'notifications': st.notifications.list_for(recipient, unread_only=unread_only)[:50],
        'unread': st.notifications.unread_count(recipient),
    })


@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
@csrf_protect
def api_notification_read(notification_id):
    recipient = session.get('student_id') or current_parent_id() or current_student_id()
    if not recipient:
        return jsonify({'error': 'no session'}), 401
    _get_storage().notifications.mark_read(notification_id, recipient)
    return jsonify({'status': 'ok'})


# --- B15.4: Auth routes (design spec 03 §2) ----------------------------------
# Parent: email + password (argon2id). Student: parent-launch profile pick, or
# avatar + 4-digit PIN on a shared device. Email verification and password
# reset need outbound email — they land with B24.1 (notifications); until then
# signup activates immediately and reset is parent-support-driven.

@app.route('/signup', methods=['GET', 'POST'])
@csrf_protect
def signup_page():
    if request.method == 'GET':
        return render_template('signup.html')
    data = request.form if request.form else (request.get_json(silent=True) or {})
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    display_name = (data.get('display_name') or '').strip()
    if not email or '@' not in email or len(password) < 8:
        return jsonify({'error': 'valid email and a password of 8+ characters required'}), 400
    st = _get_storage()
    if st.accounts.get_parent_by_email(email):
        # generic message — no user enumeration
        return jsonify({'error': 'unable to create account'}), 400
    parent_id = st.accounts.create_parent(email, hash_secret(password),
                                          display_name, status='active')
    st.consent.record(parent_id, 'tos', True, 'v1',
                      method='checkbox', ip_address=request.remote_addr)
    helga_auth.login_parent(parent_id)
    return jsonify({'status': 'ok', 'parent_id': parent_id}), 201


@app.route('/login', methods=['GET', 'POST'])
@csrf_protect
def login_page():
    if request.method == 'GET':
        return render_template('login.html')
    data = request.form if request.form else (request.get_json(silent=True) or {})
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    parent = _get_storage().accounts.get_parent_by_email(email)
    if (not parent or parent.get('status') != 'active'
            or not verify_secret(parent.get('password_hash', ''), password)):
        return jsonify({'error': 'invalid credentials'}), 401  # generic, no enumeration
    helga_auth.login_parent(parent['id'])
    return jsonify({'status': 'ok', 'parent_id': parent['id']})


@app.route('/logout', methods=['POST'])
@csrf_protect
def logout():
    helga_auth.clear_principal()
    return jsonify({'status': 'ok'})


@app.route('/students', methods=['GET'])
@parent_required
def students_picker():
    students = _get_storage().accounts.list_students(current_parent_id())
    if request.args.get('format') == 'json' or request.path.startswith('/api/'):
        return jsonify({'students': students})
    return render_template('students.html', students=students)


@app.route('/api/students', methods=['POST'])
@csrf_protect
@parent_required
def create_student():
    data = request.get_json(force=True)
    name = (data.get('display_name') or '').strip()
    grade_band = data.get('grade_band') or '6-8'
    if not name:
        return jsonify({'error': 'display_name required'}), 400
    if grade_band not in ('K-2', '3-5', '6-8', '9-12'):
        return jsonify({'error': 'invalid grade_band'}), 400
    st = _get_storage()
    # B20.3 seat enforcement: active students may not exceed the seat allowance
    if st.accounts.count_active_students(current_parent_id()) >= \
            st.subscriptions.seats_for(current_parent_id()):
        return jsonify({'error': 'seat limit reached — archive a learner or upgrade'}), 402
    pin = data.get('pin')
    pin_hash = hash_secret(str(pin)) if pin else None
    student_id = st.accounts.create_student(
        current_parent_id(), name, grade_band=grade_band,
        grade_numeric=data.get('grade_numeric'), pin_hash=pin_hash,
        interests=data.get('interests') or [])
    st.consent.record(current_parent_id(), 'coppa_data', True, 'v1',
                      student_id=student_id, method='checkbox',
                      ip_address=request.remote_addr)
    return jsonify({'status': 'ok', 'student_id': student_id}), 201


@app.route('/students/<student_id>/launch', methods=['POST'])
@csrf_protect
@owns_student_required('student_id')
def launch_student_session(student_id):
    # parent already authed — no PIN needed (spec 03 §2.4 Path A)
    helga_auth.launch_student(student_id)
    return jsonify({'status': 'ok', 'student_id': student_id})


@app.route('/students/exit', methods=['POST'])
@csrf_protect
def exit_student_session():
    helga_auth.exit_student()
    return jsonify({'status': 'ok'})


@app.route('/students/<student_id>/pin-set', methods=['POST'])
@csrf_protect
@owns_student_required('student_id')
def set_student_pin(student_id):
    pin = str((request.get_json(force=True) or {}).get('pin') or '')
    if not (pin.isdigit() and len(pin) == 4):
        return jsonify({'error': 'PIN must be exactly 4 digits'}), 400
    _get_storage().accounts.update_student(student_id, pin_hash=hash_secret(pin))
    helga_auth.reset_pin_failures(student_id)
    return jsonify({'status': 'ok'})


@app.route('/family/<parent_id>', methods=['GET'])
def family_pin_grid(parent_id):
    """Avatar grid for one family's PIN login (spec 03 §2.4 Path B). The
    family scope is the URL; the PIN is verified within it. Only students
    with a PIN set are shown."""
    st = _get_storage()
    parent = st.accounts.get_parent(parent_id)
    if not parent or parent.get('status') != 'active':
        abort(404)
    students = [s for s in st.accounts.list_students(parent_id) if s.get('pin_hash')]
    safe = [{'id': s['id'], 'display_name': s['display_name'],
             'avatar_url': s.get('avatar_url'), 'grade_band': s['grade_band']}
            for s in students]
    return render_template('family.html', parent_id=parent_id, students=safe)


@app.route('/students/<student_id>/pin', methods=['POST'])
@csrf_protect
def student_pin_login(student_id):
    data = request.form if request.form else (request.get_json(silent=True) or {})
    pin = str(data.get('pin') or '')
    family = data.get('parent_id') or ''
    st = _get_storage()
    student = st.accounts.get_student(student_id)
    # PIN can never select a sibling-family's student (spec 03 §8.1)
    if (not student or student.get('status') != 'active'
            or not student.get('pin_hash') or student.get('parent_id') != family):
        return jsonify({'error': 'invalid'}), 404
    wait = helga_auth.pin_locked(student_id)
    if wait:
        return jsonify({'error': f'locked, retry in {wait}s'}), 423
    if not verify_secret(student['pin_hash'], pin):
        helga_auth.record_pin_failure(student_id)
        return jsonify({'error': 'invalid'}), 401
    helga_auth.reset_pin_failures(student_id)
    helga_auth._regenerate_session()
    session['parent_id'] = student['parent_id']
    helga_auth.launch_student(student_id)
    return jsonify({'status': 'ok', 'student_id': student_id})


@app.route('/api/auth/session', methods=['GET'])
def auth_session_info():
    """Who am I — drives the FE7 shell (login state, active student)."""
    return jsonify({
        'parent_id': current_parent_id(),
        'student_id': session.get('student_id'),
        'role': session.get('role'),
        'effective_student_id': current_student_id(),
    })


if __name__ == '__main__':
    _monitored_spawn(state_poller, "state_poller")
    _monitored_spawn(health_check_poller, "health_check_poller")
    # STT/Audio service connections removed — text-only mode
    socketio.run(app, host='0.0.0.0', port=5000)
