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
