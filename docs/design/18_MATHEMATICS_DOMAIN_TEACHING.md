# Mathematics domain — teaching without letting the learner solve

**Status:** built 2026-08-22. Second domain, after computer science.

The product must build and teach any mathematics course up to college level —
arithmetic, algebra, geometry, trigonometry, precalculus, calculus, statistics
— at the quality of a real course, Socratically, **without ever asking the
learner to solve anything.**

---

## 1. Why the constraint is harder here than in code

The computer-science domain had to avoid "type this command". Mathematics has
to avoid "now you try", which is the move mathematics teaching is almost
entirely built from. There is no marker and no computer-algebra system, so a
learner's answer cannot be checked — and an unchecked wrong answer that gets
praised teaches the error.

The evidence says the constraint is not the handicap it looks like:

| technique | evidence |
|---|---|
| **Erroneous examples** — show a wrong solution, ask where it first fails | students using interactive erroneous examples beat problem-solving students on a **delayed** post-test (Adams et al., CHB 2014) |
| **Worked example + self-explanation** — show the full solution, ask what licenses one step | the self-explanation effect (Chi et al. 1989); successful self-explainers relate a step to an **underlying principle**, unsuccessful ones restate it — so the question must ask for the principle |
| **Comparison** — two correct methods, which is more efficient and why | Rittle-Johnson & Star: effective for procedural **flexibility**, and dependent on prior knowledge, so it is withheld from a first encounter |
| **Prediction** — qualitative, then reveal | needs no arithmetic, and the source already contains the verified answer |

A systematic review of Socratic method in mathematics (Frontiers in Education,
2026) was consulted and is honestly thin — it says so itself: *"Studies use
terms like 'Socratic questioning' without spelling out what these mean
operationally."* The four techniques above came from the specific literatures,
which have effects attached.

---

## 2. The component that decides whether the maths is TRUE

`mathml.py` converts presentation MathML to LaTeX. It is not a rendering
nicety. Measured against OpenStax Calculus Volume 1 using the generic
`get_text()` extraction every HTML reader in this repository uses:

| should be | extracted as |
|---|---|
| `3² = 9` | `3 2 = 9` — thirty-two equals nine |
| `f(x) = √x` | `f ( x ) = x` — the root vanishes |
| `x = ⅓(y+1)²` | `x = 1 3 (y+1) 2` — a third becomes thirteen |

Each is a **false statement in well-formed prose**. No structural check
downstream would flag one. The equivalent computer-science bug destroyed
indentation and took four attempts to fix; this one is worse, because bad
indentation is visible and `3 2 = 9` is not.

Two further defects were found by its own tests: `\Deltay` (an undefined
control word — KaTeX renders nothing) and `f^{'}` where the space-appending
rule for operators had leaked into subscripts. Both are fixed and pinned.

LaTeX rather than Unicode because KaTeX is already vendored and wired into
`learn.html` and `session.js`, and because LaTeX survives the round trip
through JSON and the model prompt.

---

## 3. Sources, and a robots.txt finding

OpenStax publishes the whole ladder: Prealgebra → Elementary → Intermediate →
College Algebra → Precalculus → Algebra & Trigonometry → Calculus 1/2/3, plus
Statistics. It also marks worked examples **semantically** —
`[data-type=example]` wrapping `.os-problem-container` and
`.os-solution-container` — which is near-perfect mining precision, 11 complete
worked examples on a single Calculus page.

**But `robots.txt` disallows the content path:**

```
Disallow: /apps/archive        ← where all book content lives
Disallow: /contents
User-agent: GPTBot
Disallow: /books/              ← AI crawlers, book content specifically
```

`PoliteFetcher` refuses it correctly. So this domain reads **metadata only**
(the release manifest and CMS catalogue, neither disallowed) and takes **book
content from a locally supplied copy** — the channel OpenStax distributes for
offline use, and the right one for an offline-first tutor anyway.
`parse_book_html` applies the identical MathML conversion and example mining to
a local EPUB, which carries the same `os-*` markup. Nothing is lost.

> **Pre-existing issue, flagged not imitated:** `services/research/syllabus_sources.py:438`
> fetches `/apps/archive/.../contents/...` directly, with no robots check —
> `_get_json` is a plain `requests` call. That is shipping code crawling a
> disallowed path.

