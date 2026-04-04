# Helga Course System Overhaul - Implementation Plan

## Context

The Helga Socratic Voice Tutor's course creation pipeline suffers from several critical issues:
1. The small LLM (Qwen2.5-1.5B, ~800 token output) frequently fails to generate complex nested JSON structures (~30% failure rate)
2. Content hydration produces bloated concept files that mix teaching context with flashcard data
3. Depth levels 1-5 don't produce meaningfully different courses
4. No validation/recovery in the creation pipeline - failures abort entire courses
5. No cleanup for half-built courses that clutter the filesystem
6. Review tab lacks proper flashcard generation workflow
7. Course cards on the UI are minimal and uninformative

This overhaul addresses all 10 requirements while keeping the tri-layer storage (SQLite/JSON/Markdown) and offline-first architecture intact.

---

## Phase 1: LLM JSON Simplification & Pre-Skeleton Checks

**Files:** `services/core/course_builder.py`, `services/common/llm_utils.py`

### 1A. Flatten LLM JSON Requests (Solve JSON Complexity)

**Problem:** Asking the small LLM for nested JSON (modules with embedded units with embedded lessons) causes frequent malformed output.

**Solution:** Progressive single-level generation. Each LLM call produces only a flat array of simple objects.

**Changes to `course_builder.py` - `SkeletonBuilder`:**

- **Step 1 - Generate Modules:** LLM returns flat list: `[{"title": "...", "rationale": "...", "scope": ["...", "..."]}]` (current approach, keep it)
- **Step 2 - Generate Units per Module:** For each module, LLM returns flat list: `[{"title": "...", "focus": "..."}]` — NO nested lessons inside
- **Step 3 - Generate Lessons per Unit:** For each unit, LLM returns flat list: `[{"title": "...", "objective": "..."}]` — NO nested concepts
- **Step 4 - Generate Concepts per Lesson:** For each lesson, LLM returns flat list: `[{"title": "...", "objectives": ["...", "..."]}]`

This replaces the current `_build_substructures()` which asks for nested `[{title, lessons: [{title}]}]` format. Each call is simpler, more reliable, and easier to validate.

**New method:** `_build_substructures_progressive()` replaces `_build_substructures()`

### 1B. Pre-Skeleton Builder Checks

**New method in `SkeletonBuilder`:** `_run_preflight_checks(topic, max_depth)`

Checks before any LLM calls:
1. LLM health check (already exists, enhance with response quality test)
2. Topic validation (non-empty, reasonable length 3-200 chars)
3. Depth range validation (1-5)
4. Storage writability check (can write to courses directory)
5. ZIM availability check (if ZIM source selected)
6. Memory/resource check (basic)

Each check logs result: `[PREFLIGHT] ✓ LLM Online (latency: 230ms)` or `[PREFLIGHT] ✗ ZIM archive not found`

Emit status callbacks for each check so the live viewer shows them.

---

## Phase 2: Creation Pipeline Checks & Recovery

**Files:** `services/core/course_builder.py`, `services/rag/librarian.py`

### 2A. Skeleton Validation Checkpoints

After each generation phase, validate before proceeding:

**Post-Module Check:**
- Minimum module count met (based on depth)
- No empty titles after normalization
- At least 2 scope items per module
- If fails: retry module generation (up to 3x), then abort with clear error

**Post-Unit Check (per module):**
- At least 1 unit generated
- No duplicate unit titles within module
- If fails: retry unit generation for that module only

**Post-Lesson Check (per unit):**
- At least 1 lesson generated
- If fails: retry lesson generation for that unit only

**Post-Concept Check (per lesson):**
- At least 2 concepts generated
- Each concept has at least 1 learning objective
- If fails: retry concept generation for that lesson only

**New method:** `_validate_phase(phase_name, items, min_count, parent_title)` — returns `(valid, issues[])`

### 2B. Hydration Pipeline Checks

**In `ContentHydrator.hydrate()`:**

- **Pre-hydration check:** Verify all concept UIDs exist in structure, content directory exists
- **Per-concept validation:** After generating markdown, check:
  - File written successfully (exists and >100 bytes)
  - Contains required sections (Core Definition, Contextual Explanation, Socratic Hook)
  - If validation fails: retry that concept (up to 2 more times)
- **Post-hydration audit:**
  - Count successful vs failed concepts
  - If >30% failed: mark course as "partial" instead of "ready"
  - Log summary: `"Hydration: 45/50 succeeded, 5 failed (10%)"`

### 2C. Live Viewer Check Display

