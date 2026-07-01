"""Auth & identity for the web-ui (B15.4, design spec 03 §1–§2).

Two roles, one account tree: parents (email+password) own students
(profile pick or 4-digit PIN). The signed Flask session cookie carries the
contract keys — the same cookie Socket.IO sees, so one login authenticates
both HTTP routes and the websocket:

    session['parent_id']   par_…   always present once any login completes
    session['student_id']  stu_…   only inside a launched/PIN student session
    session['role']        'parent' | 'student'

These session keys are the source of truth (spec 03 §1.1). The helper
contract below is the API; wrapping it in Flask-Login later is a localized
swap inside this module.

R0→R1 cutover: until real accounts exist, `current_student_id()` falls back
to the legacy student so the single-user app keeps running. Once a session
carries a real student, the fallback never fires for it.
"""

import logging
import os
import sys
import time
from functools import wraps

from flask import abort, jsonify, redirect, request, session, url_for

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from services.common.storage import DEFAULT_STUDENT_ID, LEGACY_PARENT_ID  # noqa: E402

# --- password / PIN hashing: argon2id (spec 01 §2), werkzeug fallback -------
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError

    _ph = PasswordHasher()

    def hash_secret(secret: str) -> str:
        return _ph.hash(secret)

    def verify_secret(hashed: str, secret: str) -> bool:
        if not hashed:
            return False
        try:
            return _ph.verify(hashed, secret)
        except (VerifyMismatchError, InvalidHashError):
            return False
        except Exception:
            return False

except ImportError:  # pragma: no cover — argon2-cffi is in requirements
    from werkzeug.security import check_password_hash, generate_password_hash

    logger.warning("argon2-cffi unavailable — falling back to werkzeug hashing")

    def hash_secret(secret: str) -> str:
        return generate_password_hash(secret)

    def verify_secret(hashed: str, secret: str) -> bool:
        try:
            return bool(hashed) and check_password_hash(hashed, secret)
        except Exception:
            return False


_storage_getter = None  # injected by app.py (init_auth)


def init_auth(storage_getter):
    """Wire the shared StorageManager accessor in (avoids circular import)."""
    global _storage_getter
    _storage_getter = storage_getter


def _storage():
    return _storage_getter()


# --- helper contract (spec 03 §1.2) -----------------------------------------

def current_parent_id():
    """par_… of the logged-in parent (owning parent for a student session)."""
    try:
        return session.get('parent_id')
    except RuntimeError:
        return None  # outside request context (unit tests calling directly)


def current_student_id():
    """stu_… of the active student session. R0 fallback: the legacy student,
    so the app keeps running before accounts exist. A bare parent session
    (no launched student) gets None — parents never drive a tutoring FSM."""
    try:
        sid = session.get('student_id')
        role = session.get('role')
    except RuntimeError:
        return DEFAULT_STUDENT_ID  # outside request context → legacy posture
    if sid:
        return sid
    if role == 'parent':
        return None
    return DEFAULT_STUDENT_ID


def require_student_id():
    """current_student_id() or abort 401. Used by per-student APIs."""
    sid = current_student_id()
    if not sid:
        abort(401)
    return sid


def owns_student(student_id):
    """True iff the student belongs to the logged-in parent (spec 03 §8.1)."""
    pid = current_parent_id()
    if not pid or not student_id:
        return False
    try:
        return _storage().accounts.owns_student(pid, student_id)
    except Exception as e:
        logger.error(f"owns_student check failed: {e}")
        return False


def principal_still_valid():
    """Re-validate the session principal against the DB (cheap indexed reads):
    a suspended parent or re-homed/archived student is logged out immediately."""
    pid = session.get('parent_id')
    sid = session.get('student_id')
    if not pid and not sid:
        return True  # anonymous (R0 legacy flow) — nothing to validate
    try:
        st = _storage()
        if pid:
            parent = st.accounts.get_parent(pid)
            if not parent or parent.get('status') != 'active':
                return False
        if sid and sid != DEFAULT_STUDENT_ID:
            student = st.accounts.get_student(sid)
            if (not student or student.get('status') != 'active'
                    or (pid and student.get('parent_id') != pid)):
                return False
        return True
    except Exception as e:
        logger.error(f"principal validation failed: {e}")
        return False


