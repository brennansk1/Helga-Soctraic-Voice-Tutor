# Design Spec 14 — Off-Screen & Physical Work

> Owns everything Helga cannot teach through a chat window: the **concept taxonomy** that
> identifies it, the three **delivery mechanisms** (kit, curated video, parent-guided
> activity), how off-screen work is **verified** and how it enters **FSRS**, and the
> **offline materials manifest** that gets a parent the things they need without the
> machine ever fetching anything.
>
> **Evidence base:** `docs/research/MODE_B_RESEARCH_FINDINGS.md` (*FINDINGS §…*). This is
> the section of the brief we had thought about least, and the section where the research
> is most pessimistic about our preferred mechanism. That pessimism is designed in, not
> softened.
>
> **Depends on:** `04_CATALOG_AND_STANDARDS.md` (build pipeline, `structure.json`, review
> CMS, versioning), `01_DATA_MODEL.md` (`concept_standards`, `user_progress`,
> `enrollments`, `consent_records`, `notifications`), `05_ASSESSMENT_ENGINE.md` (grading
> reuse, non-gating diagnostic), `06_PARENT_DASHBOARD.md` (where materials surface),
> `12_YOUNG_LEARNER_DELIVERY.md` (the `SHOW` phase is where a manipulative belongs).
>
> **Grounding (verified against HEAD):** the epistemic axis exists as a **runtime keyword
> regex**, `aid_policy.is_arbitrary()` (`services/common/aid_policy.py:164-179`), consumed
> via `prompts._concept_is_arbitrary()` (`prompts.py:579-589`) at `prompts.py:715` — it is
> **not** a persisted per-concept attribute, which changes what "we already have this axis"
> means (§1.1). The build pipeline and its `tag_concept_standards` hook are specified in
> `04` §3.2/§3.4. SearXNG search exists at
> `services/research/research_server.py:340 searxng_search` behind
> `/api/research_concept` (`:643`) and `/api/research_batch` (`:658`). The FSRS API is
> `FSRSEngine.calculate_memory(stability, difficulty, rating, days_elapsed)`
> (`services/core/fsrs_engine.py:77`) and `next_interval(stability)` (`:141`).
> Anything not read is marked **UNVERIFIED**.
>
> **Mode A is untouched.** Everything here is a property of **catalog** courses built by
> `CatalogBuildJob` (`04` §3.2). A Mode A course carries no enactment classification, no
> materials manifest, and never enters any flow in this spec.

---

## 0. The recommendation, and the honest framing that comes with it

FINDINGS §"Teaching what a screen cannot teach":

> **"Bring PE and studio arts INTO scope as parent-guided off-screen modules — but market
> Helga as 'academic-core-complete, enrichment-supported'.** Utah requires them to graduate
> (1.5 Fine Arts credits, 1.5 PE + 0.5 Health under R277-700-6), so an accredited-transcript
> ambition cannot exclude them; but the evidence that parent-delivered guides get used is
> weak, so **never let a physical requirement block academic progression.**"

So the brief's §8.5d question — *bring them in, or be explicit that we are academic-core?* —
is answered **both**: bring them in as a real, designed, non-blocking track, and say in
plain language what that track is and is not.

The pessimism is specific and quantified. FINDINGS §"The three mechanisms": parent-guided
activity is *"the weakest mechanism, correctly identified. Fidelity is the documented
failure point: roughly **10% of evidence-based programs in real family settings are
delivered as intended** (Biglan 2015)."* And the countervailing caveat, which we also
carry: FINDINGS §Caveats — *"Parent-fidelity pessimism comes from clinical parent-training
and home-visiting programs, which may not transfer to motivated homeschool families who
self-selected into a tutoring product. **Measure it before trusting it.**"*

The design consequence of holding both at once: **assume ~10% fidelity, instrument it, and
make the minimum viable version still valuable** (FINDINGS, same section). Everything below
degrades to something useful when the parent does nothing at all.

---

## 1. The taxonomy — two axes, not three points on one

FINDINGS §"Classification: two axes, not three points on one":

1. **Epistemic** — derivable vs must-be-told (*"we already have this"*).
2. **Enactment channel** — pure-dialogue / dialogue+diagram / screen-practice /
   on-screen-manipulative / physical-manipulative / real-experiment /
   sustained-bodily-practice / supervised-instruction.

> *"'Needs physical interaction' is **orthogonal**, not a third point on the tell/derive
> line."*

That answers brief §8.1c directly. A convention can need a physical channel (holding a
ruler to see what a centimetre *is*); a derivable principle can be pure dialogue.

### 1.1 What "we already have the epistemic axis" actually means

Verified, and the qualification matters: the epistemic distinction exists as
`aid_policy.is_arbitrary()` — a **narrow keyword regex evaluated per turn on the concept
text** (`aid_policy.py:164-179`), whose docstring says *"Deliberately narrow. A false
positive costs one diagram; a false negative costs a diagram of nothing."* It is consumed
by the tutor prompt at `prompts.py:715` to switch on the honest-telling rules
(`prompts.py:58-89`).

