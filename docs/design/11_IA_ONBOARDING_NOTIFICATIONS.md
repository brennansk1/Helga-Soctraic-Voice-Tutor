# Design Spec 11 — Information Architecture, Onboarding & Notifications

> Implementation-ready spec for **Workstream I** (kid-first tab restructure, FE5–FE8), **FE7** (auth +
> onboarding flows) and **B24** (notifications & communications). This document defines the *navigation
> surfaces*, *route map old→new*, *onboarding step sequences*, *role-based shell switching*, and the
> *notification system*. It assumes the data model in `01_DATA_MODEL.md` (canonical table/column names),
> the grade adaptation parameters in `02_GRADE_ADAPTATION.md`, auth/session in `03_AUTH_*` (referenced),
> the placement/diagnostic flow in `05_*`, the parent dashboard pages in `06_PARENT_DASHBOARD.md` (owns
> its own page contents — here we only define how a parent *enters/switches* into it), consent/COPPA in
> `08_COMPLIANCE.md`, billing/plans in `09_BILLING.md`, and the skill-tree in `07_SKILL_TREE.md`.
> Build-tree refs: FE5–FE8, B19.3, B22.2, B24.1–4, B25.x; baseline FE1 design system.
>
> **Grounding (verified against HEAD):** the app today has **no auth** — a single global FSM, a single
> `data/user_profile.json`, 8 nav tabs in `services/web-ui/templates/base.html`, `/account` and `/palace`
> redirect to `/`. This spec describes the transformation; it does **not** assume any of the multi-tenant
> plumbing exists yet. It depends on B15 (accounts/identity) landing first — see `01_DATA_MODEL.md` and the
> build manifest. Where a route is "kept", we mean the template is reused/refactored, not rebuilt.

---

## 0. Scope, principles, dependencies

**This spec owns:** the navigation IA across all three roles, the student app shell (FE5), catalog browse +
enroll UX, role-based routing & account switching, onboarding flows (parent + student), the notification
system end-to-end (in-app bell + email + triggers + provider/queue), and the component/a11y hooks the IA
must not block.

**This spec defers to:**
- `06_PARENT_DASHBOARD.md` — the *contents* of every parent page (children overview, per-child progress,
  elective approval queue body, exports). We define only the **entry point and shell switch**.
- `03_AUTH_*` — password hashing (argon2id), session/cookie mechanics, CSRF, rate limiting, Flask-Login
  wiring. We define **route guards and landing logic**.
- `08_COMPLIANCE.md` — exact consent copy, COPPA verifiable-consent method, retention/erase. We define
  **where consent capture sits in the onboarding sequence** and which `consent_records.consent_type` rows
  must exist before a child can start.
- `09_BILLING.md` — Stripe Checkout/portal, plan catalog, trial mechanics. We define **the plan/trial step
  placement** and seat-gating UX.
- `05_*` (placement) — diagnostic exam blueprint. We define **the optional placement step** in the
  student first-run.
- `07_SKILL_TREE.md` / B22.2 — the skill-tree data shape & node math. We define **where the tree view lives
  in the Learn surface** and the catalog-as-tree browse entry.

**Design principles (non-negotiable):**
1. **Three distinct surfaces, one app.** Student app (kid-first), Parent dashboard (admin-of-family), Admin/ops
   (health + CMS). A logged-in identity sees exactly one default shell; switching is explicit and audited.
2. **Kid-first means fewer choices, bigger targets, no system internals.** Students never see Status, health,
   raw Settings, billing, or other students. Grade-band drives presentation density (spec 02).
3. **Catalog-first for students.** The free-form "create a course" flow is removed from the student surface;
   students *browse and enroll* in published catalog courses for their `grade_band`. Custom electives move
   behind parent approval (`enrollments.status = 'pending_approval'`, B19.3).
4. **No dark patterns in notifications.** Nudges for minors are parent-controllable, quiet by default for young
   bands, never guilt/streak-pressure framed for K-2. (Spec 02 affect handling; B22.6 safety.)
5. **Reuse existing templates/JS.** `learn.html`, `session.js`, `quiz.html`, `review.html`, `schedule.html`,
   `courses.js`, `wizard.js`, `progress-tree.js` are refactored, not rewritten. Token-based CSS (FE1) extends
   with a kid theme variant rather than a fork.

---

## 1. Information Architecture — the three surfaces

### 1.1 Surface map

| Surface | Audience | Default shell template | Nav style | Entry |
|---|---|---|---|---|
| **Student app** | the learner (kid) | `shell_student.html` (new base) | 4 big bottom-tabs (mobile-first) / left rail (desktop) | login as student (PIN/avatar) or parent "Launch for child" |
| **Parent dashboard** | billing owner / guardian | `shell_parent.html` (new base) — pages owned by spec 06 | top nav + per-child switcher | login as parent |
| **Admin / ops** | operator (you), and CMS reviewers | `shell_admin.html` (reuses `status.html` + new CMS) | top nav, restricted | login as admin role; `/admin/*` route prefix, guarded |

All three extend a shared `base.html` skeleton (theme bootstrap, CSRF helper, toast container, notification
bell partial) but inject a **different `app-nav` block** and a different `body_class`. The current single
`base.html` nav (the 8 `<a>` links, lines 169–185) is replaced by `{% block app_nav %}` so each shell supplies
its own.

### 1.2 The current 8 tabs → new IA (route map old→new)

Current nav (verified `base.html:169-185`): **Home, Courses, Learn, Quiz, Review, Schedule, Status, Settings(⚙️)**.

