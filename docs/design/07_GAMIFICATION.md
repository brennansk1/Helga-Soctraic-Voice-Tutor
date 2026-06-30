# Design Spec 07 — Gamification 2.0 (intrinsic-first, server-authoritative, COPPA-safe)

> Implementation-ready spec for branch **B22** (B22.1–B22.6) and frontend **FE8** (skill-tree map).
> Canonical table/column names come from `docs/design/01_DATA_MODEL.md §6`
> (`student_gamification`, `xp_ledger`, `badges`, `student_badges`, `quests`, `student_quests`,
> `cosmetics` JSON). Grade-appropriate framing follows `docs/design/02_GRADE_ADAPTATION.md`.
> **Design philosophy and every guardrail below are grounded in `docs/GAMIFICATION_RESEARCH.md`**
> (the canonical research basis: Hanus & Fox 2015; Du & Hew 2024; Deci/Koestner/Ryan 1999;
> Cordova & Lepper 1996; Kaißer et al. 2025; the Prodigy/Epic-COPPA cautionary cases).
> This spec **replaces** the global key-value gamification in `services/rag/librarian.py:1637-1939`
> with per-student tables and a **server-authoritative** award path.
>
> **Two non-negotiable invariants:**
> 1. **Learning wins over engagement.** When a mechanic raises engagement but flattens or harms mastery
>    velocity or FSRS retention, it is **cut or redesigned** — the Hanus & Fox failure signature (§11).
>    XP/badges are *informational feedback*, never the driver. The **mastery map is the primary surface;
>    the XP counter is not.**
> 2. **XP is never trusted from the client.** Awards fire only inside services from real, verified events.
>    There is no client-callable "give me XP" endpoint.

---

## 0. Design philosophy & guardrails (read first — it constrains every mechanic below)

Helga's gamification is built on **Self-Determination Theory** (autonomy, competence, relatedness) + **flow**
(optimal challenge), **not** reward maximization. The research is unusually consistent that engagement-maximizing
reward loops carry a *tail risk of undermining the very learning Helga exists to produce* (Hanus & Fox 2015: more
game elements → lower intrinsic motivation → **lower final exam scores**; Du & Hew 2024: gamification's effect on
intrinsic motivation is real but *small*, g=0.257, and weakest on **competence**, the need most tied to mastery).

**The intrinsic core is primary. XP/badges are secondary informational feedback.** Concretely, the surfaces are
ranked: (1) the **mastery map / skill tree** (§5) and per-standard/Bloom progress — the highest-preference,
lowest-risk mechanic in learner studies; (2) **interest-personalized content** (§7) — Helga's biggest intrinsic
lever; (3) **immediate informational feedback** rewarding good *reasoning*; (4) XP/levels/badges as quiet progress
signals layered on top. If those four are working, the XP counter could be hidden entirely (and is, for older bands
and `reduced_distraction` — §8) without weakening the system.

The four SDT/flow layers map onto Helga's existing engine:

| SDT/flow need | Research note | How Helga satisfies it |
|---|---|---|
| **Competence** | weakest gamification effect (g=0.277) → **over-invest here** | mastery map per Utah standard + Bloom; informational feedback every Socratic turn; FSRS retention surfaced |
| **Autonomy** | reliable gamification win (g=0.638) | choose topic/path/avatar/theme/difficulty stance; choose *how* to demonstrate mastery; the disable/minimize toggle (§8) |
| **Relatedness** | **largest** effect (g=1.776), Helga's **thinnest** dimension | tutor warmth; **parent layer** (collaborative goals, family celebration); optional cooperative challenges — **never competition** (§9) |
| **Flow** | challenge slightly above skill; failure low-stakes | FSRS + adaptive Bloom keep the flow channel moving with the learner (spec 02); re-attemptable answers |

