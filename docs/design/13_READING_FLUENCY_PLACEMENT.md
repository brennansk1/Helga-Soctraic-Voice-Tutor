# Design Spec 13 — Reading-Fluency Placement (WCPM-driven mode detection)

> Owns **how Helga finds out whether a child can read**, and what it does with an answer
> that is itself noisy. A read-aloud passage is scored to words-correct-per-minute with
> the ASR we already have; the result selects a **delivery profile** (pre-reader /
> transitional / reader), never a curriculum level, and **never gates anything**.
>
> **Evidence base:** `docs/research/MODE_B_RESEARCH_FINDINGS.md` (*FINDINGS §…*).
> The WCPM switching rule is the single most heavily caveated recommendation in that
> document and this spec is built around that caveat rather than despite it.
>
> **Depends on:** `05_ASSESSMENT_ENGINE.md` (the non-gating `diagnostic` exam kind —
> §1, §5.3), `01_DATA_MODEL.md` (`students`, `exams`, `exam_attempts`,
> `exam_item_responses`, `accommodations`), `02_GRADE_ADAPTATION.md` (bands),
> `12_YOUNG_LEARNER_DELIVERY.md` (what a pre-reader profile actually delivers),
> `06_PARENT_DASHBOARD.md` (the override surface).
>
> **Grounding (verified against HEAD):** `services/stt/stt_server.py:131-148` —
> `POST /api/stt` returns `{"text", "backend"}` and **nothing else**: no timestamps, no
> per-word confidence, no alternatives, no duration. `session.js:1115-1143`
> (`transcribeAndSend`) posts a `MediaRecorder` blob and does not measure elapsed time.
> Everything this spec needs beyond a bare transcript is an **additive change**, listed
> in §3.1. Anything not read is marked **UNVERIFIED**.
>
> **Mode A is untouched.** Placement runs only for students with a `students` row
> carrying a K-12 `grade_band`. An adult session has no `students` row resolving a band
> (`fsm_logic.py:391-397`) and never enters this flow.

---

## 0. The finding, and the caveat that shapes the whole design

FINDINGS §"Re-banding": our four bands *"were wrong at the bottom. The discontinuity that
matters is **reading fluency** — 'learning to read' → 'reading to learn', ~end of grade
2-3 — not a grade line."* And, decisively: ***"Detect it, don't assume it."***

The recommended instrument is oral reading fluency in words-correct-per-minute, against
Hasbrouck & Tindal (2017) norms:

| WCPM | Mode |
|---|---|
| < ~60 | pre-reader/emergent — voice primary, no required reading, tap/manipulative input, TTS + word highlighting |
| ~60-100 | transitional — short readable instructions, mixed voice/text, short typed words |
| > ~100 (+ working-memory & expressive-language check) | dialogue can carry more weight |

Reference points from the same section: end grade 1 ≈ 60 WCPM; end grade 2 ≈ 100; end
grade 3 ≈ 107; end grade 5 ≈ 139-146.

**Now the caveats, which are load-bearing:**

- FINDINGS §Caveats: *"The WCPM switching thresholds are a **synthesis** of
  Hasbrouck-Tindal norms applied to our use case, **not a validated switching rule.**
  Validate against our own data."*
- FINDINGS §"The binding constraints…": ASR word error rate on child speech is
  **26.9% at age 5, 14.6% at 6, 10.5% at 7, 5.1% at 8+** (Yeung & Alwan 2018); open-source
  models on 4-9 year-olds reach **>40% WER** for the youngest.
- FINDINGS §"Re-banding": *"our own ASR can estimate WCPM from a read-aloud passage, but
  child-ASR error means treat it as approximate and confirm near thresholds."*

Read together: **we are proposing to measure a threshold with an instrument whose error
is larger than the decision margin.** At age 5, a child who reads a passage perfectly can
be transcribed at ~73% accuracy, and a naive WCPM would report ~44 against a 60 threshold.
The design must therefore be built so that a wrong measurement is *cheap*, and so the
error's known **direction** is exploited rather than ignored. That is §4 and §5.

---

