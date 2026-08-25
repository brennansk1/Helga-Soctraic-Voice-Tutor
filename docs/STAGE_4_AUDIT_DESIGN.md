# Stage 4 — The Audit

The last thing that happens before a course is allowed to be taught, and the
last chance to catch anything the first three stages let through.

Stages 1–3 build: skeleton, syllabus audit, hydration. Each has gates, and
every gate asks a question about the concept in front of it. Stage 4 asks the
questions none of them can: **is this true, is it complete, and does it agree
with itself across the whole course.**

---

## Why it exists, stated as evidence

Not as a good idea. As the record of what got through.

| What shipped | Which gate should have caught it | Why it didn't |
|---|---|---|
| "ORDER BY ... ASC places NULLs first" (backwards) | none | no gate asked whether a sentence was true |
| Same NULL error in two more concepts | none | a human audit missed them too; execution found them |
| "NULL = NULL is true" | `fact_check.py` | sampled 34%, and asked the model that wrote it |
| `## Core Explanation` = "X is a key concept in sql." | depth contract | it met the word count with a stub in another section |
| Model deliberation in taught text | none | added afterwards, at the write path |
| 21% of citations off-topic | relevance gate | ran at fetch time, not against the finished text |
| One concept calling a default deterministic, then non-deterministic | none | no gate reads two sentences together |
| Two concepts teaching opposite NULL orderings | none | **no gate has ever read two concepts together** |

The last two are the reason this must be a stage and not another per-concept
check. Self-contradiction and cross-concept contradiction are invisible to
anything that validates one file at a time.

And the deeper finding: `sources.passage` was written **empty 529 times out of
529**, under a schema comment saying a claim "cannot be verified against a
passage that has expired". The evidence to check any of this against did not
exist. Stage 4 is not buildable until that is fixed — which is why Pass 0 is a
pass and not a prerequisite footnote.

---

## Six principles, each from something measured

**1. Deterministic before probabilistic.**
Anything that can be settled by running it, parsing it, or querying it is
settled that way, and a model never sees it. The SQL verifier built on
2026-08-25 found six real errors with zero false positives and no model in the
loop. A model asked the same questions found none in 38 attempts.

**2. Binary checklist items, never scalar scores.**
HelgaBench swings **±1.4 out of 5 between identical runs on identical input**.
A gate on "quality ≥ 3.5" is a coin flip. The literature agrees —
decomposing into independently verifiable checklist items outperforms scalar
scoring — but the local measurement is the reason. Every Stage 4 question is
answerable yes/no with a quotation.

**3. Flag and regenerate; never edit in place.**
A wrong check that regenerates costs one generation. A wrong check that edits
**writes a new falsehood into the lesson**. Three separate attempts at the SQL
patterns today convicted a correct sentence; that class of error is normal, not
exceptional, and the architecture has to be safe when it happens.

**4. Unchecked is not clean.**
Every result carries what was checked, not only what was found. `fact_check.py`
reported clean over a course with seven errors because "no findings" and "no
coverage" were the same output. Stage 4 reports `supported / contradicted /
no-evidence / not-applicable` as four separate counts, always.

**5. Evidence is persisted, never re-derived.**
The research cache has a 24h/7d TTL. Any design that reads it is a design that
works on the day of the build and silently degrades to nothing afterwards.

**6. Escalate by cost.**
Cheap checks run on everything. Expensive checks run only on what cheap checks
could not settle. The 35B builder is touched only for concepts that a small
model failed to repair.

---

## Shape

```
   hydration complete
          │
   ┌──────▼─────────────────────────────────────────────┐
   │ PASS 0 — evidence assembly            (no model)   │
   │   persist passages · index · corpora · gap-fill    │
   └──────┬─────────────────────────────────────────────┘
   ┌──────▼─────────────────────────────────────────────┐
   │ PASS 1 — deterministic gates          (no model)   │
   │   structure · execution · citations · cross-course │
   └──────┬─────────────────────────────────────────────┘
          │  everything still standing
   ┌──────▼─────────────────────────────────────────────┐
   │ PASS 2 — grounding            (checker, ~1–4 GB)   │
   │   decontextualise · retrieve · entail              │
   └──────┬─────────────────────────────────────────────┘
          │  only what failed
   ┌──────▼─────────────────────────────────────────────┐
   │ PASS 3 — repair                (editor, ~4–8 GB)   │
   │   revise w/ evidence → re-check → escalate → mark  │
   └──────┬─────────────────────────────────────────────┘
   ┌──────▼─────────────────────────────────────────────┐
   │ PASS 4 — verdict & report                          │
   │   coverage · per-concept · gate to "ready"         │
   └────────────────────────────────────────────────────┘
