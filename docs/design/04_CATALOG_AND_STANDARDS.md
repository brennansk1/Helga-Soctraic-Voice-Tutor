# Design Spec 04 — Catalog & Standards (Utah K-12)

> Implementation-ready design for the **read-only, globally-shared, standards-aligned course
> catalog** and the **authoring/review CMS** that produces it. Covers build tree branches
> **B16.1–B16.7** (catalog + standards) and **B26.1–B26.5** (authoring/review/versioning/audit/
> provenance). Canonical table/column names come from `docs/design/01_DATA_MODEL.md` §4, §4.1;
> band parameters from `docs/design/02_GRADE_ADAPTATION.md` §7; curriculum content from
> `docs/UTAH_K12_CURRICULUM_REFERENCE.md` (the standards source-of-truth).
>
> **Design principle:** the catalog is *authored once, offline, by an admin, reviewed by a human,
> versioned, and frozen*. Students consume an immutable published snapshot. This is the opposite of
> the student-facing free-text course creation path (`SkeletonBuilder` driven from a topic string).
> We **reuse** the existing `SkeletonBuilder → SyllabusAuditor → ContentHydrator` pipeline
> (`services/core/course_builder.py`) but drive it from a **standards brief** instead of a topic,
> and write its output into a **separate store** (`data/catalog/courses/`).

---

## 0. Conventions & scope

- New UID prefix: catalog standards have no UID (the Utah `code` *is* the PK). Catalog courses reuse
  the existing `course_`/`mod_`/`unit_`/`less_`/`con_` 8-hex scheme; nothing new.
- Catalog tables carry **no `student_id`** (spec 01 §0): `standards`, `concept_standards`, and the
  catalog rows of `courses`. They are global and read-only to students.
- Catalog course **files** live under `data/catalog/courses/{uid}/` (mirrors the existing
  `data/courses/{uid}/` layout: `structure.json` + `content/{concept_uid}.md`). User electives stay
  in `data/courses/`. This physical split is the simplest enforcement of "students cannot mutate the
  catalog" — the catalog directory is mounted read-only in the student request path.
- All SQL goes through `StorageManager` sub-stores; any interpolated column must be in the relevant
  `_VALID_COLUMNS` whitelist (existing pattern, `storage.py`).

### Subject enum (canonical — used everywhere)
From spec 01 §4 `standards.subject`. The string literal is the contract:

```
math | ela | science | social_studies | world_lang | health | cs | financial_lit | library_media
```

Map from the reference doc's 9 subjects: SUBJECT 1 → `math`, 2 → `science`, 3 → `ela`,
4 → `social_studies`, 5 → `world_lang`, 6 → `health`, 7 → `cs`, 8 → `financial_lit`,
9 → `library_media`. PE / Fine-Arts / Driver-Ed / Keyboarding / hands-on CTE are **out of scope**
(reference doc "Subjects EXCLUDED"); their teachable theory fragments, if ever authored, attach to an
existing subject (e.g. music theory → no subject yet; defer).

---

## 1. Standards ingestion (B16.1)

### 1.1 Target tables (already defined in spec 01 §4 — repeated for context)

```sql
CREATE TABLE IF NOT EXISTS standards (
    code         TEXT PRIMARY KEY,     -- '6.RP', 'BIO.1', 'SII.A.REI', 'GFL.3', 'K.CC'
    subject      TEXT NOT NULL,        -- subject enum above
    grade_band   TEXT,                 -- 'K-2'|'3-5'|'6-8'|'9-12' OR an HS course code ('GFL','USG','BIO')
    grade_numeric INTEGER,             -- 0(K)..12, NULL for HS course-based
    strand       TEXT NOT NULL,        -- 'Ratios & Proportional Relationships'
    text         TEXT NOT NULL,        -- the standard statement (verbatim where possible)
    is_enrichment INTEGER DEFAULT 0,   -- 1 = ★ supplementary (reference doc ★ marks)
    source       TEXT DEFAULT 'USBE',
    adopted_year INTEGER
);
CREATE INDEX IF NOT EXISTS idx_standards_subject ON standards(subject, grade_band);
```

### 1.2 Seed format — `data/standards/{subject}.yaml`

One YAML file per subject (human-editable, diff-friendly, re-scrapable per USBE adoption cycle).
YAML over JSON specifically so the `text` field can be multi-line and so a curriculum author can hand-
edit. The loader also accepts `.json` with the identical shape for programmatic generation.

`data/standards/math.yaml`:

