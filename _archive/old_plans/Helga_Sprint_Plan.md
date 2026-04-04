# Helga Socratic Tutor — Comprehensive Audit & Sprint Plan

**Platform: Mac Mini M4 Pro 24GB | Model: Qwen 3 14B Q4_K_M via Ollama**
**Sprint Target: 80–120 hours | Date: March 30, 2026**

---

## PART 1: CODEBASE AUDIT — BUGS, MISSING FEATURES & DEAD CODE

### 1.1 Critical Bugs

| # | File | Line/Area | Bug | Severity |
|---|------|-----------|-----|----------|
| B1 | `services/core/fsm_logic.py` | `handle_socratic_answer()` regex | `r'"grade":\s*"Grade\s*(\d)"'` replacement uses `\x01` (SOH byte) instead of `\1` backreference — grade parsing silently fails, defaults to 3 every time | **CRITICAL** |
| B2 | `services/core/fsm_logic.py` | `handle_flashcard_answer()` | Same `\x01` backreference bug in grade regex | **CRITICAL** |
| B3 | `services/core/fsm_logic.py` | `handle_global_commands()` | References undefined variable `event` on line with `TOGGLE_TTS` — `event.get('payload')` will crash since only `event_type` and `text` are in scope | **CRITICAL** |
| B4 | `services/core/fsm_logic.py` | `transition()` LOBBY state | Duplicate `elif "list" in text: self.list_courses()` on consecutive lines | Minor |
| B5 | `main.py` | `check_prerequisites()` | Two consecutive `return all_ok` statements — second is unreachable dead code | Minor |
| B6 | `services/inference-llm/server.py` | `MODEL_ID` | Hardcoded to `"Qwen/Qwen2.5-0.5B-Instruct"` despite comments/docs saying 3B GPTQ — wrong model loaded | **CRITICAL** |
| B7 | `services/web-ui/app.py` | routes | 15+ API routes referenced by frontend JS/templates have no backend handler (see §1.2) | **HIGH** |
| B8 | `services/core/fsm_logic.py` | `__init__` | `self.speak()` and `self.start_listening()` called in constructor — these fire HTTP requests to services that may not be ready yet, causing startup race conditions | HIGH |
| B9 | `services/core/fsm_logic.py` | `handle_socratic_answer()` grade==4 | Skips the NEXT syllabus item (`syllabus_queue.pop(0)`) then calls `next_syllabus_item()` which pops ANOTHER — so grade 4 skips TWO items, not one | HIGH |
| B10 | `services/web-ui/templates/learn.html` | `enterSession()` | References `textOnlyToggle` element (`document.getElementById("text-only-toggle")`) which doesn't exist in the template HTML | Medium |
| B11 | `services/core/fsm_logic.py` | DB connection | Entire KuzuDB block commented out with `self.conn = None` — core-logic has zero direct DB access | By design |
| B12 | `services/web-ui/static/js/session.js` | Line 1 | `window.socket = io()` creates socket immediately on script load, then `DOMContentLoaded` creates ANOTHER `socket = io()` — double connection | Medium |
| B13 | `services/audio/Dockerfile` | Piper download | Downloads `piper_arm64.tar.gz` (ARM64/Jetson) — will fail on Mac x86/ARM mismatch | **CRITICAL for migration** |

### 1.2 Missing Routes (Frontend references → no backend handler)

These routes are called by templates/JS but have no handler in `services/web-ui/app.py`:

| Route | Referenced By | Purpose |
|-------|--------------|---------|
| `GET /review` | `base.html` nav | Review page render |
| `GET /test` | `base.html` nav | Test page render |
| `GET /palace` | `base.html` nav | Memory Palace render |
| `GET /status` | `base.html` nav | Status page render |
| `GET /course/<uid>/structure` | `courses.html` View button | Course structure page |
| `POST /api/set_active_course` | `courses.html` Start button | Set active course before redirect |
| `DELETE /api/delete_course` | `courses.html` delete button | Delete course |
| `POST /api/upload_epub` | `courses.html` EPUB form | EPUB upload |
| `GET /api/courses` | `courses.html`, `learn.html` | Course list (should proxy to RAG) |
| `GET /api/voices` | `session.js` | Voice list for TTS selector |
| `POST /api/check_sudo` | `session.js` | Sudo password check |
| `POST /api/set_sudo` | `session.js` | Sudo password set |
| `GET /api/quiz` | `test.html` | Quiz questions |
| `POST /api/quiz/grade` | `test.html` | Grade quiz answer |
| `POST /api/run_tests` | `test.html` | Run test suite |
| `POST /api/update_card` | `review.html` | Update SR card |
| `POST /anchor` | `memory_palace.html` | Anchor concept |

### 1.3 Dead Code / Removing Voice Interaction

These files/functions are ENTIRELY related to voice and should be removed or gutted:

| Target | What to Remove |
|--------|---------------|
| `services/input/` (entire directory) | Whisper STT, VAD, keyboard listener — all voice input |
| `services/audio/` (entire directory) | Piper TTS, earcon mixer — all voice output |
| `services/tts-qwen/` (entire directory) | Qwen TTS engine — unused alternative TTS |
| `services/inference-stt/` (entire directory) | STT inference server |
| `services/inference-llm/` (entire directory) | Custom HF Transformers server — replacing with Ollama |
| `services/web-ui/app.py` | `connect_to_stt_service()`, `connect_to_audio_service()`, all WebRTC/audio socket handlers, STT client, audio client globals |
| `services/web-ui/static/js/session.js` | ~400 lines: all WebRTC functions, `startMicrophone()`, `stopMicrophone()`, `playAudioChunk()`, `processAudioQueue()`, `stopPlayback()`, audio context setup, volume slider logic, mic toggle logic, `ensurePlaybackAudioContext()` |
| `services/web-ui/templates/learn.html` | Mic toggle button, speaker toggle button, volume control div, audio visualizer, STT preview element, all audio-related CSS |
| `services/core/fsm_logic.py` | `speak()` method (replace with transcript-only append), `play_sound()`, `stop_audio()`, `start_listening()`, all `self.audio_url`/`self.stt_url`/`self.vad_url` refs, TTS toggle handlers, voice settings, earcon references |
| `docker-compose.yml` | `input-node`, `audio-engine`, `qwen-engine` (LLM) service definitions, `/dev/snd` device mounts, PulseAudio volumes, NVIDIA runtime declarations |
| `services/common/prompts.py` | Architect prompt (Memory Palace), vividness prompt |
| `configs/alsa_monitor.conf`, `configs/pipewire.conf` | Audio config files |

### 1.4 Content Hydration — Removing Wikipedia/ZIM/Kolibri

These files implement the Wikipedia ZIM scanning and Kolibri content pipeline being replaced with LLM-generated content:

| Target | Action |
|--------|--------|
| `services/core/content_provider.py` | **DELETE** — `ZimProvider`, `KolibriProvider`, `EnsembleProvider` all removed |
| `services/core/course_builder.py` | **REWRITE** — `ContentHydrator` currently searches ZIM/Kolibri; replace with LLM-only content generation with self-consistency verification |
| `services/rag/librarian.py` | Remove `libzim` import, `ZIM_PATH`, `zim_file` global, all ZIM search logic |
| `tools/ingest.py`, `tools/ingest_simple.py`, `tools/epub_ingest.py` | **DELETE** — ZIM/EPUB ingestion tools |
| `docker-compose.yml` | Remove ZIM volume mount (`/app/data/zim`), Kolibri volume mount |
| `services/core/Dockerfile` | Remove `libzim-dev` from apt-get |
| `services/rag/Dockerfile` | Remove `libzim-dev` from apt-get, `libzim` from requirements |
| `courses.html` | Remove "Upload EPUB" button and modal |

---

## PART 2: ARCHITECTURAL CHANGES

### 2.1 Platform Migration: Jetson → Mac Mini M4 Pro 24GB

**Current state:** 6 Docker containers designed for Jetson Orin Nano (8GB, NVIDIA CUDA/TensorRT, ARM64).

**Target state:** 3 Docker containers + native Ollama on Mac Mini M4 Pro 24GB (Apple Silicon, no CUDA).

#### New Architecture

```
┌─────────────────────────────────────────────────┐
│  Mac Mini M4 Pro 24GB                            │
│                                                   │
│  ┌──────────┐  Native (not Docker)                │
│  │  Ollama   │  Qwen 3 14B Q4_K_M (~9.5GB)       │
│  │  :11434   │  ~20-25 tok/s on M4 Pro            │
│  └──────────┘                                     │
│                                                   │
│  ┌─── Docker Compose ───────────────────────┐    │
│  │                                           │    │
│  │  ┌─────────┐  ┌───────────┐  ┌────────┐ │    │
│  │  │ web-ui  │  │core-logic │  │  rag   │ │    │
│  │  │ :5000   │→ │ :5003     │→ │ :5002  │ │    │
│  │  │ Flask   │  │ FSM+API   │  │ SQLite │ │    │
│  │  │ SocketIO│  │           │  │ +vec   │ │    │
│  │  └─────────┘  └───────────┘  └────────┘ │    │
│  └───────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

#### Key Decisions

**Model: Qwen 3 14B Q4_K_M** — Research confirms this is the Mac Mini M4 Pro 24GB sweet spot. At ~9.5GB model file, it leaves ~14GB for OS + KV cache + Docker. Delivers dramatically better Socratic behavior, JSON compliance, and grading accuracy vs the current 0.5B model. Generates 20–25 tok/s on M4 Pro (273 GB/s bandwidth). Fallback: Qwen 3 8B if memory is tight.

**LLM Serving: Native Ollama (not Docker)** — Ollama on macOS accesses Metal GPU acceleration natively. Dockerized Ollama on Mac loses GPU access. Run Ollama as a background service, Docker services connect to `host.docker.internal:11434`.

**Database: SQLite + sqlite-vec (replacing KuzuDB)** — KuzuDB causes persistent file-lock contention between rag-engine and core-logic (currently "solved" by commenting out core-logic's DB connection). SQLite with sqlite-vec extension provides: single-file database (no lock contention with WAL mode), native vector search for embeddings, standard SQL for relational queries + graph-like traversals via recursive CTEs, 30MB memory footprint, battle-tested concurrent read access. The knowledge graph hierarchy (Course→Module→Unit→Lesson→Concept) maps naturally to foreign-key parent_id columns with recursive CTE queries replacing Cypher traversals.

### 2.2 New Database Schema (SQLite + sqlite-vec)

```sql
-- Core hierarchy
CREATE TABLE courses (
    uid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    overview TEXT,
    status TEXT DEFAULT 'active',
    created_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE concepts (
    uid TEXT PRIMARY KEY,
    course_uid TEXT NOT NULL REFERENCES courses(uid) ON DELETE CASCADE,
    parent_uid TEXT REFERENCES concepts(uid),
    title TEXT NOT NULL,
    resource_text TEXT,
    bloom_level INTEGER DEFAULT 1,
    ordinal INTEGER DEFAULT 0,
    depth_level INTEGER DEFAULT 0,  -- 0=module, 1=unit, 2=lesson, 3=concept
    completed BOOLEAN DEFAULT 0,
    stability REAL DEFAULT 0.0,
    difficulty REAL DEFAULT 5.0,
    due_date INTEGER DEFAULT 0,
    last_review INTEGER DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    misconceptions TEXT DEFAULT '[]',
    analogies TEXT DEFAULT '[]',
    FOREIGN KEY (course_uid) REFERENCES courses(uid)
);

CREATE TABLE concept_embeddings (
    concept_uid TEXT PRIMARY KEY REFERENCES concepts(uid) ON DELETE CASCADE,
    embedding BLOB
);

CREATE TABLE prerequisites (
    from_uid TEXT NOT NULL REFERENCES concepts(uid) ON DELETE CASCADE,
    to_uid TEXT NOT NULL REFERENCES concepts(uid) ON DELETE CASCADE,
    strength REAL DEFAULT 1.0,
    PRIMARY KEY (from_uid, to_uid)
);

CREATE TABLE interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_uid TEXT REFERENCES concepts(uid),
    course_uid TEXT REFERENCES courses(uid),
    user_input TEXT,
    tutor_response TEXT,
    grade INTEGER,
    bloom_level INTEGER,
    response_time_ms INTEGER,
    timestamp INTEGER DEFAULT (unixepoch())
);

CREATE TABLE session_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

SELECT vector_init('concept_embeddings', 'embedding', 'type=FLOAT32,dimension=384');
```

**Migration query equivalents:**

| KuzuDB Cypher (current) | SQLite equivalent |
|--------------------------|-------------------|
| `MATCH (c:Course)-[:HAS_MODULE\|HAS_UNIT\|...]->(con:Concept)` | `SELECT * FROM concepts WHERE course_uid = ? ORDER BY depth_level, ordinal` |
| `MATCH (c:Concept {uid: $uid})` | `SELECT * FROM concepts WHERE uid = ?` |
| Flat syllabus traversal | `SELECT * FROM concepts WHERE course_uid = ? AND depth_level = 3 ORDER BY ordinal` |
| Vector similarity search | `SELECT c.*, v.distance FROM concepts c JOIN vector_quantize_scan(...) v ON c.uid = v.rowid` |

### 2.3 LLM-Generated Content (Replacing ZIM/Kolibri)

The new `ContentHydrator` generates all educational content via the 14B model with accuracy safeguards:

**Generation pipeline per concept:**
1. **Generate** lesson content with structured prompt including topic, parent context, learning objectives, target Bloom's level
2. **Self-consistency check** — generate 3 versions at temperature 0.7, extract key factual claims from each, keep only claims present in ≥2/3 versions
3. **Confidence flagging** — if consistency < 66% on a claim, prepend with hedging language or flag for user verification
4. **Misconception generation** — separate prompt: "What are 3 common misconceptions about [concept]?"
5. **Analogy generation** — separate prompt: "Provide 2 analogies for explaining [concept] to a beginner"
6. **Embed** final text with sentence-transformers for vector search

### 2.4 LLM Communication Overhaul

**Current:** Core-logic sends raw prompts to `/v1/completions` endpoint on custom HF Transformers Flask server, using Llama-2 prompt format (`<|begin_of_text|>` tags).

**New:** Core-logic sends requests to Ollama's OpenAI-compatible API at `host.docker.internal:11434`.

**Changes to `services/core/fsm_logic.py`:** Create centralized `llm_chat()` helper:

```python
def llm_chat(self, system_prompt, user_message,
             max_tokens=256, temperature=0.6,
             json_mode=False):
    payload = {
        "model": "qwen3:14b",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False
    }
    if json_mode:
        payload["format"] = "json"
    resp = requests.post(
        f"{self.llm_url}/v1/chat/completions",
        json=payload, timeout=60)
    return resp.json()["choices"][0]["message"]["content"]
```

**All 9 LLM call sites to update:**
1. `ask_socratic_question()` — Socratic question generation
2. `handle_socratic_answer()` — grading (`json_mode=True`)
3. `next_syllabus_item()` — bridge generation
4. `next_card()` — examiner question
5. `handle_flashcard_answer()` — flashcard grading (`json_mode=True`)
6. `handle_flashcard_answer()` — hint generation
7. `place_concept()` — vividness prompt (if keeping palace)
8. `course_builder.py` `llm_generate()` — course skeleton
9. `course_builder.py` content hydration

**Prompt format overhaul:** Remove all `<|begin_of_text|><|start_header_id|>` Llama-format tokens from `services/common/prompts.py`. Convert all prompt functions to return `(system_message, user_message)` tuples. Ollama handles chat templating.

### 2.5 Streaming Responses (New Feature)

Currently all LLM responses are buffered — user sees nothing for 3-8 seconds. Adding streaming:

1. Core-logic calls Ollama with `"stream": True`, reads chunked response
2. Forwards each token chunk to web-ui via `POST /api/stream_token`
3. Web-ui broadcasts via Socket.IO `stream_token` event
4. Frontend renders tokens incrementally into current AI chat bubble
5. `requestAnimationFrame` batches renders every 30-60ms

---

## PART 3: FEATURE IMPLEMENTATION PLAN

### 3.1 Learn Tab — Text-Only Socratic Chat

**Specific UI changes to `learn.html`:**

| Element | Current | Action |
|---------|---------|--------|
| `#mic-toggle` button | Mic on/off | **REMOVE** |
| `#speaker-toggle` button | TTS on/off | **REMOVE** |
| `.mic-container` div | Mic + status emoji | **REMOVE** |
| `#volume-slider` + label | Volume control | **REMOVE** |
| `.volume-control` div | Volume container | **REMOVE** |
| `#stt-preview` div | "Listening..." overlay | **REMOVE** |
| `.audio-visualizer` div | Audio waveform | **REMOVE** |
| `#audio-status` div | "Audio Ready" | **REMOVE** |
| `#pause-resume` button | Pause/Resume | KEEP |
| `.ai-avatar-container` | Large avatar header taking ~120px | **REDESIGN** — collapse to small inline indicator |
| `#text-input` | Single-line input | **UPGRADE** — auto-growing textarea |
| `#send-btn` | Small circle button | **UPGRADE** — prominent send button |
| Sudo password modal | For course creation | **REMOVE** (no longer needed on Mac) |

**New UI elements to ADD:**

| Element | Purpose |
|---------|---------|
| Typing indicator | Animated dots while LLM generates |
| Streaming text | Tokens appear word-by-word in AI bubble |
| Grade badge | 🟢🟡🔴 appears on AI response after grading |
| Hint accordion | 3-tier progressive reveal below questions |
| Quick-reply chips | "I don't know", "Give me a hint", "Next topic" |
| Concept progress | Inline bar showing Bloom's level per concept |
| Session summary | End-of-session card: concepts, grades, time |

### 3.2 FSRS Spaced Repetition (Proper Implementation)

**Current state:** `FSRSEngine` class exists (50 lines) but is never called. `handle_flashcard_answer()` hardcodes `stability: 10.0` and ignores the engine.

**Implementation:**
1. Replace `fsrs_engine.py` with `py-fsrs` library wrapper
2. Store FSRS state per concept in SQLite `concepts` table
3. Auto-create review entries after Socratic interactions
4. Review tab fetches concepts where `due_date <= now()`
5. Infer FSRS ratings from dialogue (not self-assessment buttons):
   - Rating 1: Can't answer / "I don't know"
   - Rating 2: Partially correct after hints
   - Rating 3: Correct after some guidance
   - Rating 4: Immediately correct with explanation
6. Review page uses same chat UI as Learn, different concept source

### 3.3 Bloom's Taxonomy Integration

1. Track `bloom_level` per concept in SQLite
2. Modify Socratic prompts to target current Bloom's level with appropriate question stems
3. Mastery thresholds: L1-2: 90%/5 items, L3-4: 80%/4 items, L5-6: 70%/3 items
4. Require 2 consecutive correct before advancing level
5. On failure at Level N, drop to Level N-1 (not Level 1)

### 3.4 Tab Integration Strategy

| Tab | Purpose | Integration |
|-----|---------|-------------|
| **Home** | Dashboard, stats, resume | Due review count, streak, active course |
| **Courses** | Browse, create, delete | LLM content pipeline on create |
| **Learn** | Socratic dialogue (syllabus order) | Primary learning flow |
| **Test** | Adaptive quiz (weakness-ordered) | Cross-course, Bloom's-aligned |
| **Review** | Spaced repetition (FSRS due) | Same chat UI, different concept source |
| **Status** | System health | Keep, remove dead services |

**Remove:** Palace tab (unfinished, no 3D, adds complexity without learning value).

---

## PART 4: SPRINT EXECUTION PLAN

### Phase 1: Infrastructure (12-16 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 1.1 Delete dead services | 2 | Delete `services/input/`, `services/audio/`, `services/tts-qwen/`, `services/inference-stt/`, `services/inference-llm/` | Remove all 5 voice/TTS/STT/LLM services |
| 1.2 Strip docker-compose.yml | 1 | `docker-compose.yml` | Remove `input-node`, `audio-engine`, `qwen-engine`, `night_audit`. Remove NVIDIA runtime, CUDA env vars, `/dev/snd`, PulseAudio volumes. Add `extra_hosts: ["host.docker.internal:host-gateway"]` for Ollama |
| 1.3 Rewrite Dockerfiles | 2 | Core + RAG + Web-UI Dockerfiles | Remove `libzim-dev`, NVIDIA base images. Use `python:3.11-slim` for all. Remove ARM-specific downloads |
| 1.4 Create SQLite schema | 3 | New: `services/rag/schema.sql`, Modified: `services/rag/librarian.py` | Full SQLite + sqlite-vec schema. Migration utility if preserving data |
| 1.5 Setup Ollama integration | 2 | `services/core/fsm_logic.py` | Add `llm_chat()` helper. Set `self.llm_url = "http://host.docker.internal:11434"`. Test connectivity |
| 1.6 Update requirements.txt | 2 | All requirements files | Remove: `kuzu`, `libzim`, `zimply`, `pyaudio`, `webrtcvad`, `silero-vad`, `aiortc`, `piper-tts`, `faster-whisper`. Add: `fsrs`, `sqlite-vec` |

### Phase 2: Core Logic Overhaul (20-28 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 2.1 Strip voice from FSM | 4 | `fsm_logic.py` | Remove `speak()` → `add_message()`, `play_sound()`, `stop_audio()`, `start_listening()`. Remove all audio/stt/vad URLs. Remove `TOGGLE_MIC`, `TOGGLE_TTS`, `SPEECH_DETECTED` handlers. Remove `voice_id`, `tts_enabled` |
| 2.2 Fix critical bugs | 2 | `fsm_logic.py` | Fix B1/B2 (regex `\x01`→`\\1`), B3 (`event` undefined), B4 (duplicate elif), B9 (double-skip grade 4) |
| 2.3 Rewrite prompts | 4 | `services/common/prompts.py` | Remove Llama-2 tokens. Return `(system, user)` tuples. Add Bloom's level. Add JSON schema to grading. Remove architect/vividness prompts |
| 2.4 Convert LLM calls | 4 | `fsm_logic.py` | Update all 9 call sites to `self.llm_chat()` via Ollama. Use `json_mode=True` for grading. Increase `max_tokens` to 256 |
| 2.5 Implement FSRS | 4 | `fsm_logic.py`, new: `fsrs_service.py` | Install `py-fsrs`. Create wrapper. Call after each graded interaction. Replace hardcoded `stability: 10.0` |
| 2.6 Add Bloom's tracking | 3 | `fsm_logic.py`, `prompts.py` | Track per-concept bloom_level. Modify question generation. Advance on consecutive mastery |
| 2.7 Implement streaming | 4 | `fsm_logic.py`, `web-ui/app.py` | Streaming LLM calls. Core→web-ui SSE chunks. Web-ui→browser Socket.IO |
| 2.8 SQLite state persistence | 3 | `fsm_logic.py` | Replace JSON file state with `session_state` table |

### Phase 3: RAG Engine Rewrite (12-16 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 3.1 KuzuDB → SQLite | 6 | `services/rag/librarian.py` | Full rewrite of all `kuzu` → `sqlite3`. sqlite-vec for vectors. WAL mode. Rewrite `/search`, `/api/courses`, `/api/stats`, `/api/course_structure`, `/flat_syllabus` |
| 3.2 Rewrite course_builder | 6 | `services/core/course_builder.py` | Remove ZimProvider/KolibriProvider. LLM-only content gen with 3-pass self-consistency. Misconception + analogy generation. Write to SQLite |
| 3.3 New RAG endpoints | 2 | `services/rag/librarian.py` | Add: `/api/due_concepts`, `/api/concept_details`, `/api/update_mastery`, `/api/course_tree` |
| 3.4 Embedding pipeline | 2 | `services/rag/librarian.py` | Embed with `all-MiniLM-L6-v2`. Store via sqlite-vec |

### Phase 4: Web UI Overhaul (20-28 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 4.1 Strip voice from learn.html | 3 | `learn.html` | Remove: mic, speaker, volume, audio viz, STT preview, sudo modal |
| 4.2 Redesign chat interface | 6 | `learn.html`, `style.css` | Typing indicator, streaming render, grade badges, hint accordion, quick-reply chips, auto-growing textarea, mobile responsive |
| 4.3 Rewrite session.js | 6 | `session.js` | Delete ~400 lines audio/WebRTC. Fix B12 double socket. Add: streaming handler, typing indicator, hint accordion, quick-reply chips, grade badges |
| 4.4 Add missing routes | 3 | `web-ui/app.py` | Add handlers: `/review`, `/test`, `/status`, `/course/<uid>/structure`, `/api/courses`, `/api/set_active_course`, `/api/delete_course`. Remove audio handlers |
| 4.5 Review tab | 3 | `review.html` | Match Learn chat UI. Pull from `/api/due_concepts`. Show "X concepts due" header |
| 4.6 Test tab | 3 | `test.html` | Adaptive quiz: weakest concepts across courses. MC + free response. Score display |
| 4.7 Home page updates | 2 | `home.html` | Add review count card. Fix stats loading. Remove Palace card |
| 4.8 Course structure viz | 2 | `course_structure.html` | Cytoscape.js node graph. Color by mastery. Click-to-navigate |
| 4.9 Clean base.html | 1 | `base.html` | Remove Palace nav link. Verify all nav routes work |
| 4.10 Clean courses.html | 1 | `courses.html` | Remove EPUB upload button + modal. Remove ZIM source badges |

### Phase 5: Performance & Polish (8-12 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 5.1 Response caching | 3 | `fsm_logic.py` | LRU cache on `llm_chat()` for identical prompts. Cache bridges |
| 5.2 Pre-generation | 2 | `course_builder.py` | Pre-gen 2-3 Socratic questions per concept during creation |
| 5.3 Prompt compression | 2 | `prompts.py` | Keep total under 2000 tokens. Summarize history to last 3 turns |
| 5.4 Clean up main.py | 1 | `main.py` | Update for Mac. Remove sudo. Remove dead services from SERVICES dict |
| 5.5 Testing | 4 | Multiple | E2E testing all tabs. SQLite migration verification. Edge cases |

---

## PART 5: FILE-LEVEL CHANGE MANIFEST

### Files to DELETE

```
services/input/              (entire directory)
services/audio/              (entire directory)
services/tts-qwen/           (entire directory)
services/inference-stt/       (entire directory)
services/inference-llm/       (entire directory)
services/core/content_provider.py
services/web-ui/templates/memory_palace.html
services/web-ui/templates/_archive/
tools/ingest.py
tools/ingest_simple.py
tools/epub_ingest.py
tools/generate_earcons.py
tools/mock_channel.sqlite3
tools/benchmark_jetson.sh
configs/alsa_monitor.conf
configs/pipewire.conf
_archive/                    (48MB of test DBs)
plans/webrtc_audio_plan.md
scripts/mnemosyne-fan-control.py
```

### Files to REWRITE (>50% changed)

```
services/core/fsm_logic.py
services/rag/librarian.py
services/core/course_builder.py
services/common/prompts.py
services/web-ui/app.py
services/web-ui/static/js/session.js
services/web-ui/templates/learn.html
docker-compose.yml
```

### Files to MODIFY (<50% changed)

```
services/web-ui/templates/base.html
services/web-ui/templates/home.html
services/web-ui/templates/courses.html
services/web-ui/templates/review.html
services/web-ui/templates/test.html
services/web-ui/templates/status.html
services/web-ui/templates/course_structure.html
services/web-ui/static/css/style.css
services/web-ui/static/js/status.js
services/core/Dockerfile
services/rag/Dockerfile
main.py
```

### Files to CREATE

```
services/rag/schema.sql
services/core/fsrs_service.py
services/core/llm_client.py
```

---

## PART 6: SPRINT SCHEDULE

| Day | Phase | Focus | Hours |
|-----|-------|-------|-------|
| 1-2 | Phase 1 | Delete dead code, Docker setup, SQLite schema, Ollama | 12-16 |
| 3-5 | Phase 2 | Core logic: fix bugs, strip voice, prompts, LLM calls | 20-28 |
| 6-7 | Phase 3 | RAG engine: SQLite rewrite, course builder, embeddings | 12-16 |
| 8-11 | Phase 4 | Web UI: strip voice, redesign chat, routes, all tabs | 20-28 |
| 12-13 | Phase 5 | Caching, streaming, testing, bug fixes | 8-12 |
| **Total** | | | **72-100 hrs** |

---

## PART 7: KEY RESEARCH FINDINGS

**Model selection:** Qwen 3 14B Q4_K_M is the consensus Mac Mini M4 Pro 24GB sweet spot. At ~9.5GB it leaves ~14GB headroom. Dramatically fewer hallucinations than 7-8B models. 20-25 tok/s on M4 Pro.

**Socratic pedagogy:** Khanmigo enforces "never give the answer" via structural constraints, not just instructions. ETH Zurich's StratL system (ACL 2025) proves prompting alone is insufficient — a transition-graph dialogue manager is needed. The FSM already provides this; it needs better prompts and Bloom's integration.

