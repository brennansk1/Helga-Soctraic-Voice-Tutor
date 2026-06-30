# Design Spec 03 — Multi-Tenancy, Auth & Per-Student FSM (B15.4–B15.8)

> Implementation-ready design for turning Helga from a single global session into N isolated,
> grade-appropriate students on one server. Builds on **spec 01** (canonical schema —
> `parents`, `students`, `enrollments`, `fsm_sessions`, `student_id` as the isolation key) and
> **spec 02** (grade bands resolved at session start). This spec designs the *implementation*:
> auth (B15.4), Socket.IO room scoping (B15.5), the per-student FSM registry (B15.6), per-student
> persistence (B15.7), and the isolation test suite (B15.8). Schema/migrations (B15.1–B15.3) are
> assumed landed per spec 01; this spec consumes them.
>
> **The seam:** B15.6 replaces `fsm = MnemosyneFSM()` (`fsm_logic.py:3498`, one global) with
> `registry.get(student_id)`. Everything downstream (`StorageManager` calls, persistence,
> Socket.IO rooms) keys off `student_id`. The registry boundary is also where the eventual
> stateless "Option A" (§7) plugs in for multi-worker (B23.5) with no call-site churn.

---

## 0. Current state being replaced (verified line refs)

| Concern | Today | Target |
|---|---|---|
| FSM instance | `fsm = MnemosyneFSM()` global singleton, `fsm_logic.py:3498` | `registry.get(student_id)` in `fsm_registry.py` (§4) |
| FSM entry points | `handle_event()` `fsm_logic.py:3502`, `get_state()` `:3512`, plus ~30 `fsm.storage.*`/`fsm.*` calls in REST routes `:3516+` | route reads `student_id` from request, resolves via registry (§4.6) |
| FSM persistence | single `data/user_state.json`, `_save_current_course_progress` `:1415` / `_load_course_progress` `:1462` / `_load_state_from_disk` `:1504` | per-student `fsm_sessions` row (spec 01 §2, §3) (§4.5) |
| Timer thread | one `threading.Thread(self.check_timers)` per FSM, `:345`/`:656` — would be N threads with N students | one registry-level sweeper (§4.4) |
| Storage scoping | `StorageManager` sub-store methods take no `student_id` (`ProgressStore.update_progress` `storage.py:844`) | leading `student_id` param everywhere (spec 01 §8); FSM passes `self.student_id` |
| Status push | `send_status_update` POSTs web-ui `/api/update_thinking_status` `fsm_logic.py:588`; web-ui broadcasts `socketio.emit('status_update', data)` to **all** clients `app.py:582` | payload carries `student_id`; web-ui emits `room=f"student:{id}"` (§3) |
| State fan-out | `state_poller` greenlet polls core `/state` every 2s and `socketio.emit('state_update', full_state)` to **all** `app.py:137,172` | retired for MT; push-to-room on event completion (§3.4) |
| Identity | none; `session['_csrf_token']` only `app.py:96` | Flask-Login + `session['student_id']` + role (§1,§2) |

Cross-student leakage today is structural: one FSM, one JSON file, one broadcast. B15.5–B15.7 fix
all three; B15.4 supplies the identity that keys them; B15.8 proves isolation.

---

## 1. Account & role model

Two roles, one account tree (`parents 1—N students`, spec 01 §2). **There is no separate users
table** — `parents.id` and `students.id` are the principals.

| Role | Principal id | Set in session | Capabilities |
|---|---|---|---|
| `parent` | `par_<hex>` | `session['parent_id']`, `session['role']='parent'` | manage students, approve electives, view progress, billing/consent. **Cannot** drive a tutoring FSM. |
| `student` | `stu_<hex>` | `session['student_id']`, `session['parent_id']` (owning parent), `session['role']='student'` | one tutoring session (its own FSM), own progress/flashcards/reviews. **Scoped to its `parent_id`.** |

A parent "launches" a student → the session gains `student_id` **in addition to** `parent_id`
(role flips to `student` for the duration of the launched session, but `parent_id` is retained so
the parent can return to their dashboard). A student PIN-login session has `student_id` + the
derived `parent_id`, role `student`, and **no** parent dashboard rights.

### 1.1 Session keys (the contract)

