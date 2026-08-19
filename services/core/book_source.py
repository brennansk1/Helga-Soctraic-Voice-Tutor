"""Reading the book during hydration — the source, not a recollection.

WHY A TOOL AND NOT A PROMPT STUFF
---------------------------------
A chapter is 3,000-12,000 words. The served context is 32,768 tokens and must
also hold the section template, the ledger context and the generated output, so
a whole chapter cannot simply be pasted in — and a whole BOOK certainly cannot.

So the book is a *tool the hydrator calls*: given a concept, return the passage
of the chapter that actually bears on it. That is the difference between a course
built FROM a book and a course built from a model's memory of a book.

HOW A PASSAGE IS CHOSEN
-----------------------
Deterministically, and in two steps:

  1. The lesson already records which chapter it came from (`book_chapter`),
     written when the skeleton was built. No search needed — the link is
     structural.
  2. Within that chapter, rank chunks by overlap with the concept title and
     objectives, and return the best few in READING ORDER.

Reading order matters: a book argues cumulatively, and handing a model the
third-best passage before the best one inverts an argument the author built.

WHY NOT EMBEDDINGS HERE
-----------------------
They are available and would rank slightly better. But the candidate set is one
chapter's handful of chunks, lexical overlap resolves it, and an embedding call
per concept on a 40-chapter book is 120+ calls to reorder six items. The ledger
uses embeddings because it ranks across a whole course; this ranks within a
chapter the structure already chose.

CONCEPT NAMING
--------------
The skeleton leaves concept titles EMPTY on purpose — the book decides what its
own chapters teach, and inventing concept names before reading the chapter is
how a course ends up with plausible headings the source does not support.
`propose_concepts` reads the chapter and names them.
"""

import logging
import re

logger = logging.getLogger(__name__)

# How much of a chapter to hand the model for one concept. Comfortably inside
# 32k alongside the template and output, and roughly a long section of a book.
PASSAGE_CHARS = 7000

_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was",
         "its", "into", "how", "why", "what", "when", "which", "their", "them"}


def _words(text):
    return {w for w in re.findall(r"[a-z']+", (text or "").lower())
            if len(w) > 3 and w not in _STOP}


def passage_for(book, chapter_order, title="", objectives=None,
                max_chars=PASSAGE_CHARS, chunk_size=3000):
    """The part of a chapter that bears on this concept, in reading order.

    Returns '' when the chapter is missing rather than falling back to another
    chapter: material from the wrong chapter is worse than none, because it
    reads as authoritative and is not what the lesson is about.
    """
    ch = book.chapter(chapter_order) if book else None
    if ch is None:
        return ""
    chunks = ch.chunks(size=chunk_size, overlap=300)
    if not chunks:
        return ""
    if len(ch.text) <= max_chars:
        return ch.text

    want = _words(f"{title} {' '.join(objectives or [])}")
    if not want:
        # Nothing to rank by — the chapter's opening is the author's own
        # framing and the best default.
        return ch.text[:max_chars]

    scored = []
    for i, c in enumerate(chunks):
        cw = _words(c)
        overlap = len(want & cw) / len(want) if want else 0.0
        scored.append((overlap, i, c))
    scored.sort(key=lambda r: -r[0])

    picked, total = [], 0
    for overlap, i, c in scored:
        if total + len(c) > max_chars:
            continue
        picked.append((i, c))
        total += len(c)
        if total >= max_chars * 0.85:
            break
    if not picked:
        return ch.text[:max_chars]
    # BACK INTO READING ORDER. A book argues cumulatively; handing the model the
    # third-best passage before the best one inverts the author's sequence.
    picked.sort(key=lambda r: r[0])
    return "\n\n[...]\n\n".join(c for _, c in picked)


def source_block(book, chapter_order, title="", objectives=None):
    """The passage, wrapped as prompt material with its provenance stated."""
    text = passage_for(book, chapter_order, title, objectives)
    if not text:
        return ""
    ch = book.chapter(chapter_order)
    return (f"### FROM THE BOOK — {book.title}, "
            f"chapter {chapter_order}: {ch.title if ch else ''}\n"
            f"Write from THIS TEXT. Where it is explicit, follow it; do not "
            f"substitute your own recollection of the book.\n\n"
            f"{text}\n")


CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "chapter_title": {"type": "string"},
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "objectives": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["concepts"],
}


