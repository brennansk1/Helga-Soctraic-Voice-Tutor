# Helga — Course Creation Pipeline Optimization Sprint Plan (Sprint S1)

> **Written 2026-08-05.** Consolidates the dual-model audit findings (Gemini 3.1 Pro × Claude Opus 4.6) into a single, execution-ready sprint plan. Complements `docs/SPRINT_PLAN.md` by providing the dedicated engineering backlog for the 3-phase course creation engine (`Skeleton Creation` → `Content Hydration` → `Asset Collection`).

---

## Executive Summary & Target Metrics

| Metric | Baseline (Current) | Sprint S1 Target | Primary Driver |
|---|---|---|---|
| **Skeleton Latency** | 4 – 19 minutes (29+ serial calls) | **1 – 3 minutes** (~3x speedup) | Module parallelization (`course_builder.py:L1736`) |
| **Skeleton JSON Reliability** | Soft parsing (`expected_type="list"`) | **100% Schema-Enforced** | Ollama `schema=` grammar constraint (`L1922`) |
| **Hydration Overlap** | `bg_slots=1` (effectively serial) | **`bg_slots=2`** (~30% throughput gain) | Overlapping research I/O with GPU compute |
| **Misconception Pedagogy** | Belief / Correction (partial) | **Full Refutation Text** | Added `Why it seems right` cognitive anchor |
| **Worked Example Scaffolding** | Full solution at all levels | **Worked Example Fading** | Faded completion problems starting at Bloom 3 |
| **Prerequisite Mapping** | Positional chain (last 5 titles) | **Topological Prerequisite DAG** | LLM-based causal dependency edge extraction |

---

## Work Packages & Task Backlog

### Epic 1: Phase 1 — Skeleton Creation Optimization

#### Task S1.1: Schema Grammar Constraints for Skeleton LLM Calls
* **Files:** `services/core/course_builder.py:L1922-L1928`, `L1960`, `L2030`, `L2150`
* **Problem:** Skeleton generation uses prompt instructions and soft parsing (`expected_type="list"`), lacking Ollama's `schema=` parameter. The asset collector docstring identifies `schema=AID_PLAN_SCHEMA` as the single biggest reliability driver.
* **Implementation:** Pass Pydantic/JSON schemas for module, unit, lesson, and concept lists into Ollama's `format` parameter via `llm_generate_json`.
* **Definition of Done:** 0 JSON parse retries or fallback stubs across 10 test course builds.

#### Task S1.2: Module-Level Skeleton Parallelization
* **Files:** `services/core/course_builder.py:L1736-L2251` (`_build_substructures_progressive`)
* **Problem:** Skeleton generation executes sequentially across 4 modules × 2 units × 2 lessons = 29+ serial LLM calls, taking up to 19 minutes before hydration begins.
* **Implementation:** Wrap unit, lesson, and concept generation for independent modules in a `ThreadPoolExecutor`.
* **Definition of Done:** Skeleton generation wall-clock time drops below 3 minutes on Mac Mini M4 Pro.

#### Task S1.3: Causal Prerequisite DAG Generation
* **Files:** `services/core/course_builder.py:L2503`, `services/research/curriculum_research.py:L131`
* **Problem:** Prerequisites are assigned as the previous 5 concept titles in syllabus order—a linear list rather than a learning dependency graph.
* **Implementation:** Prompt the skeleton generator to extract explicit prerequisite dependency pairs `(concept_A -> concept_B)` based on conceptual prerequisites, and validate for cycles using a topological sort.
* **Definition of Done:** Concepts have explicit, non-positional prerequisite IDs forming a valid DAG.

---

### Epic 2: Phase 2 — Content Hydration Optimization

#### Task S2.1: Full Refutation Text Misconception Template
* **Files:** `services/core/course_builder.py:L3852-L3854`
* **Problem:** The `## Misconceptions` template uses `Belief` and `Correction` only, missing the cognitive anchor explaining why the error is intuitively tempting (Tippett 2010, Sinatra & Broughton 2011).
* **Implementation:** Update the concept doc prompt template:
  ```markdown
  ## Misconceptions
  - **Belief**: [Common student misconception]
    **Why it seems right**: [Intuitive or perceptual reason for the error]
    **Correction**: [Scientific/logical explanation of why it is incorrect]
    **Key distinction**: [Crucial concept boundary the student must master]
  ```
* **Definition of Done:** 100% of hydrated concept docs contain all 4 refutation fields.

