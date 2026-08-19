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

This section is deliberately detailed. The three mechanisms below already
interact, and a recommendation that ignores how they are wired will not be
implementable.

### 2.1 The Socratic loop — what a session actually is

A session covers **one concept**. The tutor does not present material and then
test it; it asks a question, grades the answer 1–5, and chooses the next move
from the grade.

**Six question types, cycled** — the learner is not asked the same *kind* of
question repeatedly:

| type | asks for |
|---|---|
| **Scenario** | apply the idea to a situation |
| **Mechanism** | explain how/why it works |
| **Contrast** | distinguish it from a neighbouring idea |
| **Application** | use it on a concrete problem |
| **Edge Case** | where it breaks or does not apply |
| **Synthesis** | connect it to other concepts |

**Two teaching modes**, chosen per turn by rule (not by a model call):

* **QUESTION** — the default Socratic move.
* **LECTURE** — the tutor explains instead of asking. Triggered when the learner
  says they do not know, when the last grade was ≤ 1, or when they have given
  two consecutive partial (grade-2) answers. This is the "stop interrogating
  someone who is lost" valve.

**Escalation when a learner stalls** (adult bands): at 2 consecutive misses the
tutor changes the explanation rather than pressing harder; at 4 it offers to move
on; at 20 questions the concept is **parked** — explicitly *not* completed, handed
back to the review scheduler, and the learner proceeds.

*Relevant to your answer:* the tutor is a question-asking machine, so the
**testing effect and generation effect are already exploited continuously** —
every interaction is retrieval practice. What it does *not* currently do is space
those retrievals deliberately; spacing is handled by a separate system (2.3).

### 2.2 Bloom's taxonomy — how it is actually wired

Bloom is not decoration here; it is the **difficulty controller**, and it moves
during a session.

* Each concept carries a **Bloom target**; each course a **floor and ceiling**
  from its preset (e.g. a College Course runs 1–4, a Graduate Seminar 3–6).
* The learner starts at the floor. **Two consecutive grades ≥ 3 advance one
  level**, up to the course ceiling.
* A grade ≤ 1 **drops the level by one**; a grade of 2 holds it and resets the
  streak.
* For younger bands, repeated misses also ease the level *downward* toward the
  floor, so the next question is genuinely simpler rather than merely rephrased.
* Levels: 1 Remember · 2 Understand · 3 Apply · 4 Analyze · 5 Evaluate ·
  6 Create.

So the tutor is continuously hunting for the edge of the learner's competence
within a concept, and the mastery gate (below) requires reaching the concept's
Bloom target — not merely answering correctly at an easy level.

*Relevant to your answer:* this is a within-session adaptive difficulty
mechanism. **We do not know what should happen to Bloom level across a
four-year horizon** — whether a review months later should restart at the floor,
resume at the level attained, or drop by some function of elapsed time.

### 2.3 FSRS-5 — how it is used, and where it is thin

A direct FSRS-5 implementation (not a wrapper around a library). Per-concept
memory state — **stability, difficulty, lapses, next-review date** — is persisted
on the progress row (schema v10), so concepts *and* flashcards are both scheduled
by it.

* The tutor's **1–5 grade is passed to FSRS as the rating**, so the same judgement
  that drives Bloom movement also drives scheduling.
* Measured interval growth on repeated successful recall:
  **3 → 11 → 35 → 101 days**.
* A grade produced during a model outage is marked `graded: false` so it cannot
  silently enter the scheduler as a real assessment.

**Where it is thin, and why we are asking:**

* Scheduling is **per concept**. There is no notion of a *course* or a
  *programme* in the scheduler — nothing knows that Calculus II depends on
  Calculus I, or that a term-1 concept is a prerequisite for term-5 material.
* **Cross-course review is not implemented.** The schema supports it (review
  dates are indexed independently of course), but nothing currently resurfaces a
  Calculus I concept while a learner is inside Calculus II.
* Nothing connects **mastery-gate completion** to **initial FSRS state**. A
  concept that was hard-won and one that was easy enter the scheduler the same
  way.
* Nothing connects **review failure** back to **course status**. A learner can
  fail a review of a concept in a module they have already "passed", and the
  module stays passed.

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
