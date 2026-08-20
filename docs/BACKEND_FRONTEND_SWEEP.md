# Backend ↔ frontend wiring sweep

**Date:** 2026-08-19 · **Method:** every `@app.route` in `services/web-ui/app.py`
diffed against every `fetch(...)`, `action=` and `href="/api/..."` in
`templates/` and `static/js/`, then each hit verified by hand — a regex finds
candidates, it does not find truth.

100 web-ui routes, 60 distinct frontend calls.

## Fixed in this pass

| What | Was | Now |
|---|---|---|
| **Degree programmes** | `plan_degree()` fully implemented, validated, tested — called only by the test suite and a QA script. No table, no route. `degree.js`/`home.js` called `/api/program` and `/api/programs`, which did not exist, so the flagship rendered example data. | Schema v17 (`programs`, `program_courses`), `ProgramStore`, 4 core routes, 4 proxies. Real plans render; elective choice persists. |
| **Memory + storage** | `memory_guard` measured pressure for `gpu_gate` only. No route, no UI. | `/api/system/resources`; Settings storage panel; app-wide safeguard card that clears itself. |
| **Concept sources** | `sources` + `claim_sources` written since v12, never read back. | `get_concept_sources()`, RAG + web-ui routes, trust panel in the session view. |
| **Cancel a build** | `/api/cancel_creation` existed from the start with no caller. | Confirmed cancel on the build page; releases the single-build lock. |

## Still orphaned — deliberate, no action

These answer something other than the browser:

- `/api/billing/webhook` — inbound from an external service.
- `/api/update_thinking_status` — FSM → web-ui push, server to server.
- `/api/auth/session` — consumed by the login form flow, not by `fetch`.
- `/api/media/<name>`, `/api/media/<name>/attribution` — reached via `<img src>`,
  which the diff cannot see.

## Still orphaned — legacy, superseded

Live code paths exist; these particular routes lost their caller:

- `/api/schedule/complete` — the schedule page's "complete" button is a link
  into `/review`; completion runs through the FSRS flashcard path. This is the
  deprecated SM-2 route.
- `/api/custom_course/create`, `/api/create_custom_course` — the wizard calls
  `/api/create_course_custom`. These two are earlier spellings.
- `/api/course_status/<uid>` — the WIZ-6 polling endpoint; the carousel and
  build view use Socket.IO status instead.

## Still orphaned — real gaps, not yet built

Backend capability with no user-facing surface. Each is a feature decision
rather than a bug, so none was wired on assumption:

- `/api/notifications`, `/api/notifications/<id>/read` — a notifications
  system with no UI anywhere in the app.
- `/api/build/status` — the build view listens to Socket.IO and never asks
  for status directly, so a reload mid-build cannot recover stage state.
- `/api/health/all` — `/status` polls `/api/fsm_state` only; service health is
  collected and never shown.
- `/api/user_profile`, `/api/gamification/award_xp` — profile and XP.
- `/api/generate_flashcards`, `/api/update_card` — manual flashcard authoring.
- `/api/draft/reorder`, `/api/upload_source`, `/api/course_meta`,
  `/api/course_modules` — draft-editing surface.

## Keeping it swept

`tools/css_theme_guard.py` catches the CSS equivalents of this class of bug.
The route diff above is worth re-running whenever routes are added; the
one-liner is in this file's header.
