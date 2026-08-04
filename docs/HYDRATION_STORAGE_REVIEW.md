# What hydration stores, and what actually reaches the tutor

*Review date: 2026-08-04. Measurements are against a mastery-4 concept document
built to the exact template the hydrator emits (839 words, 4,669 characters) —
reproduced in `tests/common/test_concept_doc.py` as `DOC`.*

## The short version

The concern that prompted this review was that the `.md` files are too thin.
They are not. A mastery-4 concept carries fifteen sections: metadata, learning
objectives, prerequisites, mastery criteria, a core explanation, key facts, a
worked example, misconceptions, edge cases, three Bloom-banded Socratic hooks,
two analogies, a named governing result, a derivation, an exercise and cited
sources. Course-level records add a depth-contract verdict, a fact-check
verdict, level calibration, a grounding verdict and an asset manifest. There is
a per-concept provenance row in SQLite recording which sources and which model
produced it.

The problem is the opposite of thin. **The pipeline stores a great deal and
delivers very little of it to the models that teach from it.** Every defect
below is of the same shape: something expensive is computed, written correctly,
and then read by nobody — or read through a filter that removes precisely the
expensive part.

---

## 1. The tutor could not see the explanation *(fixed)*

`_redact_context_for_tutor` deleted `Core Explanation`, `Key Facts` and
`Real-World Examples` from the tutor's context. The stated purpose — GAP 6 — was
to stop the model reading the answer out loud.

It applied to the **lecturer** as well, and a micro-lecture's entire job is to
explain the concept. The lecturer was handed misconceptions and hooks with the
explanation removed, and then the system prompt told it:

> *5. Fill In The Gaps: … If the reference text lacks sufficient detail to
> properly teach the concept, fill in the gaps with accurate information …*

So the pipeline spent a research call, a broadened-query retry, a depth-contract
enforcement loop and a sampled fact check producing a grounded explanation, hid
it from the model that teaches, and then instructed that model to invent one.

The sampled fact check is the sharpest illustration: it exists to catch false
technical claims — a `substance_check` found them in 50% of sampled concepts —
and it only ever edits sections the tutor could not read.

**Measured on `DOC`:**

| | reaches the lecturer |
|---|---|
| stored | 4,669 chars · 839 words |
| after the delete list | 3,345 chars · 572 words |
| after the `[:2000]` head cut | **2,000 chars · 335 words — 43% of stored** |

Sections that reached the lecturer: Metadata, Learning Objectives,
Prerequisites, Mastery Criteria, Misconceptions, Edge Cases, Socratic Hooks,
Analogies. Not one word of the theorem.

**Fix.** `services/common/concept_doc.py` replaces the delete list with a
per-mode selection on the same budget:

* **lecture** — Core Explanation, Key Facts, the worked example, the governing
  result, misconceptions, analogies. Metadata and Sources are dropped, because
  `- **Path**: Course > Module > Unit > Lesson` teaches nothing and was
  occupying the front of every truncated context.
* **socratic** — the same ground truth **minus the worked example**. That one
  section is the real spoiler the delete list was built for: a step-by-step
  solution is trivially easy to hand over. Facts and the explanation are not
  spoilers; they are what stops the questioner teaching an error, and what its
  grading compares against.

After the fix the lecturer gets 423 words of substance in 2,267 characters,
where it previously got 335 words of boilerplate and hooks in 2,000.

## 2. A head cut deleted exactly what higher mastery buys *(fixed)*

`Governing Result`, `Derivation` and `Exercise` are appended **last**, and only
at mastery ≥ 4 and ≥ 5. `context_text[:2000]` takes the front. So those three
were the first casualties of every truncation — a mastery-5 course cost far more
to build and delivered *less* substance to the prompt than a mastery-2 one.

**Fix.** `tutor_context()` packs by priority, not document order, so the first
thing dropped is the least useful section rather than the last one written. Each
section also carries a cap, so one runaway section cannot starve the rest, and
trims land on a line boundary — a section cut mid-sentence reads to the model as
an error it should continue.

## 3. The dense embedding was 100% boilerplate *(fixed)*

`build_dense_index` embedded `title + body[:512]`. On a real document, 512
characters covers:

```
# The Pythagorean Theorem
## Metadata
- **Bloom Target**: 4 (Analyze)
- **Depth**: 4
- **Path**: Euclidean Geometry > Triangles > Right Triangles > Metric Relations
- **Complexity**: core
- **Source**: research+llm
## Learning Objectives
…
## Prerequisites
Prior concepts: Similar Triangles, Area of a Rectangle,
```

Not one word of the explanation. Worse, every concept in a course shares its
`**Path**` line, so the whole course collapsed toward a single vector and the
nearest neighbour of any query was decided by position in the tree rather than
by meaning. Hybrid mode was actively worse than FTS5 alone.

**Fix.** `index_text()` selects Core Explanation, Key Facts, Learning
Objectives and the worked example, up to 1,200 characters, falling back to the
raw body when nothing parses so a stub is still findable.

## 4. The search index went stale, and deletions did not cascade *(fixed)*

`concept_fts` was populated in exactly two places: lazily, when found **empty**,
and by a full rebuild at the end of a course build.

