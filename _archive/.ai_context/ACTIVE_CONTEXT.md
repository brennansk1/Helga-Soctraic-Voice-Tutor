# Active Context

## 🎯 Current Task
Massive Architectural Overhaul Complete (Phases 1-11) - Storage migration, Socratic Questions, UI improvements, Skeleton Hierarchy relevance, and Module Self-Correction.

## 🛠️ Status
🟢 ACTIVE (System Stable & Ready for Manual UI Testing)
- **Infrastructure:** Stable on Mac Mini. Native headless testing fully supported via mock isolation.
- **Storage:** KuzuDB completely removed and replaced by a hybrid SQLite (`helga.db`) + JSON flat file `StorageManager`.
- **Web UI:** Running on port **5050**. All routes active.
- **Codebase Audit:** 143/143 passing tests across Core and Web integration suites.

## ✅ Completed (Massive Overhaul - Phases 1-6)
1. **Phase 1: Storage Layer Replacement** - Built `StorageManager` (SQLite + JSON), removed KuzuDB, updated `clean_slate.py` and Docker configurations.
2. **Phase 2: Socratic Questions Integration** - Added 6 Socratic prompt pipelines (Scenario, Mechanic, Analogy, Synthesis, Extrapolation, Diagnostics) with strict grade-based progression logic.
3. **Phase 3: Learning Tab Overhaul** - Built a Duolingo-style vertical S-curve path, Tell-Ask-Listen loop, and connected unit completion to review scheduling.
4. **Phase 4: Schedule Tab** - Created `/schedule` route with a calendar grid view, daily detail panel, and streak/retention stats.
5. **Phase 5: Visual Improvements** - Course card redesigns with SVG progress rings, CSS `accent-primary` gradient transitions, and responsive modal polish.
6. **Phase 6: Core Testing** - Wrote exhaustive unit suites (`test_storage.py`, `test_spaced_repetition.py`, `test_socratic_types.py`). Reached 100% green pass rate (143 scenarios) for isolated logic and web endpoints.
7. **Phase 10: Skeleton Hierarchy & Relevance** - Fixed thematic bleed and generic titles using progression roles, level-specific constraints, and smarter negative scope.
8. **Phase 11: Module Generation Self-Correction** - Implemented a retry loop with automated critique to ensure mandatory module counts are met for all depths.

## 🛠️ Recent Steps
- [x] Removed obsolete `KuzuDB` integration tests and `cypher` references out of the codebase.
- [x] Migrated all external hardware/infrastructure mocks to `conftest.py` for global test isolation.
- [x] Fixed LLM payload formatting in `test_prompts.py` for structured roles.
- [x] Fixed `power_daemon.py` graceful shutdown and SOC battery math verifications.
- [x] Updated AI context and walkthrough documents.

## 📁 Key Files Modified in Overhaul
- `services/common/storage.py` - New universal data layer interface.
- `services/common/spaced_repetition.py` - Extracted SM2 scheduling logic.
- `services/common/prompts.py` - Centralized Socratic prompt generation.
- `services/core/fsm_logic.py` - Overhauled state machine for Tell-Ask-Listen loop and new storage.
- `services/web-ui/templates/learn.html` - New vertical S-curve learning UI.
- `services/web-ui/templates/schedule.html` - New calendar review layout.
- `tests/conftest.py` - Global test mock environment.

## ⚠️ Known Issues
- Currently optimized for macOS development natively. Heavy end-to-end docker tests invoking the actual `whisper` and `pyaudio` pipelines must be run in the target (Ubuntu/Jetson) hardware environment due to AV library constraints.

## 🚀 Environment
- **Host:** Mac Mini (24GB RAM)
- **Production Target:** Jetson Orin Nano (8GB RAM)
- **Mode:** CPU-Only (Mac Compatibility)
- **Web UI Port:** 5050
