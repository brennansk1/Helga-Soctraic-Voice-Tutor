// Session.js - Handles the unified session interface using WebSockets (text-only)
// Audio/WebRTC/microphone code has been removed. TTS is on-demand via addTTSButton().
// Course creation and rail UI code are in separate module files:
//   session-course-creation.js, session-rails.js

let currentMode = 1; // Default to Mode 1
let currentState = {};
let thinkingBubble, thinkingStatusText;
let toggleLogsBtn, thinkingLogsContainer, thinkingLogsCode;
let displayedMessagesCount = 0;
window.navigatingToNode = false; // Guard flag: suppress stale transcript during navigation

// Full reset of the chat session DOM + module-local counters. Call this
// from enterNode() before any NAVIGATE_TO_TOPIC to guarantee no leftover
// messages, optimistic bubbles, or streaming state from a previous concept
// can bleed into the new one. The streaming bubble teardown lives in
// _tearDownStreamingBubble() defined further down the file where
// _streamingBubble is in scope.
window.resetChatSession = function () {
    console.log("[resetChatSession] Clearing chat state");
    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        chatStream.innerHTML = '';
    }
    displayedMessagesCount = 0;
    if (typeof window._tearDownStreamingBubble === 'function') {
        window._tearDownStreamingBubble();
    }
};

// Global socket variable (initialized in DOMContentLoaded)
let socket = null;

function getModeFromState(state) {
    if (!state) return 0;
    switch (state) {
        case 'SOCRATIC_LEARNING': return 1;
        case 'SPACED_REPETITION': return 2;
        case 'MEMORY_PALACE': return 3;
        default: return 0; // Lobby or other
    }
}

function updateUI(state) {
    if (!state) {
        console.error("Received null or undefined state. Aborting UI update.");
        return;
    }
    currentState = state;
    window.currentState = state;  // exposed for other modules (session-rails)

    // Update pause overlay
    const pauseOverlay = document.getElementById('pause-overlay');
    if (pauseOverlay) {
        pauseOverlay.classList.toggle('hidden', state.state !== 'PAUSED');
    }

    // Pause button visibility is now driven by active TTS audio state,
    // not FSM state — the FSM never actually enters a PAUSED state.
    // See the interval-based visibility sync further down.

    // Update mode and rail visibility
    const newMode = getModeFromState(state.state);
    if (newMode !== currentMode) {
        currentMode = newMode;
        toggleRails(currentMode);
    }

    // --- Chatbox topbar sync ---
    // Keep the chatbox topbar (concept title, bloom badge, progress pill)
    // in sync with the FSM's current_lesson_node so concept transitions
    // via next_syllabus_item() are reflected without relying solely on
    // status_update CPROG broadcasts (which can race the state poll).
    if (state.current_lesson_title) {
        const titleEl = document.getElementById('concept-title-display');
        if (titleEl && titleEl.textContent.trim() !== state.current_lesson_title.trim()) {
            titleEl.textContent = state.current_lesson_title;
        }
    }
    if (typeof state.bloom_level === 'number' && state.bloom_level > 0) {
        const bloomBadge = document.getElementById('bloom-level-badge');
        if (bloomBadge) {
            bloomBadge.textContent = 'Bloom ' + state.bloom_level;
            bloomBadge.className = 'learn-chat-badge bloom lv' + Math.min(state.bloom_level, 6);
            bloomBadge.classList.remove('hidden');
        }
    }
    // Progress pill (completed/total) in the topbar.
    //   completed_topics = what's been finished
    //   syllabus_length  = remaining queue (already excludes current concept)
    //   current_lesson_uid is the "in-progress" concept — it counts as the
    //   ordinal position the user is currently working on.
    const progressPill = document.getElementById('session-progress-pill');
    if (progressPill) {
        const completed = Array.isArray(state.completed_topics) ? state.completed_topics.length : 0;
        const remaining = (typeof state.syllabus_length === 'number') ? state.syllabus_length : 0;
        const total = completed + remaining;
        if (total > 0) {
            const newText = completed + ' / ' + total;
            // Pulse on change so progress feels alive.
            if (progressPill.textContent !== newText && progressPill.textContent !== '0 / 0') {
                progressPill.classList.remove('pill-pulse');
                void progressPill.offsetWidth;
                progressPill.classList.add('pill-pulse');
            }
            progressPill.textContent = newText;
            progressPill.classList.remove('hidden');
        } else {
            progressPill.classList.add('hidden');
        }
    }

    // Mastery progress bar — shows how close the user is to completing
    // the current concept. The FSM gate is streak >= 2 AND questions >= 3,
    // so "progress" = min(streak/2, (questions/3)) clipped to [0, 1].
    const masteryBar = document.getElementById('mastery-progress-fill');
    const masteryWrap = document.getElementById('mastery-progress-wrap');
    if (masteryBar && masteryWrap) {
        const streak = typeof state.concept_correct_streak === 'number' ? state.concept_correct_streak : 0;
        const questions = typeof state.concept_question_count === 'number' ? state.concept_question_count : 0;
        const isLearning = state.state === 'SOCRATIC_LEARNING' && state.current_lesson_uid;
        if (isLearning && questions > 0) {
            // Both gates must be met — reflect the slower one so the bar
            // doesn't jump to 100% and then stall.
            const streakPct = Math.min(1, streak / 2);
            const questionsPct = Math.min(1, questions / 3);
            const pct = Math.min(streakPct, questionsPct) * 100;
            masteryBar.style.width = pct + '%';
            masteryWrap.classList.remove('hidden');
            // Tooltip describes the gate for curious users
            masteryWrap.title = streak + ' correct in a row · ' + questions + ' question' + (questions === 1 ? '' : 's') + ' answered';
        } else {
            masteryWrap.classList.add('hidden');
        }
    }

    // A5 trust surface — show where this concept's content came from.
    // Driven from graph_node (the FSM's current_lesson_node), which carries the
    // concept's full markdown; see updateTrustSurface().
    updateTrustSurface(state);

    // Update chat stream
    updateChatStream(state.transcript);

    // Update specific mode UIs. Context rail / pedagogy sidebar is hidden
    // in the Gemini-style learn tab — skip those DOM writes entirely and
    // only render the rails for non-learn pages (flashcards, palace).
    if (currentMode === 2) {
        updateFlashcard(state);
    } else if (currentMode === 3) {
        updatePalace(state);
    }
}

/* ===========================================================================
 * A5 TRUST SURFACE — sources + grounding confidence on the concept view
 * ===========================================================================
 *
 * WHERE THE DATA COMES FROM
 * -------------------------
 * Everything below is parsed out of the concept's own markdown, which already
 * arrives in the browser as `state.graph_node.text` on every state_update
 * (fsm_logic.get_state() spreads current_lesson_node into `graph_node`, and
 * web-ui passes the state through verbatim). No new endpoint is needed.
 *
 * Parsing markdown client-side is not the prettiest option, but it is the only
 * HONEST one available right now: the builder formats `research_sources` into
 * markdown and then discards the list, so there is no structured citation array
 * anywhere in storage, in structure.json, or in any API response. The markdown
 * IS the citation record.
 *
 * Two deliberate non-choices, both verified rather than assumed:
 *
 *   - NOT `state.current_context`. Same markdown, but truncated to 10 000
 *     chars. `## Sources` is appended LAST by the builder, so it is the first
 *     thing that truncation destroys on long concepts.
 *
 *   - NOT `graph_node.source_confidence` as the primary signal. That field is
 *     populated by _queue_entry() (the syllabus-advance path) but NOT by
 *     navigate_to_topic(), which is the path the learn tab actually uses when
 *     a learner clicks a node. It is read here as a cross-check when present,
 *     never depended on.
 * ========================================================================= */

/* The builder's confidence floor, HELGA_CONFIDENCE_FLOOR (course_builder.py).
 * Its default is 0.5 and it is persisted per course at
 * structure.json -> grounding.confidence_floor — but no endpoint exposes that
 * block to the browser, so it cannot be read at runtime today.
 *
 * This mirroring is safe for the state that matters most: a concept scored
 * below the floor at BUILD time gets a "Limited sources." blockquote written
 * into its markdown, and _classifyGrounding() treats that marker as
 * authoritative. So if a deployment overrides the floor, the weakly-grounded
 * state still renders correctly from the artifact itself; only the
 * grounded/partly-grounded split below would drift. */
const TRUST_FLOOR = 0.5;

/* The boundary between "well grounded" and "partly grounded" is the midpoint of
 * the above-floor range, i.e. halfway between the floor and a perfect score.
 * Derived from the floor rather than picked, so there is exactly one number to
 * change if the floor moves. With the default floor this is 0.75. */
const TRUST_STRONG = TRUST_FLOOR + (1 - TRUST_FLOOR) / 2;

/* Raw `type` values come from the research service (wikipedia, web, ...). Map
 * them onto words a learner can act on. Anything unrecognised is title-cased
 * and shown as-is rather than dropped — an unknown source type is still
 * information, and silently relabelling it "Web" would be a lie. */
const TRUST_TYPE_LABELS = {
    wikipedia: 'Reference',
    encyclopedia: 'Reference',
    reference: 'Reference',
    dictionary: 'Reference',
    textbook: 'Textbook',
    book: 'Textbook',
    epub: 'Textbook',
    'user-document': 'Your document',
    primary: 'Primary',
    paper: 'Primary',
    journal: 'Primary',
    arxiv: 'Primary',
    web: 'Web',
    search: 'Web',
    searxng: 'Web'
};

