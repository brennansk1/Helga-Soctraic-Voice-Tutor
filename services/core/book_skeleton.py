"""A course shaped like the book it came from.

THE DECISION
------------
The normal skeleton builder invents structure from research: modules, units,
lessons, concepts, sized to a calendar. When the source is a BOOK, that is the
wrong instinct — the author already made those decisions, and they are usually
better than ours because they had the whole subject in view and a publisher's
editor.

So for a book the structure is READ, and the shape adapts to what the book has:

    parts present          ->  PART becomes a MODULE, CHAPTER becomes a LESSON
    no parts, many chapters ->  CHAPTER becomes a LESSON directly, no modules
    few, very long chapters ->  CHAPTER becomes a MODULE, its sections LESSONS

Concepts are then drawn from within each chapter, several per lesson, which is
the one level the book does not hand us.

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

# Below this a "chapter" is really a section of a longer argument, and the book
# is better modelled with chapters as modules.
LONG_CHAPTER_WORDS = 6000

# Fewer chapters than this and a flat lesson list is too thin to be a course.
MIN_CHAPTERS_FOR_FLAT = 5

# Concepts per lesson, matching the researched-course ladder so the two paths
# produce comparably sized study units.
CONCEPTS_PER_LESSON = (2, 4)


def choose_shape(book):
    """How this book should map onto the course ladder.

    Returns {"shape", "why", "modules", "lessons"} — always with a reason,
    because "why is this course shaped like this" must be answerable from the
    record rather than reverse-engineered.
    """
    chapters = book.chapters
    parts = book.parts
    n = len(chapters)
    mean_words = (sum(c.words for c in chapters) / n) if n else 0

    if parts and len(parts) >= 2:
        return {
            "shape": "parts_as_modules",
            "why": f"the book has {len(parts)} parts, which are the author's "
                   f"own top-level grouping",
            "modules": len(parts),
            "lessons": n,
        }

    if n and mean_words >= LONG_CHAPTER_WORDS and n <= 15:
        return {
            "shape": "chapters_as_modules",
            "why": f"{n} chapters averaging {int(mean_words)} words — each is "
                   f"long enough to be a module, its sections lessons",
            "modules": n,
            "lessons": None,
        }

    if n >= MIN_CHAPTERS_FOR_FLAT:
        return {
            "shape": "chapters_as_lessons",
            "why": f"{n} chapters and no parts — a flat lesson sequence follows "
                   f"the book's own order",
            "modules": 0,
            "lessons": n,
        }

    return {
        "shape": "chapters_as_lessons",
        "why": f"only {n} chapter(s); using them as lessons and relying on "
               f"concepts for depth",
        "modules": 0,
        "lessons": n,
    }


def _uid(prefix):
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def build_structure(book, course_title=None, concepts_per_lesson=3):
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
                for i in range(concepts_per_lesson)
            ],
        }

    if shape["shape"] == "parts_as_modules":
        for m_i, part in enumerate(book.parts, 1):
            chs = [c for c in book.chapters if c.part == part]
            module = {"uid": _uid("mod"), "title": part, "ordinal": m_i,
                      "units": [{"uid": _uid("unit"), "title": part,
                                 "ordinal": 1,
                                 "lessons": [_lesson(c, i + 1)
                                             for i, c in enumerate(chs)]}]}
            course["modules"].append(module)
        # Chapters before the first part heading would otherwise be lost.
        orphans = [c for c in book.chapters if not c.part]
        if orphans:
            course["modules"].insert(0, {
                "uid": _uid("mod"), "title": "Introduction", "ordinal": 0,
                "units": [{"uid": _uid("unit"), "title": "Introduction",
                           "ordinal": 1,
                           "lessons": [_lesson(c, i + 1)
                                       for i, c in enumerate(orphans)]}]})

    elif shape["shape"] == "chapters_as_modules":
        for m_i, ch in enumerate(book.chapters, 1):
            # Long chapters get several lessons over the same chapter text;
            # hydration splits the material by concept rather than by heading,
            # because a chapter's internal headings are unreliable across books.
            n_lessons = max(2, min(4, ch.words // 3000))
            unit = {"uid": _uid("unit"), "title": ch.title, "ordinal": 1,
                    "lessons": []}
            for l_i in range(n_lessons):
                lesson = _lesson(ch, l_i + 1)
                lesson["title"] = f"{ch.title} ({l_i + 1}/{n_lessons})"
                lesson["chapter_part"] = [l_i, n_lessons]
                unit["lessons"].append(lesson)
            course["modules"].append({"uid": _uid("mod"), "title": ch.title,
                                      "ordinal": m_i, "units": [unit]})

    else:  # chapters_as_lessons
        course["modules"].append({
            "uid": _uid("mod"), "title": course["title"], "ordinal": 1,
            "units": [{"uid": _uid("unit"), "title": course["title"],
                       "ordinal": 1,
                       "lessons": [_lesson(c, i + 1)
                                   for i, c in enumerate(book.chapters)]}]})

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
    try:
        storage.courses.create_course(course)
    except Exception as e:
        logger.error(f"[BOOK] could not store course: {e}")
        return None
    logger.info(f"[BOOK] built {course['uid']}: {summarise(course)}")
    return course