## 1. What is being decided — delivery profile, not grade band

**Design decision: WCPM selects a `delivery_profile`, and `students.grade_band` is left
alone.**

The two are different questions and conflating them is a real hazard:

| Question | Answer comes from | Column |
|---|---|---|
| *What should this child be taught?* | the parent, at signup (`11_IA…` §5.1 step 4), and the diagnostic's per-standard subscores (`05` §5.3) | `students.grade_band`, `students.grade_numeric` |
| *How should it be delivered to them?* | reading fluency (this spec) | `students.settings.delivery_profile` |

A nine-year-old reading at 45 WCPM needs grade-4 mathematics delivered with narration and
tap input — not kindergarten mathematics. Writing a low WCPM back into `grade_band` would
do the second thing, and would also silently move their Bloom ceiling, their mastery gate,
and which catalog courses they can even see (`11_IA…` §3.1 filters catalog by band from
session). That is unacceptable and this spec never does it.

### 1.1 The three profiles and exactly which fields they override

`delivery_profile ∈ {pre_reader, transitional, reader}`, stored in the existing
`students.settings` JSON column (`01_DATA_MODEL.md` §2, `settings TEXT DEFAULT '{}'`).

It overrides a **closed subset** of `GRADE_BAND_PROFILES` behaviour — the presentation
fields — and **never** the pedagogical ones:

| Field | Overridden by delivery_profile? | Why |
|---|---|---|
| `tts_default` | **Yes** | this is what fluency is about |
| `allow_markdown` | **Yes** | reading load |
| input modality (widget-only / mixed / free text) | **Yes** (§`12` §3.2) | typing and ASR reliability |
| shell tab count, text size, reading load (`15_AGE_ADAPTIVE_SHELL.md`) | **Yes** | |
| `max_words`, `max_sentences`, `new_ideas`, `register`, `answer_expectation` | **No** | these are register and working-memory, set by age/band |
| `bloom_floor`, `bloom_ceiling`, `gate_streak`, `gate_questions`, `gate_types` | **No** | these are mastery, and a fluent reader is not thereby a better mathematician |
| `question_types_for_band()` (`prompts.py:516`) | **No** | Mechanism/Synthesis are gated on working memory, not reading |

Default mapping when no check has run (so the system is correct on day one, before any
placement):

| `grade_band` | default `delivery_profile` |
|---|---|
| `K-1` (and legacy `K-2`) | `pre_reader` |
| `2-3` | `transitional` |
| `4-5`, `6-8`, `9-12` | `reader` |

**A measured result may move a child in either direction from that default**, which is the
whole point of detecting rather than assuming.

---

## 2. The instrument — a `read_aloud` item on the existing diagnostic

This rides the **non-gating `diagnostic` exam kind** already defined in
`05_ASSESSMENT_ENGINE.md` §1: *"Placement / pre-assessment; sets entry depth & Bloom floor.
**Non-gating, non-failing.** … `pass_threshold` n/a (`0.0`) … Writes recommended start,
never blocks."* §5.3 of that spec restates it: *"A `diagnostic` exam never blocks."*

### 2.1 Additive extension to `ITEM_SCHEMA`

`05` §2.2's `item_type` enum is `["mcq", "free", "numeric", "ordering"]`. Add
**`read_aloud`**, with three type-specific fields:

```python
# additive to ITEM_SCHEMA (05 §2.2)
"passage":            {"type": "string"},   # required iff read_aloud
"passage_word_count": {"type": "integer"},  # required iff read_aloud; precomputed
"passage_level":      {"type": "string"},   # e.g. "G1", "G2", "G3" — Hasbrouck-Tindal anchor
```

`05` §2.2's post-generation per-type validation gains: *read_aloud needs `passage`,
`passage_word_count` and `passage_level`.*

**A `read_aloud` item is graded deterministically in-process, no LLM** — it joins the
objective family in `05` §2.4. That matters: on a one-model box, placement must not
compete with teaching for the LLM.

### 2.2 Passages are fixtures, not generated

