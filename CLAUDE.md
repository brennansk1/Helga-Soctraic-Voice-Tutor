# CLAUDE.md — Project Helga: Socratic Voice Tutor

> **Stack reality — verified 2026-08-28 by running the system, not by reading it.**
> Everything in this section was checked against the live machine on that date.
> The "MASTER IMPLEMENTATION PLAN" far below is ARCHIVE: it describes a
> Jetson/Qwen2.5/ZIM/KuzuDB stack that no longer exists. `docs/HELGA_BUILD_TREE.md`
> is the reverse-engineered audit and roadmap.

## Project Overview
Helga is an offline AI tutor. Three learning modes are live — **Socratic
teaching**, **spaced-repetition review**, and the **Memory Palace**.

`/quiz`, `/review`, `/schedule` and `/test` are 302 aliases folded into
`/practice` (verified by request, 2026-08-28; they now carry their query string
across, so `/quiz?course_uid=X` asks about X).

`/palace` is NOT one of them: it returns 200 and renders. This file said it was
gone and redirecting; the route renders `palace.html`, its own docstring records
that it was deliberately restored after a spell of redirecting to home, and
`tests/web/tab_account/test_honest_controls.py` requires it to be linked from
`/learn` — which it is, as the palace mode of the open course. It is a mode of a
course rather than a nav destination, which is presumably how the doc came to
call it gone.

## Hardware (measured)
**Apple M4 — the base chip, not a Pro — with 24 GB of unified memory.** This is
the single most load-bearing fact about the system and it was wrong here for
months. Consequences that follow from it, all measured:
  * A 35B model and the container stack together exceed comfortable residency.
    Weights get evicted between turns, so a lesson can pay a ~2 minute cold load
    even mid-session. `OLLAMA_KEEP_ALIVE=30m` is set and reaches Ollama; the
    machine simply does not hold it.
  * Turn latency is dominated by PREFILL, not decode. Prefix caching measured
    31.6x on a warm cache (27.7s -> 0.88s).
  * Speculative decoding does not work on this hardware. Do not retry it.

## Architecture
Containers (docker compose): **web-ui** (Flask + Socket.IO, 5050->5000),
**core-logic** (FSM + course build, 5003), **rag-engine** (course CRUD, review
queue, search, 5002), **tts** (Kokoro, 5005), **research** (build-time
augmentation, 5006), **searxng** (8080), **stt** (optional; reports `offline`
when absent and the UI says so rather than pretending).

Host-native, NOT containers: **Ollama** on 11434, and the **verifier**
(MiniCheck entailment) on 5007.

There is no inference-llm container. `helga-sqlcheck` is a live Postgres used to
EXECUTE SQL claims during the audit — content correctness is checked by running
the claim, not by asking a model.

## Model
`OLLAMA_MODEL` defaults to **`nail-35b-a3b-ctx`** (compose, `.env`,
`llm_client.py` all agree — this has broken three times by drifting apart, so
tests pin it). Reached at `host.docker.internal:11434` over the
OpenAI-compatible API.

## Storage
SQLite (`helga.db`, WAL) at **schema v21**, plus JSON course structures and
Markdown concept files. No KuzuDB, no ZIM.

A NOTE ON THE WAL FILES: writing to `data/helga.db` from host Python while the
containers are running leaves `-shm`/`-wal` files the containers cannot read,
and the services then die with "disk I/O error". Stop the services, remove
`data/helga.db-shm` and `data/helga.db-wal`, start them again.

## The review system (mode A)
Concept markdown is the item bank. `services/common/review_items.py` extracts
four tiers of item from sections the hydrator already writes — Key Facts,
Misconceptions, Edge Cases, Bloom-banded Socratic Hooks — with NO model calls,
because review-time latency has to be zero on this hardware. 2,460 items came
out of 186 concepts, 58% of them higher-order.

The mix is deliberate and evidence-led: practising facts alone transfers no
better than not practising, and mixed factual + higher-order beats either pure
form. Every concept yields items at several tiers; its Bloom target shifts the
RATIO, never closes a lane.

`services/common/review_scheduler.py` is the queue policy over FSRS — load
balancing, priority, interleaving across courses, leech escalation, retirement,
and a daily new-item budget that spends down. `services/common/item_bank.py`
builds a course's items (Stage 5 of the build).

## Key files
| File | Purpose |
|------|---------|
| `services/core/fsm_logic.py` | FSM — all Socratic interaction |
| `services/core/course_builder.py` | Build pipeline, Stages 1-5 |
| `services/core/course_audit.py` | Audit checks + `is_teachable` (one definition of a teachable concept, shared with the course list) |
| `services/core/course_repair.py` | Pass 3 repair |
| `services/core/sql_ground_truth.py` | Executes SQL claims against `helga-sqlcheck` |
| `services/core/fsrs_engine.py` | FSRS-5, direct implementation |
| `services/common/review_items.py` | Item extraction from concept markdown |
| `services/common/review_scheduler.py` | Queue policy, maturity bands |
| `services/common/item_bank.py` | Per-course item build |
| `services/common/storage.py` | SQLite + JSON + Markdown facade |
| `services/common/user_profile.py` | Profile merge/sanitise (partial saves must MERGE) |
| `services/verifier/verifier_server.py` | MiniCheck entailment, host-native :5007 |
| `services/web-ui/static/js/practice.js` | Review session and queue UI |
| `services/web-ui/static/js/degree.js` | Programme view; areas group by slot |

