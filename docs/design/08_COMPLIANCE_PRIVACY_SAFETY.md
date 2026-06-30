# Design Spec 08 — Compliance, Privacy & Safety (B21)

> Implementation-ready mechanism design for the K-12 platform's legal posture and minor-safety
> controls. Covers **B21.1–B21.6** (Workstream F, P1/R2). Canonical table/column names come from
> `docs/design/01_DATA_MODEL.md` and are used verbatim here (`consent_records`, `audit_log`,
> `accommodations`, `parents`, `students`, `notifications`). Curriculum/legal grounding for Health
> Strand 6 is `docs/UTAH_K12_CURRICULUM_REFERENCE.md` (Subject 6, Strand HD). Existing code we extend:
> `services/core/safety.py` (B8 filter), `services/common/prompts.py` (`sanitize_untrusted`,
> `UNTRUSTED_FENCE`, `SOCRATIC_SYSTEM_RULES`), `services/core/fsm_logic.py` (safety gate at
> `transition()`, `_detect_ignorance`), `services/web-ui/app.py` (`FLASK_SECRET_KEY`, `csrf_protect`).
>
> ⚠️ **This is mechanism design, not legal advice.** The company **retains qualified counsel for
> sign-off** on every published policy document, on the verifiable-consent method finally adopted, and
> on the breach-notification timelines per jurisdiction. This spec designs the *technical controls* that
> make compliance enforceable in code; counsel determines the *legal obligations* those controls satisfy.
> Open questions for counsel are collected in §11.

---

## 0. Scope, principles, and threat model

**Who we protect:** children under 13 (COPPA), all K-12 students as "education records" subjects
(FERPA + Utah Student Data Protection Act, Utah Code §53E-9-2xx), and the special case of Health
Strand 6 (Human Development) instruction (Utah Code parental notify/consent).

**Design principles (enforced in code, not just policy):**
1. **Consent precedes use.** A child account is *inert* — it cannot be launched, enrolled, or sent to
   the tutor — until the controlling consent records exist and are current. The gate is a hard server
   check, not a UI affordance (§1, §3).
2. **Data minimization for minors.** Children's accounts collect the minimum needed to teach: a
   display name, grade band, and learning data. No precise geolocation, no behavioral ad profile, no
   third-party trackers, no sale of data (§1.3).
3. **Inference stays home.** No minor's prompt, answer, or PII ever leaves the family's
   self-hosted boundary to a third-party LLM. This is the single largest privacy advantage of the
   architecture and it is *enforced by the deployment topology*, not by a vendor promise (§1.4, B21.3).
4. **Everything that touches a student record is audited.** Every read/export/delete/consent change of
   student data writes `audit_log` (§2.4).
5. **Defense in depth on the model.** Untrusted input is fenced (existing `sanitize_untrusted`), checked
   before tutoring, the system prompt constrains the model, and model output is checked before it
   reaches a child (§5).
6. **Withdrawable, not one-way.** Consent can be withdrawn; withdrawal has a defined, audited effect
   (§3.4).

**Threat model (in scope):** child types unsafe free text; model emits unsafe/age-inappropriate output;
prompt-injection via answers or uploaded source content; parent of one family accessing another
family's data (tenant isolation, spec 01 §8); a breach exfiltrating `helga.db`; an unconsented child
reaching Health Strand 6 content; secrets leaking from config. **Out of scope here** (covered elsewhere):
billing fraud (B20), GPU fair-queue DoS (B23.1), full WCAG (B25).

---

## 1. COPPA — verifiable parental consent before a child account is usable (B21.1, B21.3)

COPPA governs operators collecting personal information from children **under 13**. The controlling
fact in our model is `students.grade_band`/`grade_numeric`: a student with `grade_numeric < 7` (roughly
under 13) — i.e. bands `K-2`, `3-5`, and the younger half of `6-8` — is treated as **COPPA-covered**.
Because age is self-declared by the parent at account creation, we apply the COPPA gate to **all child
accounts** by default and let counsel decide whether to relax it for declared-13+ students. (Erring
toward more protection is cheap; under-protecting is not.)

### 1.1 The consent gate (the hard server check)

A child account passes through these states; the gate is the transition from `pending_consent` to usable.

```
parent registers ──► parent creates student (students.status='active', but NOT consent-cleared)
        │
        ▼
   COPPA consent flow (§1.2) ──► consent_records(consent_type='coppa_data', granted=1,
        │                                          method='card_verify', policy_version=X)
        ▼
   child account USABLE  ◄── enforced by coppa_consent_current(student_id) == True
```

**`coppa_consent_current(student_id) -> bool`** (new helper in `ConsentStore`, spec 01 §8):
returns True iff there exists a `consent_records` row with `student_id` (or the student's `parent_id`
for account-level), `consent_type='coppa_data'`, `granted=1`, and `policy_version` ≥ the current
`COPPA_POLICY_VERSION`. Withdrawn (a later `granted=0` row) flips it False.

**Enforcement points (all must call the gate — defense in depth):**
| Layer | File | Check |
|---|---|---|
| Student launch / PIN login | `web-ui/app.py` (login + `set_active_course`) | block with `403 consent_required` → redirect parent to consent flow |
| FSM session entry | `core/fsm_logic.py` `SET_CONTEXT` / `enter_mode_*` | refuse to set `active_student_id` if gate fails; emit status `CONSENT_REQUIRED` |
| Enrollment | `rag/librarian.py` enrollment create | reject `enrollments` insert |
| Any tutoring event | `core/fsm_logic.py` `transition()` top guard | drop `TEXT_INPUT`/`user_speech` with a parent-facing notice |

