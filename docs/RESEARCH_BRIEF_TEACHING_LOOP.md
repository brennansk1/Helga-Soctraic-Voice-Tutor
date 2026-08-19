# Research brief — Phase 4, the Socratic teaching loop

**For:** Claude Research
**From:** Project Helga
**Question in one line:** For an offline Socratic tutor that can draw diagrams,
render mathematics and show images, how should it decide *when* to teach with a
visual, *how* to grade an answer it generated the material for, *how* to reach
everything it now knows about a course, and *how* to stay safe across a
multi-year relationship with a learner — without becoming either exploitable or
uselessly prudish?

> Fourth in a series. `RESEARCH_BRIEF_LONG_HORIZON_LEARNING.md` covered
> scheduling and retention, `RESEARCH_BRIEF_CONTENT_HYDRATION.md` the content,
> `RESEARCH_BRIEF_ASSET_COLLECTION.md` the images. This one covers the live
> session where all of it meets a learner.

---

## 0. The task, stated plainly

**Teach one concept, Socratically, using every capability the system now has —
and know when not to use them.**

Four things make it hard, and an answer that solves only some does not solve it:

1. **The tutor has powerful tools and no judgement about when to reach for
   them.** It can draw twelve kinds of diagram, render LaTeX, and show licensed
   photographs. Nothing tells it that a number line helps here and hurts there,
   or that the third diagram in four turns is noise.

2. **It grades work it wrote the material for.** The generator, the examiner and
   the marker are one model with one context. That is the assessment-validity
   problem from the first brief, now live in the session rather than in theory.

3. **It has far more material than fits in a turn.** Since the last three
   phases: a claims ledger, retained source passages, teaching objects with
   question seeds per Bloom band, spoken maths, and captioned assets with roles.
   Retrieval into a 32k window is now a real design problem, not an afterthought.

4. **Safety has to behave like a good teacher, not a filter.** A history tutor
   must be able to discuss atrocity, a biology tutor anatomy, a literature tutor
   violence in a novel. The same tutor must not be talked out of its role by a
   learner who tries. **Over-blocking is a product failure, not a safe default.**

---

## 1. What the system is

**Helga** — offline, self-hosted Socratic AI tutor. **Mac mini, Apple M4, 24 GB.**
No cloud APIs at tutoring time.

### The model, and the budget every recommendation must fit

    nail-35b-a3b-ctx, Ollama.  qwen35moe MoE, 256 experts / 8 per token
    34.7B total / ~3B active, IQ3_S (3.44 bpw)
    weights 12.74 GB · num_ctx 32768 · ~142 s cold load
    MEASURED: 30.1 tok/s decode, 247 tok/s prefill  -> a turn costs ~18-30 s
    capabilities: [tools, thinking, completion]  -- NO VISION

Measured ceiling: **~15.0 GB resident**, and past ~16 GB this machine does not
degrade gracefully — throughput is flat at ~31 tok/s and then generation stops
returning usable output. At 32k the model is 13.51 GB, leaving ~1.5 GB for
anything co-resident. Tutoring is the *tighter* phase because TTS and STT are
live.

**The tutor cannot see.** It can emit a diagram *spec*, and it can attach an
image by id, but it cannot look at either. Any recommendation involving visual
verification means a model swap (`qwen3.5:9b`, 6.14 GB, vision-capable, already
pulled) with the generation model unloaded.

---

## 2. What already exists — please build on, not replace

### 2.1 The Socratic loop

A session covers **one concept**. Six question types are cycled — Scenario,
Mechanism, Contrast, Application, Edge Case, Synthesis — so the learner is never
asked the same *kind* of question repeatedly.

Two modes, chosen **by rule, not by a model call**: QUESTION is the default;
LECTURE fires when the learner says they do not know, when the last grade was
≤1, or after two consecutive grade-2 answers.

Escalation: at 2 consecutive misses the tutor changes the explanation rather
than pressing harder; at 4 it offers to move on; at **20 questions the concept
is parked** — explicitly *not* completed, handed back to the scheduler.

