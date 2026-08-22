# Design Spec 12 — Young-Learner Delivery (K-1 and 2-3)

> How the tutor actually teaches a child who cannot reliably read, type, or be
> transcribed. Owns the **direct-instruction-first teaching loop**, **closed-response
> answer capture**, the **shortened hint ladder**, **TTS + word highlighting**, and
> **age-graded AI disclosure**. Bands `K-1` and `2-3` only.
>
> **Evidence base:** `docs/research/MODE_B_RESEARCH_FINDINGS.md` (cited inline as
> *FINDINGS §…*). Where the research flagged a conclusion as extrapolation, this spec
> says so at the point of use rather than laundering it into a requirement.
>
> **Depends on:** `02_GRADE_ADAPTATION.md` (bands, `GRADE_BAND_PROFILES`),
> `01_DATA_MODEL.md` (`students`, `fsm_sessions`, `accommodations`),
> `05_ASSESSMENT_ENGINE.md` (grading reuse), `13_READING_FLUENCY_PLACEMENT.md`
> (which band a child is actually delivered at), `15_AGE_ADAPTIVE_SHELL.md` (the shell
> this loop renders into).
>
> **Grounding (verified against HEAD, branch `feat/nail-consolidation`):** the
> re-banding to `K-1 | 2-3 | 4-5 | 6-8 | 9-12`, `LEGACY_GRADE_BANDS`, `is_young_band()`,
> `question_types_for_band()` and the process-praise registers **already exist** in
> `services/common/prompts.py:379-536`, guaranteed by `tests/core/test_grade_bands.py`.
> This spec does **not** re-specify them. Every other code reference below was read;
> line numbers are from HEAD. Anything unverified is labelled **UNVERIFIED**.
>
> **Mode A is out of scope and must stay untouched.** Every mechanism here is gated on
> `is_young_band(self.grade_band)` (`prompts.py:477`). Note the FSM's own default is
> `self.grade_band = "9-12"` (`fsm_logic.py:391`), not the `6-8` that
> `DEFAULT_GRADE_BAND` (`prompts.py:438`) and spec 02 §1 document — see §9 O-1. Either
> way `is_young_band()` is False for Mode A, so nothing here reaches the adult tutor.

---

## 0. What the evidence actually said, and what this spec therefore is

Our own hypothesis was *"Socratic questioning is developmentally wrong for under-8s"*.
The research says that is **false** — guided dialogue demonstrably helps children well
under 8 (Alexander's Dialogic Teaching EEF RCT: +2 months English/science, +1 maths,
2,493 Year 5 pupils; FINDINGS §"The verdict on our central hypothesis"). The conclusion
survives for **different reasons**, and the difference changes the design:

| Reason dialogue loses at K-1/2-3 | Design consequence |
|---|---|
| **Novices need guidance** (Kirschner/Sweller/Clark 2006; Stockard et al. 2018, 328 studies, reading effect ~0.66). A child is a novice in nearly every standard they meet. | Teach first, ask second. §2. |
| **Our Socratic engine scores ~2.1/5** and three interventions moved it by zero. | Don't route the youngest through our weakest subsystem. §2. |
| **The mechanics defeat dialogue** — ~4-item working memory at age 5; child ASR WER 26.9% (5), 14.6% (6), 10.5% (7), 5.1% (8+) (Yeung & Alwan 2018); typing unreliable before ~grade 2. | Closed-response capture, §3. Shortened ladder, §4. |

So: **fix the tutor AND route K-1/2-3 around it — not either/or** (FINDINGS, same
section). Dialogue is *reduced*, not deleted: the per-band target is
**direct instruction + practice ~70% / dialogue ~10%** at K-1 and **~60% / ~20%** at 2-3
(FINDINGS §"Per-band parameters"). This spec's loop is what that ratio looks like in code.

**Caveat carried forward, not resolved:** FINDINGS §Caveats — *"Almost none of this is
measured on AI tutors with young children specifically."* Every number below is a
starting value to tune from transcript review, exactly as spec 02 §2 already says of its
own table. The acceptance criteria in §8 test **mechanism**, not pedagogical outcome.

---

## 1. Where this plugs in — no new FSM state

**Decision: this is a sub-mode of `SOCRATIC_LEARNING`, not a new state.** Reasons, all
verified:

- `SOCRATIC_LEARNING` is load-bearing in nine places
  (`fsm_logic.py:1053, 1103, 1194, 1207, 1257, 1401, 1870, 1919, 2002`) — nav guards,
  pause/resume, progress save, the event dispatch. A parallel state means touching all of
  them and forking the persistence path.
- `state` is persisted in the FSM session blob (`01_DATA_MODEL.md` §3). A new state value
  breaks any resident blob written before it.
- The teaching-mode seam **already exists**: `ask_socratic_question(context_trigger,
  initial_mode=None)` (`fsm_logic.py:2472`) already accepts `initial_mode="LECTURE"` and
  is already called that way on retry exhaustion (`fsm_logic.py:3125, 3147`).

### 1.1 New FSM attribute

```python
# fsm_logic.py, __init__ (near the grade_band block at :390-397)
self.concept_phase = None        # None for older bands / Mode A; 'TELL'|'SHOW'|'TRY'|'CHECK'
self.phase_attempts = 0          # attempts inside the current phase
```

