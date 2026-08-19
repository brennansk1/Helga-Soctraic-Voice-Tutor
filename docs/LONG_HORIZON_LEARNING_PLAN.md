# Long-horizon learning — the plan, from research

_Research delivered 2026-08-19 in answer to `RESEARCH_BRIEF_LONG_HORIZON_LEARNING.md`.
This document records the findings and turns them into staged work. **Not yet
implemented** — the skeleton builder is the current focus._

---

## The finding that reframes everything

**The gate and the scheduler were never competing objectives. They are two
phases of one process: _successive relearning_** — retrieval to a mastery
criterion, repeated across spaced sessions.

The brief asked "gate *or* schedule?". The answer is that the gate is sessions
1–3 of a relearning schedule and FSRS is its continuation. Everything below
follows from that.

Rawson & Dunlosky's programme is the evidence base, and the decisive number is
about *spacing*, not volume:

> One-week retention was better when students recalled items correctly **once in
> each of three spaced sessions** than when they recalled each item **three times
> in a single session** — 68% vs 26%.

Long-horizon figures: ~68% lab retention at one month and ~49% at four months
against ~11% for unpracticed controls; Bahrick's word-pair work shows ~60%
survival at **five years** with sessions spaced ~56 days.

---

## Findings that change existing design decisions

### 1. Do not over-grind a concept on day one — "relearning override"

Vaughn, Dunlosky & Rawson (2016): the benefit of a higher initial criterion is
**significantly attenuated after relearning**. A hard-won gate and an easy gate
converge after two or three spaced reviews.

**Consequence for Helga:** the conjunctive mastery gate (streak + Bloom target +
question-type diversity) is doing more work on day one than the evidence
supports. Gate quality should set initial FSRS stability **weakly** — first
interval only, within the Again/Hard vs Good/Easy band — not seed a long
interval from one strong day.

### 2. A pass should be provisional until it survives spacing

Three states, not two:

| state | meaning |
|---|---|
| **Provisional Pass** | gate reached in session |
| **Confirmed Pass** | survived ≥2 spaced successful retrievals; FSRS stability ≥ ~21 days |
| **Lapsed** | a review failed |

Confirmation timing follows Cepeda's temporal ridgeline — optimal gap is ~10–20%
of the target retention interval, falling to ~5% at a year. So: first
confirmation at **1–3 days**, second at **1–2 weeks**, third at **~1 month**;
"Confirmed" at roughly the 2–4 week mark. Three sessions is the sweet spot;
returns diminish beyond three or four.

### 3. A lapse must not un-pass a module

A single lapse drops a *concept* Confirmed → Provisional and injects a short
relearning step. A **module** loses Confirmed status only if **>20–30% of its
prerequisite-tagged concepts** fall below mature stability.

Framing is load-bearing, not cosmetic: *"the review caught this before you lost
it"*, never a demotion. A punitive frame increases quitting in a voluntary
population.

### 4. Desired retention should differ by concept role

Not "as high as possible" — workload rises steeply above 90% (90→95% roughly
doubles reviews; 95→97% doubles again).

| concept role | desired retention |
|---|---|
| prerequisite / load-bearing | **0.85–0.90** |
| leaf | **0.70–0.80** |

Some forgetting is necessary. Deliberately let low-value leaf concepts decay.

### 5. The binding constraint is motivation, not the algorithm

A mature ~5,400-concept pool plausibly generates ~55–160 reviews/day (~15–30 min)
— but uncapped intake routinely produces 300–1,600+/day in real Anki decks, and
that is the dominant cause of abandonment. Self-paced completion baselines are
brutal: median MOOC completion **12.6%**; edX overall **3.13%** in 2017–18.

**So:** a fixed daily **time box** (~20–30 min), and **new-concept intake
throttled by the young-concept backlog**. That single throttle prevents the
review-debt spiral that kills most SRS users.

### 6. Interleave neighbours, space strangers

Rohrer & Taylor (2007): interleaved practice tripled delayed-test scores — 63%
vs 20% at one week (d = 1.34) — while looking *worse* during practice. The
mechanism is learning to **choose** the right approach, so it needs confusable
alternatives co-present.

Cross-course interleaving therefore pays most between **neighbours** (Calc I /
Calc II); across unrelated courses it is just spacing. Firth (2021) cautions that
interleaving whole *topics* is not yet evidence-based — studied units are small.

**Session mix once the pool matures:** ~⅔–¾ new Socratic work, ~¼–⅓ resurfaced
review, drawn preferentially from the current course's prerequisite chain.

### 7. Prerequisite decay — never hard-block

| prerequisite retrievability | action |
|---|---|
| ≥ 0.85 | proceed |
| 0.6–0.85 | **remediate just-in-time** — 1–2 retrieval reps before the new concept |
| < 0.6 | **teach through with scaffolding** — compressed relearning inside the new concept |

Blocking a self-paced volunteer at a prerequisite wall is a quitting trigger.
Medical education is the closest analogue to a four-year horizon: little loss for
~1.5–2 years then a logarithmic decline, with >50% of first-year knowledge
unreproducible after 8–10 months in some studies. Decay is real; spiral revisit
is the remedy.

