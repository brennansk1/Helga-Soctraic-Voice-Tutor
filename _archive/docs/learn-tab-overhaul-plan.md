# LEARN TAB OVERHAUL — Implementation Plan

## Current Status: [COMPLETE — ALL PHASES]
**Last Updated:** 2026-03-10
**Goal:** Fix 10 bugs found in the teaching loop audit + 8 learn.html UI bugs to make the Socratic teaching flow functional, pedagogically sound, and resilient.

### Technical Breakthroughs (Completed)
- [x] **Native LLM Acceleration:** Migrated from Dockerized CPU inference to native Ollama (Metal/GPU), reducing response latency from 45s to <5s.
- [x] **Studio-Quality TTS:** Eliminated manual PCM chunking/static. Now streams 22.05kHz WAV files via 10MB Base64 WebSocket envelopes with native C++ decoding.
- [x] **Auto-Socratic Transition:** Lectures no longer "dead-end". The LLM is now prompted to immediately append a Socratic question to every explanation.

---

## PHASE 1: Critical FSM Fixes (Completed ✅)

- [x] **Step 1.1 — BUG 1:** Set `question_start_time` in Socratic flow.
- [x] **Step 1.2 — BUG 2:** Add user answers to `conversation_history`.
- [x] **Step 1.3 — BUG 3:** Fix grade 2 dead-end (Always ask follow-up).

---

## PHASE 2: Teaching Loop Quality Improvements (Completed ✅)

- [x] **Step 2.1 — BUG 4:** Add retry limit (3 attempts) before auto-escalating to lecture.
- [x] **Step 2.2 — BUG 10:** Rule-based mode classifier (Ignorance detection).
- [x] **Step 2.3 — BUG 6:** Inject LLM grading feedback into tutor's spoken response.
- [x] **Step 2.4 — BUG 5:** Fix analogy extraction regex (`##+` support).
- [x] **Step 2.5 — BUG 7:** Prevent premature concept completion on Grade 4.

---

## PHASE 3: Learn.html UI Fixes (Completed ✅)

- [x] **Step 3.1 — LRN-3:** `setActiveCourse()` redirect includes `course_uid`.
- [x] **Step 3.2 — LRN-1:** Context-setting `startCourse()` handler.
- [x] **Step 3.3 — LRN-7:** 60s Navigation Guard timeout (safety reset).
- [x] **Step 3.4 — LRN-6:** Structure-ready loading guards.
- [x] **Step 3.5 — LRN-9/11:** `PAUSE_SESSION` on back button + re-fetch structure.
- [x] **Step 3.7 — Null guards:** Traverse structure safely.
- [x] **Step 3.8 — Resize leak:** Listener cleanup on re-render.

---

## PHASE 4: Design Improvements (Completed ✅)

- [x] **Step 4.1 — BUG 8:** Auto-populate `syllabus_queue` after single-concept navigation.
- [x] **Step 4.2 — BUG 9:** Feed Socratic grades to FSRS for review scheduling.
- [x] **Step 4.3 — LRN-12:** Persist and restore `conversation_history` to DB.

---

## PHASE 6: Audio Engineering & Stability (Completed ✅)

- [x] **WAV Streaming:** Bypassed `audioop` resampling; sending raw 22.05kHz WAV.
- [x] **Payload Cap:** Increased Socket.IO `max_http_buffer_size` to 10MB to prevent truncation.
- [x] **Unblock Autoplay:** Sync `AudioContext` unlock on user click event.
- [x] **JSON Envelope:** Wrapped Base64 in dictionary to avoid Engine.IO type errors.

---

## Current Known Issues / Problems
1. **Audio Smoothness:** User reports audio is still "not smooth" on some responses (Evaluating potential browser buffer underruns or network jitter).
2. ~~**Missing Structure 404s:**~~ Fixed — ROOT CAUSE: state poller passed `current_lesson_uid` (concept UID) instead of `active_course_uid` (course UID) to `/api/course_structure`. Also increased proxy timeout from 3s to 10s.
3. **STT Deadlocks:** Occasional 0-byte STT chunks causing "Audio Ready" but no transcript (Needs auto-reconnect logic).
4. ~~**Teaching Context Gaps:**~~ Fixed — FSM now broadcasts PEDAGOGY status updates; state poller also sends graph_node pedagogy data via renderPedagogy().

