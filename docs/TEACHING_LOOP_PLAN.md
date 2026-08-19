# The Socratic teaching loop — the plan, from research

_Research delivered 2026-08-19 in answer to `RESEARCH_BRIEF_TEACHING_LOOP.md`._

---

## The finding that reframes the phase

**Q4 (context drift) and Q5 (prompt hijacking) are one problem with one answer:
the model is not a reliable custodian of session facts.**

Both are solved by moving authority *out of the model's context* and into
deterministic session state. When a learner says *"you already marked this
correct"*, the answer is a fact in a ledger, not a negotiation the model can
lose. This is the same shape as the hydration phase's finding — two questions,
one answer — and it is nearly free, because the state object is needed for
restart continuity anyway.

---

## Two findings that overturn what we assumed

### 1. No offline content classifier is viable. Safety must be prompt-level.

The brief asked for the honest answer on over-blocking. It is: **do not deploy a
content classifier as the safety layer.**

* Of the candidates, **only Llama Guard 3-1B-INT4 (440 MB) fits** the ~1.5 GB
  budget. ShieldGemma-2B (~1.7 GB) and Granite Guardian 3.x-2B (~1.55 GB)
  exceed it on weights alone.
* And the one that fits **over-blocks**: on 1,000 benign clinical questions it
  falsely blocked **7.4%**. Prompt Guard and ProtectAI fit easily but are
  *injection* detectors, not topic classifiers — ProtectAI-v2 showed a **42.5%
  false-positive rate** on benign prompts containing words like "ignore" or
  "explosive".
* **At 7% FPR a student asking 20 legitimate questions in a WWII unit hits a
  wrongful refusal almost every session** — invisible in aggregate safety
  metrics, extremely visible to the learner.

So the control is **subject × age-band × intent, handled deterministically**:

| layer | mechanism | source of truth |
|---|---|---|
| **Subject licenses the topic** | the course's system prompt explicitly authorises atrocity as history, anatomy as biology, dangerous reactions as chemistry | course structure |
| **Age band gates depth** | mapped from the enrolled learner's age | **enrolment metadata we already hold** |
| **Intent** | a bounded judgement — "is this inside the authorised scope for this learner", not "is this text dangerous" | the model, narrowly |

The decisive insight: **the signal distinguishing "explain the nerve-agent
mechanism because we're studying WWI chemistry" from misuse is not in the text
— it is in the enrolment.** A classifier reading the lesson structurally cannot
see it; we have parents, consent and enrolment records and can gate on it
deterministically.

### 2. The full transcript is actively harmful. Keep state, not history.

* **"LLMs Get Lost in Multi-Turn Conversation"** (ICLR 2026, 200,000+ simulated
  conversations): performance drops **39% on average**, decomposed into a minor
  −15% aptitude loss and a **+112% increase in unreliability** — and "when LLMs
  take a wrong turn, they get lost and do not recover."
* **Lost-in-the-middle** (TACL 2024): retrieval accuracy is U-shaped, so a long
  transcript buries early pedagogical context exactly where the model attends
  least.

This inverts the scheduling brief's worry that transcript history was lost on
restart. **The transcript was never the thing worth keeping.** Continuity is
reconstructed from state, and the state is what we persist.

---

## Findings that change existing decisions

### Grading is broken in a way our instruments cannot see

The ±1.4/5 swing we measured is **not an anomaly — it is normal small-judge
behaviour**. Expect Krippendorff's α in the **0.4–0.6** range untreated (Qwen-3
reached 0.563 on MT-Bench against an accepted-good threshold of 0.80), and our
grader is the same family, quantised harder at IQ3_S.

Four fixes, in order of cost:

1. **Keep five levels, add named anchors.** A 5-point scale measured *best* —
   highest exact agreement (40%), highest bucketed agreement (70%), lowest
   normalised variance — degrading monotonically at 6+ points. The scale is not
   the problem; the missing anchors are.
2. **Grade against the stored grade-3 threshold**, which the teaching object
   already holds. Committing the criterion *before* seeing the answer makes the
   judgement a comparison against a fixed target rather than a fresh holistic
   impression. **The cheapest reliability gain available.**
3. **Split the three downstream uses.** Mode selection tolerates ±1 noise; FSRS
   integrates over many reviews; **Bloom promotion does not** — a spurious
   two-in-a-row ≥3 promotes a learner past their level. Add hysteresis, and
   spend a confirmatory re-grade *only* at the promotion boundary. That is the
   one place a second ~18–30 s call is worth it.
4. **Return a misconception id, not just a score.** The belief/correction pairs
   are already stored and unused. A classification against a fixed small set is
   **more stable than the number** and drives better feedback.

**The trap:** a grader that always returns 3 shows high test-retest agreement
and looks stable while carrying zero information. **Measure score entropy
alongside stability.** A stable, low-entropy grader is broken, not good.