**FSRS v6:** 99.6% superiority over SM-2, requires 20-30% fewer reviews. Track per concept, not per flashcard. Infer ratings from dialogue quality.

**Content accuracy:** LLMs cannot reliably self-correct reasoning (ICLR 2024). Self-consistency (majority voting across 3 generations) catches 60-70% of factual errors without external knowledge.

**Database:** KuzuDB's file-locking (documented in codebase comments) is fundamental to its architecture with multiple containers. SQLite WAL mode supports concurrent readers natively. sqlite-vec provides vector search at 30MB overhead. Eliminates the most persistent reliability issue.

**Streaming:** Users perceive streaming as 40% faster even when total time is identical. Critical for 14B model generating 6-12 seconds of content.

**Bloom's Taxonomy:** Research from AIED 2024 shows the most effective LLM question generation strategy combines chain-of-thought with explicit Bloom's level definitions and few-shot examples per level.

---

## PART 8: COMPREHENSIVE UI AUDIT — USER FLOW ANALYSIS

### 8.1 Current User Flow Walkthrough (Every Screen)

#### Flow 1: Home → First Visit (New User)

**What happens:** User lands on `/`. Sees "Welcome to Helga" hero, 3 stat cards (Courses: 0, Concepts Mastered: 0, Day Streak: 0), and 5 feature cards.

**Issues found:**
- **I1:** Stats fetch fails silently — if backend is down, stats just show 0 with no error feedback
- **I2:** "Active Session" card says "Jump right back into your learning flow" even when no session exists — misleading
- **I3:** Feature cards link to `/session` (dead route), `/palace` (unbuilt), and pages with no backend routes
- **I4:** No onboarding — new user has zero guidance on what to do first. Should direct to course creation
- **I5:** "Memory Palace" card promises "persistent 3D mental space" — feature doesn't exist
- **I6:** No visual hierarchy — all 5 cards look identical. The primary action (create first course) is buried
- **I7:** Stat cards show "Day Streak: 0" but streak tracking isn't implemented anywhere in the backend

#### Flow 2: Courses → Create a Course

**What happens:** User clicks Courses nav → sees page header + "Create by Topic" and "Upload EPUB" buttons → clicks "Create by Topic" → modal with topic input + depth selector → submits → animation modal with cube loader, live tree, system logs.

**Issues found:**
- **I8:** "Upload EPUB" button has no backend handler — clicking opens modal, but form submission goes to `/api/upload_epub` which returns 404
- **I9:** Course creation modal emits `socket.emit('text_input', ...)` but status.js also creates a `socket = io()` (third socket connection on this page alongside session.js's two). Three competing socket connections
- **I10:** Progress tree references ZIM source badges (📚 Wiki, 🤖 AI) that are being removed
- **I11:** Course depth selector says "1-5" but `SkeletonBuilder` only uses the depth to set `max_depth` in a loop that creates 2 modules regardless — depth selection is cosmetic
- **I12:** No way to cancel course creation once started — modal has no close button during progress
- **I13:** After creation success, redirects to `/learn` but doesn't set active course — user lands in lobby with no course selected
- **I14:** Course cards show "0% Complete" with progress bar, but progress is never calculated from backend data
- **I15:** Delete button calls `/api/delete_course` (no handler). Course deletion is non-functional
- **I16:** "View" button navigates to `/course/${uid}/structure` (no route handler)
- **I17:** "Start/Resume" button calls `/api/set_active_course` (no handler)

#### Flow 3: Learn → Socratic Session

**What happens:** User clicks Learn → lobby overlay with course selector + "Start Session" button → selects course → lobby hides, chat interface revealed → AI avatar + chat stream + controls bar with mic/speaker/volume/text input.

**Issues found:**
- **I18:** Lobby course selector calls `/api/courses` (no handler in web-ui/app.py) — dropdown stays empty
- **I19:** `enterSession()` calls `window.sendEvent("TOGGLE_TEXT_ONLY", ...)` with `textOnlyToggle.checked` but `textOnlyToggle` element doesn't exist — throws `TypeError: Cannot read properties of null`
- **I20:** Chat stream starts with hardcoded "Hello Brennan" — should be dynamic or generic
- **I21:** AI avatar takes ~120px vertical space showing a broken image (`avatar_placeholder.png` doesn't exist in static/img/) and "Audio Ready" status
- **I22:** Controls bar has 6 elements (speaker, mic+status, text input, send, pause) — 3 of them are voice-only and being removed
- **I23:** `session.js` creates socket on line 1 AND in DOMContentLoaded — double connection with duplicate event handlers
- **I24:** No typing indicator while LLM generates response (3-8 seconds of nothing)
- **I25:** No streaming — responses appear all at once after full generation
- **I26:** Chat bubbles have contenteditable="true" for user messages (edit-in-place) but the edit event sends to FSM which doesn't properly re-process
- **I27:** Left rail (context/progress) is hidden by default and never shown because `updateContextRail()` calls `/api/course_structure` (no handler)
- **I28:** Right rails (flashcard, palace) are hidden and never activated
- **I29:** Thinking bubble appears at bottom of chat stream (absolute positioned in corner) — often invisible because user has scrolled

#### Flow 4: Review → Spaced Repetition

**What happens:** User clicks Review → page loads → calls `/api/fsm_state` and `/api/courses` → shows course selection or auto-starts → fetches `/api/due_cards` → shows flashcard with "Reveal" button → manual grade buttons (Hard/Good/Easy).

**Issues found:**
- **I30:** Page calls `/api/due_cards` — this route doesn't exist in web-ui or RAG service
- **I31:** Manual grade buttons (Hard/Good/Easy) with hardcoded stability values (2.0/6.0/10.0) — not using FSRS algorithm at all
- **I32:** Card display is basic reveal-style (not Socratic) — shows title, user thinks, clicks reveal, sees answer text, self-rates. This is Anki-style, not what we want
- **I33:** `/api/update_card` route doesn't exist
- **I34:** No progress tracking — after grading, no feedback on how many cards remain or session stats
- **I35:** Different chat bubble styling than Learn page (emerald green) — inconsistent
- **I36:** No connection between Review outcomes and FSRS scheduling

#### Flow 5: Test → Adaptive Quiz

**What happens:** User clicks Test → checks `/api/courses` → calls `/api/quiz` → shows generated question → user types answer → calls `/api/quiz/grade` → shows PASS/FAIL with score.

**Issues found:**
- **I37:** `/api/quiz` and `/api/quiz/grade` routes don't exist — entire page is non-functional
- **I38:** No concept targeting — quiz doesn't specify which concepts to test
- **I39:** "Next Question" button calls `loadQuestion()` which fetches from missing API again
- **I40:** Indigo color theme looks different from other pages — no visual consistency

#### Flow 6: Status → System Health

**What happens:** User clicks Status → template renders with Jinja `{% for service_name, service in services_health.items() %}` → Socket.IO status.js polls health.

**Issues found:**
- **I41:** No route for `/status` in web-ui/app.py — will 404
- **I42:** Template expects `services_health` context variable from Jinja — route would need to pass this
- **I43:** status.js creates ANOTHER `const socket = io()` — yet another socket connection
- **I44:** Health check polls 6 services including 4 being removed (input-node, audio-engine, qwen-engine, internet)
- **I45:** "Run Post-Ingestion Tests" button calls `/api/run_tests` — no handler
- **I46:** Live Health Log section expects `{{ logs }}` context variable — never provided

#### Flow 7: Settings Modal

**What happens:** User clicks ⚙️ in header → modal with theme selector (Cyberpunk/Light/Reader) + font size controls.

**Issues found:**
- **I47:** Theme selector offers "Cyberpunk" but CSS only defines one theme (Blue Mountain / light). No cyberpunk or reader theme CSS exists — selecting them does nothing visible
- **I48:** Settings modal uses `var(--accent-cyan)` for border/shadow — this variable is undefined in current CSS (leftover from old cyberpunk theme)
- **I49:** Font scale works but applies to `calc(16px * var(--font-scale))` only on body — doesn't scale components

### 8.2 Cross-Cutting UI Issues

| # | Issue | Impact |
|---|-------|--------|
| X1 | **3-5 duplicate Socket.IO connections** per page (session.js line 1, session.js DOMContentLoaded, status.js, courses.html inline script) — causes duplicate event handlers, race conditions, wasted connections | HIGH |
| X2 | **No loading states** — pages that fetch data show "Loading..." text but no skeleton/spinner. Failed fetches show nothing | HIGH |
| X3 | **No error boundaries** — if any API call fails, user sees nothing or console errors. No toast notifications for failures | HIGH |
| X4 | **session.js loaded on every page** (via base.html) — mic/audio code, socket connections, and event listeners fire on Home, Courses, Status pages where they're irrelevant | HIGH |
| X5 | **No mobile responsiveness** beyond basic `@media (max-width: 768px)` — learn page three-column layout breaks, controls unusable on mobile | Medium |
| X6 | **Mixed CSS variable names** — templates use `var(--bg-card)`, `var(--bg-main)`, `var(--text-main)`, `var(--primary-color)`, `var(--accent-cyan)` etc. that don't exist in style.css. Each template defines its own inline `<style>` overriding the design system | HIGH |
| X7 | **No accessibility** — no ARIA labels, no keyboard navigation, no screen reader support, no focus management | Medium |
| X8 | **Inconsistent component styling** — each template (learn.html, review.html, test.html) redefines `.message`, `.controls`, `.text-input`, `.session-interface` with different values | HIGH |

---

## PART 9: UI REDESIGN — "GERMAN ALPS" THEME + GAMIFICATION

### 9.1 Design Direction: "Helga — German Alps"

**Concept:** A warm, professional learning environment that evokes the German Alps — crisp alpine air, Bavarian lodge warmth, evergreen forests, and snowcapped peaks. Clean and modern like Duolingo but with an earthy, grounded sophistication rather than primary-color playfulness.

**Typography:**
- Primary: `'DM Sans'` (Google Fonts, clean geometric sans-serif — professional but warm)
- Mono/Code: `'JetBrains Mono'` (for log output, code blocks)
- Weight scale: 400 (body), 600 (labels), 700 (headings), 800 (hero/logo)

**Logo Treatment:** "Helga" in DM Sans 800 weight with the mountain emoji 🏔️ retained. Subtle text-shadow suggesting depth. Consider adding a small Edelweiss flower SVG icon.

### 9.2 Color System: "Alpine" Palette

```css
:root {
    /* === ALPINE LIGHT THEME (default) === */

    /* Backgrounds — snow and stone */
    --bg-primary: #f5f0eb;       /* Warm parchment — like alpine lodge paper */
    --bg-secondary: #ffffff;      /* Clean white — fresh snow */
    --bg-tertiary: #e8f4f0;       /* Pale pine — hint of evergreen */
    --bg-chat: #faf8f5;           /* Warm off-white for chat area */

    /* Text — deep forest and stone */
    --text-primary: #2d3a2e;      /* Dark forest green — primary readable text */
    --text-secondary: #6b7c6e;    /* Muted sage — secondary/labels */
    --text-inverted: #ffffff;

    /* Accent — Alpine Blue (Bavarian sky) */
    --accent-primary: #2e6b8a;    /* Deep alpine lake blue — primary actions */
    --accent-primary-hover: #23567a;
    --accent-primary-shadow: #1d4a6a;  /* 3D button lip */
    --accent-primary-glow: rgba(46, 107, 138, 0.15);

    /* Accent Secondary — Warm Timber */
    --accent-secondary: #c17f4a;  /* Warm amber/wood — secondary actions */
    --accent-secondary-hover: #a8693a;

    /* Accent Tertiary — Alpine Meadow */
    --accent-tertiary: #4a8c6f;   /* Rich meadow green — success/nature */

    /* Status Colors */
    --status-success: #4a8c6f;    /* Alpine meadow green */
    --status-warning: #d4a843;    /* Golden edelweiss */
    --status-error: #c45c4a;      /* Brick/terracotta red */
    --status-info: #2e6b8a;       /* Alpine blue */

    /* Gamification Colors */
    --xp-gold: #d4a843;           /* Edelweiss gold — XP, achievements */
    --xp-glow: rgba(212, 168, 67, 0.3);
    --streak-fire: #e87c3f;       /* Warm amber — streak counter */
    --mastery-gold: #c9a84c;      /* Mastered concept badge */
    --mastery-silver: #a0aaa2;    /* In-progress concept */

    /* Bloom's Level Colors (grade indicators) */
    --bloom-remember: #7fb3d0;    /* Light sky blue */
    --bloom-understand: #5a9bb5;  /* Medium blue */
    --bloom-apply: #4a8c6f;       /* Meadow green */
    --bloom-analyze: #c17f4a;     /* Timber amber */
    --bloom-evaluate: #8b5e3c;    /* Dark wood */
    --bloom-create: #d4a843;      /* Edelweiss gold */

    /* UI Elements */
    --border-color: #d4cdc4;      /* Warm stone gray */
    --border-radius: 14px;
    --border-radius-sm: 8px;
    --border-radius-lg: 20px;
    --shadow-soft: 0 2px 8px rgba(45, 58, 46, 0.06);
    --shadow-card: 0 4px 16px rgba(45, 58, 46, 0.08);
    --shadow-button: 0 3px 0 var(--accent-primary-shadow);
}

/* === ALPINE DARK THEME === */
[data-theme="dark"] {
    --bg-primary: #1a2420;        /* Deep forest night */
    --bg-secondary: #232e28;      /* Dark pine */
    --bg-tertiary: #2a3830;       /* Forest floor */
    --bg-chat: #1e2824;

    --text-primary: #d8e0d9;      /* Moonlit snow */
    --text-secondary: #8a9a8d;    /* Misty sage */

    --border-color: #3a4a3e;      /* Dark moss */

    --accent-primary: #5da0c2;    /* Lighter alpine blue for dark bg */
    --accent-primary-hover: #4a8db0;
    --accent-primary-shadow: #3a7595;

    --shadow-soft: 0 2px 8px rgba(0, 0, 0, 0.2);
    --shadow-card: 0 4px 16px rgba(0, 0, 0, 0.3);
}
```

### 9.3 Gamification System

#### XP (Experience Points)

| Action | XP Earned | Multiplier |
|--------|-----------|------------|
| Answer Socratic question correctly | 10 XP | ×1.5 if first try |
| Complete a concept | 25 XP | ×2 if Bloom's L4+ |
| Complete a module | 100 XP | — |
| Complete a review session | 15 XP per card | — |
| Pass a test question | 20 XP | ×1.5 if no hints |
| Maintain daily streak | 5 XP × streak_day | Caps at ×30 |

**Storage:** Add to `session_state` table: `total_xp`, `daily_xp`, `streak_days`, `streak_last_date`.

#### Visual XP Elements

- **XP counter in header:** Small gold badge next to logo showing total XP (e.g., "⭐ 1,250 XP")
- **XP gain animation:** When earning XP, "+10 XP" floats up from the chat bubble with golden glow, fades out after 1.5s
- **Level badges:** Every 500 XP = new level. Display level number in header beside XP
- **Streak counter:** 🔥 emoji + day count in header. Pulses on first daily interaction. Dies if no interaction for 24h (show 💀 for 1 session as motivator)

#### Mastery Badges (Per Concept)

| Badge | Criteria | Visual |
|-------|----------|--------|
| 🌱 Seedling | First interaction | Small green dot |
| 🌿 Growing | Bloom's L2 reached | Green sprout icon |
| 🌲 Rooted | Bloom's L3 reached | Small pine tree |
| 🏔️ Summit | Bloom's L4+ mastered | Mountain peak icon |
| ⭐ Edelweiss | Bloom's L5+ AND FSRS stability > 30 days | Gold flower |

These display on the course structure visualization and next to concept names in the left rail.

#### Progress Celebration

- **Concept completion:** Brief confetti animation (CSS-only, no library) + "Concept mastered!" toast
- **Module completion:** Larger celebration with mountain scenery CSS animation + summary card
- **Course completion:** Full-screen alpine sunrise animation + comprehensive stats card

### 9.4 TTS Replacement: Kokoro TTS

**Current:** Piper TTS in Docker container with ARM64 binary and ONNX models. 2 voices. Mediocre quality.

**Replacement: Kokoro TTS** — 82M parameters, Apache 2.0 license, runs on CPU at real-time speed on Apple Silicon (0.7× real-time on M1, faster on M4 Pro). 14 built-in voices. Dramatically better naturalness than Piper while remaining lightweight.

**Implementation:**

1. **New service:** Replace `services/audio/` with a minimal `services/tts/` containing a Flask server wrapping Kokoro's Python API (`KPipeline`)
2. **On-demand only:** TTS is NOT auto-triggered. Each tutor message in the chat gets a small ▶️ play button
3. **Frontend:** Clicking play button sends `POST /api/tts` with the message text → receives WAV audio → plays via Web Audio API
4. **Caching:** Cache generated audio keyed by text hash. Same message text → serve cached WAV instantly
5. **Voice selection:** Expose Kokoro's 14 voices in Settings modal dropdown

**Docker setup:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install kokoro-tts soundfile flask
COPY tts_server.py .
EXPOSE 5005
CMD ["python", "tts_server.py"]
```

**tts_server.py (core logic):**
```python
from flask import Flask, request, send_file
from kokoro import KPipeline
import soundfile as sf
import hashlib, os, io

app = Flask(__name__)
pipeline = KPipeline(lang_code='a')  # 'a' = American English
CACHE_DIR = "/app/data/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

@app.route('/api/tts', methods=['POST'])
def synthesize():
    text = request.json.get('text', '')
    voice = request.json.get('voice', 'af_heart')  # Default voice
    cache_key = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(cache_path):
        return send_file(cache_path, mimetype='audio/wav')

    generator = pipeline(text, voice=voice)
    # Kokoro yields (graphemes, phonemes, audio_chunk) tuples
    audio_chunks = []
    for _, _, chunk in generator:
        audio_chunks.append(chunk)

    import numpy as np
    full_audio = np.concatenate(audio_chunks)
    sf.write(cache_path, full_audio, 24000)
    return send_file(cache_path, mimetype='audio/wav')

@app.route('/api/voices', methods=['GET'])
def list_voices():
    return {"voices": [
        "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
        "am_adam", "am_michael", "bf_emma", "bf_isabella",
        "bm_george", "bm_lewis", "af_alloy", "af_nova", "am_echo"
    ]}

@app.route('/health')
def health():
    return {"status": "healthy", "engine": "kokoro", "params": "82M"}
```

**Frontend play button (in chat bubbles):**
```html
<!-- Added to each tutor message bubble -->
<button class="tts-play-btn" onclick="playTTS(this)" data-text="...">
    <svg>▶</svg>
</button>
```

```javascript
async function playTTS(btn) {
    const text = btn.dataset.text;
    btn.disabled = true;
    btn.innerHTML = '⏳';
    try {
        const voice = localStorage.getItem('helga-voice') || 'af_heart';
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, voice})
        });
        const audioBlob = await resp.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        btn.innerHTML = '⏸';
        audio.onended = () => { btn.innerHTML = '▶'; btn.disabled = false; };
        audio.play();
    } catch(e) {
        btn.innerHTML = '▶'; btn.disabled = false;
        showToast('TTS playback failed', 'error');
    }
}
```

### 9.5 Component-Level Redesign Specifications

#### Header (base.html)

**Current:** White bar, blue "Helga" text with 🏔️, 7 nav links + settings gear. 72px height.

**New design:**
- Background: `var(--bg-secondary)` with subtle bottom border in `var(--border-color)`
- Left: 🏔️ Helga logo + XP badge (⭐ 1,250) + Streak (🔥 7)
- Center: Nav pills — **Home**, **Courses**, **Learn**, **Review**, **Test**, **Status** (6 items, Palace removed)
- Right: Settings ⚙️ + theme toggle (☀️/🌙)
- Active nav: filled pill with `var(--accent-primary)` background, white text
- Height: 64px (slightly reduced)

#### Home Page (home.html)

**Current:** Generic hero + 5 equal feature cards.

**New design:**
- **Hero:** "Welcome back, Brennan" with daily summary: "You have 3 concepts due for review"
- **Primary CTA:** Large button "Continue Learning" (if active course) or "Create Your First Course" (if none)
- **Stats row:** 4 cards — Total XP, Streak Days, Concepts Mastered, Courses Active
- **Activity feed:** Last 5 learning interactions with timestamps (from `interactions` table)
- **Quick actions grid:** 3 cards only — Learn (primary, large), Review (secondary), Test (secondary)
- Remove Palace and "Active Session" cards

#### Courses Page (courses.html)

**Current:** Functional but EPUB upload doesn't work, course actions don't work.

**Changes:**
- Remove "Upload EPUB" button and modal entirely
- Fix "Create by Topic" to work with new LLM-only pipeline
- Fix course cards: Start → `/api/set_active_course` (add route), Delete → `/api/delete_course` (add route), View → `/course/<uid>/structure` (add route)
- Add course progress calculation from backend
- Course card colors: derive from course title hash → pick from Alpine palette array
- Add "due for review" badge on course cards showing count of due concepts

#### Learn Page (learn.html) — MAJOR REDESIGN

**Layout:** Single-column centered chat (max-width 800px) with optional left sidebar for course navigation.

**Components (top to bottom):**

1. **Session header bar** (48px): Course name + current concept title + Bloom's level indicator + close button
2. **Chat area** (flex-grow, scrollable):
   - Tutor messages: Left-aligned, warm stone background, ▶️ TTS play button, grade badge after grading
   - User messages: Right-aligned, alpine blue background
   - Typing indicator: Three-dot animation in tutor bubble while LLM generates
   - Streaming: Tokens render incrementally into current tutor bubble
3. **Quick-reply chips** (optional row above input): "I don't know", "Give me a hint", "Skip to next"
4. **Input area** (sticky bottom):
   - Auto-growing `<textarea>` (1-5 rows), placeholder: "Type your response..."
   - Send button (mountain icon or arrow)
   - No mic, no speaker, no volume

**Left sidebar** (280px, collapsible):
- Course title
- Concept list with mastery badges (🌱🌿🌲🏔️⭐)
- Circular progress indicator
- Click concept to navigate

#### Review Page (review.html) — REDESIGN

**Change from Anki-style flashcards to Socratic review:**
- Same chat UI as Learn page
- Header says "Spaced Review — X concepts due"
- Tutor asks Socratic question about due concept (not reveal-style)
- Grade inferred from dialogue quality (no manual Hard/Good/Easy buttons)
- After answering, FSRS updates automatically
- Progress: "3 of 8 reviewed" counter

#### Test Page (test.html) — REDESIGN

- Same chat UI as Learn page
- Header says "Knowledge Test" + score counter
- Mix of multiple-choice (quick-reply chips) and free-response questions
- Targets weakest concepts across all courses
- Shows running score and final summary card

#### Settings Modal

**Current:** Theme selector (3 broken themes) + font size.

**New:**
- Theme toggle: Light / Dark (only 2, both working)
- Font size: slider (keep)
- Voice: dropdown of Kokoro's 14 voices + preview button
- Sound effects: toggle for XP/completion sounds

### 9.6 CSS Architecture Cleanup

**Problem:** Each template has 100-300 lines of inline `<style>` blocks that redefine the same components (`.message`, `.controls`, `.text-input`, `.session-interface`) with conflicting values. Templates reference CSS variables that don't exist (`--bg-card`, `--bg-main`, `--text-main`, `--primary-color`, `--accent-cyan`, `--accent-emerald`, `--accent-indigo`).

**Solution:**

1. **Single source of truth:** All CSS in `style.css`. Remove all `<style>` blocks from templates.
2. **Component classes:** Define once: `.chat-container`, `.chat-message.tutor`, `.chat-message.user`, `.chat-input-area`, `.chat-send-btn`, `.sidebar`, `.progress-ring`
3. **Page-specific modifiers:** `.page-learn`, `.page-review`, `.page-test` applied to `<main>` for accent color overrides only
4. **Variable consolidation:** Map all scattered variable names to the Alpine palette:
   - `--bg-card` → `--bg-secondary`
   - `--bg-main` → `--bg-primary`
   - `--text-main` → `--text-primary`
   - `--primary-color` → `--accent-primary`
   - `--accent-cyan` → DELETE (old cyberpunk)
   - `--accent-emerald` → `--accent-tertiary`
   - `--accent-indigo` → `--accent-primary`

### 9.7 JavaScript Architecture Cleanup

**Problem:** `session.js` is loaded on EVERY page via `base.html` and creates socket connections, audio contexts, and event listeners regardless of whether the page needs them.

**Solution:**

1. **Split session.js into modules:**
   - `socket.js` — single socket connection, only created when needed, exported as singleton
   - `chat.js` — chat UI logic (message rendering, streaming, typing indicator, TTS play buttons), imported only on Learn/Review/Test pages
   - `gamification.js` — XP animations, streak display, loaded globally via base.html
   - `session.js` — slim orchestrator for Learn page only
2. **Page-specific loading:** Use `{% block page_scripts %}` in each template to load only needed JS
3. **Remove all audio/WebRTC code** — ~400 lines deleted from session.js

---

## PART 10: UPDATED SPRINT TASKS (UI + TTS additions)

These tasks integrate into the Phase 4 (Web UI Overhaul) section of the sprint.

### Phase 4 (revised): Web UI Overhaul (28-36 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 4.1 Alpine CSS system | 4 | `style.css` | Replace entire CSS with Alpine palette (§9.2). Define all component classes. Light + Dark themes. Remove all template inline styles |
| 4.2 Update base.html | 2 | `base.html` | New header: XP badge, streak counter, 6 nav links (no Palace), theme toggle (sun/moon), DM Sans font. Split session.js into page-specific includes |
| 4.3 Redesign home.html | 3 | `home.html` | Welcome back greeting, daily summary, primary CTA, 4 stat cards, activity feed, 3 quick-action cards. Fix API connections |
| 4.4 Fix courses.html | 2 | `courses.html` | Remove EPUB upload. Fix course card actions (add route handlers). Add review-due badges. Alpine card styling |
| 4.5 Redesign learn.html | 6 | `learn.html` | Full chat redesign per §9.5. Remove all voice UI. Add typing indicator, streaming render, grade badges, hint accordion, quick-reply chips, TTS play buttons, concept progress. Left sidebar with course nav |
| 4.6 Redesign review.html | 3 | `review.html` | Convert from Anki flashcards to Socratic review chat. Same chat component as Learn. FSRS-driven concept selection |
| 4.7 Redesign test.html | 3 | `test.html` | Adaptive quiz with same chat component. MC chips + free response. Score counter. Summary card |
| 4.8 Rewrite session.js | 4 | `session.js`, new: `chat.js`, `socket.js`, `gamification.js` | Split into modules. Delete 400 lines audio code. Fix double socket. Add streaming, typing indicator, TTS play, XP animations |
| 4.9 Add all missing routes | 3 | `web-ui/app.py` | Routes: `/review`, `/test`, `/status`, `/course/<uid>/structure`, `/api/courses`, `/api/set_active_course`, `/api/delete_course`, `/api/tts` (proxy to TTS service), `/api/voices`, `/api/due_concepts` |
| 4.10 Gamification backend | 2 | `fsm_logic.py` | XP calculation after each graded interaction. Streak tracking (daily login). Store in `session_state` table. Emit XP events via status_update |
| 4.11 Settings modal | 1 | `base.html`, `settings.js` | Light/Dark theme toggle. Voice selector (Kokoro voices). Font size. Remove broken Cyberpunk/Reader themes |
| 4.12 Status page cleanup | 1 | `status.html`, `status.js` | Remove dead services. Fix Jinja template to use client-side rendering only. Fix socket duplication |

### New Phase: TTS Service (4-6 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| T.1 Create Kokoro TTS service | 3 | New: `services/tts/tts_server.py`, `services/tts/Dockerfile`, `services/tts/requirements.txt` | Flask server wrapping Kokoro KPipeline. `/api/tts` endpoint with audio caching. `/api/voices` endpoint. Health check |
| T.2 Add to docker-compose | 0.5 | `docker-compose.yml` | Add `tts` service (port 5005, python:3.11-slim, 512M memory limit) |
| T.3 Frontend TTS integration | 1.5 | `chat.js` | Play button on each tutor message. `playTTS()` function. Voice preference in localStorage |
| T.4 Test TTS quality | 1 | Manual | Test all 14 Kokoro voices. Select best default. Verify caching works |

### Updated Sprint Schedule

| Day | Phase | Focus | Hours |
|-----|-------|-------|-------|
| 1-2 | Phase 1 | Delete dead code, Docker, SQLite, Ollama | 12-16 |
| 3-5 | Phase 2 | Core logic: bugs, prompts, LLM calls, FSRS, Bloom's | 20-28 |
| 6-7 | Phase 3 | RAG: SQLite rewrite, course builder, embeddings | 12-16 |
| 8-9 | Phase 4a | CSS system, base.html, home, courses | 11 |
| 10-11 | Phase 4b | Learn/Review/Test redesign, session.js rewrite | 16 |
| 12 | Phase 4c | Routes, gamification, settings, status cleanup | 7 |
| 13 | TTS Phase | Kokoro service, Docker, frontend integration | 4-6 |
| 14-15 | Phase 5 | Caching, streaming polish, E2E testing | 8-12 |
| **Total** | | | **90-120 hrs** |

---

## PART 11: FILES TO DELETE/CREATE/MODIFY (Updated)

### Additional Files to DELETE

```
services/web-ui/templates/memory_palace.html
services/audio/                     (entire — replaced by services/tts/)
configs/alsa_monitor.conf
configs/pipewire.conf
```

### Additional Files to CREATE

```
services/tts/tts_server.py          — Kokoro TTS Flask server
services/tts/Dockerfile             — Python 3.11 + kokoro-tts
services/tts/requirements.txt       — kokoro-tts, soundfile, flask, numpy
services/web-ui/static/js/chat.js   — shared chat component
services/web-ui/static/js/socket.js — singleton socket manager
services/web-ui/static/js/gamification.js — XP animations, streak display
```

### Files with TOTAL REWRITE now

```
services/web-ui/static/css/style.css  — full Alpine palette + component library
services/web-ui/templates/learn.html  — voice → text-only chat redesign
services/web-ui/templates/review.html — flashcard → Socratic review
services/web-ui/templates/test.html   — connect to backend + adaptive quiz
services/web-ui/templates/home.html   — personalized dashboard
services/web-ui/templates/base.html   — new header, nav, script loading
```

---

## PART 12: TUTORING FLOW AUDIT — PEDAGOGICAL PIPELINE ANALYSIS

### 12.1 Complete Tutoring Flow Trace (As-Is)

```
USER clicks "Start Course"
  │
  ├─► resume_course(uid) / enter_mode_1(text)
  │     └─► GET /flat_syllabus?uid=X  →  RAG returns [{uid, title, text}, ...]
  │           └─► self.syllabus_queue = linear list (no hierarchy, no prereq sort)
  │
  ├─► next_syllabus_item()
  │     ├─ Mark previous concept completed (add UID to set)
  │     ├─ Pop first concept from queue
  │     ├─ self.current_context = resource_text[:10000]  ← HARD TRUNCATION
  │     ├─ GET /teaching_context?uid=X  ← ENDPOINT DOESN'T EXIST → always empty
  │     ├─ Generate bridge sentence (if not first concept)
  │     └─► ask_socratic_question("Initiate concept exploration.")
  │
  ├─► ask_socratic_question(context_trigger)
  │     ├─ Build history_str from conversation_history
  │     │     └─ history = [(trigger, question), ...] — NO STUDENT ANSWERS
  │     ├─ get_socratic_tutor_prompt(context, history, misconceptions=[], analogies=[])
  │     ├─ POST /v1/completions → LLM generates ONE question
  │     ├─ self.last_question = question
  │     ├─ self.question_start_time = now
  │     └─ Append (trigger, question) to history — cap at 5
  │
  ├─► USER types answer → handle_socratic_answer(text)
  │     ├─ latency = now - question_start_time
  │     ├─ question_start_time = 0  ← RESET TO ZERO
  │     ├─ get_socratic_grading_prompt(concept_TITLE, question, answer)
  │     │     NOTE: receives TITLE only, NOT resource_text
  │     ├─ POST /v1/completions → grade 1-4 (broken regex → usually defaults to 3)
  │     ├─ Hesitation check: markers ≥ 2 OR latency > 8s → grade -= 1
  │     │
  │     └─► DECISION MATRIX:
  │           ├─ Grade ≤ 1: ask_socratic_question("Give me a simplified analogy.")
  │           │     (loops on same concept — NO attempt limit)
  │           ├─ Grade == 2: "Can you be more specific?" → pass  ← DOES NOTHING
  │           │     (no new question, timer broken, creates death spiral)
  │           ├─ Grade == 3: mark completed → next_syllabus_item()
  │           │     (ONE question total — concept "mastered")
  │           └─ Grade ≥ 4: mark completed → SKIP TWO concepts (Bug B9)
  │
  └─► syllabus_queue empty → "Course module complete." → LOBBY
