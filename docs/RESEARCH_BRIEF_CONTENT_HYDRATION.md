# Research brief — content hydration, storage, and retrieval

**For:** Claude Research
**From:** Project Helga
**Question in one line:** For an offline AI tutor that generates its own
course content, what should be *stored*, in *what format*, at *what size*, and
*how retrieved* — how does each concept get generated with awareness of the
whole course rather than in isolation, and how can all of it be made faster
without losing quality?

> Companion to `RESEARCH_BRIEF_LONG_HORIZON_LEARNING.md`, which covered
> scheduling and retention. This one covers the layer beneath it: the content
> those sessions actually teach from.

---

## 0. The task, stated plainly

**Take a textbook and a pile of research, and distil them into a course good
enough that someone who knows the subject would call it professional — for any
subject, at any level, offline, on one machine.**

That is the whole objective. Every question below is a sub-problem of it.

Four things make it hard, and an answer that solves only some of them does not
solve the task:

1. **Many kinds of course, one pipeline.** Linear Algebra with a canonical
   textbook and a published syllabus, and Dungeon Mastering with neither, must
   both come out well. The subject with the *least* published material is the
   one most likely to be taught badly, and it cannot be the one the system
   quietly gives up on.

2. **Distillation, not transcription.** The output is not a summary of a
   textbook. It is teaching material: an explanation pitched at a stated mastery
   level, a worked example carried to a result, the misconceptions a learner
   actually holds, and questions that make them think. A faithful compression of
   a chapter is not a lesson.

3. **The whole must cohere, and it is built in pieces.** Concepts are generated
   one at a time and have to add up to a course — no contradictions, no idea
   used before it is introduced, no idea taught five times because five lessons
   each decided they needed it first.

4. **It has to be true.** This is where we are weakest, and §2.9 states it
   honestly: generated courses contain verified false claims and roughly half of
   concepts are hollow. **A faster or tidier pipeline that does not move that
   number is not progress.**

We have solved the equivalent problem one layer up. The *skeleton* — the
structure of modules, units, lessons and concepts — now measures as
professional-grade against a published syllabus, for a textbook-backed subject
and a sourceless one alike, using instruments with no model in them. **We are
asking how to do the same for what goes inside it.**

---

**How the content is consumed, since it determines everything else.** Helga
teaches **Socratically and in text only** — it asks a question, grades the
answer 1–5, and picks the next move from the grade. It cycles six question types
(Scenario, Mechanism, Contrast, Application, Edge Case, Synthesis) and switches
from QUESTION to LECTURE mode by rule when a learner is lost. Bloom level is the
difficulty controller and moves *within* a session: two grades ≥3 advance a
level, a grade ≤1 drops one. Per-concept memory state (stability, difficulty,
lapses, next review) is scheduled by **FSRS-5**, and the tutor's 1–5 grade is
the FSRS rating.

**The human never reads these files.** The only consumer is the model, at
question-generation and grading time. That is directly relevant to Q2.

---

## 1. What the system is

**Helga** is an offline, self-hosted Socratic AI tutor on a single **Mac Mini
M4 Pro, 24 GB**, with no cloud dependency at tutoring time.

* **LLM — this is load-bearing for most of the questions below:**
  `nail-35b-a3b-ctx`, served by **Ollama** on the host.

  | | |
  |---|---|
  | architecture | `qwen35moe` — Mixture of Experts, **256 experts, 8 used per token**, 40 blocks |
  | size | **34.7B total parameters, ~3B active** per token |
  | quantisation | **IQ3_S** |
  | context — model maximum | **262,144 tokens** |
  | context — **as actually served** | **`num_ctx 16384`** |
  | cold load | **~142 s** |

  The gap between 262k and 16k is deliberate and is a **memory** decision on a
  24 GB box, not an oversight — but it is exactly the constraint that shapes
  Q2 (how much content can be in a prompt), Q3 (how much source text can be
  retrieved at once) and Q6 (whether concepts can be batched into one call).
  **`-ctx` is a custom Modelfile variant**: the stock tag runs Ollama's default
  4096, which silently truncated generation for a full debugging cycle before
  it was found.

  Any recommendation that assumes a large context should say what it would cost
  in RAM at this quantisation, and whether it still fits alongside the
  containers. One model is resident at a time.