### Spotlighting is one layer, not the defence

Static-attack numbers are excellent — injection success from >50% to **<2%**.
Adaptive numbers are not: under search-based attack **ASR is above 95%**, and
human red-teamers produced 265 successful attacks against spotlighting.

Grading is the harder case, and it is measured: educational-grading injection
reaches **ASR 0.73–0.82**, with ~20-point grade inflation — and **models that
resisted "almost never said so"**, so the grader cannot self-report.

The state ledger, not spotlighting, is what actually holds.

### When to draw: a rule, not a model call

Concept-type → aid-kind affinity, overridden by question-type, triggered on the
**second consecutive miss** (coinciding with the existing "change the
explanation" rule, making the diagram *be* the changed explanation). Cap at
**one aid per three turns, never two in a row.**

Do not enumerate twelve kinds in the prompt. The engine picks the kind; the
model fills one schema. That also sidesteps the `minItems` problem entirely.

**Honest caveat: the density evidence does not exist.** One-per-three-turns is a
reasoned default, not an evidenced constant. Instrument it.

### Prefix-cache governs context assembly

At 247 tok/s, a 20k context costs ~80 s cold and ~0 s cached — but **any change
to the prefix invalidates everything after it.** So retrieval that rewrites the
prefix per turn is unaffordable; retrieval that appends to the suffix is cheap.

Turn layout, stable → volatile: system prompt + role · concept material
assembled once at session start · running-state summary · last 2–3 turns
verbatim.

---

## Staged implementation

### Stage 1 — highest leverage, lowest cost

1. Named rubric anchors; grade against the stored grade-3 threshold.
2. **The running-state object and graded-ledger.** Persist it; stop persisting
   the transcript. One artifact fixes drift detection, hijack resistance and
   restart continuity at once.
3. Role assertion in the cached prefix + a one-line suffix reassert.
4. Deterministic injection pre-check; keep spotlighting as layer one.

### Stage 2

5. Split the grade's three uses with hysteresis; confirmatory re-grade only at
   the Bloom-promotion boundary.
6. Misconception id from the stored belief/correction pairs.
7. The aid-decision rule; constrain generation to the single selected kind.
8. Deterministic drift signals with per-session baselines.

### Stage 3 — safety

9. Subject × age-band prompt-level scoping from enrolment metadata. **No content
   classifier.**
10. Crisis path: safe-messaging response + crisis resource + guardian
    notification, with a two-tier visibility model published to both learner and
    parent.

---

## Crisis handling — the ceiling, stated plainly

**An offline system with no escalation path must not attempt risk assessment.**
Suicide risk assessment is a task for trained professionals with validated
instruments; an offline LLM has none of that, and attempting to triage is both
clinically inappropriate and dangerous.

What it *must* do: respond with brief, non-judgmental, safe-messaging-compliant
language; surface the crisis line; and **route to a human through the channel it
does have — the guardian record.** The system cannot call 988, but it is not
without an escalation path.

What it must **never** attempt: risk scoring, safety planning, no-suicide
contracts, method discussion, or talking a learner out of a denial.

**Two-tier visibility**, published up front to learner and parent: a private
learning tier (not surfaced by default — this is the trust that makes tutoring
work) and a safety tier that always notifies. Do not silently log everything to
parents; it destroys the relationship and is not required.

---

## What to measure — and what looks like success but is not

| signal | what it really means |
|---|---|
| grader test-retest agreement high | **check entropy** — a grader that always returns 3 is stable and useless |
| safety filter never fires | indistinguishable from broken — feed it known-positives in CI |
| drift detector never fires | same; inject a scripted drift transcript to prove it can trigger |
| spotlighting's <2% ASR | a *static* figure; adaptive is >95%. The ledger holds, not the fence |
| grader reports it resisted manipulation | models that resisted "almost never said so" — the self-report is worthless |
| aggregate safety metrics clean | a 7% wrongful-refusal rate is invisible here and obvious to the learner |

**Thresholds that change the plan:** if test-retest α ≥ 0.75 with high entropy
after Stage 1, grading is fixed; if α stays < 0.5, escalate to multi-sample
voting or reduce the grade's authority over Bloom further. If wrongful refusal
on a held-out academic set exceeds ~1–2%, **loosen the subject authorisation —
do not add a classifier.**

---

## Caveats carried forward

* **Model-family monoculture.** Grader, generator and judge are all qwen.
  Self-consistency measured within the family overstates reliability; audit with
  a different small model or with deterministic checks.
* The **one-aid-per-three-turns** cap is reasoned, not evidenced.
* The **7% over-block figure is clinical, not classroom** text. The true
  academic FPR is unmeasured — which is exactly why the recommendation is to
  measure our own wrongful-refusal rate rather than trust a vendor number.
* **Offline crisis handling is genuinely limited**, and the product should say
  so plainly rather than simulating a counsellor.
