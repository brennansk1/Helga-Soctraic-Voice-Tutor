# Research brief — Phase 3, asset collection

**For:** Claude Research
**From:** Project Helga
**Question in one line:** For an offline AI tutor that generates its own
courses, how should it acquire the images and diagrams a learner looks at —
extracting from a textbook where one exists, and scraping only *verifiably safe
and correctly licensed* sources where one does not — on a machine whose language
model **cannot see images at all**?

> Third in a series. `RESEARCH_BRIEF_LONG_HORIZON_LEARNING.md` covered
> scheduling and retention; `RESEARCH_BRIEF_CONTENT_HYDRATION.md` covered the
> text those sessions teach from. This one covers what appears on screen.

---

## 0. The task, stated plainly

**Give every concept the picture it needs — the real figure from the textbook
when there is one, a correct generated diagram or a properly licensed
photograph when there is not — and never, under any circumstance, put an
inappropriate image in front of a learner.**

Four things make it hard, and an answer that solves only some does not solve it:

1. **Two completely different acquisition paths.** With a textbook, this is a
   document-extraction problem (figures, captions, and the text they belong to).
   Without one, it is a *search-and-vet* problem, and the vetting is the hard
   part.

2. **The safety bar is absolute, and the generation model cannot look.** A
   wrong fact in generated prose is a defect. A pornographic or violent image
   shown to a learner is a product-ending failure. **`nail-35b-a3b` has no
   vision capability**, so nothing can vet an image while the course is being
   built — the intended answer is a model swap and a whole-course visual pass at
   the end (§1), which is itself one of the things we want evaluated.

3. **Image licensing is much harder than text licensing.** A figure inside a
   copyrighted textbook is not ours to redistribute even when the surrounding
   facts are freely usable. Most image APIs make you *infer* a licence from the
   collection, which is how incorrectly-licensed material ends up in teaching
   material.

4. **It must degrade, never fail.** Hydration failing means no course. Assets
   failing must mean a course with fewer pictures.

---

## 1. What the system is

**Helga** — offline, self-hosted Socratic AI tutor. **Mac mini, Apple M4
(4P + 6E), 24 GB unified memory.** No cloud APIs at tutoring time.

### The model — and the constraint that dominates this brief

    nail-35b-a3b-ctx, served by Ollama
    qwen35moe MoE - 256 experts, 8 per token, 40 blocks
    34.7B total / ~3B active,  IQ3_S (3.44 bpw)
    weights 12.74 GB,  num_ctx 16384 today  (model max 262144)
    measured: 30.1 tok/s decode, 247 tok/s prefill, ~142 s cold load

    capabilities: ["tools", "thinking", "completion"]      <-- NO VISION

**This is a regression against what Phase 3 was designed for.** `B13` swapped
the stack to Qwen3.5-9B *specifically because it was multimodal*, so that
"visuals can become objects of Socratic inquiry", and built vision plumbing on
that basis: `chat(images=…)`, image rendering in chat, student image upload
wired end-to-end. The current model cannot use any of it.

Consequences that shape every question below:

* Nothing **during generation** can look at a candidate image — not to judge
  appropriateness, not to judge whether it depicts the concept, not to caption
  it. `B13.6` (VLM relevance/caption/alt-text pass) is the one unbuilt item.
* Any classifier we add is **another model competing for memory**, against a
  measured and unforgiving budget (§4).

### The intended architecture: a model swap for a final visual pass

The plan we want evaluated — **not assumed correct** — is to **unload
`nail-35b-a3b`, load a vision model, and inspect every collected asset in one
pass at the end of the build, before the course becomes available.**

Verified on this machine: the vision model is already present.

| model | size | capabilities |
|---|---|---|
| **`qwen3.5:9b`** | **6.14 GB** Q4_K_M | `completion, vision, tools, thinking` |
| `qwen3.5:9b-mlx` | 8.29 GB nvfp4 | same |
| `qwen3.5:4b` | **3.16 GB** | `completion, vision, tools, thinking` |