```
session = {
  'parent_id':  'par_…'   # always present once any login completes
  'student_id': 'stu_…'   # present only inside a launched/PIN student session
  'role':       'parent' | 'student'
  '_csrf_token': '…'      # existing, app.py:96 — unchanged
}
```

Flask-Login's `current_user` (§2.1) wraps a `Principal` object; the raw `session[...]` keys above
are the source of truth `Principal` is built from, so non-Flask-Login code (the Socket.IO handlers,
which run in the same cookie context) can read them directly.

### 1.2 Helper contract — `services/web-ui/auth.py` (new)

```python
def current_parent_id() -> str | None:
    """par_… of the logged-in parent (owning parent for a student session). None if anon."""

def current_student_id() -> str | None:
    """stu_… of the active student session. None if a bare parent session or anon.
    During R0 (no auth yet, spec 01 §1 backfill) returns 'stu_legacy0' so the app keeps running."""

def require_student_id() -> str:
    """current_student_id() or abort(401). Used by every per-student API/proxy."""

def owns_student(student_id: str) -> bool:
    """True iff students.parent_id == current_parent_id(). Cross-tenant guard (§8.1)."""
```

`current_student_id()` resolution order: `session['student_id']` → (R0 fallback) `'stu_legacy0'`.
Once B15.4 lands, the R0 fallback is removed and a missing `student_id` on a per-student route is a
401, not a silent legacy default. This single switch is the R0→R1 cutover for identity.

**Every** call into core or RAG from web-ui must inject `current_student_id()` (§5); routes that
don't (catalog browse, health) are explicitly the global ones.

---

## 2. Auth flows

Recommendation: **Flask-Login** (`flask-login>=0.6`). It rides the existing signed Flask session
cookie, which is the *same* cookie Socket.IO sees (`async_mode='gevent'`, `app.py:91`), so a single
login authenticates both HTTP routes and the websocket — no separate token handshake. Password
hashing: **argon2id** via `argon2-cffi` (`PasswordHasher()`), matching `parents.password_hash` /
`students.pin_hash` in spec 01 §2.

### 2.1 Flask-Login wiring

```python
# auth.py
login_manager = LoginManager(); login_manager.init_app(app)
login_manager.login_view = 'login'

class Principal(UserMixin):
    def __init__(self, role, parent_id, student_id=None):
        self.role, self.parent_id, self.student_id = role, parent_id, student_id
    def get_id(self):                      # serialized into the session cookie
        return f"{self.role}:{self.parent_id}:{self.student_id or ''}"

@login_manager.user_loader
def load_user(token):
    role, parent_id, student_id = token.split(':', 2)
    # re-validate against DB each request: parent active? student still owned & active?
    if not _principal_still_valid(role, parent_id, student_id or None):
        return None
    return Principal(role, parent_id, student_id or None)
```

`login_user(principal)` writes the cookie; `_principal_still_valid` re-checks `parents.status` and
`students.status`/`parent_id` on **every** request (cheap indexed lookups) so a suspended account or
re-homed student is logged out immediately — defends session fixation/stale-session (§8.3).

### 2.2 Decorators

```python
def parent_required(f):           # role == 'parent'; else 401/redirect to login
def student_session_required(f):  # current_student_id() present; else 401
def owns_student_required(arg='student_id'):
    """For parent routes acting on a child: assert owns_student(kwargs[arg]); else 403."""
```

Behavior: HTML routes redirect to `/login` on failure; `/api/*` and proxies return JSON
`401`/`403`. All three set `Vary: Cookie` and never leak whether a foreign id exists (return 403 not
404 only after confirming ownership; for unknown ids return 404 — see §8.1).

### 2.3 Parent flows

| Flow | Route(s) | Method | Guard | Notes |
|---|---|---|---|---|
| Signup | `/signup` | GET/POST | none | argon2id hash → `parents` row `status='pending_verify'`; emit verify email; write `consent_records` (TOS/COPPA, spec 01 §2). Rate-limited. |
| Verify email | `/verify/<token>` | GET | none | single-use signed token (itsdangerous, TTL 24h) → set `email_verified_at`, `status='active'`. |
| Login | `/login` | GET/POST | none | fetch `parents` by email; `ph.verify(hash, pw)`; on success `login_user(Principal('parent', id))`. Generic error on failure (no user-enumeration). Lockout §8.2. |
| Logout | `/logout` | POST | login_required | `logout_user()`; clear `student_id`. |
| Password reset request | `/password/reset` | POST | none | always 200 (no enumeration); if email exists, signed token emailed. |
| Password reset confirm | `/password/reset/<token>` | GET/POST | none | verify token, set new argon2id hash, invalidate all sessions (bump a `pw_version` claim or rotate secret-derived salt). |

