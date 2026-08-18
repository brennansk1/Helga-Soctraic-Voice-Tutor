# AI University — design

_Draft, 2026-08-18. Extends the preset system from single courses to programs._

## The one idea that makes this feasible

**A Course is the atomic unit. Everything larger is a Program: an ordered set of
courses with prerequisite edges.**

That collapses five separate features into one abstraction plus a planner:

| user picks | Program of |
|---|---|
| College Course, 1 semester | 1 course |
| College Course, 2 semesters | 2 courses (Linear Algebra I → II) |
| Associate | ~20 courses |
| Bachelor's | ~40 courses |
| Seminar (day/weekend/week) | 1 course, small scope |

A two-semester sequence is **not a stretched course** — it is two courses with a
prerequisite edge. That distinction matters: stretching one course to twice the
length is exactly the "spread too thin" failure worried about, whereas Linear
Algebra II is a genuinely different course with its own syllabus. Treating both
as Programs means the "cobble together curriculum" logic is written once.

## Why programs must be planned cheaply and built lazily

Measured on this hardware: **~2 min per concept**, and a College Course preset
is **30 concepts ≈ 60 min**.

| program | courses | concepts | build time if eager |
|---|---|---|---|
| 2-semester | 2 | 60 | ~2 h |
| Associate | ~20 | 600 | **~20 h** |
| Bachelor's | ~40 | 1,200 | **~40 h** |

Generating a degree up front is 40 hours of compute for an artifact representing
four years of study, most of which the learner will never reach. So:

* **The Program plan is cheap** — research + a small number of LLM calls
  producing course specs, titles, prerequisite edges and term placement. Target
  well under a minute. This is what the learner sees immediately.
* **Courses materialise on enrolment** — ~60 min each, built while the learner
  is working through the previous one.

Storage is a non-issue (1,200 concepts ≈ 5 MB of markdown). The binding
constraint is not compute, it is that a degree is a multi-year object.

## Degree structure: template first, research second

The instinct was to have SearXNG find real associate/bachelor's syllabi and plan
from those. **Measured today, SearXNG's engine pool is exhausted after ~14
queries** (see `docs/RESEARCH_SOURCES.md`) — it cannot be the backbone of a
planner that needs dozens of lookups. Building degree planning on the least
reliable source would make the whole feature as fragile as its weakest input.

Invert it. Degree *shape* is stable public knowledge and belongs in a template:

```
Associate  60 credits  ~20 courses: gen-ed 7 · core 9  · electives 3 · capstone 1
Bachelor's 120 credits ~40 courses: gen-ed 12 · core 16 · electives 9 · capstone 3
```

**Verified against published sources rather than assumed** (2026-08-18):

| | credits | courses | terms | years |
|---|---|---|---|---|
| Associate | 60 | ~20 | 4 | 2 |
| Bachelor's | 120 | ~40 | 8 | 4 |

A semester is ~15 weeks; a course is 3–4 credit hours; a full-time load is 12–18
credits, i.e. **4–6 courses per term**.

An earlier draft used 30 courses for an associate. The arithmetic rules it out:
at 4–6 courses per term over two terms a year, 30 courses is three years, and an
associate is by definition a **two-year** degree. 20 courses ÷ 5 per term ÷ 2
terms = 2 years exactly, and 40 ÷ 5 ÷ 2 = 4 years for a bachelor's. The two
independent facts agree, which is the check worth trusting.

### Module count per course is already right

A module is conventionally ~2 weeks of a semester, giving **7–8 modules** per
15-week course (some schools instead use three 5-week blocks).

Helga's College Course preset yields `concepts_per_module 5` and
`total_concepts_approx 30` — **6 modules of 5 concepts**. That sits inside the
real range without any change. The existing calibration is sound; only the
program layer above it is new.

Research then supplies **which subjects fill each slot**, as evidence, using the
tiered matching already designed for skeletons (at-level match → copy; one level
above → evidence; nothing → synthesise).

The pay-off: **the same code path serves "Associate in Nursing" and "Associate
in Dungeons & Dragons."** One has real syllabi to match against and one does
not, but neither changes the algorithm — the D&D case simply runs with zero
external matches. No special "custom degree" branch to write or maintain.