```

### 12.2 Pedagogical Flaws

#### F1: Conversation history excludes student answers (CRITICAL)

`conversation_history` stores `(context_trigger, question)` — the student's actual response is never recorded. The LLM generating the next question has zero knowledge of what the student said. Socratic scaffolding (building question N+1 on the student's answer to question N) is impossible. This is the single most damaging flaw — it reduces dialogue to disconnected Q&A.

**Fix:** Store `{question, answer, grade}` dicts. Append student answer after grading. Serialize both sides into prompt history.

#### F2: Grading prompt lacks source context (CRITICAL)

`get_socratic_grading_prompt(concept, question, user_answer)` receives only the concept TITLE (e.g., "Photosynthesis"), not the `resource_text`. The LLM grades against its own parametric knowledge. For specialized or course-specific content, grades are unreliable — the LLM may accept wrong answers or reject correct ones that use the source's terminology.

**Fix:** Pass `self.current_context[:3000]` as 4th parameter. Include as "Source Truth" in grading prompt.

#### F3: Grade 2 creates stuck state (HIGH)

Grade 2 says "Can you be more specific?" then `pass`. No new question generated. `question_start_time` already reset to 0. Timer nudge checks `question_start_time > 0` — won't fire. Next student input: `latency = now - 0` → huge number → always triggers hesitation → grade penalized. Creates downward death spiral.

**Fix:** Reset `question_start_time = time.time()` after grade-2 feedback. Generate targeted follow-up probing the specific weakness.

#### F4: Only ONE question per concept (HIGH)

Grade 3 ("Correct") immediately marks concept completed and advances. No verification depth — one lucky guess = "mastery." Learning science requires multiple demonstrations across question types before declaring mastery (Bloom's mastery learning: 2+ consecutive correct).

**Fix:** Require `correct_streak ≥ 2` AND `question_count ≥ 3` before advancing. Rotate question types on same concept.

#### F5: No micro-lecture fallback on repeated failure (HIGH)

`get_micro_lecture_prompt()` exists in prompts.py but is NEVER called. Grade 1 loops forever: "Let's look at this from another angle" → new question → grade 1 → repeat. No maximum attempts. No escalation to direct instruction. Research shows explanation at "point of impasse" (after 2-3 failures) is when it has maximum impact.

**Fix:** After 3 consecutive grade-1 answers, call `get_micro_lecture_prompt()`, deliver explanation, follow with verification question, reset fail counter.

#### F6: Hesitation penalty is wrong for text mode (HIGH)

`_detect_hesitation()` penalizes "I think", "well", "maybe" and latency > 8 seconds. In text chat, "I think the reason is..." is excellent causal reasoning. 8 seconds is barely time to read the question and start typing. This constantly penalizes thoughtful students.

**Fix:** Delete `_detect_hesitation()` entirely. Grade quality is already assessed by the LLM grading rubric.

#### F7: No question type variation (MEDIUM)

Same `get_socratic_tutor_prompt()` template for every question. No rotation between Socratic question types (clarification, probing assumptions, probing evidence, exploring viewpoints, probing implications, application). Produces repetitive questioning that fails to probe different facets of understanding.

**Fix:** Rotate through 6 question types based on `concept_question_count % 6`. Pass type as parameter to prompt.

#### F8: Bridge is passive — missed interleaving opportunity (MEDIUM)

Bridge generates a narrated transition. Student is never asked to make the connection themselves. Active interleaving (student explains how concepts relate) produces stronger learning than passive transitions.

**Fix:** After bridge, ask student to explain the connection before proceeding.

#### F9: No prior knowledge probe (MEDIUM)

System immediately asks Socratic questions without gauging what the student already knows. A student who already understands the concept gets asked basic questions; a student who has no foundation gets the same.

**Fix:** Diagnostic question before first Socratic question. Use response to set initial Bloom's level. Skip concept if student demonstrates existing mastery (grade 4).

#### F10: Hard context truncation (MEDIUM)

`resource_text[:10000]` — naive truncation. Critical information past 10K chars is lost. No summarization.

**Fix:** For texts > 4000 chars, summarize via LLM preserving definitions and examples. With 14B model's 32K context window, we have more room than the current 10K limit.

#### F11: No consolidation checkpoints (MEDIUM)

After completing a concept, the system immediately moves to the next. There's no periodic review of recently completed concepts — despite research showing that interleaved recall within a session dramatically improves retention.

**Fix:** Every 3-4 concepts, insert a quick-recall round on recently completed concepts.

#### F12: Timer nudge fires repeatedly with no escalation (LOW)

After 20s inactivity: "Do you need more time, or a hint?" — repeats every 20s forever with identical message. No escalation to actually providing a hint.

**Fix:** First nudge: "Take your time." Second: "Would you like a hint?" Third: auto-deliver a hint.

#### F13: Flat syllabus ignores prerequisites (LOW)

Concepts returned in ordinal order, not topologically sorted by prerequisite dependencies. A concept might appear before its prerequisite.

**Fix:** Topological sort on prerequisites table before building syllabus queue.

### 12.3 Redesigned Tutoring Flow (To-Be)

```
USER starts course
  │
  ├─► Load syllabus (topologically sorted by prerequisites)
  │
  ├─► FOR EACH concept:
  │     │
  │     ├─► PHASE 1: DIAGNOSTIC PROBE
  │     │     ├─ One assessment question gauging prior knowledge
  │     │     ├─ Grade → set initial bloom_level
  │     │     └─ If grade 4+: skip concept (already known)
  │     │
  │     ├─► PHASE 2: ACTIVE BRIDGE (if not first concept)
  │     │     ├─ Ask student to connect previous concept to current
  │     │     └─ Acknowledge, correct if needed
  │     │
  │     ├─► PHASE 3: SOCRATIC DIALOGUE LOOP
  │     │     ├─ Select question_type (rotate through 6 types)
  │     │     ├─ Generate question at current bloom_level
  │     │     ├─ Full history (questions AND answers) in prompt
  │     │     ├─ Source context in grading prompt
  │     │     │
  │     │     ├─ Grade 1 (fails < 3): Progressive hint → re-ask
  │     │     ├─ Grade 1 (fails ≥ 3): MICRO-LECTURE → verify
  │     │     ├─ Grade 2: Targeted follow-up → stay on concept
  │     │     ├─ Grade 3: Increment streak → next question type
  │     │     └─ Grade 4: Increment streak → consider Bloom's advance
  │     │     │
  │     │     ADVANCE when: correct_streak ≥ 2 AND questions ≥ 3
  │     │     └─► Update FSRS, record interaction, award XP
  │     │
  │     └─► PHASE 4: CONSOLIDATION (every 3-4 concepts)
  │           └─ Quick recall on recently completed concepts
  │
  └─► COURSE COMPLETE → Summary, FSRS scheduling, celebration
```

### 12.4 Sprint Tasks for Tutoring Flow Fixes

| Task | Hours | Priority | Fixes | Details |
|------|-------|----------|-------|---------|
| TF.1 Fix conversation history | 1 | CRITICAL | F1 | Store `{question, answer, grade}` dicts. Serialize both sides to prompt |
| TF.2 Add context to grading | 0.5 | CRITICAL | F2 | Pass `current_context[:3000]` to grading prompt as source material |
| TF.3 Fix grade-2 stuck state | 1 | HIGH | F3 | Reset timer, generate follow-up, remove bare `pass` |
| TF.4 Multi-question mastery | 2 | HIGH | F4 | Require ≥2 correct streak AND ≥3 questions. Rotate question types |
| TF.5 Micro-lecture fallback | 1.5 | HIGH | F5 | Track fail count. After 3 failures, deliver explanation via `get_micro_lecture_prompt()` |
| TF.6 Remove hesitation penalty | 0.5 | HIGH | F6 | Delete `_detect_hesitation()` and all calls. Text-mode only |
| TF.7 Question type rotation | 1.5 | MEDIUM | F7 | Define 6 types with prompt instructions. Rotate by question count |
| TF.8 Active bridge questioning | 1 | MEDIUM | F8 | Ask student to explain concept connection before proceeding |
| TF.9 Diagnostic probe | 1.5 | MEDIUM | F9 | Assessment question before first Socratic. Set initial bloom level |
| TF.10 Smart context prep | 1 | MEDIUM | F10 | LLM summarization for texts > 4K chars. Preserve definitions |
| TF.11 Consolidation checkpoints | 1.5 | MEDIUM | F11 | Every 3-4 concepts, interleaved recall round on recent concepts |
| TF.12 Prerequisite-sorted syllabus | 1 | LOW | F13 | Topological sort on prerequisites before building queue |
| **Total** | **14** | | | |

### 12.5 Updated Phase 2 Timeline

Phase 2 grows from 28 hours to **42 hours** with tutoring flow fixes included. The tutoring flow tasks (TF.1-TF.12) execute in priority order after the infrastructure tasks (2.1-2.8), as they depend on the new LLM client and prompt system being in place first.

---

## PART 13: COURSE CREATION PIPELINE AUDIT — SKELETON, HYDRATION & STORAGE

### 13.1 Complete Creation Pipeline Trace (As-Is)

```
USER submits "Create by Topic" form on courses.html
  │
  ├─► courses.html inline JS:
  │     socket.emit('text_input', {text: "create course X with depth 3"})
  │     ↓
  │     web-ui/app.py has NO @socketio.on('text_input') handler
  │     ↓
  │     EVENT GOES INTO THE VOID — CREATION NEVER STARTS
  │     (Also emitted TWICE: once in connect callback, once as fallback)
  │
  ├─► ALTERNATIVE: learn.html session.js:
  │     sendEvent('TEXT_INPUT', ...) → POST /api/event → core-logic /event
  │     → fsm.transition() → start_creation(text)
  │     (This path works but is on the WRONG page)
  │
  └─► start_creation(text):
        ├─ Parse topic + depth from free-text string
        ├─ _creation_pipeline() in background thread:
        │
        ├─► Step 1: ServiceManager.stop_for_ingestion()
        │     └─ Stops input-node + audio-engine (being deleted)
        │
        ├─► Step 2: DatabaseManager.create_temp_database()
        │     └─ Creates /app/data/kuzu_db/db_temp
        │
        ├─► Step 3: SkeletonBuilder.build(topic)
        │     ├─ CREATE Course node (uid, title, status='skeleton')
        │     ├─ LLM: "Create 2 modules for 'topic'"  ← ALWAYS 2, ignores depth
        │     ├─ For each module (always 2):
        │     │   ├─ CREATE Module node
        │     │   ├─ CREATE 1 Unit (hardcoded)
        │     │   ├─ CREATE 1 Lesson (hardcoded)
        │     │   ├─ LLM: "Create 3 concepts for 'module_title'"  ← ALWAYS 3
        │     │   └─ For each concept (always 3):
        │     │       └─ CREATE Concept node with:
        │     │            resource_text = json.dumps(objectives)
        │     │            ← THIS IS A JSON ARRAY, NOT PROSE
        │     │            e.g. '["Define photosynthesis", "Explain light reactions"]'
        │     └─ Returns course_uid
        │     Total: always 2 modules × 1 unit × 1 lesson × 3 concepts = 6 concepts
        │     No: overview, prerequisites, misconceptions, analogies, embeddings
        │
        ├─► Step 4: ContentHydrator.hydrate(course_uid)
        │     ├─ MATCH (c:Concept) RETURN c.uid, c.title
        │     │   ← QUERIES ALL CONCEPTS IN ENTIRE DB, NOT JUST THIS COURSE
        │     ├─ For each concept found:
        │     │   ├─ Search ZIM (being removed)
        │     │   ├─ If no ZIM hit:
        │     │   │   text = llm_generate("Explain {title} in 2 paragraphs.")
        │     │   │   ← MINIMAL PROMPT: no structure, no examples, no terms
        │     │   └─ SET c.resource_text = text
        │     └─ SET Course.status = 'ready'
        │     No: self-consistency, misconceptions, analogies, embeddings, Bloom's
        │
        ├─► Step 5: DatabaseManager.validate_temp_database()
        │     └─ Just checks node count > 5
        │
        ├─► Step 6: DatabaseManager.atomic_swap_database()
        │     └─ Rename db→db_backup, db_temp→db (KuzuDB file swap)
        │
        └─► Step 7: ServiceManager.restart_after_ingestion()
              └─ Restarts rag-engine, input-node, audio-engine
```

### 13.2 SkeletonBuilder Flaws

| # | Flaw | Impact | Current Code |
|---|------|--------|-------------|
| SK1 | **Depth parameter completely ignored** — always creates exactly 2 modules with 3 concepts each (6 total) regardless of depth 1-5 selection | User expects depth 5 to produce a comprehensive course; gets the same 6 concepts as depth 1 | `prompt = f"Create 2 modules for '{topic}'"` — hardcoded 2 |
| SK2 | **resource_text stores JSON objectives, not prose** — `json.dumps(con.get('objectives', []))` writes `'["Define X", "Explain Y"]'` as the concept's teaching content | If hydration fails (or during the window between skeleton and hydration), the tutoring flow sets `current_context = '["Define X"]'` — nonsense for a Socratic prompt | `"txt": json.dumps(con.get('objectives', []))` |
| SK3 | **No course overview generated** — Course node gets title and status only. `overview` field stays NULL | Courses page shows "No description" for every course. Home page stats are meaningless | No `SET c.overview = ...` call anywhere |
| SK4 | **No prerequisite relationships** — DEPENDS_ON edges are never created between concepts | Syllabus is flat ordinal ordering. Concept that requires prior knowledge may appear before its prerequisite. Tutoring flow F13 is impossible to fix without prereq data | No `CREATE (c1)-[:DEPENDS_ON]->(c2)` anywhere |
| SK5 | **No misconception/analogy generation** — these fields are never populated during creation | Tutoring prompt's `{misc_str}` and `{analog_str}` are always empty. The `/teaching_context` endpoint (which doesn't exist anyway) would return nothing | Not attempted |
| SK6 | **No STRUCT: progress events emitted** — `status_callback` is stored but never called | courses.html progress tree stays blank ("Architecting syllabus hierarchy..." forever). User has no visibility into what's being built | `self.status_callback = status_callback` — never invoked |
| SK7 | **Silent failure on LLM parse errors** — if `extract_python_list()` returns None, `or []` silently creates zero items | User waits for creation, gets a "course" with 0-1 modules and 0 concepts. No error shown | `items = extract_python_list(llm_generate(prompt)) or []` |
| SK8 | **Flat hierarchy** — always 1 unit per module, 1 lesson per unit. The 4-level hierarchy (Course→Module→Unit→Lesson→Concept) is cosmetic; structurally it's Course→Concept with wrappers | Course structure visualization shows a meaningless hierarchy. No grouping by subtopic | Hardcoded `ordinal: 1` for unit and lesson |
| SK9 | **Prompts are trivial** — "Create 2 modules for 'X'" with no pedagogical guidance. No instruction about learning progression, scope boundaries, or logical ordering | LLM produces arbitrary module splits that may overlap, be too broad, or miss critical subtopics | Single-line prompts with no examples |
| SK10 | **No depth_level set on Concept nodes** — schema has `depth_level INT64` but SkeletonBuilder never sets it | `flat_syllabus` can't distinguish modules from concepts. Course tree can't render hierarchy | Not set in CREATE statement |

### 13.3 ContentHydrator Flaws

| # | Flaw | Impact | Current Code |
|---|------|--------|-------------|
| HY1 | **Queries ALL concepts in DB, not course-scoped** — `MATCH (c:Concept) RETURN c.uid, c.title` | If user has 2 courses, hydrating course B also re-hydrates (overwrites) all concepts from course A. Multi-course state is corrupted | No WHERE clause, no course_uid filter |
| HY2 | **Fallback prompt produces minimal content** — "Explain {title} in 2 paragraphs" | Generated content has no structure: no key terms, no definitions, no examples, no exercises. Tutor has thin context to ask questions about | `llm_generate(f"Explain {title} in 2 paragraphs.")` |
| HY3 | **No self-consistency verification** — single LLM generation with no factual checking | Hallucinated facts go directly into course content. Student learns incorrect information. No confidence flagging | Single call, result stored directly |
| HY4 | **No embedding generation** — `summary_vector FLOAT[384]` is never populated | Semantic search (`/search` endpoint) can't find concepts by meaning. Vector similarity returns 0 for everything | Not attempted |
| HY5 | **No Bloom's level assignment** — all concepts default to the same level | Tutoring flow can't differentiate foundational concepts from advanced ones. Question difficulty can't adapt | No bloom_level in CREATE statement |
| HY6 | **`import gc; gc.collect()` inside the hydration loop** — called per concept | Unnecessary performance hit. GC on every iteration slows creation | Inside `for uid, title in concept_list:` loop |
| HY7 | **No key terms extraction** — content is raw prose with no structured metadata | Tutoring flow can't test specific terminology. Review can't target vocabulary | Not attempted |
| HY8 | **No progress emissions** — status_callback stored but not used during hydration | No "Hydrating concept 3/6" progress visible to user | Never called |

### 13.4 Data-Tutoring Misalignment

What the redesigned tutoring flow (Part 12) needs per concept vs. what the current pipeline actually stores:

| Data Needed by Tutor | Currently Stored | Gap |
|----------------------|-----------------|-----|
| Prose educational content (200-400 words) | JSON objectives array OR 2 generic paragraphs | **CRITICAL** — tutor context is garbage or thin |
| Key terms with definitions | Nothing | Missing |
| 2-3 concrete examples | Nothing | Missing |
| 3 key takeaways / summary | Nothing | Missing |
| Common misconceptions (3) | Nothing | Missing |
| Teaching analogies (2) | Nothing | Missing |
| Bloom's target level (1-6) | Nothing (defaults to 1) | Missing |
| Prerequisite concept UIDs | Nothing | Missing |
| Concept embedding (384-dim) | Nothing | Missing |
| Course overview paragraph | Nothing | Missing |
| Hierarchical depth_level | Nothing | Missing |

### 13.5 UI Course Creation Flaws

| # | Flaw | Impact |
|---|------|--------|
| UI1 | **courses.html creation is completely broken** — `socket.emit('text_input')` has no server handler. Event is silently dropped. Course is never created | No course can be created from the courses page |
| UI2 | **Event emitted TWICE** — once in socket connect callback, once as fallback `if (socket.connected)`. If it DID work, it would attempt to create the same course twice | Race condition / duplicate creation |
| UI3 | **Two separate creation UIs** — courses.html has its own progress modal (cube loader + tree + logs), learn.html/session.js has a DIFFERENT progress modal. Two codebases for one feature | Maintenance nightmare. Neither works properly |
| UI4 | **Progress tree expects STRUCT: events that are never emitted** — tree stays blank showing "Architecting syllabus hierarchy..." forever | User has no feedback during creation (30-90 seconds of blank progress) |
| UI5 | **No cancel button during creation** — modal has no close/cancel option once creation starts | User can't abort if they made a typo or it's taking too long |
| UI6 | **After success, redirects to /learn without setting active course** — user lands in learn lobby with no course selected | User has to manually select the course they just created |
| UI7 | **No validation of topic input** — empty or very short topics get passed through | LLM may generate garbage for topics like "a" or "the" |
| UI8 | **No creation-in-progress indicator on courses page** — if creation is running in background, user can start ANOTHER one | No concurrent creation guard on the courses page (session.js has one, courses.html doesn't) |
| UI9 | **Course card Start/Resume calls missing `/api/set_active_course`** — returns 404 | Start button is non-functional |
| UI10 | **Course card Delete calls missing `/api/delete_course`** — returns 404 | Delete button is non-functional |
| UI11 | **Course card View navigates to missing `/course/<uid>/structure`** — returns 404 | View button is non-functional |
| UI12 | **No loading skeleton on courses page** — shows "Loading courses..." text, then either cards or "No active courses" | No visual loading state |

### 13.6 Redesigned Course Creation Pipeline (To-Be)

```
USER clicks "Create by Topic" on courses.html
  │
  ├─► FRONTEND: Validate topic (≥3 chars, not duplicate)
  │     ├─ POST /api/create_course {topic, depth}  ← NEW REST endpoint
  │     ├─ Show progress modal with real-time updates via Socket.IO
  │     └─ Cancel button sends POST /api/cancel_creation
  │
  └─► BACKEND: core-logic /api/create_course endpoint
        │
        ├─► Step 1: CURRICULUM DESIGN (SkeletonBuilder)
        │     ├─ LLM prompt with pedagogical structure:
        │     │   "Design a curriculum for '{topic}' at depth level {depth}.
        │     │    Return a JSON object with this structure:
        │     │    {
        │     │      'overview': 'Course description (2-3 sentences)',
        │     │      'modules': [
        │     │        {
        │     │          'title': 'Module Name',
        │     │          'description': 'What this module covers',
        │     │          'concepts': [
        │     │            {
        │     │              'title': 'Concept Name',
        │     │              'objectives': ['Learning objective 1', ...],
        │     │              'bloom_level': 1-6,
        │     │              'prerequisites': ['title of prerequisite concept'],
        │     │              'estimated_minutes': 5-15
        │     │            }
        │     │          ]
        │     │        }
        │     │      ]
        │     │    }
        │     │    
        │     │    Depth guide:
        │     │    - Depth 1: 2 modules, 2-3 concepts each (overview)
        │     │    - Depth 2: 3 modules, 3-4 concepts each (foundational)
        │     │    - Depth 3: 4 modules, 4-5 concepts each (comprehensive)
        │     │    - Depth 4: 5 modules, 5-6 concepts each (detailed)
        │     │    - Depth 5: 6+ modules, 6-7 concepts each (expert)
        │     │    
        │     │    Order concepts so prerequisites come before dependents.
        │     │    Assign bloom_level: start at 1-2, progress to 3-4, end at 5-6."
        │     │
        │     ├─ Parse response with robust JSON extraction
        │     ├─ Validate: ≥2 modules, ≥2 concepts per module, all fields present
        │     ├─ If parse fails: retry up to 3 times with error feedback
        │     │
        │     ├─ Write to SQLite:
        │     │   ├─ INSERT course (uid, title, overview, status='building')
        │     │   ├─ For each module → INSERT concept (depth_level=0, parent=course)
        │     │   ├─ For each concept → INSERT concept (depth_level=3, parent=module)
        │     │   ├─ For each prerequisite ref → INSERT INTO prerequisites
        │     │   └─ Emit progress: "Skeleton complete: {n} modules, {m} concepts"
        │     │
        │     └─ Validate: SELECT count(*) from concepts WHERE course_uid = ?
        │
        ├─► Step 2: CONTENT GENERATION (ContentHydrator)
        │     ├─ Query concepts FOR THIS COURSE ONLY:
        │     │   SELECT * FROM concepts WHERE course_uid = ? AND depth_level = 3
        │     │
        │     ├─ For each concept (with progress emission):
        │     │   ├─ LLM prompt with full context:
        │     │   │   "Generate educational content for this concept:
        │     │   │    Course: {course_title}
        │     │   │    Module: {parent_module_title}
        │     │   │    Concept: {concept_title}
        │     │   │    Learning objectives: {objectives}
        │     │   │    Bloom's level: {bloom_level} ({bloom_name})
        │     │   │    
        │     │   │    Generate a JSON response:
        │     │   │    {
        │     │   │      'content': '300-500 word lesson with clear explanations',
        │     │   │      'key_terms': [{'term': '...', 'definition': '...'}],
        │     │   │      'examples': ['Concrete example 1', 'Example 2'],
        │     │   │      'takeaways': ['Key point 1', 'Key point 2', 'Key point 3'],
        │     │   │      'misconceptions': [
        │     │   │        {'belief': 'Common wrong belief', 'correction': 'Why it is wrong'}
        │     │   │      ],
        │     │   │      'analogies': ['Analogy for explaining this concept']
        │     │   │    }"
        │     │   │
        │     │   ├─ Self-consistency check (3 generations, majority vote on claims)
        │     │   ├─ Parse → store:
        │     │   │   ├─ resource_text = formatted prose (content + key_terms + examples + takeaways)
        │     │   │   ├─ misconceptions = JSON array
        │     │   │   ├─ analogies = JSON array
        │     │   │   └─ Emit progress: "Hydrated {i}/{n}: {concept_title}"
        │     │   │
        │     │   └─ Generate embedding → store in concept_embeddings
        │     │
        │     └─ UPDATE course SET status = 'ready'
        │
        ├─► Step 3: VALIDATION
        │     ├─ Count concepts with non-empty resource_text
        │     ├─ Count concepts with embeddings
        │     ├─ Verify prerequisite graph has no cycles
        │     └─ If validation fails: mark course status='error', report to UI
        │
        └─► Step 4: NOTIFY UI
              ├─ Emit "Course built successfully!" via Socket.IO
              ├─ Auto-set as active course
              └─ UI redirects to /learn with course pre-selected
