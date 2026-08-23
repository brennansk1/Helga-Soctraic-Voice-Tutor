# What "good enough to release" means, in numbers

**Written 2026-08-21.** The sprint has been chasing "acceptable for release"
without anyone defining it. A goal stated against an undefined bar cannot be
met, only argued about. This defines the bar, states where the product actually
sits against it, and says plainly what is and is not shippable.

---

## 1. Why the composite is the wrong gate

The obvious gate — "domain composite above X" — does not work, and it is worth
saying why before proposing an alternative.

The composite's **measured noise floor is 0.533** across runs of an identical
configuration. The seven domains span 2.54 to 3.34. So the entire spread
between our best and worst domain is 0.80 — barely more than one and a half
noise floors. A composite gate would be measuring the instrument's variance as
much as the product.

Worse, the composite is a weighted blend that can hide a failure. A domain could
clear a composite bar while teaching false things, because high `visual_policy`
and `notation_speakable` would carry it.

**Gate on dimensions, not on the blend.** A dimension has a smaller floor
(0.00–1.00, mostly ≤0.47) and each one means something specific a learner would
notice.

---

## 2. The bar

Two tiers, because the two modes carry different stakes. Mode A is an adult who
chose this. Mode B is somebody's child.

### Tier 1 — BLOCKING. Below this, do not ship to anyone.

| dimension | bar | why this is blocking |
|---|---|---|
| **accuracy** | **≥ 4.0** | Teaching something false is worse than teaching nothing. This is VanLehn's point about why some tutoring systems fail to beat a textbook. |
| **misconception_handling** | **≥ 4.0** | A tutor that lets a wrong belief stand has actively made things worse than a book, which at least does not affirm the error. |

### Tier 2 — QUALITY. Below this, ship only with the limitation stated plainly.

| dimension | bar | why |
|---|---|---|
| **socratic** | ≥ 3.0 | Below 3 the product does not do the thing its name claims. |
| **honest_telling** | ≥ 3.0 | Below 3 it wastes the learner's turn asking them to guess an arbitrary fact. |
| **visual_policy** | ≥ 3.0 | Below 3 the diagram system is built and not used. |
| **adaptation** | ≥ 3.0 | Below 3 it is following a script. |

### Tier 3 — MODE B ONLY. Additional, non-negotiable, and not yet measurable.

Children are not consenting adults and the bar is higher:

- **accuracy ≥ 4.5** on any standards-aligned content.
- **Zero** safety-filter escapes across the full run.
- Grade-band register verified by transcript review, per band.
- The K-1 delivery path measured at all — it currently is not.

---

## 3. Where the product actually sits, 2026-08-21

| domain | accuracy | misconception | socratic | honest_telling | visual_policy | adaptation |
|---|---|---|---|---|---|---|
| mathematics | 4.30 | 5.00 | 2.80 | 1.00* | 3.53 | 2.20 |
| computer science | 4.53 | 4.33 | 2.47 | 3.00 | 3.80 | 2.07 |
| science | 4.27 | 4.50 | 1.67 | 4.20 | 2.87 | 1.40 |
| history | 4.20 | 4.43 | 2.27 | 2.20 | 3.27 | 2.13 |
| law | 4.00 | 4.17 | 2.40 | 2.60 | 2.93 | 2.20 |
| medicine | **3.80** | **3.50** | 2.20 | 1.80 | 2.33 | 1.73 |
| language & literature | 4.13 | 4.44 | 2.13 | 2.20 | 2.67 | 2.13 |

\* mathematics `honest_telling` is n=1 — unmeasured, not regressed.

