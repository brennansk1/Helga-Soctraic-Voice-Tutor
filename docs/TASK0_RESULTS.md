# Task 0 — rebuild on the current pipeline

_Run 2026-08-18. Model: `nail-35b-a3b`. Topic: "The Pythagorean Theorem",
College Course preset (scope 3, mastery 3, starting_from 2). Skeleton only —
criterion 6 scores the skeleton, so hydration is not needed._

## Verdict: the measurement did not complete, and that is the finding

The pre-committed decision rule in `AI_UNIVERSITY_DESIGN.md` R1 cannot be
applied, because **two wiring bugs prevented a valid coverage number from being
produced**. Neither is a quality verdict on the course. Both are exactly what
Task 0 existed to surface, and both block every quality claim in Part II.

## Finding 1 — a cold model silently disables the entire grounding chain

Two runs, same code, same topic. The only difference was whether Nail was
already resident.

| | cold | warm |
|---|---|---|
| build time | 570.5 s | **255.8 s** |
| curriculum evidence | **NONE — "Generating UNGUIDED"** | Wikibooks *Geometry* (31 ch) + OpenStax *Contemporary Mathematics* (14) |
| broadened subjects | `tried: ['The Pythagorean Theorem']` | `broadened via ['Mathematics', 'Geometry']` |

The cause is a chain of three silent steps:

1. The **parent-subject lookup** ("what discipline is this part of?") is the
   first LLM call of a build and gates the broadening.
2. During a cold load that call exceeded the **90 s timeout, three times**
   (`LLM Timeout after 90s (attempt 3/3)`). Nail's cold load was measured at
   ~142 s, so the timeout cannot be met from cold by construction.
3. The failure returned **no broader subjects**, and "the model did not answer"
   became "this topic has no broader subject." With only the narrow topic
   tried, no Wikibook matches, and the build proceeds UNGUIDED.

This is the **absent-vs-zero error** again, in the most expensive position yet:
a transient timeout silently disables the research chain that the last several
weeks of work exist to feed. The build still reports success.

It also explains something previously attributed to the research service: a
first-build-after-restart is exactly when this fires.

**Fixes required (design, not yet implemented):**
* Timeout must accommodate a cold load, or the model must be warmed before the
  first call — a 90 s budget against a ~142 s cold load is unsatisfiable.
* A failed broadening must be **degraded state, not an empty answer**. The
  builder should refuse to claim "no evidence exists" when it never got to look.
* **Cache the parent-subject lookup.** `docs/CACHING.md` already lists it as
  candidate 1 — "tiny, effectively deterministic, identical across rebuilds,
  safe to cache; pure win." Cached, this failure cannot recur after the first
  successful build.

## Finding 2 — the syllabus gate never receives the syllabus

Criterion 6 is described in `MODE_A_STATUS.md` as the gate's **external anchor**:

> Every other criterion in the gate is self-referential: an LLM judging output
> produced by an LLM… Only comparison against something written by a human who
> teaches the subject can do that.

On the warm run the builder had a real syllabus in hand — Geometry's 31 chapters.
The check then reported:

```
grounding    : "model-knowledge (WEAK — same model family wrote the course)"
topics_checked: 12
coverage_pct : 0            CHECK:SYLLABUS:INADEQUATE:0
```

`course_builder.py:1461` calls `check_structure(course_dict)` with **no reference
argument**, though `format_brief(brief)` is available in the same class. So the
gate's one non-self-referential criterion **ran self-referentially**: the same
model family that wrote the course judged its coverage from memory.

**The 0% is an instrument failure, not a course failure**, and it must not be
compared against the 42% baseline — the baseline used a different grounding
configuration, and comparing across a changed instrument is the documented
`helgabench_a0` error. The generated modules do not look like an 0%-coverage
course:

> Right Triangle Anatomy · Euclidean Distance and Area Relationships ·
> Pythagorean Equation and Algebraic Rearrangement · Direct Calculation of
> Missing Sides · Coordinate Geometry and Distance Formula Derivation ·
> Inverse Theorem and Triangle Classification