## Additional Fixes Applied (Post-Phase 4)
- **AUTO-1/UI-4:** Removed duplicate `socket.connect()` in `setupCreationSocket()`
- **EPUB-4:** `closeEpubModal()` now resets the form
- **LRN-5:** `NAVIGATE_TO_TOPIC` now returns error if no `active_course_uid` instead of slow cross-course search
- **Stale context leak:** `navigate_to_topic()` now clears `conversation_history` alongside `transcript`
- **Missing progress sync:** `next_syllabus_item()` now calls `storage.progress.mark_completed()` so path view shows green nodes
- **Auto-save:** Progress auto-saved after each concept completion
- **LLM artifact leak:** Bridge intro and hint responses now cleaned via `clean_llm_response()`
- **Micro-lecture gaps:** `get_micro_lecture_prompt()` now receives `missing_concepts` from misconceptions data
- **Bare except:** Fixed bare `except:` in grading JSON extraction
- **UI cleanup:** Question type badge hidden on back navigation; pedagogy sidebar reset on new concept

## Teaching Flow Audit Fixes (Full Audit)
- **CRITICAL — State poller 404s:** `app.py` state_poller passed concept UID as course UID to RAG — course_structure was always null. Fixed to use `active_course_uid`.
- **CRITICAL — Skip marks complete:** SKIP_CONCEPT and "next" command called `next_syllabus_item()` which marks concept complete. Added `_advance_without_completing()` method that skips without completion.
- **HIGH — 2s user message delay:** User saw no feedback for up to 2 seconds after sending. Added optimistic render in `sendTextMessage()` — user message appears instantly.
- **MEDIUM — "go back" was dead:** `handle_nav_commands("go back")` just played a sound and swallowed the event. Now saves progress and speaks confirmation.
- **MEDIUM — Question badge persists:** Badge not reset when entering new concept via enterNode(). Now hidden on entry.
- **MEDIUM — Pedagogy sidebar flicker:** `renderPedagogy()` re-created DOM every 2s poll cycle. Added change detection to skip re-render when data unchanged.

### Step 1.1 — BUG 1: Set `question_start_time` in Socratic flow
**File:** `services/core/fsm_logic.py`
**Location:** After line 1350 (`self.speak(question)`) in `ask_socratic_question()`

**Problem:** `question_start_time` is never set in the Socratic flow (only set in spaced repetition line 1609 and memory palace line 1711). It stays at 0, so `latency = time.time() - 0 = 1.7 billion seconds`. `_detect_hesitation()` checks `latency > 8` — always true — every grade > 2 is silently penalized -1.

**Fix:** Add one line after line 1350:
```python
self.speak(question)
self.question_start_time = time.time()  # Start response timer
```

Also set it in the grade 2 dead-end branch (line 1500-1502) where `speak()` is called without a follow-up question, and after bridge intro in `next_syllabus_item()` (after line 1210) since `ask_socratic_question` is called there too (it'll be set at the end of that call).

**Dependencies:** None — standalone fix.

---

### Step 1.2 — BUG 2: Add user answers to `conversation_history`
**File:** `services/core/fsm_logic.py`
**Location:** Start of `handle_socratic_answer()` (after line 1408)

**Problem:** Only two `conversation_history.append` calls exist: `(None, intro)` at line 1195 and `(context_trigger, question)` at line 1347. Student answers are never recorded. The LLM grades and generates follow-ups with zero memory of what the student said.

**Fix:** Add at the start of `handle_socratic_answer()`, after `self.question_start_time = 0`:
```python
# Record student answer in conversation history for LLM context
self.conversation_history.append((text, None))  # User text, no assistant response yet
```

Then after the tutor speaks in each grade branch (grades 1-4), update the last history entry's assistant response. OR simpler: append `("tutor_feedback", response_text)` after `self.speak()` in the grade branches where the tutor speaks feedback (lines 1500, 1506, 1519, 1526).

**Also add:** Cap `conversation_history` at 20 entries (matching PERF-5 transcript cap):
```python
if len(self.conversation_history) > 20:
    self.conversation_history = self.conversation_history[-20:]
```

**Dependencies:** None — standalone fix.

---

### Step 1.3 — BUG 3: Fix grade 2 dead-end
**File:** `services/core/fsm_logic.py`
**Location:** Lines 1499-1502

**Problem:** When grade=2 and `missing_concepts` is empty, the code calls `self.speak("You're close...")` but NEVER calls `ask_socratic_question()`. The session stalls — student has no question to respond to.

**Fix:** Replace the `else` branch:
```python
else:
    self.speak(
        "You're close, but that's a bit vague. Can you be more specific?"
    )
    # BUG-3 FIX: Always ask a follow-up question after hint
    self.ask_socratic_question(
        "[SYSTEM NOTE: Student gave a vague answer. Ask a more targeted version of the same question type, focusing on specifics.]"
    )
```

**Dependencies:** Step 1.1 (so the timer is set after the follow-up question).

---

## PHASE 2: Teaching Loop Quality Improvements

### Step 2.1 — BUG 4: Add retry limit for grade 1/2
**File:** `services/core/fsm_logic.py`
**Location:** `__init__` (add attribute), `handle_socratic_answer()` grade 1/2 branches, `next_syllabus_item()` (reset)

**Problem:** Student scoring grade 1-2 stays on the same question type forever with no escalation.

**Fix:**

a) Add attribute in `__init__` near line 240:
```python
self.socratic_retry_count = 0
```

