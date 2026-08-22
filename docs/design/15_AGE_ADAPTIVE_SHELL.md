# Design Spec 15 — The Age-Adaptive Student Shell

> The student UI, band by band: **how many tabs**, **how big the targets**, **how much
> text**, **what a child can operate unassisted**, and how accessibility is built into the
> one shell rather than forked into a second one.
>
> **Evidence base:** `docs/research/MODE_B_RESEARCH_FINDINGS.md` (*FINDINGS §…*), whose
> UI/UX section is the most directly actionable part of the research and also the part
> most heavily sourced to vendor design guidance rather than experiment. Distinctions are
> made at the point of use.
>
> **Composes with `11_IA_ONBOARDING_NOTIFICATIONS.md`, does not contradict it.** Spec 11
> owns the route map, the four-tab IA, the three shells, onboarding and notifications.
> This spec owns **how many of those four tabs a given child sees, and at what physical
> scale**. Where a number in spec 11 was set before the research arrived, this spec raises
> it and says so (§4.1).
>
> **Also depends on:** `02_GRADE_ADAPTATION.md` (bands), `12_YOUNG_LEARNER_DELIVERY.md`
> (the teaching loop this renders), `13_READING_FLUENCY_PLACEMENT.md`
> (`delivery_profile` — the second axis, §2.2), `01_DATA_MODEL.md` §7 (`accommodations`),
> `07_GAMIFICATION.md` §8 (band-scaled skins).
>
> **Grounding (verified against HEAD):** the token CSS is
> `services/web-ui/static/css/design-system.css` with `:root` token blocks at `:20, :111,
> :486` and a size scale `--font-size-sm|base|xl|2xl|3xl` (`:498-500`), spacing
> `--space-N`, weights `--font-weight-*`. Fonts are **self-hosted woff2** in
> `services/web-ui/static/fonts/` (IBM Plex Sans 400/400i/500/600/700, DM Sans 700,
> JetBrains Mono 400/600) wired by `static/css/fonts.css`. **The current `base.html` nav
> has drifted from spec 11's description — see §1.1.** Anything not read is marked
> **UNVERIFIED**.
>
> **Mode A is untouched.** Every rule here is scoped by `data-band` on `<body>` in the
> **student** shell (`shell_student.html`, spec 11 §2). The adult app renders through the
> existing `base.html` and carries no band.

---

## 1. Where this sits relative to spec 11

### 1.1 Correction: spec 11's nav description is stale

Spec 11 §1.2 states the current nav is *"(verified `base.html:169-185`): **Home, Courses,
Learn, Quiz, Review, Schedule, Status, Settings(⚙️)**"*. At HEAD the nav is at
`base.html:279-328` and reads **Home `/`, Courses `/courses`, Degree `/degree`, Library
`/library`, Progress `/progress`, Practice `/practice`, Test `/test`, Settings
`/settings`**.

Two things follow, and neither changes spec 11's conclusion:

- The **Practice merge already happened**: `/practice` is live, its active-state test
  covers `['/practice', '/quiz', '/review', '/schedule']` (`base.html:324`), and
  `static/js/practice.js` exists. Spec 11 §1.2 rows 4-6 are therefore partly implemented
  rather than pending.
- New adult surfaces (`/degree`, `/library`, `/progress`, `/test`) landed after spec 11 was
  written. **None of them belong in the student shell**, which strengthens rather than
  weakens spec 11 §2.5's removal list. They are added to it here.

Spec 11's route map should be re-verified against HEAD before implementation. Flagged as
O-1; this spec does not rewrite it.

### 1.2 The division of labour

| Owns | Spec |
|---|---|
| Route map, `/app/*` namespace, role guards, onboarding sequences, notification kinds | **11** |
| The four student tabs' *contents* and data sources | **11** §2 |
| **How many of those tabs render, per band** | **15** (this) §2 |
| **Physical scale, reading load, input modality, motion** | **15** §3-§5 |
| Which teaching loop runs inside Learn | **12** |
| Which delivery profile a child resolves to | **13** |

---

## 2. Tab count by band