## The taxonomy, with every level anchored to a verified number

Helga has five structural levels below a program. For an "as close to the real
thing as possible" system, each needs a defensible real-world analogue rather
than a plausible-sounding one.

| Helga level | Real analogue | Count | Anchor |
|---|---|---|---|
| **Program** | Degree | — | Associate 60 cr / ~20 courses / 2 yr · Bachelor's 120 cr / ~40 courses / 4 yr |
| **Course** | 3-credit semester course | 20 or 40 per program | 15 weeks · 45 sessions of 50 min · **135 h total student work** |
| **Module** | 2-week block | **6–8** per course | a module is conventionally ~2 weeks of a term |
| **Unit** | ~1 week / topic cluster | 2–3 per module | subdivision of the block |
| **Lesson** | one class session | **~45** per course | 3 × 50 min per week × 15 weeks |
| **Concept** | one textbook section / learning objective | **see below** | measured from OpenStax |

The Carnegie definition fixes the top of this: 1 credit hour = 1 hour of
instruction + 2 hours of preparation per week for 15 weeks, so a 3-credit course
is 45 contact hours inside **135 hours of total student work**.

### The concept count is the one number that is currently wrong

Counting *teachable* sections in real OpenStax books (front matter, key terms,
chapter summaries and review exercises excluded):

| book | chapters | teachable sections |
|---|---|---|
| Prealgebra 2e | 11 | **80** |
| College Algebra 2e | 9 | **77** |
| Calculus Volume 1 | 6 | **51** |
| Principles of Economics 2e | 34 | 275 _(a two-semester book — micro + macro)_ |

So **a one-semester course covers roughly 50–80 teachable sections.**

Helga's College Course preset produces **30 concepts** (6 modules × 5). If a
concept is the analogue of a textbook section — and it is: both are one
self-contained idea with its own content and its own review cards — then the
current preset delivers **under half a real semester course**.

That is the gap between "a course about Linear Algebra" and "Linear Algebra I".
It is invisible today because there is nothing to compare against; it becomes
glaring the moment the system claims to be a university.

**Recommendation: a semester course should target ~60 concepts, not 30.** Costs,
stated plainly:

| | concepts | build/course | bachelor's (40 courses) |
|---|---|---|---|
| today | 30 | ~60 min | ~40 h |
| parity | ~60 | **~2 h** | **~80 h** |

80 hours is only tolerable because of lazy materialisation — it is ~2 hours per
course, built while the learner works through the previous one, spread across
four years of study. Eagerly it would be indefensible.

Keeping 30 is also defensible, but then the honest label is "condensed course",
not a semester equivalent, and a degree of them is not a degree's worth of
material. Worth deciding deliberately rather than inheriting.

### One number deliberately left open

Whether 60 concepts *equals* 135 hours of student work is unmeasured. A Socratic
dialogue on one concept plus its review is plausibly 20–40 minutes, which puts a
60-concept course at 20–40 hours — well under 135. One-to-one tutoring is
genuinely more time-efficient than lecture-plus-homework, so some of that gap is
real rather than missing material; how much is an empirical question this
project has not answered. **Measure session length before claiming hour
equivalence anywhere in the UI.** Coverage parity (sections) is defensible now;
workload parity (hours) is not yet.

### Measurement caveat

Biology 2e reported 8 chapters / 46 sections above, which understates it — it is
a unit-grouped book and the counter collapsed its units. Chapter counts for
unit-grouped books in that table are not reliable; the single-semester figures
(Prealgebra, College Algebra, Calculus 1) are.


## Registration: choice that is also cost control

Offer N candidate courses per term from the plan's elective slots; the learner
picks; the choice locks. This gives the real-university feel that was asked for,
and it means **only chosen courses are ever built** — the elective slots that
were not picked cost nothing. The engagement mechanic and the cost control are
the same mechanism.

Locking also protects coherence: prerequisite edges are only meaningful if the
sequence is stable.

**The planner must emit a validated DAG.** Calc 1 → 2 → 3 ordering has to be
correct or the program is incoherent in a way that is invisible until a learner
hits a course they cannot follow. Cycle detection and "every prerequisite
appears in an earlier term" are cheap checks and belong in the planner, not in
review.