Unloading nail frees 12.74 GB, so a vision pass at 6.14 GB runs with roughly
**9 GB spare** — ample for batching, and it makes the safety check a
*whole-course gate* rather than a per-image guess. The cost is one model swap
(~142 s reload back to nail, but nothing needs nail afterwards) and a pass over
every asset.

This turns the vision gap from a blocker into a **phase-ordering decision**, and
it is the shape the rest of the pipeline already uses. The open questions are
whether a 9B VLM is *accurate enough to gate on*, whether the 4B would do
(halving the cost again), what the prompt and rubric should be, and what happens
to an image it is unsure about.

### Measured memory budget — every recommendation must fit this

Determined empirically by loading at increasing sizes with output verified:

| resident | free | decode | state |
|---|---|---|---|
| 14.82 GB | 24% | 31.0 tok/s | healthy |
| 15.75 GB | 14% | 30.9 tok/s | tight |
| 16.40 GB | 8% | *no usable output* | thrashing |
| 17.72 GB | 6% | *no usable output* | thrashing |

**Safe ceiling ~15.0 GB resident; past ~16 GB it is a cliff, not a slope** —
throughput is flat at ~31 tok/s and then generation simply stops working.

The LLM at 32k context is 13.51 GB, leaving roughly **1.5 GB for anything
else co-resident**, and the hydration plan has already spent part of that on a
verifier (MiniCheck 0.73 GB int8) and an embedding model. **A vision or safety
model must fit in what remains, or run in its own phase with the LLM unloaded**
(a ~142 s reload each way).

---

## 2. What already exists — please build on, not replace

### 2.1 Phase 3 is real code, not a plan

`services/core/asset_collector.py` (32 KB) already runs **after hydration,
before the course is enterable**, on the stated reasoning:

* it needs the finished text — you cannot choose a diagram from half-written prose
* only a whole-course pass can see that eight concepts all want the same water
  cycle; **per-concept work structurally cannot dedupe**, and duplicate figures
  are the most visible way this could go wrong
* different failure semantics — assets failing must never fail a build
* re-runnable alone: add a source, re-collect

It emits **diagram-as-code** specs under grammar-constrained output (Ollama
`format`) with retry against the *named* validation failure — the same
correction-round pattern used elsewhere, measured at 5/5 where prompt-only
enforcement is 0/5.

### 2.2 The current safety model is an allowlist, and nothing else

`services/research/image_sources.py` queries exactly five sources:

| source | note |
|---|---|
| **Wikimedia Commons** | ~120M files; returns licence + attribution explicitly |
| **NASA images** | |
| **Library of Congress** | |
| **Met Museum** | collection-level licence trusted |
| **Art Institute of Chicago** | collection-level licence trusted |

Licensing is **fail-closed**: `licence_ok()` returns True only on an affirmative
open licence. **An unknown licence is a rejected licence.** Public domain, CC0,
CC BY and CC BY-SA accepted; NonCommercial and NoDerivatives rejected.

**There is no content-safety filtering of any kind** — no NSFW classifier, no
safe-search, no appropriateness check. Grep for `nsfw|safe_search|explicit`
returns nothing. Today's entire safety story is *"we only ask five
institutional sources"*.

That posture is deliberate and mostly sound. It is also the thing this brief
needs to extend without breaking, because five sources is thin for a system that
must teach any subject — and note that two of those five are **art museums**,
whose collections contain a great deal of legitimate, curated, and entirely
unclothed classical art.

### 2.3 Media is downloaded once, served locally

`services/common/media_cache.py`: every image is fetched **once at build time**,
stored under `DATA_ROOT/media`, and referenced by a same-origin path. A remote
`<img>` in a tutoring turn is rejected — it would put a network round-trip on
the critical path of a session that already costs ~30 s of inference, and a
remote URL is a lesson with an expiry date.

### 2.4 Accessibility already depends on this

`B13.9` feeds deterministic alt-text from the diagram spec into TTS and the
text-only path. Any asset that arrives *without* a spec — i.e. a scraped
photograph — has no such description, and the model cannot generate one because
it cannot see the image.

