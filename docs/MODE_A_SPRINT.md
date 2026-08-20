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
| All 7 done-criteria VERIFIED | per-criterion, below | **6/7** — voice is the holdout |
| ≥1 course passes the full conjunctive quality gate | `tools/golden_courses.py evaluate` + `tools/syllabus_check.py` | **CLOSED** — course_f690297d, GATE: PASS (n=0 → n=1) |
| Full suite green | `python3 -m pytest tests/ -q` | **CLOSED** — 2096 passed, 32 skipped, 0 failed (2026-08-20) |
| Zero KNOWN bugs open | §3 + §4 below empty or explicitly waived | **CLOSED** — all 24 verified findings fixed; each recurring class has a guard |
| No silent-failure paths in the audited surface | §5 sweep re-run clean | **CLOSED** — 8 further empty-200s → named 503s |
| Tutor quality at/above calibrated baseline | `tools/helgabench.py --repeat 3 --compare …` | **OPEN** — 2.83 vs 3.27 (−0.44), but the baseline predates the nail-35b swap, so it compares two models as well as two codebases. Needs a fresh baseline on the current model. |
| Honest instruments | `tools/helgabench.py --self-test` before trusting any score | **CLOSED** — two instruments were themselves lying (stub marker; judge timeout cascade), both fixed |

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
| C | docker-compose, .env, model files — **LANDED 19b169d** | A6: 30m idle-eviction default, set in compose for BOTH LLM-calling services (a per-request keep_alive overrides the server env, so host-only would not have worked). Two scoreboard premises corrected by measurement: the tts 2048M cap is right (2.03 GB peak RSS on the max request; 1536M OOM-killed) and the "duplicate" Kokoro copies are two formats, torch + MLX, both load-bearing on an offline appliance — nothing deleted | done: compose parses, 145 targeted tests pass, both Kokoro caches verified intact |
| D | llm_utils.py, llm_client.py — **LANDED 650e1c1** | A7: shared breaker in `llm_breaker.py` (ships in every image; the old one lived in gpu_gate, unreachable from RAG, so the BUILD path had none). Named taxonomy: `LLMUnavailable` / `LLMBadOutput` / `LLMRequestRejected`; 4xx never trips the circuit; probe interval backs off | done: 37 breaker tests + 143 targeted pass. Unit-tested only — never observed against a real outage; soak stays open |
| E | storage.py, tools/reconcile_courses.py — **LANDED f19d8bb** | AUTO-10 closed; reconcile tool | done: 248 targeted tests pass; dry-run is SQLite-enforced read-only |
| F | (frontend JS) — **LANDED be8d632** | completion recognised from the stream that IS emitted + a shared probe so page and pill can't disagree; onclick XSS gone (delegated listeners + data-attrs); lock only arms on 2xx; only a clean empty list may show the demo plan; Practice quiz wired to the grader. Bonus find: build-view.js had a ReferenceError killing the whole view on its first message | done: 160 web tests, guard clean |
| G | librarian.py — **LANDED 4c97655** | outage → 503 `GRADING_UNAVAILABLE`, no grade key, FSRS untouched (asserted not-called); `/api/due_concepts` no longer lies "all caught up" on failure (503 total / `degraded` partial); same sweep fixed `/teaching_context`, `/api/gamification`, `/api/review_stats`. quiz.html already handles the shape — outage no longer pollutes the score | done: 153 targeted tests pass. 2 pre-existing isolation-only failures in that file's harness, verified pre-existing by stash |
| H | course_builder.py — **LANDED 2056649** | a stub is a failed concept (gate sees it; all-stub raises, minority-stub → "partial"); dedup is Jaccard@0.75 + filler-token rule + 25% module budget — every casualty pair survives, true dupes still die. Its two "ready"-overwrite handoffs applied in 0c71889. Also explained the earlier hydration-test mystery: the test was vacuously exercising the stub path | done: 84 targeted tests |
| I | book_reader.py, book_source.py, program.py, taught_ledger.py — **LANDED 998e2f5** | all five real: `_toc_spans` boundaries from distinct start pages (order-independent, can't be empty); zero-concept chapter now counted + `BOOK:WARN:CHAPTER_SKIPPED`; cross-slot dup costs one placeholder not a ProgramError; degraded digests never cached; embedder provenance truthful (reproduced first) | done: 24 new tests, 231 targeted pass; regressions verified by stash-per-file |
| J | startup_preflight.py, app.py, resources.js/.css — **LANDED 63f3ae0** | four-check verdict (installed RAM vs transient pressure kept distinct, with different remedies); blocking gate with a live counter, self-clearing; always inspectable in Settings. Thresholds derived from MEMORY_BUDGET, not hard-coded to this machine | done: 87 targeted tests; browser-verified both themes. Docker-context handoff applied (0dcfe45) |
| K | library_api.py, library.js/.css, img — **LANDED dc499b2, registered f1efdfa** | FIVE sources live-verified (IA, Gutendex incl. the 10.4s-vs-0.06s trailing-slash trap, Wikibooks, Wikiversity, OpenStax via a working keyless endpoint); covers proxied + disk-cached, digest-based placeholder detection, HTML-at-200 rejected, network failure never cached as a miss; degradation proven live when Gutendex genuinely timed out mid-test | done: 207 targeted tests, guard clean, browser-verified both themes |

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

### The app.py queue — **DONE 7b4d492** (Build button actually builds via the
one shared pipeline path; badge uses the gate's predicate; four fake-success
proxies now 503 with names; target_date forwarded AND read (librarian half in
the same commit); student_id on SET_CONTEXT / EPUB upload / creation_status;
base.html double build-guard.js load removed; web-ui image granted
services/common (0dcfe45); K's blueprint registered (f1efdfa))

### ~~The app.py queue~~ (original list, for the record)
1. **`/api/books/build` is a stub** — validates availability then returns
   `{'status':'started'}, 202` with no thread, no core POST, no build_state
   write (app.py:493-530). *The* criterion-6 blocker. Wire it to the real book
   pipeline (`build_from_book`), reusing the single-build lock and status
   stream. Test: POST → build_state active → status stream flows → course dir
   appears (fixture book, no LLM needed for the wiring test).
2. **Availability badge vs build gate disagree** — `:619` accepts any `.txt`,
   `:517` requires `_djvu.txt`; green badge, then 422 (fold into #1).
3. **Proxy layer converts RAG failures back into fake successes** — agent G
   fixed librarian, but app.py re-introduces the identical bug on transport
   failure (the most likely outage): `/api/programs` (`:1143`, 200+empty),
   `/api/due_cards` (`:1465`, `{'cards': []}` 200), **`/api/due_concepts`
   (`:1863`, `{'concepts': []}` 200 — defeats G's fix entirely, practice.js
   only ever sees the proxy)**, `/api/gamification` (`:861`, fabricated
   zeros). All four → named 503s.
4. **`target_date` dropped by the proxy** (`:1462`) — forward it; then
   librarian must read it (needs a small `get_due_cards(target_date=)` change —
   coordinate with G's landed code).
5. **`student_id` not forwarded** on `/api/set_active_course` (`:1078`) and
   `/api/upload_epub` (`:1287`) → child B's upload lands on the default FSM.
6. **Register agent K's `library_api` blueprint** (one line, K's file header
   says which).

### The keep_alive client queue — **DONE** (applied after D landed; also fixed residency() discarding expires_in_s, which made the short-window warning impossible to fire)
Agent C set the server default to 30m but four client-side spots still pin
`-1` and would override it per request:
- `services/core/llm_client.py:34` default `'-1'` → `'30m'` (+ rewrite the
  pinning argument in the comment above it)
- `llm_client.py` `chat()` payload comment (~L92-100), same reasoning
- `llm_client.py:444` `warn_if_not_pinned()` — the policy is now INVERTED:
  it should warn on pinned or on a window under ~10 min, not on unpinned.
  Called from `fsm_logic.py:370`.
- `services/common/llm_utils.py:461` default `"-1"` → `"30m"`
`tests/core/test_llm_throughput.py` asserts only presence + env override, so
none of these break it.

### fsm_logic.py queue — **DONE 18a0c47** (review mode grades the question
actually asked against the card's real content, and an outage marks nothing
correct; missing grade falls back instead of inventing a 3, nesting-safe
extraction, graded=False finally consumed by the scheduler; SKIP excludes the
skipped concept; PAUSED answers instead of swallowing)

### ~~fsm_logic.py queue~~ (original list, for the record)
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
13. ~~book_reader `doc.close()`~~ — **DONE f1efdfa** (try/finally; every
    malformed PDF was a leaked native handle).

---

## 4. Lane 2b — features required for "releasable", not yet started

0b. **Marketable-feature lanes (user-approved, Mode B items excluded):**
   Lane L (course export/import) **LANDED fc1890b, wired 0c71889** — 42 tests,
   round-trip verified, residue-free rejections incl. path traversal and
   zip-bomb guards. Lane M **LANDED 2fc3f3d, wired** — all three: the notebook merges the
   Markdown session notes (the ones real sessions produce — the v13 table has
   no production writer, a finding worth keeping) with table rows; the bell
   synthesizes due-reviews from Practice's endpoint because the notification
   store's recipients are structurally Mode B (parents); printable syllabus +
   completion certificate with server-checked completeness. 31 targeted tests,
   nav stays at 7.

1. **Book → course, wired end to end** = §3 item 1 (DONE 7b4d492) + agent K's
   UI (DONE) + agent I's pipeline fixes (DONE). Run §6.2 to verify.
2. **A4 pedagogy floor** — adaptation 2.8, "Misconception holder" 2.4; judge
   notes say *lecturing instead of questioning*. Cheapest wins per the
   scoreboard: the dialogue contract + learner-history personalisation
   (A4.1a/b). Measure only via median-of-3 (`--repeat 3`), never a single run
   (±1.4/5 noise floor).
3. ~~Backup/restore drill~~ — **DONE**: `tools/backup_drill.py`, run against
   the REAL data directory (schema 17, integrity ok, all rows/dirs survive).
   Also incidentally proves the migrated live DB is healthy.
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

## 6. Lane 3 — the long runs (NOW RUNNING overnight via tools/overnight_mode_a.py; results land in docs/overnight/<stamp>/MORNING_REPORT.md)

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
python3 tools/backup_drill.py                          # a backup that restores
python3 tools/golden_courses.py evaluate               # gate verdicts
python3 tools/path_audit.py                            # structural
# + §5 sweeps, + §6 results recorded in MODE_A_STATUS.md with dates
```

Then update `MODE_A_STATUS.md`: every criterion row VERIFIED with its command
and date, and the §4 "genuinely NOT done" list empty. That file remains the
scoreboard; when it shows 7/7 with a passing gate course, Mode A is done.
