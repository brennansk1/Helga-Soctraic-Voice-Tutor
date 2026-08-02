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
- Generated concepts are labelled `Source: research+llm` but contain **zero citations** — no URLs,
  DOIs, or references in any of the 36 files. The label is not currently truthful.
- Hybrid dense retrieval **degrades silently** to FTS5 when deps are missing — a silent quality
  cliff with no operator signal.

**Course artifact quality (n=1, `course_10e8a4de` "causal inference"):** 36 concepts, 6 modules,
zero stubs, 626–876 words each, Bloom ramps correctly 1→2→3→3→4→5. Prose is genuinely good —
real technical content with prerequisites and mastery rubrics. Two defects: mild domain drift in the
weakest concept (renders "causal pathway" in epidemiology framing), and **7 of 21 lessons have ≤1
concept** — a third of the tree is degenerate scaffolding.

> **Evidence gap that matters:** there is exactly **one** generated course on disk, with
> `teaching_style=""`. We have no empirical basis for claiming quality across settings. Sprint S1
> exists primarily to create that basis.

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
| Citations / grounding | **Missing** | Label claims research grounding; zero citations emitted |
| `source_confidence` acted upon | **Missing** | Computed, shown, ignored |
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
> Arc III into **S1.5 — a blocking prerequisite for every other Mode B sprint.**
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

**1. Citation-grounded generation — S2.** This is the single most important lift. DeepTutor's core
claim is that tutoring output is *grounded in retrievable sources with citations*. It directly fixes
our worst honesty defect: content labelled `research+llm` with zero references. Adopting this makes
`source_confidence` meaningful for the first time — a concept with no retrievable support should be
*visibly* ungrounded, or regenerated, not silently shipped.

**2. TutorBench-style evaluation — S0 and every sprint after.** The paper introduces **TutorBench**,
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

Nine sprints in three arcs. Each has an explicit **exit gate** — objective, checkable, and verified
by an independent reviewer who did not do the work (see §5). **A sprint does not end because work
finished; it ends because the gate passed.**

### Arc I — Make it true (S0–S2)
Stop shipping claims we can't back. Nothing new gets built on unverified ground.

**S0 — Ground truth & harness** *(no features)*
- Audit every "landed/partial" row in §2 to DONE/NOT-DONE with file:line evidence.
- Stand up the stack; convert the 8 environmental e2e failures into genuine pass/fail.
- Build **HelgaBench v0**: ≥6 student-simulator profiles, ≥3 subjects, run by a second model;
  record baseline scores for tutoring quality, grading accuracy, and Socratic adherence.
- Build **golden-course eval**: generate 6 courses across the slider space (scope×mastery×
  starting_from) and 2 grade bands. This creates the missing evidence base.
- *Gate:* baseline numbers exist and are reproducible; §2 has no "needs verification" rows.

**S1 — Course depth contract**
- Define measurable depth targets per (scope, mastery) cell: concept count, word budget, required
  formalism (equations/proofs/derivations for STEM), prerequisite chain depth.
- Enforce post-generation: verify output against the contract; regenerate or flag on miss.
- Fix degenerate structure (7/21 lessons with ≤1 concept) — merge or expand.
- Fix domain drift (the epidemiology-framing bug).
- *Gate:* all 6 golden courses meet their depth contract; a graduate-level request produces
  measurably deeper output than an introductory one on the same topic. Blind-rated by an
  independent reviewer.

**S1.5 — Curriculum content** *(blocking prerequisite for all Mode B work)*
- Author/ingest Utah Core Standards seed files into `data/standards/*.yaml|json` — the loader
  (`standards_loader.py`) is already built and idempotent, so this is a content task, not a code task.
- Create the `data/catalog/` read-only catalog store (B16.2 — never started despite being marked done).
- Generate and review a first tranche of catalog courses, standards-tagged, for at least one
  subject × one grade band.
- *Gate:* `SELECT COUNT(*) FROM standards` is non-trivial and matches the published Utah strand
  count for the covered subjects; coverage audit (`storage.py:2100`) reports zero unmapped standards
  in that tranche; a real student can complete a standards-aligned lesson end to end.