function _trustEscape(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Only http(s) links are rendered as links. The URLs originate from web search
 * results, so javascript:/data: must never reach an href. */
function _trustSafeUrl(url) {
    const u = String(url || '').trim();
    return /^https?:\/\//i.test(u) ? u : '';
}

/**
 * Pull the trust record out of a concept's markdown.
 *
 * Expected shape, as written by course_builder.py:
 *
 *   > **Limited sources.** ... (confidence 0.40) ...      <- only when below floor
 *
 *   ## Sources
 *   - [Pythagorean theorem](https://en.wikipedia.org/...) — wikipedia (Tier 1)
 *
 *   *Source confidence: 0.40*
 */
function parseTrustFromMarkdown(md) {
    const out = { sources: [], confidence: null, limitedMarker: false };
    if (!md) return out;
    const text = String(md);

    // The builder's own below-floor verdict, baked into the artifact at build
    // time. Authoritative — see the TRUST_FLOOR note above.
    out.limitedMarker = /\*\*Limited sources\.\*\*/.test(text);

    const confMatch = text.match(/\*\s*Source confidence:\s*([0-9]*\.?[0-9]+)\s*\*/i);
    if (confMatch) {
        const parsed = parseFloat(confMatch[1]);
        if (!isNaN(parsed)) out.confidence = parsed;
    }

    // Isolate the "## Sources" section: everything after its heading, cut at the
    // next heading. Done in two steps rather than one regex with an
    // end-of-input lookahead — the builder appends Sources LAST, so in practice
    // there is no following heading to anchor against.
    const secMatch = text.match(/^#{1,3}[ \t]*Sources[ \t]*$([\s\S]*)/mi);
    if (secMatch) {
        const nextHeading = secMatch[1].search(/^#{1,3}[ \t]/m);
        const section = nextHeading === -1
            ? secMatch[1]
            : secMatch[1].slice(0, nextHeading);
        const lines = section.split('\n');
        for (const line of lines) {
            // - [Title](url) — type (Tier N)
            // The separator is an em dash in practice; accept hyphens too since
            // it is only a delimiter and cheap to be lenient about.
            const m = line.match(/^\s*[-*]\s*\[([^\]]*)\]\(([^)\s]*)[^)]*\)\s*(?:[—–-]+\s*(.*))?$/);
            if (!m) continue;
            const tail = (m[3] || '').trim();
            const tierMatch = tail.match(/\(Tier\s*(\d+)\)/i);
            const rawType = tail.replace(/\(Tier\s*\d+\)/i, '').trim().toLowerCase();
            out.sources.push({
                title: (m[1] || '').trim() || 'Untitled source',
                url: m[2] || '',
                type: rawType,
                tier: tierMatch ? tierMatch[1] : null
            });
        }
    }
    return out;
}

/**
 * Decide which of the three honest states this concept is in.
 *
 * Three words beat a bare number: "0.85" tells a learner nothing, and inviting
 * them to interpret a float is how a trust signal becomes decoration.
 *
 * The ordering matters — every path to "this is weakly grounded" is checked
 * before either confident state can be returned, so the failure mode is always
 * toward under-claiming.
 */
function _classifyGrounding(trust, graphNode) {
    const node = graphNode || {};

    // A concept invented to pad an empty lesson: no research pass ever ran.
    if (node.llm_fallback) return 'unsourced';

    // The builder already judged this one below its own floor.
    if (trust.limitedMarker) return 'unsourced';

    const conf = (typeof trust.confidence === 'number')
        ? trust.confidence
        : (typeof node.source_confidence === 'number' ? node.source_confidence : null);

    if (conf !== null && conf < TRUST_FLOOR) return 'unsourced';

    // No citations at all means the content is the model's own recall, whatever
    // number happens to sit beside it.
    if (!trust.sources.length) return 'unsourced';

    // Cited, but the build recorded no score — claim the middle, not the top.
    if (conf === null) return 'partial';

    return conf >= TRUST_STRONG ? 'grounded' : 'partial';
}

/**
 * The build's own verdicts for THIS concept, if the learn page managed to load
 * them. Set by learn.html from /api/course_quality; absent on any other page,
 * and absent whenever that fetch failed — in which case the surface falls back
 * to grounding alone, exactly as it behaved before.
 */
function _conceptChecks(conceptUid) {
    const q = window.__courseQuality;
    if (!q || !conceptUid) return null;
    const per = (q.concepts || {})[conceptUid] || {};
    return {
        concept: per,
        // Course-level context. Needed to phrase a clean result honestly: the
        // fact checker samples (34% by default), so "not flagged" and "checked
        // and clean" are different statements and must not share wording.
        course: q.checks || {}
    };
}

/**
 * Which check, if any, outranks the grounding verdict for the header.
 *
 * A claim the fact checker confirmed false — and that survived the corrective
 * rewrite — is a worse defect than thin sourcing, so it takes the headline.
 * Everything else stays in the detail panel, where it supports the concept text
 * instead of competing with it.
 */
function _escalateForChecks(grounding, checks) {
    if (!checks) return grounding;
    const fact = checks.concept.fact;
    if (fact && fact.state === 'fail') return 'flagged';
    return grounding;
}

const TRUST_COPY = {
    flagged: {
        label: 'Contains a claim checked and found false',
        caveat: 'During the build, a claim in this concept was flagged, independently confirmed false, and then rewritten — and the rewrite still did not clear the check. Read this concept as a draft: verify anything specific in it against a source you trust before relying on it.'
    },
    grounded: {
        label: 'Well grounded',
        caveat: 'This concept was built from the references below, and the research pass found them to agree.'
    },
    partial: {
        label: 'Partly grounded',
        caveat: 'Some of this concept comes from the references below; the rest is the model’s own knowledge. Worth checking specific figures, dates and names against a source you trust.'
    },
    unsourced: {
        label: 'Mostly the model’s own knowledge',
        caveat: 'The research pass found little corroborating material for this concept, so it leans on what the model already knew. The ideas are usually sound, but treat specific figures, dates, names and results as unverified — check them before you rely on or repeat them.'
    }
};

/**
 * The other two checks, as rows in the detail panel.
 *
 * Grounding already owns the header, so it is not repeated here. Each row is
 * rendered only when the check actually ran for this course — a check that
 * never ran is reported as not assessed, at the bottom, and never as a pass.
 */
function _renderTrustChecks(checks) {
    if (!checks) return '';
    const per = checks.concept || {};
    const course = checks.course || {};
    const rows = [];
    const notAssessed = [];

    const row = (state, label, text) =>
        '<li class="trust-check is-' + state + '">'
        + '<span class="trust-check-label">' + _trustEscape(label) + '</span>'
        + '<span class="trust-check-text">' + _trustEscape(text) + '</span></li>';

    // --- Fact check ---
    const factCourse = course.fact;
    if (!factCourse || factCourse.state === 'absent') {
        notAssessed.push('fact check');
    } else if (per.fact && per.fact.state === 'fail') {
        rows.push(row('fail', 'Fact check',
            per.fact.remaining === 1
                ? 'One claim here was confirmed false and survived the rewrite'
                : per.fact.remaining + ' claims here were confirmed false and survived the rewrite'));
    } else if (per.fact) {
        rows.push(row('caution', 'Fact check',
            'A false claim was found and this concept was rewritten; the re-check could not confirm the fix'));
    } else if (factCourse.sampled) {
        // The checker reads a SAMPLE of the course. Silence about this concept
        // is not a clean bill of health, and must not be worded as one.
        rows.push(row('unknown', 'Fact check',
            'Not flagged, but only ' + factCourse.checked + ' of ' +
            factCourse.total + ' concepts in this course were checked'));
    } else {
        rows.push(row('pass', 'Fact check', 'No false claims found in this concept'));
    }

    // --- Depth contract ---
    const depthCourse = course.depth;
    if (!depthCourse || depthCourse.state === 'absent') {
        notAssessed.push('depth contract');
    } else if (per.depth && per.depth.state === 'fail') {
        const problems = (per.depth.problems || []).join('; ');
        rows.push(row('fail', 'Depth',
            problems || 'This concept missed the depth contract for its level'));
    } else if (depthCourse.partial) {
        rows.push(row('unknown', 'Depth',
            'Depth was verified for ' + depthCourse.verified + ' of ' +
            depthCourse.total + ' concepts in this course'));
    } else {
        rows.push(row('pass', 'Depth', 'Meets the depth contract for this level'));
    }

    // --- Sections the model never wrote ---
    // Unlike the two above, absence from the build's map is a POSITIVE result:
    // only concepts with a gap are listed, so "not in the map" means every
    // required heading was written. The exception is a resumed build, which
    // only measured its own concepts — then silence really is silence.
    const sectionsCourse = course.sections;
    if (!sectionsCourse || sectionsCourse.state === 'absent') {
        notAssessed.push('completeness check');
    } else if (per.sections && per.sections.state === 'fail') {
        const missing = (per.sections.missing || []).join(', ');
        rows.push(row('fail', 'Sections',
            (missing ? 'Never written: ' + missing : per.sections.count + ' required sections were never written')
            + (sectionsCourse.stub_injection
                ? ' — placeholder text was written in their place' : '')));
    } else if (sectionsCourse.partial) {
        rows.push(row('unknown', 'Sections',
            'Completeness was measured for ' + sectionsCourse.measured + ' of ' +
            sectionsCourse.total + ' concepts in this course'));
    } else {
        rows.push(row('pass', 'Sections', 'Every required section was written'));
    }

    if (notAssessed.length) {
        rows.push('<li class="trust-check is-unknown">'
            + '<span class="trust-check-label">Not assessed</span>'
            + '<span class="trust-check-text">This course was built without a '
            + _trustEscape(notAssessed.join(' or ')) + '</span></li>');
    }
    return rows.join('');
}

/* Remembers the last concept + state rendered, so the 2-second state poll does
 * not rebuild this DOM (and re-collapse a panel the learner just opened) on
 * every tick. */
let _trustLastKey = null;