## Assessment: the validity problem, and the thing we can do that universities cannot

Gating course completion on a test is right. But **a tutor that writes the exam
from the content it generated is grading its own homework** — the exam will
reliably test exactly what was taught, in the framing it was taught, which
inflates every pass. That is a credibility hole in anything calling itself a
degree.

Four mitigations, in order of value:

1. **Write exam items from the source textbook section, not from generated
   prose.** Now viable — OpenStax gives real chapters *and* sections, so the
   assessment can be grounded in the same authority the course was, without
   being downstream of the model's own wording.
2. **Hold out material.** Reserve some sections from teaching, use them for
   transfer questions. Tests whether the concept generalised or the phrasing was
   memorised.
3. **Require retention, not just performance.** A pass should mean *demonstrated
   after a delay*, which is precisely what FSRS already measures. This is worth
   dwelling on: a real university tests you once in December and never checks
   again. Helga can require that you still have it in March. **Retention over
   time is a stronger credential than a one-shot exam**, and it is a capability
   the incumbent format structurally cannot offer.
4. **Cross-course review.** Calculus II should resurface Calculus I cards. This
   needs the FSRS queue to span courses rather than sit per-course — worth
   checking against the current schema before committing to it.

## The conceptual-sufficiency disclaimer

Endorsed, and worth making sharper than a single flag. Three tiers, not two:

| tier | example | what the learner is told |
|---|---|---|
| **Conceptual-complete** | philosophy, pure maths, history, literary theory | nothing — text tutoring genuinely suffices |
| **Conceptual + practice required** | programming, statistics, chemistry, spoken languages | names the specific missing practice: "you will need a compiler / a lab / a speaking partner" |
| **Fundamentally embodied or safety-critical** | surgery, welding, cooking, electrical work, aviation, clinical care | states plainly that the practical component cannot be taught here, and that this is theory only |

Two refinements that matter:

* **Name what is missing, specifically.** "This course cannot give you titration
  technique or lab safety practice" is honest and useful. A generic "AI may be
  inaccurate" banner is legal noise that everyone clicks through, and it trains
  learners to ignore the one warning that is real.
* **For tier 3, consider refusing the degree framing** and offering a "theory
  companion" instead. An "Associate Degree in Welding" delivered as text is a
  claim the system cannot honour; the same content honestly labelled as
  background theory is genuinely valuable.

This is a credibility feature before it is a legal one.

## The second disclaimer: not enough subject to fill the scope

A distinct failure from the one above, and it arrives with the degree tiers: a
**master's in D&D lore**. Nothing about it is un-teachable conceptually — tier 1
on the table above — but the subject cannot sustain 40 courses. The material
runs out long before the structure does.

Left unchecked, this produces the project's documented failure at program scale.
The model will not refuse; it will happily emit 40 course titles and pad them,
which is the "50% of concepts hollow" problem multiplied by forty.

**Ground this in measured evidence, not in asking the model.** The research
brief already counts exactly what is needed:

* `structural_sources` — how many real syllabi exist for the subject
* `chapter_count` — how much structure they contain between them
* whether any textbook matched at all, and at what level

A subject with one 12-chapter Wikibook and no OpenStax match cannot fill 40
courses, and that is a fact about the evidence rather than a judgement call. The
LLM is a useful second opinion on *why* — a niche subject versus a genuinely
young field — but the trigger should be the count. Asking a model "is there
enough material?" invites the same optimism that generates the padding.

**`degraded` must gate this warning.** If the brief came back degraded, thin
evidence means *we could not look*, not *the subject is thin*. Telling a learner
their subject is too small when Wikimedia was throttling would be the
absent-vs-zero error surfaced directly to a user, which is the worst place for
it. No claim about subject size unless the lookups actually completed.

**Offer the right-sized alternative rather than only a warning.** A warning that
can only be accepted or cancelled pushes people to accept. Three options:

1. **Resize** — "the evidence supports about 6 courses; build a certificate
   instead of a master's" (recommended, and pre-selected)
