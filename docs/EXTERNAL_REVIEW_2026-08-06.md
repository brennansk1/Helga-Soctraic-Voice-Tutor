# External review — 2026-08-06

Seven independent reviewers run on a **different model family** (Gemini, via the
Antigravity CLI) against the pipeline at commit `9b36530`. Four audited the
code; three researched the literature with grounded web search.

The point of using another model was not extra throughput. Nine agents had just
overhauled this pipeline, and every one of them inherited the framing of the
engineer who briefed them — so they largely confirmed what they were told to
look for. These seven were given the same context and **explicitly invited to
contradict it**. Two of them did, and both were right.

---

## How to read this document

Every claim below is marked:

| mark | meaning |
|---|---|
| **VERIFIED** | re-checked directly against the code or by execution |
| **REFUTED** | the reviewer's claim was checked and is wrong |
| **UNVERIFIED** | plausible, not yet checked — do not act on it alone |

That distinction is not bureaucratic. Of the claims checked, **two of the most
confident were false**, and two more were regressions introduced by the very
session that was fixing things. A finding's confidence and its correctness were
uncorrelated.

---

## 1. The largest finding: verdicts computed and thrown away

**VERIFIED.**

```
grep -rn 'fact_check|depth_contract' services/web-ui/   ->   0 hits
```

The pipeline runs an LLM fact-check with independent confirmation, a blind
level-calibration judge, a depth contract and a grounding confidence score. All
of it is written into `structure.json`. **The UI reads none of it.**

A course whose fact-check found unresolved false claims is, to a learner,
indistinguishable from one that passed clean.

The reviewer's phrasing is the one worth keeping: *"The expensive work of
evaluating truth and depth is already happening; the UI is deaf to it."*

This is the cheapest high-value change in the system — roughly thirty lines,
with nothing upstream needing to change. It is also the only change on the list
that improves a course **that has already been built**.

### 1b. A related claim of mine that was wrong

**VERIFIED (as an error).** `course_builder.py:3821`:

```python
self._last_injected_sections = list(missing)
```

One occurrence in the entire codebase. A write with no read.

When stub injection was switched off, this was described — by me — as making
structural failures "recorded instead of laundered". It does not. The log line
changed and the padding stopped, which is real, but the durable record does not
exist. A document missing three sections and one missing none are still
indistinguishable downstream.

---

## 2. The depth contract measures syntax, not rigour

**VERIFIED.** `services/core/depth_contract.py:42`:

```python
"formal_definition": re.compile(r"\*\*Definition\*\*|^#+\s*Definition|is defined as|...")
```

It checks for the *word* "Definition". `**Step 1**` around trivial arithmetic
satisfies `worked_example`. `\btherefore\b` validates a "derivation". LaTeX
dollar signs count as formal notation.

This is not a new discovery — `tools/substance_check.py` already documents
marker-stuffed nonsense passing at mastery 5 while genuine graduate prose
fails. What is new is the consequence being observed in real output.

### The consequence, verified

`data/courses/course_2b9df59e/content/con_4c467f98.md:29-32` contains a "worked
example" that passed every structural check and is mathematically incoherent:

> *"if we treat the missing corner as 'empty,' our count drops below the true
> area defined by the full right triangle's hypotenuse squared"*

It is not a partial-squares argument, and its conclusion does not follow from
its steps. It passed because it had `**Step 1**` headings.

### The proposed replacement (UNVERIFIED, promising)

Reframe the model as an **extractor** rather than a judge, and decide in
deterministic code:

- *"List every specialised term used but not defined in this text."*
- *"For each procedure, is it explained WHY it works, or only HOW to use it?"*

Then a rule over the extracted JSON. The insight is that extraction sidesteps
the scoring variance that makes a 1-5 judgement unusable here (measured at
±1.4/5 between identical runs) rather than trying to average it away.