FINDINGS §"UI/UX specifics": *"**Four tabs is ~three too many for a kindergartener.** K-1:
collapse to a **single 'Today' screen with one action** ('Tap to start'); everything else
adult-gated or hidden. Second tab at 2-3; full four-tab IA by 4-5. **The student shell
should itself be age-adaptive in tab count.**"*

This answers the brief's §7.8 challenge to spec 11's four tabs: four is right, **from 4-5
up**.

### 2.1 The table

| Band | Tabs | Which | What happened to the rest |
|---|---|---|---|
| **K-1** | **0 (no tab bar)** | A single **Today** screen with **one primary action**: a large "Tap to start" that resumes the current concept. | Learn is *inside* the action. Practice is folded into the lesson (due cards are surfaced by the tutor, not by a tab). My Stuff is adult-gated. Catalog is adult-gated. |
| **2-3** | **2** | **Today** · **Practice** | Learn remains inside Today's action. My Stuff and Catalog are adult-gated. |
| **4-5** | **4** | Today · Learn · Practice · My Stuff | Full spec 11 §1.2 IA. Catalog reachable from Today/Learn, not a tab (unchanged from 11 §1.2). |
| **6-8** | **4** | as 4-5 | |
| **9-12** | **4** | as 4-5 | |

Rationale for **which** tab arrives second, at 2-3: Practice, not Learn. Learn is where the
child already is when they tap the primary action, so a Learn tab at 2-3 is a second door
to the room they are standing in. Practice is a genuinely different intent ("do the due
cards") and is the one a beginning reader can form on their own.

**Threshold that would change this** (FINDINGS §"Thresholds that would change these
recommendations"): *"K-1 single-action screen under-challenges children who handle two
choices → add the second tab earlier."* Instrumented in §7.

### 2.2 The second axis: `delivery_profile` does **not** move the tab count

`13_READING_FLUENCY_PLACEMENT.md` §1 separates *what is taught* (`grade_band`) from *how it
is delivered* (`delivery_profile`). This spec keys the two axes to different properties,
which resolves spec 13's O-7:

| Property | Keyed on | Why |
|---|---|---|
| **Tab count, navigation depth, autonomy** | `grade_band` | These are about age, self-direction and what a child forms intentions about — not about decoding rate. A twelve-year-old who reads at 45 WCPM is still a twelve-year-old and must never get the kindergarten screen. |
| **Reading load, TTS default, input modality, text size floor** | `delivery_profile` | These are exactly what fluency governs. |
| **Tap target size, choice count, scrolling, motion** | `grade_band`, raised by `delivery_profile` when it is lower than the band default | Motor precision tracks age; a `pre_reader` at any age also benefits from fewer simultaneous choices. |

Concretely: `data-band` **and** `data-profile` are both attributes on `<body>`; the CSS
layer keys on both. Spec 11 §2 currently specifies only
`data-band="K-2|3-5|6-8|9-12"` — that value list is stale post-re-banding and must become
`K-1|2-3|4-5|6-8|9-12`, with the legacy names mapped server-side through
`LEGACY_GRADE_BANDS` (`prompts.py:463`) before the attribute is written. Writing a legacy
band straight into the DOM would give a K-2 student no matching CSS at all.

### 2.3 What K-1 actually renders

One screen. Three states, never more:

```
┌──────────────────────────────────┐
│  [Helga icon]  Helga is a        │   ← AI disclosure, spoken + shown (12 §6.1)
│                computer helper.   │
│                                   │
│         ┌───────────────┐         │
│         │               │         │   ← ONE control. ≥76 CSS px tall (§4.1).
│         │  ▶  Let's go  │         │     Label ≤ 3 words. Spoken on load.
│         │               │         │
│         └───────────────┘         │
│                                   │
│              🔊 (replay)          │   ← the only secondary control
└──────────────────────────────────┘
```

| State | Primary action |
|---|---|
| has an active concept | "Let's go" → resume at `enrollments.current_concept_uid` |
| nothing active | "Let's go" → the recommended first concept (spec 11 §5.2 step 4) |
| nothing available | a spoken "Ask a grown-up to pick something" + no action. **Never a dead end** (spec 11 §5.2). |

Everything else on spec 11's Today card list (§2.1: due-today strip, daily quest, streak
chip, empty-state CTA) is **removed at K-1**. The due queue is not deleted — the tutor
surfaces due cards inside the lesson. The quest and streak are removed because
FINDINGS §"Gamification genuinely inverts by age" says to age-gate extrinsic mechanics
**off** at K-1; that is spec 07's decision to make (`12` §9 O-5) and this spec renders
whatever spec 07 resolves, but the default here is off.

---

## 3. What a child can operate unassisted

Brief §7.1-§7.2. FINDINGS §"UI/UX specifics" gives the numbers; the autonomy column comes
from FINDINGS §"Per-band parameters".

| | K-1 | 2-3 | 4-5 | 6-8 | 9-12 |
|---|---|---|---|---|---|
| **Autonomy** | adult-launched, single-action screen | adult-launched, 1-2 choices | largely independent | independent | fully independent |
| **Simultaneous on-screen choices** | **1-3** | **≤4** | ≤6 | unconstrained | unconstrained |
| **Scrolling** | **none** — everything fits one viewport | minimal (one screen-height of overflow, max) | normal | normal | normal |
| **Text input** | **none** | optional single word | yes | yes | yes |
| **Primary input** | tap + voice-as-selector (`12` §3.2) | tap, some typing | typing + tap | typing | typing |
| **Login** | parent-launch only (no PIN) | avatar + 4-digit PIN | PIN | PIN | PIN |
| **Icons** | **literal only** | literal | literal preferred | conventional allowed | conventional |
| **Reading required to operate** | **none** | short labels, always narrated | labels | full | full |

Sources and their weight, stated honestly:

- The **1-3 / ≤4 choices**, **no scrolling**, **no text input below grade 2**, and
  **literal icons** rules come from FINDINGS §"UI/UX specifics", attributed there to
  Nielsen Norman Group's children's UX research — **practitioner design guidance, not
  experiment.** They are specific and testable, which is what makes them usable; they are
  not peer-reviewed effect sizes.
- **Typing is not reliable before ~grade 2 and not primary until ~grade 4-5**
  (FINDINGS §"The binding constraints…") — this one is a developmental claim, and it is why
  the input row changes where it does.
- *"Children treat anything button-like as tappable and abandon quickly if taps don't
  respond"* (FINDINGS §"UI/UX specifics") produces two hard rules: **every element that
  looks like a button is one**, and **every tap produces visible feedback within 100 ms**,
  independent of whether the underlying request has returned. The FSM turn takes seconds
  (brief §9.1); the acknowledgement must not.

### 3.1 Adult-launched, concretely

K-1 and 2-3 are *"adult-launched"*. Spec 11 §4.2 already specifies the mechanism —
`POST /api/launch_child` mints a student session from a parent session — and §5.2 step 1
already contemplates *"If no PIN set, login is parent-launch-only"*. This spec makes that
the **default for K-1**, not an option: a five-year-old should not have a credential.
Spec 11 O-7 asks whether an avatar-only kiosk mode is acceptable on a trusted family
device; this spec's answer is that parent-launch is the K-1 default and kiosk mode is the
family's opt-in, resolved by spec 03/08.

---

## 4. Physical constraints — the numbers, in the units we actually ship

### 4.1 Tap targets

FINDINGS §"UI/UX specifics": *"Tap targets **≥ 2 cm × 2 cm** for young children (NN/g) —
roughly **60-75+ pt**, well above WCAG 2.2 AA's 24×24 CSS px floor and above Apple's 44 pt
/ Material's 48 dp."*

**The unit in that sentence is ambiguous and the ambiguity is a factor of 1.33.** 2 cm at
the CSS reference of 96 px/inch is **≈ 76 CSS px**; 60 pt is **80 CSS px**; 60 CSS px is
1.6 cm. We ship CSS pixels, so this spec states everything in CSS px and anchors on the
physical measurement rather than the point value:

```css
:root                        { --target-min: 44px; }   /* WCAG 2.2 AA floor + Apple 44pt */
[data-band="4-5"]            { --target-min: 56px; }
[data-band="2-3"]            { --target-min: 64px; }
[data-band="K-1"]            { --target-min: 76px; }   /* ≈ 2 cm — the NN/g figure */
[data-profile="pre_reader"]  { --target-min: max(var(--target-min), 64px); }
[data-accommodation~="larger_targets"] { --target-min: max(var(--target-min), 76px); }
```

Every interactive element in the student shell sets `min-block-size` and `min-inline-size`
to `var(--target-min)`. **This raises spec 11 §8's "≥56px for K-2 primary targets"**, which
was set before the research arrived and sits below the NN/g figure for a five-year-old.
Spec 11's 56px survives as the 4-5 value.

`accommodations.larger_targets` (`01_DATA_MODEL.md` §7) is honoured at every band — it is
the same token, so a 9-12 student with the accommodation gets the K-1 target size without
any of the K-1 layout.

### 4.2 Type and spacing

FINDINGS: *"**Body text** ≥16px, **18px+** for the youngest; line height ≥1.5; adjustable
spacing (WCAG 1.4.12)."*

| Token | 6-8 / 9-12 | 4-5 | 2-3 | K-1 | `pre_reader` override |
|---|---|---|---|---|---|
| body `--font-size-base` | 16px | 17px | 18px | **20px** | ≥18px |
| `line-height` | 1.5 | 1.5 | **1.6** | **1.6** | ≥1.6 |
| `letter-spacing` | 0 | 0 | 0.01em | 0.02em | ≥0.01em |
| `word-spacing` | 0 | 0 | 0.05em | 0.08em | ≥0.05em |
| max line length | 75ch | 65ch | **45ch** | **32ch** | ≤45ch |

`line-height ≥ 1.5` is WCAG 1.4.12 (Text Spacing) and applies at **every** band, including
Mode A — it is an AA requirement, not a young-learner nicety. The spacing values above the
minimum are the "adjustable spacing" affordance pre-applied.

**Implementation note:** these are overrides on the existing token scale
(`design-system.css:498-500` defines `--font-size-3xl|2xl|xl`), added as a `[data-band]`
layer, **not a fork**. Spec 11 §7 already requires this (*"a `data-band` + `body.kid` CSS
layer over the existing Alpine tokens… It is a *theme variant* (token overrides) not a
separate stylesheet, so dark/light parity is inherited"*) and this spec supplies the
values.

### 4.3 Fonts — and why not OpenDyslexic

FINDINGS: *"**Dyslexia fonts:** evidence for OpenDyslexic/Dyslexie is **thin**. Use a
well-designed sans-serif with generous x-height and distinct b/d/p/q (e.g. Atkinson
Hyperlegible) + always-available TTS."*

Verified: the app self-hosts IBM Plex Sans (400/400i/500/600/700), DM Sans 700 and
JetBrains Mono in `static/fonts/*.woff2`, wired by `static/css/fonts.css`. There is **no**
Atkinson Hyperlegible and no OpenDyslexic.

Decision:

- **Do not ship OpenDyslexic.** The evidence is thin and shipping it would be a visible
  accessibility gesture with no measured benefit — the worst kind.
- **Do ship Atkinson Hyperlegible** as an additional self-hosted woff2 (it is
  open-licensed), offered as a per-student font choice in My Stuff / parent settings
  (`students.settings.font`, already in the column comment at `01` §2). Offline constraint
  respected: it is a bundled asset, not a webfont fetch. **UNVERIFIED:** licence terms and
  file size have not been checked against the repo's asset budget.
- **IBM Plex Sans remains the default** at every band. It has the generous x-height and
  distinguishable `b/d/p/q` the finding actually asks for; the named font is an example in
  the source, not a requirement.
- **TTS is always available** at every band, on every tutor message — that is the part of
  the finding with real support, and it already exists (`playMessageTTS`,
  `session.js:284`).

### 4.4 Icons

*"Icons must be **literal, not abstract**"* (FINDINGS §"UI/UX specifics").

- K-1 / 2-3: an icon depicts **the thing**, not a metaphor. Practice is a picture of cards,
  not a dumbbell. There is no hamburger, no gear, no abstract glyph, no chevron-only
  affordance.
- Every icon at K-1 and 2-3 carries a **text label** and is narrated on first render.
  An icon alone is not a control for a pre-reader.
- `static/css/icons.css` exists (18.2 KB) and is the place the band variants live.
  **UNVERIFIED:** whether its icon set is literal enough for young bands, or whether new
  assets are needed. If new assets are needed they must be simple line/solid glyphs —
  **not illustrated characters**, which brief §9.7 puts out of scope.

### 4.5 No scrolling at K-1 — what that costs

"No scrolling" is a real constraint, not a preference: a K-1 screen that overflows has a
bug, not a scrollbar. Consequences:

- The Today screen (§2.3) has a fixed three-element budget.
- **The lesson view is the hard case.** A Socratic chat grows. At K-1 the resolution is
  that the chat renders **only the current tutor turn and the response widget** — the
  history is not shown. The tutor turn is ≤15 words (`prompts.py:398`) and one sentence, so
  it fits. History is available to the **parent** (`12` §6.2), not to the child.
- At 2-3 the chat shows the current turn plus the previous one, with overflow allowed to
  one screen-height.
- From 4-5 the existing `#chat-stream` scroll behaviour (`session.js:354`
  `_attachChatScrollTracker`, plus the jump-to-bottom button) applies unchanged.

This is a genuine departure from the adult chat metaphor and should be treated as such in
review: it is closer to a picture book turning pages than to a transcript.

---

## 5. TTS and narration in the shell

`GRADE_BAND_PROFILES[band]['tts_default']` is `True` for K-1 and 2-3 (`prompts.py:406,
415`) and is **currently consumed by nothing** — verified: `speak()` is
*"text-only, no TTS"* (`fsm_logic.py:741`), `play_sound`/`stop_audio` are no-ops (`:1035`,
`:1039`), and all speech is a per-message client button (`session.js:284`). See
`12_YOUNG_LEARNER_DELIVERY.md` §5.1.

**The shell is where `tts_default` becomes real.** Rules:

| Band / profile | Behaviour |
|---|---|
| K-1, or `delivery_profile='pre_reader'` | **Auto-play** every tutor turn and every control label on first render. Replay control always present (§2.3). |
| 2-3, or `transitional` | Auto-play tutor turns; labels narrated on first render only. |
| 4-5+ | Manual, per message — today's behaviour, unchanged. |
| `accommodations.read_aloud_default = 1` | Auto-play at every band. |

Auto-play requires a user gesture in most browsers. The session's first gesture is the
"Let's go" tap (§2.3), which is sufficient to unlock audio for the session — so the flow
must be built to unlock the `Audio` context on that tap rather than on the first tutor
turn. Narration is segmented and highlighted per `12` §5.2-§5.3, including the
8-word-per-segment transient-information cap.

**Barge-in already exists and is correct:** `startVoiceRecording()` pauses in-flight TTS
(`session.js:1075-1076`). Keep it.

---

## 6. Accessibility — one mode, built better

FINDINGS §"Open questions we had not asked": *"**Accessibility/neurodivergence: build into
the one mode, don't fork.** Dyslexia → legible sans-serif + spacing + TTS (already have).
ADHD → shorter segments, movement breaks, fewer simultaneous choices. Autism →
predictability, consistent layout, previewable session structure, no surprising changes.
These are 'the same mode built better'."*

**Design decision: there is no accessibility mode.** Every mechanism below is either always
on, or is a token/flag that composes with the band layer. Nothing is behind a "special
needs" toggle, because a fork is a second surface that rots.

| Need | What the shell does | Always on? | Backing |
|---|---|---|---|
| **Dyslexia** | legible sans-serif default, adjustable spacing (§4.2), Atkinson Hyperlegible option (§4.3), TTS always available, and — decisively — narration alongside text rather than instead of it | spacing + TTS: **yes** | FINDINGS §"Cognitive load": Knoop-van Campen et al. (2018) found a **reversed** redundancy effect in 11-year-olds with dyslexia — spoken+written *helped* |
| **ADHD** | short segments (`12` §2.4: rotate every 5-8 min at K-1, 8-10 at 2-3), fewer simultaneous choices (§3), one primary action per screen, movement-break prompts between segments, `reduced_distraction` strips gamification flourish | segments + choice caps: **yes**. `accommodations.reduced_distraction`: opt-in | FINDINGS §"Open questions…"; the accommodation column already exists (`01` §7) and `07_GAMIFICATION.md` §8.2 already honours it |
| **Autism** | **consistent layout** (the same element in the same place every session), **previewable session structure** ("we'll do three things today" shown before starting), **no surprising changes** — including the §4 rule that a `delivery_profile` change never takes effect mid-session (`13` §4.3) | **yes, all of it** | FINDINGS §"Open questions…" |
| **Low vision / motor** | `--target-min` (§4.1), `larger_targets` accommodation, visible `:focus-visible` ring (spec 11 §8 confirms it exists in FE1.6 and must be kept), keyboard operability of all nav | **yes** | WCAG 2.1 AA (spec 11 §8 / B25.1) |
| **Vestibular** | `prefers-reduced-motion` **and** `students.settings.reduced_motion` both suppress confetti, streak pulses and path animation | **yes** | spec 11 §8, already specified |
| **ELL** | TTS + text is the same mechanism as the dyslexia row; `accommodations.simplified_language` already threads into generation and theming (`05` §7) | TTS: **yes** | FINDINGS §"Open questions…": *"the redundancy-reversal evidence means TTS + highlighting already serves ELLs; consider L1 support for instructions"* — L1 support is **not** designed here (O-4) |

Two structural commitments that make "don't fork" true rather than aspirational:

1. **The accommodation flags are CSS tokens, not branches.** `accommodations`
   (`01` §7) renders as `data-accommodation="larger_targets reduced_distraction"` on
   `<body>`, and the token layer reads it (§4.1 shows the pattern). No template forks.
2. **The `aria-live` contract survives the refactor.** Spec 11 §8 verified
   `#chat-stream` carries `role="log" aria-live="polite"` (`learn.html:71`) and requires it
   be preserved. The K-1 single-turn chat (§4.5) **removes DOM history**, which is exactly
   the kind of change that silently breaks a live region — the current turn must still be
   inserted into a persistent live-region container rather than the container being
   replaced.

### 6.1 Predictability is a young-learner feature, not only an autism feature

The "previewable session structure" rule is worth stating generally: at K-1 and 2-3 the
session opens by **saying what will happen** ("We'll learn one thing, then you'll try it")
and closes by **saying what happened**. That composes with `12` §2's TELL→SHOW→TRY→CHECK
loop, which is already a fixed, announceable shape. A predictable loop is easier to
preview, which is a reason to prefer a fixed loop over an adaptive one at these ages
independent of the direct-instruction argument.

---

## 7. Instrumentation — the thresholds that would change this

FINDINGS §"Thresholds that would change these recommendations" names one that is this
spec's business. Instrument it rather than guessing later:

| Signal | Logged | Decision it feeds |
|---|---|---|
| K-1 sessions where the child taps the primary action **without adult intervention**, and where the session then proceeds | `activity_log` (`01` §2.1) | *"K-1 single-action screen under-challenges children who handle two choices → add the second tab earlier."* |
| Taps on non-interactive elements at K-1/2-3 | client event, aggregated | *"Children treat anything button-like as tappable"* — a hot spot means something looks like a control and is not |
| Time from tap to visible feedback (p95) | client timing | the 100 ms rule (§3) |
| Abandonment within 10 s of a screen render, by band | `activity_log` | *"abandon quickly if taps don't respond"* |

**No student-facing telemetry is ever shown to the child**, and none of it is a
gamification signal (`07` §9.3).

---

## 8. Acceptance criteria (tests)

**Tab count**

- A `K-1` student session renders **no tab bar** and exactly **one** primary action on
  `/app/today`.
- A `2-3` student session renders exactly **two** tabs (Today, Practice).
- A `4-5`, `6-8` or `9-12` student session renders exactly **four** tabs (Today, Learn,
  Practice, My Stuff) — the existing spec 11 §10 assertion, unchanged.
- No student session at any band renders Status, Settings, billing, course-create,
  `/degree`, `/library`, `/progress` or `/test` (§1.1).
- A `K-1` student navigating directly to `/app/me` or `/app/catalog` is redirected to
  `/app/today` (adult-gated, not 403 — a five-year-old must not meet an error page).

**Band attribute**

- `<body data-band>` is one of `K-1|2-3|4-5|6-8|9-12` — **never** a legacy value. A student
  row carrying `'K-2'` renders `data-band="K-1"` (mapped through `LEGACY_GRADE_BANDS`,
  `prompts.py:463`).
- `<body data-profile>` is one of `pre_reader|transitional|reader` and is present at every
  band.

**Physical constraints**

- Every interactive element in the student shell has computed
  `min-height ≥ var(--target-min)` for its band: 76px at K-1, 64px at 2-3, 56px at 4-5,
  44px at 6-8/9-12.
- A `9-12` student with `accommodations.larger_targets = 1` gets 76px targets **and** four
  tabs (the two axes are independent).
- Computed body `font-size` ≥ 18px at K-1 and 2-3, ≥ 16px at every band.
- Computed `line-height` ≥ 1.5 at **every** band including the adult shell (WCAG 1.4.12).
- At K-1, no student route produces a document whose scroll height exceeds the viewport at
  a 375×812 and a 1280×800 viewport.
- At K-1, no screen presents more than 3 simultaneous choices; at 2-3, no more than 4.
- At K-1 there is **no** `<input type="text">` or `<textarea>` in any rendered student
  template.
- Every rendered tap target produces a visual state change within 100 ms of `pointerdown`,
  measured independently of network latency.

**TTS**

- A K-1 session auto-plays the first tutor turn after the primary-action tap, with no
  further gesture.
- A 4-5 session does **not** auto-play (regression guard: today's behaviour must not change
  for older bands).
- `accommodations.read_aloud_default = 1` auto-plays at 9-12.

**Accessibility**

- `#chat-stream`'s live region survives the K-1 single-turn render: the container carrying
  `role="log" aria-live="polite"` is not replaced between turns, only its children.
- All nav, the account switcher and the bell dropdown are keyboard-operable with a visible
  focus ring, at every band (spec 11 §10, unchanged).
- `prefers-reduced-motion: reduce` **or** `settings.reduced_motion = 1` suppresses every
  animation in the student shell.
- No template branches on an accessibility flag; accommodations appear only as
  `data-accommodation` tokens (assert by grepping the student templates for
  `accommodations.` in conditionals).

**Mode A**

- The adult shell (`base.html`) carries no `data-band` and no `data-profile`, and its
  computed target sizes and font sizes are unchanged by this spec — except
  `line-height ≥ 1.5`, which is an AA fix and applies everywhere.

---

## 9. Open questions

1. **O-1 — spec 11's route map is stale** (§1.1). `/degree`, `/library`, `/progress`,
   `/test` post-date it and `/practice` already exists. Re-verify spec 11 §1.2 against HEAD
   before building; the conclusions hold but the row-by-row disposition does not.
2. **O-2 — is Practice the right second tab at 2-3?** §2.1 argues it over Learn on
   intent-formation grounds. That is reasoning, not evidence; FINDINGS says only *"Second
   tab at 2-3"* without saying which.
3. **O-3 — K-1 chat with no history.** §4.5 removes the transcript from the child's view to
   honour "no scrolling". This is a large departure and might be worse than a short
   scrollable history. Testable with children; untestable by us.
4. **O-4 — L1 (home-language) support for instructions.** FINDINGS raises it for ELL
   learners; we have 14 Kokoro voices, all American English (`tts_server.py:37-41, :56-59`
   — `KOKORO_LANG='a'`). Genuine L1 support would need other voice packs and translated
   instruction strings, both out of current scope. Say so publicly rather than implying
   coverage.
5. **O-5 — Atkinson Hyperlegible licence and asset budget** (§4.3, UNVERIFIED).
6. **O-6 — are the existing icons literal enough?** (§4.4, UNVERIFIED). If not, who draws
   the replacements, given illustrated characters are out of scope (brief §9.7)?
7. **O-7 — the 2 cm / 60 pt / 76 px ambiguity** (§4.1). We anchored on the physical
   measurement. If the NN/g source means 60 **pt** (≈80 px), K-1 targets should be 80px and
   even less fits on one non-scrolling screen. Worth resolving against the primary source
   before building.
8. **O-8 — movement breaks** (§6, ADHD row) are asserted but not designed: who prompts
   them, does the FSM pause, and does a break count against session time? Interacts with
   `14_OFF_SCREEN_AND_PHYSICAL.md` §3.3's activity guides.
