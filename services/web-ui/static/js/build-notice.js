/* The one-at-a-time notice.
 *
 * Helga builds exactly one course at a time — not a limitation but the model:
 * a build is dozens of sequential LLM calls on a single machine, and two at
 * once would make both slower than either alone. So a learner who asks for a
 * second build is not making a mistake, they are meeting a rule nobody told
 * them about.
 *
 * Refusing inside a card's own subtitle put that message wherever the click
 * happened to be — a caption on a control they had already looked away from.
 * This is a card, centred, that says what is happening, what it means, and
 * gives the one action worth offering: go and watch the build that IS running.
 *
 * Shared, because the degree page, the create carousel and the courses page
 * can all provoke the same refusal and none of them should invent their own
 * words for it.
 */
(function () {
    "use strict";

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    var host = null, lastFocus = null;

    function close() {
        if (!host) return;
        host.classList.add("hidden");
        // Return focus where the learner left it, not to the top of the page.
        if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
    }

    /**
     * @param {object} o
     *   title   headline
     *   body    one or two sentences of plain explanation
     *   action  {label, href} — optional primary action
     *   tone    "busy" (default) | "blocked"
     */
    function show(o) {
        o = o || {};
        lastFocus = document.activeElement;

        if (!host) {
            host = el("div", "build-notice-backdrop hidden");
            host.id = "build-notice";
            host.addEventListener("click", function (e) {
                if (e.target === host) close();   // click-away dismisses
            });
            document.addEventListener("keydown", function (e) {
                if (e.key === "Escape" && host && !host.classList.contains("hidden")) {
                    close();
                }
            });
            document.body.appendChild(host);
        }
        host.textContent = "";

        var card = el("div", "build-notice" + (o.tone === "blocked" ? " is-blocked" : ""));
        card.setAttribute("role", "alertdialog");
        card.setAttribute("aria-modal", "true");
        card.setAttribute("aria-label", o.title || "Notice");

        card.appendChild(el("h2", "build-notice-title", o.title || "One at a time"));
        card.appendChild(el("p", "build-notice-body", o.body || ""));

        var actions = el("div", "build-notice-actions");
        if (o.action && o.action.href) {
            var go = el("a", "btn btn-primary", o.action.label || "Go");
            go.href = o.action.href;
            actions.appendChild(go);
        }
        var dismiss = el("button", "btn btn-secondary", o.dismissLabel || "Stay here");
        dismiss.type = "button";
        dismiss.addEventListener("click", close);
        actions.appendChild(dismiss);
        card.appendChild(actions);

        host.appendChild(card);
        host.classList.remove("hidden");
        // Focus the primary action so the keyboard path is one key, not a hunt.
        var first = card.querySelector("a.btn, button.btn");
        if (first) first.focus();
    }

    /* The specific refusal this file exists for. */
    function buildInProgress(what) {
        show({
            title: "A course is already being built",
            body: (what ? "“" + what + "” is waiting. " : "") +
                  "Helga builds one course at a time — a build is dozens " +
                  "of model calls on this machine, and two at once would make " +
                  "both slower than either alone. Your choice is saved; it " +
                  "starts when this one finishes.",
            action: { label: "Watch the build", href: "/build" },
            dismissLabel: "Stay here",
        });
    }

    window.HelgaBuildNotice = { show: show, close: close,
                                buildInProgress: buildInProgress };
})();