```yaml
subject: math
source: USBE
adopted_year: 2016          # default; per-row override allowed (math is mid-revision)
standards:
  - code: K.CC
    grade_band: K-2
    grade_numeric: 0
    strand: Counting & Cardinality
    text: >
      Count to 100 by ones and tens; count forward from a given number; write numerals 0-20;
      count to tell the number of objects (one-to-one correspondence, cardinality); compare quantities.
  - code: 6.RP
    grade_band: 6-8
    grade_numeric: 6
    strand: Ratios & Proportional Relationships
    text: >
      Understand ratio concepts and use ratio reasoning: ratio language, unit rates, and percent as
      rate per 100.
  - code: 6.NS
    grade_band: 6-8
    grade_numeric: 6
    strand: The Number System
    text: >
      Divide fractions by fractions; compute fluently with multi-digit numbers and decimals; GCF/LCM;
      rational and negative numbers; four-quadrant plane; absolute value.
  - code: SII.A.REI
    grade_band: SII              # HS integrated course code, not a band
    grade_numeric: null
    strand: Algebra — Reasoning with Equations & Inequalities
    text: Solve quadratic and polynomial equations; solve systems involving quadratics.
  - code: 6.RP.PROB           # ★ enrichment example
    grade_band: 6-8
    grade_numeric: 6
    strand: Ratios & Proportional Relationships
    text: Introductory probability as an extension of rate/proportion reasoning.
    is_enrichment: 1
```

`data/standards/ela.yaml` (strand-based, grade-banded):

```yaml
subject: ela
source: USBE
adopted_year: 2023
standards:
  - code: 6.R
    grade_band: 6-8
    grade_numeric: 6
    strand: Reading
    text: >
      Key Ideas & Details (theme, summary, cite evidence, inference); Craft & Structure (figurative/
      connotative meaning, text structure, point of view); Integration of Knowledge & Ideas.
  - code: 6.W
    grade_band: 6-8
    grade_numeric: 6
    strand: Writing
    text: Opinion/argumentative, informative/explanatory, and narrative writing; plan-draft-revise-edit.
  - code: 6.SL
    grade_band: 6-8
    grade_numeric: 6
    strand: Speaking & Listening
    text: >
      Participate in collaborative conversations; present with logical sequencing and clear
      pronunciation; integrate and evaluate media. (Pairs with Helga TTS/STT.)
```

`data/standards/world_lang.yaml` (proficiency-level, not grade — "GFL" here means the World-Languages
proficiency scaffold; do not confuse with Financial Literacy `GFL`):

```yaml
subject: world_lang
source: USBE
adopted_year: 2020
standards:
  - code: WL.NL.IC          # Novice-Low, Interpersonal Communication
    grade_band: null         # proficiency-keyed, band derived from student at delivery time
    grade_numeric: null
    strand: Interpersonal Communication
    text: >
      "I can" recognize and exchange memorized words/phrases (name, nationality, family, address) in
      short social interactions.
  - code: WL.IM.PR          # Intermediate-Mid, Presentational
    grade_band: null
    grade_numeric: null
    strand: Presentational Communication
    text: '"I can" create with language to narrate and describe in major time frames.'
```

`data/standards/science.yaml` (HS course-based, e.g. Biology):

```yaml
subject: science
source: USBE
adopted_year: 2019           # SEEd
standards:
  - code: BIO.1
    grade_band: BIO           # HS course code
    grade_numeric: null
    strand: Interactions with Organisms and the Environment
    text: >
      Analyze interactions of organisms and populations with the living and non-living factors of an
      ecosystem; energy/matter flow; carrying capacity; ecosystem stability and change.
  - code: BIO.3
    grade_band: BIO
    grade_numeric: null
    strand: Genetic Patterns
    text: Model inheritance of traits; analyze variation; relate DNA/genes/proteins to traits.
```

`data/standards/financial_lit.yaml` (graduation-required HS course):

```yaml
subject: financial_lit
source: USBE
adopted_year: 2008
standards:
  - code: GFL.1
    grade_band: GFL
    grade_numeric: 11
    strand: Economic concepts and economic thinking
    text: GDP, inflation, supply/demand, scarcity, factors of production, economic systems.
  - code: GFL.4
    grade_band: GFL
    grade_numeric: 11
    strand: Credit and debt
    text: >
      Purpose of credit; creditworthiness (the 5 Cs); types of credit; simple interest; credit
      reports/scores; predatory lending and scams.
```

### 1.3 Loader — `services/common/standards_loader.py` + admin CLI

```python
# services/common/standards_loader.py
def load_standards_dir(storage: StorageManager, path: str = "data/standards",
                       prune: bool = False) -> dict:
    """Idempotent upsert of every data/standards/*.yaml|*.json into the `standards` table.

    - Validates: subject ∈ SUBJECT_ENUM; code unique within file; strand/text non-empty;
      grade_band ∈ band-or-course-code set; is_enrichment ∈ {0,1}.
    - Upserts via INSERT OR REPLACE on `code` (re-scrape just re-runs this).
    - `prune=True` deletes DB rows whose code is absent from every seed file (USBE retirement),
      but REFUSES to prune a code that has concept_standards rows pointing at it (would orphan
      published content) — logs those as 'retire_blocked' and exits non-zero.
    - Returns {inserted, updated, pruned, blocked, errors[]}.
    """
```

