# Asset collection — the plan, from research

_Research delivered 2026-08-19 in answer to `RESEARCH_BRIEF_ASSET_COLLECTION.md`._
_**Parked until content hydration is finished.** Logged now so the findings are_
_not re-derived later._

---

## The finding that settles the phase

**Q2 (is a 9B VLM a good enough safety gate?) and Q4 (is "no photographs at
all" acceptable?) are one question, and both answers point the same way:**

* a general 9B VLM at 4-bit is **not** an acceptable sole safety gate for a
  system whose learners may include minors, and
* photographs earn **almost no pedagogical value** in a text-only Socratic
  tutor anyway.

**Recommendation: diagrams, charts and public-domain schematic illustrations
only. No photographs.** That single decision removes the NSFW gate, the model
swap, the VLM caption pass, the source × subject matrix, and most of the B13
vision plumbing — all at once.

### This overrides the architecture we proposed

The brief put forward unloading nail and running `qwen3.5:9b` over every asset
as a final gate. The research answers directly: **do not do this.**

* **The model authors disclaim the use case.** The Llama-Guard-3-11B-Vision card
  states it "is not meant to be used as an image safety classifier"; it is for
  multimodal *conversation* moderation. A general 9B VLM is weaker still.
* **Pixel-only safety collapses in practice.** SingGuard measured
  LlamaGuard3-Vision at F1 **0.0025** on weapon detection and **0.1875** on
  violence in pixel-only settings.
* **Quantisation destroys exactly the cases a gate exists for.** Naive 4-bit PTQ
  raised jailbreak ASR from 0.3% to **42.4%** on Llama-2-7B-Chat; W4A4 dropped
  SafetyBench "by more than 20 points"; a VLM study found 4-bit acts as a
  low-pass filter that preserves *average* accuracy while eroding tail-case
  discrimination — **+12.5% spurious-correlation reliance, up to +98%
  calibration error.** The model would pass our captioning smoke tests while
  having lost the borderline judgements the gate is for.
* The one counter-example proves it: Llama Guard 3-1B-INT4 matches full
  precision **only** because it used quantisation-aware training plus 8B-teacher
  distillation. Naive `Q4_K_M` gets none of that recovery.

So `qwen3.5:4b` is definitively insufficient and `qwen3.5:9b` is insufficient
*as a sole gate*. The 4B-vs-9B gap is real but irrelevant — both are below the
bar.

### And the pedagogy says the photos were not worth it

* **Seductive-details effect, meta-analysed.** Cromley et al., *Educational
  Psychology Review* (2025), 177 effect sizes across 50 studies: seductive
  details produce a significant **negative** effect on learning (g = −0.16),
  harming comprehension (−0.19), recall (−0.17) and transfer (−0.12). The
  earlier Sundararajan & Adesope (2020) meta-analysis found **g = −0.33**.
* Decorative photographs are the archetypal seductive detail — Harp & Mayer's
  lightning studies showed an interesting-but-irrelevant photo *reduced*
  comprehension.
* **Damage is worst for low-prior-knowledge, low-working-memory learners** —
  which is the entire target population of a tutor.
* What *does* help is narrow: representational, organisational and explanatory
  graphics — diagrams, charts, schematic line drawings. Mayer's own instructive
  condition used monochromatic schematic line drawings.
* A Socratic tutor needs them least of all: the cognitive work is verbal
  reasoning, and a photo competes for the same working memory.

**The medical-imagery and art-history cases — the ones that made a source ×
subject matrix necessary — are precisely the photographic cases being dropped.**

---

## Findings on the other questions

### MinerU: adopt, but for ingestion only — and benchmark first

The repo's earlier "wrong economics" verdict is **reversed for the ingestion
path**, on two specific changes:

* **MinerU 2.5** (Sept 2025) is a 1.2B VLM scoring 90.67 overall on
  OmniDocBench, beating MonkeyOCR-pro-3B by 1.82 and reportedly Gemini 2.5 Pro,
  GPT-4o and Qwen2.5-VL-72B on document parsing.
* **An MLX backend**, benchmarked at **~38 s/page on a Mac mini M4 (16 GB)**
  versus ~148 s/page on transformers — a 100–200% speedup, and the single
  biggest change for our hardware.
* **Licence change (v3.1.0, April 2026)**: AGPLv3 → an Apache-2.0-based licence
  with thresholds (100M MAU / $20M月) irrelevant to us, and AGPL-encumbered
  YOLO/layoutreader dependencies removed.

But scoped: **it is an input tool for parsing syllabi and reference texts into
Markdown, not part of the asset or safety path.** Its 1.2B VLM competes for the
same single-model-resident slot as nail, so it is batch/offline, never inline.

Given this project has twice paid for adopt-then-remove (KuzuDB, ZIM), the
discipline is: **benchmark PyMuPDF4LLM (zero-dependency) and Docling (MIT, CPU)
first** on real syllabi, and adopt MinerU only if it beats both by a
pre-declared margin (suggested ≥10 points on heading/table/formula fidelity).
For clean born-digital syllabi PyMuPDF4LLM will likely suffice.

Marker was rejected on licensing (GPL-3.0 + RAIL-M weights); GROBID converts
formulas to images, which is actively bad for accessibility.

### Math for TTS — high value, model-free, do it regardless

KaTeX renders but cannot speak. The accessibility ecosystem solved this a decade
ago and it is entirely deterministic:

* **MathJax Speech Rule Engine (SRE)** converts MathML/LaTeX to speech with
  selectable rule sets — MathSpeak (verbose, unambiguous) and **ClearSpeak**
  (natural: `\frac{a}{b}` → "a over b").
* `latex-to-speech` (npm, on MathJax + SRE) runs offline in Node.
* **MathCAT** (Rust, DAISY, the MathPlayer successor) is the alternative and
  also emits Nemeth/UEB braille.
* MathML Core is now natively supported in all major browsers.

**Pipeline:** at *hydration* time — not session time — for every `$…$` span:
keep the LaTeX as canonical, generate and store a **ClearSpeak speech string**,
optionally store MathML. At session time TTS reads the pre-generated string, the
visual path uses KaTeX, the text-only path uses either. Cost is a few ms per
formula, zero model, zero runtime latency. Store in
`concept_math(concept_id, latex, mathml, speech_clearspeak)`.

### Asset storage — BLOBs in SQLite, against the raw benchmark

Images sit *above* the ~100 KB performance crossover, so pure benchmarking would
say filesystem. The recommendation is SQLite anyway, and the reasoning is
integrity rather than speed: **a single-file database cannot develop dangling
references to missing image files**, WAL gives atomic rebuilds, same-origin
serving from a blob is trivial, and the course stays one portable artefact.

Schema: an `assets` table with `sha256` (dedup key + integrity), `bytes`,
`mime`, `width/height`, `source`, `license`, `license_verified_at`,
`provenance_url`, `alt_text`, `caption`, `caption_verified`; joined via
`concept_assets(concept_id, asset_id, role)`. Alt text lives in the same row so
it survives rebuilds atomically with the bytes. Spill to filesystem only for
assets over a few MB.

### Captions: use the source's own words, never a model's

For a diagram-only corpus the model-free instrument is trivial — **the caption
should be the source institution's published title/description** (Met, AIC and
PhET all provide them), verified by string match, with zero generation.