The FSM guard sits **above** the existing safety gate at `fsm_logic.py:809`, as a new branch at the
top of `transition()`: if the active student is COPPA-covered and `coppa_consent_current` is False,
do not process the event — `send_status_update('CONSENT_REQUIRED')` and return.

### 1.2 Verifiable parental consent method

COPPA requires the consent be *verifiable* — reasonably calculated to ensure a parent (not the child)
gives it. We implement the **"monetary transaction" / credit-card verification** method, which dovetails
with our Stripe billing (B20):

1. Parent reaches the consent screen (logged into the parent dashboard, itself behind argon2id auth, §8).
2. Parent reads the COPPA disclosure (what we collect, how we use it, that inference is self-hosted,
   their rights) and the linked Privacy Policy (§6).
3. Parent completes a **Stripe-verified card action** — either the subscription checkout (B20.1) or, for
   free/granted seats, a $0/low-value card verification (Stripe `SetupIntent`). The *successful card
   verification event* is the verifiable signal. We **store no PAN/CVV**; we store only the Stripe
   `provider_customer_id` (in `subscriptions`) and record `method='card_verify'` on the consent row.
4. On the verified webhook, write `consent_records(consent_type='coppa_data', granted=1,
   method='card_verify', policy_version=COPPA_POLICY_VERSION, ip_address=<parent ip>)`, write
   `audit_log(action='consent_change', actor_role='parent', subject_student_id=<stu>)`, and flip the
   child account usable.

> **Counsel sign-off item (§11):** confirm card-verification satisfies the current FTC verifiable-consent
> standard for our use, and whether a fallback method (signed form upload, knowledge-based verification)
> is required for parents without a card. Design accommodates a `method='signed'` value already.

### 1.3 Data minimization for children

Enforced at the schema and collection layer:
- **Collected for a child:** `students.display_name` (a first name / nickname — UI copy instructs "no full
  legal name needed"), `grade_band`/`grade_numeric`, `interests` (≤20 free strings, §1.3.1), `settings`,
  and learning data (`user_progress`, `activity_log`, `exam_attempts`, gamification). That is the floor
  needed to teach.
- **Never collected for a child:** precise geolocation, contact info (email/phone live on the *parent*
  row only), photographs (avatar is a chosen non-photo cosmetic), persistent cross-site identifiers.
- **No behavioral advertising / no third-party ad or analytics SDKs.** There is no ad targeting in the
  product at all; a lint check (CI) asserts no ad/analytics network calls in `web-ui` static assets.
- **`interests` sanitization (§1.3.1):** free-text interests pass through `sanitize_untrusted` before
  storage (they later flow into `get_socratic_tutor_prompt` user_profile and theming) and are capped at
  20 entries; this prevents a child from injecting instructions into their own tutor prompt.
- **Gamification stays within-family / anonymized** (B22.6): no open leaderboards, no public profiles.

### 1.4 Self-hosted-inference advantage and where it is enforced (B21.3)

The system's LLM calls go to **Ollama on the host** (`host.docker.internal:11434`) and STT to the
host-native ASR — both inside the family's own machine. **No minor prompt, answer, transcript, or PII is
sent to any third-party LLM API.** This is what makes the COPPA/FERPA posture defensible: there is no
"third-party operator" receiving children's data for the core tutoring loop.

**Where the data-flow boundary is enforced (must hold; CI/architecture guard):**
- `services/core/llm_client.py` / `llm_utils.py`: the only LLM endpoint is the configured
  `OLLAMA_*` host URL. **Guard:** a config-validation check at startup asserts the resolved LLM base URL
  is loopback / `host.docker.internal` / a private-range host, and **refuses to boot** (or loudly warns +
  blocks minor sessions) if it points at a public cloud LLM endpoint. A unit test
  (`tests/test_inference_boundary.py`) asserts no module imports a hosted-LLM SDK (openai-cloud, etc.).
- `services/research/research_server.py` (build-time augmentation via Wikipedia/SearXNG) is the **only**
  egress path, and it runs at **catalog-build time on operator content, never on a child's live input**.
  Document this boundary: research egress carries *curriculum topics*, not *student data*.
- TTS (Kokoro) and STT are local; no audio leaves the box.

> **Data-flow boundary statement (publish in Privacy Policy):** "Tutoring inference runs entirely on the
> device in your home. Your child's questions and answers are processed locally and are not sent to any
> outside AI provider." Counsel verifies the wording.

---

## 2. FERPA / Utah Student Data Protection Act — access, export, correct, delete, retention (B21.2)

Student learning records are "education records." Parents (and eligible students) have rights of
**access, export, correction, and deletion**, plus we owe **retention limits** and a **breach process**.
A **named Data Manager** owns this domain.

### 2.1 Named role: Data Manager

A designated company **Data Manager** (role recorded in ops docs, on-call contact in the Privacy Policy)
is accountable for: approving exports/deletes that aren't self-service, responding to parent rights
requests within the SLA, owning the breach runbook (§2.5), and being the audit-log reviewer. In the
self-hosted single-family deployment the *parent* is effectively the data controller for their family;
the company Data Manager governs the hosted/SaaS path and the codebase defaults. Counsel confirms the
controller/processor split per deployment model (§11).

### 2.2 Parent rights — concrete endpoints

All endpoints are parent-authenticated (argon2id session, §8), CSRF-protected (`csrf_protect`,
`app.py:102`), **scoped to the parent's own students** (tenant isolation via `students.parent_id`,
spec 01 §8), and **every call writes `audit_log`**.