#### Task S2.2: Worked-Example Fading at Bloom $\ge 3$
* **Files:** `services/core/depth_contract.py:L63-L69`, `services/core/course_builder.py:L3805-L3810`
* **Problem:** The pipeline generates full worked examples at every level. Cognitive Load Theory (Renkl 2002, Atkinson 2003) requires scaffolding to fade as expertise grows.
* **Implementation:** 
  - Bloom 1–2: Full worked example (complete steps & solution).
  - Bloom 3–4: Faded worked example + **Completion Problem** (Step 1 given, Step 2 left for student).
  - Bloom 5–6: Full exercise problem (unassisted).
* **Definition of Done:** `depth_contract.py` validates completion problems for Bloom 3–4 and full exercises for Bloom 5–6.

#### Task S2.3: Hydration Concurrency & I/O Overlapping (`bg_slots=2`)
* **Files:** `services/core/course_builder.py:L2574-L2579`, `services/core/gpu_gate.py`
* **Problem:** `_bg_cap` defaults to `bg_slots=1`, making hydration serial. Network-bound SearXNG research calls stall GPU generation.
* **Implementation:** Increase default `bg_slots` to 2 for hydration, allowing Concept N's network research to overlap with Concept N-1's GPU inference.
* **Definition of Done:** Hydration throughput increases by $\ge 30\%$ without triggering GPU gate admit timeouts.

#### Task S2.4: Research Circuit Breaker & Concept Checkpointing
* **Files:** `services/core/course_builder.py:L2612`, `services/common/build_state.py`
* **Problem:** Consecutive research service outages cause all concepts to ship ungrounded without stopping the build.
* **Implementation:** Implement a circuit breaker that pauses hydration after 3 consecutive network research failures. Save atomic per-concept hydration checkpoints in `build_state.json`.
* **Definition of Done:** Hydration resumes from the exact failed concept on restart, and research outages halt the build gracefully.

---

### Epic 3: Phase 3 — Asset Collection & Visual Aid Optimization

#### Task S3.1: Geometry Coordinate Validation at Build Time
* **Files:** `services/core/asset_collector.py`, `services/common/visual_aids.py:L404`
* **Problem:** Angle/geometry claims (e.g., `"right": true`) are verified at render time, but malformed coordinates can be saved to the database.
* **Implementation:** Run coordinate validation during asset collection before saving to the visual aid spec.
* **Definition of Done:** Invalid geometry specs are rejected and regenerated during course build.

#### Task S3.2: Faded "Scaffold" Visual Aid Slot
* **Files:** `services/core/asset_collector.py:L462-L517`, `services/common/aid_policy.py`
* **Problem:** Visual aids pose the question (`opening`) or show the steps (`worked_example`), but lack a dedicated slot for staged problem solving at Bloom 3.
* **Implementation:** Add a `scaffold` slot that renders the problem frame with blank/question-mark elements for completion tasks.
* **Definition of Done:** Bloom 3 concepts receive `scaffold` visual aids when `aid_policy` triggers.

#### Task S3.3: Thread-Safe Asset Library Writes
* **Files:** `services/core/asset_collector.py:L350-L411`
* **Problem:** Reads from `asset_library.json` are not protected against concurrent writes during parallel course builds.
* **Implementation:** Wrap `asset_library.json` read/write operations in an atomic file lock (`filelock`).
* **Definition of Done:** 0 JSON corruption errors during concurrent multi-course asset generation.

---

## Verification & Acceptance Gates

A sprint item is **DONE** only when its corresponding gate passes:

1. **Gate 1 (Skeleton Throughput & Schema):**
   - 10 test courses built end-to-end.
   - Skeleton wall-clock time $\le 3$ minutes.
   - 0 JSON parse retries or fallback stubs in skeleton logs.

2. **Gate 2 (Pedagogical Quality):**
   - 100% of concept docs pass the updated 4-field refutation text check.
   - Bloom 3+ concepts verified to contain faded completion problems.

3. **Gate 3 (Hydration Overlap & Grounding):**
   - Hydration throughput achieves $\ge 30\%$ speedup under `bg_slots=2`.
   - 0 silent ungrounded concept shipments during research service outages.

4. **Gate 4 (Asset Collection & Integrity):**
   - Visual aids verify `stage: 1` answer suppression and geometry coordinate validation.
   - 0 file locking collisions in `asset_library.json`.