* **Storage today:** SQLite (WAL, schema v10) for structured state, JSON for
  course structures, **Markdown files for concept content**, one file per
  concept at `data/courses/{course_uid}/content/{concept_uid}.md`.
* **Search today:** SQLite **FTS5** over content. `sentence-transformers`
  (`all-MiniLM-L6-v2`) is loaded but **not used** for dense retrieval.
* **Removed:** KuzuDB and ZIM/`libzim` were both ripped out. We have already
  paid for one wrong storage bet; that is directly relevant to question 1.

### The pipeline

Course creation runs in two phases:

1. **Skeleton** — research → structure (modules → units → lessons → concepts).
   Recently hardened; measured PROFESSIONAL against a published syllabus.
2. **Hydration** — for each concept, generate the Markdown body. **This is the
   phase this brief is about.**

Sources feeding hydration: Wikipedia/Wikibooks/Wikiversity, OpenStax, Internet
Archive metadata, and a self-hosted **SearXNG** for general web search, behind a
research microservice.

---

## 2. What exists now — please build on, not replace

### 2.1 The Markdown contract

Every concept file is generated against a fixed section template:

| section | content |
|---|---|
| `## Mastery Criteria` | what a grade-3 answer must show, at this concept's Bloom level |
| `## Core Explanation` | the body, to a word target |
| `## Key Facts` | 3-5 verified bullets |
| `## Real-World Examples` | **one worked example carried to a result** — naming a field where the idea applies is explicitly rejected |
| `## Misconceptions` | belief / correction pairs |
| `## Edge Cases & Limitations` | 2-3 bullets |
| `## Socratic Hooks` | one question each at Bloom 1-2, 3-4, 5-6 |

**Word target scales with mastery level:** 150 / 250 / 400 / 600 / 800 words for
Awareness / Understanding / Application / Proficiency / Expertise.

At tutoring time the FSM does **not** feed the whole file to the model. It
regex-extracts specific sections — `## Socratic Hooks`, `## Advanced Notes` /
`## Edge Cases & Limitations` — and injects those. So the file is already
functioning as a **section-addressed store queried by a parser**, which is part
of why question 1 is live.

### 2.2 The depth contract — how mastery level is enforced

This is the most important existing mechanism and any recommendation has to fit
it. It exists because of a measured failure: **every concept in the first real
course landed between 626 and 876 words (stdev 57.7) regardless of Bloom level,
module position, or requested mastery.** The mastery slider was declared in
prompts and verified nowhere, so the model converged on its own comfortable
length and the slider was decorative.

The insight was that **length is the wrong proxy** — a graduate treatment is not
an undergraduate one with more words. So each level requires *structural
elements*, detected heuristically (LaTeX, unicode maths, or plain prose all
accepted — the goal is catching categorically missing rigour, not enforcing a
house style):

| level | words | required elements |
|---|---|---|
| 1 Awareness | 120–1000 | any_source |
| 2 Understanding | 200–1300 | worked_example, any_source |
| 3 Application | 320–1500 | formal_definition, worked_example, any_source |
| 4 Proficiency | 500–1800 | + named_result, derivation_or_proof, primary_source |
| 5 Expertise | 700–2200 | + formal_notation, exercise |

`validate_concept()` returns *which* elements are missing, so the generator
regenerates against a named deficiency rather than blindly retrying — and a
course that cannot meet its level can be refused the label instead of shipping
silently. Default 2 retries (`HELGA_DEPTH_RETRIES`).

Note the word bands here are wider than the `content_words` targets in 2.1;
those are prompt hints, these are the enforced contract.

### 2.3 Other quality enforcement

* **Grounding threshold** — below a minimum, content is not presented as
  verified.
