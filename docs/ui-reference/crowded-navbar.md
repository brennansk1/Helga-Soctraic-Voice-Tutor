# Reference: the header, as of 2026-08-25

Kept as an example of a crowded navigation bar, for a later UI pass.

A screenshot could not be captured: the browser pane is not on any OS display,
so `screencapture` reaches only the desktop. This records the same evidence in
a form that does not go stale — the inventory and the real markup.

## Measured

| | |
|---|---|
| header width | 1280px |
| header height | 59px |
| interactive elements in the DOM | **17** |
| visible at once | **13** |

Thirteen interactive targets in a 1280×59 strip, plus a four-badge
gamification cluster that appears conditionally.

## What is competing for the space

**Primary nav — 7 links**
Home · Courses · Degree · Library · Progress · Practice · Settings

**Utilities — 6 more controls**
- search (button that expands into an input)
- `#continue-pill` — "Continue"
- `#notebook-pill` — "Notebook"
- `#build-pill` — "Building…" with a pulse animation
- `#ask-open-btn` — "Ask"
- `#notify-bell` with an unread count badge
- `#header-theme-toggle`

**Conditional — 4 more**
`.gamification-bar`: XP counter, level badge, streak badge, daily-goal dots.

## Why it reads as crowded

Three pills (`Continue`, `Notebook`, `Building…`) share one visual treatment
and one region while meaning three unrelated things — resume a session, open a
tool, and report background state. Build state in particular is *status*, not
navigation, and it is permanent chrome rather than something that appears when
there is a build to report.

Seven top-level destinations is already at the upper end for a product whose
primary path is open course → learn. `Degree`, `Practice` and `Library` are all
outside Mode A.

## The markup

Extracted live from `/learn`, 5,352 characters.

```html
<header class="app-header" role="banner">
  <div class="logo"><a href="/">Helga</a></div>
  <nav class="app-nav" id="app-nav" role="navigation" aria-label="Main navigation">
    <a href="/" class="nav-link">Home</a>
    <a href="/courses" class="nav-link">Courses</a>
    <a href="/degree" class="nav-link">Degree</a>
    <a href="/library" class="nav-link">Library</a>
    <a href="/progress" class="nav-link">Progress</a>
    <a href="/practice" class="nav-link">Practice</a>
    <a href="/settings" class="nav-link">Settings</a>
  </nav>
  <div class="header-utils">
    <div class="helga-search-wrapper" role="search">…</div>
    <a href="#" class="build-pill hidden" id="continue-pill"><span id="continue-label">Continue</span></a>
    <a href="/notebook" class="build-pill hidden" id="notebook-pill"><span>Notebook</span></a>
    <a href="/build" class="build-pill hidden" id="build-pill">
      <span class="build-now-pulse" aria-hidden="true"></span>
      <span id="build-pill-label">Building…</span>
    </a>
    <button id="ask-open-btn" class="ask-open-btn">…Ask</button>
    <div class="gamification-bar hidden" id="gamification-bar">
      <span class="xp-badge" id="xp-counter">0 XP</span>
      <span class="level-badge" id="level-badge">Lv 1</span>
      <span class="streak-badge" id="streak-counter">… 0</span>
      <span class="daily-goal-dots" id="daily-goal-dots"></span>
    </div>
    <div class="notify-wrap"><button id="notify-bell" class="notify-bell">…<span class="notify-count">1</span></button></div>
    <button id="header-theme-toggle" class="header-theme-toggle">…</button>
  </div>
</header>
```

Styles live across `style.css`, `design-system.css` and `surfaces.css`.
