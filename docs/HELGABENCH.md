# HelgaBench — measuring whether the tutor actually tutors

`tools/grading_eval.py` measures grading accuracy on static cases. That is
necessary and not sufficient: it cannot tell you whether the tutor *tutors*.
The failures that matter to a learner are conversational — lecturing instead of
questioning, missing a stated misconception, accepting a fluent bluff, looping
when the student is stuck. None of them appear in a single-turn grade.

HelgaBench drives the tutor with a **profile-driven student simulator** and
scores the transcript against an explicit rubric. Simulator, tutor and judge
are three separate model calls, so the tutor never grades its own work.

---

## Why these dimensions

The rubric is not invented. Each dimension traces to a specific result in the
tutoring literature, and the ones we added for domain benchmarking trace to the
same places.

| dimension | what it operationalises |
|---|---|
| **socratic** | Chi & Wylie's ICAP: *interactive* and *constructive* activity beat *active* and *passive*. A monologue leaves the learner passive however good the prose. |
| **adaptation** | Wood, Bruner & Ross on scaffolding — *contingency*: support is calibrated to what this learner has just shown, and faded as they improve. |
| **accuracy** | The floor. VanLehn's analysis of why some tutoring systems fail to beat text: incorrect content is worse than none. |
| **progression** | Step-level granularity — a turn that does not advance the solution state is a turn the learner did not need. |
| **misconception_handling** | Graesser's expectation- and misconception-tailored dialogue: naming and confronting the specific false belief, not offering generic encouragement. |
| **honest_telling** | Koedinger & Aleven's *assistance dilemma* — eliciting is not always better than telling, and for content that cannot be derived it is worse. See below. |
| **visual_integration** | Mayer's multimedia principles: a figure helps when it is *referred to* and *contiguous* with the words; a decorative figure is extraneous load. |

The five-profile design and the simulator/judge separation follow the pattern
used by TutorBench (DeepTutor) and the pedagogical-ability evaluations that
grew out of the BEA shared task. The taxonomy that most resembles ours is
Maurya et al.'s eight-dimension scheme (mistake identification, mistake
location, revealing of the answer, providing guidance, actionability,
coherence, tutor tone, human-likeness); our `misconception_handling` covers the
first two, `socratic` covers *revealing of the answer* inversely, and
`progression` covers actionability and coherence.

> **Verify before citing externally.** These references are recorded from
> working knowledge, with enough detail to look up. Confirm the exact venue,
> year and DOI before quoting any of them in a paper or a public claim about
> this repository. See "References" at the end.

---

## The two things a general rubric misses

A generic tutoring rubric scores a maths dialogue and a history dialogue with
the same yardstick, and the yardstick is wrong for both in opposite
directions. Two properties matter enough to score explicitly.

### 1. Not everything can be taught Socratically

You cannot elicit that the symbol for gold is `Au`, that Python indexes from
zero, or that the Battle of Hastings was 1066. These are **arbitrary** — true
by convention or by contingent fact, not derivable from anything the learner
already knows.

Asking "can you guess what year Hastings was?" is not Socratic teaching. It is
a guessing game that wastes the learner's turn and teaches them the tutor is
not paying attention. Koedinger & Aleven's assistance dilemma is precisely
this trade: there is a point at which withholding information stops helping.

So the benchmark separates every topic into **derivable** and **arbitrary**
content, and scores them by *opposite* criteria:

- on derivable content, telling the answer is a failure (`socratic`)
- on arbitrary content, refusing to tell is a failure (`honest_telling`)

A tutor that scores well on both is doing the hard thing: knowing which is
which. A tutor that Socratises everything scores well on one and badly on the
other, and that pattern is diagnostic rather than merely bad.

### 2. The figure is part of the teaching