| Right | Endpoint | Effect |
|---|---|---|
| Access (view) | `GET /api/parent/students/<stu>/record` | Read-only rendered summary; `audit_log action='view_progress'` |
| Export | `POST /api/parent/students/<stu>/export` | Builds the full data bundle (§2.3); `action='export_data'` |
| Correct | `PATCH /api/parent/students/<stu>` | Edit correctable fields (display_name, grade_band, interests, accommodations); `action='correct_data'` |
| Delete | `DELETE /api/parent/students/<stu>` | Cascade delete (§2.4); `action='delete_data'` |

### 2.3 Export — the student data bundle

`export_student_bundle(parent_id, student_id) -> dict` (new `ComplianceStore` / service method) returns
**all data tied to that `student_id`**, the isolation key (spec 01 §2). The bundle is a single JSON
(downloadable as `.json`, with a human-readable HTML render option) containing:

```jsonc
{
  "exported_at": "<iso>", "policy_version": "<x>", "format_version": 1,
  "student":        { ...students row (minus pin_hash) },
  "parent":         { id, email, display_name },           // contact context only
  "consent_records":[ ...all consent rows for this student ],
  "enrollments":    [ ... ],
  "progress":       [ ...user_progress where student_id ],
  "activity":       [ ...activity_log ],
  "flashcards":     [ ... ], "scheduled_reviews": [ ... ],
  "exam_attempts":  [ ...with exam_item_responses ],
  "gamification":   { student_gamification, xp_ledger, student_badges, student_quests },
  "accommodations": { ... },
  "fsm_session":    { ...fsm_sessions.blob (decoded) },
  "notifications":  [ ...where recipient_id = student_id ],
  "audit_log":      [ ...where subject_student_id = student_id ]   // their own access history
}
```

Built by querying each per-student sub-store with `student_id` (spec 01 §8 sub-store API). **Never includes
other students** (a `WHERE student_id = ?` everywhere; assertion in tests). `pin_hash`/`password_hash` are
**excluded** (export of a hash is a credential leak). The export call itself appends to `audit_log`.

### 2.4 Delete — cascade + audit

`DELETE /api/parent/students/<stu>` performs a **hard cascade delete** within a single transaction. The
schema is already designed for this: every per-student table declares
`REFERENCES students(id) ON DELETE CASCADE` (spec 01 §2, §5–§7), so `PRAGMA foreign_keys=ON`
(set in `_ThreadLocalDB`) makes `DELETE FROM students WHERE id=?` cascade to `enrollments`,
`consent_records`, `user_progress`, `activity_log`, `flashcards`, `scheduled_reviews`, `exam_attempts`
(→ `exam_item_responses`), `student_gamification`, `xp_ledger`, `student_badges`, `student_quests`,
`accommodations`, `fsm_sessions`, and child `notifications`.

Sequence:
1. Authn + tenant check (parent owns `stu`).
2. Write `audit_log(action='delete_data', actor_role='parent', subject_student_id=<stu>, detail=<counts>)`
   **before** the delete, so the audit survives the cascade (audit_log has **no** FK to students and
   `subject_student_id` is a plain TEXT — by design, spec 01 §7 — so it is **not** cascaded away).
3. Delete on-disk per-student artifacts that aren't in SQLite: any user-elective course dirs created
   solely for that student, uploaded source files (`data/uploads/...`), cached audio. (Catalog content is
   global and untouched.)
4. Commit. Optionally emit a parent confirmation notification.

**Soft-delete option:** for accidental-deletion protection, support `students.status='deleted'` +
scheduled hard-purge after a short grace window (e.g. 30 days), configurable. The grace window itself is
a retention decision for counsel (§11). Either way the audit record is permanent.

### 2.5 Retention schedule + breach process

**Retention schedule (defaults; counsel finalizes durations):**

| Data class | Retention default | Trigger to purge |
|---|---|---|
| Active student account + learning data | While account active | Delete request, or N months after account inactivity |
| `consent_records` | **Retain indefinitely** (proof of consent) — survives student soft-delete; purged only on full account erasure + legal hold clear | n/a |
| `audit_log` | Retain ≥ required audit window (e.g. 1–3 yrs); never auto-deleted by student delete | Time-based purge job |
| `activity_log` (fine-grained) | Rolling window (e.g. 12 months) | Time-based |
| `exam_attempts` / responses | Retain while enrolled + grace | Account delete |
| Uploaded source files (`data/uploads`) | Delete after ingestion completes; cleanup on failure (WIZ-5) | Post-ingestion |
| Cached TTS audio | Ephemeral; LRU/age cap | Cron |
| Backups containing student data | Match account retention; encrypted (§8) | Backup rotation |

A scheduled **retention purge job** (off-hours, like the night-audit) enforces time-based windows and is
itself audited.

**Breach process (runbook owned by Data Manager):**
1. **Detect** (anomalous `audit_log` access patterns, failed-integrity alerts, infra alerts).
2. **Contain** (rotate `FLASK_SECRET_KEY` + Stripe keys, revoke sessions, isolate host).
3. **Assess scope** using `audit_log` (which `subject_student_id`s were accessed/exported) + DB diff.
4. **Notify** per Utah Student Data Protection Act + state breach-notification law timelines — affected
   parents and any required state authority. **Timelines and notice content are a counsel sign-off item
   (§11).**
5. **Remediate + post-mortem.** Record the incident; update controls.

---

## 3. Consent management — the matrix, versioning, re-consent, withdrawal (B21.1)

Backed entirely by `consent_records` (spec 01 §2): `consent_type` ∈
`{coppa_data, tos, privacy, health_strand6, marketing}`, `granted` 0/1, `policy_version`, `method`,
`ip_address`, append-only (a change writes a **new row**; we never UPDATE a consent — the latest row by
`created_at` for a (`parent_id`,`student_id`,`consent_type`) tuple is authoritative).

