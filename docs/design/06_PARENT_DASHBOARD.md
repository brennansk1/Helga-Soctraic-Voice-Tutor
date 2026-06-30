# Design Spec 06 — Parent / Guardian Dashboard (B19 · FE6)

> Implementation-ready design for the role-gated parent surface (Workstream E, B19.1–B19.6).
> **Canonical table/column names come from `docs/design/01_DATA_MODEL.md` and are used verbatim
> here** (`parents`, `students`, `enrollments`, `consent_records`, `subscriptions`, `accommodations`,
> `audit_log`, `notifications`, `standards`, `concept_standards`). Ties: spec 08 (Compliance/FERPA/COPPA,
> = B21), spec 09 (Billing/Stripe, = B20), spec 11 (Student IA — owns the kid app shell). Build-tree refs:
> `docs/HELGA_BUILD_TREE.md` B19 / FE6 / B5.6 (N+1); `docs/BUILD_MANIFEST.md` B19 rows.
>
> Spec 03 (Multitenancy/Auth/FSM) is not yet written; this spec assumes the auth primitives it will own:
> `current_parent()` (Flask-Login, B15.4), `current_student_id()`, `@parent_required`, and the
> per-student sub-store `student_id` parameter (B15.3, spec 01 §8). Where this spec needs them it names
> them explicitly and marks the dependency.

---

## 0. Scope & non-goals

**In scope (this spec owns):** the parent web surface — its pages, routes, templates, the storage/API
queries backing each, the elective-approval state machine, seat enforcement at the dashboard layer, the
PDF report, and the parent-side of consent/export/delete. Every parent endpoint and its auth/ownership guard.

**Out of scope (owned elsewhere, referenced only):**
- Kid/student app shell, nav, and IA → **spec 11**. This spec deliberately does **not** restyle the student app.
- Stripe Checkout / portal / webhook → **spec 09 (B20)**. Here we only *read* `subscriptions.seats`/`status`
  and link out to billing; we never write subscription rows.
- Consent/policy text, retention windows, COPPA verification method, FERPA retention → **spec 08 (B21)**.
  Here we capture/display consent and run export/delete; legal semantics live in spec 08.
- Exam generation/grading → spec for B18. We *read* `exam_attempts` for the progress detail.
- Auth/login/registration flows → **spec 03 / FE7**. We assume a logged-in parent.

---

## 1. Role-gated surface (parent app shell)

### 1.1 Two shells, one Flask app
The student (kid) experience keeps the existing `base.html` chrome (gamification bar, playful nav). The
parent dashboard is a **distinct shell** rendered from a new `parent_base.html` that does **not** extend
`base.html` — it has its own nav (no XP/streak bar, no kid quick-links), a child-switcher, and a
"Parent" badge. This mirrors the `{% extends %}` + `{% block content %}` convention already used by every
template, just with a parent-specific base.

```
templates/
  base.html              (existing — student shell, untouched)
  parent_base.html       (NEW — parent shell: nav, child switcher, logout)
  parent/
    children.html        (B19.1 overview — landing page after parent login)
    child_detail.html    (B19.2 per-child progress + standards coverage)
    approvals.html       (B19.3 elective approval queue)
    manage_students.html (B19.4 add/edit/archive + seat status)
    reports.html         (B19.5 report builder + download)
    account.html         (B19.6 consent / data rights / billing link)
```

### 1.2 Routing & gating
A parent-only blueprint keeps the surface isolated and lets one decorator gate the whole tree. All
page routes render a template; all data comes from `/parent/api/*` JSON endpoints (same proxy/fetch
pattern as the student app — pages render a shell, JS fetches data on `DOMContentLoaded`, exactly like
`home.html:loadStats()`).

```python
# services/web-ui/app.py  (new blueprint, registered on the same app)
parent_bp = Blueprint('parent', __name__, url_prefix='/parent')

@parent_bp.before_request
@parent_required          # 401→/login if not a logged-in parent (spec 03 / FE7)
def _guard():
    pass
```

`@parent_required` (provided by spec 03 / B15.4): resolves `current_parent()` from the Flask-Login
session; if the session role is `student` or anonymous → 302 to `/login` for pages, 401 JSON for
`/parent/api/*`. The student app routes (`/`, `/learn`, `/courses` …) are unchanged and serve the kid
shell.

### 1.3 Parent page list

