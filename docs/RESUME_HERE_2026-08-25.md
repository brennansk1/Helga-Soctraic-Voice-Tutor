# Paused mid-sprint — what is done, what is left

Written at the moment work paused to design Stage 4. Everything below is the
live state of the machine and the branch, not a plan.

## Running right now — do not kill

- **Advanced SQL build: 66 of 83 concepts.** Started ~08:1x MDT, writing a
  concept every few minutes, roughly an hour left. It has already been killed
  and resumed several times today by container restarts; each restart costs the
  in-flight concept and a resume.
- `helga-sqlcheck` — PostgreSQL 16.13, 57 MB, port 55432. The SQL verifier
  measures against it. Safe to stop; `python -m services.core.sql_ground_truth`
  re-measures when it is back.

## Committed but NOT deployed

These three are on the branch and correct in tests. They take effect only when
`helga-core-logic` restarts, and restarting it kills the build. **Deploy them
when the build finishes.**

| commit | what |
|---|---|
| `d9a8c5d` | SQL claims checked by execution, wired into the hydration gate |
| `793f547` | background yields bandwidth to a live learner |
| `a1e3971` | research keeps source text so claims can be checked later |

After the restart, confirm the gate is live: a hydration log line reading
`[FALSE]` is the checker working. Silence is ambiguous — see "unchecked is not
clean" below.

## Deadline-bound: evidence expires tonight

`sources.passage` was empty for all 529 rows (fixed in `a1e3971`, but the fix
only helps future builds). The research cache still holds this morning's
material — 1,291 rows, **24h TTL from ~08:13 MDT**, so it is gone around 08:00
tomorrow.

Recovering it is possible but partial: of 246 rows that unpickle, only 55 carry
text (33 `primary`, 16 `textbook`, 6 `wiki`). The cache is keyed by a hash of
the query, not by concept, so mapping entries back to concepts needs the same
key derivation the research service uses.

**Decision needed:** backfill from the cache before it expires, or re-run
research for the two courses after Stage 4 exists. Backfill is cheap and lossy;
re-running is expensive and complete.

## Content defects, found and located, not yet fixed

Five concepts state things PostgreSQL contradicts. Verified by execution, not
opinion:

| course | concept | what it says | what the engine does |
|---|---|---|---|
| SQL | `con_a77be12a` | ASC puts NULLs first; DESC puts them last | exactly backwards, both halves |
| SQL | `con_cf3e73a1` | "PostgreSQL puts NULLs first in ASC" | NULLs sort last under ASC |
| SQL | `con_95030479` | "Standard SQL guarantees short-circuit evaluation" | evaluation order is unspecified |
| Advanced SQL | `con_c3e1094e` | "ORDER BY revenue DESC ... NULLs will appear last" | NULLs appear first under DESC |
| Advanced SQL | `con_97d1e4ed` | "NULL = NULL is true" | UNKNOWN, and the reason given is false |

`con_cf3e73a1` and `con_c3e1094e` were **missed by the human audit** and found
by execution.

Also open from the audit, located but unfixed: model deliberation left in the
taught text (6 concepts), `## Core Explanation` replaced by a one-line stub
(2 in SQL, 6 of 9 in Advanced SQL), off-topic citations (21% overall),
"Grounding unavailable" printed as lesson text (5), "Part 2" in the curriculum
path (9), missing `## Analogies` (2).

The write-path guards now REJECT all of these shapes, so they cannot recur.
The already-written concepts need re-running — which is Stage 4's job.

## Blocked on the owner

**P0.1 security.** Ports 5002/5003/5006 bind `0.0.0.0`; `pipeline_api` can
write course content with no auth; `DELETE /api/courses` has no auth; a tunnel
origin (`learn.thunderheadonline.com`) appears in the logs. Bind to localhost,
or add auth — not a decision to make unilaterally.

## Measured today, worth not re-deriving

- **A reserved slot is not reserved bandwidth.** Two 4-token generations took
  **145 seconds** during a build, with the slot free and dispatch immediate.
  The machine runs decode at ~85% of its 120 GB/s ceiling.
- **Model read throughput under load: 84 MB/s** — a 16 GB blob takes 3.4
  minutes. This is contention, not the SSD; re-measure idle before concluding
  anything about load times.
- Disk is **94% full**, 26 GB free.
- Local time is **MDT**; container logs are **UTC**. A six-hour gap between a
  log line and a file mtime is that, not a stall.

## Unchecked is not clean

The SQL verifier returns `(findings, checked)`. An empty `findings` with an
empty `checked` means nothing was verified. Anything reporting on it must say
"unchecked", never "passed" — reporting clean coverage on unverified content is
how `fact_check.py` ran 38 times over a course with seven errors and found
none.

## Remaining sprint queue

`docs/SPRINT_2026-08-25.md` holds all ~90 findings in priority order. Not yet
started: P0.7 (research thread pool starves), P2.1–P2.9 (inert guards, grade 5
unreachable, PyMuPDF AGPL in an Apache repo), P3.6–P3.11 (research parallelism
and caching), P4 (polish).

Then the actual goal: **live Mode A testing against `docs/READY_FOR_USE.md`**,
as a beta tester driving the real UI — including the sections marked *(visual)*,
which must be looked at, not inferred from a 200.