**Fix required:** pass the fetched outline into `check_structure` as the
reference. The evidence is already in the process; it is simply not handed over.

## Finding 3 — the structural ladder is far below target

Independent of the two bugs, the built structure is flat:

| | built | target (Part I ladder) |
|---|---|---|
| modules | 6 | 8 |
| units | **6** | 15 |
| lessons | **6** | 45 |
| concepts | 30 | 135 |

One unit per module and one lesson per unit. This is the same shape
`MODE_A_STATUS.md` criticised in the 42% course — "three modules of one lesson
each" — and it confirms the Part I analysis from a real build rather than from
arithmetic: **the course is ~21% filled against the parity target**, and the
missing structure is concentrated in the unit and lesson levels, which are
currently collapsing to one child each.

## What this changes

* **No quality claim from Part II can be made yet.** Condition 2 needs a working
  criterion 6, and criterion 6 is not currently measuring anything external.
* **The premise is not refuted.** Grounding demonstrably works when warm — two
  real syllabi were found and ranked correctly (Geometry 4.50 over Primary
  Mathematics 0.50). What failed was plumbing on either side of it.
* **Three fixes now precede any university work**, all small, all in the
  existing pipeline rather than the new design:
  1. cold-start / timeout / cache on the parent-subject lookup
  2. pass the outline into `check_structure`
  3. unit and lesson fan-out (the ladder)

Then re-run Task 0 and apply the R1 decision rule against a number that means
something.

---

# Task 0, second run — after the three fixes

_Same day. Topic changed to **Linear Algebra** so the result could be compared
against a real published course: **MIT 18.06** (OCW, Spring 2010)._

## The fixes work

| | before (broken) | after (fixed) | target |
|---|---|---|---|
| modules | 6 | 6 | 6–8 |
| units | **6** | **18** | ~15 |
| lessons | **6** | **54** | 45 |
| concepts | **30** | **155** | 144 |
| grounding | UNGUIDED (cold) | Wikibooks *Linear Algebra* + OpenStax *College Algebra* | — |
| criterion 6 grounding | `model-knowledge (WEAK)` | **`external`** | external |

**Fix 1 proved itself in production during this very run.** The LLM
parent-subject lookup timed out again, and the log shows the no-LLM path
catching it:

```
[SKELETON] parent subjects via Wikipedia categories: ['Linear algebra', ...]
[SKELETON] evidence for 'Linear Algebra' (broadened via ['Linear algebra', 'Numerical analysis'])
```

Before the fix that same timeout produced an UNGUIDED build reporting success.

## Coverage against MIT 18.06 — 70%, measured without a judge

The published 18.06 syllabus lists ten topic areas. Checking the generated
skeleton's full title set against them by **keyword matching only** — no LLM
anywhere in the instrument, so it cannot drift:

| | area | result |
|---|---|---|
| 1 | Elimination / LU factorization | HIT |
| 2 | Ax=b: column space, rank, nullspace | HIT |
| 3 | Bases & four fundamental subspaces | HIT |
| 4 | **Least squares & projections** | **MISS** |
| 5 | **Gram-Schmidt & QR** | **MISS** |
| 6 | Determinants | HIT |
| 7 | Eigenvalues / diagonalization | HIT |
| 8 | **Symmetric & positive definite** | **MISS** |
| 9 | Linear transformations / SVD | HIT |
| 10 | Applications | HIT |

**7/10 = 70%.**

**The misses are one coherent cluster, not scattered noise.** Least squares,
projections, Gram-Schmidt, QR, symmetric and positive-definite matrices are all
*orthogonality* — MIT's lectures 14–17 and 25–28. The generated course covers the
spine of the subject (spaces → elimination → determinants → eigenvalues) and
drops the orthogonality arc entirely. A scattered 70% would suggest random
weakness; a clustered 70% points at a specific missing region, which is
actionable.

## The finding that matters most: it is over-long AND under-covering

**54 lessons generated against MIT's 34 lectures — 59% more sessions — while
covering 70% of the syllabus.** The course is not short of room. It spends its
extra length going deeper on what it already chose rather than reaching the
material it missed.

