# Science domain — the observation is supplied, never demanded

**Status:** built 2026-08-23. Fourth domain, after computer science,
mathematics and history.

---

## 1. The constraint, and the door it opens

Each domain turns on one thing the learner must not be asked for.

| domain | prohibition |
|---|---|
| computer science | don't ask them to type code |
| mathematics | don't ask for a solved answer |
| history | don't ask them to guess a contingent fact |
| **science** | **don't ask for an observation they cannot make** |

A learner at a screen cannot see what happens when the current reverses, cannot
weigh the precipitate, cannot run the cross. "So what do you think happens?"
asked as though the answer were derivable is history's failure wearing a lab
coat — a quiz with the result withheld.

But unlike the other three, this rule opens a door rather than only closing
one. The learner **predicts**, the tutor **supplies** the observation, the
learner **explains** the gap. Predict–Observe–Explain is the best-evidenced
conceptual-change strategy in science education — a measured normalised gain of
0.44, effective specifically at *correcting misconceptions* rather than
papering over them — and it happens to be exactly what this project needs:
predicting commits a learner to a consequence of what they already believe and
asks them to compute nothing.

The second rule follows the first: **never ask them to calculate a value.** Ask
about units, dimensions, sign, direction, order of magnitude, and what a result
would *mean*. All reasoning; no arithmetic.

---

## 2. Johnstone's triangle, encoded as kinds

Johnstone's account of why chemistry is hard generalises across the natural
sciences. Any phenomenon can be described at three levels — **macroscopic**
(what is observed), **submicroscopic** (the model explaining it), **symbolic**
(the notation) — and the central risk is **cognitive overload**, because
meaningful learning requires movement among all three while working memory
holds one.

An expert slides between levels without noticing. A learner cannot, and the
unannounced slide is where the thread is lost. So the kinds separate them, and
every prompt names which level the concept sits on.

| kind | level | rank |
|---|---|---|
| `OBSERVATION` | macroscopic | 0 |
| `MISCONCEPTION` | a confusion *between* levels | 1 |
| `QUANTITY` | macroscopic (measured) | 2 |
| `LAW` | macroscopic ↔ symbolic | 3 |
| `MECHANISM` | submicroscopic | 4 |
| `MODEL` | submicroscopic | 5 |
| `REPRESENTATION` | symbolic | 6 |
| `EXPERIMENT` | macroscopic (what was done) | 7 |
| `CLASSIFICATION` | macroscopic, by criterion | 8 |

`OBSERVATION` ranks first because the phenomenon must exist for the learner
before any model of it can mean anything. `MISCONCEPTION` ranks second rather
than last, and that is a claim from the FCI literature rather than a
preference: these beliefs survive instruction that never addresses them
directly, so meeting one late means the learner has already spent the lesson
reinterpreting everything through it.

**One domain for three subjects.** Physics, chemistry and biology differ
enormously in content and hardly at all in the structure that decides teaching:
all three separate phenomenon from model, all three carry quantities that are
wrong without units, all three have catalogued misconceptions, all three answer
"how do we know?" with an experiment. Where they differ they differ in
*content* — impetus, conservation of mass in gases, the design stance — and
content lives in the guidance text.

---

## 3. The miner, and a category error I made

The pair this domain mines is **a setup and what actually happened**.

The first version looked for explicit staging — "Consider…", "Suppose…", "The
result is…". Measured on 21,575 characters of real LibreTexts physics:

    setups found      1
    outcomes found    1   (unrelated to the setup)
    pairs mined       0   from eight pages

The mistake was a category error, not a loose regex. **POE is a classroom
activity; textbooks are not transcripts of one.** Books state results directly.

But the raw material is everywhere in ordinary declarative prose, because a
conditional sentence *is* a POE with the answer printed:

> "If the voltage source is suddenly removed, current will continue to flow in
> the coil because of electromagnetic induction."

Split it, withhold the consequent, and a learner who predicts the current stops
has just met electromagnetic induction the useful way. Measured, these shapes
appear about once per 3,000 characters.

### Precision cost three rounds

Splitting conditionals naively mined **more pairs and no usable ones**:

| mined as a "prediction" | what it actually was |
|---|---|
| "When you have mastered this chapter, → you should be able to compare and contrast…" | a learning-objectives list |
| "Since electric potential is…, → the product is the change in energy per second" | a definition |
| "…nonbonding electrons are omitted, → but you still have to keep them in mind" | advice to the reader |

