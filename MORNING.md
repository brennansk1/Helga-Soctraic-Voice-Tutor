# Morning handover — 2026-08-24

## Start here

```bash
cd ~/Desktop/helga-live && docker compose up -d
```

Then open **http://localhost:5050**.

*(The stack was already up and healthy when this was written — you may not need
to start anything.)*

**Run it from `~/Desktop/helga-live`, not from `Desktop/Helga-Soctraic-Voice-Tutor-main`.**
That matters — see "Where your work lives" below.

---

## The SQL course

**Advanced SQL for analytics engineering** — `course_7c8e2bce` — **status: ready**,
so it opens straight from the courses list.

99 concepts across 6 modules, built at scope 3 / mastery 4 / starting-from 2, so
it is Tier 1 "employable", not a beginner tour.

| Module |
|---|
| CTE Structure and Caching Semantics |
| Window Function Clause Order and Partitions |
| Explicit Frame Definitions and Row Ordering |
| Set Operations and Data Reconciliation |
| Recursive CTEs and Tree Traversal |
| Execution Plan Analysis and Query Optimization |

**101 concepts hydrated, 0 failures.** Then 2 scaffolding concepts were pruned
and every remaining concept re-typed with the corrected rules:

| | before the fixes | after |
|---|---|---|
| `TOOL_BOUNDARY` (tutor REFUSES to answer) | 12 | **0** |
| `UNKNOWN` (no teaching guidance) | 2 | **0** |
| `MECHANISM` | 59 | 72 |

Zero `TOOL_BOUNDARY` is the right answer for pure SQL — there is no vendor GUI
to choose between. 67 concepts typed by pattern, 32 by the model, none left
unknown.

**Verified by reading a real tutor turn**, on the concept that was broken:

> **ROWS vs RANGE Distinction** — kind `MECHANISM`, 11,773 chars of content
>
> "`ROWS` counts physical positions, treating duplicates as separate entities,
> while `RANGE` groups all rows sharing the same sort value into one logical
> unit.
> **If you have three rows all dated "2023-01-01" and ask for the "previous
> 1 row", would `RANGE` return just one of them, or all three?**"

Before tonight's fix this concept was typed `TOOL_BOUNDARY`, whose guidance
tells the tutor **not to answer** — so the single most important distinction in
window functions would have been met with a refusal.

**A good place to start:** module 3, *Explicit Frame Definitions and Row
Ordering*. It is where the ROWS/RANGE material lives and it is the part of SQL
most people never actually learn.

---

## Where your work lives, and why it moved

Every domain package and all 270+ commits of this session were checked out in
**`/tmp/helga-main`**. macOS clears `/tmp` on reboot. The commits themselves
were safe (they live in `Desktop/Helga-Soctraic-Voice-Tutor-main/.git`), but the
working tree and the course data were not.

I moved the whole worktree, data included, to **`~/Desktop/helga-live`** with
`git worktree move`. Branch is `feat/nail-consolidation`.

**Two separate course libraries now exist:**

| Location | Courses | Notes |
|---|---|---|
| `~/Desktop/helga-live/data` | 6 | recent work + the new SQL course |
| `Desktop/Helga-Soctraic-Voice-Tutor-main/data` | 19 | your older library, early August |

I did **not** merge them. Merging two SQLite libraries is easy to get wrong and
hard to undo, and it is your call, not mine. The DB was backed up before I
touched anything: `data/backups/pre_sql_build_20260823_224910.db`.

---

## What changed tonight

**Two classification defects, found by reading what a real course was typed as**

- `TOOL_BOUNDARY`'s pattern opened with a bare `vs`, so **"ROWS vs RANGE
  Distinction"** was labelled *do-not-answer*. That kind's guidance tells the
  tutor to refuse, so the single most important distinction in window functions
  would have gone untaught. A boundary decision now needs a real layer on both
  sides of the comparison.
- Worse: `_KIND_BRIEF` never defined `TOOL_OPERATION` or `TOOL_BOUNDARY` while
  the schema accepted them. The model was handed two enum values it had never
  been told the meaning of and guessed — "Index Scan Types", "Set Operation
  Efficiency" and "Adjacency List Traversal" all came back as refuse-to-answer.

Twelve concepts were wrongly typed; after the fix, zero.

A test now fails whenever any domain can answer a kind its prompt does not
define. This was the seventh instance of "component extended, reader left
behind" in this project.

**Padding is no longer shipped as content**