def concept_prompt(book, chapter_order, n, course_title="", chapter_part=None):
    """Ask the model to name this chapter's concepts, having read it.

    `chapter_part` splits a long chapter across several lessons — the model is
    told which portion this lesson covers so the lessons do not all name the
    same three ideas.
    """
    ch = book.chapter(chapter_order)
    if ch is None:
        return None
    text = ch.text
    if chapter_part:
        idx, total = chapter_part
        span = max(1, len(text) // max(1, total))
        text = text[idx * span:(idx + 1) * span]
    text = text[:12000]

    part_note = ""
    if chapter_part:
        idx, total = chapter_part
        part_note = (f"\nThis lesson covers part {idx + 1} of {total} of the "
                     f"chapter. Name only what THIS part teaches.\n")

    return (
        f"### CHAPTER TEXT — {book.title}, chapter {chapter_order}: {ch.title}\n"
        f"{text}\n\n"
        f"### TASK\n"
        f"1. Give this chapter a SHORT DESCRIPTIVE TITLE of 3-7 words saying "
        f"what it is about. Many books number their chapters and do not name "
        f"them — \"Chapter I\" tells a learner nothing about what they are "
        f"about to study, so name it from what you have just read. Do not "
        f"include the word \"Chapter\" or a number; those are added "
        f"separately.\n"
        f"2. Name the {n} concepts this chapter actually teaches, in the order "
        f"the chapter presents them.{part_note}\n"
        f"Rules:\n"
        f"- Take them FROM THE TEXT above. Do not add what the book does not "
        f"cover, and do not name a concept the chapter only mentions in passing.\n"
        f"- A concept title is 2-6 words and names a specific idea. "
        f"\"Introduction\", \"Key Points\" and \"Overview\" name nothing.\n"
        f"- Give each 2 learning objectives, phrased as what the learner will "
        f"be able to do.\n"
    )


# A chapter heading that names nothing. Austen's "Chapter I", a textbook's
# "3.2", a manual's "Section Four" — all of them are positions, not subjects.
_BARE_HEADING = re.compile(
    r"^\s*(?:chapter|ch\.?|section|part|lesson|unit)?\s*"
    r"[0-9IVXLCivxlc]*\s*[.:)-]?\s*$",
    re.IGNORECASE)


def needs_a_title(title):
    """Does this chapter heading tell a learner anything?"""
    return bool(_BARE_HEADING.match((title or "").strip()))


def compose_title(order, original, named=None):
    """"Chapter 3 — The Cell Membrane".

    The number is kept because it locates the learner in the book they
    uploaded, and the subject is added because the number alone does not say
    what they are about to study. A book that already titles its chapters keeps
    the author's words; only a bare heading is replaced.
    """
    subject = (named or "").strip().strip('".')
    if not needs_a_title(original) and original.strip():
        # The author named it. Use theirs, prefixed only if it lacks a number.
        base = original.strip()
        return base if re.search(r"\d|[IVXLC]{1,7}\b", base) else f"Chapter {order} — {base}"
    if subject:
        return f"Chapter {order} — {subject}"
    return original.strip() or f"Chapter {order}"


def parse_concepts(raw, want):
    """Concepts from the model's reply, padded to `want` only if it under-fills.

    Under-filling is recorded rather than silently topped up with invented
    titles: a chapter that genuinely carries two ideas should not be made to
    carry four.
    """
    items = []
    if isinstance(raw, dict):
        items = raw.get("concepts") or []
    elif isinstance(raw, list):
        items = raw
    out = []
    for it in items:
        if isinstance(it, dict) and (it.get("title") or "").strip():
            out.append({"title": it["title"].strip()[:120],
                        "objectives": [o for o in (it.get("objectives") or [])
                                       if isinstance(o, str)][:3]})
        elif isinstance(it, str) and it.strip():
            out.append({"title": it.strip()[:120], "objectives": []})
    if len(out) < want:
        logger.info(f"[BOOK] chapter yielded {len(out)} concept(s) against "
                    f"{want} requested — not padding")
    return out[:want]


def attach_concepts(course, book, llm_json_fn, per_lesson=3):
    """Name every empty concept in a book-shaped course, chapter by chapter.

    One call per lesson, not per concept: the model reads the chapter once and
    names all of that lesson's concepts together, which is both cheaper and more
    coherent — concepts named in one pass do not overlap the way independently
    named ones do.
    """
    named, skipped = 0, 0
    for m in (course.get("modules") or []):
        for u in (m.get("units") or []):
            for lesson in (u.get("lessons") or []):
                ch_order = lesson.get("book_chapter")
                slots = lesson.get("concepts") or []
                if not ch_order or not slots:
                    continue
                prompt = concept_prompt(book, ch_order, len(slots),
                                        course_title=course.get("title", ""),
                                        chapter_part=lesson.get("chapter_part"))
                if not prompt:
                    skipped += len(slots)
                    continue
                try:
                    raw = llm_json_fn(prompt=prompt, schema=CONCEPT_SCHEMA,
                                      expected_type="dict", max_tokens=700)
                except Exception as e:
                    logger.warning(f"[BOOK] concept naming failed for chapter "
                                   f"{ch_order}: {e}")
                    skipped += len(slots)
                    continue
                got = parse_concepts(raw, len(slots))
                # Name the chapter too, when the book only numbered it.
                #
                # The model omits `chapter_title` often enough that half of a
                # sample kept bare "Chapter I" headings, so the first concept is
                # the fallback: it was drawn from the same reading and names the
                # chapter's opening subject, which beats a Roman numeral.
                # `chapter_name`, not `named` — the latter is the counter, and
                # shadowing it turned the tally into a string mid-loop.
                chapter_name = (raw.get("chapter_title")
                                if isinstance(raw, dict) else None)
                if not chapter_name and got:
                    chapter_name = got[0]["title"]
                if chapter_name:
                    lesson["title"] = compose_title(
                        ch_order, lesson.get("title", ""), chapter_name)
                for slot, c in zip(slots, got):
                    slot["title"] = c["title"]
                    slot["learning_objectives"] = c["objectives"]
                    named += 1
                # A lesson whose chapter yielded fewer concepts keeps fewer,
                # rather than shipping empty slots that hydrate into filler.
                lesson["concepts"] = [s for s in slots if s.get("title")]
    logger.info(f"[BOOK] named {named} concept(s) from the book, {skipped} skipped")
    return {"named": named, "skipped": skipped}
