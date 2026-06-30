# Design Spec 05 — Assessment Engine (Exams, Interest-Theming, Gating)

> Implementation-ready design for **B18.1–B18.5** plus **Workstream G (interests-into-exams)**.
> Builds the formal exam layer on top of the existing Socratic grader and FSRS engine — it does
> **not** replace them. Canonical table/column names come from `docs/design/01_DATA_MODEL.md` §5
> (`exams`, `exam_attempts`, `exam_item_responses`) and §7 (`accommodations`); per-band grading
> calibration comes from `docs/design/02_GRADE_ADAPTATION.md` §4. Standards thresholds (GFL 74%,
> Basic Civics 35/50) come from `docs/UTAH_K12_CURRICULUM_REFERENCE.md`.
>
> **Reuse-first principle:** objective items grade deterministically in-process; free-response items
> reuse `get_socratic_grading_prompt` + `GRADE_JSON_SCHEMA` (`services/common/prompts.py:390,402`).
> Item generation reuses the constrained-JSON (`format=<schema>`) pattern. Gating reuses
> `_check_mastery_gate` semantics (`fsm_logic.py:1061`) and writes to `enrollments`/`user_progress`.

---

## 0. Where this lives

New service module **`services/exam/exam_engine.py`** (Flask blueprint mounted on the RAG service
process, port 5002, alongside `librarian.py`) — it shares `StorageManager` and the LLM utils with the
RAG engine and avoids a new container. New storage sub-store **`ExamStore`** (spec 01 §8) backs the
three exam tables. The FSM (`services/core/fsm_logic.py`) is a **client** of these endpoints for
checkpoint gating; the web-ui proxies them for the standalone exam UI.

```
Browser ─HTTP─▶ web-ui /api/exam/* ─HTTP─▶ exam_engine (rag proc :5002) ─▶ ExamStore (helga.db)
FSM (checkpoint gate) ─HTTP─▶ exam_engine /api/exam/* (server-to-server)
exam_engine ─▶ llm_generate_json(format=ITEM_SCHEMA)   (generation + theming + validity)
exam_engine ─▶ get_socratic_grading_prompt + GRADE_JSON_SCHEMA   (free-item grading)
```

All LLM calls go through `services/common/llm_utils.py` (`llm_generate_json` with `json_schema=`),
never the API directly — same rule as the rest of the codebase.

---

## 1. Exam taxonomy → `exams.kind` enum + `exams.blueprint`

`exams.kind` enum (matches 01 §5 column comment exactly):

| `kind` | Purpose | `scope_uid` | `course_uid` | Typical `pass_threshold` | Gating effect |
|---|---|---|---|---|---|
| `diagnostic` | Placement / pre-assessment; sets entry depth & Bloom floor. **Non-gating, non-failing.** | null | course or null | n/a (`0.0`) | Writes recommended start, never blocks |
| `checkpoint` | Per-unit gate. Must pass to unlock next unit. | unit `unit_…` | course | `0.80` (band-tunable) | Pass → unlock; fail → remediation |
| `unit` | Unit summative (broader than checkpoint; may coexist or replace it). | unit `unit_…` | course | `0.80` | Same as checkpoint when used as gate |
| `summative` | Course summative (end-of-course mastery across all standards). | null | course | `0.80` | Completion + standards report (spec 06) |
| `standardized_prep` | GFL end-of-course / Basic Civics practice modes. | null | course or null | `0.74` GFL, `0.70` Civics | Non-gating practice; scored to Utah cut |

> `unit` and `checkpoint` differ by intent only: a `checkpoint` is the smallest gate that unlocks the
> next unit; a `unit` summative is the full unit assessment. For v1 a unit's gate is a single
> `checkpoint` exam; a richer `unit` summative is additive and uses the same machinery.

### 1.1 `exams.blueprint` JSON (item slots)

`blueprint` is a JSON object of **item slots**. Each slot pins a `standard_code`, a `bloom` level
(1–6, Bloom labels match `prompts.py:320-326`), an `item_type`, and a `count`. The engine generates
`count` items per slot per attempt. `exams.standard_codes` is the deduped union of slot
`standard_code`s (kept denormalized for fast "which standards does this exam assert" queries).