* **Fact-check pass** (`check_content` / `correction_hint`) against source text.
* **Correction rounds beat prompts.** Measured repeatedly in this codebase:
  prompt-only enforcement changed nothing 5/5, while one correction round
  *naming the specific offender* fixed it 5/5. Any recommendation that amounts
  to "put it in the prompt" should say why it would work here.

### 2.4 What the research service actually returns per concept

`POST /api/research_concept` returns `{key_facts, examples, edge_cases,
sources, confidence, search_degraded}`, and those are injected into the
generation prompt as labelled blocks to be synthesised into the matching
sections.

Beyond the general sources, there is **domain-specific routing**:
`classify_domains()` keyword-matches a topic to domains, and `fetch_domain_sources()`
then queries **Met Museum**, **Art Institute of Chicago**, **LoC Chronicling
America**, and **Wikidata** as appropriate. Q5 should treat this as the pattern
to extend — domain-routed authoritative APIs — rather than more general search.

`search_degraded` exists because a throttled search returning zero sources must
never be readable as "this concept has no sources". That absent-vs-zero
distinction has bitten this project several times and any caching or retrieval
recommendation must preserve it.

### 2.5 How hydration is invoked — one concept at a time

**Each concept is hydrated by its own LLM call, in isolation.** The prompt
carries the concept title, its objectives, its Bloom level, the names of its
lesson and module, and the research payload — and nothing about the rest of the
course. There is no ledger of what has already been taught and no view of what
comes later. Q7 and Q8 both follow from this.

For contrast, the *skeleton* phase went the other way: a module's entire
subtree is now generated in ONE call, which cut the number of calls and
improved coherence, because the model could see the module as a unit. Whether
that argument extends to hydration — and at what granularity it breaks against
a 16k context — is Q8.

### 2.6 Caching that already exists

| layer | what | TTL |
|---|---|---|
| `syllabus_sources._get_json` | all MediaWiki + Internet Archive + OpenStax lookups | 7 d |
| `curriculum_research.curriculum_brief` | assembled brief per (topic, level, broader) | 7 d |
| `research_server` | web search / page extraction | 24 h / 7 d |

Measured effect: **172× fewer fetches** on a rebuild. The rule we hold ourselves
to is that a cache must never turn a *failed* lookup into an apparently
successful empty one — absent and zero must stay distinguishable.

**What is NOT cached: the LLM generation calls themselves**, which is where the
hydration time actually goes.

### 2.7 The cost, measured

* **~90 s per concept** hydrated on `nail-35b-a3b` (3 concepts in 269 s).
* A parity-sized course is **~104-135 concepts** → **~2.5-3.5 hours** per
  course.
* A bachelor's programme is ~40 courses → **~5,400 concepts**.

That number is the reason Q6 exists. At 90 s/concept, a full degree's
content is ~135 hours of generation.

### 2.8 Supplementary-source policy (just implemented)

Sources are now split by relevance: only sources at or above a grounding bar may
shape structure or be measured against; weaker but related sources are labelled
**supplementary** and passed forward *specifically to hydration*, capped at a
minority share of the course. The reasoning was that "does this book speak for
the subject?" is a question a weak source always fails, while "does this passage
serve **this concept**?" is narrower and one it can legitimately pass.

**We would like this reasoning stress-tested.** It is currently an argument, not
a measurement.

### 2.9 The known quality gap — please read this before answering

Independent of everything above, measured on real generated courses:

* **Generated content states verified false claims.** Not hedged or vague —
  wrong.
* **Roughly half of concepts come out hollow** — structurally complete, passing
  the section template, saying little.
* **An LLM judge scoring the same course twice swings ±1.4 / 5.** We do not gate
  on a single judged run for that reason, and we distrust model-graded quality
  metrics generally.

So the honest state is: **the skeleton is measurably good and the content is
not.** Every instrument that currently passes is structural — it reads titles,
counts, and section presence. Nothing reads the content for truth.

