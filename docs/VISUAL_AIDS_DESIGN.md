# Visual teaching aids (B13) — design

**What this is.** A diagram drawn above a tutor message: a number line, a labelled
triangle, a concept map. Called by the model, mid-dialogue, when a picture makes a
question askable that words cannot.

**What it is not.** Decoration, and never the answer. An aid that shows the result
converts a Socratic turn into a lecture with pictures.

Status: built, unit-tested (115 tests), rendered and inspected in Chromium in
both themes. **Never run against a live Ollama model** — see §9.

---

## 1. The decision everything else follows from

**Aids are specs, not pixels.** The model emits `{"kind":"number_line","min":-5,…}`
and the *browser* draws the SVG. Nothing server-side ever produces an image.

Four constraints in this system force that, and any one of them alone would be
enough:

| Constraint | What pixels would cost |
|---|---|
| `/state` returns the whole transcript on a **2-second poll** | A base64 PNG is 40–200 KB. Fifty in a transcript is a multi-megabyte payload re-sent every two seconds. A spec is 0.3–2 KB — and even that leaves the poll (§3). |
| Dark mode | A matplotlib PNG bakes a white canvas. SVG drawn from CSS custom properties re-themes for free. Verified in both themes. |
| **~30 s per LLM call** | A spec rides in the *same* generation as the message. Zero extra round-trips. |
| Testability | You can assert that a spec labelled the hypotenuse. You cannot assert that about a PNG. |

A fifth benefit was not designed for but turned out to matter most: **11 of the 12
kinds need no third-party Python at all**, because the browser does the drawing.
That is why aids work in a container where `sympy`, `matplotlib`, `numpy`,
`networkx` and `scipy` are all undeclared and every other viz tool fails closed
(§10).

The pre-existing pixel tools (`plot_function`, `plot_points`, `draw_graph`) are
untouched. They feed the tutor's private grading reasoning, are never displayed,
and are not part of this feature.

---

## 2. The twelve kinds

| Kind | For | Model difficulty |
|---|---|---|
| `number_line` | inequalities, negatives, fractions, rounding | low |
| `geometry` | shapes, proofs, labelled figures | **high** — coordinates + cross-refs |
| `plot` | curves, functions, trends | medium |
| `bars` | comparing quantities | low |
| `graph` | concept maps, flowcharts, causal chains, taxonomies | low |
| `timeline` | historical sequence | low |
| `table` | structured comparison | low |
| `venn` | overlap and contrast | low |
| `cycle` | repeating processes | low |
| `steps` | a procedure or worked method | low |
| `fraction` | part-whole, equivalence | low |
| `image` | a retrieved picture with callout pins | n/a — not model-authored |

`table` and `steps` render as real `<table>` and `<ol>`, not SVG: they are prose,
and semantic HTML gives wrapping and screen-reader structure SVG cannot.

---

## 3. Data flow

```
model emits ```aid {…}  OR calls show_visual(…)
        │
        ▼
extract_aids() / aid_sink()        ← rails: JSON repair, aliases, verification
        │
        ▼
normalize_aid()                    ← clamps, enums, bounds; NEVER raises
        │
        ├──► AidStore (per-FSM, LRU 64)      full spec
        │
        └──► transcript entry ["aids"]       ~200-byte descriptor
                    │                        {id, kind, title, alt, stage, tier}
                    ▼
             GET /api/aid/<id>  ──► full spec, fetched once, cached, 404 = normal
                    │
                    ▼
             aids.js draws SVG above the message text
```

**The descriptor/spec split is the load-bearing idea.** The transcript is polled
in full every two seconds; the descriptor is what rides there. It carries `alt`
deliberately, so the card renders its frame, its accessible description and its
text-only fallback with **no fetch at all** — and so a 404 after LRU eviction is a
non-event.

Aid ids are content hashes: identical diagrams collapse to one store entry, and an
id is stable across re-renders, so a pinned aid stays pinned.

---

## 4. Two production paths

**Inline fence (default).** The model writes `` ```aid {…} `` inside its reply.
The fence is lifted out before moderation, so the safety checker judges the prose
a learner actually reads, and the JSON never reaches the chat even when rejected.
Costs **zero extra LLM round-trips** and survives token streaming, because the
fence closes before the message ends.