Both are added to the persisted blob key list in `01_DATA_MODEL.md` §3
(`concept_phase`, `phase_attempts`). `None` is the Mode-A value and the loader default,
so an existing blob deserialises unchanged.

### 1.2 The three insertion points (exact)

| # | Function | Line (HEAD) | Change |
|---|---|---|---|
| I-1 | `navigate_to_topic()` | `fsm_logic.py:1863` | After the concept context loads and before the opening question is asked: if `is_young_band(self.grade_band)` → `self.concept_phase = 'TELL'`, `self.phase_attempts = 0`, and call `ask_socratic_question(..., initial_mode='LECTURE')`. Older bands: unchanged (`concept_phase` stays `None`). |
| I-2 | `handle_socratic_answer()` | `fsm_logic.py:2979` | Two guards inserted **before** the existing body: the answer-source guard (§3.4) at the very top, then the phase advance (§2.3) immediately after `self.turn_state.record(...)` (`:3047`) and **before** the decision matrix at `:3111`. When `concept_phase is None` the whole block is skipped and the existing matrix runs verbatim. |
| I-3 | `ask_socratic_question()` | `fsm_logic.py:2472` | The phase (not just the grade) selects `teaching_mode`. The existing grade→mode rules at `:2490-2496` remain the fallback for `concept_phase is None`. |

`_check_mastery_gate()` (`fsm_logic.py:1615`) needs **no change** — it already reads
`gate_streak/gate_questions/gate_types` from the band and already clamps
`need_types` to `len(self._question_types())` (`:1626`).

---

## 2. The teaching loop — TELL → SHOW → TRY → CHECK

A composition over the existing turn machinery, not a replacement for it.

```
 navigate_to_topic (I-1)
        │
        ▼
   ┌─────────┐  one turn, no question asked of the child
   │  TELL   │  micro-lecture: the thing itself, stated plainly and warmly
   └────┬────┘  mode=LECTURE · ≤ profile.max_words · ends with "Watch." not "?"
        │ (auto-advance, no answer required)
        ▼
   ┌─────────┐  one turn, a worked example the child watches
   │  SHOW   │  mode=LECTURE + a staged visual aid (stage 1 withheld)
   └────┬────┘  ends by naming what the child is about to do
        │ (auto-advance)
        ▼
   ┌─────────┐  ONE closed-response question (§3), band-gated type (SCENARIO/
   │   TRY   │  APPLICATION/CONTRAST only — prompts.py:505-513)
   └────┬────┘
        │ graded 1-4 by the existing grading call (fsm_logic.py:3015-3032)
        ├── grade ≥ 3 ────────────────────────────────► CHECK
        ├── grade ≤ 2, phase_attempts < retry_limit ──► one hint (§4), stay in TRY
        └── grade ≤ 2, phase_attempts ≥ retry_limit ──► back to SHOW (re-tell, new example)
        ▼
   ┌─────────┐  a second closed-response question, same or one other allowed type
   │  CHECK  │  feeds the existing mastery gate (fsm_logic.py:1615) unchanged
   └────┬────┘
        │ gate met → next_syllabus_item() (existing, fsm_logic.py:2292)
        └─ gate not met → TRY again from _next_unpassed_type_index()
```

### 2.1 Why TELL comes first, and what it costs

Direct instruction first is the whole point (FINDINGS §"The verdict…", reason 1). The cost
is that the tutor **states an answer before the child has tried** — which for an adult is
exactly the `honest_telling`/`socratic` trade-off already measured in this repo
(`prompts.py:58-89`: pushing honest telling up moved `adaptation` 2.80 → 2.07 in the same
run). We accept that trade **only inside the young bands**, because the evidence for
novice guidance is the stronger of the two. It is band-scoped precisely so it cannot
regress Mode A's `socratic` score.

### 2.2 Phase → prompt parameters

| Phase | `teaching_mode` | Question asked? | Aid policy | Word cap |
|---|---|---|---|---|
| `TELL` | `LECTURE` | **No** | opening slot (`aid_policy.SLOT_OPENING`, `aid_policy.py:89`) preferred | `profile['max_words']` (K-1 = 15, 2-3 = 30) |
| `SHOW` | `LECTURE` | **No** | worked-example slot (`SLOT_WORKED`, `aid_policy.py:91`), **staged** — the answer element carries `stage: 1` and is withheld | `profile['max_words']` |
| `TRY` | existing typed-Socratic path | **Yes, exactly one** | cooldown applies (`COOLDOWN_TURNS = 2`, `aid_policy.py:61`) | `profile['max_words']` |
| `CHECK` | existing typed-Socratic path | **Yes, exactly one** | as `TRY` | `profile['max_words']` |

The one-question rule and the word cap are already enforced in code by
`_enforce_dialogue_contract()` (`fsm_logic.py:2764`), which regenerates against *named*
violations. Nothing new is needed — but note TELL and SHOW turns **must not end in a
question**, which is the inverse of the adult contract. This requires a contract variant;
see §9 O-2.

