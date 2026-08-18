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