**Tool call.** `show_visual(kind, spec)` plus two computed helpers. One tool with
a `kind` discriminator, not eleven separate tools — a tier-2 9B model picks well
from a short menu and badly from a 36-entry schema, and every tool description is
prompt weight on a 30 s/call budget.

- `visualize_function(expressions, …)` — the model supplies a formula, SymPy
  supplies 200 samples. The model cannot produce accurate samples of `sin(x)/x`
  itself; this is the difference between a curve a learner can trust and one they
  cannot.
- `visualize_data(values, labels, …)` — charts numbers the tutor already has.

The tools return a ~20-token confirmation, not the spec echoed back.

---

## 5. Pedagogy: staged reveal

Any element may carry `"stage": 1`. It is drawn but hidden, and revealed later —
a class toggle on already-drawn elements, so nothing reflows and the learner's eye
stays put.

`reveal` decides who uncovers it, and **defaults to `tutor`**:

- **`tutor`** — the FSM advances the stage after the learner answers. The reveal
  is a consequence of thinking. The card says *"more once you answer"* and shows
  no button.
- **`learner`** — a button appears. For reference figures and worked examples,
  where self-paced uncovering is the point.

A "Show me" button on a Socratic diagram is a spoiler with a nice label. That is
why the default is what it is.

The canonical use: draw the triangle with legs 3 and 4 and the hypotenuse labelled
`?` at stage 0; reveal `5` at stage 1. Never draw the result you are asking for.

---

## 6. Trust surface

Every card carries a provenance chip, because a learner should be able to tell a
computed figure from one the model drew from memory:

| Tier | Chip | Meaning |
|---|---|---|
| `computed` | **Computed** | Drawn from exact values calculated here. |
| `retrieved` | **From source** | From a cited source; carries attribution + licence. |
| `authored` | **Sketch** | The model drew it. Not independently checked. |