```json
{
  "version": 1,
  "shuffle": true,
  "slots": [
    { "standard_code": "6.RP.1", "bloom": 2, "item_type": "mcq",     "count": 3 },
    { "standard_code": "6.RP.3", "bloom": 3, "item_type": "numeric", "count": 2 },
    { "standard_code": "6.RP.3", "bloom": 4, "item_type": "free",    "count": 1 },
    { "standard_code": "6.RP.2", "bloom": 2, "item_type": "ordering","count": 1 }
  ]
}
```

Total items = Σ `count`. Per-standard subscores (spec 06) aggregate over slots sharing a
`standard_code`. `bloom` per slot is bounded by the band ceiling/floor (`GRADE_BAND_PROFILES`,
spec 02 §3) at blueprint-authoring time for catalog exams.

**Blueprint authoring source.** Checkpoint/unit blueprints are derived from `concept_standards`
(01 §4): for the unit's concepts, collect distinct `standard_code`s, weight slot `count` by coverage
(`full` ≥ `partial`), and set `bloom` from each concept's `bloom_level` (clamped to band). GFL/Civics
blueprints are hand-authored fixtures keyed to their official strands (see §6.3). A blueprint validator
asserts: every `standard_code` exists in `standards`, every `bloom ∈ [band_floor, band_ceiling]`,
`item_type ∈ {mcq,free,numeric,ordering}`, and `count ≥ 1`.

---

## 2. Item generation & grading

### 2.1 Per-attempt generation (no fixed bank in v1)

Items are generated **per attempt** so theming (§3) and accommodations (§7) apply at attempt time and
retakes get fresh items (§9). The durable record is `exam_item_responses` (01 §5). A persisted item
bank is an explicit later option (§10).

Generation is constrained to one slot: target `standard_code` + `bloom` + `item_type`. Reuse the
constrained-JSON pattern — pass `ITEM_SCHEMA` to Ollama `format` via `llm_generate_json(...,
json_schema=ITEM_SCHEMA)`, exactly as the grader uses `GRADE_JSON_SCHEMA`.

### 2.2 Item JSON schema (`ITEM_SCHEMA`)

```python
ITEM_SCHEMA = {
  "type": "object",
  "properties": {
    "item_type":   {"type": "string", "enum": ["mcq", "free", "numeric", "ordering"]},
    "prompt":      {"type": "string"},
    # mcq:
    "choices":     {"type": "array", "items": {"type": "string"}},   # 3-5; required iff mcq
    "answer_index":{"type": "integer", "minimum": 0},                 # required iff mcq
    # numeric:
    "answer_value":{"type": "number"},                               # required iff numeric
    "tolerance":   {"type": "number"},                               # abs tolerance, default 0
    "unit":        {"type": "string"},
    # ordering:
    "ordering":    {"type": "array", "items": {"type": "string"}},   # correct order; required iff ordering
    # free:
    "rubric":      {"type": "string"},                               # grade-3 bar; fed to Socratic grader
    "exemplar":    {"type": "string"},                               # model answer (never shown to student)
    # shared:
    "standard_code": {"type": "string"},
    "bloom":         {"type": "integer", "minimum": 1, "maximum": 6},
    "difficulty":    {"type": "string", "enum": ["easy", "medium", "hard"]}
  },
  "required": ["item_type", "prompt", "standard_code", "bloom", "difficulty"]
}
```

The engine validates per-type required fields after generation (mcq needs `choices` + `answer_index`;
numeric needs `answer_value`; ordering needs `ordering`; free needs `rubric`). On a malformed item it
retries once, then falls back to a slot-level stock item (logged).

### 2.3 Generation prompt outline