### 2.2 Grading, and where it is weak

The tutor grades each answer **1–5**, and that grade does three jobs at once:

* it moves **Bloom level** within the session — two grades ≥3 advance a level up
  to the course ceiling, a grade ≤1 drops one, a 2 holds and resets the streak
* it is passed to **FSRS-5** as the review rating
* it drives mode selection (LECTURE at ≤1)

A grade produced during a model outage is marked `graded: false` so it cannot
enter the scheduler as a real assessment.

**What is unvalidated:** nothing checks that a 3 means the same thing twice.
This project has measured its own LLM judge swinging **±1.4 out of 5 on
identical input**, and that judge is the same family as the grader.

### 2.3 The presentation tool — more capable than the tutor knows

`services/common/visual_aids.py` validates a JSON spec; `static/js/aids.js`
renders it as SVG client-side. **Twelve kinds:**

    number_line · geometry · plot · bars · graph · timeline
    table · venn · cycle · steps · fraction · image

with a large alias table (`flowchart→graph`, `pie→fraction`, `worked_example→steps`,
`histogram→bars`, …) so a model that asks for the wrong name still gets a
diagram.

Three rules the renderer keeps, which any recommendation must not break:

1. **Never `innerHTML` a spec value.** Labels go in via `textContent`, which is
   why the server may preserve `<` and `>` — `2 < x < 5` must survive as an
   inequality, so the escaping obligation sits at the render boundary and is
   absolute.
2. **An aid must never cost the learner their turn.** A failed fetch, unknown
   kind or malformed spec degrades to the written description.
3. **No aid is ever a bitmap.** Aids are drawn; images are a separate kind with
   a same-origin rule.

Generation is grammar-constrained (Ollama `format`) with retry against the
*named* validation failure.

**What is missing: any notion of WHEN.** Nothing decides that this turn wants a
diagram, which kind, or that the learner has had three already.

### 2.4 What the tutor can now reach — and mostly does not

Built in the last three phases, none of it wired into the live session:

| available | what it holds |
|---|---|
| `taught_concepts` / `taught_claims` | atomic claims + bge-m3 embeddings for everything already taught |
| `sources` / `claim_sources` | retained source passages, per claim, with a degraded flag |
| `teaching_objects` | worked steps, belief/correction pairs, **question seeds per Bloom band**, the grade-3 threshold, prerequisite links |
| `concept_math` | LaTeX + pre-generated **ClearSpeak speech string** per formula |
| `assets` / `concept_assets` | licensed, captioned images with a required **role** |
| `concepts` + FTS5 | bodies in SQLite, searchable |

The FSM today regex-extracts `## Socratic Hooks` and `## Edge Cases` out of the
Markdown. **The question seeds, mastery threshold, worked steps, claims,
sources, maths and assets are all sitting in tables the session never queries.**

### 2.5 Safety today: spotlighting, and nothing else

`sanitize_untrusted()` truncates a learner's answer and strips the fence marker,
then the caller wraps it in `UNTRUSTED_FENCE` and instructs the model to treat
the span as data. It deliberately **does not rewrite content** — altering a
student's words would corrupt grading.

That is the entire defence. There is no drift detection, no role-reassertion, no
content policy, and no distinction between a learner asking about the Holocaust
in a history course and a learner trying to talk the tutor out of being a tutor.

---

## 3. The questions

### Q1. When should the tutor reach for a visual, and which kind?

We have twelve kinds and no policy.

* What does the evidence say about **when a diagram helps a Socratic exchange**
  versus competing with it? The multimedia-learning work we already applied to
  assets (seductive details, g = −0.16; representational graphics help,
  decorative ones hurt) was about *instructional text*. Does it transfer to a
  turn-by-turn dialogue, where the learner is producing rather than reading?
* Is the trigger the **concept type** (a number line for inequalities, a cycle
  for a process), the **question type** (Contrast wants a table; Mechanism wants
  a cycle), the **learner's state** (two misses → draw it), or some combination?