### 8. Assessment validity, ranked by power

1. **Delayed retention** — least gameable; FSRS already provides the instrument
2. **Source-derived items** — generate from the textbook section, not from
   Helga's own prose. Attacks criterion contamination at the root
3. **Transfer questions** — novel scenario/edge-case, used as *held-out
   assessment* rather than teaching moves
4. **Held-out material** — a reserved fraction of each concept's question space

### 9. Motivation — XP and streaks are the risk, not the reward

Self-determination theory: extrinsic rewards can crowd out intrinsic motivation
for an *already voluntary* activity, and Helga's learner is voluntary by
definition. Streaks are **controlling** ("don't break the chain") and convert a
chosen activity into an obligation; when the streak breaks — inevitable over four
years — abandonment often follows.

* **Keep:** progress framed as competence (*"your Calc I is holding at 88%
  retrievability"*), mastery milestones, autonomy over path and pace.
* **Demote or make optional:** streaks as loss-framed obligations, XP as the
  primary loop, anything that punishes a missed day.

### 10. Calibrate expectations: ~0.5–1.0σ, not 2σ

Bloom's 2-sigma comes from two small dissertations and an illustrative
hand-drawn figure; it has never replicated at that magnitude. VanLehn (2011):
human tutoring **d = 0.79**, intelligent tutoring systems **d = 0.76** — ITS is
nearly as effective as human tutoring, and both are well below 2σ. Mastery
learning alone replicates at ~0.52 SD.

**Benchmarking Helga against 2σ would mis-judge a working system as failing.**

---

## Staged implementation

Ordered so each stage is independently useful.

### Stage 1 — Couple the gate to the scheduler
* Three-state model: Provisional → Confirmed (stability ≥ ~21 d over ≥2 spaced
  successes) → Lapsed
* Seed initial FSRS stability from gate quality, compressed to the
  Again/Hard vs Good/Easy band; first interval 1–3 days
* Demote a module only when >20–30% of its prerequisite concepts fall below
  mature stability
* **Change course if:** Confirmed-Pass rates collapse — that indicts the
  teaching, not the gate

### Stage 2 — Differentiate retention, cap load
* Tag concepts prerequisite/load-bearing vs leaf; DR 0.85–0.90 vs 0.70–0.80
* Fixed daily review **time box** (~20–30 min); on overflow prioritise due
  prerequisites of the current course, then oldest-overdue
* **Throttle new intake by young-concept backlog** — the single most important
  rule in this document for retention of the *learner*
* **Change course if:** due count exceeds the time box for >1–2 weeks

### Stage 3 — Prerequisite-aware sequencing
* Build the concept prerequisite graph (Knowledge Space Theory / ALEKS-style
  outer fringe)
* Apply the block/remediate/teach-through rule; never hard-block
* Resurface prior-course concepts from the current course's prerequisite chain

### Stage 4 — Rebuild assessment for validity
* Items generated from source text; held-out items never used in teaching
* Module pass requires *delayed* demonstration
* Explicit transfer items as held-out assessment

### Stage 5 — Motivation cleanup
* Demote XP/streaks from the core loop; replace with competence/retention
  dashboards and autonomy over path
* A/B streaks present/absent against 90-day continuation

---

## What to measure — and what looks like success but is not

**Working:**
* **Confirmed-Pass survival curve** — fraction still at mature stability at 1, 3,
  6, 12 months. The core long-horizon KPI
* True retention vs desired retention — divergence means parameters or content
  are off
* Delayed-transfer accuracy on held-out source-derived items
* Prerequisite-intact rate — dependents entered with prerequisite ≥ 0.85
* 30/90/180-day continuation; queue-size trend (a rising queue predicts quitting)

**Looks like working, is not** — several invisible from inside:

| signal | what it really means |
|---|---|
| gate pass-rate high, delayed retention low | **grading its own homework** — the flagship invisible failure |
| true retention ~100% | intervals too short; workload inflated and the spacing benefit destroyed |
| XP/streak engagement up, Confirmed-Pass flat | gamification driving activity, not learning |
| queue cleared daily with zero lapses | learner may be pattern-matching fixed phrasings — rotate surface forms, test transfer |
| high in-session grades, poor interleaved performance | fluency illusion; only delayed interleaved tests reveal it |
| metrics averaged over active learners | **survivorship** — strugglers quit and the average flatters. Denominate against the original cohort |

---

## Caveats carried forward

* Effect sizes come largely from vocabulary and maths problems in lab or short
  classroom studies. Helga's Socratic, conceptual, adult-solo context matches
  none of them exactly — treat every parameter as a starting point to tune
  against Helga's own instrumentation.
* The ~55–160 reviews/day figure is **extrapolated, not measured**. Validate
  before promising anything in the UI.
* Retention-based passing has a motivation cost. The framing is load-bearing.
* Transfer is fragile — test for it, never assume Socratic gating produces it.
* **No cohort means relatedness is structurally unmet.** SDT predicts a permanent
  headwind for a solo learner that none of the above fully solves, and it likely
  caps achievable retention below cohorted programmes.