| # | Current tab / route | Disposition | New home | Why |
|---|---|---|---|---|
| 1 | **Home** `/` | **MERGED** | Student → **Today** (`/app/today`); Parent → **Children overview** (`/parent`) | "Home" was a stats+CTA dashboard. Split by role: kids get an actionable "what to do now"; parents get the family overview. |
| 2 | **Courses** `/courses` | **SPLIT + MOVED** | Student → **Catalog browse** (`/app/catalog`, read-only, grade-filtered) + enrolled list inside **Learn**; create flow → **parent-gated electives** (`/parent/electives`) | Free-form "create course first" is wrong for kids and unsafe (no review/standards). Browsing replaces it; authoring moves to parent/CMS. |
| 3 | **Learn** `/learn` | **KEPT (refactored)** | Student → **Learn** (`/app/learn`) — adds skill-tree (spec 07) above the existing path view | The Socratic session is the product core; `learn.html` + `session.js` reused as-is, tree added. |
| 4 | **Quiz** `/quiz` | **MERGED** | Student → **Practice** (`/app/practice`, "Test yourself" sub-mode) | Quiz + Review are both the same chat shell; merging removes a redundant tab from kid nav. |
| 5 | **Review** `/review` | **MERGED** | Student → **Practice** (`/app/practice`, "Flashcards / due" sub-mode) | FSRS review belongs next to quizzing under one "Practice" surface. |
| 6 | **Schedule** `/schedule` | **MERGED (split)** | Student "due today" → **Today**; calendar/forecast → **Parent** per-child view (spec 06) | Kids don't manage calendars; "what's due" surfaces on Today, the planning view is a parent concern. |
| 7 | **Status** `/status` | **MOVED** | **Admin/ops** (`/admin/status`) only | System health is not a learner or parent concern; restrict to operator. |
| 8 | **Settings ⚙️** `/settings` | **SPLIT** | Student → light **My Stuff** (`/app/me`: avatar, interests, gamification toggle, font/TTS); heavy settings (data export/reset, account) → **Parent account** (`/parent/account`) | Destructive/account/billing controls must not be in a child's reach (COPPA, spec 08). |
| — | `/account` (redirects to `/`) | **REPLACED** | Parent `/parent/account` | Becomes the real account/consent/data surface (spec 06/08). |
| — | `/palace` (redirects to `/`) | **REMOVED** | — | Memory Palace is dead (build tree B4.4); drop the route. |

**Net student nav: 4 tabs** — Today, Learn, Practice, My Stuff — plus a Catalog entry reachable from Today/Learn
(not a primary tab). **Status and heavy Settings are removed from the student view** (FE5.5).

### 1.3 Route namespace convention (new)

To make guards trivial and unambiguous:
- `/app/*` → **student** shell (requires `role=student` session). Legacy `/learn`, `/quiz`, `/review`,
  `/schedule`, `/` issue a 301/302 to the corresponding `/app/*` route **when the session is a student**.
- `/parent/*` → **parent** shell (requires `role=parent`). Pages owned by spec 06.
- `/admin/*` → **admin/ops** (requires `role=admin`).
- `/auth/*` → unauthenticated onboarding/login (signup, verify, login, reset, consent capture).
- `/api/*` → JSON; every per-student endpoint resolves `student_id` from the session (B15.3), never from the
  query string for a student role (a student cannot pass an arbitrary `student_id`).

Legacy routes are kept as **redirect shims** for one release so deep links and the global creation banner
(`base.html` `/api/creation_status` poll) keep working; the shim resolves the role and redirects.

---

## 2. Student app shell (FE5)

New base `shell_student.html` (extends the shared skeleton). **Bottom tab bar** on mobile (4 large targets,
≥ 56px, spec 02 K-2 even larger), **left rail** on desktop. The gamification header bar from current
`base.html` is retained but **role-gated and toggle-respecting** (B22.5). Notification **bell** appears in the
student top-right (§6). No Status, no health, no billing, no other students.

Grade-band presentation (spec 02 `GRADE_BAND_PROFILES`, resolved from `students.grade_band`) drives a
`data-band="K-2|3-5|6-8|9-12"` attribute on `<body>`; the kid theme CSS (FE7/§7) keys off it: K-2 gets the
largest type, biggest targets, most iconography and least text; 9-12 gets the dense, text-forward layout
closest to today's UI.

### 2.1 Today — `/app/today` (FE5.1) — *merges Home + Schedule-due*

