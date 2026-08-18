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
| Associate | ~30 courses |
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
| Associate | ~30 | 900 | **~30 h** |
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
Associate  ~30 courses: gen-ed 10 · core 14 · electives 5  · capstone 1
Bachelor's ~40 courses: gen-ed 12 · core 16 · electives 9  · capstone 3
```

(Course counts are the spec, not derived from credit hours — a 3-credit
assumption gives 20 for an associate, which undercounts labs and 1–2 credit
requirements.)

Research then supplies **which subjects fill each slot**, as evidence, using the
tiered matching already designed for skeletons (at-level match → copy; one level
above → evidence; nothing → synthesise).

The pay-off: **the same code path serves "Associate in Nursing" and "Associate
in Dungeons & Dragons."** One has real syllabi to match against and one does
not, but neither changes the algorithm — the D&D case simply runs with zero
external matches. No special "custom degree" branch to write or maintain.

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