This is a strong argument for the design's **copy-spine tier**: Strang's
*Introduction to Linear Algebra* (18.06's text) has chapters for exactly the
missing cluster. A course that followed the book's chapter order would not have
been able to skip orthogonality. Asking the model to *select* topics is where the
coverage is lost, and copying removes that step.

## Criterion 6's judge is confirmed unusable in its current form

With the reference now correctly supplied (`grounding: external`), the judge
still returned:

```
coverage_pct : 0    verdict: INADEQUATE
missing      : ['Vector Spaces', 'Linear Independence', 'Basis and Dimension',
                'Linear Maps', 'Matrix Representations', 'Determinants', ...]
```

The generated modules are titled **"Vector Spaces and Linear Combinations"**,
**"Basis and Dimension"**, **"Matrix-Vector Multiplication and Linear Maps"**,
**"Determinants and Inverses"**. The judge declared as missing four topics that
are literally module titles.

This is precisely the defect `syllabus_check.py` documents about itself —
*"'Potential outcomes' is declared missing from an outline whose first module is
literally 'Potential Outcomes'"* — and it is now reproduced with the reference
wired in, so it is not a plumbing problem. **The judge, not the plumbing, is the
remaining fault.**

Two instruments ran on the same course and disagreed completely:

| instrument | result | can it drift? |
|---|---|---|
| criterion 6 (LLM judge) | **0%, INADEQUATE** | yes — and demonstrably wrong here |
| keyword coverage vs MIT 18.06 | **70%** | no — string matching only |

The keyword result is verifiable by eye. **Criterion 6 must not gate anything
until its judge is fixed**, and the judge-free comparison should become the
primary coverage instrument — which is what the design already argued on
principle (condition 2: "key-term coverage… needs no judge at all, so it cannot
drift") and is now supported by measurement.

## Against the R1 decision rule

R1 pre-committed: **≥70% → grounding works, proceed with the design as written.**

70% lands exactly on that boundary, measured against real external ground truth
rather than a judge. Combined with the clustered nature of the misses and the
over-length finding, the reading is: **the pipeline is sound and the remaining
coverage gap is a topic-selection problem that the copy-spine tier is designed to
solve.** Proceed — with copy-spine promoted, not deferred.

---

# Task 0, runs 3 and 4 — backfill, and the variance finding

## Structure now hits the design target

| | run 2 (fixes) | run 3 (backfill) | run 4 (+ranking fix) | target |
|---|---|---|---|---|
| modules | 6 | 6 | 6 | 6–8 |
| units | 18 | 18 | 16 | ~15 |
| lessons | 54 | 52 | **47** | 45 |
| concepts | 155 | 152 | **135** | 144 |
| concepts/lesson | 2.87 | 2.92 | **2.87** | 3 |

Run 4 lands within 4% of the lesson target and exactly on the 135-concept figure
the Part I ladder derives. **The structural half of condition 1 is met.**

## Coverage improved — and varies

| run | coverage vs MIT 18.06 |
|---|---|
| 2 — fixes only | 70% |
| 3 — + backfill | **90%** |
| 4 — + ranking fix | **80%** |

**Three runs of identical code and prompt produced 70 / 90 / 80.** That spread
is not caused by the changes between runs — run 4's changes *narrowed* what
backfill may draw from, which is correct, and its coverage still landed between
the other two.

This is the same lesson this project already learned about judges, now about the
**generator**: a single build is not a measurement. Any future claim of the form
"change X improved coverage" needs **median-of-3 at minimum**, or it is reading
noise. The +20-point jump attributed to backfill in run 3 is, on this evidence,
somewhere between +0 and +20 — real in direction, unquantified in size.

## Backfill is drawing from the right book, and still catching poor chapters

Run 4's source selection worked exactly as designed:

```
[BACKFILL] using 2 of 3 syllabi (best: 'Linear Algebra' @ 9.667);
           weaker sources excluded from the coverage checklist
```

OpenStax *College Algebra* is now correctly excluded, and the off-topic
Exponential/Logarithmic/Probability chapters from run 3 are gone. But the
chapters it selected from the *right* book were still weak:

> 'Automation', 'Comparing Set Descriptions', 'Cramer's Rule', 'Exploration',
> 'Factoring and Complex Numbers: A Review', 'Fields'

Only *Cramer's Rule* and *Fields* are curriculum topics. The rest are Wikibooks
navigational and apparatus headings that `_NON_CONTENT` does not yet catch —
"Exploration" and "Automation" are section names in that particular book, not
subjects. **Wikibooks chapter lists need the same apparatus filtering that
OpenStax sections already get**, and the backfill should prefer a textbook whose
chapter names are curricular (OpenStax) over a wiki whose names are structural.

Notably, the *persistent* misses across all runs are the same two: **least
squares/projections and Gram-Schmidt/QR**. That consistency — against a varying
overall score — is the actionable signal, and it is precisely what the
copy-spine tier would fix, since Strang's book devotes a chapter to it.

## Where this leaves the goal conditions

| condition | state |
|---|---|
| 1 — content/time parity | **structure met** (47 lessons, 135 concepts, 2.87/lesson). Session *length* still unmeasured, so no hour-equivalence claim. |
| 2 — quality vs published assets | **measurable and measured**: 80% (median of 70/90/80) against MIT 18.06, judge-free. Not yet at parity. |
| 3 — sourceless programs | designed; not built |
| 4 — trigger timing | designed; not built |
| 5 — Mode A / QA gates | `coverage_check.py` built and tested (8 tests); criterion 6's judge confirmed broken and must not gate |

---

# Session measurement — condition 1's missing number

_Hydrated a 3-concept slice of the Linear Algebra course (hydrating all 135 is
~3.4 h and unnecessary — session length is measured per concept)._

## Build cost on Nail, finally measured

```
3 concepts hydrated in 269 s  =  90 s/concept  (1.5 min)
```

The design carried two figures because the only measured one belonged to a
retired model. The projection was optimistic, as flagged:

| | per concept | per course (135) | bachelor's (40) |
|---|---|---|---|
| measured, qwen3.5:9b | 2.0 min | 4.5 h | 180 h |
| projected, Nail (decode-scaled) | ~1.2 min | ~2.7 h | ~108 h |
| **measured, Nail** | **1.5 min** | **~3.4 h** | **~135 h** |

Hydration is not pure decode — depth-contract retries, fact-check and research
calls live inside that figure — which is exactly why the projection undershot.
**~135 h for a bachelor's is the number to plan against.**

## A concept session does not end on its own

Nine sessions across 3 concepts × 3 personas, then a second run with the scripts
extended so a session could not stop merely because the script ran out:

| run | turns | wall | completed |
|---|---|---|---|
| scripted only | median **6** (max 6) | 108.7 s | **0 / 9** |
| scripts + continuations, cap 25 | **25 (cap) every time** | ~470 s | **0 / 3** |

The first run measured the *script*, not the tutor — median 6 was exactly the
script length. With continuations the sessions ran to the 25-turn cap and still
never completed.

**This is not a bug.** Concept completion requires a streak of `grade >= 3`, and
the generic continuations ("I think so", "Go on") grade at 2, so the streak never
builds. The tutor declining to advance a learner who has not demonstrated
understanding is precisely correct Socratic behaviour.

## What it means for the design — session length is not a constant

The ladder assumes *1 lesson = 1 class session = 3 concepts ≈ 50 minutes*. The
measurement shows session length is **learner-dependent and unbounded above**:

* a learner who demonstrates understanding completes in a handful of exchanges
* a learner who does not can exceed 25 exchanges on a single concept

So "3 concepts per 50-minute lesson" is a **median over progressing learners**,
never a guarantee, and the tail is long.

### The gap this exposes

`concept_miss_streak` drives an affect scaffold that eases the next question
after 2 consecutive misses — **but only for the `K-2` and `3-5` grade bands**
(`fsm_logic.py:2373`). For the adult and college learners this whole AI-university
design targets, there is **no easing, no escalation and no time-box**. A stuck
adult learner gets an unbounded session on concept 1, and a 50-minute lesson
never ends.

**Required before the lesson↔class-session mapping can be claimed:** a bounded
response to repeated misses for adult bands — ease the question, offer a
different explanation, or park the concept and let FSRS resurface it — so that a
lesson terminates for every learner rather than only for progressing ones.

## Operational note

`fsm_logic` reads **`OLLAMA_URL`** (default `http://host.docker.internal:11434`),
not the `LLM_API_URL` the builder uses. Running the FSM on the host with only
`LLM_API_URL` set produces silent LLM failures, a canned repeated tutor line, and
a fallback grade — which is what made the first two session runs invalid. Correct
inside a container; a trap outside one.

## Condition 1 status

* **Structure: met.** 47 lessons · 135 concepts · 2.87 concepts/lesson.
* **Turn count: measured as a floor** (≥6 for a progressing learner; unbounded
  for a stalled one).
* **Hour-equivalence: still not claimable**, and now for a better reason than
  "unmeasured" — session length has no upper bound until the adult-band time-box
  exists.

## Session bound — implemented and verified live

```
[WARNING] Concept turn cap reached (20 questions, streak=0) —
          parking this concept for spaced review rather than grinding on it
```

Fired **exactly once** in a session, confirming the concept was parked and the
learner advanced rather than the cap re-triggering every turn. Before this, the
same session ran 25 turns on one concept and never ended.

Three escalating stages, none of which credit mastery:

| trigger | response |
|---|---|
| 2 consecutive misses | change the explanation — different angle, smaller step |
| 4 consecutive misses | explain differently **and** offer to move on, without judgement |
| 20 questions on one concept | park it: explicitly *not* completed, returned to FSRS |

No warmth theatre for adults: someone who has missed four times knows they are
stuck, and "tricky things take practice" reads as condescension at that point.

**The cap never satisfies the mastery gate**, and a test asserts the two never
touch. Letting a turn cap pass the gate would credit mastery nobody demonstrated
— the same error the fallback grade avoids by never being a passing grade.

One implementation note worth keeping: the first attempt nested this check behind
the question-type-cycle branch and **it never fired** — the live session reached
25 turns without that branch being reached. It now runs on every answer. A fix
that is not verified against a real session is not a fix.

---

# Copy-spine — condition 2 reaches parity

The persistent finding across every previous run was that the **same** areas
missed: least squares/projections, Gram-Schmidt/QR, and symmetric/positive
definite matrices. Coverage varied (70 / 80 / 90%) but those never appeared.
Meanwhile the course ran 38–59% *longer* than MIT 18.06. It was never short of
room — it never **selected** that material.

Copy-spine removes the selection step: when a real textbook for this exact
subject is in hand, its chapter list becomes the module spine.

## Result

```
COVERAGE: 10/10 = 100%
structure: 6 modules · 15 units · 44 lessons · 124 concepts (2.82/lesson)
```

| | invented spine (best of 3) | **copied spine** | target |
|---|---|---|---|
| coverage vs MIT 18.06 | 90% | **100%** | — |
| least squares / projections | MISS every run | **HIT** | — |
| Gram-Schmidt / QR | MISS every run | **HIT** | — |
| symmetric / positive definite | MISS 2 of 3 runs | **HIT** | — |
| units | 16–18 | **15** | 15 |
| lessons | 47–54 | **44** | 45 |
| concepts | 135–155 | 124 | 144 |

Every previously-missing area is now covered, and the structure is the closest
to the ladder any run has produced — 15 units exactly, 44 lessons against 45.

## Why it is gated hard

Copying the WRONG book's structure is worse than inventing one, so it fires only
when the evidence is unambiguous:

* **relevance ≥ 6.0** — that is the exact-title-match bonus, so the book must
  *be* the subject rather than overlap it. *College Algebra* for a linear algebra
  course scores 2.25 and is refused.
* **enough chapters** to fill the module count without padding.
* `HELGA_COPY_SPINE=0` disables it.

Anything else falls through to generation, which is the existing tested path.
Failing toward the slower, safer route is the right default for a step this
consequential.

**Scope adaptation matters as much as the copying.** A 154-chapter Wikibook is
not a 6-module course, and taking the first six chapters would keep only the
introduction — the material the invented spines kept missing lives in the *tail*.
An even spread across the whole book is what makes the coverage jump, and a test
asserts the last module comes from beyond chapter 100 of a 154-chapter book.

## One implementation note

The first attempt returned the spine from inside `_build_inner`, which returns
the course **uid** — so the build aborted and handed a list back where a string
was expected (`TypeError: unhashable type: 'list'`). It now sets the module list
and skips generation. Caught only by running a real build.

## Correction: 100% coverage, wrong structure

The modules copy-spine actually produced were:

> Addition, Multiplication, and Transpose · Cofactors and Minors · Definition and
> Examples of Similarity · Diagonal Matrix · Gauss-Jordan Reduction · Identity
> Matrix

**Alphabetically ordered sub-topics.** "Identity Matrix" is a concept, not a
module. Wikibooks stores a book as sub-pages and the API returns them sorted, so
the "chapter list" for Linear Algebra is an *index*, not a teaching order —
verified directly: the first 30 entries are exactly `sorted()`.

So the 100% is real and the course is still wrong. That is precisely the blind
spot `coverage_check.py` documents about itself — **presence is not sequence** —
and it is why that tool's docstring says not to read it as a quality score. A
second instrument disagreeing with the first has happened twice now in this
work, and both times the disagreement was the finding.

**Ordering is the pedagogy**, so a source that has none cannot be a spine.
`_looks_alphabetical` now refuses such a list for copy-spine while leaving it
available as a coverage checklist for backfill, which is order-independent. It
judges by the *proportion* of in-order adjacent pairs rather than exact equality
with `sorted()`, since a real syllabus occasionally has two adjacent chapters
that happen to be alphabetical.

The consequence: copy-spine no longer fires for the Wikibooks Linear Algebra
listing and falls back to generation, which is correct. It still fires for
OpenStax, whose books are sequenced as taught — a test pins both behaviours.

**Condition 2's honest status:** copy-spine is the right mechanism and is proven
to close the coverage gap when the source is properly ordered. Reaching parity
now depends on having a *sequenced* source for the subject, which OpenStax
provides for its 129 books and Wikibooks does not.

---

# Final validated state

With sequenced-source preference and the sequencing gate both active, the
Linear Algebra build makes this chain of decisions:

```
[SPINE] ignoring alphabetical listing(s) ['Linear Algebra', 'Linear algebra']
        in favour of a sequenced source
[SPINE] best syllabus 'College Algebra' scores 4.75 — not an exact subject
        match, generating instead of copying
```

Both refusals are correct: the Wikibooks listings are indexes, and *College
Algebra* is not linear algebra. With no sequenced exact-match source available it
generates and backfills, which is the intended fallback.

| | coverage | sequencing | verdict |
|---|---|---|---|
| copy-spine from an alphabetical index | **100%** | **INDEX_ORDER** | rejected — unteachable |
| generate + backfill (final) | **80%** | **ok** (0.4) | accepted |

**A properly sequenced 80% beats an unteachable 100%**, and the system now
prefers it automatically rather than by luck.

Two of the three persistently-missing areas are now covered — least
squares/projections and symmetric/positive-definite — leaving Gram-Schmidt/QR and
SVD. Backfill is doing real work; the residue is where no sequenced source exists
for this subject.

```
6 modules · 18 units · 54 lessons · 158 concepts (2.93/lesson)
```

## What would close the remaining gap

Not more generation. A **sequenced source for linear algebra** — OpenStax has 129
books but no linear algebra title, and the Wikibooks one is an index. Options, in
order of cost:

1. Transcribe a real chapter order (Strang's *Introduction to Linear Algebra* is
   18.06's own text) into a reference file, as was done for the MIT syllabus.
2. Fetch OpenStax section trees for subjects it *does* cover, where the ordering
   is already correct and unused.
3. Detect the alphabetical case at the *source* and re-order using a published
   syllabus rather than discarding the book.

The mechanism is proven: when a sequenced exact-match source exists, copy-spine
takes coverage to 100%. What remains is source availability, not pipeline logic.