The builder filled empty lessons with `"{lesson} Part N"` stubs, on the reasoning
that a generic concept beats a hole in the path. It does not: the course
advertised 108 concepts, five of which were dead ends whose only objective was
"Understand ... Part 2 Lesson 3". Those are now pruned before classification, so
no model time is spent typing a concept about to be dropped.

**A part-built course can be finished instead of rebuilt** *(new feature)*

`hydrate()` marks a course `partial` if even **one** concept in a hundred comes
back a stub, and the UI rendered anything not `ready` as a disabled card. The
only way forward was Delete and rebuild — discarding every concept that *had*
hydrated, which on this hardware is hours.

Hydration already skips concepts that have content; nothing exposed it. There is
now a **"Resume build"** button on `partial` / `failed` cards that retries only
what failed. `ready` is refused outright, and a second click or second tab cannot
start a competing hydrator over the same files.

**An unfinished course was being deleted on every restart** *(the serious one)*

`clean_failed_courses` runs on every RAG service import and deleted any course
in `{failed, hydration_failed, building, skeleton}` outright — directory and
SQLite row, no prompt, no backup.

Hydration is resumable; it skips every concept that already has content. So a
course stopped at 60 of 100 concepts was hours of model time being thrown away,
and it made the new "Resume build" button unreachable for most of the states it
was written for. Worse, the stale-build reaper *fed* it: the reaper marks an
abandoned build `failed` so it stops showing as in progress, and the next RAG
start then destroyed it.

This was not theoretical. **Your SQL course sat at `skeleton` for five and a
half hours while it hydrated.** Starting the stack in that window would have
deleted all 101 concepts. I found it only because bringing the containers up at
04:29 silently removed a *different* course (`course_8e382826`, an abandoned
"causal inference" skeleton) — directory and row, no log entry in sight.

A course with even one hydrated concept is now preserved and logged. Empty
shells and corrupt/missing `structure.json` are still cleaned up, because
nothing was generated and nothing is lost.

**History knew eras but not the things people type**

Across 24 realistic topics, 23 routed on keywords alone. The miss was "The Roman
Republic" — no history teaching at all, while "Ancient Rome" and "The Roman
Empire" worked. Added named polities and events. A bare `roman` is deliberately
**not** among them: "Roman numerals" is maths.

---

## Checks run

| Area | Result |
|---|---|
| SQLite integrity / FKs / WAL | ok, clean, wal |
| SQLite ↔ disk consistency | zero orphans both directions |
| Indexes | present (`idx_concepts_course`, `idx_progress_review`, …) |
| Model config split-brain | consistent across compose, code, and what is resident |
| `main.py` model false-green | **closed** — exact-tag match now |
| Domain routing | 24/24 realistic topics |
| Resume-where-you-left-off | works end to end; card reads "Continue: ‹concept›" |
| Stale-build reaper | runs at startup; no more permanently spinning cards |
| Full test suite | **3099 passed, 31 skipped, 0 failed** |
| Stack running | 5 services up and healthy on :5050 |
| Course loads in the UI | 6 modules, 99 concepts |
| Domain layer inside the container | imports; SQL routes to computer_science; all 99 kinds correct |

---

## Known gaps — read this before trusting the content

1. **The SQL course has zero citations.** I built it outside Docker, so the
   research/SearXNG service was unreachable and every concept is `Source:
   llm-only`. The content I spot-checked was accurate on subtle points (NULL
   equality in set operations, `EXCEPT` asymmetry, `INTERSECT` precedence), but
   it is model-generated and unverified. Treat specifics you would *act* on with
   more care than the mechanics. Courses built from the UI with the stack up
   will gather sources normally.

2. **`level_verified` will read false.** The depth contract failed on ~100% of
   concepts, and it is not because the content is thin. It asks for
   `primary_source` / `any_source` (see gap 1) and for `named_result` /
   `formal_definition` — maths-shaped requirements a correct SQL concept cannot
   satisfy. The contract needs to be domain-aware; that is a real piece of work,
   not a late-night edit.

3. **Title-only LLM classification is proven on computer science only.** It was
   replicated mechanically to maths, history and science but never run against
   the live model there.

4. **The career checklist's commercial tiers** (pricing, discovery, contracts,
   positioning) have no domain and get generic teaching.

5. **270+ commits are unpushed**, on `feat/nail-consolidation`.

---

## Useful commands

Read one real tutor turn for any concept — the only check that answers
"can it actually teach this?":

```bash
cd ~/Desktop/helga-live && python3 tools/tutor_turn.py "ROWS vs RANGE" "I'm not sure"
```

Watch a build:

```bash
tail -f ~/Desktop/helga-live/hydrate.log
```
