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

## 8. What is deliberately not built

**No solver and no marker.** Adding one would change the pedagogy entirely and
is a substantial build. Until it exists the product teaches reasoning *about*
mathematics, and the release notes must say so rather than implying practice it
cannot check.

**No generated errors.** A model asked to invent a wrong solution invents a
plausible one, and in mathematics a generated "error" is frequently not an
error at all, or is wrong in a second unintended way. Only errors the source
itself flags are used.

**No content crawling.** See §3.