**Overjustification guardrail (the single most important constraint — strongest in children; Deci/Koestner/Ryan
1999).** *Expected, tangible, task-contingent* rewards for an already-interesting activity reduce intrinsic
motivation once rewards stop. What does **not** harm: *unexpected* rewards, *verbal praise*, *informational
feedback*. Therefore Helga's rewards are designed to be **unexpected + informational**, not expected-contingent
prizes, and they **reward epistemic behaviors** Socratic teaching values — good reasoning, productive struggle,
revising a wrong answer, asking a good question — **not** mere correctness. *Reward the dialogue itself; do not
build a candy shell around a pill* (the Prodigy failure mode, where children "tolerate the math to get to the
game"). Implementation consequences:

- XP for an `answer` is **not** announced as an expected per-answer payout for kids; it accrues quietly into the
  mastery signal. For young bands the *visible* reward is **verbal praise + a progress step**, not a number.
- A dedicated **epistemic-bonus** reason (`epistemic`, §2.2) grants *unexpected* XP for revision/struggle/good
  questions — surfaced as praise ("Nice — you caught your own mistake and fixed it"), the number is incidental.
- No reward is promised in advance ("answer 5 and get 50 XP!") to young bands; quests state a *learning goal*, and
  any XP is a quiet consequence, not the advertised payoff (§6).

This philosophy is enforced downstream by the **age-scaling skin** (§8 — one engine many skins, reward salience
fades with age), the **humane streak** rules (§9.2), the **minors ethics filter** (§9.3), and the
**measurement kill-thresholds** (§11). Read those as part of the philosophy, not as afterthoughts.

---

## 1. Where we are today (baseline to migrate from)

| Concern | Baseline (librarian.py) | Target (this spec) |
|---|---|---|
| Storage | global K-V `gamification` + `achievements` in `helga.db` | per-student `student_gamification`, `xp_ledger`, `badges`/`student_badges`, `quests`/`student_quests` (spec 01 §6, migration **v7**) |
| Identity | none — one global user | `student_id` (`stu_…`) via `current_student_id()` (spec 01 §1, §8) |
| Award trigger | `POST /api/gamification/award_xp` proxied through web-ui (`app.py:503`) — **client-reachable** | server-internal `GamificationStore.award()`; the public award route is **removed** |
| Award caller (real) | FSM fire-and-forget HTTP to RAG on `grade>=3` (`fsm_logic.py:2310-2332`) | FSM calls in-process `GamificationStore.award(...)`; no HTTP self-trust |
| Levels | `_LEVEL_THRESHOLDS` hardcoded list (`librarian.py:1712`) | closed-form curve (§3); thresholds list retained as a cache |
| "Badges" | 13 hardcoded `achievements` rows | catalog `badges` + criteria DSL (§4); legacy 13 map 1:1 |
| Skill tree | none | **primary surface** — FE8 mastery map from `standards ⟕ concept_standards ⟕ user_progress` (§5) |
| Quests | none | `quests`/`student_quests`, `period_key` reset, learning-goal framed (§6) |
| Cosmetics | none | earned-only, interest+level gated `student_gamification.cosmetics` JSON (§7) |
| Framing/toggle | `gamification_enabled` flag + `data-gamification` attr + level-up toast | grade-banded skin + minimize/disable toggle + `accommodations.reduced_distraction` (§8) |
| Safety | n/a | no leaderboards ever; within-family + anonymized opt-in only; minors ethics filter (§9) |

Migrate legacy data in **v7** per spec 01 §1 backfill: move the global `gamification` K-V under `stu_legacy0`, seed
`student_gamification`, and write one synthetic `xp_ledger` row (`reason='migration'`) so the ledger sum reconciles.

---

## 2. Architecture & XP economy

```
event happens (real, verified)
   │
   ├─ FSM   answer graded, concept/module completed, epistemic behavior detected
   ├─ RAG   spaced review recorded
   └─ Exam grader   exam attempt passed
        ▼  in-process call (no HTTP self-trust)
   GamificationStore.award(student_id, reason, ref_uid, ctx)   ← the ONLY XP entry point
        │ 1. compute amount (pure fn, §2.2)
        │ 2. INSERT xp_ledger (audit / anti-cheat)
        │ 3. UPDATE student_gamification (total_xp, level, daily_xp, streak)
        │ 4. evaluate_badges(student_id, trigger)        (§4.3)
        │ 5. advance_quests(student_id, reason, ctx)     (§6.4)
        │ 6. unlock_cosmetics(...) on level-up           (§7)
        ▼ AwardResult {xp_earned, total_xp, level, level_up, new_badges, quest_updates, cosmetic_unlocks, praise}
   surfaced via existing status/state channel (mastery-map refresh + band-appropriate feedback)
```

- **New sub-store:** `GamificationStore` in `services/common/storage.py` (spec 01 §8), thread-local, `'student_id'`
  in `_VALID_COLUMNS`; every method takes a leading `student_id`.
- The **public** `/api/gamification/award_xp` route and its web-ui proxy (`app.py:503`) are **deleted.** Any award
  happens behind the verified event that justifies it.

### 2.1 Server-authoritative guarantee (requirement #9)

1. `award()` is **never** wired to a browser-reachable route.
2. Each caller passes a `ref_uid` and the store **verifies the underlying fact** (re-derive grade≥3; check
   `user_progress` for completion; check `exam_attempts.passed=1`; confirm a review row was graded).
3. **Idempotency:** completion-class reasons (`complete_concept|complete_module|exam_pass|quest_complete`) refuse a
   second award for the same `(student_id, reason, ref_uid)` → `xp_earned=0`, no ledger row.
4. **Audit invariant:** `student_gamification.total_xp == SUM(xp_ledger.amount)` per student (test §12).
5. **Anti-grind caps:** daily cap **2000 XP/student** (`daily_xp`/`daily_date`); per-concept `answer` cap = base×4
   per concept per day. Past cap → a `:capped` ledger note (amount 0), `total_xp` unchanged. Caps are **never
   surfaced as guilt/limit messaging to kids** (§9.2).

### 2.2 Award table (reconciled with baseline + research)

Base values reconcile with existing `award_xp` (`librarian.py:1836`). The research reframing is in **how/when XP is
*shown*, not the math**: bases stay, but raw XP salience is **de-emphasized** for older bands (§8), the visible
reward for young bands is praise + a progress step, and the new **`epistemic`** reason makes the highest-signal
rewards *unexpected* and tied to reasoning rather than correctness.

| `reason` | Trigger (verified) | Base XP | Notes |
|---|---|---|---|
| `answer` | Socratic answer graded **≥ 3** | **10** | grade<3 → 0. Quiet for young bands (praise is the visible reward). Matches baseline. |
| `epistemic` | revised a wrong answer to correct / sustained productive struggle / asked a substantive question | **15** | **unexpected + informational** (overjustification-safe). Surfaced as *praise*, not an advertised payout. Rewards the dialogue itself. **New.** |
| `complete_concept` | concept mastered in `user_progress` | **25** | idempotent. Matches baseline. |
| `complete_module` | all module concepts complete | **100** | idempotent, flat. Matches baseline. |
| `review` | spaced-repetition card graded | **15** | repeatable, counts to daily cap. Matches baseline. |
| `exam_pass` | `exam_attempts.passed=1` | **150** | once per exam pass. **New.** |
| `quest_complete` | `student_quests.status→completed` | per-quest `xp_reward` | from `quests.xp_reward` (§6). **New.** |
| `badge` (internal) | a badge unlock | `badges.xp_reward` | unexpected bonus; ledger `reason='badge'`. |

**Multipliers** (apply to `answer`, `epistemic`, `complete_concept`, `exam_pass`): first-try ×1.5
(`socratic_retry_count==0`), Bloom 4–5 ×2.0, Bloom 6 ×2.5. Stack multiplicatively then `floor()`. `review` and
`complete_module` are flat. **XP base does not scale by grade band** — difficulty is already expressed through the
Bloom multiplier; this keeps "older ≠ inherently more points."

```python
def compute_xp(reason, *, grade=None, bloom_level=1, first_try=False, quest_reward=0, badge_reward=0):
    if reason == "answer":
        if grade is None or grade < 3: return 0
        base = 10
    elif reason == "epistemic":        base = 15      # unexpected + informational; reward reasoning/revision
    elif reason == "complete_concept": base = 25
    elif reason == "complete_module":  return 100     # flat
    elif reason == "review":           return 15      # flat
    elif reason == "exam_pass":        base = 150
    elif reason == "quest_complete":   return quest_reward
    elif reason == "badge":            return badge_reward
    else: return 0
    mult = 1.0
    if first_try:             mult *= 1.5
    if bloom_level in (4, 5): mult *= 2.0
    elif bloom_level >= 6:    mult *= 2.5
    return int(base * mult)
```

> **Epistemic detection hook:** the FSM already tracks `socratic_retry_count` and grades each turn. A revision is
> "graded <3 then ≥3 on the same concept/question without the answer being given." Productive struggle / good
> questions reuse the existing affect/`_detect_ignorance` path (spec 02 §6). `award(reason='epistemic')` returns a
> `praise` string the FSM speaks — the *number* is incidental and may be hidden entirely (§8).

---

## 3. Level curve

### 3.1 Formula (closed-form, rising thresholds)

```
threshold(L) = 50 * (L-1) * L                          # XP to reach level L; threshold(1)=0
level_from_xp(xp) = floor((1 + sqrt(1 + xp/12.5)) / 2) # inverse, clamped ≥1
```

| Level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 | 15 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| XP to reach | 0 | 100 | 300 | 600 | 1000 | 1500 | 2100 | 2800 | 4500 | 10500 | 19000 |

Levels 1–6 match `_LEVEL_THRESHOLDS` exactly; the curve is now infinite/monotonic (removes the baseline
`[-1]+2000` fudge at `librarian.py:1805`). Cache the first ~40 thresholds for `next_level_xp`/`prev_level_xp`.
`level` is recomputed from `total_xp` on every award and stored denormalized.

### 3.2 What leveling unlocks — and what it deliberately does NOT

Level confers **status + cosmetics only**, never power (no XP multipliers from level) — avoiding a
rich-get-richer loop. On a level increase: (1) band-appropriate feedback (level-up is a *full* moment for K-2, a
quiet header bump for 9-12 — §8); (2) `unlock_cosmetics_for_level()` for interest-matched cosmetics (§7);
(3) possible `level_*` badge. **Level is a progress signal, not the goal** — it is subordinate to the mastery map.

---

## 4. Badges — informational, milestone-tied, not bolt-on

Badges are tied to **genuine learning milestones** (standard/strand mastery, reasoning depth), not arbitrary
point thresholds — keeping them *informational feedback* rather than expected contingent prizes. They are
unexpected (young bands aren't told "do X to earn badge Y" in advance).

### 4.1 Catalog + criteria DSL

`badges(id, name, description, icon, criteria, xp_reward, scope)`, `scope ∈ {standard, strand, streak, special}`
(spec 01 §6). `criteria` is a JSON DSL evaluated server-side — new badges are data, not code.

```json
{ "type": "<enum>", "params": { … } }
```

| `type` | Params | Fires when |
|---|---|---|
| `standard_mastery` | `{standard_code}` | all concepts mapped to that standard mastered |
| `strand_mastery` | `{subject, strand}` | all standards in strand mastered |
| `strand_progress` | `{subject, strand, pct}` | ≥ `pct` of strand standards mastered |
| `concept_count` | `{n}` | total mastered concepts ≥ `n` |
| `streak_days` | `{n}` | `streak_days` ≥ `n` |
| `bloom_reached` | `{level}` | ≥1 correct at `bloom_level ≥ level` |
| `epistemic_count` | `{n}` | ≥ `n` `epistemic` awards (revisions/good questions) — **rewards the dialogue** |
| `exam_pass` | `{kind}` or `{exam_id}` | a matching exam passed |
| `perfect_concept` | `{}` | concept completed with a grade-4 answer |
| `review_count` | `{n}` | ≥ `n` reviews graded |
| `first_event` | `{event}` | first `answer`/`course`/`concept` |
| `quest_count` | `{n}` | ≥ `n` quests completed |

### 4.2 ~12 example badges

| id | name | scope | criteria | xp |
|---|---|---|---|---|
| `bdg_first_answer` | Curious Mind | special | `{"type":"first_event","params":{"event":"answer"}}` | 25 |
| `bdg_self_corrector` | Self-Corrector | special | `{"type":"epistemic_count","params":{"n":5}}` | 80 |
| `bdg_perfect_concept` | Ace | special | `{"type":"perfect_concept","params":{}}` | 75 |
| `bdg_streak_3` | On a Roll | streak | `{"type":"streak_days","params":{"n":3}}` | 100 |
| `bdg_streak_7` | Week Warrior | streak | `{"type":"streak_days","params":{"n":7}}` | 200 |
| `bdg_streak_30` | Monthly Master | streak | `{"type":"streak_days","params":{"n":30}}` | 500 |
| `bdg_concepts_10` | Explorer | special | `{"type":"concept_count","params":{"n":10}}` | 150 |
| `bdg_concepts_50` | Scholar | special | `{"type":"concept_count","params":{"n":50}}` | 300 |
| `bdg_std_6rp` | Ratio Wrangler | standard | `{"type":"standard_mastery","params":{"standard_code":"6.RP"}}` | 120 |
| `bdg_strand_geometry` | Shape Shifter | strand | `{"type":"strand_mastery","params":{"subject":"math","strand":"Geometry"}}` | 250 |
| `bdg_bloom_5` | Evaluator | special | `{"type":"bloom_reached","params":{"level":5}}` | 250 |
| `bdg_exam_summative` | Final Boss | special | `{"type":"exam_pass","params":{"kind":"summative"}}` | 300 |

(Legacy 13 achievements map 1:1; e.g. `streak_3`→`bdg_streak_3`.)

### 4.3 Evaluation hook

`evaluate_badges(student_id, trigger)` runs **inside `award()`** (step 4) so checks ride on verified events — never
a client poll. Scanned by trigger: `answer`→`first_event`/`bloom_reached`/`perfect_concept`;
`epistemic`→`epistemic_count`; `complete_concept`→`standard_mastery`/`strand_*`/`concept_count` (filtered to the
standards the completed concept touches — bounded, no full-catalog scan); `exam_pass`→`exam_pass`;
`review`→`review_count`; streak update→`streak_days`; `quest_complete`→`quest_count`. Newly-unlocked →
`INSERT OR IGNORE INTO student_badges` (idempotent PK) + `award(reason='badge')`; returned in `AwardResult.new_badges`.

---

## 5. Skill-tree / mastery map (B22.2 / FE8) — THE PRIMARY SURFACE

This is the **highest-preference, lowest-risk mechanic** and the surface the Learn experience is built around
(FE5.2). It is the competence engine: the XP counter is subordinate to it. Subjects → strands (branches) →
standards (masterable nodes).

### 5.1 Node states

Node = a standard (`standards.code`); state derived from the student's progress on mapped concepts
(`concept_standards ⟕ user_progress` for `student_id`):

| State | Rule |
|---|---|
| `mastered` | every mapped concept mastered |
| `in_progress` | ≥1 mapped concept started, not all mastered |
| `available` | none started, all prerequisite standards `mastered` |
| `locked` | a prerequisite standard not yet `mastered` |

Prereqs v1: sequential within a strand by `grade_numeric` then code; a later `standards.prerequisites` column can
override. Enrichment standards (`is_enrichment=1`) render as optional ★ side-nodes, never blocking.

### 5.2 Data contract (frontend consumes)

`GET /api/skill_tree?subject=math` (student resolved server-side) →

```json
{
  "student_id": "stu_…", "subject": "math", "grade_band": "6-8",
  "branches": [
    { "strand": "Ratios & Proportional Relationships", "mastered": 1, "total": 3,
      "nodes": [
        { "standard_code": "6.RP.1", "label": "Understand ratios", "state": "mastered",
          "is_enrichment": false, "mastered_concepts": 2, "total_concepts": 2,
          "badge_id": null, "prereqs": [] },
        { "standard_code": "6.RP.2", "label": "Unit rate", "state": "available",
          "mastered_concepts": 0, "total_concepts": 3, "badge_id": "bdg_std_6rp", "prereqs": ["6.RP.1"] },
        { "standard_code": "6.RP.3", "label": "Solve rate problems", "state": "locked",
          "prereqs": ["6.RP.2"], "total_concepts": 4 }
      ] }
  ],
  "summary": { "nodes_total": 24, "nodes_mastered": 7, "pct": 0.29 }
}
```

Clicking `available`/`in_progress` → existing learn flow (next unmastered concept → `NAVIGATE_TO_TOPIC`).
`locked` nodes are non-interactive with a prereq tooltip. Computation is read-only, cached per `(student_id,
subject)`, invalidated on any `complete_concept`. One join, all subject concepts in a single `IN (...)` (no N+1).
Only **published catalog** standards at the student's band appear (spec 01 §4.1).

### 5.3 Rendering (FE8) — intensity scales with band

SVG/CSS tree; nodes colored by state (`locked` grey, `available` outlined, `in_progress` partial-fill ring =
`mastered_concepts/total_concepts`, `mastered` filled + check + earned badge). The **tree itself is the constant
spine across all bands** (§8); only ornament varies: K-2 gets characters at branch ends + animated reveal, 9-12
gets a dense data-forward dashboard. Honors `data-gamification="off"` (plain progress list) and
`reduced_distraction` (no animation).

---

## 6. Quests / daily challenges — learning-goal framed (overjustification-safe)

Quests state a **learning goal**, not an advertised XP payout — the XP is a quiet consequence, dodging the
expected-contingent-reward trap. For young bands the quest copy is the goal ("Master 3 new ideas today"), and XP is
not shown as the bait.

### 6.1 Definitions (`quests`, global)

`quests(id, title, kind, target, xp_reward, cadence)`, `cadence ∈ {daily, weekly}`.

| `kind` | Counts | Advanced on reason |
|---|---|---|
| `answer_correct` | correct answers | `answer` |
| `complete_concept` | concepts mastered | `complete_concept` |
| `review` | reviews done | `review` |
| `bloom_high` | answers at bloom ≥ 4 | `answer` (bloom≥4) |
| `epistemic` | revisions / good questions | `epistemic` — **rewards the dialogue** |
| `exam_pass` | exams passed | `exam_pass` |

### 6.2 Example quests (goal-framed copy)

| id | title (shown) | kind | target | xp | cadence |
|---|---|---|---|---|---|
| `qst_daily_3concepts` | Master Three New Ideas | `complete_concept` | 3 | 60 | daily |
| `qst_daily_review5` | Strengthen Five Memories | `review` | 5 | 40 | daily |
| `qst_daily_revise` | Catch and Fix a Mistake | `epistemic` | 1 | 30 | daily |
| `qst_weekly_deep` | Think Deeply Fifteen Times | `bloom_high` | 15 | 200 | weekly |
| `qst_weekly_explorer` | Explore a Whole Strand | `complete_concept` | 20 | 250 | weekly |

### 6.3 `student_quests` + period reset

PK `(student_id, quest_id, period_key)`. `period_key` = daily `YYYY-MM-DD` (student-local) / weekly `YYYY-Www`.
Lazy assignment: on `GET /api/quests` or first matching award, `INSERT OR IGNORE` a `progress=0, status='active'`
row for the current period. Old periods are history; never resurfaced. **No countdown timer** is shown for young
bands (§9.2) — a quest simply resets; the UI never pressures with "X hours left."

### 6.4 Progress hook

`advance_quests(student_id, reason, ctx)` inside `award()` (step 5): match active current-period quests whose
`kind` matches `reason` (+ `ctx` filters like `bloom≥4`); `progress += 1` clamped to `target`; on reaching target,
`status='completed'` + `award(reason='quest_complete', quest_reward=...)`. XP is credited **server-side at
completion**; `POST /api/quests/claim` only marks `status='claimed'` for animation ack and **grants no XP**.

---

## 7. Interest-themed cosmetic rewards (B22.4) — earned only; the interest profile is the centerpiece

**The interest profile is Helga's single biggest intrinsic-motivation lever** (Cordova & Lepper 1996:
contextualization + personalization + choice produce *large* gains) and the one capability competitors structurally
can't match at scale. Cosmetics are the visible tip; the deeper application is **content** — problems, narratives,
examples, Socratic hooks, and exam items contextualized in the child's specific interests (dinosaurs, basketball,
K-pop, Minecraft). **Cosmetics are EARNED, never bought** (§9.3).

