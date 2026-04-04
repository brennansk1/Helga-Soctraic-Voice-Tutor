# Helga — Complete Verification Checklist

> Comprehensive E2E verification covering every user flow, feature, and loop.
> Updated: 2026-03-14

---

## STATUS LEGEND
- [ ] = Not verified
- [x] = Verified working
- [!] = Known issue / partial

---

## FLOW A: AUTOMATIC COURSE CREATION (Full E2E)

> User creates a course by typing a topic. Tests the full pipeline:
> Browser → FSM text parser → SkeletonBuilder → ContentHydrator → storage → redirect

### A1. Initiation
- [ ] Navigate to `/courses`
- [ ] Click "Create Course" → creation modal opens
- [ ] Select "Automatic" tab
- [ ] Type a topic (e.g., "Photosynthesis") in the topic input
- [ ] Click "Create" / Submit

### A2. Pipeline Progress
- [ ] Socket.IO connects and status_update events begin arriving
- [ ] Progress bar shows staged updates (Skeleton → Hydration → Finalizing)
- [ ] No "Error" status appears during normal creation
- [ ] Status messages are readable (not truncated to 80 chars)
- [ ] If LLM fails during skeleton, user sees meaningful error (not "System Idle")

### A3. Pipeline Completion
- [ ] "Course Complete!" message or redirect fires on success
- [ ] Course appears in `/courses` listing with correct title
- [ ] Course card shows module/concept count
- [ ] Course status is "ready" (not "building" or "failed")

### A4. Failure Recovery
- [ ] If creation fails mid-hydration, course is marked "failed" or "partial" (not "ready")
- [ ] Starting a second creation while one is in progress shows "busy" error
- [ ] Closing browser mid-creation does not leave orphaned thread forever
- [ ] Status updates go only to the initiating browser (not all connected clients)

### A5. Content Verification
- [ ] Open the created course's structure.json — verify modules/units/lessons/concepts exist
- [ ] Open a concept markdown file — verify it has real content (not stub placeholder)
- [ ] Verify concept UIDs follow `con_` prefix + 8-char hex format
- [ ] Verify no duplicate concept titles within a module

---

## FLOW B: CUSTOM COURSE WIZARD (Full E2E)

> User builds a course with manual control: metadata → module design → preview → create

### B1. Step 1 — Metadata
- [ ] Click "Create Course" → select "Custom Wizard" tab
- [ ] Enter course title, description, teaching style
- [ ] Select content source (None / Upload / ZIM)
- [ ] Click "Next" → moves to Step 2

### B2. Step 2 — Module Design
- [ ] Module input fields appear (title + description for each)
- [ ] Can add/remove modules dynamically
- [ ] Can upload source files per module (txt, md, pdf)
- [ ] Click "Preview" → POST to `/api/custom_course/preview`

### B3. Step 3 — Preview
- [ ] Preview shows generated structure (modules → units → lessons → concepts)
- [ ] LLM fallback modules show warning indicator (if applicable)
- [ ] Module count matches what user specified
- [ ] Can go back to edit modules without losing data

### B4. Finalize & Create
- [ ] Click "Create Course" → POST to `/api/custom_course/create`
- [ ] Progress indicator shows during creation
- [ ] On success, course appears in listing with correct title
- [ ] Source files are cleaned up after successful creation

### B5. Edge Cases
- [ ] Upload a non-supported file type → rejected with clear error
- [ ] Submit with empty module title → validation error shown
- [ ] If hydration fails, course marked "partial" (retryable), not silently broken
- [ ] Module-to-source-file mapping is by title, not position (WIZ-1/WIZ-2 fix)
- [ ] If creation takes >300s (504 timeout), user can poll `/api/course_status/{uid}`

---

## FLOW C: EPUB UPLOAD (Full E2E)

> User uploads an EPUB file → system extracts content → generates course

### C1. Upload
- [ ] Click "Create Course" → select "EPUB Upload" tab
- [ ] EPUB file input accepts only `.epub` files
- [ ] Non-EPUB files rejected client-side before upload
- [ ] File size >50MB shows warning/rejection
- [ ] Click "Upload" → progress indicator appears

### C2. Processing
- [ ] Server extracts text from EPUB chapters
- [ ] SkeletonBuilder generates course structure from content
- [ ] ContentHydrator populates concept markdowns
- [ ] Status updates stream back to browser via Socket.IO

### C3. Result
- [ ] On success, course appears in listing with EPUB-derived title
- [ ] Course has proper module/unit/lesson/concept hierarchy
- [ ] Concept content reflects actual EPUB chapter content
- [ ] On close/reopen of EPUB modal, form is reset (EPUB-4 fix)