**Design decision: passages are a small human-reviewed fixture set, authored once, never
LLM-generated at attempt time.** Three reasons:

1. **Comparability.** A WCPM is only meaningful against a passage of known difficulty. A
   freshly generated passage has an unknown level, so the number it produces is not
   comparable to the norms or to the child's own previous check.
2. **Determinism.** Re-checks (§6) compare a child to themselves. A different passage each
   time turns drift into noise.
3. **Safety and cost.** No LLM call in the placement path at all.

```
data/catalog/reading/passages.yaml     # fixture, versioned, human-reviewed
  - id: rp_g1_01
    level: G1
    word_count: 52
    text: "..."
    interest_neutral: true             # NOT interest-themed; see §2.3
```

Minimum viable set: **three passages per level at G1, G2, G3** — nine total. Reviewed
through the existing catalog CMS review path (`04_CATALOG_AND_STANDARDS.md` §4.2) as
content, with the reviewer confirming the level.

**UNVERIFIED / gap:** we have no leveling tool and no leveled corpus. Assigning `level`
is a human judgement by whoever authors the fixture. Word count and sentence length are
mechanical proxies; readability formulas are not in the dependency set. This is the
weakest link in the instrument and is O-1.

### 2.3 Interest theming is **disabled** for `read_aloud`

