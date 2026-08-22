# Research brief — designing Mode B (K-12) for Helga

**For:** deep research
**Prepared:** 2026-08-21
**Decision this feeds:** the pedagogical and UI/UX design of Helga's K-12 mode,
grade by grade, K through 12.

---

## 0. What we need out of this

A **grade-by-grade design specification, K-12** (§7), answering for each grade
what the teaching interaction should be, how much is voice vs text vs visual,
how simple the interface must be in concrete numbers, how long a session runs,
and what an adult has to do.

The load-bearing hypothesis we want tested is in §6; §8 covers the subjects a
screen cannot teach at all. **We would rather be told we are wrong, with
evidence, than have our assumptions confirmed politely.**

Sections 1–5 describe the system in detail, because recommendations that do
not fit the machine we have are not actionable. §9 lists the constraints that
bound any answer.

---

## 1. What Helga is

A **fully offline, self-hosted AI tutor**. The language model, speech
synthesis, speech recognition, search index, and all learner data run on the
user's own hardware — a Mac mini M4 Pro, 24 GB. No data leaves the machine and
no third-party API is called, ever. This is architectural, and it is what makes
minor-safety compliance tractable at all.

**Mode A (adults, self-directed)** is built and running: name a topic, Helga
researches and builds a course from real sources, teaches it in Socratic
dialogue, schedules review with FSRS spaced repetition, and can assemble
courses into a credit-bearing degree plan.

**Mode B (K-12)** is this brief's subject. Its *machinery* is largely built
(§4). Its **pedagogical design for young children is not**, and that is the gap
this research fills.

### 1.1 The stack

| Layer | What runs |
|---|---|
| Inference | one local model, ~14 GB resident, OpenAI-compatible API |
| Speech out | Kokoro TTS, 82M params, 14 voices, adjustable rate |
| Speech in | Nemotron ASR on the Neural Engine |
| Storage | SQLite (WAL + full-text search) + JSON course structures + Markdown concept files |
| Search | self-hosted SearXNG, used at course-build time only |
| UI | Flask + Socket.IO web app in a browser |

Practical consequence: **one model, one machine.** A tutor turn costs a few
seconds. We cannot run a second model concurrently for, say, a separate
affect-detection pass without paying that latency serially.

---

## 2. How Helga actually teaches, today

This is the engine Mode B would inherit. Understanding it matters because the
research question is partly "which of these mechanisms should be switched off
for a six-year-old".

### 2.1 The session loop

A course is a tree: **course → module → unit → lesson → concept**. A concept is
a Markdown document with sections for explanation, worked examples,
misconceptions, analogies, key terms, Socratic hooks, and pre-built diagrams.

The tutor is a state machine. The teaching state is `SOCRATIC_LEARNING`; the
others handle the lobby, pre-assessment, spaced repetition, style selection and
pausing. Within a concept the loop is:

1. Tutor asks **one** question (a hard rule — never two).
2. Student answers in text or by voice.
3. A separate **grading call** scores the answer 1–4, names the concepts the
   answer missed, and gives a reason.
4. That grade drives the next move: raise difficulty, probe the weak part,
   offer a hint, or drop into a micro-lecture.
5. When a **mastery gate** is satisfied, the concept completes and FSRS
   schedules its first review.

### 2.2 The six question types

Every question is generated as one of six types, cycled deliberately so a
concept is approached from multiple angles: **Scenario, Mechanism, Contrast,
Application, Edge Case, Synthesis.** The mastery gate requires passing on
several *distinct* types, not just several questions — so a student cannot pass
by repeating one trick.

### 2.3 The hint ladder

On a wrong answer the tutor escalates in fixed order: probing question → small
conceptual hint → larger hint that narrows the space → worked example, followed
immediately by a parallel question to check transfer. If the student says "I
don't know", the system detects it and **stops questioning**, delivers a 2–3
sentence micro-lecture, then asks a simple verification question.

### 2.4 Bloom tracking and the mastery gate

Each concept tracks a Bloom level (Remember → Create). The level ramps within
bounds set per grade band. The gate is a *conjunction*: a streak of correct
answers, a minimum number of questions, and a minimum number of distinct
question types. Defaults are 2 / 3 / 3 for adults.

### 2.5 What the tutor is told about the learner

Four separate channels, each deliberately distinct:

