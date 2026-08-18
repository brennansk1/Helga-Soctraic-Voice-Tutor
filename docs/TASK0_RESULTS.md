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