| Route | Template | Shows | Backed by (endpoint → store/query) |
|---|---|---|---|
| `GET /parent` | redirect → `/parent/children` | — | — |
| `GET /parent/children` | `parent/children.html` | Card per student: name, grade_band, active-course count, mastery %, streak, time-on-task (7d), due reviews, pending-approval badge | `GET /parent/api/children` → `AccountStore.list_students` + per-student aggregates (§2) |
| `GET /parent/children/<student_id>` | `parent/child_detail.html` | Standards coverage, Bloom progression, activity timeline, flagged struggles, per-course progress | `GET /parent/api/children/<sid>/overview`, `/standards`, `/timeline`, `/struggles` (§3) |
| `GET /parent/approvals` | `parent/approvals.html` | Queue of `enrollments.status='pending_approval'` across all the parent's students | `GET /parent/api/approvals` (§4) |
| `GET /parent/students` | `parent/manage_students.html` | Add/edit/archive students; grade_band, interests, accommodations; seat usage `N/seats` | `GET /parent/api/students`, seat status from `subscriptions` (§5) |
| `GET /parent/reports` | `parent/reports.html` | Report builder (pick child + date range + sections); download button | `GET /parent/api/children/<sid>/report.pdf` (§6) |
| `GET /parent/account` | `parent/account.html` | Consent records, data export/delete buttons, link to billing (spec 09) | `GET /parent/api/consents`, `POST …/export`, `…/delete` (§7) |

`parent_base.html` nav links: Children · Approvals (badge = pending count) · Students · Reports · Account · Log out.
The Approvals badge count comes from a cheap `GET /parent/api/notifications/unread_count` poll (mirrors
the existing 4s creation-banner poll in `base.html`).

### 1.4 Multi-tenant data access (the rule that governs every endpoint)
The isolation key is `students.id` (`stu_…`, spec 01 §0). **Every** parent endpoint that touches a
student resolves the student through an ownership check first:

```python
def _owned_student_or_404(parent, student_id):
    stu = storage.accounts.get_student(student_id)            # AccountStore (spec 01 §8)
    if not stu or stu['parent_id'] != parent['id'] or stu['status'] == 'deleted':
        audit(parent, 'access_denied', subject_student_id=student_id)  # §9
        abort(404)        # 404 not 403 — don't confirm the row exists to a non-owner
    return stu
```

This is the single choke-point for B19's security requirement (§9). The per-user sub-stores already take
a leading `student_id` after B15.3, so all reads below pass the verified `stu_…`.

---

## 2. B19.1 — Children overview

`GET /parent/api/children` → returns one row per non-archived student of `current_parent()`. For each
student we compute six aggregates. **N+1 caution (B5.6):** the naive loop is "per student → per course →
per concept query". We hold the per-student work to a fixed small number of queries and reuse the
existing single-query aggregates rather than the per-concept pattern that B5.6 already removed from
`/api/course_structure`.

### 2.1 Per-student aggregates and their exact queries
All queries are `student_id`-scoped (B15.3). `?` = bound param.

**(a) grade_band** — directly from `students.grade_band` (already loaded by `list_students`).

**(b) active courses** — count of `enrollments` in a learning state:
```sql
SELECT COUNT(*) FROM enrollments
WHERE student_id = ? AND status = 'active';
```

**(c) mastery %** — completed concepts / total concepts across the student's active courses. Reuse the
existing single-pass aggregate `ProgressStore.get_completion_percentage(course_uid, total)` is per-course;
for the overview we want one number per student. Two queries, no per-concept loop:
```sql
-- completed (mastered) concepts for this student, across active enrollments
SELECT COUNT(*) FROM user_progress p
JOIN enrollments e ON e.student_id = p.student_id AND e.course_uid = p.course_uid
WHERE p.student_id = ? AND p.status = 'completed' AND e.status = 'active';
```
Total concepts per course already exists denormalized via `CourseStore.get_course_stats(uid)['concepts']`
(used by `/api/stats`); sum it over the student's active `course_uid`s in Python (one cheap call per
course, courses-per-student is small — typically <10). `mastery_pct = round(100 * completed / max(total,1))`.

**(d) streak** — reuse `ActivityStore.get_streak()` (storage.py:1160), now `student_id`-scoped:
`get_streak(student_id)`. No change to the walk logic; just the `WHERE student_id = ?` filter B15.3 adds.

**(e) time-on-task (7-day)** — sum of `activity_log.duration_seconds` over the trailing 7 days:
```sql
SELECT COALESCE(SUM(duration_seconds), 0) AS secs
FROM activity_log
WHERE student_id = ? AND created_at >= ?;     -- ? = (today - 7d) ISO date
```
Returned as `time_on_task_7d_minutes = round(secs/60)`.

**(f) due reviews** — count from `ProgressStore.get_due_reviews(student_id, today)` (existing method,
storage.py:880) length, or directly:
```sql
SELECT COUNT(*) FROM user_progress
WHERE student_id = ? AND next_review_date <= ? AND status != 'locked';
```

**(g) pending approvals** (for the card badge):
```sql
SELECT COUNT(*) FROM enrollments
WHERE student_id = ? AND status = 'pending_approval';
```

### 2.2 Query budget
Per child: ~5 SQLite reads + `get_streak` (1) + `get_course_stats` × (active courses). With M children
that is `~6M + (courses)`. All are indexed (`idx_enroll_status`, `idx_progress_student`,
`idx_activity_student` — spec 01 §2/§2.1). Acceptable for the dashboard; if M grows, batch (c)/(e)/(f)
into single `GROUP BY student_id` queries over `WHERE student_id IN (…)`. **Do not** reintroduce a
per-concept loop (B5.6 regression).