### 2.4 Student login — two paths, both scoped to one parent

**Path A — parent-launch profile pick** (the common, young-kid case):
| Route | Method | Guard | Notes |
|---|---|---|---|
| `/students` (picker) | GET | parent_required | lists `students WHERE parent_id=current_parent_id()` |
| `/students/<student_id>/launch` | POST | parent_required + owns_student_required | flips session: keep `parent_id`, set `student_id`, `role='student'`; `login_user(Principal('student', parent_id, student_id))`. No PIN needed (parent already authed). |
| `/students/exit` | POST | student_session_required | drop `student_id`, restore `role='parent'` (returns to dashboard). |

**Path B — avatar + 4-digit PIN** (kid logs in on a shared device without the parent each time):
| Route | Method | Guard | Notes |
|---|---|---|---|
| `/family/<parent_id>` (or short family code) | GET | none | renders avatar grid for that parent's students only. The family scope is the URL/code; PIN is checked *within* it. |
| `/students/<student_id>/pin` | POST | none | `student.parent_id` must match the family scope; `ph.verify(student.pin_hash, pin)`; on success `login_user(Principal('student', student.parent_id, student_id))`. Lockout §8.2. |

PIN is a 4-digit secret → **always argon2id-hashed** (`pin_hash`, spec 01 §2), never compared
plaintext, and **only** valid against students under the addressed parent — a PIN can never select a
sibling-family's student (§8.1). PIN-null students (`pin_hash IS NULL`) are parent-launch-only and
are not shown on the PIN grid.

### 2.5 Full route table (auth-relevant) with guards

| Route | Methods | Guard | Role result |
|---|---|---|---|
| `/signup`, `/verify/<t>`, `/login`, `/password/reset[/<t>]` | GET/POST | none | establishes `parent` |
| `/logout`, `/students/exit` | POST | login_required / student_session_required | — |
| `/students`, `/students/<id>/launch`, `/students/<id>/pin-set` | GET/POST | parent_required (+owns) | — |
| `/family/<parent_id>`, `/students/<id>/pin` | GET/POST | none (family-scoped) | establishes `student` |
| `/learn`, `/quiz`, `/review`, `/schedule` (student app) | GET | student_session_required | — |
| `/api/event`, `/api/set_active_course`, `/api/course_structure`, all per-student `/api/*` proxies | POST/GET | student_session_required | — |
| `/account`, `/api/parent/*`, dashboards (B19) | GET/POST | parent_required | — |
| `/status`, `/health`, catalog browse | GET | none / login_required (status) | global |

---

## 3. Socket.IO room scoping (B15.5 — fixes B6.3 broadcast)

Every connected browser joins exactly one student room. **All** student-directed emits become
`room=f"student:{id}"`. The two existing leak points (`app.py:172`, `:582`) and the connect-time
push (`:245`) are fixed; the status-only room (`status_room`, `:233`/`:267`) stays as-is (ops view).

### 3.1 On connect — join the room from the cookie

```python
@socketio.on('connect')
def handle_connect():
    sid_student = current_student_id()         # reads the same session cookie (§1.1)
    if not sid_student:
        return False                           # reject socket for unauthenticated/parent-only
    join_room(f"student:{sid_student}")
    # push initial state for THIS student only (replaces app.py:245 global emit)
    emit('state_update', _fetch_state(sid_student), room=request.sid)
```

A student with two tabs ⇒ two sids, same room ⇒ both receive that student's updates and only those.
`request.sid` is still used for the *initial* per-connection snapshot.

### 3.2 Emit-site rewrites (exact)

