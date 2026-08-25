# Stage 4 — The Audit

**Revised after surveying the pipeline.** The first draft designed this as a new
stage on a pipeline it had not read. Most of what it proposed already exists,
under other names, and the parts that do not exist are smaller and more specific
than it claimed. This version says what to *promote*, what to *wire*, and what
is genuinely new.

---

## What the pipeline already is

There are **two** pipelines, not one (`docs/COURSE_PIPELINE.md`):

| | researched path | book path |
|---|---|---|
| 1 | Evidence — `curriculum_brief`, `_partition_brief` at `GROUNDING_RELEVANCE` | Parse — `book_reader.open_book` |
| 2 | Scope — `scope_fit` | Shape — `book_skeleton.choose_shape` |
| 3 | Skeleton — per-module subtree, correction rounds | Name by reading — `book_source.attach_concepts` |
| 4 | Hydration — ledger retrieval, depth contract, redundancy correction | Hydrate from the text |
| 5 | Assets | **Gate — `book_course_qa`** |
| 6 | **Gates — `skeleton_qa` + `hydration_qa`** | |

**There is already a gates stage.** Stage 4 is not a new stage bolted on the
end; it is the existing gate, promoted from a manual tool into the pipeline and
extended. Calling it "Stage 4" is a convenience — in the researched path it is
stage 6, in the book path stage 5, and it must work for both.

Everything downstream is shared between the pipelines — storage, the ledger, the
depth contract, redundancy correction, retained sources — so the audit attaches
to the shared layer and inherits both paths for free.

---

## What already exists that the first draft proposed building

This is the important table. Every row was going to be written from scratch.

| First draft proposed | Already exists | Where |
|---|---|---|
| MiniCheck entailment checker | **Built, and validated on a seeded set** | `services/core/claim_verifier.py` |
| Claim decomposition | `extract_claims` | `services/core/taught_ledger.py` |
| Embed claims, retrieve neighbours | `embed`, `neighbours`, `_cosine`, hybrid dense+lexical | `taught_ledger.py`, already bge-m3 via Ollama |
| Cross-concept duplicate detection | `check_redundancy` | `taught_ledger.py` |
| Corpus of authoritative docs | DevDocs manifest + doc-site crawl, domain-routed | `services/domains/computer_science/devdocs.py`, `source_for()` |
| Doc site as a corpus | **Read as a *Book*** — `crawl`, `to_book`, `sequence` | `services/research/doc_reader.py` |
| Textbook full text | `open_book` — EPUB, PDF ToC-ladder, text | `services/research/book_reader.py` |
| Source trust weighting | `SOURCE_KIND_WEIGHTS`, `is_documentation`, `confidence_from_sources` | `services/research/ranking.py` |
| Whole-course quality report | `hydration_qa` — redundancy, substance, hollowness, grounding, supplementary, depth, truth | `tools/hydration_qa.py` |
| Structure report | `skeleton_qa`, `book_course_qa` | `tools/skeleton_qa.py` |
| Level verdict | `calibrate` | `services/common/level_calibration.py` |
| "Unchecked is not clean" | **Already the stated rule**: "NOT RUN is never a pass" | `hydration_qa.py` |
| Binary checklist over scalar scores | **Already the stated rule**: "arithmetic wherever possible" | `hydration_qa.py` |
| Verdict recorded on the course | `course["fact_check"]`, `depth_contract`, `level_calibration`, `grounding`, `missing_sections` | `course_builder.py`, read by `_quality_*` in `web-ui/app.py` |

The repo also already has the **promotion pattern** this design needs, stated in
`level_calibration.py`: *"the library form of `tools/level_audit.py` so hydration
can record a verdict at generation time instead of it being an after-the-fact
manual step."* Stage 4 is that same move, applied to `hydration_qa`.

---

## The actual defect: the good verifier is not in the pipeline

`claim_verifier` has exactly **one** caller — `tools/hydration_qa.py`, a manual
tool. Nothing in a build imports it. Meanwhile `fact_check.py`, which asks the
model that wrote the content whether it was right, on a 34% sample, **is** wired
into `course_builder.py:5493`.

So the pipeline runs the weak checker and not the strong one. That is the whole
finding, and it explains the audit result exactly: `fact_check` ran 38 times over
two courses and found none of the seven false claims a reader found by hand.

**And the strong one cannot simply be swapped in.** It was already measured here,
on a seeded false-claim set, 2026-08-19:

```
accuracy             4/6  (0.667)
false claims caught  3/3      <- the direction that matters
true claims flagged  2/3      <- the direction that makes it unusable as a gate
```

Both false flags were claims needing **one inference step** from the passage —
"a fair d20 is uniform over 1 to 20, so its mean is (1+20)/2 = 10.5" judged
UNSUPPORTED for the claim "the expected value of a fair twenty-sided die is
10.5". High recall on falsehood, poor precision on truth.

That measurement, not the literature, is what determines the design below.

---

## What is genuinely new

Five things. Everything else is wiring.

**N1 — Execution.** `sql_ground_truth.py` settles a claim by running it against
a real PostgreSQL. No model, no judgement, no false positives measured across
178 concepts. This is a tier that did not exist in any form, and it is the only
tier immune to the self-grading problem. **Built.**

**N2 — A second opinion, because MiniCheck's failure mode is known.** Its
measured weakness is flagging TRUE claims that need one inference step. Those are
disproportionately *arithmetic and computational* — which is exactly what N1
settles. So the tiers compose in a specific order rather than in parallel:

```
claim -> executable?  ── yes ─> run it. Done. (no model, decisive)
           │
           no
           ▼
        MiniCheck (recall: caught 3/3 falsehoods)
           │ supported ─> done
           │ unsupported ─> NOT a defect yet. 2 of 3 of these are true.
           ▼
        second opinion: three-way NLI (entail / neutral / contradict)
           only `contradict` is a defect; `neutral` is no-evidence, reported
```

The order matters and was not in the first draft: routing computable claims to
execution *first* removes the largest slice of MiniCheck's known false positives
before it ever sees them.

**N3 — Cross-concept contradiction.** `check_redundancy` already finds the same
idea taught twice. Nothing finds the same idea taught *two contradictory ways* —
and the audit found exactly that (two concepts giving opposite NULL orderings,
each internally consistent, each passing every gate). This reuses
`taught_ledger.neighbours()` unchanged; only the question asked of the retrieved
pair is new.

**N4 — Evidence that survives the build.** `sources.passage` was written empty
529 times out of 529, beneath a schema comment saying the cache "must never be
the only copy". Also now retained: the grounding text the model was actually
shown (separates *the source was wrong* from *the model invented it*), a content
hash (tells a verdict whether it still describes the file), and the sources the
prompt's **word budget** dropped — which passed relevance and lost a size
contest with a context window a fact-checker does not have. **Built.**

**N5 — Thin content, per concept.** `check_substance` (claims per concept) and
`check_hollowness` (teaching-object completeness) already measure thinness —
as course-level averages. They report that half a course is hollow and name no
concept, so nothing downstream can repair what they find. The per-concept form
measures three independent signals on the whole body: concrete density,
share of sentences carrying nothing specific, and paragraph self-repetition.

Its thresholds are the interesting part, because they were wrong twice.
Guessed first — the density floor landed five times below the thinnest concept
in the corpus, so two of three checks could not fail anything and "0 findings"
looked like success. Then set at p05/p95, which flags 5% of *acceptable*
content by construction and produced five findings, all false on inspection:
dense technical prose that used fewer code spans than average. They now sit
outside the observed range of content we accept (density never below 0.109,
empty-sentence share never above 0.389 across 178 concepts), so the check stays
silent on a good course — which is the correct answer, not a failure to find
something.

Near-verbatim paragraph repetition flags on its own, without corroboration: it
escaped the two-axis rule by being full of real SQL identifiers while being the
same sentence eight times. Validated on the corpus, it found `INTERSECT ALL
Logic`, whose definition paragraph appears **twice verbatim** — once standalone
and once under `### Core Explanation`. Nothing else catches that: the depth
contract sees required elements present, content guards see no stub, and
`check_redundancy` compares across concepts, not within one.

