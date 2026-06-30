# 10. Deployment & Scaling — Implementation Spec (B23, B27)

> **Status:** design, implementation-ready. Maps to build-tree branches **B23.1–B23.7** and
> **B27.1–B27.5** (`docs/HELGA_BUILD_TREE.md`, `docs/BUILD_MANIFEST.md`). Closes baseline debt
> **B9.4** (host ports exposed) and **B9.5** (Ollama SPOF / no circuit breaker).
>
> **Grounding (verified against code):**
> - Live tutoring LLM path: `services/core/llm_client.py` — `LLMClient.chat()` (~L47), module
>   singleton `get_llm_client()` (~L318), `timeout=60, retries=3`. Called from
>   `services/core/fsm_logic.py` `_call_llm()` (~L417) and `_call_llm_stream()` (~L427).
> - Background build/hydration LLM path: `services/common/llm_utils.py` `llm_generate()` (~L117)
>   posts **directly** to `LLM_API_URL` (~L24), bypassing `LLMClient`. Used by
>   `services/core/course_builder.py`. **Both paths hit the same Ollama** at
>   `host.docker.internal:11434` → both must pass through the same admission gate.
> - Web UI: `services/web-ui/app.py` Flask + `SocketIO(async_mode='gevent')` (~L91); rooms used
>   for scoped status updates (`_creation_initiator_sid`, ~L122).
> - Storage: `services/common/storage.py` `_ThreadLocalDB` (~L24, WAL), `schema_version` table
>   (~L144), `SearchStore` FTS5 (~L492). Sub-store interface per `01_DATA_MODEL.md` §8.
> - Postgres portability rules: `01_DATA_MODEL.md` §9.
> - Infra: `docker-compose.yml` (6 services, host ports 5050/5003/5002/5005/5006/8080),
>   `Makefile` `backup` target, `deploy.sh`, `main.py`.

---

## 0. Problem statement & scope

Today Helga is single-GPU, single-tenant-ish, single-worker. The roadmap (R1 → R4) puts **N
concurrent students** on **one GPU**. The GPU is the hard bottleneck: Ollama serves a fixed number
of parallel slots (`OLLAMA_NUM_PARALLEL`), and an unbounded fan-in of live tutoring turns plus
background course-building turns will (a) blow the 60s request timeout, (b) let a background catalog
hydration starve a live student, and (c) produce unbounded tail latency.

This spec designs, at implementation depth:

1. **GPU admission control** (B23.1/B23.2/B23.3) — bounded semaphore + per-student fair queue +
   two priority classes, in-process and gevent-friendly, wrapping both LLM paths.
2. **Capacity / unit economics** (B27.4) — students-per-GPU model with worked numbers (feeds
   pricing in spec 09).
3. **SQLite → Postgres** (B23.4) — psycopg pool behind the existing sub-store interface.
4. **Multi-worker** (B23.5) — Redis sessions + Socket.IO message queue + stateless hydrate-per-turn
   FSM + cross-process GPU semaphore.
5. **Production topology** (B23.6) — Caddy → gunicorn-gevent → web-ui; Ollama on metal; compose
   evolution.
6. **Backups & DR** (B23.7) — nightly dumps, retention, restore drill, secrets.
7. **Observability** (B27.1–B27.3) — structured JSON logs, Prometheus metrics, xAPI event log.
8. **Scale-out triggers** — concrete thresholds.
9. **Env/config matrix.**
10. **Test/load plan + acceptance criteria.**

**Sequencing.** §1–§2 + §6–§7 land at **R1** (single box, single worker, many students). §3–§5
are **R4** (deferred; trigger-gated). Nothing in §1 requires Redis or Postgres — it is purely
in-process and ships first.

---

## 1. GPU concurrency control (B23.1 / B23.2 / B23.3)

### 1.1 The bottleneck, precisely

Ollama runs **one** model (`MAX_LOADED_MODELS=1`) and serves up to `OLLAMA_NUM_PARALLEL` requests
concurrently by splitting the KV cache into that many slots. Beyond that, Ollama queues internally
with no fairness and no backpressure signal — requests just block until they hit our 60s client
timeout. We must **not** let more than `OLLAMA_NUM_PARALLEL` requests be in flight to Ollama at once,
and we must decide *which* request gets the next freed slot.

The fix is an **admission gate** in the core service that every LLM call acquires before touching
Ollama. It enforces:

- **Concurrency cap** = `OLLAMA_NUM_PARALLEL` (default sized below).
- **Two priority classes:** `INTERACTIVE` (live tutoring) and `BACKGROUND` (course building,
  catalog hydration). Background is **capped to at most 1 in-flight slot** and never preempts/starves
  interactive.
- **Per-student fairness:** among waiting interactive turns, dispatch round-robin across distinct
  `student_id`s so one chatty student can't monopolize the GPU.
- **Backpressure:** when an interactive turn would wait beyond a threshold, emit a `busy` status to
  that student's Socket.IO room instead of silently sitting on the 60s timeout.

### 1.2 Where it lives

New module: **`services/core/gpu_gate.py`**. Single process (core-logic) owns all LLM traffic in
the R1 single-worker topology, so an in-process gate is authoritative. (At R4 multi-worker the
counter moves to Redis — §4.4 — behind the *same* `GpuGate` API, so call sites never change.)

Both LLM entry points acquire the gate:

- `services/core/llm_client.py` → `LLMClient.chat()` / `chat_stream()` / `chat_json()` /
  `chat_with_tools()` wrap their `requests.post` in `with gpu_gate.admit(...)`.
- `services/common/llm_utils.py` → `llm_generate()` / `llm_generate_json()` wrap their
  `requests.post(LLM_API_URL, ...)` the same way.