---

## FLOW D: COURSE INSTRUCTION — SOCRATIC LEARNING (Full E2E)

> User selects a course → navigates to concept → Socratic teaching loop → completion
> This is the PRIMARY teaching flow and the core user experience.

### D1. Course Selection (Courses → Learn Tab)
- [ ] From `/courses`, click "Start Journey" on a course
- [ ] URL changes to `/learn?course_uid=<uid>`
- [ ] FSM receives SET_CONTEXT event → active_course_uid is set
- [ ] Course title displays in learn tab header
- [ ] Path visualization renders with all concept nodes

### D2. Path Visualization
- [ ] Nodes show correct completion status (locked/available/completed)
- [ ] First uncompleted concept is highlighted as "current"
- [ ] Completed concepts show checkmark/green state
- [ ] SVG connection lines render between nodes
- [ ] Clicking a locked node does nothing (or shows "complete prerequisites first")
- [ ] Nodes are not clickable during initial render (loading guard)

### D3. Concept Entry (Click Node → Session View)
- [ ] Click an available concept node → session view appears
- [ ] Path view hides, session view (chat + controls) shows
- [ ] Mode indicator badge shows "Socratic Learning"
- [ ] Concept title displays in info bar
- [ ] Progress bar shows X/Y concepts completed
- [ ] Chat stream clears stale messages from previous concept
- [ ] `navigatingToNode` flag resets within 10s if FSM is slow

### D4. Socratic Teaching Loop
- [ ] FSM speaks an introductory micro-lecture about the concept
- [ ] Audio plays through browser (TTS enabled by default)
- [ ] First question appears (type: SCENARIO or similar)
- [ ] Question type badge shows current type + dot progression
- [ ] User can type response in text input → message appears immediately (optimistic render)
- [ ] User can use microphone → STT transcription appears → auto-sends

### D5. Grading & Progression
- [ ] FSM grades user response (1-4 scale)
- [ ] Grade 4 (mastery): positive feedback, advance to next question type
- [ ] Grade 3 (adequate): brief feedback, advance to next type
- [ ] Grade 2 (partial): hint provided, retry same type
- [ ] Grade 1 (incorrect): micro-lecture + retry, max 2 retries before advancing
- [ ] After all 6 question types completed → concept marked as mastered
- [ ] "You've mastered this concept" spoken before advancing

### D6. Concept Completion → Next Concept
- [ ] After mastery, next concept in syllabus queue loads automatically
- [ ] Bridge intro speaks ("Now let's move to...")
- [ ] Conversation history clears (no stale LLM context leaks)
- [ ] New concept's pedagogy (misconceptions/analogies) loads in sidebar
- [ ] Progress bar updates (X+1/Y)
- [ ] storage.progress.mark_completed() called → path node updates on refresh

### D7. Skip & Navigation
- [ ] Click skip button (⏭) → advances to next concept WITHOUT marking current as complete
- [ ] Say "next" in voice mode → same skip behavior
- [ ] Say "go back" → saves progress, speaks confirmation
- [ ] Back button (←) → returns to path view, session pauses
- [ ] Returning to path view shows updated completion status
- [ ] FSM receives PAUSE_SESSION event on back button (mic/TTS stop)

### D8. Course Completion
- [ ] After last concept mastered, FSM speaks completion message with next-step guidance
- [ ] "COURSE_COMPLETE" status update triggers completion overlay in browser
- [ ] Overlay shows options: "Review with Flashcards", "Browse More Courses", "Stay Here"
- [ ] Clicking "Review with Flashcards" → navigates to `/review`
- [ ] Clicking "Browse More Courses" → navigates to `/courses`
- [ ] FSM state transitions to LOBBY

### D9. Session Resume
- [ ] Navigate away from learn tab mid-session, then return
- [ ] Click "Resume Course" on course card → learn tab loads with progress restored
- [ ] FSM restores current_lesson_node from user_state.json
- [ ] Syllabus queue resumes from where user left off
- [ ] Completed topics are still marked complete in path view

---

## FLOW E: SPACED REPETITION / REVIEW (Full E2E)

> User reviews previously studied concepts using flashcard-style questions

### E1. Entry from Learn Tab
- [ ] After completing concepts in Socratic mode, FSRS schedules reviews
- [ ] Say "review" or navigate to `/review` page
- [ ] Due cards load from storage.progress.get_due_reviews()
- [ ] If no cards due → helpful message with next steps (not dead-end)

