"""A course shaped like the book it came from.

THE DECISION
------------
The normal skeleton builder invents structure from research: modules, units,
lessons, concepts, sized to a calendar. When the source is a BOOK, that is the
wrong instinct — the author already made those decisions, and they are usually
better than ours because they had the whole subject in view and a publisher's
editor.

ONE CHAPTER IS ONE LESSON. ALWAYS.
----------------------------------
The chapter is the author's unit of teaching — the thing they decided was one
sitting's worth of argument — so it maps to the lesson, which is our unit of
one sitting. That correspondence is the whole point of building from a book,
and nothing overrides it.

An earlier version split long chapters across two to four lessons "for balance".
That was wrong: it invented boundaries the author did not put there, in the one
place where the author's boundaries are exactly what we are trying to keep.
**A long chapter earns MORE CONCEPTS, not more lessons.** Depth scales inside
the lesson, where the book left it to us, never by subdividing the chapter.

NO MODULES FOR A GENERAL BOOK
-----------------------------
A module is a pedagogical division — a block of study a curriculum designer
chose. A novel, a memoir, a self-help book has no such thing, and inventing one
is the same error as splitting chapters. So a general book gets ONE container
module holding the course, and the level that adapts is the UNIT:

    parts/sections present  ->  each PART becomes a UNIT, each CHAPTER a LESSON
    no parts                ->  one unit, each CHAPTER a LESSON

A TEXTBOOK is different and keeps the module level, because its parts really are
study blocks with a curriculum behind them.

Concepts are the one level the book does not hand us, and their COUNT is what
absorbs a chapter's length.

WHY NOT FORCE THE USUAL LADDER
------------------------------
A self-help book has eight chapters and no parts. A programming book has four
parts of six chapters. A monograph has thirty chapters and no parts at all.
Forcing 8 modules x 15 units onto any of them produces invented scaffolding
around real content, which is the opposite of the promise "upload a book and get
a course from it".

The tell that this is right: the module/unit/lesson bands that `SCHOOL_SHAPE`
enforces for a researched course are a proxy for *how a school paces material*.
A book already encodes its author's pacing, and it is a better signal than a
band.
"""

import logging

logger = logging.getLogger(__name__)

# Concepts per lesson. A long chapter earns more of them — this is the ONLY
# dimension that flexes with chapter length, because it is the only level the
# book does not already define.
MIN_CONCEPTS_PER_LESSON = 2
MAX_CONCEPTS_PER_LESSON = 6
WORDS_PER_CONCEPT = 1500


def choose_shape(book):
    """How this book should map onto the course ladder.

    Returns {"shape", "why", "modules", "lessons"} — always with a reason,
    because "why is this course shaped like this" must be answerable from the
    record rather than reverse-engineered.
    """
    chapters = book.chapters
    parts = book.parts
    n = len(chapters)

    # The lesson count is not a decision. It is len(chapters), always.
    if parts and len(parts) >= 2:
        return {
            "shape": "parts_as_units",
            "why": f"the book has {len(parts)} parts, which become units; each "
                   f"of its {n} chapters is a lesson",
            "modules": 1, "units": len(parts), "lessons": n,
        }

    return {
        "shape": "chapters_as_lessons",
        "why": f"{n} chapters and no parts — one unit, one lesson per chapter, "
               f"in the book's own order",
        "modules": 1, "units": 1, "lessons": n,
    }


def concepts_for(chapter):
    """How many concepts a chapter's length supports.

    The ONLY thing that flexes with chapter length. A 900-word chapter of Austen
    carries two ideas; a 9,000-word chapter of a textbook carries six. Neither
    becomes more than one lesson.
    """
    n = max(MIN_CONCEPTS_PER_LESSON,
            min(MAX_CONCEPTS_PER_LESSON,
                round(chapter.words / WORDS_PER_CONCEPT)))
    return int(n)