```

### 13.7 Schema Additions for Course Quality

The proposed schema in Part 2.2 needs these additional columns on `concepts`:

```sql
-- Add to concepts table:
    learning_objectives TEXT DEFAULT '[]',  -- JSON array of objective strings
    key_terms TEXT DEFAULT '[]',            -- JSON array of {term, definition}
    examples TEXT DEFAULT '[]',             -- JSON array of example strings
    takeaways TEXT DEFAULT '[]',            -- JSON array of key point strings
    estimated_minutes INTEGER DEFAULT 10,   -- Estimated learning time
```

The `resource_text` field should store **formatted prose only** (the lesson content), not JSON objectives. Structured metadata goes in dedicated columns. This separation means:
- The Socratic prompt gets clean prose in `resource_text`
- The grading prompt can reference `key_terms` for terminology verification
- The UI can display `takeaways` as a summary card after concept completion
- `estimated_minutes` enables session time prediction on the learn page

### 13.8 Sprint Tasks for Course Creation Pipeline

These tasks replace the existing "3.2 Rewrite course_builder" task (previously 6 hours) with a more detailed breakdown:

| Task | Hours | Priority | Fixes | Details |
|------|-------|----------|-------|---------|
| CB.1 Rewrite SkeletonBuilder with pedagogical prompts | 4 | CRITICAL | SK1-SK10 | Single structured LLM call returns full curriculum JSON. Depth parameter controls module/concept count. Bloom's levels assigned. Prerequisites extracted. Course overview generated. Robust parsing with 3 retries. Emit STRUCT: progress events |
| CB.2 Rewrite ContentHydrator (LLM-only, course-scoped) | 5 | CRITICAL | HY1-HY8 | Query concepts by course_uid only. Rich content prompt (prose + key_terms + examples + takeaways + misconceptions + analogies). Self-consistency 3-pass for factual claims. Emit per-concept progress. No gc.collect() in loop |
| CB.3 Embedding generation pipeline | 1.5 | HIGH | HY4 | After content generation, embed each concept's resource_text with all-MiniLM-L6-v2. Store in concept_embeddings table via sqlite-vec |
| CB.4 Prerequisite graph validation | 1 | HIGH | SK4 | Validate no cycles in prerequisite graph (topological sort). Store DEPENDS_ON from skeleton's prerequisite field |
| CB.5 Course creation REST endpoint | 1.5 | CRITICAL | UI1-UI3 | New `POST /api/create_course` in core-logic. Replaces text-command parsing. Returns course_uid. Emits Socket.IO progress events. Cancel support |
| CB.6 Unified creation progress UI | 3 | HIGH | UI3-UI8 | Single creation flow on courses.html only (remove from learn.html). Real progress bar driven by backend events. Live concept list as items are generated. Cancel button. Topic validation. Error display. Redirect to /learn with active course set |
| CB.7 Course card action routes | 1.5 | CRITICAL | UI9-UI11 | Add `/api/set_active_course`, `/api/delete_course`, `/course/<uid>/structure` routes to web-ui. Proxy to core-logic/RAG as needed |
| CB.8 Content quality validation | 1 | MEDIUM | — | Post-creation check: all concepts have resource_text > 100 chars, all have embeddings, prereq graph is acyclic. Flag failures in course status |
| **Total** | **18.5** | | | |

### 13.9 Updated Phase 3 Timeline

Phase 3 grows from 12-16 hours to **24-28 hours** with the course creation pipeline improvements:

| Original Task | Hours | New/Revised Task | Hours |
|---------------|-------|-------------------|-------|
| 3.1 KuzuDB → SQLite | 6 | 3.1 KuzuDB → SQLite (unchanged) | 6 |
| 3.2 Rewrite course_builder | 6 | CB.1 Skeleton rewrite | 4 |
| | | CB.2 Hydrator rewrite | 5 |
| | | CB.3 Embedding pipeline | 1.5 |
| | | CB.4 Prerequisite validation | 1 |
| | | CB.5 REST creation endpoint | 1.5 |
| | | CB.6 Unified creation UI | 3 |
| | | CB.7 Course card routes | 1.5 |
| | | CB.8 Content validation | 1 |
| 3.3 New RAG endpoints | 2 | 3.3 New RAG endpoints (unchanged) | 2 |
| 3.4 Embedding pipeline | 2 | (merged into CB.3) | — |
| **Original: 16** | | **New Total: 26.5** | |

### 13.10 Web Search Augmentation — Architecture

The ContentHydrator's biggest weakness is reliance on LLM parametric knowledge alone. This section adds a self-hosted web search pipeline that runs during course creation (not during live tutoring) to provide source material for content generation.

**Stack:** SearXNG (search) + trafilatura (extraction) + Wikipedia-API (primary source) + DiskCache (caching). Zero API keys. Zero external costs. Fully self-hosted.

**When search runs:** Only during the ContentHydrator's per-concept loop (CB.2). The tutoring flow reads from stored `resource_text` and never touches the network. Search is a batch operation, not a live dependency.

**Pipeline per concept:**

```
FOR EACH concept in course:
  │
  ├─► PHASE A: QUERY GENERATION (local, ~0.5s)
  │     ├─ Query 1: Wikipedia-API direct lookup by concept title
  │     ├─ Query 2: Template query: "{concept_title} {module_title} explained"
  │     ├─ Query 3: Template query: "{concept_title} definition examples"
  │     ├─ Query 4 (mastery ≥ 3): "{concept_title} analysis academic"
  │     └─ Query 5 (mastery ≥ 4): "{concept_title} research scholarly"
  │
  ├─► PHASE B: SEARCH + EXTRACT (parallel, ~1-3s per concept)
  │     ├─ Wikipedia-API: Direct Python call, returns clean text, no HTTP scraping
  │     ├─ SearXNG queries 2-5: GET http://searxng:8080/search?q=...&format=json
  │     │   Returns top 5 URLs per query with snippets
  │     ├─ Domain quality filter: tier 1/2/3 scoring, block known bad domains
  │     ├─ De-duplicate URLs across queries
  │     ├─ Take top 5 unique URLs after scoring
  │     └─ trafilatura.extract() on each URL → clean markdown text
  │
  ├─► PHASE C: ASSEMBLY (~0s)
  │     ├─ Combine: Wikipedia text (if found) + top 3 extracted pages
  │     ├─ Truncate combined source material to ~3000 words
  │     ├─ Store source URLs in concept.sources JSON field
  │     └─ Build "Reference Material" block for LLM prompt
  │
  └─► PHASE D: LLM GENERATION (existing CB.2 flow, now with sources)
        ├─ Prompt includes: Course Design Brief + concept metadata +
        │   "REFERENCE MATERIAL:\n{assembled_sources}"
        ├─ Instruction: "Synthesize the reference material into a lesson.
        │   Preserve factual accuracy from sources. Write at {mastery_label}
        │   register. Flag any claim not supported by the references."
        └─ Self-consistency check (3-pass) on generated content
```

### 13.11 SearXNG Docker Integration

Add to `docker-compose.yml`:

```yaml
  searxng:
    image: searxng/searxng:latest
    container_name: helga-searxng
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./configs/searxng:/etc/searxng:rw
    environment:
      - BASE_URL=http://localhost:8080
      - INSTANCE_NAME=helga-search
    deploy:
      resources:
        limits:
          memory: 256M
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
```

**SearXNG configuration** (`configs/searxng/settings.yml`):

```yaml
use_default_settings: true
search:
  formats: [html, json]
  default_lang: en
server:
  limiter: false          # No rate limit for local use
  image_proxy: false      # Don't need images
  port: 8080
  bind_address: "0.0.0.0"
  secret_key: "helga-internal-only"
engines:
  - name: google
    engine: google
    shortcut: g
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
  - name: wikipedia
    engine: wikipedia
    shortcut: wp
  - name: bing
    engine: bing
    shortcut: bi
```

**Container footprint:** ~183MB image, ~200-256MB RAM at runtime. Adds negligible overhead to the Mac Mini's ~2GB total Docker footprint.

### 13.12 Search Research Service Implementation

A new lightweight service (`services/research/research_server.py`) handles all web search and extraction. This keeps network access isolated from core-logic — only the research service has outbound internet.

**Why a separate service (not built into core-logic):**
- Security isolation: core-logic stays network-restricted; only research service has internet
- Can be disabled entirely for fully offline operation (courses generated from parametric knowledge only)
- Can be swapped for a different search backend without touching core-logic
- Independent health monitoring

**Service spec:**

| Property | Value |
|----------|-------|
| Container | `helga-research` |
| Port | 5006 |
| Dependencies | SearXNG (for web search), internet access (for page fetching) |
| Libraries | `trafilatura`, `wikipedia-api`, `aiohttp`, `diskcache`, `flask` |
| Memory limit | 384M |

**API endpoints:**

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/research_concept` | POST | `{title, module_title, course_title, mastery, queries[]}` | `{sources: [{url, title, text, domain_tier}], wikipedia: {text, url}, combined_text: "...", confidence: 0.0-1.0}` |
| `/api/research_batch` | POST | `{concepts: [{title, module_title}], course_title, mastery}` | `{results: {concept_uid: {sources, combined_text, confidence}}}` |
| `/api/cache_stats` | GET | — | `{cached_queries, cached_pages, cache_size_mb}` |
| `/health` | GET | — | `{status, searxng_reachable, cache_entries}` |

**Core implementation (`services/research/research_server.py`):**

```python
import asyncio, aiohttp, hashlib, json, os, time, logging
from flask import Flask, request, jsonify
import trafilatura
import wikipediaapi
from diskcache import Cache

app = Flask(__name__)
logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://helga-searxng:8080")
cache = Cache("/app/data/research_cache")
CACHE_TTL_SEARCH = 86400      # 24 hours for search results
CACHE_TTL_EXTRACT = 604800    # 7 days for extracted page content
wiki = wikipediaapi.Wikipedia(user_agent="Helga/1.0 (Socratic Tutor)", language="en")

# --- Domain quality tiers ---
TIER_1 = {"en.wikipedia.org", "plato.stanford.edu", "ocw.mit.edu",
          "arxiv.org", "www.khanacademy.org", "mathworld.wolfram.com",
          "www.nature.com", "www.britannica.com"}
TIER_2 = {"developer.mozilla.org", "docs.python.org", "realpython.com",
          "www.investopedia.com", "www.sciencedirect.com"}
BLOCKED = {"chegg.com", "coursehero.com", "brainly.com", "quizlet.com",
           "studocu.com", "bartleby.com"}

def domain_tier(url):
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    if domain in BLOCKED: return -1
    if domain in TIER_1 or domain.endswith(".edu") or domain.endswith(".gov"): return 1
    if domain in TIER_2 or domain.startswith("docs."): return 2
    return 3

def cache_key(prefix, text):
    return f"{prefix}:{hashlib.md5(text.encode()).hexdigest()}"

# --- Wikipedia lookup (synchronous, fast, no SearXNG needed) ---
def wiki_lookup(title):
    key = cache_key("wiki", title)
    cached = cache.get(key)
    if cached: return cached
    page = wiki.page(title)
    if page.exists():
        result = {
            "text": page.summary[:2000] + ("\n\n" + page.text[:3000] if len(page.summary) < 500 else ""),
            "url": page.fullurl,
            "title": page.title
        }
        cache.set(key, result, expire=CACHE_TTL_EXTRACT)
        return result
    return None

# --- SearXNG search (async) ---
async def searxng_search(session, query, max_results=5):
    key = cache_key("search", query)
    cached = cache.get(key)
    if cached: return cached
    try:
        async with session.get(f"{SEARXNG_URL}/search", params={
            "q": query, "format": "json", "categories": "general",
            "language": "en", "pageno": 1
        }, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = []
                seen_domains = set()
                for r in data.get("results", [])[:max_results * 2]:
                    url = r.get("url", "")
                    tier = domain_tier(url)
                    if tier == -1: continue  # blocked
                    from urllib.parse import urlparse
                    dom = urlparse(url).netloc
                    if dom in seen_domains: continue
                    seen_domains.add(dom)
                    results.append({
                        "url": url, "title": r.get("title", ""),
                        "snippet": r.get("content", ""), "tier": tier
                    })
                    if len(results) >= max_results: break
                # Sort by tier (1 best)
                results.sort(key=lambda x: x["tier"])
                cache.set(key, results, expire=CACHE_TTL_SEARCH)
                return results
    except Exception as e:
        logger.warning(f"SearXNG search failed for '{query}': {e}")
    return []

# --- Page extraction (async fetch, sync extract) ---
async def extract_page(session, url):
    key = cache_key("page", url)
    cached = cache.get(key)
    if cached: return cached
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                               headers={"User-Agent": "Helga/1.0"}) as resp:
            if resp.status == 200:
                html = await resp.text()
                text = trafilatura.extract(html, output_format="markdown",
                                           include_formatting=True,
                                           include_links=False)
                if text and len(text) > 100:
                    cache.set(key, text, expire=CACHE_TTL_EXTRACT)
                    return text
    except Exception as e:
        logger.warning(f"Extraction failed for {url}: {e}")
    return None

# --- Full research pipeline for one concept ---
async def research_concept(title, module_title, course_title, mastery):
    """Search + extract for a single concept. Returns combined source text."""
    sources = []
    combined_parts = []

    # 1. Wikipedia first (synchronous, fast, highest quality)
    wiki_result = wiki_lookup(title)
    if wiki_result:
        combined_parts.append(f"## Source: Wikipedia — {wiki_result['title']}\n{wiki_result['text']}")
        sources.append({"url": wiki_result["url"], "title": wiki_result["title"],
                        "domain_tier": 1, "type": "wikipedia"})

    # 2. Generate search queries
    queries = [
        f"{title} {module_title} explained",
        f"{title} definition examples",
    ]
    if mastery >= 3:
        queries.append(f"{title} in-depth analysis")
    if mastery >= 4:
        queries.append(f"{title} academic overview research")

    # 3. Search via SearXNG (parallel)
    connector = aiohttp.TCPConnector(limit=10, limit_per_host=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        search_tasks = [searxng_search(session, q) for q in queries]
        all_results = await asyncio.gather(*search_tasks)

        # De-duplicate URLs across queries
        seen_urls = {s["url"] for s in sources}  # Wikipedia already seen
        unique_results = []
        for result_list in all_results:
            for r in result_list:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    unique_results.append(r)

        # Take top 5 by tier
        unique_results.sort(key=lambda x: x["tier"])
        top_results = unique_results[:5]

        # 4. Extract pages (parallel, max 5 concurrent)
        extract_tasks = [extract_page(session, r["url"]) for r in top_results]
        extracted = await asyncio.gather(*extract_tasks)

        for r, text in zip(top_results, extracted):
            if text and len(text) > 100:
                # Truncate individual pages to ~1000 words
                words = text.split()
                if len(words) > 1000:
                    text = " ".join(words[:1000]) + "..."
                combined_parts.append(f"## Source: {r['title']}\n{text}")
                sources.append({"url": r["url"], "title": r["title"],
                                "domain_tier": r["tier"], "type": "web"})

    # 5. Assemble combined text (cap at ~3000 words total)
    combined = "\n\n".join(combined_parts)
    words = combined.split()
    if len(words) > 3000:
        combined = " ".join(words[:3000]) + "\n\n[Truncated for length]"

    # Confidence: based on source quality and quantity
    confidence = min(1.0, (
        (0.4 if wiki_result else 0.0) +
        min(0.6, len(sources) * 0.15)
    ))

    return {
        "sources": sources,
        "combined_text": combined,
        "confidence": confidence,
        "word_count": len(combined.split())
    }

# --- Flask endpoints ---
@app.route("/api/research_concept", methods=["POST"])
def handle_research_concept():
    data = request.json
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(research_concept(
            data["title"], data.get("module_title", ""),
            data.get("course_title", ""), data.get("mastery", 2)
        ))
        return jsonify(result)
    finally:
        loop.close()

@app.route("/api/research_batch", methods=["POST"])
def handle_research_batch():
    data = request.json
    concepts = data.get("concepts", [])
    course_title = data.get("course_title", "")
    mastery = data.get("mastery", 2)

    loop = asyncio.new_event_loop()
    try:
        async def batch():
            sem = asyncio.Semaphore(3)  # Max 3 concepts researched in parallel
            async def bounded(c):
                async with sem:
                    return await research_concept(
                        c["title"], c.get("module_title", ""),
                        course_title, mastery)
            tasks = [bounded(c) for c in concepts]
            return await asyncio.gather(*tasks)

        results = loop.run_until_complete(batch())
        return jsonify({
            "results": {c["title"]: r for c, r in zip(concepts, results)}
        })
    finally:
        loop.close()

@app.route("/api/cache_stats", methods=["GET"])
def handle_cache_stats():
    return jsonify({
        "cached_entries": len(cache),
        "cache_size_mb": round(cache.volume() / 1048576, 1)
    })

@app.route("/health", methods=["GET"])
def health():
    searxng_ok = False
    try:
        import requests as req
        r = req.get(f"{SEARXNG_URL}/healthz", timeout=3)
        searxng_ok = r.status_code == 200
    except: pass
    return jsonify({
        "status": "healthy" if searxng_ok else "degraded",
        "searxng_reachable": searxng_ok,
        "cache_entries": len(cache)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
```

### 13.13 ContentHydrator Integration (Updated CB.2)

The existing CB.2 task now includes a research step before each LLM generation call. The modified hydration flow:

```python
# In ContentHydrator.hydrate_concept():

# 1. Call research service for source material
research = requests.post("http://helga-research:5006/api/research_concept", json={
    "title": concept["title"],
    "module_title": parent_module["title"],
    "course_title": course["title"],
    "mastery": course["mastery"]
}, timeout=30).json()

# 2. Build enhanced prompt with source material
prompt = f"""
{course_design_brief}

CONCEPT: {concept['title']}
BLOOM'S LEVEL: {concept['bloom_level']} ({bloom_name})
LEARNING OBJECTIVES: {concept['objectives']}

REFERENCE MATERIAL (use this as your primary source of facts):
{research['combined_text']}

INSTRUCTIONS:
- Synthesize the reference material into a lesson at {mastery_label} register.
- Preserve factual accuracy from the sources.
- Write {word_count} words of clear educational prose.
- If a claim is NOT supported by the reference material, prefix it with [unverified].
- Include key_terms, examples, takeaways, misconceptions, and analogies.
- Write for a student with {start_label} background knowledge.

Return JSON: {{
  "content": "...",
  "key_terms": [...],
  "examples": [...],
  "takeaways": [...],
  "misconceptions": [...],
  "analogies": [...]
}}
"""

# 3. Store source URLs alongside content
concept.sources = json.dumps(research["sources"])
concept.source_confidence = research["confidence"]
```

**When search returns nothing** (rare topic, SearXNG down, no relevant results): the system falls back to parametric-only generation (same as current behavior) and sets `source_confidence = 0.0`. The quality verification script flags low-confidence concepts for manual review.

**Schema addition** to `concepts` table:

```sql
    sources TEXT DEFAULT '[]',              -- JSON array of {url, title, domain_tier}
    source_confidence REAL DEFAULT 0.0,     -- 0.0 = parametric only, 1.0 = rich sources
```

### 13.14 Performance Analysis

**Timing for a 30-concept course:**

| Step | Per-Concept | 30 Concepts (parallel) | Notes |
|------|------------|----------------------|-------|
| Wikipedia lookup | ~0.3s | ~2s (cached after first) | Synchronous, fast |
| SearXNG queries (3-5) | ~1-2s | ~5-8s (3 concepts parallel) | Async, semaphore=3 |
| Page extraction (5 URLs) | ~1-3s | ~8-15s (parallel within concept) | trafilatura, async fetch |
| DiskCache lookup | ~1ms | negligible | SQLite-backed |
| **Total search phase** | ~2-5s | **~15-25s** | Well under 5-minute target |
| LLM generation (existing) | ~30-60s | ~15-30min (sequential, LLM bound) | Qwen 3 14B at ~10 tok/s |

The search phase adds only 15-25 seconds to a process already dominated by LLM generation time (15-30 minutes). The search runs in parallel with early LLM calls via the producer-consumer pattern — while concept 1 is being generated by the LLM, concepts 2-4 are being researched.

**Cache behavior:** After the first course on a topic, subsequent courses on related topics hit cache heavily. A second course on "Ancient Greek Ethics" after creating one on "Greek Philosophy" would cache-hit on ~60% of searches.

### 13.15 Graceful Degradation

| Failure Scenario | Behavior | User Experience |
|-----------------|----------|-----------------|
| SearXNG container down | Research service returns `confidence: 0.0`, empty sources | Course still generates from parametric knowledge. Progress UI shows "⚠️ Web research unavailable — using built-in knowledge" |
| Internet connection down | Same as above — SearXNG can't reach upstream engines | Same degraded-but-functional behavior |
| trafilatura extraction fails on a URL | Skipped, next URL tried | Fewer sources, slightly lower confidence. No user-visible impact |
| Research service itself down | core-logic catches timeout, proceeds without research | Same as SearXNG down. Logged for monitoring |
| Wikipedia has no article | Skipped, SearXNG results used instead | Slightly lower confidence |
| All searches return zero results | Parametric-only generation, confidence=0.0 | Quality verification flags concept for review |

**The system never blocks on search failure.** Course creation always completes — search augmentation is purely additive.

### 13.16 Updated Sprint Tasks (with Web Search)

The web search integration adds 3 new tasks and modifies CB.2:

| Task | Hours | Priority | Details |
|------|-------|----------|---------|
| WS.1 SearXNG Docker setup | 1 | CRITICAL | Add SearXNG service to docker-compose.yml. Create `configs/searxng/settings.yml`. Verify JSON API returns results. Health check |
| WS.2 Research service | 4 | CRITICAL | New `services/research/research_server.py` + Dockerfile + requirements.txt. Wikipedia-API integration. SearXNG client with async search. trafilatura extraction with domain tier filtering. DiskCache with TTLs. `/api/research_concept`, `/api/research_batch`, `/health` endpoints. Concurrency control (semaphore=3 concepts parallel). Error handling for all failure modes |
| WS.3 Research integration into hydrator | 2 | CRITICAL | Modify CB.2's hydration loop to call research service before LLM generation. Inject combined source text into generation prompt. Store sources and confidence in concepts table. Fallback to parametric-only on research failure. Progress emission: "Researching {concept}..." → "Generating {concept}..." |
| WS.4 Confidence flagging UI | 1 | MEDIUM | In learn.html, concepts with `source_confidence < 0.3` show a subtle indicator: "ℹ️ This content was generated without external sources — verify critical facts." In course structure view, color-code concepts by confidence (green=high, amber=medium, red=low) |
| **Total** | **8** | | |

**CB.2 is modified, not replaced.** The original 5-hour CB.2 estimate now includes the research integration from WS.3 (previously separate):

| Task | Original Hours | Revised Hours |
|------|---------------|---------------|
| CB.2 Hydrator rewrite | 5 | 5 (unchanged — WS.3 covers research integration separately) |
| WS.1-WS.4 (new) | — | 8 |
| **Net addition** | | **+8 hours** |

### 13.17 Updated Phase 3 Timeline (with Web Search)

| Task | Hours |
|------|-------|
| 3.1 KuzuDB → SQLite | 6 |
| CB.1 Skeleton rewrite | 4 |
| CB.2 Hydrator rewrite | 5 |
| CB.3 Embedding pipeline | 1.5 |
| CB.4 Prerequisite validation | 1 |
| CB.5 REST creation endpoint | 1.5 |
| CB.6 Unified creation UI | 3 |
| CB.7 Course card routes | 1.5 |
| CB.8 Content validation | 1 |
| WS.1 SearXNG Docker | 1 |
| WS.2 Research service | 4 |
| WS.3 Research→hydrator integration | 2 |
| WS.4 Confidence flagging UI | 1 |
| 3.3 New RAG endpoints | 2 |
| **Phase 3 Total** | **34.5** |

### 13.18 Files to Create (Web Search)

```
services/research/research_server.py    — Flask server with search + extraction pipeline
services/research/Dockerfile            — Python 3.11 + trafilatura + wikipedia-api + aiohttp + diskcache
services/research/requirements.txt      — trafilatura, wikipedia-api, aiohttp, diskcache, flask
configs/searxng/settings.yml            — SearXNG configuration (JSON output, no limiter)
```

---

## PART 14: INTERACTIVE COURSE CREATOR — DUAL-PATH DESIGN

### 14.1 Two Creation Paths

The courses page offers two clearly distinct buttons:

```
┌──────────────────────────────────────────────────────────────┐
│  My Courses                           [⚡ Quick Create]      │
│                                       [🛠️ Build Custom]     │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                     │
│  │ Course 1│  │ Course 2│  │   + +   │                     │
│  │         │  │         │  │  Empty  │                     │
│  └─────────┘  └─────────┘  └─────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

**Path A — Quick Create (⚡):** Single modal. User types topic + picks depth. LLM generates everything automatically. This is the existing pipeline from Part 13.6 with all the fixes applied. Fast, zero friction, good for "I just want to learn X."

**Path B — Build Custom (🛠️):** Full-page multi-step wizard. User defines the structure with LLM assistance at every level. Each node can be user-defined OR LLM-generated. Clarifying Q&A round before content generation. This is for users who know what they want to learn and want control over curriculum shape.

Both paths converge at the same ContentHydrator (Step 2 of Part 13.6) — they just produce the skeleton differently.

### 14.2 Path A — Quick Create (Improved Modal)

Replaces the current broken modal. Remains a simple overlay on the courses page — no page navigation.

**UI:**
```
┌───────────────────────────────────────┐
│         ⚡ Quick Create Course         │
│                                       │
│  Topic:                               │
│  ┌───────────────────────────────┐    │
│  │ e.g. Classical Mechanics      │    │
│  └───────────────────────────────┘    │
│                                       │
│  Depth:                               │
│  🌱──────🌿──────🌲──────🏔️──────⭐  │
│        ▲ Comprehensive                │
│  ~20 concepts · ~3 hours              │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │        Create Course →          │  │
│  └─────────────────────────────────┘  │
│              Cancel                   │
└───────────────────────────────────────┘
```

**Changes from current:**
- Depth selector becomes a visual slider (not a dropdown) with live estimate of concept count and time
- Topic validation: ≥3 characters, show warning if duplicate title exists
- Submits via `POST /api/create_course` (REST, not broken socket.emit)
- After submit: modal transitions to progress view (reuses same modal, no second modal)
- Progress shows real-time concept list as skeleton is built, then per-concept hydration status
- On completion: "Start Learning →" button appears, sets active course and redirects to `/learn`

No other changes to Path A — all the structural improvements (pedagogical prompts, depth scaling, self-consistency, prerequisites, embeddings) come from the Part 13 CB.1-CB.8 tasks.

### 14.3 Path B — Build Custom: Full-Page Wizard

Navigates to `/courses/new` — a dedicated full-page experience with a step indicator bar.

#### Step 1: Course Setup

**URL:** `/courses/new` (initial state)

**Purpose:** Name, describe, and configure the course. This is where the LLM starts building context about what the user wants.

**UI Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  ← Back to Courses                                           │
│                                                              │
│  Step 1 of 5: Course Setup                                   │
│  ●━━━━━━━━○━━━━━━━━○━━━━━━━━○━━━━━━━━○                      │
│  Setup    Modules   Details   Q&A     Generate               │
│                                                              │
│  ┌─ Course Title ──────────────────────────────────────┐    │
│  │ Classical Mechanics                                  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─ What do you want to get out of this course? ───────┐    │
│  │ I want to understand Newtonian mechanics well enough │    │
│  │ to solve introductory physics problems. I've taken   │    │
│  │ calculus but never had a physics course.             │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  Your background with this topic:                            │
│  ┌──────────┐ ┌──────────────┐ ┌───────────────────┐       │
│  │🌱 New    │ │🌿 Some       │ │🔄 Refreshing      │       │
│  │to this   │ │ background   │ │ knowledge          │       │
│  └──────────┘ └──────────────┘ └───────────────────────┘    │
│       ▲ selected                                             │
│                                                              │
│                          ┌──────────────────────┐            │
│                          │  Next: Define Modules →│           │
│                          └──────────────────────┘            │
└──────────────────────────────────────────────────────────────┘
```

**Fields:**
- **Course title** (required, 3-100 chars)
- **Course goal / description** (optional but encouraged, textarea 3-5 rows). This is the most important context signal — the LLM uses it to tailor module suggestions, question framing, and depth calibration
- **Prior knowledge level** (3 radio pills: New / Some background / Refreshing)

**State stored client-side:**
```javascript
const courseBuilder = {
    title: "",
    description: "",
    prior_knowledge: "new",  // "new" | "some" | "refreshing"
    modules: [],             // populated in Step 2
    clarification_answers: {} // populated in Step 4
};
```

#### Step 2: Module Outline