New `StandardsStore` sub-store (spec 01 §8) owns the SQL. Whitelist columns:
`{code, subject, grade_band, grade_numeric, strand, text, is_enrichment, source, adopted_year}`.

CLI: `python -m services.core.catalog_admin standards load [--prune]` (admin job, never in request path).
Run it on container start (best-effort) and manually after each USBE re-scrape.

**Acceptance:** every code in the reference doc that we choose to seed is loadable;
`SELECT count(*) FROM standards WHERE subject='math'` matches the seed file row count;
`is_enrichment=1` rows correspond 1:1 to the doc's ★ marks.

---

## 2. Catalog course JSON schema (B16.2)

### 2.1 `courses` table catalog columns (spec 01 §4.1 — repeated)

`ALTER TABLE courses ADD COLUMN`: `subject TEXT`, `grade_band TEXT`, `grade_numeric INTEGER`,
`is_catalog INTEGER DEFAULT 0`, `catalog_status TEXT DEFAULT 'draft'`
(`draft|reviewed|published|retired`), `version INTEGER DEFAULT 1`,
`visibility TEXT DEFAULT 'private'` (`private|catalog`), `reviewed_by TEXT`, `published_at TEXT`,
`enrichment_included INTEGER DEFAULT 0`. Add all of these to `CourseStore`'s SQLite sync (§2.3).

### 2.2 `structure.json` extension

The existing `structure.json` already carries `uid, title, teaching_style, status, created_at, scope,
mastery, starting_from, bloom_floor, bloom_ceiling, modules[]` (see `SkeletonBuilder._build_inner`).
Catalog courses add a `catalog` block at the top level and `standards` tags on concepts. Concrete
example — Utah **Grade 6 Mathematics: Ratios & Proportional Relationships** (subject `math`, band
`6-8`):

```jsonc
{
  "uid": "course_6a1b2c3d",
  "title": "Grade 6 Mathematics: Ratios, Rates & Percent",
  "teaching_style": "",
  "status": "ready",                 // existing hydration status (skeleton|available|ready|failed)
  "created_at": "2026-07-02T10:00:00",
  "scope": 3, "mastery": 3, "starting_from": 2,
  "bloom_floor": 2, "bloom_ceiling": 5,    // from GRADE_BAND_PROFILES['6-8'] (spec 02 §3)

  "catalog": {                        // NEW — present iff is_catalog
    "is_catalog": true,
    "subject": "math",
    "grade_band": "6-8",
    "grade_numeric": 6,
    "standard_codes": ["6.RP", "6.NS"],          // union of all concept_standards in this course
    "enrichment_included": false,                 // baseline-only build (B16.7)
    "catalog_status": "published",                // draft|reviewed|published|retired
    "version": 2,
    "visibility": "catalog",                      // private|catalog
    "reviewed_by": "par_admin01",
    "published_at": "2026-07-05T14:00:00",
    "changelog_ref": "data/catalog/courses/course_6a1b2c3d/CHANGELOG.md"
  },

  "modules": [
    {
      "uid": "mod_11111111", "title": "Ratio Language & Reasoning", "ordinal": 1,
      "bloom_target": 2, "bloom_label": "Understand",
      "scope": ["ratio notation", "equivalent ratios", "ratio tables"],
      "standard_codes": ["6.RP"],                 // module-level rollup (denormalized convenience)
      "units": [
        {
          "uid": "unit_22222222", "title": "What a Ratio Is",
          "lessons": [
            {
              "uid": "less_33333333", "title": "Describing Ratios",
              "concepts": [
                {
                  "uid": "con_44444444",
                  "title": "Ratio Notation and Language",
                  "bloom_level": 2,
                  "depth_level": 3,
                  "learning_objectives": ["Write a ratio three ways", "Read ratio language in a word problem"],
                  "concept_standards": [                 // NEW — drives concept_standards table rows
                    {"standard_code": "6.RP", "coverage": "partial"}
                  ]
                },
                {
                  "uid": "con_55555555",
                  "title": "Unit Rate and Price Comparison",
                  "bloom_level": 3,
                  "learning_objectives": ["Compute a unit rate", "Use unit rate to compare two buys"],
                  "concept_standards": [
                    {"standard_code": "6.RP", "coverage": "full"}
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

Rules:
- `concept_standards[]` on each concept is the source of truth; `module.standard_codes` and
  `catalog.standard_codes` are denormalized rollups recomputed at write time.
- `coverage ∈ {full, partial, enrichment}` mirrors `concept_standards.coverage` (spec 01 §4).
- `coverage:"enrichment"` is only allowed when the referenced standard has `is_enrichment=1` OR when
  `catalog.enrichment_included=true`.

### 2.3 SQLite sync — extend `CourseStore.create_course`/`update_course`

`create_course`/`update_course` currently sync only `(uid,title,overview,status,teaching_style)`
(`storage.py:241,297`). Extend the INSERT/UPDATE to also write the catalog columns from
`course_dict['catalog']` (NULL when absent). After writing `structure.json`, the catalog writer also
**syncs `concept_standards` rows** for every concept's `concept_standards[]` (delete-then-insert per
course, scoped by the course's concept UIDs). This is the only place `concept_standards` is written;
it runs inside the catalog admin job, never the student path.

---

## 3. Offline authoring pipeline (B16.3 / B26.1)

### 3.1 The standards-driven brief

A course is authored from a **brief file** (YAML), not a topic string. The brief names the standards
and the structural intent; the build job translates it into a topic-equivalent prompt and the existing
three sliders.

`data/catalog/briefs/math_g6_rp.yaml`:

```yaml
brief_id: math_g6_rp
title: "Grade 6 Mathematics: Ratios, Rates & Percent"
subject: math
grade_band: 6-8
grade_numeric: 6
standard_codes: [6.RP, 6.NS]      # standards this course must cover
enrichment: false                  # baseline only (B16.7); true pulls in ★ codes too
# Slider mapping (compute_course_params); band sets bloom bounds, sliders set breadth/depth.
scope: 3
mastery: 3
starting_from: 2
teaching_style: ""
# Optional human structural hints (else auditor/skeleton infer):
module_hints:
  - "Ratio language and equivalent ratios"
  - "Unit rates and price comparison"
  - "Percent as rate per 100"