Before adopting it, test against the two known adversarial cases: the
marker-stuffed text that currently passes at level 5, and the genuine prose
that currently fails. The proposed threshold ("no unjustified procedures at
all") looks brittle on real text.

---

## 3. Pedagogy: three defects, all verified in the prompt source

Found independently by an accreditation reviewer and a learning scientist,
neither seeing the other.

### 3a. The tutor abandons scaffolding exactly when it is needed

`services/common/prompts.py:645`:

```
- LECTURE: If student says "I don't know", "unsure", "explain", "help", or admits ignorance.
```

Two bugs in one line.

**Keyword collision.** *"I'm unsure why step 2 works"* is a precise, well-formed
question. It contains a LECTURE keyword, so it is routed to a canned
micro-lecture that ignores what was asked. This is the mechanism behind the
measured "ignoring what the student actually asked" fault.

**Wrong response to being stuck.** The moment a learner realises they do not
know is when they are most primed to construct meaning. Replacing that with an
answer removes the work that produces learning.

The serious counter-argument was put to the learning scientist deliberately:
Kirschner, Sweller & Clark show novices genuinely need direct instruction, not
discovery. Her resolution, which survives it: **an LLM micro-lecture is not
direct instruction — it is an answer dump.** Direct instruction is worked,
sequenced and checked.

### 3b. An instruction that overrides the Socratic rules

`services/common/prompts.py:508`:

```
5. Fill In The Gaps: Use your broad knowledge base to supplement the reference material.
```

Forty lines below the Socratic rules it contradicts. It also silently
undermines grounding: content invented from model knowledge is precisely what
the confidence score exists to flag, and the learner sees no difference.

### 3c. The hint ladder is inverted for novices

`services/common/prompts.py:46`:

```
d. Only as a last resort, give a worked example — then immediately ask a parallel question.
```

Worked-example research and the expertise-reversal effect say the opposite: a
novice needs the example **early**, with guidance fading as competence grows.
The same example that helps a novice hinders an expert. This treats it as a
last resort for exactly the learners who need it most.

### What the reviewers said to protect

Not everything is wrong, and this matters as much as the defects:

- the hint ladder's **structure** (probing → small hint → large hint → example)
- never affirming a wrong answer (`prompts.py:47`)
- eliciting and confronting misconceptions (`prompts.py:407`) — and the fear of
  a familiarity-backfire effect is outdated; it largely fails to replicate
- diagram syntax that hides the answer (`prompts.py:79`)
- grade-band calibration across developmental stages

---

## 4. Regressions introduced by the overhaul itself

Both **UNVERIFIED but credible**, both found by the adversarial correctness
reviewer, and both are flaws in fixes that were reported as complete.

**`storage.py:870` — cache poisoning window.** `_cache_put` reads the file
signature *after* the write completes. If the other process writes in that
window, this process caches its own stale document under the new signature —
and then serves it indefinitely, because the signature matches. Fix: stat the
temp fd before `os.replace`.

**`storage.py:1052` — a degraded path made fatal.** Removing a broad `except`
means `OperationalError: database is locked` can now escape `update_course`,
propagate out of `hydrate`, and kill the pipeline thread — where it previously
logged and continued with a stale index.

**The reaper fix was half a fix.** The timezone comparison is correct on the
SQLite path. But `course_builder.py:1101` writes `created_at` in **local** time
via `time.strftime`, and the JSON fallback path still compares that against a
UTC cutoff — wrong in both directions depending on the sign of the offset.

---

## 5. Claims that were checked and are FALSE

Recorded because knowing what *isn't* broken is worth as much as the reverse.

**"A level-3 College Course does not meet its claim."** The reviewed course is
`mastery: 2`. Finding high-school content in a high-school course is not a
finding. **The level-3 claim remains untested** — no level-3 course exists on
disk. One level-2 course is currently the entire evidence base for a five-level
product.

**"Spaced-repetition decay is silently disabled."** The claim was that
`date.fromisoformat` raises on datetime strings, defaulting elapsed time to
zero. Every writer produces a bare date (`date.today().isoformat()`), which
parses fine. Host Python 3.9 and container Python 3.11 were both checked for a
version divergence — there is none. FSRS is not broken.

---

## 6. Confirmed sound — do not re-spend review time here

The adversarial reviewer was asked to attack four load-bearing claims. All four
survived, verified independently on a different model:

- an individual scaffolding flag overrides the `HELGA_LEAN` group flag in
  **both** directions
- a placeholder stub counts as a failure while genuine-but-ungrounded content
  counts as a success — **a research-service outage cannot trip the abort gate**
- in-process worker mutations are correctly covered by `_course_lock`
- the reaper timestamp fix is correct on the SQLite path

Also cleared: no SQL injection (every dynamic-column path whitelists first);
`_ThreadLocalDB`; `build_state._atomic_write`; the `delete_course` cascade;
book-mode copyright containment in the asset collector.

---

## 7. Literature findings

Sources marked **verified** were confirmed to resolve and to be correctly
titled. Effect sizes were **not** verified and several look mis-attributed —
the reviewers' own delegates flagged them as possibly recalled rather than
retrieved. Act on the mechanisms, not the numbers.

**Format restrictions consume reasoning capacity** — *"Let Me Speak Freely?"*,
[arXiv 2408.02442](https://arxiv.org/abs/2408.02442) (**verified**). Reports
that strict format constraints cost domain-logic accuracy, more so on small
models. This is a plausible mechanism for §2's structurally-perfect,
mathematically-incoherent example. The proposed remedy is cheap: a leading
`scratchpad` field in the schema so the model reasons before it formats.

**Multi-perspective planning** — STORM,
[arXiv 2402.14207](https://arxiv.org/abs/2402.14207) (**verified**): simulate
expert personas interviewing a curriculum agent before freezing the syllabus.
Aimed at coverage. High effort; risks topic drift.

**Chain-of-verification** —
[arXiv 2309.11495](https://arxiv.org/abs/2309.11495) (**verified**): generate
verification questions, answer them *without* the draft in context, then
revise. Three LLM calls where there is currently one — unaffordable while a
build takes ~77 minutes.

**Judge reliability** —
[arXiv 2306.05685](https://arxiv.org/abs/2306.05685) and
[arXiv 2310.07641](https://arxiv.org/abs/2310.07641) (**verified**). Both
support moving from pointwise 1-5 scoring to **pairwise with position
swapping**, which is what `tools/substance_duel.py` already does.

**`unsloth.ai/blog/mtp` returns 404** — the multi-token-prediction throughput
claim is unsupported. Do not plan around it.

---

## 8. The build-time trade-off this created

Measured on `qwen3:14b` under the lean pipeline:

| | pre-overhaul | lean |
|---|---|---|
| per concept | 151s | ~383s |
| 12-concept course | ~30 min | **~77 min** |

Consistent across concepts (384.0s, 381.2s), so structural rather than noise.
The cause is almost certainly grounding: the model now receives up to 20,000
characters where it received 1,500, and prefill scales with input length.

`RESEARCH_REMAINDER_CHARS` is a dial, not a switch. Sweeping it at 4k / 8k /
20k would show whether most of the quality arrives well before 20k. As it
stands, quality was bought at 2.5x wall-clock without knowing the shape of that
curve.

---

## 9. What the reviews did not settle

- **The level-3 claim.** Untestable until a level-3 and a level-5 course exist.
- **Whether the scratchpad fix works.** One paper, no rebuttal literature found
  despite being requested. It must be validated with `tools/model_gate.py`.
- **The effect sizes** in the pedagogy report. Directions are well-established;
  the specific numbers are not confirmed and at least one attribution is
  probably wrong.
- **Comparable open-source systems.** The research returned none — no
  textbook-generation projects, no "hardest problem" write-ups. Either the
  search was thin or the space is emptier than expected.

---

## 10. Method note

The two claims that most needed refuting came from reviewers who were **told**
what to look for and looked anyway — one refuted a bug I had introduced, the
other refuted a claim I had made about my own fix.

The nine in-house agents, briefed by the same engineer, largely confirmed that
engineer's hypotheses. The seven external ones, on a different model, refuted
two of them and found four defects nobody had suspected.

Where a reviewer's finding was checkable, checking it was worth the cost every
time: **two headline findings were false**, and the check took minutes.