This matters for how you answer. A recommendation that makes hydration faster
or more compact while leaving this gap untouched is worth less to us than one
that attacks it — and a recommendation that would *worsen* factual grounding in
exchange for speed is one we would reject.

### 2.10 Things that have already gone wrong here

Offered so recommendations can be checked against them:

* **A 4096-token context ceiling** (Ollama's default without `num_ctx`) silently
  truncated generation. It was found only after four wrong hypotheses and
  logging raw response bodies.
* **A reasoning model with thinking enabled** consumed the whole token budget
  and returned **empty** content for every concept.
* **`minItems` is stripped** from `response_format` for /v1 compatibility, and
  /v1 ignores the `format` field that carries it — so **no JSON-schema minimum
  binds in this pipeline.** Any structured-output recommendation must not assume
  schema constraints are enforced.
* **KuzuDB and ZIM were both adopted and later removed.** Relevant to Q1: we
  have paid adoption cost twice.

---

## 3. The questions

### Q1. Is a database worth it for the Markdown files — DuckDB or otherwise?

Today: one `.md` file per concept on disk, plus SQLite for structured state,
plus JSON for structure. Retrieval at tutoring time is regex over a file.

* Would DuckDB (or SQLite FTS5 alone, or a document store) meaningfully beat
  flat files here, given the access pattern is *mostly single-concept reads by
  primary key*, with occasional search?
* **Session notes** are a separate case: append-heavy, per-session, and they
  accumulate over years. Same store or different?
* What is the honest break-even? We removed KuzuDB and ZIM after adopting them,
  so **"it depends" answers should say what it depends on**, in terms we can
  measure before committing.
* Does keeping content in a queryable store make the *retrieval* question
  (Q3) materially easier, and is that the real argument rather than speed?

### Q2. What should actually be in the `.md`, and how compressed?

Given the template in 2.1 and the 150-800 word targets:

* Is the section set right for a **Socratic** tutor specifically? What is
  missing, and what is dead weight?
* **How compressed should it be?** The tutor asks questions rather than
  lecturing — so is a 600-word explanation serving the session, or is a denser
  structured representation (claims, worked steps, question seeds, dependency
  links) a better fit for a model that will *re-express* it anyway?
* Is prose the right storage format at all when the consumer is an LLM and the
  human never reads the file directly?
* Should the file store the *material* or the *teaching moves*, or both
  separately?

### Q3. How do we guarantee the tutor has the textbook information it needs?

The textbook material is fetched during research and cached, but what reaches
the tutor at session time is the generated Markdown — a lossy re-expression of
whatever was retrieved during hydration.

* Should source passages be **retained verbatim** alongside the generated
  content, and retrieved at tutoring time?
* Given a 24 GB machine running one model, what retrieval design is realistic —
  FTS5, dense vectors via the already-loaded MiniLM, hybrid, or none?
* Does a Socratic tutor actually need source text at question time, or only at
  generation and grading time? **We do not know**, and it changes the storage
  design substantially.
* How should this interact with the caching in 2.6 — is the cache the right
  place for source text to live, or does it need a durable home?

### Q4. Should we worry about file size across many courses?

* ~5,400 concepts for a bachelor's, at 150-800 words each. What does that come
  to across content, plus retained sources, plus per-session notes over 4 years?
* Where does that stop being trivial on a Mac Mini — and which component
  (content, sources, embeddings, notes, SQLite WAL) grows fastest?
* Is there a compaction or tiering strategy worth designing in **now** rather
  than retrofitting?

### Q5. What other APIs should we hydrate from?

Current: Wikipedia / Wikibooks / Wikiversity, OpenStax, Internet Archive,
SearXNG. We already respect documented rate limits per host.

* What high-quality, **freely accessible, rate-limit-friendly** sources are we
  missing — especially for STEM worked examples, and for subjects with no open
  textbook (the sourceless case is our weakest)?
* Which are genuinely open vs. open-looking but license-encumbered for derived
  content?
* Anything with a stable bulk/offline distribution, given we are offline at
  tutoring time?

### Q6. How do we make hydration faster without losing quality?

At ~90 s/concept this is the dominant cost in the product.

* Where does the time actually go for a **256-expert MoE with ~3B active
  parameters at IQ3_S**, and what are the real levers? Specifically: does an MoE
  of this shape behave like a 3B model for throughput and a 35B model for
  memory, and what does that imply for batching? Is expert-routing overhead or
  memory bandwidth the binding constraint on Apple Silicon?
* Prompt caching / KV reuse across concepts that share a lesson's context;
  speculative decoding; MLX vs the Ollama/llama.cpp path; concurrency on one
  24 GB box; whether a **larger `num_ctx` than 16384** would pay for itself by
  enabling batching, and what it costs in RAM at IQ3_S.
* Is **IQ3_S the right quantisation** for content that must be factually
  correct, or is aggressive quantisation itself contributing to the false-claim
  problem in 2.9? A higher-quality quant that is slower per concept but needs
  fewer correction retries could be faster end to end.
* Is there a **quality-improving** speedup — e.g. generating a whole lesson's
  concepts in one call the way the skeleton builder now does per module, which
  cut calls *and* improved coherence?
* What should we refuse to trade away? We have measured that a reasoning model
  with thinking enabled consumed the entire token budget and returned **empty**
  content, so naive knob-turning here has already cost us a full debugging cycle.

### Q7. How do we stop the same idea being re-taught across many lessons?

**This is the failure mode we are most worried about and least able to detect.**

A real course teaches dice probability *once*, then uses it. A generated one can
teach it five or more times, because each lesson is hydrated independently and
each one reasonably decides the learner needs the underlying idea explained
before the lesson's own topic makes sense.

Concretely, for a Dungeon Mastering course: *"Probability in Combat"*,
*"Encounter Difficulty Math"*, *"Advantage and Disadvantage"*, *"Skill Check
Design"* and *"Damage Expectation"* are five perfectly reasonable, entirely
distinct lesson titles — and all five will plausibly open by explaining what a
d20 distribution is.

**Everything we currently have is title-level and therefore blind to this:**

* `_is_duplicate` — normalised title comparison, per level, with domain-word
  exclusion so "Causal Graphs" and "Causal Models" are not false positives
* `check_filler` — near-duplicate titles, repeated stems ("Introduction to" six
  times), shared-word saturation within a lesson
* `check_uniformity` — duplicate rate ≤ 5%

Not one of them can see two differently-titled lessons teaching the same
content. The five titles above pass every check we own.

Questions:

* **How is this measured?** Ideally with an instrument that has no model in it,
  since our LLM judge swings ±1.4/5. Is there a workable signal — n-gram or
  embedding overlap between concept bodies, extracted-claim overlap, dependency
  attribution? What threshold separates *legitimate reinforcement* from
  *redundant re-teaching*?
* **How much repetition is correct?** Real courses deliberately spiral, and our
  companion brief found that spaced re-exposure is the mechanism that makes
  material stick. So the target is not zero. What does a real curriculum's
  repetition profile actually look like — how many times is a foundational idea
  legitimately revisited, and in what form (re-taught vs. assumed vs. cited)?
* **What is the fix?** Options we can see: a concept-level "already taught"
  ledger consulted at hydration; explicit prerequisite links so a lesson *cites*
  rather than re-explains; hydrating a whole lesson or module in one call
  (which is what fixed an analogous problem in the skeleton builder); or a
  post-pass that detects and collapses. Which of these actually works, and which
  break under a 16k context?
* **What should a lesson do instead of re-teaching?** A one-line callback, a
  link, an assumed-knowledge note, a brief retrieval prompt? The tutor is
  Socratic, so "briefly ask them to recall it" may be a better move than either
  re-teaching or silence.

### Q8. How does the hydrator become aware of the course as a whole?

Today **each concept is hydrated in isolation.** The generation prompt sees the
concept title, its objectives, its Bloom level, its module and lesson names, and
the research payload — but not the rest of the course. It does not know what
was taught in module 1, what module 6 will assume, or that this idea appears
again in three lessons' time. Q7 is one consequence of that; hollow and
misaimed content is another.

The obvious fix is not available: **the whole course does not fit.** A parity
course is 104–135 concepts against a served context of **16,384 tokens**, which
must also hold the section template, research payload, and the generated output.

So the question is what *compressed representation* of the course should
accompany each hydration call, and how it is maintained:

* What belongs in it — a running ledger of taught concepts, a prerequisite
  graph, per-module summaries, extracted claims, the objectives alone?
* **How is it compressed, and by what?** Rolling summarisation costs an LLM call
  per step and drifts; a structured index does not drift but is coarser.
* Is this better solved by **retrieval** (fetch the k most related already-taught
  concepts for each hydration) than by carrying a global summary? If so, what
  index — we already have FTS5, and an unused MiniLM.
* Does **ordering** solve part of it for free? Hydrating in teaching order means
  the ledger is always complete for everything prior. What does that cost in
  parallelism, given hydration is already ~90 s/concept and cannot easily
  afford to be serial?
* **Precedent from our own skeleton builder:** consolidating a module's whole
  subtree into ONE call reduced calls *and* improved coherence, because the
  model could see the module as a unit. Does the same argument extend to
  hydrating a full lesson's concepts in one call — and where does it break, at
  a lesson, a unit, or a module, given 16k?
* What is the right split between context the model is *given* and constraints
  *checked afterwards* — our repeated finding is that prompt instructions do not
  hold (0/5) while correction rounds naming a specific offender do (5/5).

### Q9. What repos or Python libraries would improve this stage?

Specifically for: document/section-aware storage, hybrid retrieval, structured
LLM output with retries, factual verification against sources, Markdown
schema validation, incremental/resumable long pipelines, evaluation
harnesses for generated educational content, **near-duplicate / redundancy
detection across documents** (Q7), and **context compression or hierarchical
summarisation for long-document generation** (Q8).

We prefer **small, well-maintained, offline-capable** dependencies. Naming a
library is less useful than saying which of our specific problems it solves and
what it would replace.

---

## 4. Hard constraints

| constraint | detail |
|---|---|
| **Offline at tutoring time** | no cloud APIs during a session; hydration may use the network |
| **One local model, 24 GB** | `nail-35b-a3b-ctx`, MoE 34.7B/~3B active, IQ3_S, **`num_ctx` 16384** (model max 262144), ~142 s cold load, ~90 s per concept |
| **Mac Mini M4 Pro** | Apple Silicon; MLX available; no CUDA |
| **SQLite is incumbent** | WAL, schema v10, migrations exist; replacing it needs a real argument |
| **Self-hosted only** | SearXNG is ours; no paid APIs |
| **We have already removed two storage bets** | KuzuDB and ZIM. Adoption cost is real and we have paid it twice |

---

## 5. What would make the answer most useful

* **A stated recommendation**, not a survey. Where the literature or practice is
  contested, take a position and say why.
* **Numbers we can check before committing** — break-even sizes, expected
  latency, expected disk. We instrument heavily and prefer instruments with no
  model in them.
* **What to measure**, including what would look like an improvement while
  actually being a regression. This project has repeatedly found that its own
  measurements were the problem: a course once scored 100% coverage while being
  an unteachable alphabetical index.
* **Rank the questions.** If Q7 (re-teaching) and Q8 (whole-course awareness)
  turn out to have one shared answer, say so — we suspect they might, since
  both are consequences of hydrating concepts in isolation.
* **What to abandon.** If part of the current design — the section template, the
  word targets, one-file-per-concept, FTS5, the unused MiniLM — is wrong, say
  so plainly.
* **Failure modes that would be invisible from inside**, especially any where
  content quality degrades without the pipeline reporting it. Our fact-check and
  depth contract both run *inside* the same system that generated the content.
