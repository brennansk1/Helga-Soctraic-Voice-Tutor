# Web App Overhaul Plan

> Full audit of the Helga web UI — every template, stylesheet, JavaScript file, and backend route.  
> Organized by severity: 🔴 Critical → 🟡 Medium → 🟢 Nice-to-Have

---

## 🔴 Phase 1: Security & Correctness Bugs

### SEC-1: XSS via `innerHTML` (Critical)
**Files:** `review.html`, `test.html`, `courses.html`, `schedule.html`  
Every page that renders user-provided or server-returned text uses raw `innerHTML`:
```js
// review.html:191 — user-controllable course titles injected as HTML
div.innerHTML = `<span>${text}</span>`;
// test.html:176 — same pattern
// courses.html:2975 — module titles from API injected raw
```
**Fix:** Use `textContent` for plain text. For structured HTML (grade buttons, etc.) build DOM nodes programmatically or sanitize with `DOMPurify`.

### SEC-2: No CSRF Protection
**File:** `app.py`  
All POST endpoints (`/api/text_input`, `/api/update_card`, `/api/upload_epub`, `/api/draft/reorder`) accept bare JSON/form posts with no CSRF token.  
**Fix:** Add `flask-wtf` CSRFProtect or validate a custom `X-CSRF-Token` header on all state-mutating routes.

### SEC-3: Course Titles Rendered in `onclick` Attributes
**File:** `courses.html:2977`, `review.html:264`  
Titles containing quotes break HTML and can be used for injection:
```js
onclick="openSourceUpload('${mod.uid}', '${mod.title.replace(/'/g, "\\\\'")}')"
```
**Fix:** Use `data-*` attributes + `addEventListener()` instead of inline `onclick`.

### SEC-4: Unbounded File Upload Types
**File:** `app.py` — `upload_epub()` checks extension but `upload_source()` accepts `.txt, .md, .pdf, .epub` with no content-type validation or virus scanning.  
**Fix:** Validate MIME types, scan for malicious content, enforce per-file size limits.

---

## 🔴 Phase 2: Architecture & Code Quality

### ARCH-1: `courses.html` is a 3,100-line Monolith (123 KB)
This single file contains:
- 7 modal dialogs
- ~800 lines of inline `<style>` 
- ~1,800 lines of inline `<script>`
- Wizard state machine, drag-and-drop, Socket.IO listeners, course CRUD, live build visualization

**Fix:** Extract into components:
| Component | Lines | Extract To |
|-----------|-------|-----------|
| CSS for build visualization | ~400 | `static/css/build.css` |
| CSS for wizard/modals | ~200 | `static/css/wizard.css` |
| Automatic course creation JS | ~600 | `static/js/course_auto.js` |
| Custom wizard JS | ~500 | `static/js/course_wizard.js` |
| Draft board JS | ~200 | `static/js/draft_board.js` |
| Course cards/grid JS | ~400 | `static/js/courses.js` |

### ARCH-2: `session.js` is a 1,493-line God File (55 KB)
Contains: WebSocket handling, WebRTC setup, microphone I/O, UI state management, audio playback, chat rendering, thinking bubble, course creation modals, context rail rendering, flashcard logic, palace logic.  
**Fix:** Split into modules:
- `socket_handler.js` — connection, event binding
- `audio_io.js` — mic input, TTS output, WebRTC
- `chat_renderer.js` — message rendering, markdown, thinking bubble
- `ui_state.js` — path view, progress, mode switching

### ARCH-3: CSS Class Conflicts Across Pages
These classes are redefined with conflicting styles in different templates:
| Class | Defined In | Conflict |
|-------|-----------|----------|
| `.message` | `style.css`, `learn.html`, `review.html`, `test.html` | Different padding, border-radius, max-width |
| `.controls` | `style.css`, `learn.html`, `review.html`, `test.html` | Different padding, backgrounds |
| `.conversation-log` | `learn.html`, `review.html`, `test.html` | Different gap, padding |
| `.ai-avatar` | `learn.html`, `review.html`, `test.html` | Different sizes (100px / 60px / 80px) |
| `.text-input` | `style.css`, `learn.html`, `test.html` | Different padding, borders |
| `.status-badge` | `style.css`, `learn.html` | Different meaning entirely |
| `.stat-card` | `home.html`, `schedule.html` | Different flex/grid layouts |
| `.secondary-btn` | `style.css`, `learn.html` | Conflicting display property |