### 3.1 The consent matrix

| `consent_type` | Scope | Required before | Method | Withdrawable? | Effect of withdrawal |
|---|---|---|---|---|---|
| `tos` | account (student_id NULL) | parent uses product at all | `checkbox` (click-accept) | n/a (using = accepting current ToS) | account use blocked until re-accept |
| `privacy` | account | any data collection | `checkbox` | re-consent on policy change | data ops blocked until re-accept |
| `coppa_data` | per child | child account usable (§1) | `card_verify` (or `signed`) | **Yes** | child account becomes inert (§3.4) |
| `health_strand6` | per child | enrolling in / rendering HD content (§4) | `checkbox`/`signed` (parent) | **Yes** | HD content blocked + concepts hidden |
| `marketing` | account | sending marketing email | `checkbox`, **opt-in default OFF** | **Yes** | stop marketing sends; no effect on service |

`marketing` is strictly separate and never bundled with required consents (dark-pattern avoidance;
required for COPPA — you cannot condition the service on marketing consent).

### 3.2 Policy version tracking

Each consentable document has a monotonically increasing version constant the app reads at runtime:
`TOS_POLICY_VERSION`, `PRIVACY_POLICY_VERSION`, `COPPA_POLICY_VERSION`, `HEALTH_STRAND6_NOTICE_VERSION`
(stored in a `policy_versions` config table or constants module, with the rendered document text under
version control in `docs/legal/`). The consent row records the **exact version the parent saw**, giving a
defensible "this parent consented to *this text*" record.

### 3.3 Re-consent on policy change

A helper `consent_current(parent_id, student_id, consent_type) -> bool` returns True iff the latest row is
`granted=1` **and** `policy_version >= current_version` for that type. When we ship a new policy version,
all prior consents go "stale" for that type; on next login the parent is shown a **re-consent prompt** for
each stale required consent before continuing. Re-consent writes a fresh row (new version). The gates in
§1.1 and §4 call `consent_current`, so a stale required consent automatically blocks use until refreshed.

### 3.4 Withdrawal handling

`POST /api/parent/consent` with `{consent_type, granted:false, student_id?}` writes a new
`granted=0` row + `audit_log(action='consent_change')`. Effects:
- **`coppa_data` withdrawn:** `coppa_consent_current` flips False → child account inert (all §1.1 gates
  fail). Parent is informed the account is paused and offered export/delete. (Withdrawal does not auto-delete
  data; the parent must request deletion separately — but consent withdrawal must be honored immediately.)
- **`health_strand6` withdrawn:** HD concepts are immediately re-gated (§4); any in-progress HD enrollment
  is paused; HD concepts disappear from the child's path render.
- **`marketing` withdrawn:** suppress marketing notifications/email; service unaffected.
- **`tos`/`privacy`:** treated as "must re-accept current version to continue."

`POST /api/parent/consent` with `granted:true` is the grant path (used by §1.2 and §4).

---

## 4. Health Strand 6 (Human Development) gating + abstinence framing (B21.4)

Per `UTAH_K12_CURRICULUM_REFERENCE.md` Subject 6, Strand HD: Utah Code requires **parental notification**
for child-sexual-abuse-prevention instruction and **parental consent** for sex education, and instruction
**stresses abstinence before marriage and fidelity after marriage**. We bake both the *gate* and the
*framing* into the system.

### 4.1 Identifying HD content

Catalog concepts that belong to Health Strand 6 are tagged in the standards layer: a `standards` row with
`subject='health'` and `strand='Human Development'` (spec 01 §4), linked via `concept_standards` to the
relevant `concept_uid`s. A concept is **HD-gated** iff any of its linked standards is a Human Development
standard. A derived `concept_is_health_strand6(concept_uid) -> bool` helper (joins
`concept_standards`→`standards`) is the single source of truth. Mark the whole Health "Human Development"
**module/unit** as gated at catalog-build time so enrollment can check at the course level too.

### 4.2 The consent gate — enforced at enrollment AND at concept render

Two enforcement points (defense in depth), both calling
`consent_current(parent_id, student_id, 'health_strand6')`:

1. **Enrollment (`rag/librarian.py`):** a student cannot enroll into a course/module containing HD content
   unless `health_strand6` consent is current. Parent enrolling the child sees the **notification +
   consent** screen first (notification satisfies the notify requirement; the checkbox/signed action
   satisfies the consent requirement); on grant, write `consent_records(consent_type='health_strand6')`
   + `audit_log`. Without consent, HD courses/modules are **not offered** to that student (filtered out of
   the catalog the child sees, like unpublished courses are, spec 01 §4.1).

2. **Concept render in FSM (`core/fsm_logic.py`):** as a backstop, when the FSM is about to deliver an
   HD-gated concept (in `enter_node` / `get_concept_details` path), it re-checks
   `consent_current(...,'health_strand6')`. If not current (e.g. consent withdrawn mid-course, §3.4), the
   FSM **does not render the concept** — it skips/holds and emits a parent-facing `CONSENT_REQUIRED`
   status rather than teaching the material. This guarantees no HD content is delivered the instant consent
   lapses, even if the enrollment-time filter was stale.

### 4.3 Abstinence-stressing framing — in content AND in the tutor system prompt

Two layers:
- **Baked into content (authoring/CMS, B26):** HD concept markdown is authored to stress abstinence before
  marriage and fidelity after, per Utah Code. The content review gate (B26.2) checks HD concepts for this
  framing before `published`.
