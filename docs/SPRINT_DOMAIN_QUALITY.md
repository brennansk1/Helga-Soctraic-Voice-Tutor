# Sprint: domain teaching quality, and teaching code properly

**Instrument:** `tools/bench_domains.py`, fingerprint `c98fa5eb86455db5`
**Baselines:** `docs/baselines/domain_*.json`, measured 2026-08-20 17:17–19:35
**Methodology:** `docs/HELGABENCH.md`

Every gate below is a number this instrument produces. Nothing in this sprint
is done because it sounds right; it is done when the measurement moves and a
repeat run says the movement is larger than the noise.

---

## 0. What the baselines actually say

|  domain | score | right_move | present | dialogue | accuracy | domain dim |
|---|---|---|---|---|---|---|
| Computer science | 3.34 | 2.33 | 3.71 | 3.00 | 4.27 | 3.53 |
| Medicine | 3.16 | 2.20 | 3.31 | 2.34 | 3.80 | 4.20 |
| Science | 3.00 | 2.47 | 3.13 | 2.11 | 4.33 | 2.87 |
| Language & literature | 2.87 | 2.13 | 3.82 | 2.86 | 4.13 | 1.60 |
| Mathematics | 2.63 | 2.03 | 3.04 | 2.28 | 3.03 | 2.80 |
| History | 2.58 | 2.20 | 3.27 | 2.31 | 3.60 | 1.53 |
| Law | 2.54 | 1.80 | 3.40 | 2.52 | 3.33 | 1.80 |

Median 2.87. **The important reading is not the ranking, it is what is
identical across all seven.**

    socratic       2.00 in every domain
    visual_policy  1.00 in six of seven
    honest_telling 1.00 in five of seven
    right_move     1.80–2.47, the lowest component everywhere

Seven domains, five student profiles, three topics each, and `socratic` lands
on exactly 2.00 every time. That is not seven problems. It is one behaviour,
and accuracy sitting at 3.03–4.33 in the same runs says it is not a knowledge
problem.

**Where the domains genuinely differ is one axis, and it is informative:**
history (1.53) and literature (1.60) score their own dimension far below
medicine (4.20) and computer science (3.53). Those first two ask whether the
tutor can hold a question open; the second two ask whether it can draw a hard
line. **The tutor draws lines well and holds questions open badly.** A general
rubric could not have told us that, which is the argument for keeping the
domain split.

---

## 1. The domain set

Seven exist. Two more are proposed, and the reasoning is stated because the
choice is a guess dressed as a decision otherwise.

**We have no usage data.** Nobody has used this product yet. So "top 5 most
common" cannot be measured, only argued from what the product is: an offline,
self-directed adult learner with a degree planner. The evidence available is
thin — the courses actually built in this repo are causal inference, the
Pythagorean theorem and The Art of War.

Ranked by expected volume for THIS product:

| # | domain | status | why |
|---|---|---|---|
| 1 | **Programming / CS** | exists | the largest adult self-study category, and the one where a tutor can verify an answer deterministically |
| 2 | **Mathematics & statistics** | exists | foundational, gates the degree tier, and the domain most improved by tools |
| 3 | **Economics & finance** | **ADD** | very high adult self-study volume; currently unrepresented |
| 4 | **Natural sciences** | exists (science) | consider splitting bio / chem / physics if the single dimension proves too coarse |
| 5 | **Languages** | **ADD** | huge volume, and the one where Helga's TTS/STT is a genuine differentiator rather than a nicety |

Medicine, law, history and literature stay. They are lower-volume but they are
the *discriminating* cases — history and literature are currently the two
lowest domain dimensions, so dropping them would hide the weakness.

**A4.1 — add `economics_and_finance`** (dimension: `quantified_claim` — does
the tutor make the student attach a number and a mechanism to an assertion,
rather than accepting a directional story?)

**A4.2 — add `languages`** (dimension: `production_over_recognition` — does the
tutor make the learner PRODUCE the form, not merely recognise it? Needs an
arbitrary topic: gendered nouns are arbitrary and must simply be told.)

Both need the same shape as the others: two derivable topics, one arbitrary,
an expected aid kind. `tests/tools/test_bench_domains.py` enforces that shape,
so a malformed addition fails rather than silently under-measuring.

---

## 2. Lane A — conversation fidelity (the one that matters)

**This is the whole of `socratic` 2.00.** From the judge's own words on the
maths run, three hallucinations of dialogue history in fifteen dialogues:

- *"apologizing for confusion that never existed"*
- *"incorrectly claims the student made a calculation error… despite the
  student having already correctly identified"*
- *"the student falsely claimed it was not provided, and the tutor failed to
  correct this hallucination"*

plus four cases of missing an error the student really did make.

Already ruled out: history reaches the model as correctly paired
user/assistant messages, in full, untruncated. **The tutor has the transcript
and misremembers it.**

Also already ruled out as the fix: A4.1a, the dialogue contract. It works —
turns over 60 words went 38% → 5%, ends-with-a-question 98% → 100% — and
`socratic` did not move at all. **Shorter turns ending in questions are not
Socratic teaching.** Do not spend more effort on turn shape.

