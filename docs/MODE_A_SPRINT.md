# Mode A — the closing sprint

**Written 2026-08-19, mid-flight: nine agents are landing work on
`feat/nail-consolidation` as this is written.** This doc is the single plan for
taking Mode A from "5 of 7 criteria verified" to **releasable**: every
criterion verified, at least one course passing the full quality gate, the
known-bug backlog empty, and the failure modes named rather than silent.

It supersedes the task lists scattered through `MODE_A_STATUS.md` §4/§6. The
scoreboard stays the *measurement* record; this is the *work* record. Nothing
here is done until the test beside it passes.

---

## 0. Definition of released

Mode A is releasable when ALL of the following are true, each backed by a
command:

| Gate | Command | Today |
|---|---|---|
| All 7 done-criteria VERIFIED | per-criterion, below | 5/7 |
| ≥1 course passes the full conjunctive quality gate | `tools/golden_courses.py evaluate` + `tools/syllabus_check.py` | **n=0** |
| Full suite green | `python3 -m pytest tests/ -q` | 1842 pass (pre-agent-landings) |
| Zero KNOWN bugs open | §3 + §4 below empty or explicitly waived | ~14 open |
| No silent-failure paths in the audited surface | §5 sweep re-run clean | in progress |
| Tutor quality at/above calibrated baseline | `tools/helgabench.py --repeat 3 --compare docs/baselines/helgabench_a1_calibrated.json` | unmeasured since judge fix |
| Honest instruments | `tools/helgabench.py --self-test` before trusting any score | passes |

"No bugs" operationally means: **no known bug**, and the classes of bug we
keep finding (silent failure → fake success; substring matching; single-theme
styling; unwired backends) each have a guard or a sweep that would catch a
recurrence.

---

## 1. State snapshot (2026-08-19, evening)

The 7 criteria:

| # | Criterion | State | What closes it |
|---|---|---|---|
| 1 | Genuine depth | VERIFIED | — (re-verify after rebuild, §6.1) |
| 2 | Socratic, voice or text | **BUILT, never run** | §6.3 voice run |
| 3 | See where content came from | VERIFIED + **UI shipped today** (trust panel) | re-verify populated after rebuild |
| 4 | Reviewed on schedule (FSRS) | VERIFIED | **at risk**: quiz-grading bug damages FSRS (§2, agent G) |
| 5 | All three modes reachable | VERIFIED | — |
| 6 | Bring your own material | **PARTIAL** | dead Build button (§4.1) + one real book end-to-end (§6.2) |
| 7 | Every control does what it says | VERIFIED, **regressed in spirit** | the dead Build button and never-emitted `course_ready` are exactly this criterion; closes with §4.1 + agent F |

