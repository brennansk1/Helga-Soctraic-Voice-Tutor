# Design Spec 07 — Gamification 2.0 (server-authoritative, per-student, COPPA-safe)

> Implementation-ready spec for branch **B22** (B22.1–B22.6) and frontend **FE8** (skill-tree map).
> Canonical table/column names come from `docs/design/01_DATA_MODEL.md §6`
> (`student_gamification`, `xp_ledger`, `badges`, `student_badges`, `quests`, `student_quests`,
> `cosmetics` JSON). Grade-appropriate framing follows `docs/design/02_GRADE_ADAPTATION.md`.
> This spec **replaces** the global key-value gamification currently in
> `services/rag/librarian.py:1637-1939` (`gamification`/`achievements`/`user_profile` K-V,
> `_LEVEL_THRESHOLDS`, `award_xp`, `check_streak`) with per-student tables and a
> **server-authoritative** award path.
>
> **Non-negotiable invariant: XP is never trusted from the client.** Awards fire only inside
> services from real, verified events (a graded answer, a recorded completion, a passed exam,
> a quest target reached). There is no client-callable "give me XP" endpoint.

---

## 0. Where we are today (baseline to migrate from)

| Concern | Baseline (librarian.py) | Target (this spec) |
|---|---|---|
| Storage | global K-V `gamification` table + `achievements` table in `helga.db` | per-student `student_gamification`, `xp_ledger`, `badges`/`student_badges`, `quests`/`student_quests` (spec 01 §6, migration **v7**) |
| Identity | none — one global user | `student_id` (`stu_…`) on every row via `current_student_id()` (spec 01 §1, §8) |
| Award trigger | `POST /api/gamification/award_xp` proxied through web-ui (`app.py:503`) — **client-reachable** | server-internal `GamificationStore.award()` called from FSM / RAG / exam grader; the public award route is **removed** |
| Award caller (real) | FSM fire-and-forget HTTP to RAG on `grade>=3` (`fsm_logic.py:2310-2332`) | FSM calls in-process `GamificationStore.award(...)` (RAG) / direct store call; no HTTP self-trust |
| Levels | `_LEVEL_THRESHOLDS` hardcoded list (`librarian.py:1712`) | closed-form curve `level_from_xp()` (this spec §2); thresholds list retained as a cached lookup |
| "Badges" | 13 hardcoded `achievements` rows | catalog `badges` with a criteria DSL (this spec §3); the 13 legacy ones map 1:1 |
| Skill tree | none | FE8 tree from `standards ⟕ concept_standards ⟕ user_progress` (this spec §4) |
| Quests | none | `quests`/`student_quests` with `period_key` reset (this spec §5) |
| Cosmetics | none | `student_gamification.cosmetics` JSON, interest+level gated (this spec §6) |
| Framing/toggle | `gamification_enabled` profile flag + `data-gamification` attr + level-up toast (`base.html`) | grade-banded framing + `settings.gamification_enabled` + `accommodations.reduced_distraction` (this spec §7) |
| Safety | n/a | no open leaderboards; within-family + anonymized percentile only (this spec §8) |

Migrate legacy data in **v7** per spec 01 §1 backfill: move the global `gamification` K-V row under
`stu_legacy0`, seed `student_gamification` from it, and replay nothing (the `xp_ledger` starts at the
backfilled `total_xp` with a single synthetic `reason='migration'` row so the ledger sum reconciles).

---

## 1. Architecture & module placement

```
event happens (real, verified)
   │
   ├─ FSM (services/core/fsm_logic.py)         answer graded, concept/module completed
   ├─ RAG (services/rag/librarian.py)          spaced review recorded
   └─ Exam grader (services/.../exams)         exam attempt passed
        │
        ▼  in-process call (no HTTP self-trust)
   GamificationStore.award(student_id, reason, ref_uid, ctx)   ← THE only XP entry point
        │   1. compute amount from XP economy table (§1.2)  ── pure function, server-side
        │   2. INSERT xp_ledger (audit/anti-cheat)
        │   3. UPDATE student_gamification (total_xp, level, daily_xp, streak)
        │   4. evaluate_badges(student_id, trigger)          (§3.4)
        │   5. advance_quests(student_id, reason, ctx)       (§5.3)
        │   6. return AwardResult {xp_earned, total_xp, level, level_up, new_badges, quest_updates, cosmetic_unlocks}
        ▼
   surfaced to UI via the existing status/state channels (toast, header badges, skill-tree refresh)
```

- **New sub-store:** `GamificationStore` in `services/common/storage.py` (spec 01 §8), thread-local DB,
  `'student_id'` added to its `_VALID_COLUMNS`. All methods take a **leading `student_id`**.
