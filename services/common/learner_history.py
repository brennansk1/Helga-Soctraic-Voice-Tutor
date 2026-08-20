"""A4.1b — what THIS learner has actually struggled with.

The sprint plan calls this "the thing no competitor can do", and the claim
holds: ChatGPT cannot do it because it has no memory of your last three
sessions, and fixed-curriculum platforms do it worse because their model of you
is a percentage. Helga has `times_correct`, `lapses` and FSRS `stability` per
concept per learner, persisted since schema v10, and until now the tutor never
read any of it.

WHY IT IS A SEPARATE CHANNEL FROM `misconceptions`
--------------------------------------------------
`prompts.py` already renders a misconceptions list as "Students often believe
X". That is a claim about students in general. "You have missed this twice,
both times confusing mediators with confounders" is a claim about the person in
the chair, and it earns a different tone and different authority. Collapsing
the two would make the tutor say "students often" about something it watched
this learner do, which is both wrong and slightly insulting.

WHAT IT REFUSES TO SAY
----------------------
Silence is a real answer here. With no record, or a record too thin to mean
anything, this returns None and the prompt carries no learner section at all.
An invented struggle is worse than no personalisation: the tutor would open by
correcting a mistake the learner never made.
"""

#: Below this many attempts a record is noise, not a pattern. One wrong answer
#: on a Tuesday is not "you struggle with this".
MIN_ATTEMPTS = 2

#: FSRS stability in days. Below this, the memory is fragile regardless of
#: whether the last answer happened to be right.
FRAGILE_STABILITY_DAYS = 3.0

#: Hard cap on what reaches the prompt. This rides in every tutor turn, so it
#: is bounded by design rather than by hoping the data stays small.
MAX_CONCEPTS = 3
MAX_CHARS = 400


def _row_signal(row):
    """Is there anything worth telling the tutor about this concept?"""
    if not row:
        return None
    lapses = int(row.get("lapses") or 0)
    correct = int(row.get("times_correct") or 0)
    attempts = int(row.get("times_seen") or row.get("attempts") or 0)
    attempts = max(attempts, correct + lapses)
    stability = row.get("stability")

    if attempts < MIN_ATTEMPTS:
        return None
    if lapses >= 2:
        return ("lapses", lapses,
                f"have forgotten this {lapses} times after getting it right")
    if lapses == 1 and correct == 0:
        return ("wrong", 1, "got this wrong and have not yet got it right")
    if stability is not None:
        try:
            if float(stability) < FRAGILE_STABILITY_DAYS and attempts >= MIN_ATTEMPTS:
                return ("fragile", float(stability),
                        "hold this only briefly — it fades within days")
        except (TypeError, ValueError):
            pass
    return None


def summarise(progress_rows, concept_titles=None, max_concepts=MAX_CONCEPTS):
    """A sentence the tutor can act on, or None.

    `progress_rows` maps concept_uid -> the stored progress dict.
    `concept_titles` maps concept_uid -> a human title; a uid in a prompt is
    noise, so a concept with no title is dropped rather than shown raw.
    """
    titles = concept_titles or {}
    findings = []
    for uid, row in (progress_rows or {}).items():
        title = titles.get(uid)
        if not title:
            continue                     # never put a raw uid in a prompt
        sig = _row_signal(row)
        if sig:
            findings.append((sig[0], sig[1], title, sig[2]))

    if not findings:
        return None

    # Worst first: a repeated lapse outranks a fragile memory.
    order = {"lapses": 0, "wrong": 1, "fragile": 2}
    findings.sort(key=lambda f: (order.get(f[0], 9), -float(f[1] or 0)))
    findings = findings[:max_concepts]

    # Phrases are third-person PLURAL because they are prefixed with "they":
    # "they has forgotten" shipped once and read like a bug in the tutor.
    parts = [f"{title} — they {why}" for _, _, title, why in findings]
    note = ("THIS LEARNER'S RECORD (from their own past sessions, not a "
            "generalisation): " + "; ".join(parts) + ". "
            "Use it: pick up where they actually struggled rather than "
            "starting from scratch. Do not read this list back to them.")
    return note[:MAX_CHARS]


def for_concept(storage, concept_uid, course_uid=None, student_id=None,
                related_uids=None, titles=None):
    """Build the note from live storage. Returns None on any failure.

    Deliberately swallowing: a tutor turn must never fail because the
    personalisation lookup did. The worst case is teaching without the history,
    which is exactly where the product was before this existed.
    """
    try:
        uids = list(related_uids or [])
        if concept_uid and concept_uid not in uids:
            uids.insert(0, concept_uid)
        rows = {}
        for uid in uids[:12]:            # bounded: this runs per turn
            try:
                row = storage.progress.get_progress(uid, student_id=student_id)
            except TypeError:
                row = storage.progress.get_progress(uid)
            if row:
                rows[uid] = row
        return summarise(rows, titles or {})
    except Exception:
        return None