* **How often is too often?** Is there evidence on visual density in tutoring
  dialogue — and what is the failure mode of a tutor that illustrates every turn?
* Should the decision be **rule-based like mode selection**, or model-chosen?
  Mode selection is a rule precisely because a model call per turn is expensive
  and drifts; the same argument may apply here.
* **How should the tutor be told what it can draw?** Twelve kinds plus an alias
  table is a lot of prompt. Is there a better shape than an enumeration — and
  how do we stop it asking for a kind that does not exist, given that in this
  pipeline **`minItems` is stripped for /v1 and no JSON-schema minimum binds**?

### Q2. How should a tutor grade work it generated the material for?

* Is a **1–5 scale** right, or does it invite the ±1.4 drift we measured? Would
  a rubric with named anchors, or a smaller ordinal scale, be more stable?
* The teaching object stores a per-concept **grade-3 threshold** written at
  hydration. Does grading against a *pre-committed, concept-specific* criterion
  measurably reduce drift compared with grading against a general scale?
* **How should grade stability be measured** with no human marker? Re-grading
  the same answer is the obvious instrument — what agreement rate is acceptable,
  and what does the literature on LLM-as-judge self-consistency suggest we
  should expect?
* One grade drives **Bloom movement, FSRS scheduling, and mode selection**.
  Should it? Is a single scalar carrying three decisions a design flaw, and what
  would separating them cost?
* **Partial credit and misconception detection.** The teaching object holds
  belief/correction pairs. Should grading identify *which* misconception an
  answer exhibits rather than scoring it — and is that more actionable?

### Q3. What should reach the model in a turn, out of everything available?

A turn is ~18–30 s and 32k of context, holding the system prompt, the concept
material, dialogue history, and the response.

* Given §2.4, **what belongs in a turn and what should be retrieved on demand?**
  Claims? The grade-3 threshold? The question seed for the current Bloom band?
  Prior-concept claims from the ledger? Source passages?
* Is **retrieval per turn** affordable at 247 tok/s prefill, or should a session
  assemble its context once and hold it?
* **Dialogue history is the thing that grows.** What is the right policy —
  full transcript, last N turns, or a running state (concept, Bloom level,
  misses, misconceptions seen)? The scheduling brief warned that transcript
  history was lost entirely on restart.
* **How should assets and maths enter the turn?** The tutor cannot see an image;
  it has a caption, alt text and a role. Is that enough to decide to show it?
  For maths, the speech string exists for TTS — should the *model* also see it,
  or only the LaTeX?
* What is the right **split between context given and constraints checked
  afterwards**? Our repeated finding: prompt instructions do not hold (0/5),
  correction rounds naming a specific offender do (5/5).

### Q4. Context drift over a long session, and over years

* What does the evidence say about **role and instruction drift** in long
  multi-turn dialogues with a small quantised model — how many turns before a
  system prompt stops binding, and what re-anchoring actually works?
* Is periodic **role re-assertion** effective, or does it just consume context?
  Where should it sit — system prompt, per-turn preamble, or a checked
  post-condition?
* **How is drift detected without a model?** Is there a deterministic signal —
  the tutor answering its own questions, mode-switching without a trigger,
  stopping asking questions at all — that could be measured per turn?
* Sessions resume across days and years. **What state must survive a restart**
  for the tutor to be the same tutor, and what is safe to forget?

### Q5. Prompt hijacking, from someone sitting in front of a tutor

The learner is the untrusted input. They are also the customer, and often a
child.

* Spotlighting plus a fence is our whole defence. **What is its measured
  effectiveness**, and what defeats it — multi-turn setup, roleplay framing,
  instructions embedded in an answer to a legitimate question, encoded text?
* **A learner trying to get out of work is the common case**, not an attacker:
  "you already marked this correct", "my teacher said skip this", "ignore the
  rubric". These are social, not technical. What handles them?
* Are there attacks specific to **an answer that is graded**? An answer is
  untrusted text that the model must both *evaluate* and *not obey* — is that a
  distinct and harder case than ordinary injection?