- **Baked into the live tutor system prompt:** when the FSM teaches an HD-gated concept, it injects an
  **HD framing directive** into the Socratic system message (alongside `SOCRATIC_SYSTEM_RULES` in
  `prompts.py`). A new `HEALTH_STRAND6_FRAMING` constant, appended to the system prompt **only** when
  `concept_is_health_strand6(concept_uid)` is true, e.g.:

  > "This is Utah Health Strand 6 (Human Development) content. Teach age-appropriately and clinically.
  > Per Utah Core Standards, instruction stresses abstinence before marriage and fidelity after marriage
  > as the expected standard. Keep an objective, respectful, factual tone; defer value-laden personal
  > questions to the student's parent/guardian. Do not introduce sexually explicit detail beyond the
  > stated educational standard."

  Wiring: `get_socratic_tutor_prompt` / `get_typed_socratic_prompt` gain a `health_strand6=True` flag
  (passed by the FSM when the concept is gated) that appends `HEALTH_STRAND6_FRAMING` to `system_content`.
  This sits inside the same minor-safe pipeline (§5), so HD output still passes the output check.

### 4.4 Parental notification record

The notification shown to the parent before consent is itself logged: writing the `health_strand6` consent
row (with `method` and `policy_version = HEALTH_STRAND6_NOTICE_VERSION`) plus an `audit_log` entry is the
durable proof that notification + consent occurred. A `notifications` row (`kind='system'`) to the parent
provides the in-app record.

---

## 5. Minor-safe tutoring guardrails — input + output moderation pipeline (B21.5)

Extends the existing B8 filter (`services/core/safety.py`) and the prompt-injection fencing
(`prompts.py` `sanitize_untrusted` + `UNTRUSTED_FENCE`). Today the FSM checks **input** at
`fsm_logic.py:820` (`check_safety_detailed`). B21.5 adds **(a) output checking** and **(b) escalation**.

### 5.1 The moderation pipeline

```
child free-text input
   │
   ├─[1] length + structural sanitize  (sanitize_untrusted, cap 2000)         prompts.py
   ├─[2] INPUT safety check            (check_safety_detailed)                 safety.py  (existing, fsm_logic.py:820)
   │        ├─ self_harm  → crisis-resource surface + parent struggle alert (§5.3)  [do NOT just block silently]
   │        ├─ nsfw/violence/hate → age-appropriate refusal + redirect
   │        ├─ prompt_injection → refusal (already handled)
   │        └─ safe → continue
   │
   ├─[3] TUTORING (LLM call, Ollama, local)  — input fenced; HD framing if gated (§4.3)
   │
   ├─[4] OUTPUT safety check           (NEW: check_output_safety)             safety.py (new)
   │        ├─ flagged → suppress model text, substitute safe fallback, log, (optional) regenerate once
   │        └─ safe → deliver to child
   │
   └─[5] deliver (text/TTS)
```

Steps [1][2] exist; **[4] is new**; [2]'s self_harm branch is upgraded to *surface + escalate* rather than
plain block.

### 5.2 Output moderation (new)

`check_output_safety(model_text, node_title, grade_band) -> SafetyResult` (new in `safety.py`, reusing the
existing category vectorizer + `SAFE_CONTEXTS` overrides). Called in `fsm_logic.py` on **every model
response before `self.speak(...)`** (the central emit point). On a flag:
- Do **not** emit the raw model text.
- Emit a safe, age-appropriate fallback ("Let's keep going with the lesson — here's a question…") and
  **regenerate once** with a tightened system note; if the regeneration also flags, fall back to a
  scripted safe turn and log.
- Write the incident (category, concept, **not** the raw unsafe text beyond a short hash/snippet) to logs;
  for a minor, count toward the struggle/safety signal (§5.3).
- Because inference is local (§1.4), this is *our* model we are filtering, not a third party's — and the
  same `SAFE_CONTEXTS` educational overrides apply so legitimate biology/history isn't false-flagged.

### 5.3 Profanity, self-harm, abuse, crisis-resource surfacing, age-appropriate refusal

- **Profanity:** add a `PROFANITY_KEYWORDS` category to `safety.py` with **grade-band-aware** strictness
  (stricter for K-5). A profanity hit on *input* → gentle age-appropriate redirect, not a hard error; on
  *output* → suppressed/regenerated. (Counsel/product decide tone.)
- **Self-harm / suicide:** highest priority (already first in `check_safety_detailed`). **Surface the
  988 Suicide & Crisis Lifeline** (existing message) **and**, for a minor, **trigger a parent alert**
  (§5.4). Never gamify, quiz, or "redirect to lesson" dismissively over a self-harm signal — the supportive
  message + escalation takes precedence and the lesson is *paused*, not resumed silently.
- **Abuse disclosure** (a child disclosing harm/abuse): treat as a high-severity safety category
  (`abuse_disclosure`) — respond with a supportive, non-investigative message, surface a trusted-adult /
  reporting resource (consistent with Health Strand HD's "harassment/abuse prevention and reporting"
  curriculum), and **escalate to the parent** via alert. **Mandatory-reporting obligations are a counsel
  sign-off item (§11)** — the product surfaces resources and notifies the parent; it does not make legal
  determinations.
- **Age-appropriate refusal:** refusal copy is selected by `grade_band` — a K-2 refusal is warm and
  simple; a 9-12 refusal can be more direct. Refusal never lectures or shames.

### 5.4 Escalation to parent (struggle / crisis → notification)

On a self-harm, abuse, or repeated-distress signal, the FSM creates a high-priority
`notifications` row (`kind='struggle_alert'`, `recipient_role='parent'`, `recipient_id=<parent_id>`,
`ref_uid=<student_id>`) and (when B24 email is live) an out-of-band email to the parent. This reuses the
B24/B25 struggle-alert plumbing but with a **safety severity** that bypasses any digest batching — a crisis
alert is immediate. The alert body is supportive and **does not transcribe** the child's raw text; it tells
the parent a safety concern was detected and to check in. Every escalation writes `audit_log`.