- **misconceptions** — "students often believe X", a claim about students in
  general, drawn from the concept document.
- **learner history** — "you have forgotten this twice", a claim about *this
  child*, drawn from their own FSRS record across past sessions.
- **turn state** — what they have established *in this session*, built in code
  from the graded answers, so the model is told the state rather than
  re-deriving it from the transcript each turn.
- **interests** — used to source analogies.

### 2.6 The turn contract

Enforced in code, not requested in a prompt. Every tutor turn must be under a
word cap, must end in a question, must engage with what the student actually
said, must introduce at most one new technical term, and any claim about what
the student said must match the transcript. A turn that breaks a rule is
regenerated against the **named** violation. (We measured that prompt-only
enforcement lands 0/5 while naming the specific offender lands 5/5.)

---

## 3. The visual presenter — what we can already draw

Relevant because "more visual" is a likely recommendation for young children,
and we want it grounded in what exists.

**Thirteen figure kinds** render inline in the chat: `number_line`, `geometry`,
`plot`, `bars`, `graph` (concept maps, flowcharts, causal chains), `timeline`,
`table`, `venn`, `cycle`, `steps`, `fraction`, `code`, `image` (assets
collected at course-build time). LaTeX renders properly via a vendored offline
KaTeX.

Two properties matter pedagogically:

- **Staging.** Any element can carry `stage: 1`, hiding it until the student
  answers. A geometry figure can label the unknown side "?" and reveal the value
  afterwards. This is what stops a diagram converting a question into a lecture
  with pictures.
- **A per-turn policy decides whether to draw at all.** It scores the moment
  (concept opening, student stuck, prose already failed, visual subject matter),
  prefers a diagram already built and checked at course-build time over
  authoring a fresh one, and enforces hard budgets: **3 diagrams per concept (4
  for K-2 and 3-5), 10 per session, and a 2-turn cooldown** so a picture cannot
  appear every turn. When the answer is "no", the diagram grammar is withheld
  from the prompt entirely.

Every asset carries a **provenance tier**: computed, retrieved, or authored.

**Note the existing young-learner bias:** K-2 and 3-5 already get a higher
diagram budget than adults. Whether 4 is remotely the right number for a
six-year-old is exactly the kind of thing we are asking.

---

## 4. What already exists for Mode B

Substantially more than a greenfield. There are eleven implementation-ready
design specs (data model, grade adaptation, multi-tenancy/auth, catalog and
standards, assessment engine, parent dashboard, gamification, compliance,
billing, deployment, information architecture).

### 4.1 Grade-band adaptation — the current canonical table

Already implemented and driving prompts, the state machine, and content
generation:

| Parameter | K-2 | 3-5 | 6-8 | 9-12 |
|---|---|---|---|---|
| Persona | warm playful guide | friendly encouraging coach | curious thinking-partner | rigorous academic mentor |
| **Max words / tutor turn** | **25** | 45 | 70 | 110 |
| Sentences / turn | 1–2 | 2–3 | 2–4 | 3–5 |
| New ideas / turn | 1 | 1 | 1–2 | 2 |
| Vocabulary ceiling | top-2000 common words | common + 1 key term/turn | grade-appropriate academic | full academic register |
| Question framing | concrete, playful, here-and-now | concrete, light abstraction | concrete→abstract bridges | abstract, multi-step |
| Bloom floor / ceiling | 1 / 3 | 1 / 4 | 2 / 5 | 2 / 6 |
| Mastery gate streak / Qs / types | 2 / 2 / 2 | 2 / 3 / 3 | 2 / 3 / 3 | 3 / 4 / 3 |
| Affirmation density | very high | high | moderate | calibrated |
| Hint ladder depth | shallow, fast worked example | medium | medium | full 4-step |
| Expected answer length | a word or short phrase | a sentence | 1–2 sentences with a reason | multi-clause with justification |
| **TTS default** | **ON, slower rate** | ON | off (opt-in) | off |
| Read own text aloud | yes | optional | no | no |
| Emoji | sparing | rare | none | none |
| Markdown / LaTeX | none | minimal | yes | yes |

**These numbers were set by judgement, not from developmental literature.** The
spec itself flags them as "starting values, tune from transcript review", and
lists as an open question whether K-2 should split into K-1 / 2. **A grounded
replacement for this table is a primary deliverable of this research.**