* A second course built after the index was populated was invisible to search
  until something else forced a rebuild.
* Content rewritten mid-session — by `update_topic_context`, by session notes,
  by the asset collector's `## Visual Aids` write — stayed searchable only under
  its old text.
* `delete_course`'s cascade list, whose docstring says its purpose is that
  nothing is missing from it, omitted `concept_fts`, `concept_vec` and
  `hydration_provenance`. A deleted course kept answering searches, and the
  licensing record of content that no longer existed was kept indefinitely.

**Fix.** `SearchStore.index_concept()` upserts one row on every content save,
wired through a `CourseStore.on_content_saved` hook and guarded so an index
failure never costs the caller their content write. The three tables joined the
cascade list. Note that `concept_fts` is an FTS5 virtual table with no `UNIQUE`
constraint, so the upsert deletes explicitly first — `INSERT OR REPLACE` appends
there, and a concept edited five times would answer the same query five times.

## 5. Per-concept quality signals were written and never read *(fixed)*

`source_confidence` (computed per concept, retried once against a broadened
query when thin) and `llm_fallback` (a title generated to pad an empty lesson)
were both written into `structure.json` — and `get_flat_concepts()` dropped
them. A 0.12-confidence concept was taught in exactly the same confident voice
as a 0.9 one.

**Fix.** Both, plus `ordinal`, now survive `get_flat_concepts()`. The four
copy-pasted syllabus-queue dict literals collapsed into one `_queue_entry()`
builder — that duplication is *why* the fields went unused; adding one meant
editing four places, and the site that got missed simply taught without it.
`_grounding_note()` then tells the tutor to teach the shape of the idea and stop
short of specifics it cannot stand behind. It stays silent for well-grounded
concepts, so it does not become boilerplate the model learns to ignore.

---

## Not fixed — recommended, with reasons

### A. The research material is fetched, used once, and thrown away

`content_to_use` — up to 5,000 characters of fetched source text, the product of
a `/api/research_concept` call plus a broadening retry — feeds a single LLM call
and is then discarded. `## Sources` keeps a flat list of URLs with no claim-level
attribution, and `hydration_provenance` keeps the same list in SQLite.

Consequences: a concept cannot be re-hydrated without re-fetching (and the web
will have moved); the tutor cannot quote a source when a student asks "how do we
know that?"; and the fact checker cannot be re-run offline against the material
that produced the claim.

Suggested shape: `data/courses/{uid}/research/{concept_uid}.md`, capped at ~8 KB,
carrying the fetched excerpts with their source URLs interleaved. Roughly 300 KB
for a 36-concept course. **Not built here because it has no consumer today** —
building the store before the reader is how the previous five defects happened.
The reader to build first is "cite the source for this claim" in the tutor.

### B. Claim-level attribution does not exist

Sources are attributed per *concept*, not per *claim*. When the tutor asserts
something, nothing connects that sentence to the URL that grounded it. This is
the prerequisite for any real citation feature and for a cheaper fact check —
checking a claim against the passage that produced it is far cheaper than
checking it against the model's whole knowledge, which is what makes the current
check cost 8–10 minutes per concept and forces it to sample.

### C. `## Sources` is text, not data

The section is generated as markdown and re-parsed nowhere. Tier and confidence
are formatted into prose (`— encyclopedia (Tier 2)`, `*Source confidence:
0.71*`). The same facts already exist as structured rows in
`hydration_provenance`; the UI should read those rather than regex the markdown.

### D. The low-confidence marker is positionally fragile

The `> **Limited sources.**` blockquote is appended to `structured_md` *before*
`## Sources`, so it attaches to whatever section happens to be last at that
moment — `Exercise` at mastery 5, `Analogies` at mastery 3. It reads correctly
in the concept view but belongs in its own section or in the front matter, not
glued to the end of an unrelated one. `_grounding_note()` now covers the tutor
side of this; the learner-facing side is still positional.

### E. The pedagogy-fetch block is duplicated across the FSM

The misconceptions / analogies / mastery-criteria load appears verbatim in
several transition paths. `_queue_entry()` fixed the queue-construction half of
this family; the pedagogy half is still copy-pasted and will drift the same way.

### F. `rebuild_search_index()` is O(entire corpus) per course build

It walks every course and reads every concept file. With per-save indexing now
in place the full rebuild is a repair tool rather than the primary path, and the
call at the end of `hydrate_course_content` could be narrowed to the course just
built.

---

## Verification

`tests/common/test_concept_doc.py` (35), `tests/core/test_search_freshness.py`
(10) and `tests/core/test_grounding_note.py` (10). The search-freshness tests
were run against the pre-change tree first: 5 of the 10 fail there, which is the
point of writing them. Full suite: 1,212 passed against a baseline of 1,157,
with the same 8 pre-existing failures — 7 in `test_tutor_tools.py` from the
undeclared sympy/scipy/matplotlib dependencies, and `test_memory_guard.py`
reading 0 GB because this container mocks `psutil`.

The tests assert on **what reaches the model**, not on whether a function
returns a string — the defect they cover did not throw, did not log, and passed
every test in the suite.
