# Research brief — long-horizon retention for multi-year degree programmes

**For:** Claude Research
**From:** Project Helga
**Question in one line:** What is the optimal learning system for a student who
must pass a module assessment *now* and still hold the material *four years
later* — and how should the two goals be reconciled when they conflict?

---

## 1. What the system is

**Helga** is an offline, self-hosted AI tutor. It runs on a single Mac Mini
(M4 Pro, 24 GB) with a local LLM, no cloud dependency at tutoring time. It has
three modes: Socratic dialogue, spaced-repetition review, and a memory-palace
mode.

Teaching is **Socratic and text-based**: the tutor asks questions rather than
lecturing, grades each learner answer 1–5, and advances only when the learner
demonstrates understanding. There is no video, no lab, no human instructor.

### The change that prompts this brief

Helga is moving from **single courses** to **multi-year degree programmes**:

| | scale |
|---|---|
| course | ~135 concepts, ~45 sessions, one semester |
| associate | ~20 courses, 4 terms, **2 years** |
| bachelor's | ~40 courses, 8 terms, **4 years** |

A single course can be crammed. **A four-year programme cannot.** Material from
term 1 must still be available in term 8, and the system has no lecture hall,
no study group, and no exam week to force consolidation.

---

## 2. What already exists (please build on, not replace)

### 2.1 Spaced repetition — FSRS-5

A direct FSRS-5 implementation (not a wrapper) drives **both** flashcards and
concepts. Per-concept memory state (stability, difficulty, lapses) is persisted.

**Measured interval growth on successful recall:** 3 → 11 → 35 → 101 days.

### 2.2 The mastery gate

A concept is *not* completed until the learner clears a conjunctive gate:

* a **streak** of grades ≥ 3 (band-dependent: 2 correct for young learners,
  3 of 4 for senior),
* a **Bloom level** target — the gate rises through Remember → Understand →
  Apply → Analyze → Evaluate → Create,
* **question-type diversity** — the learner must pass several *kinds* of
  question, not the same kind repeatedly.

### 2.3 A bounded session

Measured: a stalled learner could run indefinitely on one concept. There is now
an escalation — ease the explanation at 2 misses, offer to move on at 4, and at
20 questions the concept is **parked**: explicitly *not* completed, returned to
the FSRS queue, and the learner continues.

### 2.4 Grade provenance

A grade produced during an LLM outage is marked `graded: false` so it cannot be
mistaken for a real assessment by FSRS or by a mastery calculation.

### 2.5 Content provenance

Courses are built from real published syllabi where they exist (OpenStax,
Wikibooks, transcribed textbook chapter orders). Coverage against a real syllabus
is measured — 100% against MIT 18.06 for linear algebra. Where no published
source exists, the course is labelled as such.

---

## 3. The problem we cannot resolve ourselves

### 3.1 The central tension

**Mastery-gating optimises for the module. Spaced repetition optimises for the
year.** They are not the same objective and they can actively fight:

* The mastery gate says "you have it — move on." FSRS then schedules a review in
  3 days, 11 days, 35 days.
* But the learner is *also* starting the next module, which has its own gate,
  its own new material, and its own reviews.
* By term 8 of a bachelor's, a learner has ~5,400 concepts in the review pool.

We do not know what the right relationship between these two systems is.

### 3.2 The assessment-validity problem

**The tutor generated the content and would also write the exam.** That is
grading its own homework: the exam will test exactly what was taught, in the
framing it was taught, and inflate every pass.

We have partial mitigations in mind (generate items from the *source textbook
section* rather than generated prose; hold out material; require demonstration
after a delay) but no principled design.

### 3.3 What a "pass" should mean over four years

A real university tests you in December and never checks again. We *can* check
again — FSRS already measures whether something stuck. **Should a module pass
require demonstrated retention after a delay rather than performance on a single
day?** If so, what delay, and what happens to a learner who passes the test and
fails the retention check three weeks later?