Four filters fixed it, and the last one is the interesting one:

1. **boilerplate** — chapter furniture, objectives, figure references
2. **definitional** — an outcome that restates a term, anywhere in its opening
3. **second person** — advice is not a physical outcome
4. **a physical *verb*** — requiring a physical *word* was not enough. "the
   change in potential energy" and "chemical reactions" both contain one and
   neither is something that happens. The cheap test for verb-work is what
   precedes the word: an article or preposition makes it a noun.

And the filters had to be **shared between both mining paths**. They lived only
in the split path at first, so the explicit path kept returning "A scientific
theory, contrary to what many people think, is not a guess" — the same defect
through a second door.

### Measured after

| book | POE pairs (6 pages) |
|---|---|
| Spiral Physics (Algebra-Based) | 2 |
| Basic Cell and Molecular Biology | 2 |
| Organic Chemistry (OpenStax) | 1 |
| Working with Molecular Genetics | 5 |
| Heat and Thermodynamics | 0 |

All four physics and biology pairs are usable:

> "If the voltage source is suddenly removed …" → *current will continue to
> flow in the coil because of electromagnetic induction*
>
> "As the chromosomes separate and daughter cells form …" → *nuclei reappear
> and chromosomes de-condense*

**Yield is low and precision is high, and that is the deliberate trade.** A
concept with no pair falls back to per-kind guidance, which is a worse turn.
A concept with a *bad* pair produces a turn that asks a learner to predict a
definition, which is worse than no turn at all.

### The zero is the book, not the miner

Thermodynamics returning nothing was worth checking rather than assuming.
Tatum's *Heat and Thermodynamics* yields **3 conditional candidates in 21,712
characters, and none survives the filters** — because they are not physics:

    "Once we have accepted that heat is but a form of energy, → and the joule
     will serve for both."
    "Because of these difficulties, I am choosing not to use the … → and I am
     hoping that the context will make it clear"

It is a first-person text about notation and pedagogy. The filters rejected
authorial asides correctly; there was no POE material to find. Some books are
simply the wrong genre for this move, and the honest response is a course built
on per-kind guidance rather than a manufactured prediction.

---

## 4. Sources

LibreTexts keeps a **separate library per science** — `bio`, `chem`, `phys` —
so `source_for` chooses the library from the subject rather than hardcoding
one. This matters more here than in any earlier domain, because this single
domain spans three libraries and a hardcoded one would answer "organic
chemistry" from the physics shelf.

| subject | selected |
|---|---|
| Physics | Spiral Physics – Algebra Based (385 p) |
| organic chemistry | Organic Chemistry (OpenStax) (499 p) |
| cell biology | Basic Cell and Molecular Biology (158 p) |
| thermodynamics | Heat and Thermodynamics (132 p) |
| genetics | Working with Molecular Genetics (143 p) |

---

## 5. Wiring, verified rather than assumed

`source_for` takes `**_` and an explicit `doc_resolver`, and a test asserts
both. That is not defensive style: `book_skeleton` calls
`source_for(subject, doc_resolver=…)`, and the mathematics domain shipped a
signature taking `subject` only — so every call raised `TypeError` into that
site's `except Exception`, was logged as "domain source lookup failed", and the
domain silently supplied nothing.

Building this domain also exposed that **`source_for` and `classify_concepts`
were never declared in the registry's `OPTIONAL` tuple**, despite being called
by `book_skeleton` for a long time. `contract_report` therefore could not see
them, and a domain implementing one wrongly looked complete. Both are now
declared, which is how the mismatch above would have been caught.

Verified through the real prompt path: the standing rule, the Johnstone level,
and the per-kind guidance all arrive in the system message, and `pair_block`
reaches the tutor through the registry.

---

## 6. Measured on the benchmark

Fingerprint `4faf5407715a9e4d` — the same instrument the maths and history runs
were taken under, so these are comparable.

    === Science — DOMAIN SCORE 3.164/5 ===
      right_move        2.47   accuracy      3.73
      domain_dimension  3.33   presentation  3.62   dialogue  2.73

| dimension | science | n |
|---|---|---|
| notation_speakable | 5.00 | 15 |
| misconception_handling | 4.71 | 7 |
| honest_telling | 3.80 | 5 |
| accuracy | 3.73 | 15 |
| **mechanism_over_recall** | **3.33** | 15 |
| visual_policy | 3.00 | 15 |
| visual_integration | 2.87 | 15 (unstable ±1.33) |
| progression | 2.50 | 15 |
| adaptation | 2.10 | 15 |
| socratic | 2.03 | 15 |