| Line | Today | Becomes |
|---|---|---|
| `app.py:172` (`state_poller`) | `socketio.emit('state_update', full_state)` | **retired** for MT (§3.4); if kept transitionally, must loop rooms — do not broadcast |
| `app.py:245` (connect) | `socketio.emit('state_update', fsm_state)` | `emit('state_update', state, room=request.sid)` (per-student fetch, §3.1) |
| `app.py:573` (`stream_token`) | `socketio.emit('stream_token', {...})` | `socketio.emit('stream_token', {...}, room=f"student:{data['student_id']}")` |
| `app.py:580/582` (`status_update`) | `room=_creation_initiator_sid` else broadcast | `socketio.emit('status_update', data, room=f"student:{data['student_id']}")` |

The fragile `_creation_initiator_sid` global (`app.py:122`) is **deleted** — rooms replace it.
Course-creation progress now naturally targets the initiating student's room.

### 3.3 Carrying `student_id` back from core → web-ui

`fsm_logic.py:send_status_update` (`:588`) and `_call_llm_stream`'s per-token POST currently send
payloads with no owner. Fix at the source: the FSM instance knows `self.student_id` (§4.3), so it
stamps every outbound status/token:

```python
# fsm_logic.py send_status_update / send_pipeline_stage / _call_llm_stream
data['student_id'] = self.student_id     # <-- always present on a per-student FSM
requests.post(f"{self.web_ui_url}/api/update_thinking_status", json=data, timeout=15)
```

`update_thinking_status` (`app.py:567`) then reads `data['student_id']` and emits to that room
(§3.2). No reliance on which browser sid POSTed; the FSM is the authority on ownership. If
`student_id` is missing (legacy/bug), web-ui drops the message (do **not** broadcast — fail closed).

### 3.4 Retiring the global `state_poller`

The 2s poller (`app.py:137`) is a single-tenant artifact: one `/state`, one broadcast. For MT it is
**replaced by push-on-completion**: after `registry.get(student_id).transition(event)` returns
(via the `/api/event` proxy round-trip), web-ui fetches that student's state and emits it
`room=f"student:{id}"`. This is event-driven, scales with active students, and removes the
broadcast leak. The course-structure enrichment the poller did (`app.py:154-167`) moves into this
per-student push path (same RAG call, scoped `uid`). Timer-driven nudges (`check_timers`, §4.4) also
push to the owning room. (A low-frequency liveness ping may remain, but carries no per-student
state.)

---

## 4. The FSM registry — `services/core/fsm_registry.py` (new, B15.6)

Kills the global `fsm = MnemosyneFSM()` (`fsm_logic.py:3498`). One `MnemosyneFSM` **per active
student**, held in an in-process LRU with idle-TTL eviction and flush-on-evict. `StorageManager`
stays a **single shared instance** (thread-safe `_ThreadLocalDB`, WAL) injected into every FSM; the
FSM passes `self.student_id` into every storage call.

### 4.1 API

```python
class FSMRegistry:
    def __init__(self, storage: StorageManager, *, max_size=64, idle_ttl=1800, sweep_interval=60):
        self._storage = storage
        self._fsms: "OrderedDict[str, MnemosyneFSM]" = OrderedDict()
        self._locks: dict[str, threading.RLock] = {}     # per-student serialization (§4.7)
        self._reg_lock = threading.RLock()               # guards _fsms/_locks structure only
        self._max, self._ttl = max_size, idle_ttl
        self._start_sweeper(sweep_interval)              # ONE thread for all students (§4.4)

    def get(self, student_id: str) -> MnemosyneFSM: ...  # create-or-revive + LRU touch + hydrate
    def lock_for(self, student_id: str) -> threading.RLock: ...
    def evict_idle(self) -> int: ...                     # flush+drop FSMs idle > ttl
    def evict(self, student_id: str) -> None: ...        # explicit flush+drop (logout, capacity)
    def flush_all(self) -> None: ...                     # shutdown hook → persist every live FSM
    def stats(self) -> dict: ...                         # live count, lru order, last_touch (ops)
```

### 4.2 `get()` lifecycle

```python
def get(self, student_id):
    with self._reg_lock:
        fsm = self._fsms.get(student_id)
        if fsm is None:
            self._evict_if_over_cap()                    # LRU: flush+drop oldest if at max_size
            fsm = MnemosyneFSM(student_id)               # __init__ hydrates from fsm_sessions (§4.5)
            fsm.storage = self._storage                  # shared StorageManager (NOT per-student)
            self._fsms[student_id] = fsm
            self._locks.setdefault(student_id, threading.RLock())
        self._fsms.move_to_end(student_id)               # LRU touch
        fsm.last_touch = time.time()
        return fsm
```

