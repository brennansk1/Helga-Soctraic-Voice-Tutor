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
*Gate: hallucination count in a 15-dialogue run, counted from judge
`worst_moment` text, drops from 3 to 0. socratic ≥ 3.0 median.*

**A.2 — A STRUCTURED TURN STATE, NOT A TRANSCRIPT.**
The model is being asked to re-read the dialogue every turn and infer state.
Give it the state explicitly instead: what the student has got right, what
they got wrong and how, what is still open. Built in code from the graded
answers, not inferred by the model.
*Gate: adaptation ≥ 3.0 median across domains (currently 2.11–3.00).*

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
*Gate: visual_policy ≥ 3.0 median across domains.*

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
*Gate: honest_telling ≥ 4.0 median, and derivable `socratic` must not fall —
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

**E.1 — MEASURE THE NOISE FLOOR.** Nothing above can be called an improvement
until this exists. Two identical runs per domain, `--floor` set from the
spread. Every attempt so far has been killed by a code change mid-run.
*Blocks every other gate in this document.*

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