### 2.5 MinerU is what the user intends to adopt — and the repo has already rejected it once

`docs/SPRINT_PLAN.md` lists **"MinerU / Docling / GraphRAG — heavy multimodal
parsing and graph construction; wrong economics"** among things explicitly not
adopted.

The user now intends to use MinerU for the textbook path. **We would like this
reversal examined rather than rubber-stamped**: what changed, whether the
"wrong economics" judgement was about throughput, memory, or accuracy, and
whether it holds on an M4 with a 15 GB ceiling.

---

## 3. The questions

### Q1. Is MinerU the right textbook-extraction tool here?

* How does MinerU compare with Docling, Marker, Nougat, Unstructured, or plain
  PyMuPDF for **figure extraction with caption association** — which is what we
  actually need, not text extraction?
* **What does it cost to run** on Apple Silicon with ~1.5 GB of spare RAM and
  no CUDA? Does it need its own models resident, and can it run in a phase with
  the LLM unloaded?
* Does it correctly bind a figure to *its caption and its surrounding section*?
  An extracted image with no idea which concept it illustrates is close to
  useless to us.
* Does it handle the formats we would actually feed it — scanned PDFs, EPUB,
  OpenStax and LibreTexts HTML?
* **Was the earlier "wrong economics" verdict right?** If the answer is "adopt
  it", say what changed.

### Q2. What may we legally do with figures extracted from a textbook?

This is the question we are least equipped to answer ourselves.

* A figure inside an openly licensed textbook (OpenStax CC-BY, LibreTexts
  CC-BY-SA/NC) is often **separately credited to a third party** with different
  terms. How is that detected at scale rather than assumed?
* For a textbook that is *not* openly licensed, is extracting figures for a
  single private learner defensible, and does that change if the course is ever
  shared? We are self-hosted and non-commercial, but we would rather have a
  bright line than a judgement call.
* Should extracted figures be treated as a **separate licence tier** from
  scraped CC images — for instance usable but never exportable?
* What attribution must be carried, and where must it be displayed?

### Q3. Without a textbook: how do we widen sourcing without widening risk?

Five sources is too thin to teach arbitrary subjects; general web scraping is
unacceptable. The user's requirement is explicit: **verified sources and
webpages only.**

* What further **institutional, allowlistable** image sources exist with
  machine-readable licences? Candidates we have not evaluated: Smithsonian Open
  Access, Rijksmuseum, Europeana, Internet Archive, PhET, NOAA/USGS, NIH/NLM
  Open-i, Wellcome Collection, BHL, ESA/ESO, CDC PHIL, USDA.
* Several of these are **medical or anatomical**. Open-i and Wellcome contain
  clinical imagery that is entirely appropriate for a medicine course and
  entirely inappropriate elsewhere. **Is per-source allowlisting sufficient, or
  does safety have to be per-image?**
* Same question for the two art museums already trusted: classical nudes are
  legitimate art-history material and unacceptable in a chemistry course. Is the
  right control **source × subject**, rather than source alone?
* Is there a defensible notion of a "verified webpage" beyond a curated
  allowlist — and if so, what verifies it?

### Q4. Is a VLM swap-in pass the right safety gate, and how should it work?

**The core safety question.** The intended design (§1) unloads nail and runs
`qwen3.5:9b` over every collected asset before the course is released.

* **Is a 9B VLM at Q4_K_M accurate enough to gate on?** What is its
  false-negative rate on unsafe imagery, and how does that compare with a
  dedicated classifier (NudeNet, Falconsai NSFW, CLIP-based NSFW heads, LAION
  safety classifiers)? Would **both** — cheap classifier first, VLM on
  survivors — beat either alone?
* Would **`qwen3.5:4b` (3.16 GB)** do the job, halving the cost again? Where
  does a 4B stop being reliable for this?