**Fix:** Namespace page-specific styles (`.review-page .message`, `.test-page .controls`), or consolidate into unified component classes in `style.css`.

### ARCH-4: Inline Styles Everywhere
Nearly every template uses `style="..."` attributes for layout:
```html
<!-- courses.html:97 — 3 lines of inline CSS on a label -->
<label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; 
  background: rgba(255,255,255,0.05); padding: 0.75rem; border-radius: 8px; 
  border: 1px solid rgba(255,255,255,0.1); flex: 1;">
```
~200+ inline `style=` attributes across templates.  
**Fix:** Create utility classes in `style.css` or move to page-scoped `<style>` blocks with named classes.

### ARCH-5: Stale/Dead Files
- `templates/review.html.orig` (12 KB) — git merge leftover
- `templates/review.html.rej` (938 B) — rejected patch
- `templates/_archive/` — 2 archived templates

**Fix:** Delete these files. They confuse maintenance and inflate the repo.

---

## 🟡 Phase 3: Dark Mode & Theme Consistency

### THEME-1: Hardcoded Colors Breaking Dark Mode
| File | Line | Issue |
|------|------|-------|
| `test.html:101` | `.message.ai { background: white }` | White on dark background |
| `test.html:123` | `.controls { background: white }` | Same |
| `schedule.html:148` | `.calendar-section { border: 1px solid #e5e7eb }` | Hardcoded light border |
| `schedule.html:268` | `.day-detail-panel { border: 1px solid #e5e7eb }` | Same |
| `schedule.html:290` | `.detail-header { border-bottom: 1px solid #f3f4f6 }` | Same |
| `courses.html:682` | `.build-log-content { background: #0d1117 }` | Hardcoded dark in light theme |
| `style.css:416` | `#thinking-logs { background-color: #0a101f }` | Always dark regardless of theme |

**Fix:** Replace all hardcoded colors with CSS variables (`var(--bg-secondary)`, `var(--border-color)`).

### THEME-2: Missing Themes
`style.css` only defines `:root` (light) and `[data-theme="premium-dark"]`. The settings modal offers 4 themes:
- Premium Dark ✅
- Cyberpunk ❌ (missing CSS)
- Light ✅ (`:root` default)
- Reader ❌ (missing CSS)

**Fix:** Add `[data-theme="cyberpunk"]` and `[data-theme="reader"]` CSS variable blocks.

### THEME-3: Font Inconsistencies
- `base.html` loads `Nunito` from Google Fonts
- `style.css` sets `font-family: 'Nunito', 'Segoe UI', sans-serif`
- `learn.html` overrides with `font-family: 'Inter', sans-serif` (Inter is never loaded)
- `account.html` references `font-family: 'Nunito', sans-serif` in form inputs

**Fix:** Use one consistent font stack. If using Inter, add it to the Google Fonts import in `base.html`.

---

## 🟡 Phase 4: Mobile & Responsive Design

### MOB-1: No Hamburger Menu
The nav bar has 9 links + settings button. On screens < 1024px these overflow or wrap badly.  
**Fix:** Add a hamburger toggle that collapses nav links into a dropdown/slide-out panel on mobile.

### MOB-2: No Responsive Breakpoints (Most Pages)
Only `schedule.html` and `account.html` have `@media` queries. Every other page is desktop-only:
- `courses.html` wizard modals have `min-width: 600px` hardcoded — unusable on phones
- `learn.html` path nodes assume desktop viewport
- `home.html` feature grid works but stats bar doesn't wrap

**Fix:** Add breakpoints at 768px and 480px for:
- Stacked layouts for stat cards and feature grids
- Full-width modals on mobile
- Hide sidebar by default on learn page
- Collapsible calendar on schedule page

### MOB-3: Fixed Heights Preventing Scroll
```css
/* learn.html */
.learn-wrapper { height: calc(100vh - 60px); }
.session-main { height: 85vh; }
/* test.html */
.session-interface { height: calc(100vh - 120px); }
```
These prevent content from scrolling properly on short viewports (phones, split-screen).  
**Fix:** Use `min-height` instead of `height`, or flex layouts that allow scrolling within the chat area.