To keep one gate instance across both modules, `gpu_gate.py` exposes a module-level singleton
`get_gpu_gate()` (mirrors `get_llm_client()`), and `llm_utils` imports it lazily (it lives in
`common/`, so guard the import; if core isn't the caller, fall back to a no-op gate so RAG-side
callers aren't blocked by an unavailable core gate).

### 1.3 Call-site tagging

Every call must tag its **class** and **student_id**. We thread a small `LLMContext` through:

```python
# services/core/gpu_gate.py
from dataclasses import dataclass

INTERACTIVE = "interactive"
BACKGROUND  = "background"

@dataclass
class LLMContext:
    klass: str = INTERACTIVE        # INTERACTIVE | BACKGROUND
    student_id: str = "_anon"       # for fairness + per-student metrics
    room: str | None = None         # Socket.IO room for backpressure 'busy' emits
    request_id: str | None = None   # correlation id (B27.1)
```

Tagging rules (no guessing — explicit at every call site):

| Call site | File | Class | student_id source |
|---|---|---|---|
| Socratic turn / grading / hints | `fsm_logic.py` `_call_llm`, `_call_llm_stream` | `INTERACTIVE` | active session's `student_id` |
| Skeleton / module / unit / lesson / concept generation | `course_builder.py` (via `llm_utils`) | `BACKGROUND` | initiating parent's `student_id` (or `_system`) |
| Catalog hydration (offline authoring, B26.1) | authoring job runner | `BACKGROUND` | `_system` |
| Vision `describe_image`, tool-use rounds | `llm_client.py` | inherit caller's class | caller's student_id |

Implementation: add an optional `ctx: LLMContext = None` parameter to `LLMClient.chat()` and
`llm_generate()`, defaulting to `LLMContext(INTERACTIVE, "_anon")`. The FSM passes
`ctx=LLMContext(INTERACTIVE, self.student_id, room=self.session_room, request_id=req_id)`.
`course_builder` passes `ctx=LLMContext(BACKGROUND, parent_student_id)`. Default-interactive is the
safe bias: a missed tag degrades to "treated as live," never "silently starved."

### 1.4 Data structures

In-process, gevent-friendly (cooperative, no OS threads needed — gevent monkey-patches the world,
so a `gevent.lock.Semaphore` and a plain greenlet-safe deque suffice):

```python
import gevent
from gevent.lock import BoundedSemaphore
from gevent.event import Event
from collections import deque, OrderedDict
import itertools, time

class GpuGate:
    def __init__(self, num_parallel, bg_slots=1, busy_after_s=8.0, max_queue=256):
        self.cap         = num_parallel               # total in-flight cap
        self.bg_slots    = bg_slots                   # max background in-flight (>=1, < cap)
        self.busy_after  = busy_after_s               # emit 'busy' if waited longer
        self.max_queue   = max_queue                  # hard admission limit (reject beyond)

        self._inflight       = 0
        self._bg_inflight    = 0
        # interactive waiters: per-student FIFO + round-robin cursor over students
        self._iq = OrderedDict()    # student_id -> deque[_Waiter]   (insertion-ordered)
        self._rr = None             # round-robin iterator over student keys
        self._bq = deque()          # background waiters (plain FIFO)
        self._sched_lock = BoundedSemaphore(1)   # guards counters + queues
        self._waiters = 0

class _Waiter:
    __slots__ = ("event", "enqueued_at", "klass", "student_id", "ctx")
    def __init__(self, ctx):
        self.event = Event(); self.enqueued_at = time.monotonic()
        self.klass = ctx.klass; self.student_id = ctx.student_id; self.ctx = ctx
```

### 1.5 Algorithm

**Admit (acquire a slot):**

```
admit(ctx):
    with _sched_lock:
        if total_waiters >= max_queue:           # overload guard
            raise GpuOverloaded                  # caller emits 'busy', returns graceful msg
        if can_dispatch_now(ctx):                # see rule below
            grant(ctx); return Slot
        w = _Waiter(ctx)
        enqueue(w)                               # per-student deque (interactive) or _bq (bg)
        _waiters += 1
    # waited path — block this greenlet until scheduler grants it
    woke = w.event.wait(timeout=admit_timeout)   # admit_timeout = client timeout - small margin
    # backpressure: a watchdog greenlet (below) fires 'busy' at busy_after, not here
    if not woke: raise GpuOverloaded
    return Slot

can_dispatch_now(ctx):
    if _inflight >= cap: return False
    if ctx.klass == BACKGROUND and _bg_inflight >= bg_slots: return False
    # priority: never let a queued BACKGROUND take a slot while INTERACTIVE waits
    if ctx.klass == BACKGROUND and any_interactive_waiting(): return False
    return True
```

**Release (return a slot) + scheduler — the core of fairness & anti-starvation:**

```
release(slot):
    with _sched_lock:
        _inflight -= 1
        if slot.klass == BACKGROUND: _bg_inflight -= 1
        _dispatch_next()

_dispatch_next():           # called under _sched_lock
    while _inflight < cap:
        w = _pick_interactive_rr()          # 1) ALWAYS drain interactive first
        if w is None:
            # 2) only if NO interactive waiting AND bg under its slot cap
            if _bg_inflight < bg_slots and _bq:
                w = _bq.popleft()
            else:
                break
        grant_locked(w)                     # ++_inflight (++_bg_inflight if bg); w.event.set()

_pick_interactive_rr():     # round-robin across students, FIFO within a student
    if not _iq: return None
    # advance cursor to next non-empty student deque; pop its head
    for _ in range(len(_iq)):
        sid = next_round_robin_key()        # itertools.cycle-like over list(_iq.keys())
        dq = _iq.get(sid)
        if dq:
            w = dq.popleft()
            if not dq: del _iq[sid]         # drop empty student bucket
            return w
    return None
```

Key invariants:
- **Background ≤ `bg_slots` (=1)** in flight, **and** background is only granted when **no**
  interactive turn is waiting. So a course build uses at most 1 slot and yields the instant a live
  student queues. Live tutoring cannot be starved by background work.
- **Round-robin over `student_id`** gives per-student fairness among interactive turns: with K
  students each waiting, each gets every Kth freed slot regardless of how many turns they've queued.
- The scheduler runs under a single `_sched_lock`; grant just `event.set()`s the chosen waiter's
  greenlet, which then proceeds to call Ollama. No busy-waiting.

**Backpressure watchdog (the "busy" emit).** A single long-lived greenlet scans interactive waiters
each ~1s; any waiter whose `now - enqueued_at > busy_after` and not yet notified gets a one-time
`status_update` to `ctx.room`:

```
{"type": "GPU_BUSY", "msg": "Helga is thinking — lots of students right now, one moment…",
 "queue_depth": N, "eta_s": est}
```

This reaches the browser via the existing `send_status_update` → web-ui → Socket.IO room path
(`fsm_logic.py` `send_status_update`, `app.py` room emit). The student sees a friendly "busy" rather
than a frozen spinner that eventually 60s-times-out. `eta_s` ≈ `position_in_class * avg_gen_p50 /
cap`. We do **not** drop the request — it still gets dispatched fairly; `busy` is informational.

**Overload (`GpuOverloaded`).** If the queue exceeds `max_queue` or a waiter exceeds `admit_timeout`
(set just under the 60s client timeout, e.g. 55s), `admit` raises. The FSM catches it, emits a
graceful "I'm at capacity, try again in a moment" message, and returns — **never** a raw 60s
timeout traceback. This is also the signal that feeds the "add a second GPU" scale-out trigger (§8).

### 1.6 Timeout interaction

Today `chat(timeout=60, retries=3)`. With the gate, the 60s budget is split: **queue wait** +
**generation**. Set `admit_timeout = max(5, timeout - generation_budget)` where `generation_budget`
is the expected p95 generation time (e.g. 20s). Concretely: a turn may wait up to ~40s in queue,
then has ~20s to generate, staying inside the 60s client deadline. Retries should **not** re-queue
from scratch on a queue-wait timeout (that amplifies overload); on `GpuOverloaded` we surface busy
and do not retry. Retries remain only for genuine Ollama connection errors.

### 1.7 Ollama tuning (B23.3) — host env

Set on the **host** (Ollama runs on metal, not in a container):

| Var | Value | Why |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `-1` | Pin the model in VRAM forever; no cold reload between students (a reload is multi-second and would serialize the whole gate). |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | One model only; prevents VRAM thrash. Our gate assumes a single model. |
| `OLLAMA_NUM_PARALLEL` | `4` (24GB-class) → tune | The real concurrency knob; **must equal** `gpu_gate.cap`. Each slot consumes KV-cache VRAM; raising it trades VRAM for concurrency. Start at 4, raise while p95 stays bounded and VRAM has headroom. |
| `OLLAMA_NUM_GPU` / layers | model-dependent | Ensure full GPU offload of the 9B Q4 model. |

**`gpu_gate.cap` is initialized from `OLLAMA_NUM_PARALLEL`** (read the same env in core), so the two
never drift. Document that changing one requires changing the other (or have core query
`/api/ps`/config at startup and log a warning on mismatch).

---

## 2. Capacity model / unit economics (B27.4)

Goal: **how many concurrently *active* students does one GPU support**, with stated assumptions, so
spec 09 (pricing) can divide GPU $/month by sustainable seats and add headroom.

### 2.1 Definitions

- **Active student** = currently in a Socratic session, taking turns. Total enrolled ≫ concurrently
  active (most are offline). We size for the **concurrent-active peak**.
- **Turn** = one Socratic exchange = one LLM generation (a question/feedback). Streaming output.

### 2.2 Assumptions (stated; tune from §7 metrics)

| Symbol | Meaning | Assumed value |
|---|---|---|
| `T_out` | output tokens per Socratic turn (one Helga reply) | 180 tok |
| `T_in` | prompt tokens (system + concept + history) | ~1500 tok (prefill, cheaper/token) |
| `tok/s` | aggregate decode throughput of the 9B Q4 model on the target GPU | 60 tok/s @ 1 slot; scales sub-linearly with `NUM_PARALLEL` |
| `P` | `OLLAMA_NUM_PARALLEL` (slots) | 4 |
| `r` | turns per **active** student per minute (think + read + type/speak between turns) | 1.0 turn/min (one turn ~ every 60s) |
| `t_turn` | wall-clock generation per turn | `T_out / tok_s_per_slot` |

### 2.3 Throughput math

With `P` slots and modest per-slot throughput loss, model aggregate decode as
`tok/s_agg ≈ tok_s_1slot * P * 0.8` (the 0.8 is contention/batching efficiency at small P).

```
tok/s_agg          = 60 * 4 * 0.8            = 192 tok/s
turns/s (system)   = tok/s_agg / T_out       = 192 / 180   ≈ 1.07 turns/s
turns/min (system) = 1.07 * 60               ≈ 64 turns/min
```

Each active student consumes `r = 1.0` turn/min. So **sustainable concurrent-active students**:

```
N_active = turns_per_min_system / r = 64 / 1.0 ≈ 64
```

But that's the saturation point (utilization → 100%, queueing explodes). Apply a **headroom rule**:
run at ≤ 60% of saturation so p95 stays bounded and the background slot + bursts have room.

```
N_active_safe = 0.6 * 64 ≈ 38 concurrent active students per GPU
```

### 2.4 Active-vs-enrolled multiplier → seats

If, at peak, ~15% of enrolled students are concurrently active (homeschool/after-school usage is
spread across the day), then:

```
N_enrolled ≈ N_active_safe / 0.15 ≈ 38 / 0.15 ≈ 250 enrolled students per GPU (peak-limited)
```

### 2.5 Worked unit-economics example (feeds spec 09)

Assume a GPU box at **$X/month** (cloud or amortized hardware). Tokens/student/month for cost
attribution (B27.4 reports this per student from real metrics — these are estimates):

```
turns/active-hour    = 60
active hours/student/month (assume 8 h/wk * 4.3)   ≈ 34 h
turns/student/month  = 60 * 34                       ≈ 2040 turns
tokens/student/month = 2040 * (T_in_billed + T_out)  ≈ 2040 * (1500*0.25 + 180) ≈ 1.1M tok
        # prefill billed at ~0.25 equiv weight vs decode for cost attribution
```

Cost per student = `(X / 250)`; e.g. a `$1,200/mo` GPU box ⇒ ~`$4.80`/enrolled-student/month raw
GPU cost, before storage/egress/overhead. Spec 09 sets price = (this) × (gross-margin multiplier) +
fixed platform cost / seats. **Headroom rule for pricing:** never sell past `N_active_safe` of
concurrent peak; trigger a second GPU (§8) at 80% of `N_active_safe` sustained.

> All numbers above are **back-of-envelope with stated assumptions**. Replace with measured
> `gen_p50/p95`, `tok/s_agg`, and observed concurrent-active peak from §7 metrics before pricing.

---

## 3. SQLite → Postgres migration (B23.4)

**Trigger:** SQLite write contention under multi-worker (§4) or sustained `SQLITE_BUSY` / WAL
checkpoint stalls. SQLite stays the default; this is a connection-string swap, not a rewrite,
because of the sub-store abstraction (`01_DATA_MODEL.md` §8/§9).

### 3.1 Swap `_ThreadLocalDB` for a pool — same shape

Introduce `services/common/db.py` with a backend-selecting factory keyed on `DB_BACKEND`
(`sqlite`|`postgres`):

```python
def get_db(db_url):
    if db_url.startswith("postgresql://"):
        return _PgPool(db_url)        # psycopg_pool.ConnectionPool, min/max from env
    return _ThreadLocalDB(sqlite_path_from(db_url))
```

`_PgPool.get()` returns a pooled connection with `row_factory=dict_row` so sub-stores keep using
`row["col"]` access exactly as with `sqlite3.Row`. **The sub-store classes
(`ProgressStore`, `FlashcardStore`, `ActivityStore`, `ScheduleStore`, `SettingsStore`,
`SearchStore`, and the new per-student stores in `01_DATA_MODEL.md` §8) take a handle, not a path** —
they already isolate SQL, so only `_ThreadLocalDB`/`_PgPool` and dialect-specific SQL change.

### 3.2 Dialect translation (per `01_DATA_MODEL.md` §9)

| SQLite | Postgres |
|---|---|
| `INSERT OR REPLACE` | `INSERT … ON CONFLICT (pk) DO UPDATE SET …` |
| `AUTOINCREMENT` | `GENERATED ALWAYS AS IDENTITY` |
| `datetime('now')` | `now()` |
| JSON in `TEXT` | `jsonb` |
| `?` placeholders | `%s` (psycopg) — centralize via a tiny param adapter or use named params everywhere |
| FTS5 MATCH | `tsvector` + GIN, **or** keep catalog FTS in a read-only SQLite sidecar (catalog is global, see §3.4) |
| `concept_vec` (sqlite-vec) | `pgvector` |

Centralize the `?`→`%s` and `INSERT OR REPLACE` differences in `db.py` helper methods
(`db.upsert(table, keys, row)`) so sub-stores call one API and never hand-write dialect SQL.

### 3.3 Port `schema_version` migrations

`storage.py` `_init_db()` already runs an integer-`schema_version` migration ladder (v1→v2→v3, ~L144).
Re-express each step as backend-agnostic DDL (or branch on backend where types differ:
`TEXT`→`jsonb`, etc.). The Postgres path runs the same numbered ladder against an empty DB, then the
ETL (§3.5) loads rows. Keep the ladder the single source of truth — no separate Postgres DDL dump.

### 3.4 FTS5 strategy

The catalog (courses/concepts) is **global and read-mostly** (`01_DATA_MODEL.md` §8 keeps catalog
stores global). Two options, decide by load:

- **Simple (recommended first):** keep catalog full-text search in a **read-only SQLite sidecar
  file** with FTS5, rebuilt by the authoring/hydration job. Per-student data goes to Postgres;
  catalog search keeps working unchanged. Zero `tsvector` work.
- **Full:** move concept text to Postgres `tsvector` + GIN and rewrite `SearchStore.hybrid_search`'s
  FTS branch to `to_tsquery`. Only worth it if catalog must live in Postgres for ops simplicity.

### 3.5 One-shot ETL preserving `student_id`

A migration script `scripts/sqlite_to_pg.py`:
1. Apply the numbered schema ladder to the empty Postgres DB.
2. For each table, stream rows from SQLite and `COPY`/batch-insert into Postgres, **preserving
   `student_id` and all PKs/UIDs verbatim** (UIDs are app-generated `con_`/`course_` strings, not
   serials — no remap needed; reset identity sequences after load for any serial columns).
3. Re-derive FTS (rebuild sidecar or `tsvector`).
4. Verify: row counts per table match; checksum a sample of `user_progress` by `(student_id,
   concept_uid)`; spot-check JSON columns parse as `jsonb`.
5. Cut over `DB_BACKEND`/`DATABASE_URL`; keep SQLite file as fallback for one release.

JSON course `structure.json` + markdown content stay on disk / object storage (§5) — only relational
state migrates.

---

## 4. Multi-worker (B23.5)

**Trigger:** one gunicorn-gevent worker saturates CPU on the web/Socket.IO side (not GPU), or we
want HA. Goes with Postgres (§3) and Redis. **GPU concurrency is unaffected by worker count** — see
§4.4.

### 4.1 What breaks with >1 worker (today, single-worker assumptions)

- **Flask sessions** are signed cookies (fine cross-worker) **but** any server-side per-process state
  isn't shared. `_creation_initiator_sid` (`app.py` ~L122) and the CSRF token in `session` are the
  in-memory/cookie cases to audit.
- **Socket.IO rooms** are per-process: a room emit from worker A won't reach a socket connected to
  worker B. → needs a **message queue**.
- **FSM registry** (B15.6, the per-student `fsm_registry.py` replacing the global singleton): if
  worker A holds student S's FSM in memory, worker B can't serve S's next turn. → needs **stateless
  hydrate-per-turn**.
- **GPU gate** (§1) is in-process; with N workers there'd be N independent caps → N×`NUM_PARALLEL`
  hitting Ollama. → needs a **cross-process semaphore**.

### 4.2 Redis-backed Flask sessions + Socket.IO message queue

- Flask sessions → server-side store via `flask-session` with `SESSION_TYPE=redis`. CSRF token and
  `_creation_initiator_sid`-style data live in the shared session, not process memory.
- `SocketIO(..., message_queue=os.environ["SOCKETIO_MESSAGE_QUEUE"])` (e.g. `redis://redis:6379/0`).
  Now `socketio.emit(..., room=student_room)` from any worker fans out to whichever worker holds the
  socket. The existing room-scoped status-update path (FSM → web-ui → room) works across workers
  with **no call-site change** — only the `SocketIO(...)` constructor in `app.py` gains
  `message_queue=`.

### 4.3 Stateless FSM — hydrate-per-turn (Option A)

Convert the FSM registry to **stateless**: on each incoming event, the handling worker **hydrates**
the student's FSM from durable storage (`user_state.json`/Postgres: current node, state, transcript
tail), processes the single transition, **persists** the new state, and discards the in-memory FSM.
Any worker can serve any student's next turn — no affinity, no sticky sessions required.