| Field | Value |
|---|---|
| **Route** | `GET /app/today` |
| **Template** | `app_today.html` (new; replaces `home.html` for students) |
| **Purpose** | The single "what do I do now" surface. One primary CTA, the due queue, the daily quest. |
| **Contents** | (1) **Continue / Next lesson** card — resume the active enrollment at its `current_concept_uid`, deep-links into `/app/learn?course_uid=…&concept_uid=…`. (2) **Due today** strip — count + "Practice now" → `/app/practice?mode=due` (replaces the Schedule-due surface). (3) **Daily quest** — today's `student_quests` row (target + progress ring), XP reward. (4) Streak chip (gamification, toggle-respecting). (5) Empty-state when no enrollment → "Pick your first course" → `/app/catalog`. |
| **Data sources** | `GET /api/today` (new aggregate; see §9) → `{ next_lesson, due_count, daily_quest, streak, has_enrollment }`. Backed by `enrollments` (active + `current_concept_uid`), `flashcards`/`scheduled_reviews` due count (spec 01 §2.1), `student_quests` (spec 01 §6), `student_gamification`. Reuses logic from current `/api/stats`, `/api/review_stats`, `/api/due_cards` but **scoped to `student_id`**. |
| **Grade-band** | K-2/3-5: one card visible at a time, large illustration, ≤1 line text, TTS read-aloud of the CTA available; 6-8/9-12: compact multi-card grid (closer to today's `home.html` stats row). |

### 2.2 Learn — `/app/learn` (FE5.2) — *keeps `learn.html`, adds skill-tree*

| Field | Value |
|---|---|
| **Route** | `GET /app/learn?course_uid=…[&concept_uid=…]` |
| **Template** | refactor of existing **`learn.html`** + `session.js`, `session-rails.js`. |
| **Purpose** | The Socratic session (unchanged core) plus a **skill-tree map** (spec 07 / B22.2 / FE8) as the navigation view. |
| **Contents** | Adds **MAIN VIEW 0: skill-tree** above the existing path view. The current `#path-view` (vertical concept path) becomes the *within-course* view; the new tree is the *strands→standards* map from spec 07. Existing `#session-view` (chat shell, lines 43–90) is untouched — the Gemini-style chat, mastery bar, bloom badge, image attach, TTS all stay. Enrolled-course switcher replaces the old course-list entry into Learn. |
| **Data sources** | `GET /api/course_structure?uid=` (existing, scoped to student), new `GET /api/skill_tree?course_uid=` (spec 07) for the tree, `POST /api/event` for `SET_CONTEXT` / `NAVIGATE_TO_TOPIC` / `TEXT_INPUT` (unchanged FSM path), `state_update`/`status_update`/`stream_token` Socket.IO (now room-scoped per B15.5). |
| **Concept-click flow** | Unchanged from today: node click → `enterNode(uid,title)` → hide tree/path, show chat → `sendEvent('NAVIGATE_TO_TOPIC',{topic_id,course_uid})` → FSM opens with band-appropriate question (spec 02). The nav guard in `session.js` is reused. |
| **Grade-band** | Tree node density and labels follow band (K-2 sees few large nodes with icons; 9-12 sees the full strand map). Bloom badges already present; band sets floor/ceiling (spec 02 §5). |
| **Removed** | dead `#back-to-path-btn`, ZIM/sudo modal, Memory Palace rail in `session-rails.js` (`updatePalace`) for the student shell. |

### 2.3 Practice — `/app/practice` (FE5.3) — *merge Quiz + Review*

| Field | Value |
|---|---|
| **Route** | `GET /app/practice?mode=quiz\|due\|flashcards` (default = smart: due if any, else quiz) |
| **Template** | `app_practice.html` — a thin host that mounts the **existing** `quiz.html` chat shell and `review.html` chat shell as two sub-modes (both already use the identical `learn-container`/`session-interface` markup, verified). A segmented toggle ("Test yourself" / "Flashcards") switches sub-mode without a full page load. |
| **Purpose** | One surface for assessment + spaced repetition; removes two tabs from kid nav. |
| **Contents** | Sub-mode **Test yourself** → existing quiz flow (`/api/quiz`, `/api/quiz/grade`). Sub-mode **Flashcards/Due** → existing review flow (`/api/due_cards`, `/api/grade_card_fsrs`, `/api/auto_generate_flashcards`). The "due today" count on Today deep-links here with `mode=due`. |
| **Data sources** | existing quiz/review endpoints, **scoped to `student_id`**; FSRS engine unchanged. |
| **Grade-band** | K-2/3-5 practice uses manipulative/visual answer widgets where available (spec 02 §6 B17.5) and a single grade button set with friendly labels; 9-12 keeps the Again/Hard/Good/Easy granularity. |

### 2.4 My Stuff — `/app/me` (FE5.4) — *interests, gamification profile, avatar*

| Field | Value |
|---|---|
| **Route** | `GET /app/me` |
| **Template** | `app_me.html` — a **light** refactor of the profile/appearance portions of `settings.html` only. |
| **Purpose** | Kid-safe self-expression + the few preferences a child may control. |
| **Contents** | (1) **Avatar** picker (cosmetics unlocked via `student_gamification.cosmetics`, B22.4) — no arbitrary image upload for minors by default (parent setting). (2) **Interests** chips (writes `students.interests`, max 20; feeds interest-themed exams B18.3 and recommendations). (3) **Gamification profile** — level, XP, streak, badges (`student_badges`), current quest. (4) Kid-safe **preferences**: TTS on/off + rate, font scale, reduced-motion, gamification on/off toggle (B22.5). |
| **NOT here** | data export, reset progress, account deletion, billing, password, consent — all **parent-only** (`/parent/account`, spec 06/08). The destructive "reset progress" dialog from `settings.html` is removed from the student surface. |
| **Data sources** | `GET/PATCH /api/student/profile` (new, scoped) → name/avatar/interests/settings on `students`; `GET /api/gamification` (scoped); `student_badges`, `student_quests`. |
| **Grade-band** | K-2 reduces this to avatar + a couple of toggles with icons; older bands get the full list. |

### 2.5 What is removed from the student view (FE5.5)

- **Status / health** (`/status`) → admin only.
- **Heavy Settings** (data export, reset, account, theme-as-system) → parent account.
- **Course creation / custom wizard** (`/courses/new`, `courses.js` quick-create, `wizard.js`) → not in student
  nav; custom electives are *requested* and go through parent approval (§3).
- **Memory Palace**, ZIM/sudo modal, EDIT_MESSAGE — dead, removed.

---

## 3. Catalog browse + enroll (replaces "create course first" for students)

The student's path to learning is **browse → enroll**, not **create**. (B16.2 catalog, B16.4 published subjects,
B19.3 elective approval.)