Also already specified but not validated: for K-2 and 3-5 maths the system may
emit a **structured answer widget** (tap-to-count, number-line drag) instead of
demanding a typed answer, and on repeated misses for those bands it switches to
encouragement and a simpler scaffold rather than escalating difficulty.

### 4.2 Assessment engine

Exam kinds include a **non-gating diagnostic** for placement (sets entry depth
and Bloom floor, never blocks or fails), plus mastery checks and unit exams.

There is an **interest-theming** mechanism: an item is generated
interest-blind, then rewritten so its *surface context* matches the child's
stated interests (a ratio problem about soccer goals) while the assessed
standard, Bloom level, answer and difficulty are held constant. A validity
guard compares the themed item against the base item and rejects a rewrite that
changed the substance.

### 4.3 Gamification — already research-grounded, with hard guardrails

XP, levels, badges, streaks and a mastery map exist, built on
Self-Determination Theory rather than reward maximisation, with a written
research basis (Hanus & Fox 2015; Du & Hew 2024; Deci/Koestner/Ryan 1999;
Cordova & Lepper 1996; the Prodigy and Epic COPPA cases).

Two invariants are declared non-negotiable: **learning wins over engagement** —
a mechanic that raises engagement but flattens mastery velocity or FSRS
retention is cut — and **the mastery map is the primary surface, the XP counter
is not.**

We are not asking you to redo this from scratch, but we do want it
**stress-tested by age** (§7.7): the existing research is largely not
age-stratified, and what is true for a 15-year-old may invert for a 6-year-old.

### 4.4 Compliance and safety

Verifiable parental consent at signup and per child; consent matrix with
versioning, re-consent and withdrawal; full export, correction and deletion
rights; all inference self-hosted so no third-party model sees minor data;
health/human-development content locked by default pending explicit consent;
output moderation with crisis-resource surfacing that alerts a parent
**without transmitting the sensitive transcript**.

### 4.5 The planned K-12 information architecture

The adult app has 8 tabs. The K-12 student app is specified down to **four**:
**Today** (what to do now), **Learn** (skill tree + Socratic session),
**Practice** (quiz + spaced repetition merged), **My Stuff** (avatar,
interests, font/TTS toggles).

Deliberately removed from the child's reach: system status, heavy settings,
data export/reset, billing, and **course creation** — free-form "make me a
course" is judged wrong for children and unsafe without standards review, so
authoring moves to a parent-gated elective request flow. Calendars move to the
parent; children see only "what's due" on Today.

Three shells, one app: student, parent dashboard, admin/ops.

**We want this IA challenged.** Four tabs is a big improvement on eight, and it
may still be three too many for a kindergartener.

### 4.6 What is NOT built

**The standards table is empty.** Schema, loader, exam engine, parent
dashboard, compliance code and grade-band adaptation are all genuinely built —
but there are zero standards rows and the seed directory does not exist, so
**Mode B cannot teach a single standards-aligned lesson today.** Curriculum
content is the blocking dependency, and it is being addressed separately. It is
not what this brief is about; we mention it so nobody designs around content
that exists.

---

## 5. The measured problem that motivates the central question

We benchmark tutoring quality across seven adult domains with a purpose-built
instrument (deterministic scorers plus an LLM judge, with a published noise
floor). The consistent finding:

| dimension | score /5 | what it measures |
|---|---|---|
| **socratic** | **~2.1** | did the tutor draw reasoning out with questions, rather than lecture |
| **adaptation** | **1.3–2.8** | did it adjust to *this* learner rather than follow a script |
| accuracy | 3.0–4.5 | was everything it said factually correct |
| misconception handling | 2.7–4.4 | did it catch and correct wrong beliefs |
| honest telling | 1.0–3.0 | did it simply state things that cannot be derived |

Read plainly: **Helga is a competent, accurate explainer that mostly lectures
and adapts poorly.** `socratic` sits near 2 in *every* domain, and three
separate engineering interventions aimed squarely at it moved it by zero. The
judge's recurring complaints are *"repeats the same question in different
words"*, *"ignored the student's explicit request to move on"*, and *"delivers
mini-lectures"*.

Two implications, and we want both examined rather than assumed:

- If Socratic dialogue is the weakest thing this system does **with articulate
  adults**, betting a six-year-old's instruction on it is the highest-risk
  design choice available.