It is **not** stored per concept, not reviewed, and not tuned for anything but the diagram
decision. So:

- **Do not reuse `is_arbitrary()` as the persisted epistemic tag.** Its precision was
  chosen for a cheap, reversible decision; a curriculum classification is neither.
- Persist the epistemic axis as its own field, seeded from `is_arbitrary()` as a **hint**
  during build and confirmed by the same human review as the enactment channel (§2.3).

### 1.2 The enactment channel enum

Canonical, use verbatim. Eight values, straight from FINDINGS.

| `enactment_channel` | Meaning | Physical? | What the tutor does |
|---|---|---|---|
| `pure_dialogue` | talk alone suffices | no | today's Socratic loop |
| `dialogue_diagram` | needs a figure to be teachable | no | today's loop + `aid_policy` |
| `screen_practice` | needs repetition on screen | no | Practice surface (`11_IA…` §2.3) |
| `onscreen_manipulative` | needs a draggable/tappable object | no | the widgets in `12` §3.3 |
| `physical_manipulative` | needs an object in the hand | **yes** | `SHOW` phase with the object (§3.1) |
| `real_experiment` | needs materials, a procedure, an outcome | **yes** | predict → do → explain (§3.2) |
| `sustained_bodily_practice` | needs repeated bodily doing over time (PE, an instrument, drawing) | **yes** | logged practice + debrief (§3.3) |
| `supervised_instruction` | needs a competent adult present (safety or technique) | **yes** | parent guide, hazard-flagged (§3.3) |

```python
PHYSICAL_CHANNELS = frozenset({
    "physical_manipulative", "real_experiment",
    "sustained_bodily_practice", "supervised_instruction",
})
```

**How much of a nominally physical subject is genuinely screen-teachable** (brief §8.1d) —
FINDINGS §"Manipulatives": Carbonneau, Marley & Selig (2013, 55 studies, N = 7,237) find a
small-to-moderate benefit for concrete manipulatives over abstract symbols, moderated by
design (**plainer manipulatives transfer better**); virtual manipulatives are competitive
and *sometimes superior* **at secondary level**, but concrete objects retain an edge for
young children on foundational number/fraction concepts, and CRA
(Concrete-Representational-Abstract) is well-supported.

That produces one concrete rule rather than a principle:

> **For a young band, an `onscreen_manipulative` classification on a foundational
> number/fraction concept is a classification error.** It should be
> `physical_manipulative`, with our widget serving as the **Representational** bridge, not
> as the Concrete stage. Above 6-8, `onscreen_manipulative` is acceptable on its own.

This is enforced as a build-time lint (§2.4), not as a runtime behaviour.

### 1.3 `structure.json` extension

Additive to the concept block already specified in `04` §2.2:

```jsonc
{
  "uid": "con_44444444",
  "title": "Comparing Lengths with a Ruler",
  "bloom_level": 2,
  "learning_objectives": ["..."],
  "concept_standards": [{"standard_code": "2.MD", "coverage": "full"}],

  "enactment": {                                  // NEW
    "channel": "physical_manipulative",
    "epistemic": "told",                          // derivable | told
    "source": "verb",                             // verb | llm | human
    "confidence": 0.9,                            // verb=1.0, llm=model-reported, human=1.0
    "reviewed_by": "par_admin01",                 // REQUIRED before publish iff channel is physical
    "reviewed_at": "2026-08-30T12:00:00",
    "blocks_progression": false,                  // ALWAYS false. §5. Present so it is auditable.
    "materials": ["mat_ruler_30cm", "mat_string"],// -> materials.json ids (§4)
    "activity_ref": "act_ruler_compare",          // -> the parent guide (§3.3), null if none
    "debrief": {                                  // §3.4 — authored at build time, never shown early
      "recall_prompt": "Tell me what you measured and what you found out.",
      "doer_question": "Which was longer, and by about how many centimetres?",
      "mastery_criteria": "Names both objects, gives a difference with a unit."
    }
  }
}
```

### 1.4 New tables (v10, additive)

