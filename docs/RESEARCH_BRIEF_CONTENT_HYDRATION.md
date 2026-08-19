# Research brief — content hydration, storage, and retrieval

**For:** Claude Research
**From:** Project Helga
**Question in one line:** For an offline AI tutor that generates its own
course content, what should be *stored*, in *what format*, at *what size*, and
*how retrieved* — and how can the generation be made faster without losing
quality?

> Companion to `RESEARCH_BRIEF_LONG_HORIZON_LEARNING.md`, which covered
> scheduling and retention. This one covers the layer beneath it: the content
> those sessions actually teach from.

---

## 1. What the system is

**Helga** is an offline, self-hosted Socratic AI tutor on a single **Mac Mini
M4 Pro, 24 GB**, with no cloud dependency at tutoring time.

* **LLM:** one local model via Ollama, `nail-35b-a3b` (MoE, 34.7B total /
  ~3B active). One model in memory at a time.
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

### 2.2 Quality enforcement that already exists

* **Depth contract** — a generated concept is checked and regenerated if too
  thin (`HELGA_ENFORCE_DEPTH`, default on, 2 retries).
* **Grounding threshold** — below a minimum, content is not presented as
  verified.
* **Fact-check pass** with a correction hint.
* **Correction rounds beat prompts.** Measured repeatedly in this codebase:
  prompt-only enforcement of a rule changed nothing 5/5, while one correction
  round *naming the specific offender* fixed it 5/5. Any recommendation that
  amounts to "put it in the prompt" should say why it would work here.

### 2.3 Caching that already exists

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

### 2.4 The cost, measured

* **~90 s per concept** hydrated on `nail-35b-a3b` (3 concepts in 269 s).
* A parity-sized course is **~104-135 concepts** → **~2.5-3.5 hours** per
  course.
* A bachelor's programme is ~40 courses → **~5,400 concepts**.

That number is the reason question 6 exists. At 90 s/concept, a full degree's
content is ~135 hours of generation.

### 2.5 Supplementary-source policy (just implemented)

Sources are now split by relevance: only sources at or above a grounding bar may
shape structure or be measured against; weaker but related sources are labelled
**supplementary** and passed forward *specifically to hydration*, capped at a
minority share of the course. The reasoning was that "does this book speak for
the subject?" is a question a weak source always fails, while "does this passage
serve **this concept**?" is narrower and one it can legitimately pass.

**We would like this reasoning stress-tested.** It is currently an argument, not
a measurement.

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
* How should this interact with the caching in 2.3 — is the cache the right
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

* Where does the time actually go for a MoE model of this size, and what are the
  real levers — batching, prompt caching / KV reuse across concepts sharing
  context, shorter targets, structured output, speculative decoding, quantisation
  trade-offs, concurrency on a single 24 GB box?
* Is there a **quality-improving** speedup — e.g. generating a whole lesson's
  concepts in one call the way the skeleton builder now does per module, which
  cut calls *and* improved coherence?
* What should we refuse to trade away? We have measured that a reasoning model
  with thinking enabled consumed the entire token budget and returned **empty**
  content, so naive knob-turning here has already cost us a full debugging cycle.

### Q7. What repos or Python libraries would improve this stage?

Specifically for: document/section-aware storage, hybrid retrieval, structured
LLM output with retries, factual verification against sources, Markdown
schema validation, incremental/resumable long pipelines, and evaluation
harnesses for generated educational content.

We prefer **small, well-maintained, offline-capable** dependencies. Naming a
library is less useful than saying which of our specific problems it solves and
what it would replace.

---

## 4. Hard constraints

| constraint | detail |
|---|---|
| **Offline at tutoring time** | no cloud APIs during a session; hydration may use the network |
| **One local model, 24 GB** | one model resident; ~90 s per concept measured |
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
* **What to abandon.** If part of the current design — the section template, the
  word targets, one-file-per-concept, FTS5, the unused MiniLM — is wrong, say
  so plainly.
* **Failure modes that would be invisible from inside**, especially any where
  content quality degrades without the pipeline reporting it. Our fact-check and
  depth contract both run *inside* the same system that generated the content.