Helga can draw twelve kinds of figure inline (`number_line`, `geometry`,
`plot`, `bars`, `graph`, `timeline`, `table`, `venn`, `cycle`, `steps`,
`fraction`, `image`) and marks each with a provenance tier — `computed`
(deterministic from data), `retrieved` (from a grounded source, with
attribution) or `authored` (the model's own sketch, unverified).

A tutor that never draws is not using the product. A tutor that draws
decoration is adding extraneous load. A tutor that draws *the answer it is
asking the student to find* has converted a Socratic turn into a lecture with
pictures — which is why the aid grammar has a `stage` field, and why staging is
scored.

---

## What is measured deterministically

Judged scores are expensive and noisy. Anything that can be computed from the
transcript is computed, and only the genuinely subjective parts go to a judge.

| check | how |
|---|---|
| aid offered | an ```` ```aid ```` fence appears in the turn |
| aid JSON valid | it parses, and `kind` is one of the twelve |
| aid kind is the right one | matches `aid_policy.affinity_for(tags, title, question_type)` |
| aid density respected | at most one per message, and no closer than `MIN_TURNS_BETWEEN_AIDS` |
| the answer was withheld | the element carrying the target value has `"stage": 1` |
| the figure was not narrated | the message does not describe the figure back ("as you can see…") |
| notation is speakable | `math_speech.unspoken()` returns nothing for the turn |

That last one matters for a **voice** tutor specifically: `\perp` and
`\hat{}` currently come back untranslated, so a spoken lesson reads them as
raw LaTeX.

---

## Noise, and why single runs do not gate

Measured on this repository: one judge call on the core rubric swings **±2 on
an identical transcript** (observed 5, 3, 3, 5), and end-to-end the benchmark
moves **±1.4/5 between identical runs**.

Therefore:

- every judged dimension is the **median of N samples** (default 3)
- `misconception_handling` is sampled separately and returns **`None`** when
  the student never erred — not a zero, because an absent key clamped to the
  floor is a score manufactured from silence
- no gate may be set on a single run

### The bench now supplies the turn state (fixed 2026-08-21)

`run_dialogue` calls the production prompt builder, and its comment claimed it
used "the REAL production prompt". That was **not accurate**, and the gap was
found on 2026-08-21 while trying to measure A.2.

`get_socratic_tutor_prompt` takes 14 inputs. The FSM supplies all 14. The
benchmark supplied **four**: `context_text`, `conversation_history`,
`bloom_level`, `aid_policy`. Current state:

| production input | supplied? |
|---|---|
| `context_text`, `conversation_history`, `bloom_level`, `aid_policy` | yes, always |
| `turn_state` | **yes, since 2026-08-21** — see below |
| `learner_history` | no, and correctly so: a simulated student has no past sessions |
| `misconceptions` | **no — and the bench scores `misconception_handling` while withholding the list production provides** |
| `grade_band`, `style_modifier`, `user_profile` | no — the personalisation machinery `adaptation` is meant to measure |
| `system_note`, `analogies`, `prior_concepts`, `health_strand6` | no |

So every score published before 2026-08-21 — `socratic` ~2.14, `adaptation`
1.33–2.80 — describes a **stripped-down tutor**, not the shipped one. This does
not mean the product was secretly better; it means the instrument and the
product were not the same system, and those figures inherit that caveat.

**`turn_state` is now supplied.** The bench grades each simulated student answer
with the production grading prompt (`_grade_student_turn`) and feeds the result
into `TurnState`, exactly as the FSM does. This was blocking: A.2's structured
turn state is built from graded answers, nothing in the bench produced a grade,
so the one intervention aimed at the semantic quality of a question was
invisible to the instrument measuring it.

`misconceptions` is the next one worth supplying, because the bench scores a
dimension it withholds the input for.

**The fingerprint hole is closed.** `rubric_fingerprint()` covered the rubric
text and judge prompts but **not which production inputs the bench supplies**,
so changing what the tutor is told would have compared two different systems
while claiming they matched. `BENCH_PROMPT_INPUTS` is now part of the hashed
payload, with a test asserting that adding an input changes the fingerprint.
Supplying the turn state moved it from `c98fa5eb86455db5` to `a21992105fe9aad7`,
correctly invalidating every earlier baseline.

Note the distinction this rests on. Changing the **dialogue contract's rules**
is a change to the PRODUCT — the contract runs in `fsm_logic` too, and the bench
applies it because production does — so it does not bump the fingerprint, and a
before/after comparison across it is exactly what the benchmark is for. Changing
**which inputs the bench supplies** is a change to the INSTRUMENT and does bump
it. Getting that backwards would either block a valid measurement or licence an
invalid one.

### What `socratic` is NOT measuring

Measured 2026-08-21 across five domain runs, 11 dialogues scored `socratic` >= 4
against 58 scored <= 2:

| feature of the tutor's turns | high (>=4) | low (<=2) |
|---|---|---|
| declarative statements per turn | 2.32 | 2.62 |
| words per turn | 40.3 | 43.8 |
| ends with a question | 0.98 | 1.00 |
| repeats an earlier turn | 0.00 | 0.02 |
| open-stem questions (why/how/what-if) | 0.50 | **0.61** |
| question length in words | 17.5 | 17.1 |

**No surface feature of a tutor turn predicts the score**, and the open-question
hypothesis runs backwards. An apparent signal in student word count is an
artifact of the student PROFILE: `silent_struggler` averages 8.3 words and
`confident_bluffer` 63.2, a spread that swamps the 6.5-word gap between the
groups.

This matters for anyone reading the scores. `socratic` has a floor of **0.00
across three identical runs** — the most reproducible dimension in the
instrument — so it is not arbitrary. It is measuring something **semantic**:
whether the question follows from what this particular student just reasoned.
A turn can be short, end in a question, avoid repetition and use an open stem
and still be generic.

The practical consequence: this dimension **cannot be moved by surface rules**,
and interventions that target turn shape should not be expected to move it. Two
did not (A4.1a, A.1) before this was measured.

### The floor is per-dimension, not just per-score

A single composite floor is necessary and **not sufficient**. Two identical
mathematics runs (n=15 dialogues each, `nail-35b-a3b-ctx`, 2026-08-20) moved
the composite by only 0.162 while individual dimensions moved much further:

| dimension | swing between identical runs |
|---|---|
| visual_integration | **1.20** — cannot support a one-point claim at n=15 |
| progression | 0.47 |
| honest_telling | 0.40 |
| visual_policy | 0.27 |
| misconception_handling | 0.23 |
| accuracy, adaptation, notation_rigour, notation_speakable | 0.13 |
| socratic | 0.00 |

So a per-dimension claim resting on the composite floor can be pure noise. The
floors live in `DIMENSION_FLOORS`, `compare()` labels every dimension REAL or
noise against its own floor, and `--noise-floor a.json b.json` regenerates them
rather than leaving them a hand-copied constant.

### Means, not medians, in every reported figure

Dimensions are the **median of N judge samples** for one dialogue — that is
sampling noise reduction and it stays. But **aggregation across dialogues is a
mean.** Reporting a median across dialogues was a real defect in this tool:
across the two identical runs above, the `visual_integration` median went
**5 → 1** — a four-point swing on a five-point scale, from nothing at all —
while its mean moved 3.27 → 2.07. The composite was always built from means, so
the headline was sound while the table beneath it was not. The dimension table
now prints mean, n, and observed range, and labels any dimension whose own
floor exceeds 1.0 as unstable.

The judge ships a **calibration self-check** with three planted transcripts: a
tutor that accepts a misconception must score ≤ 2, one that corrects clearly
must score ≥ 4, and a dialogue where the student never erred must return
`None`. If those do not hold the tool prints `JUDGE MISCALIBRATED — its scores
cannot be trusted` and the run is void.

> A false MISCALIBRATED verdict has happened twice, and it was the harness, not
> the judge: a cold model load (measured 3m31s) exceeded the judge's 60s
> timeout and tripped the circuit breaker. Tools call `warm_up()` first for
> this reason.

---

## Running it

```bash
# core benchmark, all five student profiles
python3 tools/helgabench.py --repeat 3 --out baseline.json

# is the judge trustworthy right now?
python3 tools/helgabench.py --self-test

# per-domain, with aid and telling scored
python3 tools/bench_domains.py --domain mathematics --repeat 3
python3 tools/bench_domains.py --all --out docs/baselines/domains.json

# deterministic checks only — no model required, runs in CI
python3 tools/bench_domains.py --static-only
```

---

## References

Recorded from working knowledge; **verify venue, year and DOI before citing
externally**.

- Bloom, B. S. (1984). *The 2 Sigma Problem: The Search for Methods of Group
  Instruction as Effective as One-to-One Tutoring.* Educational Researcher.
  — the aspiration this product is measured against.
- Chi, M. T. H., & Wylie, R. (2014). *The ICAP Framework: Linking Cognitive
  Engagement to Active Learning Outcomes.* Educational Psychologist.
- Wood, D., Bruner, J. S., & Ross, G. (1976). *The Role of Tutoring in Problem
  Solving.* Journal of Child Psychology and Psychiatry. — scaffolding,
  contingency, fading.
- Koedinger, K. R., & Aleven, V. (2007). *Exploring the Assistance Dilemma in
  Experiments with Cognitive Tutors.* Educational Psychology Review. — the
  basis for `honest_telling`.
- VanLehn, K. (2011). *The Relative Effectiveness of Human Tutoring,
  Intelligent Tutoring Systems, and Other Tutoring Systems.* Educational
  Psychologist.
- Graesser, A. C., et al. *AutoTutor* — expectation- and misconception-tailored
  dialogue.
- Mayer, R. E. *Multimedia Learning* — multimedia, coherence, signalling and
  contiguity principles; the basis for `visual_integration`.
- Tack, A., & Piech, C. (2022). *The AI Teacher Test: Measuring the Pedagogical
  Ability of Blender and GPT-3 in Educational Dialogues.* EDM.
- Maurya, K. K., et al. (2025). *Unifying AI Tutor Evaluation: An Evaluation
  Taxonomy for Pedagogical Ability Assessment of LLM-Powered AI Tutors.* NAACL
  / BEA shared task.
- Stasaski, K., et al. (2020). *CIMA: A Large Open Access Dialogue Dataset for
  Tutoring.* BEA.