**Purpose:** Define the top-level structure. User creates module stubs. Each module can be user-titled or LLM-suggested.

**UI Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  Step 2 of 5: Module Outline                                 │
│  ○━━━━━━━━●━━━━━━━━○━━━━━━━━○━━━━━━━━○                      │
│                                                              │
│  Define the main sections of your course.                    │
│  Add modules manually, or let Helga suggest a structure.     │
│                                                              │
│  ┌─ Module 1 ──────────────────────────────── ✕ ────┐       │
│  │  Title: [ Kinematics: Motion in 1D and 2D       ]│       │
│  │  Note to Helga: (optional)                        │       │
│  │  ┌──────────────────────────────────────────────┐│       │
│  │  │ Focus on projectile motion and relative      ││       │
│  │  │ velocity. Skip rotational stuff for now.     ││       │
│  │  └──────────────────────────────────────────────┘│       │
│  └───────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Module 2 ──────────────────────────────── ✕ ────┐       │
│  │  Title: [ Newton's Laws and Forces              ]│       │
│  │  Note to Helga: (optional)                        │       │
│  │  ┌──────────────────────────────────────────────┐│       │
│  │  │ Include friction and inclined planes.        ││       │
│  │  │ I struggled with free body diagrams before.  ││       │
│  │  └──────────────────────────────────────────────┘│       │
│  └───────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Module 3 ──────────────────────────────── ✕ ────┐       │
│  │  Title: [ Work, Energy, and Conservation Laws   ]│       │
│  │  Note to Helga: (optional)                        │       │
│  │  ┌──────────────────────────────────────────────┐│       │
│  │  │                                              ││       │
│  │  └──────────────────────────────────────────────┘│       │
│  └───────────────────────────────────────────────────┘       │
│                                                              │
│  [+ Add Module]    [✨ Suggest Modules from Helga]           │
│                                                              │
│  ↑↓ Drag to reorder                                         │
│                                                              │
│  ┌────────────┐              ┌────────────────────┐         │
│  │ ← Back     │              │ Next: Add Details → │         │
│  └────────────┘              └────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

**Interactions:**

| Action | What Happens |
|--------|-------------|
| **+ Add Module** | Appends empty module card with title input + notes textarea |
| **✨ Suggest Modules** | Calls `POST /api/suggest_modules` with `{title, description, prior_knowledge}`. LLM returns 3-6 module suggestions. Each appears as a card the user can accept (✓), edit, or dismiss (✕). User can accept some and add their own |
| **✕ on module card** | Removes that module (confirm if it has content) |
| **↑↓ Drag reorder** | HTML5 drag-and-drop to reorder modules. Order = teaching sequence |
| **Note to Helga** | Free-text textarea per module. Passed to LLM during content generation as `user_guidance`. Examples: "Focus on practical examples", "Skip the math derivations", "I already know this basics, go deeper" |

**Suggest Modules prompt (backend):**
```
You are designing a curriculum for a student.

Course: {title}
Student's goal: {description}
Student's background: {prior_knowledge}

Suggest {depth_scaled_count} modules that would form a logical learning progression for this course.
Order them from foundational to advanced.

Return JSON only:
[
  {"title": "Module Name", "description": "What this module covers (1 sentence)"},
  ...
]
```

**Validation:** At least 1 module required. Each module must have a title ≥3 chars.

#### Step 3: Drill Down (Optional Detail)

**Purpose:** For each module, the user can optionally define lessons/concepts — or leave them for the LLM. This is the key "user controls structure, LLM fills gaps" interaction.

**UI Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  Step 3 of 5: Add Details (Optional)                         │
│  ○━━━━━━━━○━━━━━━━━●━━━━━━━━○━━━━━━━━○                      │
│                                                              │
│  Expand any module to add specific lessons or concepts.      │
│  Anything you leave empty, Helga will generate for you.      │
│                                                              │
│  ┌─ 📦 Module 1: Kinematics ──────────────── [Expand ▼] ─┐  │
│  │                                                         │  │
│  │  ┌─ Concept 1 ─────────────────────────── ✕ ──┐       │  │
│  │  │ Title: [ Displacement vs Distance          ]│       │  │
│  │  │ Note:  [ Make sure to distinguish vectors  ]│       │  │
│  │  └─────────────────────────────────────────────┘       │  │
│  │                                                         │  │
│  │  ┌─ Concept 2 ─────────────────────────── ✕ ──┐       │  │
│  │  │ Title: [ Velocity and Acceleration         ]│       │  │
│  │  │ Note:  [                                   ]│       │  │
│  │  └─────────────────────────────────────────────┘       │  │
│  │                                                         │  │
│  │  [+ Add Concept]  [✨ Suggest Concepts]                 │  │
│  │                                                         │  │
│  │  ── Helga will also generate: ──                        │  │
│  │  🤖 ~2-3 additional concepts to complete this module    │  │
│  │     (based on depth setting and your notes)             │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 📦 Module 2: Newton's Laws ──────────── [Expand ▼] ─┐  │
│  │  No concepts defined yet.                               │  │
│  │  🤖 Helga will generate all concepts for this module.   │  │
│  │  [+ Add Concept]  [✨ Suggest Concepts]                 │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 📦 Module 3: Work & Energy ─────────── [Expand ▼] ─┐   │
│  │  (collapsed — click to expand)                         │   │
│  └─────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────┐              ┌──────────────────────────┐   │
│  │ ← Back     │              │ Next: Helga's Questions → │   │
│  └────────────┘              └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Key Design Principles:**

1. **Every module is collapsible.** Default: collapsed. Click to expand and add concepts. Modules with no user-defined concepts show "🤖 Helga will generate all concepts for this module."
2. **Hybrid at every level.** User can define 2 of 5 concepts and let the LLM generate the remaining 3. The UI shows: "── Helga will also generate: ~N additional concepts ──" based on the depth setting minus user-defined count.
3. **Notes are the power feature.** Every module card and every concept card has a "Note to Helga" textarea. This is free-text guidance that gets injected into the LLM's content generation prompt. Examples:
   - Module-level: "Focus on real-world applications, not derivations"
   - Concept-level: "I always confuse this with X, make sure to address that"
   - Concept-level: "Include a worked example with numbers"
4. **✨ Suggest Concepts** per module calls `POST /api/suggest_concepts` with `{course_title, course_description, module_title, module_note, existing_concepts[], prior_knowledge}`. Returns concept suggestions the user can accept/edit/dismiss.

**Suggest Concepts prompt (backend):**
```
You are designing the internal structure of a course module.

Course: {title}
Course goal: {description}
Student background: {prior_knowledge}
Module: {module_title}
Module guidance from student: {module_note}
Already defined concepts: {existing_concept_titles}

Suggest {remaining_count} additional concepts that would complete this module.
Order from foundational to advanced within the module.
Do not duplicate or overlap with already-defined concepts.

Return JSON only:
[
  {"title": "Concept Name", "description": "One sentence about what this covers"},
  ...
]
```

**Data model at this point:**
```javascript
courseBuilder.modules = [
    {
        title: "Kinematics",
        note: "Focus on projectile motion...",
        user_defined: true,
        concepts: [
            { title: "Displacement vs Distance", note: "Distinguish vectors...", user_defined: true },
            { title: "Velocity and Acceleration", note: "", user_defined: true }
        ]
        // LLM will fill in remaining concepts
    },
    {
        title: "Newton's Laws",
        note: "Include friction and inclined planes...",
        user_defined: true,
        concepts: []
        // LLM generates ALL concepts for this module
    }
];
```

#### Step 4: Helga's Clarifying Questions

**Purpose:** Before generating content, the LLM asks 3-5 smart questions based on everything the user has provided. This closes gaps in context and significantly improves content quality. This is the most differentiated feature — no other tutoring product does this.

**How it works:**

1. Frontend sends the complete `courseBuilder` object to `POST /api/clarify_course`
2. Backend assembles full context and prompts the LLM to generate clarifying questions
3. User answers each question in-place
4. Answers are stored and injected into every content generation prompt

**UI Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  Step 4 of 5: Helga's Questions                              │
│  ○━━━━━━━━○━━━━━━━━○━━━━━━━━●━━━━━━━━○                      │
│                                                              │
│  Before I build your course, I have a few questions to       │
│  make sure I create exactly what you need.                   │
│                                                              │
│  ┌─ Question 1 ──────────────────────────────────────┐      │
│  │  You mentioned you've taken calculus. Should I     │      │
│  │  include calculus-based derivations (integrals,    │      │
│  │  derivatives) or keep it algebra-based?            │      │
│  │                                                    │      │
│  │  ┌──────────────────────────────────────────────┐ │      │
│  │  │ Calculus-based is fine, that's what I need   │ │      │
│  │  │ for my physics class.                        │ │      │
│  │  └──────────────────────────────────────────────┘ │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  ┌─ Question 2 ──────────────────────────────────────┐      │
│  │  For the Work & Energy module, do you want to      │      │
│  │  cover just mechanical energy, or also include     │      │
│  │  thermal energy and thermodynamics basics?         │      │
│  │                                                    │      │
│  │  ┌──────────────────────────────────────────────┐ │      │
│  │  │ Just mechanical for now.                     │ │      │
│  │  └──────────────────────────────────────────────┘ │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  ┌─ Question 3 ──────────────────────────────────────┐      │
│  │  You said you struggled with free body diagrams.   │      │
│  │  Should I add an extra dedicated concept for FBD   │      │
│  │  practice before moving to Newton's Laws?          │      │
│  │                                                    │      │
│  │  ┌──────────────────────────────────────────────┐ │      │
│  │  │ Yes please, that would help a lot.           │ │      │
│  │  └──────────────────────────────────────────────┘ │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  ┌─ Question 4 ──────────────────────────────────────┐      │
│  │  Do you have a preference for SI units only, or    │      │
│  │  should I include imperial unit conversions?       │      │
│  │                                                    │      │
│  │  ┌──────────────────────────────────────────────┐ │      │
│  │  │ SI only.                                     │ │      │
│  │  └──────────────────────────────────────────────┘ │      │
│  └────────────────────────────────────────────────────┘      │
│                                                              │
│  (All questions optional — skip any you don't care about)    │
│                                                              │
│  ┌────────────┐              ┌──────────────────────────┐   │
│  │ ← Back     │              │ Generate My Course →  🚀  │   │
│  └────────────┘              └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Clarifying Questions prompt (backend):**
```
You are preparing to build a custom educational course. Review everything the student has provided and ask 3-5 clarifying questions that will help you create the best possible content.

Course title: {title}
Student's goal: {description}
Student's background: {prior_knowledge}
Modules defined by student:
{for each module:}
  - {module.title} (note: {module.note})
    Concepts: {concept titles or "LLM will generate"}
{end for}

Rules for your questions:
1. Ask about SCOPE boundaries — what to include/exclude
2. Ask about DEPTH preferences — math level, theoretical vs practical
3. Ask about SPECIFIC STRUGGLES the student mentioned in their notes
4. Ask about CONTEXT — is this for a class, self-study, professional development?
5. If the student left modules with no concepts, ask what they expect in those modules
6. Do NOT ask about things already clearly stated in the description or notes

Return JSON only:
[
  {"question": "Your question text", "context": "Why this matters for course quality"},
  ...
]
```

**Key design decisions:**
- Questions are generated ONCE when the user arrives at Step 4. Not regenerated on back-navigation.
- All questions are optional — skip button per question. Unanswered questions are omitted from context.
- Answers are free-text (1-3 sentences each). No multiple choice — we want natural language context.
- The "context" field from the JSON is shown as a small muted subtitle under each question so the user understands why it matters.

#### Step 5: Generate (Progress View)

**Purpose:** Skeleton is built incorporating all user structure + LLM-filled gaps + clarifying answers. Content is hydrated. Same progress UI as Quick Create but with richer feedback because we have the user's structure to show.

**UI Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  Step 5 of 5: Building Your Course                           │
│  ○━━━━━━━━○━━━━━━━━○━━━━━━━━○━━━━━━━━●                      │
│                                                              │
│  Creating: Classical Mechanics                               │
│  ████████████████████░░░░░░░░  62%                           │
│                                                              │
│  📦 Module 1: Kinematics                          ✅         │
│     ├─ Displacement vs Distance (your concept)     ✅        │
│     ├─ Velocity and Acceleration (your concept)    ✅        │
│     ├─ Projectile Motion (generated)               🔄        │
│     └─ Relative Velocity (generated)               ⏳        │
│                                                              │
│  📦 Module 2: Newton's Laws                       🔄         │
│     ├─ Free Body Diagrams (added from Q&A)         ⏳        │
│     ├─ Newton's First Law (generated)              ⏳        │
│     ├─ Newton's Second Law (generated)             ⏳        │
│     └─ Friction and Inclined Planes (generated)    ⏳        │
│                                                              │
│  📦 Module 3: Work & Energy                       ⏳         │
│     └─ (concepts will appear as generated)                   │
│                                                              │
│  Currently: Generating content for "Projectile Motion"...    │
│                                                              │
│  [Cancel]                                                    │
└──────────────────────────────────────────────────────────────┘
```

**Progress states per item:** ⏳ Pending → 🔄 Generating → ✅ Complete → ❌ Failed

**Concept labels:** "(your concept)" for user-defined items, "(generated)" for LLM-created, "(added from Q&A)" for concepts added based on clarifying question answers.

**On completion:**
```
┌──────────────────────────────────────────────────────────────┐
│  ✅ Course Created Successfully!                              │
│                                                              │
│  Classical Mechanics                                         │
│  3 modules · 12 concepts · ~2.5 hours estimated             │
│                                                              │
│  ┌──────────────────────┐  ┌─────────────────────────┐     │
│  │  Start Learning →  🚀 │  │  View Structure  📊     │     │
│  └──────────────────────┘  └─────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

### 14.4 Backend: Custom Creation Pipeline

The guided wizard submits a single payload at Step 5 via `POST /api/create_course_custom`:

```javascript
// Payload sent from frontend at Step 5
{
    "title": "Classical Mechanics",
    "description": "I want to understand Newtonian mechanics...",
    "prior_knowledge": "some",
    "modules": [
        {
            "title": "Kinematics",
            "note": "Focus on projectile motion...",
            "concepts": [
                {"title": "Displacement vs Distance", "note": "Distinguish vectors..."},
                {"title": "Velocity and Acceleration", "note": ""}
            ]
        },
        {
            "title": "Newton's Laws",
            "note": "Include friction and inclined planes. I struggled with FBDs.",
            "concepts": []  // LLM generates all
        },
        {
            "title": "Work & Energy",
            "note": "",
            "concepts": []
        }
    ],
    "clarification_answers": [
        {"question": "Calculus-based derivations?", "answer": "Yes, calculus-based."},
        {"question": "Mechanical energy only?", "answer": "Just mechanical for now."},
        {"question": "Extra FBD concept?", "answer": "Yes please."},
        {"question": "SI units only?", "answer": "SI only."}
    ]
}
```

**Backend pipeline (core-logic):**

```
POST /api/create_course_custom
  │
  ├─► Step A: BUILD CONTEXT DOCUMENT
  │     Assemble a "Course Design Brief" from all user inputs:
  │     
  │     """
  │     COURSE DESIGN BRIEF
  │     Title: Classical Mechanics
  │     Goal: I want to understand Newtonian mechanics...
  │     Student background: Some prior knowledge
  │     
  │     STUDENT'S MODULE PLAN:
  │     Module 1 — Kinematics
  │       Student note: "Focus on projectile motion..."
  │       Student-defined concepts: Displacement vs Distance (note: "Distinguish vectors"), 
  │                                  Velocity and Acceleration
  │       → LLM should generate ~2-3 additional concepts for this module
  │     
  │     Module 2 — Newton's Laws
  │       Student note: "Include friction and inclined planes. I struggled with FBDs."
  │       Student-defined concepts: none
  │       → LLM should generate all concepts (~4-5) for this module
  │     
  │     Module 3 — Work & Energy
  │       Student note: (none)
  │       Student-defined concepts: none
  │       → LLM should generate all concepts (~4-5) for this module
  │     
  │     CLARIFYING Q&A:
  │     Q: Calculus-based derivations? → A: Yes, calculus-based
  │     Q: Mechanical energy only? → A: Just mechanical for now
  │     Q: Extra FBD concept? → A: Yes please
  │     Q: SI units only? → A: SI only
  │     """
  │     
  │     This brief is injected into EVERY subsequent LLM call
  │     as system context — it never gets lost or truncated.
  │
  ├─► Step B: SKELETON GENERATION (per module)
  │     For modules where user defined NO concepts:
  │       LLM generates full concept list with brief as context
  │     For modules where user defined SOME concepts:
  │       LLM generates remaining concepts, respecting user's as anchors
  │     For Q&A-driven additions (e.g., "add FBD concept"):
  │       LLM inserts at appropriate position in relevant module
  │     
  │     Per-module prompt:
  │     """
  │     {course_design_brief}
  │     
  │     Generate concepts for Module: {module_title}
  │     Student guidance: {module_note}
  │     Already defined by student: {user_concept_titles}
  │     
  │     Generate {needed_count} additional concepts.
  │     Each concept needs: title, learning_objectives, bloom_level, 
  │     prerequisites (referencing other concept titles).
  │     
  │     IMPORTANT: 
  │     - Respect the student's notes and Q&A answers
  │     - Don't duplicate student-defined concepts
  │     - Order so prerequisites come before dependents
  │     - {prior_knowledge}-appropriate vocabulary
  │     
  │     Return JSON: [{"title": "...", "objectives": [...], 
  │                     "bloom_level": 1-6, "prerequisites": [...]}]
  │     """
  │     
  │     Write all concepts to SQLite (user-defined + generated)
  │     Mark each concept: source = "user" or "generated" or "qa_added"
  │     Emit STRUCT progress events per concept
  │
  ├─► Step C: CONTENT HYDRATION (same as Part 13.6 Step 2)
  │     For EACH concept, generate content with the design brief
  │     injected as additional context:
  │     
  │     """
  │     {standard content generation prompt from CB.2}
  │     
  │     ADDITIONAL CONTEXT FROM STUDENT:
  │     Course goal: {description}
  │     Student background: {prior_knowledge}
  │     Module note: {module_note}
  │     Concept note: {concept_note}  ← user's per-concept guidance
  │     Student Q&A answers: {relevant_qa_answers}
  │     
  │     Tailor your content to this student's specific needs.
  │     """
  │     
  │     Self-consistency check, embedding generation, etc.
  │
  ├─► Step D: VALIDATION (same as Part 13.6 Step 3)
  │
  └─► Step E: NOTIFY UI
```

**Key insight: the "Course Design Brief" is the context strategy.** It's assembled once from all user inputs and injected into every LLM call (skeleton generation per module, content generation per concept, misconception generation, analogy generation). This means the user's notes, Q&A answers, and structural decisions propagate everywhere without being lost. The brief is also stored in the `courses` table as `design_brief TEXT` for potential re-generation later.

### 14.5 Schema Addition

Add to `courses` table:
```sql
    design_brief TEXT,          -- assembled context document from wizard
    creation_mode TEXT,         -- 'quick' or 'custom'
```

Add to `concepts` table:
```sql
    source TEXT DEFAULT 'generated',  -- 'user' | 'generated' | 'qa_added'
    user_note TEXT DEFAULT '',        -- user's guidance note for this concept
```

### 14.6 New API Endpoints

| Endpoint | Method | Purpose | Request | Response |
|----------|--------|---------|---------|----------|
| `/api/suggest_modules` | POST | LLM suggests modules for Step 2 | `{title, description, prior_knowledge}` | `{modules: [{title, description}]}` |
| `/api/suggest_concepts` | POST | LLM suggests concepts for a module in Step 3 | `{title, description, prior_knowledge, module_title, module_note, existing_concepts[]}` | `{concepts: [{title, description}]}` |
| `/api/clarify_course` | POST | LLM generates clarifying questions for Step 4 | Full `courseBuilder` object | `{questions: [{question, context}]}` |
| `/api/create_course_custom` | POST | Trigger custom course generation (Step 5) | Full `courseBuilder` object with answers | `{course_uid, status: "building"}` + Socket.IO progress events |

These are in addition to the existing `POST /api/create_course` for Path A (Quick Create).

### 14.7 UI Architecture: Full-Page Wizard vs. Modal

**Why full-page, not modal:**
- Steps 2-4 require significant screen real estate (module cards, concept trees, Q&A form)
- Users need to see their growing structure while editing
- Modals create a sense of "I can't go back" — a page with URL-based steps (`/courses/new?step=2`) feels like a document you're building
- Browser back button works naturally

**Route:** `/courses/new` — a single page with client-side step management (no page reload between steps). Step state in URL query param for bookmarkability and browser back support.

**Navigation:** Step indicator bar at top with clickable completed steps. "Back" and "Next" buttons at bottom. "Next" validates current step before advancing. User can click any completed step to return and edit.

**Mobile responsive:** Steps stack vertically. Module cards become full-width. Notes textareas expand. Step indicator becomes a compact "Step 2 of 5" text instead of the full bar.

### 14.8 Sprint Tasks for Interactive Course Creator

| Task | Hours | Priority | Details |
|------|-------|----------|---------|
| CC.1 Create `/courses/new` page template | 4 | CRITICAL | Full-page wizard with 5 steps. Step indicator bar. Client-side step management. All form fields per §14.3. Module cards with add/remove/reorder. Concept cards within modules. Notes textareas on every card. Responsive layout |
| CC.2 Wizard JavaScript (step logic + state) | 3 | CRITICAL | `courseBuilder` state object. Step validation and advancement. Add/remove module/concept cards. Drag-and-drop reorder for modules. Expand/collapse modules in Step 3. Estimated counts display. URL query param sync |
| CC.3 "✨ Suggest" feature (modules + concepts) | 2 | HIGH | `POST /api/suggest_modules` and `POST /api/suggest_concepts` endpoints in core-logic. LLM prompts per §14.3. Frontend: call API, render suggestion cards with accept/edit/dismiss buttons. Loading spinner during generation |
| CC.4 Clarifying Q&A (Step 4) | 2 | HIGH | `POST /api/clarify_course` endpoint. LLM prompt per §14.3. Frontend: render question cards with answer textareas. "Skip" option per question. Store answers in courseBuilder state |
| CC.5 Custom creation backend pipeline | 3 | CRITICAL | `POST /api/create_course_custom` endpoint. Assemble Course Design Brief from payload. Per-module skeleton generation with user concepts as anchors. Inject brief into all hydration prompts. Q&A-driven concept insertion. Mark concept.source. Store design_brief in courses table |
| CC.6 Step 5 progress UI | 1.5 | HIGH | Real-time progress tree showing user-defined vs generated concepts. Per-item status icons (⏳🔄✅❌). Source labels. Completion card with "Start Learning" button |
| CC.7 Update Quick Create modal | 1 | MEDIUM | Improve depth slider visual. Add topic validation. Fix REST submission (already in CB.5). Transition to progress view within same modal |
| CC.8 Route + navigation | 1 | CRITICAL | Add `GET /courses/new` route to web-ui. "Build Custom" button on courses page. Update courses page to show both creation buttons. Breadcrumb navigation |
| CC.9 Add web-ui proxy routes | 1 | HIGH | Proxy `/api/suggest_modules`, `/api/suggest_concepts`, `/api/clarify_course`, `/api/create_course_custom` from web-ui to core-logic |
| **Total** | **18.5** | | |

### 14.9 Where This Fits in the Sprint

The course creator wizard depends on:
- Phase 1 complete (SQLite, Ollama connection)
- Phase 2 tasks 2.3-2.4 complete (new prompts + LLM client)
- Phase 3 CB.1-CB.2 complete (skeleton builder + hydrator rewrite)

It can execute in parallel with Phase 4 UI work (different pages). Recommended placement: **alongside Phase 4a-4b (Days 10-13)** since it's primarily a frontend + new-endpoints task.

**Updated Phase 3 scope:** CB.5 (REST creation endpoint) should support BOTH `POST /api/create_course` (quick) and `POST /api/create_course_custom` (guided). The hydration pipeline is shared.

### 14.10 Three-Slider Parameter System (Replacing Depth)

The single "Depth 1-5" parameter is replaced by three independent sliders. Full specification is in the Verification Guide §12. Summary of impact on sprint tasks:

#### Schema Change (affects task 3.1)

Replace `depth INTEGER` in courses table with:
```sql
    scope INTEGER DEFAULT 3 CHECK(scope BETWEEN 1 AND 5),
    mastery INTEGER DEFAULT 2 CHECK(mastery BETWEEN 1 AND 5),
    starting_from INTEGER DEFAULT 1 CHECK(starting_from BETWEEN 1 AND 5),
```

#### Skeleton Builder Change (affects task CB.1)

The LLM prompt's "Depth guide" section is replaced with a "Course Configuration Block" that derives module count, concepts-per-module, Bloom's range, content register, and vocabulary level from the three parameters. Node counts are guidelines, not hard caps — the LLM generates as many modules and concepts as the topic naturally requires at the given scope.

Key formula:
- **Modules** ≈ `{1:3, 2:4, 3:6, 4:8, 5:11}[scope]` (guideline, ±30%)
- **Concepts/module** ≈ `{1:3, 2:4, 3:5, 4:7, 5:10}[mastery]` (guideline, ±30%)
- **Bloom's floor** = `{1:1, 2:1, 3:2, 4:3, 5:4}[starting_from]`
- **Bloom's ceiling** = `{1:2, 2:3, 3:4, 4:5, 5:6}[mastery]`
- **Words/concept** ≈ `{1:150, 2:250, 3:400, 4:600, 5:800}[mastery]`
- **No hard node caps.** Total concepts = modules × concepts/module (uncapped)

#### Content Hydration Change (affects task CB.2)

Content register (vocabulary, tone, depth of explanation) scales with mastery level:
- Mastery 1: Explanatory, no jargon, all terms defined immediately
- Mastery 3: Analytical, field terminology expected, citations by name
- Mastery 5: Scholarly, graduate-level register, engagement with debates

Starting_from controls what gets skipped:
- Start 1: Full foundation, begin with "What is X?"
- Start 3: Compress intro to 1-2 review concepts
- Start 5: Skip to advanced content, expert-to-expert

#### UI Change (affects tasks CC.1, CC.7)

Both Quick Create modal and Wizard Step 1 show three visual sliders instead of one dropdown. Each slider has labeled ticks and a live estimate panel showing projected module count, concept count, estimated time, and Bloom's range.

#### Course Structure Standard (affects tasks CB.1, CB.5)

Courses must follow academic ordering:
1. Modules are topologically ordered (Module N can reference N-1, not vice versa)
2. Within modules, concepts progress simple → complex
3. Bloom's levels ascend across the course (early modules lower, later modules higher)
4. Every module except the first begins with a bridge concept referencing the prior module
5. Prerequisites form a DAG (no cycles)
6. No orphan concepts (every concept connects to at least one other)
7. Node limits removed — courses are as long as they need to be

#### Quality Test Addition (affects task 6.4)

A course quality verification script (`verify_course_quality.py`) tests every generated course against 20 measurable standards (10 structural, 10 content). Three benchmark courses (Greek Philosophy, Machine Learning, Sourdough Baking) serve as regression tests. Full spec in Verification Guide §13.

**Revised sprint total with course creator:**

| Phase | Focus | Hours |
|-------|-------|-------|
| Phase 1 | Infrastructure | 12-16 |
| Phase 2 | Core logic + tutoring flow | 34-42 |
| Phase 3 | RAG + course creation pipeline | 24-28 |
| Phase 4a | CSS, base, home, courses page, wizard | 15 |
| Phase 4b | Learn/Review/Test, session.js, wizard JS | 21 |
| Phase 4c | Routes (incl CC.9), gamification, settings | 8 |
| Phase 4d | Suggest + Q&A features | 7 |
| TTS | Kokoro | 4-6 |
| Phase 5 | Caching, streaming | 6-8 |
| Phase 6 | Security, tests, deployment, docs | 10-14 |
| Acceptance | Final testing + 3 benchmark courses | 4-6 |
| **Total** | | **145-172 hrs** |

### 14.11 Course Creation UI — Complete Visual Design

Both creation paths share a unified visual language: a real-time "course construction" experience that shows the user exactly what's happening at every stage. The visual system uses three distinct phases — **Designing** (skeleton), **Researching** (web search), and **Writing** (content generation) — each with its own visual treatment.

#### Socket.IO Events (Backend → Frontend)

The backend emits these events during creation. All UI animations are driven by these events:

| Event | Payload | When Fired |
|-------|---------|------------|
| `creation:phase` | `{phase: "skeleton"|"research"|"hydration"|"validation"|"complete"|"error", message: "..."}` | On major phase transition |
| `creation:module` | `{uid, title, ordinal, status: "created"|"complete"}` | When a module is added to skeleton |
| `creation:concept` | `{uid, title, module_uid, ordinal, source: "user"|"generated"|"qa_added", status: "pending"|"researching"|"writing"|"complete"|"error"}` | When a concept is created or changes status |
| `creation:research` | `{concept_uid, sources_found: int, wikipedia: bool, confidence: float}` | When research completes for a concept |
| `creation:progress` | `{percent: 0-100, current_action: "..."}` | Periodic progress update |
| `creation:complete` | `{course_uid, title, modules: int, concepts: int, estimated_hours: float}` | Course ready |
| `creation:error` | `{message: "...", recoverable: bool}` | On failure |

#### Quick Create — Full Modal Animation Sequence

**Phase 1: Input Form (initial state)**

```
┌─────────────────────────────────────────────────┐
│            ⚡ Quick Create Course                 │
│                                                  │
│  Topic                                           │
│  ┌────────────────────────────────────────┐      │
│  │ Greek Philosophy                       │      │
│  └────────────────────────────────────────┘      │
│                                                  │
│  Scope — How much to cover                       │
│  🔬━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━🌍       │
│  Focused        Standard        Comprehensive    │
│                                       ▲          │
│                                                  │
│  Mastery — How deep to go                        │
│  📖━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━🎓       │
│  Awareness                       Expertise       │
│  ▲                                               │
│                                                  │
│  Starting from                                   │
│  🌱━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━🧠       │
│  No background                    Advanced       │
│  ▲                                               │
│                                                  │
│  ┌──────────────────────────────────────┐       │
│  │ 📊 ~33 concepts · ~5 hrs · 11 mods  │       │
│  │    Bloom's 1-2 · Awareness level     │       │
│  └──────────────────────────────────────┘       │
│                                                  │
│  ┌──────────────────────────────────────┐       │
│  │          Create Course →              │       │
│  └──────────────────────────────────────┘       │
│                 Cancel                           │
└─────────────────────────────────────────────────┘
```

**Slider interaction details:**
- Each slider has labeled ticks at positions 1-5
- Thumb shows the current label text (e.g., "Comprehensive")
- Moving any slider triggers a live recalculation of the estimate panel
- Estimate panel (`📊`) uses a subtle background pulse animation when values change (0.3s ease)
- Sliders use Alpine palette: track in `var(--border-color)`, filled portion in `var(--accent-primary)`, thumb in `var(--accent-primary)` with shadow

**Phase 2: Transition (user clicks "Create Course →")**

The form fields fade out (0.3s), the modal smoothly expands in height (0.4s ease-in-out), and the progress view fades in (0.3s). The "Create Course →" button transforms into a "Cancel" button at the bottom. Total transition: 0.7s.

**Phase 3: Skeleton Building (~30-60s)**

```
┌─────────────────────────────────────────────────┐
│       🏔️ Building: Greek Philosophy              │
│                                                  │
│  ┌─ Phase 1 of 3: Designing Curriculum ─────┐   │
│  │  ████████████████░░░░░░░░░░░  48%         │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ┌─ Course Map ──────────────────────────────┐   │
│  │                                            │   │
│  │  📦 Pre-Socratic Thinkers          ✅     │   │
│  │     ├─ Thales and the Milesians     ·      │   │
│  │     ├─ Heraclitus and Change        ·      │   │
│  │     └─ Parmenides and Being         ·      │   │
│  │                                            │   │
│  │  📦 Socrates                        ✅     │   │
│  │     ├─ The Socratic Method          ·      │   │
│  │     ├─ Ethics and Virtue            ·      │   │
│  │     └─ The Trial and Death          ·      │   │
│  │                                            │   │
│  │  📦 Plato                      🔄 building │   │
│  │     ├─ Theory of Forms              ·      │   │
│  │     └─ ... (concepts appearing)     ·      │   │
│  │                                            │   │
│  │  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   │   │
│  │  (more modules will appear)                │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  Designing module structure...                    │
│                                                  │
│  [Cancel]                                        │
└─────────────────────────────────────────────────┘
```

**Animation details for skeleton phase:**
- Module cards slide in from the left (0.25s ease-out, staggered 100ms per module)
- As each module is emitted by the backend, it appears with a subtle scale-up animation (0.95→1.0)
- Concepts within a module appear one by one with a fade-in (0.2s), connected by a vertical line (CSS `border-left`)
- Concept dots (·) are small circles in `var(--border-color)` — they'll become status icons in the next phase
- The "Course Map" scrolls to keep the latest addition visible
- Phase label at top: "Phase 1 of 3: Designing Curriculum" with a 🧠 brain icon that gently pulses
- Progress bar uses a striped animation (CSS `background-size` animation) to show activity

**Phase 4: Web Research (~15-25s)**

The phase transitions: phase bar slides from "Designing Curriculum" to "Researching Sources." The module/concept tree stays visible. Now each concept gets a research status treatment:

```
┌─────────────────────────────────────────────────┐
│       🏔️ Building: Greek Philosophy              │
│                                                  │
│  ┌─ Phase 2 of 3: Researching Sources ──────┐   │
│  │  ████████░░░░░░░░░░░░░░░░░░░  28%         │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ┌─ Course Map ──────────────────────────────┐   │
│  │                                            │   │
│  │  📦 Pre-Socratic Thinkers                  │   │
│  │     ├─ Thales and the Milesians  🔍 3 srcs │   │
│  │     │   📄 Wikipedia ✓  🌐 plato.stanford  │   │
│  │     ├─ Heraclitus and Change     🔍 2 srcs │   │
│  │     │   📄 Wikipedia ✓  🌐 britannica.com  │   │
│  │     └─ Parmenides and Being      🔎 ...    │   │
│  │                                            │   │
│  │  📦 Socrates                               │   │
│  │     ├─ The Socratic Method       ⏳ queued  │   │
│  │     ├─ Ethics and Virtue         ⏳ queued  │   │
│  │     └─ The Trial and Death       ⏳ queued  │   │
│  │                                            │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  🔎 Researching "Parmenides and Being"...        │
│     Searching Wikipedia, Google, DuckDuckGo      │
│                                                  │
│  [Cancel]                                        │
└─────────────────────────────────────────────────┘
```

**Animation details for research phase:**
- Each concept transitions from `·` (dot) to `🔎` (magnifying glass, animated gentle bounce) when being researched
- When research completes: `🔎` → `🔍 N srcs` (N sources found), green flash on the row (0.3s)
- Below the concept name, a small indented line shows source icons: 📄 for Wikipedia, 🌐 for web sources, with domain name in muted text. This line slides down into view (0.2s)
- Concepts waiting show `⏳ queued` in muted text
- The status line at the bottom of the modal shows the current action with a typing-dot animation: "Searching Wikipedia, Google, DuckDuckGo" with dots cycling
- If a concept gets zero sources: `⚠️ 0 srcs` in amber — still continues, just flagged

**Phase 5: Content Writing (~3-5 min)**

The phase transitions to "Writing Content." Now each concept shows writing progress:

```
┌─────────────────────────────────────────────────┐
│       🏔️ Building: Greek Philosophy              │
│                                                  │
│  ┌─ Phase 3 of 3: Writing Content ──────────┐   │
│  │  ████████████████████░░░░░░░░  62%         │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ┌─ Course Map ──────────────────────────────┐   │
│  │                                            │   │
│  │  📦 Pre-Socratic Thinkers          ✅      │   │
│  │     ├─ Thales and the Milesians     ✅ 📗  │   │
│  │     ├─ Heraclitus and Change        ✅ 📗  │   │
│  │     └─ Parmenides and Being         ✅ 📗  │   │
│  │                                            │   │
│  │  📦 Socrates                        🔄     │   │
│  │     ├─ The Socratic Method          ✅ 📗  │   │
│  │     ├─ Ethics and Virtue           ✍️ ...  │   │
│  │     │   ░░░░░░████░░  writing 340 words    │   │
│  │     └─ The Trial and Death          ⏳     │   │
│  │                                            │   │
│  │  📦 Plato                           ⏳     │   │
│  │  📦 Aristotle                       ⏳     │   │
│  │  ⋯ 7 more modules                          │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ✍️ Writing "Ethics and Virtue" (3 sources)      │
│    Using: Wikipedia, Stanford Encyclopedia       │
│                                                  │
│  [Cancel]                                        │
└─────────────────────────────────────────────────┘
```

**Animation details for writing phase:**
- Active concept shows `✍️` (writing hand) with a tiny inline progress bar showing approximate word count
- The inline progress bar fills as the LLM generates tokens (estimated from elapsed time vs target word count)
- Completed concepts show `✅ 📗` — the book icon appears with a small pop animation (scale 0→1 over 0.15s)
- Module header gets `✅` when all its concepts are complete, with a subtle shimmer animation across the module row
- Collapsed modules (beyond visible area) show "⋯ N more modules" that expands as the user scrolls
- Status line at bottom shows which sources are being used for the current concept

**Phase 6: Completion**

All items show ✅. The progress bar hits 100% with a golden fill animation. The tree fades slightly (opacity 0.8) and a completion card slides up from the bottom:

```
┌─────────────────────────────────────────────────┐
│       🏔️ Greek Philosophy                        │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │       ✅ Course Created Successfully!      │   │
│  │                                            │   │
│  │  🏔️ Greek Philosophy                      │   │
│  │                                            │   │
│  │  📦 11 modules  ·  📝 33 concepts          │   │
│  │  ⏱ ~5 hours estimated                      │   │
│  │  🔍 127 sources found  ·  📗 92% confidence │   │
│  │                                            │   │
│  │  ┌────────────────────────────────────┐   │   │
│  │  │      Start Learning →  🚀          │   │   │
│  │  └────────────────────────────────────┘   │   │
│  │                                            │   │
│  │  ┌──────────────┐  ┌──────────────┐       │   │
│  │  │ View Structure│  │ Back to      │       │   │
│  │  │      📊      │  │ Courses      │       │   │
│  │  └──────────────┘  └──────────────┘       │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**Completion animation:**
- Brief confetti burst (CSS-only, ~1.5s) using Alpine palette colors
- Stats fade in sequentially (modules → concepts → time → sources → confidence) with 150ms stagger
- "Start Learning" button has a subtle glow pulse in `var(--accent-primary)`
- Total sources found and average confidence give the user immediate quality feedback

#### Custom Wizard — Step-by-Step Visual Details

**Step Indicator Bar (persistent across all steps):**

```
  ┌──────────────────────────────────────────────────────┐
  │  ●━━━━━━━●━━━━━━━○━━━━━━━○━━━━━━━○                  │
  │  Setup   Modules  Details  Q&A    Generate           │
  └──────────────────────────────────────────────────────┘
```

- Completed steps: filled circle (`●`) in `var(--accent-primary)`, connecting line solid
- Current step: filled circle with subtle pulse animation, label in bold
- Future steps: hollow circle (`○`), connecting line dashed, label in `var(--text-secondary)`
- Clicking a completed step navigates back (with a smooth 0.3s slide transition between step content)
- On mobile: collapses to "Step 2 of 5: Modules" text with back/next arrows

**Step 1: Course Setup — Visual Polish**

All form fields use Alpine-styled inputs:
- Text inputs: 2px border in `var(--border-color)`, 14px border-radius, focus glow in `var(--accent-primary-glow)`
- Textarea auto-grows from 3 to 5 rows as user types
- Prior knowledge pills: three rounded buttons in a row, selected one fills with `var(--accent-primary)` with a 0.2s slide transition
- Three sliders laid out vertically with generous spacing
- Estimate panel at bottom uses `var(--bg-tertiary)` background, updates with a number-ticker animation (digits roll up/down when values change)

**Step 2: Module Outline — Interactive Cards**

```
  ┌─ Module 1 ─────────────────────────────── ✕ ──┐
  │                                                 │
  │  📦  ┌─────────────────────────────────────┐   │
  │      │ Kinematics: Motion in 1D and 2D     │   │
  │      └─────────────────────────────────────┘   │
  │                                                 │
  │  💬 Note to Helga                               │
  │  ┌─────────────────────────────────────────┐   │
  │  │ Focus on projectile motion. Skip        │   │
  │  │ rotational stuff for now.               │   │
  │  └─────────────────────────────────────────┘   │
  │                                                 │
  │  ≡ drag handle                                  │
  └─────────────────────────────────────────────────┘
```

**Card interactions:**
- **Drag reorder:** Module cards have a ≡ handle on the left. Dragging creates a semi-transparent ghost (opacity 0.5) with a blue drop-zone indicator between other cards (4px line in `var(--accent-primary)`). Drop animates card into new position (0.3s ease)
- **Delete (✕):** Hover reveals red tint. Click shows inline confirmation: "Remove this module?" with Yes/No. On confirm, card collapses upward (0.3s) and disappears
- **Add Module (+):** Button at bottom of card list. Clicking inserts a new empty card with a slide-down animation (0.25s)
- **✨ Suggest Modules:** Button with sparkle animation on hover. Clicking shows a loading state (button text → "Thinking..." with dots animation). After 3-8 seconds, suggestion cards slide in below the button:

```
  ┌─ ✨ Suggestions from Helga ────────────────────┐
  │                                                 │
  │  ┌─────────────────────────────── ✓  ✕ ──┐    │
  │  │ Newton's Laws and Forces                │    │
  │  │ Covers force, mass, acceleration...     │    │
  │  └────────────────────────────────────────┘    │
  │                                                 │
  │  ┌─────────────────────────────── ✓  ✕ ──┐    │
  │  │ Work, Energy, and Conservation          │    │
  │  │ Covers kinetic, potential, work...      │    │
  │  └────────────────────────────────────────┘    │
  │                                                 │
  │  ┌─────────────────────────────── ✓  ✕ ──┐    │
  │  │ Momentum and Collisions                 │    │
  │  │ Covers impulse, elastic/inelastic...    │    │
  │  └────────────────────────────────────────┘    │
  │                                                 │
  └─────────────────────────────────────────────────┘
```

- Suggestion cards have a lighter background (`var(--bg-tertiary)`) and a dashed border to visually distinguish them from user-created cards
- ✓ (accept): card morphs into a full module card (border becomes solid, background transitions, title becomes editable). Smooth 0.3s transition
- ✕ (dismiss): card collapses out (0.2s)
- User can edit the title inline before accepting

**Step 3: Drill Down — Expandable Module Sections**

Each module from Step 2 appears as a collapsible section. Default state: collapsed with concept count.

```
  ┌─ 📦 Kinematics (2 your + ~3 generated) ── [▼] ─┐
  │                                                   │
  │  ┌─ Your concept ──────────────────── ✕ ──┐     │
  │  │  Title: [ Displacement vs Distance     ]│     │
  │  │  💬:    [ Make sure to distinguish      ]│     │
  │  │         [ vectors from scalars          ]│     │
  │  └─────────────────────────────────────────┘     │
  │                                                   │
  │  ┌─ Your concept ──────────────────── ✕ ──┐     │
  │  │  Title: [ Velocity and Acceleration    ]│     │
  │  │  💬:    [                               ]│     │
  │  └─────────────────────────────────────────┘     │
  │                                                   │
  │  [+ Add Concept]   [✨ Suggest Concepts]          │
  │                                                   │
  │  ── 🤖 Helga will generate ~3 more concepts ──   │
  │     based on scope and mastery settings           │
  └───────────────────────────────────────────────────┘
  
  ┌─ 📦 Newton's Laws (0 your + ~5 generated) [▶] ─┐
  │  🤖 Helga will generate all concepts.            │
  │  Click to expand and add your own.               │
  └───────────────────────────────────────────────────┘
```

**Interaction details:**
- [▼]/[▶] toggles expand/collapse with a smooth height transition (0.3s ease)
- Collapsed modules show a one-line summary with concept counts
- Concept cards within modules use the same pattern as module cards: title input + note textarea + delete
- "✨ Suggest Concepts" works identically to module suggestions but scoped to that module
- The "🤖 Helga will generate ~N more" line updates dynamically as user adds/removes concepts
- Generated count = `mastery_concept_base[mastery] - user_defined_count`, minimum 0

**Step 4: Clarifying Questions — Conversational Feel**

Questions are styled like a chat between the user and Helga, not a form:

```
  ┌──────────────────────────────────────────────────┐
  │  Before I build your course, I have a few         │
  │  questions to make it exactly right.              │
  │                                                    │
  │  ┌─ 🏔️ Helga asks: ──────────────────────────┐  │
  │  │  You mentioned you've taken calculus.        │  │
  │  │  Should I include calculus-based derivations │  │
  │  │  or keep it algebra-based?                   │  │
  │  │                                              │  │
  │  │  Why this matters: Determines whether        │  │
  │  │  content includes integrals and derivatives  │  │
  │  │  or only algebraic relationships.            │  │
  │  └─────────────────────────────────────────────┘  │
  │                                                    │
  │  ┌─ Your answer: ─────────────────────────────┐  │
  │  │ Calculus-based is fine, that's what I need  │  │
  │  │ for my physics class.                       │  │
  │  └─────────────────────────────────────────────┘  │
  │                                  [ Skip this one ] │
  │                                                    │
  │  ┌─ 🏔️ Helga asks: ──────────────────────────┐  │
  │  │  For the Work & Energy module, do you       │  │
  │  │  want just mechanical energy, or also       │  │
  │  │  include thermal energy and thermo basics?  │  │
  │  └─────────────────────────────────────────────┘  │
  │                                                    │
  │  ┌─ Your answer: ─────────────────────────────┐  │
  │  │                                             │  │
  │  └─────────────────────────────────────────────┘  │
  │                                  [ Skip this one ] │
  └──────────────────────────────────────────────────┘
```

**Visual details:**
- Helga's question cards use `var(--bg-tertiary)` with a left border in `var(--accent-primary)` — similar to tutor chat bubbles
- "Why this matters" line is in `var(--text-secondary)`, smaller font, provides transparency
- Answer textareas are 2-3 rows, auto-grow
- "Skip this one" link in muted text — clicking collapses the question card (0.3s) and shows a struck-through label
- Questions slide in one at a time with staggered animation (200ms between each) when the page loads
- A small "🏔️ Helga is thinking..." loader appears for 3-8 seconds while the LLM generates questions, with the three-dot bounce animation

**Step 5: Generation — Identical Tree View**

Uses the exact same progress UI as Quick Create Phase 3-6, but with richer labels:
- User-defined concepts show "(your concept)" tag in `var(--accent-secondary)`
- Generated concepts show "(generated)" tag in `var(--text-secondary)`
- Q&A-added concepts show "(added from Q&A)" tag in `var(--status-info)`
- The module tree is pre-populated from Step 2/3 (user already knows the structure), so the skeleton phase is faster and just fills in generated concepts
- Research and writing phases proceed identically to Quick Create

### 14.12 CSS Animation Specifications

All animations use CSS only (no JavaScript animation libraries). Performance-critical: all animations use `transform` and `opacity` only (GPU-composited, no layout reflow).

```css
/* === Course Creation Animations === */

/* Card slide-in */
@keyframes slideInLeft {
    from { opacity: 0; transform: translateX(-20px); }
    to { opacity: 1; transform: translateX(0); }
}
.module-card-enter { animation: slideInLeft 0.25s ease-out; }

/* Concept fade-in */
@keyframes fadeInDown {
    from { opacity: 0; transform: translateY(-8px); }
    to { opacity: 1; transform: translateY(0); }
}
.concept-enter { animation: fadeInDown 0.2s ease-out; }

/* Research magnifying glass bounce */
@keyframes searchBounce {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.15); }
}
.concept-researching .status-icon { animation: searchBounce 1s ease-in-out infinite; }