If captions were ever generated, the metric is **CHAIR** (Rohrbach et al. 2018)
against a detector or source metadata — and never against the same model family
that wrote them, which is the self-grading trap this project already hit with
its ±1.4/5 judge.

### Relevance and safety are two jobs; B13.6 conflates them

Different thresholds, different failure costs. Safety is a fail-closed
high-recall gate where a miss is catastrophic; relevance is a quality filter
where a miss is embarrassing. One VLM pass with one threshold means tuning for
caption quality loosens safety. They must be separate stages.

---

## Staged plan

### Stage 0 — Decide the photo question (blocks everything else)

Adopt **diagram/chart/illustration only**. Restrict sources to diagram and
line-art holdings — **PhET (CC-BY)**, Wikimedia Commons diagram categories, CC0
museum line-art, NASA/USGS schematic figures — and delete the photographic
scrape path.

*Change course if:* a controlled A/B on real courses shows representational
photographs improving answer quality by a margin that survives judge noise —
**measured with a model-free instrument**. The literature predicts it will not.

### Stage 1 — Only if photographs are kept anyway

A cascade, never a VLM alone: purpose-built classifiers first (**NudeNet +
Falconsai in agreement, fail-closed, tuned for recall not precision**), human
review of a random sample every build, and the VLM used *only* for
relevance/caption on already-passed images.

