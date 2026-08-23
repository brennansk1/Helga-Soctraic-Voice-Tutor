# Mathematics domain — teaching without letting the learner solve

**Status:** built 2026-08-22. Second domain, after computer science.
**CLOSED 2026-08-22** at the owner's direction, with the benchmark gate
knowingly unmet and ACCEPTED at ~2.41 against 3.5 — see §9. The decision was
made on the evidence below, not by running out of ideas quietly: ten runs, one
+0.28 gain from a plumbing defect, five subsequent changes all measuring as
noise, and no domain in this repository's history ever exceeding 2.30. Four of five release clauses are verified; the
`adaptation` gate of 3.5 is not reached and is not reachable at the prompt
layer on `nail-35b-a3b-ctx`. Re-open if the gate is revised or the model
changes.

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

## 9. The benchmark: four runs, no measurable improvement

Four runs under fingerprint `4faf5407715a9e4d`, n=15 dialogues each.
A = domain layer wired; B = + bluff detection; C = + behaviour-chosen material;
D = + learner-state precedence.

| dimension | A | B | C | D | floor (4-run) |
|---|---|---|---|---|---|
| domain score | 3.324 | 3.369 | **3.700** | 3.329 | **0.376** |
| adaptation | 2.00 | 2.13 | 2.40 | 2.00 | **0.40** |
| socratic | 2.60 | 2.13 | 2.20 | 2.13 | 0.47 |
| accuracy | 4.27 | 4.47 | 5.00 | 4.80 | 0.73 |
| notation_rigour | 2.80 | 3.33 | 4.07 | 3.13 | 1.27 |
| notation_speakable | 5.00 | 5.00 | 5.00 | 5.00 | 0.00 |

**Nothing here clears its noise floor.** No dimension moved further than the
instrument's own run-to-run spread across four identical configurations.

### A retraction, and the mistake worth learning from

After run C this document claimed real, floor-beating gains: adaptation +0.40
against a floor of 0.13 ("3× the floor"), the composite +0.376 against 0.162
("2.3×"), `notation_rigour` +1.27 against 1.00. It also called adaptation
"monotonic across all three runs" (2.00 → 2.13 → 2.40).

Run D read 2.00, and re-deriving the floors from four runs grew every one of
them:

    adaptation      0.13 -> 0.40    3x
    socratic        0.00 -> 0.47
    visual_policy   0.27 -> 0.53    2x
    composite      0.162 -> 0.376   2.3x

Each claimed gain landed **exactly at** its re-derived floor. Run C was a high
draw — the composite over four runs reads 3.324 / 3.369 / **3.700** / 3.329 —
and three ascending points were three noise samples.

This is the second time the floors have been withdrawn in this file's history:
`accuracy` was estimated at 0.13 from two runs until a third showed 0.87. The
lesson is not "use one more run". It is that **a single high draw is
indistinguishable from success**, and that a delta measured against a floor
derived from a handful of runs is worth very little.

### The one floor-beating result: the domain layer DOES work

A fifth run with `HELGA_MATHS_RULE_VARIANT=none` — every standing instruction
this sprint added, removed, same instrument:

| dimension | A–D mean | no guidance | diff | floor |
|---|---|---|---|---|
| **misconception_handling** | 4.65 | **2.40** | **−2.25** | 0.75 |
| adaptation | 2.13 | 2.07 | −0.07 | 0.40 |
| socratic | 2.27 | 2.07 | −0.20 | 0.47 |

Two things follow, and they point in opposite directions.

**The domain guidance is doing real work.** Removing it drops
`misconception_handling` by 2.25 — three times its floor, and the largest
effect measured anywhere in this sprint. This is the only floor-beating result
obtained, and it is an ablation rather than an improvement claim, which is the
more trustworthy direction: it is much harder to get a 3× floor drop by luck
than a 1× rise.

**And it has nothing to do with the gate.** `adaptation` reads 2.07 without any
of it and 2.13 with all of it. The hypothesis that this sprint's prescriptive
guidance was making the tutor *more* scripted — raised twice in this document —
is disproved.

### Why 3.5 is not reachable by prompt-level work

| evidence | |
|---|---|
| 42% of all dialogues score **1** | n=60; the mode is the bottom of the scale, not a near miss |
| a 3.5 mean needs ~half of dialogues at **4+** | currently **15%** |
| no domain has ever exceeded **2.30** | across 7 domains and the whole recorded history |
| the strongest behavioural difference found is **+0.42** | at the 0.40 floor |
| removing ALL guidance moves it **−0.07** | it is not a guidance problem |