```
SYSTEM: You are an expert K-12 assessment item writer for the Utah Core Standards.
  Write exactly ONE assessment item that tests the given standard at the given Bloom level
  as the given item type. Output ONLY valid JSON matching the schema — no prose.

  STANDARD: {standard_code} — "{standard_text}"        # from standards.text (01 §4)
  STRAND:   {strand}                                    # standards.strand
  BLOOM:    Level {bloom} — {bloom_name}: {bloom_directive}   # reuse prompts.py:320-326 labels
  ITEM TYPE: {item_type}
  GRADE BAND: {grade_band}                              # spec 02 §3 register + vocab ceiling

  RULES:
  - The item MUST test {standard_code} at Bloom {bloom}; do not drift to an easier recall task.
  - Use {grade_band} vocabulary and sentence length ({profile.register}).
  - mcq: exactly one correct choice; 3-5 plausible distractors that reflect real misconceptions.
  - numeric: a single numeric answer; give answer_value, tolerance, and unit.
  - ordering: 3-5 steps; give the correct order in `ordering`.
  - free: write a `rubric` describing the Grade-3 bar and an `exemplar` answer.
  - NEVER reveal the answer in the prompt text. NEVER add commentary.
```

Two-step flow when interests apply: **generate (this step) → theme (§3) → validity guard (§4)**.

### 2.4 Grading

- **Objective items (mcq / numeric / ordering)** — graded **deterministically in-process**, no LLM:
  - `mcq`: `is_correct = (response_index == answer_index)`.
  - `numeric`: parse student value; `is_correct = abs(value - answer_value) <= tolerance`. Unit
    mismatch (if `unit` set and student supplied a different unit) → incorrect.
  - `ordering`: `is_correct = (response_sequence == ordering)` (exact). Optional partial-credit mode
    (Kendall-tau) is a later flag; v1 is exact.
  - Store `is_correct ∈ {0,1}` and `grade ∈ {0,1}` in `exam_item_responses` (objective uses 0/1 in the
    `grade` column per 01 §5 comment).

- **Free-response items** — reuse the **existing Socratic grader**. Call
  `get_socratic_grading_prompt(concept=standard_text, question=prompt, user_answer=response,
  context_text=rubric, bloom_level=bloom, mastery_criteria=rubric)` and run it through
  `llm_generate_json(json_schema=GRADE_JSON_SCHEMA)` (identical to `fsm_logic.py:2110-2124`). It returns
  `grade ∈ 1..4`. Store the 1–4 grade in `exam_item_responses.grade`; derive
  `is_correct = 1 if grade >= 3 else 0` (Grade-3 = "Good" = correct, per the grader rubric).
  - **Grade-band calibration (spec 02 §4):** pass `grade_band` into the grading prompt so a correct
    terse K-2 answer earns ≥3 and is not failed for lack of explanation. (Requires adding the
    `grade_band` kwarg to `get_socratic_grading_prompt`, already planned in spec 02 §4.)

- **Item score normalization** for the attempt score (§6): objective → `is_correct` (0 or 1);
  free → `grade/4` mapped to `{1:0.0, 2:0.5, 3:0.85, 4:1.0}` (configurable), then thresholded to
  `is_correct` for pass-count purposes. Both the continuous fraction and `is_correct` are retained.

---

## 3. Interests-into-exams themer (B18.3) — the engagement feature

**Goal:** make an item *feel* personal (a ratio problem about soccer goals) **without changing the
assessed `standard_code`, the Bloom level, the answer, or the difficulty.** The themer rewrites only
**surface context** — names, setting, objects — never the mathematical/logical structure.