Changes vs single-worker:
- `fsm_registry.get(student_id)` becomes `hydrate(student_id)` → build FSM from stored state instead
  of returning a long-lived in-memory object.
- Every handler ends with an atomic persist (already moving that way: `_save_current_course_progress`
  + `_atomic_write`; LRN-12 persists transcript). Postgres makes this a row upsert under
  `student_id`.
- Transcript cap (PERF-5) keeps the hydrate payload small.
- Trade-off: a little extra read/write per turn (negligible vs a GPU turn). Option B (sticky
  sessions + in-memory FSM) is **rejected** — it reintroduces affinity and complicates failover.

### 4.4 Cross-process GPU semaphore

Swap the in-process `GpuGate` counters for a **Redis-backed counting semaphore** behind the *same*
`GpuGate` API (call sites in `llm_client.py`/`llm_utils.py` unchanged):

- Total in-flight cap: Redis key `gpu:inflight` (atomic `INCR`/`DECR` with cap check via Lua, or a
  Redis `SEMAPHORE` list of `cap` tokens via `BRPOPLPUSH`).
- Background sub-cap: separate `gpu:bg_inflight` token pool (`bg_slots` tokens).
- Fairness across students cross-worker: per-student Redis lists `gpu:wait:{student_id}` + a
  round-robin pointer; or simpler at R4, a Redis **sorted set** keyed by `(last_served_ts,
  student_id)` to approximate round-robin (pop the least-recently-served waiting student). Background
  waiters use a separate FIFO list and are only popped when the interactive set is empty.