### 5.5 Where it plugs into existing code

| Step | Hook |
|---|---|
| Input sanitize/fence | `prompts.py` `sanitize_untrusted` / `UNTRUSTED_FENCE` (unchanged) |
| Input safety | `fsm_logic.py` `transition()` TEXT_INPUT branch, `check_safety_detailed` (exists) |
| HD framing inject | `prompts.py` `get_socratic_tutor_prompt(... health_strand6=...)` (§4.3) |
| Output safety | new `safety.check_output_safety`, called in `fsm_logic.py` central `speak()`/emit |
| Crisis/abuse escalation | `fsm_logic.py` → `NotificationStore` + B24 email; `audit_log` |
| New categories | `safety.py` add `PROFANITY_KEYWORDS`, `abuse_disclosure` |

---

## 6. ToS / Privacy Policy + Utah Fits All eligibility posture (B21.6)

### 6.1 Documents to publish (versioned in `docs/legal/`, counsel-approved)

| Document | Consent tie | Audience |
|---|---|---|
| **Terms of Service** | `consent_type='tos'` | parent |
| **Privacy Policy** | `consent_type='privacy'` | parent (includes the §1.4 self-hosted-inference statement, data-handling matrix §7, retention §2.5, rights §2.2) |
| **COPPA Children's Privacy notice** | `consent_type='coppa_data'` | parent (what's collected for kids, §1.3; verifiable consent method §1.2; rights) |
| **Health Strand 6 parental notice** | `consent_type='health_strand6'` | parent (Utah Code notify/consent §4) |
| **Acceptable Use / minor-safety summary** | n/a (informational) | parent + (kid-friendly version) |

Each document carries its version constant (§3.2). The footer of the app links all of them.

### 6.2 Utah Fits All eligibility posture

Utah Fits All is the state scholarship program; to be a reimbursable provider the product must be able to
present the data points the scholarship/expense-approval process expects and retain the records to back
reimbursements. Designed support:
- **Provider-eligibility data points to expose** (provider profile / B26 admin): subject coverage mapped to
  **Utah Core Standards** (we already tag `concept_standards`→`standards`, spec 01 §4), grade bands served,
  that the program is a defined educational service, and the privacy/safety posture above.
- **Records the product must retain for reimbursement** (B20.4 + here): itemized receipts/invoices per
  family (Stripe), proof of service / enrollment + usage (`enrollments`, `activity_log`, `exam_attempts`
  give an auditable learning record), and the consent/eligibility records. Make these **exportable as a
  parent expense bundle** (extends the §2.3 export with billing artifacts) so a parent can submit for
  reimbursement.
- **Standards-alignment report:** a per-student or per-course report listing covered Utah standard codes —
  useful both for Utah Fits All justification and for parents. Generated from `concept_standards`.

> **Counsel/program sign-off item (§11):** exact Utah Fits All provider-eligibility requirements and the
> precise documentation the program demands evolve; verify against the current program rules before
> publishing eligibility claims.

---

## 7. Data-handling matrix

Every data category × where stored × who can access × retention × encryption posture. (Storage = SQLite
`helga.db` WAL + on-disk files, spec 01; access scoped by `students.parent_id` tenant isolation, spec 01 §8.)