### 7.1 Model

`student_gamification.cosmetics` JSON (spec 01 §6):

```json
{ "unlocked": ["avatar_rocket","frame_space","theme_galaxy","avatar_dino"],
  "equipped": { "avatar":"avatar_rocket", "frame":"frame_space", "theme":"theme_galaxy" } }
```

Global cosmetic catalog (a JSON constant in v1, no DB table needed): each item
`{id, slot∈{avatar,frame,theme}, name, interest_tags[], unlock:{type∈{level,badge,quest,default}, …}}`.

### 7.2 Unlock rule (interest × level)

On level-up, `unlock_cosmetics_for_level()` adds every level-gated cosmetic where
`unlock.level<=new_level && (interest_tags ∩ students.interests ≠ ∅ OR interest_tags empty)`. A "space" student
(spec 01 §2 `students.interests`) unlocks space avatars; a "dinosaurs" student unlocks dino cosmetics — unlocks
feel *personal* and autonomy-supporting (Birk et al.: avatar identification raises effort, enjoyment, even
learning). Equipping (`PATCH /api/cosmetics/equip`) is a preference write validated against `unlocked`. The
equipped avatar takes precedence over `students.avatar_url`.

### 7.3 Wider interest application (cross-ref)

The same `students.interests` profile feeds content theming (spec 02; exam theming spec 01 §5 `exam_attempts.theme`)
and is also used to **retire mechanics an individual child stops responding to** (§8.3 individual-level fade). Treat
the profile as a **wellbeing asset to protect** (encrypted, minimized) — not an engagement asset to exploit
(research Caveats; UNICEF children's-rights-by-design).

---

## 8. One engine, many skins — age-scaling that fades (B22.5)

**Architecture: one progression engine, many skins.** The **spine never changes** — mastery tracking, FSRS,
the progress/level ledger, the skill tree. Only three dials scale by developmental stage (Kaißer et al. 2025;
developmental reward research): **(a) narrative/aesthetic intensity, (b) reward salience/animation density,
(c) autonomy/control.** The five universal principles (Feedback, Progression, Autonomy, Coherence, Adaptivity) are
*applied differently*, not replaced.

| Dial | K-2 (≈5-7) | 3-5 (≈8-10) | 6-8 (≈11-13) | 9-12 (≈14-18) + adult remediation |
|---|---|---|---|---|
| **Skin / aesthetic** | animated companion, storytelling wrapper, collectibles/stickers, TTS-heavy | richer narrative + quests, avatar progression, collections | quests, **cooperative/team** goals, co-creation, real-world content, customization | uncluttered **"tool-like"** competence dashboard, minimal ornament |
| **Reward salience** | high — multisensory praise + a progress step; **avoid overstimulating flashing/constant animation** (cognitive-load risk) | medium — gentle goals | **low — constant points visibility OFF**; salience perceived as controlling is most harmful here | minimal — XP/badges quiet or hidden; points "detached from learning feel superficial" |
| **Autonomy** | **guided choice only** ("too much freedom disorients") | more topic/path choice; self-paced mastery map introduced | high — customization, opt-in cooperation | high — long-term goals, self-reflection via progress history, "challenge me" |
| **Streaks** | encouragement only, generous grace (§9.2) | encouragement, grace | grace, no peer-pressure framing | tied to real outcomes; opt-in stricter |
| **Comparison** | none | self-vs-past only | **opt-in cooperative** only; never performance leaderboards | self-vs-past + opt-in anonymized cohort |

**Default intensity decreases by band**, and **9-12 + adult-remediation users land in low-gamification mode by
default** (research: a motivated teen/adult logging in to fix a weak section should get the high-efficiency
"purposeful tool" experience, not childish reward loops). The XP counter is **de-emphasized for older bands** — for
9-12 the *primary* surface is the mastery dashboard; XP is a subtle line item or hidden.

**Resolution:** read `student.grade_band` (FSM blob, spec 02 §1) → pick the skin profile. `GET /api/gamification`
returns `framing: {band, intensity, animations, sound, xp_visible}` so the frontend never re-derives band.

### 8.1 Prominent minimize/disable toggle (learner + parent controlled)

A first-class control, **defaulting to low-intensity for 9-12 and adult remediation**, learner- and
parent-settable, persisted server-side on `settings.gamification_enabled` (mirrored to `localStorage
'helga-gamification'`, `base.html:351`). Three states: **full / minimal / off**.

- **off:** all gamification surfaces hidden — `data-gamification="off"` already hides the bar (`base.html:65`);
  toasts early-return (`base.html:337`). **XP/levels still accrue server-side** (re-enabling isn't a hard reset).
  The mastery map degrades to a plain progress list — the *spine survives* the skin being off.
- **minimal:** spine + mastery map + quiet feedback; no animations, no XP floats, no celebratory modals.
- **full:** band-appropriate skin.

### 8.2 Accommodations (must respect — spec 01 §7)

- `accommodations.reduced_distraction=1` → force `animations=false, sound=false, confetti=false`, XP/levels as
  **static text only**, suppress floats/modals — regardless of band (composes with toggle; off wins).
- `accommodations.larger_targets` → larger skill-tree node/button hit targets.

### 8.3 Individual-level fade

Beyond band defaults, use the interest profile + response data to **retire mechanics a specific child stops
responding to** (research §“Leveraging the … interest profile”). E.g., if quests no longer change voluntary
practice for a learner, quietly de-emphasize them for that learner (ties to the §11 overjustification threshold).

---

## 9. Safety — COPPA / SDT-relatedness / no dark patterns (B22.6)

### 9.1 Relatedness via the parent layer — NOT competition

Relatedness is the **largest** gamification effect (g=1.776) and Helga's **thinnest** dimension; the healthy source
for a solo/homeschool learner is the **family**, not peer competition (Classcraft's cooperative model, not
Prodigy's tiers):

- **Parent-set collaborative goals** — parents set goals *with* (not just *for*) the child; family celebration of
  milestones; a **wellbeing-oriented** parent dashboard surfacing **mastery + effort, not streak length**.
  Endpoint: `GET /api/family/leaderboard` (parent-auth) is really a *within-family sibling celebration* view,
  scoped strictly to `students.parent_id` — never cross-family.
- **Optional cooperative/team challenges** (shared family goal) — relatedness without competition.
- **NO public/absolute leaderboards ever.** No endpoint, query, or UI exposes one student's identity/XP/rank to
  another family (Hanus & Fox; Kim et al. 2024: leaderboards lowered exam scores and didn't raise practice). Any
  comparison is **self-vs-past** or **anonymized cohort percentile**, **opt-in**, cohort < 20 suppressed (no
  re-identification), no identities/names/ids returned.

### 9.2 Humane streaks (never loss-aversion)

- **Freezes / grace days / repair**, never guilt or FOMO. K-2/3-5 get a **1-day grace** before any reset (a single
  busy day isn't punished); a "streak freeze" item can pause without breaking. Even Duolingo's loss-aversion model
  is criticized for making users "fear losing progress more than they're motivated by learning" — Helga does not
  copy that.
- **No loss-framed messaging** ("you'll lose your streak!") and **no FOMO countdowns** for young bands; missed-day
  copy is neutral/welcoming ("Welcome back! Let's learn something fun").
- **Notifications (cross-ref B24.3 / spec 01 `notifications`):** at most one gentle daily nudge; **no** late-night
  pings, **no** high-frequency streak reminders, parent-controllable, suppressed under `reduced_distraction`/off.
  Ties to spec 02 affect handling — gamification never escalates pressure on a struggling young learner.

### 9.3 Minors ethics filter (the product serves minors, non-ad)

Categorically forbidden (the Prodigy FTC complaint; the Epic/COPPA $520M action — children under 13 often can't
recognize persuasive intent, making these patterns especially exploitative):

- **NO loot boxes / variable-ratio monetized random rewards** ("gamblification").
- **NO pay-to-win tiers, monetized battle passes** — **cosmetics are EARNED, never bought** (§7).
- **NO FOMO countdown timers / artificial scarcity.**
- **NO manipulative re-engagement** exploiting loss aversion.
- **NO currency-masking** ("gems" obscuring real cost) — XP is a progress signal, not a spendable currency.
- **NO "have vs. have-not" social tiers** (the core Prodigy harm).

Allowed (ethically appropriate): cosmetic-only earned rewards; curriculum-aligned quests; seasonal/thematic events
tied to learning; the curriculum skill tree; cooperative/creator modes; roguelike *replayable practice* with varied
questions; collection/album of mastered concepts.

---

## 10. Endpoints

Read endpoints resolve `student_id` from the session (`current_student_id()`, spec 01 §1) — never from an
unverified client id. **There is no public award endpoint.**

| Method & path | Auth | Purpose | Notes |
|---|---|---|---|
| `GET /api/skill_tree?subject=` | student | **primary surface** — mastery map (§5.2) | cached per `(student_id,subject)` |
| `GET /api/gamification` | student | header state + `framing{band,intensity,animations,sound,xp_visible}` | replaces `librarian.py:1777`; per-student; XP de-emphasized for older bands |
| `GET /api/badges` | student | unlocked + locked, grouped by `scope` | milestone-tied |
| `GET /api/quests` | student | active current-period quests (goal-framed) | lazy-assigns period rows (§6.3) |
| `POST /api/quests/claim` | student | mark a **completed** quest `claimed` (anim ack) | **grants no XP** |
| `PATCH /api/cosmetics/equip` | student | equip an unlocked cosmetic | validated against `unlocked` |
| `GET /api/family/leaderboard` | parent | within-family sibling celebration (§9.1) | parent-auth; own students only; wellbeing-oriented |
| `GET /api/cohort/percentile` | student | anonymized opt-in cohort percentile (§9.1) | suppressed if cohort < 20 |
| `POST /api/gamification/check_streak` | student | daily streak update on session start | per-student; K-2/3-5 grace (§9.2); evaluates streak badges |
| ~~`POST /api/gamification/award_xp`~~ | — | **DELETED** | award is server-internal only (`GamificationStore.award`) |

`GamificationStore.award(student_id, reason, ref_uid, ctx) -> AwardResult` is **internal** (no route). Callers:
FSM (`answer`, `epistemic`, `complete_concept`, `complete_module` — replacing the HTTP self-call at
`fsm_logic.py:2310-2332`), RAG review handler (`review`), exam grader (`exam_pass`). `AwardResult` surfaces through
the existing status/state channel (mastery-map refresh + band-appropriate feedback), not a new client award round-trip.

---

## 11. Measurement & decision thresholds (learning wins — the Hanus & Fox signature)

**Every mechanic is provisional and is killed/redesigned if it helps engagement while flattening or harming
learning.** Instrument both layers and gate rollouts on the lagging layer.

| Layer | Metrics |
|---|---|
| **Leading (engagement)** | session frequency, voluntary return, time-in-dialogue, quest/streak participation |
| **Lagging (learning — decisive)** | **mastery velocity** (standards mastered/week), **FSRS retention** (recall at review), **standard coverage**, exam pass rates |
| **Motivation health** | short periodic intrinsic-motivation self-report; **voluntary practice when no reward is offered** |
| **Distress** | streak anxiety signals, quitting-after-failure rate, subgroup disengagement under any comparison mechanic |

**Decision thresholds (these change shipping decisions):**

1. **Hanus & Fox kill rule:** a mechanic that raises a leading metric while **mastery velocity or FSRS retention is
   flat/down** in an A/B test is **cut or redesigned**. Learning wins. The single most important rule.
2. **Overjustification warning:** **declining voluntary practice when rewards are absent** → reduce reward
   contingency for that population/individual (more XP `epistemic`/unexpected, hide the counter, lean on praise).
   Ties to §8.3 individual fade.
3. **Comparison-harm rule:** any subgroup (esp. lower-performers) showing distress/disengagement under a comparison
   mechanic → make it **relative/opt-in or remove**. (Pre-empted by §9.1 having no leaderboards at all.)
4. **Gradual rollout + auto-rollback:** ship behind flags with daily monitoring; **auto-rollback on retention or
   learning harm** — Duolingo's experimentation discipline, but the objective function is **child wellbeing +
   learning**, not revenue.

**Build order (research Recommendations):** Stage 1 intrinsic core first (mastery map, interest avatar,
informational feedback rewarding reasoning, FSRS flow, humane streaks). Stage 2 relatedness/family + earned
cosmetics + quests. Stage 3 age-scaling skins + the minimize/disable toggle. XP/badges layer on **after** the
intrinsic core proves out — never lead with them.

---

## 12. Test plan / acceptance criteria

**Server-authoritative (requirement #9):**
1. No browser-reachable route grants XP (`/api/gamification/award_xp` → 404); forged POSTs can't change `total_xp`.
2. Replaying the same `complete_concept`/`exam_pass`/`quest_complete` `ref_uid` grants XP **once** (idempotency).
3. `complete_concept` is refused unless `user_progress` shows mastery for that student (fact verification).
4. Daily cap: awards beyond 2000/day don't raise `total_xp`; a `:capped` ledger note is written.

**Economy / per-student / audit (B22.1):**
5. A grade-≥3 answer for `stu_A` writes exactly one ledger row for `stu_A` and touches only `stu_A` (isolation).
6. grade<3 → no XP; first-try Bloom-5 answer=30; `epistemic` revision=15 (×mult); `review`=15 flat; `exam_pass`
   first-try=225.
7. `SUM(xp_ledger.amount) == student_gamification.total_xp` for every student after a random event sequence.
8. An `epistemic` award fires on a wrong→right revision and returns a `praise` string; the XP number can be hidden
   without affecting accrual.

**Mastery map primary surface (B22.2):**
9. Node states correct: all mapped concepts mastered→`mastered`; some started→`in_progress`; none started + prereqs
   mastered→`available`; prereq unmastered→`locked`. Enrichment never locks downstream.
10. `/api/skill_tree` reflects a `complete_concept` after cache invalidation.

**Badges / quests:**
11. Mastering a standard's last concept unlocks its `standard_mastery` badge once, writes `student_badges`, credits
    badge XP via the ledger.
12. A daily quest for `2026-06-30` resets (new `period_key`) on `2026-07-01`; progress doesn't carry over. Reaching
    target auto-completes + credits XP server-side; `claim` grants none.

**Framing / fade / accommodations (B22.5):**
13. `gamification_enabled=off` hides all surfaces but XP still accrues; the mastery-map spine survives as a plain
    list. `minimal` shows spine + quiet feedback, no animation.
14. `reduced_distraction=1` forces static text only, no sound/animation, regardless of band.
15. Band framing: K-2 → `intensity=high, animations=true, xp_visible=true`; 9-12 → `intensity=low,
    animations=false, xp_visible=false` and defaults to low/minimal gamification; adult-remediation defaults low.

**Safety / no dark patterns (B22.6):**
16. No endpoint returns another family's student identity/XP/rank. `family/leaderboard` returns only the parent's
    own students; `cohort/percentile` returns bucketed values with no identities and is suppressed when cohort<20.
17. K-2 missing one day does not reset streak to 0 (grace); no loss-framed/FOMO notification is sent.
18. There is no purchasable cosmetic, no random/variable-ratio reward, no countdown timer in any band's UI.

**Measurement (learning wins — §11):**
19. The analytics pipeline records both leading (session/voluntary-return) and lagging (mastery velocity, FSRS
    retention, coverage) metrics per A/B arm, enabling the Hanus & Fox kill rule and auto-rollback.

---

## 13. Open questions

1. **Epistemic-behavior detection precision** — what exactly counts as "productive struggle" / "good question"
   beyond the revision case? Tune from transcripts; risk of over-rewarding (re-introducing contingency).
2. **`xp_visible=false` default boundary** — exact band/age cutoff, and whether advanced younger learners can opt
   into the tool-like mode early (and vice-versa).
3. **Cohort percentile definition** — same `grade_band` only vs band+subject; the minimum-cohort suppression
   threshold (20 proposed) needs a privacy review (children's data).
4. **Streak timezone** — period keys use student-local date; add `students.timezone`? (currently server-local).
5. **Prerequisite source** — v1 derives strand ordering from `grade_numeric`+code; some strands aren't linear →
   may need an explicit `standards.prerequisites` column sooner.
6. **Validating the age bands** — Kaißer et al. recommendations are untested "design propositions"; validate the
   K-2/3-5/6-8/9-12 skin defaults with Helga's own A/B data (research Caveats).
7. **Cosmetic catalog location** — JSON constant (v1) vs a `cosmetics` table once non-engineers author items.
8. **Legacy `stu_legacy0` ledger** — seed from migrated total (proposed) vs replay from `activity_log` for accurate
   history.