`max_size` is the **registry memory cap** (§6.5). At cap, the LRU-oldest *idle* FSM is flushed to
its `fsm_sessions` row and dropped before admitting a new one; a student whose FSM was evicted simply
re-hydrates on next `get()` (state lossless because §4.5 persists on every save point).

### 4.3 `MnemosyneFSM.__init__(self, student_id)` changes

```python
def __init__(self, student_id: str):
    self.student_id = student_id                 # NEW — the isolation key, used everywhere
    self.last_touch = time.time()
    self.state = "LOBBY"
    ...                                          # existing attrs fsm_logic.py:233-326 unchanged
    # REMOVED: self.timer_thread = threading.Thread(self.check_timers); .start()  (:345-346)
    #          → replaced by registry-level sweeper (§4.4)
    # REMOVED: self.state_file = …/user_state.json (:237) and signal handlers (:349-350)
    #          → persistence is per-row (§4.5); signals are process-level, set once in main, not per-FSM
    self.storage = None                          # injected by registry.get() (shared)
    self._hydrate_from_row()                     # replaces _load_state_from_disk (:353)
```

`self.storage` is injected post-construct (§4.2) so the single `StorageManager` is shared. Grade
band (spec 02 §1) is resolved here from `students.grade_band` and stored on the FSM + in the blob.

### 4.4 One timer sweeper, not a thread per student

Today each FSM spawns `check_timers` (`:345`, infinite `while True: sleep(1)`). With N students that
is N threads. The registry runs **one** sweeper:

```python
def _sweep(self):
    while True:
        time.sleep(self._sweep_interval)
        now = time.time()
        for sid, fsm in list(self._fsms.items()):
            fsm.tick(now)                        # was the body of check_timers (fsm_logic.py:656)
        self.evict_idle()                        # flush+drop idle FSMs
```

`MnemosyneFSM.check_timers` (the `while True` loop) is refactored into a pure `tick(now)` that does
one pass and returns (no loop, no sleep). A nudge fired in `tick` pushes to the student's room
(§3.3). The 1s cadence of the old per-FSM loop is not pedagogically needed; the sweep interval
(default 60s, configurable) suffices for idle-nudge timers — if sub-minute nudges are required, run
the sweeper at the old 1s but iterate the (small) live set, still one thread.

### 4.5 Per-student persistence → `fsm_sessions` row (B15.7)

Replaces `data/user_state.json` (`fsm_logic.py:237,1415,1462,1504`). The blob shape is spec 01 §3
(capped transcript/history per PERF-5). Re-point the existing serializers:

```python
def _save_session(self):                         # was _save_current_course_progress (:1415)
    blob = self._serialize_blob()                # same keys as :1427-1449 + state, grade_band, schema:1
    self.storage.fsm.upsert(self.student_id, json.dumps(blob))   # new FsmSessionStore (spec 01 §8)

def _hydrate_from_row(self):                     # was _load_state_from_disk / _load_course_progress
    row = self.storage.fsm.get(self.student_id)
    if row: self._apply_blob(json.loads(row['blob']))
```

New `FsmSessionStore` (`storage.py`, spec 01 §8): `upsert(student_id, blob)` →
`INSERT OR REPLACE INTO fsm_sessions(student_id, blob, updated_at) VALUES(?,?,datetime('now'))`;
`get(student_id)`. **Atomic by construction** (single-row upsert in WAL) — the LRN-8 atomic-write
concern for the JSON file disappears. Save points unchanged from today (on concept advance, on
state-save calls); add a save on `evict`/`flush_all` so eviction never loses turns.

### 4.6 Entry-point rewrites (`fsm_logic.py:3498-3513` and the ~30 `fsm.*` routes)

```python
registry = FSMRegistry(StorageManager(DATA_ROOT))    # replaces `fsm = MnemosyneFSM()` (:3498)

def _student_id_from_request():
    # web-ui injects it; trust only because web-ui is the sole caller behind the network boundary
    return (request.json or {}).get('student_id') or request.args.get('student_id')

@app.route("/event", methods=["POST"])
def handle_event():
    sid = _student_id_from_request() or abort(400)
    fsm = registry.get(sid)
    with registry.lock_for(sid):                      # serialize one student's rapid events (§4.7)
        fsm.transition(request.json)
    return {"status": "ok"}

@app.route("/state", methods=["GET"])
def get_state():
    sid = _student_id_from_request() or abort(400)
    return registry.get(sid).get_state()
```