b) In `handle_socratic_answer()`, increment on grade <= 2:
```python
if grade <= 1:
    self.socratic_retry_count += 1
    if self.socratic_retry_count >= 3:
        # Escalate: force micro-lecture then advance
        self.socratic_retry_count = 0
        self.speak("Let me explain this concept to help you understand.")
        self.ask_socratic_question(
            "[SYSTEM NOTE: Student failed 3 times. Give a clear micro-lecture explanation, then advance to the next question type.]",
            initial_mode="LECTURE"
        )
        self.socratic_type_index += 1  # Advance past this type
    else:
        self.play_sound("FRICTION_GRIND")
        self.ask_socratic_question("Identify gap in knowledge.")

elif grade == 2:
    self.socratic_retry_count += 1
    if self.socratic_retry_count >= 3:
        # After 3 partial attempts, give targeted help and advance
        self.socratic_retry_count = 0
        self.socratic_type_index += 1
        self.ask_socratic_question(
            "[SYSTEM NOTE: Student struggled 3 times. Briefly clarify the gap, then ask the NEXT question type.]",
            initial_mode="LECTURE"
        )
    else:
        # existing grade 2 logic (with BUG-3 fix)
        ...
```

c) Reset in `next_syllabus_item()` after line 1200:
```python
self.socratic_type_index = 0
self.socratic_retry_count = 0  # Reset retry counter for new concept
```

d) Reset on grade >= 3 (success resets retry counter):
```python
# In grade 3 and grade 4 branches:
self.socratic_retry_count = 0
```

**Dependencies:** Step 1.3 (grade 2 branch is modified there).

---

### Step 2.2 — BUG 10: Replace LLM teaching mode classifier with rule-based logic
**File:** `services/core/fsm_logic.py`
**Location:** Lines 1225-1248 in `ask_socratic_question()`

**Problem:** Extra LLM call (15s timeout) on every question cycle to classify LECTURE vs QUESTION. On a Jetson device this doubles latency per question. The heuristic at line 1230 already catches IDK phrases.

**Fix:** Replace lines 1225-1248 with:
```python
elif self.conversation_history:
    last_entry = self.conversation_history[-1]
    if last_entry[0]:
        last_text = str(last_entry[0]).strip()
        # Rule-based mode selection (replaces slow LLM classifier)
        if self._detect_ignorance(last_text.lower()):
            teaching_mode = "LECTURE"
        elif hasattr(self, '_last_socratic_grade') and self._last_socratic_grade <= 1:
            teaching_mode = "LECTURE"
        # Otherwise keep default QUESTION mode
```

Also store the grade in `handle_socratic_answer()`:
```python
self._last_socratic_grade = grade  # After grade is determined, before decision matrix
```

This removes one LLM call per question cycle, halving latency.

**Dependencies:** Step 1.2 (conversation_history now has student text).

---

### Step 2.3 — BUG 6: Use grading feedback in tutor response
**File:** `services/core/fsm_logic.py`
**Location:** Lines 1462-1464 and grade branches (1486-1532)

**Problem:** `feedback = result.get("feedback", "")` is captured but never used. Student gets generic "Correct." / "Exactly right." instead of targeted feedback.