2. **Broaden** — "as a master's in Game Design with D&D as the through-line"
3. **Proceed anyway** — explicitly acknowledging that later courses will be
   thin, so the learner is choosing padding rather than discovering it

Option 1 is usually the honest answer and produces something genuinely good,
which is a better outcome than either a refusal or forty hollow courses.

## On not selling it

The assumption was that the research service is what creates exposure. That is
worth inverting, because it is mostly backwards:

* **OpenStax (CC-BY) and Wikibooks (CC-BY-SA) are explicitly licensed for
  reuse, including commercially**, with attribution and — for Wikibooks —
  share-alike. The open-textbook path is the *safe* part, and it is now the
  primary source of structure.
* **The risky parts are narrower than the whole service:** SearXNG scraping
  engines against their terms, and hydrating content from arbitrary copyrighted
  pages. Both are droppable without losing the textbook spine.
* **The largest exposure is vocabulary, not code.** "University", "degree",
  "credits" imply accreditation, which is regulated in most jurisdictions
  regardless of quality. "Program", "track", "study plan" describe the same
  artifact without the claim.

So if distribution ever became interesting, the retreat is much smaller than
abandoning the idea: keep open-textbook grounding, drop web hydration, rename
the credential vocabulary. Worth knowing now, because per-source provenance is
nearly free to record while building and expensive to reconstruct later.

## Recommended cuts

* **Drop High School** — student mode covers it, as noted.
* **Advanced Undergraduate is a level, not a program tier.** It is `mastery 4`
  applied to courses; it does not need its own program type. Keeping it as a
  modifier avoids a near-duplicate branch.
* **Seminar is a Course, not a Program** — assumed-knowledge maps onto the
  existing `starting_from` axis (already 1–4) and day/weekend/week onto `scope`.
  No new machinery.

## Open questions before implementation

1. Where does a Program stop being one object? A bachelor's spans years — does
   it survive preset changes, model changes, schema migrations?
2. ~~Does the FSRS queue span courses?~~ **The schema already supports it** —
   `user_progress` is keyed `(student_id, concept_uid)` with `course_uid` as a
   column, and `idx_progress_review` indexes `next_review_date` *without*
   scoping to a course. Cross-course review is a query change, not a migration.
   Still needs checking at the call sites, which may filter by course anyway.
3. What happens when a learner fails the gate twice? Real programs have retake
   limits; unlimited retries make the credential meaningless.
4. Is the credit template US-specific? It currently is.

---

# Part II — The four conditions for calling this done

The sections above establish the shape. These four establish when it is
*trustworthy*, each with an instrument and an acceptance criterion. Nothing here
should be believed because it sounds right; every claim below names how it gets
measured.

## Condition 1 — A session equals a class session, and a course equals a course

### The convergence that makes this tractable

Two independent anchors, neither derived from the other:

* **Carnegie / contact hours:** a 3-credit course is 3 × 50 min per week × 15
  weeks = **45 class sessions**.
* **Real textbook volume:** teachable sections in single-semester OpenStax books
  — Prealgebra 2e 80, College Algebra 2e 77, Calculus Volume 1 51 — i.e.
  **51–80 sections**.

At **1.5 concepts per session**, 45 sessions × 1.5 = **68 concepts**, which lands
inside the measured section range. Two unrelated sources agreeing is the
strongest evidence available here, and it fixes the whole ladder:

```
1 course   = 45 lessons          (a lesson IS a class session)
1 lesson   = 1-2 concepts        (a 50-min class covers one or two sections)
1 course   = ~60-68 concepts     (both anchors agree)
```

Today Helga produces 30 concepts per course = **0.67 concepts per session** — a
class session covering two-thirds of one idea. The structure is not wrong, it is
**under-filled by slightly more than half**.

### What has to be measured before any of this is claimed

The ladder above is arithmetic. The one empirical link is **how long a Socratic
session on one concept actually takes**, and this project has never measured it.
Without that number, "a session equals a class session" is an assertion.

**Instrument: `tools/session_clock.py`** (to build)

Drives real Socratic sessions through the FSM against real generated concepts
and records, per concept:

| measure | why |
|---|---|
| wall-clock to `concept_complete` | the headline number |
| learner-facing turn count | proxy for depth of dialogue |
| tokens in / out | separates thinking time from reading time |
| time to first question | is the learner waiting on the model? |
| median across ≥5 concepts × ≥3 personas | one session is not a measurement |

**Personas matter more than repetitions here.** A fast learner and a
misconception-holder will differ by more than the noise, and the design target
is the *median learner*, not the fastest path through.

### Calibration, once the number exists

| measured concept-session | concepts per lesson | concepts per course |
|---|---|---|
| ~50 min | 1 | 45 |
| ~25 min | 2 | 90 → cap at ~68 |
| ~35 min | 1.5 | 68 ← expected |

If a concept-session measures ~35 min, the current design needs no structural
change — only the concept count raised from 30 to ~68. If it measures ~10 min,
concepts are too thin and the fix is depth per concept, not more of them. **The
measurement decides which problem we have**, and the two fixes are opposites, so
guessing is worse than waiting.

### Acceptance criterion (condition 1)

1. `session_clock` reports a median concept-session length with n ≥ 15
   (5 concepts × 3 personas), and the interquartile range is reported alongside
   it — a median with unstated spread is the same false precision as a
   single-sample judge score.
2. `concepts_per_course` for the College Course preset is set from that median
   so that **lessons × session-length ≈ 45 × 50 min**, and the derivation is
   recorded in the preset table rather than typed in.
3. A built course's actual lesson count is within ±15% of 45.
4. **No hour-equivalence claim appears in the UI until (1) exists.** Coverage
   parity is defensible today; workload parity is not.

## Condition 2 — As good as the published assets we are pulling from

This is the condition that can actually be measured well, because for the first
time we *have* the reference: OpenStax gives real chapters and sections for the
same subject the course was built from.

### The gate: source parity

For any concept traceable to a source section, compare the generated content
against that section. This is not similarity — a good Socratic concept SHOULD
read differently from a textbook — it is **coverage and correctness against a
known-good reference**:

| dimension | question | failure it catches |
|---|---|---|
| **Key-term coverage** | does the concept cover the terms the section defines? | the 42%-coverage failure, at concept scale |
| **Claim consistency** | does it contradict the source? | the documented "states verified-false claims" problem |
| **Worked-example parity** | source has a worked example; does the concept? | hollow concepts |
| **Level fidelity** | does it sit at the source's level? | college text condensed into vagueness |

Key-term coverage is the strongest of the four because it is nearly objective:
extract defined terms from the source section, check presence and correct use in
the generated concept. That needs no judge at all, so it cannot drift.

### The blind A/B, and the noise discipline it requires

For the subjective half, present the judge with the generated concept and the
source section, **unlabelled and position-randomised**, and ask which better
teaches the idea to a student at the target level.

This project has measured its judges twice and both times found them noisy:
HelgaBench swings **±2 on an identical transcript**, and the syllabus judge
scores a *complete* outline at ~71%. So the discipline is not optional:

* **Median of ≥3 samples.** Never gate on one run — that has been established
  here by measurement, not preference.
* **Self-test the instrument first.** Feed the judge two copies of the *source*
  section. It should be a coin flip. If the judge prefers position A at 70%,
  the instrument is broken and every result from it is noise.
* **Report the noise floor beside the score,** always.
* **Treat the score as a lower bound**, as criterion 6 already does.

### What "parity" means operationally

Not "beats the textbook" — that is unfalsifiable puffery. Parity is:

* key-term coverage **≥ 85%** of the source section's defined terms
* **zero** contradictions of the source on checkable claims
* blind A/B **not significantly worse** than the source at p < 0.05, i.e. the
  bar is *indistinguishable*, not *superior*

### Acceptance criterion (condition 2)

1. Judge self-test passes (source vs itself ≈ 50/50 within noise) **before** any
   parity number is quoted.
2. On a real rebuilt course with OpenStax-matched sections: key-term coverage
   ≥ 85%, zero source contradictions.
3. Blind A/B median-of-3 shows no significant deficit against the source.
4. Every number carries its n and its spread.

## Condition 3 — Custom programs, where no published equivalent exists