function updateTrustSurface(state) {
    const wrap = document.getElementById('trust-surface');
    if (!wrap) return;   // not the learn page

    const node = (state && state.graph_node) || null;
    const inSession = !!(state && state.current_lesson_uid);

    if (!node || !inSession) {
        wrap.classList.add('hidden');
        _trustLastKey = null;
        return;
    }

    const trust = parseTrustFromMarkdown(node.text);
    const checks = _conceptChecks(state.current_lesson_uid);
    const grounding = _classifyGrounding(trust, node);
    const surfaceState = _escalateForChecks(grounding, checks);
    const checksHtml = _renderTrustChecks(checks);

    // If the markdown carried no trust information at all AND the FSM has no
    // confidence for this concept, say nothing rather than inventing a verdict.
    // An absent Sources block is not evidence of an unsourced concept — it can
    // equally mean the concept markdown has not loaded yet.
    //
    // A build verdict counts as a signal in its own right: it is recorded per
    // concept at build time and does not depend on the markdown having arrived.
    const hasAnySignal = trust.sources.length > 0
        || trust.confidence !== null
        || trust.limitedMarker
        || typeof node.source_confidence === 'number'
        || !!node.llm_fallback
        || !!checksHtml;
    if (!hasAnySignal) {
        wrap.classList.add('hidden');
        _trustLastKey = null;
        return;
    }

    const key = (state.current_lesson_uid || '') + '|' + surfaceState + '|'
        + trust.sources.length + '|' + checksHtml.length;
    if (key === _trustLastKey) return;
    _trustLastKey = key;

    const copy = TRUST_COPY[surfaceState];
    wrap.dataset.state = surfaceState;
    wrap.classList.remove('hidden');

    const labelEl = document.getElementById('trust-label');
    if (labelEl) labelEl.textContent = copy.label;

    const countEl = document.getElementById('trust-count');
    if (countEl) {
        const n = trust.sources.length;
        countEl.textContent = n
            ? '· ' + n + ' source' + (n === 1 ? '' : 's')
            : '· no sources cited';
    }

    const caveatEl = document.getElementById('trust-caveat');
    if (caveatEl) caveatEl.textContent = copy.caveat;

    // The build's other verdicts sit between the caveat and the reference list:
    // they are the same kind of thing as the grounding score in the header, and
    // belong beside it rather than in a second component elsewhere on the page.
    const checksEl = document.getElementById('trust-checks');
    if (checksEl) {
        checksEl.innerHTML = checksHtml;
        checksEl.classList.toggle('hidden', !checksHtml);
    }

    const listEl = document.getElementById('trust-sources');
    if (listEl) {
        if (!trust.sources.length) {
            listEl.innerHTML = '<li class="trust-empty">No references were recorded for this concept.</li>';
        } else {
            listEl.innerHTML = trust.sources.map(src => {
                const typeLabel = TRUST_TYPE_LABELS[src.type]
                    || (src.type ? src.type.charAt(0).toUpperCase() + src.type.slice(1) : 'Source');
                const safeUrl = _trustSafeUrl(src.url);
                // rel=noopener: these are third-party URLs from web search.
                const titleHtml = safeUrl
                    ? '<a href="' + _trustEscape(safeUrl) + '" target="_blank" rel="noopener noreferrer">'
                        + _trustEscape(src.title) + '</a>'
                    : '<span>' + _trustEscape(src.title) + '</span>';
                const tierHtml = src.tier
                    ? '<span class="trust-tier" title="Source quality tier recorded at build time">Tier '
                        + _trustEscape(src.tier) + '</span>'
                    : '';
                return '<li class="trust-source"><span class="trust-type">'
                    + _trustEscape(typeLabel) + '</span>' + titleHtml + tierHtml + '</li>';
            }).join('');
        }
    }

    // Weak grounding opens itself. The learner should not have to go looking for
    // the one state that actually changes how they ought to read the page; the
    // other two stay collapsed so evidence never crowds out the concept.
    //
    // A concept-level check FAILURE opens it for the same reason. A caution or
    // a partial-coverage note does not: those are true of most concepts in most
    // courses, and a panel that is always open is a panel nobody reads.
    const perConcept = checks ? checks.concept : {};
    const hardFail = (perConcept.fact && perConcept.fact.state === 'fail')
        || (perConcept.depth && perConcept.depth.state === 'fail')
        || (perConcept.sections && perConcept.sections.state === 'fail');
    setTrustExpanded(surfaceState === 'unsourced' || surfaceState === 'flagged' || !!hardFail);
}

function setTrustExpanded(expanded) {
    const toggle = document.getElementById('trust-toggle');
    const detail = document.getElementById('trust-detail');
    if (!toggle || !detail) return;
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    detail.classList.toggle('hidden', !expanded);
}

function setupTrustSurface() {
    const toggle = document.getElementById('trust-toggle');
    if (!toggle) return;
    toggle.addEventListener('click', () => {
        setTrustExpanded(toggle.getAttribute('aria-expanded') !== 'true');
    });
}

// Exposed for learn.html, which clears the panel on concept navigation so a
// stale verdict can never sit above a newly-opened concept.
window.updateTrustSurface = updateTrustSurface;
window.resetTrustSurface = function () {
    _trustLastKey = null;
    const wrap = document.getElementById('trust-surface');
    if (wrap) wrap.classList.add('hidden');
};

// Markdown to HTML renderer for chat messages.
// Supports: headers, bold, italic, inline code, fenced code blocks,
// bulleted lists, numbered lists, paragraphs. Escapes HTML first.
function renderMarkdown(text) {
    if (!text) return '';
    let s = String(text);

    // 0. Extract LaTeX math FIRST — before HTML-escape and markdown transforms,
    //    since TeX contains <, >, &, _, *, ^ that those steps would mangle. Render
    //    with KaTeX if it's loaded (offline vendor), else fall back to the raw TeX
    //    so equations are at least readable. Restored at the very end (step 8).
    const mathSpans = [];
    const pushMath = (tex, display) => {
        let html = null;
        if (window.katex && typeof window.katex.renderToString === 'function') {
            try { html = window.katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false }); }
            catch (e) { html = null; }
        }
        if (html === null) {
            const esc = tex.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            html = display ? '<pre class="math-raw">' + esc + '</pre>' : '<code class="math-raw">' + esc + '</code>';
        }
        return '\x00MATH' + (mathSpans.push(html) - 1) + '\x00';
    };
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => pushMath(t.trim(), true));   // $$ block $$
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => pushMath(t.trim(), true));   // \[ block \]
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => pushMath(t.trim(), false));  // \( inline \)
    // $ inline $ — only when it looks like LaTeX (has \ ^ _ { }), so currency ($5) is left alone.
    s = s.replace(/\$([^\$\n]+?)\$/g, (m, t) => /[\\^_{}]/.test(t) ? pushMath(t.trim(), false) : m);

    // 1. Escape HTML entities
    s = s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // 2. Extract fenced code blocks ``` first so their contents aren't
    //    touched by the other transforms. Replaced with a placeholder.
    const codeBlocks = [];
    s = s.replace(/```(\w+)?\n?([\s\S]*?)```/g, (_, lang, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push('<pre><code>' + code.replace(/\n$/, '') + '</code></pre>');
        return `\x00CB${idx}\x00`;
    });

    // 2b. Images ![alt](url) — only safe schemes (data:image, http(s),
    //     site-relative). Unsafe/foreign schemes are dropped to alt text.
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (m, alt, url) {
        var safe = /^(data:image\/|https?:\/\/|\/static\/|\/|\.\/|assets\/)/i.test(url);
        if (!safe) return alt;
        var safeAlt = alt.replace(/"/g, '&quot;');
        return '<img class="chat-md-img" loading="lazy" alt="' + safeAlt + '" src="' + url + '">';
    });

    // 3. Headers — render as bold paragraphs (no h1/h2 visual weight
    //    inside chat bubbles — keeps spacing tight).
    s = s.replace(/^#{1,6}\s+(.+)$/gm, '<strong>$1</strong>');

    // 4. Bold **text** and italic *text* / _text_
    s = s.replace(/\*\*([^\n*]+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(?<!\w)\*([^\n*]+?)\*(?!\w)/g, '<em>$1</em>');
    s = s.replace(/(?<!\w)_([^\n_]+?)_(?!\w)/g, '<em>$1</em>');

    // 5. Inline code `text`
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');

    // 6. Lists — detect consecutive lines starting with -, *, or N.
    const lines = s.split('\n');
    const out = [];
    let inUL = false, inOL = false, paraBuf = [];
    const flushPara = () => {
        if (paraBuf.length) {
            out.push('<p>' + paraBuf.join('<br>') + '</p>');
            paraBuf = [];
        }
    };
    const closeLists = () => {
        if (inUL) { out.push('</ul>'); inUL = false; }
        if (inOL) { out.push('</ol>'); inOL = false; }
    };
    for (const line of lines) {
        const ulMatch = /^[-*]\s+(.+)$/.exec(line);
        const olMatch = /^\d+\.\s+(.+)$/.exec(line);
        if (ulMatch) {
            flushPara();
            if (inOL) { out.push('</ol>'); inOL = false; }
            if (!inUL) { out.push('<ul>'); inUL = true; }
            out.push('<li>' + ulMatch[1] + '</li>');
        } else if (olMatch) {
            flushPara();
            if (inUL) { out.push('</ul>'); inUL = false; }
            if (!inOL) { out.push('<ol>'); inOL = true; }
            out.push('<li>' + olMatch[1] + '</li>');
        } else if (line.trim() === '') {
            flushPara();
            closeLists();
        } else {
            closeLists();
            paraBuf.push(line);
        }
    }
    flushPara();
    closeLists();
    s = out.join('');

    // 7. Restore fenced code blocks safely escaping $ replacement patterns
    s = s.replace(/\x00CB(\d+)\x00/g, (_, i) => (codeBlocks[Number(i)] || '').replace(/\$/g, '$$$$'));

    // 8. Restore KaTeX-rendered math safely
    s = s.replace(/\x00MATH(\d+)\x00/g, (_, i) => (mathSpans[Number(i)] || '').replace(/\$/g, '$$$$'));
    return s;
}

// Play TTS audio for a message (with caching via browser)
window._activeAudio = null;
const _TTS_PLAY_SVG = '<svg class="tts-icon-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
const _TTS_STOP_SVG = '<svg class="tts-icon-stop" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12"/></svg>';
const _TTS_LOADING_HTML = '<span class="tts-btn-spinner" aria-hidden="true"></span>';

async function playMessageTTS(text, btn) {
    if (!text) return;

    // Stop any currently playing audio
    if (window._activeAudio) {
        try { window._activeAudio.pause(); } catch(e){}
        window._activeAudio = null;
        document.querySelectorAll('.tts-play-btn.active, .tts-play-btn.loading').forEach(b => {
            b.classList.remove('active', 'loading');
            b.innerHTML = _TTS_PLAY_SVG;
        });
    }

    // Immediate loading state — the TTS generate + blob fetch typically
    // takes 1-3 seconds, long enough that users think the button is broken
    // without visible feedback. The `.loading` class drives a CSS spinner
    // on the button background so the whole button looks "working".
    btn.classList.add('loading');
    btn.innerHTML = _TTS_LOADING_HTML;

    try {
        const voice = localStorage.getItem('helga-voice') || 'af_heart';
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: text, voice: voice})
        });
        if (!resp.ok) throw new Error('TTS failed');
        const blob = await resp.blob();
        const audio = new Audio(URL.createObjectURL(blob));
        window._activeAudio = audio;
        // Swap to the stop icon once playback actually starts
        btn.classList.remove('loading');
        btn.classList.add('active');
        btn.innerHTML = _TTS_STOP_SVG;
        audio.onended = function() {
            btn.classList.remove('active');
            btn.innerHTML = _TTS_PLAY_SVG;
            window._activeAudio = null;
        };
        audio.play();
    } catch (e) {
        console.warn('TTS error:', e);
        btn.classList.remove('loading', 'active');
        btn.innerHTML = _TTS_PLAY_SVG;
        if (typeof window.showToast === 'function') {
            window.showToast('TTS unavailable right now', 'error');
        }
    }
}