* What belongs in a **deterministic pre-check** versus a model judgement, given
  a model call per turn costs ~18–30 s?
* Should a hijack attempt be **refused, deflected, or taught through**? A tutor
  that lectures a fourteen-year-old about prompt injection has lost them.

### Q6. Content safety that behaves like a teacher, not a filter

**The requirement is explicit: over-blocking is a failure.**

* A history tutor must discuss the Holocaust, slavery, and atrocity; a biology
  tutor anatomy and reproduction; a literature tutor violence and sexuality in
  set texts; a chemistry tutor dangerous reactions. **How do real classroom
  systems draw this line**, and what do the age-banding frameworks (Common Sense
  Media's 0–5 category scales mapped to developmental bands) actually give us?
* Is the right control **subject × age band × intent**, rather than keywords?
  What signal distinguishes "explain the mechanism of nerve agents because we
  are studying WWI chemistry" from a genuine misuse request?
* **What is the false-positive cost, and how is it measured?** A filter that
  blocks a legitimate history question is invisible in aggregate metrics and
  extremely visible to the learner.
* Given no vision and ~1.5 GB co-resident, is a **small text classifier**
  affordable, or must this be prompt-level? What are the candidates and their
  measured false-positive rates on *educational* text specifically — not general
  web text, where the base rates are entirely different?
* **Self-harm, abuse disclosure, crisis.** A learner alone with a tutor for
  years will eventually say something that is not about the subject. What is the
  responsible design for an offline system that cannot escalate to a human, and
  what must it never attempt?
* Where does **parental visibility** belong, given the platform has parents,
  students and consent records already?

### Q7. What repos or libraries would improve this stage?

For: dialogue state management, LLM-as-judge calibration and self-consistency
measurement, prompt-injection detection and benchmarks, small content classifiers
tuned for educational text, structured output with retries against Ollama, and
deterministic drift detection.

Small, well-maintained, offline-capable, and sized for ~1.5 GB co-resident.
Naming a library is less useful than saying which of our problems it solves,
what it replaces, and what it costs in RAM.

---

## 4. Hard constraints

| constraint | detail |
|---|---|
| **A turn costs ~18–30 s** | 30.1 tok/s decode measured; a second model call per turn roughly doubles it |
| **~1.5 GB co-resident** | model is 13.51 GB at 32k against a ~15.0 GB ceiling; past ~16 GB output stops entirely |
| **The tutor cannot see** | vision requires unloading the 12.74 GB model and loading `qwen3.5:9b` |
| **No JSON-schema minimum binds** | `minItems` stripped for /v1, `format` ignored there — validate after generation |
| **Offline at tutoring time** | no cloud APIs, no escalation path to a human |
| **Aids never cost a turn** | any failure degrades to the written description |
| **Never `innerHTML` a spec value** | the escaping obligation lives at the render boundary |
| **Learners include minors** | and the platform already has parents, consent and enrolment records |

---

## 5. What would make the answer most useful

* **A stated recommendation**, not a survey — especially on Q1 (when to draw)
  and Q6 (where the safety line sits), where we currently have no policy at all.
* **Concrete parameters.** If the answer is "re-assert the role every N turns",
  give N. If it is "block below age band X", say what X is and on what evidence.
* **Rules over model calls where possible.** Mode selection is a rule because a
  per-turn model call is expensive and drifts. Say where that argument applies
  and where it genuinely does not.
* **What to measure**, including what would look like success while failing. A
  safety filter that never fires and a drift detector that never triggers are
  both indistinguishable from broken, and this project's recurring failure has
  been that its own instruments were the problem.
* **What to abandon.** If the 1–5 grade, the 20-question cap, the six question
  types, or the twelve aid kinds are wrong at this scale, say so plainly.
* **The honest answer on over-blocking.** If no available content classifier can
  tell a history lesson from a harmful request at an acceptable false-positive
  rate, say that — "prompt-level only, with these specific instructions" is a
  legitimate and actionable finding.