## Things that keep going wrong here
Read these before changing anything; each has cost real time more than once.
  * **Component built, path never fires.** The signature defect. A feature works
    in isolation and nothing calls it: 40 flashcards with no review surface,
    `activity_log` empty because one call passed its arguments positionally,
    `/api/profile` answering 200 while dropping the field. Grep for a READER
    before believing a writer works.
  * **Two sources for one number.** Home said 186 due while Practice said 34.
    If two screens can show the same quantity, they must read the same endpoint.
  * **Name collisions in one JS scope.** `function show()` twice, and a `var`
    overwriting a hoisted function, each killed a whole page silently.
    `tests/frontend/test_static_asset_integrity.py` guards this.
  * **Undefined CSS tokens.** `var(--x)` where `--x` does not exist drops the
    whole declaration. Same test file guards it.
  * **A JS handler that throws is a page that looks quiet.** build-view.js's
    Stage 5 block used `text` where the function's variable is `msg`, so a
    ReferenceError fired on the FIRST message of every build and on every one
    after it, killing the handler before the `ITEMS:` check and everything
    below. The "Review items" tile could never light up, on any build, and the
    page just looked like it had nothing to say. Nothing surfaces console
    errors, so this is invisible unless you open devtools on a LIVE build —
    which is how it was finally found, after months. When a live view seems
    quiet, read the console before believing the server is silent.
  * **Two endpoints, neither of which sees every build.**
    `/api/creation_status` reads the FSM in core-logic and sees a build started
    from Create. A RESUME runs `ContentHydrator` inside the rag-engine and is
    invisible there for its whole duration — `/api/build/status` is the record
    it claims. Asking only the first made the courses page offer "Resume build"
    during a live resume, and made /build say "No course is building" while one
    ran. Ask both, as `build-guard.js`'s own probe does.
  * **Images older than requirements.txt.** `jsonschema` was declared on
    2026-08-25 and the images were built on the 24th, so for three days EVERY
    schema-constrained LLM call in the system ran unvalidated and the
    schema-mismatch retry could not fire. The service said so in its logs once
    per process and nobody was reading. After changing any requirements.txt,
    `docker compose build <service>` — restarting a container does not rebuild
    its image. Check with:
    `docker exec helga-rag-engine python3 -c "import jsonschema"`

## Development Conventions
- **Python 3.10+**, no type stubs needed
- **Testing:** `pytest` — run `python -m pytest tests/ -v` from project root
- **Logging:** Use `logging.getLogger(__name__)` per module, never bare `except: pass`
- **Error handling:** Always catch specific exceptions, log them, provide fallbacks
- **Storage:** All course data under `data/courses/{uid}/structure.json` and `data/courses/{uid}/content/{concept_uid}.md`
- **LLM calls:** Always through `services/common/llm_utils.py` — never call the LLM API directly
- **Docker:** Services use container names (e.g., `helga-core-logic`, `helga-rag-engine`)
- **UID format:** `course_`, `mod_`, `unit_`, `less_`, `con_` prefix + 8-char hex
- **SQL:** Use `os.path.join()` not string concatenation; whitelist column names before SQL interpolation
- **Paths:** Always `os.path.join()`, never string concat

## Known Constraints
- **Apple M4 (base) / 24 GB:** Ollama runs natively on the host and the containers share what is left. `nail-35b-a3b-ctx` does not stay resident beside them, so cold reloads happen mid-session — see Hardware above. Hydration is sequential; avoid speculative `gc.collect()`.
- **Ollama is a hard external dependency:** all LLM calls go to `host.docker.internal:11434`. There is still no *fallback model*, but there is now a **circuit breaker** — `services/common/llm_breaker.py`, shared by `llm_client.py` (tutoring) and `llm_utils.py` (building). When the host is down it fast-fails instead of paying a timeout per call, and probes its way back automatically. Tune with `OLLAMA_BREAKER_TRIP` / `OLLAMA_BREAKER_PROBE_S` (see `docs/HELGA_BUILD_TREE.md` B9.5).
- **LLM failures are NAMED, and the names matter:** `LLMUnavailable` (circuit open / timeout / transport / overloaded) means *we never got an answer*; `LLMBadOutput` (bad JSON / schema mismatch / empty response) means *the model answered badly*; `LLMRequestRejected` (4xx) means *our payload is wrong and the host is fine*. Only the first family counts toward the breaker. Pass `strict=True` (or set `LLM_STRICT_ERRORS=1`) to `llm_generate`/`llm_generate_json`/`chat` to get the exception; otherwise read `last_llm_failure()` after a `""`/`None`. Never collapse these back into one "content unavailable" branch — that is how a dead host used to produce a course full of stubs marked `ready`.
- **LLM output:** JSON can still fail whatever the model — always use `llm_generate_json()` with retry, and prefer Ollama's `format` (JSON-schema) constrained output where possible.
- **Single global FSM session:** state is not per-user/per-tab; multiple browsers share one session (see `docs/HELGA_BUILD_TREE.md` B6.3).
- **Socket.IO events are receive-only in browser** — all commands go Browser → HTTP POST `/api/event` → Web-UI → HTTP POST core `/event` → FSM