notes: "Keep examples concrete and Utah-relevant (snacks, sports stats)."
```

### 3.2 Job runner — `CatalogBuildJob`

New module `services/core/catalog_build.py`, invoked by the CLI (§3.4). It is a thin orchestrator
over the **existing** pipeline classes, with three changes from the student path:

1. **Topic synthesis from standards.** Instead of a free-text topic, build the skeleton prompt from
   the brief: `topic = brief.title`; then inject a **standards block** assembled by joining
   `standards.text` for each `standard_code` into the system prompt so the LLM names real Utah strand
   sub-areas. This reuses `SkeletonBuilder(scope=, mastery=, starting_from=, teaching_style=)` exactly;
   we add an optional `standards_brief: dict` kwarg that, when present, (a) seeds the module-generation
   prompt with the standard texts and `module_hints`, and (b) is carried through to hydration.

2. **Band-aware bloom bounds.** Override `compute_course_params` bloom bounds with the band's
   `bloom_floor/bloom_ceiling` from `GRADE_BAND_PROFILES[grade_band]` (spec 02 §3, §5). The course
   dict's `bloom_floor/bloom_ceiling` are set from the band, and `progressive_bloom()` ramps within
   those bounds unchanged.

3. **Write to the catalog store.** The job constructs a `StorageManager` rooted at
   `data/catalog/` (so `courses_dir = data/catalog/courses`) and a catalog-scoped SQLite that syncs the
   catalog columns. The student-facing `StorageManager` stays rooted at `data/`.

Pipeline sequence (all existing code):

```
brief.yaml
  → SkeletonBuilder(storage=catalog_storage, scope, mastery, starting_from,
                    standards_brief=brief).build(topic=brief.title)        # B16.3 / reuse
  → SyllabusAuditor(...).audit(course_uid)                                 # reuse
  → ContentHydrator(storage=catalog_storage, mastery, standards_brief=brief).hydrate(course_uid)
  → tag_concept_standards(course_uid, brief)   # NEW: write concept_standards[] + DB rows
  → set catalog block: is_catalog=true, subject, grade_band, version=1, catalog_status='draft'
  → emit provenance log (§7)