`05` §3 rewrites an item's surface context to match `students.interests`, guarded by the
validity check in §4. That mechanism is correct for assessed content and **wrong here**:
a re-skinned passage has a different word count, different word frequencies and therefore
a different difficulty — the exact variable being measured. `05` §3.1 already has a skip
condition (*"skip theming if `students.interests` empty OR item_type == 'ordering' with
embedded labels risky"*); add `item_type == 'read_aloud'` to it unconditionally.

### 2.4 How it is presented to a child who cannot read

Circular by construction: we are asking a possible pre-reader to read. The presentation
must make a genuine non-reader's zero score *legible as a zero*, not as a failure:

- The passage is on screen at the young-band type size (`15_AGE_ADAPTIVE_SHELL.md` §4).
- The instruction is **spoken**, never only written: *"Read this out loud. If you get
  stuck, that's okay — just say what you can."*
- **No timer is visible.** Timing is server-side. A visible clock on a struggling
  five-year-old is the thing that makes this feel like a test, which FINDINGS §"UI/UX
  specifics" and `05` §1 both rule out for a diagnostic.
- A **"skip this"** control is always present and always sufficient. A skip is recorded as
  `outcome='skipped'` and resolves to the **default profile for the band** (§1.1) — never
  to `pre_reader` by inference.
- `accommodations.read_aloud_default = 1` (`01_DATA_MODEL.md` §7) means the child has a
  standing read-aloud accommodation. **The read-aloud item is then not administered at
  all**; profile resolves to `pre_reader` by the accommodation, and the parent override
  (§5.3) is the only thing that changes it. Measuring reading fluency in a child whose IEP
  already answers the question is pointless and unkind.

---

## 3. Scoring a passage to WCPM

```
WCPM = words_correct / (duration_seconds / 60)
```

Both terms are problems.

### 3.1 `duration_seconds` — the ASR does not tell us

Verified: `/api/stt` (`stt_server.py:131-148`) returns `{"text", "backend"}`. The
`faster-whisper` backend's segments *do* carry `seg.start`/`seg.end` but the adapter
discards them (`stt_server.py:104-107`, `" ".join(seg.text...)`); the Nemotron adapter
returns a bare string or a dict's `text` field (`:78-84`).

**Required additive change**, in preference order:

1. **Server-authoritative (preferred).** `transcribe()` decodes the temp file it already
   writes (`:143-145`) and returns `duration_ms`. This needs `soundfile` in the STT
   service's requirements — it is already a dependency of the TTS service
   (`tts_server.py` imports `sf`), so the package is known-good on this platform.
   **UNVERIFIED:** whether `soundfile` can decode the browser's `audio/webm;codecs=opus`
   (`session.js:1056`) without ffmpeg. If it cannot, the recorder must be pinned to a
   decodable container for this one flow, or duration comes from path 2.
2. **Client-supplied fallback.** `session.js` records `performance.now()` at
   `_mediaRecorder.start()` (`:1087`) and at `onstop` (`:1082`) and posts the elapsed
   milliseconds.

**Path 2 must never be the only path for a stored result.** A client-supplied duration is
exactly the number that, if wrong or tampered with, inflates WCPM and promotes a child out
of the support they need. A result whose duration came from the client is stored with
`duration_source='client'` and is **never sufficient on its own to change a profile**
(§5.2) — it can only confirm a server-measured one.

Also required: a **hard cap on passage recording length** (90 s). Beyond it the attempt is
`outcome='incomplete'`, not a very low WCPM. A child who wandered off is not a slow reader.

### 3.2 `words_correct` — alignment, not string equality

The reference passage is known. Compute:

```python
def score_read_aloud(reference: str, transcript: str) -> dict:
    ref = _tokenise(reference)        # lowercase, strip punctuation, expand digits,
    hyp = _tokenise(transcript)       #   collapse whitespace, drop filler tokens
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    words_correct = sum(b.size for b in sm.get_matching_blocks())
    return {
        "words_correct": words_correct,
        "words_total": len(ref),
        "accuracy": words_correct / max(1, len(ref)),
    }
```

`difflib.SequenceMatcher` is already used in this codebase for title duplicate detection
(`course_builder._is_duplicate`, and flagged for replacement at scale in
`CLAUDE.md` PERF-2) — at 50-80 tokens it is trivially fast and needs no new dependency.

Two deliberate choices:

- **Matching blocks, not `ratio()`.** We want *how many reference words were produced in
  order*, which is what a WCPM is. A similarity ratio is not that.
- **Filler tokens dropped before alignment** ("um", "uh", repeated immediate tokens). A
  child self-correcting is reading, not erring; ASR insertions are noise either way.

This is the standard curriculum-based-measurement construction adapted to a transcript
rather than a human scorer. **It is our construction, not something FINDINGS specifies** —
the research gives the thresholds and the error rates, not the alignment method.

### 3.3 The result is a lower bound, and we say so

Every ASR error subtracts from `words_correct` and none add to it. So:

> **`wcpm_measured` is a lower bound on true WCPM, with a floor error of the age-indexed
> child WER.**

Expected floor error from FINDINGS §"The binding constraints…", carried as a documented
constant so nobody has to re-derive it:

```python
# ASR word-error floor on child speech (Yeung & Alwan 2018, via MODE_B_RESEARCH_FINDINGS).
# Used ONLY to widen the uncertainty band around a measurement — never to scale a WCPM up.
CHILD_WER_FLOOR = {5: 0.269, 6: 0.146, 7: 0.105, 8: 0.051}   # age -> WER; >=8 uses 0.051
```

**We do not apply a correction factor.** Dividing a measured WCPM by `(1 - WER)` would
manufacture precision the evidence does not support: the WER figures are population means
from one study on a different ASR, and our two backends (`nemotron-mlx`,
`faster-whisper`, `stt_server.py:56,87`) have unknown child performance. Instead the
number is used for **one thing**: to make the decision rule asymmetric (§4.2).

---

## 4. Turning a noisy number into a mode — three mechanisms

### 4.1 Thresholds and margins

```python
WCPM_PRE_TRANSITIONAL = 60      # FINDINGS: end grade 1 ~= 60
WCPM_TRANSITIONAL_READER = 100  # FINDINGS: end grade 2 ~= 100
WCPM_MARGIN = 15                # our hysteresis half-width; NOT from FINDINGS
```

`WCPM_MARGIN = 15` is **our judgement**, chosen so the dead band (±15) is comparable to
the spread between adjacent Hasbrouck-Tindal grade anchors (60 → 100 → 107) rather than
to the ASR error, which is larger than any margin we could pick. Tune from our own data —
FINDINGS §Caveats explicitly asks for that.

### 4.2 Asymmetry — the error's direction is known, so use it

Because `wcpm_measured` is a **lower bound** (§3.3):

| Movement | Rule | Rationale |
|---|---|---|
| **Promote** (`pre_reader → transitional → reader`) | one check above `threshold + MARGIN` is sufficient | a child cannot be transcribed as reading *more* than they read; a high measurement despite ASR error is strong evidence |
| **Demote** (`reader → transitional → pre_reader`) | requires **two** checks below `threshold − MARGIN`, on **different passages**, at least one session apart | a low measurement is exactly what a bad transcription looks like |
| **Near the line** (within ±`MARGIN`) | administer a **second passage at the same level** in the same attempt; use the **higher** of the two | FINDINGS: *"treat it as approximate and confirm near thresholds"* |

Taking the higher of two is the same asymmetry restated: the maximum of two lower bounds
is a tighter lower bound.

### 4.3 Hysteresis — a child must not flip modes turn-to-turn

Three locks, all of which must clear before a stored profile changes:

1. **Dead band.** No change while `wcpm` is within `± WCPM_MARGIN` of the boundary — this
   is what stops oscillation for a child sitting exactly on 60.
2. **Minimum dwell.** A profile that changed less than **14 days or 3 completed sessions
   ago** (whichever is longer) cannot change again automatically. Parent override (§5.3)
   ignores dwell.
3. **Single-step.** A change moves **one** profile step. `reader → pre_reader` in one
   result is refused; it must go via `transitional`. A two-step drop is far more likely a
   broken microphone than a child who forgot how to read.

**A profile change never happens mid-session.** It is applied at the *next* session start,
so a child never experiences the interface changing underneath them — which is also the
autism-predictability requirement in FINDINGS §"Open questions we had not asked"
(*"predictability, consistent layout, previewable session structure, no surprising
changes"*).

---

## 5. Storage, and who can change it

### 5.1 New table (v10, additive)

Follows `01_DATA_MODEL.md` §0 conventions.

```sql
CREATE TABLE IF NOT EXISTS reading_fluency_checks (
    id            TEXT PRIMARY KEY,            -- rfc_<hex>
    student_id    TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    attempt_id    TEXT REFERENCES exam_attempts(id),  -- the diagnostic it rode on; null if standalone
    passage_id    TEXT NOT NULL,               -- data/catalog/reading/passages.yaml id
    passage_level TEXT,                        -- G1 | G2 | G3
    words_total   INTEGER NOT NULL,
    words_correct INTEGER NOT NULL,
    duration_ms   INTEGER NOT NULL,
    duration_source TEXT NOT NULL,             -- server | client
    wcpm          REAL NOT NULL,               -- computed, stored for auditability
    accuracy      REAL NOT NULL,               -- words_correct / words_total
    asr_backend   TEXT,                        -- stt_server.py backend.name, for later re-analysis
    outcome       TEXT NOT NULL DEFAULT 'scored', -- scored | skipped | incomplete | not_administered
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rfc_student ON reading_fluency_checks(student_id, created_at);
```

Append-only. `asr_backend` is stored deliberately: when child-ASR improves past the
FINDINGS §"Thresholds that would change these recommendations" trigger (<10% WER at age
six re-opens voice as a gated channel at K-1), we will want to re-analyse old checks by
backend rather than start over.

### 5.2 The resolved profile

Lives in `students.settings` JSON (no schema change):

```jsonc
"delivery_profile": {
  "value": "transitional",           // pre_reader | transitional | reader
  "source": "measured",              // default | measured | parent | accommodation
  "wcpm": 71.4,                      // null when source != measured
  "decided_at": "2026-08-21T10:04:00",
  "locked_until": "2026-09-04T10:04:00"   // minimum-dwell expiry (§4.3)
}
```

Resolution order at session start, in the FSM alongside the existing band resolution
(`fsm_logic.py:390-397`):

```
accommodations.read_aloud_default == 1        -> pre_reader           (source=accommodation)
settings.delivery_profile.source == 'parent'  -> that value           (source=parent, sticky)
settings.delivery_profile.source == 'measured'-> that value
otherwise                                     -> band default (§1.1)  (source=default)
```

A result whose `duration_source='client'` (§3.1) may **confirm** an existing measured
profile but may not establish or change one.

### 5.3 Parent override — always wins, always sticky

Surfaced on `/parent/students` (`06_PARENT_DASHBOARD.md` §1.3, *"Add/edit/archive
students; grade_band, interests, accommodations"*) as a third control on the same form.

- Setting it writes `source='parent'`, which **outranks every measured result and ignores
  minimum dwell**.
- It is **sticky**: subsequent checks are still recorded in `reading_fluency_checks` (the
  measurement is useful) but do not change the profile until the parent clears the
  override back to "let Helga decide".
- The UI states plainly what it changes and what it does **not**: *"This changes how
  lessons are presented — reading, narration and how your child answers. It does not
  change what your child is taught, or their grade level."*
- Writes an `audit_log` row (`01_DATA_MODEL.md` §7, `action='consent_change'`-family;
  **UNVERIFIED** whether a suitable action value exists — if not, add
  `action='delivery_profile_change'`).

Rationale is directly from FINDINGS §Caveats — the switching rule is *"not a validated
switching rule"* — plus the general principle that a parent watching their own child read
is a better instrument than a 26.9%-WER ASR. The override is not a fallback; on current
evidence it is the **higher-quality signal**, and the UI should not frame it as a
correction to the machine.

---

## 6. Re-checking over time

| When | What runs |
|---|---|
| **First run** | Offered inside the optional placement step (`11_IA…` §5.2 step 3). Skippable; skip → band default. |
| **Every 90 days** | One passage, one level above the child's current profile anchor, presented inside an ordinary session as a "read this with me" moment, not as an exam. |
| **On band change** | When a parent moves `grade_band` (e.g. a new school year), the dwell lock is cleared and a check is offered. |
| **On parent request** | A "check reading again" button on `/parent/students`. |
| **Never** | Automatically after a bad session, a run of wrong answers, or a low exam score. Reading fluency is not a mood. |

The 90-day cadence is chosen against the growth rates in FINDINGS §"Re-banding" (grade 2
≈ 100 → grade 3 ≈ 107 → grade 5 ≈ 139-146): roughly 20-40 WCPM per school year, so a
quarter is the shortest interval at which real growth exceeds our ±15 margin. **This is
arithmetic on the norms, not a research recommendation.**

Every check writes a row regardless of whether it changes anything, so the parent
dashboard can show a **trend**, which is more honest than a single classification. It
renders on `/parent/children/<id>` alongside the Bloom progression
(`06_PARENT_DASHBOARD.md` §3.2) as a sparkline with the two threshold lines drawn, plus
the sentence: *"This is an estimate from a speech recogniser, and it under-counts. Treat
it as a rough guide."*

---

## 7. What this must never do

Stated as prohibitions, because each is a failure mode with a plausible-looking path to it:

1. **Never gate.** No profile, and no WCPM, blocks a concept, a course, an exam, or
   progression. This is inherited from the `diagnostic` kind (`05` §1: *"Non-gating,
   non-failing"*, `pass_threshold 0.0`) and must not be quietly re-introduced by a shell
   that hides content from a `pre_reader`.
2. **Never write to `grade_band`.** §1.
3. **Never surface the number to the child.** No score, no "you read 47 words", no
   comparison to a norm, no badge for it. `07_GAMIFICATION.md` §9.3's minors-ethics filter
   applies; a reading score is precisely the kind of thing that becomes a self-concept.
4. **Never score the passage for comprehension.** This item measures decoding rate only.
   Comprehension is what the rest of the diagnostic is for.
5. **Never let a failed transcription look like a failed child.** `outcome` distinguishes
   `skipped` / `incomplete` / `not_administered` from `scored` precisely so an empty
   transcript resolves to "no data", never to 0 WCPM.
6. **Never re-run it more than once per session.**

---

## 8. Acceptance criteria (tests)

**Scoring**

- `score_read_aloud(ref, ref)` → `accuracy == 1.0`, `words_correct == len(tokens(ref))`.
- A transcript with three substituted words on a 50-word passage → `words_correct == 47`.
- A transcript with an inserted filler ("um") does not reduce `words_correct`.
- Digits and their spelled forms are equivalent under `_tokenise` ("3" ≡ "three").
- WCPM arithmetic: 60 words correct in 60 000 ms → `wcpm == 60.0`.
- A recording over the 90 s cap yields `outcome='incomplete'` and **no** `wcpm`.
- An empty transcript yields `outcome='incomplete'`, never `wcpm == 0`.

**Decision rule — the load-bearing ones**

- A `reader` child measuring 44 WCPM once is **still `reader`** (demotion needs two).
- The same child measuring 44 again, ≥1 session later, on a **different** `passage_id`,
  becomes `transitional` — **not** `pre_reader` (single-step, §4.3.3).
- A `pre_reader` measuring 78 WCPM once becomes `transitional` immediately (promotion
  asymmetry, §4.2).
- A measurement of 62 (within ±15 of 60) triggers a **second passage** in the same attempt
  and uses the higher of the two.
- A profile changed 3 days ago does not change again automatically, whatever the
  measurement (dwell lock).
- A profile change decided mid-session does not take effect until the next session start.
- `duration_source='client'` never establishes or changes a profile; it may confirm one.

**Isolation and safety**

- A `diagnostic` attempt containing a `read_aloud` item has `pass_threshold == 0.0`,
  `passed` is not set, and produces **no** `enrollments` or `user_progress` write beyond
  the recommended-start behaviour `05` §5.3 already defines.
- No response from any endpoint in this flow contains `wcpm` when the caller's role is
  `student` (§7.3).
- `students.grade_band` is byte-identical before and after any number of checks.
- With `accommodations.read_aloud_default = 1`, no `read_aloud` item is generated and the
  stored row is `outcome='not_administered'`, `source='accommodation'`.
- Interest theming is skipped for `read_aloud` (`05` §3.1 skip list) — assert
  `theme_validated` is not set and the passage text is byte-identical to the fixture.
- A parent override survives ten subsequent measured checks.

**Mode A**

- An FSM with no resolved `students` row never generates a `read_aloud` item and never
  reads `delivery_profile`.

---

## 9. Open questions

1. **O-1 — passage leveling.** We have no leveling instrument. Who assigns `level`, on
   what basis, and how do we know a G2 passage is a G2 passage? Without this the WCPM is
   precise about the wrong thing. (See §2.2 UNVERIFIED.)
2. **O-2 — can `soundfile` decode the browser's opus/webm?** Determines whether
   server-authoritative duration (§3.1 path 1) is available at all, and therefore whether
   any measurement can change a profile.
3. **O-3 — `WCPM_MARGIN = 15` is a guess.** FINDINGS asks us to validate the thresholds
   against our own data; the margin has even less backing than the thresholds. What would
   the validation look like given we have no ground-truth WCPM?
4. **O-4 — the third threshold's second half is unimplemented.** FINDINGS puts the
   >100 tier as *"(+ working-memory & expressive-language check)"*. We measure neither.
   Do we ship the reading half alone and say so, or is a >100 WCPM without those checks a
   misleading promotion?
5. **O-5 — should `delivery_profile` be per-subject?** A child may read fluently and still
   need narration in mathematics, where the notation is the barrier. One global profile is
   simpler and is what §1 specifies; per-subject is a plausible v2.
6. **O-6 — 90-day cadence vs. the school year.** §6's interval is arithmetic on the norms.
   Homeschool families do not share a calendar; is "every 90 days" or "at the start of each
   term the parent declares" the better trigger?
7. **O-7 — does a `pre_reader` at 6-8 or 9-12 exist in practice, and what does the shell
   do?** §1 permits it deliberately (an older struggling reader), but
   `15_AGE_ADAPTIVE_SHELL.md` keys tab count on **band**, not profile. A 12-year-old must
   not get the K-1 single-action screen. Spec 15 §2 resolves this; confirm the split is
   right.
8. **O-8 — audit action value.** Whether `audit_log.action` needs a new
   `delivery_profile_change` value (§5.3 UNVERIFIED).