The strongest difference between a 5-scoring and a 1-scoring dialogue is that
the tutor **quotes the learner's own words**: *"You said 'idk, maybe,' which
suggests you're unsure if the arrow's line counts as a direction change."* Both
5s do it. But quoted dialogues average 2.27 against 1.85 — one noise floor —
and 14 quoted dialogues still scored 1. (This sprint had earlier FORBIDDEN that
move, on the intuition that being told you are being handled is worse than not
being handled. The data disagrees; the effect is too small to have mattered.)

**The conclusion is about the release criteria, not the domain.** Reaching 3.5
would require turning the 25 dialogues that score 1 into 4s, and nothing at the
prompt layer moves that. It needs a different model, longer dialogues than four
turns, or a re-examination of whether 3.5 is the right number for this system —
a question worth asking, given no subject has ever come within 1.2 of it.

### Seven runs, and the one thing that worked

    A=2.00  B=2.13  C=2.40  D=2.00  |  E=2.53  F=2.53  G=2.47

A–D are before the fix; E, F and G after, agreeing within 0.06. The gain of
+0.40 is the most solidly established result of this work, and it came from
neither a prompt guess nor a pedagogical idea. It came from finding an
instruction that **existed and never reached the model**:

`turn_state.render()` carried "CHANGE YOUR APPROACH — asking it again in
different words has already failed", and it reached **1 prompt in 60**. Two
faults stacked: it keyed on a counter that a rewording resets (and rewording is
the first thing a stuck tutor does), and when it did fire, a 600-character cap
truncated it away because it was appended last. It now reaches 26 of 26
eligible prompts.

That is the failure the judge's own rationales name above all others:
*"The tutor repeated the same question about walking West after the student
already answered 'idk'."*