/* Writing hand wiggle */
@keyframes writeWiggle {
    0%, 100% { transform: rotate(0deg); }
    25% { transform: rotate(-5deg); }
    75% { transform: rotate(5deg); }
}
.concept-writing .status-icon { animation: writeWiggle 0.8s ease-in-out infinite; }

/* Completion pop */
@keyframes completePop {
    0% { transform: scale(0); opacity: 0; }
    60% { transform: scale(1.15); opacity: 1; }
    100% { transform: scale(1); opacity: 1; }
}
.concept-complete .book-icon { animation: completePop 0.3s ease-out; }

/* Module complete shimmer */
@keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
}
.module-complete {
    background: linear-gradient(90deg,
        transparent 25%, var(--accent-primary-glow) 50%, transparent 75%);
    background-size: 200% 100%;
    animation: shimmer 1.5s ease-in-out;
}

/* Progress bar stripe animation */
@keyframes progressStripes {
    0% { background-position: 0 0; }
    100% { background-position: 30px 0; }
}
.progress-bar-active {
    background-image: linear-gradient(
        45deg, rgba(255,255,255,0.15) 25%, transparent 25%,
        transparent 50%, rgba(255,255,255,0.15) 50%,
        rgba(255,255,255,0.15) 75%, transparent 75%);
    background-size: 30px 30px;
    animation: progressStripes 0.6s linear infinite;
}

/* Source line slide-in */
@keyframes sourceSlideIn {
    from { opacity: 0; max-height: 0; }
    to { opacity: 1; max-height: 24px; }
}
.source-line { animation: sourceSlideIn 0.2s ease-out; }

/* Completion confetti (CSS only) */
@keyframes confettiFall {
    0% { transform: translateY(-100vh) rotate(0deg); opacity: 1; }
    100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
}
.confetti-piece {
    position: fixed;
    width: 8px;
    height: 8px;
    animation: confettiFall 2s ease-in forwards;
}

/* Estimate panel number ticker */
@keyframes tickUp {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}
.estimate-value-change { animation: tickUp 0.3s ease-out; }

/* Suggestion card distinction */
.suggestion-card {
    background: var(--bg-tertiary);
    border: 2px dashed var(--accent-primary);
    border-radius: var(--border-radius);
}

/* Phase transition */
@keyframes phaseSlide {
    from { opacity: 0; transform: translateX(30px); }
    to { opacity: 1; transform: translateX(0); }
}
.phase-label-enter { animation: phaseSlide 0.4s ease-out; }
```

### 14.13 Updated Sprint Tasks (UI Animations + Full Creation Flow)

The existing CC and CB tasks are updated and new animation tasks are added:

| Task | Hours | Priority | Details |
|------|-------|----------|---------|
| CC.7 (revised) Quick Create modal — full animation | 3 | CRITICAL | Three-slider form with live estimates + number ticker animation. Modal expand transition on submit. Three-phase progress view (skeleton→research→writing) with phase labels and striped progress bar. Module/concept tree with slide-in animations. Source line display during research phase. Inline word-count bar during writing. Completion card with confetti + stats cascade. Cancel button throughout |
| CC.1 (revised) Custom wizard page — full visual polish | 5 | CRITICAL | Full `/courses/new` page. Animated step indicator bar with click-to-navigate. Step 1: Alpine-styled inputs, auto-grow textarea, pill selection animation, three sliders with estimate panel. Step 2: Module cards with drag handles, reorder animations, delete confirmation, "✨ Suggest" with loading state + suggestion cards with accept/dismiss morph. Step 3: Collapsible module sections with expand/collapse, nested concept cards, per-module suggest, dynamic "🤖 will generate ~N" counter. Step 4: Chat-style Q&A cards with staggered entrance, "Why this matters" context line, skip/collapse. Step 5: Shared progress tree component (same as Quick Create) |
| CC.6 (revised) Shared progress tree component | 3 | CRITICAL | Reusable JavaScript component used by BOTH Quick Create modal and Custom Wizard Step 5. Accepts Socket.IO events from §14.11. Renders module/concept tree with all status transitions (pending→researching→writing→complete→error). Source line display with domain icons. Inline progress bars. Phase label transitions. Completion stats card with confetti. Source labels (your concept / generated / qa_added). Auto-scroll to active item |
| CC.10 (new) Animation CSS library | 1.5 | HIGH | All CSS keyframe animations from §14.12 in a single `creation-animations.css` file loaded only on courses page and wizard page. CSS-only confetti (no JS library). GPU-composited transforms only (no layout reflow) |
| CC.11 (new) Socket.IO event integration | 2 | CRITICAL | Backend emits all events from §14.11 event table during course creation. ContentHydrator emits `creation:concept` status changes (pending→researching→writing→complete). Research service results forwarded as `creation:research` events. core-logic emits `creation:phase` on major transitions. Web-ui relays events to browser via Socket.IO room per course_uid |

**Revised CC task totals:**

| Task | Old Hours | New Hours | Change |
|------|-----------|-----------|--------|
| CC.1 Wizard page | 4 | 5 | +1 (visual polish) |
| CC.2 Wizard JS | 3 | 3 | unchanged |
| CC.3 Suggest features | 2 | 2 | unchanged |
| CC.4 Q&A Step 4 | 2 | 2 | unchanged |
| CC.5 Custom backend | 3 | 3 | unchanged |
| CC.6 Progress tree component | 1.5 | 3 | +1.5 (shared component + animations) |
| CC.7 Quick Create modal | 1 | 3 | +2 (full animation sequence) |
| CC.8 Routes | 1 | 1 | unchanged |
| CC.9 Proxy routes | 1 | 1 | unchanged |
| CC.10 Animation CSS (NEW) | — | 1.5 | new |
| CC.11 Socket events (NEW) | — | 2 | new |
| **Total** | **18.5** | **26.5** | **+8** |

---

## PART 15: GAMIFICATION, PROGRESS SYSTEM & SETTINGS

### 15.1 Design Philosophy

The gamification system serves learning, not the reverse. Every reward mechanic reinforces a pedagogically sound behavior: consistent daily practice (streak), deep engagement over surface-level completion (Bloom's multipliers), and long-term retention (FSRS-linked mastery badges). The entire system is toggleable — a user who finds gamification distracting can disable it in settings, and every feature degrades gracefully to a clean, reward-free interface.

### 15.2 User Profile & Preferences Schema

```sql
CREATE TABLE user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Default rows inserted on first launch:
INSERT INTO user_profile VALUES
    ('display_name', ''),
    ('avatar_emoji', '🧑‍🎓'),
    ('theme', 'light'),                    -- 'light' | 'dark'
    ('font_scale', '1.0'),                 -- 0.8 to 1.4
    ('tts_voice', 'af_heart'),             -- Kokoro voice ID
    ('tts_enabled', 'true'),               -- show play buttons
    ('gamification_enabled', 'true'),       -- XP, streaks, badges, celebrations
    ('sound_effects', 'true'),             -- completion sounds, XP ding
    ('show_bloom_level', 'true'),          -- Bloom's indicators on concepts
    ('show_source_confidence', 'true'),    -- confidence indicators on content
    ('daily_goal_minutes', '30'),          -- daily learning time target
    ('session_reminder', 'false'),         -- browser notification reminder
    ('preferred_question_style', 'mixed'), -- 'socratic' | 'direct' | 'mixed'
    ('auto_advance', 'false'),             -- auto-advance to next concept on mastery
    ('compact_mode', 'false');             -- denser UI with less whitespace
```

**API endpoints:**

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/profile` | GET | — | `{display_name, avatar_emoji, theme, ...all keys}` |
| `/api/profile` | PATCH | `{key: value, ...}` (partial update) | `{status: "ok", updated: ["key1","key2"]}` |
| `/api/profile/reset` | POST | — | Reset all to defaults |

The profile is loaded once on page load via `GET /api/profile` and cached in a JavaScript `window.userProfile` object. Changes via the settings panel send `PATCH /api/profile` and update the local cache immediately (optimistic UI).

### 15.3 Gamification State Schema

```sql
CREATE TABLE gamification (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Default rows:
INSERT INTO gamification VALUES
    ('total_xp', '0'),
    ('daily_xp', '0'),
    ('level', '1'),
    ('streak_days', '0'),
    ('streak_last_date', ''),              -- ISO date string: '2026-04-01'
    ('longest_streak', '0'),
    ('total_concepts_mastered', '0'),
    ('total_modules_completed', '0'),
    ('total_courses_completed', '0'),
    ('total_study_minutes', '0'),
    ('total_questions_answered', '0'),
    ('total_correct_answers', '0'),
    ('daily_goal_progress_minutes', '0'),
    ('daily_goal_last_date', ''),
    ('achievements_unlocked', '[]');       -- JSON array of achievement IDs
```

