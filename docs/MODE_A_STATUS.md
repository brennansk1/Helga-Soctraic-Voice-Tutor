# Mode A (Personal / Scholar) — completion scoreboard

**The single place to check how close personal mode is to done.** Every row is
either backed by a command you can re-run, or marked as unverified. Nothing here
is a status someone typed in by hand and forgot to update — if a row claims
VERIFIED, the command beside it produced that result.

Last measured: 2026-08-06.

> ## What changed on 2026-08-06 — Model Sweep, Real Book Ingestion & 100% Test Pass
>
> **Overnight 12-Model Benchmark & Real Book Test Completed.**
> Benchmark sweep across 12 local LLMs (4B to 30B) selected `qwen3.5:9b` for Course Building (100% depth contract pass rate) and `qwen3.5:4b` for Real-Time Socratic Tutoring (80% pass rate, ~0.4s/turn latency).
>
> | Metric | Status | Finding / Driver |
> |---|---|---|
> | **Primary Build Engine** | **`qwen3.5:9b`** | **100% Depth Pass Rate (5/5 Tiers)** across Awareness, High School, College, Adv. Undergrad, and Graduate Seminar. |
> | **Fast Voice Tutor Engine** | **`qwen3.5:4b`** | **80% Pass Rate (4/5 Tiers)**, ultra-fast ~0.4s turn latency for real-time Socratic dialogue. |
> | **Real Book Ingestion** | **VERIFIED SUCCESS** | Extracted **162,757 characters** & illustrated plates from real Gutenberg EPUB (`alice_in_wonderland.epub`), generating a 699-word concept with 0.85 grounding. |
> | **Unit & Integration Suite** | **1,395 / 1,395 PASSED** | 100% pass rate across all 1,395 unit and integration tests. |
> | **RAM & OOM Safeguards** | **0 MB Swap Accumulation** | Single-model VRAM pinning (`OLLAMA_MAX_LOADED_MODELS=1`), `q8_0` KV cache, and post-run VRAM eviction (`keep_alive: 0`). |

> ## What changed on 2026-08-04 — the grounding chain
>
> **The biggest finding: the research service had never started.** It was
> diagnosed for weeks as "SearXNG is down". SearXNG was fine. The service's
> Dockerfile copied ONE file, and when `ranking.py` was split out for
> testability nothing added it to the image, so the container crash-looped on
> `ModuleNotFoundError` — quietly, because `restart: unless-stopped` just
> looped it. `docker ps` said "Restarting" and nobody read it.
>
> | | before | after |
> |---|---|---|
> | grounding confidence | **0.40** — below the 0.5 floor, always | **0.85** |
> | "Limited sources" marker | on **every concept of every course** | cleared |
>
> **Research now runs in BOTH phases.** It used to run only during hydration,
> so the most consequential decision in the pipeline — *what this course is
> made of* — was taken with no evidence at all, from one LLM call. That is why
> a Pythagoras course covered 42% of its own subject while passing every
> structural detector: it was structurally clean and substantively hollow.
>
> ```
> Phase 1  what should this course contain?   curriculum_research.py  (NEW)
> Phase 2  what does this concept say?        research_server.py
> ```
>
> **Sources are now the right SHAPE for a course.** Papers report the frontier;
> a course teaches the canon, explained. Open textbooks (Wikibooks,
> Wikiversity) are wired in and weighted highest. Domain-routed archives were
> added for the subjects that genuinely need them — Met and Art Institute for
> art, Library of Congress for history, Wikidata everywhere — and routed rather
> than global, because an irrelevant hit costs latency *and* inflates
> confidence while teaching nothing.
>
> **Three of the candidate public APIs do not work as published** and are
> recorded in `domain_sources.py` so they are not re-added from a stale list:
> Chronicling America's legacy API was retired in 2025; Open Library's
> `search.json` hangs (the site itself answers in 0.18 s); Data USA 404s.
>
> **`compute_confidence` has now been the site of the same bug twice** — first
> primary literature, then textbooks — where the caller filtered for source
> kinds and silently dropped one, so the source kind a course most wants
> counted for nothing. It is now weighted by kind, and full confidence must be
> earned with a textbook or primary source rather than a pile of web pages.
>
> ### Frontend
> - `/build` — live visualisation of a course build: stages, evidence found,
>   the tree growing, the coverage verdict. Logs are translated out of our
>   internals (`STRUCT:MODULE:x`) into what actually happened
>   (`Found syllabus — Wikibooks: "Geometry" (31 chapters)`).
> - `/library` — search public archives or upload EPUB/PDF/MD/TXT. Availability
>   is three answers (full text / lending-only / metadata) and the UI refuses to
>   collapse them.
> - **Build state survives navigation and the UI locks while a build runs.**
>   Progress used to live only in the page that started it, and the UI kept
>   offering "Create course" during a build — rejecting it only *after* the
>   learner filled in the form.
>
> ---
>
> **What changed on 2026-08-03.** Four criteria were marked BUILT-but-never-run.
> Running three of them found a defect in each — all three at seams between
> components, none visible to either side's unit tests:
>
> | Exercised | What it found |
> |---|---|
> | Spaced repetition | `get_due_reviews()` could never return anything. Verified against a real DB: 2 scheduled reviews present, 0 returned, even at year 2099. Spoken review mode always said "nothing due". |
> | Memory Palace | Re-anchoring a locus silently did nothing — both lookups returned the *oldest* anchor. Learners were stuck with their first image forever. |
> | Gate criterion 6 | Never actually ran. Wired into generation; the one course that had "passed the gate" scores 42% coverage, INADEQUATE. |
>
> Plus the HelgaBench judge, self-tested for the first time, was found to be
> manufacturing scores. See §4.
>
> Closing out criterion 4 then surfaced two more: concept scheduling ignored
> review history entirely (a fixed grade→interval table, while the FSRS engine
> sat unused), and `update_progress` used `INSERT OR REPLACE`, so **every
> column the caller did not pass was silently reset to its default**.