Status callbacks for checks:
- `CHECK:PREFLIGHT:LLM:PASS` / `CHECK:PREFLIGHT:LLM:FAIL:reason`
- `CHECK:SKELETON:MODULES:PASS:4_modules`
- `CHECK:SKELETON:UNITS:RETRY:module_title`
- `CHECK:HYDRATION:CONCEPT:FAIL:concept_title`
- `CHECK:HYDRATION:SUMMARY:45/50`

Frontend parses these to show green checkmarks or red X marks in the log.

---

## Phase 3: Depth Logic Overhaul

**Files:** `services/core/course_builder.py`

### New Depth Configuration

Replace scattered depth logic with a centralized `DEPTH_PROFILES` dictionary:

```python
DEPTH_PROFILES = {
    1: {
        "label": "Quick Overview",
        "academic_level": "Introductory",
        "target_modules": 2,
        "units_per_module": 1,
        "lessons_per_unit": 1,
        "concepts_per_lesson": 2,
        "content_words": 150,
        "vocabulary": "simple terms, everyday language, high-level intuition",
        "instruction": "Explain like a casual introduction. Use simple analogies. No jargon.",
        "hydration_sections": ["Core Definition", "Contextual Explanation", "Socratic Hook"],
    },
    2: {
        "label": "Foundational",
        "academic_level": "Undergraduate",
        "target_modules": 3,
        "units_per_module": 2,
        "lessons_per_unit": 2,
        "concepts_per_lesson": 3,
        "content_words": 250,
        "vocabulary": "standard educational level, key technical terms introduced",
        "instruction": "Cover fundamentals with clear definitions. Introduce terminology.",
        "hydration_sections": ["Core Definition", "Component Breakdown", "Contextual Explanation", "Socratic Hook"],
    },
    3: {
        "label": "Comprehensive",
        "academic_level": "Graduate",
        "target_modules": 3,
        "units_per_module": 3,
        "lessons_per_unit": 2,
        "concepts_per_lesson": 3,
        "content_words": 350,
        "vocabulary": "technical depth, precise mechanisms, causal relationships",
        "instruction": "Thorough coverage with technical precision. Explain mechanisms and causality.",
        "hydration_sections": ["Core Definition", "Component Breakdown", "Contextual Explanation", "Misconceptions", "Socratic Hook"],
    },
    4: {
        "label": "Advanced",
        "academic_level": "Postgraduate / Professional",
        "target_modules": 4,
        "units_per_module": 3,
        "lessons_per_unit": 3,
        "concepts_per_lesson": 4,
        "content_words": 450,
        "vocabulary": "deep technical precision, professional terminology, edge cases",
        "instruction": "Professional-level detail. Cover edge cases, trade-offs, and real-world nuances.",
        "hydration_sections": ["Core Definition", "Component Breakdown", "Contextual Explanation", "Misconceptions", "Advanced Notes", "Socratic Hook"],
    },
    5: {
        "label": "Expert / Research",
        "academic_level": "Doctoral / Research",
        "target_modules": 4,
        "units_per_module": 4,
        "lessons_per_unit": 3,
        "concepts_per_lesson": 5,
        "content_words": 600,
        "vocabulary": "doctoral-level synthesis, cutting-edge research, theoretical frameworks",
        "instruction": "Research-level depth. Include theoretical frameworks, open problems, and frontier research.",
        "hydration_sections": ["Core Definition", "Component Breakdown", "Contextual Explanation", "Misconceptions", "Advanced Notes", "Research Frontiers", "Socratic Hook"],
    },
}
```

This replaces the scattered depth_labels dicts and scaling math. Each prompt references the profile for vocabulary, instruction style, and structural sizing.

---

## Phase 4: Content Hydration Overhaul & Context File Redesign

**Files:** `services/core/course_builder.py`, `services/common/storage.py`

### 4A. Remove Flashcards from Context Files

The `_condense_and_structure_content()` method currently asks the LLM to generate flashcards inline. Remove the Flashcards section from the prompt and output format.

**New context file structure (.md):**

```markdown
# {Concept Title}

## Metadata
- **Depth**: {depth} ({label})
- **Path**: {course} > {module} > {unit} > {lesson}
- **Source**: {zim|llm-generated|local-file}

## Core Definition
{One precise sentence defining the concept}

## Component Breakdown
- **{Component 1}**: {Explanation}
- **{Component 2}**: {Explanation}
- **{Component 3}**: {Explanation}

## Contextual Explanation
{Deep-dive content, depth-appropriate, ~{content_words} words}

## Misconceptions
- **Common Belief**: {wrong assumption}
  **Reality**: {correction}

## Advanced Notes  ← (depth 4-5 only)
{Edge cases, trade-offs, professional nuances}

## Research Frontiers  ← (depth 5 only)
{Open problems, cutting-edge developments}

## Socratic Hook
{Open-ended question to test understanding}

## Teaching Anchors
- **Key Relationships**: {How this connects to prior/next concepts}
- **Prerequisite Knowledge**: {What the student should already know}
- **Common Entry Point**: {Best way to start teaching this}
```