### 2.3 Response shape
```json
{ "children": [ {
  "student_id": "stu_ab12cd34", "display_name": "Maya", "grade_band": "6-8",
  "active_courses": 3, "mastery_pct": 42, "streak_days": 5,
  "time_on_task_7d_minutes": 86, "due_reviews": 7, "pending_approvals": 1,
  "avatar_url": null
} ] }
```
`children.html` renders cards (reuse `.stat-card`/grid CSS from `home.html`); each card links to
`/parent/children/<student_id>`. Every successful `GET /parent/api/children` writes one
`audit_log` row `action='view_progress'` (one per dashboard load, `subject_student_id=NULL`,
detail lists the viewed ids) so we don't spam the audit table per child.

---

## 3. B19.2 — Per-child progress detail

`GET /parent/children/<student_id>` renders `child_detail.html`; data via four endpoints (all
`_owned_student_or_404` first; each writes an `audit_log` `view_progress` row with
`subject_student_id=<sid>`).

### 3.1 Standards coverage (the headline view)
Join the global catalog `concept_standards` (spec 01 §4) to the student's `user_progress` to bucket each
Utah code the student's enrolled courses touch into **mastered / in_progress / not_started**.

`GET /parent/api/children/<sid>/standards` →
```sql
-- All standards reachable through this student's enrolled courses, with the
-- student's best status per concept rolled up to the standard.
SELECT s.code, s.subject, s.strand, s.text, s.is_enrichment,
       cs.concept_uid, cs.coverage,
       COALESCE(p.status, 'not_started') AS concept_status,
       COALESCE(p.bloom_level, 0)        AS bloom_level
FROM enrollments e
JOIN course_concepts cc       ON cc.course_uid = e.course_uid     -- concept→course map*
JOIN concept_standards cs     ON cs.concept_uid = cc.concept_uid
JOIN standards s              ON s.code = cs.standard_code
LEFT JOIN user_progress p     ON p.concept_uid = cc.concept_uid AND p.student_id = e.student_id
WHERE e.student_id = ? AND e.status IN ('active','completed');
```
`*` There is no `course_concepts` table today (structure lives in `structure.json`). Two options, decide
at build (open question Q3):
  - **(A)** add a thin `course_concepts(course_uid, concept_uid)` index table populated at course
    create/hydrate time (cheap, makes this a pure SQL join), **or**
  - **(B)** resolve the concept list per course in Python from `CourseStore.get_course` (the structure
    walk already in `/api/course_structure`) and query `concept_standards` for that id set
    (`WHERE cs.concept_uid IN (…)`). One walk per enrolled course; reuse the B5.6 single progress map.

Recommended: **(A)** — it removes the structure walk from a parent-facing read path and is reused by the
PDF report (§6) and by B26.4 (standards-coverage audit).

**Roll-up to standard status** (in Python over the rows): a standard's status is the **max** over its
concepts, ordered `not_started < in_progress < mastered`:
- **mastered** — concept status `completed` (the FSM only marks `completed` after `_check_mastery_gate`
  passes: streak ≥2, bloom ≥ target, ≥3 distinct question types — `fsm_logic.py:1061`). So "mastered" is
  exactly `user_progress.status='completed'`.
- **in_progress** — concept has a `user_progress` row but status ≠ `completed` (any of: `in_progress`,
  `locked`-then-touched, partial bloom). Equivalent: `status NOT IN ('completed') AND status IS NOT NULL`.
- **not_started** — no `user_progress` row (the `LEFT JOIN` produced `not_started`).

A standard rolls up to **mastered** iff **all** its mapped concepts are mastered; **in_progress** if at
least one concept is touched but not all mastered; else **not_started**. (Conservative "all concepts"
rule so a parent can trust a green standard for homeschool records.) Enrichment standards
(`is_enrichment=1`) are bucketed separately and shown under a "★ Enrichment" subsection.

Response groups by `subject → strand → [standards]` with per-strand and per-subject coverage percentages
(`mastered / total`). This is the homeschool-accountability artifact and the §6 PDF's core table.

### 3.2 Bloom progression
Cognitive depth, not just coverage. From `user_progress.bloom_level` (1–6, set by the FSM) over the
student's mastered concepts:
```sql
SELECT bloom_level, COUNT(*) AS n
FROM user_progress
WHERE student_id = ? AND status = 'completed'
GROUP BY bloom_level ORDER BY bloom_level;
```
Rendered as a 6-bar histogram ("how deeply, not just how much"). Optionally per subject by joining the
§3.1 standard→subject map.

### 3.3 Activity timeline
`GET /parent/api/children/<sid>/timeline?days=30` → reuse `ActivityStore.get_activities(student_id,
start_date, end_date)` (storage.py:1125). Group by `DATE(created_at)`:
```sql
SELECT DATE(created_at) AS day, activity_type,
       COUNT(*) AS events, COALESCE(SUM(duration_seconds),0) AS secs,
       AVG(grade) AS avg_grade
FROM activity_log
WHERE student_id = ? AND created_at >= ?
GROUP BY day, activity_type ORDER BY day DESC;
```
Rendered as a per-day strip (minutes + event types + avg grade). `details` JSON column is available for
drill-down but not surfaced by default.