An "Associate in DMing for D&D" has no syllabus to match. Two separate things
must still hold: the quality bar, and an honest warning when the subject cannot
fill the shape.

### The gate composition CHANGES when there is no source

This is the part most likely to be got wrong. Condition 2's strongest
instrument — key-term coverage against a source section — **does not exist
here**. If the gate is simply run as-is, a sourceless course scores zero on the
source criteria and either fails everything or, worse, has those criteria
silently skipped and reports a clean pass on a weaker gate.

That is the absent-vs-zero error in its most damaging position: a course with
*no reference* would look identical to a course that *matched its reference*.

So the gate is explicitly two-configuration, and the configuration is recorded
on the course:

| criterion | with source | without source |
|---|---|---|
| depth contract (apparatus) | enforced | **enforced** |
| level calibration | enforced | **enforced** |
| fact-check / substance | enforced | **enforced, and weighted higher** |
| structure | enforced | **enforced** |
| grounding confidence | enforced | enforced at a **lower floor**, honestly labelled |
| key-term coverage vs source | enforced | **N/A — recorded as N/A, never as 0** |
| syllabus realism (criterion 6) | enforced | **N/A** |
| internal coherence | — | **enforced (replacement)** |

The replacement criterion matters: with no external ground truth, the checks
that remain are internal consistency (does module 7 depend on something module 3
never taught?) and factual correctness against general knowledge. Fact-checking
carries more weight precisely because nothing else can.

**A sourceless course must display that it is sourceless.** Not as a failure —
as a fact about its provenance.

### Over-stretch detection: measured, not opined

The failure to prevent is a **master's in D&D lore**: conceptually teachable,
but the material runs out long before 40 courses do. The model will not refuse;
it will emit 40 titles and pad them, which is the hollow-concept problem
multiplied by forty.

Trigger on the evidence counts the brief already produces:

```
evidence_volume = structural_sources, chapter_count, matched textbooks (+levels)
requested_volume = courses x concepts_per_course       (e.g. 40 x 68 = 2,720)
```

A subject with one 12-chapter Wikibook and no textbook match cannot support
2,720 concepts, and that is arithmetic rather than judgement. **The LLM's role
is to explain, not to decide** — asking a model "is there enough material?"
invites the same optimism that produces the padding.

Three hard rules:

1. **`degraded` suppresses the warning entirely.** If lookups failed or were
   throttled, thin evidence means *we could not look*. Telling a learner their
   subject is too small when Wikimedia was rate-limiting is the absent-vs-zero
   error delivered straight to a user — the worst place it could surface.
2. **Sourceless is not the same as over-stretched.** D&D DMing has little
   academic literature and is still a real, deep practice. The trigger is
   evidence volume *relative to requested scope*, so a 6-course certificate in
   D&D DMing should pass cleanly while a 40-course master's does not.
3. **Calibrate the threshold before shipping it.** Run the detector across
   known-good subjects (Calculus, Biology) and known-thin ones. A detector that
   fires on Calculus is broken; one that never fires is decoration.

### Offer the right-sized alternative, not just a warning

A warning with only *accept* and *cancel* trains people to accept. Three options,
with the honest one pre-selected:

1. **Resize** — "the evidence supports about 6 courses; build a certificate
   instead of a master's" _(recommended, pre-selected)_
2. **Broaden** — "as a master's in Game Design, with D&D as the through-line"
3. **Proceed anyway** — explicitly acknowledging later courses will be thin, so
   the learner *chooses* padding rather than discovering it

### Acceptance criterion (condition 3)

1. Gate configuration is recorded per course; N/A criteria are stored as N/A and
   never as 0, and the summary states which configuration ran.
2. Detector calibrated: does not fire on Calculus/Biology at degree scope; does
   fire on a 40-course master's in a subject with one thin book.
3. `degraded` briefs never produce an over-stretch warning — asserted by test.
4. A sourceless course is visibly labelled as such in the UI.

## Condition 4 — Generation triggered at the right time

Lazy materialisation is only correct if the *timing* is. Two failure modes, and
they pull in opposite directions: build too early and compute is spent on
courses never taken; build too late and the learner hits a locked door.

### The binding constraint is hardware, not policy