```sql
CREATE TABLE IF NOT EXISTS concept_enactment (
    concept_uid  TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,          -- see §1.2 enum
    epistemic    TEXT,                   -- derivable | told
    source       TEXT NOT NULL,          -- verb | llm | human
    reviewed_by  TEXT,                   -- parent_id of the reviewer; NULL until reviewed
    reviewed_at  TEXT,
    activity_ref TEXT,
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enactment_channel ON concept_enactment(channel);

CREATE TABLE IF NOT EXISTS course_materials (
    id            TEXT PRIMARY KEY,      -- mat_<hex>
    course_uid    TEXT NOT NULL,
    kind          TEXT NOT NULL,         -- kit | tool | consumable | video | household
    description   TEXT NOT NULL,         -- "a 250 ml graduated cylinder"  <- ALWAYS rendered
    search_term   TEXT NOT NULL,         -- "250ml graduated cylinder"     <- ALWAYS rendered
    url           TEXT,                  -- resolved once at build time; may rot; NEVER rendered alone
    url_resolved_at TEXT,
    substitute    TEXT,                  -- household substitute, or NULL when there is none (§6.1)
    cost_band     TEXT,                  -- free | under_10 | under_25 | over_25
    optional      INTEGER DEFAULT 0,     -- 1 = enrichment; no substitute exists (§6.1)
    hazard        TEXT DEFAULT 'none',   -- none | sharp | heat | chemical | small_parts | physical_exertion
    supervision   TEXT DEFAULT 'none',   -- none | adult_present | adult_performs
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_materials_course ON course_materials(course_uid);

CREATE TABLE IF NOT EXISTS offscreen_completions (
    id           TEXT PRIMARY KEY,       -- ofc_<hex>
    student_id   TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    concept_uid  TEXT NOT NULL,
    evidence     TEXT NOT NULL,          -- debrief | parent_attested | photo | none
    attested_by  TEXT,                   -- parent_id when evidence='parent_attested'
    debrief_grade INTEGER,               -- 1-4 from the existing grader, when evidence='debrief'
    minutes      INTEGER,                -- logged practice minutes (accreditation, §6.3)
    photo_path   TEXT,                   -- stored, NEVER machine-checked (§3.5)
    note         TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_offscreen_student ON offscreen_completions(student_id, concept_uid);
```

---

## 2. Auto-classification at course-build time

FINDINGS §"Classification": *"Partly auto-classifiable: a standard's verb is a strong
signal ('identify', 'explain' → screen; 'measure', 'perform', 'demonstrate', 'construct' →
physical). **Auto-classify at build time, human-review the physical subset** —
mis-classifying a lab as screen-teachable produces a hollow science education."*

That is exactly the shape of the existing `tag_concept_standards` step, which is already
*"LLM-assisted first pass… a draft tag flagged `"tag_source": "llm"`… the human reviewer
must confirm before publish. **Never publish an LLM-only tag.**"* (`04` §3.4). This spec
copies that pattern rather than inventing one.

### 2.1 Where it runs

New step in the `CatalogBuildJob` pipeline (`04` §3.2), immediately after
`tag_concept_standards` because it reads the standards it wrote:

```
  → ContentHydrator(...).hydrate(course_uid)
  → tag_concept_standards(course_uid, brief)        # existing (04 §3.4)
  → classify_enactment(course_uid, brief)           # NEW  (this spec)
  → resolve_materials(course_uid, brief)            # NEW  (§4)
  → set catalog block: catalog_status='draft'
```

Both new steps live in `services/core/catalog_build.py` alongside the existing hook. They
run **offline, at build time, never on a student request** — the same rule `04` §3.6
already states for the whole job.

### 2.2 The classifier — verb-first, LLM second, human last

**Stage 1 — deterministic verb map (source `verb`).** Run over the concept's
`learning_objectives` and the `standards.text` of its tagged `concept_standards`
(`01` §4). Deterministic, no LLM, no network.

```python
_ENACTMENT_VERBS = (
    # -> physical
    (r"\b(measure|weigh|construct|build|assemble|perform|demonstrate|play|sing|"
     r"draw from life|sculpt|dissect|cultivate|conduct an experiment|carry out)\b",
     "real_experiment"),
    (r"\b(manipulate|sort objects|count objects|fold|cut out|arrange physical)\b",
     "physical_manipulative"),
    (r"\b(run|throw|catch|jump|swim|dribble|rehearse|practi[cs]e daily|"
     r"fitness|endurance|technique)\b",
     "sustained_bodily_practice"),
    # -> screen
    (r"\b(identify|explain|describe|define|compare|contrast|classify|interpret|"
     r"summari[sz]e|analy[sz]e|evaluate|justify|predict|calculate|solve)\b",
     "pure_dialogue"),
)
```

Ordered **physical-first deliberately**: a standard reading *"measure and compare
lengths"* contains both `measure` and `compare`, and the expensive error is calling a lab
screen-teachable, not the reverse. This is the same asymmetry `aid_policy` uses in its own
comment at `:174-176`, applied to a decision with much higher stakes.

**Stage 2 — LLM pass for the unmatched (source `llm`).** Only for concepts no verb
matched. One constrained call per concept via
`llm_generate_json(json_schema=ENACTMENT_SCHEMA)` — the same constrained-JSON pattern as
`05` §2.1. Returns `{channel, epistemic, confidence, reason}`. Every result is flagged
`source='llm'`.

**Stage 3 — human review (source `human`).** §2.3.

**Fallback:** a concept with no verb match and a failed LLM call gets
`channel='pure_dialogue'`, `source='llm'`, `confidence=0.0`. It is teachable as-is and
surfaces in review as unclassified — a hollow science lesson is worse than an over-cautious
one, but a *stalled build* is worse than both.

### 2.3 The publish guard — human review of the physical subset

`04` §4.1's transition table gains one guard, in the same form as the existing ones:

| from | action | to | **added guard** |
|---|---|---|---|
| draft | `submit_for_review` | reviewed | *(existing)* + every concept has an `enactment.channel` |
| reviewed | `publish` | published | *(existing)* + **every concept whose `channel ∈ PHYSICAL_CHANNELS` has `reviewed_by` set** + every material referenced by a physical concept exists in `course_materials` with a non-empty `description` **and** `search_term` |