Landed today, verified: schema v10→v17 on the real DB (backed up); the
grading/`_detect_ignorance` substring bug (worst find of the day — correct
answers graded as "I don't know"); command substring matching ("the stop codon
terminates translation" destroyed the session); AUTO-10 write-order fix +
`tools/reconcile_courses.py`; trust panel; startup preflight (agent, landing);
`/library` seen working; theme/contrast guards.

---

## 2. Lane 1 — in flight right now (nine agents)

Each row lands as its own commit. **Verify on landing** = run before accepting;
none is done on the agent's word.

| ID | Scope (files owned) | Delivers | Verify on landing |
|---|---|---|---|
| A | *(read-only, done)* core bug hunt | 12 findings → §3/§4 | n/a — findings recorded below |
| B | *(read-only, done)* web bug hunt | 12 findings → §3/§4 | n/a |
| C | docker-compose, .env, model files | A6: `OLLAMA_KEEP_ALIVE` idle-eviction via server env; tts container right-size; Kokoro de-dup **(report-only if risky)** | `docker compose config` parses; idle RSS measured before/after; no model file deleted without byte-identical proof |
| D | llm_utils.py, llm_client.py | A7: circuit breaker; "unreachable" ≠ "bad JSON" as named errors | `pytest -q -k "llm or client"`; breaker unit tests pass; no second retry layer stacked on `llm_generate_json` |
| E | storage.py, tools/reconcile_courses.py — **LANDED f19d8bb** | AUTO-10 closed; reconcile tool | done: 248 targeted tests pass; dry-run is SQLite-enforced read-only |
| F | courses.js, wizard.js, degree.js, build-view.js, build-guard.js, create.js, practice.js, schedule.html, practice.html | `course_ready` never emitted → client-side completion; onclick-injection XSS; create.js lock-on-502; demo-plan-over-real-data; target_date (client half); Practice quiz dead end | `pytest tests/web -q`; browser: build completes → link appears → lock releases; `Newton's Laws` card delete works; core down → no lock armed |
| G | librarian.py | LLM outage during quiz grading must not report FAIL nor touch FSRS; empty-success sweeps | `pytest -q -k "quiz or grade or card"`; new tests: outage ≠ FAIL, FSRS untouched |
| H | course_builder.py | all-stub course can't be "ready"; dedup no longer deletes "Logistic Regression" for sharing a word with "Linear Regression" | `pytest -q -k "builder or dedup or hydrat"`; casualty pairs survive; true dupes still removed |
| I | book_reader.py, book_source.py, program.py, taught_ledger.py | equal-start-page ToC chapter loss; zero-concept lesson counted as success; cross-slot degree duplicate → ProgramError; degraded digest cached permanently; embedder provenance | `pytest -q -k "book or program or ledger"`; equal-page fixture; "Statistics in two slots" plans instead of raising |
| J | startup_preflight.py (new), app.py, resources.js/.css | startup hardware preflight + blocking-but-honest UI gate; clears itself when room returns | `pytest -q -k "preflight or memory"`; browser: blocked/degraded/ok states; guard passes |
| K | library_api.py (new), library.js, library.css, static/img | multi-source search (IA + Gutendex + Wikibooks, live-verified), covers proxied+cached, blank fallback (IA fakes 200 for missing covers — detect by hash), filters, dedup, per-source status, detail view | `pytest -q -k "library or cover"`; browser: search shows thumbnails + source labels; one source down ≠ empty results |

**Merge/integration plan** — all nine land on one branch:
1. As each lands: run its "verify on landing" column only.
2. After D and J both land (they may brush against shared imports):
   `pytest -q -k "llm or client or preflight"` together.
3. After ALL land: `python3 tools/css_theme_guard.py`, re-run the route diff
   (§5), then **one full suite** — the only full run of the sprint.
4. Conflicts: files were partitioned up front; the known seam is **app.py**
   (agent J owns it now; the §4.1 queue is serialized behind J).

---

## 3. Lane 2a — verified bugs, assigned to nobody yet (the backlog)

Recorded here with file:line because the agent reports live nowhere else.
Every one of these blocks "no known bugs".

### The app.py queue (serialized: start only after agent J lands)
1. **`/api/books/build` is a stub** — validates availability then returns
   `{'status':'started'}, 202` with no thread, no core POST, no build_state
   write (app.py:493-530). *The* criterion-6 blocker. Wire it to the real book
   pipeline (`build_from_book`), reusing the single-build lock and status
   stream. Test: POST → build_state active → status stream flows → course dir
   appears (fixture book, no LLM needed for the wiring test).
2. **Availability badge vs build gate disagree** — `:619` accepts any `.txt`,
   `:517` requires `_djvu.txt`; green badge, then 422 (fold into #1).
3. **`/api/programs` proxy returns 200+empty on failure** (`:1143`) — agent F
   fixes the client; the proxy should still 503 so *every* client sees a named
   failure. Same for **`/api/due_cards`** (`:1465`, empty-success) — pairs with
   agent G's librarian sweep.
4. **`target_date` dropped by the proxy** (`:1462`) — forward it; then
   librarian must read it (needs a small `get_due_cards(target_date=)` change —
   coordinate with G's landed code).
5. **`student_id` not forwarded** on `/api/set_active_course` (`:1078`) and
   `/api/upload_epub` (`:1287`) → child B's upload lands on the default FSM.
6. **Register agent K's `library_api` blueprint** (one line, K's file header
   says which).

### fsm_logic.py queue (mine; file is free now)
7. **Review mode grades against nothing** — spaced-rep grading reads
   `self.last_question` (only ever set in the Socratic path, `:2605`) and
   `self.current_card.get("text")` (key never exists; cards carry
   `front/back`, `:3021-3027` vs `:3057/:3078`). Review answers are graded
   against a stale question and empty reference content, returning
   confident-looking grades. **Criterion-4 blocker in practice.**
8. **`_parse_grade_response` defaults a missing grade to 3 (passing)**
   (`:2705`), its `\{[^{}]*\}` regex can't match nested JSON so a valid
   grade-1 response can parse as a pass; and `graded`/`grade_source` — the
   outage-vs-assessment distinction — are **never read**, so infra-fallback
   grades feed FSRS as real assessments.
9. **SKIP on the last concept re-teaches the skipped concept forever** —
   `_advance_without_completing` repopulates the queue from not-completed
   concepts, which includes the one just skipped (`:2113-2138`).
10. **PAUSED has no transition() handler** — partially mitigated today (pause
    now requires an explicit command and speaks), but the state itself still
    swallows unknown events; add a handler or route unhandled input to a hint.

### Lower-confidence (verify before fixing)
11. course_builder `scope` str joined char-by-char into the prompt
    (`:1507` vs `:2837/:3354`) — type mismatch certain, damage unverified.
12. Resumed builds stamp `depth_contract.met_pct` from remaining-only counts
    (`:4461`) — can mark a mostly-unverified course `level_verified: true`.
13. book_reader `doc.close()` not in `finally` (`:419`) — handle leak masked
    by the blanket `return None` at `:462`.

---

## 4. Lane 2b — features required for "releasable", not yet started

1. **Book → course, wired end to end** = §3 item 1 + agent K's UI + agent I's
   pipeline fixes. When wired, run §6.2.
2. **A4 pedagogy floor** — adaptation 2.8, "Misconception holder" 2.4; judge
   notes say *lecturing instead of questioning*. Cheapest wins per the
   scoreboard: the dialogue contract + learner-history personalisation
   (A4.1a/b). Measure only via median-of-3 (`--repeat 3`), never a single run
   (±1.4/5 noise floor).
3. **Backup/restore drill** (A7) — `helga.db` + `data/courses` snapshot,
   restore on a clean tree, courses open. Script it: `tools/backup_drill.py`.
4. **Cover cache bound** — agent K caches covers on disk; add size cap +
   eviction so the offline cache can't grow unbounded (folds into K follow-up).
5. **Orphan cleanup decision** — `tools/reconcile_courses.py --fix` removes 10
   dead rows; the 6 protected `ready` rows need a human call (user decision:
   restore or `--include-ready`).

---

## 5. The recurring-bug-class sweeps (run at sprint end, then keep)

These are the four classes that produced nearly every bug this week. Each has
a mechanical check; all four must be clean at release:

| Class | Sweep | Status |
|---|---|---|
| Token/theme "right in light mode by accident" | `python3 tools/css_theme_guard.py` | guard exists, clean |
| Backend↔frontend orphans (routes nobody calls / calls nobody serves) | route-diff script in `docs/BACKEND_FRONTEND_SWEEP.md` header | re-run after agents J/K/F land |
| Silent failure → fake success (`except: return [], 200`, stub 202s, demo-data-over-real) | `grep -rn "return jsonify.*\[\].*200\|status.*started" services/` + eyeball; agents F/G fixing known set | re-sweep after landings |
| Substring matching on user utterances | `tests/core/test_utterance_matching.py` | guard exists, passes |

Plus: contrast sweep (both themes, composited) — 5 remaining findings are all
harness alpha-blindness, verified by hand; re-run only if palettes change.

---

## 6. Lane 3 — the long runs (deliberately excluded from tonight; required for release)

In dependency order. 1–2 unattended; queue overnight.

1. **THE run that matters** (~40 min): rebuild Pythagoras with the new
   grounding chain + hydration + ledger, then
   `tools/syllabus_check.py` vs the 42% baseline. Also the first course ever
   built on schema v17 — confirms sources/claims actually populate, which
   makes the trust panel show real data for the first time.
   *If coverage doesn't move meaningfully above 42%: the brief is fetched but
   unused — look at `course_builder._build_inner` prompt injection.*
2. **One real book end-to-end** (~1 hr): OpenStax *Biology 2e* (PDF) and one
   Gutenberg EPUB through the WIRED Build button to a persisted course;
   `tools/book_course_qa.py` verdict BOOK_FAITHFUL. Closes criterion 6.
3. **Voice end-to-end** (~15 min, needs a human + mic): mic → `/api/stt` →
   transcript → FSM grades it → TTS answer audible. Closes criterion 2. Also
   covers the STT service that has never been load-tested.
4. **Quality battery** (~45 min): `helgabench.py --self-test` then
   `--repeat 3 --compare helgabench_a1_calibrated.json`; `sycophancy_probe`;
   `persistence_probe`; `tier_probe`. Median-of-3 discipline throughout.
5. **Soak** (A7): leave the stack up 24 h with the memory guard logging;
   assert no drift past the 15.0 GB ceiling and the breaker never wedges open.
6. **Golden matrix** (the n=1 problem): one course per preset tier through the
   full gate — the real evidence base the scoreboard says was never built.

---

## 7. Release checklist (the final hour)

```bash
python3 tools/css_theme_guard.py                       # theme classes
python3 -m pytest tests/ -q                            # FULL suite, once
python3 tools/reconcile_courses.py                     # stores converge (exit 0)
python3 tools/helgabench.py --self-test                # instrument honest
python3 tools/golden_courses.py evaluate               # gate verdicts
python3 tools/path_audit.py                            # structural
# + §5 sweeps, + §6 results recorded in MODE_A_STATUS.md with dates
```

Then update `MODE_A_STATUS.md`: every criterion row VERIFIED with its command
and date, and the §4 "genuinely NOT done" list empty. That file remains the
scoreboard; when it shows 7/7 with a passing gate course, Mode A is done.