The "Teaching Anchors" section is new — it gives the AI tutor direct guidance on how to use this content in a Socratic dialogue.

### 4B. Improved ZIM Hydration Path

Enhance ZIM content collection:
- Search with multiple query variants (title, title+course, objectives)
- Extract longer snippets (2000 chars instead of 1500)
- Better HTML cleanup for Wikipedia content
- Score and rank snippets by relevance before passing to LLM

### 4C. Improved LLM Hydration Path

When no ZIM content available:
- Generate content with depth-appropriate prompts from DEPTH_PROFILES
- Include hierarchy context and previously-covered concepts to avoid repetition
- Separate the structuring step: first generate raw content, then format into markdown sections
- This two-step approach produces better output from the small LLM than asking for structured markdown in one shot

---

## Phase 5: Flashcard Generation System (Review Tab)

**Files:** `services/rag/librarian.py`, `services/common/storage.py`, `services/web-ui/templates/review.html`

### 5A. Storage Layer

**New directory:** `/data/courses/{course_uid}/flashcards/`
**New file per concept:** `{concept_uid}.json`

```json
{
  "concept_uid": "con_abc123",
  "concept_title": "Thermal Conductivity",
  "cards": [
    {"front": "What is thermal conductivity?", "back": "A measure of a material's ability to conduct heat."},
    {"front": "How does thermal conductivity relate to material properties?", "back": "..."},
    {"front": "What units is thermal conductivity measured in?", "back": "Watts per meter-kelvin (W/m·K)"}
  ],
  "generated_at": "2026-02-22T10:30:00"
}
```

**New methods in `CourseStore`:**
- `has_flashcards(course_uid)` → bool (checks if flashcards/ dir exists with files)
- `save_flashcards(course_uid, concept_uid, cards_data)` → path
- `get_flashcards(course_uid, concept_uid)` → dict
- `get_all_flashcards(course_uid)` → list[dict]

### 5B. Flashcard Generation Endpoint

**New endpoint in `librarian.py`:** `POST /api/generate_flashcards`
- Input: `{course_uid}`
- Process: For each concept in course:
  1. Read concept .md content
  2. LLM generates 3 flashcards from the content (simple prompt, flat JSON)
  3. Save to flashcards/{concept_uid}.json
- Returns: `{status: "ok", cards_generated: 45}`
- Status callbacks during generation for live progress

### 5C. Review Tab Overhaul

When user selects a course for review:
1. Check if flashcards exist: `GET /api/has_flashcards?course_uid=X`
2. If NO flashcards:
   - Show generation UI: "Preparing flashcards for first review..."
   - Progress bar as cards are generated
   - Call `POST /api/generate_flashcards`
   - On completion, transition to review session
3. If flashcards exist:
   - Fetch due cards: `GET /api/due_cards?course_uid=X`
   - Standard review flow (show front → reveal back → grade)

### 5D. Due Cards & Grading Endpoints

**Implement missing endpoints in `librarian.py`:**

`GET /api/due_cards?course_uid=X`:
- Query SQLite `user_progress` for concepts with `next_review_date <= today`
- If no due cards, return all unreviewed concepts (status='locked')
- Load flashcard JSON for each due concept
- Return flattened card array

`POST /api/update_card`:
- Input: `{concept_uid, course_uid, grade}` (hard/good/easy)
- Update `user_progress` with SM-2 algorithm: new easiness_factor, interval, next_review_date
- Return updated schedule info

---

## Phase 6: Auto-Cleaner for Failed Courses

**Files:** New file `services/common/course_cleaner.py`, `services/rag/librarian.py`

### 6A. Cleaner Module

**New file:** `services/common/course_cleaner.py`

```python
def clean_incomplete_courses(data_dir):
    """Remove courses with status != 'ready' on startup."""
    courses_dir = os.path.join(data_dir, "courses")
    cleaned = 0
    preserved = 0

    for name in os.listdir(courses_dir):
        structure_path = os.path.join(courses_dir, name, "structure.json")
        if not os.path.exists(structure_path):
            # No structure = incomplete, delete
            shutil.rmtree(os.path.join(courses_dir, name))
            cleaned += 1
            continue

        with open(structure_path) as f:
            course = json.load(f)

        status = course.get("status", "unknown")
        if status not in ("ready",):
            shutil.rmtree(os.path.join(courses_dir, name))
            cleaned += 1
        else:
            preserved += 1

    logger.info(f"Course cleanup: {cleaned} removed, {preserved} preserved")
```