### 3.1 Browse — `/app/catalog`

| Field | Value |
|---|---|
| **Template** | `app_catalog.html` (new). Visually a refactor of the course-card grid from `courses.html`/`courses.js`, but cards are **published catalog courses**, not user-created. |
| **Query** | `GET /api/catalog?grade_band=<from session>&subject=<optional>` → only `courses` rows where `is_catalog=1 AND catalog_status='published' AND grade_band = student.grade_band` (spec 01 §4.1). The student's band is taken from the session, **never** from the request — a 3-5 student cannot list 9-12 courses. |
| **Card** | subject icon, title, standards-coverage chip (count of Utah codes, spec 01 §4), grade band, "Start" / "Enroll" button. Enrolled courses show "Continue". |
| **Browse axes** | by **subject** (math, ela, science, …), within the student's band only. Optional skill-tree entry: "See the map" → catalog-as-tree (FE8, spec 07). |
| **Empty state** | if no published courses for the band yet: friendly "New courses are coming for your grade" + suggestion to ask a parent for a custom elective. |

### 3.2 Enroll (catalog course)

1. Student taps **Start/Enroll** → `POST /api/enroll {course_uid}`.
2. Server validates `course_kind='catalog'`, band match, and **seat/plan/consent gates** (spec 08/09):
   creates `enrollments` row (`status='active'`, `course_kind='catalog'`, `current_concept_uid=NULL`).
3. Redirect into `/app/learn?course_uid=…` at the first concept. First-run shows the skill-tree/path with the
   first node `current`, rest `locked` (existing path-view states).