**The refinement on top of it was neutral.** Run G dropped the difficulty
instead of handing over the answer — a change the judge's rationales explicitly
asked for ("provided the answer directly … instead of using a guiding
question"). It scored 2.47 against 2.53, well inside the floor. It is kept on
principle, not on evidence.

### Ten runs: the final picture

    A=2.00 B=2.13 C=2.40 D=2.00 | E=2.53 F=2.53 G=2.47 H=2.27 I=2.20 J=2.47

    before the plumbing fix (A-D)   mean 2.13
    after (E-J, six runs)           mean 2.41

**One gain, +0.28, from a plumbing fix.** Five subsequent changes, all
well-reasoned, all measuring as noise.

**A decline that was not there.** E through I read 2.53, 2.53, 2.47, 2.27,
2.20 — monotonically non-increasing over five runs, which looks like
cumulative damage from the changes stacked on top of the fix. A revert of all
three was written before it was tested. J, on the identical configuration as I,
came back 2.47 and dissolved the pattern.

That revert would have removed three principled changes on the strength of a
trend that did not exist, and would have felt entirely justified. The only
thing that prevented it was replicating before acting — the same rule that made
E and F believable in the first place.

### Where it plateaus

    A=2.00 B=2.13 C=2.40 D=2.00 | E=2.53 F=2.53 G=2.47 H=2.27

Four runs after the mechanism fix, mean 2.45. Two further changes were made on
top of it, both well-reasoned and both **neutral**:

| change | run | result |
|---|---|---|
| scaffold down rather than hand over the answer | G | 2.47 vs 2.53 |
| make BLUFFING decisive, as GIVING_UP already was | H | 2.27 vs 2.51 |

The second was a real defect — the bluffing instruction sat at position 6534
while the mined material sat at 5173 opening "THIS TURN OVERRIDES THE GENERAL
GUIDANCE ABOVE", so the queued example won and the instruction to challenge
lost. Identical to the stuck-learner defect already fixed. The judge names its
consequence three times. And its target profile moved 1.11 → 1.33, which is
nothing, while unrelated profiles swung by more than a point in both
directions at n=3 each.

**Both are kept on principle and labelled as such.** Neither earned its keep by
measurement, and saying so is the point: an instruction that is demonstrably in
the wrong place is worth moving whether or not a ±0.40 instrument can see it,
but that is a different claim from "this improved the tutor".

**The honest summary of the whole effort: one +0.40 gain, from plumbing, and
nothing since.** Everything invented measured as noise; the only thing that
moved the number was finding an instruction that never reached the model.

### What is left is a different class of problem

The remaining low scorers are led by:

> *"The tutor accepts the student's nonsensical justification for why the
> slopes are identical without challenging"* — three times, `confident_bluffer`

That is **not** a mechanism gap. BLUFFING has been detected since run B and its
instruction reads "Do not accept it. Ask them to explain WHY, in plain words."
The instruction reaches the prompt and the model does not follow it.

Every one of the ten defects found in this work was *"the instruction never
reached the model"*. This is *"it reached the model and lost"* — the same shape
as the finding that no mechanical feature separates a dialogue scoring 1 from
one scoring 4. **The plumbing vein is largely mined out; what remains is
semantic compliance**, and that is a different kind of problem needing a
different kind of work.

### The judge is reliable — a correction

The claim above that the instrument cannot resolve change was half right, and
the wrong half mattered.

Rescoring the SAME 15 transcripts three times:

    run means            2.00 / 2.13 / 2.13      spread 0.13
    per-dialogue spread  0.33 mean; 10 of 15 identical all three times

The judge agrees with itself. The 0.40 run-to-run floor is GENERATION variance
— every run is a different conversation — not judging variance. So the
adaptation scores are a real and reproducible judgement about the tutor, and
the earlier suggestion that the 3.5 gate be re-examined because the numbers
were unreliable does not stand.

The gate is a genuine target. What remains true is that comparing two
CONFIGURATIONS by re-running generation is nearly useless at n=15; comparisons
should rescore fixed transcripts instead.

And the open question is sharper than before: the judge reliably separates a 1
from a 4 while no mechanical feature does. It is seeing something semantic that
none of length, variance, question rate, learner-echo or stuck-handling
captures.

### What is verified regardless of the judge

These are computed, not scored, and none depends on a rubric:

* the bluff detector fires **3/3** on the real bluffer transcripts (was 0/3)
* **5/5** concepts hand a bluffer and a give-up learner different material
* **4/5** stuck learners get an explanation instead of another question (was 0/5)
* **14/14** worked examples land on the concept they belong to (all were off by one)
* `\int` is speakable; MathML no longer yields `3 2 = 9`
* `notation_speakable` is 5.00 with range 5–5, and has a floor of **0.00**
  because it is the one number here that is measured rather than judged

**The release gate is not met**, and there is no evidence any change in this
sprint moved it. `adaptation` must reach 3.5; four runs put it between 2.00
and 2.40, which is one draw's worth of spread around a single value.

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

## 11. Adapting the material, not just the wording

`adaptation` is the release gate, and its rubric is: *"Did the tutor adjust to
THIS student's demonstrated level and behaviour, rather than following a
script?"*

Choosing material from the concept's KIND alone **is** a script — the same
concept produces the same turn whoever is sitting there. Worse, the mined
blocks in §5 impose a fixed turn shape ("show this, then ask that"), so the
domain layer was arguably making the tutor *more* scripted, not less.

The learner's behaviour was already computed on every turn
(`services/common/learner_behaviour`) and **nothing used it to decide what to
show**. A bluffer and someone who has given up need opposite moves and were
getting identical material:

| behaviour | move | why |
|---|---|---|
| BLUFFING | ERROR_HUNT, then PREDICT | forces a commitment fluency cannot fake |
| GIVING_UP | WORKED_STEP | stop questioning; show the whole solution |
| TERSE | PREDICT | answerable in a few words; "which line is wrong" is not |
| HEDGING | COMPARE | resolves the specific thing they are unsure about |
| AHEAD | COMPARE | believe them, raise difficulty |

So the build now stores **alternatives** alongside the default — nested inside
`teaching_pair`, not under a new top-level key, because a domain writing
material the FSM does not read is the defect this file already caused once
(§10). Selection happens at teaching time via the optional `choose_move`
contract hook, since it depends on what the learner has just done.

**Measured deterministically**, through `fsm_logic._domain_teaching`, on a
real built course: **5 of 5 concepts give a bluffer and a learner who has
given up different material** — ERROR_HUNT versus the complete worked
solution.

This does not depend on the judge, which is the point: the benchmark cannot
resolve an effect this size (§9), and a deterministic check can.

---

## 12. What is deliberately not built

**No solver and no marker.** Adding one would change the pedagogy entirely and
is a substantial build. Until it exists the product teaches reasoning *about*
mathematics, and the release notes must say so rather than implying practice it
cannot check.

**No generated errors.** A model asked to invent a wrong solution invents a
plausible one, and in mathematics a generated "error" is frequently not an
error at all, or is wrong in a second unintended way. Only errors the source
itself flags are used.

**No content crawling.** See §3.