> **⚠ These figures are NOT comparable with runs after 2026-08-22.**
> They were taken under instrument fingerprint `c98fa5eb86455db5` /
> `a21992105fe9aad7`, before `bench_domains` supplied `concept_kind`. The bench
> was passing six production inputs while the FSM passed the domain's concept
> kind as well, so every number in this table measures
> **production-minus-the-domain-layer** — a configuration that does not ship.
> That is the same defect `turn_state` had before it was fixed.
>
> Supplying it moved the fingerprint to `4faf5407715a9e4d`, which is the
> mechanism working: holding a new run against this table compares two
> different instruments.
>
> To answer "did the domain layer help?", run both arms under the CURRENT
> instrument — `HELGA_BENCH_NO_DOMAIN=1` withholds the kind and nothing else.
> Until that pair exists, the mathematics row above is **superseded, not
> beaten**.
>
> **Update 2026-08-22, four runs in:** no dimension has moved further than the
> instrument's own run-to-run spread. The floors were re-derived from four runs
> and every one grew — `adaptation` 0.13 → 0.40, composite 0.162 → 0.376 — which
> retracted three "floor-beating" gains claimed after a single high draw.
> `adaptation` reads 2.00 / 2.13 / 2.40 / 2.00 against a 3.5 gate.
>
> **The 3.5 adaptation gate should be re-examined.** Across 60 dialogues, 42%
> score 1 and 15% score 4+; a 3.5 mean needs roughly half at 4+. No domain has
> ever exceeded 2.30 in this repository's recorded history. An ablation run with
> ALL domain guidance removed moved it by −0.07, so it is not a guidance
> problem — while the SAME ablation dropped `misconception_handling` by 2.25,
> three times its floor, which is the only floor-beating result obtained and
> shows the instrument can detect a real effect when one exists.
>
> Reaching 3.5 needs a different model, dialogues longer than four turns, or a
> different number. It is not reachable at the prompt layer.
>
> ## DECISION 2026-08-22: mathematics ships with the gate unmet
>
> **Accepted at ~2.41 against a 3.5 gate, by the owner's decision.** Recorded
> here so nobody later reads the shipped state as a claim that the gate was
> reached — it was not.
>
> What was established across ten runs:
>
> | | |
> |---|---|
> | before / after the one real fix | 2.13 → 2.41 |
> | source of the gain | a plumbing defect, not a teaching idea |
> | five subsequent changes | all measured as noise |
> | best adaptation ever recorded, any domain | 2.30 |
> | judge reliability | high — identical transcripts rescore to 2.00/2.13/2.13 |
>
> The gain came from `turn_state.render()`: "CHANGE YOUR APPROACH" was reaching
> **1 prompt in 60**, keyed to a counter a rewording resets and then truncated
> away by a 600-character cap. Nothing invented afterwards moved the number.
>
> **For whoever picks this up.** The judge is trustworthy per-transcript, so the
> target is real, not an artifact. But run-to-run variance is ~0.40 because every
> run is a different conversation (0.062 text similarity), so **detecting
> anything smaller than 0.4 needs about n=60 per arm — four runs a side, ~2.3
> hours each.** Budget that before attempting an increment, and prefer
> ablations: removing the domain layer dropped `misconception_handling` by 2.25,
> three times its floor, and is the only floor-beating result the work produced.
>
> **CORRECTION 2026-08-22, after testing the judge itself.** The suggestion
> above that the gate be re-examined rested on the scores being unreliable.
> THEY ARE NOT. Rescoring the SAME 15 transcripts three times gives run means
> of 2.00 / 2.13 / 2.13 — a spread of 0.13 — with 10 of 15 dialogues scored
> identically every time and the rest differing by exactly one point.
>
> So the judge is consistent, and the 0.40 run-to-run floor is almost entirely
> GENERATION variance: every run is a different conversation (tutor and student
> turns repeat at 0.062 similarity across runs). The low adaptation scores are a
> real, reproducible judgement about the tutor's behaviour.
>
> Two things follow. **The gate is a genuine target**, not a measurement
> artifact — the case for revising the number is much weaker than stated above.
> And **the methodology should change**: compare configurations by rescoring
> FIXED transcripts, not by re-running generation, which is where the noise is.
>
> Also worth noting: across 75 dialogues, no mechanical feature separates the 34
> scoring 1 from the 11 scoring 4+ — length, variance, question rate, echoing
> the learner's words, handling a stuck learner are all indistinguishable. The
> judge reliably tells them apart and surface metrics cannot. Whatever it is
> seeing is semantic, and finding it is the actual path to the gate.