## Event System (FSM ↔ Web UI)
Events flow: **Browser** → `sendEvent()` in session.js → HTTP POST `/api/event` → **web-ui** → HTTP POST core `/event` → **FSM**

Status updates flow back: **FSM** `send_status_update()` → HTTP POST web-ui `/api/update_thinking_status` → Socket.IO `status_update` → **Browser**

State polls: web-ui **state_poller greenlet** (every 2s) → GET core `/state` → Socket.IO `state_update` → **Browser** `updateUI()`

Key FSM events:
| Event | Handled | Notes |
|-------|---------|-------|
| `SET_CONTEXT` | Yes | Sets active_course_uid + teaching_style |
| `NAVIGATE_TO_TOPIC` | Yes | Global handler, any state |
| `TEXT_INPUT` | Yes | State-specific |
| `RESUME_COURSE` | Yes | LOBBY state only |
| `SKIP_CONCEPT` | Yes | Uses _advance_without_completing() |
| `TOGGLE_TEXT_ONLY` | Yes | |
| `TOGGLE_TTS` | Yes | Enables/disables audio |
| `PAUSE_SESSION` | Yes | Stops TTS/STT on back button |
| `DELETE_COURSE` | Yes | |

---

## Planning & Tracking Documents

| Document | Purpose |
|----------|---------|
| `manual_verification_tests.md` | **PRIMARY** — Complete E2E verification checklist (13 flows A-M), remaining implementation items |
| `plan.md` | Final product plan with P0-P7 priorities and status percentages |
| `.ai_context/TASK_QUEUE.md` | Phase completion tracking (Phases 1-11) and remaining P3/P4 work |
| `.ai_context/ACTIVE_CONTEXT.md` | Current system state and environment |

**Always reference `manual_verification_tests.md` for what to build/verify next.** The "Remaining Implementation Items" section at the bottom is the prioritized work queue.

---

# MASTER IMPLEMENTATION PLAN

---

## FLOW 1: AUTOMATIC COURSE CREATION
**Path:** courses.html topic-form → Socket.IO `text_input` → FSM text parser → SkeletonBuilder → ContentHydrator → storage → "Course built successfully!" → redirect

### Bugs & Fixes

**AUTO-1 (Critical): Duplicate `socket.connect()` in `setupCreationSocket()`**
- `courses.html` lines 2614–2624: `socket.connect()` called twice with identical guard — second call is dead code, causes confusing state
- **Fix:** Deduplicate; use `if (!socket.connected) socket.connect()` once

**AUTO-2 (Critical): `text_input` HTTP timeout too short**
- `app.py` Socket.IO handler: `requests.post(..., timeout=5)` — course creation takes minutes
- If core takes >5s to ACK, exception is caught and silently logged; browser never knows
- **Fix:** Increase timeout to 60s; emit Socket.IO error back to browser on failure

**AUTO-3 (Critical): No ACK to browser on event receipt**
- Browser emits `text_input`, web-ui forwards to core, but no confirmation is sent back
- If core is down, browser sits on spinner forever
- **Fix:** Emit `{'event': 'creation_ack', 'status': 'received'}` on Socket.IO connection after forwarding

**AUTO-4 (Critical): Status update broadcast hits ALL clients**
- `send_status_update()` → web-ui emits `socketio.emit('status_update', data)` with no room/sid — sends to every connected browser
- **Fix:** Emit to specific sid; store initiating sid when `text_input` is received and use `socketio.emit(..., room=initiating_sid)`

**AUTO-5 (High): Thread never joined — orphaned on FSM restart**
- `_creation_pipeline()` runs in `threading.Thread(target=...).start()` — never joined
- If core restarts, thread becomes orphaned; partial writes occur
- **Fix:** Track thread reference; add cleanup handler on shutdown; write `status: "aborted"` if thread killed

**AUTO-6 (High): `finally` block restarts services even when creation fails**
- `_creation_pipeline()` `finally:` always calls `sm.restart_after_ingestion()` and sends `"System Idle"`
- Browser receives `"System Idle"` even after an error — never triggers the "Course Complete!" redirect
- **Fix:** Use a `success` flag; only broadcast completion message on actual success; keep `finally` for cleanup only

**AUTO-7 (High): LLM pre-flight failure leaves no cleanup**
- `SkeletonBuilder._run_preflight_checks()` returns `None` on failure
- Thread continues to `finally:` block, restarts services unnecessarily
- **Fix:** Raise `CourseCreationError` from preflight; catch at pipeline level and emit error status