def clear_principal():
    for k in ('parent_id', 'student_id', 'role'):
        session.pop(k, None)


def _regenerate_session(keep=()):
    """Session-fixation defence (spec 03 §8.3): wipe everything except `keep`
    keys on privilege change so a pre-auth token can't ride into an authed
    session. (Flask re-signs the cookie on mutation.)"""
    preserved = {k: session[k] for k in keep if k in session}
    session.clear()
    session.update(preserved)


def login_parent(parent_id):
    _regenerate_session()
    session['parent_id'] = parent_id
    session['role'] = 'parent'
    session.permanent = True


def launch_student(student_id):
    """Parent→student launch, or PIN login (parent_id derived). Keeps
    parent_id so the parent can exit back to their dashboard."""
    session['student_id'] = student_id
    session['role'] = 'student'
    session.permanent = True


def exit_student():
    session.pop('student_id', None)
    if session.get('parent_id'):
        session['role'] = 'parent'
    else:
        clear_principal()


# --- decorators (spec 03 §2.2) ----------------------------------------------

def _wants_json():
    return '/api/' in request.path or request.is_json


def _deny(code, message):
    if _wants_json():
        return jsonify({'error': message}), code
    if code == 401:
        return redirect(url_for('login_page', next=request.path))
    abort(code)


def parent_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'parent' or not session.get('parent_id'):
            return _deny(401, 'parent login required')
        if not principal_still_valid():
            clear_principal()
            return _deny(401, 'session no longer valid')
        return f(*args, **kwargs)
    return decorated


def student_session_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_student_id():
            return _deny(401, 'student session required')
        if not principal_still_valid():
            clear_principal()
            return _deny(401, 'session no longer valid')
        return f(*args, **kwargs)
    return decorated


def owns_student_required(arg='student_id'):
    """For parent routes acting on a child. Unknown id → 404 (don't confirm
    existence); known-but-foreign → 403 (spec 03 §8.1)."""
    def wrapper(f):
        @wraps(f)
        @parent_required
        def decorated(*args, **kwargs):
            sid = kwargs.get(arg) or (request.view_args or {}).get(arg)
            try:
                student = _storage().accounts.get_student(sid) if sid else None
            except Exception:
                student = None
            if not student:
                return _deny(404, 'not found')
            if student.get('parent_id') != current_parent_id():
                return _deny(403, 'forbidden')
            return f(*args, **kwargs)
        return decorated
    return wrapper


# --- PIN brute-force lockout (spec 03 §8.2) ----------------------------------
# Per-student exponential backoff after 5 consecutive failures. In-process
# state is sufficient for the R1 single-worker topology; moves to the DB with
# B23.5 multi-worker.

_PIN_MAX_FAILS = 5
_PIN_BACKOFFS = (60, 300, 900, 3600)   # 1m → 5m → 15m → cap 1h
_pin_state = {}   # student_id -> {'fails': int, 'locked_until': float, 'locks': int}


def pin_locked(student_id):
    """Seconds remaining on this student's PIN lock, or 0 if unlocked."""
    st = _pin_state.get(student_id)
    if not st:
        return 0
    remaining = st.get('locked_until', 0) - time.time()
    return max(0, int(remaining))


def record_pin_failure(student_id):
    st = _pin_state.setdefault(student_id, {'fails': 0, 'locked_until': 0, 'locks': 0})
    st['fails'] += 1
    if st['fails'] >= _PIN_MAX_FAILS:
        backoff = _PIN_BACKOFFS[min(st['locks'], len(_PIN_BACKOFFS) - 1)]
        st['locked_until'] = time.time() + backoff
        st['locks'] += 1
        st['fails'] = 0
        logger.warning(f"PIN locked for student {student_id} ({backoff}s)")


def reset_pin_failures(student_id):
    _pin_state.pop(student_id, None)