### 6B. Run on Startup

In `librarian.py` startup (before Flask app.run):
```python
from services.common.course_cleaner import clean_incomplete_courses
clean_incomplete_courses(DATA_ROOT)
```

This runs every time the Docker container restarts.

---

## Phase 7: Course Card Visual Overhaul

**Files:** `services/web-ui/templates/courses.html`

### New Card Design

Replace the current minimal cards with richer information cards:

**Card Layout:**
```
┌─────────────────────────────────────┐
│ [Gradient header bar with icon]     │
│  📐 Nuclear Engineering             │
│  Depth: ●●●○○ (3/5 Comprehensive)  │
├─────────────────────────────────────┤
│                                     │
│  ┌─────┐  4 Modules                │
│  │ 78% │  12 Units                  │
│  │ ○○○ │  28 Lessons                │
│  └─────┘  86 Concepts              │
│  Progress                           │
│                                     │
│  Status: ● Ready                    │
│  Created: Feb 22, 2026              │
│  Last studied: 2 days ago           │
│                                     │
│  [▶ Continue Learning]  [📋 Review] │
│                          [🗑️ Delete]│
└─────────────────────────────────────┘
```

**Enhancements:**
- Show depth level as filled/empty dots
- Show full stats (modules, units, lessons, concepts)
- Show creation date and last study date
- Progress ring is larger and more prominent
- Status badge (Ready / Building / Partial / Failed)
- Direct links to both Learn and Review modes
- Subtle gradient based on course topic hash
- Hover effect with slight elevation

### API Enhancement

Modify `GET /api/courses` response to include:
- `depth` field
- `created_at` formatted date
- Full stats (modules, units, lessons, concepts)
- Last activity date from SQLite
- Completion percentage from SQLite

---

## Phase 8: Storage Verification

**Files:** `services/common/storage.py`

Verify and enforce the storage separation:
- **SQLite** (`helga.db`): user_progress, activity_log, scheduled_reviews, user_settings
- **JSON** (`structure.json`): Course hierarchy (modules→units→lessons→concepts with UIDs, titles, ordinals, objectives)
- **Markdown** (`content/*.md`): Concept teaching content
- **JSON** (`flashcards/*.json`): Flashcard data (NEW - separate from content)

No structural changes needed here — the current architecture already follows this pattern. Just ensure no flashcard data leaks into .md files (handled in Phase 4A).

---

## Implementation Order

1. **Phase 3** - Depth profiles (foundation for everything else)
2. **Phase 1A** - Flatten LLM JSON requests
3. **Phase 1B** - Pre-skeleton checks
4. **Phase 2** - Pipeline validation checkpoints
5. **Phase 4** - Content hydration overhaul + context file redesign
6. **Phase 6** - Auto-cleaner
7. **Phase 5** - Flashcard generation system
8. **Phase 7** - Course card visuals
9. **Phase 8** - Storage verification (mostly confirmation)

---

## Files Modified

| File | Changes |
|------|---------|
| `services/core/course_builder.py` | Major: flatten JSON gen, depth profiles, pipeline checks, context file redesign, remove flashcards from content |
| `services/common/storage.py` | Add flashcard storage methods to CourseStore |
| `services/common/llm_utils.py` | Minor: no changes needed |
| `services/common/course_cleaner.py` | **NEW**: Auto-cleanup module |
| `services/rag/librarian.py` | Add flashcard endpoints, due_cards/update_card implementation, cleaner on startup, enhanced course listing |
| `services/web-ui/templates/courses.html` | Course card visual overhaul |
| `services/web-ui/templates/review.html` | Flashcard generation flow, improved review UX |
| `services/web-ui/app.py` | New proxy routes for flashcard endpoints |
| `docker-compose.yml` | No changes needed |

---

## Verification

1. **Course creation (depth 1):** Should produce ~2 modules, ~2 units, ~4 concepts with simple language
2. **Course creation (depth 5):** Should produce ~4 modules, ~16 units, ~60 concepts with research-level language
3. **Pipeline recovery:** Intentionally corrupt an LLM response to verify retry logic
4. **Auto-cleaner:** Create a course, set status to "building", restart container, verify it's deleted
5. **Flashcard generation:** Select a course for review, verify flashcard generation UI appears, cards are created in flashcards/ directory
6. **Review flow:** After flashcards exist, verify due cards are served and grading updates SM-2 weights
7. **Course cards:** Visual inspection of courses page for richer card display
8. **Logs:** Check that preflight checks and validation checkpoints appear in live viewer