**AUTO-8 (High): Module LLM failure silently continues with partial data**
- `course_builder.py` module generation: all 3 retries fail → `return None` but no exception raised
- Substructure builder receives empty module list, continues, writes empty course
- **Fix:** Raise `SkeletonBuildError` on retry exhaustion; propagate to pipeline

**AUTO-9 (High): Substructure partial build never aborts — corrupt course written**
- `_build_substructures_progressive()`: unit/lesson/concept LLM failures log and continue
- Empty or stub units/lessons written to `structure.json`
- Course marked `"ready"` with 0-concept lessons
- **Fix:** Count failures; if >50% of concepts fail, abort and mark course `"failed"`; emit error status

**AUTO-10 (High): SQLite insert failure leaves orphaned JSON on disk**
- `storage.py` `create_course()`: JSON written first, SQLite insert in try/except
- If SQLite fails: JSON exists on disk, course invisible to `/api/courses` list query
- **Fix:** Write SQLite row first; only write JSON if SQLite succeeds; or wrap both in a transaction-style check

**AUTO-11 (High): Hydration failures produce stub content with no user indication**
- Per-concept `except` in hydrator increments `failed_count` but sends no `status_update`
- Placeholder text `"Content for X is currently unavailable"` is written as the concept markdown
- User sees course as `"ready"` but content is stubs
- **Fix:** Send `STRUCT:WARN:CONCEPT_STUB:{title}` status on each stub; at end, if `failed_count > 0` log summary

**AUTO-12 (High): Error messages truncated to 80 chars**
- `_creation_pipeline()` exception handler: `self.send_status_update(f"Error: {str(e)[:80]}")`
- Truncated errors make debugging impossible
- **Fix:** Log full error to file logger; send short user-friendly message to browser; include hint to check logs

**AUTO-13 (Medium): Browser pattern matching breaks on slight message variations**
- `courses.html` status handler looks for substrings `"preparing database"`, `"hydrat"`, `"finaliz"` etc.
- Any variation in FSM message text breaks progress bar
- **Fix:** Use structured status events `{"type": "PIPELINE_STAGE", "stage": "SKELETON", "pct": 20}` instead of free-text parsing

**AUTO-14 (Medium): ServiceManager stop/restart during creation blocks all other requests**
- `sm.stop_for_ingestion()` shuts down services; any concurrent requests get 503
- No warning to other connected browsers
- **Fix:** Broadcast `{"type": "MAINTENANCE", "msg": "Course building..."}` to all clients before stopping; restore after

**AUTO-15 (Medium): Status update 5s timeout drops updates under load**
- `send_status_update()` → `requests.post(web_ui_url, timeout=5)` — if web-ui is busy, timeouts silently drop status
- Browser stuck on stale progress
- **Fix:** Increase timeout to 15s; add retry (1 retry) before dropping

---

## FLOW 2: CUSTOM COURSE WIZARD
**Path:** Step 1 (metadata) → Step 2 (modules) → Step 3 (preview via POST `/api/custom_course/preview`) → Finalize (POST `/api/custom_course/create` FormData) → redirect

### Bugs & Fixes

**WIZ-1 (Critical): `zip()` silently truncates module-structure mismatch**
- `librarian.py` line 630: `for module_data, module_spec in zip(structure_data.get('modules', []), modules):`
- If preview generated 2 modules but user sent 3 module specs (e.g. with 3 source files), 3rd is silently dropped
- If LLM renamed a module in preview, source files map to wrong modules
- **Fix:** Validate `len(structure.modules) == len(modules)` before zip; assert title match; log mismatch clearly; use dict keyed by title instead of positional zip

**WIZ-2 (Critical): Source file–to–module mapping is positional only, not by title**
- Frontend sends `source_0`, `source_1` etc.; these are matched to modules by index
- Preview can reorder/rename modules; indices no longer correspond
- **Fix:** Frontend should include `module_title` alongside each file; backend matches by title not index

**WIZ-3 (High): LLM fallback in preview is silent — user sees generic structure**
- `course_builder.py` `generate_preview_for_module()`: if LLM fails for units/lessons/concepts, uses hardcoded fallbacks (`"Unit Fundamentals"`, `["Overview", "Key Principles"]`)
- No indication in preview UI that LLM failed
- **Fix:** Return `"llm_fallback": true` flag per module; frontend shows warning badge on affected modules

**WIZ-4 (High): Hydration failure after structure write = partial course marked `"failed"`**
- `librarian.py` line 738–742: catches hydration error, sets `status: "hydration_failed"`, re-raises
- Course structure exists on disk but half-hydrated
- **Fix:** On hydration failure, attempt partial recovery; mark course `"partial"` (not failed); allow user to retry hydration

**WIZ-5 (High): Uploaded source files never cleaned up on failure**
- `app.py` saves source files to `/app/data/uploads/` before forwarding to RAG
- If RAG returns error or hydration fails, these files remain forever
- **Fix:** Add cleanup in error path: `os.unlink()` each saved file; or use a temp dir with TTL

**WIZ-6 (High): 504 timeout gives ambiguous feedback**
- If RAG takes >300s, web-ui returns 504; frontend shows "course may still be processing"
- User has no way to check actual status
- **Fix:** Add `GET /api/course_status/{uid}` endpoint; frontend polls it after 504; shows definitive success/fail