**S2 — Real grounding & citations**
- Complete hybrid retrieval; **remove silent degradation** — if dense is unavailable, say so loudly.
- Emit inline citations in generated concepts, resolvable to a retrieved source.
- Make `source_confidence` load-bearing: below threshold → regenerate, or surface as
  "limited sources" in the UI. Stop labelling ungrounded content `research+llm`.
- *Gate:* ≥90% of concepts in golden courses carry ≥1 resolvable citation; zero concepts ship below
  the confidence floor without a visible marker; HelgaBench grounding score beats S0 baseline.

### Arc II — Make it good (S3–S5)

**S3 — Pedagogy & personalization**
- Enable tutor tools (`HELGA_ENABLE_TUTOR_TOOLS`) behind a reliability gate — they're built and
  disabled; validate and turn on. Keep `run_python` off by default.
- Layered learner memory; difficulty-calibrated item generation; wire FSRS ↔ Socratic loop.
- *Gate:* HelgaBench personalization score up materially vs baseline; no regression in grading
  accuracy; tool-call failure rate below an agreed threshold.

**S4 — UX truth & the learn loop**
- Fix the no-op toggles and the `/api/profile/reset` 404 — **no control may lie about its effect**.
- Reconcile the April orphaned UI work (`wip/april-2026-orphaned-work`: `learn-chat.css` 1138 lines,
  `progress-tree.js`, avatars) — cherry-pick what's still wanted.
- Kid-first IA (8→4 tabs per `docs/design/11`), onboarding, notifications.
- *Gate:* every interactive control has a verified effect; e2e suite green; accessibility pass.

**S5 — Compliance & safety** *(hard gate for Mode B)*
- COPPA verifiable parental consent; FERPA/Utah data rights (export + deletion).
- Output moderation, crisis detection, parent alerting without sensitive-transcript transmission.
- Health Strand 6 consent gating.
- *Gate:* independent adversarial safety review — red-team the tutor with distress, abuse, and
  jailbreak prompts. **No Mode B exposure to a real minor before this passes.**

### Arc III — Make it last (S6–S8)

**S6 — Catalog breadth** — extend S1.5's first tranche to the remaining subjects and grade bands;
CMS review pipeline; versioning and provenance; admin console HTML frontend (API exists, UI deferred).
*Gate:* coverage report shows no unmapped standards across all shipped subjects.

**S7 — Optimization pass** — see §6. *Gate:* p95 latency and generation-time targets met, no quality
regression on HelgaBench/golden courses.

**S8 — Scale & ops** — GPU fair-queue hardening, Ollama circuit breaker, Postgres migration path,
backups, observability. *Gate:* survives a soak test and a simulated Ollama outage without data loss.

**Billing (B20) and gamification (B22)** slot into R3 alongside S4/S6; they are not on the quality
critical path and must never gate a correctness sprint.

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

**Standing quality instruments** (run every sprint, tracked over time):
- **HelgaBench** — simulated-student tutoring quality across profiles.
- **Golden courses** — regenerate the 6-course matrix; diff quality metrics; catch generation drift.
- **Grading eval** — `tools/grading_eval.py` + `grading_eval_cases.json` already exist; extend.
- **Honesty audit** — every user-visible claim (labels, confidence scores, toggles) verified to
  match actual behavior. This catches the `research+llm` class of defect.

---

## 6. Optimization passes

Deliberately scheduled *after* correctness (S7), because optimizing unverified behavior is waste.

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
3. Start **S0**. Do not start S1 until §2 has no unverified rows.
4. Triage `wip/april-2026-orphaned-work` for the UI work worth keeping (scheduled in S4).
5. **Scope the curriculum-content effort early.** S1.5 is the long pole for Mode B and it is a
   content/licensing problem, not an engineering one — sourcing and rights for Utah Core Standards
   material has a lead time that no sprint velocity can compress. Begin it in parallel with S0.

---

## 8. Provenance of this document

Every status claim here was verified against running code, a live service call, or a database query
on 2026-08-02 — not taken from `BUILD_MANIFEST.md`, which was materially wrong about B16.1/B16.2.
Where a claim could not be independently confirmed (the DeepTutor layered-memory structure), it is
labelled as unverified in place. Bulk file reading was delegated; **every consequential finding was
re-checked directly**, and two delegated claims were found wrong and corrected. Apply the same
standard to anything added to this plan.