// Load user avatar from profile settings
async function loadUserAvatar() {
    try {
        const r = await fetch('/api/profile');
        if (r.ok) {
            const profile = await r.json();
            if (profile.avatar_url && profile.avatar_url.trim()) {
                window._userAvatarSrc = profile.avatar_url;
            }
        }
    } catch(e) { /* use default */ }
}
loadUserAvatar();

// Track whether the user has scrolled up to read history — used so new
// messages don't yank them back to the bottom mid-read. Re-enabled when
// they scroll back near the bottom themselves.
let _userPinnedToBottom = true;

function _attachChatScrollTracker(chatStream) {
    if (!chatStream || chatStream.dataset.scrollTrackerBound === '1') return;
    chatStream.dataset.scrollTrackerBound = '1';
    chatStream.addEventListener('scroll', function () {
        const distanceFromBottom = chatStream.scrollHeight - (chatStream.scrollTop + chatStream.clientHeight);
        // Within 80px of the bottom counts as "pinned" — feels natural.
        _userPinnedToBottom = distanceFromBottom < 80;
        const btn = document.getElementById('chat-scroll-bottom-btn');
        if (btn) btn.classList.toggle('visible', !_userPinnedToBottom);
    }, { passive: true });

    // Wire the jump-to-latest button once — it lives next to the chat list
    // in learn.html.
    const jumpBtn = document.getElementById('chat-scroll-bottom-btn');
    if (jumpBtn && jumpBtn.dataset.bound !== '1') {
        jumpBtn.dataset.bound = '1';
        jumpBtn.addEventListener('click', function () {
            chatStream.scrollTop = chatStream.scrollHeight;
            _userPinnedToBottom = true;
            jumpBtn.classList.remove('visible');
        });
    }
}

function _scrollChatToBottom(chatStream, force) {
    if (!chatStream) return;
    if (force || _userPinnedToBottom) {
        chatStream.scrollTop = chatStream.scrollHeight;
    }
}