**WIZ-7 (Medium): Module spec `title` vs structure `title` never validated**
- No check that `modules[i].title == structure.modules[i].title`
- LLM may have subtly renamed module; source files then hydrate wrong module's concepts
- **Fix:** After zip, assert title match; if mismatch, log warning and use structure title

**WIZ-8 (Medium): Source file path not validated before passing to hydrator**
- `app.py` adds `source_file` path to module spec; path is relative to container
- If uploads directory doesn't exist or file was evicted, hydrator gets FileNotFoundError (caught silently)
- **Fix:** Verify file exists and is readable before forwarding to RAG; return 400 if not

**WIZ-9 (Medium): No progress feedback during `create` step**
- After `wizardFinalizeAndCreate()` POSTs to `/api/custom_course/create`, browser waits with spinner
- Socket.IO listener is set up but RAG doesn't emit status updates during wizard creation
- **Fix:** RAG's `create_custom_course_wizard()` should call hydrator with a `status_callback` that posts to web-ui; web-ui emits to Socket.IO

**WIZ-10 (Low): `content_source` radio not re-read from wizard step 1 during finalize**
- `wizardFinalizeAndCreate()` reads `document.querySelector('input[name="wizard_content_source"]:checked')?.value`
- This always reads the current DOM value — if user navigated away and back, it may have been reset
- **Fix:** Store `content_source` in `customCourseWizard.courseData` during step 1 collection (same as `title`, `description`, `teaching_style`)

---

## FLOW 3: EPUB UPLOAD
**Path:** courses.html EPUB modal → POST `/api/upload_epub` → BROKEN

### Bugs & Fixes

**EPUB-1 (Critical): `/api/upload_epub` route returns 400 — completely unimplemented**
- `app.py` lines 506–509: returns `{'error': 'EPUB ingestion is currently handled via CLI'}`, status 400
- Frontend's success handler checks `res.ok` — 400 falls to error path, shows error message
- **Fix:** Implement the full EPUB ingestion flow:
  1. Save uploaded file to `/app/data/uploads/`
  2. Create a course UID
  3. Call `LocalFileProvider` to extract text from EPUB
  4. Pass to `SkeletonBuilder` to generate course structure from EPUB content
  5. Hydrate with `ContentHydrator` using the EPUB as primary source
  6. Return `{course_uid, status: "ready"}` on success

**EPUB-2 (High): Frontend doesn't validate file type before upload**
- EPUB modal accepts `.epub` via `accept=".epub"` on file input, but no JS validation
- Non-EPUB files can still be submitted; backend receives them without type check
- **Fix:** Add JS validation: `if (!file.name.endsWith('.epub')) { showError(...); return; }`

**EPUB-3 (High): No progress feedback during EPUB upload**
- Single POST with no streaming — user sees nothing while large files upload and process
- **Fix:** Use chunked upload or at minimum a progress event on the XHR; connect Socket.IO status updates from the pipeline

**EPUB-4 (Medium): EPUB modal `closeEpubModal()` doesn't reset form**
- If upload fails, next time modal opens, previous file is still selected
- **Fix:** Add `document.getElementById('epub-form').reset()` in `closeEpubModal()`

**EPUB-5 (Medium): No file size limit enforcement**
- Large EPUBs (>100MB) could time out or exhaust memory during extraction
- **Fix:** Add client-side size check (warn >50MB); add server-side limit in Flask `MAX_CONTENT_LENGTH`

---

## FLOW 4: COURSES PAGE → LEARN TAB
**Path:** Course card button → FSM context set → `/learn?course_uid=X` → structure fetch → path render → concept click → FSM session

### Entry Point A: "Start Journey" Direct Link
**Current:** `<a href="/learn?course_uid=${course.uid}">` — navigates directly, FSM context **never set**

**LRN-1 (Critical): "Start Journey" link skips FSM context entirely**
- Direct `<a>` tag: no API call, FSM `active_course_uid` stays `None`
- All `NAVIGATE_TO_TOPIC` events fall back to slow cross-course search
- **Fix:** Replace with `onclick="startCourse('${course.uid}', '${escapeHtml(course.title)}')"` that calls `POST /api/set_active_course` then navigates with `?course_uid=`

**LRN-2 (Critical): `SET_CONTEXT` event not handled in FSM**
- `learn.html` line 1225: sends `SET_CONTEXT` 500ms after load as a safety net
- `fsm_logic.py` `transition()`: no handler for `SET_CONTEXT` — silently dropped
- **Fix:** Add handler before state-specific block:
  ```python
  elif event_type == 'SET_CONTEXT':
      uid = event.get('payload', {}).get('course_uid')
      if uid:
          self.active_course_uid = uid
          try:
              course = self.storage.courses.get_course(uid)
              self.current_teaching_style = course.get('teaching_style', '') if course else ''
          except Exception as e:
              logging.warning(f"SET_CONTEXT meta load failed: {e}")
      return
  ```