The schedule/stats/etc. routes at `fsm_logic.py:3516+` that call `fsm.storage.*` must each resolve
`registry.get(sid)` (or call the shared `StorageManager` directly with `sid`). Mechanical but
required — every `fsm.` in module-route scope becomes `registry.get(sid).` and every storage call
gains `student_id`. **Trust boundary:** core `/event` accepts `student_id` from the request because
its only caller is web-ui (network-internal, container-to-container); web-ui derives it from the
*authenticated session*, never from client-supplied body. Core must not be exposed publicly (§8.1).

### 4.7 Per-instance lock (serialize one student's rapid events)

Two quick events from the same student (double-tap, two tabs) must not interleave on one FSM's
mutable attrs. `registry.lock_for(sid)` returns a per-student `RLock`; `/event` holds it across
`transition()`. Different students take different locks → full concurrency across students, strict
serialization within a student. The lock is per-student, **not** global, so a slow LLM turn for
student A never blocks student B (subject to the GPU queue, §6.6).

---

## 5. Request lifecycle, end-to-end

```
Browser (student session cookie)
  │  POST /api/event {type:'TEXT_INPUT', payload:{text}}   (no student_id in body)
  ▼
web-ui  /api/event  [student_session_required, csrf_protect]
  │  sid = current_student_id()                  # from authenticated session, §1.2
  │  body = {**request.json, 'student_id': sid}  # inject — client never supplies it
  │  POST core /event (timeout 60)
  ▼
core  /event
  │  fsm = registry.get(sid)                      # create/revive + hydrate fsm_sessions, §4.2
  │  with registry.lock_for(sid):  fsm.transition(body)
  │      … LLM stream: each token → POST web-ui /api/update_thinking_status
  │                    {type:'stream_token', token, student_id: sid}     # §3.3
  │      … fsm._save_session()  → fsm_sessions row upsert                # §4.5
  ▼
web-ui  /api/update_thinking_status
  │  socketio.emit('stream_token'|'status_update', data,
  │                room=f"student:{data['student_id']}")                 # §3.2
  ▼
Browser(s) in room student:<sid>  (this student's tabs only)  render tokens
  │
  └─ after core /event returns → web-ui fetches that student's /state and
     emits 'state_update' room=student:<sid>  (replaces the global poller, §3.4)
```

Parent-launch differs only at the top: the session already holds `parent_id`; `/students/<id>/launch`
adds `student_id`; from there the lifecycle is identical.

---

## 6. Concurrency & failure modes

| # | Scenario | Behavior | Mechanism |
|---|---|---|---|
| 6.1 | **Two tabs, same student** | Both in `student:<id>`; both see all updates. Rapid events serialized. | one FSM per student (§4.2) + per-student lock (§4.7) + shared room (§3.1) |
| 6.2 | **Two different students concurrent** | Fully parallel FSMs, isolated state, isolated rooms, separate locks; only the GPU queue (spec 10 / B23.1) and SQLite WAL writes serialize. | registry keyed by `student_id`; `_ThreadLocalDB` WAL |
| 6.3 | **Restart recovery** | Each student re-hydrates from its `fsm_sessions` row on first `get()` post-restart; position/transcript/bloom restored (spec 01 §3). No global file to corrupt. | `_hydrate_from_row` (§4.5); `flush_all()` on graceful shutdown |
| 6.4 | **Eviction mid-session** | Idle FSM (> `idle_ttl`) is flushed then dropped; next event re-hydrates transparently — student notices nothing but a one-turn cold-start. | `evict_idle` flush-on-evict (§4.4); lossless because every turn persists |
| 6.5 | **Registry memory cap** | At `max_size`, LRU-oldest *idle* FSM flushed+dropped before admitting new. If all live FSMs are non-idle and cap is hit, admit anyway but log pressure (correctness > cap); alert (B27). Tune `max_size` to RAM (24GB Mac Mini). | `_evict_if_over_cap` (§4.2) |
| 6.6 | **GPU queue full (spec 10 / B23.1)** | FSM turn blocks on the per-student fair-queue semaphore in `llm_client`; the per-student lock is **held** during the wait, so that student's other events queue behind it, but other students proceed. Surfaces as a "thinking…" status to the waiting student's room. Cap queue depth; shed with a "busy, try again" status rather than unbounded blocking. | semaphore in `get_llm_client().chat()`; lock scope §4.7 |
| 6.7 | **Core restarted mid-stream** | In-flight stream tokens lost (in-memory); on reconnect, browser re-joins room and gets a fresh `state_update`; FSM re-hydrates last persisted turn. | §3.1 reconnect + §4.5 |
| 6.8 | **Parent suspended / student re-homed mid-session** | Next request fails `_principal_still_valid` (§2.1) → logged out; socket rejected on next connect (§3.1). | per-request re-validation |