- Conversely, if the evidence says young children genuinely do learn through
  guided questioning, the right response is to fix the tutor, not route around
  it. **We need to know which.**

---

## 6. The central hypothesis to test

> **For the youngest grades (roughly K-2, possibly K-3), Socratic questioning is
> the wrong primary mode entirely. Those grades should be taught through direct
> instruction, heavy visual aids, voice interaction, and structured practice —
> with dialogue used sparingly, if at all.**

We believe this from intuition and from our own benchmark numbers, not from the
developmental literature.

- **6.1** What does the evidence say about **Socratic / inquiry dialogue with
  children under 8**? Is the binding constraint working memory, expressive
  language, metacognition, or something else? At what age does guided
  questioning start to outperform direct instruction, and under what conditions?
- **6.2** The **direct-instruction vs discovery-learning** debate has a large
  evidence base (Kirschner/Sweller/Clark's minimal-guidance critique; Mayer on
  guided discovery; the responses to both). What is the current state of it, and
  **how does it break down by age**?
- **6.3** Cognitive load theory gives worked-example effects, expertise
  reversal, modality and redundancy effects. **What do these predict for a
  voice-plus-picture tutor talking to a 6-year-old versus a 16-year-old?** The
  redundancy effect in particular bears on our K-2 "TTS on + read own text
  aloud" default.
- **6.4** Where exactly is the transition? A grade, a reading-fluency threshold,
  a developmental stage, or variable enough that the system should **detect** it
  rather than assume it from grade? We have a non-gating diagnostic that could
  carry that detection if you tell us what to measure.
- **6.5** If dialogue is wrong for K-2, **what is right?** Name the specific
  interaction patterns with the best evidence for that age, and how each maps
  onto a tutor that can speak, listen, draw thirteen figure kinds, and stage
  what it draws.
- **6.6** Our six question types (§2.2) and our hint ladder (§2.3) are adult
  designs. **Which survive contact with a young child, and in what order should
  a young-learner ladder escalate?** The current K-2 setting short-circuits to a
  worked example after one failed hint — is that right?

---

## 7. The grade-by-grade deliverable

For **each grade K, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12** — or for whatever
banding the evidence supports; tell us if our four bands are wrong:

| Field | What we need |
|---|---|
| Primary interaction mode | direct instruction / guided practice / Socratic dialogue / game / mixed, with the ratio |
| Voice vs text vs visual | the split, and which is primary |
| Reading load | how much text on screen is acceptable; can they read instructions at all |
| Input method | voice / tap / type / draw |
| Session length | minutes before attention degrades |
| Adult involvement | what a parent must do; what the child does alone |
| Autonomy | can they navigate the app themselves, or is it adult-launched |
| Assessment | how to check understanding without it feeling like a test |
| Failure handling | what happens on a wrong answer |
| Motivation | what actually sustains engagement at this age |

Plus, in numbers we can put straight into the table in §4.1: max words per
tutor turn, sentences, new ideas, expected answer length, diagram budget per
concept, TTS default, and mastery-gate thresholds.

Mark the **transition points** explicitly — where the design must change and the
evidence for placing the boundary there.

### The UI/UX questions specifically

This is where we are least equipped to guess and most want concrete, testable
constraints rather than principles.

- **7.1** What can a child **operate unassisted** at each age? Reading load,
  tap-target size, number of simultaneous on-screen choices, tolerance for
  scrolling, ability to use a text input at all.
- **7.2** **At what age can a child type usefully?** Below it, voice and touch
  carry everything — what does that imply for answer capture, and how do we
  handle a speech-recognition error with a child who cannot read the correction?
- **7.3** What do the **established design systems** for children's software
  actually specify — concrete sizes, contrast, word counts, iconography,
  navigation depth? (Nielsen Norman Group's children's UX work, WCAG/COGA
  cognitive-accessibility guidance, published guidelines from major children's
  learning products.)
- **7.4** How do the **best existing products** differ *between* age bands —
  Khan Academy Kids, Prodigy, Duolingo ABC, Teach Your Monster to Read, Osmo,
  Endless Alphabet, Seesaw? We care about what changes with age, not a feature
  list.
- **7.5** **Session length and pacing** by age; break structures; how much
  repetition is productive rather than tedious.