**API endpoints:**

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/gamification` | GET | — | Full gamification state object |
| `/api/gamification/award_xp` | POST | `{action, concept_uid, grade, bloom_level}` | `{xp_earned, multiplier, new_total, level_up: bool, new_level}` |
| `/api/gamification/check_streak` | POST | — | `{streak_days, streak_alive: bool, xp_earned}` |

### 15.4 XP System — Complete Design

#### XP Earning Table

| Action | Base XP | Multiplier Conditions | Max XP |
|--------|---------|----------------------|--------|
| Answer Socratic question correctly (grade ≥ 3) | 10 | ×1.5 if first try (no prior wrong answer on this question) | 15 |
| Answer correctly at Bloom's L4+ | 10 | ×2.0 (stacks with first-try) | 30 |
| Complete a concept (mastery threshold met) | 25 | ×1.5 if zero hint requests during concept | 37 |
| Complete a module (all concepts mastered) | 100 | ×1.25 if completed in a single session | 125 |
| Complete a course | 500 | — | 500 |
| Review session: correct recall (grade ≥ 3) | 15 | ×1.5 if FSRS stability > 30 days | 22 |
| Pass a test question | 20 | ×1.5 if no hints used | 30 |
| Daily streak maintained | 5 × streak_day | Caps at streak_day = 30 (150 XP max) | 150 |
| Daily goal reached | 50 | — | 50 |
| First course created | 100 | One-time bonus | 100 |
| First concept mastered | 50 | One-time bonus | 50 |

#### Leveling System

| Level | XP Required (cumulative) | Title | Badge |
|-------|-------------------------|-------|-------|
| 1 | 0 | Wanderer | 🥾 |
| 2 | 100 | Trail Scout | 🧭 |
| 3 | 300 | Hiker | 🥾 |
| 4 | 600 | Pathfinder | 🗺️ |
| 5 | 1,000 | Mountain Guide | 🏕️ |
| 6 | 1,500 | Alpine Climber | ⛏️ |
| 7 | 2,200 | Ridge Walker | 🏔️ |
| 8 | 3,000 | Summit Seeker | 🧗 |
| 9 | 4,000 | Peak Master | 🏔️ |
| 10 | 5,500 | Edelweiss Scholar | ⭐ |
| 11+ | +2,000 per level | Legend I, II, III... | 👑 |

Formula: `level = floor(sqrt(total_xp / 50)) + 1` (approximation, actual lookup table used)

#### XP Animation Sequence

When XP is earned during a tutoring interaction:

1. Grade badge appears on tutor response (🟢/🟡/🔴) — 0.2s fade-in
2. If grade ≥ 3: XP float animation — "+10 XP" text in edelweiss gold (`#d4a843`) rises from the chat bubble, drifts upward 40px over 1.5s, fades out
3. If multiplier applied: multiplier text appears briefly beside XP ("×1.5 first try!") in smaller font, same float animation
4. Header XP counter ticks up with number-roll animation (digits transition individually, 0.3s)
5. If level up: header badge does a 0.5s glow pulse, level number transitions with scale-up (1.0→1.2→1.0), a toast notification slides in: "🏔️ Level 5: Mountain Guide!" with the new badge icon
6. XP progress bar (thin line below header) fills toward next level — smooth 0.5s transition

**When gamification is disabled:** Steps 2-6 don't fire. The grade badge (step 1) still appears because it's learning feedback, not gamification. XP is still tracked in the database (so re-enabling shows accurate totals) but not displayed.

### 15.5 Streak System

**How streaks work:**

1. On any interaction (sending a message in Learn, completing a review card, answering a test question), the system calls `POST /api/gamification/check_streak`
2. Backend compares `streak_last_date` to today's date:
   - Same day: no change (already counted today)
   - Yesterday: increment `streak_days`, update `streak_last_date` to today, award streak XP
   - 2+ days ago: streak dies. Reset `streak_days = 1`, award no streak bonus, update `streak_last_date`
3. `longest_streak` updates if current streak exceeds it

**Streak UI in header:**

```
Active streak (day 7):     🔥 7
First interaction today:   🔥 7 (pulse animation + "+35 XP" float)
Streak just died:          💀 0 (skull shows for first session only, then → 🔥 1)
No interactions yet:       🔥 0 (dimmed, no pulse)
Gamification disabled:     (streak counter hidden entirely)
```

**Streak protection:** If the user misses one day, the next session shows a "streak freeze" toast: "Your 7-day streak ended yesterday. Starting fresh at 🔥 1." No punishment beyond losing the streak — the system doesn't nag or guilt-trip.

### 15.6 Mastery Badges — Per-Concept Visual Progression

| Stage | Criteria | Icon | Color | CSS Class |
|-------|----------|------|-------|-----------|
| Unseen | No interactions yet | `○` (hollow circle) | `var(--border-color)` | `.badge-unseen` |
| 🌱 Seedling | First interaction started | Seedling SVG | `#8bc34a` (light green) | `.badge-seedling` |
| 🌿 Growing | Bloom's L2 reached | Sprout SVG | `#4caf50` (green) | `.badge-growing` |
| 🌲 Rooted | Bloom's L3 reached | Pine tree SVG | `#2e7d32` (dark green) | `.badge-rooted` |
| 🏔️ Summit | Bloom's L4+ mastered | Mountain SVG | `var(--accent-primary)` | `.badge-summit` |
| ⭐ Edelweiss | Bloom's L5+ AND FSRS stability > 30 days | Star/flower SVG | `var(--xp-gold)` | `.badge-edelweiss` |

**Where badges appear:**
- Course structure page: next to each concept title
- Learn page left sidebar: concept list with badges
- Home page: "Recently mastered" section shows latest badge upgrades
- Review page: due concepts show their current badge level

**Badge upgrade animation:** When a concept advances to a new badge level during a tutoring session:
1. Current badge icon does a quick spin (360° over 0.4s)
2. Cross-fade to new badge icon (0.3s)
3. Brief particle burst around the badge in the badge's color (CSS-only, 0.6s)
4. Toast notification: "🌿 Growing! You reached Bloom's Level 2 on 'Photosynthesis'"

### 15.7 Daily Goal System

Users set a daily learning time target in Settings (default: 30 minutes). The system tracks active learning time (time spent on Learn/Review/Test pages with interactions, not idle time).

**Header indicator:**

```
Before starting:    ○○○○○○  0/30 min
Halfway:            ●●●○○○  15/30 min
Goal reached:       ●●●●●●  30/30 min ✅ (+50 XP)
Over goal:          ●●●●●●●● 45/30 min ✅
```

- Uses a ring of 6 dots that fill progressively with `var(--accent-tertiary)` (meadow green)
- When goal is reached: all dots pulse simultaneously (0.5s), "+50 XP" float animation, toast: "🎯 Daily goal reached!"
- Resets at midnight local time (tracked via `daily_goal_last_date`)

**When gamification is disabled:** Daily goal indicator is hidden. Time is still tracked internally.

### 15.8 Achievement System

One-time unlockable achievements that recognize milestones. Each has an ID, name, description, icon, and XP reward.

| ID | Name | Description | Criteria | XP |
|----|------|-------------|----------|-----|
| `first_course` | First Steps | Create your first course | 1 course created | 100 |
| `first_mastery` | First Bloom | Master your first concept | 1 concept at 🌲+ | 50 |
| `streak_7` | Week Warrior | Maintain a 7-day streak | streak_days ≥ 7 | 75 |
| `streak_30` | Month of Mountains | Maintain a 30-day streak | streak_days ≥ 30 | 300 |
| `bloom_4` | Deep Thinker | Reach Bloom's L4 on any concept | Any concept bloom ≥ 4 | 100 |
| `bloom_6` | Creator | Reach Bloom's L6 (Create) | Any concept bloom = 6 | 250 |
| `edelweiss_1` | Edelweiss | Earn your first ⭐ mastery badge | 1 concept at ⭐ | 200 |
| `edelweiss_10` | Alpine Garden | Earn 10 ⭐ mastery badges | 10 concepts at ⭐ | 500 |
| `course_complete` | Summit Reached | Complete an entire course | 1 course 100% | 500 |
| `questions_100` | Century | Answer 100 questions | total_questions ≥ 100 | 100 |
| `questions_1000` | Millennium | Answer 1,000 questions | total_questions ≥ 1000 | 500 |
| `perfect_concept` | Flawless | Master a concept with zero wrong answers | concept mastered, 0 grade<3 | 75 |
| `night_owl` | Night Owl | Study after 10 PM | interaction at hour ≥ 22 | 25 |
| `early_bird` | Early Bird | Study before 7 AM | interaction at hour < 7 | 25 |
| `five_courses` | Curator | Create 5 courses | 5 courses created | 200 |
| `reviewer` | Memory Keeper | Complete 50 review sessions | 50 review sessions done | 150 |

**Achievement unlock animation:**
1. Full-width banner slides down from top of page (0.4s ease-out)
2. Banner background: gradient in Alpine palette colors
3. Shows: achievement icon (large) + name + description + XP reward
4. "+{XP} XP" counter ticks up in the banner
5. Banner auto-dismisses after 4 seconds (or tap to dismiss)
6. Achievement stored in `achievements_unlocked` JSON array (never re-triggered)

**Achievement page:** Accessible from Settings or a trophy icon in the header. Shows all achievements in a grid — unlocked ones are full color with unlock date, locked ones are grayed out with progress indicators where applicable (e.g., "47/100 questions answered").

### 15.9 Progress Dashboard — Home Page Integration

The Home page becomes a progress-focused dashboard when the user has active courses:

```
┌──────────────────────────────────────────────────────────────┐
│  Welcome back, Brennan! 🏔️                                   │
│                                                              │
│  ┌─ Today ──────────────────────────────────────────┐       │
│  │  🔥 7-day streak    ●●●○○○ 15/30 min    Lv.5 🏕️ │       │
│  │  ⭐ 1,250 XP        ████████░░ → Level 6         │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Continue Learning ──────────────────────────────┐       │
│  │  📦 Classical Mechanics — Newton's Laws           │       │
│  │  🌿 Free Body Diagrams (Bloom's L2)              │       │
│  │  ┌──────────────────────────────────────────┐    │       │
│  │  │          Continue →                       │    │       │
│  │  └──────────────────────────────────────────┘    │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Due for Review ─────────────────────────────────┐       │
│  │  3 concepts due today                             │       │
│  │  🌲 Displacement vs Distance  ·  due now          │       │
│  │  🌿 Velocity and Acceleration  ·  due in 2h       │       │
│  │  🌱 Projectile Motion  ·  due in 5h               │       │
│  │  ┌──────────────────────────────────────────┐    │       │
│  │  │          Start Review →                   │    │       │
│  │  └──────────────────────────────────────────┘    │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Your Progress ──────────────────────────────────┐       │
│  │  Classical Mechanics  ████████░░░░  65%  12/18    │       │
│  │  Greek Philosophy     ██░░░░░░░░░░  12%   4/33   │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Recent Achievements ────────────────────────────┐       │
│  │  🏆 Week Warrior (3 days ago)                     │       │
│  │  🌿 First Bloom (5 days ago)                      │       │
│  └──────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

**When gamification is disabled:** The "Today" row hides streak, XP, and level. Daily goal hides. Achievement section hides. "Continue Learning" and "Due for Review" remain (they're learning features, not gamification). Course progress bars remain.

### 15.10 Settings Page — Full Design

Settings is a full page at `/settings` (not a modal — too much content for a modal). Accessible from the ⚙️ icon in the header.

**Layout:**

```
┌──────────────────────────────────────────────────────────────┐
│  ← Back                                                      │
│                                                              │
│  Settings                                                    │
│                                                              │
│  ┌─ Profile ────────────────────────────────────────┐       │
│  │                                                    │       │
│  │  Display Name                                      │       │
│  │  ┌────────────────────────────────────────┐       │       │
│  │  │ Brennan                                │       │       │
│  │  └────────────────────────────────────────┘       │       │
│  │  Used in greetings and the tutoring dialogue.     │       │
│  │                                                    │       │
│  │  Avatar                                            │       │
│  │  [🧑‍🎓] [👨‍💻] [👩‍🔬] [🧑‍🏫] [🏔️] [📚] [custom...]  │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Appearance ─────────────────────────────────────┐       │
│  │                                                    │       │
│  │  Theme          [☀️ Light]  [🌙 Dark]              │       │
│  │                                                    │       │
│  │  Font Size      A━━━━━●━━━━━A                     │       │
│  │                 0.8x       1.2x (current: 1.0x)   │       │
│  │                                                    │       │
│  │  Compact Mode   [ ] Denser layout, less whitespace │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Learning Preferences ───────────────────────────┐       │
│  │                                                    │       │
│  │  Question Style                                    │       │
│  │  [Socratic]  [Direct]  [Mixed ✓]                  │       │
│  │  Socratic = guiding questions                      │       │
│  │  Direct = straightforward Q&A                      │       │
│  │  Mixed = Helga chooses based on context            │       │
│  │                                                    │       │
│  │  Auto-Advance   [ ] Move to next concept           │       │
│  │                      automatically on mastery      │       │
│  │                                                    │       │
│  │  Show Bloom's Indicators  [✓]                      │       │
│  │  Show bloom level badges on concepts               │       │
│  │                                                    │       │
│  │  Show Source Confidence   [✓]                      │       │
│  │  Show how well-sourced each concept is             │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Gamification ───────────────────────────────────┐       │
│  │                                                    │       │
│  │  Enable Gamification  [✓]                          │       │
│  │  XP, streaks, levels, badges, and celebrations     │       │
│  │                                                    │       │
│  │  ┌── (visible when gamification enabled) ──────┐  │       │
│  │  │                                              │  │       │
│  │  │  Sound Effects      [✓]                      │  │       │
│  │  │  Plays sounds on XP gain, level up, etc.     │  │       │
│  │  │                                              │  │       │
│  │  │  Daily Goal         ┌──────┐                 │  │       │
│  │  │                     │ 30   │ minutes          │  │       │
│  │  │                     └──────┘                 │  │       │
│  │  │                                              │  │       │
│  │  │  Session Reminder   [ ]                      │  │       │
│  │  │  Browser notification if you haven't         │  │       │
│  │  │  studied today (sent at 7 PM)                │  │       │
│  │  │                                              │  │       │
│  │  └──────────────────────────────────────────────┘  │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Voice ──────────────────────────────────────────┐       │
│  │                                                    │       │
│  │  Text-to-Speech     [✓] Show play buttons          │       │
│  │                                                    │       │
│  │  Voice Selection                                   │       │
│  │  ┌────────────────────────────────────────┐       │       │
│  │  │ af_heart (Female, warm)          ▼     │       │       │
│  │  └────────────────────────────────────────┘       │       │
│  │  [▶ Preview Voice]                                │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ Data ───────────────────────────────────────────┐       │
│  │                                                    │       │
│  │  Your Stats                                        │       │
│  │  Courses: 3  ·  Concepts: 68  ·  Questions: 247   │       │
│  │  Study time: 12.5 hrs  ·  Accuracy: 73%            │       │
│  │                                                    │       │
│  │  [View All Achievements 🏆]                        │       │
│  │                                                    │       │
│  │  [Reset Gamification Progress]  (confirmation)     │       │
│  │  [Delete All Courses]           (confirmation)     │       │
│  │  [Export My Data]               (JSON download)    │       │
│  │                                                    │       │
│  └────────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─ About ──────────────────────────────────────────┐       │
│  │  Helga v2.0 · Socratic Tutor                      │       │
│  │  Qwen 3 14B via Ollama · Mac Mini M4 Pro 24GB     │       │
│  │  Kokoro TTS · SearXNG · SQLite                     │       │
│  │  github.com/brennansk1/helga                       │       │
│  └────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

**Interaction details:**