---

## 7. The eventual stateless "Option A" (deferred to B23.5)

**Option B (this spec):** stateful in-process registry; FSM lives in core's memory between turns.
Correct and simple for **single-worker** gevent (current topology). It does **not** survive
multi-worker/multi-process because a student's FSM lives in one worker's heap.

**Option A (deferred):** *hydrate-per-turn* — no resident FSM. Each `/event` constructs a
`MnemosyneFSM(student_id)`, loads the `fsm_sessions` blob, runs one `transition`, persists, and
discards. Stateless ⇒ any worker can serve any turn ⇒ horizontally scalable behind Redis-backed
Socket.IO message queue + Redis sessions (B23.5).

**Why deferred:** per-turn hydrate adds a DB read+write and loses warm in-memory caches each turn;
unnecessary until we run >1 worker. **The seam is the registry:** `registry.get(student_id)` is the
*only* place that decides "resident vs fresh." Option A is implemented by swapping the registry impl
for one whose `get()` always builds+hydrates and whose `transition` wrapper always persists+discards
— **no call-site changes** in `handle_event`/`get_state`/routes. Persistence (§4.5) and the
per-student lock (which becomes a Redis lock under Option A) are already row/key scoped, so the
B→A migration is localized to `fsm_registry.py`. Keep `transition()` free of "resident-only"
assumptions (no relying on attrs surviving between turns beyond what the blob carries) to keep the
seam clean.

---

## 8. Security

### 8.1 Cross-tenant isolation guarantees
- **Storage:** every per-student query filters `WHERE student_id = ?` (spec 01 §8); the FSM only
  ever passes `self.student_id`. A student session can construct no query for another `student_id`.
- **FSM:** registry keyed by `student_id`; no shared mutable FSM. A's `transition` touches only A's
  instance.
- **Web boundary:** web-ui injects `student_id` from the **authenticated session**, never from the
  request body (§5). Core trusts the body only because it is unreachable except from web-ui — core
  must bind to the internal network only (docker network), **never** published to host/public.
- **Ownership:** parent acting on a child passes `owns_student_required` (§2.2) — `403` if the
  child's `parent_id` ≠ `current_parent_id()`. Unknown id ⇒ `404` (don't confirm existence);
  known-but-foreign ⇒ `403`.
- **Rooms:** student-directed emits are `room=f"student:{id}"`; unauthenticated sockets are rejected
  at connect (§3.1); missing `student_id` on a status push fails closed (§3.3).

### 8.2 PIN brute-force lockout
4-digit PIN = 10⁴ space → must rate-limit. Per-`student_id` counter (in `helga.db`, e.g. a small
`pin_attempts(student_id, fails, locked_until)` table or reuse `audit_log`): after **5** consecutive
failures, lock that student's PIN for an exponential backoff (1m→5m→15m, cap 1h); successful verify
resets. Lock is **per student**, also rate-limit per source IP to blunt distributed guessing. Parent
can always launch (Path A) regardless of PIN lock, and can reset the PIN. Log every failure to
`audit_log` (spec 01 §7).

### 8.3 Session fixation
`login_user` issues a fresh session post-auth; on privilege change (parent→student launch,
student→parent exit) **regenerate** the session id and re-issue the cookie so a pre-auth fixation
token can't ride into an authed session. Re-validate principal every request (§2.1).