**Fix:** Store feedback and incorporate it into spoken responses:

```python
# Grade 3 branch (line 1518-1519), replace:
self.speak("Correct.")
# With:
if feedback:
    self.speak(feedback)
else:
    self.speak("Correct.")

# Grade 4+ branch (line 1506), replace:
self.speak("Exactly right.")
# With:
if feedback:
    self.speak(feedback)
else:
    self.speak("Exactly right.")
```

Note: `feedback` is a local variable in scope from line 1464. For grade 1 (ignorance bypass at line 1411-1414), feedback won't exist — guard with `feedback = locals().get('feedback', '')` or initialize `feedback = ""` at the top of the method.

**Dependencies:** None.

---

### Step 2.4 — BUG 5: Fix pedagogy extraction regex
**File:** `services/core/fsm_logic.py`
**Location:** Lines 1153-1154 in `next_syllabus_item()`

**Problem:** Regex searches for `### Analogies` (h3) but the hydration prompt in `course_builder.py` generates `## Analogies` (h2). Analogies are never extracted.

**Fix:** Change line 1153-1154 from:
```python
ana_match = _re.search(
    r"### Analogies\s*\n(.*?)(?=\n##\s|\n###\s|\Z)", content, _re.DOTALL
)
```
To:
```python
ana_match = _re.search(
    r"##+ Analogies\s*\n(.*?)(?=\n##\s|\Z)", content, _re.DOTALL
)
```

The `##+` pattern matches both `##` and `###`, making it robust against either format.

**Dependencies:** None.

---

### Step 2.5 — BUG 7: Fix premature concept completion on grade 4
**File:** `services/core/fsm_logic.py`
**Location:** Lines 1504-1515

**Problem:** Grade 4 adds concept to `completed_topics` immediately at line 1507-1508, even if student only answered 1 of 6 question types. Then `socratic_type_index += 2` — if it exceeds the array, `next_syllabus_item()` is called, which adds the concept AGAIN. If it doesn't exceed, the concept was already marked complete but student continues answering.

**Fix:** Only mark complete when actually moving to next concept:
```python
elif grade >= 4:
    self.play_sound("SUCCESS_CHORD")
    self.socratic_retry_count = 0
    if feedback:
        self.speak(feedback)
    else:
        self.speak("Exactly right.")
    # Accelerate: skip next type too
    self.socratic_type_index += 2
    if self.socratic_type_index >= len(SOCRATIC_QUESTION_TYPES):
        # All types exhausted — NOW mark complete and move on
        if self.current_lesson_node and self.current_lesson_node.get("uid"):
            self.completed_topics.add(self.current_lesson_node["uid"])
        self.next_syllabus_item()
    else:
        self.ask_socratic_question("Continue deeper exploration.")
```

**Dependencies:** Step 2.3 (feedback variable used).

---

## PHASE 3: Learn.html UI Fixes

### Step 3.1 — LRN-3: Fix `setActiveCourse()` redirect to include course_uid
**File:** `services/web-ui/templates/courses.html`
**Location:** `setActiveCourse()` function, redirect line

**Problem:** Redirects to `/learn` without `?course_uid=`, causing an immediate redirect back to `/courses`.

**Fix:** Change:
```javascript
window.location.href = '/learn';
```
To:
```javascript
window.location.href = '/learn?course_uid=' + uid;
```

**Dependencies:** None.

---

### Step 3.2 — LRN-1: Replace "Start Journey" direct link with context-setting onclick
**File:** `services/web-ui/templates/courses.html`
**Location:** Course card template where `<a href="/learn?course_uid=...">` is generated

**Problem:** Direct `<a>` tag navigates to learn page without ever setting FSM active course context via API.

**Fix:** Replace the `<a>` tag with:
```html
<button onclick="startCourse('${course.uid}', '${escapeHtml(course.title)}')">Start Journey</button>
```

Add the `startCourse()` function:
```javascript
async function startCourse(uid, title) {
    try {
        await fetch('/api/event', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({type: 'SET_CONTEXT', payload: {course_uid: uid}})
        });
    } catch(e) {
        console.warn('Failed to set context:', e);
    }
    window.location.href = '/learn?course_uid=' + uid;
}
```

**Dependencies:** LRN-2 SET_CONTEXT handler (already implemented in fsm_logic.py).