- Backpressure watchdog: each worker still runs its local watchdog scanning its own connected
  sockets' waiters; ETA uses the shared queue depth from Redis.
- **Stale-token reclaim:** every acquired token carries a TTL ≥ client timeout; a reaper releases
  tokens whose owner died mid-request (worker crash) so the cap can't leak.

The cap is now **global across workers** → Ollama still sees ≤ `NUM_PARALLEL` regardless of worker
count. This is the critical correctness property of multi-worker.

---

## 5. Production topology (B23.6)

**Principle: one beefy GPU Linux box first. No Kubernetes.** Vertical before horizontal.

### 5.1 R1 target (single worker, many students)

```
            Internet (443)
                │  auto-TLS (Let's Encrypt)
            ┌───▼────┐
            │ Caddy  │  reverse proxy, HTTPS, WS upgrade, gzip
            └───┬────┘
                │ 127.0.0.1 only
        ┌───────▼────────┐
        │ gunicorn       │  -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker
        │  (1 worker)    │  web-ui Flask + Socket.IO (async_mode='gevent')
        └───────┬────────┘
        internal docker network (no host ports — closes B9.4)
   ┌────────┬───┴────┬─────────┬──────────┐
 core-logic rag-engine tts   research   searxng
        │
        │ host.docker.internal:11434
   ┌────▼─────┐
   │ Ollama   │  on host metal, GPU, KEEP_ALIVE=-1, NUM_PARALLEL=P
   └──────────┘
   SSD: helga.db (SQLite→Postgres later) + data/courses (→ object storage at 2nd box)
```