```

---

## Pass 0 — Evidence assembly

Nothing downstream can work without this, and today none of it exists.

**0.1 Persist passages.** Done (`a1e3971`): `_citation()` now carries up to
4000 characters of source text, and the ledger's `sources.passage` stores it.

**0.2 Retain the full text, not just the cited excerpt.** 4000 characters is
enough to check a claim, not always enough to *find* the sentence that settles
it. Long sources (textbooks, doc chapters) get a `source_documents` table
storing the full retrieved text once, with `sources` referencing it. Textbooks
are the case that matters: a Wikibooks chapter is the single most useful
grounding document a course has and is exactly what a 4000-character cap
truncates.

**0.3 Authoritative corpora, downloaded once.** Per-domain, offline, licence-
checked. PostgreSQL ships an official offline HTML tarball for precisely this
use, so it is a download rather than a scrape — unlike OpenStax, whose content
API is robots-disallowed. Corpora live under `data/corpora/{domain}/` with a
manifest recording source URL, licence, version and fetch date.

**0.4 One index.** Passages, documents and corpora are chunked and embedded
with **bge-m3**, which is already resident at 633 MB and currently used for
nothing in the audit path. Chunk on heading and paragraph boundaries; keep
`(source_id, char_start, char_end)` so a finding can quote the exact span.

**0.5 Gap-fill through the research service.** A claim whose retrieval returns
nothing above threshold is not a failure — it is a research request. Stage 4
calls `/api/research` with the claim's own terms (not the concept title, which
is what made research return generic pages), stores whatever comes back through
0.1–0.2, and re-runs retrieval once. A second miss is recorded as
`no-evidence`, which is an honest verdict and not a defect.

> **Deadline note.** The two existing SQL courses have no passages, and their
> cache entries expire ~08:00 tomorrow. Either backfill from cache before then,
> or accept re-running research for those two courses.

---

## Pass 1 — Deterministic gates

No model. Runs in seconds over a whole course. Each item is yes/no with a
quotation.

**1.1 Structure and depth.** Re-run `validate_concept()` and
`content_guards.inspect()` over the *stored* file. Both already exist and both
run at the write path — but the write path is not where a course is finally
assembled, and re-running is cheap. This catches anything written by a path
that bypassed the gate, which is how the stub explanations got in.

**1.2 Required sections.** `## Core Explanation`, `## Misconceptions`,
`## Analogies`, worked example, citations — presence, and non-triviality
(a section under a word floor is missing, not present).

**1.3 Executable claims.** `sql_ground_truth.check_markdown()` and its
successors. Domain-routed: SQL claims to a live engine, arithmetic to an
evaluator, unit conversions to a converter, dates to a calendar. This tier
grows by adding probes, and every probe carries the query that settles it.

**1.4 Citation integrity.** For each cited source: the row exists, a passage is
stored, the URL is well-formed, and the passage's subject overlaps the
concept's. This is where "Met Museum cited in a SQL course" dies — measurable
without a model, because the source's own title and text are on hand.

**1.5 Course-level coherence.** The checks nothing has ever run:
- duplicate or near-duplicate concept titles
- a concept whose prerequisites appear later in the path
- `Part 2` and other splitter artefacts in the curriculum path
- **claim-pair contradiction candidates**: embed every `taught_claims` row,
  retrieve near-duplicate claims across concepts with opposing polarity
  markers, and hand only those pairs to Pass 2. This is how two concepts
  teaching opposite NULL orderings gets caught, and it costs one index scan.

---

## Pass 2 — Grounding

Only claims Pass 1 could not settle. One small model.

**2.1 Decontextualise.** "It sorts them last" is unverifiable. Each
`taught_claims` row is rewritten to stand alone, carrying its concept title —
SAFE's self-contained step, and the reason naive claim extraction under-reports.

**2.2 Retrieve.** Top-k chunks per claim from the Pass 0 index, preferring
corpus > textbook > primary > wiki > web.

**2.3 Entail.** For each (claim, evidence) pair: **supported / contradicted /
not-enough-evidence**. A purpose-built grounding model, not a general one —
this is pure prefill with a three-token answer, which is the workload a
0.8–4B checker is built for and a 35B MoE is wasted on.

Model choice, licence-first, because an AGPL dependency in an Apache repo is
already an open finding:
- **MiniCheck-DeBERTa-v3-Large / Flan-T5-Large** — MIT, ~0.4–0.8 GB. Default.
- **Bespoke-MiniCheck-7B** — stronger, but commercial use requires a licence
  agreement. Not usable without one.
- **Paladin-mini (3.8B, Phi-4-mini based)** — newer, strong on numeric and
  closed-domain reasoning; licence to be confirmed before use.