The admin review console (`04` §4.2) per-concept panel gains an **Enactment** row showing
the channel, a `verb`/`llm`/`human` source badge (LLM highlighted as "needs confirmation",
exactly as the existing standards-tag badge), the materials list, and the authored
`debrief` block. Confirming sets `source='human'`, `reviewed_by`, `reviewed_at`.

**Only the physical subset requires review.** A `pure_dialogue` misclassification costs a
concept taught slightly flatly. A `real_experiment` misclassified as `pure_dialogue` costs
a child their science education, which is precisely the failure FINDINGS names.

### 2.4 Build-time lints (warnings, not blocks)

- **CRA lint** (§1.2): `onscreen_manipulative` on a concept whose course band is `K-1`,
  `2-3` or `4-5` **and** whose standard is in a foundational number/fraction strand →
  warn *"physical manipulative recommended for this band (Carbonneau et al. 2013)"*.
- **Hollow-science lint:** a course with `subject='science'` and **zero** concepts in
  `PHYSICAL_CHANNELS` → warn. A science course with no experiment in it is a claim about
  the course, and someone should have to look at it.
- **Epistemic disagreement lint:** `is_arbitrary()` says arbitrary but the classifier said
  `epistemic='derivable'` (or vice versa) → warn, surface both.
- **Coverage lint:** a physical concept whose `materials` list is empty → warn.

---

## 3. The three mechanisms

FINDINGS §"The three mechanisms" is the source for all three. Effectiveness, not
feasibility, drove the ordering.

### 3.1 Kits and physical manipulatives

> *"Evidence supports hands-on kit learning… **Sequence the kit during/around the
> concept** — manipulative in-hand when taught (CRA Concrete), experiment after the
> explanatory setup so the child predicts-then-tests."*

Two different sequences, and the classification picks which:

| Channel | Sequence | Where in the loop |
|---|---|---|
| `physical_manipulative` | **object in hand while taught** | `12` §2's `SHOW` phase. The tutor's SHOW turn says what to pick up; the staged figure on screen mirrors it as the Representational bridge. |
| `real_experiment` | **explain → predict → do → explain** | Predict happens on screen (a graded `TRY` question before the activity). Do happens off screen. Explain is the debrief (§3.4). |

The predict step is not decoration: it is what makes the debrief answerable only by someone
who did the thing, because the interesting question is always *"was your prediction
right?"*

### 3.2 Curated video

> *"Adequate for observing phenomena and visualising the invisible; **inferior for
> procedural/measurement skill and for explanatory connection** (physical labs beat
> virtual on explanation quality). Actively worse than doing when the objective IS the
> manipulation. Integrate by questioning **before** (predict), **during** (notice),
> **after** (explain)."*

Rules that follow:

- Video is a **substitute of last resort** for `real_experiment`, and is marked as such in
  the manifest (`kind='video'`, and the concept keeps its `real_experiment` channel — the
  channel describes what the concept *needs*, not what the family managed).
- Video is **never** a substitute for `sustained_bodily_practice` or for a
  measurement/procedural objective. Watching someone dribble a basketball is not PE.
- Integration: the tutor asks a **predict** question before the link is surfaced, the
  parent sheet carries a one-line **notice** prompt ("watch what happens to the balloon"),
  and the **explain** question is the debrief (§3.4). Same three-stage structure as §3.1.
- **The machine never plays the video.** Helga is offline (brief §9.1). The link surfaces
  on the parent's dashboard and the printed sheet (§4.3); the family watches it on their
  own device.

### 3.3 Parent-guided activity — designed for ~10% fidelity

FINDINGS: *"What makes a guide actually get used: (1) minimal prep and time; (2) explicit
step-by-step scripts; (3) **materials pre-supplied** — a kit in a box beats 'gather these
items'; (4) a concrete completion checkpoint. **Assume low fidelity; make the minimum
viable version still valuable.**"*

The activity guide format is therefore fixed and short:

```
ACTIVITY  act_ruler_compare              Time: 10 minutes   Prep: none
YOU NEED  a 30 cm ruler · a piece of string        (substitutes: any ruler; a shoelace)
SAFETY    none
DO        1. Pick two things in the room.
          2. Ask <child> which is longer. Write the guess down.
          3. Measure both with the ruler.
          4. Ask: were we right? By how much?
DONE WHEN <child> has said a number with "centimetres" in it.
```

Four structural commitments, each traceable to one of the four fidelity factors:

1. **Time and prep are stated first**, in minutes, and a guide over **15 minutes** for K-5
   is a build-time lint failure.
2. **Numbered imperative steps.** No paragraphs, no rationale. The rationale goes to the
   parent dashboard, not into the script.
3. **Substitutes inline**, not in an appendix (§6.1).
4. **"DONE WHEN" is a single observable event**, which is also exactly what the parent taps
   to attest (§3.5).