**Wrong-level books are refused.** OpenStax has no linear algebra text, and the
generic relevance matcher answers "Linear Algebra" with *Algebra 1* — a
high-school book for a university subject. `book_for` returns `None` for the
subjects OpenStax does not cover, so the caller falls back to the researched
path instead of silently building from the wrong level.

Routing also matches on **word boundaries**: substring matching sent
"Pre*calculus*" to *Calculus Volume 1*, the same defect as the CS domain's
bare `api` inside "therapist".

---

## 4. Concept kinds

Nine, and none shared with computer science — `SYNTAX` vs `MECHANISM` is a real
distinction about code and meaningless here.

`DEFINITION` · `NOTATION` · `THEOREM` · `PROOF` · `PROCEDURE` ·
`REPRESENTATION` · `APPLICATION` · `ESTIMATION` · `MISCONCEPTION`

The distinctions that matter are between **true by definition**, **true and
provable**, and **convention**. Teaching those identically is the classic
failure: asking a learner to derive why `∫` is an elongated S asks them to
reason about a historical accident, and telling them the Mean Value Theorem
"just is" removes the only interesting thing about it.

`PROOF` outranks `THEOREM` deliberately — "Proof of the Chain Rule" matches
both (`rule\b` is a theorem pattern) and is a proof.

**No `SHAPE` override.** The CS domain widens `SCHOOL_SHAPE` because
documentation topics are genuinely uneven. Mathematics is the case
`SCHOOL_SHAPE` was calibrated *for*: taught on a calendar, chapters sized to
weeks by people who teach it. Declining the override is the contract working —
`SHAPE` is optional and absence means the shared default.

---

## 5. The standing rule

Per-kind guidance was not enough. Measured over 24 turns without it, 2 asked
the learner to compute:

* *"what is the average rate of change between $x=1$ and $x=1+h$?"*
* *"what is the derivative of the product of two functions?"*

Neither kind's guidance happened to name that failure, and the model reaches
for "ask them to work it out" by default — it is what mathematics teaching
looks like everywhere in its training data. So `NEVER_SOLVE` is stated **once,
globally**, in `prompt_line`, and applies even when the kind is `UNKNOWN`.

Result: **0 solve-violations in 24 turns.**

---

## 6. Signposting is pointing, not diagnosing

The erroneous-examples research says novices do better when the error is
**highlighted**. The first implementation read that as "say which part contains
the mistake", and measured output was:

> *"The mistake is in the very first line where $x^{-2}$ is equated to $-x^2$.
> The rule broken is that a negative exponent indicates a reciprocal..."*

Location **and** rule — the entire exercise, given away in the first sentence.
For a one-line statement, "which part" *is* the answer.

A signpost points at a feature to examine; it does not deliver the verdict. The
block now names where to look and forbids the diagnosis explicitly, with the
turn's shape stated **before** the material rather than after it.

Result: **6/6 samples withheld the diagnosis**, up from 1/2.

---

## 7. Measured

24 turns, two independent passes, across the whole ladder (Prealgebra →
Calculus), through the production prompt path:

| | result |
|---|---|
| turns | 24 |
| ended with exactly one question | 23/24 |
| **asked the learner to solve** | **0** |
| empty | 0 |
| mined move used | **4/4, both passes** |

The detector itself was validated first (10/10 on hand-built cases), because
the CS equivalent had flagged its own prohibition as a violation. Two of its
early "violations" were artifacts and were fixed rather than reported:
a tutor *restating* a problem it was about to solve, and a NOTATION turn asking
what a symbol *denotes* — which is the guidance working, not failing.

---

## 8. The notation has to be SPOKEN, not just rendered

`notation_rigour` — the mathematics benchmark's own dimension — scores whether
notation is correct **and readable aloud**, because Helga teaches by voice. A
turn containing LaTeX that `math_speech` cannot render is a turn the learner
hears as raw markup.

That is in direct tension with §2: the fix for false mathematics was to emit
LaTeX. So the LaTeX this domain produces was checked against the speaker, and
**10 of 66 commands it can emit were unspeakable** — including `\int`.

The cause was a one-character bug in `services/core/math_speech.py`:

```python
re.sub(r"\\(sum|prod|int)\b", ...)     # \b never fires before "_"
```

An underscore is a **word character**, so `\int_0^1` has no word boundary after
`int` and the rule silently declined. The braced rule above it required
`_{...}^{...}`, which `\int_0^1` also is not. Neither matched, so *the ordinary
way of writing a definite integral* was spoken as "\int" — the most common
symbol in a calculus course, heard as markup, in every turn that used one.