* **What is the right rubric and prompt?** "Is this appropriate" is too vague to
  be reliable. Should it be a structured checklist — nudity, gore, violence,
  hate symbols, distressing medical imagery — with per-axis verdicts, given
  constrained output is available?
* **Context-dependence.** Classical nudes are correct for art history and wrong
  for chemistry; clinical imagery is correct for medicine and wrong elsewhere.
  Should the gate be told the *course subject and learner age band* and judge
  appropriateness **relative to that**, rather than absolutely?
* **What happens to an image it is unsure about?** Our licence rule is
  fail-closed; the analogue is to drop anything not affirmatively verified safe.
  Is that right, and what does it cost in coverage? Is a **quarantine tier**
  — collected and stored but not shown until reviewed — worth the complexity?
* **What is the false-positive cost?** A gate that rejects half of a legitimate
  anatomy course is also a failure. How should that be measured before trusting
  it?
* Can the same pass do double duty — safety **and** relevance (Q5) **and**
  alt-text (§2.4) in one look at each image? That would close `B13.6` and the
  accessibility gap in the same phase.

### Q4b. Where and how should visual assets be stored and embedded?

The tutor will **display images, diagrams and mathematical notation inline** in
the Socratic dialogue. KaTeX is already vendored and rendering in
`session.js`, and `media_cache` already stores images same-origin.

* **What should be embedded where?** Options: a path reference on the concept
  row, an `assets` table joined to concepts, the asset inlined into the concept's
  teaching object, or a separate manifest per course. The hydration plan is
  already folding concept bodies into SQLite — do assets follow, and at what
  size threshold (the ~100 KB in-DB/on-disk crossover lands right in the middle
  of typical image sizes)?
* **How does the tutor decide to show one mid-dialogue?** Per-concept fixed
  slot, retrieved by relevance at turn time, or chosen by the model from an
  offered set? It cannot see them, so any turn-time choice is made on metadata.
* **Should assets be embedded for retrieval** — image or caption embeddings in
  the same index as the concept ledger — so "show me the diagram for this" is a
  query rather than a lookup?
* **Math notation specifically**: is LaTeX-in-Markdown the right storage form
  given KaTeX renders it, or should notation be a first-class field on the
  teaching object? What breaks in the **TTS and text-only paths**, where a
  formula must become speech?
* What metadata must travel with every asset to make it usable and lawful —
  licence, attribution, source URL, alt text, the safety verdict and which model
  produced it, the concept it belongs to?

### Q5. How do we know an image actually depicts the concept?

Separate from safety, and currently unsolved (`B13.6` is blocked on vision).

* Without a VLM, how much can be inferred from **caption, alt text, filename,
  and source metadata** alone? Is text-only relevance matching good enough to
  ship, and what is its failure mode?
* Is a **CLIP-style image-text similarity score** the right instrument — small,
  offline, no generation — and what threshold separates "depicts this" from
  "vaguely related"?
* How do we detect the specific failure of a *correct-looking but wrong* figure
  — right subject, wrong species/era/mechanism?

### Q6. Generated diagram or sourced photograph — which, when?

We can already generate diagram-as-code specs, validated and regenerated against
named failures. That path has no licence risk, no safety risk, and no relevance
risk — it is drawn to spec.

* For which concept types is a **generated diagram strictly better** than a
  found image, and where is a photograph genuinely necessary?
* Given the risk profile, should sourcing be the **fallback** rather than the
  default — i.e. draw it if it can be drawn, search only if it cannot?
* What visual kinds does a Socratic tutor actually need? Our generator covers
  schematic kinds; what is missing for the sciences and humanities?
* Is local diffusion (`B13.8`, unbuilt, explicitly *not* for technical
  diagrams) worth anything here, given it would need to fit the same budget?

### Q7. How do we avoid the same figure appearing across many concepts?

The stated reason Phase 3 is a whole-course pass. Directly parallel to the
re-teaching problem in the hydration brief, which turned out to share its
answer with whole-course awareness.

* What is the right **model-free** duplicate detector for images — perceptual
  hashing (pHash/dHash), embedding similarity, or exact-URL dedupe?