```

### 3.3 Band-aware hydration injection (spec 02 §7)

`ContentHydrator` already calls `_condense_and_structure_content(... mastery_level, bloom_level ...)`.
Add a `grade_band` parameter threaded from the brief into that call; inside, compose the band's
`register` / `answer_expectation` / `max_words` from `GRADE_BAND_PROFILES[grade_band]` into the
hydration prompt (same constant spec 02 §3). Effect: a `6-8` ratios concept and a `9-12` Biology
concept carry materially different vocabulary/concreteness in their `content/{concept_uid}.md`,
satisfying spec 02 §7. The Socratic-Hook section is generated at the band register too.

### 3.4 Concept→standards tagging (`tag_concept_standards`)

After hydration, attach standards to concepts. Two strategies, in order:

1. **Hint-driven (preferred, deterministic):** the brief may carry a `concept_standard_map` keyed by
   concept title regex → `{standard_code, coverage}`. Authors fill this during review.
2. **LLM-assisted first pass:** for unmapped concepts, prompt the LLM with the concept title +
   objectives + the brief's candidate `standard_codes` and ask which code(s) it covers and at what
   coverage. This is a *draft* tag flagged `"tag_source": "llm"` in `structure.json`; the human
   reviewer (§4) must confirm before publish. Never publish an LLM-only tag.

Each resulting `{standard_code, coverage}` is written to the concept's `concept_standards[]` in
`structure.json` and to the `concept_standards` table (§2.3). A concept with **zero** standard tags is
allowed only if `coverage` would be `enrichment`; otherwise it's a coverage gap the reviewer must fix
(it surfaces in the audit, §6).

### 3.5 Mastery / Bloom bounds from band

`course.bloom_floor/ceiling` = band bounds (spec 02 §5). The FSM's `_check_mastery_gate` then reads
`gate_streak/gate_questions/gate_types` from the band at delivery time (spec 02 §5) — the catalog
course doesn't store gate params; they resolve from the **student's** band at runtime, with the
course band as fallback. Per-module `bloom_target` is already persisted by `SkeletonBuilder`.

### 3.6 CLI

```
python -m services.core.catalog_admin build  data/catalog/briefs/math_g6_rp.yaml
python -m services.core.catalog_admin build  data/catalog/briefs/        # whole dir, sequential
```

Runs **sequentially** (Mac Mini 24GB constraint; `_build_lock` already serializes). Never invoked
from a Socket.IO / HTTP student request. Output: a `draft` catalog course under
`data/catalog/courses/{uid}/` ready for review.

---

## 4. Review workflow / CMS (B26.2)

### 4.1 State machine on `courses.catalog_status`

```
        author build job
draft ───────────────────────────┐
  │  (admin edits content/tags)   │
  │                               ▼
  │   approve()           reviewed ──── publish() ──► published
  │   (reviewer signs off)    ▲                          │
  └───────────────────────────┘                          │ retire()  (superseded / pulled)
            request_changes() (published→? : no; edits go via new version §5)
                                                         ▼
                                                      retired
```

Transitions (admin-only, enforced server-side):

| from | action | to | guard |
|---|---|---|---|
| draft | `submit_for_review` | reviewed | all concepts hydrated (no stubs); ≥1 concept_standard per non-enrichment concept |
| reviewed | `approve` | reviewed (sets `reviewed_by`) | reviewer recorded |
| reviewed | `publish` | published | every standard in `standard_codes` covered by ≥1 `coverage∈{full,partial}` concept; `published_at` set; `visibility='catalog'` |
| reviewed | `request_changes` | draft | reviewer note required |
| published | `retire` | retired | a replacement version exists OR force flag; in-progress enrollments handled per §5 |
| any | (no direct edit of published) | — | published is immutable; changes require a new version (§5) |

`visibility` flips to `catalog` only on `publish`; `retire` flips it back to `private` (hidden from new
enrollments but existing pinned enrollments keep their version, §5).

### 4.2 Admin review console

A new admin-only blueprint in the web-ui (gated behind an admin role on `parents`, or a build-time
`ADMIN_TOKEN`). It is **not** linked from the student UI. Shows, per catalog course:

- **Course header:** subject, grade_band, version, catalog_status, standard_codes coverage summary
  (X/Y standards covered), reviewer, published_at.
- **Per-concept panel:** the rendered `content/{concept_uid}.md`, the concept's `concept_standards[]`
  tags (with `tag_source` badge: `human`/`llm` — LLM tags highlighted as "needs confirmation"), the
  band/bloom level, and **provenance** (§7: the sources used to hydrate it).
- **Edit affordances:** edit the concept Markdown inline (writes `content/{concept_uid}.md` and bumps
  an in-progress draft); add/remove/confirm standard tags; re-run hydration for a single concept;
  flag a concept as enrichment.
- **Approve / publish buttons** with the state-machine guards enforced server-side.

### 4.3 Endpoints (admin blueprint, `services/web-ui` → proxy → catalog admin service)

```
GET    /api/admin/catalog/courses                       → list catalog courses (any status) + coverage rollup
GET    /api/admin/catalog/courses/<uid>                 → full structure.json + per-concept provenance
GET    /api/admin/catalog/courses/<uid>/concepts/<cuid> → rendered md + tags + provenance
PUT    /api/admin/catalog/courses/<uid>/concepts/<cuid> → edit md / tags  {markdown?, concept_standards?}
POST   /api/admin/catalog/courses/<uid>/concepts/<cuid>/rehydrate → re-run hydrator for one concept
POST   /api/admin/catalog/courses/<uid>/transition      {action: submit_for_review|approve|publish|request_changes|retire, note?}
POST   /api/admin/catalog/build                         {brief_id}  → enqueue offline build (async; returns job id)
GET    /api/admin/catalog/jobs/<job_id>                 → build job status (reuses status_callback stream)
```

Only `is_catalog=1 AND catalog_status='published'` rows are ever returned by the **student** course-
listing path (`librarian.py` course CRUD + `/api/courses`). Add the predicate to the student list query
and to `get_course` access checks. Admin endpoints bypass it.

---

## 5. Versioning (B26.3)

### 5.1 Version + changelog

- `courses.version` (INTEGER, default 1) is the published version counter. Each course directory holds
  `data/catalog/courses/{uid}/CHANGELOG.md` — append-only, one entry per publish:
  `## v{n} — {published_at} — {reviewed_by}\n- {summary}\n- standards added/removed: …`.