### Entry Point B: `setActiveCourse()` via button
**LRN-3 (Critical): `setActiveCourse()` redirects to `/learn` without `course_uid`**
- `courses.html` line 1694: `window.location.href = '/learn'`
- `learn.html` line 1199: redirects back to `/courses` if no `course_uid` param
- Loop: user clicks button → `/learn` → immediately back to `/courses`
- **Fix:** `window.location.href = '/learn?course_uid=' + uid`

**LRN-4 (High): `RESUME_COURSE` only handled in LOBBY state**
- `fsm_logic.py` line 418: `if self.state == 'LOBBY': ... elif event_type == 'RESUME_COURSE'`
- If FSM is in SOCRATIC_LEARNING (mid-session), RESUME_COURSE is dropped
- **Fix:** Make RESUME_COURSE a global handler (like NAVIGATE_TO_TOPIC) that saves current state before switching

### Entry Point C: Concept node click in path view
**LRN-5 (Critical): `active_course_uid` may be `None` when `NAVIGATE_TO_TOPIC` fires**
- `get_concept_details()` line 536: `if self.active_course_uid:` → falls back to cross-course search if None
- Cross-course search is slow (scans all JSON files), may return wrong concept if titles collide
- **Fix:** Enforce SET_CONTEXT before allowing NAVIGATE_TO_TOPIC; if `active_course_uid` still None, return error instead of guessing

**LRN-6 (High): Race condition — user clicks node before structure renders**
- `fetch('/api/course_structure')` is async; user can click before `renderStructure()` completes
- `nodeEl.dataset.uid` is undefined → `NAVIGATE_TO_TOPIC` sent with `topic_id: undefined`
- FSM receives undefined uid, speaks error, falls to LOBBY
- **Fix:** Add loading state on path-view; disable all node clicks until structure fully rendered; show spinner

**LRN-7 (High): `navigatingToNode` flag never reset**
- `learn.html` line 1453: `window.navigatingToNode = true`
- Set on every `enterNode()` call, only cleared in `session.js` `updateChatStream()` when fresh content arrives
- If FSM sends no response (e.g. concept not found), flag stays `true` forever — all subsequent messages suppressed
- **Fix:** Add a 10s timeout: if flag still `true` after 10s, reset it and show error message in chat

**LRN-8 (High): `_save_current_course_progress()` does NOT use atomic write**
- `fsm_logic.py` line 812: `with open(self.state_file, 'w') as f: json.dump(...)` — direct write
- `_atomic_write()` exists at line 573 but isn't used here
- Concurrent requests can corrupt `user_state.json`
- **Fix:** Replace direct write with `self._atomic_write(self.state_file, json.dumps(full_state, indent=2))`

**LRN-9 (High): Back button never notifies FSM — state mismatch**
- `learn.html` back button only toggles DOM visibility; sends no event to FSM
- FSM stays in SOCRATIC_LEARNING while user sees path view
- If voice mode active, microphone still captures input and sends to FSM
- **Fix:** Emit `PAUSE_SESSION` event to FSM on back button; add handler that stops TTS/STT without clearing progress

**LRN-10 (Medium): Course structure renders before progress data loads**
- `renderStructure()` sets node states (completed/current/locked) from `data.structure` which includes completion flags from RAG
- RAG fetches completion from SQLite on every `/api/course_structure` call (N queries for N concepts)
- No caching — large courses (100+ concepts) cause slow renders
- **Fix:** Cache completion data in RAG response; add `Cache-Control: max-age=30` header; invalidate on progress update

**LRN-11 (Medium): Node completion status never refreshed after concept completion**
- When user completes a concept in session view and clicks back, path view still shows old status
- `renderStructure()` only runs once on DOMContentLoaded
- **Fix:** Re-fetch structure and re-render path on `back-to-path-btn-session` click; or emit `CONCEPT_COMPLETED` via Socket.IO and update node class client-side

**LRN-12 (Medium): Page refresh mid-session loses chat history if FSM restarted**
- FSM transcript is in-memory; if core-logic container restarts, transcript is gone
- `_load_course_progress()` restores `current_lesson_node` from `user_state.json` but not `transcript`
- User sees empty chat with no context
- **Fix:** Persist transcript to `user_state.json` alongside other progress; restore on load

**LRN-13 (Low): Both `#back-to-path-btn` and `#back-to-path-btn-session` exist with partial handlers**
- `learn.html` lines 1497–1502: `#back-to-path-btn` has an empty event listener (deprecated comment at line 1497)
- Creates dead code and listener count confusion
- **Fix:** Remove `#back-to-path-btn` and its listener entirely; consolidate on `#back-to-path-btn-session`

---

## PHASE-BY-PHASE IMPLEMENTATION PLAN

### Phase 1 — Critical Flow Fixes (All course types broken without these)