### E2. Review Session
- [ ] First card question appears (LLM-generated from concept content)
- [ ] User answers via text or voice
- [ ] FSM grades answer (correct / hint / fail)
- [ ] Correct: positive feedback, advance to next card
- [ ] Incorrect (attempt 1): hint provided, retry
- [ ] Incorrect (attempt 2): answer revealed, card rescheduled with short interval

### E3. Review Completion
- [ ] After all due cards reviewed → completion message with next-step guidance
- [ ] FSM transitions to LOBBY
- [ ] Review intervals update correctly (FSRS algorithm)
- [ ] Next review dates appear in Schedule page

### E4. Schedule Page Integration
- [ ] Navigate to `/schedule` → calendar renders current month
- [ ] Days with pending reviews show indicators
- [ ] Click a day → detail panel shows review items for that day
- [ ] "Start Review Session" button navigates to review flow
- [ ] Completing reviews updates the schedule display

---

## FLOW F: MEMORY PALACE (Full E2E)

> User explores concepts spatially through virtual "loci"

### F1. Entry
- [ ] From LOBBY state, say "enter palace" or navigate to palace mode
- [ ] If no active course → helpful error message (not dead-end)
- [ ] If active course → first concept locus loads
- [ ] Mode indicator shows "Memory Palace"

### F2. Navigation
- [ ] "Next" / "forward" → moves to next locus (concept)
- [ ] Locus description (concept title) is spoken
- [ ] Sonar/spatial audio cues trigger on arrival
- [ ] Wraps around at end of concept list (circular navigation)

### F3. Concept Anchoring
- [ ] Can anchor new concepts to current locus
- [ ] Vivid description prompt fires for memory encoding
- [ ] Palace state persists across sessions

### F4. Exit
- [ ] Saying "exit" or "go back" → returns to LOBBY
- [ ] Transition message with next-step guidance (not dead-end silence)

---

## FLOW G: MODE SWITCHING (Seamless Transitions)

> User switches between Socratic, Review, and Palace modes without confusion

### G1. Socratic → Review
- [ ] Mid-session, user says "review" → progress saved, mode switches
- [ ] If no due cards → message says so with option to continue studying
- [ ] Session state from Socratic preserved for later resume

### G2. Socratic → Palace
- [ ] Mid-session, user says "enter palace" → progress saved, mode switches
- [ ] Palace uses same active_course_uid for concept loci
- [ ] Can return to Socratic and resume from same concept

### G3. Review → Socratic
- [ ] After review complete, user says "open [course]" → Socratic resumes
- [ ] Progress from before review is intact

### G4. LOBBY State
- [ ] In LOBBY, user always has clear options communicated:
  - "open [course]" → Socratic Learning
  - "review" → Spaced Repetition
  - "enter palace" → Memory Palace
  - "list courses" → See available courses
  - "create a course on [topic]" → Course creation
- [ ] No silent dead-ends where user doesn't know what to do

---

## FLOW H: AUDIO / TTS (Quality & Reliability)

### H1. Basic Playback
- [ ] TTS audio plays at correct pitch and speed (not chipmunk / slow)
- [ ] Audio sample rate matches between Piper (22050 Hz) and browser AudioContext
- [ ] Volume is consistent across utterances (no jarring loud/quiet swings)
- [ ] Gentle fade-in on audio start (no harsh pop)