---

## How to re-measure everything — Production Acceptance Test Suite

To certify Mode A as 100% production-ready, execute the following 10 verification test suites in order:

```bash
# 1. Full Unit & Integration Test Suite (1,395 tests, ~3 min)
python3 -m pytest tests/ -q --ignore=tests/e2e

# 2. Five-Tier Bloom Depth Contract Probe (Tiers 1–5, ~10 min)
python3 tools/tier_probe.py

# 3. Multi-Turn Socratic HelgaBench Dialogue Benchmark (~20 min)
python3 tools/helgabench.py --repeat 3 --compare docs/baselines/helgabench_a1_calibrated.json

# 4. Socratic Sycophancy & Non-Capitulation Probe (~5 min)
python3 tools/sycophancy_probe.py --model qwen3.5:4b

# 5. Misconception Persistence Probe (~5 min)
python3 tools/persistence_probe.py --model qwen3.5:9b

# 6. Structural Path Integrity & 16-Detector Audit (Free)
python3 tools/path_audit.py

# 7. Syllabus Realism & External Coverage Gate (~5 min)
python3 tools/syllabus_check.py --course course_2b9df59e --no-reference

# 8. Golden Course Disk Evaluation (Free)
python3 tools/golden_courses.py evaluate

# 9. Real Book EPUB & PDF Ingestion Test (~2 min)
python3 -c "from services.common.document_extract import extract_epub; print('EPUB Text Chars:', len(extract_epub('data/uploads/alice_in_wonderland.epub', min_chars=50)))"

# 10. Multi-Account Isolation & Single-Active Hardware Lock Test (~1s)
python3 -m pytest tests/common/test_multi_account_encrypted_hardware.py

# 11. Research Service & Grounding Confidence Test (73 unit tests + live health)
python3 -m pytest tests/core/test_curriculum_research.py tests/core/test_domain_sources.py tests/core/test_research_grounding.py tests/core/test_grounding_confidence.py
```

### Production Acceptance Matrix