- **RAG** (`librarian.py`) keeps the **read** endpoints (`GET /api/gamification`, skill tree, quests) and
  calls `GamificationStore` for review awards. **FSM** calls `GamificationStore` (via the RAG service's
  in-process store or a thin internal RPC — same process boundary as today, but **not** the public route).
- The **public** `/api/gamification/award_xp` route and its web-ui proxy (`app.py:503`) are **deleted**.
  Any award now happens behind the event that justifies it.

### 1.2 Server-authoritative guarantee (B22 / requirement #9)

1. `GamificationStore.award()` is **never** wired to an HTTP route reachable by a browser.
2. Every caller passes a `ref_uid` (concept/module/exam/quest/review id) and the store **verifies the
   underlying fact** before awarding:
   - `answer`: caller is the grader; grade ≥ 3 is checked in-store (re-derive, don't trust a passed flag).
   - `complete_concept`/`complete_module`: store checks `user_progress`/structure that the unit is actually
     marked complete for this `student_id` (idempotency: refuse a second award for the same `ref_uid`).
   - `exam_pass`: store checks `exam_attempts.passed=1` for `(student_id, ref_uid)`.
   - `review`: store checks a `scheduled_reviews`/`flashcards` row was graded this call.
3. **Idempotency / anti-cheat:** before inserting, the store checks `xp_ledger` for an existing row with the
   same `(student_id, reason, ref_uid)` for completion-class reasons (concept/module/exam/quest). Duplicate →
   `xp_earned=0`, no ledger row. `answer`/`review` are repeatable but rate-limited (§1.4).
4. `xp_ledger` is the source of truth for audit; `student_gamification.total_xp` must always equal
   `SUM(xp_ledger.amount)` for that student (a reconciliation test asserts this — §10).

### 1.3 Daily XP cap & anti-grind (anti-cheat)

- **Daily XP cap** per student: `2000` XP/day (`daily_xp`/`daily_date` on `student_gamification`). Awards past
  the cap still write a `0`-amount ledger note (`reason` suffixed `:capped`) for analytics; `total_xp` unchanged.
- **Per-concept answer XP cap:** the `answer` reason awards at most `answer_base × 4` per concept per day
  (prevents farming one easy concept). Tracked by counting `xp_ledger` `answer` rows with that `ref_uid` today.
- These caps are **muted in framing for young kids** (we never show "you hit your cap" to K-2 — §7).

---

## 2. XP economy (B22.1) — exact award table

All values are **server constants** in one module (`GAMIFICATION_ECONOMY` next to `GRADE_BAND_PROFILES`).
Base values **reconcile with the existing** `award_xp` (`librarian.py:1836`: answer 10, complete_concept 25,
complete_module 100, review 15) and extend them with `exam_pass` and `quest_complete`.

### 2.1 Base award table

| `reason` | Trigger (verified server-side) | Base XP | Notes |
|---|---|---|---|
| `answer` | Socratic answer graded **≥ 3** | **10** | grade < 3 → 0 (no ledger row). Matches baseline. |
| `complete_concept` | concept marked mastered in `user_progress` | **25** | once per concept (idempotent). Matches baseline. |
| `complete_module` | all concepts in a module complete | **100** | once per module (idempotent). Matches baseline. |
| `review` | spaced-repetition card graded | **15** | repeatable; counts toward daily cap. Matches baseline. |
| `exam_pass` | `exam_attempts.passed=1` | **150** | once per `exam_id` per pass; checkpoint/unit/summative. **New.** |
| `quest_complete` | `student_quests.status→completed` | per-quest `xp_reward` | comes from `quests.xp_reward` (§5). **New.** |
| `badge` (internal) | a badge unlock | `badges.xp_reward` | bonus XP attached to badge unlock (§3); ledger `reason='badge'`. |

### 2.2 Multipliers (apply to `answer`, `complete_concept`, `exam_pass`)

Reconciles with baseline (`first_try ×1.5`, `bloom_level≥4 ×2.0`) and adds a high-bloom tier:

| Multiplier | Condition | Factor |
|---|---|---|
| First-try | `socratic_retry_count == 0` (answer) / first attempt (exam) | **×1.5** |
| High-bloom | `bloom_level ∈ {4,5}` | **×2.0** |
| Top-bloom | `bloom_level == 6` (9-12 ceiling) | **×2.5** |

Multipliers **stack multiplicatively**, then `floor()`. Example: first-try Bloom-5 answer = `10 × 1.5 × 2.0 = 30`.
`review` and `complete_module` take **no** multipliers (flat). `quest_complete`/`badge` use their own fixed reward.

> **Grade-band note (not a multiplier):** XP base does **not** scale by grade band — a K-2 concept and a 9-12
> concept are each worth `25`. Difficulty is already expressed through the Bloom multiplier, which a 9-12 student
> reaches more often. This keeps the economy fair and prevents "older = inherently more points."

### 2.3 Pure function (server)

```python
def compute_xp(reason, *, grade=None, bloom_level=1, first_try=False, quest_reward=0, badge_reward=0):
    if reason == "answer":
        if grade is None or grade < 3:
            return 0
        base = 10
    elif reason == "complete_concept": base = 25
    elif reason == "complete_module":  return 100          # flat, no multipliers
    elif reason == "review":           return 15           # flat
    elif reason == "exam_pass":        base = 150
    elif reason == "quest_complete":   return quest_reward  # from quests.xp_reward
    elif reason == "badge":            return badge_reward
    else: return 0
    mult = 1.0
    if first_try:                 mult *= 1.5
    if bloom_level in (4, 5):     mult *= 2.0
    elif bloom_level >= 6:        mult *= 2.5
    return int(base * mult)
```

Every non-zero result becomes **one** `xp_ledger` row `(student_id, amount, reason, ref_uid, created_at)` and a
`student_gamification` update inside a single transaction.

---

## 3. Level curve (B22.1)

### 3.1 Formula (concrete, rising thresholds)

Closed-form so it scales past the baseline's 12-entry list. **Cumulative XP to reach level `L`:**

```
threshold(L) = 50 * (L-1) * L         # = 50·L(L-1);  threshold(1)=0
level_from_xp(xp) = floor((1 + sqrt(1 + xp/12.5)) / 2)   # inverse, clamped to ≥1
```

`threshold(L)` is the **incremental quadratic** (each level costs `100·(L-1)` more than the last):

| Level | XP to reach | Δ from prev |
|---|---|---|
| 1 | 0 | — |
| 2 | 100 | 100 |
| 3 | 300 | 200 |
| 4 | 600 | 300 |
| 5 | 1000 | 400 |
| 6 | 1500 | 500 |
| 7 | 2100 | 600 |
| 8 | 2800 | 700 |
| 10 | 4500 | … |
| 15 | 10500 | … |
| 20 | 19000 | … |

> **Reconciliation:** levels 1–6 match `_LEVEL_THRESHOLDS` exactly (0,100,300,600,1000,1500). Level 7+ diverges
> slightly (baseline 2200 → formula 2100) but the curve is now infinite and monotonic, removing the baseline's
> `[-1]+2000` fudge at `librarian.py:1805`. Cache the first ~40 thresholds in a list for `next_level_xp`/
> `prev_level_xp` display; compute beyond that with the closed form.

`level` is **recomputed from `total_xp` on every award** and stored (denormalized) on `student_gamification.level`
so headers/queries don't re-derive it. `award()` returns `level_up = new_level > old_level`.

### 3.2 What leveling unlocks

Leveling is the **gate for cosmetics** (§6) and some badges (§3). Each `level_from_xp` increase triggers:
1. Level-up toast (existing `window.showLevelUpToast`, `base.html:334`) — framed per band (§7).
2. `unlock_cosmetics_for_level(student_id, new_level)` — adds any level-gated cosmetics whose interest tags the
   student has selected (§6). Returns `cosmetic_unlocks` in `AwardResult`.
3. Possible `level_*` badge unlock (e.g. `level_10` "Seasoned Learner").

Level itself confers **no power** (no XP multipliers from level) — purely status + cosmetics, to avoid a
rich-get-richer loop.

---

## 4. Badges (B22.3) — catalog + criteria DSL

### 4.1 `badges` catalog (global, spec 01 §6)

`badges(id, name, description, icon, criteria, xp_reward, scope)`. `scope ∈ {standard, strand, streak, special}`
(spec 01). `criteria` is a **JSON DSL** (one object) evaluated by a small server-side interpreter — declarative so
new badges are data, not code.

### 4.2 Criteria DSL

```json
{ "type": "<enum>", "params": { … } }
```

| `type` | Params | Fires when |
|---|---|---|
| `standard_mastery` | `{standard_code}` | all concepts mapped to that standard are mastered for the student |
| `strand_mastery` | `{subject, strand}` | all standards in the strand are mastered |
| `strand_progress` | `{subject, strand, pct}` | ≥ `pct` of strand standards mastered (e.g. 0.5 "halfway") |
| `concept_count` | `{n}` | total mastered concepts ≥ `n` |
| `streak_days` | `{n}` | `student_gamification.streak_days` ≥ `n` |
| `bloom_reached` | `{level}` | student answered ≥1 correct at `bloom_level ≥ level` |
| `exam_pass` | `{kind}` or `{exam_id}` | a matching exam attempt passed |
| `perfect_concept` | `{}` | concept completed with a grade-4 answer (legacy `perfect_concept`) |
| `review_count` | `{n}` | ≥ `n` spaced reviews graded |
| `first_event` | `{event}` | first `answer`/`course`/`concept` (legacy `first_answer`/`first_course`) |
| `quest_count` | `{n}` | ≥ `n` quests completed |

`scope` is metadata for grouping in the UI; `type` drives evaluation. `xp_reward` is awarded via `award(reason='badge')`.

### 4.3 ~12 example badge definitions

| id | name | scope | criteria | xp |
|---|---|---|---|---|
| `bdg_first_answer` | Curious Mind | special | `{"type":"first_event","params":{"event":"answer"}}` | 25 |
| `bdg_perfect_concept` | Ace | special | `{"type":"perfect_concept","params":{}}` | 75 |
| `bdg_streak_3` | On a Roll | streak | `{"type":"streak_days","params":{"n":3}}` | 100 |
| `bdg_streak_7` | Week Warrior | streak | `{"type":"streak_days","params":{"n":7}}` | 200 |
| `bdg_streak_30` | Monthly Master | streak | `{"type":"streak_days","params":{"n":30}}` | 500 |
| `bdg_concepts_10` | Explorer | special | `{"type":"concept_count","params":{"n":10}}` | 150 |
| `bdg_concepts_50` | Scholar | special | `{"type":"concept_count","params":{"n":50}}` | 300 |
| `bdg_std_6rp` | Ratio Wrangler | standard | `{"type":"standard_mastery","params":{"standard_code":"6.RP"}}` | 120 |
| `bdg_strand_geometry` | Shape Shifter | strand | `{"type":"strand_mastery","params":{"subject":"math","strand":"Geometry"}}` | 250 |
| `bdg_strand_half_bio` | Half-Way Biologist | strand | `{"type":"strand_progress","params":{"subject":"science","strand":"Biology","pct":0.5}}` | 100 |
| `bdg_bloom_5` | Evaluator | special | `{"type":"bloom_reached","params":{"level":5}}` | 250 |
| `bdg_exam_summative` | Final Boss | special | `{"type":"exam_pass","params":{"kind":"summative"}}` | 300 |

(Legacy 13 achievements map onto these; e.g. `streak_3`→`bdg_streak_3`, `bloom_5`→`bdg_bloom_5`.)

### 4.4 Evaluation hook — when & where

`evaluate_badges(student_id, trigger)` runs **inside `award()`** (step 4) so badge checks ride on the same verified
events that grant XP — never on a client poll:

- **trigger = `answer`** → evaluate `first_event`, `bloom_reached`, `perfect_concept`.
- **trigger = `complete_concept`** → evaluate `standard_mastery`, `strand_progress`, `strand_mastery`,
  `concept_count` (this is the heavy one; query `concept_standards ⟕ user_progress` scoped to the standards the
  just-completed concept touches, so we only re-check affected strands, not the whole catalog).
- **trigger = `exam_pass`** → evaluate `exam_pass`.
- **trigger = `review`** → evaluate `review_count`.
- **trigger = streak update** (in `check_streak`) → evaluate `streak_days`.
- **trigger = `quest_complete`** → evaluate `quest_count`.

For each unlocked-but-not-yet-owned badge: `INSERT OR IGNORE INTO student_badges(student_id, badge_id)` (PK
`(student_id, badge_id)` makes it idempotent), then `award(reason='badge', badge_reward=...)` for its XP. Returns
the newly-unlocked badges in `AwardResult.new_badges` for the toast. Evaluation is **bounded**: only candidate
badges whose `type` matches the trigger are scanned, and `standard_mastery`/`strand_*` are filtered to the
standards touched by the triggering concept.

---

## 5. Skill-tree map (B22.2 / FE8) — gamified Learn surface

The skill tree is the visual catalog: **subjects → strands (branches) → standards (masterable nodes)**. It is the
gamified replacement/companion for the path view in `learn.html` (FE5.2 keeps `learn.html`, adds the tree).

### 5.1 Node model & states

A **node = a standard** (`standards.code`). A node's state is derived from the student's progress on the concepts
mapped to that standard (`concept_standards.concept_uid` ⟕ `user_progress` for `student_id`):

| State | Rule |
|---|---|
| `mastered` | every concept mapped to the standard is mastered for this student |
| `in_progress` | ≥1 mapped concept started but not all mastered |
| `available` | no mapped concept started, **and** all prerequisite standards are `mastered` |
| `locked` | a prerequisite standard is not yet `mastered` |

Prerequisites: in v1, ordering within a strand is **sequential by `grade_numeric` then standard code**
(strand standards form a chain); a later `prerequisites` column on `standards` can override. Enrichment standards
(`standards.is_enrichment=1`) render as **optional ★ side-nodes**, never block downstream.

### 5.2 Data contract (frontend consumes)

`GET /api/skill_tree?student_id=<implicit current>&subject=math` →

```json
{
  "student_id": "stu_…",
  "subject": "math",
  "grade_band": "6-8",
  "branches": [
    {
      "strand": "Ratios & Proportional Relationships",
      "mastered": 1, "total": 3,
      "nodes": [
        { "standard_code": "6.RP.1", "label": "Understand ratios",
          "state": "mastered", "is_enrichment": false,
          "concepts": [ {"uid":"con_ab12cd34","title":"What is a ratio?","mastered":true} ],
          "mastered_concepts": 2, "total_concepts": 2,
          "badge_id": null, "prereqs": [] },
        { "standard_code": "6.RP.2", "label": "Unit rate",
          "state": "available", "is_enrichment": false,
          "mastered_concepts": 0, "total_concepts": 3,
          "badge_id": "bdg_std_6rp", "prereqs": ["6.RP.1"] },
        { "standard_code": "6.RP.3", "label": "Solve rate problems",
          "state": "locked", "prereqs": ["6.RP.2"], "total_concepts": 4 }
      ]
    }
  ],
  "summary": { "nodes_total": 24, "nodes_mastered": 7, "pct": 0.29 }
}
```

- Clicking an `available`/`in_progress` node → existing learn flow: pick the next unmastered concept and
  `NAVIGATE_TO_TOPIC` (reuses current FSM path). `locked` nodes are non-interactive with a tooltip naming the
  prereq. `mastered` nodes show the earned badge if `badge_id` is unlocked.
- Computation is **read-only** and cacheable per `(student_id, subject)`; invalidate on any `complete_concept`
  award. One query joins `standards`, `concept_standards`, and `user_progress`; avoid N+1 by fetching all mapped
  concepts for the subject in a single `IN (...)`.
- Only **published catalog** standards/concepts appear (spec 01 §4.1 visibility): a student sees the tree for
  their enrolled catalog subjects at their band.

### 5.3 Rendering (FE8)

SVG/CSS tree, strands as horizontal branches, standards as nodes colored by state
(`locked` grey, `available` outlined, `in_progress` partial-fill ring = `mastered_concepts/total_concepts`,
`mastered` filled + check + badge). Enrichment nodes are smaller ★ off-branch. Honors `data-gamification="off"`
(renders as a plain progress list) and `reduced_distraction` (no animation; §7).

---

## 6. Quests / daily challenges (B22.3)

### 6.1 Definitions (`quests`, global, spec 01 §6)

`quests(id, title, kind, target, xp_reward, cadence)`. `cadence ∈ {daily, weekly}`. `kind` is the progress
counter the quest tracks:

| `kind` | Counts | Advanced on award reason |
|---|---|---|
| `answer_correct` | correct answers | `answer` |
| `complete_concept` | concepts mastered | `complete_concept` |
| `review` | spaced reviews done | `review` |
| `bloom_high` | answers at bloom ≥ 4 | `answer` (when `bloom_level≥4`) |
| `session_minutes` | active minutes | session heartbeat (RAG) |
| `exam_pass` | exams passed | `exam_pass` |

### 6.2 Example quests

| id | title | kind | target | xp | cadence |
|---|---|---|---|---|---|
| `qst_daily_3concepts` | Daily Three | `complete_concept` | 3 | 60 | daily |
| `qst_daily_10answers` | Ten Good Answers | `answer_correct` | 10 | 50 | daily |
| `qst_daily_review5` | Clear the Deck | `review` | 5 | 40 | daily |
| `qst_weekly_deep` | Deep Thinker | `bloom_high` | 15 | 200 | weekly |
| `qst_weekly_explorer` | Strand Explorer | `complete_concept` | 20 | 250 | weekly |

### 6.3 `student_quests` + period reset

`student_quests(student_id, quest_id, progress, status, period_key)`, PK `(student_id, quest_id, period_key)`.
`period_key` is the reset bucket: **daily** = `YYYY-MM-DD` (student-local date), **weekly** =
`YYYY-Www` (ISO week). Assigning a quest for the current period is **lazy**: on `GET /api/quests` or on the first
matching award of the period, `INSERT OR IGNORE` a row with `progress=0, status='active', period_key=<current>`.
Old periods stay as history (claimed/expired); they are never resurfaced.

### 6.4 Progress tracking hook

`advance_quests(student_id, reason, ctx)` runs inside `award()` (step 5):
1. Find active `student_quests` for the **current period** whose `quests.kind` matches `reason` (+ `ctx` filters
   like `bloom_level≥4`).
2. `progress += 1` (or += minutes for `session_minutes`), clamp to `target`.
3. If `progress >= target` and `status='active'` → set `status='completed'`, then `award(reason='quest_complete',
   ref_uid=quest_id, quest_reward=quests.xp_reward)`. The quest's XP itself goes through the ledger.
4. Claiming: completed quests are auto-credited (no manual claim needed for XP), but `POST /api/quests/claim`
   exists to let the UI acknowledge/animate and mark `status='claimed'` (cosmetic-only state). No XP is granted by
   the claim route — XP already happened server-side at completion (server-authoritative, requirement #9).

Quest expiry: at period rollover, unfinished `active` rows are left as-is (a nightly job or lazy read marks them
`expired`); they do not carry over.

---

## 7. Interest-themed cosmetic rewards (B22.4)

### 7.1 Model

Cosmetics are unlockable, non-functional avatar/theme items, **stored on the student** in
`student_gamification.cosmetics` (JSON), not as separate rows (catalog is small, per-student state is tiny):

```json
{
  "unlocked": ["avatar_rocket", "frame_space", "theme_galaxy", "avatar_dino"],
  "equipped": { "avatar": "avatar_rocket", "frame": "frame_space", "theme": "theme_galaxy" }
}
```

A small **global cosmetic catalog** (a JSON constant or a `cosmetics` static file — does **not** need a DB table
in v1) defines each item:

```json
{ "id": "avatar_rocket", "slot": "avatar", "name": "Rocket",
  "interest_tags": ["space", "science"], "unlock": { "type": "level", "level": 3 } }
```

`slot ∈ {avatar, frame, theme}`. `unlock.type ∈ {level, badge, quest, default}`.

### 7.2 Unlock rule (interest + level)

On level-up (§3.2) `unlock_cosmetics_for_level()` adds every catalog cosmetic where
`unlock.type=='level' && unlock.level<=new_level && (interest_tags ∩ student.interests ≠ ∅ OR interest_tags empty)`.
This makes unlocks feel **personal**: a student who chose "space" in `students.interests` (spec 01 §2) unlocks
space-themed avatars as they level; a "dinosaurs" student unlocks dino cosmetics. Generic (untagged) cosmetics
unlock for everyone. Badge/quest-gated cosmetics unlock when that badge/quest is earned.
Equipping is a pure preference write (`PATCH /api/cosmetics/equip`) — validated against `unlocked`.

### 7.3 Framing

Cosmetics replace the static `avatar_url` for kids who want it (the equipped `avatar` cosmetic takes precedence
over `students.avatar_url` in the header). Fully suppressed when gamification is off (§8 framing).

---

## 8. Grade-appropriate framing (B22.5)

Framing varies by **grade band** (spec 02 §1) and respects two switches:
`settings.gamification_enabled` (existing toggle) and `accommodations.reduced_distraction` (spec 01 §7).

| Aspect | K-2 / 3-5 (playful) | 6-8 (moderate) | 9-12 (subtle/achievement) |
|---|---|---|---|
| Level-up | full-screen animated toast, sound, confetti | sliding toast | quiet header badge bump, no sound |
| XP gain | big floating "+30!" with sparkle | small floating number | header counter increments, no float |
| Streak | flame mascot + cheer (no pressure — §9) | flame icon + count | day count, neutral |
| Badge unlock | celebratory modal w/ character | toast | inline list entry |
| Skill tree | colorful, characters at branch ends | clean tree | dense, data-forward |
| Language | "Awesome! You leveled up!" | "Level up!" | "Reached Level 8" |
| Sound effects | on by default (`sound_effects`) | optional | off by default |

Resolution: read `student.grade_band` (FSM session blob, spec 02 §1) → pick the framing profile. The frontend
receives a `framing` field in `GET /api/gamification` (`{"band":"3-5","animations":true,"sound":true}`) so it
doesn't re-derive band client-side.

### 8.1 Toggle & accommodations (must respect)

- `settings.gamification_enabled == false` (mirrored to `localStorage 'helga-gamification'`, `base.html:351`):
  XP/levels **still accrue server-side** (so turning it back on isn't a hard reset), but **all** surfaces are
  hidden — `data-gamification="off"` already hides the bar (`base.html:65`), and toasts early-return
  (`base.html:337`). Skill tree degrades to a plain progress list. Quests hidden.
- `accommodations.reduced_distraction == 1`: force `animations=false, sound=false, confetti=false` regardless of
  band; show XP/levels as **static text only**, suppress floating numbers and celebratory modals. This composes
  with the toggle (toggle off wins = hide entirely). Badges still record; they just appear quietly.
- `accommodations.larger_targets`: skill-tree nodes/buttons use the larger hit-target sizing.

---

## 9. Safety (B22.6) — COPPA / no dark patterns

### 9.1 Leaderboards

- **No open / cross-family leaderboards.** There is **no** endpoint, query, or UI that exposes one student's
  identity, XP, or rank to another family. Hard rule, enforced at the store: any "comparison" query is scoped to
  `parents.id` (within-family) or returns **anonymized aggregates only**.
- **Allowed comparisons:**
  1. **Within-family sibling view** — a parent (and, if the parent enables it, a sibling) may see siblings'
     XP/level/badges. Scoped by `students.parent_id`; requires the requester to be the parent or a sibling under
     the same parent. Endpoint: `GET /api/family/leaderboard` (parent-auth; returns only `parent_id`'s students).
  2. **Anonymized cohort percentile** — "you're ahead of ~70% of learners your grade this week." Computed from an
     aggregate of same-`grade_band` students with **no identities, no names, no ids** returned — just a bucketed
     percentile (rounded to 10s) and a minimum cohort size (suppress if cohort < 20 to prevent re-identification).
     Off by default; opt-in per family.

### 9.2 No manipulative dark patterns

- **No aggressive streak pressure for young kids.** For K-2/3-5, streaks are framed as encouragement only:
  - No "you'll lose your streak!" loss-framed notifications. Missed-day messaging for young bands is neutral
    ("Welcome back! Let's learn something fun.").
  - **Streak freeze / grace:** a missed day for K-2/3-5 does not reset to 0 on the first miss — it pauses
    (1-day grace) before reset, so a single busy day isn't punished. (9-12 may use stricter streaks if opted in.)
  - Tie to spec 02 affect handling: gamification never escalates pressure on a struggling young learner.
- **Notification policy (cross-ref B24.3 / spec 01 `notifications`):** gamification nudges obey the notifications
  policy — **no** late-night pings, **no** high-frequency streak reminders for young bands, parent-controllable.
  At most one gentle daily "ready to learn?" nudge, suppressed if `reduced_distraction` or gamification off.
- **No pay-for-XP, no loot boxes, no purchasable cosmetics.** Cosmetics are earned only; nothing is randomized or
  monetized.
- **No countdown timers / FOMO** on quests for young bands (daily quests simply reset; the UI never shows a
  pressuring countdown to K-2/3-5).

---

## 10. Endpoints

All read endpoints resolve `student_id` from the session (`current_student_id()`, spec 01 §1) — **never** from a
client-supplied id that isn't ownership-checked. **There is no public award endpoint.**

| Method & path | Auth | Purpose | Notes |
|---|---|---|---|
| `GET /api/gamification` | student | header state: `total_xp, level, next_level_xp, prev_level_xp, streak_days, daily_xp, daily_goal, framing{}` | replaces `librarian.py:1777`; per-student |
| `GET /api/badges` | student | unlocked + locked badges (catalog ⟕ `student_badges`) | grouped by `scope` |
| `GET /api/skill_tree?subject=` | student | §5.2 data contract | cached per `(student_id,subject)` |
| `GET /api/quests` | student | active quests for current period (+ progress, status) | lazy-assigns period rows (§6.3) |
| `POST /api/quests/claim` | student | mark a **completed** quest `claimed` (animation ack) | **grants no XP** (already credited) |
| `PATCH /api/cosmetics/equip` | student | equip an already-unlocked cosmetic | validates against `unlocked` |
| `GET /api/family/leaderboard` | parent | within-family sibling XP/level/badges (§9.1) | parent-auth, own students only |
| `GET /api/cohort/percentile` | student | anonymized cohort percentile (§9.1) | opt-in; suppressed if cohort < 20 |
| `POST /api/gamification/check_streak` | student | daily streak update on session start | per-student; grace rule for K-2/3-5 (§9.2); evaluates streak badges |
| ~~`POST /api/gamification/award_xp`~~ | — | **DELETED** | award is server-internal only (`GamificationStore.award`) |

`GamificationStore.award(student_id, reason, ref_uid, ctx) -> AwardResult` is **internal** (no route).
Callers: FSM (`answer`, `complete_concept`, `complete_module` — replacing the HTTP self-call at
`fsm_logic.py:2310-2332`), RAG review handler (`review`), exam grader (`exam_pass`). `AwardResult` is surfaced to
the UI through the existing status/state channel (header refresh + toast), not a new client award round-trip.

---

## 11. Test plan / acceptance criteria

**XP per-student on real events (B22.1):**
1. A graded answer (grade ≥ 3) for `stu_A` writes exactly one `xp_ledger` row for `stu_A` and increments only
   `stu_A`'s `student_gamification.total_xp`; `stu_B` is unaffected. (per-student isolation)
2. grade < 3 → no ledger row, no XP. (matches baseline)
3. First-try Bloom-5 answer = 30 XP; flat `review` = 15; `complete_module` = 100; `exam_pass` first-try = 225.
   (economy + multipliers, §2)
4. `SUM(xp_ledger.amount) WHERE student_id == student_gamification.total_xp` for every student after a random
   event sequence. (ledger reconciliation / audit invariant)

**Can't self-award via client (server-authoritative, B22 / #9):**
5. There is **no** route a browser can call to grant XP (`/api/gamification/award_xp` returns 404). A forged
   `POST` to any read endpoint cannot change `total_xp`.
6. Replaying the same `complete_concept`/`exam_pass`/`quest_complete` `ref_uid` grants XP **once** (idempotency).
7. `complete_concept` award is refused if `user_progress` does not actually show that concept mastered for the
   student (fact verification).
8. Daily cap: awards beyond 2000 XP/day don't increase `total_xp`; a `:capped` ledger note is written.

**Skill-tree node states (B22.2):**
9. A standard with all mapped concepts mastered → `mastered`; some started → `in_progress`; none started but
   prereqs mastered → `available`; prereq unmastered → `locked`.
10. Enrichment standards never appear as `locked` prereqs and never block downstream nodes.
11. `/api/skill_tree` reflects a `complete_concept` award after cache invalidation (node transitions correctly).

**Badges & quests:**
12. Completing the last concept of a standard with a `standard_mastery` badge unlocks it once, writes
    `student_badges`, and credits the badge XP through the ledger.
13. A daily quest assigned for `2026-06-30` resets (new `period_key` row) on `2026-07-01`; yesterday's progress
    does not carry over.
14. Reaching a quest target auto-completes it and credits `xp_reward` server-side; `POST /api/quests/claim`
    grants no additional XP.

**Framing / accommodations (B22.5):**
15. `settings.gamification_enabled=false` hides all surfaces but XP still accrues server-side (re-enabling shows
    accumulated XP).
16. `accommodations.reduced_distraction=1` forces `animations=false, sound=false`; level-up shows static text only.
17. K-2 framing returns `animations:true, sound:true`; 9-12 returns `animations:false`. (band resolution)

**Safety / no cross-family exposure (B22.6):**
18. No endpoint returns another family's student identity, name, id, or XP. `GET /api/family/leaderboard` for a
    parent returns **only** that parent's students.
19. `GET /api/cohort/percentile` returns a bucketed percentile with **no** identities and is suppressed (empty)
    when the same-grade cohort < 20.
20. A K-2 student missing one day does not reset streak to 0 (1-day grace); no loss-framed notification is sent.

---

## 12. Open questions

1. **Multipliers on `complete_concept`?** Spec keeps concept completion flat-ish (base 25 × first-try/bloom). Should
   module/exam carry first-try too, or stay flat to avoid runaway? (current: module flat, exam multiplied.)
2. **Cohort percentile cohort definition** — same `grade_band` only, or same band + subject? And the exact
   minimum-cohort suppression threshold (20 proposed) needs a privacy review.
3. **Prerequisite source** — v1 derives strand ordering from `grade_numeric`+code; do we need an explicit
   `standards.prerequisites` column sooner (some strands aren't linear)?
4. **Streak timezone** — daily/period keys use student-local date; we need the student's timezone (currently
   server-local). Add `students.timezone`?
5. **Cosmetic catalog location** — JSON constant vs a `cosmetics` DB table. Spec 01 §6 lists `cosmetics` as JSON
   on the student; a global catalog table may still be worth it once items are authored by non-engineers.
6. **Session-minutes quests** need a reliable active-time heartbeat (anti-idle); defer `session_minutes` quests
   until that exists.
7. **XP for the legacy `stu_legacy0`** after backfill — start ledger from migrated total (proposed) vs replay
   from `activity_log` to get an accurate ledger history.