### H2. Queue & Serialization
- [ ] Rapid speak() calls don't overlap (serialized through thread pool)
- [ ] Audio queue in browser caps at 10 chunks (no memory bloat)
- [ ] If queue is full, oldest chunk drops (not newest)
- [ ] Audio playback has timeout safety (queue doesn't freeze forever)

### H3. TTS Toggle
- [ ] Speaker button toggles TTS on/off
- [ ] When TTS off, transcript still appears in chat (text-only mode)
- [ ] Toggle state consistent between UI button and FSM

### H4. Voice Input (STT)
- [ ] Mic button activates microphone
- [ ] STT transcription appears in preview element
- [ ] Final transcription auto-sends as text input to FSM
- [ ] Stopping mic cleanly releases audio resources

### H5. Multi-Client
- [ ] Two browsers connected simultaneously
- [ ] Audio from one session does NOT play in the other browser
- [ ] Status updates scoped to originating client

---

## FLOW I: COURSE MANAGEMENT

### I1. Course Listing
- [ ] `/courses` shows all courses with title, status, concept count
- [ ] Course cards render special characters safely (no XSS)
- [ ] "Start Journey" and "Resume Course" buttons present per card

### I2. Delete Course
- [ ] Click delete button → confirmation dialog with correctly escaped title
- [ ] Confirm delete → course removed from listing
- [ ] Course files cleaned up from disk (structure.json, content/)
- [ ] SQLite row removed

### I3. Course with Special Characters
- [ ] Create a course titled `O'Brien's "Test" <Course>` → no HTML injection
- [ ] Course title renders correctly in cards, modals, and learn tab header

---

## FLOW J: NAVIGATION & UI

### J1. Page Navigation
- [ ] Home page loads with stats (courses, mastered, streak)
- [ ] Courses page lists all courses
- [ ] Learn tab loads with course path visualization
- [ ] Review page loads with due cards or empty state
- [ ] Schedule page shows calendar with review dates
- [ ] Test/Quiz page loads with course selection
- [ ] Status page shows service health
- [ ] Settings page allows theme/voice changes

### J2. Theme Switching
- [ ] Premium Dark theme loads correctly
- [ ] Light theme loads correctly
- [ ] Cyberpunk theme (if implemented) loads correctly
- [ ] Reader theme (if implemented) loads correctly
- [ ] Theme persists across page navigation

### J3. Error States
- [ ] API failure shows user-friendly error (not raw exception)
- [ ] Network timeout shows retry option
- [ ] Empty course list shows "Create your first course" CTA
- [ ] Empty review queue shows guidance (not blank page)

---

## FLOW K: DATA INTEGRITY

### K1. Progress Persistence
- [ ] Complete a concept → refresh page → concept still marked complete
- [ ] Close browser → reopen → course progress intact
- [ ] FSM restart → user_state.json restores progress
- [ ] Transcript persists in user_state.json for session resume

### K2. Storage
- [ ] Course structure.json is well-formed JSON
- [ ] Concept markdown files exist for all concepts in structure
- [ ] SQLite database accessible and not corrupted
- [ ] Atomic writes prevent corruption on concurrent access (WAL mode)

### K3. FSRS Scheduling
- [ ] Completing a concept schedules FSRS review with correct interval
- [ ] Grade 4 → longer interval than Grade 2
- [ ] Reviews appear in schedule at correct future dates
- [ ] Skipping a concept does NOT schedule a review

---

## FLOW L: DOCKER & DEPLOYMENT

### L1. Service Startup
- [ ] `docker-compose up --build` starts all services without errors
- [ ] All services reach healthy state (health checks pass)
- [ ] Web UI accessible at port 5050
- [ ] Core logic, RAG, audio, input services all reachable

### L2. Service Recovery
- [ ] Restart core-logic container → FSM reinitializes cleanly
- [ ] Restart RAG container → courses still accessible
- [ ] All containers have restart policies configured

### L3. Offline Operation
- [ ] App works without internet connection (no CDN dependencies break it)
- [ ] All JS/CSS loads from local assets
- [ ] LLM inference runs locally (no external API calls)

---

## FLOW M: SECURITY

### M1. Input Validation
- [ ] Course title with `<script>` tag is escaped (no XSS)
- [ ] File upload rejects non-allowed types
- [ ] File upload enforces size limits (MAX_CONTENT_LENGTH)
- [ ] SQL queries use parameterized statements (no injection)

### M2. Prompt Injection
- [ ] Malicious user input doesn't alter system prompts
- [ ] Content filtering catches harmful content
- [ ] LLM safety layer active for all generation

---

## REMAINING IMPLEMENTATION ITEMS (Not Yet Built)

### Priority 1 — Must Have for Launch
- [x] EPUB upload route fully implemented (EPUB-1) — FSM parses epub filepath, uses LocalFileProvider
- [x] CSRF protection on all POST endpoints — session-based tokens, auto-attached via fetch wrapper
- [ ] Mobile responsive design (all pages)
- [x] `/api/course_status/{uid}` polling endpoint — RAG + web-ui proxy both implemented
- [ ] Full py-fsrs integration tests

### Priority 2 — Should Have
- [ ] Onboarding flow for first-time users
- [ ] Skeleton loading screens on all pages
- [ ] Bundle CDN dependencies locally for offline
- [ ] Accessibility (ARIA labels, focus management, skip nav)
- [ ] Status page polling optimization (visibility API)

### Priority 3 — Nice to Have
- [ ] Incremental ingestion (add content to existing course)
- [ ] Browser automation tests (Playwright)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Architecture documentation with Mermaid diagrams
- [ ] Lightweight `/api/courses/summary` endpoint
