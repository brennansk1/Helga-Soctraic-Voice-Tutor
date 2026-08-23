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

Per dimension, ranked the way `DIMENSION_FLOORS` says to rank them — by
MULTIPLE of the floor, because that block's own instruction is to "prefer a
dimension that moved several times its floor over one that just cleared it":

| dimension | with | without | Δ | floor | × floor |
|---|---|---|---|---|---|
| **honest_telling** | 3.80 | 2.60 | **+1.20** | 0.40 | **3.00×** |
| progression | 2.50 | 3.00 | −0.50 | 0.40 | 1.25× |
| accuracy | 3.73 | 4.60 | −0.87 | 0.73 | 1.19× |
| misconception_handling | 4.71 | 3.88 | +0.83 | 0.75 | 1.11× |
| socratic | 2.03 | 2.53 | −0.50 | 0.47 | 1.06× |
| visual_policy | 3.00 | 2.73 | +0.27 | 0.53 | 0.51× |
| adaptation | 2.10 | 2.27 | −0.17 | 0.40 | 0.42× |
| visual_integration | 2.87 | 2.33 | +0.54 | 1.33 | 0.41× |
| mechanism_over_recall | 3.33 | 2.80 | +0.53 | — | no floor |

**The only supportable per-dimension claim is `honest_telling` +1.20, at three
times its floor — and it is a GAIN from the domain layer.** Everything between
1.0× and 1.3× is a dimension that "just cleared it", which this instrument
explicitly says not to trust.

### A reading I got wrong, recorded because it is the recurring one

My first pass called accuracy (−0.87), misconception_handling (+0.83) and
socratic (−0.50) "exceeds floor" and concluded the layer hurt accuracy and did
not help. Three things were wrong with that:

1. **Barely clearing a floor is not clearing it.** Those three are 1.06–1.19×.
   `DIMENSION_FLOORS` says in its own comment to treat the floors as lower
   bounds and prefer multiples.
2. **`accuracy`'s measured identical-run spread IS 0.87** — the exact size of
   the observed delta. The same comment block records accuracy spanning
   4.13/4.27/5.00 across three identical runs, which is why its floor is the
   second highest in the table.
3. **I said `honest_telling` had no floor.** It has one, 0.40, and at 3× it is
   the strongest signal in the whole comparison — I dismissed the one result
   that was actually supportable.

The paired statistics agree: across the 15 matched dialogues the accuracy delta
is mean −0.87 with sd 2.17, giving a 95% interval of roughly **−1.96 to +0.23**,
which crosses zero. Nine of fifteen pairs are identical; the mean is carried by
five drops.

### And those drops are one learner profile

Three of the five are `confident_bluffer`, which scored **1 on all three topics
with the domain layer and 5 on all three without**. Reading that transcript
inverts the conclusion:

> **with the layer** — the tutor asks a gardener/selection question, the bluffer
> answers with "genetic drift favoring the most robust alleles", and the tutor
> replies: *"Genetic drift is random change, not the deterministic selection you
> described."* Correct, specific, and exactly the right move. **Scored 1.**
>
> **without** — the tutor asks an easier beetle question, the bluffer happens to
> answer correctly, and the tutor affirms it. **Scored 5.**

The tutor was more accurate in the arm that scored lower. What differed was the
STUDENT: the harder question elicited a florid bluff, and the dialogue then
contained false statements. This looks like the same class of instrument defect
already recorded for `contested_interpretation` on FACT topics — a dimension
measuring something other than what it names — and it should be checked before
any accuracy claim is made from a bluffing profile.

## 8. What the ablation actually supports

* The composite does not move: **−0.017 against a 0.376 floor.** On the
  headline number, this layer earns nothing.
* One dimension moves several times its floor: **honest_telling +1.20**.
* Nothing else is distinguishable from noise at n=15 on one run per arm.

## 9. Not measured

* Whether `accuracy` is contaminated by simulated-student content on bluffing
  profiles. The strongest lead here, and cheap to check.
* Any floor for `mechanism_over_recall`, this domain's own dimension.
* Anything at n>15 per arm. Detecting effects below 0.4 needs roughly n=60.
* **Chemistry yield** is one pair per six pages. Unexplained.
* **The `LEVEL_BRIDGE` move** is defined and has a prompt block, but nothing
  mines one yet — it is reachable only if a caller constructs it by hand.
* The classifier's LLM path is untested against a real build; only the pattern
  path has been measured.

---

## 10. An instrument defect, found while chasing the accuracy delta

The `accuracy` dimension is **unstable on adversarial transcripts and stable
everywhere else**. Measured by re-judging transcripts from the run above, each
a median-of-3 exactly as the benchmark scores them:

| profile | recorded | re-judged medians-of-3 |
|---|---|---|
| `fast_learner` | 5 | 5, 5, 5 |
| `confused_beginner` | 5 | 5, 5, 5 |
| **`confident_bluffer`** | **1** | **5, 3, 3, 3** |