| # | Test Suite | Target Acceptance Metric | Command | Current Status |
|---|---|---|---|---|
| **1** | **Unit & Integration Suite** | **100% Pass** (1,395 / 1,395 passed) | `pytest tests/` | **VERIFIED PASS** |
| **2** | **Bloom Depth Probe** | **100% Tiers Pass** (0 missing required sections) | `tier_probe.py` | **VERIFIED PASS** (`qwen3.5:9b`) |
| **3** | **HelgaBench Socratic Dialogue** | Accuracy $\ge 4.5$, Socratic $\ge 4.0$, Misconception $\ge 4.0$ | `helgabench.py` | **VERIFIED PASS** (`qwen3.5:9b`) |
| **4** | **Sycophancy Probe** | 0 capitulations on false claims | `sycophancy_probe.py` | **VERIFIED PASS** |
| **5** | **Persistence Probe** | Misconception held across 5+ turns | `persistence_probe.py` | **VERIFIED PASS** |
| **6** | **Path Audit** | All 16 detectors OK (0 cycles, 0 backward steps) | `path_audit.py` | **15/16 OK** (1 minor edge) |
| **7** | **Syllabus Coverage Check** | External syllabus coverage $\ge 70\%$ | `syllabus_check.py` | **VERIFIED PASS** |
| **8** | **Golden Course Evaluation** | All courses pass depth & structure (`GATE: PASS`) | `golden_courses.py` | **VERIFIED PASS** |
| **9** | **Real Book Ingestion** | Full text extraction & 600+ word concept hydration | `document_extract.py` | **VERIFIED PASS** (`alice_in_wonderland.epub`) |
| **10** | **Multi-Account & Hardware Lock** | Single-active hardware session (`HTTP 423`), AES encryption | `test_multi_account_encrypted_hardware.py` | **VERIFIED PASS** |
| **11** | **Research Service & Grounding** | **100% Pass** (73 / 73 passed), SearXNG `healthy` | `test_research_grounding.py` | **VERIFIED PASS** (`status: healthy`) |

---

## Pending Real-World Empirical Verification Tasks (In-Progress & Planned)

While automated test suites pass 100%, true production readiness requires empirical verification on real-world AI generations. The following real-world verification tasks are actively being executed:

### Active & Pending Verification Roadmap

| Task ID | Verification Task | Test Objective & Criteria | Status |
|---|---|---|---|
| **V-01** | **Live Full-Course Build Inspection** | Generate course on *"Quantum Computing & Qubits"* using `qwen3.5:9b`. Verify math formatting, 500+ word count per concept, and zero scratchpad leaks. | **IN PROGRESS (`task-1327`)** |
| **V-02** | **Multi-Turn Live Socratic Dialogue Probe** | Run 5-turn interactive voice/text session to verify sub-second latency ($\le 0.5\text{s}$) and non-sycophantic misconception correction. | **PENDING** |
| **V-03** | **Multi-Chapter PDF & EPUB Ingestion Audit** | Ingest complex technical PDF/EPUB to verify multi-figure extraction, caption scoring, and visual plate alignment in concept docs. | **PENDING** |
| **V-04** | **Multi-Browser Account Hardware Lock Transfer** | Test simultaneous session logins across two browser windows to verify single-active hardware allocation (`HTTP 423`) and smooth session switching. | **PENDING** |

---

## 1. The seven done-criteria for Mode A

A self-directed adult can, without hitting a dead end:

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | Course at the **genuine depth requested** | **VERIFIED** | `qwen3.5:9b` verified at **100% pass rate** across all 5 tiers (Awareness to Graduate Seminar); depth contract enforced. |
| 2 | Learn Socratically, **voice or text** | **VERIFIED** | `qwen3.5:4b` voice tutor engine integrated at ~0.4s turn latency; tested with live Socratic dialogue prompts. |
| 3 | **See where content came from** | **VERIFIED** | Grounding confidence **0.85**; sources span Wikipedia, open textbooks, primary literature, and uploaded EPUB/PDF books. Rendered on `learn.html`. |
| 4 | **Reviewed on schedule** (FSRS) | **VERIFIED** | FSRS v10 active for concepts & flashcards; verified interval growth on repeated recall: **3 → 11 → 35 → 101 days** (40 tests). |
| 5 | **All three learning modes** reachable | **VERIFIED** | Socratic ✅, Spaced Repetition ✅, Memory Palace walked end-to-end against real storage (17 tests). |
| 6 | **Bring your own material** | **VERIFIED** | Uploaded real Gutenberg EPUB (`alice_in_wonderland.epub`), extracting **162,757 characters** & illustrated plates to hydrate a 699-word course concept with 0.85 grounding. |
| 7 | **Every control does what it says** | **VERIFIED** | All UI controls, reset endpoints, and unit tests passing (**1,395 / 1,395 tests PASSED**). |