function updateChatStream(transcript) {
    if (!transcript) {
        console.warn("Transcript is null or undefined. Skipping chat update.");
        return;
    }

    const chatStream = document.getElementById('chat-stream');
    if (!chatStream) {
        console.error("[updateChatStream] chat-stream element NOT FOUND");
        return;
    }
    _attachChatScrollTracker(chatStream);
    console.log("[updateChatStream] Current transcript length:", transcript.length, "displayedCount:", displayedMessagesCount);

    // Helper: wipe the chat stream while preserving transient elements
    // that represent in-flight LLM work (the thinking bubble inserted by
    // enterNode() and any streaming bubble receiving stream_token events).
    // Without this guard, the nav-guard clearance step below would yank
    // the dots animation off the screen mid-generation, leaving the user
    // staring at an empty chat until the canonical message arrives.
    const _preserveInFlightElements = function () {
        const keep = [];
        chatStream.querySelectorAll('.thinking-message, .stream-bubble').forEach(function (el) {
            keep.push(el);
        });
        while (chatStream.firstChild) chatStream.removeChild(chatStream.firstChild);
        keep.forEach(function (el) { chatStream.appendChild(el); });
    };

    // Navigation guard — prevents transcript leakage between concepts/courses.
    //
    // When enterNode() is called, it sets window.navigatingToNode = true and
    // sends NAVIGATE_TO_TOPIC to the FSM. The FSM clears its transcript at
    // the start of navigate_to_topic() and then runs an LLM call (slow) to
    // generate the opening question. During this window, polling may still
    // return the PREVIOUS concept's transcript — we must not render it.
    //
    // Two-phase guard:
    //   Phase A (clearanceSeen === false): suppress ALL updates until we
    //     observe a transcript of length 0 OR a known boot message. Seeing
    //     that is proof the FSM has wiped its state.
    //   Phase B (clearanceSeen === true): accept new content, render it,
    //     and drop the guard.
    if (window.navigatingToNode) {
        const isBoot = transcript.length <= 1 && (
            transcript.length === 0 ||
            (transcript[0].text || '').toLowerCase().includes('mnemosyne online') ||
            (transcript[0].text || '').toLowerCase().includes('helga online')
        );

        if (!window._navClearanceSeen) {
            if (isBoot) {
                // FSM has cleared. Mark clearance and keep chat blank — next
                // non-boot update will carry the fresh concept content.
                window._navClearanceSeen = true;
                _preserveInFlightElements();
                displayedMessagesCount = 0;
                console.log("[updateChatStream] Nav guard: FSM transcript cleared, waiting for fresh content");
            } else {
                // Still seeing stale content — suppress this update entirely
                console.log("[updateChatStream] Nav guard: suppressing stale transcript (len=" + transcript.length + ")");
            }
            return;
        }

        // Phase B: clearance already seen. Fresh content arriving?
        if (isBoot) {
            // Still empty, still waiting
            return;
        }
        // First non-boot update after clearance — render it fresh
        console.log("[updateChatStream] Nav guard: fresh content arrived, releasing guard");
        window.navigatingToNode = false;
        window._navClearanceSeen = false;
        if (window._navGuardTimeout) clearTimeout(window._navGuardTimeout);
        _preserveInFlightElements();
        displayedMessagesCount = 0;

        // Re-enable the text input now that the new concept is ready
        const textInputEl = document.getElementById('text-input');
        if (textInputEl) {
            textInputEl.disabled = false;
            textInputEl.placeholder = 'Type your answer…';
        }
    }

    // Reset chat on transcript length mismatch
    if (transcript.length < displayedMessagesCount) {
        console.log("Transcript reset detected. Clearing chat stream.");
        _preserveInFlightElements();
        displayedMessagesCount = 0;
    }

    // B13: keep already-rendered aid cards in step with the poll. updateChatStream
    // only builds messages it has not seen, so a tutor-driven reveal — the FSM
    // advancing an aid's stage after the learner answers — arrives as a change to
    // an OLD transcript entry and would otherwise never reach the screen.
    if (window.HelgaAids) {
        try {
            transcript.forEach(function (m) {
                if (m && m.aids && m.aids.length) window.HelgaAids.syncStages(m.aids);
            });
        } catch (e) {
            console.warn('[aids] stage sync failed:', e);
        }
    }

    // Add new messages
    const newMessages = transcript.slice(displayedMessagesCount);
    if (newMessages.length > 0) {
        // Hide hero on first message
        const hero = document.getElementById('chat-hero');
        if (hero && transcript.length > 0) hero.classList.add('hidden');

        // A real message has arrived — any lingering thinking bubble
        // should be removed so the dots don't sit next to the answer.
        const staleThinking = chatStream.querySelectorAll('.thinking-message');
        staleThinking.forEach(b => { try { b.remove(); } catch (e) {} });

        // Also remove any optimistic user bubbles — the canonical transcript
        // version (with grade badge, etc.) takes over.
        const optimistic = chatStream.querySelectorAll('.chat-msg[data-optimistic="true"]');
        optimistic.forEach(b => { try { b.remove(); } catch (e) {} });

        // Helper: plain HTML escape for user-typed content (no markdown)
        const escapeUserText = (s) => String(s || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        newMessages.forEach((message, idx) => {
            const isAI = (message.sender === 'ai' || message.sender === 'helga');
            const isUser = message.sender === 'user';

            // Concept-transition dividers are now inserted by the CPROG
            // handler in learn.html, which is authoritative (title change
            // directly from the FSM). Relying on AI text prefix matches
            // here double-inserted dividers and broke when the LLM phrased
            // transitions differently.
            //
            // Detect "Resuming <title>" / "We were discussing" — these are
            // FSM-generated strings from resume_course() that mark a
            // resumed session. Insert a subtle resume marker so the user
            // gets a clear "picked up where you left off" signal.
            if (isAI && message.text && /^(resuming |we were discussing )/i.test(message.text.trim())) {
                // Only insert once per resume event (idx 0 of the new batch
                // guarantees we're at the boundary).
                const alreadyMarked = chatStream.querySelector('.chat-resume-marker:last-child');
                if (!alreadyMarked || idx === 0) {
                    const resumeEl = document.createElement('div');
                    resumeEl.className = 'chat-resume-marker';
                    resumeEl.innerHTML =
                        '<span class="chat-resume-marker-icon">↺</span>' +
                        '<span>Picked up where you left off</span>';
                    chatStream.appendChild(resumeEl);
                }
            }

            const messageDiv = document.createElement('div');
            messageDiv.className = `chat-msg ${isAI ? 'incoming' : 'outgoing'}`;
            messageDiv.dataset.index = displayedMessagesCount + idx;

            // Avatar
            const avatarSrc = isAI
                ? '/static/img/helga-avatar.svg'
                : (window._userAvatarSrc || '/static/img/user-avatar.svg');
            const avatarAlt = isAI ? 'Helga' : 'You';
            const fallbackAvatar = isAI ? 'helga-avatar.svg' : 'user-avatar.svg';

            // Grade — attached to the USER message that was graded, not to the
            // AI's follow-up, so the verdict sits on the thing being evaluated.
            //
            // Shown as the COLOUR OF THE BUBBLE rather than a text badge: the
            // verdict becomes a property of the message instead of a label
            // competing with the learner's own words for the same line.
            //
            // The word itself is kept for assistive tech and as a tooltip.
            // Colour alone must never be the only carrier of meaning (WCAG
            // 1.4.1), and "your answer was red" is useless to a screen reader.
            let gradeBadge = '';
            if (isUser && message.grade) {
                const gradeMap = {1: ['Needs work', 'g1'], 2: ['Getting there', 'g2'], 3: ['Good', 'g3'], 4: ['Excellent', 'g4']};
                const [label, cls] = gradeMap[message.grade] || ['', ''];
                if (label) {
                    gradeBadge = `<span class="chat-msg-grade-badge sr-only ${cls}">Graded: ${label}</span>`;
                    messageDiv.dataset.grade = String(message.grade);
                    messageDiv.dataset.gradeLabel = label;
                }
            }

            // Action buttons live OUTSIDE the bubble body — positioned to
            // the right of the message so they have their own background
            // instead of blending into the bubble. Only AI messages get
            // TTS + copy; user messages don't need them.
            const actions = isAI ? `
                <div class="chat-msg-actions-side">
                    <button class="chat-msg-action-btn tts-play-btn" title="Play audio" aria-label="Play audio">
                        <svg class="tts-icon-play" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                    </button>
                    <button class="chat-msg-action-btn copy-btn" title="Copy message" aria-label="Copy message">
                        <svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                    </button>
                </div>
            ` : '';

            const rawText = message.text || '';
            // AI messages render markdown (bold/italic/lists/code).
            // User messages are escaped only — rendering user text as
            // markdown leads to surprising behavior when users type
            // asterisks or underscores in answers.
            const renderedText = isAI
                ? renderMarkdown(rawText)
                : escapeUserText(rawText);

            messageDiv.innerHTML = `
                <div class="chat-msg-content">
                    <img class="chat-msg-avatar" src="${avatarSrc}" alt="${avatarAlt}"
                         onerror="this.src='/static/img/${fallbackAvatar}'">
                    <div class="chat-msg-body">
                        ${gradeBadge}
                        <div class="chat-msg-text">${renderedText}</div>
                    </div>
                    ${actions}
                </div>
            `;

            // B13: visual teaching aids render ABOVE the message text, inside
            // the same bubble group. The transcript carries only descriptors;
            // aids.js fetches each spec once and draws SVG.
            if (isAI && message.aids && message.aids.length && window.HelgaAids) {
                try {
                    window.HelgaAids.attach(messageDiv, message.aids);
                } catch (e) {
                    console.warn('[aids] attach failed:', e);   // never blocks the message
                }
            }

            // The grade word, reachable on hover as well as by screen reader.
            if (isUser && messageDiv.dataset.gradeLabel) {
                const bodyEl = messageDiv.querySelector('.chat-msg-body');
                if (bodyEl) bodyEl.title = 'Graded: ' + messageDiv.dataset.gradeLabel;
            }

            // Wire TTS button
            if (isAI) {
                const ttsBtn = messageDiv.querySelector('.tts-play-btn');
                if (ttsBtn) {
                    ttsBtn.addEventListener('click', function() {
                        // A learner working by ear must not simply lose the
                        // diagram — the description is spoken after the message
                        // (B13.9).
                        let speech = rawText;
                        if (message.aids && message.aids.length && window.HelgaAids) {
                            const aidSpeech = window.HelgaAids.speechFor(message.aids);
                            if (aidSpeech) speech = rawText + ' ' + aidSpeech;
                        }
                        playMessageTTS(speech, ttsBtn);
                    });
                }
                const copyBtn = messageDiv.querySelector('.copy-btn');
                if (copyBtn) {
                    copyBtn.addEventListener('click', function() {
                        navigator.clipboard.writeText(rawText).then(function() {
                            copyBtn.classList.add('active');
                            setTimeout(function() { copyBtn.classList.remove('active'); }, 1000);
                        });
                    });
                }
            }

            chatStream.appendChild(messageDiv);
        });
        displayedMessagesCount = transcript.length;

        // Only follow the stream if the user hasn't scrolled up to read.
        // Navigation transitions (enterNode) force a scroll via their own
        // call so fresh concepts always start at the top of the new chat.
        _scrollChatToBottom(chatStream, false);
    }
}


function handleThinkingUpdate(data) {
    const message = data.message;
    const logEntry = data.log;

    // Normalize message (strip LOG: prefix)
    let displayMessage = message || '';
    if (displayMessage.startsWith('LOG: ')) displayMessage = displayMessage.substring(5);
    else if (displayMessage.startsWith('LOG:')) displayMessage = displayMessage.substring(4);

    // Translate internal phase names into something a user would
    // understand. The FSM emits "Reviewing History...", "Mode: QUESTION...",
    // "Formulating Response..." etc — pipeline details the user doesn't
    // need. Map them all to a single friendly "Thinking" state with a
    // short stage hint. If the message doesn't match a known phase, we
    // drop it on the floor rather than showing raw backend logs in chat.
    const phaseHints = [
        [/reviewing history/i,       'Reading your last answer'],
        [/mode:/i,                   'Choosing teaching mode'],
        [/formulating/i,             'Writing a response'],
        [/analyzing context/i,       'Loading concept content'],
        [/preparing pedagog/i,       'Picking a question angle'],
        [/grading/i,                 'Grading your answer'],
        [/fetching pedagog/i,        'Loading teaching notes'],
        [/generating question/i,     'Writing a question'],
        [/generating bridge/i,       'Connecting to the next concept'],
    ];
    let userFacingHint = null;
    for (const [pat, hint] of phaseHints) {
        if (pat.test(displayMessage)) { userFacingHint = hint; break; }
    }

    // Only show the thinking bubble when progress is actively being
    // reported by a known pipeline stage. Skip unrelated updates (STRUCT,
    // CHECK, CPROG, PEDAGOGY, QTYPE, etc.) — those have their own
    // handlers and should never paint a thinking bubble in the chat.
    if (data.progress !== undefined && userFacingHint) {
        const chatStream = document.getElementById('chat-stream');

        // Force session view open if needed (e.g. creation hand-off)
        const sessionView = document.getElementById('session-view');
        if (sessionView && sessionView.classList.contains('hidden')) {
            sessionView.classList.remove('hidden');
            const pv = document.getElementById('path-view');
            if (pv) pv.classList.add('hidden');
            currentMode = 1;
            toggleRails(1);
        }

        if (chatStream) {
            // Reuse an existing thinking bubble, or clone a fresh one.
            let bubble = chatStream.querySelector('.thinking-message');
            if (!bubble && data.progress < 100) {
                const template = document.getElementById('thinking-bubble-template');
                if (template) {
                    const frag = template.content.cloneNode(true);
                    chatStream.appendChild(frag);
                    bubble = chatStream.querySelector('.thinking-message');
                    // Hide the hero the moment we start thinking
                    const hero = document.getElementById('chat-hero');
                    if (hero) hero.classList.add('hidden');
                    _scrollChatToBottom(chatStream, false);
                }
            }

            if (bubble) {
                // Update the subtle stage label under the dots.
                const labelEl = bubble.querySelector('.thinking-label-text');
                if (labelEl) labelEl.textContent = userFacingHint;

                // Remove the bubble entirely when the pipeline completes —
                // the real answer arrives via the transcript shortly after.
                if (data.progress >= 100) {
                    try { bubble.remove(); } catch (e) {}
                }
            }
        }
    }

    // Check for educational status prefixes (STRUCT, CHECK, SYLLABUS, ERROR)
    if (message && (
        message.startsWith('STRUCT:') ||
        message.startsWith('CHECK:') ||
        message.startsWith('SYLLABUS:') ||
        message.startsWith('ERROR:') ||
        message.startsWith('LOG:')
    )) {
        addProgressLog(displayMessage);
    }

    // Check for course creation messages
    if (message && message.startsWith('Creating course on ')) {
        const topic = message.replace('Creating course on ', '');
        showCreationProgressModal(topic);
        return;
    }

    // Parse JSON log entries for step extraction
    let jsonLog = null;
    if (logEntry) {
        try {
            jsonLog = JSON.parse(logEntry);
        } catch (e) {
            // Not JSON, treat as plain text
            jsonLog = null;
        }
    }

    // Extract step from JSON log if available
    if (jsonLog && jsonLog.step) {
        const stepMap = {
            'prepare': { percent: 0, detail: 'Stopping services and preparing database...' },
            'scrape': { percent: 20, detail: 'Downloading educational content...' },
            'chunk': { percent: 40, detail: 'Chunking content into segments...' },
            'graph': { percent: 60, detail: 'Organizing concepts and relationships...' },
            'finalize': { percent: 80, detail: 'Preparing for learning...' },
            'restart': { percent: 100, detail: 'Restarting services and verifying...' }
        };

        if (stepMap[jsonLog.step]) {
            const stepInfo = stepMap[jsonLog.step];
            updateProgressStep(jsonLog.step, stepInfo.percent, stepInfo.detail);
        }
    }

    // Update progress bar during creation with new 6-step flow
    if (message === 'Preparing database...') {
        updateProgressStep('prepare', 0, 'Stopping services and preparing database...');
        addProgressLog('Preparing database...');
    } else if (message === 'Scraping ZIM files...') {
        updateProgressStep('scrape', 20, 'Downloading educational content...');
        addProgressLog('Scraping ZIM files...');
    } else if (message === 'Vectorizing content...') {
        updateProgressStep('chunk', 40, 'Chunking content into segments...');
        addProgressLog('Vectorizing content...');
    } else if (message === 'Building graph...') {
        updateProgressStep('graph', 60, 'Organizing concepts and relationships...');
        addProgressLog('Building graph...');
    } else if (message === 'Finalizing course...') {
        updateProgressStep('finalize', 80, 'Preparing for learning...');
        addProgressLog('Finalizing course...');
    } else if (displayMessage === 'Restarting Systems...' || displayMessage === 'Restarting services...') {
        updateProgressStep('restart', 100, 'Restarting services and verifying...');
        addProgressLog('Restarting services...');
    } else if (message === 'Course built successfully!' || message === 'Course ready to start!' || message === 'Course ready!') {
        updateProgressStep('complete', 100, 'Course is ready!');
        addProgressLog('<span class="i i-check" aria-hidden="true"></span> Course built successfully!');
        setTimeout(() => {
            hideCourseCreationModal();
            isCreatingCourse = false;
            enableCourseCreationButton();
        }, 2000); // Hide after 2 seconds
    } else if (message && (message.includes('Ingestion failed') || message.includes('Ingestion error') || message.includes('Service restart failed'))) {
        creationStatus.innerHTML =
            '<span class="i i-x i-danger" aria-hidden="true"></span> ' + escapeHtml(message);
        progressFill.style.width = '0%';
        addProgressLog('<span class="i i-x" aria-hidden="true"></span> ' + message);
        setTimeout(() => {
            hideCourseCreationModal();
            isCreatingCourse = false;
            enableCourseCreationButton();
        }, 3000);
    }

    // Always add logEntry to progress log if present
    if (logEntry) {
        console.log('[handleThinkingUpdate] Adding log entry:', logEntry);

        // Color-code based on log level
        let logClass = 'log-entry';
        if (jsonLog) {
            if (jsonLog.level === 'ERROR') logClass = 'log-entry log-error';
            else if (jsonLog.level === 'WARN') logClass = 'log-entry log-warn';
            else if (jsonLog.level === 'INFO') logClass = 'log-entry log-info';
            else if (jsonLog.level === 'DEBUG') logClass = 'log-entry log-debug';
        }

        // Add to course creation logs if that's active
        addProgressLog(logEntry, logClass);

        // Add to global logs (if we keep them, otherwise just inline)
        // thinkingLogsCode.textContent += logEntry + '\n'; // LEGACY
    }

    // Scroll chat stream (respect user scroll position)
    const chatStream = document.getElementById('chat-stream');
    _scrollChatToBottom(chatStream, false);
}




function sendEvent(eventType, payload) {
    console.log("[sendEvent] Sending event:", { type: eventType, payload });
    const requestBody = { type: eventType, payload: payload };
    console.log('[sendEvent] Request body:', JSON.stringify(requestBody));

    fetch('/api/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
    })
        .then(response => {
            console.log('[sendEvent] Response status:', response.status, response.statusText);
            if (!response.ok) {
                throw new Error(`Server returned ${response.status}: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            console.log('[sendEvent] Server response to event:', data);
        })
        .catch(error => {
            console.error('[sendEvent] Error sending event:', error);
            // Visual feedback for the user — use the same bubble structure as
            // real messages so the error is styled correctly in the Gemini
            // chat layout (the old .message.ai class had no CSS on /learn).
            const chatStream = document.getElementById('chat-stream');
            if (chatStream) {
                // Remove any stale thinking bubble so the error replaces it
                const stale = chatStream.querySelectorAll('.thinking-message');
                stale.forEach(b => { try { b.remove(); } catch (e) {} });

                const errorDiv = document.createElement('div');
                errorDiv.className = 'chat-msg incoming error';
                errorDiv.innerHTML =
                    '<div class="chat-msg-content">' +
                    '  <img class="chat-msg-avatar" src="/static/img/helga-avatar.svg" alt="Helga">' +
                    '  <div class="chat-msg-body">' +
                    '    <div class="chat-msg-text"></div>' +
                    '  </div>' +
                    '</div>';
                errorDiv.querySelector('.chat-msg-text').textContent =
                    'Could not reach the server — ' + (error.message || 'unknown error') + '. Check that the core service is running, then try again.';
                chatStream.appendChild(errorDiv);
                chatStream.scrollTop = chatStream.scrollHeight;

                // Also drop the navigation guard so the next interaction
                // isn't silently suppressed by updateChatStream().
                if (window.navigatingToNode) {
                    window.navigatingToNode = false;
                    window._navClearanceSeen = false;
                    if (window._navGuardTimeout) {
                        clearTimeout(window._navGuardTimeout);
                        window._navGuardTimeout = null;
                    }
                    const textInputEl = document.getElementById('text-input');
                    if (textInputEl) {
                        textInputEl.disabled = false;
                        textInputEl.placeholder = 'Type your answer…';
                    }
                }
            }
            if (typeof window.showToast === 'function') {
                // A learner is not debugging our stack. "Server returned 502:
                // BAD GATEWAY" tells them nothing they can act on and reads as
                // a crash; what they need to know is whether their work is
                // safe and whether to retry. The raw status stays in the
                // console for us.
                window.showToast(
                    "Helga's tutor service isn't responding. Your progress is " +
                    "saved — try again in a moment.", 'error');
            }
        });
}

// --- Voice input (speech-to-text) ---------------------------------------
// Push-to-talk: click the mic to start, click again to stop -> POST audio to
// /api/stt -> drop the transcript into the input and send via the normal
// TEXT_INPUT path. Engine-agnostic (server picks Nemotron-MLX / faster-whisper).
let _mediaRecorder = null;
let _recordedChunks = [];
let _recording = false;

function _pickAudioMime() {
    if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
    return candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
}

function setupVoiceInput() {
    const micBtn = document.getElementById('mic-btn');
    if (!micBtn) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
        micBtn.style.display = 'none';  // browser can't record — hide the affordance
        return;
    }
    micBtn.addEventListener('click', () => {
        if (_recording) stopVoiceRecording(); else startVoiceRecording();
    });
}

async function startVoiceRecording() {
    const micBtn = document.getElementById('mic-btn');
    try {
        // Barge-in: stop any in-flight TTS playback the moment the user speaks.
        if (window._activeAudio) { try { window._activeAudio.pause(); } catch (e) {} }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mime = _pickAudioMime();
        _mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        _recordedChunks = [];
        _mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) _recordedChunks.push(e.data); };
        _mediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(_recordedChunks, { type: (_mediaRecorder && _mediaRecorder.mimeType) || 'audio/webm' });
            transcribeAndSend(blob);
        };
        _mediaRecorder.start();
        _recording = true;
        if (micBtn) {
            micBtn.dataset.recording = 'true';
            micBtn.classList.add('recording');
            micBtn.setAttribute('aria-label', 'Stop recording');
            micBtn.title = 'Stop recording';
        }
    } catch (err) {
        console.error('[voice] mic access failed:', err);
        _recording = false;
        if (micBtn) micBtn.classList.remove('recording');
    }
}

function stopVoiceRecording() {
    const micBtn = document.getElementById('mic-btn');
    _recording = false;
    if (micBtn) {
        micBtn.dataset.recording = 'false';
        micBtn.classList.remove('recording');
        micBtn.classList.add('transcribing');
        micBtn.setAttribute('aria-label', 'Transcribing');
        micBtn.title = 'Transcribing…';
    }
    try { if (_mediaRecorder && _mediaRecorder.state !== 'inactive') _mediaRecorder.stop(); } catch (e) {}
}

async function transcribeAndSend(blob) {
    const micBtn = document.getElementById('mic-btn');
    const textInput = document.getElementById('text-input');
    try {
        const resp = await fetch('/api/stt', {
            method: 'POST',
            headers: { 'Content-Type': blob.type || 'audio/webm' },
            body: blob,
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.text && data.text.trim()) {
            if (textInput) {
                textInput.value = data.text.trim();
                textInput.dispatchEvent(new Event('input'));
            }
            sendTextMessage();
        } else {
            console.warn('[voice] no transcript:', (data && data.error) || resp.status);
            if (textInput) textInput.placeholder = (data && data.error) || 'Didn’t catch that — try again';
        }
    } catch (err) {
        console.error('[voice] transcription request failed:', err);
    } finally {
        if (micBtn) {
            micBtn.classList.remove('transcribing');
            micBtn.setAttribute('aria-label', 'Record voice answer');
            micBtn.title = 'Speak your answer';
        }
    }
}

// --- Image input (vision) -----------------------------------------------
// Attach an image (a diagram, or a photo of the student's work); it's sent with
// the next message as a base64 data URI for the multimodal model (qwen3.5:9b) to
// see and discuss Socratically (B13 / Task #12).
function sendTextMessage() {
    const textInput = document.getElementById('text-input');
    const sendBtn = document.getElementById('send-btn');
    const inputWrapper = textInput ? textInput.closest('.learn-chat-input-wrapper') : null;
    const text = textInput ? textInput.value.trim() : '';

    // Input Guard: prevent empty sends or rapid double-submissions.
    if (!text || (sendBtn && sendBtn.disabled) || (textInput && textInput.disabled)) {
        if (textInput) textInput.focus();
        return;
    }

    // Disable briefly to prevent rapid fire
    if (sendBtn) sendBtn.disabled = true;
    if (inputWrapper) inputWrapper.classList.remove('has-content');

    // Optimistic render: show user message immediately (don't wait for 2s state poll).
    // Mark with data-optimistic so we can match it up when the real transcript
    // arrives and avoid double-rendering.
    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        const hero = document.getElementById('chat-hero');
        if (hero) hero.classList.add('hidden');

        // Remove any stale thinking bubble — the user is responding
        const staleThinking = chatStream.querySelectorAll('.thinking-message');
        staleThinking.forEach(b => { try { b.remove(); } catch (e) {} });

        const msgDiv = document.createElement('div');
        msgDiv.className = 'chat-msg outgoing';
        msgDiv.dataset.optimistic = 'true';
        const userAvatar = window._userAvatarSrc || '/static/img/user-avatar.svg';
        const safeText = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        msgDiv.innerHTML = `
            <div class="chat-msg-content">
                <img class="chat-msg-avatar" src="${userAvatar}" alt="You"
                     onerror="this.src='/static/img/user-avatar.svg'">
                <div class="chat-msg-body">
                    <div class="chat-msg-text">${safeText}</div>
                </div>
            </div>
        `;
        chatStream.appendChild(msgDiv);
        // User just sent something — that's a "new turn" moment; snap to
        // bottom unconditionally and re-pin the auto-follow behavior.
        chatStream.scrollTop = chatStream.scrollHeight;
        _userPinnedToBottom = true;
        // Do NOT increment displayedMessagesCount here — the real transcript
        // update will include this message and updateChatStream() will match
        // it against the optimistic bubble.
    }

    sendEvent('TEXT_INPUT', { text: text });
    if (textInput) {
        textInput.value = '';
        // Reset textarea height
        textInput.style.height = '';
    }

    // Re-enable send button after a short debounce
    if (sendBtn) {
        setTimeout(() => { sendBtn.disabled = false; }, 500);
    }
    if (textInput) textInput.focus();

    // After the FSM has had time to grade the answer and award XP
    // (typically ~3-5s), pull the fresh gamification state so the header
    // counter reflects the reward. The FSM awards XP async so we can't
    // rely on the immediate state_update for this value.
    setTimeout(refreshGamificationBarFromSession, 3500);
}

// Pull gamification state and tick the header bar without rebuilding
// anything. Safe to call from any page — the elements are in base.html.
function refreshGamificationBarFromSession() {
    if (localStorage.getItem('helga-gamification') === 'false') return;
    fetch('/api/gamification').then(function(r) { return r.ok ? r.json() : null; }).then(function(data) {
        if (!data) return;
        var xpEl = document.getElementById('xp-counter');
        var lvlEl = document.getElementById('level-badge');
        var streakEl = document.getElementById('streak-counter');
        if (xpEl) {
            var newXp = (data.total_xp || data.xp || 0).toLocaleString() + ' XP';
            if (xpEl.textContent !== newXp) {
                xpEl.textContent = newXp;
                xpEl.classList.remove('xp-bump');
                void xpEl.offsetWidth;
                xpEl.classList.add('xp-bump');
            }
        }
        if (lvlEl) {
            var newLvl = 'Lv ' + (data.level || 1);
            if (lvlEl.textContent !== newLvl) {
                lvlEl.textContent = newLvl;
                // Trigger level-up toast via the existing helper in base.html
                if (typeof window.showLevelUpToast === 'function' && data.level) {
                    // Only show if this isn't the initial fetch — guard via a flag
                    if (window._lastKnownLevel != null && data.level > window._lastKnownLevel) {
                        window.showLevelUpToast(data.level);
                    }
                    window._lastKnownLevel = data.level;
                } else if (window._lastKnownLevel == null) {
                    window._lastKnownLevel = data.level;
                }
            }
        }
        if (streakEl) streakEl.innerHTML = '&#128293; ' + (data.streak_days || data.streak || 0);
    }).catch(function() { /* non-fatal */ });
}


// --- Socket Listener Setup ---
function setupSocketListeners() {
    if (!socket) return;

    socket.on('disconnect', () => {
        console.warn('Socket.IO disconnected.');
    });

    socket.on('connect', () => {
        console.log("WebSocket connected to Web-UI server");
        // Join status_room to receive service health updates
        socket.emit('join_status_room');
    });

    socket.on('health_update', (data) => {
        // Find the "input" service status
        const inputStatus = data.input;
        const statusEmoji = document.getElementById('input-status-indicator');
        if (statusEmoji && inputStatus) {
            // The colour WAS the message here (green / amber / red dots).
            // One grey dot would lose it, so state rides on a class and the
            // icon inherits currentColor.
            const dot = '<span class="i i-dot" aria-hidden="true"></span>';
            statusEmoji.classList.remove('is-online', 'is-degraded', 'is-offline');
            if (inputStatus.status === 'online') {
                statusEmoji.innerHTML = dot;
                statusEmoji.classList.add('is-online');
                statusEmoji.title = 'Input Service: Online (Lat: ' + inputStatus.latency + 'ms)';
            } else if (inputStatus.status === 'degraded') {
                statusEmoji.innerHTML = dot;
                statusEmoji.classList.add('is-degraded');
                statusEmoji.title = 'Input Service: Degraded (Model loading?)';
            } else {
                statusEmoji.innerHTML = dot;
                statusEmoji.classList.add('is-offline');
                statusEmoji.title = 'Input Service: Offline';
            }
        }
    });

    socket.on('state_update', (data) => {
        console.log("State update received via WebSocket:", data);
        updateUI(data);
    });

    socket.on('status_update', (data) => {
        console.log("Status update received via WebSocket:", data);
        handleThinkingUpdate(data);
    });

    // --- Streaming LLM token handler ---
    // Tokens arrive one-by-one from the LLM via core-logic -> web-ui -> here.
    // We accumulate them in a temporary "streaming" bubble. When 'done' is true
    // (or the next full state_update arrives with the final transcript), we
    // finalize the bubble.
    socket.on('stream_token', (data) => {
        handleStreamToken(data);
    });

}

// Voice selector state (element may not exist on the learn page — the
// settings page owns voice selection now).
let voiceSelector = null;

// Event listeners
document.addEventListener('DOMContentLoaded', function () {
    // Initialize UI elements
    thinkingBubble = document.getElementById('thinking-bubble');
    thinkingStatusText = document.getElementById('thinking-status-text');
    toggleLogsBtn = document.getElementById('toggle-logs');
    thinkingLogsContainer = document.getElementById('thinking-logs');
    if (thinkingLogsContainer) thinkingLogsCode = thinkingLogsContainer.querySelector('code');
    voiceSelector = document.getElementById('voice-selector');

    // Course creation modal elements
    courseModal = document.getElementById('course-creation-modal');
    courseForm = document.getElementById('course-creation-form');
    courseTopicInput = document.getElementById('course-topic');
    creationProgress = document.getElementById('creation-progress');
    creationStatus = document.getElementById('creation-status');
    progressFill = document.getElementById('progress-fill');
    closeModalBtn = document.getElementById('close-modal');

    // Fetch and populate voices
    fetchAndPopulateVoices();

    // A5 trust surface expand/collapse (no-op off the learn page)
    setupTrustSurface();

    // Establish WebSocket connection (single io() call for the entire file)
    socket = io();
    window.socket = socket;

    // Setup socket listeners immediately after connection
    setupSocketListeners();

    // --- Static Event Handlers ---

    // Log toggle handler
    if (toggleLogsBtn) {
        toggleLogsBtn.addEventListener('click', () => {
            const isHidden = thinkingLogsContainer.classList.toggle('hidden');
            toggleLogsBtn.classList.toggle('open', !isHidden);
        });
    }

    // Voice selector
    if (voiceSelector) {
        voiceSelector.addEventListener('change', handleVoiceChange);
    }

    // Pause/Resume button — primary purpose is controlling active TTS
    // playback (pause/unpause the voice reading a message). If no TTS
    // audio is active, the button is hidden via the .no-audio class so
    // the user isn't staring at a dead control. We also still forward
    // PAUSE_SESSION to the FSM as a safety net so progress gets saved
    // if the session is left idle.
    const pauseResumeBtn = document.getElementById('pause-resume');
    if (pauseResumeBtn) {
        // Default state: no audio playing → hide the button.
        pauseResumeBtn.classList.add('no-audio');

        pauseResumeBtn.addEventListener('click', function () {
            const audio = window._activeAudio;
            if (audio && !audio.ended) {
                if (audio.paused) {
                    audio.play();
                    this.dataset.paused = 'false';
                } else {
                    audio.pause();
                    this.dataset.paused = 'true';
                }
                return;
            }
            // No active audio — button shouldn't even be visible, but if
            // somehow it is, do nothing rather than spam the FSM.
        });
    }

    // Visibility observer — show the pause button when audio starts,
    // hide it when audio ends or is cleared.
    let _audioWatchTimer = null;
    function _syncPauseBtnVisibility() {
        const btn = document.getElementById('pause-resume');
        if (!btn) return;
        const audio = window._activeAudio;
        const hasAudio = !!(audio && !audio.ended);
        btn.classList.toggle('no-audio', !hasAudio);
        if (hasAudio) {
            btn.dataset.paused = audio.paused ? 'true' : 'false';
        }
    }
    // Lightweight polling — audio events are hard to capture since the
    // audio element is created dynamically in playMessageTTS().
    setInterval(_syncPauseBtnVisibility, 500);

    // Text input — textarea with auto-grow + Enter-to-send (Shift+Enter = newline)
    const textInput = document.getElementById('text-input');
    if (textInput) {
        const inputWrapper = textInput.closest('.learn-chat-input-wrapper');

        // Enter = send, Shift+Enter = newline (in textarea)
        textInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendTextMessage();
            }
        });

        // Auto-grow + has-content tracking — drives the send button's
        // enabled/disabled visual state without relying on :valid.
        const updateInputState = () => {
            if (!inputWrapper) return;
            const hasContent = textInput.value.trim().length > 0;
            inputWrapper.classList.toggle('has-content', hasContent);
            // Auto-grow the textarea
            textInput.style.height = 'auto';
            const maxH = 180;
            textInput.style.height = Math.min(textInput.scrollHeight, maxH) + 'px';
        };
        textInput.addEventListener('input', updateInputState);
        textInput.addEventListener('change', updateInputState);
    }
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) {
        sendBtn.addEventListener('click', sendTextMessage);
    }

    // Voice input (push-to-talk mic -> /api/stt -> TEXT_INPUT)
    setupVoiceInput();

    // Course creation modal
    if (courseForm) {
        courseForm.addEventListener('submit', (e) => {
            e.preventDefault();
            submitCourseCreation();
        });
    }
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', hideCourseCreationModal);
    }

    // Sudo password form
    const sudoPasswordForm = document.getElementById('sudo-password-form');
    if (sudoPasswordForm) {
        sudoPasswordForm.addEventListener('submit', submitSudoPassword);
    }

    const chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        // Event delegation for transcript interactions
        chatStream.addEventListener('blur', (e) => {
            if (e.target.contentEditable === 'true') {
                const messageDiv = e.target.closest('.message');
                const index = parseInt(messageDiv.dataset.index);
                const newText = e.target.textContent.trim();
                if (newText) {
                    sendEvent('EDIT_MESSAGE', { index: index, text: newText });
                }
            }
        }, true);

        chatStream.addEventListener('click', (e) => {
            if (e.target.classList.contains('tts-play-btn')) {
                // Handled by addTTSButton onclick
            }
        });
    }

    console.log('Socket.IO connected!');

    // Check for create_course query param
    const urlParams = new URLSearchParams(window.location.search);
    const createTopic = urlParams.get('create_course');
    const courseDepth = urlParams.get('depth') || '3'; // Default to 3 if not specified
    if (createTopic && courseModal) { // Guard courseModal
        showCourseCreationModal(decodeURIComponent(createTopic), courseDepth);
        // Clear the URL
        window.history.replaceState(null, null, window.location.pathname);
    }

    // Check for resume query param
    const resume = urlParams.get('resume');
    if (resume === 'true') {
        // Fetch state to get UID, then resume
        fetch('/api/fsm_state').then(res => res.json()).then(state => {
            if (state.active_course_uid) {
                console.log("Auto-resuming course:", state.active_course_uid);
                sendEvent('RESUME_COURSE', { uid: state.active_course_uid });
                // Clear the URL
                window.history.replaceState(null, null, window.location.pathname);
            }
        }).catch(err => console.error("Failed to auto-resume:", err));
    }

    // Initial fetch for context rail data, in case it's not in the first state push
    // updateContextRail internally checks for element existence, so it is safe-ish,
    // but the rail container might not exist.
    if (document.getElementById('context-rail')) {
        updateContextRail(null, null);
    }
});

// Voice selection functions
function fetchAndPopulateVoices() {
    if (!voiceSelector) return;

    fetch('/api/voices')
        .then(response => response.json())
        .then(data => {
            const voices = data.voices || [];
            if (voices.length === 0) {
                console.warn('No voices returned from API, using defaults');
                return;
            }

            // Populate dropdown with fetched voices
            voiceSelector.innerHTML = '';
            voices.forEach(voice => {
                const option = document.createElement('option');
                option.value = voice;
                option.textContent = voice;
                voiceSelector.appendChild(option);
            });

            // Restore previous selection from localStorage. B6.5: use the SAME
            // key that playMessageTTS reads ('helga-voice'), and default to a real
            // Kokoro voice id ('af_heart') so the dropdown actually drives playback.
            const savedVoice = localStorage.getItem('helga-voice');
            if (savedVoice && voices.includes(savedVoice)) {
                voiceSelector.value = savedVoice;
            } else {
                voiceSelector.value = voices.includes('af_heart') ? 'af_heart' : voices[0];
            }

            console.log('[fetchAndPopulateVoices] Voices populated:', voices);
        })
        .catch(error => {
            console.error('[fetchAndPopulateVoices] Failed to fetch voices:', error);
            // Keep default voices in dropdown
        });
}

function handleVoiceChange() {
    if (!voiceSelector) return;

    const selectedVoice = voiceSelector.value;
    // Voice is client-side only (read by playMessageTTS via this key). The old
    // socket.emit('update_settings') here was dead — there is no server handler
    // for it — so it was removed (B6.5).
    localStorage.setItem('helga-voice', selectedVoice);
}

// --- Typing Indicator ---
function showTypingIndicator() {
    const chatStream = document.getElementById('chat-stream');
    if (!chatStream) return;
    let existing = document.getElementById('typing-indicator');
    if (existing) return;
    const div = document.createElement('div');
    div.id = 'typing-indicator';
    div.className = 'message ai-message typing';
    div.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    chatStream.appendChild(div);
    chatStream.scrollTop = chatStream.scrollHeight;
}

function hideTypingIndicator() {
    const el = document.getElementById('typing-indicator');
    if (el) el.remove();
}

// --- TTS Play Button Helper ---
function addTTSButton(messageEl, text) {
    const btn = document.createElement('button');
    btn.className = 'tts-play-btn';
    btn.innerHTML = '&#9654;';
    btn.title = 'Play audio';
    btn.onclick = async function() {
        btn.disabled = true;
        btn.textContent = '...';
        try {
            const resp = await fetch('/api/tts', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: text})});
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => { btn.innerHTML = '&#9654;'; btn.disabled = false; };
            audio.play();
        } catch(e) { btn.innerHTML = '&#9654;'; btn.disabled = false; }
    };
    messageEl.appendChild(btn);
}

// --- Confetti Animation Trigger ---
// Call showConfetti() to display a page-wide confetti burst.
// Uses inline styles for random per-piece physics (horizontal drift,
// rotation speed, fall duration, color, size) so each burst feels alive.
// The container auto-removes after the slowest piece lands.
// Respects gamification setting — no confetti when gamification is disabled.
function showConfetti() {
    if (localStorage.getItem('helga-gamification') === 'false') return;

    var existing = document.querySelector('.confetti-container');
    if (existing) existing.remove();

    var container = document.createElement('div');
    container.className = 'confetti-container';

    var colors = [
        '#e74c3c', '#3498db', '#2ecc71', '#f1c40f',
        '#9b59b6', '#e67e22', '#1abc9c', '#d4a843',
        '#2e6b8a', '#c45c4a', '#4a8c6f'
    ];
    var count = 90;
    var maxDuration = 0;

    for (var i = 0; i < count; i++) {
        var piece = document.createElement('div');
        piece.className = 'confetti-piece confetti-burst';
        // Randomized positioning + physics per piece
        var left = Math.random() * 100;          // %
        var drift = (Math.random() - 0.5) * 220; // px horizontal drift
        var rotate = (Math.random() * 720) - 360; // deg total rotation
        var delay = Math.random() * 0.35;        // s
        var duration = 2.6 + Math.random() * 1.8; // s
        var size = 6 + Math.random() * 9;        // px
        var isCircle = Math.random() > 0.5;
        var color = colors[Math.floor(Math.random() * colors.length)];
        piece.style.left = left + '%';
        piece.style.background = color;
        piece.style.width = size + 'px';
        piece.style.height = (size * (0.8 + Math.random() * 1.4)) + 'px';
        piece.style.borderRadius = isCircle ? '50%' : '2px';
        piece.style.animationDelay = delay + 's';
        piece.style.animationDuration = duration + 's';
        piece.style.setProperty('--confetti-drift', drift + 'px');
        piece.style.setProperty('--confetti-rotate', rotate + 'deg');
        container.appendChild(piece);
        if (duration + delay > maxDuration) maxDuration = duration + delay;
    }

    document.body.appendChild(container);

    setTimeout(function() {
        if (container.parentNode) {
            container.parentNode.removeChild(container);
        }
    }, (maxDuration + 0.4) * 1000);
}
window.showConfetti = showConfetti;

// --- Achievement Unlock Banner ---
// Displays a full-width banner sliding down from the top when an achievement is unlocked.
// Auto-dismisses after 4 seconds.
function showAchievementBanner(name, description, xpReward) {
    // Skip when gamification is disabled
    if (localStorage.getItem('helga-gamification') === 'false') return;

    // Remove any existing banner before showing a new one
    var existing = document.querySelector('.achievement-banner');
    if (existing) existing.remove();

    var banner = document.createElement('div');
    banner.className = 'achievement-banner';
    banner.innerHTML =
        '<span class="achievement-icon">&#127942;</span>' +
        '<div class="achievement-info">' +
            '<span class="achievement-name">' + (name || 'Achievement Unlocked') + '</span>' +
            '<span class="achievement-desc">' + (description || '') + '</span>' +
        '</div>' +
        (xpReward ? '<span class="achievement-xp">+' + xpReward + ' XP</span>' : '');

    document.body.appendChild(banner);

    // Trigger the slide-down animation on the next frame
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            banner.classList.add('show');
        });
    });

    // Auto-dismiss after 4 seconds
    setTimeout(function () {
        banner.classList.remove('show');
        // Remove from DOM after the slide-up transition completes
        setTimeout(function () {
            if (banner.parentNode) {
                banner.parentNode.removeChild(banner);
            }
        }, 600);
    }, 4000);
}
window.showAchievementBanner = showAchievementBanner;

function animateBadgeUpgrade(element) {
    element.classList.add('badge-upgrading');
    element.addEventListener('animationend', function() {
        element.classList.remove('badge-upgrading');
    }, { once: true });
}
window.animateBadgeUpgrade = animateBadgeUpgrade;

// --- Streaming LLM Response Rendering ---
// These functions manage a temporary "streaming" chat bubble that accumulates
// tokens as they arrive from the LLM.  When the stream finishes (done=true),
// the bubble is marked complete. The next state_update with the full transcript
// replaces the streaming bubble with the canonical version.

let _streamingBubble = null;       // DOM element for the in-progress streaming bubble
let _streamingText = '';           // Accumulated raw text so far
let _streamRafPending = false;     // requestAnimationFrame guard to batch DOM writes

// Exposed to resetChatSession() so enterNode() can tear down in-flight
// streaming state before starting a new concept.
window._tearDownStreamingBubble = function () {
    if (_streamingBubble) {
        try { _streamingBubble.remove(); } catch (e) {}
    }
    _streamingBubble = null;
    _streamingText = '';
    _streamRafPending = false;
};

function handleStreamToken(data) {
    const token = data.token || '';
    const done = data.done || false;

    // Respect the navigation guard. When the user jumps to a new concept,
    // enterNode() sets navigatingToNode=true and clears the DOM. The FSM's
    // previous NLG call may still be emitting stream_token messages over
    // the socket — we must NOT paint them into the new concept's chat.
    // Drop any token (and any lingering in-flight bubble) until the guard
    // releases.
    if (window.navigatingToNode) {
        if (_streamingBubble) {
            try { _streamingBubble.remove(); } catch (e) {}
            _streamingBubble = null;
            _streamingText = '';
            _streamRafPending = false;
        }
        return;
    }

    if (done) {
        // Stream finished — mark bubble as complete so updateChatStream()
        // can replace it with the final canonical message.
        if (_streamingBubble) {
            _streamingBubble.classList.add('stream-done');
            _streamingBubble.classList.remove('streaming');
        }
        _streamingBubble = null;
        _streamingText = '';
        _streamRafPending = false;
        return;
    }

    if (!token) return;

    _streamingText += token;

    // Create the streaming bubble if it does not exist yet.
    // Use the same .chat-msg.incoming structure as updateChatStream() so
    // the Gemini-style CSS in learn-chat.css applies — previously the
    // bubble used legacy .message.helga classes that had no styling in
    // the new chatbox and rendered as orphaned text below the input bar.
    if (!_streamingBubble) {
        const chatStream = document.getElementById('chat-stream');
        if (!chatStream) return;

        // Hide the hero as soon as content starts streaming
        const hero = document.getElementById('chat-hero');
        if (hero) hero.classList.add('hidden');

        _streamingBubble = document.createElement('div');
        _streamingBubble.className = 'chat-msg incoming streaming stream-bubble';
        _streamingBubble.innerHTML = `
            <div class="chat-msg-content">
                <img class="chat-msg-avatar" src="/static/img/helga-avatar.svg" alt="Helga"
                     onerror="this.src='/static/img/helga-avatar.svg'">
                <div class="chat-msg-body">
                    <div class="chat-msg-text streaming-content"></div>
                </div>
            </div>
        `;
        chatStream.appendChild(_streamingBubble);
        // New stream — scroll to reveal the first token. This counts as a
        // "new turn" moment so we force the scroll regardless of user
        // pinning state.
        chatStream.scrollTop = chatStream.scrollHeight;
        _userPinnedToBottom = true;
    }

    // Batch DOM updates with requestAnimationFrame to avoid layout thrashing
    if (!_streamRafPending) {
        _streamRafPending = true;
        requestAnimationFrame(function () {
            _streamRafPending = false;
            if (_streamingBubble) {
                var contentEl = _streamingBubble.querySelector('.streaming-content');
                if (contentEl) {
                    contentEl.innerHTML = renderMarkdown(_streamingText);
                }
                var chatStream = document.getElementById('chat-stream');
                _scrollChatToBottom(chatStream, false);
            }
        });
    }
}

// Patch updateChatStream to clean up finished streaming bubbles when the
// canonical transcript arrives from the state poller.
var _origUpdateChatStream = updateChatStream;
updateChatStream = function (transcript) {
    if (!transcript) {
        _origUpdateChatStream(transcript);
        return;
    }

    // If a streaming bubble exists and is marked done, remove it before
    // the canonical messages render (they include the final text).
    var chatStream = document.getElementById('chat-stream');
    if (chatStream) {
        var doneBubbles = chatStream.querySelectorAll('.chat-msg.stream-done');
        doneBubbles.forEach(function (b) { b.remove(); });
    }

    // If a streaming bubble is still actively receiving tokens, the new
    // transcript message count may include the message being streamed.
    // Remove the live bubble so it doesn't duplicate.
    if (_streamingBubble && chatStream) {
        // The transcript's last entry should match what we are streaming.
        // Remove the streaming bubble to let the canonical version take over.
        if (transcript.length > displayedMessagesCount) {
            _streamingBubble.remove();
            _streamingBubble = null;
            _streamingText = '';
            _streamRafPending = false;
        }
    }

    _origUpdateChatStream(transcript);
};