| Category | Examples | Where stored | Who can access | Retention | Encryption |
|---|---|---|---|---|---|
| **Parent PII** | email, display_name | `parents` | the parent; Data Manager (audited) | account life + legal | at-rest: full-disk/volume encryption; in-transit: TLS (Caddy, B23.6); `password_hash` argon2id (§8) |
| **Child PII (minimized)** | display_name, grade_band, interests, avatar | `students` | parent; child (own); Data Manager (audited) | account life; delete cascade (§2.4) | at-rest disk encryption; `pin_hash` argon2id |
| **Consent records** | type, granted, version, method, ip | `consent_records` | parent (own); Data Manager | indefinite (proof) | at-rest |
| **Learning data** | progress, mastery, bloom, streaks, fsm session | `user_progress`, `fsm_sessions`, `activity_log` | parent; child (own); Data Manager (audited) | rolling/account life (§2.5) | at-rest; **never sent to 3rd-party LLM** (§1.4) |
| **Assessment data** | attempts, item responses, scores | `exam_attempts`, `exam_item_responses` | parent; child (own) | account life + grace | at-rest |
| **Gamification** | XP, badges, quests | `student_gamification`, `xp_ledger`, ... | parent; child (own); within-family only | account life | at-rest |
| **Health Strand 6 interactions** | HD concept progress/transcript fragments | `user_progress`, `fsm_sessions` (gated) | parent; child (own); **gated by consent §4** | account life | at-rest; local inference only |
| **Accommodations (IEP/504)** | flags, notes | `accommodations` | parent (set_by); child effect only | account life | at-rest (sensitive — treat like PII) |
| **Payment** | card details | **Stripe (never on our box)**; we store only `provider_customer_id`/`provider_sub_id` | parent; Stripe; Data Manager (IDs only) | per Stripe + receipts retention | PCI handled by Stripe; we keep **no PAN/CVV** |
| **Audit log** | who accessed what | `audit_log` | Data Manager (review); subject parent (own student's, via export) | ≥ audit window; not cascaded by delete | at-rest |
| **Notifications** | alerts, digests | `notifications` | recipient | rolling | at-rest |
| **Uploaded sources** | EPUB/docs for course build | `data/uploads/` (transient) | the uploading parent; ingestion | delete post-ingestion / on failure | at-rest; deleted promptly |
| **Logs/telemetry** | app logs | host log files | Data Manager / ops | rolling; **no raw unsafe child text / no PII in logs** | at-rest; redaction discipline |
| **Secrets** | FLASK_SECRET_KEY, Stripe keys | secret store / env, not VCS | ops only | rotate on breach | encrypted secret store (§8) |

---

## 8. Security baseline (B21 cross-cutting)

- **Secrets management:** `FLASK_SECRET_KEY` is **persisted** (not the current ephemeral
  `secrets.token_hex(32)` fallback at `app.py:84` — that resets sessions on restart). Source it from a real
  secret store / `.env` outside VCS; the app **warns** today when unset (`app.py:85-88`) — escalate to a
  **hard refusal to boot in production** when unset, and to **rotate on breach** (§2.5). Stripe secret/
  webhook-signing keys live in the same secret store, never in code or client. `.env` and any key files are
  in `.gitignore` and scanned in CI (secret-scanning).
- **Encryption in transit:** all client↔server traffic over **TLS** (Caddy/TLS topology, B23.6). LLM/STT/
  TTS traffic is loopback/private (§1.4).
- **Encryption at rest:** full-disk/volume encryption on the host carrying `helga.db` + `data/`; backups
  encrypted (§2.5). (Field-level encryption for the most sensitive columns — `accommodations.notes`,
  contact PII — is a possible enhancement; flagged for counsel/threat-model review, §11.)
- **Password / PIN hashing:** **argon2id** for `parents.password_hash` and `students.pin_hash`
  (spec 01 §2 already specifies argon2id) — never plaintext, never fast hashes. PINs are short (4-digit) so
  pair argon2id with **rate limiting + lockout** on PIN attempts to resist brute force.
- **Rate limiting:** on auth endpoints (login, PIN), consent endpoints, export/delete, and the event
  intake path. Per-parent and per-IP. Protects against credential stuffing and abuse of the heavy
  export/delete operations.
- **CSRF:** keep `csrf_protect` (`app.py:102`) on all state-changing POST/PATCH/DELETE — explicitly
  including the new consent/export/delete endpoints (§9). Sessions are httpOnly + Secure + SameSite cookies.
- **Tenant isolation:** every per-student query carries `WHERE student_id = ?` and verifies
  `students.parent_id == session parent` (spec 01 §8). This is the control that prevents cross-family data
  access; it is tested (§10).
- **Audit_log coverage:** writes on `login`, `view_progress`, `export_data`, `delete_data`,
  `correct_data`, `consent_change`, and every safety escalation. The `audit_log` table (spec 01 §7) is the
  forensic backbone for the breach process (§2.5).
- **Fail-safe vs fail-open:** the safety filter fails **open** for educational use (existing behavior) so a
  classifier error doesn't block a legitimate kindergartner — acceptable because output moderation (§5.2)
  is a second layer and inference is our own local model. The **consent gates fail closed** (no consent ⇒
  no use). These two postures are intentional and documented.

---

## 9. Endpoints (consolidated)

All parent-scoped, argon2id-authenticated, `csrf_protect`-decorated, tenant-checked, and audited.

| Method + path | Purpose | Audit action |
|---|---|---|
| `POST /api/parent/consent` | grant/withdraw a consent (`{consent_type, granted, student_id?, policy_version}`) | `consent_change` |
| `GET /api/parent/consent/status` | current consent state per type (drives re-consent prompts, §3.3) | (read) |
| `POST /api/coppa/verify` (or Stripe webhook handler) | record verifiable `coppa_data` consent on card-verify (§1.2) | `consent_change` |
| `GET /api/parent/students/<stu>/record` | view student record (FERPA access) | `view_progress` |
| `POST /api/parent/students/<stu>/export` | full data bundle (§2.3) | `export_data` |
| `PATCH /api/parent/students/<stu>` | correct fields (FERPA correction) | `correct_data` |
| `DELETE /api/parent/students/<stu>` | cascade delete (§2.4) | `delete_data` |
| `POST /api/parent/students/<stu>/health_consent` | HD notify+consent grant (§4) | `consent_change` |
| `GET /api/parent/expense_bundle` | Utah Fits All reimbursement records (§6.2) | `export_data` |

**Moderation hook points** (not REST endpoints — in-process functions):
| Hook | Location |
|---|---|
| `coppa_consent_current(student_id)` gate | top of FSM `transition()`; web-ui login; librarian enrollment |
| `consent_current(...,'health_strand6')` gate | librarian enrollment + FSM concept render (§4.2) |
| input `check_safety_detailed` | FSM `transition()` TEXT_INPUT (`fsm_logic.py:820`, exists) |
| output `check_output_safety` (new) | FSM central emit / `speak()` (§5.2) |
| crisis/abuse escalation → `notifications` + email | FSM safety branch (§5.4) |
| HD framing inject | `prompts.py` prompt builders (§4.3) |

---

## 10. Test plan / acceptance criteria

| # | Test | Acceptance |
|---|---|---|
| T1 | **No child use before consent** | Create child, no `coppa_data` consent → login/launch/enroll/`TEXT_INPUT` all blocked with `consent_required`; after card-verify consent row written, child usable. Gate enforced at all four layers (§1.1). |
| T2 | **COPPA verifiable method records correctly** | Card-verify writes `consent_records(coppa_data, method='card_verify', policy_version)` + `audit_log`; **no PAN/CVV stored** anywhere. |
| T3 | **Data minimization** | Child creation rejects/strips disallowed fields (geolocation, contact); CI asserts no ad/analytics SDK calls; `interests` capped at 20 and sanitized. |
| T4 | **Inference boundary** | `tests/test_inference_boundary.py`: LLM base URL resolves to loopback/private host; no hosted-LLM SDK imported; startup refuses public LLM endpoint. |
| T5 | **Export returns ALL student data** | Export bundle contains every per-student table's rows for that `student_id`, **and nothing from any other student**; excludes `pin_hash`/`password_hash`; export writes `audit_log(export_data)`. |
| T6 | **Delete cascades + audited** | `DELETE student` removes rows in all per-student tables (FK cascade), leaves catalog untouched, deletes on-disk artifacts, and the `audit_log(delete_data)` row **survives** the cascade. |
| T7 | **Health Strand 6 blocked without consent** | Student without `health_strand6` consent: HD courses/modules not offered (enrollment); if reached, FSM refuses to render HD concept; granting consent unblocks; **withdrawing** consent immediately re-gates (concept render check). |
| T8 | **HD framing present** | When teaching an HD-gated concept, the tutor system prompt includes `HEALTH_STRAND6_FRAMING` (abstinence/fidelity framing); non-HD concepts do not. |
| T9 | **Safety filter catches a red-team set** | A red-team corpus (self-harm phrasings, profanity, NSFW, hate, prompt-injection, abuse disclosure) — input check blocks/redirects/surfaces correctly; **educational overrides** (biology/history/health context) are NOT false-flagged; output check suppresses an unsafe model response and regenerates/falls back. |
| T10 | **Crisis escalation** | A self-harm input surfaces 988 resource, pauses the lesson, creates an immediate `notifications(struggle_alert)` to the parent (not batched), and writes `audit_log`; no raw child text in the alert. |
| T11 | **Re-consent on policy bump** | Incrementing `PRIVACY_POLICY_VERSION` makes prior `privacy` consent stale; next login prompts re-consent and blocks use until granted; new version row written. |
| T12 | **Consent withdrawal effects** | `coppa_data` withdrawal → account inert; `health_strand6` withdrawal → HD re-gated; `marketing` withdrawal → service unaffected; all write new `granted=0` rows + audit. |
| T13 | **Tenant isolation** | Parent A cannot access/export/delete Parent B's student (403); every per-student query scoped by `parent_id`. |
| T14 | **Audit coverage** | Each of login/view/export/delete/correct/consent_change/safety-escalation produces exactly one `audit_log` row with correct `actor_role`, `action`, `subject_student_id`. |
| T15 | **Secrets/security baseline** | App refuses to boot in prod without persisted `FLASK_SECRET_KEY`; argon2id verified on parent password + student PIN; rate-limit/lockout triggers on repeated bad PINs; CSRF rejected when token missing on consent/export/delete. |

---

## 11. Open questions for counsel (sign-off required before launch)

1. **Verifiable consent method:** does Stripe card-verification satisfy the current FTC COPPA
   verifiable-parental-consent standard for our use, and is a non-card fallback (signed form / KBA) required?
   (Design supports `method='signed'`.)
2. **Age boundary:** apply the COPPA gate to *all* child accounts (current design) vs only declared
   under-13; and how to handle the 13th-birthday transition.
3. **Controller/processor split:** in the self-hosted single-family deployment is the *parent* the
   controller and the company a processor, vs the hosted SaaS path? This changes who owes breach notice.
4. **Breach notification timelines + content** under the Utah Student Data Protection Act and applicable
   state breach-notification statutes — exact deadlines, recipients (parents, state board), and notice text.
5. **Retention durations:** finalize the §2.5 table numbers (inactivity purge window, soft-delete grace,
   audit_log retention, activity_log window) and any legal-hold rules.
6. **Mandatory reporting:** the product surfaces resources and alerts the parent on abuse disclosure — what,
   if any, reporting obligation attaches to the company/Data Manager, and how does that interact with the
   self-hosted model?
7. **Health Strand 6:** confirm the exact Utah Code citation, the required notice text/timing, whether
   consent must be written ("signed") vs click-accept, and the precise abstinence-framing wording to bake
   into content + prompt.
8. **Utah Fits All:** current provider-eligibility requirements, the exact reimbursement documentation set,
   and any restrictions on claiming standards alignment.
9. **Profanity/refusal tone** and the kid-facing safety copy — product + counsel review for age
   appropriateness and to avoid implying clinical/diagnostic claims.
10. **Field-level encryption:** whether `accommodations.notes` (potential IEP/504 / disability data) and
    contact PII warrant column-level encryption beyond full-disk encryption.

---

## 12. Build order (maps to B21.1–B21.6)

| Step | Branch | Depends on | Deliverable |
|---|---|---|---|
| 1 | B21.3 | spec 01 | Inference-boundary guard + test (cheapest, locks the architecture advantage) |
| 2 | B21.1 | consent_records, B20 | `consent_current`/`coppa_consent_current` helpers + COPPA gate at all 4 layers + card-verify wiring |
| 3 | B21.5 | safety.py, B24 | `check_output_safety`, profanity/abuse categories, crisis escalation to `notifications` |
| 4 | B21.4 | standards layer, B26 | HD tagging, enrollment + render gate, `HEALTH_STRAND6_FRAMING` prompt inject |
| 5 | B21.2 | sub-store API | export/delete/correct endpoints + cascade + retention purge job, all audited |
| 6 | B21.6 | B20.4 | publish ToS/Privacy/COPPA/HD docs (counsel-approved), Utah Fits All expense bundle |

> **Reminder:** ship nothing customer-facing in §6 (and no consent method in §1.2) without the §11
> counsel sign-off. The technical controls above can and should be built and tested ahead of that sign-off.