- Optional immutable snapshots: on each publish, copy `structure.json` to
  `data/catalog/courses/{uid}/versions/v{n}.json` so a pinned enrollment can always read the exact
  content it started on even after a republish overwrites the live `structure.json`.

### 5.2 Enrollment pinning

`enrollments` (spec 01 §2) gains an effective pin via a new column (v9 micro-migration, additive):
`enrollments.course_version INTEGER` — the `courses.version` at enrollment time. Resolution:

- New enrollment → `course_version = current published version`.
- Student delivery reads content from `versions/v{course_version}.json` (falls back to live
  `structure.json` if no snapshot, i.e. v1 legacy). Progress (`user_progress`) is keyed by
  `concept_uid`; concept UIDs are **stable across versions** unless a concept is removed.

### 5.3 Republish without breaking in-progress enrollments

A republish is a **new version of the same `uid`**, not a new course:

1. Admin clones the published course into a `draft` working copy (same `uid`, `version` unchanged yet),
   edits, reviews.
2. On `publish`: `version += 1`; write `versions/v{new}.json`; overwrite live `structure.json`;
   append CHANGELOG; set `published_at`.
3. **In-progress enrollments stay pinned** to their `course_version` (they read `versions/v{old}.json`)
   until explicitly migrated. New enrollments get the new version.
4. **Migration (optional, admin-triggered):** `POST /api/admin/catalog/courses/<uid>/migrate_enrollments
   {from_version, to_version}` — only safe when the concept-UID set is a superset (no removed concept
   a student already completed). The migrator maps progress by `concept_uid`; concepts present in old
   but absent in new are flagged; concepts new in v{n} start uncompleted. Refuses (lists conflicts) if a
   completed concept was removed, preserving the student's record.

Retiring a course (`retire`) hides it from new enrollment but pinned enrollments continue on their
snapshot; a `retired` course with zero active enrollments may be hard-deleted by an admin.

---

## 6. Standards-coverage audit (B26.4)

A report answering: *which Utah codes have published content, and where are the gaps?* — drives the
build backlog (§8).

### 6.1 Query (against `standards ⟕ concept_standards ⟕ courses`)

```sql
-- Coverage per standard code, counting only PUBLISHED catalog content.
SELECT s.subject, s.grade_band, s.code, s.strand, s.is_enrichment,
       COUNT(DISTINCT cs.concept_uid)                          AS concept_count,
       COUNT(DISTINCT c.uid)                                   AS course_count,
       MAX(CASE WHEN cs.coverage='full' THEN 1 ELSE 0 END)     AS has_full_coverage
FROM standards s
LEFT JOIN concept_standards cs ON cs.standard_code = s.code
LEFT JOIN courses c
       ON c.uid = (SELECT course_uid_for_concept(cs.concept_uid))   -- resolved via structure index
      AND c.is_catalog = 1 AND c.catalog_status = 'published'
GROUP BY s.code
ORDER BY s.subject, s.grade_band, s.code;
```

Because `concept_standards` has no `course_uid`, maintain a lightweight resolver: either add a
`course_uid` column to `concept_standards` (recommended — denormalize at tag-write time in §2.3) or a
`concept_index(concept_uid PK, course_uid)` table populated on catalog write. **Recommendation:** add
`concept_standards.course_uid` so this join is a plain equi-join — cheap, and `concept_standards` is
only written by the admin job.

### 6.2 Report shape & gaps

Buckets per `(subject, grade_band)`:
- **Covered:** `has_full_coverage=1`.
- **Partial-only:** concept_count>0 but no `full`.
- **Gap:** concept_count=0 (no published content). Split by `is_enrichment` so baseline gaps rank above
  enrichment gaps.

CLI + endpoint: `python -m services.core.catalog_admin audit [--subject math]` →
`GET /api/admin/catalog/coverage`. Output is the backlog input for §8 (counts of baseline gaps per
subject/band).

**Acceptance:** for a freshly published Grade-6 RP course, `6.RP` shows `has_full_coverage=1`; an
un-authored `8.G` shows a baseline gap.

---

## 7. Provenance log (B26.5)

Records the sources used during hydration — supports the legal/licensing posture (F1) and the review
console's "where did this content come from" panel.

### 7.1 Where stored

Per-course JSONL file (append-only, co-located with the course, survives DB resets, easy to ship to
legal): `data/catalog/courses/{uid}/provenance.jsonl`. One line per hydrated concept:

```jsonc
{"concept_uid": "con_44444444", "title": "Unit Rate and Price Comparison",
 "hydrated_at": "2026-07-02T10:14:33Z", "source_type": "research+llm",
 "research_confidence": 0.62, "llm_model": "qwen3.5:9b", "course_version": 1,
 "sources": [
   {"title": "Ratio (mathematics)", "url": "https://en.wikipedia.org/wiki/Ratio",
    "type": "wikipedia", "domain_tier": "1", "license": "CC-BY-SA-3.0"},
   {"title": "Unit rate — USBE 6.RP guide", "url": "https://uen.org/...",
    "type": "web", "domain_tier": "1", "license": "unknown"}
 ]}
```

The hydrator already collects `research_sources` (title/url/type/domain_tier) and
`research_confidence` (`ContentHydrator.hydrate`, ~line 1851/1893). Provenance emission is a thin hook
in `_hydrate_one`: after `save_concept_content`, append the line. `license` is best-effort
(Wikipedia → CC-BY-SA; otherwise `unknown` and flagged for legal review). `source_type:"llm-only"`
concepts log an empty `sources[]` and `llm_model` — making purely-generated content auditable too.

### 7.2 Optional DB mirror

For cross-course queries (e.g. "every concept that used source X"), an additive global table:

```sql
CREATE TABLE IF NOT EXISTS hydration_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_uid TEXT NOT NULL, concept_uid TEXT NOT NULL, course_version INTEGER,
    source_type TEXT, llm_model TEXT, research_confidence REAL,
    sources TEXT,                 -- JSON array (same shape as JSONL line)
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prov_course ON hydration_provenance(course_uid);
```

The JSONL is the durable record of truth; the table is a queryable convenience refreshed from JSONL.

---

## 8. Build order operationalized (B16.4 → B16.6) — backlog

Turns the reference doc's Phase 1/2/3 (Recommendations §2) into a concrete authoring backlog. Each
line is one brief (`data/catalog/briefs/*.yaml`). Counts are *courses to author*, baseline-only first;
enrichment toggled later (B16.7).

### Phase 1 (B16.4) — highest ROI, mandatory & heavily assessed

| Subject | Band/Course | Courses (briefs) | Notes |
|---|---|---|---|
| math | K-2, 3-5, 6-8 (per grade K-8) | 9 (one per grade K..8) | strand-rich, most Socratic; start here |
| ela | K-2, 3-5, 6-8, 9-12 | 4 (one per band; SL strand exploits TTS/STT) | Speaking & Listening = differentiator |
| financial_lit | GFL (HS) | 1 | graduation-required; YouScience 74% cut score → exam blueprint |
| social_studies | USG (HS) | 1 | graduation-required; Basic Civics Test 35/50 → exam blueprint |

Phase-1 total ≈ **15 catalog courses**. Acceptance (B16.4): every published Phase-1 concept tags ≥1
Utah code and is human-reviewed.

### Phase 2 (B16.5)

| Subject | Scope | Courses |
|---|---|---|
| science (SEEd) | K-8 (per grade) + Biology, Chemistry, Physics, Earth&Space | 9 + 4 = 13 |
| social_studies | K-6 + Utah Studies, US Hist I/II, World History, World Geography | ~11 |
| cs | K-5, 6-8, 9-12 (theory) | 3 |

Phase-2 total ≈ **27 courses**. Acceptance: published + reviewed.

### Phase 3 (B16.6)

| Subject | Scope | Courses |
|---|---|---|
| world_lang | Novice/Intermediate proficiency tracks (per language later) | ~2 scaffolds |
| health | K-2, 3-5, 6-8, 9-12 (Strand 6 gated, §10) | 4 |
| library_media | K-5, 6-12 | 2 |

Phase-3 total ≈ **8 courses**. Build order within each phase follows the coverage audit (§6): author the
briefs that close the most baseline gaps first.

---

## 9. Catalog vs user electives — separation & FSM routing

### 9.1 Two physical stores

| | Catalog | User electives |
|---|---|---|
| dir | `data/catalog/courses/{uid}/` | `data/courses/{uid}/` |
| `courses.is_catalog` | 1 | 0 |
| `courses.visibility` | `catalog` (when published) | `private` |
| mutability | read-only to students; admin-only writes | student/parent create + delete |
| who sees it | any student whose band matches (published only) | only the owning family, approved |
| created by | offline `CatalogBuildJob` (§3) | student request path (`SkeletonBuilder` from topic) |

`StorageManager` is instantiated with a root: student path → `data/`; catalog admin → `data/catalog/`.
`CourseStore.list_courses()` for students filters `is_catalog=1 AND catalog_status='published'` UNION
the student's own approved electives (`is_catalog=0` joined to `enrollments` by owner). A catalog course
is offered to a student only when `courses.grade_band == student.grade_band` (spec 02 §1); electives may
differ in band and use the student's band for delivery.

### 9.2 Enrollment + FSM path selection