### 5.2 Caddy + gunicorn-gevent

- **Caddy** terminates TLS (automatic certs), proxies `/` and `/socket.io/` to gunicorn, handles the
  WebSocket `Upgrade`. A 6-line `Caddyfile` (`yourhost { reverse_proxy web-ui:5000 }`) — Caddy
  upgrades WS transparently.
- **gunicorn** replaces the dev server, **one gevent-websocket worker** at R1 (Socket.IO long-poll +
  WS need gevent; `app.py` is already `async_mode='gevent'` and monkey-patches at import). Command:
  `gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 --worker-connections 1000
  app:app`. Multi-worker (§4) only after Redis MQ is in.

### 5.3 Drop host port exposure (B9.4)

Remove the `ports:` host mappings for `core-logic`, `rag-engine`, `tts`, `research`, `searxng` in
`docker-compose.yml`. Only Caddy binds host `443`/`80`. Internal services talk over the
`internal` bridge network by container name (already configured). `main.py`/`deploy.sh` health checks
switch from `localhost:5003` etc. to `docker exec`/internal checks (or hit `/health` via Caddy where
appropriate).

### 5.4 Ollama health / circuit breaker (B9.5 / B27.5)

Wrap the gate's Ollama calls in a **circuit breaker** in `gpu_gate.py` (or a thin `OllamaBreaker`):
- States CLOSED → OPEN → HALF_OPEN. Trip to OPEN after `N` consecutive connection failures/timeouts
  to Ollama. While OPEN, `admit()` fast-fails with a friendly "tutor is temporarily unavailable"
  status (no 60s hang, no retry storm). HALF_OPEN probes `health_check()` (`llm_client.health_check`,
  ~L292) periodically; one success closes the breaker.