- **7.6** **Feedback and error handling.** How should a tutor respond to a wrong
  answer from a 6-year-old versus a 12-year-old? What does the evidence say
  about praise, about correcting errors directly, and about discouragement risk?
  Our current K-2 setting is "very high affirmation density" — is that
  supported, or does it shade into the empty praise that the evidence says
  backfires?
- **7.7** **Gamification by age.** Stress-test §4.3: where do extrinsic rewards
  help, where do they measurably harm long-term motivation, and **does the
  answer invert between a 6-year-old and a 15-year-old?**
- **7.8** **Is a four-tab navigation right?** Would a kindergartener be better
  served by a single screen with one action?

---

## 8. Teaching what a screen cannot teach

**This is the section we have thought about least and may matter most.**

Helga's current position, written into the curriculum scope: **Physical
Education and Fine Arts studio/performance are EXCLUDED** — "motor-skill/fitness
performance cannot be taught via text/voice/media" — along with driver
education behind-the-wheel and keyboarding. Only their *theory* strands are in
scope: music notation and intervals, art history and criticism, game rules,
fitness principles, movement anatomy, sportsmanship. Hands-on CTE is likewise
out of scope.

That exclusion is defensible for a chat tutor and **indefensible for anything
claiming to deliver K-12**. Utah requires PE and arts for graduation. A child
cannot complete a grade on theory alone, and a science education without a
single experiment is not a science education.

There is also **no concept of homework anywhere in the system.** Everything
happens inside a session, in the browser, while the child is at the machine.
Nothing is ever assigned to be done away from it.

### 8.1 Classifying what needs what

- **8.1a** How should a concept be **classified** by what it requires — pure
  dialogue, dialogue plus a diagram, screen practice, a physical manipulative,
  a real experiment, sustained bodily practice, or supervised instruction? Is
  there an existing taxonomy for this, or do we need to build one?
- **8.1b** Can that classification be **derived automatically** from a standard
  code and its text, or does it need human authoring per concept? We generate
  courses from sources with an LLM, so an automatic classifier would scale and a
  manual one would not.
- **8.1c** Our tutor already distinguishes concepts that can be **reasoned to**
  from those that must simply be **told** (a convention, a date, a name), and
  routes them differently. Is "needs physical interaction" a third category on
  that same axis, or an orthogonal dimension?
- **8.1d** How much of a nominally physical subject is genuinely
  screen-teachable? For a fraction that a child must *feel* with physical
  manipulatives, does an on-screen manipulative substitute adequately at each
  age, or is there evidence that the physical object matters?

### 8.2 The three mechanisms we are considering

We want each researched on effectiveness, not just feasibility.

- **8.2a — Physical kits.** Point parents at purchasable kits (science
  equipment, manipulatives, art supplies) tied to specific concepts. What does
  the evidence say about hands-on kit learning at home versus screen
  substitutes? Who does this well — what can be learned from Kiwi Crate, MEL
  Science, Home Science Tools, Montessori material suppliers, or the kit models
  used by established homeschool curricula (Sonlight, Oak Meadow, Torchlight)?
  **How do such programmes sequence a kit against the lesson** — before, during,
  or after the concept is taught?
- **8.2b — Curated video.** Link demonstrations and experiments the child
  watches rather than performs. When is watching an experiment pedagogically
  adequate, and when is it actively worse than doing it? What does the evidence
  on demonstration versus hands-on inquiry say, **and does it change by age?**
  How should video be integrated with a dialogue tutor — does the tutor question
  the child before, during, or after?
- **8.2c — Parent-guided activity with a script.** For PE, art, music practice
  and lab work: Helga produces a guide, the parent runs the activity, the child
  does it away from the screen. **What makes a parent-delivered activity guide
  actually get used?** This is the mechanism we are least confident in — it
  depends on parent time, willingness and competence, and it is where homeschool
  curricula most often fail in practice. What does the research on parent-led
  instruction say about compliance and completion?

### 8.3 Homework and off-screen work

- **8.3a** Should Helga assign work to be done **away from the machine** at all?
  What is the evidence on homework efficacy by age — where the well-known
  finding is that it does little for primary-age children and more for
  secondary?
- **8.3b** If work happens off-screen, **how does the system learn that it
  happened, and how well?** Parent attestation, a photo of the work, the child
  describing what they did back to the tutor, a follow-up question that only
  someone who did the activity could answer? Each has a different reliability
  and a different burden.