**N6 — Repair.** Nothing today fixes what a gate finds; `hydration_qa` reports
and stops. Repair is extrinsic by rule (evidence in the prompt, never "this is
wrong, fix it"), minimal (RARR), and re-verified by the same checks — so a bad
repair costs a generation rather than putting a new falsehood in a lesson.

---

## How it integrates, concretely

**Reuse `hydration_qa`'s checks by promoting them, not copying them.**
`services/core/course_audit.py` currently re-implements depth, substance and
grounding checks that `tools/hydration_qa.py` already has. That is the
duplication this revision exists to stop: the checks move into the library,
`hydration_qa` becomes a thin CLI over it, and there is one implementation.
`course_audit` keeps what is genuinely its own — content guards, execution,
tutor sections, citation integrity, coherence, systemic folding.

**Ask the domain, do not hardcode the domain.** `services/domains/registry.py`
is a plugin contract (`classify`, `rank`, `guidance`, `prompt_line`, optional
`source_for`). Stage 4 uses it three ways:
- *corpus selection* — `source_for(subject)` already returns TECHNOLOGY (DevDocs
  has it), TOOL (crawl its doc site) or CONCEPT (no authoritative site; use the
  researched path). The first draft's "download the PostgreSQL tarball" was a
  worse, narrower version of a routing decision the registry already makes.
- *source ranking* — `rank` and `ranking.SOURCE_KIND_WEIGHTS` order evidence for
  retrieval. No new trust model.
- *repair prompts* — `guidance` and `prompt_line` carry the domain's voice, so a
  repaired mathematics concept does not come back written like a history one.

**Honour the domain calibration in the contract.** `DOMAIN_APPLIED` drops
`named_result` for applied domains, and normative sources count as
`primary_source` only there. The audit calls `validate_concept(body, mastery,
topic, domain, sources)` with the domain **read off the course**, never
re-inferred — re-inference is what made hydration demand a named theorem of
every SQL concept when 0 of 16 known-good ones had one.

**Everything is a Book.** `doc_reader.to_book` already turns a documentation site
into the same object `book_reader.open_book` produces from an EPUB or PDF. Stage
4 indexes *Books*, so uploads, textbooks and doc sites are one path, and the book
pipeline's own courses get audited against their own source with no extra work.

**Write into the verdict keys that already exist.** The UI reads
`course["fact_check"]`, `depth_contract`, `level_calibration`, `grounding`,
`missing_sections` through `_quality_*`. The audit writes the same keys, so the
existing course cards and trust panel surface it with no UI change — and
`concepts_total` becomes the whole course rather than the last build run, which
is the bug that made a 95-concept course report "All 14 concepts met the depth
contract".

**Median-of-3 on anything with a model in it.** Already the pipeline's stated
discipline, and it matches the local measurement that this repo's LLM judge
swings ±1.4/5 between identical runs. The deterministic tiers need it not at all,
which is another reason to route as much as possible to them.

---

## Efficiency, revised

The first draft argued efficiency from model sizes. The measured numbers say the
argument is really about **routing**:

| tier | cost | coverage |
|---|---|---|
| execution (N1) | **0.25s for 95 concepts** — measured | computable claims only |
| deterministic checks | same pass, no model | structure, hygiene, citations, coherence |
| MiniCheck | 0.78B, CPU, ~400x cheaper than a large judge | every remaining claim |
| second-opinion NLI | small, and only on MiniCheck's unsupported set | the minority |
| repair + 35B escalation | expensive | single-digit percentage |

`claim_verifier` already has `unload()`, so the checker does not have to sit
resident beside the builder — which matters on a machine where reading a model
under load measured 84 MB/s.

Prefix caching still applies: one grounding document, many claims, and hydration
is *already* invariant-first for exactly this reason.

---

## What it cannot catch

- **Claims with no evidence anywhere** — reported as `no-evidence`, never as
  supported. Coverage is a number, not an implication.
- **Pedagogy** — whether an explanation is clear or an analogy illuminates.
  A checklist can ask whether an analogy is present, not whether it is good.
- **A wrong authoritative source** — if the corpus is wrong the audit agrees
  with it. Execution is the only tier immune, and covers only computable claims.
- **MiniCheck's known blind spot** — one-inference-step truths, measured at 2 in
  3. N1 and N2 route around it; they do not fix it.

---

## Phasing, revised

| phase | scope | status |
|---|---|---|
| **A** | Execution tier; deterministic course audit; evidence persistence | **done** — 0.25s/95 concepts |
| **B** | Promote `hydration_qa`'s checks into the library; delete the duplicated ones from `course_audit` | next, and it *removes* code |
| **C** | Wire `claim_verifier` into the pipeline with the N2 routing and a second opinion | the actual defect |
| **D** | Cross-concept contradiction via `neighbours()` | small, reuses the index |
| **E** | Corpus via `source_for` → `doc_reader.to_book` | domain-routed, no new fetching |
| **F** | Repair + escalation; write the existing verdict keys | makes it fix rather than report |

Phase B is the one that matters most for the concern that prompted this
revision: it is the phase that makes the codebase smaller.