**A.1 — GROUND EVERY CLAIM ABOUT THE STUDENT IN A QUOTE.**
The contract's `reference` rule checks word overlap, so a turn can satisfy it
while mischaracterising what the learner said. Extend it: if a turn asserts
the student did something ("you said", "your error", "you're confusing",
"as you noted"), it must quote a span that actually appears in their last two
messages. Deterministic, same named-violation regeneration as A4.1a.
*Gate as originally written: hallucination count in a 15-dialogue run, counted
from judge `worst_moment` text, drops from 3 to 0. socratic ≥ 3.0 median.*

**RESULT (2026-08-21) — SHIPPED, NO MEASURABLE EFFECT. This entry replaces an
earlier one that claimed a real +0.371; that claim was WRONG and the reasoning
is worth keeping because the mistake is repeatable.**

What was claimed first: composite 2.988 -> 3.359, with `accuracy` and
`misconception_handling` both reaching a flat 5.00, called REAL against
per-dimension floors of 0.13 and 0.23.

Why it was wrong: those floors came from **two** runs. A third run put
`accuracy` at 4.13 / 4.27 / 5.00 — a spread of **0.87, nearly seven times the
two-run estimate**. Two samples do not estimate a spread; they estimate the gap
between two draws, which is a lower bound and usually a poor one.

The decisive evidence is the B.1 run, which **still contains A.1**: `accuracy`
fell back to 4.33 there. If A.1 had produced the flat 5.00, it would still be
producing it. It did not replicate.

Re-judged against three-run floors, **every dimension is noise and the
composite delta (+0.371) is inside the composite floor (0.533)**:

| dimension | before | after | verdict |
|---|---|---|---|
| visual_integration | 3.27 | 4.20 | noise (floor 2.13) |
| notation_rigour | 3.07 | 3.93 | noise (floor 1.00) |
| accuracy | 4.13 | 5.00 | noise (floor 0.87) |
| misconception_handling | 4.43 | 5.00 | noise (floor 0.80) |
| socratic | 1.93 | 1.93 | no movement |
| adaptation | 1.67 | 1.67 | no movement |

**A.1 stays in the product.** It is correct, it is tested (31 contract tests),
and it demonstrably catches invented attributions in the real FSM path. It
simply did not move this instrument. Shipping a correct change with an honest
"no measured effect" is the right outcome; claiming the +0.371 was not.

Composite 2.988 → 3.359 (+0.371, floor 0.162). Per dimension, against the
per-dimension floors measured the same day:

| dimension | before | after | | |
|---|---|---|---|---|
| accuracy | 4.13 | **5.00** (no spread) | +0.87 | REAL (floor 0.13) |
| notation_rigour | 3.07 | 3.93 | +0.87 | REAL (floor 0.13) |
| misconception_handling | 4.43 | **5.00** (no spread) | +0.57 | REAL (floor 0.23) |
| notation_speakable | 4.87 | 4.60 | −0.27 | REAL regression (floor 0.13) |
| **socratic** | 1.93 | **1.93** | 0.00 | **no movement** |
| adaptation | 1.67 | 1.67 | 0.00 | no movement |
| visual_integration | 3.27 | 4.20 | +0.93 | inside its own 1.20 floor — noise |

Two things to be honest about.

**The gate could not be evaluated as specified.** A keyword count over judge
prose conflates three unrelated things: the tutor inventing an attribution
(what A.1 targets), the tutor stating a false fact about the subject, and the
*simulated student* hallucinating — which is the student profile working as
designed. The post-A.1 run scores 3 "flags", but reading them, all three are
student-side or adaptation complaints; one literally reads *"the tutor
repeatedly ignores the student's confident bluffs and hallucinated historical
facts, instead delivering factual corrections"*, i.e. the tutor behaving
correctly. Pre-A.1 runs score 3, 4, 4 and 5 on the same detector, so even the
raw count moves less than its own noise. **Do not re-use this gate.** The
replacement is below.

that $y$ is treated as an 'independent variable'"* and none survive. It is a
bigger effect than the gate anticipated, on dimensions the gate did not name.

**What A.1 did not do**: `socratic` is 1.93 before and 1.93 after — identical
to two decimal places. Combined with A4.1a (turn shape fixed, socratic
unmoved), two independent interventions have now left `socratic` exactly where
it was. Turn shape is not it and truthfulness is not it. A.2 is the remaining
hypothesis; if A.2 also fails to move it, stop attacking `socratic` directly
and investigate what the dimension is actually scoring.

*Replacement gate for any future truthfulness work: `accuracy` and
`misconception_handling` hold at 5.00 with zero spread. Both are cheap to check
and neither depends on keyword-matching the judge's prose.*

**A.2 — A STRUCTURED TURN STATE, NOT A TRANSCRIPT.**
The model is being asked to re-read the dialogue every turn and infer state.
Give it the state explicitly instead: what the student has got right, what
they got wrong and how, what is still open. Built in code from the graded
answers, not inferred by the model.
*Gate: adaptation ≥ 3.0 MEAN across domains (currently 1.67 on mathematics,
1.33–2.80 across the seven). Mean, not median — see §7. The measured
per-dimension floor for adaptation is 0.13, so a move of this size is well
inside what this instrument can resolve.*

