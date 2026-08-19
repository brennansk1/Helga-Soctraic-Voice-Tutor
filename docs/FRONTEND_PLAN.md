# Frontend plan — marketing-grade UI for the university

_Recon 2026-08-19. This is the plan the build follows; each stage is checked off
as it lands._

## What recon found

**The foundation is better than expected.** `design-system.css` (1,139 lines) is
a real token system — both themes enforced ("a token defined for one theme is a
light-mode constant waiting to look broken"), a motion vocabulary with durations
capped ~250ms, self-hosted type. `base.html` has a proper three-zone nav. This
is refinable, not replaceable: **no overhaul of the design system.**

**The gaps are features, not polish:**

| gap | severity |
|---|---|
| **No degree UI exists at all** — `program.py`, `build_scheduler.py`, registration, prereq DAGs: all backend, no API route, no page | the flagship feature is invisible |
| Course creation is a plain 3-step wizard — none of the new machinery (book upload → structure, presets, scope warnings) is presented | the funnel undersells the product |
| The build view has never been watched in a browser (Mode A §4 0b) | the most engaging moment is unverified |
| Trust surface (sources, confidence) not shown in session view (Mode A §4 2) | credibility feature built, hidden |
| No nav lock during a build | a user can wander mid-build with no warning |

## Mode A pending items (criterion 1 of this task)

From `MODE_A_STATUS.md`, still open and NOT frontend:
1. **Nothing rebuilt since the grounding chain changed** — the 40-min overnight
   rebuild + criterion-6 re-run against the 42% baseline. *(queued run, not code)*
2. **Voice never exercised end-to-end** (done-criterion 2).
3. **A6**: Ollama idle-eviction, tts container 2048M for a 319MB model.
4. **A7**: circuit-breaker fallback, soak test, backup drill.
5. **n=0 through the full gate** — golden matrix unrun.

Frontend items this plan absorbs: §4 0b (watch /build), §4 2 (trust surface).

## The build, staged

### Stage 1 — Course-creation carousel (the funnel)
`create.html` + `create.css` + `create.js`. Horizontal carousel, arrow keys +
click arrows left/right, dot indicators below, one decision per page:

1. **Source** — upload a book (EPUB/PDF/MD/TXT, drag-drop, shows the parse:
   "59 chapters, no parts → one lesson per chapter") **or** build from research.
2. **Template** — **degrees front and center**: Associate (20 courses/2yr) and
   Bachelor's (40/4yr) as large cards, then College Course, Seminar, Quick
   Overview as smaller cards. Each card states its real ladder from the
   verified taxonomy.
3. **Subject & level** — topic, mastery slider with the depth contract's real
   words per level, teaching style.
4. **Scope check** — live: calls the evidence sweep, shows `scope_fit` verdict
   and the conceptual-sufficiency tier *before* committing (the disclaimer is a
   credibility feature — show it here, not after).
5. **Review & create** — the whole plan restated + a large CREATE button.
   For degrees: states that courses build lazily, one ahead of the learner.

Book uploads skip pages 2–4 (the book decides structure) and go straight to a
parse-preview page → create.

### Stage 2 — Live build view (the show)
Upgrade `build.html` to consume the status stream that now exists:
`RESEARCH:*` (sources found, named), `CHECK:SCOPE`, `STRUCT:*` (skeleton growing
as a live tree), `BOOK:READING:n:total` (chapter progress bar), hydration
per-concept ticks, `ASSET:*`, gate verdicts as they land. Design: a vertical
timeline of phases with the current one expanded, a growing course-tree
visual, and honest failure states ("still being prepared", named warnings).

### Stage 3 — Degree map (the flagship)
`degree.html`: **zoomable prerequisite DAG** — pan/zoom SVG, terms as columns,
courses as nodes, prereq edges drawn, status colour (built / building / locked /
elective-choice / complete). **Elective choice moment**: at 70% through a
course, the N candidate cards appear (the registration mechanic); choosing locks
and triggers the lazy build — the generation-status badge on each node makes
"the university builds itself as you go" *visible*. Needs a thin API:
`GET /api/program/<uid>` (plan + statuses), `POST /api/program/<uid>/choose`.

### Stage 4 — Home + nav + tabs
Home: hero → "continue learning" (real progress) → degree progress strip →
recent courses. Nav: build-in-progress pill in the bar, **soft lock** during
creation (leaving warns, build continues server-side — honest, not jailing).
Chat/learn: trust surface (sources + confidence on each concept), aids polish.
Other tabs: consistency pass on tokens, empty states, loading states.

## Rules carried from the codebase
* Never `innerHTML` untrusted values (the aids renderer's rule, applied
  everywhere).
* Every long operation shows a counter, not a spinner ("a spinner with no
  counter is indistinguishable from a hang").
* Failure states are named and honest; no silent fallbacks.
* Both themes for every new token; motion under 250ms; self-hosted assets only
  (offline appliance).