| Element | Behavior |
|---------|----------|
| Display name | Text input. On blur or Enter, auto-saves via PATCH. Used in home page greeting and tutor's first message ("Hello, Brennan") |
| Avatar | Emoji picker (6 presets + "custom..." that opens an emoji selector). Displayed in header next to name |
| Theme toggle | Two pills. Instant switch — applies `data-theme="dark"` to `<html>`. Saves via PATCH |
| Font size slider | Range 0.8-1.4 in 0.1 steps. Live preview as slider moves. Saves on release |
| Gamification toggle | Master switch. When turned OFF: XP counter, streak, daily goal, level, achievements all hide from header and home page. Celebrations don't fire. All gamification sub-settings collapse/hide with a 0.3s slide animation |
| Sound effects | Toggle. When ON, plays a subtle "ding" on XP gain, a chord on level up, and a celebration sound on achievements. All sounds are short (<1s) Web Audio API tones — no audio files needed |
| Daily goal | Number input (5-120 minutes, step 5). Changes save immediately |
| Voice selection | Dropdown of Kokoro's 14 voices with descriptive labels. "▶ Preview" plays a 3-second sample |
| Reset gamification | Double confirmation ("Are you sure? This resets XP, level, streak, and achievements."). Does NOT reset learning progress (FSRS, Bloom's, completed concepts) |
| Delete all courses | Triple confirmation (type "DELETE" to confirm). Cascading delete in SQLite |
| Export data | Downloads JSON file containing: profile, gamification state, all courses with concepts and interactions |

### 15.11 How Gamification Integrates with Existing Features

**In the tutoring flow (Learn page):**
- After each graded answer: XP award → float animation → header counter update
- After concept mastery: badge upgrade animation → concept completion celebration → XP award
- After module completion: module celebration (larger) → XP award → check for achievements
- Background: daily goal timer ticking, streak checked on first interaction of the day

**In the review flow (Review page):**
- After each correct recall: XP award (review-specific rate)
- After session complete: session summary card shows XP earned, concepts reviewed, accuracy %

**In course creation:**
- First course created: "First Steps" achievement triggers
- Course completion: "Summit Reached" achievement triggers

**In the header (base.html, visible on all pages):**
```
┌──────────────────────────────────────────────────────────────┐
│ 🏔️ Helga    Home  Courses  Learn  Review  Test  Status       │
│                                                              │
│              🧑‍🎓 Brennan  ⭐ 1,250  Lv.5 🏕️  🔥 7  ●●●○○○  ⚙️│
└──────────────────────────────────────────────────────────────┘
```

With gamification disabled:
```
┌──────────────────────────────────────────────────────────────┐
│ 🏔️ Helga    Home  Courses  Learn  Review  Test  Status       │
│                                                              │
│                                          🧑‍🎓 Brennan     ⚙️  │
└──────────────────────────────────────────────────────────────┘
```

**The display_name flows into the tutor's prompts.** When a user sets their name in settings, the Socratic tutor prompt includes: "The student's name is {display_name}. Address them by name occasionally (not every message)." This makes the tutoring feel personal without being repetitive.

### 15.12 Sound Effects (Web Audio API)

No audio files needed. All sounds are generated programmatically:

```javascript
// sounds.js — loaded only when sound_effects = true
const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

function playXPDing() {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain).connect(audioCtx.destination);
    osc.frequency.setValueAtTime(880, audioCtx.currentTime);   // A5
    osc.frequency.setValueAtTime(1108, audioCtx.currentTime + 0.1); // C#6
    gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.4);
    osc.start(); osc.stop(audioCtx.currentTime + 0.4);
}

function playLevelUp() {
    [523, 659, 784, 1047].forEach((freq, i) => {  // C5 E5 G5 C6
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain).connect(audioCtx.destination);
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.12, audioCtx.currentTime + i * 0.12);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + i * 0.12 + 0.3);
        osc.start(audioCtx.currentTime + i * 0.12);
        osc.stop(audioCtx.currentTime + i * 0.12 + 0.3);
    });
}

function playAchievement() {
    // Ascending arpeggio with reverb-like decay
    [440, 554, 659, 880, 1108].forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'triangle';
        osc.connect(gain).connect(audioCtx.destination);
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.1, audioCtx.currentTime + i * 0.1);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + i * 0.1 + 0.8);
        osc.start(audioCtx.currentTime + i * 0.1);
        osc.stop(audioCtx.currentTime + i * 0.1 + 0.8);
    });
}
```

### 15.13 Sprint Tasks for Gamification & Settings

| Task | Hours | Priority | Details |
|------|-------|----------|---------|
| GS.1 User profile schema + API | 1.5 | CRITICAL | `user_profile` table + default rows. `GET/PATCH /api/profile`, `/api/profile/reset` endpoints in RAG service. Profile loaded on page load, cached in JS |
| GS.2 Gamification state schema + API | 2 | CRITICAL | `gamification` table + default rows. `GET /api/gamification`, `POST /api/gamification/award_xp`, `POST /api/gamification/check_streak` endpoints. XP calculation with multipliers. Level computation. Streak logic |
| GS.3 Achievement engine | 2 | HIGH | Achievement definitions (16 achievements). Check function called after each interaction. Unlock detection + storage in `achievements_unlocked` JSON. One-time trigger guard |
| GS.4 Settings page (full) | 4 | CRITICAL | New `/settings` route + template. All 6 sections (Profile, Appearance, Learning, Gamification, Voice, Data). All form controls with auto-save via PATCH. Gamification sub-settings collapse when master toggle off. Voice preview. Data export. Destructive action confirmations. Alpine-styled throughout |
| GS.5 Header gamification bar | 2 | HIGH | XP counter + level badge + streak counter + daily goal dots in header. Conditional rendering based on `gamification_enabled`. Number-roll animation for XP. Streak pulse on first daily interaction. Daily goal dot fill progression |
| GS.6 XP + badge animations (learn page) | 2 | HIGH | XP float animation on grade ≥ 3. Multiplier label. Badge upgrade spin + particle burst. Concept/module/course completion celebrations. Level-up toast with new badge. All CSS-only animations |
| GS.7 Achievement unlock animation | 1 | MEDIUM | Full-width banner slide-down on unlock. Icon + name + description + XP. Auto-dismiss after 4s. Achievement grid page (locked/unlocked with progress) |
| GS.8 Sound effects (Web Audio) | 1 | MEDIUM | `sounds.js` with 3 programmatic sounds (XP ding, level-up chord, achievement arpeggio). Conditional on `sound_effects` preference. No audio file dependencies |
| GS.9 Home page progress dashboard | 2 | HIGH | "Today" stats row. "Continue Learning" card with current concept + badge. "Due for Review" card with due count + top 3 concepts. Course progress bars. Recent achievements. All conditional on gamification + profile state |
| GS.10 Display name in tutor prompts | 0.5 | MEDIUM | Load `display_name` from profile on session start. Inject into Socratic prompt: "The student's name is {name}." Update greeting in home page |
| GS.11 Gamification toggle integration | 1 | HIGH | When `gamification_enabled = false`: hide XP, streak, daily goal, level from header and home. Suppress celebration animations. Still track XP/streak in DB (re-enabling restores state). Grade badges and learning progress always visible |
| **Total** | **19** | | |

### 15.14 Where This Fits in the Sprint

The gamification and settings work depends on:
- Phase 1 complete (SQLite available)
- Phase 4a partially complete (base.html header, Alpine CSS)
- Phase 2 partially complete (tutor flow grading works, for XP triggers)

**Recommended placement:** Split across Phase 4 and Phase 5.
- GS.1-GS.3 (schema + APIs, 5.5h): alongside Phase 4a (Day 13-15), as they're backend-only
- GS.4-GS.5 (settings page + header bar, 6h): Phase 4b (Day 16-17)
- GS.6-GS.11 (animations + integration, 7.5h): Phase 5 (Day 20-21), after all pages are built

---

## PART 16: PRODUCTION READINESS — GAPS, TESTING, DEPLOYMENT & SECURITY

### 16.1 TurboQuant Assessment: NOT for this sprint

**What it is:** Google's TurboQuant (ICLR 2026, published March 24, 2026) compresses LLM KV caches to 3-4 bits per value with zero accuracy loss, yielding ~5× memory reduction and ~8× attention speedup on H100 GPUs.

**Why it's relevant in theory:** On a 24GB Mac Mini running Qwen 3 14B, the KV cache is the binding constraint on conversation length. TurboQuant would let us maintain dramatically longer conversation histories (from ~8K to ~40K+ tokens) without memory pressure.

**Why it's excluded from this sprint:**

1. **No Ollama support yet.** Ollama does not expose `--cache-type-k` / `--cache-type-v` flags. TurboQuant integration exists only as a llama.cpp discussion (PR pending, not merged). Official Google code is expected Q2 2026.
2. **Apple Silicon implementation is experimental.** A community Metal implementation exists in the llama.cpp discussion thread but is not validated beyond basic tests. Production stability is unknown.
3. **14B models are fine without it.** At Q4_K_M quantization, Qwen 3 14B uses ~9.5GB model weight + ~2-4GB KV cache at typical conversation lengths (2K-4K tokens). This fits comfortably in 24GB. TurboQuant solves a problem we don't yet have.
4. **Risk vs. reward.** Integrating bleeding-edge KV compression into a sprint targeting production readiness introduces instability for marginal gain. Our conversations rarely exceed 4K context tokens.

**Recommendation:** Add TurboQuant to a post-sprint optimization backlog. When Ollama ships native support (likely Q3 2026), it becomes a config flag change: `OLLAMA_KV_CACHE_TYPE=turbo4`. Zero code changes needed at that point.

### 16.2 Security Gaps (CRITICAL for production)

| # | Issue | Severity | Current State | Fix |
|---|-------|----------|--------------|-----|
| S1 | `.env` contains `HELGA_SUDO_PASSWORD=Spencer@1` in plaintext | **CRITICAL** | Password committed to repo | Delete sudo password from .env. Remove all sudo-related code (not needed on Mac). Add `.env` to `.gitignore` |
| S2 | `.env` contains `CLOUDFLARED_TOKEN=your_cloudflare_token_here` | HIGH | Placeholder but pattern is dangerous | Use `.env.example` for templates, `.env` in `.gitignore` |
| S3 | CORS set to `cors_allowed_origins="*"` | HIGH | Any origin can connect WebSocket | Set to `["http://localhost:5000", "http://127.0.0.1:5000"]` in production |
| S4 | All Docker containers run as `user: 0:0` (root) | HIGH | Root inside every container | Create non-root user in Dockerfiles, run as UID 1000 |
| S5 | No input sanitization on chat messages | HIGH | User text goes directly into LLM prompts and rendered as HTML with `innerHTML` | Sanitize HTML output (escape `<>&"'`), use `textContent` not `innerHTML` for user messages. Add prompt injection guardrails |
| S6 | `contenteditable="true"` on user chat messages | Medium | XSS vector — user can edit DOM | Remove contenteditable. If edit-in-place is needed, use a controlled input overlay |
| S7 | No rate limiting on API endpoints | Medium | Unlimited requests from any client | Add Flask-Limiter: 30 req/min on `/api/event`, 5 req/min on `/api/tts` |
| S8 | No HTTPS in production | HIGH | All traffic unencrypted | Add Caddy reverse proxy (auto-HTTPS with Let's Encrypt) or use Cloudflare tunnel |
| S9 | `data/` directory set to `chmod 777` in deploy.sh | Medium | World-writable database files | Use `chmod 755` for dirs, `644` for files, owned by app user |
| S10 | LLM prompts vulnerable to injection | Medium | User text concatenated directly into system prompts | Wrap user input in XML delimiters: `<student_response>{text}</student_response>`. Never let user text appear in system prompt role |

### 16.3 Test Suite — Complete Rewrite Required

**Current state:** 4,502 lines of tests across 27 test files. ALL test the old architecture:
- `test_content_provider.py` — tests ZimProvider, KolibriProvider (being deleted)
- `test_atomic_swap.py` — tests KuzuDB atomic swap (being deleted)
- `test_hydrator.py` — tests ZIM content hydration (being deleted)
- `test_vad_logic.py`, `test_mixer.py`, `test_audio_sweep.py`, `test_audio_flow.py` — test voice pipeline (being deleted)
- `test_zim_seek.py`, `test_ingest_logic.py` — test ZIM ingestion (being deleted)
- `test_key_press.py`, `test_power_daemon.py` — test Jetson hardware (being deleted)
- `test_grading_logic.py` — tests FSM but references old API format and `play_sound` mocks
- `test_web_ui.py` — tests routes that are being rewritten
- `test_fsrs.py` — tests custom FSRSEngine being replaced with py-fsrs

**Action:** Delete all existing tests. Write new test suite from scratch targeting the new architecture.

#### Required Test Categories

**Unit Tests (pytest, target: 85% coverage on core modules)**

| Test File | Tests | What It Validates |
|-----------|-------|-------------------|
| `tests/unit/test_fsrs_service.py` | 8-10 tests | py-fsrs wrapper: first review, subsequent review, rating inference from dialogue, due date calculation, stability/difficulty updates, edge cases (rating=1 repeatedly, rating=4 repeatedly) |
| `tests/unit/test_prompts.py` | 10-12 tests | Every prompt function returns (system, user) tuple. No Llama-2 tokens present. Bloom's level correctly inserted. JSON schema instructions present in grading prompts. Prompt lengths under 2000 tokens |
| `tests/unit/test_bloom_tracker.py` | 6-8 tests | Level advancement on consecutive mastery. Level regression on failure (N→N-1, not N→1). Threshold enforcement per level. Can't exceed level 6. Starts at level 1 |
| `tests/unit/test_grading.py` | 8-10 tests | Grade parsing from clean JSON. Grade parsing from markdown-wrapped JSON. Grade parsing from malformed LLM output (regex fallback). Default grade on complete parse failure. Hesitation detection. Grade adjustment for hesitation |
| `tests/unit/test_gamification.py` | 12-15 tests | XP calculation per action type with correct base values. Multiplier stacking (first-try × Bloom's L4+). Streak increment on daily login. Streak reset after 24h gap (not 48h, not 23h). Streak XP caps at day 30. Level calculation from total XP matches lookup table. Level-up detection (returns `level_up: true` when threshold crossed). Daily goal detection (progress ≥ target). Achievement unlock triggers exactly once (not re-triggered). Achievement check for each of the 16 defined achievements. XP earned while gamification disabled still tracked in DB. Multiple XP awards in same request don't double-count |
| `tests/unit/test_user_profile.py` | 6-8 tests | GET /api/profile returns all default keys on fresh DB. PATCH /api/profile updates specific keys without overwriting others. Display name stored and retrieved correctly. Theme value constrained to 'light' or 'dark'. Font scale constrained to 0.8-1.4 range. Reset endpoint restores all defaults. Invalid key in PATCH ignored gracefully |
| `tests/unit/test_llm_client.py` | 6-8 tests | Request formatting for Ollama API. JSON mode payload. Streaming mode. Timeout handling. Retry on connection error. Response parsing |
| `tests/unit/test_content_generator.py` | 6-8 tests | Self-consistency check (3-pass). Confidence flagging below threshold. Content length validation. Misconception format. Analogy format |
| `tests/unit/test_course_builder.py` | 10-12 tests | SkeletonBuilder: depth 1 produces ~6 concepts, depth 3 produces ~20, depth 5 produces ~40+. Course overview is populated prose (not null). Bloom's levels assigned in ascending order. Prerequisites reference valid concept titles. LLM parse failure triggers retry (up to 3). resource_text is prose (not JSON array). ContentHydrator: queries only concepts for target course_uid. Generated content ≥100 words per concept. Misconceptions array has ≥1 entry. Key terms extracted. Embedding generated per concept |
| `tests/unit/test_tutoring_flow.py` | 10-12 tests | Conversation history stores questions AND answers. Grade-2 resets question timer. Multi-question mastery requires ≥2 streak AND ≥3 questions. Micro-lecture triggers after 3 failures. Grade-4 skips exactly one concept (not two). Hesitation penalty removed. Question type rotates correctly. Diagnostic probe sets initial Bloom's level. Consolidation triggers every 3-4 concepts |

**Integration Tests (pytest, requires running services)**

| Test File | Tests | What It Validates |
|-----------|-------|-------------------|
| `tests/integration/test_sqlite_rag.py` | 10-12 tests | Schema creation. Course CRUD. Concept CRUD. Vector search (insert embedding, query nearest). Flat syllabus query. Course tree query. Due concepts query. Concurrent read access (WAL mode). Foreign key cascading deletes |
| `tests/integration/test_ollama_connection.py` | 4-5 tests | Ollama reachable at `host.docker.internal:11434`. Model `qwen3:14b` loaded. Chat completion returns valid response. JSON mode returns parseable JSON. Streaming returns chunks |
| `tests/integration/test_kokoro_tts.py` | 4-5 tests | TTS service health check. Generate audio from text (returns WAV). Cache hit on repeat request. Voice parameter accepted. Audio file > 0 bytes |
| `tests/integration/test_web_routes.py` | 12-15 tests | Every route returns 200 (or correct error). `/api/courses` proxies correctly. `/api/event` proxies POST to core. `/api/tts` proxies to TTS. `/api/set_active_course` sets state. `/api/delete_course` cascades. Static assets served. Socket.IO connects |
| `tests/integration/test_course_creation.py` | 10-12 tests | Create course via POST /api/create_course (not socket emit). Verify depth parameter produces proportional concept counts (depth 1 vs depth 3). Verify concepts have prose resource_text ≥100 words (not JSON arrays). Verify misconceptions and analogies populated. Verify bloom_level assigned (not all default 1). Verify prerequisites stored. Verify embeddings exist for all concepts. Verify course overview not null. Verify course status transitions: building→ready. Verify concurrent creation is blocked. Verify failed creation sets status='error'. Course appears in `/api/courses` list |
| `tests/integration/test_custom_course_wizard.py` | 8-10 tests | `/api/suggest_modules` returns valid module list given course context. `/api/suggest_concepts` returns concepts that don't duplicate user-defined ones. `/api/clarify_course` generates 3-5 questions (not 0, not 20). `/api/create_course_custom` accepts full wizard payload and creates course. User-defined concepts appear with `source='user'`. Generated concepts appear with `source='generated'`. User notes (module + concept level) are stored and non-empty in design_brief. Q&A answers modify generated content (spot-check: if answer says "SI units only" then content doesn't reference imperial). Course with mixed user/generated concepts has correct prerequisite ordering. Concurrent custom creation blocked |
| `tests/integration/test_web_search_pipeline.py` | 8-10 tests | SearXNG health endpoint reachable. Research service health reports `searxng_reachable: true`. `/api/research_concept` returns sources and combined_text for a well-known topic ("photosynthesis"). Wikipedia content present in results for common topics. Domain tier filtering blocks known bad domains (chegg.com). Cache hit returns identical results on second call (verify via timing <50ms). `/api/research_batch` processes 5 concepts in parallel. Graceful degradation: mock SearXNG down → research service returns confidence=0.0 with empty sources (no crash). Concepts from generated course have `sources` JSON with ≥1 URL for ≥80% of concepts. `source_confidence` values are between 0.0 and 1.0 |
| `tests/integration/test_gamification_settings.py` | 10-12 tests | GET /api/profile returns all expected keys with default values. PATCH /api/profile updates display_name and persists across requests. GET /api/gamification returns zeroed state on fresh DB. POST /api/gamification/award_xp with grade=3 returns positive xp_earned. XP multiplier applied correctly (first-try × Bloom L4+). POST /api/gamification/check_streak on first call sets streak to 1. Second check_streak same day doesn't increment streak. Level-up detected when XP crosses threshold. Achievement "first_course" unlocks after course creation. Achievement doesn't re-trigger on second check. Gamification state persists across service restart (Docker volume). Settings page loads at /settings with HTTP 200 |

**End-to-End Tests (playwright or selenium, requires full stack running)**

| Test File | Tests | What It Validates |
|-----------|-------|-------------------|
| `tests/e2e/test_full_learning_flow.py` | 5-6 tests | Navigate Home→Courses→Create course→wait for creation→Start course→answer Socratic question→receive graded response→see XP animation→next concept→verify progress saved |
| `tests/e2e/test_review_flow.py` | 3-4 tests | Create course→complete a concept→wait for due date (or mock time)→navigate to Review→answer review question→verify FSRS state updated |
| `tests/e2e/test_navigation.py` | 5-6 tests | Every nav link loads correct page. Settings modal opens/closes. Theme toggle switches between light/dark. No console errors on any page. Socket.IO connects once per page (no duplicates) |
| `tests/e2e/test_tts_playback.py` | 2-3 tests | Tutor message has play button. Click play→audio plays (mock audio output). Play button state changes to pause during playback |
| `tests/e2e/test_course_wizard.py` | 5-6 tests | Navigate Courses→"Build Custom"→ `/courses/new` loads with Step 1. Fill Step 1→Next→Step 2 shows. Add module manually→appears as card. Click "✨ Suggest Modules"→suggestions appear→accept one→card added. Navigate to Step 3→expand module→"✨ Suggest Concepts"→concepts appear. Step 4→clarifying questions rendered→answer them→Next→Step 5 progress shows. Verify user-defined concepts labeled "(your concept)" and generated ones labeled "(generated)" |

**Acceptance Criteria (all must pass before deployment)**

| # | Criterion | How to Verify |
|---|-----------|---------------|
| A1 | All 6 nav links return 200 | `pytest tests/integration/test_web_routes.py` |
| A2 | Course creation via REST produces ≥5 concepts with prose ≥100 words | `pytest tests/integration/test_course_creation.py` |
| A3 | Depth 1-5 produces proportionally different concept counts | `pytest tests/unit/test_course_builder.py::test_depth_scaling` |
| A4 | Concepts have misconceptions, analogies, bloom_level, key_terms | `pytest tests/integration/test_course_creation.py::test_content_metadata` |
| A5 | Embeddings generated; semantic search returns results | `pytest tests/integration/test_sqlite_rag.py::test_vector_search` |
| A6 | Socratic dialogue loop with history in prompt | `pytest tests/e2e/test_full_learning_flow.py` |
| A7 | Conversation history includes questions AND answers | `pytest tests/unit/test_tutoring_flow.py::test_history_includes_answers` |
| A8 | Mastery requires ≥2 correct streak AND ≥3 questions | `pytest tests/unit/test_tutoring_flow.py::test_multi_question_mastery` |
| A9 | Micro-lecture triggers after 3 consecutive failures | `pytest tests/unit/test_tutoring_flow.py::test_micro_lecture_fallback` |
| A10 | FSRS updates concept.due_date after interaction | `pytest tests/integration/test_sqlite_rag.py::test_fsrs_update` |
| A11 | TTS play button generates audio via Kokoro | `pytest tests/integration/test_kokoro_tts.py` |
| A12 | No duplicate Socket.IO connections | Manual E2E test |
| A13 | Streaming tokens render incrementally | Manual E2E test |
| A14 | XP increments after correct answer | `pytest tests/e2e/test_full_learning_flow.py` |
| A15 | Dark/light theme toggles correctly | `pytest tests/e2e/test_navigation.py` |
| A16 | Docker compose brings 4 services healthy in 60s | `pytest tests/integration/test_health_checks.py` |
| A17 | Graceful recovery if Ollama temporarily unavailable | `pytest tests/integration/test_ollama_connection.py::test_recovery` |
| A18 | No sensitive data in git history | `git log --all -p -- .env` grep returns nothing |

### 16.4 Deployment Infrastructure

#### New `docker-compose.yml` (production-ready)

```yaml
version: '3.8'

services:
  web-ui:
    build: services/web-ui
    container_name: helga-web-ui
    ports:
      - "5000:5000"
    depends_on:
      core-logic:
        condition: service_healthy
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - FLASK_ENV=production
    volumes:
      - app-data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 256M

  core-logic:
    build:
      context: .
      dockerfile: services/core/Dockerfile
    container_name: helga-core-logic
    ports:
      - "5003:5003"
    depends_on:
      rag-engine:
        condition: service_healthy
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_URL=http://host.docker.internal:11434
      - RAG_URL=http://helga-rag-engine:5002
      - TTS_URL=http://helga-tts:5005
      - FLASK_ENV=production
    volumes:
      - app-data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5003/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M

  rag-engine:
    build:
      context: .
      dockerfile: services/rag/Dockerfile
    container_name: helga-rag-engine
    ports:
      - "5002:5002"
    environment:
      - FLASK_ENV=production
    volumes:
      - app-data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5002/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 768M

  tts:
    build: services/tts
    container_name: helga-tts
    ports:
      - "5005:5005"
    volumes:
      - tts-cache:/app/data/tts_cache
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5005/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M

  searxng:
    image: searxng/searxng:latest
    container_name: helga-searxng
    restart: unless-stopped
    volumes:
      - ./configs/searxng:/etc/searxng:rw
    environment:
      - BASE_URL=http://localhost:8080
      - INSTANCE_NAME=helga-search
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 256M

  research:
    build: services/research
    container_name: helga-research
    ports:
      - "5006:5006"
    depends_on:
      searxng:
        condition: service_healthy
    environment:
      - SEARXNG_URL=http://helga-searxng:8080
      - FLASK_ENV=production
    volumes:
      - research-cache:/app/data/research_cache
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5006/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 384M

volumes:
  app-data:
    driver: local
  tts-cache:
    driver: local
  research-cache:
    driver: local

networks:
  default:
    driver: bridge
```

**Key changes from current docker-compose:**
- `condition: service_healthy` replaces `depends_on` without health gating
- Named volumes instead of bind-mount `./data` (persistent across rebuilds)
- No `user: 0:0` (containers create non-root users in Dockerfile)
- No NVIDIA runtime, no /dev/snd, no PulseAudio
- `extra_hosts` for Ollama access from inside containers
- `restart: unless-stopped` instead of `restart: always`
- Memory limits appropriate for Mac Mini 24GB (total: ~2GB for all containers, leaving ~12GB for Ollama + OS)

#### Reverse Proxy (Caddy)

For production access beyond localhost, add a `Caddyfile`:

```
helga.local {
    reverse_proxy localhost:5000
    tls internal
}
```

Or for Cloudflare tunnel (already referenced in .env):
```bash
cloudflared tunnel --url http://localhost:5000
```

#### Updated `deploy.sh`

```bash
#!/bin/bash
set -e

echo "=== Helga Socratic Tutor — Mac Mini Deployment ==="

# 1. Prerequisites check
echo "[1/5] Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "Docker required. Install Docker Desktop for Mac."; exit 1; }
command -v ollama >/dev/null 2>&1 || { echo "Ollama required. Install from ollama.com"; exit 1; }

# 2. Pull LLM model
echo "[2/5] Pulling Qwen 3 14B model..."
ollama pull qwen3:14b

# 3. Create data directories
echo "[3/5] Setting up data directories..."
mkdir -p data/logs data/sqlite data/tts_cache data/hf_cache

# 4. Environment setup
echo "[4/5] Checking environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from template. Review before continuing."
    exit 1
fi

# 5. Build and start
echo "[5/5] Building and starting services..."
docker compose build
docker compose up -d

# Health check
echo "Waiting for services to start..."
sleep 10
for service in web-ui core-logic rag-engine tts; do
    if docker inspect --format='{{.State.Health.Status}}' "helga-$service" 2>/dev/null | grep -q healthy; then
        echo "  ✓ $service: healthy"
    else
        echo "  ✗ $service: not yet healthy (may still be starting)"
    fi
done

echo ""
echo "=== Helga is running ==="
echo "  Web UI:  http://localhost:5000"
echo "  Ollama:  http://localhost:11434"
echo "  Logs:    docker compose logs -f"
```

#### Updated `Makefile`

```makefile
.PHONY: build up down logs clean test test-unit test-integration deploy health

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	docker compose down -v
	docker system prune -f

deploy:
	./deploy.sh

health:
	@for svc in web-ui core-logic rag-engine tts; do \
		printf "%-15s " "$$svc:"; \
		curl -sf http://localhost:$$(docker port helga-$$svc | head -1 | cut -d: -f2)/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "offline"; \
	done

test-unit:
	cd tests && python -m pytest unit/ -v --tb=short

test-integration:
	cd tests && python -m pytest integration/ -v --tb=short

test-e2e:
	cd tests && python -m pytest e2e/ -v --tb=short

test: test-unit test-integration

backup:
	mkdir -p backups
	cp data/sqlite/helga.db backups/helga_$$(date +%Y%m%d_%H%M%S).db
	@echo "Backup saved to backups/"
```

### 16.5 Error Handling & Resilience

| Area | Current | Required for Production |
|------|---------|----------------------|
| Ollama down | Core-logic crashes or hangs on timeout | Retry with exponential backoff (3 attempts). Return cached/pre-generated response. Show "Tutor temporarily unavailable" toast to user |
| RAG-engine down | Web-UI shows blank pages | Core-logic catches 502 errors from RAG proxy. Returns last-known course state from `session_state`. Web-UI shows stale-data warning banner |
| TTS down | Play button silently fails | Frontend catches fetch error, shows toast "Voice playback unavailable", disables play buttons with "offline" styling |
| SQLite locked | Write fails if another process holds lock | WAL mode handles concurrent reads. For writes, retry 3× with 100ms backoff. Only RAG-engine writes; core-logic writes via RAG API |
| LLM returns garbage | Grade parse fails, defaults to grade 3 | Log the raw LLM response for debugging. Use majority voting (3 attempts) for grading calls. If all 3 fail to parse, mark as "needs review" and skip to next question |
| Browser disconnects | Socket.IO reconnection is default | Ensure `socket.io` client has `reconnection: true, reconnectionAttempts: 5`. Show "Reconnecting..." banner. Re-fetch state on reconnect |
| Course creation fails midway | Partial data in SQLite | Wrap course creation in SQLite transaction. `ROLLBACK` on any error. Show clear error message to user |
| Docker container OOM | Container restarts, loses in-memory state | All state persisted in SQLite (not in-memory). `restart: unless-stopped` brings container back. Health check detects recovery |

### 16.6 Database Backup & Migration

**Automated daily backup (cron on Mac Mini):**
```bash
# Add to crontab -e
0 3 * * * cp ~/helga/data/sqlite/helga.db ~/helga/backups/helga_$(date +\%Y\%m\%d).db && find ~/helga/backups -name "*.db" -mtime +30 -delete
```

**Schema migration system:**
Create `services/rag/migrations/` directory with numbered SQL files:
```
001_initial_schema.sql
002_add_bloom_level.sql
003_add_gamification.sql
```

On startup, RAG-engine checks a `schema_version` table and runs any unapplied migrations in order. This prevents manual schema management as the app evolves post-sprint.

### 16.7 Documentation Deliverables

| Document | Purpose | Contents |
|----------|---------|----------|
| `README.md` | Project overview + quickstart | Architecture diagram, prerequisites (Docker, Ollama, Qwen 3 14B), 5-minute setup, screenshots |
| `.env.example` | Environment template | All config vars with placeholder values and comments. NO real secrets |
| `docs/ARCHITECTURE.md` | Technical architecture | Service diagram, data flow, API contracts between services, SQLite schema, decision rationale |
| `docs/DEPLOYMENT.md` | Production deployment guide | Mac Mini setup, Ollama config, Docker compose, Caddy/Cloudflare tunnel, backup cron, monitoring |
| `docs/API.md` | Internal API reference | Every route in web-ui, core-logic, rag-engine, tts with request/response schemas |
| `CONTRIBUTING.md` | Developer guide | How to set up dev environment, run tests, code style, PR process |

### 16.8 Environment Configuration

**New `.env.example`:**
```bash
# === Helga Configuration ===

# LLM Model (must be pulled via: ollama pull qwen3:14b)
OLLAMA_MODEL=qwen3:14b
OLLAMA_URL=http://host.docker.internal:11434

# Data paths (Docker volumes handle these, override for dev)
# HELGA_DATA_ROOT=./data

# Optional: Cloudflare tunnel for remote access
# CLOUDFLARED_TOKEN=your_token_here

# Pedagogy settings
SOCRATIC_DEPTH=3
FSRS_RETENTION=0.90
DEFAULT_VOICE=af_heart

# Flask
FLASK_ENV=production
SECRET_KEY=generate-a-random-key-here
```

**Startup validation in each service:**
```python
# Add to each service's startup
required_vars = ['OLLAMA_URL']  # varies per service
for var in required_vars:
    if not os.getenv(var):
        logger.critical(f"Missing required environment variable: {var}")
        sys.exit(1)
```

### 16.9 Monitoring & Observability

**Health dashboard:** The Status page (`/status`) becomes the production monitoring dashboard:
- 4 service cards (web-ui, core-logic, rag-engine, tts) with live health polling
- Ollama status card (model loaded, VRAM usage, tokens/sec)
- SQLite stats (database size, concept count, interaction count)
- System stats (Mac Mini CPU%, memory%, disk%)
- Last 20 log entries (aggregated from all services)

**Structured logging:** All services output JSON logs to stdout (Docker captures to `docker logs`):
```python
import logging, json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "service": record.name,
            "msg": record.getMessage(),
            "extra": getattr(record, 'extra', {})
        })
```

### 16.10 Final Sprint Phase Additions

#### Phase 6: Production Hardening (10-14 hours)

| Task | Hours | Files | Details |
|------|-------|-------|---------|
| 6.1 Security fixes | 3 | `.env`, `.env.example`, `.gitignore`, `web-ui/app.py`, `session.js` | Remove sudo password from .env. Add .env.example. CORS whitelist. Remove `innerHTML` for user text (use `textContent`). Add prompt injection delimiters. Add Flask-Limiter |
| 6.2 Non-root Docker | 1 | All Dockerfiles | Add `RUN useradd -m appuser` + `USER appuser` to each Dockerfile |
| 6.3 Error handling | 3 | `fsm_logic.py`, `web-ui/app.py`, `chat.js` | Ollama retry with backoff. Graceful degradation UI (offline banners, disabled buttons). Transaction wrapping for course creation. Reconnection banner for Socket.IO |
| 6.4 Write test suite | 6 | New: `tests/unit/`, `tests/integration/` (7 unit test files, 5 integration test files) | 50-60 unit tests + 35-40 integration tests. pytest + pytest-asyncio. See §16.3 for full matrix |
| 6.5 Deployment infra | 2 | `deploy.sh`, `Makefile`, `Caddyfile`, `docker-compose.yml` | Production docker-compose with health checks. Deploy script for Mac Mini. Makefile with test/backup/health targets. Optional Caddy reverse proxy |
| 6.6 Documentation | 2 | `README.md`, `.env.example`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md` | Complete rewrite of README for new architecture. Architecture doc with diagrams. Deployment guide |
| 6.7 Database migrations | 1 | New: `services/rag/migrations/`, `services/rag/migrate.py` | Numbered migration files. Auto-run on startup. Schema version tracking |
| 6.8 Structured logging | 1 | All service `*.py` files | JSON log formatter. Consistent log levels. Remove print() debug statements |
| 6.9 Backup automation | 0.5 | `Makefile`, `docs/DEPLOYMENT.md` | `make backup` command. Cron setup instructions. 30-day retention |
| 6.10 Final acceptance testing | 2 | Manual + automated | Run all A1-A27 acceptance criteria. Browser testing on Safari + Chrome. Verify dark theme. Load test (10 concurrent requests) |

### 16.11 FINAL Sprint Schedule (Complete)

| Day | Phase | Focus | Hours |
|-----|-------|-------|-------|
| 1-2 | Phase 1 | Delete dead code, Docker, SQLite, Ollama, SearXNG | 12-16 |
| 3-7 | Phase 2 | Core logic: bugs, prompts, LLM calls, FSRS, Bloom's + tutoring flow (TF.1-TF.12) | 34-42 |
| 8-12 | Phase 3 | RAG SQLite rewrite + course creation pipeline (CB.1-CB.8) + web search (WS.1-WS.4) + RAG endpoints | 34-36 |
| 13-15 | Phase 4a | CSS Alpine system, base.html, home, courses + Quick Create animations (CC.7,CC.10) + wizard (CC.1,CC.8) + gamification schema/APIs (GS.1-GS.3) | 25 |
| 16-18 | Phase 4b | Learn/Review/Test redesign, session.js, progress component (CC.6), wizard JS (CC.2), Socket events (CC.11), settings page (GS.4), header gamification bar (GS.5) | 30 |
| 19 | Phase 4c | Routes (CC.9), status cleanup | 8 |
| 20 | Phase 4d | Suggest modules/concepts + clarifying Q&A (CC.3,CC.4,CC.5) | 7 |
| 21 | TTS Phase | Kokoro service, Docker, frontend integration | 4-6 |
| 22 | Phase 5 | Caching, streaming polish, XP/badge animations (GS.6), achievement UI (GS.7), sounds (GS.8), home dashboard (GS.9), name integration (GS.10), gamification toggle (GS.11) | 14-16 |
| 23-25 | Phase 6 | Security, error handling, tests, deployment, docs | 10-14 |
| 26 | Acceptance | Final acceptance testing + 3 benchmark courses | 4-6 |
| **Total** | | | **182-207 hrs** |

### 16.12 Definition of Done

The sprint is complete when ALL of the following are true:

**Infrastructure**
- [ ] `docker compose up` brings all 6 services (web-ui, core-logic, rag-engine, tts, searxng, research) healthy within 90 seconds
- [ ] Ollama serves Qwen 3 14B responses at ≥15 tok/s
- [ ] All 6 navigation tabs (Home, Courses, Learn, Review, Test, Status) load without errors
- [ ] No `console.error` output in browser DevTools during normal operation
- [ ] No duplicate Socket.IO connections on any page

**Course Creation (A5-A9)**
- [ ] Course creation from Courses page triggers backend and shows real-time progress
- [ ] Created course has prose resource_text (not JSON arrays) with word count meeting mastery-level minimum
- [ ] Each concept has populated misconceptions, analogies, bloom_level, and key_terms
- [ ] Concept embeddings are generated and semantic search returns results
- [ ] Three-slider system (scope, mastery, starting_from) produces proportionally different courses: scope 5 ≈ 11 modules, mastery 5 ≈ 10 concepts/module, no hard caps

**Tutoring Flow (A10-A14)**
- [ ] Socratic dialogue loop works end-to-end: question → answer → graded response with grade badge
- [ ] Conversation history includes both questions AND student answers in LLM prompts
- [ ] Concept advancement requires ≥2 consecutive correct answers AND ≥3 total questions
- [ ] Micro-lecture triggers after 3 consecutive failures on a concept
- [ ] FSRS scheduling updates concept due dates after each interaction

**Learning Features (A15-A19)**
- [ ] Bloom's level tracks and advances per concept
- [ ] XP counter increments after correct answers
- [ ] Streak counter tracks daily activity
- [ ] TTS play button generates and plays audio via Kokoro
- [ ] Review tab pulls FSRS-due concepts and presents Socratic recall questions

**Quality & Security (A20-A24)**
- [ ] Alpine theme renders correctly in both light and dark modes
- [ ] Streaming tokens render incrementally in chat bubbles
- [ ] `make test-unit` passes (≥85% coverage on core modules)
- [ ] `make test-integration` passes (all service interactions verified)
- [ ] No sensitive data in `.env` or git history

**Deployment (A25-A27)**
- [ ] `make backup` creates valid SQLite backup
- [ ] README.md accurately describes the new architecture and setup
- [ ] Application recovers gracefully from Ollama being temporarily unavailable

**Interactive Course Creator (A28-A34)**
- [ ] Quick Create (⚡) works from courses page: topic + depth → REST call → progress → redirect to Learn
- [ ] Build Custom (🛠️) wizard loads at `/courses/new` with all 5 steps navigable
- [ ] "✨ Suggest Modules" returns LLM-generated module suggestions that user can accept/edit/dismiss
- [ ] "✨ Suggest Concepts" returns LLM-generated concept suggestions per module
- [ ] Clarifying Q&A step generates 3-5 relevant questions based on course structure and user notes
- [ ] User-defined concepts appear in final course marked as `source='user'`, generated ones as `source='generated'`
- [ ] User notes (module-level and concept-level) visibly influence generated content (spot-check 2-3 concepts)

**Quality Benchmarks (A35-A37)**
- [ ] Benchmark "Greek Philosophy" (scope=5, mastery=1, start=1) passes all QS and QC standards
- [ ] Benchmark "Machine Learning" (scope=3, mastery=4, start=2) passes all QS and QC standards
- [ ] Benchmark "Sourdough Baking" (scope=1, mastery=3, start=1) passes all QS and QC standards

**Web Search Augmentation (A38-A43)**
- [ ] SearXNG container starts healthy and returns JSON search results
- [ ] Research service (`helga-research`) starts healthy and reports `searxng_reachable: true`
- [ ] Generated concepts have `sources` field with ≥1 URL for ≥80% of concepts
- [ ] Wikipedia content appears in source material for well-known topics (spot-check 3 concepts)
- [ ] Course creation completes successfully when SearXNG is down (graceful degradation, parametric fallback)
- [ ] Concepts with `source_confidence < 0.3` show low-confidence indicator in UI

**Course Creation UI & Animations (A44-A50)**
- [ ] Quick Create modal transitions smoothly from form → three-phase progress view (no page reload, no second modal)
- [ ] Progress tree shows modules/concepts appearing with slide-in animations during skeleton phase
- [ ] Research phase shows 🔎 icons with source counts per concept and source domain names
- [ ] Writing phase shows ✍️ icon with inline word-count progress bar per active concept
- [ ] Completion triggers confetti animation + stats cascade (modules → concepts → time → sources)
- [ ] Custom Wizard "✨ Suggest" buttons show loading state and return suggestion cards with accept/dismiss morphing
- [ ] Both creation paths use the same shared progress tree component (visual consistency)

**Gamification & Settings (A51-A62)**
- [ ] Settings page loads at `/settings` with all 6 sections (Profile, Appearance, Learning, Gamification, Voice, Data)
- [ ] Display name saved in settings appears in home page greeting and tutor addresses user by name
- [ ] Theme toggle (light/dark) switches instantly on all pages
- [ ] XP counter in header increments with number-roll animation after correct answer
- [ ] Level-up triggers toast notification with new level badge
- [ ] Streak counter shows current streak days, pulses on first daily interaction
- [ ] Daily goal dots fill progressively; reaching goal triggers "+50 XP" celebration
- [ ] Mastery badges (🌱→🌿→🌲→🏔️→⭐) display correctly on course structure and learn sidebar
- [ ] Badge upgrade during tutoring triggers spin + particle animation
- [ ] Achievement unlocks show full-width banner with icon, name, and XP reward
- [ ] Gamification toggle OFF hides all XP/streak/level/daily goal/achievements from UI (learning features remain)
- [ ] XP and streak data still tracked in DB when gamification is OFF (re-enabling restores accurate state)