`enrollments.course_kind ∈ {catalog, elective}` (spec 01 §2) records which store a course lives in. At
session start the FSM resolves the course directory by `course_kind`:

```
enrollment.course_kind == 'catalog'  → read data/catalog/courses/{uid}/  (pinned version §5.2)
enrollment.course_kind == 'elective' → read data/courses/{uid}/
```

`MnemosyneFSM` `SET_CONTEXT`/`NAVIGATE_TO_TOPIC` carry the `course_kind` so `get_concept_details` /
`get_concept_content` hit the right `courses_dir`. Catalog content is never written by the FSM.

### 9.3 ★ Baseline / enrichment toggle (B16.7)

Each standard is `is_enrichment ∈ {0,1}` (§1) and each concept's `coverage` may be `enrichment`. A
parent setting (on `students.settings`, e.g. `"enrichment": true|false`) controls whether ★ content is
surfaced:

- **core only** (default): the FSM/path view hides concepts whose *only* standard tag is enrichment and
  hides courses built with `enrichment_included=true` unless the parent opts in.
- **core + enrichment:** enrichment concepts/courses are shown inline.

Implementation: build **baseline** and **enrichment** as separate authored artifacts where they diverge
materially (a baseline `6.RP` course vs a `6.RP + ★probability` course = two briefs, `enrichment:
false|true`), OR tag enrichment concepts within one course and filter at delivery by the parent setting.
**Recommendation:** single course, per-concept `coverage:"enrichment"` tags, runtime filter — avoids
duplicate authoring and keeps coverage audit simple. The toggle is a query-time filter, not a re-build.

---

## 10. Test plan / acceptance criteria + open questions

### 10.1 Tests

| Area | Test | Pass |
|---|---|---|
| Standards loader | `load_standards_dir` upserts math.yaml | row count == seed; re-run idempotent; ★ rows have `is_enrichment=1` |
| Loader prune guard | prune with a code that has `concept_standards` rows | refuses, returns `blocked`, non-zero exit |
| Catalog build | `CatalogBuildJob` from `math_g6_rp.yaml` | course written under `data/catalog/courses/`, `is_catalog=1`, `catalog_status='draft'`, every concept hydrated |
| Band-aware hydration | same concept title at band 6-8 vs 9-12 | word count / reading level differ per spec 02 §8 |
| Concept tagging | `tag_concept_standards` | every non-enrichment concept has ≥1 `concept_standards`; LLM tags flagged `tag_source:llm` |
| State machine | publish with an uncovered standard | rejected; publish with full coverage succeeds, sets `published_at`, `visibility='catalog'` |
| Student visibility | student `/api/courses` | sees only `published` catalog (band-matched) + own approved electives; never a `draft` |
| Versioning | republish v1→v2 | in-progress enrollment still reads `versions/v1.json`; new enrollment gets v2 |
| Migration guard | migrate enrollment where a completed concept was removed | refuses, lists conflicts |
| Coverage audit | publish G6 RP | `6.RP` shows `has_full_coverage=1`; un-authored `8.G` shows baseline gap |
| Provenance | hydrate a concept with research sources | `provenance.jsonl` line written with sources+license; llm-only logs empty sources |
| Enrichment toggle | student with `enrichment:false` | enrichment-only concepts hidden; with `true`, shown |

### 10.2 Open questions

- **Math mid-revision (reference doc Caveats):** the 2016 standards are seeded now; when USBE adopts the
  revision, codes may change. Plan: keep `adopted_year` per row; on re-scrape, `load_standards --prune`
  with the retire-block guard; published courses pinned to retired codes keep their snapshot but surface
  in the audit as "standard retired — re-author." Do we version `standards` themselves (a
  `standards_history` table) or treat the seed files (git-versioned) as the history? **Lean: git is the
  history; DB holds current.**
- **Sub-standard granularity:** the reference doc is strand/standard level, not the lettered `a/b/c`
  sub-standards. v1 tags at the strand code (`6.RP`); a future ingest of full USBE PDFs adds
  `6.RP.A.1`-style codes. `concept_standards.standard_code` already supports any string, so this is
  additive — but `coverage:"partial"` is doing the work of "covers some sub-standards" until then.
- **Coverage definition:** is one `full` concept enough to call a strand "covered," or do we require N
  concepts / all sub-standards? v1 = one `full` concept = covered; revisit when sub-standards land.
- **World-language band mapping:** WL is proficiency-keyed, not grade-banded; delivery band comes from
  the student. Confirm the catalog `grade_band` field can hold `null`/proficiency and the band resolver
  (spec 02 §1) falls through to student band cleanly.
- **Health Strand 6 gating:** sex-ed content requires Utah parental notification/consent (reference doc
  §5; `consent_records.consent_type='health_strand6'`, spec 01 §2). The publish guard for any course
  containing Strand-6 concepts must require the consent gate to exist before student delivery — wire
  into §4.1 publish guards before authoring health.