This mirrors how interests already feed analogies in the Socratic tutor (`prompts.py:281-283`: "They
are interested in: {interests}. Use these domains for analogies when possible.") — but for exams the
constraint is far stricter because the item is graded.

### 3.1 Two-step generate-then-theme flow

```
1. GENERATE  base_item ← llm_generate_json(generation_prompt, ITEM_SCHEMA)   # §2, interest-blind
2. (skip theming if students.interests empty OR item_type == 'ordering' with embedded labels risky)
3. THEME     themed_item ← llm_generate_json(theme_prompt(base_item, interest), ITEM_SCHEMA)
4. VALIDATE  ok, reason ← validity_guard(base_item, themed_item)             # §4
5. if ok:  store themed_item, exam_item_responses.theme_validated = 1, attempt.theme = interest
   else:   store base_item, theme_validated = 0   (fall back to un-themed; log reason)
```

The chosen interest = first of `students.interests` (JSON array, 01 §2) that has not been over-used in
this attempt (round-robin so one attempt isn't all soccer). `exam_attempts.theme` records the applied
theme for audit.

**Invariant carried across the theme step:** for objective items the *answer must not change* —
`answer_index` / `answer_value` / `ordering` and `choices` count are passed through and the themer is
forbidden from renumbering. The safest implementation passes the answer key **out of band** (the themer
only rewrites `prompt` and the *text* of `choices`, while the engine re-attaches the original
`answer_index`/`answer_value`/`ordering` positions). The validity guard (§4) then confirms semantics.

### 3.2 Theme prompt outline

```
SYSTEM: You are rewriting an assessment item to be about a student's interest, WITHOUT changing
  what it tests or its answer. You are a surface re-skinner, not an item writer.

  STUDENT INTEREST: {interest}                      # e.g. "soccer"
  STANDARD (must stay): {standard_code} — "{standard_text}"
  BLOOM (must stay): {bloom}
  DIFFICULTY (must stay): {difficulty}
  ORIGINAL ITEM (JSON): {base_item_json}

  RULES — DO change:
  - The scenario, names, objects, and setting so the item is about {interest}.
  RULES — DO NOT change:
  - The numbers, quantities, relationships, or operations required to solve it.
  - The correct answer, the number of choices, or which choice is correct.
  - The cognitive demand (still Bloom {bloom}) or the difficulty ({difficulty}).
  - The standard being tested ({standard_code}).
  - For mcq: keep the SAME number of choices; rewrite each choice's wording to fit the theme but
    keep the SAME option correct and keep distractors equally plausible.
  Output ONLY JSON matching the schema. Keep standard_code, bloom, difficulty, and the answer fields
  byte-identical to the original.
```

Example: base `"A recipe needs 3 cups flour to 2 cups sugar. What is the ratio?"` (6.RP.1, Bloom 2,
answer 3:2) → themed `"A striker scored 3 goals for every 2 assists. What is the ratio of goals to
assists?"` — same standard, same Bloom, same answer 3:2.

---

## 4. Validity guard (B18.4)

An **automated** check that the themed item still tests the **same `standard_code` at the same Bloom**
and has the **same answer and difficulty**. Sets `exam_item_responses.theme_validated`. Runs only when
theming was applied. Two layers — cheap structural checks first, then an LLM verifier — and the item is
accepted only if **both** pass.

### 4.1 Structural checks (deterministic, run first)

```python
def structural_ok(base, themed) -> tuple[bool, str]:
    if themed["standard_code"] != base["standard_code"]: return False, "standard drift"
    if themed["bloom"]        != base["bloom"]:          return False, "bloom drift"
    if themed["difficulty"]   != base["difficulty"]:     return False, "difficulty drift"
    if themed["item_type"]    != base["item_type"]:      return False, "type drift"
    t = themed["item_type"]
    if t == "mcq":
        if len(themed["choices"]) != len(base["choices"]): return False, "choice count changed"
        if themed["answer_index"] != base["answer_index"]: return False, "answer index moved"
    elif t == "numeric":
        if themed["answer_value"] != base["answer_value"]: return False, "numeric answer changed"
        if themed.get("tolerance", 0) != base.get("tolerance", 0): return False, "tolerance changed"
    elif t == "ordering":
        if len(themed["ordering"]) != len(base["ordering"]): return False, "step count changed"
    # numbers preserved: the multiset of numeric tokens in the prompt must match
    if _numeric_tokens(themed["prompt"]) != _numeric_tokens(base["prompt"]):
        return False, "quantities changed"
    return True, "ok"
```

`_numeric_tokens` extracts the multiset of numbers from the prompt; a re-skin that altered `3`→`4`
fails here without any LLM call. (For free items there is no answer key to compare; the structural pass
checks standard/bloom/difficulty/numeric-token equality only.)

### 4.2 Verifier LLM pass (semantic)

A second, cheap LLM call with a tight `VERIFY_SCHEMA` (`{same_standard: bool, same_bloom: bool,
same_answer: bool, same_difficulty: bool, reason: str}`):

```
SYSTEM: Compare two assessment items. Answer ONLY the JSON. Do not solve creatively — judge equivalence.
  ORIGINAL: {base_item_json}
  REWRITTEN: {themed_item_json}
  Does the rewritten item test the SAME standard ({standard_code}) at the SAME Bloom level,
  have the SAME correct answer, and the SAME difficulty? Set each boolean and give a one-line reason.
```

`theme_validated = 1` **iff** `structural_ok` is True AND all four verifier booleans are True.

### 4.3 On failure

Fall back to the **un-themed base item**: store `base_item`, set
`exam_item_responses.theme_validated = 0`, leave `exam_attempts.theme` unchanged for that item, and log
the failure `reason` (for tuning the themer). The student still gets a valid, correctly-difficult item —
engagement degrades gracefully, **assessment integrity never does.** Optionally retry theming once with
a different interest before falling back (config flag, default off in v1).

---

## 5. Progression gating (B18.2) — integrate with mastery, do not bypass

A `checkpoint`/`unit` exam gates the next unit. It **complements** the per-concept Socratic mastery
gate (`_check_mastery_gate`, `fsm_logic.py:1061`): a student first masters each concept Socratically,
then the checkpoint confirms standard-level retention across the unit before unlocking the next unit.

### 5.1 Pass → unlock

On `passed = 1` for a unit's checkpoint:
1. Mark the unit complete for the standards it asserts: upsert `user_progress`
   (`ON CONFLICT(student_id, concept_uid)`, 01 §2.2) for each concept in the unit not already at the
   mastery status — set/confirm `status='mastered'` for concepts whose standards were all passed.
2. Advance the enrollment: `UPDATE enrollments SET current_concept_uid = <first concept of next unit>`
   for `(student_id, course_uid)`. The next unit's concepts become reachable in the path view (the
   web-ui `course_structure` lock computation reads `enrollments.current_concept_uid` + `user_progress`).
3. Emit XP / badges (spec: `xp_ledger.reason='exam_pass'`, 01 §6) and a notification if configured.

### 5.2 Fail → Socratic remediation (not a wall)

On `passed = 0`:
1. Compute the **missed standards**: standards whose per-standard subscore (§6.2) is below threshold.
2. Do **not** unlock the next unit. Route the student back into Socratic teaching on exactly the missed
   standards: for each missed `standard_code`, find its concepts via `concept_standards` and reset those
   concepts' mastery so the FSM re-teaches them. The FSM enters `SOCRATIC_LEARNING` on the first such
   concept (reuses existing `NAVIGATE_TO_TOPIC` machinery — gating produces a targeted remediation queue,
   it does not invent a new teaching path).
3. After remediation, the student retakes the checkpoint with **freshly generated items** (§9).

This is the key integration rule: **the checkpoint never bypasses the mastery engine — a fail feeds the
mastery engine more work; a pass records mastery the engine already implied.**

### 5.3 Diagnostic placement (non-gating)

A `diagnostic` exam never blocks. Its per-standard subscores set the recommended **entry Bloom floor /
start unit** (mirrors the legacy `enter_pre_assessment` / `_compute_module_depths` intent at
`fsm_logic.py:2805`, but standards-driven). Result writes a suggested `current_concept_uid` to the
enrollment; the student can always start earlier.

---

## 6. Scoring & thresholds

### 6.1 Per-attempt score

```
attempt.score = Σ item_fraction / N_items          # item_fraction ∈ [0,1] per §2.4
attempt.passed = 1 if attempt.score >= exam.pass_threshold else 0
```

`pass_threshold` is stored on the exam (01 §5; default `0.80`). For `standardized_prep`:
- **GFL** (General Financial Literacy end-of-course): `pass_threshold = 0.74` (74% cut score,
  `UTAH_K12_CURRICULUM_REFERENCE.md` §GFL). Passing the *course* is the graduation requirement, not
  passing the exam — so GFL prep is **non-gating**, scored-to-cut practice.
- **Basic Civics**: `pass_threshold = 0.70`, surfaced as **35 / 50** (`round(score*50)` shown as
  `X / 50`, pass at ≥35). Blueprint has 50 mcq slots across the civics strands.

The UI shows raw fraction, the cut line, and pass/fail. `standardized_prep` reports are practice-mode
(repeatable, never gate).

### 6.2 Per-standard subscores

For each distinct `standard_code` across the attempt's items:

```
subscore[code] = Σ item_fraction (items with that code) / count(items with that code)
passed_standard[code] = subscore[code] >= exam.pass_threshold   # or a per-standard threshold override
```

Subscores are computed at grade time from `exam_item_responses` (group by `standard_code`) and returned
in the grade response. They are the unit of the **parent standards-coverage report (spec 06)**: spec 06
aggregates `passed_standard` across a student's attempts to render which Utah standards are covered/
mastered. The "missed standards" set in §5.2 is exactly `{code : not passed_standard[code]}`.

---

## 7. Accommodations (spec 01 §7)

At **attempt start**, snapshot the student's `accommodations` row into
`exam_attempts.accommodations` (JSON) so a mid-stream change to the IEP/504 row never alters an
in-flight attempt and the record is auditable. Honored fields:

| Accommodation (01 §7 column) | Effect on the attempt |
|---|---|
| `extended_time` | Timer multiplier 1.5× (if exam is timed). |
| `no_timer` | Disable the attempt timer entirely. |
| `extra_scaffolding` | Allow one optional hint per free item (hint does not change grading); show fewer items per screen. Objective answers unaffected. |
| `simplified_language` | Generation **and** theming prompts get a "use simplified, ELL-friendly language; keep the same standard, Bloom, numbers, and answer" directive. Difficulty/standard unchanged — validity guard (§4) still enforced. |
| `read_aloud_default` | Item prompts auto-played via Kokoro TTS. |
| `reduced_distraction` | Minimal exam UI, no gamification flourish. |

Snapshot shape (example):
`exam_attempts.accommodations = {"extended_time":1,"no_timer":0,"extra_scaffolding":1,"simplified_language":0,"read_aloud_default":1,"reduced_distraction":0}`.

`simplified_language` composes with theming: simplify first or theme first is fine as long as the
validity guard runs last on the final rendered item.

---

## 8. Endpoints & attempt state machine

All under the exam blueprint (`:5002`), proxied by web-ui at `/api/exam/*`. Every endpoint is
student-scoped (`student_id` from session per 01 §8; server-to-server calls from the FSM pass it
explicitly).

### 8.1 `exam_attempts.status` state machine

```
        start_attempt                 submit_attempt              (auto, sync)
 (none) ───────────▶ in_progress ───────────────▶ submitted ───────────────▶ graded
                         │                                                      │
                         │ abandon / TTL expiry                                 │ (terminal: score, passed,
                         ▼                                                      ▼  per-standard subscores)
                     abandoned                                              graded
```

- `in_progress`: items generated lazily as the student requests them; responses accumulate.
- `submitted`: all responses captured; grading not yet finalized (objective grades may already be set).
- `graded`: `score`, `passed`, subscores written; gating side-effects (§5) fired. Terminal.
- `abandoned`: closed without submit (explicit abandon or TTL); does not count toward attempt limits if
  zero items answered (anti-frustration), else counts (anti-gaming).

### 8.2 Endpoints (request / response shapes)

**`POST /api/exam/attempt/start`** — begin an attempt.
```jsonc
// req
{ "exam_id": "exm_ab12cd34", "course_uid": "course_…" }
// res 200
{ "attempt_id": "att_…", "status": "in_progress", "n_items": 7,
  "accommodations": { "extended_time": 1, "no_timer": 0, "...": 0 },
  "timer_seconds": null,            // null when no_timer or untimed
  "theme": "soccer" }               // chosen interest, or null
```
Side effects: creates `exam_attempts` row (`in_progress`), snapshots accommodations, enforces attempt
limits (§9). 409 if an `in_progress` attempt already exists for `(student_id, exam_id)` (returns it).

**`GET /api/exam/attempt/{attempt_id}/next`** — fetch the next un-answered item (lazy generation).
```jsonc
// res 200 — answer keys are NEVER included
{ "item_index": 2, "n_items": 7, "item": {
    "response_id": "…",            // exam_item_responses.id placeholder for this slot
    "item_type": "mcq",
    "prompt": "A striker scored 3 goals for every 2 assists. What is the ratio of goals to assists?",
    "choices": ["3:2", "2:3", "5:1", "1:5"],       // mcq only; order is server-randomized
    "standard_code": "6.RP.1", "bloom": 2,
    "themed": true, "theme_validated": true } }
// res 200 when done
{ "done": true }
```
Server generates → themes (§3) → validates (§4), then **persists the item with its answer key** into
`exam_item_responses` and returns the **answer-stripped** view (§9 anti-leakage).

**`POST /api/exam/attempt/{attempt_id}/response`** — submit one item's answer.
```jsonc
// req (one of):
{ "response_id": "…", "response_index": 0 }                 // mcq
{ "response_id": "…", "response_value": 1.5, "unit": "cups" }// numeric
{ "response_id": "…", "response_order": ["a","c","b"] }     // ordering
{ "response_id": "…", "response_text": "Because the ratio…" }// free
// res 200 — NO correctness leaked during in_progress (anti-gaming)
{ "recorded": true, "answered": 3, "n_items": 7 }
```
Objective items are graded server-side immediately and stored, but `is_correct` is **not returned**
until the attempt is `graded`. Free items are queued for grading at submit.

**`POST /api/exam/attempt/{attempt_id}/submit`** — finalize, grade, fire gating.
```jsonc
// req
{ }
// res 200
{ "attempt_id": "att_…", "status": "graded",
  "score": 0.86, "passed": true, "pass_threshold": 0.80,
  "display": { "raw": "6/7", "civics": null },               // civics shows "X / 50"
  "per_standard": { "6.RP.1": {"subscore": 1.0, "passed": true},
                    "6.RP.3": {"subscore": 0.75, "passed": false} },
  "missed_standards": ["6.RP.3"],
  "gating": { "kind": "checkpoint", "unlocked_next_unit": false,
              "remediation_concepts": ["con_…","con_…"] },
  "theme": "soccer" }
```
Grades all free items (Socratic grader), computes `score`/`passed`/subscores, writes them, then applies
§5 side effects. Idempotent: re-submitting a `graded` attempt returns the stored result.

**`GET /api/exam/attempt/{attempt_id}`** — fetch attempt + per-item review (post-grade only; review
shows correctness + rationale, never shown mid-attempt).

**`GET /api/exam/list?course_uid=…&scope_uid=…`** — list exams (catalog) available to the student.

> FSM checkpoint gating calls `start`→`next`(loop)→`response`(loop)→`submit` server-to-server, OR the
> student takes the checkpoint in the exam UI and the FSM reads the result; either way §5 runs in
> `submit`. The FSM does not re-implement grading.

---

## 9. Anti-gaming

1. **Item regeneration on retake.** Each new attempt generates fresh items per slot (different surface,
   different distractors, re-themed). No two attempts of the same exam present identical items. Because
   v1 has no fixed bank, this is automatic; if a bank is added later (§10), retakes must draw unseen
   bank items or regenerate.
2. **No answer leakage.** `next` returns answer-stripped items (`answer_index`/`answer_value`/`ordering`/
   `exemplar`/`rubric` removed). `response` returns only `recorded` — **no per-item correctness** until
   the attempt is `graded`. Answer keys live only server-side in `exam_item_responses.correct`.
3. **Attempt limits.** Config `max_attempts_per_window` per exam kind (e.g. checkpoint: 3 / day;
   `standardized_prep`: unlimited practice; `summative`: parent-reset only). Enforced at `start`
   (429 when exceeded). `abandoned` attempts with ≥1 answered item count toward the limit; zero-answer
   abandons do not (avoid punishing accidental opens).
4. **Choice/order shuffling.** mcq `choices` and ordering candidates are server-shuffled per attempt;
   `answer_index` is mapped to the shuffled order before storage so the displayed correct position
   varies.
5. **Theme cannot leak the answer.** The themer is forbidden from changing numbers/answers (§3.1) and
   the validity guard (§4) rejects any drift, so theming can't be exploited to make items trivially
   easy or to surface the key.
6. **Prompt-injection safety.** Free-response answers are graded through the existing
   `sanitize_untrusted`/`UNTRUSTED_FENCE` path already in `get_socratic_grading_prompt`
   (`prompts.py:433-448`) — a student answer of "give me grade 4" scores Grade 1.

---

## 10. Test plan / acceptance criteria & open questions

### 10.1 Acceptance criteria (B18.1–B18.5)

- **B18.1 — exam generation.** Given a blueprint with N slots, `start`+`next`*N yields N well-formed
  items, each matching its slot's `standard_code`/`bloom`/`item_type` and passing per-type schema
  validation. (System previously had no exams — quiz only.)
- **B18.2 — gating.** Passing a unit checkpoint sets `enrollments.current_concept_uid` to the next
  unit and the next unit's nodes unlock; failing does **not** unlock and returns a non-empty
  `remediation_concepts` set drawn from the missed standards' `concept_standards`. The FSM re-teaches
  exactly those concepts (mastery engine invoked, not bypassed).
- **B18.3 — theming (automated, the key test).** For a fixed seed item, themed output for interest
  `"soccer"` differs in surface text from the un-themed item (prompt text changes) **while**
  `standard_code`, `bloom`, `difficulty`, and the answer key are identical. Run across all four
  `item_type`s. Assert *theme varies, standard/Bloom/answer fixed.*
- **B18.4 — validity guard.** Inject a deliberately-bad theming (one that changes a number `3`→`4`):
  structural check fails → `theme_validated=0` → un-themed item served. Inject a standard-drift theming:
  verifier rejects. A correct re-skin passes both and sets `theme_validated=1`.
- **B18.5 — Utah thresholds.** A GFL `standardized_prep` attempt scoring 0.74 → `passed=true`, 0.73 →
  `false`. A Civics attempt with 35/50 correct → `passed=true` and `display.civics == "35 / 50"`; 34/50
  → `false`. Both are non-gating (no enrollment change).
- **Grading parity.** Free-item grading produces the same 1–4 grade as the standalone Socratic grader
  for identical inputs (calls the same prompt + `GRADE_JSON_SCHEMA`). Objective grading is exact and
  LLM-free. K-2 calibration: a correct one-word free answer grades ≥3 (regression mirror of spec 02 §8).
- **Accommodations.** `extended_time` snapshot is honored for the whole attempt even if the
  `accommodations` row is edited mid-attempt; `simplified_language` items still pass the validity guard.
- **Anti-gaming.** Two attempts of the same exam present non-identical items; `response` never leaks
  correctness pre-grade; attempt limit returns 429.

### 10.2 Open questions

- **Item bank vs per-attempt (v1 = per-attempt).** Per-attempt gives free theming + fresh retakes but
  makes exams non-reproducible and spends LLM calls at attempt time. A persisted, reviewable, validated
  item bank (additive table per 01 §5 note) would enable parent/teacher item review, faster attempts,
  and psychometric stats — decide before scale-out (R4). The schema (`ITEM_SCHEMA`) is already
  bank-ready.
- **Per-standard threshold overrides** vs a single exam-wide `pass_threshold` for the missed-standards
  computation (§6.2) — v1 reuses the exam threshold; standardized prep may want per-strand cuts.
- **Ordering partial credit** (Kendall-tau) vs exact — v1 exact.
- **GFL/Civics blueprint fidelity** — how closely the practice blueprints should mirror the official
  YouScience/Basic Civics strand weighting (we have strand names, not the official item counts).
- **Diagnostic depth** — how many items a placement diagnostic should ask before it confidently sets an
  entry Bloom floor (balance placement accuracy vs cold-start friction).