Fixed, along with `\iint` / `\iiint` / `\oint` (Calculus III), `\land` /
`\lor` / `\neg` (discrete mathematics), matrix environments, and a greedy
bare-limit match that read `\int_0^1x` as "from 0 to **1x**".

The end-to-end path — MathML → LaTeX → speech — is now pinned by a test, since
each stage passing alone would not catch a LaTeX dialect the converter emits
and the speaker cannot read.

---

## 9. The benchmark, and what it can and cannot see

Two runs under fingerprint `4faf5407715a9e4d` (n=15 dialogues each), differing
only by the bluff-detection fix:

| dimension | A | B | floor |
|---|---|---|---|
| domain score | 3.324 | 3.369 | 0.162 composite |
| adaptation | 2.00 | 2.13 | **0.13** |
| socratic | 2.60 | 2.13 | 0.00 (underestimate) |
| misconception_handling | 5.00 | 4.33 | **0.80** |
| notation_speakable | 5.00 | 5.00 | 0.40 |

**The two runs are indistinguishable.** The adaptation gain is exactly the
measured noise floor, and the file that records those floors warns to "prefer a
dimension that moved several times its floor over one that just cleared it".
The misconception drop is *below* its floor, so it is not a regression either.

The clearest evidence is a profile I did not touch: `silent_struggler`
adaptation moved 1.33 → 3.00 between the two runs with no change addressed to
it. Run-to-run variance dominates everything at this scale.

**What that means for the release gate.** `adaptation` must reach 3.5 from
2.0 — more than ten times its noise floor. That will not come from detector
tweaks, and it cannot be *measured* from single runs: the floors themselves were
withdrawn once when a third run showed `accuracy` spread nearly seven times the
two-run estimate.

**What is verified regardless of the judge.** The bluff detector fires 3/3 on
the real bluffer transcripts where it fired 0/3 before; `\int` is speakable
where it was raw markup; MathML no longer yields false statements. These are
deterministic and do not depend on a noisy rubric to be true.

---

## 10. Building a real course, and two defects only that revealed

`build_from_book` on a calculus textbook, through the production builder:

    3 modules, 6 units, 12 lessons, 36 concepts
    classified      36/36   (0 unknown)
    teaching moves  14      (9 WORKED_STEP, 5 ERROR_HUNT)
    modules named   Foundations of Change / Calculating Rates /
                    Accumulating Quantities   — synthesised, not chapter titles

Two defects surfaced only here, and neither could have been caught by a
component test.

**The tutor could not see any of it.** The domain attached its mined material
as `teaching_move`; `fsm_logic._domain_teaching` reads `teaching_pair`. Fifteen
moves, invisible. That is the ninth instance of this failure in this
repository, and it was introduced *in the same session that documented the
pattern*, in a file sitting beside the document describing it. `teaching_move`
was also already taken — `services/common/teaching_move.py` is an unrelated
reverted mechanism.

`tests/domains/test_domain_reaches_the_tutor.py` now runs EVERY domain's
`attach_to_course` and fails if it writes any `teach*` field other than the one
the FSM reads.

**Every example was attached to the wrong concept.** `attach_to_course` popped
moves in order, so within a lesson each concept received whichever example came
next — systematically off by one:

| concept | material it was given |
|---|---|
| Applying the Squeeze Theorem | a factoring limit |
| Integration by Parts | the antiderivative of 1/x |
| Definite Integrals via Power Rule | ∫xeˣ — which *is* by parts |

The tutor taught integration by parts from a power-rule example. Matching on
title vocabulary (minus the words every maths title contains) fixed all 14
pairings, and matching is a preference rather than a filter — an unmatched
concept still gets an example from its own lesson.

**Measured after, on the built course through the production path**, 10 turns:
one question 10/10, asked to solve **0**, empty 0, material used 9/10. The two
solve-violations present before the matching fix disappeared with it: given
material about its own concept, the tutor stops restating a mismatched problem.

---

## 11. What is deliberately not built

**No solver and no marker.** Adding one would change the pedagogy entirely and
is a substantial build. Until it exists the product teaches reasoning *about*
mathematics, and the release notes must say so rather than implying practice it
cannot check.

**No generated errors.** A model asked to invent a wrong solution invents a
plausible one, and in mathematics a generated "error" is frequently not an
error at all, or is wrong in a second unintended way. Only errors the source
itself flags are used.

**No content crawling.** See §3.