- **8.3c** How does off-screen work enter the **mastery and spaced-repetition
  model**? Our FSRS scheduler is driven by graded answers. An activity that is
  not graded has no obvious place in it. Should a parent attestation produce a
  grade, and if so how much should it be trusted?
- **8.3d** How is it **scheduled and reminded** without becoming nagging?
  Notifications reach the parent, not the child.

### 8.4 The constraint that makes this hard

**Helga is offline by design.** It cannot fetch a video, check whether a kit is
still sold, or follow a link. So:

- **8.4a** Where do purchase links and video links actually **surface**? The
  parent's own phone via the dashboard, an exported shopping list, a printed
  materials sheet at course start? What is the least-friction path that does not
  compromise the offline guarantee?
- **8.4b** Links **rot**. A kit goes out of stock, a video is deleted. What is
  the maintenance model for external references in a product that cannot check
  them, and how should the interface behave when a reference is dead?
- **8.4c** Should materials be resolved **once, at course build time** (when the
  machine may briefly have network access for research) and thereafter treated
  as fixed content?

### 8.5 Equity, safety and accreditation

- **8.5a** **Kits cost money.** How should a curriculum handle a family that
  cannot buy them? Is there a viable substitute path using household objects,
  and does the evidence support it?
- **8.5b** **Safety and liability** for at-home experiments and physical
  activity — what do established homeschool science curricula do about
  age-appropriate hazards, required supervision, and disclaimers?
- **8.5c** For **accreditation and transcripts**, what does a K-12 programme
  actually have to evidence for PE, arts and lab science? What do accredited
  online and homeschool programmes accept as proof — parent attestation, logged
  hours, portfolio, video? This bounds how rigorous the verification in §8.3b
  has to be.
- **8.5d** Given all of the above: **is the right answer to bring PE and studio
  arts into scope with parent-guided delivery, or to keep them excluded and be
  explicit that Helga is an academic-core product a family supplements
  elsewhere?** We are genuinely undecided and want a recommendation.

---

## 9. Constraints any recommendation must respect

Fixed. A recommendation that violates one is not usable.

1. **Everything runs offline on one Mac mini.** No cloud, no third-party APIs.
   One language model resident, local TTS and ASR. A tutor turn costs seconds,
   not milliseconds.
2. **All inference is self-hosted** — a legal requirement, not a preference.
3. **Verifiable parental consent**, full export/deletion rights, health content
   locked by default.
4. **Crisis detection must alert a parent without transmitting the transcript.**
5. **No live human tutors, no peer interaction, no social features.** One child,
   one AI, plus a parent dashboard.
6. Content is **standards-aligned** (initially Utah K-12).
7. **We can draw the thirteen figure kinds in §3 and speak in 14 voices. We
   cannot produce bespoke illustrated characters, animated video, or custom
   game art at scale.** Please do not design around assets we cannot make.
8. Delivered in a **browser**, on whatever hardware the family has.

---

## 10. What good output looks like

- **Evidence-weighted, not balanced.** Where the literature is genuinely
  contested, say so and say which way it leans and how strongly. Where there is
  a clear answer, give it plainly.
- **Concrete over principled.** "Max 8 words on screen for K-1, tap targets
  ≥ 60 pt, no text input below grade 2" is usable. "Keep it age-appropriate" is
  not.
- **Cite sources**, distinguishing peer-reviewed findings from vendor design
  guidance from practitioner consensus.
- **Flag thin or absent evidence** — particularly for AI tutors specifically
  with young children, where much may be extrapolation from human-tutor and
  educational-software research. Say when you are extrapolating.
- **Tell us if §6 is wrong.** It is the load-bearing assumption and we would
  rather find out now.

---

## 11. Open questions we have not thought to ask

Add anything material we have missed. Suspected blind spots:

- **Accessibility and neurodivergence** — how the design changes for a child
  with dyslexia, ADHD, or autism, and whether that is a separate mode or the
  same one built better.
- **Multilingual and English-language-learner** considerations.
- **What the parent needs to see to trust an AI teaching their child**, and how
  that differs by the child's age.
- **Risks specific to young children** that do not apply to adults —
  over-attachment, anthropomorphism, substitution for human interaction — and
  what the evidence says about mitigating them in the design itself.
- **Whether a child should know they are talking to an AI**, how that should be
  communicated at each age, and what the evidence says about disclosure.