| ID | File | Fix |
|----|------|-----|
| LRN-2 | `fsm_logic.py` | Add `SET_CONTEXT` handler in `transition()` |
| LRN-3 | `courses.html` | Fix `setActiveCourse()` redirect to include `course_uid` |
| LRN-1 | `courses.html` | Replace "Start Journey" `<a>` with `startCourse()` onclick |
| AUTO-6 | `fsm_logic.py` | Fix `finally` block — only send completion on success |
| BUG-4 | `librarian.py` | Set `status: "ready"` after hydration completes |
| EPUB-1 | `app.py` | Implement `/api/upload_epub` route end-to-end |
| WIZ-1 | `librarian.py` | Replace positional `zip()` with title-keyed dict matching |

### Phase 2 — Security & Data Integrity

| ID | File | Fix |
|----|------|-----|
| BUG-7 | `storage.py` | Whitelist column names in all `UPDATE` methods |
| BUG-8 | `storage.py` | Thread-local SQLite connections with `PRAGMA journal_mode=WAL` |
| BUG-9 | `storage.py` | Add missing indexes on `course_uid`, `next_review_date`, `status` |
| LRN-8 | `fsm_logic.py` | Use `_atomic_write()` in `_save_current_course_progress()` |
| AUTO-4 | `app.py` | Scope `status_update` Socket.IO emits to originating client sid |
| Docker | `docker-compose.yml` | Remove `/var/run/docker.sock` mount from core-logic |

### Phase 3 — Library Integrations

| ID | File | Fix |
|----|------|-----|
| BUG-3 | `fsrs_engine.py` | Replace with `py-fsrs` v6 wrapper (`fsrs>=6.0.0`) |
| BUG-3 | `requirements.txt` | Add `fsrs>=6.0.0` |
| LLM-1 | `llm_utils.py` | Add `repair_json()`: trailing commas, single quotes, Python literals |
| LLM-2 | `llm_utils.py` | Add schema validation to `llm_generate_json()` |
| WIZ-3 | `course_builder.py` | Return `llm_fallback` flag when hardcoded fallbacks used |

### Phase 4 — Course Creation Hardening

| ID | File | Fix |
|----|------|-----|
| AUTO-13 | `fsm_logic.py` + `courses.html` | Use structured status objects `{stage, pct}` not free-text strings |
| AUTO-8 | `course_builder.py` | Raise `SkeletonBuildError` on module retry exhaustion |
| AUTO-9 | `course_builder.py` | Abort if >50% concept hydration fails; mark `status: "failed"` |
| AUTO-10 | `storage.py` | Write SQLite row before JSON; verify both on failure |
| AUTO-11 | `course_builder.py` | Emit `STRUCT:WARN:STUB` status per failed concept |
| WIZ-4 | `librarian.py` | Mark partial courses `"partial"` not `"failed"`; allow retry |
| WIZ-5 | `app.py` | Clean up uploaded source files on hydration failure |
| WIZ-8 | `app.py` | Validate source file path exists before forwarding to RAG |
| WIZ-9 | `librarian.py` | Wire `status_callback` into wizard hydration |

### Phase 5 — Learn Tab Hardening

| ID | File | Fix |
|----|------|-----|
| LRN-5 | `fsm_logic.py` | Return explicit error if `active_course_uid` is None on `NAVIGATE_TO_TOPIC` |
| LRN-6 | `learn.html` | Disable node clicks until `renderStructure()` completes; show loader |
| LRN-7 | `learn.html` + `session.js` | Add 10s timeout to reset `navigatingToNode` flag |
| LRN-9 | `learn.html` | Emit `PAUSE_SESSION` to FSM on back button click; add FSM handler |
| LRN-4 | `fsm_logic.py` | Make `RESUME_COURSE` a global handler (not LOBBY-only) |
| LRN-11 | `learn.html` | Re-fetch structure on back navigation to refresh completion status |
| LRN-12 | `fsm_logic.py` | Persist + restore transcript in `user_state.json` |
| LRN-13 | `learn.html` | Remove dead `#back-to-path-btn` and its empty handler |

### Phase 6 — Performance & Reliability

| ID | File | Fix |
|----|------|-----|
| AUTO-5 | `fsm_logic.py` | Track creation thread; write `status: "aborted"` on shutdown |
| AUTO-2 | `app.py` | Increase `text_input` forward timeout to 60s |
| AUTO-15 | `fsm_logic.py` | Increase `send_status_update()` timeout to 15s with 1 retry |
| LRN-10 | `librarian.py` | Cache progress data in structure response; add Cache-Control header |
| WIZ-6 | `app.py` + `librarian.py` | Add `GET /api/course_status/{uid}` polling endpoint |
| PERF-1 | `storage.py` | Replace SELECT+INSERT/UPDATE with `INSERT OR REPLACE` upsert |
| PERF-2 | `course_builder.py` | Replace `difflib.SequenceMatcher` in `_is_duplicate()` with hash bucketing |
| PERF-3 | `storage.py` | Add schema migration system keyed on `schema_version` integer |
| PERF-4 | `app.py` | Wrap `gevent.spawn()` calls with monitored restart wrapper |
| PERF-5 | `fsm_logic.py` | Cap `self.transcript` at 50 entries; cap `conversation_history` at 20 |

### Phase 7 — Docker & Ops

