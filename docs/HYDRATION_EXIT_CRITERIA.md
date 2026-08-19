# Content hydration — exit criteria

_When is hydration development done? The answer is a command, not a paragraph._

```bash
python3 tools/hydration_qa.py --course <uid> --data-root <dir> --structure <structure.json>
```

**Exit is `CONTENT_READY` on the median of three builds, with no check reporting
NOT RUN.**

Three, not one, because this project measured its own generator swinging ±10
points on identical input and its LLM judge swinging ±1.4/5 between identical
runs. A single green build has misled us repeatedly.

---

## The criteria

Every one is arithmetic on the ledger — no model — except `truth`, which is
advisory and explained below.

| check | threshold | why that number |
|---|---|---|
| **redundancy** | ≤10% of concepts re-introduce ≥half their claims | Not zero. Bruner's spiral is the retention mechanism the scheduling design depends on, so what is bounded is concepts that *re-introduce* rather than *build*. |
| **substance** | ≥2.0 claims/concept, 0 empty | A concept that asserts nothing cannot be taught or verified. |
| **hollowness** | mean completeness ≥0.70, ≤10% below half | The measured defect: ~half of concepts structurally complete and substantively empty. The section template **cannot see this by construction** — passing it *is* having the headings. |
| **grounding** | ≥80% of claims linked to a retained source | A claim from nowhere cannot be checked, now or later. |
| **supplementary** | ≤20% of claims rest *only* on below-bar sources | Measured in **claims, not sources** — one weak book can dominate content while being a minority of the source list. |
| **depth** | ≥90% meet their depth contract | The mastery slider has to mean something; it was once decorative. |
| **truth** | advisory — see below | |

### Why `truth` is advisory and not a gate

MiniCheck-Flan-T5-Large was pulled, wired, and **validated on a seeded set
before being trusted** — which is what the plan demanded, and it paid off
immediately:

```
accuracy             4/6  (0.667)
false claims caught  3/3      <- the direction that matters
true claims flagged  2/3      <- the direction that makes it unusable as a gate
```

It caught **every** falsehood. It also rejected two **true** claims that needed
one step of inference from their passage:

> claim `"The expected value of a fair twenty-sided die is 10.5."`
> passage `"A fair d20 is uniform over 1 to 20, so its mean is (1+20)/2 = 10.5."`
> verdict **UNSUPPORTED**

Teaching material is *written* to rephrase and generalise its sources, so this
is the norm here rather than an edge case. Gating on it would reject correct
content faster than it catches wrong content.

So `truth` **reports and flags for review**; it never fails a course. Promoting
it to a gate requires two things first: span-level claim attribution (claims are
currently linked to a concept's source *set*, not the passage each came from,
which manufactures mismatches), and a measured false-positive rate on real
content.

**This is itself an exit criterion:** the harness must report `truth` as run,
even while advisory. A verifier that is never invoked is indistinguishable from
one that is broken.

---

## What is NOT an exit criterion

Stated explicitly, because each is a plausible-looking metric that would mislead:

* **"The full test suite passes."** It does (1679+). It says nothing about
  content quality — every test is about mechanism, not output.
* **A `PROFESSIONAL` verdict from `skeleton_qa`.** That grades structure. A
  course can be PROFESSIONAL and full of false claims; that gap is the entire
  reason this document exists.
* **An LLM judge score.** ±1.4/5 on identical input. Never gate on it.
* **Zero redundancy.** Would mean the spiral has been suppressed and legitimate
  reinforcement destroyed.
* **A truth check that flags nothing.** Indistinguishable from a broken one.
  Track the rejection rate; "too clean" is the alarm.

---

## Blocked, and honestly so

**The quantisation hypothesis cannot be tested on this machine.** The research
proposed IQ3_S vs IQ4_XS as the test of whether aggressive quantisation drives
the false-claim rate. Measured: IQ4_XS weights are ~15.7 GB against a ~15.0 GB
safe ceiling — over budget *before any KV cache*, and past ~16 GB this machine
does not degrade gracefully but stops producing usable output.

The precision ceiling here is IQ3_M, barely a step from IQ3_S and still inside
the band the literature calls degradation. **That experiment is out of scope
for this hardware**, not pending. It does not block exit; it removes a lever.

---

## Current state

| item | state |
|---|---|
| taught-concepts ledger, claims + retrieval + redundancy | **done** |
| ledger wired into hydration (retrieve → generate → record) | **done** |
| redundancy correction round naming the offender | **done** |
| teaching object: claims, steps, misconceptions, seeds, threshold | **done** |
| hollowness measurable per concept | **done** |
| retained sources + claim links (v12) | **done** |
| session notes + compaction boundary (v13) | **done** |
| supplementary share measured in claims | **done** |
| `num_ctx` 32k, chosen for co-residency | **done** |
| prompt inverted for prefix caching | **done** |
| extended open sources (OpenAlex, PubChem, arXiv, Gutenberg) | **done, all four probed live** |
| MiniCheck pulled, wired, seed-validated | **done — advisory** |
| `hydration_qa` harness | **done** |
| span-level claim attribution | **not done** — blocks promoting `truth` to a gate |
| IQ4_XS experiment | **out of scope** — does not fit 24 GB |

**Not yet run: the harness against a real build.** Every threshold above is
argued from measurement of the *defects*, not yet calibrated against a real
course. The first real run may well show a threshold is wrong — and moving one
with a stated reason is legitimate; moving one because a build failed it is not.