- Emits a `status_update`/metric on state change → alerting (§7). This closes the "Ollama SPOF, no
  circuit breaker" debt (B9.5) and B27.5.

### 5.5 Second box / object storage

When a second GPU box is added (§8), `data/courses` (JSON structures + markdown) moves to **object
storage** (S3-compatible / MinIO) so all web/core workers and both GPU boxes read one catalog;
Postgres (§3) is the shared relational store; Ollama runs on each GPU box and the gate's Redis
semaphore can shard per-box (one gate per Ollama, routed by a simple load balancer / least-queue
pick).

### 5.6 docker-compose evolution

Add three services and remove host ports:

```yaml
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]          # ONLY public ports on the host
    volumes: [./configs/caddy/Caddyfile:/etc/caddy/Caddyfile, caddy_data:/data]
    depends_on: { web-ui: { condition: service_healthy } }
    networks: [internal]

  redis:                                  # R4: sessions + Socket.IO MQ + GPU semaphore
    image: redis:7-alpine
    command: ["redis-server", "--save", "60", "1", "--appendonly", "yes"]
    volumes: [redis_data:/data]
    networks: [internal]
    profiles: ["scaleout"]                # off until R4

  postgres:                               # R4: relational store
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=helga
      - POSTGRES_USER=helga
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes: [pg_data:/var/lib/postgresql/data]
    networks: [internal]
    profiles: ["scaleout"]

  web-ui:
    # ports: REMOVED (B9.4)              # was "5050:5000"
    command: >                            # replace dev server with gunicorn-gevent
      gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker
      -w ${GUNICORN_WORKERS:-1} --worker-connections 1000 -b 0.0.0.0:5000 app:app
    environment:
      - SOCKETIO_MESSAGE_QUEUE=${SOCKETIO_MESSAGE_QUEUE:-}   # redis://redis:6379/0 at R4
      - SESSION_TYPE=${SESSION_TYPE:-filesystem}             # redis at R4
      - DATABASE_URL=${DATABASE_URL:-}                       # postgres URL at R4
  # core-logic / rag-engine / tts / research / searxng: drop `ports:` blocks
```

`volumes:` add `caddy_data`, `redis_data`, `pg_data`. R4 services gated behind a compose `profiles:
["scaleout"]` so the R1 box stays minimal.

---

## 6. Backups & DR (B23.7)

### 6.1 Extend `make backup`

The current `Makefile` `backup` target snapshots `helga.db` + tars `data/courses`. Extend:

- **Nightly cron** (`make backup` via cron or a `night_audit`-style job) producing
  `backups/helga_<ts>.db` (`sqlite3 .backup`, consistent under WAL) **or** `pg_dump -Fc helga`
  (Postgres at R4), plus `courses_<ts>.tgz`.
- **Retention:** GFS — keep 7 daily, 4 weekly, 6 monthly; prune older. Add a `make backup-prune`
  step (`find backups -mtime +N -delete` with tier logic).
- **Off-box copy:** push each nightly artifact to object storage (S3/MinIO) — a backup on the same
  SSD as the DB is not DR.
- Include `user_state.json` and any `data/uploads` worth keeping; exclude `tts_cache`, `hf_cache`,
  `research_cache` (regenerable).

### 6.2 Documented restore drill (must be tested, not just written)

`docs/runbooks/restore_drill.md` + `make restore BACKUP=<ts>`:
1. `docker compose stop core-logic rag-engine web-ui`.
2. SQLite: copy `backups/helga_<ts>.db` → `data/helga.db` (or `pg_restore` into a fresh DB).
3. `tar xzf backups/courses_<ts>.tgz -C data`.
4. `docker compose up -d`; run `make health`; verify a known student's progress + a course render.
- **Drill cadence:** quarterly, into a scratch dir, timed; record RTO. Acceptance: restore completes
  < 30 min and the verified student's last completed concept matches.

### 6.3 Secrets management