### 3.4 Flagged struggles
A "struggle" = repeated low grades on the same concept. Grades are the 1–4 Socratic scale (low = `grade < 3`,
the same threshold `_check_mastery_gate` uses to reset the streak — `fsm_logic.py:283`).
`GET /parent/api/children/<sid>/struggles` →
```sql
SELECT concept_uid,
       COUNT(*) AS attempts,
       SUM(CASE WHEN grade < 3 THEN 1 ELSE 0 END) AS low_grades,
       MAX(created_at) AS last_seen
FROM activity_log
WHERE student_id = ? AND grade IS NOT NULL AND created_at >= ?  -- trailing 30d
GROUP BY concept_uid
HAVING low_grades >= 3                 -- threshold: 3+ low grades on one concept
       AND low_grades * 1.0 / attempts >= 0.5   -- and majority were low
ORDER BY low_grades DESC;
```
**Struggle thresholds (definition):** ≥3 low (`grade<3`) graded attempts on a single concept in the
trailing 30 days **and** ≥50% of that concept's graded attempts low. Each flagged concept is resolved to
its title + course (via the §3.1 map) and shown with a "needs help" badge. This same query (run nightly,
per student) feeds the `struggle_alert` notification (B24.4) — out of scope here but the query is shared.

### 3.5 Per-course progress
For each `enrollments` row of the student, show course title, status, and completion % via the existing
`ProgressStore.get_completion_percentage(student_id, course_uid, total_concepts)` + the denormalized
`get_course_stats` concept count. No per-concept loop.

---

## 4. B19.3 — Elective approval workflow

A **catalog** course (published, grade-appropriate) enrolls a student directly. An **elective** (a
parent-/child-built custom course via the existing wizard) must be **approved by the parent before the
child can start it**. This is the `enrollments` state machine.

### 4.1 State machine (`enrollments.status`, `course_kind='elective'`)
```
                      child requests elective
        (none) ───────────────────────────────▶ pending_approval
                                                   │        │
                              parent approve        │        │  parent deny
                                                   ▼        ▼
                                                 active    denied
                                                   │
                                  child completes  │
                                                   ▼
                                                completed
                              (paused is reachable from active via existing pause flow)
```
- Allowed transitions (enforced server-side; reject others 409):
  `∅ → pending_approval` (child request), `pending_approval → active` (approve),
  `pending_approval → denied` (deny), `denied → pending_approval` (child re-requests / parent re-opens),
  `active → completed|paused`, `paused → active`.
- `approved_by` = `parent_id`, `approved_at` = `datetime('now')` set on the approve transition (columns
  exist, spec 01 §2). On deny, `approved_by` stays NULL; we record the denial in `audit_log`.
- A **denied** or **pending_approval** enrollment is invisible to the student app and the FSM refuses to
  start it (see §4.4 gate).