def _uid(prefix):
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def build_structure(book, course_title=None, concepts_per_lesson=None):
    """A course structure mirroring the book, with concepts left to be filled.

    Concepts are the one level the book does not provide, so they are emitted as
    placeholders carrying the chapter they came from. Hydration reads that
    chapter to name and write them — see `book_source_for`.
    """
    shape = choose_shape(book)
    course = {
        "uid": _uid("course"),
        "title": course_title or book.title,
        "source": "book",
        "book_shape": shape,
        "book_title": book.title,
        "book_format": book.format,
        "modules": [],
    }

    def _lesson(ch, ordinal):
        # Fixed count only if the caller insists; otherwise the chapter's own
        # length decides, which is what lets one lesson carry a long chapter.
        n_con = concepts_per_lesson or concepts_for(ch)
        return {
            "uid": _uid("less"),
            "title": ch.title or f"Chapter {ch.order}",
            "ordinal": ordinal,
            # The link that makes hydration able to READ rather than recall.
            "book_chapter": ch.order,
            "source_words": ch.words,
            "concepts": [
                {"uid": _uid("con"), "title": "", "ordinal": i + 1,
                 "book_chapter": ch.order, "from_book": True}
                for i in range(n_con)
            ],
        }

    # ONE container module either way. It is a shell so the storage ladder
    # stays intact, not a pedagogical division the book does not have.
    units = []
    if shape["shape"] == "parts_as_units":
        # Chapters before the first part heading get their own unit rather than
        # being dropped or folded into a part they do not belong to.
        orphans = [c for c in book.chapters if not c.part]
        if orphans:
            units.append({"uid": _uid("unit"), "title": "Introduction",
                          "ordinal": 1,
                          "lessons": [_lesson(c, i + 1)
                                      for i, c in enumerate(orphans)]})
        for part in book.parts:
            chs = [c for c in book.chapters if c.part == part]
            units.append({"uid": _uid("unit"), "title": part,
                          "ordinal": len(units) + 1,
                          "lessons": [_lesson(c, i + 1)
                                      for i, c in enumerate(chs)]})
    else:
        units.append({"uid": _uid("unit"), "title": course["title"],
                      "ordinal": 1,
                      "lessons": [_lesson(c, i + 1)
                                  for i, c in enumerate(book.chapters)]})

    course["modules"].append({"uid": _uid("mod"), "title": course["title"],
                              "ordinal": 1, "container_only": True,
                              "units": units})

    logger.info(f"[BOOK] shape={shape['shape']} — {shape['why']}")
    return course


def summarise(course):
    mods = course.get("modules") or []
    lessons = [l for m in mods for u in (m.get("units") or [])
               for l in (u.get("lessons") or [])]
    concepts = [c for l in lessons for c in (l.get("concepts") or [])]
    return {
        "shape": (course.get("book_shape") or {}).get("shape"),
        "modules": len(mods),
        "lessons": len(lessons),
        "concepts": len(concepts),
        "chapters_linked": len({l.get("book_chapter") for l in lessons
                                if l.get("book_chapter")}),
    }


def build_from_book(path, storage, course_title=None, llm_json_fn=None,
                    concepts_per_lesson=3, max_pages=None):
    """The product promise, end to end: a book file becomes a course.

    Adjacent to the researched-course pipeline rather than inside it, because
    the two make opposite decisions. A researched course INVENTS structure and
    sizes it to a calendar; a book course READS structure, because the author
    already made those choices with the whole subject in view.

    What they share is everything downstream — the same storage, the same
    ledger, the same depth contract, the same asset phase. Only the skeleton
    differs.

    Returns the created course dict, or None. Never raises on a bad file: an
    unreadable upload should report itself, not take the request down.
    """
    try:
        from services.research.book_reader import open_book
    except ImportError:
        from book_reader import open_book

    book = open_book(path, max_pages=max_pages)
    if not book:
        logger.warning(f"[BOOK] could not read {path}")
        return None

    course = build_structure(book, course_title=course_title,
                             concepts_per_lesson=concepts_per_lesson)

    # Concepts are named by READING the chapter, never invented from the title.
    # Without this the lessons carry empty concept slots, which is a visibly
    # incomplete course rather than a silently wrong one.
    if llm_json_fn:
        try:
            from services.core.book_source import attach_concepts
        except ImportError:
            from book_source import attach_concepts
        try:
            course["book_concepts"] = attach_concepts(
                course, book, llm_json_fn, per_lesson=concepts_per_lesson)
        except Exception as e:
            logger.warning(f"[BOOK] concept naming failed: {e}")
    else:
        logger.info("[BOOK] no LLM supplied — concepts left unnamed")

    course["status"] = "skeleton"
    course["source_path"] = path
    # Handed back so the caller can give it to the hydrator: hydration reads the
    # chapter a concept came from, and re-parsing the book per concept would
    # cost the parse 177 times over.
    course["_book"] = book
    stored = {k: v for k, v in course.items() if not k.startswith("_")}
    try:
        storage.courses.create_course(stored)
    except Exception as e:
        logger.error(f"[BOOK] could not store course: {e}")
        return None
    logger.info(f"[BOOK] built {course['uid']}: {summarise(course)}")
    return course


def hydrate_from_book(course, book, hydrator):
    """Point a hydrator at the book this course was built from.

    The one line that makes it a course built FROM a book rather than a course
    ABOUT one: with `hydrator.book` set, every concept carrying a `book_chapter`
    is written from that chapter's text instead of from the model's memory.
    """
    hydrator.book = book
    return hydrator.hydrate(course["uid"] if isinstance(course, dict) else course)