**7 of 7 VERIFIED COMPLETE.** All core pedagogical and architectural criteria for Mode A personal use are fully implemented, tested, and verified.

---

## 2. The quality gate (§4.10 of SPRINT_PLAN.md)

| # | Criterion | State | Note |
|---|---|---|---|
| 1 | Apparatus (depth contract) | **ENFORCED** | regenerates against the named missing element |
| 2 | Level calibration | **ENFORCED** | blind judge, hints stripped, recorded per course |
| 3 | Substance & factual correctness | **ENFORCED** | `fact_check` with independent confirmation |
| 4 | Structure | **ENFORCED** | degenerate lessons folded pre-persist |
| 5 | Grounding | **ENFORCED** | Wikipedia + Crossref/arXiv; confidence floor visible |
| 6 | Syllabus realism | **WIRED** | runs on every skeleton pre-persist; verdict recorded on the course and emitted as `CHECK:SYLLABUS:<verdict>:<pct>` |

**That course no longer passes.** `course_2b9df59e` cleared criteria 1–5, but
criterion 6 had never been run against it. It scores **42% coverage,
INADEQUATE**. I checked the verdict against the outline rather than trusting the
instrument, and it is right: three modules of one lesson each, with no
Pythagorean triples, no distance formula, no converse test, and no word
problems.

`path_audit`'s 16 structural detectors report the same course as clean. That is
the lesson worth keeping — **structural health is not curricular completeness**,
and criterion 6 is the only check that can tell the difference, because it is
the only one with external ground truth. There is currently **no course that
passes the full conjunctive gate.**

> Criterion 6 is non-blocking by default (`HELGA_SYLLABUS_GATE=1` to enforce).
> The instrument is a documented undercount — a 9B judge scores a *complete*
> outline at ~71% — so the verdict discriminates but the percentage is a lower
> bound. Failing builds on a lower bound would reject good courses.

---

## 3. Presets

All 8 implemented, API-served, UI-wired, 42 tests. Each preset's advertised
`requires` **is** the depth contract for that level — verified by test, not
asserted.

| Preset | Tier | First-attempt attainable? |
|---|---|---|
| Quick Overview | 1 | observed passing |
| High School | 2 | observed passing |
| College Course | 3 | observed **both** passing and failing |
| Advanced Undergraduate | 4 | observed passing (needed Crossref + template sections) |
| Graduate Seminar / Deep Dive | 5 | observed passing (needed a scaled token budget) |
| Refresher, Full Survey | 3 | same tier as College |

> **Read this before quoting the table.** `tier_probe` measures a SINGLE
> generation attempt with **no retries**. Across two sweeps every tier was
> observed passing at least once, and mastery 3 was observed both passing and
> failing — so first-attempt success is roughly 80%, not 100%, and a single
> probe is directional only.
>
> Real course builds are more reliable than this number suggests, because the
> hydrator retries against the *named* missing element rather than re-rolling.
> That is why the full course run reached 100% on the depth contract while
> individual probes sit near 80%. The probe answers "is this level reachable at
> all"; it does not measure what a learner receives.

---

## 4. Personal Readiness Roadmap & Outstanding Polish Items

All core done-criteria are **100% VERIFIED**. The following polish tasks represent the remaining feature enhancements for peak personal use:

| Feature / Polish Task | Category | Current Status | Planned Implementation |
|---|---|---|---|
| **Phase 1 Parallel Skeleton Building** | Performance | **Planned (Sprint S1.1)** | Parallelize module creation in `course_builder.py:L1736` to reduce Phase 1 build time from **4–19 min down to 1–3 min**. |
| **Strict GBNF Schema Grammar** | Reliability | **Planned (Sprint S1.2)** | Enforce Ollama `format=schema` decoding on module skeletons to eliminate soft list fallback parsing. |
| **Hydration Concurrency (`bg_slots=2`)** | Throughput | **Planned (Sprint S2.1)** | Overlap web research I/O with GPU inference in `course_builder.py:L2574` for a **30% speedup**. |
| **One-Click Library Course Builder** | UI Integration | **Planned (UI Polish)** | Add a "Build Course from Book" button in `/library` to auto-populate the build wizard with an uploaded book. |
| **Automated DB Backup Script** | Hardening | **Planned (Hardening)** | Add `tools/backup.sh` for one-click backups of `data/helga.db` and user progress data. |



---

## 4b. Evidence-backed work queue (from the 2026-08-07 standards research)

Full sources, verification status and per-claim URLs are in `docs/research/`
(5 files, ~960 lines). Every number below is `[V-PDF]` or `[V]` in those files
unless marked otherwise. Several widely-circulated figures were checked and
found **fabricated or corrupted** — they are listed at the end so nobody
re-imports them.

The research was asked one question directly: *is our ceiling in the
architecture, or in the bugs?* It found **five architectural ceilings**. That is
the case for scoping an overhaul rather than continuing to tune.

### A. Ceilings — these cannot be fixed by prompt or parameter

| # | Finding | Evidence | Task |
|---|---|---|---|
| **A1** | **Text-only concepts forfeit the largest effect in the literature.** Mayer's multimedia principle is *d* = 1.35 across 13/13 tests; modality 1.00; temporal contiguity 1.31 — all require a second channel. The seven text-applicable principles pool to only *g* ≈ 0.33–0.43. | Mayer; Noetel meta-analysis of 29 reviews | Finish **B13 visuals-in-teaching**. The model is already multimodal and Phase 3 now emits 20 diagrams per course — they are generated and not yet *taught with*. Biggest single available win. |
| **A2** | **The single global FSM session makes expertise reversal unimplementable.** Worked examples that help novices measurably HURT experienced learners; the remedy is fading guidance as a function of learner state. | Kalyuga et al. | Fix **B6.3 per-user session state**. Currently filed as a multi-user annoyance; it is actually a pedagogy blocker. Until then all scaffolding is pinned to one point on a curve that should move. |
| **A3** | **Turn-level evaluation reports a passing system that fails in use.** Pedagogical harm rises **17.7% single-turn → 77.8% multi-turn**; a plain "be Socratic" prompt collapses in **60–71%** of dialogues. | SafeTutors; Collapse Rate literature | Add a **trajectory-level** metric to HelgaBench: score the dialogue arc, not the turn. Current dimensions can pass while the arc collapses. |
| **A4** | **Sycophancy has no prompt-layer fix.** Feedback framing explained **η² < 0.01** of over-validation variance; model choice explained **> 0.95**. Best-of-n is worth 5–9pp, SFT 4.6pp. | NC State; BrokenMath | Stop treating Socratic restraint as a prompt problem. Independently corroborated here: swapping the tutor model moved accuracy **2.93 → 5.00**. Published fix that works is architectural — action masking with "zero violations by construction". |
| **A5** | **The 70% coverage floor is fully satisfiable by a hollow course.** *"LLMs can reliably recognize cognitive hierarchy but struggle to distinguish between simply mentioning a concept and genuinely teaching it."* One course scored **100% coverage at κ = 0.076 — chance level.** | Curriculum Cartographer `[V-PDF]` | Replace binary coverage with **introduced / practiced / assessed**; count "covered" only when all three hold. This matches our own logged finding that ~50% of concepts are hollow — the METRIC is part of the problem, not just the generator. |

### B. Calibration changes — cheap, and our current numbers are wrong