### Where it sits

| domain | score |
|---|---|
| mathematics | 3.390 (6 runs) |
| **science** | **3.164** |
| computer science | 3.096 |
| history | 2.993 |

Second of four on its first run, which answers "at the quality of the other
ones" on the composite. It does **not** clear the 3.5 release gate — no domain
in this system does, including the computer-science module the others were
asked to match.

### The two findings that matter more than the composite

**`adaptation` is 2.10 — the lowest of all four domains** (CS 2.67, maths 2.41,
history 2.13). Whatever this module does well, adapting to the learner in front
of it is not it, and that is now the weakest dimension in every domain built so
far. It is a property of the system, not of any one domain.

**The instrument names the failure directly:**

    derivable socratic 1.80  vs  arbitrary honest_telling 3.80
    -> Tells indiscriminately: it lectures where it should ask.

A `socratic` of 2.03 on a domain whose entire premise is *the learner predicts
first* is the uncomfortable result here. The module supplies POE material and
instructs the tutor to withhold the outcome; the tutor lectures anyway. Nothing
in §3 predicted that, and no amount of better mining fixes it — the pairs were
present and were not used as intended.

## 7. The ablation, which does not flatter this module

`HELGA_BENCH_NO_DOMAIN=1` withholds the concept kind and nothing else, same
fingerprint, same topics, same judge.

    composite   WITH 3.164   WITHOUT 3.181   delta -0.017   noise floor 0.376

**The domain layer did not improve the composite.** The difference is a
twentieth of the noise floor — not a small gain, not a small loss, nothing.

Per dimension, against the floors re-derived from four runs:

| dimension | with | without | Δ | floor | |
|---|---|---|---|---|---|
| accuracy | 3.73 | 4.60 | **−0.87** | 0.73 | exceeds |
| misconception_handling | 4.71 | 3.88 | **+0.83** | 0.75 | exceeds |
| socratic | 2.03 | 2.53 | **−0.50** | 0.47 | exceeds |
| visual_integration | 2.87 | 2.33 | +0.54 | 1.33 | noise |
| adaptation | 2.10 | 2.27 | −0.17 | 0.40 | noise |
| honest_telling | 3.80 | 2.60 | +1.20 | — | no floor |
| mechanism_over_recall | 3.33 | 2.80 | +0.53 | — | no floor |
| progression | 2.50 | 3.00 | −0.50 | — | no floor |

Read plainly: the layer appears to **help** misconception handling and its own
`mechanism_over_recall`, and to **hurt** accuracy and socratic questioning. The
gains and the losses roughly cancel, which is why the composite does not move.

### How much of this to believe

**One run per arm, n=15 each.** The floors above came from four runs; clearing
one on a single pair of runs is suggestive, not established. This project has
already retracted three single-run over-reads — run C's "floor-beating gains",
the `visual_policy` "regression", and a "monotonic decline" that dissolved when
a fourth run landed. Detecting effects below 0.4 needs roughly n=60 per arm.

Three dimensions have **no recorded floor at all** (`honest_telling`,
`mechanism_over_recall`, `progression`), so the +1.20 on honest_telling is the
largest number in the table and the least interpretable one.

### The finding worth acting on

`accuracy` falling 0.87 with the domain layer present is the result to chase,
because it is the one that would make the module actively harmful. A plausible
mechanism — untested — is crowding: the science block is the largest any domain
emits (standing rule, then a Johnstone level line, then per-kind guidance, then
POE material), and this codebase has already measured a 600-character cap
truncating a behaviour instruction into silence. If the concept's own material
is being displaced, accuracy is exactly what would fall.

That is a hypothesis. It has not been tested, and it should be before this
module is trusted in front of learners.

## 8. Not measured

* The crowding hypothesis above.
* Floors for `honest_telling`, `mechanism_over_recall` and `progression`.
* Anything at n>15 per arm.
* **Chemistry yield** is one pair per six pages. Unexplained.
* **The `LEVEL_BRIDGE` move** is defined and has a prompt block, but nothing
  mines one yet — it is reachable only if a caller constructs it by hand.
* The classifier's LLM path is untested against a real build; only the pattern
  path has been measured.