This box runs **one model at a time** (`OLLAMA_MAX_LOADED_MODELS=1`, 24 GB). A
course build is hundreds of LLM calls. **If a build runs while the learner is
mid-session, the tutoring turn queues behind it or forces a model swap** — the
learner experiences the university building itself as latency in their own
lesson. That is the single worst outcome available here, and it is the default
unless designed against.

So triggering is not merely "when", it is "when, at what priority, and yielding
to whom":

* Builds run at **BACKGROUND** priority through the existing admission gate;
  tutoring is **INTERACTIVE** and always wins.
* Builds run in **idle windows** — no active session — and pause rather than
  compete when one starts.
* A paused build must resume, not restart. A 2-hour build that restarts on every
  interruption never finishes for an active learner.

### Predictive trigger, from measured pace

Calendar-based triggers are wrong: learners binge and stall. Use their own pace.

```
concepts_remaining / learner_concepts_per_day  ->  projected finish
trigger when   projected_finish - now  <=  build_duration + margin
```

With ~2 h per course and weeks of study per course, the window is enormous —
which is exactly why this can be conservative. **Lookahead depth 1**: build only
the next course. Building five ahead spends compute on choices not yet made.

### The elective pick is on the critical path

A course cannot be built until the learner chooses it. So the *choice* must be
requested with enough lead time to build after it — prompt at roughly **70%
through the current course**, not at the end. This is where the registration
mechanic and the scheduler meet: asking early is better UX *and* the only way
the build has room.

### Degradation must be graceful and honest

Builds will fail — research throttled, a model swap, a restart. Required:

* A course reaching its start date unbuilt shows **"still being prepared"** with
  honest status, never a blank or an error.
* Failed builds retry with backoff and **surface after repeated failure** rather
  than retrying silently forever.
* A build that completed *degraded* (research unavailable) is **flagged, not
  silently accepted** — otherwise the throttling storm becomes permanent
  curriculum.

### Acceptance criterion (condition 4)

1. A build running during an active session does not measurably increase
   tutoring turn latency — **measured**, not assumed, since this is the failure
   that would define the product.
2. Interrupted builds resume rather than restart; asserted by test.
3. Across a simulated program, no course is reached before it is ready under
   normal pace; the fallback state renders when forced.
4. Lookahead never exceeds 1 unbuilt course.

## Condition 5 — Integration with the Mode A path, QA gates and MVP testing

### The dependency that sequences everything

`docs/MODE_A_STATUS.md` §4 item 0 is unambiguous:

> **NOTHING HAS BEEN REBUILT SINCE THE GROUNDING CHAIN CHANGED.** The 42%
> coverage figure is from a course built by the OLD pipeline. Whether any of
> this actually improves coverage is **unmeasured**.

Since then this branch has changed the grounding chain *again* — OpenStax added,
ranking gated on subject fit, caching and rate limiting throughout. **Every
quality claim in Part II is unmeasurable until a course is rebuilt and
re-scored.** Condition 2 cannot even begin: there is no current course to compare
against a source.

So the first task is not university work at all:

> **Task 0 — rebuild the Pythagoras course on the current pipeline and re-run
> criterion 6 against the 42% baseline.** ~40 min. Everything else waits on it,
> because everything else is a claim about quality that this number either
> supports or refutes.

This also settles whether the whole premise holds. If real-syllabus grounding
does not move 42% substantially, copying textbook spines will not save it, and
the design needs revisiting before it is built.

### How the gate extends

Mode A's quality gate has six criteria, five ENFORCED and criterion 6 WIRED but
non-blocking (a documented undercount — a complete outline scores ~71%, so
blocking on it would reject good courses). That structure is sound and should be
extended, not replaced:

| # | criterion | state | change |
|---|---|---|---|
| 1 | Apparatus (depth contract) | ENFORCED | unchanged |
| 2 | Level calibration | ENFORCED | unchanged |
| 3 | Substance / fact-check | ENFORCED | **weighted higher when sourceless** |
| 4 | Structure | ENFORCED | unchanged |
| 5 | Grounding | ENFORCED | **lower floor when sourceless, labelled** |
| 6 | Syllabus realism | WIRED, non-blocking | **N/A when sourceless** |
| **7** | **Source parity** | new | key-term coverage ≥85%, zero contradictions (condition 2) |
| **8** | **Volume parity** | new | lessons ≈ 45, concepts ≈ 60-68 (condition 1) |
| **9** | **Internal coherence** | new | no concept depends on material taught later (replaces 6/7 when sourceless) |