---

### Step 3.3 — LRN-7: Add timeout to reset `navigatingToNode` flag
**File:** `services/web-ui/templates/learn.html`
**Location:** `enterNode()` function, after line 1453

**Problem:** `window.navigatingToNode = true` is set on every `enterNode()` call. It's only cleared in `session.js` `updateChatStream()` when fresh content arrives. If FSM never responds, the flag stays true forever — all subsequent messages suppressed.

**Fix:** Add a timeout after setting the flag:
```javascript
window.navigatingToNode = true;

// LRN-7: Safety timeout — reset flag if FSM doesn't respond within 15s
if (window._navGuardTimeout) clearTimeout(window._navGuardTimeout);
window._navGuardTimeout = setTimeout(() => {
    if (window.navigatingToNode) {
        console.warn('[enterNode] Navigation guard timeout — resetting');
        window.navigatingToNode = false;
        const chat = document.getElementById('chat-stream');
        if (chat && chat.querySelector('.loading-placeholder')) {
            chat.innerHTML = `
                <div class="message ai error-msg">
                    <div class="msg-content">
                        The tutor didn't respond in time. Try clicking the concept again, or use the Skip button.
                    </div>
                </div>
            `;
        }
    }
}, 15000);
```

**Dependencies:** None.

---

### Step 3.4 — LRN-6: Disable node clicks until structure renders
**File:** `services/web-ui/templates/learn.html`
**Location:** `renderStructure()` function and `enterNode()` function

**Problem:** `fetch('/api/course_structure')` is async. User can click nodes before `renderStructure()` completes, when `nodeEl.dataset.uid` is undefined.

**Fix:** Add a loading guard:

a) At the top of the DOMContentLoaded handler:
```javascript
let structureReady = false;
```

b) Modify `enterNode()` to check:
```javascript
function enterNode(uid, title) {
    if (!structureReady) {
        console.warn('[enterNode] Structure not ready yet');
        return;
    }
    if (!uid) {
        console.warn('[enterNode] No concept UID');
        return;
    }
    // ... rest of function
}
```

c) At the end of `renderStructure()`, after `requestAnimationFrame(drawAllLines)`:
```javascript
structureReady = true;
```

**Dependencies:** None.

---

### Step 3.5 — LRN-9/11 (frontend): Emit PAUSE_SESSION on back button + re-fetch structure
**File:** `services/web-ui/templates/learn.html`
**Location:** Back button handler at line 1480

**Problem:** Back button only toggles DOM visibility. FSM stays in SOCRATIC_LEARNING. Also, node completion status is never refreshed after concept completion (LRN-11).

**Fix:** Modify the back button handler:
```javascript
document.getElementById('back-to-path-btn-session').addEventListener('click', async () => {
    // Notify FSM to pause (LRN-9 — handler already exists in fsm_logic.py)
    if (window.sendEvent) {
        window.sendEvent('PAUSE_SESSION', {});
    }

    // Toggle views
    sessionView.classList.add('hidden');
    pathView.classList.remove('hidden');
    if (sessionHeader) sessionHeader.classList.add('hidden');
    if (headerLeft) headerLeft.classList.remove('hidden');
    if (contextSidebar) contextSidebar.classList.add('hidden');

    // LRN-11: Re-fetch and re-render structure to show updated completion status
    try {
        const res = await fetch(`/api/course_structure?uid=${courseUid}`);
        const data = await res.json();
        if (data.structure) {
            renderStructure(data.structure);
        }
    } catch (e) {
        console.warn('Failed to refresh structure:', e);
    }
});
```

**Dependencies:** LRN-9 FSM handler (already implemented).

---

### Step 3.6 — LRN-13: Remove dead `#back-to-path-btn` and its empty handler
**File:** `services/web-ui/templates/learn.html`
**Location:** Lines 10-12 (HTML button) and lines 1497-1502 (JS handler)

**Problem:** `#back-to-path-btn` has an empty event listener — deprecated dead code.

**Fix:**
- Remove the button HTML at lines 10-12
- Remove the JS listener at lines 1497-1502
- Remove the reference at line 1493 (`if (document.getElementById('back-to-path-btn'))...`)

**Dependencies:** None.

---

### Step 3.7 — Null guards in `renderStructure()`
**File:** `services/web-ui/templates/learn.html`
**Location:** `renderStructure()` function (lines 1264-1380)

