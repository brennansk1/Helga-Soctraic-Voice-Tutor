# Task Queue: Project Helga

## [P0] Manual UI Testing & End-to-End Validation
- [ ] **Full E2E Verification** — see `manual_verification_tests.md` for complete checklist
    - [ ] Flow A: Automatic Course Creation (5 sub-flows)
    - [ ] Flow B: Custom Course Wizard (5 sub-flows)
    - [ ] Flow C: EPUB Upload (3 sub-flows)
    - [ ] Flow D: Socratic Learning Instruction (9 sub-flows)
    - [ ] Flow E: Spaced Repetition / Review (4 sub-flows)
    - [ ] Flow F: Memory Palace (4 sub-flows)
    - [ ] Flow G: Mode Switching (4 sub-flows)
    - [ ] Flow H: Audio / TTS Quality (5 sub-flows)
    - [ ] Flow I-M: Management, Navigation, Data Integrity, Docker, Security

## [P1] Feature Audit Complete (Phases 1-11)
- [x] **Phase 1: Storage Layer Replacement** (SQLite + JSON hybrid, KuzuDB removed).
- [x] **Phase 2: Socratic Questions** (6 Types: Scenario, Mechanic, Analogy, Synthesis, Extrapolation, Diagnostics).
- [x] **Phase 3: Learning Tab Overhaul** (S-Curve path, Tell-Ask-Listen loop).
- [x] **Phase 4: Schedule Tab** (Calendar, day details, streak/retention stats).
- [x] **Phase 5: Visual Improvements** (Course cards, SVG progress rings, CSS polish).
- [x] **Phase 6: Core Testing** (143/143 passing, exhaustive FSRS & UI routing tests).
- [x] **Phase 7: Learn Tab Deep Overhaul** (SET_CONTEXT, progress persistence, skip vs complete).
- [x] **Phase 8: Audio Pipeline Fixes** (Sample rate, queue serialization, fade-in, timeout).
- [x] **Phase 9: Mode Switching UX** (Dead-end elimination, mode indicator badge, completion overlay).
- [x] **Phase 10: Skeleton Hierarchy & Relevance** (Thematic bleed & generic titles fix).
- [x] **Phase 11: Module Generation Self-Correction** (Retry logic for module under-counts).

## [P2] Infrastructure & Polish Complete
- [x] **Database Integrity Bot:** Deprecated in favor of SQLite ACID compliance.
- [x] **Resource Management:** Docker memory limits expanded for Mac Mini.
- [x] **Log Rotation:** Configured `logrotate` for `data/logs/*.log`.
- [x] **Pedagogy Enhancements:** Progress persistence logic finalized.
- [x] **Context Protection:** Moved external dependency mocks to `conftest.py` for headless native testing.

## [P3] Remaining Implementation
- [ ] **EPUB Upload Route:** Fully implement `/api/upload_epub` end-to-end (EPUB-1)
- [ ] **CSRF Protection:** Add flask-wtf CSRFProtect to all POST endpoints
- [ ] **Mobile Responsive:** Add breakpoints at 768px and 480px for all pages
- [ ] **Course Status Polling:** Add `GET /api/course_status/{uid}` for wizard timeout recovery
- [ ] **Onboarding Flow:** First-time user guided tour

## [P4] Advanced Features & Hardening
- [ ] **Incremental Ingestion:** Support adding content to an existing course.
- [ ] **Memory Optimization:** Address Jetson 8GB OOM crashes (if any remain).
- [ ] **Performance Tuning:** Fine-tune latency for Jetson-specific inference.
- [ ] **Hardware Verification:** E2E verification on Jetson Orin Nano hardware.
- [ ] **Browser Automation Tests:** Playwright E2E test suite.
- [ ] **CI/CD Pipeline:** GitHub Actions for pytest + linting + Docker build.
