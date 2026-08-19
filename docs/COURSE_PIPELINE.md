# The course-building pipeline — current state

_Written 2026-08-19, after the multi-day sprint that rebuilt the skeleton
builder, content hydration, asset collection, the teaching loop, and the
book-to-course path. This is the map; each phase's detail lives in its own
plan document._

---

## Two pipelines, opposite philosophies

| | **Researched course** | **Book course** |
|---|---|---|
| trigger | a topic typed by the user | a file uploaded by the user |
| structure | **invented** from research, sized to a calendar | **read** from the book — the author's divisions dominate |
| shape | modules → units → lessons → concepts, school-shaped bands | textbook: chapters→modules, sections→lessons · other books: chapters→lessons, no invented modules, one lesson per chapter **always** |
| content source | research service + open APIs | **the book itself**, chapter by chapter, plus research |
| quality gate | `skeleton_qa` (coverage, sequencing, school shape…) | `book_course_qa` (linkage, order, naming, density) |
| stretch control | `scope_fit` + sourceless research loop | not applicable — the book *is* the scope |

They share everything downstream: storage, the taught-concepts ledger, the
depth contract, redundancy correction, retained sources, spoken maths, and the
asset phase.

---

## The researched path, stage by stage

1. **Evidence** — `curriculum_brief` sweeps Wikibooks/Wikiversity/OpenStax/IA;
   `_partition_brief` splits sources at `GROUNDING_RELEVANCE`: at-or-above may
   ground the course, below is **supplementary** (hydration only, share capped
   *in claims*). A brief with nothing above the bar is **sourceless**, which
   routes to the iterative research loop — the model proposes queries, measured
   coverage decides the exit, never the model's satisfaction.
2. **Scope** — `scope_fit` compares requested size against what the evidence
   supports, *before* generation, and warns rather than padding. This is the
   smart-stretcher: a thin subject gets an honest disclaimer, not filler.
3. **Skeleton** — one call per module subtree; correction rounds naming the
   specific offender (measured 5/5 vs 0/5 for prompt-only); coverage backfill
   from the best syllabus; school-shape bands enforced post-generation because
   **no JSON-schema minimum binds on /v1**.
4. **Hydration** — per concept: ledger retrieval (what the course already
   taught, hybrid bge-m3 + lexical), generation against the depth contract,
   redundancy correction, claims + sources retained, teaching object + spoken
   maths stored. Prompt is invariant-first for prefix caching.
5. **Assets** — whole-course pass: licence fail-closed, role required on every
   attachment, cross-source arbitration by perceptual hash, wallpaper sweep.
6. **Gates** — `skeleton_qa` (structure), `hydration_qa` (content: redundancy,
   substance, hollowness, grounding, supplementary share, depth; truth advisory
   via MiniCheck). Median-of-3 discipline: never trust one run.

## The book path, stage by stage

1. **Parse** — `book_reader.open_book`: EPUB (spine-ordered, chapters split
   within packed documents), PDF (ToC read as a *ladder* — the deepest level
   with enough entries is the content level), text/Markdown. Front matter that
   impersonates a chapter is dropped on arithmetic (a leading "Chapter XIII"
   followed by "Chapter I" is not where the book starts). Gutenberg boilerplate
   filtered; chapter furniture ("Introduction", "Key Terms", "Summary") is not
   a lesson.
2. **Shape** — `book_skeleton.choose_shape`, always with a recorded *why*:
   hierarchical ToC → textbook ladder; parts → units; otherwise one lesson per
   chapter under a container module. **A long chapter earns more concepts
   (2–6 by length), never more lessons.**