Sobering numbers: NudeNet reports 4.4–14.4% false negatives and independent
evaluation at 68–78% accuracy on harder sets; a child-safety study found
Falconsai has the highest precision (91.15%) but **misses over 59% of unsafe
content** at its default threshold.

*Abandon immediately if:* any explicit image passes the cascade in a
human-reviewed sample.

### Stage 2 — Ingestion (independent of the photo decision)

Benchmark PyMuPDF4LLM and Docling on 10–20 real syllabi first; adopt MinerU
(MLX backend) only on a pre-declared margin.

### Stage 3 — Storage and math (do regardless)

Assets as BLOBs; LaTeX + ClearSpeak + MathML per formula generated at hydration.
**Ship the math-for-TTS pipeline now** — high value, model-free, cheap, and
independent of the contested photo question.

---

## What to measure, and what looks like success but is not

| signal | what it really means |
|---|---|
| **safety filter rejects almost nothing** | indistinguishable from a broken filter — track rejection rate and eyeball rejected items; "too clean" is the alarm |
| longer, cleaner-looking parsed Markdown | may have silently dropped or merged concepts — measure concept count and boundaries against ground truth, not prose quality |
| high caption "quality" | if scored by the caption-writing model family, it confirms its own hallucinations |
| VLM passes captioning smoke tests | average ability survives 4-bit; the tail the gate exists for does not |
| TTS speech string is non-empty | SRE may be reading raw LaTeX ("backslash frac") — assert no control sequences remain |
| source × subject matrix reports full compliance | it can serve a classical nude to a 15-year-old in an art course and be *satisfied* — invisible by construction |

---

## What to abandon, plainly

* **The general-VLM-as-sole-safety-gate design** (`B13.6` as specified) —
  unsound for a minors-inclusive system, disclaimed by the model authors.
* **The photographic asset pipeline**, on current evidence.
* **Most B13 vision plumbing** — kept only if Stage 1 photos are retained.
* **The model-swap-for-asset-inspection plan** we proposed — moot under
  diagram-only.

**Do not abandon:** fail-closed licensing (it is the one part of the current
safety story that actually works), SQLite, KaTeX, and MinerU-for-ingestion
(conditionally, scoped to input parsing).

---

## Caveat carried forward

The research notes that several sections — model-swap economics, source
coverage, caption hallucination, relevance-vs-safety separation, and the
measurement items — are **extrapolated adjacent questions** rather than answers
to the numbered brief, because the full question list was not available to it.
Q1–Q4b are answered directly. Treat the rest as well-argued but unprompted.

---

## DECISION (2026-08-19) — where we depart from the research, and why

The research recommends **no photographs at all**. We are adopting a narrower
rule instead, on an explicit product decision: **photographs are allowed from
vetted public educational collections where the item carries an affirmative
open licence.**

This is not a rejection of the finding. It is a claim that the research's two
legs need separating, because only one of them is answered by "no photographs".

### Leg 1 — safety. The caveat answers this properly.

The safety case is about **scraping**: unvetted images needing a pixel-level
gate, and no available gate being good enough. Every number holds — a general
9B VLM at 4-bit is not a safety classifier, Falconsai misses >59% of unsafe
content at default threshold, 4-bit erodes exactly the tail-case calibration a
gate exists for.

But that argument is about **inspecting an unknown image**. It says nothing
about an image whose provenance is a curated educational collection that has
already done editorial selection. **Provenance is a stronger gate than pixel
inspection**, and the research says so itself: fail-closed licensing "is the one
part of the current safety story that actually works."

So the gate becomes allowlist + affirmative licence, not a classifier. That is
the design the research would endorse if the question were "curated source"
rather than "open web".

### Leg 2 — pedagogy. This one survives, and needs a different fix.

The seductive-details evidence is real (g = −0.16 over 177 effect sizes;
−0.33 in the earlier meta-analysis) and worst for exactly our learners.

