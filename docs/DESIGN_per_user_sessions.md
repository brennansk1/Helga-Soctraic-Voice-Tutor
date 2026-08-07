# Design: Per-User Sessions

This document outlines the architecture for supporting multiple concurrent learners (per-user sessions) in Helga.

## 1. Global State Today

Currently, Helga was built with a single-learner assumption. Global state is scattered across the following locations:

- **`user_state.json`**: Hardcoded to a single file path at `services/core/fsm_logic.py:268` (`self.state_file = os.path.join(self.data_root, "user_state.json")`).
- **The FSM Instance**: While `services/core/fsm_logic.py:4284` attempts to use an `FSMRegistry` to hold instances per `student_id`, all instances inherit the same `_shared_storage` (line 4283), effectively pointing them at the same global data.
- **Session/Course Registry (SQLite)**: 
  - `user_progress` table (`services/common/storage.py:173`): Uses `concept_uid` as the PRIMARY KEY with no `student_id` column.
  - `courses` table (`services/common/storage.py:242`): Uses `uid` as the PRIMARY KEY with no `student_id` column.
  - `activity_log` table (`services/common/storage.py:189`): No `student_id` column.

## 2. Concretely Breaking Behaviors

If two users (Student A and Student B) connect concurrently, the following specific failures occur:

1. **Progress Contamination**: Because `user_progress` lacks a `student_id` and uses `concept_uid` as the primary key, if Student A successfully masters a concept, it is marked complete globally. When Student B reaches that concept, the system will consider it already mastered by B, skipping the lesson or affecting the SRS scheduling.
2. **State Teleportation**: Because `user_state.json` is a single file, when Student A navigates to a new topic (updating the JSON), Student B's subsequent API poll (e.g., `/api/state`) will read Student A's state and physically update Student B's UI to Student A's location.
3. **Course Collision**: If both users attempt to generate a course with the same name, they will write to the same `courses` table, causing primary key conflicts or intermingling generated assets.

## 3. Candidate Architectures

### Option A: Schema Expansion (Tenant ID)
Add a `student_id` column to every table in the SQLite database and make primary keys composite (e.g., `PRIMARY KEY (student_id, concept_uid)`).
- **Trade-offs**: Standard relational design, allowing aggregate queries across all students (e.g., global difficulty of a concept). However, it requires modifying almost every SQL string in the application.
- **Migration Cost**: High. Requires a schema migration script to `ALTER TABLE`, assigning existing rows to a `default` student, and a massive rewrite of `services/common/storage.py` queries.

### Option B: Per-User Database Files (SQLite per Student)
Instead of one global `helga.db`, use `helga_{student_id}.db`. Instantiate a separate `StorageManager` for each active student.
- **Trade-offs**: Absolute isolation. Prevents cross-contamination by design. Easy to back up or delete a single student's data. Cross-student analytics become harder (requires attaching multiple DBs).
- **Migration Cost**: Low. Rename the existing `helga.db` to `helga_default.db` (migrating all existing progress to the default learner). Modify `fsm_logic.py` to instantiate `StorageManager` inside the `FSMRegistry` factory rather than globally. SQL queries remain completely unchanged.

### Option C: Isolated Course Workspaces (Filesystem)
Move all user progress and course metadata out of SQLite and into a filesystem tree (e.g., `data/students/{student_id}/progress.json`).
- **Trade-offs**: Avoids database locking issues entirely, but loses the relational querying benefits of SQLite (e.g., joining progress with course metadata).
- **Migration Cost**: Very High. Requires rewriting the entire persistence layer and writing custom JSON migration scripts for existing data.

## 4. Depth and Scaffolding Logic

Currently, pedagogy state like `draft_course_depth`, `pre_assessment_module_depths`, `concept_miss_streak`, and `grade_band` are held in memory on the FSM instance.

In a per-user architecture, these scaffolding variables must become **learner-dependent** and persist across sessions (e.g., serialized into the `fsm_sessions` blob or a new `learner_profiles` table). 

**Fading the Scaffolding**: 
The fading of scaffolding (such as the `affect_note` injected in `handle_socratic_answer` when a student struggles) should be driven by a measurable signal: the **historical `easiness_factor` (from FSRS) and `times_correct`** over multiple sessions. Instead of simply resetting `concept_miss_streak` to 0 on every new concept, the FSM should query the student's historical stability on related concepts to decide the initial depth and scaffolding level.

## 5. Recommended Option

**Recommendation: Option B (Per-User Database Files)**

**Reason**: The current `StorageManager` was heavily designed around a single-tenant assumption (using `concept_uid` and `course_uid` as sole primary keys). Rewriting every SQL query to include `WHERE student_id = ?` and handling composite keys is highly invasive and prone to accidental data leaks. By moving to `helga_{student_id}.db`, we achieve perfect isolation, zero SQL query rewrites, and the simplest possible migration path for existing data (just renaming the file).