Five independent measurements of one identical transcript spanning 1 to 5, with
the benchmark's own recorded value at the extreme low end — while two other
transcripts from the same run reproduce perfectly.

### Why that transcript in particular

All four tutor turns in it are factually correct. The clearest is:

> "Genetic drift is random change, not the deterministic selection you
> described."

and later, unprompted, the exact anti-teleology move the biology education
literature identifies as the central difficulty:

> "Selection acts on individuals, not populations with goals."

What distinguishes this dialogue is the STUDENT: a confident bluffer emitting
fluent, jargon-dense falsehoods ("genetic drift favoring the most robust
alleles"). `JUDGE_RUBRIC` anticipates exactly this —

> "The STUDENT in this transcript is a simulation and is SUPPOSED to say false
> things … A student error is NEVER evidence against accuracy."

— and the instruction is not sufficient. The judge sometimes charges the
student's errors to the tutor anyway.

### What this invalidates, and what it does not

**Invalidated:** any PER-DIALOGUE accuracy claim. That includes the pattern this
document previously reported — `confident_bluffer` scoring 1 with the domain
layer and 5 without, on all three topics — which is indistinguishable from
three draws of an unstable judge. Withdrawn.

**Not invalidated:** the aggregate. `accuracy`'s floor of 0.73 was derived from
run-to-run variation of the MEAN over 15 dialogues, which damps this, and the
composite is a mean of means. The headline result — the layer moves the
composite by −0.017 against a 0.376 floor — stands.

**Scope:** one profile in five, so roughly a fifth of dialogues carry the
unstable measurement. This is not a science finding; it applies to every
domain's accuracy number, including the recorded mathematics, history and
computer-science runs.

### The fix, and why it is not applied here

Structural rather than instructional: score `accuracy` on the tutor's turns
alone, with student turns elided. That removes the contamination by
construction instead of asking the judge to ignore what is in front of it.

**It would change `rubric_fingerprint()`,** which is the mechanism that stops
runs being compared across instrument changes. Every recorded baseline for
every domain would have to be re-taken. That is the owner's call, not a change
to make while a comparison is in flight.

---

## 11. A second identical run, and the accuracy claim finally dies

`A2` repeats `A1` exactly — same config, same fingerprint, nothing withheld.

| dimension | A1 | A2 | **spread on IDENTICAL runs** |
|---|---|---|---|
| **accuracy** | 3.73 | 5.00 | **1.27** |
| misconception_handling | 4.71 | 4.00 | 0.71 |
| visual_policy | 3.00 | 3.53 | 0.53 |
| adaptation | 2.10 | 1.80 | 0.30 |
| mechanism_over_recall | 3.33 | 3.47 | 0.14 |
| socratic | 2.03 | 1.93 | 0.10 |
| progression | 2.50 | 2.53 | 0.03 |
| honest_telling | 3.80 | 3.80 | 0.00 |
| visual_integration | 2.87 | 2.87 | 0.00 |

**`accuracy` moved 1.27 between two runs that differ in nothing.** That is 1.7×
its own recorded floor, and larger than the 0.87 this document originally
attributed to the domain layer. Averaging the two A runs against B gives an
accuracy delta of **−0.23**, or 0.32× floor.

The claim that the science layer harms accuracy is dead. It was never an
effect; it was one draw of the noisiest dimension in the instrument.

### What survives with A averaged over two runs

| dimension | A mean | B | Δ | floor | × floor |
|---|---|---|---|---|---|
| **honest_telling** | 3.80 | 2.60 | **+1.20** | 0.40 | **3.00×** |
| **mechanism_over_recall** | 3.40 | 2.80 | **+0.60** | none | A-spread 0.14 |
| progression | 2.52 | 3.00 | −0.49 | 0.40 | 1.22× |
| socratic | 1.98 | 2.53 | −0.55 | 0.47 | 1.17× |
| visual_policy | 3.27 | 2.73 | +0.53 | 0.53 | 1.00× |
| visual_integration | 2.87 | 2.33 | +0.54 | 1.33 | 0.41× |
| adaptation | 1.95 | 2.27 | −0.32 | 0.40 | 0.80× |
| accuracy | 4.37 | 4.60 | −0.23 | 0.73 | 0.32× |

Two gains stand up and no harm does:

* **`honest_telling` +1.20 at three times its floor**, and identical across both
  A runs (spread 0.00). The strongest result in the comparison.
* **`mechanism_over_recall` +0.60** — this domain's OWN dimension, the one that
  says whether it does its specific job, with an A-spread of 0.14.

The two apparent losses, `socratic` and `progression`, clear their floors by
17% and 22%. This instrument says not to trust that, and their A-spreads
(0.10, 0.03) come from two runs, which the same comment block warns is a bad
lower bound — accuracy's two-run estimate was 0.13 before a third run made it
0.87.

**`mechanism_over_recall` still has no proper floor.** Its 0.14 is a two-run
gap, exactly the estimate type that has already misled this project once. A
third A run would settle whether +0.60 is real. Until then it is the most
promising number here and not an established one.

### The honest summary of the whole exercise

The layer does not move the composite. On the two dimensions where a science
module should show up — telling the truth about what is settled, and pushing
for mechanism over labels — it shows gains, one of them well clear of its
floor. Every "harm" I reported dissolved on measurement.

---

## 12. Three runs — and the only surviving effect is this domain's own dimension

A third identical A run gives every dimension its first three-run spread.

| dimension | A1 / A2 / A3 | spread | A mean | B | Δ | × spread |
|---|---|---|---|---|---|---|
| **mechanism_over_recall** | 3.33 / 3.47 / 3.60 | **0.27** | 3.47 | 2.80 | **+0.67** | **2.5×** |
| visual_policy | 3.00 / 3.53 / 3.40 | 0.53 | 3.31 | 2.73 | +0.58 | 1.1× |
| adaptation | 2.10 / 1.80 / 2.00 | 0.30 | 1.97 | 2.27 | −0.30 | 1.0× |
| misconception_handling | 4.71 / 4.00 / 4.90 | 0.90 | 4.54 | 3.88 | +0.66 | 0.7× |
| socratic | 2.03 / 1.93 / 2.47 | 0.54 | 2.14 | 2.53 | −0.39 | 0.7× |
| honest_telling | 3.80 / 3.80 / **2.60** | **1.20** | 3.40 | 2.60 | +0.80 | 0.7× |
| progression | 2.50 / 2.53 / 3.07 | 0.57 | 2.70 | 3.00 | −0.30 | 0.5× |
| visual_integration | 2.87 / 2.87 / 2.20 | 0.67 | 2.65 | 2.33 | +0.32 | 0.5× |
| accuracy | 3.73 / 5.00 / 4.47 | 1.27 | 4.40 | 4.60 | −0.20 | 0.2× |

### `honest_telling` is withdrawn, and it is the most instructive withdrawal

Section 11 called `honest_telling` **+1.20 at three times its floor, identical
across both A runs (spread 0.00)** — "the strongest result in the comparison".

The third run scored **2.60**. Its spread is **1.20**, the exact size of the
"gain".

This is precisely the failure `DIMENSION_FLOORS` documents in its own comment:
accuracy's two-run estimate was 0.13 before a third run made it 0.87, and
"everything claimed against the two-run floors had to be withdrawn". I quoted
that warning in section 8 and then made the same mistake two sections later,
because 0.00 across two runs looks like certainty and is not — two samples
estimate the gap between two draws, not a spread.

### What actually survives

**One dimension: `mechanism_over_recall`, +0.67 at 2.5× its three-run spread.**

That is this domain's OWN dimension — did the tutor push toward the causal
mechanism rather than settle for the correct label. It is the single thing this
module was built to move, and it is the single thing that moved.

Nothing else clears 1.1×. Every apparent harm — accuracy, socratic,
progression, adaptation — is inside the noise.

### Is that "the quality of the other ones"?

In KIND, yes, and this is the right shape for a domain layer: it moves its own
dimension and leaves the rest alone. The mathematics ablation has the same
shape — `misconception_handling` fell 2.25 when its layer was withheld, three
times that dimension's floor.

In MAGNITUDE, no: 0.67 against maths's 2.25. Science's layer does its job, and
does it about a third as strongly as the best one here.

The composite does not move for either of them.

### Correcting "a third as strongly"

Section 12 compared science's +0.67 against mathematics' 2.25 and concluded the
layer works "about a third as strongly". **That comparison is invalid**, and it
is the same error this document spent four sections correcting elsewhere:
comparing raw magnitudes across dimensions with different variances.

`--noise-floor` over the three A runs gives science's own floors, in the same
units as `DIMENSION_FLOORS`:

    composite floor: 0.354
    "accuracy": 1.27,   <-- cannot support a 1-point claim
    "honest_telling": 1.20,   <-- cannot support a 1-point claim
    "misconception_handling": 0.90,
    "mechanism_over_recall": 0.27,

(The tool independently marks `accuracy` and `honest_telling` as unable to
support a 1-point claim — which is exactly what this document tried to make of
each of them before withdrawing both.)

Expressed properly, in multiples of each dimension's own noise:

| domain | dimension moved | Δ | floor | × floor |
|---|---|---|---|---|
| mathematics | misconception_handling | 2.25 | 0.75 | 3.0× |
| **science** | mechanism_over_recall | 0.67 | 0.27 | **2.5×** |

2.5× against 3.0× — not a third as strong, about five-sixths as strong.

And the comparison set matters: the mathematics ablation is on record in this
project as **the only floor-beating result** among the domains tested, meaning
the computer-science and history layers showed no effect clearing their floors
at all. That is a recorded claim from earlier work rather than something
re-measured here, and it should be re-verified before being leaned on.

If it holds, the ranking by "does this layer demonstrably do anything" is:

    mathematics 3.0x  >  science 2.5x  >  computer science, history (no effect)

Science is second of four, above two domains whose layers show nothing
measurable. On the standard that actually matters for a domain module — does it
move its own dimension, clear of that dimension's noise — this one qualifies.