**Diagram budget:** young bands already get `MAX_AIDS_YOUNG = 4` per concept
(`aid_policy.py:57`) via `_budget(grade_band)` (`aid_policy.py:238`). FINDINGS
§"Per-band parameters" recommends **4-5 for K-1, 4 for 2-3**. Raising K-1 to 5 is a
one-constant change; this spec does **not** do it, because the transient-information
constraint (§5.3) argues the opposite direction for a narrated pre-reader and the research
gives no basis to prefer 5 over 4. Left as O-3.

### 2.3 Phase advance (pseudocode for I-2)

```python
# fsm_logic.py, inside handle_socratic_answer, after self.turn_state.record(...) (:3047)
if self.concept_phase is not None:                 # young bands only
    if self.concept_phase in ('TELL', 'SHOW'):
        # These phases take no answer; reaching here means the child spoke
        # or tapped anyway. Acknowledge, do not grade, do not count.
        self._advance_phase()                      # TELL->SHOW, SHOW->TRY
        return
    if grade >= 3:
        self.phase_attempts = 0
        self.concept_phase = 'CHECK' if self.concept_phase == 'TRY' else 'CHECK'
    else:
        self.phase_attempts += 1
        if self.phase_attempts >= self._retry_limit():     # §4
            self.concept_phase = 'SHOW'                    # re-teach, new example
            self.phase_attempts = 0
```

`TELL`/`SHOW` returning early means `concept_question_count` (`:3053`) is **not**
incremented for a turn where no question was asked — which matters, because
`gate_questions` for K-1 is 2 (`prompts.py:399`) and a mis-counted TELL turn would let a
concept complete on one real answer.

### 2.4 Session shape

FINDINGS §"Per-band parameters": **K-1 10-15 min, rotate every 5-8; 2-3 15-20 min, rotate
every 8-10.** FINDINGS §"Session length" is explicit that *"attention span = age in
minutes" is practitioner heuristic, not robust experiment* and applies to
adult-directed non-preferred tasks — so it is used here as a **segment** length, not a cap.

Concretely: one TELL→SHOW→TRY→CHECK cycle is one segment. `_should_park_concept()`
(`fsm_logic.py:1582`) already terminates a concept at `CONCEPT_TURN_CAP` and returns it to
FSRS rather than grinding — that mechanism is correct for young learners and needs no
change; only the cap value is band-relevant. **UNVERIFIED:** the value of
`CONCEPT_TURN_CAP` was not read; it must be band-scoped to ≤ 8 turns for K-1 or a stuck
five-year-old sits through an adult-length grind.

---

## 3. Closed-response answer capture

### 3.1 The rule, stated once

