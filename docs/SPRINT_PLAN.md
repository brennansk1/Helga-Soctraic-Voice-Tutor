# Helga — Consolidated Sprint Plan to Ship Quality

> **Written 2026-08-02.** Supersedes ad-hoc planning. Sits *above* `BUILD_MANIFEST.md`
> (which decomposes the K-12 platform branches B15–B27) and `HELGA_BUILD_TREE.md`
> (baseline appliance Tiers A–E). This document adds: the two-mode product definition,
> the verified state of the tree, the DeepTutor integration decisions, and the
> QA/independent-review machinery that gates every sprint.

---

## 0. Verified state as of 2026-08-02

Everything in this section was checked against running code or live services today, not read from docs.

**Repository consolidated.** `main` was `b1aa72e` (April) and is now `2be3f30`. The June/July work
was stranded on two worktree branches; `claude/hopeful-jackson-bc8db4` turned out to be a strict
*ancestor* of `claude/keen-leavitt-d9bc3c`, so consolidation was a clean fast-forward with **zero
conflicts** (an automated review claimed "HIGH conflict, ~72 shared files" — that was wrong, and
the ancestry check disproved it).

| Signal | Before | After |
|---|---|---|
| Tests passing | 428 | **798** (9 fail, 3 skip) |
| `main` commits behind | 51 | 0 |
| Services | 6 | **8** (`+exam/`, `+stt/`) |
| Design docs on main | 2 | **14** (`docs/design/` 01–11, `legal/`) |
| LLM calls | **all failed** | working |

**The LLM blocker is resolved.** Old main hardcoded `qwen2.5:14b` and `llm_utils.py` read an env
var (`LLM_MODEL`) that nothing ever set — so course generation permanently requested a model that
isn't pulled. New main unifies on `${OLLAMA_MODEL:-qwen3.5:9b}` (`llm_utils.py:191`), and
`qwen3.5:9b` is present and responding. Verified with a live `/api/chat` call.