| ID | File | Fix |
|----|------|-----|
| OPS-1 | `docker-compose.yml` | Add `healthcheck` to all services with `depends_on: service_healthy` |
| OPS-2 | `docker-compose.yml` | Change `night_audit` from `restart: always` to `restart: no` |
| OPS-3 | `docker-compose.yml` | Add `restart: on-failure:3` to `qwen-engine` |
| OPS-4 | all `requirements.txt` | Pin all dependency versions |

### Phase 8 — UI & Content Polish

| ID | File | Fix |
|----|------|-----|
| UI-1 | `courses.html` | Escape course titles before inserting as innerHTML (XSS) |
| UI-2 | `schedule.html` | Add error banners when API calls fail; fix race condition on complete button |
| UI-3 | `review.html` | Validate `gradeScore` parameter; fix SM-2 vs FSRS formula mismatch |
| UI-4 | `courses.html` | Fix `setupCreationSocket()` double `socket.connect()` |
| UI-5 | `learn.html` | Remove event listener memory leak on `window resize`; clean up on re-render |
| UI-6 | `courses.html` | Store `content_source` in `customCourseWizard.courseData` at step 1 |
| UI-7 | `learn.html` | Add empty/null guards at every level of `renderStructure()` traversal |
| UI-8 | `courses.html` | `closeEpubModal()` resets the form |
| EPUB-3 | `courses.html` | Add upload progress indicator for EPUB files |
| EPUB-5 | `app.py` | Add `MAX_CONTENT_LENGTH` limit for EPUB uploads |
| LOG-1 | `fsm_logic.py` | Change `logging.info("DEBUG: ...")` to `logging.debug(...)` |

### Phase 9 — Test Coverage

| Target | Test File | What to Test |
|--------|-----------|--------------|
| `SET_CONTEXT` handler | `tests/test_fsm_logic.py` | Handler sets `active_course_uid`; teaching style loaded |
| `RESUME_COURSE` global | `tests/test_fsm_logic.py` | Works from SOCRATIC_LEARNING state, not just LOBBY |
| Atomic write in progress save | `tests/test_storage.py` | Concurrent writes don't corrupt `user_state.json` |
| SQLite upsert | `tests/test_storage.py` | `update_progress()` on new + existing concept |
| EPUB ingestion | `tests/test_epub.py` | Parse EPUB → extract text → create course structure |
| Wizard zip mismatch | `tests/test_course_builder.py` | Module count mismatch handled gracefully |
| JSON repair | `tests/test_llm_utils.py` | All malformed patterns fixed correctly |
| py-fsrs wrapper | `tests/test_fsrs_engine.py` | Correct intervals, retention values, FSRS v6 spec |
| Status polling endpoint | `tests/test_api.py` | `/api/course_status/{uid}` returns correct state |
| Course status transitions | `tests/test_librarian.py` | `building` → `ready` / `partial` / `failed` |

---

## COMPLETE FILE MODIFICATION LIST

| File | Changes |
|------|---------|
| `services/core/fsm_logic.py` | SET_CONTEXT handler; RESUME_COURSE global; PAUSE_SESSION handler; atomic progress save; transcript cap; remove duplicate list_courses; fix bare excepts; debug log levels |
| `services/core/fsrs_engine.py` | Full replacement with py-fsrs v6 wrapper |
| `services/core/requirements.txt` | Add `fsrs>=6.0.0` |
| `services/core/course_builder.py` | Raise errors on retry exhaustion; abort on >50% failure; `llm_fallback` flag; fix title dedup hash; remove gc.collect anti-pattern; singleton storage |
| `services/core/content_provider.py` | EPUB zip resource fix; numpy shape/norm guard; singleton storage |
| `services/common/storage.py` | Thread-local SQLite; column whitelists; SQL indexes; upsert; os.path.join everywhere; schema migrations |
| `services/common/llm_utils.py` | `repair_json()`; schema validation |
| `services/common/prompts.py` | Prompt injection sanitization |
| `services/rag/librarian.py` | Fix status `building→ready`; title-keyed wizard matching; `status_callback` in wizard hydration; `/api/course_status/{uid}` endpoint; bare except fixes |
| `services/web-ui/app.py` | Implement `/api/upload_epub`; fix `text_input` timeout; scope Socket.IO emits to sid; greenlet monitoring; `/api/course_status/{uid}` proxy; `EPUB MAX_CONTENT_LENGTH` |
| `services/web-ui/templates/courses.html` | `startCourse()` function; fix `setActiveCourse()` redirect; double `socket.connect()` fix; XSS escape titles; store `content_source` in step 1; `closeEpubModal()` reset; EPUB progress; structured status objects |
| `services/web-ui/templates/learn.html` | Remove dead back button; `navigatingToNode` timeout; disable nodes until render; re-fetch on back nav; null guards in renderStructure; event listener cleanup |
| `services/web-ui/templates/review.html` | `gradeScore` validation; FSRS formula fix |
| `services/web-ui/templates/schedule.html` | Error banners; completion race condition fix |
| `docker-compose.yml` | Health checks; restart policies; remove docker.sock; dependency ordering |
| `tests/` | 10 new/expanded test files |