---

## 4. Hard constraints — please respect these

| constraint | detail |
|---|---|
| **No human instructor** | no cohort, no study group, no office hours |
| **Text-only Socratic dialogue** | no video, no lab, no physical practice |
| **One local model at a time** | 24 GB; a course build is hours; tutoring turns take ~18 s |
| **Offline at tutoring time** | no cloud APIs during a session |
| **Self-paced** | no fixed term dates; a learner may binge or stall |
| **Voluntary** | no external accountability — a learner who dislikes the system simply stops |
| **~2–4 years** | the horizon is genuinely long; solutions that assume a semester will not transfer |

---

## 5. What we are asking for

### 5.1 Primary question

**Design the optimal long-horizon learning system for this context.** Concretely:
what should happen, when, to a learner working through a 40-course programme
over four years, such that they both pass their module assessments and retain the
material at the end?

We want a *recommendation*, not a survey. If the evidence supports one approach
over others, say so and say why.

### 5.2 Specific questions

1. **Gate vs schedule.** What is the right relationship between a mastery gate
   (module-level) and a spaced-repetition scheduler (programme-level)? Should
   passing a gate *set* the initial FSRS state? Should failing a review *reopen*
   a completed concept?

2. **Retention-based passing.** Is there evidence that requiring demonstrated
   retention after a delay produces better long-term outcomes than a one-shot
   assessment? What delay? What failure policy?

3. **Interleaving across courses.** Term 5 is teaching new material while terms
   1–4 need review. What is the optimal mix? Is there evidence on interleaving
   *across subjects* rather than within one?

4. **The forgetting curve at programme scale.** With ~5,400 concepts and a
   four-year horizon, what review load is realistic per day? At what point does
   the review burden exceed what a self-paced volunteer learner will sustain, and
   what should be *allowed* to decay?

5. **Prerequisite decay.** Calculus II assumes Calculus I. If a learner's
   Calculus I retention has decayed by the time they reach Calculus II, what
   should the system do — block, remediate, or teach through it?

6. **Assessment validity without an external examiner.** How should a system
   that generated its own curriculum assess it credibly? What role should
   source-derived items, held-out material, transfer questions and delayed
   retention play?

7. **Desirable difficulties in a Socratic setting.** Testing effect, spacing,
   interleaving and generation effect are established. Which of these does a
   question-asking tutor already exploit, and which are we leaving on the table?

8. **Motivation over years.** What does the evidence say about sustaining a
   self-directed learner across a multi-year programme with no cohort and no
   deadline? We have XP/streaks/quests; we do not know if they help at this
   horizon or actively harm intrinsic motivation.

### 5.3 What would make the answer most useful

* **Concrete parameters**, not principles alone. If the answer is "review before
  the retrievability drops below X", tell us X.
* **A stated position** where the literature is contested, with the reasoning.
* **What to measure.** We instrument heavily and prefer instruments with no model
  in them. Tell us what signal would show the system is working — and what would
  show it *looks* like it is working while failing.
* **What to abandon.** If part of the existing design (the conjunctive mastery
  gate, the 20-question cap, XP) is counterproductive at this horizon, say so.
* **Failure modes.** Especially any that would be invisible from inside the
  system — this project has repeatedly found that its own measurements were the
  problem, and would rather be warned in advance.

---

## 6. Context that may matter

* **Bloom's 2-sigma** is often cited for tutoring. We would rather know what
  survives replication than what is quotable.
* **The learner is usually an adult** working alone by choice, often on a subject
  with no formal programme available to them. That is the population.
* **The system already knows a great deal about each learner**: every grade,
  every miss streak, per-concept FSRS state, which question types they pass. If
  an approach needs a signal we plausibly have, assume we can compute it.
* **We will implement what you recommend.** This is not a literature review for
  its own sake — it is the design input for the next build phase.