And the fidelity assumption is designed in: **if the parent does nothing, the child still
progresses** (§5), still gets the concept taught on screen, and the concept still enters
FSRS — flagged as un-enacted, so the record is honest about what was and was not done.

**Instrument it.** `offscreen_completions` (§1.4) is the telemetry FINDINGS §Caveats asks
for (*"Measure it before trusting it"*). The decision it feeds is stated in
FINDINGS §"Thresholds…": *"Parent-completion telemetry on off-screen modules exceeds
~60-70%… → weight parent-attested work more heavily in FSRS. Below that, keep it capped and
low-confidence."* That is §3.6's cap, and the threshold at which to revisit it.

### 3.4 Verification — the debrief, and the doer-only question

FINDINGS ranks the options by reliability × burden:

> *"1. **Child describes what they did back to the tutor + a follow-up question only a doer
> could answer.** Best ratio, works inside our existing dialogue engine, needs no vision
> model, **and can be graded normally by our existing grading call** — the cleanest bridge
> from off-screen work into the graded model."*

Implementation, and it is genuinely small because the bridge already exists:

1. The concept's `enactment.debrief` block (§1.3) is authored at **build time** and
   reviewed with the rest of the physical subset. It is **never shown before the
   activity** — a doer-only question the child has read in advance is not a doer-only
   question.
2. On the next session after the activity is due, the tutor opens the concept in a
   `DEBRIEF` moment: it speaks `recall_prompt` (ungraded, no widget — this is the child
   telling a story), then asks `doer_question` as an ordinary graded turn.
3. `doer_question` goes through **the existing grading call, unchanged**:
   `get_socratic_grading_prompt(concept, question, user_answer, context_text=…,
   bloom_level=…, mastery_criteria=enactment.debrief.mastery_criteria,
   grade_band=self.grade_band)` (`prompts.py:799`), invoked exactly as
   `fsm_logic.py:3015-3032` invokes it today, with `GRADE_JSON_SCHEMA`. **No new grading
   path, no new prompt, no new schema.**
4. The 1-4 grade is stored on `offscreen_completions.debrief_grade` **and** flows into the
   ordinary Socratic path — it is a real answer to a real question.

**What makes a question doer-only.** Authoring rule for the reviewer, enforced by review
not by code: the answer must depend on a **particular, contingent outcome** of the child's
own doing — a measurement they took, a thing that surprised them, whether their prediction
held — and must not be answerable from the concept text. *"Which was longer, and by about
how many centimetres?"* is doer-only. *"Why do we use centimetres?"* is not.

**A child who did not do the activity is not punished for it.** If the debrief cannot be
answered, the tutor takes the honest exit: *"Sounds like you haven't done this one yet —
that's fine, let's come back to it."* No grade is written, `concept_miss_streak` is
untouched, and the concept is not marked failed. Grading a child down for their parent's
unavailability is the single most obvious way to make this feature harmful.

### 3.5 Parent attestation and photos

> *"2. **Parent attestation** — low burden, low reliability. Should produce a **capped,
> flagged, low-confidence** completion signal that unlocks progression but carries reduced
> FSRS weight. 3. **Photo** — we have **no resident vision model**; store for the
> parent/portfolio, do not machine-check."*

- **Attestation** is one tap on the parent dashboard's "this week" list (§4.4), writing
  `offscreen_completions(evidence='parent_attested', attested_by=parent_id, minutes=?)`.
  It never writes a grade.
- **Photo** is stored at `photo_path` for the parent's portfolio (§6.3) and is **never**
  read by any model. There is no vision model resident and the 24 GB budget does not have
  room for one; FINDINGS §"Thresholds…" names *"a resident vision model becomes affordable
  within the 24 GB budget"* as the condition under which this changes.

### 3.6 How off-screen work enters FSRS

Brief §8.3c: *"Our FSRS scheduler is driven by graded answers. An activity that is not
graded has no obvious place in it."* Correct — so we do not manufacture one.

**Design decision: a parent attestation is not a review, and never produces a rating.**

`FSRSEngine.calculate_memory(stability, difficulty, rating, days_elapsed)`
(`fsrs_engine.py:77`) takes `rating ∈ 1..4` and, for a first review, sets stability
directly from the weight for that rating (`:93-95`). Handing it a fabricated rating from an
attestation would put a real number into the child's memory model on the strength of a
parent tapping a button. Instead:

| Evidence | Effect on FSRS |
|---|---|
| `debrief` (graded 1-4) | **Normal.** It is a graded answer; it enters exactly as any other does. This is the path we want and the one FINDINGS ranks first. |
| `parent_attested` | **No rating. No `calculate_memory` call.** The concept's first review is *scheduled sooner*: `interval = min(next_interval(stability), ATTESTED_INTERVAL_CAP_DAYS)` with `ATTESTED_INTERVAL_CAP_DAYS = 7`. A low-confidence signal should produce **more** checking, not less. |
| `photo` / `none` | Identical to `parent_attested`. |

Three invariants:

1. **Attestation may never lengthen an interval.** If a graded review already exists for
   the concept, attestation is recorded and changes nothing.