**But it is about DECORATIVE images, and the research uses "photograph" as a
proxy for "decorative".** Levin's taxonomy classifies by FUNCTION, not medium: a
photograph of an actual mitochondrion, of the Berlin Wall, of Jupiter's storms
is **representational**, doing precisely the job a diagram does. A stock photo
of a smiling student is decorative — and so is an irrelevant diagram.

Medium was chosen as the proxy because it is easy to filter on and function is
not. The caveat gives a better proxy: **a curated educational collection has
already filtered by function.** NASA, USGS, CDC PHIL, Wellcome and the
Smithsonian publish images because they illustrate something.

**The rule we adopt is therefore representational-vs-decorative, enforced by
source curation plus a required role**, not diagram-vs-photograph.

### What this obliges us to do

An asset must attach to a concept with a **stated role** — `illustrates`,
`worked_example`, `data`, `schematic` — and an asset that is merely *related*
is refused. That is what keeps the seductive-details risk bounded once the
medium restriction is gone, and it is a deterministic check.

---

## What we will implement, in order

### Do now — independent of the photo question

1. **Math for TTS.** The sleeper item and the best value on the list:
   model-free, mature, cheap, and unaffected by anything contested. KaTeX
   renders but cannot speak. At hydration, for every `$…$`: keep LaTeX
   canonical, generate a **ClearSpeak** string via the MathJax Speech Rule
   Engine, optionally store MathML. Table `concept_math(concept_id, latex,
   mathml, speech_clearspeak)`.
   **Test that it works:** assert no LaTeX control sequences survive into the
   speech string — SRE silently reading "backslash frac" passes a non-empty
   check and fails only when a human listens.

2. **Assets as BLOBs in SQLite**, mirroring what v15 just did for concepts and
   for the same reason: a single-file database cannot develop dangling
   references to missing image files. `assets(sha256, bytes, mime, width,
   height, source, license, license_verified_at, provenance_url, alt_text,
   caption, caption_verified)` joined by `concept_assets(concept_id, asset_id,
   role)`. Spill to disk only above a few MB.

### Do — under the amended photo rule

3. **Expand the source allowlist to vetted educational collections**, each with
   machine-readable per-item licence: Smithsonian Open Access (CC0, minus
   culturally-sensitive), NASA, USGS, CDC PHIL, Wellcome, Rijksmuseum, PhET
   (CC-BY, ideal for diagrams), plus the existing five.
4. **Keep fail-closed licensing exactly as it is.** Unknown licence = refused.
   Note the traps: "No Known Copyright Restrictions" ≠ CC0, and institutional
   terms can differ from the per-item licence.
5. **Require a role on every concept-asset link.** No role, no attachment.
6. **Captions come from the source's own published title/description**, verified
   by string match. Zero generation, so zero caption hallucination — and no
   CHAIR metric needed because nothing is generated.

### Drop

7. **The VLM-as-sole-safety-gate, including the model-swap plan I proposed.**
   The evidence against it is strong and the model authors disclaim the use
   case. Safety comes from provenance, not pixels.
8. **The uncurated photographic scrape path.**

### Defer, with a gate

9. **MinerU.** The reversal is well-argued — 1.2B VLM at 90.67 OmniDocBench, an
   MLX backend at ~38 s/page against ~148, and the April 2026 move off AGPL.
   But this project has paid the adopt-then-remove cost twice (KuzuDB, ZIM), so:
   **benchmark PyMuPDF4LLM (zero-dependency) and Docling (MIT, CPU) first** on
   10-20 real syllabi, and adopt MinerU only on a pre-declared margin (≥10
   points on heading/table/formula fidelity). Ingestion only, never the asset
   path, and batch/offline since its VLM competes for the same resident slot.
10. **The NSFW classifier cascade.** Only needed if uncurated photos are ever
    scraped. Under the amended rule they are not, so building it now would be
    protection against a path we are not taking.

### The measurement that would overturn this

If a rejection log shows the allowlist admitting anything inappropriate, the
provenance gate has failed and Stage 1's classifier cascade becomes mandatory.
**Track the rejection rate and read the rejected items** — a filter that rejects
almost nothing is indistinguishable from a broken one, and that is this
project's recurring failure pattern applied to safety.