**Remaining 9 test failures are environmental**, not logic: 8 Playwright e2e tests need the stack
running, plus `test_llm_functional`. The one real prior defect (a fuzzing assertion that didn't
allow Flask's 308) is fixed.

**Known-good, verified:** FSM with 8 states and all 9 documented events; Socratic grading is real
rubric-based LLM grading; Kokoro TTS; FTS5 search (ranked, searches body — no longer substring);
`services/exam/exam_engine.py` (653 lines, no stubs); `services/stt/stt_server.py` (Nemotron via
MLX); `tutor_tools.py` (~25 tools, flag-gated **off** via `HELGA_ENABLE_TUTOR_TOOLS` /
`HELGA_ENABLE_CODE_TOOL`).

**Known-bad, verified:**
- `TOGGLE_TTS` / `TOGGLE_TEXT_ONLY` are **no-ops** (`fsm_logic.py:785` returns `True` and does
  nothing) while the UI presents them as working controls.
- `/api/profile/reset` exists in `librarian.py` but has **no web-ui proxy** — the Settings "Reset
  Progress" button 404s.
- `source_confidence` is computed and displayed but **nothing acts on it**. In the one existing
  course, **24 of 36 concepts score below 0.5** and ship anyway.
- `source_confidence` is inert *despite* the content being grounded — see the correction below.
- Hybrid dense retrieval **degrades silently** to FTS5 when deps are missing — a silent quality
  cliff with no operator signal.

**Course artifact quality (n=1, `course_10e8a4de` "causal inference"):** 36 concepts, 6 modules,
zero stubs, 626–876 words each, Bloom ramps correctly 1→2→3→3→4→5. Prose is genuinely good —
real technical content with prerequisites and mastery rubrics. Two defects: mild domain drift in the
weakest concept (renders "causal pathway" in epidemiology framing), and **7 of 21 lessons have ≤1
concept** — a third of the tree is degenerate scaffolding.

> **Evidence gap that matters:** there is exactly **one** generated course on disk, with
> `teaching_style=""`. We have no empirical basis for claiming quality across settings. Sprint A0's
> golden-course harness (`tools/golden_courses.py`) exists primarily to create that basis.

> ### Correction — grounding is REAL, and better than first reported
> An earlier automated pass reported "zero citations — purely parametric generation," and that
> claim was propagated into this plan before being checked. **It is wrong.** Measured directly:
> **35 of 36 concepts carry a `## Sources` block with 69 unique resolvable URLs**, tier-labelled by
> provenance (70× Tier 1, 6× Tier 2, 1× Tier 3) — PMC articles, university methods pages, and
> reference works. The research service + SearXNG grounding pipeline works.
>
> Two consequences: (1) A2 is **not** "build citations from nothing" — it is closing the last gap
> (one ungrounded concept, notably the one with `source_confidence` 0.0) and making confidence
> load-bearing; (2) the DeepTutor citation-grounding lift in §3 drops in priority accordingly.
>
> The real defect is narrower and still serious: **`source_confidence` is computed, displayed, and
> ignored.** 24 of 36 concepts score below 0.5 and ship with no gate and no user-visible marker.
>
> Filed here rather than quietly edited, because the failure mode — accepting a delegated finding
> without verification — is the exact thing §5 exists to prevent.

---

## 1. Product definition — two modes

Confirmed direction: **one engine, two modes.** These share the generation pipeline, FSM, retrieval,
and scheduler, and differ in constraints, framing, and compliance surface.

### Mode A — Scholar (personal / self-directed)
An adult or independent learner asks for a subject and gets a course *at the genuine depth and rigor
of that kind of course* — a real graduate-level treatment of causal inference should feel like one,
not like a generic 700-word-per-concept flattening.

The current three-slider system (`scope` / `mastery` / `starting_from`, `course_builder.py:214`)
is the right control surface, and domain-specific skeleton templates already exist (STEM / history /
theory variants). **What's missing is a depth contract**: nothing verifies the output actually
matches the requested rigor. Every concept in the sample course landed in a 626–876 word band
regardless of its Bloom level or position — the pipeline produces *uniform* output where it should
produce *escalating* output.

### Mode B — Utah K-12
Grade-banded (K-2 / 3-5 / 6-8 / 9-12), standards-aligned, parent-supervised, compliance-bound.
This is fully specified across `docs/design/01–11` and decomposed in `BUILD_MANIFEST.md` as R0–R4.
The design is implementation-ready; the code is substantially behind it.

**Compliance is a hard gate, not a polish item** (`docs/design/08`): verifiable parental consent at
signup and per-child; full data export + deletion rights; **all inference stays self-hosted** (no
third-party LLM may see minor data); Health Strand 6 locked by default pending explicit consent;
output moderation with crisis-resource surfacing that alerts a parent *without* transmitting the
sensitive transcript. Nothing in Mode B ships to a real child until these are implemented and tested.

### Platform decision: **Apple-native first** (2026-08-02)

**v1 targets macOS on Apple Silicon natively.** Cross-platform portability is
deferred, not abandoned — but it stops being a constraint on v1 decisions.

This is driven by measurement, not preference. The target box is a Mac Mini M4 Pro
with 24 GB unified memory, and it was found **already swapping 10.3 GB of 11.3 GB**
with Docker down and only a browser open. The stack cannot afford to pay a
portability tax in RAM on hardware that is already over-subscribed, especially
since the user runs other software on the same machine.

**What "Apple-native first" licenses us to do:**

| Area | Portable-first (old) | Apple-native first (v1) |
|---|---|---|
| Inference | Ollama/llama.cpp, GGUF | **MLX** models directly (e.g. ternary 27B at ~7 GB) |
| Embeddings | sentence-transformers + PyTorch | **Ollama `/api/embed`** — no PyTorch at all |
| TTS | Kokoro in a 2 GB container | Kokoro lazy-loaded; **`AVSpeechSynthesis` as a 0-RAM fallback** |
| STT | faster-whisper in a container | **Nemotron ASR via MLX**, host-native |
| Services | six always-on containers | host-native + on-demand; unified memory is shared, not partitioned |

**What this buys:** unified memory means a host-native process shares the model
with everything else instead of reserving a container slice. Dropping the
PyTorch dependency chain (done — see `services/common/embeddings.py`) removes
hundreds of MB and a recurring source of version conflicts. MLX quantisations
(1.7-bit ternary) have no GGUF equivalent, so portability was costing us the
best memory/quality trade available.

**What we explicitly accept:** v1 will not run on Linux or Jetson without work.
Docker Compose remains the reference topology for a future portable release, and
nothing here is allowed to hard-code macOS paths into shared logic — platform
specifics belong behind a seam (`memory_guard`, `embeddings`, the TTS/STT
adapters), not scattered through `fsm_logic` or `course_builder`.

**What this does NOT change:** the K-12 compliance posture. All inference stays
self-hosted regardless of platform; "Apple-native" is about *where* the model
runs, never about sending student data anywhere.

### The shared-core rule
Any feature built for one mode must be built in the shared core with the mode as a *parameter*, not
forked. Grade-band bounding of Bloom is the model to follow: one algorithm, a profile table
(`GRADE_BAND_PROFILES`), and Mode A simply selects an "adult/unbounded" profile.

---

## 2. Feature matrix

Status verified against code today. "Design-only" means a spec exists in `docs/design/` with no
implementing code found.

### Core engine
| Feature | Status | Notes |
|---|---|---|
| FSM, 8 states, 9 events | **Done** | All CLAUDE.md-claimed events real |
| Course generation (skeleton→hydration) | **Done** | Produces good prose; depth contract missing |
| Three-slider params | **Done** | `scope`/`mastery`/`starting_from` genuinely wired |
| Bloom progression + grade bounding | **Done** | Verified ramp 1→2→3→3→4→5 |
| Socratic rubric grading | **Done** | Real LLM grading + ignorance detector |
| FSRS scheduling | **Done** | Direct FSRS-5 implementation |
| Kokoro TTS | **Done** | |
| STT (Nemotron/MLX) | **Done** | Host-native :5001; reintroduces voice input |
| Exam engine + interest theming | **Done** | Two-layer validity guard |
| Tutor tools (~25) | **Built, disabled** | Flag-gated off pending reliability validation |
| FTS5 lexical search | **Done** | |
| Hybrid dense retrieval | **Partial** | Opt-in `?mode=hybrid`, silent degradation |
| Citations / grounding | **Done** | 35/36 concepts have a `## Sources` block; 69 unique URLs, tiered |
| `source_confidence` acted upon | **Missing** | Computed, shown, ignored; 24/36 below 0.5 ship anyway |
| Reasoning-mode handling | **Fixed 2026-08-02** | `reasoning_effort:"none"`; was emptying every response |
| Depth/rigor verification | **Missing** | No check output matches requested level |
| TTS/text-only toggles | **Broken** | No-ops |
| `/api/profile/reset` proxy | **Broken** | 404 |

### K-12 platform (Mode B)
Audited against code, not against the manifest's own claims.

| Feature | Status | Evidence |
|---|---|---|
| Data model v4–v9, multi-tenant schema | **Done** | `storage.py:100` `_init_db`, migrations to v9 |
| Auto-migration v1→v9 on open | **Done** | `storage.py:380–579`; stale v3 dev DBs self-upgrade |
| Per-student FSM registry (kills global singleton) | **Done** | `services/core/fsm_registry.py` |
| Auth: parent/student + PIN roles | **Done** | `services/web-ui/auth.py` |
| Socket.IO per-student room scoping | **Done** | `app.py:273` `join_room(f"student:{sid}")` |
| Exam engine + interest theming | **Done** | `services/exam/exam_engine.py` |
| Parent dashboard + elective approval | **Done** | `parent_api.py`, `templates/parent/*` |
| COPPA/FERPA rights + audit trail | **Done** | `parent_api.py:403`, `storage.py:2377` |
| Safety moderation gating | **Done** | `safety.py:71` → `fsm_logic.py:895` |
| GPU fair-queue | **Done** | `services/core/gpu_gate.py` |
| xAPI, `/metrics`, structured logs | **Done** | `services/common/xapi.py` |
| Standards loader | **Built** | `services/common/standards_loader.py` |
| **Utah standards CONTENT** | **🔴 ABSENT** | `standards` table = **0 rows**; `data/standards/` does not exist; `catalog/` empty |
| Read-only catalog store (B16.2) | **Not started** | `data/catalog/` does not exist (docs claim done) |
| Stripe Checkout + portal (B20.1) | **Not started** | webhook/seats exist (`app.py:1467`); no Checkout |
| Gamification skill-tree UI | **Partial** | tables exist; UI missing |
| Crisis detection + parent alerting | **Partial** | alerts exist (`common/alerts.py`); crisis path unverified |

> ### 🔴 The finding that reorders this plan
> **Mode B has a complete machine and no fuel.** Schema, loader, exam engine, parent dashboard,
> compliance code, and grade-band adaptation are all genuinely built and verified. But the
> `standards` table holds **zero rows**, the `data/standards/` seed directory the loader reads
> from **does not exist**, and `catalog/` is **empty**. `BUILD_MANIFEST.md` marks B16.1/B16.2 as
> done; the code disagrees.
>
> Consequence: **Mode B cannot teach a single standards-aligned lesson today.** No amount of
> gamification, billing, or UX work changes that. Curriculum content is therefore promoted out of
> Arc III entirely — and, per the 2026-08-02 sequencing decision, parked with Mode B (see §4.9).
>
> This is also the clearest possible vindication of the review discipline in §5: a plan built on
> the manifest's self-reported status would have sequenced months of work on top of an empty table.

---

## 3. What we take from DeepTutor

Reviewed https://github.com/HKUDS/DeepTutor (HKU Data Science Lab) — **32.0k stars, Apache 2.0,
actively developed (v1.5.8, 2026-08-02)**. Paper: *"DeepTutor: Towards Agentic Personalized
Tutoring"* (Zhao, Zhang, Ren, Guo, Chu, Ma, Huang), arXiv 2604.26962. Verified directly, not
second-hand.

Its abstract states it "unifies citation-grounded problem tutoring with difficulty-calibrated
question generation" via "a hybrid personalization engine [coupling] static knowledge grounding with
dynamic learner memory," reporting **+10.8% on personalized metrics and +29.4% on agentic reasoning
across five backbone models**. Apache 2.0 means we may port code with attribution.

### Adopt (high value, fits our constraints)

**1. Citation-grounded generation — A2.** This is the single most important lift. DeepTutor's core
claim is that tutoring output is *grounded in retrievable sources with citations*. It directly fixes
our worst honesty defect: content labelled `research+llm` with zero references. Adopting this makes
`source_confidence` meaningful for the first time — a concept with no retrievable support should be
*visibly* ungrounded, or regenerated, not silently shipped.

**2. TutorBench-style evaluation — A0 and every sprint after.** The paper introduces **TutorBench**,
"an interactive benchmark incorporating customized learner profiles grounded in university-level
curricula across five domains," evaluated by "an LLM-based first-person interactive evaluation
protocol that conducts assessments via a profile-driven student simulator."

This is exactly the independent QA mechanism this plan needs. We build **HelgaBench**: a set of
simulated student profiles (a confused 4th-grader, a fast 9th-grader, an adult with a specific
misconception, a student who bluffs confidently) driven by a *second, different model* against our
tutor. It converts "does the tutoring feel good?" from opinion into a tracked number, and it is the
only credible way to detect pedagogy regressions. **We cannot ship a quality bar we cannot measure.**

**3. Difficulty-calibrated question generation — S3.** Our exam engine themes questions to interests
and guards validity, but does not calibrate difficulty to a learner model. Their approach informs
tying item difficulty to observed mastery.

**4. Layered learner memory — S3.** A trace → curated-facts → synthesis memory hierarchy is a cheap,
high-value pattern for personalization that survives context limits. *Caveat: the specific L1/L2/L3
structure was reported by an automated pass and I could not confirm it in the paper abstract or
repo tree — treat the layering as a design idea to evaluate, not a verified spec.*

### Reject (poor fit)
- **MinerU / Docling / GraphRAG** — heavy multimodal parsing and graph construction; wrong economics
  for a 9B local model on a Mac Mini. Our EPUB/PDF path should stay lightweight.
- **Multi-user IM channels (Matrix/Zulip/Discord)** and the full Next.js book renderer (Manim,
  GeoGebra) — out of scope for an offline appliance.
- **Their multi-tenancy** — we already have a Utah-specific design; don't import a second model.

---

## 4. Sprint plan

> **Sequencing decision (2026-08-02): Mode A ships first, complete. Mode B is parked.**
> Curriculum sourcing is a licensing/content problem with lead time that engineering cannot
> compress, and it gates nothing in Mode A. So we finish the Scholar product end to end — every
> advertised capability reachable and honest — before resuming K-12. See §4.9 for what's parked
> and why none of it rots while it waits.

Eight sprints. Each has an explicit **exit gate** — objective, checkable, and verified by an
independent reviewer who did not do the work (see §5). **A sprint does not end because work
finished; it ends because the gate passed.**

### Definition of done for Mode A
Mode A is finished when a self-directed adult can, without hitting a dead end:

1. Ask for any subject and get a course **at the genuine depth requested** — a graduate-level ask
   produces graduate-level material, not a uniform 700-words-per-concept flattening.
2. Learn it Socratically, by **voice or text**, with tutoring that adapts to their demonstrated level.
3. See **where the content came from** — real citations, and honest signalling when something is
   thinly sourced.
4. Be **reviewed on schedule** via FSRS, with review tied to what they actually struggled with.
5. Use **all three advertised learning modes** — Socratic, Spaced Repetition, and Memory Palace.
6. **Bring their own material** — upload an EPUB/PDF and have a course built from it.
7. Trust that **every control does what it says it does.**

Today, items 1, 3, 5, and 6 fail outright, and 7 is violated by known no-op toggles.

### Arc I — Make it true (A0–A2)
Stop shipping claims we can't back. Nothing new gets built on unverified ground.

**A0 — Ground truth & harness** *(no features)*
- Audit every "landed/partial" row in §2 to DONE/NOT-DONE with file:line evidence.
- Stand up the stack; convert the 8 environmental e2e failures into genuine pass/fail.
- Build **HelgaBench v0**: ≥6 student-simulator profiles, ≥3 subjects, run by a second model;
  record baseline scores for tutoring quality, grading accuracy, and Socratic adherence.
- Build **golden-course eval**: generate 6 courses across the slider space (scope×mastery×
  starting_from) and 2 grade bands. This creates the missing evidence base.
- *Gate:* baseline numbers exist and are reproducible; §2 has no "needs verification" rows.

**A1 — Course depth contract** *(the heart of Mode A)*

> **Stated requirement:** setting a course to college level today does **not** produce something
> with the rigor of a real college course. The slider label is a promise the output doesn't keep.
> A1 is not done until that gap is closed and *enforced* — not nudged via prompt wording.

The measured evidence agrees with that experience. In the sample course every concept landed
between **626 and 876 words (stdev 57.7)** regardless of Bloom level, module position, or
requested mastery. That flat band is the signature of a pipeline producing a **house style**
rather than a level. A graduate treatment and a primer come out the same size and shape.

Part of the cause is now fixed: with reasoning enabled, structured-generation calls at 400–800
tokens returned **empty** and fell back to generic scaffolding. But a token fix alone will not
produce college rigor — the contract has to be specified and enforced.

- **Specify the contract per (scope, mastery) cell** as machine-checkable targets, not adjectives:
  concepts per lesson, word floor *and* required structural elements — worked examples, derivations
  or proofs for STEM, formal definitions, non-trivial problem sets, primary-literature citations,
  and an explicit prerequisite chain depth. A college-level cell should *require* e.g. formal
  notation, a derivation, and a primary source — and be rejected without them.
- **Enforce post-generation with real teeth:** validate every concept against its cell's contract;
  on miss, regenerate with the specific deficiency named; after N failures mark the concept (and
  the course) as failing its level rather than silently shipping it. **A course that cannot meet
  its contract must not be labelled with that level.**
- **Calibrate against reality:** for at least three subjects, compare the generated syllabus
  against an actual published university syllabus for the same course. This is the only honest
  check on "is this really college level."
- Fix degenerate structure (7/21 lessons with ≤1 concept — the harness gates on >20%).
- Fix domain drift (the epidemiology-framing bug in the weakest concept).
- *Gate:* **§4.10, the course quality gate, passes on every course in the golden matrix** — all
  six criteria, not a subset. Plus depth must **respond to the sliders**: scope=5/mastery=5
  produces measurably more and deeper material than scope=2/mastery=2 on the *same topic*
  (word stdev across levels well above the current 57.7; required elements present at high
  levels and absent at low). Plus a blind independent reviewer, shown a generated
  college-level course and a real one, cannot dismiss the generated one as obviously not
  college-level.
- *Precondition:* generate at least one course end-to-end with enforcement ON and measure it.
  Nothing in this sprint may be called done on the strength of code that has never produced
  a measured artifact.

**A2 — Make grounding load-bearing** *(re-scoped — citations already exist)*
- **Make `source_confidence` act.** It is currently computed, displayed, and ignored: 24 of 36
  concepts score below 0.5 and ship unmarked. Below the floor → regenerate with broader retrieval,
  and if it still fails, surface "limited sources" to the learner rather than hiding it.
- Close the coverage gap: 1 of 36 concepts has no `## Sources` block at all.
- Raise citation *quality*, not just presence — prefer primary literature over reference works for
  high-mastery cells (currently 70 Tier-1 vs 1 Tier-3), and tie this to the A1 contract.
- Complete hybrid retrieval; **remove silent degradation** — if dense retrieval is unavailable the
  system must say so loudly rather than quietly serving lexical-only results.
- *Gate:* 100% of concepts in golden courses carry ≥1 resolvable source; zero concepts ship below
  the confidence floor without a visible marker; hybrid degradation is loud; HelgaBench grounding
  score beats the A0 baseline.

### Arc II — Make it whole (A3–A5)
Close the gap between what Helga advertises and what a user can actually reach.

**A3 — Complete the advertised surface** *(the literal "finish personal mode" sprint)*
Three of the seven done-criteria fail because a feature exists in the backend with no way in:
- **Memory Palace UI.** The FSM has the state (`fsm_logic.py:957`, `:2707`) but **no template
  exists** — one of three advertised learning modes is unreachable. Build it, or make a deliberate
  decision to drop it from the product and stop advertising it. Do not leave it half-present.
- **EPUB/PDF ingestion UI.** `/api/upload_epub` is implemented (`app.py:686`) with **zero frontend
  references**. "Bring your own material" is arguably *the* personal-mode feature; wire the upload
  flow, progress feedback, and error states, and run the ingested text through the A1 depth contract.
- **Honest controls.** `TOGGLE_TTS` / `TOGGLE_TEXT_ONLY` are no-ops (`fsm_logic.py:785`);
  `/api/profile/reset` 404s for lack of a web-ui proxy. **No control may lie about its effect.**
- *Gate:* all seven Mode A done-criteria are reachable end to end by a user who was given no
  instructions; every interactive control has a verified effect; no advertised feature is
  unreachable.

**A4 — Pedagogy & personalization**
- Enable tutor tools (`HELGA_ENABLE_TUTOR_TOOLS`) behind a reliability gate — they're built and
  disabled; validate and turn on. Keep `run_python` off by default.
- Layered learner memory; difficulty-calibrated question generation; wire FSRS ↔ Socratic loop so
  review targets what the learner actually struggled with.
- *Gate:* HelgaBench personalization score up materially vs A0 baseline; no regression in grading
  accuracy; tool-call failure rate below an agreed threshold.

**A5 — UX polish & the learn loop**
- Reconcile the April orphaned UI work (`wip/april-2026-orphaned-work`: `learn-chat.css` 1138 lines,
  `progress-tree.js`, avatars) — cherry-pick what's still wanted onto the current trunk.
- Learn-tab path visualization, session continuity, transcript persistence across restarts.
- Accessibility pass.
- *Gate:* e2e suite green on a live stack; accessibility pass; no dead UI or orphaned handlers.

### Arc III — Make it last (A6–A7)

**A6 — Optimization pass** — see §6. *Gate:* p95 latency and course-generation-time targets met with
**no quality regression** on HelgaBench or the golden courses. Optimization that costs quality is
rejected, not traded off.

**A7 — Hardening & release**
- Ollama circuit breaker and graceful degradation (currently a hard external dependency with no
  fallback); backup/restore drill; observability.
- **Safety-lite:** even for consenting adults, keep crisis-resource surfacing and self-harm signal
  handling. This is the one piece of the parked compliance work that ships with Mode A.
- Fix `main.py:81`'s substring model preflight (false green).
- *Gate:* survives a soak test and a simulated Ollama outage with no data loss; adversarial safety
  review passes; a fresh install works from a clean checkout following only the README.

### 4.9 What is parked with Mode B — and why it won't rot

Parked: Utah curriculum sourcing and catalog (B16), COPPA/FERPA consent gating, parent dashboard,
Stripe Checkout, gamification skill-tree, kid-first IA, admin console frontend, Postgres/multi-worker
scale-out.

**None of this is deleted or reverted.** The multi-tenant schema, per-student FSM registry, auth,
Socket.IO room scoping, exam engine, GPU fair-queue, and compliance/audit primitives are already
built, tested, and remain on main — they simply run with a single default student in Mode A. That is
a deliberate advantage: Mode A exercises the multi-tenant paths continuously, so they stay alive
rather than bit-rotting behind a flag.

Two standing rules while parked:
1. **The shared-core rule still applies** (§1). Mode A work must not hard-code single-user
   assumptions that a later Mode B would have to unpick. Grade-band bounding stays a *profile
   selection*, not a removed feature.
2. **Curriculum sourcing has long lead time.** It is off the engineering schedule, not off the
   calendar — begin rights/sourcing conversations whenever convenient, since that clock runs
   independently of sprint velocity.

---

## 4.10 THE COURSE QUALITY GATE (hard, non-negotiable)

### What "college-level quality" means here — and what it does NOT mean

**Target: the Duolingo of a college course.** Helga delivers through a taxonomy of
modules → units → lessons → concepts, consumed in short interactive bites. It is
deliberately **not** organised like a traditional university course, and must not be
judged as if it were.

So the gate is about **equivalence of substance, not of format**:

| Must match a real college course | Must NOT be imported from one |
|---|---|
| Conceptual depth and rigor | Lecture-length exposition |
| Prerequisite chains and correct sequencing | 50-minute lecture blocks |
| Technical correctness and precision | Semester/credit-hour structure |
| Topic coverage of the real discipline | Long problem sets, term papers |
| Genuine worked reasoning, not just assertion | Textbook chapter pacing |
| Honest treatment of assumptions and failure modes | Formal assessment apparatus |

A concept is allowed to be short. It is **not** allowed to be shallow. The failure mode
we are gating against is not brevity — it is a well-written encyclopedia entry standing
in for teaching: prose that *describes* a technique without ever deriving it, working an
example, or engaging with when it breaks.

Two consequences for the instruments below:
- `level_audit` judges the sophistication of the *substance* and must not penalise the
  micro-learning format. Its prompt says so explicitly.
- Criterion 6 (syllabus realism) compares **coverage and sequencing** against a real
  syllabus. It must not compare format, pacing, or assessment structure — a mismatch
  there is the design working as intended.

**Every course Helga generates must be verified to be at the level it claims, before
the learner ever sees that label.** Not a sample. Not the golden matrix. Every course.
This gate blocks: a course that fails it may not be presented at its requested level.

### Why one measure is not enough — measured, not assumed

Two instruments disagree about the same course, and the disagreement is the whole point:

| Instrument | Measures | Result on `course_10e8a4de` (claims mastery 4) |
|---|---|---|
| `depth_contract.py` | pedagogical apparatus (form) | **0/36 pass** — 0 worked examples, 1 theorem, notation in 2 |
| `level_audit.py` | topical sophistication (substance), judge blind to claim | **3.83 vs 4.0** — calibrated |

Reconciliation: the content **reads at graduate level but does not function as a graduate
course**. Correct prerequisites and vocabulary; no derivations, no worked examples, no
exercises. An encyclopedia at graduate level, not a course at graduate level. A gate using
either instrument alone would have passed it or failed it for the wrong reason.

Each instrument is also individually defeatable — verified empirically:
- marker-stuffed nonsense **passes** the depth contract at mastery 5 (1020 words, nothing missing)
- genuine graduate prose **fails** it (5 elements "missing")

So the gate is a **conjunction**, and no single number may stand in for it.

### The gate

A course may claim level *L* only if ALL of the following hold:

1. **Apparatus** — ≥80% of concepts satisfy the `depth_contract` for *L*
   (`depth_contract.level_verified`).
2. **Calibration** — blind `level_audit` judged level is within **1.0** of *L*, judged with
   level hints stripped, ≥6 concepts × ≥2 repeats. Below *L* − 1 is a hard fail;
   materially above *L* + 1 is also a fail (inaccessible to the intended learner).
3. **Substance & factual correctness** — ≥80% of sampled concepts judged SUBSTANTIVE by
   `tools/substance_check.py`: derivations actually derive, examples carry concrete values
   to a result, and **every technical claim is true**. This is what stops marker-stuffing
   from satisfying (1). The judge must pass its own `--self-test` before its verdicts are
   trusted.

   > **This criterion is currently the binding one.** Measured on `course_10e8a4de`:
   > **3/6 SUBSTANTIVE (50%) — FAIL**, and the failures are *factual errors*, not missing
   > rigor. Verified by hand: one concept teaches "a necessary and sufficient condition for
   > endogeneity bias is the presence of a collider variable" — false in both directions.
   > Meanwhile the same course passes level calibration, has 69 real citations, and reads
   > well. **Every surface measure passes while the content states falsehoods.**
   >
   > Priority consequence: factual correctness outranks "add worked examples". Teaching a
   > confident falsehood is worse than teaching thinly.
4. **Structure** — <20% degenerate lessons (≤1 concept).
5. **Grounding** — 100% of concepts carry ≥1 resolvable source; none below the confidence
   floor without a visible marker.
6. **Syllabus realism** — for each supported subject, the generated syllabus is compared
   against a real published university syllabus at that level for **topic coverage and
   prerequisite sequencing only**. Differences in format, pacing, lesson length or
   assessment structure are expected and are NOT failures — see the framing above.
   What fails: a discipline's core topics missing, or concepts taught before their
   prerequisites.

### Enforcement, not reporting

- The gate runs **at generation time**, not as an afterthought. `ContentHydrator` already
  records `depth_contract.level_verified`; that verdict must be joined by the calibration
  and substance results and stored on the course.
- **A course failing the gate is not labelled at the requested level.** It is either
  regenerated, or presented honestly at the level it actually achieved, or marked
  `level_unverified` in the UI. Silently shipping a course under a level it does not meet
  is the exact defect this plan exists to remove.
- The gate is **not** satisfied by passing on the golden matrix. Golden courses detect
  drift; the per-course gate is what protects an individual learner.

### Noise discipline applies here too
Both instruments are LLM-judged. Single-run numbers are not evidence (§5). Calibration uses
repeats and reports dispersion; gaps below ~0.5 are inconclusive and must not be reported
as a pass or a fail.

### Status (honest)
- **(1), (4), (5)** — implemented and enforced at generation time.
- **(3) detection AND mitigation** — `tools/substance_check.py` detects; `services/common/fact_check.py`
  now *fixes*: it verdicts technical claims, requires an independent unprimed confirmation
  before acting (a false positive regenerates CORRECT content, which is worse than a miss),
  and `ContentHydrator` regenerates around confirmed-false claims with the error named.
  Courses record `fact_check.clean_pct`. Verified end-to-end against the real defect: both
  false claims from the reference course caught and confirmed.
- **(2)** — exists as `tools/level_audit.py`, **not yet wired into generation**.
- **(6) syllabus realism** — **not built**. This is the only criterion with external ground
  truth; everything else is self-referential LLM judgement, so its absence is the gate's
  weakest point.
- **No course has yet been generated end-to-end with the full pipeline and measured.**
  Until that exists, the gate is proven per-component but not as a whole, and must be
  described that way — a partially-verified gate reported as "quality verified" would
  itself be the dishonest-artifact problem this plan is about.

### Instrument discipline (learned the hard way, twice)
Every judge in this gate must pass a `--self-test` before its verdicts are trusted. Both
instruments built for criterion 3 failed their own self-test on the first attempt — the
substance checker needed calibrating, and the fact-checker flagged a *true* statement.
Neither failure would have been visible from its output on real content, which is exactly
why the self-test is mandatory rather than nice-to-have.

---

## 5. QA & independent review

The user requirement is explicit: **independent reviewers, not self-assessment.** Three layers.

**Layer 1 — Automated gates (every commit).** Full pytest; no new skips without written
justification; `ruff`; no bare `except: pass`; e2e on a real stack.

**Layer 2 — Independent reviewer (every sprint).** A reviewer who did *not* implement the work
checks it against the sprint gate, using a **different model** than the implementer (cross-model
review catches shared blind spots). Deliverable: explicit PASS/FAIL per gate criterion with
file:line evidence.

**Layer 3 — Adversarial verification (findings only).** Every material finding is independently
re-checked before it's believed.

> **This is not optional rigor — it is calibrated to observed failure.** During this very session an
> automated reviewer (a) reported "HIGH merge conflict severity, ~72 shared files" for what was
> provably a clean fast-forward, and (b) **wrote a file into the repo root despite explicit
> read-only instructions**. Both were caught only because findings were independently verified.
> **Never accept a self-reported GREEN.** Run the gate yourself, in a clean state.

> ### ⚠️ Gates must clear the noise floor
> Measured 2026-08-02, and it invalidates any gate phrased as "score beats baseline":
> **two identical HelgaBench runs (same code, same prompt, 5 dialogues each) scored
> `misconception_handling` 1.4 and 2.8, and `accuracy` 3.2 and 4.4** — a swing of ±1.4 on a
> 5-point scale from sampling alone.
>
> Consequences, which apply to every LLM-judged metric in this plan:
> 1. **A single run is not a gate.** Use `--repeat` (≥3) and judge on the mean with its
>    dispersion, never on one number.
> 2. **A fixed ±0.3 threshold reports noise as signal.** The harness now derives the
>    smallest trustworthy change from the observed standard error and labels anything
>    below it "(within noise)".
> 3. **Report `sd` and `n` beside every mean.** A mean without dispersion invites exactly
>    the false conclusion this note exists to prevent.
> 4. If an effect is smaller than the noise floor, the honest report is *"no measurable
>    effect"* — not a directional claim in whichever way the dice fell.

**Standing quality instruments** (run every sprint, tracked over time):
- **HelgaBench** — simulated-student tutoring quality across profiles. Run with `--repeat 3`
  minimum; see the noise-floor warning above.
- **Golden courses** — regenerate the 6-course matrix; diff quality metrics; catch generation drift.
- **Grading eval** — `tools/grading_eval.py` + `grading_eval_cases.json` already exist; extend.
- **Honesty audit** — every user-visible claim (labels, confidence scores, toggles) verified to
  match actual behavior. This catches the `research+llm` class of defect.

---

## 6. Optimization passes

Deliberately scheduled *after* correctness (A6), because optimizing unverified behavior is waste.

**Model routing.** We currently use one model for everything. Skeleton generation, hydration,
grading, and conversational turns have very different latency and quality needs. Route: a small fast
model for classification//routing, `qwen3.5:9b` for tutoring, and a larger model for
skeleton/syllabus generation where structure quality compounds across the whole course. Note
`qwen3.5:9b` emits `thinking` content — measure whether that's paid for on every turn and suppress
where it isn't earning its latency.

**Generation cost.** Hydration is sequential with a hardcoded 3-worker cap justified by a
Jetson comment (`course_builder.py`) that no longer applies on a Mac Mini M4 Pro — re-tune against
real memory headroom. Cache skeleton/prompt results; dedupe near-identical concept generations.

**Retrieval.** Once hybrid is real, tune chunk size, top-k, and the RRF constant against
HelgaBench grounding scores rather than by intuition.

**Serving.** Right-size container memory limits against measured usage. Ensure the GPU fair-queue
degrades gracefully rather than timing out. Keep the caches (`tts_cache`, `research_cache`) pruned —
`background_ops.py` already does this.

---

## 7. Immediate next actions

1. **Unlock "My Passport"** (`/Volumes/WD Unlocker/WD Drive Unlock.app`) and set `OLLAMA_MODELS` to
   the drive, then restart Ollama — currently only the local 27 GB store is visible. *Not blocking:*
   `qwen3.5:9b` is local and works.
2. Fix `main.py:81` — its model preflight uses a **substring** match, so `qwen3:14b` "matches"
   `qwen3:14b-q4_K_M` and reports a false green while inference 404s.
3. Start **A0**. Do not start A1 until §2 has no unverified rows.
4. Triage `wip/april-2026-orphaned-work` for the UI work worth keeping (scheduled in A5).
5. **Mode B curriculum sourcing is parked** (§4.9) but its clock runs independently of sprint
   velocity — begin rights/sourcing conversations whenever convenient, since nothing in Mode A
   blocks on them and nothing they need blocks on Mode A.

---

## 8. Provenance of this document

Every status claim here was verified against running code, a live service call, or a database query
on 2026-08-02 — not taken from `BUILD_MANIFEST.md`, which was materially wrong about B16.1/B16.2.
Where a claim could not be independently confirmed (the DeepTutor layered-memory structure), it is
labelled as unverified in place. Bulk file reading was delegated; **every consequential finding was
re-checked directly**, and two delegated claims were found wrong and corrected. Apply the same
standard to anything added to this plan.