**Problem:** No null checks for `mod.units`, `unit.lessons`, `lesson.concepts`, `concept.uid`, or `concept.title`. If structure has missing data, the page crashes silently.

**Fix:** Add guards at each traversal level:
```javascript
structure.modules.forEach(mod => {
    if (!mod || !mod.units) return;
    // ...
    (mod.units || []).forEach(unit => {
        if (!unit || !unit.lessons) return;
        // ...
        (unit.lessons || []).forEach(lesson => {
            // ...
            if (lesson.concepts) {
                lesson.concepts.forEach(concept => {
                    if (!concept || !concept.uid || !concept.title) return;
                    // ... existing node creation
                });
            }
        });
    });
});
```

**Dependencies:** None.

---

### Step 3.8 — Fix resize listener leak
**File:** `services/web-ui/templates/learn.html`
**Location:** Line 1379

**Problem:** `window.addEventListener('resize', drawAllLines)` is called every time `renderStructure()` runs (including re-render on back-button per Step 3.5). Listeners accumulate.

**Fix:** Remove before re-adding:
```javascript
// At top of renderStructure():
window.removeEventListener('resize', drawAllLines);

// At end (existing line 1379):
window.addEventListener('resize', drawAllLines);
```

**Dependencies:** Step 3.5 (which causes renderStructure to be called multiple times).

---

## PHASE 4: Design Improvements

### Step 4.1 — BUG 8: Auto-populate syllabus_queue after single-concept navigation
**File:** `services/core/fsm_logic.py`
**Location:** `next_syllabus_item()` at lines 1117-1124 (queue exhaustion check)

**Problem:** `navigate_to_topic()` clears `syllabus_queue`. After completing the navigated concept, the queue is empty, session ends, student sent to LOBBY. No auto-sequencing to next concept.

**Fix:** Before the "queue empty -> LOBBY" transition, attempt to re-populate:
```python
if not self.syllabus_queue:
    # Try to auto-populate with remaining concepts from the active course
    if self.active_course_uid:
        try:
            all_concepts = self.storage.courses.get_flat_concepts(self.active_course_uid)
            for c in all_concepts:
                if c["uid"] not in self.completed_topics:
                    content = self.storage.courses.get_concept_content(
                        self.active_course_uid, c["uid"]
                    )
                    self.syllabus_queue.append(
                        {"uid": c["uid"], "title": c["title"], "text": content or ""}
                    )
        except Exception as e:
            logging.warning(f"Failed to auto-populate syllabus: {e}")

    # If still empty after auto-populate, course is truly complete
    if not self.syllabus_queue:
        self.play_sound("SUCCESS_CHORD")
        self.speak("Course module complete. Great work.")
        self._schedule_unit_reviews_if_complete()
        self.state = "LOBBY"
        self.current_lesson_node = None
        return
```

**Dependencies:** None.

---

### Step 4.2 — BUG 9: Feed Socratic grades to FSRS for review scheduling
**File:** `services/core/fsm_logic.py`
**Location:** `next_syllabus_item()` after marking concept complete (line 1104-1115)

**Problem:** Socratic learning grades are never fed to the spaced repetition engine. A concept the student struggled with won't appear in review at the right interval.

**Fix:** After logging concept completion, schedule an FSRS review:
```python
# BUG-9: Schedule FSRS review based on Socratic performance
try:
    avg_grade = getattr(self, '_last_socratic_grade', 3)
    self.storage.schedule.schedule_concept_review(
        self.active_course_uid,
        concept_uid,
        concept_title,
        rating=avg_grade
    )
except Exception as e:
    logging.warning(f"Failed to schedule FSRS review: {e}")
```

Note: This depends on `storage.schedule.schedule_concept_review()` existing. If it doesn't yet, we add a stub that logs. Full FSRS integration deferred until CLAUDE.md Phase 3 (py-fsrs v6 upgrade).

**Dependencies:** FSRS engine (can be stubbed initially).

---

### Step 4.3 — LRN-12: Persist and restore conversation_history and transcript
**File:** `services/core/fsm_logic.py`
**Location:** `_save_current_course_progress()` and `_load_course_progress()`

**Problem:** conversation_history and transcript are in-memory only. Page refresh or container restart loses all chat history.