| Secret | Where |
|---|---|
| `FLASK_SECRET_KEY` | **Persisted** in `.env`/secret store (already wired, `app.py` ~L84 warns if ephemeral — B9.3). Must be stable so sessions survive restarts. |
| `POSTGRES_PASSWORD`, `DATABASE_URL` | secret store / `.env` not in VCS (R4) |
| Stripe (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`) | secret store; never logged (B20) |
| Redis URL/auth | `.env` (R4) |

R1: `.env` with `chmod 600`, excluded by `.gitignore`. R4 / multi-box: Docker secrets or a vault;
inject as env at container start. Add a startup assertion that `FLASK_SECRET_KEY` is set in
production (refuse to boot with an ephemeral key in `FLASK_ENV=production`).

---

## 7. Observability (B27.1 / B27.2 / B27.3)

### 7.1 Structured JSON logging + correlation (B27.1)

- Replace the plain `logging.basicConfig` format (`app.py` ~L69) with a JSON formatter
  (`python-json-logger`) across web-ui, core, rag.
- Every log record carries `request_id` (generated at the web-ui edge per HTTP/Socket.IO event,
  propagated via header `X-Request-ID` to core/rag) and `student_id`. Implement with a
  `contextvars`-based filter that injects both into each record. The `LLMContext.request_id` (§1.3)
  ties LLM-gate logs to the originating turn.
- Replace remaining `logging.info("DEBUG: …")` (LOG-1) with proper levels.

### 7.2 Prometheus metrics (B27.2)

Add `prometheus_client`; expose `/metrics` on each service (scraped internally, not host-exposed —
behind Caddy basic-auth or internal-only). Instrument **in `gpu_gate.py`** (the choke point) plus FSM
and storage:

| Metric | Type | Labels | Where |
|---|---|---|---|
| `helga_gpu_queue_depth` | Gauge | `class` | `gpu_gate` admit/release |
| `helga_gpu_inflight` | Gauge | `class` | `gpu_gate` grant/release |
| `helga_llm_gen_seconds` | Histogram | `class` | around the Ollama `requests.post` |
| `helga_llm_queue_wait_seconds` | Histogram | `class` | admit→grant duration |
| `helga_llm_tokens_total` | Counter | `student_id`, `direction(in/out)` | parse Ollama usage/response |
| `helga_active_sessions` | Gauge | — | FSM session start/stop |
| `helga_gpu_busy_emits_total` | Counter | — | backpressure watchdog |
| `helga_llm_errors_total` | Counter | `kind(timeout/conn/http/overload)` | gate/`chat` except paths |
| `helga_circuit_breaker_state` | Gauge | — | OllamaBreaker (§5.4) |
| `helga_db_busy_total` | Counter | — | `SQLITE_BUSY` catches → Postgres trigger signal |

`gen_seconds` p50/p95 (from the histogram) directly validate §1.6 timeout budgets and feed §2
capacity. `tokens_total` per `student_id` powers B27.4 cost attribution.

### 7.3 xAPI-style learning-analytics event log (B27.3)

Append-only event log (table `learning_events` and/or NDJSON to disk/object storage), one row per
pedagogically meaningful event in xAPI `actor / verb / object / result / context` shape:
- **actor** = `student_id`; **verb** = `answered | completed | reviewed | started | struggled`;
  **object** = concept/lesson/exam UID; **result** = grade/score/duration; **context** =
  `course_uid`, `request_id`, `session_id`, grade level.
- Emitted from FSM transition handlers (concept complete, grade assigned, review graded) and the FSRS
  engine. Decoupled from operational logs so it can feed the parent dashboard (B19), struggle alerts
  (B24.4), and standards-coverage reports without parsing app logs.

---

## 8. Scale-out triggers (concrete thresholds)

Act when a threshold is **sustained** (e.g. 15-min rolling), not on a spike:

| Action | Trigger |
|---|---|
| **Raise `OLLAMA_NUM_PARALLEL`** (and `gpu_gate.cap`) | VRAM headroom > 20% **and** `gpu_queue_wait p95 > 5s` at current cap. |
| **SQLite → Postgres** (§3) | `helga_db_busy_total` rising (any sustained `SQLITE_BUSY`), **or** committing to multi-worker, **or** DB file > ~5–10 GB / WAL checkpoint stalls. |
| **Multi-worker** (§4) | web-ui/gunicorn worker CPU sustained > 80% while GPU < 70% (web side is the bottleneck, not GPU), or HA required. Requires Redis + Postgres first. |
| **Second GPU box** (§5.5) | concurrent-active students sustained > 80% of `N_active_safe` (§2.3), **or** `helga_llm_errors_total{kind="overload"}` non-trivial, **or** `gen_p95` breaches the §1.6 budget at max safe `NUM_PARALLEL`. |
| **Swap Ollama → vLLM / continuous batching** | single-GPU throughput ceiling reached (raising `NUM_PARALLEL` no longer raises `tok/s_agg` — batching efficiency `0.8` factor collapsing) and we still need more concurrent students per GPU than §2 allows. vLLM's continuous batching gives materially higher concurrent throughput on the same GPU; the `GpuGate` API and `LLMContext` tagging stay, only the backend client + per-slot accounting change. |
| **Object storage for catalog** (§5.5) | adding a 2nd box (catalog must be shared) or `data/courses` outgrows one SSD comfortably. |

---

## 9. Env / config matrix

| Var | Service | Default | Secret? | Notes |
|---|---|---|---|---|
| `OLLAMA_URL` | core, rag | `http://host.docker.internal:11434` | no | existing |
| `OLLAMA_MODEL` | core, rag | `qwen3.5:9b` | no | existing |
| `OLLAMA_NUM_PARALLEL` | host (Ollama) | `4` | no | **must equal** `GPU_GATE_CAP` |
| `OLLAMA_KEEP_ALIVE` | host (Ollama) | `-1` | no | pin model in VRAM |
| `OLLAMA_MAX_LOADED_MODELS` | host (Ollama) | `1` | no | single model |
| `GPU_GATE_CAP` | core | = `OLLAMA_NUM_PARALLEL` | no | total in-flight cap (§1.4) |
| `GPU_GATE_BG_SLOTS` | core | `1` | no | max background in-flight (§1.5) |
| `GPU_GATE_BUSY_AFTER_S` | core | `8` | no | backpressure 'busy' threshold |
| `GPU_GATE_MAX_QUEUE` | core | `256` | no | overload admission limit |
| `GPU_GATE_ADMIT_TIMEOUT_S` | core | `55` | no | < client `timeout` (60) |
| `OLLAMA_BREAKER_FAILS` | core | `5` | no | consecutive failures → OPEN (§5.4) |
| `DB_BACKEND` | core, rag | `sqlite` | no | `sqlite`\|`postgres` (§3) |
| `DATABASE_URL` | core, rag | `` (→ sqlite) | **yes** | `postgresql://…` at R4 |
| `PG_POOL_MIN`/`PG_POOL_MAX` | core, rag | `1`/`8` | no | psycopg pool |
| `SOCKETIO_MESSAGE_QUEUE` | web-ui | `` (in-proc) | no | `redis://redis:6379/0` at R4 (§4.2) |
| `SESSION_TYPE` | web-ui | `filesystem` | no | `redis` at R4 |
| `REDIS_URL` | web-ui, core | `` | maybe | sessions/MQ/semaphore |
| `GUNICORN_WORKERS` | web-ui | `1` | no | raise only after Redis MQ (§4) |
| `FLASK_SECRET_KEY` | web-ui | (random, warns) | **yes** | must persist in prod (B9.3) |
| `CORS_ORIGINS` | web-ui | `localhost:5050…` | no | existing; set to public host behind Caddy |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | web-ui | — | **yes** | B20 |
| `POSTGRES_PASSWORD` | postgres | — | **yes** | compose secret |
| `METRICS_ENABLED` | all | `true` | no | expose `/metrics` (§7.2) |
| `LOG_FORMAT` | all | `json` | no | structured logging (§7.1) |
| `OBJECT_STORE_URL` / creds | core, rag | — | **yes** | catalog at 2nd box (§5.5) |

---

## 10. Test / load plan + acceptance criteria

### 10.1 Unit tests (`tests/`)
- `test_gpu_gate.py`: cap never exceeded; background ≤ `bg_slots`; background yields when an
  interactive waiter is present (anti-starvation); round-robin fairness across `student_id`s;
  `GpuOverloaded` raised past `max_queue`/`admit_timeout`; release wakes exactly one waiter; stale
  background never blocks interactive. Use gevent greenlets, fake "Ollama" sleep.
- `test_circuit_breaker.py`: CLOSED→OPEN after N fails; fast-fail while OPEN; HALF_OPEN recovery.
- `test_db_backend.py`: same sub-store API over sqlite vs a Postgres test container; upsert semantics
  match; `schema_version` ladder applies on both; ETL preserves `student_id` and row counts.
- `test_stateless_fsm.py`: hydrate→transition→persist round-trips identical to in-memory path.

### 10.2 Load test (`scripts/loadtest.py`, e.g. Locust/k6 driving Socket.IO)
- Simulate **M concurrent active students** at `r` turns/min each, plus **1 background course build**
  running throughout.
- Sweep M = 10, 20, 38, 64 against `NUM_PARALLEL=4` and measure
  `gpu_queue_wait`, `gen_seconds`, `busy_emits`, `errors{overload}`, fairness (per-student turn
  latency spread), and confirm the background build still completes.

### 10.3 Acceptance criteria
- **AC1 — no 60s timeouts:** at M = `N_active_safe` (≈38), zero client 60s timeouts; overload surfaces
  as graceful `busy`/"at capacity," never a raw timeout traceback.
- **AC2 — bounded tail:** `gen_seconds p95` within the §1.6 budget; `queue_wait p95` < configured
  `busy_after` × 2 at safe load.
- **AC3 — fairness:** with K students each queuing multiple turns, per-student p95 turn latency
  spread ≤ 1.5× the median (no student starved by a chatty peer).
- **AC4 — background never starves live:** a running course build never raises any interactive
  student's p95 by more than a small margin; background uses ≤ 1 slot and yields immediately to live.
- **AC5 — restore drill:** documented restore completes < 30 min with verified student progress
  intact (§6.2); quarterly drill timed and logged.
- **AC6 — cross-worker (R4):** with `GUNICORN_WORKERS=2` + Redis MQ, a status emit reaches a student
  on the other worker; Ollama still sees ≤ `NUM_PARALLEL` in flight (global cap holds).

### 10.4 Open questions
- Exact `tok/s_agg` vs `NUM_PARALLEL` curve on the target production GPU (the `0.8` efficiency factor
  in §2 is an assumption — measure before pricing).
- Fairness granularity: per-`student_id` (this spec) vs per-`parent_id`/seat — does a family share a
  fairness bucket?
- Round-robin vs weighted (priority for a struggling student / live exam over casual practice)?
- Redis semaphore vs routing to Ollama's own queue at R4 — is the cross-process gate worth it, or do
  we pin GPU traffic to a single core worker and only multi-worker the web tier?
- Catalog FTS: SQLite sidecar (§3.4 simple) vs Postgres `tsvector` — decide at Postgres cutover.
- Should `BACKGROUND` course builds be moved off the live GPU entirely (dedicated build window / 2nd
  GPU) once concurrent-active load is high, rather than sharing 1 slot?
```