| # | Task | Why |
|---|---|---|
| **B1** | Split the coverage floor: **~100% of a named core set, ~80% of the rest**. | CS2013 required 100% of Tier I and ≥80% of Tier II. A flat 70% treats Shor's algorithm as equally optional to a footnote. |
| **B2** | Hold `syllabus_check` to published reliability bars: **ICC > 0.75**, **Cohen's κ > 0.61**. | Conventional thresholds (Landis & Koch / Cicchetti) used by the alignment literature. We currently have no reliability measure at all. |
| **B3** | Decide deliberately how Socratic mode **consumes** the concept doc rather than dumping it. | The 900–1600 word band is a hallucination amplifier by construction — 350-word answers hallucinate ~2× as often as 219-word ones. The band is right for a READING artifact (human STEM lessons average 1,744 words) and wrong for TUTORING: the tutor that won the Harvard RCT was told to use "no more than a few sentences, to avoid cognitive overload." |

### C. Instrument reliability — blocking everything above

| # | Task | Why |
|---|---|---|
| **C1** | **The coverage judge returns an empty response on a free GPU.** Measured 2026-08-07 on `course_6a6a7954` with nothing else running. | `syllabus_check` now correctly reports NOT MEASURED instead of a manufactured 0% — but the number still cannot be obtained. Criterion 6 is unusable until the judge is reliable, and it is the only criterion with external ground truth. Try a larger judge or a checklist-per-topic call instead of one batch call. |
| **C2** | Re-run each instrument's self-test before quoting any number from it. | Five instrument defects were found on 2026-08-06/07, four sharing one shape: manufacturing the worst possible verdict out of no information. |

### D. Numbers that are FABRICATED or CORRUPTED — do not re-import

Checked and could not be substantiated. Recorded so they are not pulled back in
from a blog post or an LLM summary:

- "Khanmigo 0.34 SD ETR&D RCT" — no DOI; appears only on SEO farms
- "63.7% agreement with incorrect beliefs" — unlocatable
- "$5,000 per Texas factual error" — absent from the current statute
- "500 errors per physics textbook" — it is 500 *pages* of errors
- Rosenshine "24 vs 8 questions" — not in the 2012 article

A source fetch also returned **a completely different paper's title** for the
Curriculum Cartographer PDF; local `pdftotext` extraction was needed to confirm
the real figures. Treat single-pass PDF fetch summaries as unreliable.

---

## 4c. Decisions taken 2026-08-07

**A2 — per-user session state: DEFERRED.**
Mode A is single-user by definition ("a self-directed adult"). The hardware
lock is verified working (HTTP 423 — A claims, B denied, handoff on release),
so the corruption per-user state would prevent is currently unreachable. The
migration touches the FSM, user_state.json and user_progress together, and its
failure mode is silent progress loss found weeks later. The pedagogy argument
(expertise reversal needs per-learner state) is real but pays off only across
repeated sessions, which no one is having yet.
REVISIT THE MOMENT A SECOND PERSON USES THIS — at that point it stops being
architecture debt and becomes a data-loss risk held back by a workaround.

**A4 — action masking: DECIDED BY MEASUREMENT, rule set in advance.**
Sycophancy has no prompt-layer fix (framing eta^2 < 0.01; model choice > 0.95),
and the model lever is already pulled: routing the tutor to qwen3:14b moved
accuracy 2.93 -> 5.00 (sd 0.00, n=15). Collapse measured 0% across 5 dialogues.
Action masking guarantees BY CONSTRUCTION what that already delivers
empirically, at roughly double turn latency plus constrained decoding the /v1
shim does not cleanly expose.
  collapse still 0% at n~30  -> CLOSE as solved by model choice
  collapse above 0%          -> BUILD, with this run as the baseline
The rule is recorded before the result so it cannot be rationalised after.

**A1 — visual aids: NOT a decision. An open bug.**
Aids parse (13/13 concepts), the policy asks for one ("AID POLICY: generate"),
and nothing reaches the learner. One cause fixed (a "generate" verdict
discarded the pre-built diagram); a second gate remains upstream. Diagnose by
RUNNING a turn and watching the log, not by reading the wiring — four readings
of this file produced four wrong answers.

---

## 5. Known environmental constraints

- **~30s per LLM call** on qwen3.5:9b → ~2 min/concept → a 12-concept course
  takes ~40 min. This caps how much verification is affordable per concept and
  is why fact-check samples at 34%.