3. **Name by reading** — `book_source.attach_concepts`: one call per lesson,
   the model reads the chapter (whole ≤45k chars, **digested** above that —
   map-only, reading order preserved, cached) and names its concepts and, where
   the book only numbered the chapter, the chapter itself ("Chapter 2 — Mr
   Bennet Visits Mr Bingley"). An authored title is never altered.
4. **Hydrate from the text** — each concept carries its chapter; the hydrator
   reads that chapter's relevant passage (structural link, no search) so
   content is written *from* the book, not from the model's memory of it.
5. **Gate** — `book_course_qa`: every lesson linked to a real chapter, in the
   book's order, nothing unnamed, density in band. Recorded on the course;
   failures warn with named checks rather than aborting.

---

## Progress the user sees (status stream)

Researched: `RESEARCH:*`, `CHECK:SYLLABUS_EVIDENCE:*`, `CHECK:SCOPE:*`,
`STRUCT:*`, `STRUCT:HYDRATING/HYDRATED`, `STRUCT:REDUNDANT`, `ASSET:*`,
`ASSET:SWEPT`.

Book: `BOOK:PARSED:<fmt>:<chapters>:<parts>:<words>`, `BOOK:SHAPE:<shape>:<why>`,
`BOOK:READING:<n>:<total>:<lesson>` (chapter-by-chapter counter — a spinner
with no counter is indistinguishable from a hang), `BOOK:WARN:CHAPTER_SKIPPED`,
`CHECK:BOOK_QA:<verdict>`.

Failure paths speak: unreadable file, unsupported format, extraction failure
and QA failure each produce a user-visible message naming the problem, never a
silent fallback to a filename-derived course.

---

## Speed, matched to the measured hardware

Apple M4, 24 GB. Measured: 30.1 tok/s decode, 247 tok/s prefill, ~15.0 GB safe
ceiling (a cliff, not a slope), model 13.51 GB at `num_ctx` 32768.

* **Prefix caching** — invariant material leads every prompt; measured effect
  on concept naming: 152 s → 32 s for five chapters.
* **Digest cache** — a >45k-char chapter is digested once (150 s), not twice.
* **32k over 64k** so the verifier and embedder fit co-resident instead of
  paying ~142 s reloads.
* **Serial hydration by default** (`bg_slots=1`) because Ollama serialises and
  contention blew the admit timeout.
* **What is deliberately NOT traded**: correction rounds, the depth contract,
  fact-check sampling, ledger writes. Every one is post-generation enforcement,
  and prompt-only enforcement measured 0/5.

---

## What the product may honestly advertise

| claim | true? | backed by |
|---|---|---|
| build a professional-quality course on a topic | yes, structurally — `skeleton_qa` PROFESSIONAL, median-of-3, 100% syllabus coverage | measured on MIT 18.06 |
| upload a textbook, get a course shaped like it | yes — chapters→modules, sections→lessons, concepts named from the sections | measured on a 1,486-page OpenStax export |
| upload *any* book (EPUB/PDF/text) and get a course from **its** content | yes — one lesson per chapter, concepts and content read from each chapter | measured on Pride & Prejudice and The Art of War |
| a degree programme for any subject | shaped like one (`degree_quality`), with an honest label when model-proposed and a scope warning when thin | Economics vs D&D comparison |
| the tutor speaks mathematics | yes — ClearSpeak strings pre-generated at hydration | `math_speech`, 0 leftover LaTeX in suite |
| images are licensed and safe | licence fail-closed + curated collections + required role; **no pixel-level gate is claimed** | asset phase |
| content is fact-checked | **advisory only** — MiniCheck flags for review; it caught 3/3 seeded falsehoods but false-flags inference | `claim_verifier` seed run |

The last row is the honest edge: we do not advertise verified truth, because
the instrument that would carry that claim is not yet gate-grade.

---

## Known limits, stated rather than hidden

* Hydration quality gates are built but **not yet calibrated against a real
  full build** — thresholds may move on first contact.
* The book path's title fallback can echo a lesson's first concept.
* `Self-Help`'s `CHAPTER 1.I.` volume numbering parses but is unverified
  against the actual book.
* Lesson batching exists behind `HELGA_LESSON_BATCH=1`, unproven on a real
  build.
* MiniCheck stays advisory until span-level claim attribution lands.