This is the §4.2 open item from `MODE_A_STATUS.md` ("the trust surface is still not
on screen") answered at the level of one diagram.

---

## 7. Assistance rails

The model is qwen3.5:9b (tier 2), asked for nested JSON with cross-references,
inline in prose, **with no retry and no constrained decoding** — a fence in
mid-prose cannot use Ollama's `format` schema the way the grading path does.

The failure mode is therefore not a broken screen. A bad aid is dropped and the
message is delivered intact. The failure mode is **the feature quietly never
firing**, which is exactly the class of problem this repo keeps finding late.

So: meet the model where it is.

| # | Rail | Turns this into |
|---|---|---|
| 1 | JSON repair | `{'k': [1,2,],}`, truncated output, narrated JSON → parsed |
| 2 | Kind synonyms | `flowchart`→`graph`, `pie`→`fraction`, `numberline`→`number_line` |
| 3 | Field synonyms | `links`→`edges`, `labels`→`categories`; bare `["a","b"]` → objects |
| 4 | Derived geometry | polygon vertices → the segments that draw its edges |
| 5 | **Verified claims** | a right-angle marker whose coordinates are not 90° is **removed** |

Rails 1–4 make a diagram appear. **Rail 5 stops a wrong one appearing** — and it
is the one that matters. A right-angle square drawn on a 53° angle teaches an
error with the full authority of a figure, and no prompt wording prevents it; only
arithmetic does.

The tolerant fence matcher accepts ```` ```json ```` and bare fences, but consumes
them **only once they parse into a real aid**. An explicit ```` ```aid ```` fence is
always removed, broken or not. This asymmetry is deliberate: a tutor teaching
Python legitimately shows ```` ```python ```` blocks, and eating one would be far
worse than missing a diagram.

Rail 1 also drove an upgrade to the shared `repair_json()` — see §10.

---

## 8. UI/UX

The card sits **above** the message text, inside the same bubble group: the
learner meets the figure first and reads the question second, which is the order
the tutor is teaching in.

- **Describe (ⓘ)** — the written description is a first-class part of the card,
  not a hidden attribute. It is what a screen reader announces, what TTS speaks
  after the message, and what replaces the figure when rendering fails.
  Generated deterministically from the spec — no LLM call, cannot hallucinate,
  cannot drift from what is drawn. This closes **B13.9**.
- **Pin (📌)** — a long dialogue scrolls the diagram off the top of the screen
  exactly when you need it to answer. Pinning holds one aid in a sticky rail.
- **Enlarge (⤢)** — lightbox with Esc, focus return, and *all* layers shown; it
  is a reference view, not the dialogue.
- **Failure** — a failed fetch, unknown kind or malformed spec degrades to
  "Diagram unavailable — here it is in words:" plus the description. **There is no
  broken-image state.** Aids are strictly additive.
- **Print** — every layer shown, all controls hidden. A printed worksheet with an
  invisible answer layer is just a broken diagram.
- Responsive, `prefers-reduced-motion`, and `forced-colors` handled.

### Security boundary

The server preserves `<` and `>` in labels — stripping them turned `2 < x < 5`
into `2 x 5`, destroying the inequality a number line exists to teach. The
escaping obligation therefore moves to the render boundary and is absolute:
**aids.js builds every label with `textContent`, never `innerHTML`.** Colour is an
enum, never a CSS value from the model. `image.src` accepts only `data:` or
same-origin paths — an offline-first tutor must not silently fetch from the
internet.

---

## 9. What is NOT verified

**No live model has ever been asked to draw one of these.** Everything here is
verified against hand-written and adversarial specs, plus a Chromium render of all
12 kinds in both themes. The emission rate — *does qwen3.5:9b actually reach for a
diagram, and is its JSON valid?* — is **unmeasured**.

Run `tools/aid_probe.py` against a live Ollama to find out. It reports, per kind:
emission rate, validity rate, whether the right kind was chosen, and whether raw
JSON ever leaked into the chat (which must be 0).

```bash
python3 tools/aid_probe.py --repeat 3            # the number that matters
python3 tools/aid_probe.py --kind geometry       # the hard one
python3 tools/aid_probe.py --mode tool           # tool-calling vs the fence
```

**The probe measures expression, not correctness.** A triangle can be valid JSON,
pass every check, and still not be right-angled — rail 5 catches that one specific
lie, and nothing catches the rest. Read a high score as "the model can express
itself", never as "the diagrams are correct". Judging correctness needs a human or
a vision pass over the rendered figure.

Expected shape of the result: `steps`/`table`/`bars`/`graph` should score well;
`geometry` is the one to watch, and the right response to a bad geometry score is
to retire that kind from runtime authoring and pre-generate it at course-build
time — not to abandon the feature.

---

## 10. Two defects found along the way

**1. The entire B14 tool layer is dead in the container.** No requirements file
declares `sympy`, `numpy`, `matplotlib`, `networkx`, `scipy`, `pint`,
`periodictable`, `spacy` or `textstat`, yet 21 of the 25 registered tools import
them. Because the executor is deliberately no-raise and the imports are lazy, each
returns `{"ok": false, "error": "No module named …"}` rather than failing loudly.
Masked today only because `HELGA_ENABLE_TUTOR_TOOLS` defaults off — flip it on and
B14 fails closed while the build tree marks B14.5 ✅ "guarded lazy deps".

Same shape as the research-service Dockerfile bug: the static check added there
compares *local* imports against Dockerfile COPY; nothing compares *third-party*
imports against requirements.txt. `sympy` is now declared (needed by
`visualize_function`); the heavy cluster is left as a deliberate decision.

**2. `repair_json()` only fixed single-quoted keys, not values.** `['Mon','Tue']`
still failed to parse. It has been rewritten as an **escalating** repair that
returns the first candidate which actually parses, rather than applying every
transform blindly and letting a late blunt pass damage what an early gentle pass
had fixed. It now also handles markdown fences, curly quotes, `//` and `/* */`
comments, unquoted keys (claimed in the old docstring, never implemented),
NaN/Infinity, and single quotes inside arrays — via a character scanner that
tracks string state, because a global `'`→`"` substitution destroys apostrophes
("Newton's law").

Optional backends, in preference order: `fast-json-repair` (Rust/PyO3, MIT, wheels
for linux+macOS ARM64, needs Python 3.11+ which every service image uses),
`json-repair` (pure Python, the one that must always resolve), `json5`, and
`ast.literal_eval`. All lazily imported — repair works without any of them.

Recovery went from 9/16 to **16/16** on a malformed-JSON battery, with all 153
existing tests across the three dependent suites still passing.

---

## 11. When a diagram appears (B13.11)

`services/common/aid_policy.py` — deterministic, no LLM call. At ~30 s per call,
asking the model whether it would like to draw would cost more than the drawing
saves, so this is a pure function of the moment, like `_detect_ignorance`.

**Three outcomes, not two:**

| Verdict | Meaning |
|---|---|
| `none` | most turns |
| `reuse` | a diagram built at COURSE-CREATION time fits this moment |
| `generate` | nothing precomputed fits; let the model draw |

`reuse` is the preferred path. Course build already knows the concept and its
misconceptions, and a retry there costs nothing — so the hard diagrams
(geometry above all) are drawn, validated and regenerated against a named
failure at build time, exactly as the depth contract already works. Runtime then
**selects** rather than authors: no JSON-reliability risk, no latency, no
variance. `generate` is the lower-trust fallback for what a build cannot
anticipate.

**Enforcement is at prompt construction, not output rejection.** On a `none` or
`reuse` turn the aid grammar is left out of the prompt entirely — a model that
was never taught the syntax cannot emit a diagram, and ~590 tokens are saved on
every quiet turn.

**Restraint mechanisms** (Mayer's coherence principle: extraneous visuals reduce
learning, they do not merely fail to help):

- **Cooldown** — no diagram within 2 turns of the last, *unless* the learner is
  stuck. Stuck overrides, because that is exactly when waiting helps nobody.
- **Per-concept budget** — 3 diagrams, 4 for K-2/3-5. The multimedia effect is
  strongest for novices.
- **Repeat suppression** — aid ids are content hashes, so an identical figure
  re-emitted collapses to the same id and is dropped; the original card is still
  on screen.
- **Expertise reversal** — Bloom ≥ 4 scores *negative*. A diagram that helps a
  novice can hinder someone already fluent.
- **Don't interrupt** — a correct streak scores negative.

**Triggers**: LECTURE mode is the strongest signal (+4) — it fires when the
student said "I don't know", so prose has already been tried and failed.
Then repeated misses (+3), concept opening (+3), a visually-routed subject (+2).

**Subject routing** narrows the menu from eleven kinds to two or three, which
measurably helps a 9B model choose. Keyword-based, not an LLM call; a mis-route
is cheap because the model may still pick otherwise.

---

## 12. Photographs (B13.5)

A concept map beats a photo of a leaf for photosynthesis. But where the
*particular thing* is the content — a Vermeer, a basalt column, Saturn — a
diagram would be a lie.

**Licence: fail closed.** An unknown licence is a rejected licence. PD, CC0,
CC BY and CC BY-SA are accepted; NC and ND are refused, because a teacher
printing a worksheet is a derivative use and the restriction follows the image
there. Attribution is captured at fetch time and travels with the bytes.

**Fetch online, serve local.** Images are downloaded once at build time into
`DATA_ROOT/media` and referenced same-origin. Being online makes courses
*richer*; being offline never makes them *broken*. This also satisfies the
`image` kind's same-origin rule, which refuses remote hosts by design.

Sources, routed not global: Wikimedia Commons (`*` — the only true generalist,
and the only one returning per-file licence metadata), Met + Art Institute
(art), Library of Congress (history), NASA (science/geography).

Bytes are trusted over headers (magic-byte sniffing), SVG is excluded as
executable markup, oversize is caught mid-stream because servers lie about
Content-Length, and filenames are whitelisted against traversal before the
serving route touches disk.

**Not verified live** — this sandbox's proxy blocks the archives, so the fetch
path is tested against mocked responses only.

---

## 13. Phase 3 — Asset Collection

```
Phase 1  what should this course contain?   curriculum_research
Phase 2  what does each concept say?        ContentHydrator
Phase 3  what does the learner LOOK at?     asset_collector    <- new
```

Runs after hydration and after the depth/fact/level/grounding verdicts, and
**before the course is enterable**. Every diagram the course will use is drawn
here; a session only selects from them.

**Why its own phase, not part of hydration.** It needs the finished text. Only a
whole-course pass can see that eight concepts all want the same water cycle —
per-concept hydration structurally cannot dedupe across concepts, and duplicate
figures are the most visible way this feature could go wrong. Its failure
semantics differ (no pictures is degradable; no content is not), its resource
profile differs (part LLM, part network, and the network half overlaps), and it
is re-runnable alone.

**Why build time.** A retry costs nothing here, so `geometry` — the kind a 9B
model is least reliable at — is generated, validated, and regenerated against
the *named* validation failure. Generation is also grammar-constrained
(Ollama `format`), which an inline fence in mid-prose can never be.

### The course is now locked until the build finishes

`COURSE_AVAILABLE`-after-one-concept has been removed. It marked a course
enterable before *any* verification had run — depth contract, fact check, level
calibration, grounding and coverage all happen after hydration. "One concept
exists" was never the same claim as "ready", and shipping it as one is precisely
the structurally-clean-but-hollow failure this pipeline exists to prevent.

### Wait time

The cost is real now, so every lever is about doing less work, not deferring it:

| Lever | Effect |
|---|---|
| **Skip** | a concept routing to no visual kind makes **no LLM call at all** |
| **Reuse (course)** | duplicate subjects in one course are drawn once |
| **Reuse (machine)** | a shared library means "the water cycle" is drawn once, ever — a second course in a related subject can cost nothing |
| **One call** | all of a concept's slots in a single constrained request |
| **Overlap** | image downloads run in a thread pool while the LLM works; Ollama is serialised on one GPU, network I/O is not |
| **Budget** | a hard course-wide cap so a 120-concept course cannot become a two-hour build |

Measured on a 4-concept fixture: **2 LLM calls** — one concept abstract (skipped),
one a duplicate subject (reused). A second course sharing a concept: **0 calls**.

### Session start

Phase 3 writes `assets.json` beside the course. The FSM reads it once on
SET_CONTEXT / RESUME_COURSE / course switch, so a session learns its coverage in
one read instead of parsing every concept's markdown; the per-concept parse
still happens lazily on entry. A course built before Phase 3 has no manifest and
simply falls back to the `generate` path — the pre-existing behaviour.

### Restraint at session scale

Per-concept budget and cooldown are not enough: eight concepts × 3 diagrams is
24, every one individually justified and collectively a slideshow. So the policy
also carries a **session cap (10)** and **kind variety** — a kind shown in the
last two aids scores negative, because three number lines in a row is how a
diagram stops being looked at.

---

## 14. Book mode — EPUB and PDF

When a course is built from an uploaded book, **the book's own figures are the
only images it uses.** An author drew that diagram for that explanation; a stock
photo fetched from an archive is at best a coincidence and at worst contradicts
the text. External archives are therefore *hard-disabled* in book mode — not
deprioritised, and not overridable by env.

### PDF was advertised and unimplemented

`/library` accepted `.pdf` in its file input and MIME whitelist while
`document_extract.extract()` raised `UnsupportedDocument` for it — the same "we
appear to read your material and do not" bug that module was written to fix, one
layer up. PDF now reads through **pypdf** (BSD-3-Clause, pure Python: no AGPL
entanglement unlike PyMuPDF, no native toolchain). Text comes back with
`[[page:N]]` markers so a figure can be tied to the prose describing it.

A scanned PDF with no text layer still raises, and the error names OCR as the
fix — returning a plausible empty string is the failure being avoided.

### Extract, then REVIEW

Extraction is the easy half. **Most images embedded in a book are not figures**:
publisher colophons, chapter rules, bullet glyphs, the same logo on 600 pages.
Passing those to a learner is worse than having no images, because each one
occupies the slot a real figure would have had.

| Rejected for | Signal |
|---|---|
| page furniture | identical bytes appearing more than 3 times — needs the whole document to see |
| rule or border | aspect ratio beyond 6:1 |
| icon or glyph | below 120×100 |
| unusable | unrecognised format (SVG excluded — executable markup from an untrusted upload) |

Every rejection carries a reason, because a silent filter cannot be tuned: if a
book yields no figures, the reasons are the only way to tell whether that
verdict was right.

> **Dimensions decide, not file size.** A byte threshold looked reasonable and
> was actively wrong: a textbook figure is clean line art, which is the best
> case for PNG compression, so a real 520×400 diagram came in at 2,939 bytes and
> was filtered as "too small" while a decorative 200×200 gradient at 40 KB
> survived. Filtering on bytes rejects the best figures and keeps the ornaments.
> Bytes are now used only when dimensions cannot be read.

### Captions are free alt-text

`<figcaption>`, an `alt` attribute, or "Figure 3.2:" in the page text gives an
accurate title and description in the author's own words — no LLM call, no
hallucination risk. A caption is also the strongest positive signal that an
image is a figure at all.

### Copyright

An uploaded book is very likely in copyright. Figures are scoped to the course
built from that book, marked with the book as their source, and — enforced, not
merely documented — **never written into the shared cross-course library**.
Letting one user's textbook leak its plates into an unrelated course would be a
genuine wrong, not a tidiness issue.

Attribution also had to be made to survive persistence: `render_concept_aids`
wrote only the provenance *tier*, silently dropping `source`, `license` and
`url`, so a figure came back attributed to nobody. Attribution that does not
survive a round-trip is not attribution.

### Test cases

Replaced the private 1,325-page file with **public books**, both chosen for
having real captioned figures:

- **PDF** — an OpenStax textbook (*Astronomy 2e*, *Biology 2e*). CC BY 4.0, so
  it is legally clean to extract from and the figures stay usable downstream.
- **EPUB** — a Project Gutenberg illustrated title (*On the Origin of Species*,
  Gray's *Anatomy*). Public domain, genuine captioned plates.

`tests/fixtures/make_book_fixtures.py` generates small offline stand-ins
containing both real figures **and** the page furniture that must be rejected,
so the suite never needs the network.

---

## 15. Files

| File | Role |
|---|---|
| `services/common/visual_aids.py` | Spec model, validation, rails, descriptions, `AidStore` |
| `services/common/tutor_tools.py` | `show_visual`, `visualize_function`, `visualize_data` |
| `services/common/prompts.py` | `VISUAL_AID_RULES` + few-shot examples, flag-gated |
| `services/common/llm_utils.py` | Upgraded `repair_json()` |
| `services/core/fsm_logic.py` | Extraction in `add_message`, `REVEAL_AID`, `/api/aid/<id>` |
| `services/web-ui/app.py` | `/api/aid/<id>` proxy |
| `services/web-ui/static/js/aids.js` | 12 SVG renderers, card, lightbox, pin rail |
| `services/web-ui/static/css/aids.css` | Theme-aware colour slots, print, a11y |
| `services/web-ui/static/js/session.js` | Attaches aids above message text; feeds TTS |
| `tools/aid_probe.py` | Measures whether the live model can actually draw |
| `services/common/aid_policy.py` | When a diagram appears; select-then-generate |
| `services/research/image_sources.py` | Commons/Met/AIC/LoC/NASA + licence filter |
| `services/common/media_cache.py` | Download once at build time, serve same-origin |
| `tests/core/test_visual_aids.py` | 66 tests |
| `services/core/asset_collector.py` | Phase 3 — plan, draw, fetch, dedupe, manifest |
| `tests/core/test_aid_policy.py` | 32 tests (policy, licence, media cache) |
| `services/common/document_figures.py` | Extract + review figures from EPUB/PDF |
| `services/common/document_extract.py` | PDF text extraction (pypdf) with page markers |
| `tests/fixtures/make_book_fixtures.py` | Offline EPUB/PDF fixtures with real figures + furniture |
| `tests/core/test_asset_collector.py` | 24 tests (skip, reuse, retry, budget, library, book mode) |
| `tests/core/test_document_figures.py` | 15 tests (captions, review, PDF text) |

`HELGA_ENABLE_VISUAL_AIDS` (default **on**) gates the whole feature, including
whether the prompt grammar is spent at all. It is independent of
`HELGA_ENABLE_TUTOR_TOOLS` on purpose: the inline fence needs no tool-calling
support, so aids can ship while B14 reliability is still being validated.