Catalog enroll is **immediate** — published catalog is already parent-/CMS-approved content, so no per-enroll
parent gate (the parent controls plan/consent at the family level, and may set "approval required for all
new courses" as an account option, spec 06).

### 3.3 Custom electives → behind parent approval (B19.3)

The old free-form creation flow becomes an **elective request**:
1. From catalog (or a "Request something else" entry), the student or parent describes a desired elective.
2. If initiated by a **student**: `POST /api/elective_request {topic, notes}` → creates an `enrollments`
   row with `course_kind='elective'`, `status='pending_approval'`, and a notification of kind
   `elective_request` to the parent (§6). The student sees "Sent to your parent for approval" (no build starts).
3. The **parent** approves in `/parent/electives` (spec 06): on approve, `enrollments.approved_by=parent_id`,
   `approved_at=now`, `status='active'`, and the **existing custom build pipeline** (`wizard.js` /
   `course_builder.py` / `/api/create_custom_course`) runs *server-side under the parent's authority*, writing a
   band-appropriate elective course into `data/courses/` (not the catalog). On deny → `status='denied'` + a
   gentle student notification.
4. Parents may *also* author electives directly from their dashboard (spec 06) — same pipeline, no request step.

This keeps the powerful authoring tooling but removes it from a child's unsupervised reach and routes it through
consent/seat/approval gates.

---

## 4. Role-based routing & shell switching

Depends on B15.4 (Flask-Login) and `03_AUTH_*`. The session carries `role ∈ {parent, student, admin}` and,
for a student session, the resolved `student_id`; for a parent session, `parent_id` plus an **active
`student_id` context** when the parent is "viewing as" or launching for a child.

### 4.1 Landing logic (post-login)

```
on authenticated request to "/" or "/auth/login" success:
  if role == admin    -> redirect /admin
  elif role == parent -> if parent has 0 students  -> /auth/onboard/add-student   (finish onboarding)
                         elif consent incomplete    -> /auth/onboard/consent
                         elif no active plan/trial  -> /parent/billing (soft gate; can still view)
                         else                        -> /parent            (children overview, spec 06)
  elif role == student-> if first_run (no interests / not placed) -> /app/onboard
                         else                                       -> /app/today
  else (unauthenticated) -> /auth/login
```

A **student session never lands in a parent/admin route**; the guard on `/parent/*` and `/admin/*` 403s (or
redirects to the student's `/app/today`) for a student role. Legacy `/learn` etc. redirect to `/app/learn` for
students, to a 404/own-route for parents.

### 4.2 Account switcher

- **Parent → child:** the parent dashboard (spec 06) has a per-child switcher. "Launch for <child>" calls
  `POST /api/launch_child {student_id}` which mints a **student session** (sets `role=student`, the chosen
  `student_id`) and redirects to `/app/today`. This is the COPPA-clean handoff (parent authorizes, then the
  child uses the kid app). An audited `audit_log` row (`action='login'`/`launch_child`) is written (spec 01 §7).
- **Child → parent:** a child **cannot** switch to the parent dashboard. Returning to the parent requires the
  parent's credential (a "Parent area" button on `/app/me` prompts for the parent password / re-auth → restores
  the parent session). This is a deliberate guard, not a UX afterthought.
- **Parent ↔ student-of-parent:** switching active child is allowed within a parent session for *viewing*
  (dashboard) but *launching* always mints a fresh student session as above.

### 4.3 Deep-link & guard behavior

- Unauthenticated hit on any `/app/*`, `/parent/*`, `/admin/*` → redirect to `/auth/login?next=<path>` and honor
  `next` after login **only if the role matches** (a student logging in to a `next=/parent/...` is sent to
  `/app/today`, not the parent route).
- `/api/*` for a wrong role → `403` JSON, never a silent fallback.
- The global creation banner poll (`base.html`) and any cross-page poller must include the session cookie and be
  no-ops for a student (students don't build courses).

---

## 5. Onboarding flows (FE7)

All onboarding lives under `/auth/*` (parent) and `/app/onboard` (student first-run). Templates are new
auth-shell pages (`shell_auth.html`, minimal chrome, no nav). Each step persists progress so a drop-off can
resume (parent: `parents.status='pending_verify'` etc.; student: `students.interests`/placement flags).

### 5.1 Parent onboarding (signup → handoff)

| # | Step | Route | Writes | Gate to next |
|---|---|---|---|---|
| 1 | **Sign up** | `GET/POST /auth/signup` | `parents` row (email, argon2id `password_hash`, `status='pending_verify'`) | valid email + password; rate-limited (spec 03) |
| 2 | **Email verify** | link → `GET /auth/verify?token=` | `parents.email_verified_at`, `status='active'` | token valid (transactional email §6) |
| 3 | **Consent (COPPA/TOS)** | `GET/POST /auth/onboard/consent` | `consent_records` rows: `tos`, `privacy`, `coppa_data` (one per `consent_type`), each with `policy_version`, `method='checkbox'`, `ip_address` | TOS + privacy + COPPA all granted (spec 08). **No child may be created or launched until COPPA consent exists.** Health Strand 6 (`health_strand6`) captured later, per-course, when relevant. |
| 4 | **Add first student** | `GET/POST /auth/onboard/add-student` | `students` row: `display_name`, `grade_band` (required), `grade_numeric` (optional), `interests[]`, optional `pin_hash` (4-digit). Optional `accommodations` row (IEP/504 flags, spec 01 §7 / B25.4). | name + grade_band present; seat limit checked against `subscriptions.seats` (spec 09) |
| 5 | **Choose plan / trial** | `GET /auth/onboard/plan` → `09_BILLING.md` | `subscriptions` (`status='trialing'` or via Stripe Checkout) | a plan or trial selected; **soft** — parent can skip and land on dashboard with a "start trial" banner, but child launch may be gated by spec 09 |
| 6 | **Hand off to child** | `/parent` → "Launch for <child>" | student session minted (§4.2); `audit_log` | — (enters student first-run) |

**First-run / empty states (parent):**
- 0 students → dashboard shows a single "Add your first learner" CTA (cannot reach the rest until step 4 done).
- Consent incomplete → hard redirect to step 3 on any `/parent/*` or child-launch attempt.
- No plan and trial expired → child launch gated with an upgrade prompt (spec 09); dashboard remains viewable.

### 5.2 Student onboarding (young learner first-run)

Entered after a parent launches for the child (so identity + consent already exist). Designed for a kid who may
not read fluently: large targets, TTS prompts, minimal text (spec 02 K-2 register).

| # | Step | Route | Writes | Notes |
|---|---|---|---|---|
| 1 | **Avatar + PIN login** | `/auth/student-login` (avatar grid → tap own avatar → 4-digit PIN) | session (`role=student`) | For returning kids. First-run is reached via parent launch; subsequent logins use this. PIN compared against `students.pin_hash`. If no PIN set, login is parent-launch-only. |
| 2 | **Interest capture** | `/app/onboard` (step: interests) | `students.interests[]` | Big tappable icon chips (sports, animals, space, art…). Feeds interest-themed exams (B18.3) and catalog recommendations. Skippable. |
| 3 | **Placement (optional diagnostic)** | `/app/onboard` (step: placement) → spec 05 | `exam_attempts` (`kind='diagnostic'`) + per-standard results | Offered, not forced. Sets a starting point in the catalog/skill-tree. K-2: short, playful, manipulative-based; can be deferred by parent. |
| 4 | **First lesson** | redirect `/app/learn?course_uid=…` | `enrollments` (auto-enroll into the recommended first catalog course for the band) | Lands directly in a Socratic session at the first concept — the "aha, I'm learning" moment within a minute. |

**First-run / empty states (student):**
- No interests yet → onboarding step 2 is the landing; can skip to a default recommended course.
- No placement → step 4 enrolls in the band's recommended starter course at concept 1.
- No published catalog for the band → friendly "Ask a grown-up to set up a course" + a sample/demo lesson if
  available; no dead end.

---

## 6. Notifications & communications (B24.1–4)

One model serves both **in-app** (the `notifications` table surfaced via a bell) and **email** (transactional +
digests + alerts). Reuses canonical `notifications` (spec 01 §7): `recipient_id`, `recipient_role`, `kind`,
`title`, `body`, `ref_uid`, `read_at`, `created_at`.

### 6.1 Channels

| Channel | Used for | Recipients |
|---|---|---|
| **In-app bell** | every `notifications` row (real-time-ish via existing Socket.IO room; falls back to poll) | parent + student |
| **Transactional email** | verification, password reset, receipts, elective approve/deny, seat/billing events | parent only (no email to minors) |
| **Digest email** | weekly per-child progress digest | parent only |
| **Alert email + bell** | struggle / inactivity alerts | parent (email + bell); student gets a gentle in-app-only nudge |
| **In-app student nudge** | "you have due cards", "continue your lesson", quest progress | student only, **quiet by default**, parent-controllable |

**No SMS/push in R3** (defer). **No email to a student account, ever** (COPPA / spec 08).

### 6.2 In-app bell

- A bell partial (`_notif_bell.html`) injected into all three shells' header. Shows unread count (`read_at IS
  NULL`), opens a dropdown list newest-first.
- Realtime: when a notification is created for a recipient, emit a Socket.IO `notification` event to that
  recipient's **room** (B15.5 room scoping — recipient room = role+id). The bell increments without a poll;
  a 60s poll on `GET /api/notifications?unread=1` is the fallback.
- Mark-read on open / per-item (`POST /api/notifications/{id}/read`, or `POST /api/notifications/read_all`).
- **Student bell** is intentionally sparse and gentle: no red urgency badges for K-2 (a soft dot instead), no
  count-shaming. Respects the student/parent nudge preferences (§6.5).

### 6.3 Notification-type table

| `kind` | Trigger | Recipient(s) | Channels | Quiet for young? | `ref_uid` |
|---|---|---|---|---|---|
| `verify_email` | signup step 1 | parent | email | n/a | — |
| `password_reset` | reset request | parent | email | n/a | — |
| `receipt` | Stripe payment/invoice (spec 09) | parent | email + bell | n/a | invoice id |
| `seat_limit` | add-student over `subscriptions.seats` | parent | bell (+ email) | n/a | — |
| `trial_ending` | trial N days from expiry | parent | email + bell | n/a | sub id |
| `elective_request` | student submits custom elective (§3.3) | parent | bell + email | n/a | enrollment id |
| `elective_approved` | parent approves | student | bell (gentle) | yes | enrollment id |
| `elective_denied` | parent denies | student | bell (gentle, encouraging copy) | yes | enrollment id |
| `due_review` | student has due FSRS cards (daily window) | student | bell (gentle nudge) | **yes** — suppressed/softened for K-2 unless parent opts in | course/concept uid |
| `streak_nudge` | streak at risk | student | bell (gentle) | **yes** — off by default for K-2/3-5 (no streak pressure) | — |
| `quest_complete` | daily/weekly quest done | student | bell (celebratory, toggle-respecting) | yes | quest id |
| `weekly_digest` | weekly cron, per child | parent | email | n/a | student id |
| `struggle_alert` | repeated misses / mastery stall on a concept | parent | email + bell | n/a | student+concept |
| `inactivity_alert` | no activity ≥ N days | parent | email + bell | n/a | student id |
| `system` | maintenance / catalog update | parent (and admin) | bell | n/a | — |

**Anti-dark-pattern rules (enforced):** student-facing nudges (`due_review`, `streak_nudge`, `quest_complete`)
are (a) capped to **one per day per kind**, (b) **off by default for K-2 and 3-5** for streak/urgency types,
(c) phrased as invitations not guilt ("Want to keep going?" not "You're about to lose your streak!"), and
(d) fully controllable by the parent (§6.5). No notification ever blocks the UI or uses countdown timers for a
minor.

### 6.4 Triggers — where they fire

- **Transactional** (`verify_email`, `password_reset`, `receipt`, `elective_*`, `seat_limit`, `trial_ending`):
  fired inline from the relevant request handler / Stripe webhook (spec 09).
- **`due_review` / `streak_nudge` / `quest_*`**: evaluated by a **daily cron** (per student, in the student's
  local window) + opportunistically on session end (FSM). The FSM already tracks streaks/quests (spec 01 §6).
- **`struggle_alert`**: emitted from the FSM grading path (`fsm_logic.py`) when a student fails the mastery gate
  repeatedly on a concept (reuses the affect-detection signal from spec 02 §6 B17.7), debounced so a parent
  isn't spammed (≤1 per concept per day).
- **`inactivity_alert`** + **`weekly_digest`**: weekly cron over `activity_log` per `student_id` (spec 01 §2.1).

### 6.5 Preferences (parent-controllable, no dark patterns)

- `GET/POST /api/notifications/preferences` (parent scope) — per-channel and per-`kind` toggles, with sane,
  *gentle* defaults: digests **on**, alerts **on**, student nudges **off for K-2/3-5** and **gentle/on for 6-8/9-12**.
- Stored on `parents`/`students.settings` (JSON) as `notif_prefs`. Students may *only* soften their own nudges
  (turn down/off), never enable parent-only channels.
- Every email includes a one-click unsubscribe for **non-transactional** mail (digests/alerts); transactional
  mail (verify/reset/receipt) is exempt (legally required), per CAN-SPAM.

### 6.6 Email-sending approach (provider + queue)

- **Provider:** a pluggable `EmailProvider` interface (mirrors the STT-backend pattern) with an SMTP/transactional
  default (e.g. self-hostable SMTP or a transactional API behind one adapter). Self-hosted-friendly so no minor
  PII leaves the deployment beyond the email address itself (spec 08/B21.3). Provider, credentials, and the
  `from`/reply-to are env-configured.
- **Queue:** notifications are **enqueued, not sent inline**. A lightweight durable queue (a `notification_outbox`
  job table, or Redis when present — Redis is already on the R4 roadmap, B23.5) decouples request latency from
  delivery and gives retry-with-backoff. A worker (a `gevent` greenlet in R3, a separate process at scale)
  drains the outbox: render template → send → mark sent / retry / dead-letter. In-app rows are written
  synchronously (cheap); email is queued.
- **Templating:** transactional + digest emails use server-side Jinja templates (text + HTML multipart),
  brand-light, with required unsubscribe/footer. The weekly digest renders per-child progress from
  `06_PARENT_DASHBOARD.md`'s data (mastery, time, standards coverage, struggles) — one email per parent
  summarizing all children.
- **Idempotency:** each queued email carries a dedupe key (e.g. `weekly_digest:{student_id}:{period_key}`) so a
  cron re-run can't double-send.

---

## 7. Component / design-system implications (FE7 / FE8)

Reuse the **token-based CSS (FE1)** — do not fork. Add, don't replace.

- **Auth/onboarding screens (FE7):** new `shell_auth.html` (minimal chrome) + reusable form components
  (`forms/inputs/selects` — promotes the inline-styled wizard inputs noted in FE2.3 to tokenized components).
  A stepper/progress component for the multi-step parent + student flows (reuse the wizard's step-dot pattern
  from `wizard.js`/`course_wizard.html`). Consent screens are plain, legible, checkbox-based (spec 08).
- **Kid theme variant:** a `data-band` + `body.kid` CSS layer over the existing Alpine tokens — larger type
  scale, bigger radii, bigger hit targets, more iconography, calmer motion for `reduced_motion`/K-2. It is a
  *theme variant* (token overrides) not a separate stylesheet, so dark/light parity (FE4.1) is inherited.
- **Skill-tree view (FE8 / spec 07 / B22.2):** a reusable tree/graph component (SVG, the same approach as the
  existing `#path-svg` in `learn.html`) rendering strands→standards as branches→nodes. Used in **two places**:
  catalog-as-tree browse (`/app/catalog` "See the map") and within `/app/learn`. Node states reuse the existing
  completed/current/locked styling + bloom/mastery badges already in `learn.html`.
- **Notification bell partial** (`_notif_bell.html`) + dropdown — a new shared component across all three shells.
- **Nav components:** `app-nav` becomes a per-shell block (§1.1); the student bottom-tab bar and the parent top
  nav are two variants of a shared nav component. Retire the single hardcoded 8-link nav in `base.html`.
- Consolidate the duplicated `escapeHtml`/socket-connection helpers noted in the build tree (Task #7) while
  touching these files.

---

## 8. Accessibility hooks (must not block B25)

The IA must leave room for the WCAG 2.1 AA pass (B25.1, spec elsewhere). Concretely:

- **Keyboard & focus:** every new nav (student tabs, parent nav, account switcher, bell dropdown) is reachable
  and operable by keyboard; visible focus ring (FE1.6 global `:focus-visible` already exists — keep it). Bell
  dropdown and consent modals are focus-trapped and `Esc`-dismissable.
- **`aria-live` for chat:** the Socratic chat stream (`#chat-stream`) already has `role="log" aria-live="polite"`
  (verified `learn.html:71`) — preserve it through the refactor; do not regress to a silent re-render. Status/
  thinking updates use a polite live region, not assertive (avoid interrupting a screen reader mid-answer).
- **Larger targets for K-2:** the kid theme variant sets minimum target size (≥56px, larger than the 44px AA
  floor) for the student shell's primary actions; bottom-tab and CTA buttons especially.
- **TTS / read-aloud:** Today's CTA and lesson prompts expose a read-aloud control (reuses `/api/tts`,
  `playMessageTTS` in `session.js`); default-on for K-2 (spec 02 §6). Alt-text on avatars/illustrations feeds the
  text-only/TTS path (B13.9/B25.5).
- **Reduced motion:** honor `prefers-reduced-motion` and the per-student `settings.reduced_motion`
  (spec 01 §2) — suppress confetti/streak-pulse/path animations for those students.
- **Notifications a11y:** bell count is announced; gentle student nudges never auto-focus or steal the reading
  cursor.

---

## 9. Endpoint / route table (new pages + notification APIs)

### 9.1 Page routes (shells)

| Method | Route | Role | Renders | Notes |
|---|---|---|---|---|
| GET | `/auth/login` | none | `shell_auth` login | avatar+PIN variant at `/auth/student-login` |
| GET/POST | `/auth/signup` | none | parent signup | §5.1 step 1 |
| GET | `/auth/verify` | none | verify result | token in query (§5.1 step 2) |
| GET/POST | `/auth/reset` | none | password reset | transactional email |
| GET/POST | `/auth/onboard/consent` | parent | consent capture | §5.1 step 3 (spec 08) |
| GET/POST | `/auth/onboard/add-student` | parent | add child | §5.1 step 4 |
| GET | `/auth/onboard/plan` | parent | plan/trial | §5.1 step 5 (spec 09) |
| GET | `/app/today` | student | Today | FE5.1 |
| GET | `/app/learn` | student | Learn (tree+path+session) | reuses `learn.html` |
| GET | `/app/practice` | student | Practice (quiz+review) | `?mode=` |
| GET | `/app/me` | student | My Stuff | FE5.4 |
| GET | `/app/catalog` | student | Catalog browse | grade-filtered |
| GET | `/app/onboard` | student | first-run (interests/placement) | §5.2 |
| GET | `/parent` | parent | children overview | spec 06 |
| GET | `/parent/electives` | parent | elective approval queue | spec 06 / B19.3 |
| GET | `/parent/account` | parent | account/consent/data/settings | spec 06/08 |
| GET | `/admin` , `/admin/status` , `/admin/cms` | admin | ops + CMS | reuses `status.html`; CMS B26 |
| — | `/` , `/learn` , `/quiz` , `/review` , `/schedule` , `/settings` , `/courses` | * | **redirect shim** | role-resolve → new route; one release |
| — | `/palace` , `/account` (old) | * | **removed** | drop |

### 9.2 API endpoints (new / re-scoped)

| Method | Endpoint | Role | Purpose |
|---|---|---|---|
| GET | `/api/today` | student | aggregate for Today (next lesson, due count, quest, streak) |
| GET | `/api/catalog?grade_band=&subject=` | student | published catalog for the student's band (band from session) |
| POST | `/api/enroll` | student | enroll in a catalog course → `enrollments` |
| POST | `/api/elective_request` | student | request a custom elective → `pending_approval` + notify parent |
| GET/PATCH | `/api/student/profile` | student | name/avatar/interests/settings on `students` |
| GET | `/api/skill_tree?course_uid=` | student | tree data (spec 07) |
| POST | `/api/launch_child` | parent | mint a student session for a child (§4.2) |
| POST | `/api/elective/{enrollment_id}/approve` , `/deny` | parent | elective approval (B19.3) |
| GET | `/api/notifications?unread=1&limit=` | parent/student | list (scoped to recipient) |
| POST | `/api/notifications/{id}/read` | parent/student | mark one read |
| POST | `/api/notifications/read_all` | parent/student | mark all read |
| GET/POST | `/api/notifications/preferences` | parent | per-kind/channel toggles (student may only soften own) |
| (Socket.IO) | `notification` event | recipient room | realtime bell increment |

All per-student endpoints resolve `student_id` from the session (B15.3); a student passing a `student_id` is
ignored/403. Existing endpoints (`/api/quiz`, `/api/due_cards`, `/api/grade_card_fsrs`, `/api/course_structure`,
`/api/event`, `/api/gamification`, …) are **re-scoped** to the session's `student_id` rather than the global user.

---

## 10. Test plan / acceptance criteria

**IA & routing**
- A logged-in **student** sees exactly 4 tabs (Today, Learn, Practice, My Stuff) + a Catalog entry; **no** Status,
  no heavy Settings, no billing, no course-create.
- A **parent** lands on `/parent` (children overview); a **student** lands on `/app/today`; an **admin** on
  `/admin`. Wrong-role deep links redirect/403 per §4.3 (student → never reaches `/parent/*` or `/admin/*`).
- Legacy `/learn`, `/quiz`, `/review`, `/schedule`, `/`, `/settings` redirect to the correct new route for the
  session's role; `/palace` and old `/account` are gone.

**Catalog & enrollment**
- `/app/catalog` returns **only** `is_catalog=1 AND catalog_status='published'` courses whose `grade_band` equals
  the session student's band; a 3-5 student cannot list or enroll in a 9-12 catalog course (band taken from
  session, not request).
- Catalog enroll creates an `active` enrollment and drops the student into the first concept; a student "create
  course" attempt becomes an `elective_request` (`pending_approval`) and notifies the parent — no build starts
  until parent approval.

**Onboarding (end-to-end)**
- Parent flow completes signup → verify → consent (TOS+privacy+COPPA rows written) → add student (grade_band
  required) → plan/trial → launch child, with each gate enforced (no child launch before COPPA consent; seat
  cap enforced at add-student).
- Student first-run completes avatar/PIN → interest capture (writes `students.interests`) → optional placement
  (writes a `diagnostic` `exam_attempt`) → lands in a live Socratic lesson. Empty states (no interests, no
  placement, no catalog) never dead-end.

**Notifications**
- Each `kind` fires on its trigger (§6.3/§6.4): `elective_request` on student request, `struggle_alert` on
  repeated mastery-gate failure (debounced ≤1/concept/day), `weekly_digest`/`inactivity_alert` on the weekly
  cron, transactional emails on verify/reset/receipt.
- Student nudges (`due_review`, `streak_nudge`) are **off by default for K-2/3-5**, capped to 1/day/kind, gentle
  copy, and respect parent `notif_prefs`; **no email is ever sent to a student**.
- Bell shows correct unread count and clears on read; realtime `notification` event reaches only the recipient's
  room (no cross-student/parent leakage — pairs with B15.5/B15.8 isolation tests).
- Email queue: enqueue → worker drains → idempotent (a re-run of the weekly cron does not double-send, dedupe key
  honored); non-transactional mail carries unsubscribe.

**Accessibility (non-blocking gates)**
- All new nav, the account switcher, and the bell dropdown are keyboard-operable with visible focus; chat keeps
  `aria-live="polite"`; K-2 primary targets ≥56px; reduced-motion suppresses celebratory animation.

---

## 11. Open questions

1. **Admin role granularity** — is "admin/ops" one role or split into operator (health/infra) vs CMS reviewer
   (B26 draft→published)? Spec assumes one `role=admin` with sub-permissions; confirm against spec 06/B26.
2. **Account switcher re-auth strength** — child→parent requires the parent password; do we want a lighter
   "parent PIN" for quick switches on a shared device, or always full password? (COPPA-clean either way; UX call.)
3. **Catalog approval default** — do all catalog enrolls auto-activate, or can a cautious parent require approval
   for *every* new course (not just electives)? Spec leaves this as a per-account option (spec 06); confirm default.
4. **Quiet-hours for student nudges** — beyond per-band defaults, do we honor a parent-set quiet window
   (e.g., school hours / bedtime)? Likely yes; needs a `notif_prefs.quiet_hours` shape.
5. **Digest cadence** — weekly fixed, or parent-selectable (weekly/biweekly/off)? Spec assumes weekly default,
   off-able.
6. **Placement gating** — should a low placement *lock* higher catalog courses for the band, or only
   *recommend* a starting point? Spec 05 owns the diagnostic; IA assumes recommend-not-lock for v1.
7. **PIN-less young kids** — for pre-readers with no PIN, login is parent-launch-only; is an avatar-only
   (no PIN) "kiosk mode" acceptable on a trusted family device? Defer to spec 03/08.
8. **Skill-tree as primary vs secondary** — is the FE8 tree the default Learn landing for older bands, or always
   secondary to the linear path? Spec 07 decides; IA wires both entry points.