Medicine and language & literature are the 2026-08-20 baselines; they have not
been re-measured since B.1/C.1/C.1b/B.2, all of which improved every domain they
were measured on. **Their figures are stale and probably understate the current
product.**

### The verdict

**Tier 1 (blocking) passes in six of seven domains.** `accuracy` is 4.00–4.53
and `misconception_handling` 4.17–5.00 everywhere except **medicine**, which
fails both (3.80 and 3.50) on a stale baseline that predates four improvements.

**Tier 2 (quality) fails nearly everywhere.**

- `socratic` clears 3.0 in **zero of seven** domains. Best is 2.80.
- `adaptation` clears 3.0 in **zero of seven**. Best is 2.20.
- `honest_telling` clears in one of seven.
- `visual_policy` clears in three of seven.

---

## 4. So what is shippable

**Mode A: yes, with the limitation stated.** Tier 1 passes. The product is a
factually reliable explainer that catches misconceptions, and an adult who
chooses it gets real value from that. What it is NOT yet is a strong Socratic
tutor, and **the README and any marketing must not claim otherwise.** A product
that says "teaches by asking" while scoring 2.8 on asking is making a claim its
own instrument contradicts.

**Mode B: no.** Not because of these scores but because the standards table is
empty — it cannot teach a standards-aligned lesson at all. Tier 3 is unmeasured
in every respect: no K-1 delivery path measurement, no per-band transcript
review, no safety-escape count.

**The single blocking item for a Mode A release** is honest positioning, not a
score. The scores are what they are and they are improving; the risk is claiming
past them.

**The single blocking item before medicine ships** is re-measuring it. It fails
Tier 1 on a baseline that predates four improvements.

---

## 5. What would change this document

- **Re-measure medicine and language & literature** on the current build. If
  medicine clears Tier 1, the blocking failure disappears.
- **`socratic` ≥ 3.0.** It moved 1.87 → 2.80 (+0.70 like-for-like) on 2026-08-21,
  the first real movement after four failed attempts. It is within reach.
- **`adaptation` ≥ 3.0** is the furthest away at 1.40–2.20, and A.2 — the
  intervention aimed at it — has only just become measurable.

---

## 6. Honesty conditions attached to any release

These are not scores; they are conditions on what we are allowed to say.

1. **Do not claim Socratic excellence.** Measured 1.67–2.80.
2. **Publish the measurement.** `docs/HELGABENCH.md` carries the methodology,
   the noise floor, and the caveat that every pre-2026-08-21 figure describes a
   thinner tutor than ships.
3. **State the domain coverage.** Seven domains measured; medicine and language
   & literature on stale baselines.
4. **Do not cite Bloom's 2-sigma.** It has never been replicated at scale.
   VanLehn 2011 puts human tutoring at d = 0.79 and step-based ITS at 0.76.
5. **If a public benchmark is run, publish the result whichever way it goes** —
   or do not run it as a marketing exercise at all.

---

## 7. The business assessment raises the bar on ONE number

A commercial assessment delivered 2026-08-21 sets a stricter gate than §2 does,
on one dimension, and for a different reason. It is worth recording because it
changes what the benchmark work is *for*.

**`adaptation` is now the go/no-go number for the company, not just the
product.**

| gate | source | value |
|---|---|---|
| Stage 0 → Stage 1 (spend money) | business assessment | **adaptation ≥ 3.5** |
| Abandon trigger | business assessment | adaptation stays below **~3.0** after focused effort |
| Tier 2 quality bar | §2 of this document | adaptation ≥ 3.0 |
| **Current, mathematics** | 2026-08-21 | **2.20** |

Take the stricter. **The operative target is 3.5**, and the operative risk is
that it stalls below 3.0 — at which point the assessment's recommendation is to
stop before hiring anyone, or pivot to licensing the private-inference engine
to an existing curriculum incumbent rather than selling to families.