* How much reuse is *correct*? A recurring diagram that anchors a spiral
  curriculum is good; the same stock photo eight times is not. Where is the
  line, and is it the same structural distinction as with claims — reuse that
  **references** versus reuse that **re-presents**?

### Q8. Storage, memory, and disk for assets

* A bachelor's programme is ~5,400 concepts. At one or two images each, what is
  the realistic footprint — and what resolution should be retained, given the
  only consumer is a browser on the same machine?
* Should images live on disk with paths in SQLite, or as blobs in SQLite? The
  hydration research found small blobs are ~35% faster and ~20% denser in-DB
  **below ~100 KB**, with the filesystem winning above — and images straddle
  that line exactly.
* What re-encoding is worth doing at collection time (WebP/AVIF, max
  dimension), and does that create a licence/derivative problem for CC-BY-SA
  material?

### Q9. What repos or libraries would improve this stage?

For: PDF/EPUB figure extraction with caption binding, licence detection from
image metadata, NSFW and unsafe-content classification, CLIP-style relevance
scoring, perceptual hashing, image optimisation, and alt-text generation without
a large VLM.

We prefer **small, well-maintained, offline-capable** dependencies. Naming a
library is less useful than saying which of our problems it solves, what it
would replace, and what it costs in RAM.

---

## 4. Hard constraints

| constraint | detail |
|---|---|
| **The generation LLM cannot see** | `nail-35b-a3b`, capabilities `[tools, thinking, completion]`. Vision requires a model swap. |
| **A vision model is available** | `qwen3.5:9b` 6.14 GB and `qwen3.5:4b` 3.16 GB, both `vision`-capable, both already pulled |
| **Only one model resident at a time** | unload nail (12.74 GB) before loading a VLM; ~142 s reload each way |
| **Math renders already** | KaTeX vendored and live in `session.js`; TTS and text-only paths must still work |
| **~1.5 GB co-resident, or its own phase** | LLM at 32k is 13.51 GB against a measured ~15.0 GB ceiling; unloading costs ~142 s each way |
| **Cliff, not slope** | past ~16 GB resident, generation stops returning usable output entirely |
| **Offline at tutoring time** | collection may use the network; a session may not |
| **Same-origin media only** | every image downloaded once at build time; remote `<img>` is refused |
| **Fail-closed licensing** | unknown licence = rejected. Any new source must expose a machine-readable licence |
| **Assets must never fail a build** | degraded means fewer pictures, never no course |
| **Apple Silicon** | MPS/MLX available, no CUDA |
| **Self-hosted, non-commercial** | no paid APIs; NC-licensed material still avoided for derived content |

---

## 5. What would make the answer most useful

* **A stated recommendation**, not a survey — especially on MinerU, where the
  repo previously recorded the opposite conclusion.
* **Numbers we can check before committing**: model sizes in RAM, throughput per
  image, classifier false-negative rates, expected disk.
* **A defence-in-depth design for safety**, with the failure rate of each layer
  stated. We would rather ship fewer images than one wrong one, and we want the
  design to make that trade explicit rather than implicit.
* **What to measure**, including what would look like success while failing —
  our repeated experience is that our own instruments were the problem. A
  filter that passes everything looks identical to a filter that is working,
  until it doesn't.
* **What to abandon.** If the five-source allowlist, the diagram-as-code path,
  or the whole-course Phase 3 structure is wrong, say so plainly.
* **The honest answer on vision.** If a 9B VLM at Q4_K_M is not accurate enough
  to be the last line of defence before a learner sees an image, say so. The
  conclusion "do not scrape photographs at all — draw everything, or ship
  without pictures" is a legitimate and actionable finding, and better than a
  gate we would be trusting on faith.
* **Tell us the order of operations.** Phase 3 currently collects, and the
  proposal adds a visual pass at the end. If safety belongs earlier — at
  candidate-selection rather than post-collection — say so, and say what that
  costs given the generation model cannot see.