### 4.2 Child requests an elective (gating the custom wizard)
The existing custom-course wizard (`/courses/new`, `librarian.py:create_custom_course_wizard`,
`/api/custom_course/create`) is **reused unchanged for building structure/content**, but for a logged-in
**student** the terminal step changes: instead of going straight to "ready + start", it creates the
course rows with `courses.status='ready'` *and* an `enrollments` row
`(student_id, course_uid, course_kind='elective', status='pending_approval')`, then returns
`{"status":"pending_approval"}` so the kid UI shows "Sent to your parent for approval" instead of a Start
button. (Whether the child may even open the wizard is a policy toggle on `students.settings`, default
allowed; gate is in spec 11's student app — here we own the *enrollment* side.)

On request creation we insert a notification:
```sql
INSERT INTO notifications (id, recipient_id, recipient_role, kind, title, body, ref_uid)
VALUES (?, ?, 'parent', 'elective_request', ?, ?, ?);  -- recipient = student's parent_id, ref_uid = enrollment id
```
`ntf_…` id; this drives the Approvals nav badge (§1.3) and B24.3 in-app notifications.

### 4.3 Parent approve / deny UI + endpoint
`parent/approvals.html` lists each pending elective: child name, course title, overview, "Preview"
(read-only course structure via existing `/api/course_structure`), **Approve** / **Deny** buttons (deny
opens an optional reason field).

```
POST /parent/api/enrollments/<enrollment_id>/approve
POST /parent/api/enrollments/<enrollment_id>/deny      body: {"reason": "..."}
```
Handler (both):
1. Load enrollment; `_owned_student_or_404(parent, enrollment.student_id)` (ownership via the student).
2. Assert current status is `pending_approval` (else 409 with current status).
3. Approve → `EnrollmentStore.set_status(enrollment_id, 'active', approved_by=parent_id,
   approved_at=now)`. Deny → `set_status('denied')`; store reason in the notification/audit detail.
4. `audit_log` row `action='elective_decision'`, `subject_student_id`, `detail={enrollment_id, decision, reason}`.
5. Insert a `notifications` row for the **student** (`recipient_role='student'`,
   `kind='elective_request'`, body "approved"/"declined") so the kid sees the result.
6. Mark the originating parent notification read (`read_at=now`).

`EnrollmentStore.set_status` whitelists `status, approved_by, approved_at` (per spec 01 §8 `_VALID_COLUMNS`
rule) and is `student_id`-scoped.

### 4.4 The start gate (where approval is enforced)
The FSM `RESUME_COURSE`/`SET_CONTEXT`/`NAVIGATE_TO_TOPIC` path must refuse to start a course the student
isn't actively enrolled in. Add a check in the FSM context setter (B15.6/B15.7 registry): before
activating `course_uid` for `student_id`, look up the enrollment; if `course_kind='elective'` and
`status != 'active'`, return an error message ("This course is waiting for a parent to approve it") and do
not enter `SOCRATIC_LEARNING`. Catalog courses skip this gate. (FSM edit is small; flagged as a
cross-spec dependency on spec 03 since the FSM context setter lives there.)

---

## 5. B19.4 — Add / manage students (seat-capped)

`parent/manage_students.html` + `GET /parent/api/students` (list with status, seat usage banner).

### 5.1 CRUD endpoints
```
POST   /parent/api/students                 create   {display_name, grade_band, grade_numeric?, interests[], pin?}
PATCH  /parent/api/students/<student_id>     edit     any of: display_name, grade_band, interests, settings, pin
POST   /parent/api/students/<student_id>/archive       status → 'archived'  (soft; frees a seat)
POST   /parent/api/students/<student_id>/restore       status → 'active'    (re-checks seat cap)
POST   /parent/api/students/<student_id>/accommodations {extended_time, no_timer, reduced_distraction, ...}
```
All write through `AccountStore`/`AccommodationStore` (spec 01 §8), `parent_id` stamped from
`current_parent()` (never from the request body — prevents creating a child under another parent).
`pin` is argon2-hashed into `students.pin_hash` (spec 01 §2); never returned. `interests` validated as a
JSON array of strings, max 20 (spec 01 §2). Edit/archive/accommodations all run
`_owned_student_or_404` first; archive/restore/accommodations write `audit_log`
(`action='manage_student'`). Accommodations (`extended_time`, `no_timer`, `reduced_distraction`,
`larger_targets`, `extra_scaffolding`, `simplified_language`, `read_aloud_default`, `notes`, `set_by=parent_id`)
are consumed by the FSM/exam layer (B25.4) — here we only own the parent UI that sets them.

### 5.2 Seat enforcement (the cap)
Seats come from `subscriptions.seats` (spec 01 §2; billing writes it, spec 09). The dashboard **reads** it
and blocks over-limit creation/restore. On `POST /parent/api/students` (and `/restore`):
```sql
-- active student count for this parent
SELECT COUNT(*) FROM students WHERE parent_id = ? AND status = 'active';
-- seat allowance (default 1 if no subscription row)
SELECT COALESCE(seats, 1), status FROM subscriptions WHERE parent_id = ?;
```
If `active_count >= seats` → **HTTP 402 Payment Required** `{"error":"seat_limit","seats":N,"active":N,
"upgrade_url":"/parent/account#billing"}`. Frontend shows "You've used all N seats — add a seat to enroll
another student" linking to billing (**spec 09**). Also gate on subscription `status`: if not in
`('active','trialing')`, treat allowance as the free-tier seat count (decided in spec 09; default 1).
Archive does not delete data (status `archived`), so it is reversible and frees a seat immediately;
`delete` is the §7 hard path. **Billing details (plans, proration, the seats writes) are spec 09's** —
this spec only enforces the count it reads.

---

## 6. B19.5 — Exportable progress / standards-coverage report (PDF)

A downloadable record for homeschool portfolios and Utah Fits All / grant accountability.

### 6.1 Endpoint
```
GET /parent/api/children/<student_id>/report.pdf?from=YYYY-MM-DD&to=YYYY-MM-DD&sections=...
```
`_owned_student_or_404`; writes `audit_log` `action='export_data'`, `detail={report, range}`.
Returns `application/pdf`, `Content-Disposition: attachment; filename="helga_<name>_<from>_<to>.pdf"`.

### 6.2 Contents (sections, default all)
1. **Header** — student display name, grade_band, parent/account name, report date, date range, "Generated
   by Helga — self-hosted AI tutor" footer; a statement that all instruction was AI-tutored offline.
2. **Summary** — active courses, concepts mastered / total, overall mastery %, total time-on-task in
   range (minutes/hours from `activity_log.duration_seconds`), current streak.
3. **Standards coverage table** — the §3.1 roll-up: per subject → strand → Utah code, status
   (Mastered / In progress / Not started), with the standard `text`. This is the legally useful artifact:
   it maps work done to **USBE codes**. Enrichment (★) standards in a separate sub-table.
4. **Bloom progression** — §3.2 histogram (mastered concepts by cognitive level).
5. **Activity log** — §3.3 per-day minutes + activity types over the range (the "attendance/seat-time"
   evidence many homeschool/grant programs require).
6. **Assessments** — passed/attempted exams from `exam_attempts` in range (`status='graded'`,
   `passed`, `score`) if B18 present; omit section gracefully if no attempts.
7. **Flagged areas** — §3.4 struggles (optional; parent can exclude via `sections`).

### 6.3 Generation approach (server-side HTML→PDF)
Render a Jinja template `parent/report.html` (print CSS: page breaks per section, `@page` margins,
no nav) with the same data the on-screen detail uses, then convert HTML→PDF server-side. Use **WeasyPrint**
(pure-Python, no headless browser, deterministic, offline — fits the no-external-deps posture) in the
**web-ui** service. The report data is assembled by reusing §2/§3 query helpers (single set of functions,
called by both the JSON endpoints and the report builder), so the PDF can never drift from the dashboard.
Generation is synchronous for a single child (queries are indexed and bounded); if a report spans a very
large range, run it in a background greenlet (the app already uses `gevent.spawn`) and stream the file when
ready. Add `weasyprint` to `services/web-ui/requirements.txt` (pinned).

---

## 7. B19.6 — Account / consent / data rights

Ties **spec 08 (B21 compliance)**; this spec owns the parent-facing UI and the export/delete mechanics.
**Every** access here writes `audit_log` (the FERPA/Utah data-access record, distinct from `activity_log`).

### 7.1 Consent capture & view
`parent/account.html` lists the parent's `consent_records` (type, granted, policy_version, date, method)
and lets them grant/revoke per type. Policy text/versioning is spec 08's; here:
```
GET  /parent/api/consents                      → list consent_records for parent (+ per-student health_strand6)
POST /parent/api/consents   {consent_type, student_id?, granted, policy_version, method}
```
Insert (append-only — never UPDATE a consent row; a new row with new `granted`/`policy_version` is the
record of the change, preserving history). Capture `ip_address` from the request (spec 01 §2). Each write
→ `audit_log` `action='consent_change'`. The COPPA gate (`coppa_data` consent must exist & be `granted`
before a child can use the app) is **enforced in spec 08 / FE7**; this page is where the parent grants it.
Health Strand 6 (`health_strand6`) consent is per-student and gates that content (B21.4).

### 7.2 Data export (all of a student's data)
```
POST /parent/api/children/<student_id>/export        → 202, builds a ZIP/JSON bundle; download when ready
GET  /parent/api/children/<student_id>/export/<job>  → application/zip
```
`_owned_student_or_404`. The bundle is the **machine-readable** counterpart to the §6 PDF: a JSON export
of every per-student row keyed by `student_id` — `students` (sans `pin_hash`), `enrollments`,
`user_progress`, `activity_log`, `scheduled_reviews`, `flashcards`, `exam_attempts`/`exam_item_responses`,
`student_gamification`/`xp_ledger`/`student_badges`, `accommodations`, `consent_records` (that student),
`fsm_sessions.blob`, plus the markdown content of the student's elective courses. One bundling function
iterates the per-student sub-stores (each already `student_id`-scoped). Writes `audit_log`
`action='export_data'`.

### 7.3 Data delete (cascade)
```
POST /parent/api/children/<student_id>/delete   body: {confirm: "<display_name>"}  → hard delete
```
`_owned_student_or_404`; require the typed confirmation to match `display_name`. Deletion relies on the
`ON DELETE CASCADE` foreign keys defined in spec 01 (§2: `students.parent_id` → and every per-student table
references `students(id) ON DELETE CASCADE`). Implementation:
1. Pre-delete: write the export bundle (§7.2) to the retention store **first** (spec 08 retention policy)
   so a deletion is recoverable within the legal window, then
2. `AccountStore.delete_student(student_id)` — `DELETE FROM students WHERE id=? AND parent_id=?` (cascade
   removes all child rows). Tables without a real FK in SQLite (the `student_id` columns added by ALTER in
   spec 01 §2.1 may not all carry an enforced FK) get an explicit `DELETE … WHERE student_id=?` sweep in
   the same transaction — the delete function enumerates **every** per-student table so nothing is orphaned.
3. Frees a seat (active count drops). Writes `audit_log` `action='delete_data'`,
   `detail={student_id, tables_swept}`. Account-level delete (the whole parent) is a separate spec-08 flow.

> SQLite FK note: `PRAGMA foreign_keys=ON` must be set on each connection for cascade to fire
> (`_ThreadLocalDB`). If it isn't already, the delete function does the explicit per-table sweep regardless
> (belt-and-suspenders) — don't rely solely on cascade.

---

## 8. Endpoint table (complete)

All under the `parent_bp` blueprint; **all require parent role** (`@parent_required` on
`before_request`) and, for any `<student_id>`/`<enrollment_id>` path, an **ownership check**
(`_owned_student_or_404`, resolving the enrollment's student for enrollment paths). CSRF: all non-GET use
the existing `@csrf_protect` (`app.py:102`) / `X-CSRF-Token` header auto-attached by `base.html` — the
parent shell includes the same CSRF meta/fetch shim.

| Method | Path | Auth / ownership | Request | Response |
|---|---|---|---|---|
| GET | `/parent` | parent | — | 302 → `/parent/children` |
| GET | `/parent/children` | parent | — | `children.html` |
| GET | `/parent/api/children` | parent | — | `{children:[…]}` (§2.3) |
| GET | `/parent/children/<sid>` | parent + owns sid | — | `child_detail.html` |
| GET | `/parent/api/children/<sid>/overview` | parent + owns sid | — | summary + per-course progress (§3.5) |
| GET | `/parent/api/children/<sid>/standards` | parent + owns sid | — | grouped coverage (§3.1) |
| GET | `/parent/api/children/<sid>/bloom` | parent + owns sid | — | bloom histogram (§3.2) |
| GET | `/parent/api/children/<sid>/timeline?days=` | parent + owns sid | — | per-day activity (§3.3) |
| GET | `/parent/api/children/<sid>/struggles` | parent + owns sid | — | flagged concepts (§3.4) |
| GET | `/parent/approvals` | parent | — | `approvals.html` |
| GET | `/parent/api/approvals` | parent | — | pending electives across owned students |
| POST | `/parent/api/enrollments/<eid>/approve` | parent + owns eid.student | — | `{status:'active'}` / 409 |
| POST | `/parent/api/enrollments/<eid>/deny` | parent + owns eid.student | `{reason?}` | `{status:'denied'}` / 409 |
| GET | `/parent/students` | parent | — | `manage_students.html` |
| GET | `/parent/api/students` | parent | — | list + seat usage |
| POST | `/parent/api/students` | parent | `{display_name,grade_band,…}` | `{student_id}` / **402** seat_limit |
| PATCH | `/parent/api/students/<sid>` | parent + owns sid | partial fields | updated student |
| POST | `/parent/api/students/<sid>/archive` | parent + owns sid | — | `{status:'archived'}` |
| POST | `/parent/api/students/<sid>/restore` | parent + owns sid | — | `{}` / 402 seat_limit |
| POST | `/parent/api/students/<sid>/accommodations` | parent + owns sid | flags | updated accommodations |
| GET | `/parent/reports` | parent | — | `reports.html` |
| GET | `/parent/api/children/<sid>/report.pdf?from=&to=&sections=` | parent + owns sid | — | `application/pdf` |
| GET | `/parent/account` | parent | — | `account.html` |
| GET | `/parent/api/consents` | parent | — | consent_records list |
| POST | `/parent/api/consents` | parent (+ owns sid if student-scoped) | consent fields | new consent row |
| POST | `/parent/api/children/<sid>/export` | parent + owns sid | — | `202 {job}` |
| GET | `/parent/api/children/<sid>/export/<job>` | parent + owns sid | — | `application/zip` |
| POST | `/parent/api/children/<sid>/delete` | parent + owns sid | `{confirm}` | `{deleted:true}` |
| GET | `/parent/api/notifications/unread_count` | parent | — | `{count:N}` (nav badge) |

**Error contract:** 401 (not a parent), 404 (`_owned_student_or_404` — also covers "exists but not yours",
no information leak), 402 (`seat_limit`), 409 (illegal enrollment transition), 400 (validation),
502 (downstream rag/core proxy failure, mirroring existing handlers).

---

## 9. Security & audit (FERPA)

1. **Ownership on every student-scoped read/write.** `_owned_student_or_404` (§1.4) gates every handler
   touching a `<student_id>`/`<enrollment_id>`. `parent_id` for writes is taken from `current_parent()`,
   **never** from the request body. Non-owner access returns **404** (not 403) so a parent can't
   enumerate other families' student ids.
2. **No cross-tenant leakage in queries.** Every query in §2–§7 is `student_id`-scoped (B15.3). The only
   global reads are the catalog (`standards`, `concept_standards`, course catalog) which are read-only and
   contain no student data (spec 01 §0). Standards coverage joins catalog → that student's `user_progress`
   only.
3. **Audit logging of parent views of child data (FERPA / Utah Student Data Protection Act).** Every parent
   read of an individual child's data and every export/delete/consent/decision writes an `audit_log` row
   (`actor_id`=parent, `actor_role='parent'`, `action`, `subject_student_id`, `detail` JSON, `ip_address`).
   Actions used by this spec: `view_progress`, `export_data`, `delete_data`, `consent_change`,
   `elective_decision`, `manage_student`, `access_denied`. The overview list write (§2.3) is one row per
   load (not per child) to bound table growth; individual child-detail loads write per-child rows. Audit
   rows are append-only and are themselves part of the §7.2 export only for the account owner.
4. **CSRF** on all mutations (existing `@csrf_protect`); **rate-limit** export/delete (expensive, sensitive).
5. **PIN/password hashes** (`students.pin_hash`, `parents.password_hash`) are never returned by any
   endpoint and are excluded from the §7.2 export bundle.
6. **Defense in depth on delete:** explicit per-table `student_id` sweep + cascade, with
   `PRAGMA foreign_keys=ON` verified (§7.3).

---

## 10. Test plan / acceptance criteria

### 10.1 Happy-path E2E (the B19 acceptance walk)
1. Parent signs up (FE7) and logs in → lands on `/parent/children` (empty state).
2. Adds two students (`POST /parent/api/students` ×2) with grade_bands `K-2` and `6-8`; both appear as
   cards; seat banner shows `2/<seats>`.
3. Enrolls each in a catalog course; child #2 builds a custom elective via the wizard → enrollment created
   `pending_approval`; parent's Approvals badge shows **1**; a `notifications` `elective_request` row exists.
4. Parent opens `/parent/approvals`, clicks **Approve** → enrollment `→ active`, `approved_by/at` set,
   student notification inserted, badge → 0; the child can now start the elective (FSM gate passes).
5. After some tutoring, parent opens `/parent/children/<sid>` → sees standards coverage (≥1 Utah code
   `mastered` after the child completes a concept), Bloom histogram, activity timeline, and (if applicable)
   a flagged struggle.
6. Parent exports `report.pdf` → a valid PDF downloads containing the standards-coverage table mapped to
   USBE codes + activity log.
7. `audit_log` contains `view_progress`, `elective_decision`, `export_data` rows for this parent.

### 10.2 Isolation / negative (the security gate)
- **Parent A cannot see Parent B's child:** `GET /parent/api/children/<B_sid>/*`,
  `report.pdf`, `export`, `delete`, `approve` for a non-owned enrollment → **404** (writes `access_denied`
  audit row). Verified for *every* `<student_id>`/`<enrollment_id>` endpoint (param sweep test).
- **Student role cannot reach `/parent/*`** → 401/redirect.
- **Seat cap:** with `seats=2` and 2 active students, `POST /parent/api/students` → **402** `seat_limit`;
  archiving one then creating succeeds.
- **Illegal transition:** approving an already-`active` or `denied` enrollment → **409**.
- **Body-spoofed parent_id** on create is ignored; the row's `parent_id` is the session parent.
- **Delete cascade:** after `delete`, no rows for that `student_id` remain in any per-student table
  (assert across `user_progress`, `activity_log`, `enrollments`, `flashcards`, `scheduled_reviews`,
  `exam_attempts`, gamification, `accommodations`, `consent_records`, `fsm_sessions`); a seat is freed;
  other students of the parent are untouched.

### 10.3 Unit / query
- Standards roll-up: a standard with 2 concepts → `mastered` only when **both** `completed`; `in_progress`
  when one touched; `not_started` when neither (table-driven test on §3.1 roll-up).
- Struggle threshold: 3 low grades same concept (≥50% low) flags; 2 low does not; 3 low but 10 attempts
  (<50%) does not.
- Time-on-task: sums only the trailing-7d `duration_seconds`, `student_id`-scoped.
- N+1 guard: a child with 5 courses / 200 concepts issues a **bounded** number of queries on
  `/parent/api/children` (assert no per-concept query — B5.6 regression test).
- PDF: report endpoint returns `application/pdf`, contains the student name and at least one standard code.

### 10.4 Acceptance criteria (maps to BUILD_MANIFEST B19)
- B19.1 ✅ children overview shows grade/courses/mastery/streak/time per child.
- B19.2 ✅ per-child standards coverage (Utah codes mastered/in-progress/not-started) + Bloom + timeline + struggles.
- B19.3 ✅ child elective needs parent approve before start; state machine enforced; notifications fire.
- B19.4 ✅ add/manage students; cannot exceed `subscriptions.seats` (402).
- B19.5 ✅ downloadable progress/standards PDF for homeschool/grant.
- B19.6 ✅ consent view/capture + export + cascade delete; every access audited.

---

## 11. Open questions
- **Q1 (mastery rule):** standard = mastered only when **all** mapped concepts mastered (conservative,
  chosen here) vs a threshold (e.g. ≥80% of concepts). Confirm with homeschool/grant requirements.
- **Q2 (audit granularity):** one audit row per overview load vs per child shown. Spec picks one-per-load
  for the list, per-child for detail; confirm FERPA auditor expectations.
- **Q3 (concept→course map):** add a `course_concepts(course_uid, concept_uid)` index table (§3.1 option A,
  recommended) vs Python structure walk (option B). Affects standards query and PDF perf; decide at build.
- **Q4 (free-tier seats):** default seat allowance when `subscriptions` row absent / not active — 1?
  Owned by spec 09; this spec defaults to 1 and links out.
- **Q5 (PDF engine):** WeasyPrint (chosen — pure-Python, offline) vs headless-Chromium. Confirm no
  WeasyPrint system-lib issues in the web-ui container; pin the version.
- **Q6 (delete retention):** export-before-delete to a retention store for the legal recovery window —
  exact window and store live in spec 08; confirm the handshake.
- **Q7 (co-guardian):** single `parent_id` today (spec 01 §10). If two guardians must both see/approve,
  the `guardians` join table is needed — out of scope until product confirms.
```