And a new tier that did not exist before, because programs can fail in ways no
course-level check can see:

| # | program criterion | checks |
|---|---|---|
| P1 | Prerequisite DAG valid | no cycles; every prerequisite in an earlier term |
| P2 | Template satisfied | gen-ed / core / elective / capstone slots filled to spec |
| P3 | Scope supported by evidence | over-stretch detector (condition 3) |
| P4 | No duplicate courses | the same subject not filling two slots under two names |
| P5 | Build schedule feasible | lookahead ≤1, no course reachable before ready (condition 4) |

**Criteria 7 and 8 should ship non-blocking first**, like criterion 6 did. Both
depend on instruments that have never run; enforcing an uncalibrated instrument
rejects good work, and this project has already documented that exact failure
twice (the 1.6 pedagogy score, the 71% syllabus undercount).

### MVP scope — deliberately one program type

The temptation is to build all five program tiers. The MVP should be **the
2-semester College Course sequence only**:

* it is a Program of 2 with one prerequisite edge — exercising the planner, the
  DAG, lazy materialisation and the trigger, all of it
* it is ~4 hours of build, not 80, so the loop can be run repeatedly while
  calibrating
* it has real textbook equivalents (Linear Algebra I/II, Calculus I/II), so
  condition 2 is measurable from day one
* associate and bachelor's are then *the same machinery with a bigger template* —
  if the 2-course case is not solid, 40 courses will not be

Explicitly out of MVP: associate, bachelor's, seminars, the registration UI
beyond a single elective choice.

### Test plan

**Unit / fast (must run in CI):**

| area | asserts |
|---|---|
| Program planner | DAG valid; cycles rejected; prereqs precede dependents; template slots filled |
| Over-stretch detector | fires on thin+large; silent on Calculus at degree scope; **silent when `degraded`** |
| Gate configuration | sourceless records N/A not 0; summary names the configuration |
| Volume parity | preset concept counts derive from the measured session length, not a literal |
| Scheduler | lookahead ≤1; interrupted build resumes; unbuilt course renders the honest state |

**Instrument self-tests (before any number they produce is quoted):**

| instrument | self-test | pass |
|---|---|---|
| Source-parity judge | source section vs itself | ≈50/50 within noise |
| Over-stretch detector | known-good subjects | no fire |
| `session_clock` | same concept twice | spread reported, not hidden |

**Integration (slow, run deliberately):**

1. Task 0 rebuild → criterion 6 vs the 42% baseline
2. Build one 2-course program end to end; assert P1–P5
3. Source parity on a course with OpenStax-matched sections
4. `session_clock` across 5 concepts × 3 personas → set the concept count
5. **Latency-under-build**: tutoring turn latency with a background build running
   versus idle. This is the one that decides whether the product is usable.

### Definition of done

Condition 5 holds when: task 0 is measured; criteria 7–9 and P1–P5 exist and run
(7 and 8 non-blocking, with self-tests passing); the 2-course MVP builds and
passes the program gate; and the latency-under-build measurement shows no
material impact on tutoring.

---

## Summary of open decisions

Design questions needing an answer before development starts:

1. **Concept count** — accept ~60-68 per semester course (doubling build cost to
   ~2 h/course), or keep 30 and relabel as "condensed"?
2. **Where "degree" stops being one object** — a bachelor's spans years and will
   outlive schema and model changes.
3. **Retake policy** — what happens on a second failed gate? Unlimited retries
   make the credential meaningless.
4. **US-centric template** — the credit model currently assumes it.
5. **Vocabulary** — "degree/university/credits" imply accreditation. Worth
   settling early, since it is cheap now and expensive after the UI exists.

## Status

Design. **No implementation has begun and none should until task 0 is measured**,
because task 0 can invalidate the premise.