**Fix:** Expand the saved state:
```python
# In _save_current_course_progress() (extend course_state dict):
course_state = {
    "current_node": self.current_lesson_node,
    "syllabus_queue": self.syllabus_queue,
    "completed_topics": list(self.completed_topics),
    "transcript": self.transcript[-20:],
    "conversation_history": self.conversation_history[-10:],
    "socratic_type_index": self.socratic_type_index,
}

# In _load_course_progress() (restore additional fields):
self.transcript = data.get("transcript", [])
self.conversation_history = data.get("conversation_history", [])
self.socratic_type_index = data.get("socratic_type_index", 0)
```

**Dependencies:** Step 1.2 (conversation_history now has content worth saving).

---

## PHASE 5: Prompt Improvements

### Step 5.1 — Improve grading prompt for stricter grade 4
**File:** `services/common/prompts.py`
**Location:** `get_socratic_grading_prompt()` (lines 374-378)

**Problem:** Grade 4 (Easy) is too easy to get. LLM gives it for any decent answer.

**Fix:** Tighten grade 4 criteria:
```python
"- Grade 4 (Easy): Exceptional — student demonstrates understanding BEYOND the question. "
"They made a novel connection, identified an edge case unprompted, or explained the mechanism with precision. "
"This grade should be RARE (roughly 1 in 5 correct answers)."
```

**Dependencies:** None.

---

### Step 5.2 — Improve micro-lecture prompt to reference missing concepts
**File:** `services/common/prompts.py`
**Location:** `get_micro_lecture_prompt()` (lines 415-456)

**Problem:** When student fails and gets a micro-lecture, the prompt doesn't know what concepts were missed.

**Fix:** Add a `missing_concepts` parameter:
```python
def get_micro_lecture_prompt(topic, context_text, history=[],
                             style_modifier="standard", missing_concepts=None):
    missing_str = ""
    if missing_concepts:
        missing_str = (
            f"\n\nThe student specifically struggled with: "
            f"{', '.join(missing_concepts)}. Focus your explanation on these gaps."
        )
    # Insert missing_str into the task section of the prompt
```

**Dependencies:** None.

---

## FILES MODIFIED (Summary)

| File | Phases | Changes |
|------|--------|---------|
| `services/core/fsm_logic.py` | 1, 2, 4 | Set question_start_time; append user answers to conversation_history; fix grade 2 dead-end; retry limit with escalation; rule-based mode classifier; use feedback field; fix premature completion; auto-populate syllabus; FSRS integration stub; persist/restore history |
| `services/web-ui/templates/learn.html` | 3 | navigatingToNode timeout; structure-ready guard; PAUSE_SESSION on back + re-fetch; remove dead back button; null guards; resize listener cleanup |
| `services/web-ui/templates/courses.html` | 3 | Fix setActiveCourse redirect; add startCourse() function |
| `services/common/prompts.py` | 5 | Stricter grade 4 criteria; missing_concepts in micro-lecture |

---

## VERIFICATION PLAN

| # | What to Verify | How |
|---|----------------|-----|
| 1 | Timer fix (1.1) | Log latency values — should be 5-60s, not billions |
| 2 | History fix (1.2) | Log conversation_history length — should grow with each exchange |
| 3 | Dead-end fix (1.3) | Grade 2 with empty missing_concepts — student still gets a follow-up question |
| 4 | Retry limit (2.1) | 3 consecutive grade 1 — micro-lecture fires, then advances to next type |
| 5 | No LLM classifier (2.2) | Check logs — no "Teaching Mode Classifier" LLM call |
| 6 | Feedback (2.3) | Tutor speaks specific LLM feedback, not just generic "Correct." |
| 7 | Analogies (2.4) | Logs show `Retrieved pedagogy: X misc, Y analogies` where Y > 0 |
| 8 | Completion (2.5) | Grade 4 on question type 0 does NOT mark concept complete |
| 9 | courses.html (3.1-3.2) | Click "Start Journey" — arrive at `/learn?course_uid=X` with FSM context set |
| 10 | Nav guard timeout (3.3) | Kill core-logic container, click concept — 15s later error message appears |
| 11 | Back button (3.5) | Complete concept, click back — path view shows updated completion dots |
| 12 | Auto-sequence (4.1) | Navigate to single concept, complete it — next concept starts automatically |