### 8.4 CSRF
Existing `csrf_protect` (`app.py:102`, token at `:94/96`) already guards `/api/event`. Extend it to
**all** state-changing auth routes: `/login` (POST), `/signup`, `/students/<id>/launch`,
`/students/<id>/pin`, `/logout`, password reset confirm. Socket.IO connections are same-origin
(`cors_allowed_origins` allow-list, `app.py:90/91`) and authenticated by the session cookie; keep
the CORS allow-list tight (no `*`). Set the session cookie `HttpOnly`, `SameSite=Lax`, `Secure`
(behind Caddy TLS, B23.6).

---

## 9. Test plan / acceptance criteria (B15.8)

`tests/` — these must pass before R1 ships.

| Test | Asserts | How |
|---|---|---|
| **Storage isolation** | A cannot read/write B's `user_progress`/`flashcards`/`scheduled_reviews`. | seed A & B; `progress.get_progress(B_concept, student_id=A)` → None; `update_progress(..., student_id=A)` never mutates B's row; verify composite-PK `(student_id, concept_uid)` (spec 01 §2.2). |
| **FSM isolation** | `registry.get('A')` and `registry.get('B')` are distinct objects; advancing A's concept never changes B's `current_lesson_node`/bloom/transcript. | drive two FSMs in one process; assert no attr bleed. |
| **Two-session socket leakage** | A status/stream emit for A reaches A's room and **not** B's. | Socket.IO test client: connect two clients in rooms `student:A`/`student:B`; POST a status with `student_id:A`; assert B's client received 0 messages, A's ≥1. Directly targets B6.3. |
| **Restart-restore** | After `flush_all()` + fresh registry, each student re-hydrates exact position/transcript/bloom. | snapshot blob, recreate `FSMRegistry`, `get(sid)`, assert state equals snapshot. |
| **Eviction lossless** | Force-evict mid-session, re-`get`, state intact (6.4). | `idle_ttl=0`; `evict_idle()`; assert re-hydrate equals pre-evict. |
| **Migration backfill** | Pre-MT rows land under `stu_legacy0`; `user_progress` PK rebuilt; no row loss (spec 01 §1). | run v3→v4 on a fixture DB with single-user rows; assert every per-user row has `student_id='stu_legacy0'` and counts unchanged. |
| **Auth gating** | `parent_required`/`student_session_required` 401/redirect; `owns_student_required` 403 cross-tenant. | unauth + cross-tenant requests to each guarded route. |
| **PIN lockout** | 5 wrong PINs lock; correct PIN then 423/blocked; right PIN under another family never selects sibling student. | hammer `/students/<id>/pin`; assert lock + cross-family rejection (§8.1). |
| **student_id injection trust** | Client-supplied `student_id` in `/api/event` body is ignored; session value wins. | POST with forged `student_id:B` while authed as A; assert event ran on A's FSM. |

---

## 10. Open questions

1. **Family scope for PIN (Path B):** address by `parent_id` in URL vs a short opaque `family_code`
   (friendlier, non-enumerable). Lean `family_code`; decide during FE7.
2. **Sweep cadence:** 60s registry sweep vs keep 1s for idle nudges — does any band (spec 02, K-2)
   actually need sub-minute timer nudges? If not, 60s and delete the 1s loop entirely.
3. **`max_size` sizing on 24GB Mac Mini:** how many resident FSMs before RAM pressure with Qwen
   loaded (~9-10GB)? Measure FSM heap footprint; set cap empirically; alert before Option A is forced.
4. **Parent driving multiple children "at once":** a parent with two kids on two devices — each
   device is its own student session/cookie, so already supported; but a single parent tab
   "supervising" live needs a parent-room subscription (read-only mirror of a child's room) — defer
   to B19 dashboard, note the room model already allows `join_room(f"student:{child}")` for an
   owning parent.
5. **Where the GPU fair-queue holds the lock (6.6):** confirm with spec 10 whether the per-student
   FSM lock should be *released* while blocked on the GPU semaphore (allowing that student's reads
   to proceed) or held (strict per-student ordering). Current design holds it; revisit if it starves
   interactive reads.
6. **Logout vs eviction:** on `/logout`/`exit`, call `registry.evict(student_id)` to flush+free
   immediately, or let idle-TTL handle it? Eager evict is cleaner for capacity but costs a write on
   every logout — probably worth it.