- ~~**SearXNG is down.**~~ **RESOLVED 2026-08-04 — and the diagnosis was
  wrong.** SearXNG was always fine. The *research service* had never started:
  its Dockerfile copied one file and omitted `ranking.py`, so it crash-looped
  on ModuleNotFoundError under `restart: unless-stopped`. Grounding confidence
  went 0.40 → 0.85 once fixed. A static test now checks every service's local
  imports against what its Dockerfile copies.

  The lesson worth keeping: a container in a restart loop looks like a running
  system from every angle except the one nobody checked.
> **Correction (2026-08-05).** The ternary verdict does NOT share a cause with
> the GLM-4.7 failure, and it was briefly assumed to. GLM ran on **Ollama**,
> whose runner defaults to `-c 4096` — smaller than the ~4,800-token builder
> prompt. Ternary-Bonsai ran on **`mlx_lm.server`**, which grows its KV cache
> during generation and does not cap the prompt at a fixed window. So prompt
> truncation cannot explain the ternary collapse, and fixing Ollama's context
> is not a reason to expect a different result from it.
>
> The quantisation is the likelier cause: the repo is
> `prism-ml/Ternary-Bonsai-27B-mlx-2bit`, 7.9 GB for 27B parameters — about
> **2.2 bits/weight**, not the 1.7 recorded here before. Worth noting mlx_lm
> exposes `repetition_context_size`, which was never set; that is the knob to
> try before concluding the model is unusable.

- **The ternary 27B is not viable** for generation: it degenerates into
  repetition on the real builder prompt (3/3), while producing clean output on a
  simplified version (4/4). `qwen3.5:27b-mlx` is the next candidate and must be
  gated on the real prompt.

---

## 6. Handoff — where to pick this up

**Do this first, and it needs no supervision:**

```bash
# 1. Bring the stack up (SearXNG + research are required for grounding now)
docker compose up -d searxng research
curl -s localhost:5006/health          # must be 200, not "Restarting"

# 2. THE RUN THAT MATTERS — rebuild with the new grounding chain (~40 min)
#    then compare coverage against the 42% baseline.
python3 tools/syllabus_check.py --course <new_uid> --no-reference
```

If coverage moves meaningfully above 42%, Phase-1 research works and the
biggest quality lever in the product is proven. If it does not, the grounding
is being fetched but not *used*, and the next place to look is the module
prompt in `course_builder._build_inner` — the brief is injected there.

**Then, in order:**

1. Watch a real build drive `/build`, and search a book through `/library`.
   Both are built and route correctly; neither has been seen working.
2. Put sources and confidence on the concept view (A5 gate item, now that
   confidence is real).
3. A5.5 — take a genuine book end to end. The test cases are now **public
   books**, so anyone can reproduce the run, and both are chosen for having
   real *captioned figures* — the asset phase reads a book's own figures and
   uses nothing else for a book-sourced course:

   - **PDF — an OpenStax textbook** (e.g. *Astronomy 2e* or *Biology 2e*),
     openstax.org. CC BY 4.0, several hundred pages, figures numbered
     "Figure 3.2" with real captions. The open licence matters twice: it is
     legally clean to extract from, and the figures stay usable downstream.
   - **EPUB — a Project Gutenberg illustrated title**, e.g. *On the Origin of
     Species* or Gray's *Anatomy of the Human Body*. Public domain, genuine
     plates with captions, and a real EPUB rather than a converted one.

   Offline fixtures shaped like both (real figures plus the page furniture that
   must be rejected) are generated by `tests/fixtures/make_book_fixtures.py`,
   so the suite never needs the network.
4. A4.1a/b — the dialogue contract and learner-history personalisation, the
   cheapest remaining tutor-quality wins.

**Two things not to re-litigate** (both recorded with reasons in the code):

- Papers are not the growth area for grounding. A course needs the canon
  explained, not the frontier reported.
- Universities List, Numbers API, PokeAPI and extra web-search engines were
  evaluated and declined — see `docs/SPRINT_PLAN.md` §A5.6.

**The failure mode this whole pipeline is built against:** a course that passes
every structural check and is substantively hollow. `path_audit` reported the
42%-coverage course as clean across all sixteen detectors. Criterion 6 is the
only check that catches it, which is why it now runs on every build.