---

## 🟡 Phase 5: UX Improvements

### UX-1: No Loading States / Skeletons
Every page just shows "Loading..." text. There are no shimmering skeleton screens or progress indicators while data fetches.  
**Affected:** `courses.html`, `learn.html`, `review.html`, `test.html`, `schedule.html`, `home.html`  
**Fix:** Add CSS skeleton animations for cards, chat messages, and calendar cells.

### UX-2: No Empty States with CTAs
When a page has no data, it shows minimal text like "No cards due right now!" without directing the user to take action.  
**Fix:** Add illustrated empty states with action buttons (e.g., "Create your first course →" on courses page).

### UX-3: No Error Boundaries
If any API call fails, most pages either silently fail or show a bare `console.error`. Only `review.html` adds an error message to chat.  
**Fix:** Add a global error handler that shows a toast notification for failed API calls with retry options.

### UX-4: No Keyboard Shortcuts
The learn tab is the most-used page but has zero keyboard shortcuts.  
**Fix:** Add:
- `Enter` — Send message (already works)
- `Escape` — Close modals
- `Space` — Toggle mic (when focused on session)
- `Ctrl+S` — Skip concept
- `Left/Right arrows` — Navigate path nodes

### UX-5: No Confirmation on Destructive Actions
Course deletion (`deleteCourse()`) has no confirmation dialog.  
**Fix:** Add a confirm modal: "Are you sure you want to delete 'Course Name'? This cannot be undone."

### UX-6: Review Page — No Voice Mode
The Learn page has voice/text mode toggle, but Review and Test pages are text-only despite having the same Socratic interaction pattern.  
**Fix:** Port the mic toggle and audio playback from `learn.html` into a shared component usable by all three session pages.