> **A wrong ASR transcription must never be scored as a wrong answer.**
> (FINDINGS §"UI/UX specifics": *"never surface a text correction… A wrong transcription
> must never be scored as a wrong answer — prefer closed-response widgets for anything
> gated."*)

This is currently **violated end-to-end.** Verified: `transcribeAndSend()`
(`services/web-ui/static/js/session.js:1115`) posts the audio to `/api/stt`, writes
`data.text` into the input (`:1127`) and calls `sendTextMessage()` **immediately**
(`:1130`) with no confirmation step. `/api/stt` (`services/stt/stt_server.py:131-148`)
returns `{"text": ..., "backend": ...}` — **no per-word confidence, no timestamps, no
alternatives.** So a six-year-old's mis-transcribed answer goes straight into
`handle_socratic_answer` → the grading call → grade 1 → Bloom drop (`fsm_logic.py:3093`)
→ `concept_miss_streak++` (`:3075`).

Because the ASR gives us no confidence signal, **the guard cannot be confidence-based. It
must be structural.**

### 3.2 The structural rule

At `K-1` and `2-3`, for any turn that will be graded:

1. The tutor emits a **response widget** alongside the question (§3.3). The widget's
   selection is the **only authoritative answer channel**.
2. Voice is accepted as a *selector*, not as an answer: the transcript is matched against
   the widget's own candidate labels (§3.5). A match selects that option. A non-match is
   `AMBIGUOUS`.
3. `AMBIGUOUS` is **not a grade.** It does not call the grader, does not increment
   `concept_question_count`, does not touch `concept_miss_streak`, does not move Bloom.
   The tutor re-plays the question by voice and leaves the widget on screen.
4. Free-form typed text remains accepted at 2-3 (a beginning reader may type a word) and
   is graded normally. At K-1 there is **no text input at all** (§`15_AGE_ADAPTIVE_SHELL`
   §3) so this case cannot arise.

**Threshold that would reverse this** (FINDINGS §"Thresholds that would change these
recommendations"): child-ASR WER for six-year-olds falling below **10%** re-opens voice as
a gated answer channel for K-1.

### 3.3 Widget descriptor — rides the existing aid channel

`add_message()` (`fsm_logic.py:745`) is already *"the one choke point where visual aids are
attached"* and already attaches slim descriptors to the transcript entry
(`entry["aids"] = [aid_descriptor(a) for a in aids]`, `:801`) with the full spec fetched
once from `/api/aid/<id>` (`fsm_logic.py:4736`). Widgets reuse that shape exactly.

```jsonc
// transcript entry, alongside "aids"
"response_widget": {
  "id": "wgt_<hex>",
  "kind": "choice",              // choice | count | number_line | order
  "options": [                   // 1-3 at K-1, ≤4 at 2-3 (FINDINGS §UI/UX specifics)
    {"key": "a", "label": "3", "icon": "three_dots"},
    {"key": "b", "label": "4", "icon": "four_dots"}
  ]
}
```

The **answer key is never in the descriptor** — same anti-leakage rule as
`05_ASSESSMENT_ENGINE.md` §9.2. The full widget, including its `answer_map`, lives in a
`widget_store` mirroring `aid_store` (`fsm_logic.py:798`), fetched server-side only.

Widget kinds are deliberately four, and each is a thin re-use of an existing renderer:
`choice` (buttons), `count` (tap-to-count, tokens), `number_line` (drag a marker on the
existing `number_line` figure kind, `visual_aids.py:66`), `order` (drag 3 cards). No new
figure kinds are introduced — the 13 in `visual_aids.py:66-67` are the whole vocabulary
(constraint §9.7 of the brief).

### 3.4 Widget result → `handle_socratic_answer` normalisation path

New global event, handled in `transition()` next to `REVEAL_AID`
(`fsm_logic.py:1148-1154`), because like `REVEAL_AID` it may arrive from any state and
must not disturb the FSM's teaching state:

```
Browser  ── sendEvent('WIDGET_RESPONSE', {widget_id, key})
   → POST /api/event  → web-ui → POST core /event → transition()
       elif event_type == 'WIDGET_RESPONSE':
           self._handle_widget_response(payload)
           return
```

```python
def _handle_widget_response(self, payload):
    """Normalise a tap into the SAME string the grader would have received."""
    w = self.widget_store.get(payload.get("widget_id"))
    if w is None:                      # evicted; mirror reveal_aid's tolerance (:1021)
        logging.info("WIDGET_RESPONSE: unknown widget; ignoring")
        return
    text = w["answer_map"].get(payload.get("key"))
    if text is None:
        return
    self.handle_socratic_answer(text, source="widget")
```

`answer_map` maps option key → the **natural-language answer string** the child would have
said. So the grading call at `fsm_logic.py:3015` receives exactly the input shape it
receives today — `get_socratic_grading_prompt(concept, question, user_answer, …)` — and
`05_ASSESSMENT_ENGINE.md` §2.4's grading reuse is unaffected. **No new grading path.**

`handle_socratic_answer` gains one kwarg:

```python
def handle_socratic_answer(self, text, image=None, source="text"):
```

and one guard at the very top (insertion I-2a, before the
`conversation_history.append` at `:2984`):

```python
if (is_young_band(self.grade_band)
        and source == "asr"
        and self._pending_widget is not None):
    matched = self._match_asr_to_widget(text)     # §3.5
    if matched is None:
        self._reask_by_voice()                    # replay question; no grade, no count
        return
    text, source = matched, "widget"
```

**Two dependencies this creates, both must land with it:**

- `session.js` must stop auto-sending at K-1/2-3. `transcribeAndSend()` (`:1115`) is
  changed so that when `document.body.dataset.band` is a young band it emits
  `sendEvent('ASR_ANSWER', {text})` rather than filling the text input and calling
  `sendTextMessage()` (`:1127-1130`). The visible text field is **not** populated — a
  pre-reader cannot read a correction, and showing them a wrong one is worse than showing
  nothing (FINDINGS §"UI/UX specifics").
- `_detect_ignorance()` (`fsm_logic.py:2840`) runs before grading and hard-codes grade 1
  (`:2993-2996`). A widget-sourced answer must **skip it entirely** — a child who tapped
  "4" cannot have said "I don't know", and the phrase list contains ordinary content words
  ("pass", "lost", "help") whose substring behaviour has already caused this exact bug
  once (see the comment at `:2880-2888`).

### 3.5 ASR → widget matching

Deterministic, no LLM (this runs on every young-band turn and the box has one model
resident):

```python
def _match_asr_to_widget(self, text):
    """Return the matched option's answer string, or None (AMBIGUOUS)."""
    norm = _normalise(text)                       # lowercase, strip punctuation,
                                                  # spell out digits both ways
    best, second = None, 0.0
    for opt in self._pending_widget["options"]:
        r = difflib.SequenceMatcher(None, norm, _normalise(opt["label"])).ratio()
        ...
    # accept ONLY on a clear win: best >= 0.80 AND best - second >= 0.15
```

The margin requirement is the point. With a 20-40% child WER, a single best-match with no
runner-up gap is as likely to be an ASR artefact as an answer. Two plausible matches is
`AMBIGUOUS`, and `AMBIGUOUS` is free — it costs the child one re-ask and costs the record
nothing. **This matcher is our invention, not a research finding**; the research only
establishes the error rates that motivate it.

### 3.6 What "encouragement instead of escalation" already does

Spec 02 §6 (B17.7) is **already implemented**: `ask_socratic_question` at
`fsm_logic.py:2503-2519` fires a young-band affect scaffold after
`concept_miss_streak >= 2` — eases Bloom toward the floor and injects an `AFFECT NOTE`
that explicitly forbids pressing harder or naming the run of misses. This spec adds
nothing to it. Note it is correctly gated on `is_young_band()` and so survives the
re-banding.

---

## 4. The shortened hint ladder and the earlier micro-lecture

FINDINGS §"Which question types survive contact with a young child": *"the adult 4-step
ladder is too long for K-1 working memory. For K-1 and 2-3, short-circuit to the worked
example fast: one probe or one hint, then show them, then a parallel check. The 'I don't
know' → stop-questioning → micro-lecture → verify path matters more for young children and
should trigger sooner."*

### 4.1 There are two "hint ladders" in this codebase, and only one of them is the Socratic one

Verified, and it matters:

- `get_hint_prompt()` (`prompts.py:1012`) is the documented 4-step ladder. Its **only**
  caller is `handle_flashcard_answer()` (`fsm_logic.py:3439`) — the **spaced-repetition**
  path. It is not used during Socratic teaching at all.
- The ladder that actually runs in `SOCRATIC_LEARNING` is the `socratic_retry_count`
  escalation in `handle_socratic_answer` (`fsm_logic.py:3111-3163`): on grade ≤ 2 it
  re-asks a simpler version of the same type, and at `socratic_retry_count >= 3` it
  switches to `initial_mode="LECTURE"` for a micro-lecture + verification
  (`:3113-3126`, `:3136-3148`).

So the band-scoped change has to be made in **both** places, and only one of them is where
spec 02 §6 says it is.

### 4.2 `_retry_limit()` — band-scoped, one definition

```python
# fsm_logic.py, near _question_types (:1602)
_BAND_RETRY_LIMIT = {"K-1": 1, "2-3": 2}      # everyone else keeps 3

def _retry_limit(self):
    band = LEGACY_GRADE_BANDS.get(self.grade_band or "", self.grade_band)
    return _BAND_RETRY_LIMIT.get(band, 3)
```

Replaces the literal `3` at `fsm_logic.py:3113` and `:3136`. Effect: **K-1 drops to the
micro-lecture after ONE failed attempt; 2-3 after two; 4-5 and up keep today's three.**

### 4.3 The `get_hint_prompt` ladder skip is currently dead — a real bug

`prompts.py:1030`:

```python
ladder_skip = {"K-2": 2, "3-5": 1}.get(grade_band or "", 0)
```

These are the **pre-2026-08-21 band names.** A student whose `students.grade_band` is
`'K-1'`, `'2-3'` or `'4-5'` gets `ladder_skip = 0` — the full adult four-step ladder. This
is exactly the silent-fallback trap that `LEGACY_GRADE_BANDS` and
`test_grade_bands.py::test_legacy_band_names_still_resolve` exist to prevent, in a code
path those tests do not cover. Nothing errors; nothing logs.

**Required fix (documentation only here; implementation is out of this spec's scope):**
resolve through `LEGACY_GRADE_BANDS` and key on the new names —
`{"K-1": 2, "2-3": 1, "4-5": 1}` — so the mapping is stated in terms of the bands that
exist. Note this also *widens* the intent: the old `"3-5"` skip of 1 now applies to both
`2-3` and `4-5`, which matches FINDINGS ("K-1 and 2-3 short-circuit fast") only for the
first of them. Recommended values: `K-1: 2`, `2-3: 2`, `4-5: 1`.

### 4.4 The same stale-band bug in the grading calibration

`prompts.py:833`:

```python
if grade_band in ("K-2", "3-5"):
```

Same defect, higher stakes. This is the calibration that stops the grader failing a young
child for a terse-but-correct answer — spec 02 §4's stated purpose, and
`05_ASSESSMENT_ENGINE.md` §2.4's *"a correct one-word free answer grades ≥3"* acceptance
criterion. Under the new band names it **never fires**, so a K-1 child answering "four"
is graded against the adult rubric at `prompts.py:865` (*"Correct AND explains the
reasoning/mechanism"*) and earns Grade 2. Every downstream consequence follows: no streak,
no Bloom rise, `concept_miss_streak++`.

**Required fix:** replace both literal band tuples with `is_young_band(grade_band)`
(`prompts.py:477`), which already tolerates legacy names by construction.

### 4.5 Micro-lecture length

`get_micro_lecture_prompt()` (`prompts.py:897`) already caps by band — the comment at
`prompts.py:960` states *"band caps the lecture too — a K-2 micro-lecture is 1-2 tiny…"*.
**UNVERIFIED:** whether that cap reads the band through `get_band_profile()` (legacy-safe)
or through another literal tuple. It must be checked against the same defect as §4.3/§4.4
before this spec is implemented.

---

## 5. TTS and synchronised word highlighting

### 5.1 What the TTS service can and cannot do today

Verified in `services/tts/tts_server.py`:

| Claim | Reality |
|---|---|
| 14 voices | **True** — `VOICES`, `:37-41`, exactly 14. |
| Adjustable rate | **The service does not expose it.** `synthesize()` (`:115-150`) reads only `text` and `voice` from the body. The backend adapter accepts `speed` (`_MlxKokoro.__call__`, `:71`) and KPipeline supports it, but no request path sets it. **Spec 02's "TTS default ON, slower rate" for K-1 is not implementable as written.** |
| Word timings | **Not available.** The MLX adapter yields `(None, None, audio)` (`:76`) — graphemes and phonemes are discarded. The torch path yields real graphemes but `synthesize()` unpacks and drops them (`:137`). |
| Server-side speech | **No.** `speak()` (`fsm_logic.py:741`) is *"text-only, no TTS"*; `play_sound`/`stop_audio` are explicit no-ops (`:1035-1041`). TTS is entirely client-side, per-message, on a button (`session.js:284 playMessageTTS`). |

Two consequences for this spec, stated plainly rather than designed around:

1. **`tts_default=True` in `GRADE_BAND_PROFILES` currently drives nothing.** It must be
   consumed by the **shell** as an auto-play behaviour (`15_AGE_ADAPTIVE_SHELL.md` §5),
   not by the FSM.
2. **Word-level highlighting is not achievable from the audio.** Segment-level is.

### 5.2 Segment-level highlighting — the design

Narration is generated and highlighted **per segment**, where a segment is a clause or
short sentence, not a word:

```
POST /api/tts  { text, voice, speed }
→ { "audio_url": "...", "segments": [
      {"text": "Three apples.", "start_ms": 0,    "dur_ms": 940},
      {"text": "Count them.",   "start_ms": 940,  "dur_ms": 720}
   ] }
```

Implementation: `synthesize()` already loops `for _, _, chunk in pipe(text, ...)`
(`:137`) and concatenates. Keep the per-chunk audio lengths instead of discarding them —
`dur_ms = len(chunk) / 24000 * 1000` (the sample rate is hard-coded at `:144`). Segment
text comes from the caller's own split, passed as a list, so the server never has to
re-derive alignment. The client highlights the whole segment for its duration.

Three required additive changes to `tts_server.py`, all small:

- accept `speed` (default `1.0`; **0.85 for K-1**, per spec 02's intent) and pass it to
  `pipe(text, voice=voice, speed=speed)`;
- add `speed` to the cache key at `:127` — it is currently `md5(f"{text}:{voice}")`, so
  without this a slowed K-1 request would be served the cached adult-rate audio;
- return segment boundaries (a JSON sidecar next to the `.wav`, or a `segments` header)
  rather than only `send_file`.

**Word-level highlighting is deferred, not designed.** Claiming it without timings would
be designing around an asset we cannot produce (brief §9.7). If the torch backend's
graphemes were retained and per-grapheme audio lengths measured, word-level becomes
possible on that backend only — noted as O-4, not specified.

### 5.3 The transient-information constraint

FINDINGS §"Cognitive load": narrating on-screen words for a pre-reader is **access, not
redundancy** — the redundancy effect's founding assumption (the learner can decode the
text) does not hold. Adesope & Nesbit (2012, 57 studies) found spoken+written *beats*
spoken-only for low-prior-knowledge learners (g = 0.34); Knoop-van Campen et al. (2018)
found a **reversed** redundancy effect in 11-year-olds with dyslexia.

But the same section carries the binding caveat: **transient information effect** (Leahy
& Sweller 2011, tested on primary-school students) — *"keep narration short and
segmented."* And FINDINGS §Caveats is explicit that the modality effect **reverses under
learner-paced conditions (d = −0.14)** and Helga is self-paced, so *"the pre-reader case
for narration rests on access — they cannot read the text at all — not on the modality
effect."*

Concrete rules that follow:

| Rule | Value | Why |
|---|---|---|
| Max segment length | **8 words** | transient information; one segment must be holdable |
| Max segments per narrated turn | **2 at K-1, 4 at 2-3** | equals `max_sentences` × 2 (`prompts.py:398, 408`) |
| Text stays on screen after narration | **Always** | the transient part must be the *audio*, not the text — this is what makes it non-transient |
| Replay control | **Always present, one tap** | re-listening is the pre-reader's only re-read |
| Auto-advance between segments | **No** | self-paced; the child taps for the next segment at K-1 |

The "always on screen" rule is why this is *not* a redundancy violation: the written words
persist and are the artefact; the speech is the access ramp to them.

### 5.4 Read-aloud of the child's own text

Spec 02 §2 lists *"Read own text aloud: yes"* for K-2. At **K-1 there is no text input**
(§3.2, and `15_AGE_ADAPTIVE_SHELL.md` §3), so there is nothing to read back. The feature
applies at **2-3 only**, where a child may type a word. Specified as: after a typed answer
is submitted, the shell offers (does not auto-play) a read-back of what was submitted.

---

## 6. AI disclosure, age-graded

FINDINGS §"Open questions we had not asked": children anthropomorphise more than adults,
**voice amplifies it**, and they form genuine parasocial attachments to voice assistants
(Hoffman et al. 2021, 3-10 year-olds). *"Yes, a child should know they're talking to an AI,
at every age."* The same paragraph is flagged: **"Thin-evidence territory — flagged as
extrapolation."** The *disclosure* is a product/ethics commitment; the *specific wording
and cadence below are our judgement*, not a research result.

### 6.1 What is said, where, and how often

| Band | Wording | Where | Cadence |
|---|---|---|---|
| **K-1** | "Helga is a computer helper. Helga is not a person." | Spoken on the Today screen at the start of **every** session, before the first lesson turn; plus a persistent literal icon (a small computer) beside the tutor's name in every message. | Every session |
| **2-3** | "Helga is a computer program that helps you learn. Helga is not a person, and Helga can be wrong sometimes." | Same, spoken + written | Every session |
| **4-5** | Same as 2-3, plus "If something Helga says seems wrong, ask a grown-up." | First session of the day | Daily |
| **6-8 / 9-12** | Standard AI disclosure in the shell footer + at first run | Footer + onboarding | First run + persistent |

Three supporting rules, all from the same FINDINGS paragraph:

- **Do not over-humanise the persona for the youngest.** The K-1 persona string is
  currently *"a warm, playful learning guide for a very young child"* (`prompts.py:397`).
  Warm is fine; **the tutor must never claim feelings, a body, a family, a life outside
  the session, or that it misses the child.** This is a new named prohibition in the K-1
  and 2-3 registers — the repo's own measured lesson is that a prompt which says "be
  appropriate" lands 0/5 while naming the specific offender lands 5/5
  (`test_grade_bands.py:124-126` enforces exactly this discipline for praise).
- **No simulated emotional neediness.** No "I was waiting for you", no "don't leave me",
  no streak-loss framing. This composes with `07_GAMIFICATION.md` §9.2 (humane streaks)
  and §6.3's anti-dark-pattern rules in `11_IA_ONBOARDING_NOTIFICATIONS.md`, which already
  turn streak pressure **off by default for young bands**.
- **"Go do something with a real person" nudges.** At the end of a young-band session the
  tutor closes with an off-screen suggestion tied to the concept — which is also the entry
  point into `14_OFF_SCREEN_AND_PHYSICAL.md`.

### 6.2 Parent visibility

FINDINGS §"Open questions we had not asked": *"parents of younger children want more
transcript visibility, not less."* Therefore: **young-band transcripts are
parent-visible by default** on the parent dashboard (`06_PARENT_DASHBOARD.md` §3.3
activity timeline), while crisis-specific sensitive content stays protected by
`08_COMPLIANCE_PRIVACY_SAFETY.md`'s rule that a crisis alert reaches the parent **without
transmitting the transcript** (brief §9.4; already implemented as `_escalate_safety`,
`fsm_logic.py:1675`). Those two are compatible: the default-visible transcript is the
ordinary lesson record; the escalation path is separate and remains redacted.

---

## 7. Composition with the rest of the system

| Spec | Relationship |
|---|---|
| `02_GRADE_ADAPTATION.md` | Owns the band table and `GRADE_BAND_PROFILES`. This spec **consumes** it and adds no new band parameters except `_BAND_RETRY_LIMIT` (§4.2), which belongs beside `BAND_QUESTION_TYPES` in `prompts.py`. |
| `05_ASSESSMENT_ENGINE.md` | Unchanged. Widget answers enter the **same** grading call (§3.4). The K-1 calibration acceptance criterion in 05 §10.1 is currently broken by the stale-band bug in §4.4 and will start passing when it is fixed. |
| `07_GAMIFICATION.md` | Unchanged, and its age-fade table (§8) already specifies K-2 as *"guided choice only"* and *"avoid overstimulating flashing/constant animation"*. FINDINGS §"Gamification genuinely inverts by age" goes further — **age-gate extrinsic mechanics OFF for K-1** (Deci/Koestner/Ryan 1999: *"Tangible rewards tended to be more detrimental for children than college students"*). That is a change to spec 07's K-2 row, not to this spec; raised as O-5. |
| `11_IA_ONBOARDING_NOTIFICATIONS.md` | The four-tab IA is band-collapsed by `15_AGE_ADAPTIVE_SHELL.md`, not contradicted. |
| `13_READING_FLUENCY_PLACEMENT.md` | Decides **which** band a given child is delivered at, which is what selects this loop. |
| `14_OFF_SCREEN_AND_PHYSICAL.md` | The `SHOW` phase is where a physical manipulative is meant to be **in the child's hand** (CRA Concrete stage; FINDINGS §"Manipulatives"). |

---

## 8. Acceptance criteria (tests)

**Band scoping / Mode A**

- `MnemosyneFSM` with `grade_band` in `{4-5, 6-8, 9-12, None}` never sets
  `concept_phase` — it stays `None` through a full concept, and `handle_socratic_answer`
  takes byte-identical branches to today's code (golden-transcript regression).
- `question_types_for_band(None)` still returns all six
  (`test_grade_bands.py::test_older_bands_and_mode_A_keep_all_six` — existing, must keep
  passing).

**The teaching loop**

- A K-1 concept opens in `TELL` with `teaching_mode='LECTURE'` and the first tutor turn
  **contains no question mark**.
- Across a full K-1 concept, the number of turns that ask the child anything is ≥ 2 and
  the ratio of non-questioning to questioning turns is ≥ 2:1 (the ~70/10 DI:dialogue
  target of FINDINGS §"Per-band parameters", measured as a mechanism not a judgement).
- `concept_question_count` does not increment on a `TELL` or `SHOW` turn.
- A K-1 concept can complete: gate is `2/2/1` (`prompts.py:399`) and
  `_check_mastery_gate` clamps `need_types` to the 3 available
  (`fsm_logic.py:1626`) — regression mirror of
  `test_grade_bands.py::test_the_mastery_gate_cannot_demand_more_types_than_the_band_offers`.

**Closed response — the load-bearing ones**

- A `WIDGET_RESPONSE` event from any FSM state does not change `self.state`.
- A widget response produces a call to `get_socratic_grading_prompt` with `user_answer`
  equal to the mapped natural-language string — i.e. the grader cannot tell a tap from a
  typed answer.
- `_detect_ignorance` is **not** consulted for `source='widget'`: a widget whose
  `answer_map` value is literally `"pass"` grades normally rather than short-circuiting to
  grade 1 (`fsm_logic.py:2993`).
- **An ASR transcript that matches no widget option produces no grade.** Assert, after an
  `AMBIGUOUS` turn: `concept_question_count`, `concept_miss_streak`,
  `current_bloom_level` and `passed_question_types` are all unchanged, and no call was made
  to the grading LLM.
- Two options within 0.15 similarity of the transcript → `AMBIGUOUS`, not the better match.
- At K-1 the transcript entry for a gated question always carries a `response_widget`, and
  that descriptor never contains the answer key.

**Ladder and calibration (regressions for live bugs)**

- `get_hint_prompt(..., grade_band='K-1')` at `attempts=2` produces the **worked-example**
  instruction, not the small-hint one. (Fails today — §4.3.)
- `get_socratic_grading_prompt(..., grade_band='K-1')` contains a `GRADE CALIBRATION`
  block. (Fails today — §4.4.)
- `get_socratic_grading_prompt(..., grade_band='K-2')` — the legacy name — also contains
  it, so the fix is legacy-tolerant.
- A K-1 learner reaches `initial_mode='LECTURE'` after **one** grade-≤2 answer; a 6-8
  learner still needs three.

**TTS**

- `POST /api/tts {text, voice, speed: 0.85}` returns audio whose cache key differs from
  the same text at `speed: 1.0` (regression for the `md5(text:voice)` collision,
  `tts_server.py:127`).
- No narrated segment exceeds 8 words; a K-1 turn produces ≤ 2 segments.
- Narrated text remains in the DOM after playback ends.

**Disclosure**

- Every young-band session's first tutor-visible message is the disclosure string, before
  any lesson content.
- The K-1 and 2-3 register strings contain the named prohibition on claiming feelings —
  asserted by string presence, in the style of
  `test_grade_bands.py::test_the_youngest_band_names_the_praise_failure_mode`.

---

## 9. Open questions

1. **O-1 — the FSM's band default is `9-12`, not `6-8`.** `fsm_logic.py:391` sets
   `self.grade_band = "9-12"` when no `students` row resolves, while spec 02 §1 and
   `DEFAULT_GRADE_BAND` (`prompts.py:438`) both say `6-8`. Mode A is safe either way
   (`is_young_band` is False for both), but the documented default is wrong and an adult
   currently gets the 110-word rigorous-mentor register rather than the 70-word one. Which
   is correct?
2. **O-2 — the dialogue contract inverts for TELL/SHOW.** The turn contract requires every
   tutor turn to end in a question (brief §2.6; enforced by `_enforce_dialogue_contract`,
   `fsm_logic.py:2764`). TELL and SHOW must **not**. Does the contract gain a
   `requires_question=False` mode, or do these phases bypass `dc.check` entirely? Bypassing
   loses the word cap and the grounded-claim rule, which we want.
3. **O-3 — K-1 diagram budget 4 or 5?** FINDINGS says 4-5; `MAX_AIDS_YOUNG = 4`
   (`aid_policy.py:57`). The transient-information constraint (§5.3) argues against more
   for a narrated pre-reader. Left at 4 pending transcript review.
4. **O-4 — word-level highlighting.** Achievable only on the torch Kokoro backend, and only
   by retaining graphemes and per-grapheme audio lengths. Worth it, or is segment-level
   sufficient for print awareness? No evidence either way in FINDINGS.
5. **O-5 — extrinsic mechanics at K-1.** FINDINGS says gate them **off**; spec 07 §8 gives
   K-2 *"high reward salience — multisensory praise"* and collectibles/stickers. These
   conflict. The overjustification literature it rests on (Lepper/Greene/Nisbett 1973,
   ages 3-5) is the most age-appropriate evidence we have, but FINDINGS §Caveats notes the
   gamification meta-analysis *"did not disaggregate by age"* and calls the inversion
   *"evidence-informed prudence"*. Spec 07 owns the resolution.
6. **O-6 — `CONCEPT_TURN_CAP` is not band-scoped** (§2.4, UNVERIFIED). What should it be at
   K-1?
7. **O-7 — 2-3 typing.** FINDINGS says typing is *"not reliable before ~grade 2"* and not
   primary until grade 4-5. We allow optional typed words at 2-3. Is that the right side
   of the line, or should 2-3 be widget-only too?
8. **O-8 — widget authoring.** Who writes `answer_map`? Generated per turn by the LLM
   (cheap, unreviewed) or built at course-build time alongside the staged figures
   (reviewed, but cannot adapt to the question the tutor actually asked)? Build-time is
   safer and matches the aid-policy preference for prebuilt slots
   (`aid_policy.py:74-86`); per-turn is the only way to cover an improvised re-ask.