**BUILT 2026-08-21 — `services/common/turn_state.py`, not yet measured.**

The grader has always produced exactly this data — a grade, the concepts an
answer missed, and a reason, for every answer — and `fsm_logic` used it for
scheduling and mastery gates and then **discarded it**. The tutor was handed
the raw transcript and asked to re-derive, in prose, what had been established,
on every turn, while also teaching. It derived it badly, and that is what
`adaptation` 1.33–2.80 measures.

`TurnState` carries it forward instead. Rendered into the prompt:

    WHAT THIS STUDENT HAS DEMONSTRATED (from graded answers — this is fact,
    not your impression):
      ALREADY ESTABLISHED: <question> — they answered "<quote>"
      Do not re-teach or re-ask these. Build on them.
      STILL WRONG: <question> — they said "<quote>" (<grader's reason>)
      Address the error above before moving on.
      NOT YET COVERED: <concepts the answers keep missing>
      They have now tried this question 2 times. Change your approach —
      asking it again in different words has already failed.

Three properties worth stating, because each is a way this could have gone
wrong:

- **A fallback grade is not evidence.** `_parse_grade_response` returns grade 2
  with `graded: False` when the grading call fails — a fail-safe that never
  credits mastery. Recording it would mean that during an LLM outage the tutor
  invents an entire history of half-understanding that never happened. Refused,
  and tested.
- **A result with no usable grade is not an assessment.** Found by its own
  test: `{}` fell through as grade 0 and was logged as a WRONG answer, i.e. the
  module inventing an error the student never made — the exact failure it
  exists to prevent.
- **It states the record, not an opinion.** No "this student is struggling".
  The point is to replace the model's impression with the grader's verdict.

Renders to nothing until something has actually been graded, so the opening
turn carries no empty scaffold.

Verified end-to-end through `get_typed_socratic_prompt`: a correct answer
becomes ESTABLISHED, a later correct answer clears the earlier error on that
question, a second wrong answer supersedes the first, and two attempts trigger
the change-approach instruction. 15 unit tests, 476 in the surrounding suite.

**A.3 — DETECT THE UNADDRESSED ERROR.**
Four of the fifteen failures are "the student erred and the tutor moved on".
The grader already produces a grade per answer; when it is low and the next
tutor turn does not reference the error, that is a contract violation.
*Gate: misconception_handling holds ≥ 4.0 while socratic rises (it is
currently 5.0 — this must not be bought by breaking it).*

---

## 3. Lane B — the tutor ignores the aid request

`visual_policy` 1.00 in six of seven means precisely: **the policy asked for a
figure and none was produced.** The policy fix landed (eigenvalues now
correctly asks for a plot; the Battle of Hastings date correctly asks for
nothing). The tutor declines.

**B.1 — A `generate` DECISION SHOULD READ AS A REQUEST, NOT A PERMISSION.**
`prompt_nudge` says "You may draw ONE diagram this turn… If it would only
decorate, do not", sitting beside rules that say "most turns need none" and
"No diagram is better than a pointless one". The policy has ALREADY weighed
that cost — that is what `generate` means. Re-litigating it in the prompt is
why nothing gets drawn.
*Gate: visual_policy ≥ 3.0 MEAN across domains (currently 2.33 on
mathematics; floor 0.27).*

**B.2 — PREFER THE BUILD-TIME ASSET.**
Courses ship assets already (course_440a8494: 44 for 24 concepts). A `reuse`
costs no model call, keeps provenance, and cannot draw the answer. The
benchmark scores it 5. Make the policy reach for `reuse` before `generate`
whenever a slot fits.
*Gate: reuse ≥ 50% of figures shown, measured by `aids_reused` vs
`aids_drawn` in the results JSON.*

---

## 4. Lane C — say the thing that cannot be derived

`honest_telling` 1.00 in five of seven. On a convention, a name or a date, the
tutor will not simply state it. Note this is NOT the "Socratises everything"
pattern — the maths run showed derivable socratic 2.00 against arbitrary
honest_telling 3.00, so it is weak at both rather than miscalibrated between
them.

**C.1 — ROUTE ARBITRARY CONTENT DIFFERENTLY.**
`aid_policy.is_arbitrary()` already detects convention/name/date content and
correctly suppresses diagrams for all seven arbitrary topics. Reuse that
signal in the prompt: on arbitrary content, instruct plainly — state it, then
ask about something that CAN be reasoned about (why the convention exists,
what it enables, what breaks without it).
*Gate: honest_telling ≥ 4.0 MEAN (currently 1.40; floor 0.40), and derivable
`socratic` must not fall —
the failure mode of this fix is a tutor that starts telling everything.*

---

## 5. Lane D — teaching code properly

Computer science scores highest (3.34) and is the most under-equipped. What
exists today:

- fenced blocks render as bare `<pre><code>`. **The language tag is captured
  by the regex and discarded** — `session.js:201` — so nothing is highlighted.
- **there is no `code` aid kind.** KINDS is number_line, geometry, plot, bars,
  graph, timeline, table, venn, cycle, steps, fraction, image. Code therefore
  cannot use the `stage` mechanism, which is the one thing that lets a figure
  ask a question instead of answering it.
- `_t_run_python` exists, is off by default, and its own docstring says `-I`
  isolation "is NOT a real sandbox".

**D.1 — SYNTAX HIGHLIGHTING.** The language is already parsed and thrown away.
Offline, so no CDN: a small tokeniser for the languages actually taught, or a
vendored highlighter. Unhighlighted code in a teaching context is a wall.

**D.2 — A `code` AID KIND, WITH STAGING.** This is the important one. It makes
code a teaching object rather than a paste:
- `stage` on a line or span, so the tutor can blank the line the student must
  supply — the exact analogue of labelling a side "?" in a geometry figure
- line numbers and a `highlight` list, so "look at line 4" is a real reference
- a `caption` naming the file/function

**D.3 — RUN IT, AND SHOW THE OUTPUT.** A code tutor that cannot say what a
program actually does is guessing alongside the student. `_t_run_python`
exists; it needs a real sandbox before it can be turned on. Container or
nsjail, resource-capped, no network. *Not* `-I`.

**D.4 — THE TRACEBACK AS A TEACHING OBJECT.** A learner's error message is the
richest Socratic material available and is currently plain text. Parse it:
error type, the line, the frame. Then the tutor can ask "which line does it
name, and what was that line assuming?"

**D.5 — A DIFF VIEW.** "What changed when you fixed it?" is a core code
question and needs before/after, not two blocks.

**D.6 — CS BENCHMARK TOPICS THAT EXERCISE ALL OF IT.** The CS topics are
currently Big-O, recursion and zero-indexing. Add one that requires reading a
short program, one that requires predicting output, and one that requires
fixing a bug — and expect `code` as the aid kind for each.
*Gate: CS domain score ≥ 4.0, with visual_policy ≥ 4.0 on the code topics.*

---

## 6. Lane E — make the instrument trustworthy enough to gate on

**E.1 — MEASURE THE NOISE FLOOR. DONE 2026-08-20.**

    mathematics, identical config, frozen snapshot at 808802a
      run 1  2.988
      run 2  2.826
      NOISE FLOOR = 0.162

Run against a COPY of the repo so that edits in the working tree could not
change the instrument between the two runs — the previous three attempts were
each killed by exactly that.

Tighter than feared. The core benchmark swings ±1.4/5 between identical runs;
this is 0.162, because median-of-3 judging across 15 dialogues averages most
of it out. The dimension MEDIANS were identical across both runs — socratic
2 vs 2, adaptation 1 vs 1, visual_policy 1 vs 1, accuracy 5 vs 5 — so the whole
spread comes from sub-dimension means.

**Use `--floor 0.162`.** A delta at or under it is reported as NO CHANGE.

Caveat worth keeping: this is measured on mathematics only. A domain whose
`misconception_handling` is scoreable on fewer dialogues (it returns None when
the student never erred) will be noisier, because fewer samples feed its
median. Measure per-domain before gating a domain on a small delta.

**E.2 — PER-DOMAIN REGRESSION GATES IN CI.** The deterministic half
(`--static-only`) already runs with no model and belongs in CI now. The judged
half is a nightly.

**E.3 — SPLIT SCIENCE IF ITS DIMENSION IS TOO COARSE.**
`mechanism_over_recall` covers biology, chemistry and physics. If the variance
within science exceeds the variance between domains, it is measuring three
things and reporting one.

---

## 7. Order

1. **E.1**, the noise floor. Nothing is provable before it and it costs two
   runs per domain.
2. **Lane A**, conversation fidelity. It is `socratic` 2.00 in all seven
   domains — one fix, seven domains' worth of movement.
3. **B.1**, the aid nudge. Smallest change with a measured gate in this
   document; the policy work is already done.
4. **Lane D**, code teaching. Largest new capability, and CS is the
   highest-volume expected domain.
5. **C.1**, honest telling. Cheap, but sequence it after Lane A so a rise in
   telling cannot be confused with a fall in asking.
6. **A4.1/A4.2**, the two new domains, once the instrument is stable — adding
   domains before then just adds numbers nobody can compare.

## 8. What would make this sprint a failure

Raising the scores by tuning the instrument. Every weight, rubric and topic in
`bench_domains.py` is fingerprinted, and `--compare` refuses to diff across a
changed fingerprint precisely so that this is visible rather than convenient.
Three declining numbers during the maths loop (3.719 → 3.452 → 3.361) were all
the instrument getting more honest, and none of them was a regression. The
reverse — a rising number from a loosened rubric — must be just as visible.

---

## A.5 + A.3 — the two rules the word cap could not express

**PRE-REGISTERED PREDICTION, written 2026-08-21 BEFORE the run.** Recorded in
advance because this document already contains one result I had to retract for
reading a noisy delta as a win; a prediction written afterwards is not a
prediction.

### What changed

**A.5 `mostly_question`.** The contract enforced <=60 words and
ends-with-a-question, compliance reached ~100%, and `socratic` did not move.
Measuring real transcripts showed why: the mean tutor turn carries **2.53
declarative sentences** before its question and **45% carry three or more**
(history run, n=60). A four-sentence explanation with a question stapled on
satisfied every rule we had, sits comfortably under the word cap, and is
verbatim what the judge calls a "mini-lecture". The rubric scores `socratic` on
"long explanatory MONOLOGUES", not on length.

Supported externally: TeachLM's headline result is increasing STUDENT TALK
TIME, and MathDial penalises the "telling" move. Both are about who is doing
the talking, which the word cap does not measure.

**A.3 `no_repeat`.** The most frequent judge complaint in every domain, in its
own words: "repeats the exact same dictionary analogy and question verbatim".
MathTutorBench reports the same from the other side — tutoring degrades in
longer dialogues "where simpler questioning strategies begin to fail".
Deterministic: content-word Jaccard on whole words, comparing the whole turn
and the final question separately.

### The prediction

| dimension | direction | why |
|---|---|---|
| `socratic` | **UP**, and this is the gate | both rules target lecturing and looping, the two things the judge names |
| `progression` | up | a turn that cannot repeat has to move |
| `adaptation` | up slightly | second-order; A.2 is the real lever and is not in the bench |
| `accuracy` | **MUST NOT FALL** | this is the failure mode: a tutor forbidden to explain may cut something true |
| `honest_telling` | flat | C.1 already moved it; nothing here touches it |

**Gate: `socratic` rises by more than its floor AND `accuracy` does not fall by
more than its floor (0.87).**

### The failure mode to watch for

Forcing brevity could produce a tutor that asks without teaching — terse,
unhelpful, and worse. `mostly_question` fires on ~45% of turns, so this is a
large intervention, not a nudge. If `socratic` rises while `accuracy` or
`progression` falls, the right response is to raise MAX_STATEMENTS to 3 rather
than to keep the gain.

### Cost

~45% of turns now trigger one extra generation. At ~4.5s per call this makes a
run roughly 1.4x longer. Acceptable; noted so a slower run is not mistaken for
a stall.

### Why the fingerprint does NOT change

The contract runs in the PRODUCT (`fsm_logic._enforce_dialogue_contract`) as
well as the bench, and the bench applies it precisely because production does.
Changing the rules is therefore a change to the thing being measured, not to
the instrument — which is exactly what a before/after comparison is for.
`enforce_contract: True` stays in the fingerprint because turning enforcement
on or off WOULD be an instrument change. That distinction is deliberate.

**RESULT (2026-08-21): GATE MET, on a partially-broken run, with a smaller
effect than the headline.**

### The headline, and why it cannot be taken at face value

Mathematics 3.209 -> 3.465 (+0.256, inside the 0.533 composite floor), with
`socratic` 1.87 -> 2.80 (+0.93) and `adaptation` 1.53 -> 2.20 (+0.67).

**But five of fifteen dialogues produced no judged score.** Numbers 10-14 failed
consecutively with an empty first tutor turn, and number 15 recovered — the
signature of a transient model eviction mid-run, not a product defect. Verified
by generating the same first turn live afterwards: 611 characters, no problem.

That matters because **four of the five lost dialogues were the ARBITRARY
topic**, where `socratic` scores low by design. Dropping them raises the mean on
its own. `honest_telling` fell 2.20 -> 1.00 for the same reason and is not a
regression: **n=1**.

### Like-for-like, over the 10 dialogues scored in BOTH runs

| dimension | headline | like-for-like | verdict |
|---|---|---|---|
| `socratic` | +0.93 | **+0.70** | REAL (floor 0.00, resolution 0.25) |
| `adaptation` | +0.67 | **+0.40** | REAL (floor 0.13) |
| `progression` | +0.53 | **+0.10** | **artifact** — below resolution |
| `accuracy` | −0.03 | — | did not fall, as the gate required |

**Gate: `socratic` rises by more than its floor AND `accuracy` does not fall.
MET.** This is the first genuine movement of `socratic` in the entire effort —
four previous interventions moved it by zero.

`progression`'s apparent gain was entirely survivorship. Recorded as such.

### The methodological lesson, which is the most valuable part

Hours before this run I measured what distinguishes high-`socratic` dialogues
from low ones in saved transcripts, and found that declarative-statement count
differs by only **0.30** between them — and wrote, on the record, that I
therefore expected this prediction to fail.

It did not fail. **Enforcing a feature that barely discriminates in
observational data still moved the outcome by 0.70.**

That is the correlation-versus-intervention distinction, and it is worth
keeping: the observational analysis asked "do high-scoring dialogues happen to
have fewer statements?" (barely), while the intervention asked "does forcing
fewer statements change the score?" (yes). A feature can fail to separate two
populations and still be causal when manipulated — because in the observational
data nothing was varying it independently.

**Do not use the surface-feature analysis to rule interventions out.** It was
right that no surface feature *predicts* the score; it was wrong as a guide to
what *changes* it.

### What this run does not establish

- One run, n=10 on the moved dimensions. It needs a repeat before it is a
  finding rather than a result.
- The composite is still inside its floor.
- `honest_telling` at n=1 is unmeasured, not regressed. C.1's effect on this
  domain remains as previously measured.
- The eviction that broke five dialogues is an operational fault to fix before
  the next run, not something to average over.

---

## Five-domain results, 2026-08-21 (B.1 + C.1 + C.1b + B.2)

Measured against each domain's 2026-08-20 baseline, same rubric fingerprint
`c98fa5eb86455db5`, composite floor 0.533.

| domain | composite | honest_telling | visual_policy | socratic |
|---|---|---|---|---|
| mathematics | 2.63 → 3.21 | 1.80 → 2.20 | 2.60 → 3.27 | 2.10 → 1.87 |
| computer science | 3.34 → 3.26 | **1.40 → 3.00** | **2.60 → 3.80** | 2.60 → 2.47 |
| science | 3.00 → 3.05 | **3.00 → 4.20** | 2.33 → 2.87 | 2.00 → 1.67 |
| history | 2.58 → 2.77 | 1.40 → 2.20 | **2.33 → 3.27** | 2.07 → 2.27 |
| law | 2.54 → 2.79 | **1.00 → 2.60** | 2.33 → 2.93 | **2.07 → 2.40** |

### What replicated

**`visual_policy` (B.1) rose in all five**, +0.53 to +1.20, every one REAL
against its floor. **`honest_telling` (C.1) rose in all five**, +0.40 to +1.60.
`misconception_handling` rose sharply where it was weakest — science 3.25 →
4.50, history 2.67 → 4.43.

Two interventions, five independent domains, no exceptions. This is the part of
today's work that is established rather than suggested.

### What C.1b appears to have fixed

C.1 shipped without scoping "state the fact plainly" to the FIRST turn, so the
tutor restated the convention every turn — telling correctly, forever. C.1b
landed mid-sweep, which accidentally produced a natural experiment:

| | socratic | adaptation | ran with C.1b? |
|---|---|---|---|
| computer science | −0.13 | **−0.73** | no |
| science | **−0.33** | +0.07 | no |
| history | +0.20 | +0.13 | **yes** |
| law | **+0.33 REAL** | **+0.33 REAL** | **yes** |

Both pre-C.1b domains regressed on these two dimensions; both post-C.1b domains
improved. Consistent, and mechanistically explained by the judge's own words
("repeats the definition of zero-based indexing verbatim"). **But it is two
domains against two, confounded with the domains themselves** — history and law
are not computer science and science. Suggestive, not established. The clean
test is re-running science with C.1b.

### What has NOT moved: the composite

Every composite is inside the 0.533 floor. The arithmetic explains why:
`right_move` carries weight 0.25, the largest, and is built from `socratic` on
the two derivable topics and `honest_telling` on the one arbitrary topic. C.1
fixed the arbitrary third. **`socratic` gates the other two-thirds, and it is
the composite bottleneck.** No amount of further work on visuals or on telling
will move a composite while `socratic` sits at ~2.

That is what A.5 and A.3 target, and why the prediction above is written the way
it is.

---

## The negative result that redirects the whole lane

**Written 2026-08-21 while the A.5/A.3 run was still in flight, so it cannot be
a post-hoc rationalisation of that result.**

Question: what distinguishes a dialogue the judge scores `socratic` >= 4 from
one it scores <= 2? Measured across all five domain runs, 11 high vs 58 low.

| feature | high (>=4) | low (<=2) | gap |
|---|---|---|---|
| statements per turn | 2.32 | 2.62 | −0.30 |
| tutor words per turn | 40.3 | 43.8 | −3.4 |
| ends with a question | 0.98 | 1.00 | none |
| repeats an earlier turn | 0.00 | 0.02 | negligible |
| open-stem questions (why/how/what-if) | 0.50 | 0.61 | **wrong direction** |
| closed-stem questions | 0.05 | 0.11 | −0.06 |
| question length (words) | 17.5 | 17.1 | none |
| student words per turn | 49.5 | 43.0 | +6.5 |

**Not one surface feature of a tutor turn predicts the score.**

The apparent student-talk-time signal is an artifact. Student verbosity is set
by the PROFILE, not the tutor: `silent_struggler` averages 8.3 words and
`confident_bluffer` 63.2, a 55-word spread that swamps the 6.5-word gap. And
`fast_learner` is verbose while scoring high only 8% of the time.

The open-question hypothesis — drawn from the tutoring literature and entirely
plausible — was **falsified**: high-scoring dialogues ask FEWER open questions.
It was tested against saved transcripts before any code was written, which cost
nothing and saved building a rule that would not have worked.

### Why this is a finding rather than a dead end

`socratic` has a measured floor of **0.00 across three identical runs** — the
most reproducible dimension in the instrument. So it is not arbitrary. It is
measuring something real, consistently, that no surface feature captures.

That leaves one explanation: **it is semantic.** The judge is assessing whether
the question follows from THIS student's specific reasoning — the
"generic questioning" failure the tutoring literature describes. A turn can be
short, end in a question, avoid repetition, and use an open stem, and still be
a generic question that ignores what the student just worked out.

**Which is precisely what A.2 (structured turn state) targets, and A.2 is
invisible to this benchmark** because `run_dialogue` never passes `turn_state`
— one of the ten production inputs the bench omits (see HELGABENCH.md).

### Consequence for the pre-registered A.5/A.3 prediction

I predicted `socratic` would rise. On this evidence it probably will not: the
features those rules enforce differ by 0.30 and 0.02 between high- and
low-scoring dialogues. Recorded here BEFORE the result, because a prediction
revised after seeing the outcome is not a prediction.

A.5 and A.3 remain correct changes — a tutor should not lecture or loop, the
judge complains about both in prose, and both are cheap. But they are unlikely
to be the lever on this number.

### The actual next step

**Make the benchmark pass `turn_state`, which requires the bench to grade
student answers**, then measure A.2. Until that happens the benchmark cannot
see the one intervention aimed at the thing it is measuring, and further
surface-level rules are shots in the dark.

This also bumps a decision made earlier today: supplying `turn_state` changes
the set of production inputs the bench provides, which IS an instrument change,
so it must bump the rubric fingerprint and invalidate the existing baselines.
That cost is now clearly worth paying.

---

## A.6 — deterministic teaching move: MEASURED, AND IT MADE THINGS WORSE

**Result 2026-08-21, mathematics, same instrument and fingerprint as the A.2
baseline (`a21992105fe9aad7`), n=15 both sides.**

| dimension | A.2 baseline | A.6 | verdict |
|---|---|---|---|
| **adaptation** | 2.07 | **1.53** | **REAL regression** (floor 0.13) |
| **socratic** | 2.07 | **1.80** | **REAL regression** (floor 0.00) |
| visual_policy | 3.93 | 3.40 | REAL regression (floor 0.27) |
| accuracy | 4.27 | 4.60 | noise |
| composite | 3.229 | 3.117 | inside the 0.533 floor |

A.6 was aimed squarely at `adaptation` and moved it **down by half a point**.

### Why, and it is worth keeping

**I imposed a script to fix scriptedness.** A.6 names the move on every turn.
In this run only **2 of 5 profiles produced distinct moves** — the selector
keyed almost entirely on miss count and four of five profiles miss twice, so
`WORKED_EXAMPLE` dominated. Every turn therefore looked alike, which is exactly
what the `adaptation` rubric punishes: "rather than following a script".

`socratic` fell for a second, compounding reason: `WORKED_EXAMPLE` instructs
"stop asking, work through a parallel example". That is lecturing, and the
rubric scores `socratic` on questioning rather than lecturing. The intervention
told the tutor to lecture and was then marked down for lecturing.

### The precedent that did not transfer

B.1 moved the *diagram* decision out of the model into a deterministic policy
and `visual_policy` rose in all five domains. I reasoned from that to A.6, and
the ES-LLM literature (100% constraint adherence from separating pedagogical
decision-making from generation) pointed the same way.

The difference, in hindsight: a diagram decision is **binary and low-frequency**
— draw or do not, at most three times a concept. A teaching move is
**categorical and every-turn**. Constraining a rare binary choice removes a
failure; constraining every turn to one of six labels removes variation, and
variation is what `adaptation` measures. **A pattern that works for a
occasional gate does not automatically work for a per-turn choice.**

### What happens next

The behaviour coupling (A.7) was written after this run started and is the
direct fix: a bluffer now gets `CORRECT` rather than a worked example, a
learner who has given up gets the example immediately rather than after two
misses, and distinct moves went 2/5 to 3/5 on the same fixtures.

**But A.7 must now climb out of a 0.53 hole before it shows any gain.** If the
combined run does not clear the A.2 baseline, the correct action is to revert
A.6 entirely rather than keep tuning it — the evidence would then be that
per-turn move dictation is the wrong shape, not that this dictation was tuned
wrong.

---

## The ceiling: prompt-level intervention cannot reach the adaptation gate

**Measured 2026-08-21, after eight interventions in one day.**

### The decisive diagnostic

Comparing how much `adaptation` moves when the TOPIC changes against how much
it moves when the TUTOR changes, across four mathematics runs (B1, A5, A2, A6):

| dimension | spread across topics | spread across interventions |
|---|---|---|
| socratic | 0.83 | **1.00** |
| **adaptation** | **1.25** | **0.67** |

**For `adaptation`, which topic is being taught moves the score nearly twice as
much as anything done to the tutor.** Eigenvalues scores 2.50, partial
derivatives 1.53, the arbitrary convention topic 1.25 — a 1.25 spread that no
intervention has come close to.

For `socratic` the two are comparable, which is consistent with A.5 having
genuinely moved it (+0.70 like-for-like).

### It is not an artifact of scoring adaptation where it does not apply

A fair objection: there is less to adapt to when stating an arbitrary
convention, so including that topic drags the mean down. Excluding it:

| | all topics | derivable only |
|---|---|---|
| adaptation, A.2 baseline | 2.07 | **2.30** |
| adaptation, A.5 | 2.20 | **2.33** |

+0.23. It does not rescue the number, and the gate is **3.5**.

### The turn-level features that do NOT predict the score

Seven tested across 214 dialogues. Six point the wrong way or are flat:

| feature | direction |
|---|---|
| statements per turn, words, ends-with-question, repetition | negligible |
| open-question stems | **wrong direction** |
| student talk time | confounded by profile (8.3 to 63.2 words BY PROFILE) |
| attributing reasoning to the learner | **wrong direction** |
| opening with bare praise | **wrong direction** — high scorers do it MORE |
| naming what the learner missed | **wrong direction** (3% high vs 6% low) |

The last two were each read off two hand-picked dialogues, believed, and then
falsified by the full set. **A.8 was built on the sixth one before it was
tested.** That mistake was made twice in one afternoon, the second time while
explicitly claiming to be working empirically.

### What this means

Eight interventions. Best `adaptation` gain measured: **+0.40**. Current best:
**2.30**. Gate: **3.5**. Abandon trigger: **3.0**.

**Prompt-level intervention has a ceiling here and we are at it.** The
remaining levers are of a different kind:

1. **Fine-tuning on real tutoring dialogue.** This is what the literature
   actually reports working — TeachLM trained on ~100,000 hours of one-to-one
   tutoring, MathDial fine-tuning improving faithfulness and reducing premature
   solution disclosure. Prompting was never how those results were obtained.
2. **A stronger base model.** `socratic` and `adaptation` may simply exceed what
   a 35B MoE at 4-bit can do while also holding the content.
3. **Accepting the dimension measures something else.** It is the most
   reproducible dimension in the instrument (floor 0.00 across three identical
   runs), so it measures SOMETHING consistently — but nothing about an
   individual turn predicts it, and topic predicts it better than behaviour.

### The honest recommendation

Do not build a ninth prompt-level intervention. The business assessment's own
threshold applies: *"failure to fix tutoring quality would justify pivoting to
a B2B licensing/white-label play"* — and its abandon trigger is `adaptation`
below 3.0 after focused effort. **A full day of focused effort, eight
interventions, three of them measured improvements elsewhere, has moved
`adaptation` from 1.53 to 2.30.**

That is the number the hiring and spending decisions should be made against.

---

## A.7 measured, A.6 reverted: the gain was real and it cost accuracy

**Result 2026-08-21, mathematics. A.6 + A.7 against the A.6 run, same
fingerprint. IMPORTANT: n=9, not 15.**

Six dialogues produced no score — ALL FIVE arbitrary-topic dialogues plus one
other — so the headline `adaptation 1.53 -> 2.56 (+1.02)` is inflated by
survivorship: the lost dialogues are the ones that score lowest.

Like-for-like, over the nine dialogues scored in both runs:

| dimension | A.2 baseline | A.6 | **A.7** | A.7 vs A.2 |
|---|---|---|---|---|
| adaptation | 2.22 | 1.89 | **2.56** | **+0.33** |
| socratic | 2.00 | 2.00 | **2.67** | **+0.67** |
| **accuracy** | 4.67 | 4.78 | **3.78** | **−0.89** |

### The decision, and why the pre-committed rule was not enough

The rule written before the run was: keep A.6 if `adaptation` >= 2.07. It is
(2.56), so the rule says keep.

**The rule is overridden, because it did not anticipate a blocking-tier
regression.** `docs/RELEASE_CRITERIA.md` Tier 1 sets `accuracy` >= 4.0 as
BLOCKING — "teaching something false is worse than teaching nothing". 3.78
fails it. A configuration that buys +0.33 on adaptation by giving up 0.89 on
accuracy is not shippable at any adaptation score.

Writing the rule in advance was still right: it stopped the adaptation gain
being talked up on its own. What it could not do was foresee which OTHER
dimension would pay for it, which is an argument for gating on the blocking
tier explicitly in future pre-registrations.

**A.6 is reverted** (`teaching_move=None` in both callers). A.7 stays: the
learner's behaviour still reaches the prompt as a state line, it just no longer
dictates a move label every turn.

### The systematic failure this exposed

**All five arbitrary-topic dialogues failed with an empty first turn**, and the
A.5 run lost four of the same five. I attributed that one to a transient model
eviction. Twice, on the same topic, is not transient.

Reproducing it directly: generation on the arbitrary-topic prompt **hangs past
120 seconds** rather than returning empty quickly. So the client times out and
the empty string is recorded as an empty turn. The arbitrary topic carries
C.1's rule-suspension, A.6's TELL move, the exemplars and the misconception
list simultaneously — the heaviest prompt in the benchmark.

**Consequence: one third of every mathematics run has been silently discarded**,
and `honest_telling` has been unmeasurable on this domain for three runs. Any
mathematics figure quoted from those runs describes derivable topics only.