2. **Attestation never sets `user_progress.status = 'mastered'`.** It sets `'attested'`.
   (**UNVERIFIED:** the `user_progress.status` value set is not in
   `01_DATA_MODEL.md`'s excerpt; `'attested'` must be added to whatever enum exists, and
   every `status='mastered'` query audited so an attestation cannot be mistaken for
   demonstrated mastery — including `05` §5.1's checkpoint upsert.)
3. **The flag is visible.** The parent's standards-coverage view (`06` §3.1) renders
   attested coverage in a distinct state with the words *"parent-reported, not assessed"*.
   Nothing about this should be inferable only from a database column.

**The threshold at which to revisit:** measured parent completion >60-70%
(FINDINGS §"Thresholds…"). Until then, capped.

### 3.7 Homework — mostly, no

FINDINGS §"Homework": Cooper, Robinson & Patall (2006, *RER* 76(1)) — the
homework-achievement correlation is **~0 for elementary**, **+.07 middle**, **+.25 high
school**; the "10-minute rule" is the practitioner standard.

> *"**Do NOT build mandatory achievement-homework into K-5.** Off-screen work in early
> grades is justified **only** where the channel is irreplaceable (experiment, PE, art,
> music practice)."*

Enforced as a build lint and a runtime rule:

- A K-5 catalog course may only carry off-screen work for concepts in `PHYSICAL_CHANNELS`.
  Off-screen *practice* of a `screen_practice` concept is a lint failure at K-5.
- Grades 6-8: permitted, capped at **10 minutes × grade** per day across all courses.
- Grades 9-12: permitted under the same 10-minute rule.
- Nothing is ever mandatory, at any grade (§5).

---

## 4. The offline materials manifest

Brief §8.4 and FINDINGS §"The offline constraint": *"Resolve materials once at course-build
time and treat as fixed content… Purchase/video links surface on the **parent's dashboard**
and an exported/printed materials sheet at course start — never in the child's reach, never
requiring the offline machine to fetch… when a reference dies, show **the description of
what's needed** ('a 250 ml graduated cylinder') plus a generic search term — degrade
gracefully, never show a broken link."*

That answers brief §8.4c with a yes: **resolve once, at build time.**

### 4.1 Resolution at build time

`resolve_materials(course_uid, brief)` in `services/core/catalog_build.py` (§2.1), running
during the window in which the build machine has network access for research.

- The item list comes from the LLM classification pass (§2.2) and the reviewer, not from a
  search: the model says *what is needed* ("a 250 ml graduated cylinder", "a 30 cm ruler"),
  which is the `description`.
- `search_term` is derived deterministically from `description` (strip articles and
  quantities-in-prose, keep the noun phrase). No LLM.
- `url` is resolved **at most once** per item via the existing SearXNG path
  (`research_server.py:340 searxng_search`, behind `/api/research_batch`, `:658`). One
  batch call per course, not per concept.
- `url_resolved_at` is stamped. **No link is ever re-checked**, because the machine cannot.

### 4.2 The degradation rule — links are never load-bearing

> **`description` and `search_term` are always rendered. `url` is always secondary and
> always optional.**

Rendering order everywhere (dashboard, printed sheet, activity guide):

```
a 250 ml graduated cylinder          ← description, always
search: "250ml graduated cylinder"   ← search_term, always
buy: <url>  (found 2026-08-21)       ← url, only if present, with its resolution date
```

There is no "check link" affordance and no dead-link state, because the system cannot
distinguish a dead link from being offline. Showing the resolution date is honest about
staleness without claiming knowledge we do not have. A parent who finds the link dead uses
the search term, which is the same thing they would have done anyway.

### 4.3 Where it surfaces

| Surface | What | Route |
|---|---|---|
| **Parent dashboard — course start** | Full materials list for the course, grouped by when it is needed (module), with cost bands, substitutes, and hazard/supervision flags. Shown at enrollment and permanently thereafter. | new section on `/parent/children/<student_id>` (`06` §1.3) |
| **Printable sheet** | The same list as a one-page PDF, plus the safety disclaimer (§6.2). | `GET /parent/api/children/<sid>/materials.pdf` — reuses the report PDF machinery at `06` §6 |
| **Parent "this week" list** | Only the items needed in the next 7 days, plus the activity guides for them, plus one-tap attest. | `/parent` children overview card (`06` §2) |
| **Activity guide** | The subset that activity needs, inline (§3.3). | in the guide |
| **The child's screen** | **Never.** No links, no purchase, no prices, in any student surface. | — |

The last row is a hard rule, and follows both `11_IA…` §0 principle 2 (*"Students never
see… billing"*) and `07_GAMIFICATION.md` §9.3's minors-ethics filter. It also means the
child is never told that a thing they cannot have is what they need.

### 4.4 Reminders go to the parent, never the child

FINDINGS: *"Reminders go to the **parent only**, as a short 'this week' list, not a
stream."*

One new notification kind in `11_IA…` §6.3's table:

| `kind` | Trigger | Recipient | Channels | `ref_uid` |
|---|---|---|---|---|
| `materials_week` | weekly cron, per child, only when the coming week needs something | **parent** | email (folded into `weekly_digest`) + bell | student id |

Deliberately **one per week, folded into the existing digest** rather than a new stream.
`11_IA…` §6.3's anti-dark-pattern rules apply unchanged; there is no student-facing
notification for off-screen work at all.

---

## 5. The hard rule: a physical requirement never blocks academic progression

FINDINGS: *"the evidence that parent-delivered guides get used is weak, so **never let a
physical requirement block academic progression.**"*

Stated as invariants, each with the specific place it could otherwise leak in:

1. **`enactment.blocks_progression` is always `false`.** It exists in the schema so the
   invariant is auditable, not so it can be set.
2. **The mastery gate does not read enactment.** `_check_mastery_gate()`
   (`fsm_logic.py:1615`) reads streak, Bloom and question-type diversity. It gains no
   fourth conjunct. A concept whose off-screen work never happened completes on its
   on-screen evidence.
3. **`next_syllabus_item()` (`fsm_logic.py:2292`) is never withheld** pending an
   attestation.
4. **Checkpoint exams (`05` §5.1) never include off-screen evidence in
   `pass_threshold`.** A `checkpoint` gates on standards, and the standards are assessed on
   screen.
5. **An enrollment is never `paused` waiting for materials.** A concept whose materials the
   family does not have is taught with its substitute (§6.1), or taught on screen with the
   gap recorded, and moves on.
6. **A missing material is a note on the parent dashboard, never a wall on the child's.**
7. **The one thing a physical requirement *does* affect** is the honesty of the record: the
   concept's coverage renders as *"taught on screen; hands-on step not completed"* in the
   parent standards view (`06` §3.1). The child is never shown this framing.

Corollary for the marketing claim FINDINGS asks for: Helga is **academic-core-complete,
enrichment-supported**. The transcript can show what was assessed and what was attested,
separately, and never conflates them (§6.3).

---

## 6. Equity, safety, accreditation

### 6.1 Kits cost money

FINDINGS: *"provide a household-objects substitute path (beans for counters, measuring cups
for volume, paper folding for fractions). **Be honest that chemistry/microscopy have no
safe household substitute; mark those optional-enrichment.**"*

- Every `course_materials` row either has a **non-empty `substitute`** or has
  `optional = 1`. There is no third state, and the publish guard (§2.3) enforces it.
- `optional = 1` means the concept is reachable and completable without it, and the parent
  sheet says so in words: *"This one needs equipment there is no safe home substitute for.
  Your child can complete this course without it."*
- `cost_band` is on every row so a parent can see the total before enrolling.
- The substitute is rendered **inline in the activity guide**, not in a separate "if you
  can't afford it" section. A family should not have to identify themselves as the poor
  case to find the instructions.

### 6.2 Safety

FINDINGS: *"age-graded hazard labels, explicit required-supervision flags, adult-only steps
marked, disclaimers at materials-sheet time, hazardous experiments behind parent
acknowledgment."*

- `hazard` and `supervision` on every material (§1.4); `supervision='adult_performs'` marks
  a step the child must not do.
- The hazard label is **age-graded**: the same scissors are `sharp/adult_present` at K-1 and
  `sharp/none` at 6-8. Graded from the **course's** band, at build time.
- The disclaimer renders at materials-sheet time (§4.3) — not buried in terms of service.
- A course containing any material with `hazard ∈ {heat, chemical}` requires an explicit
  **parent acknowledgment** before its activity guides render. New
  `consent_records.consent_type = 'hazardous_activity'` (`01` §2 — the enum is currently
  `coppa_data | tos | privacy | health_strand6 | marketing`; this is an additive value),
  captured per course, versioned like the others. It gates the **guide**, never the
  **concept** — the concept is still taught on screen (§5).

### 6.3 Accreditation is a low bar, so design for honesty instead

FINDINGS: *"accredited homeschool/online programs accept parent attestation, logged hours
and portfolios for PE and arts. Utah allows credit by course completion OR competency
assessment (R277-705); homeschool families may set their own graduation criteria. **Design
verification for pedagogical honesty, not for an accreditation bar that is already
lenient.**"*

So the verification design in §3.4-§3.6 is **not** driven by what a transcript needs. It is
driven by not lying to ourselves about what a child has done. Concretely, the parent report
(`06` §6) renders three separate columns and never sums them:

| | assessed | attested | logged |
|---|---|---|---|
| meaning | graded on screen or in a debrief | a parent said it happened | minutes recorded |
| source | `exam_item_responses`, `user_progress` | `offscreen_completions.evidence='parent_attested'` | `offscreen_completions.minutes` |
| appears on transcript as | mastery | participation | hours |

That satisfies the lenient bar as a side effect, while making the difference legible to the
one person who cares whether it is real.

---

## 7. Acceptance criteria (tests)

**Classification**

- A standard whose text is *"measure and compare lengths"* classifies as physical
  (`real_experiment`), not `pure_dialogue`, despite containing "compare" — physical-first
  ordering (§2.2).
- Every classification carries a `source ∈ {verb, llm, human}`; a verb match never calls
  the LLM.
- A build where every LLM classification call fails still completes, with the unmatched
  concepts at `pure_dialogue`/`confidence=0.0`.
- `classify_enactment` writes both `structure.json` `enactment` blocks and
  `concept_enactment` rows, and they agree.

**Review guard**

- `publish` is refused for a course with any `channel ∈ PHYSICAL_CHANNELS` concept whose
  `reviewed_by` is NULL.
- `publish` is refused when a physical concept references a material with an empty
  `description` or `search_term`.
- `publish` succeeds for a course with only screen channels and no human enactment review.

**The hard rule — the load-bearing tests**

- A concept classified `real_experiment` with **zero** `offscreen_completions` rows can
  reach `_check_mastery_gate() == True` and `next_syllabus_item()` on on-screen evidence
  alone.
- A `checkpoint` exam's `score` and `passed` are byte-identical with and without an
  attestation present.
- No code path sets `enactment.blocks_progression = true`; grep-level assertion plus a
  schema-default test.
- An enrollment never enters `status='paused'` as a result of anything in this spec.

**Verification and FSRS**

- A debrief answer produces exactly one call to `get_socratic_grading_prompt` with
  `mastery_criteria` equal to the concept's `enactment.debrief.mastery_criteria`, and no
  new prompt template is introduced.
- The `doer_question` is absent from every payload sent to the client before the activity's
  completion window opens.
- A child who says they did not do the activity: no grade written, `concept_miss_streak`
  unchanged, `current_bloom_level` unchanged.
- `evidence='parent_attested'` results in **zero** calls to `FSRSEngine.calculate_memory`.
- An attestation on a concept with no prior review schedules a first review at ≤ 7 days.
- An attestation on a concept with an existing 30-day interval leaves the interval at 30
  days (never lengthens, never shortens an already-graded card).
- An attestation never produces `user_progress.status = 'mastered'`.

**Materials**

- Every rendered material includes `description` and `search_term`; a row with `url = NULL`
  renders without a link and without an error state.
- `resolve_materials` performs at most one batched network call per course and none per
  concept; running the build twice on the same brief performs zero additional calls for
  already-resolved items.
- No student-role endpoint or template ever emits a `course_materials.url`, price, or
  `cost_band`. (Assert at the API-response level, mirroring `11_IA…` §10's isolation
  tests.)
- Every `course_materials` row satisfies `substitute IS NOT NULL OR optional = 1`.
- A course with a `chemical`-hazard material renders no activity guide until a
  `consent_records` row with `consent_type='hazardous_activity'` exists — **and the
  concept is still teachable on screen** while it does not.

**Homework**

- A K-5 catalog course with off-screen work on a `screen_practice` concept fails the build
  lint.

---

## 8. Open questions

1. **O-1 — who authors the debrief questions, and are they any good?** §3.4's doer-only
   property is enforced by human review with a written rule. We have no test for it and no
   measurement. A debrief question answerable from the concept text silently converts this
   whole mechanism into ordinary questioning with extra steps.
2. **O-2 — `user_progress.status` enum** (§3.6, UNVERIFIED). Which values exist, and which
   queries would treat `'attested'` as mastery if we added it carelessly? `05` §5.1's
   checkpoint upsert is the first place to audit.
3. **O-3 — is `ATTESTED_INTERVAL_CAP_DAYS = 7` right?** It is our number. The direction
   (shorter for a weaker signal) follows from the evidence; the magnitude does not.
4. **O-4 — PE and studio arts as *courses*, not just concepts.** This spec makes physical
   concepts work inside academic courses. A 1.5-credit Fine Arts requirement
   (R277-700-6, via FINDINGS) is a whole course made of them, where §5's "never blocks"
   rule means the course can be completed having done nothing. Is that acceptable, or does
   a course whose channels are *entirely* physical need different handling — and if so,
   does that reintroduce blocking through the back door?
5. **O-5 — search-term quality.** §4.1 derives `search_term` deterministically from
   `description`. A bad search term is as useless as a dead link and we cannot test it
   offline. Should the reviewer confirm search terms as part of §2.3?
6. **O-6 — kit vendors.** FINDINGS names KiwiCo, MEL Science, Home Science Tools, Sonlight,
   Oak Meadow, Torchlight as models. We resolve generic search results, not vendor
   partnerships. Is a curated vendor list per material a better artefact than a search
   term, given it rots faster?
7. **O-7 — measuring fidelity before trusting the pessimism.** FINDINGS §Caveats says the
   ~10% figure may not transfer to self-selected homeschool families. `offscreen_completions`
   gives us the numerator and denominator. What sample size and what window before we act
   on it?
8. **O-8 — the 90-minute problem.** `sustained_bodily_practice` is not an activity, it is a
   habit over months. `offscreen_completions.minutes` records it, but nothing in this spec
   *schedules* it. Does practice belong in FSRS-adjacent scheduling, in the parent's weekly
   list, or nowhere?