**2.4 Cache the prefix.** Many claims share one grounding document. Checking
per-document rather than per-claim reuses the document prefix across every
claim in the concept — the single largest efficiency lever in the design, on a
machine where **80% of turn latency is prefill**.

---

## Pass 3 — Repair

Only what failed. Never a blanket rewrite.

**3.1 Revise minimally.** RARR's finding is the design constraint: revise the
claim to agree with the evidence *while preserving the original as much as
possible*. A repair that rewrites a good explanation to fix one sentence has
damaged the lesson to fix a footnote. The editor receives: the concept, the
failing claim, the evidence span, and an instruction to change the minimum.

**3.2 Re-check.** The repaired file goes back through Pass 1 and the affected
claims through Pass 2. **This is what makes a small editor safe** — a bad fix
is caught by the same checker that flagged the original, so the worst case is a
wasted generation rather than a new falsehood in front of a learner.

**3.3 Escalate.** Still failing after N attempts → regenerate the concept with
the 35B builder, evidence in the prompt. Still failing → mark the concept
`needs_review`, record why, and **exclude it from teaching** rather than serve
it. A concept withheld is a gap; a concept served wrong is a lie.

**3.4 Add what is missing.** A concept missing `## Analogies` is a generation
request, not an error — the editor writes the section with the concept and its
evidence in front of it, and it re-enters at 3.2 like any other repair.

---

## Pass 4 — Verdict

**4.1 The report** — per course and per concept:

```
claims            1,204
  supported         881   (73%)
  contradicted        6   → 5 repaired, 1 escalated, 0 unresolved
  no-evidence       291   (24%)   ← honest coverage, not a pass
  not-applicable     26
concepts            83
  clean              77
  repaired            5
  needs_review        1   con_c3e1094e — DESC NULL ordering, 2 repairs failed
executable probes applied: 5 of 5   engine: PostgreSQL 16.13
corpora: postgresql-16 (2026-08-25) · 3 textbooks · 41 primary
```

**4.2 The gate.** A course reaches `ready` only when: zero unresolved
contradictions, every concept passes Pass 1, and grounding coverage clears a
floor. Otherwise `needs_review`, with the list. `needs_review` is visible in
the UI as itself — not as `failed`, and never as `ready`.

**4.3 Persisted.** The verdict is stored, not printed, so "what was checked"
survives the build and can be shown to a learner who asks why a concept is
missing.

---

## Efficiency

The audit must not cost more than the build.

| lever | effect |
|---|---|
| Pass 1 settles most defects with no model | the majority of findings cost ~0 |
| per-document prefix caching in Pass 2 | one prefill amortised over every claim in a concept |
| checker ~0.4–4 GB instead of 13.5 GB | audit runs while the builder stays resident, no unload churn |
| repair only on failure | expensive tier sees single-digit percentages |
| escalation to 35B only after a small model fails | full coverage at a fraction of full-model cost |
| runs when nobody is learning | the gate now narrows background to one slot during a session |

Three resident models — bge-m3 (0.6 GB) + checker (~1–4 GB) + editor (~4–8 GB)
— fit inside the 13.5 GB the builder alone occupies, and none of them has to be
unloaded between passes. Measured today: reading a 16 GB model under load runs
at **84 MB/s**, so a design that swaps models between stages pays minutes per
swap. This one swaps nothing.

---

## What it cannot catch

Stated because a catch-all that overstates itself is the failure it exists to
prevent.

- **Claims with no evidence anywhere.** Reported as `no-evidence`, never as
  supported. Coverage is a number in the report, not an implication.
- **Pedagogy.** Whether an explanation is *clear*, whether an analogy
  illuminates, whether the ordering teaches well. Checklist items can ask
  whether an analogy is present, not whether it is good.
- **A wrong authoritative source.** If the corpus is wrong, the audit agrees
  with it. Execution is the only tier immune to this, and it covers only
  computable claims.
- **Anything outside a probe or a corpus.** Domain coverage is exactly the
  domains someone has built for.

---

## Phasing

Each phase is independently useful and independently shippable.

| phase | scope | unblocks |
|---|---|---|
| **A** | Pass 0.1–0.2 + Pass 1 (structure, execution, citations) | catches today's defects with no model at all |
| **B** | Pass 1.5 course coherence | cross-concept contradictions, first time ever |
| **C** | Pass 0.3–0.5 + Pass 2 grounding | the general fact check, all domains |
| **D** | Pass 3 repair + escalation | it fixes rather than reports |
| **E** | Pass 4 gate + report in the UI | `ready` starts meaning something |

Phase A alone would have caught six of the seven audit errors.