### UX-7: Test Page — No Course Selection
`test.html` auto-starts a quiz but never asks which course to test on. It also has no way to select difficulty or topic focus.  
**Fix:** Add a course selector screen (similar to `review.html`'s selection grid) before starting the quiz.

### UX-8: Schedule Page — No "Start Review" Action
Clicking a day shows review items, but there's no button to actually start a review session for that day's items.  
**Fix:** Add a "Start Review Session →" button in the day detail panel that links to `/review?date=YYYY-MM-DD`.

### UX-9: Home Page Stats Are Static
`loadStats()` fetches once on page load but never refreshes. If a user completes a session in another tab, the home stats are stale.  
**Fix:** Poll every 60 seconds, or use Socket.IO to push stat updates.

### UX-10: No Onboarding Flow
First-time users land on a home page with "0 Courses, 0 Mastered, 0 Day Streak" and no guidance.  
**Fix:** Add a first-visit onboarding modal or guided tour highlighting: Create Course → Learn → Review cycle.

---

## 🟡 Phase 6: Performance

### PERF-1: External CDN Dependencies Without Fallbacks
`base.html` loads from 3 external CDNs:
- `socket.io` from `cdnjs.cloudflare.com`
- `feather-icons` from `cdn.jsdelivr.net`
- Google Fonts from `fonts.googleapis.com`
- `SortableJS` from `cdn.jsdelivr.net` (loaded at bottom of `courses.html`)

If any CDN is down, the app breaks silently.  
**Fix:** Bundle critical deps locally (`socket.io.min.js`, `Sortable.min.js`). Use `font-display: swap` for Google Fonts and provide local fallback.

### PERF-2: No Asset Versioning
Only `session.js` has a cache-buster (`?v=3`). Other files (`style.css`, `toast.js`, `settings.js`, `status.js`) will serve stale cached versions after updates.  
**Fix:** Use Flask's `url_for('static', ...)` with a build hash, or add `?v={{ version }}` to all static asset references.

### PERF-3: Courses Page Loads Full Course Structures
`loadCourses()` fetches all courses including full module/unit/lesson hierarchies for rendering cards. For users with many courses, this is expensive.  
**Fix:** Add a lightweight `/api/courses/summary` endpoint that returns only `{uid, title, status, stats, created_at}`.

### PERF-4: No Image Optimization
`learn.html` loads avatar images from `ui-avatars.com` (external dep) with no local caching.  
**Fix:** Generate simple SVG avatars locally or cache the external images.

### PERF-5: Status Page Polls Every 5 Seconds
`status.html` calls `loadContext()` via `setInterval(loadContext, 5000)`. For a page that's rarely viewed, this creates unnecessary server load.  
**Fix:** Only poll when the tab is visible (`document.visibilityState === 'visible'`), and increase interval to 10-15s.

---

## 🟢 Phase 7: Accessibility (A11y)

### A11Y-1: Missing ARIA Labels
- Nav links have no `aria-current="page"` for active state
- Modal dialogs have no `role="dialog"` or `aria-modal="true"`
- Icon buttons (🎤, 🔊, ⏸, ⏭) have no `aria-label`
- Settings gear (⚙️) button has `title` but no `aria-label`

### A11Y-2: No Focus Management
- Opening a modal doesn't trap focus inside it
- Closing a modal doesn't return focus to the trigger button
- Tab order in learn.html session view is broken (sidebar elements are tabbable even when hidden)

### A11Y-3: Color-Only Status Indicators
Course status, review status, and path nodes rely solely on color to convey state. Users with color blindness can't distinguish completed/current/locked nodes.  
**Fix:** Add text labels, icons, or patterns alongside color.

### A11Y-4: No Skip Navigation Link
There's no "Skip to content" link, forcing screen reader users to tab through all 9+ nav links on every page load.

---

## 🟢 Phase 8: Code Hygiene & Polish

### CLEAN-1: Duplicate `window.socket` Declaration
`session.js:40-41` has two identical comments and the socket is declared globally:
```js
// Global socket variable
// Global socket variable
window.socket = io();
```

### CLEAN-2: Unused CSS
`style.css` contains styles for components that no longer exist:
- `#session-container` (line 146) — removed in favor of per-page layouts
- Rail styles for `#context-rail`, `#flashcard-rail`, `#palace-rail` repeated twice (lines 152-168)
- `.progress-bar` class defined twice (lines 381-389 and 554-561) with conflicting styles

### CLEAN-3: Console Logging in Production
Most JS files use `console.log`, `console.warn`, `console.error` liberally. In production, these should be suppressed or routed through a logging utility.  
**Fix:** Add a `logger.js` utility that respects a `DEBUG` flag.

### CLEAN-4: No Favicon
`base.html` uses an inline SVG emoji as favicon. This doesn't display consistently across browsers and OSes.  
**Fix:** Generate proper `.ico` and `.png` favicons from the brain emoji.

### CLEAN-5: `test.html` Name Collision
The template is called `test.html` which conflicts with the concept of testing/QA. Consider renaming to `quiz.html` or `assess.html` to avoid confusion.

### CLEAN-6: Missing Meta Tags
`base.html` has no `<meta name="description">`, no `<meta name="theme-color">`, and no Open Graph tags. While this is a local app, these improve the browser experience (tab color, PWA support).

---

## Implementation Priority

| Priority | Phase | Effort | Impact | Status |
|----------|-------|--------|--------|--------|
| 1 | SEC-1: XSS Fix | Low | Critical security fix | ✅ Done |
| 2 | THEME-1: Dark Mode Colors | Low | Immediate visual improvement | ✅ Done |
| 3 | ARCH-5: Delete stale files | Trivial | Repo cleanliness | ✅ Done |
| 4 | ARCH-3: CSS Conflicts | Medium | Fixes visual bugs | 🔄 Partial |
| 5 | MOB-1: Hamburger Menu | Medium | Mobile usability | ✅ Done |
| 6 | UX-5: Delete Confirmation | Low | Prevents data loss | ✅ Already existed |
| 7 | UX-1: Loading Skeletons | Medium | Perceived performance | ✅ CSS added |
| 8 | ARCH-1: Split `courses.html` | High | Maintainability | ✅ Done (3102→403 lines) |
| 9 | ARCH-2: Split `session.js` | High | Maintainability | ✅ Done (1492→882 lines) |
| 10 | All remaining items | Varies | Polish & completeness | ✅ Most done |