### Why this dimension and not the composite

The assessment's reasoning is independent of ours and lands in the same place.
Its central objection is that **Khan Academy (free) + Khanmigo ($4/mo) covers
most of the value for most families**, so the only defensible wedge is
standards-aligned, parent-supervised, privately-hosted tutoring for a
values-driven segment. A tutor that does not *adapt to the individual child*
is not that product — it is a worse Khan Academy with a privacy story.

It also notes that **Khanmigo's own engagement is ~15%** of students with
access, which is the incumbent failing at the same thing. That is either the
opening or the warning, and adaptation is what decides which.

### What this means for the benchmark work

- `adaptation` moves ahead of `socratic` in priority. Both are below bar;
  only one is a company-level gate.
- The two interventions aimed squarely at it — **A.2** (structured turn state)
  and **A.6** (deterministic teaching move) — are the highest-value work in the
  sprint, not side quests.
- A gain from 2.20 to 3.5 is **+1.30**, an order of magnitude larger than any
  single dimension move measured so far (the largest was +1.76 on
  `misconception_handling`, and the largest on `adaptation` was +0.40). This
  should be treated as hard, not as one more increment.
- Content is the other blocking dependency and is **not a benchmark problem**:
  the standards table is empty, so no amount of tutoring quality makes Mode B
  sellable. Both must be fixed; only one of them is measured here.

---

## 8. The bar was never calibrated against what any tutor achieves

**Added 2026-08-21.** Sections 2 and 7 set `adaptation` >= 3.0 and >= 3.5. Both
numbers were chosen by judgement — mine and the business assessment's — and
neither was checked against what a tutor, any tutor, actually scores.

External reference points, from the Scale TutorBench public leaderboard:

| model | score |
|---|---|
| Muse Spark (tutoring-specialised) | **68.6%** |
| gpt-5.4-pro | 56.6% |
| gemini-2.5-pro | 55.7% |
| o3-pro | 54.6% |

The benchmark's own conclusion: **"today's models fall short of being effective
tutors"**, and the leader **"fails nearly half of the essential tutoring
criteria"**. Pedagogical sub-skills — generating alternative solutions and
analogies — average **37.4%**.

MathTutorBench reports the same shape independently: subject expertise "does
not immediately translate to good teaching", pedagogy and expertise form a
**trade-off**, and pedagogical capability "requires specialized training" —
i.e. fine-tuning, not prompting.

### What this does and does not license

**It does NOT mean our scores are fine.** `adaptation` at 2.30 is weak, the
learner experiences it as a tutor that follows a script, and that is a real
product defect.

**It does mean the 3.5 gate is probably above what any model reaches.** A rough
linear mapping puts frontier models at roughly 2.7-3.4 on a 1-5 scale, and the
pedagogical sub-skills nearer 1.9. Setting an abandon trigger at 3.0 and a
spend trigger at 3.5 may therefore be setting them above the state of the art,
on a locally-hosted 35B MoE at 4-bit.

**Caveats that matter.** Different benchmark, different rubric, different
tasks; percentage-to-1-5 mapping is approximate and should not be quoted as
equivalence. This is a sanity check on the bar, not a claim that we match
gpt-5.4-pro.

### What the gate should probably be instead

An absolute threshold nobody clears is not a decision rule. Two better shapes:

1. **Relative**: score against a named reference on the SAME instrument. Run
   HelgaBench's rubric over a frontier model's dialogues and compare. That is
   the honest version of "are we good enough", and it is a day of work.
2. **Trajectory**: is the number improving with effort? `adaptation` went
   1.53 -> 2.30 in a day. That is a real signal about whether the approach has
   headroom, and it does not depend on picking a magic number.

**Recommendation: do not treat "adaptation < 3.0 after focused effort" as an
abandon trigger until it has been checked against a reference model on this
instrument.** The trigger may be firing on a bar no product could clear, which
would be the wrong reason to stop a business.
