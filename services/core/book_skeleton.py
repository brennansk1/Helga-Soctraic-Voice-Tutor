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
import os

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
    # A TEXTBOOK KEEPS THE MODULE LEVEL, because its chapters really are study
    # blocks with a curriculum behind them — unlike a novel's parts, which are
    # an author's dramatic division. Read from the book's own table of contents:
    # a level above the leaf means the author built a ladder, not a list.
    if getattr(book, "hierarchical", False) and parts and len(parts) >= 2:
        return {
            "shape": "textbook",
            "why": f"the book's table of contents is a ladder — {len(parts)} "
                   f"chapters over {n} sections, so chapters become modules and "
                   f"sections become lessons",
            "modules": len(parts), "units": len(parts), "lessons": n,
        }

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

    if shape["shape"] == "textbook":
        # Chapter -> module, its sections -> lessons under one unit.
        for m_i, chapter_name in enumerate(book.parts, 1):
            secs = [c for c in book.chapters if c.part == chapter_name]
            if not secs:
                continue
            course["modules"].append({
                "uid": _uid("mod"), "title": chapter_name, "ordinal": m_i,
                "units": [{"uid": _uid("unit"), "title": chapter_name,
                           "ordinal": 1,
                           "lessons": [_lesson(c, i + 1)
                                       for i, c in enumerate(secs)]}]})
        orphans = [c for c in book.chapters if not c.part]
        if orphans:
            course["modules"].append({
                "uid": _uid("mod"), "title": "Additional Material",
                "ordinal": len(course["modules"]) + 1,
                "units": [{"uid": _uid("unit"), "title": "Additional Material",
                           "ordinal": 1,
                           "lessons": [_lesson(c, i + 1)
                                       for i, c in enumerate(orphans)]}]})
        logger.info(f"[BOOK] shape={shape['shape']} — {shape['why']}")
        return course

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
                    concepts_per_lesson=None, max_pages=None,
                    status_callback=None, requested_concepts=None):
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

    def _say(msg):
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass  # a progress line must never cost the build

    book = open_book(path, max_pages=max_pages)
    if not book:
        logger.warning(f"[BOOK] could not read {path}")
        _say(f"BOOK:UNREADABLE:{os.path.basename(path)}")
        return None
    o = book.outline()
    logger.info(f"[BOOK] {book.title!r}: format={o['format']} "
                f"chapters={o['chapters']} parts={len(o['parts'])} "
                f"words={o['words']:,}")
    _say(f"BOOK:PARSED:{o['format']}:{o['chapters']}:{len(o['parts'])}:{o['words']}")

    # SMART STRETCH FOR BOOKS.
    #
    # The doc path assessed scope from the first; this one did not, so a
    # six-chapter book asked to carry a degree would have been padded into one
    # — the exact failure `check_filler` exists to catch, arriving one stage
    # earlier. A book's chapters are honest evidence of how much material the
    # subject has, so the same check applies.
    if requested_concepts:
        try:
            from services.core.scope_fit import assess_scope, describe
            scope = assess_scope(book.as_brief(), requested_concepts)
            course_scope = scope
            _say(f"BOOK:SCOPE:{scope['verdict']}:{describe(scope)[:120]}")
        except Exception as e:
            logger.warning(f"[BOOK] scope assessment failed: {e}")
            course_scope = None
    else:
        course_scope = None

    # SYNTHESISE A COURSE SHAPE FOR BOOKS TOO.
    #
    # I argued the book path should preserve source structure because "the
    # author sequenced it". That conflated two different things. An author
    # decides SEQUENCE — Pro Git's chapter order is deliberate and worth
    # keeping — but an author writes CHAPTERS, not modules and units. Measured
    # on Pro Git (15 chapters, no parts): `choose_shape` fell through to
    # `chapters_as_lessons` and produced 1 module, 1 unit, 15 lessons, which is
    # out of SCHOOL_SHAPE's band on both axes. A flat chapter list is a table
    # of contents, not a curriculum.
    #
    # So the book's ORDER is preserved (pages are handed over in chapter
    # order, and the synthesiser is told to respect it) while the SHAPE is
    # designed.
    course = None
    if llm_json_fn and len(book.chapters) >= 4:
        try:
            from services.core import doc_curriculum
            pages = [{"title": ch.title, "section": ch.part or "",
                      "url": f"chapter:{ch.order}", "text": ch.text,
                      "code_blocks": ch.text.count("```") // 2}
                     for ch in book.chapters]
            cur = doc_curriculum.propose(
                pages, course_title or book.title, llm_json_fn,
                goal=(f"work through {book.title} and be able to do what it "
                      f"teaches"),
                status_callback=status_callback)
            if cur:
                rep = doc_curriculum.shape_report(
                    cur, subject=course_title or book.title)
                _say(f"BOOK:CURRICULUM_SHAPE:{rep['modules']}:{rep['units']}:"
                     f"{rep['lessons']}:{'ok' if rep['ok'] else 'loose'}")
                course = _course_from_curriculum(
                    cur, book, course_title or book.title,
                    concepts_per_lesson)
                course["doc_curriculum"] = {"coverage": cur["coverage"],
                                            "shape": rep,
                                            "target": cur["target"]}
        except Exception as e:
            logger.warning(f"[BOOK] curriculum synthesis failed: {e}")

    if course is None:
        course = build_structure(book, course_title=course_title,
                                 concepts_per_lesson=concepts_per_lesson)
        if llm_json_fn and len(book.chapters) >= 4:
            course["shape_degraded"] = True
            course["shape_degraded_why"] = (
                "curriculum synthesis did not run; this course follows the "
                "book's own chapter list, not a designed curriculum")
    if course_scope:
        course["book_scope"] = course_scope
    _say(f"BOOK:SHAPE:{course['book_shape']['shape']}:"
         f"{course['book_shape']['why']}")

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
                course, book, llm_json_fn, per_lesson=concepts_per_lesson,
                status_callback=status_callback)
        except Exception as e:
            logger.warning(f"[BOOK] concept naming failed: {e}")
    else:
        logger.info("[BOOK] no LLM supplied — concepts left unnamed")

    # Same stamp as the doc path — a book-sourced CS course was getting no
    # domain at all, so no code examples and no tutor guidance.
    try:
        from services.domains.registry import (for_domain, classify_course)
        dk = classify_course(course, course_title or book.title,
                             llm_json_fn=llm_json_fn)
        ext = for_domain(dk)
        if ext and hasattr(ext, "classify_concepts"):
            course["concept_kinds"] = ext.classify_concepts(
                course, book, llm_json_fn=llm_json_fn,
                status_callback=status_callback)
        if ext and hasattr(ext, "attach_to_course"):
            course["code_examples"] = ext.attach_to_course(
                course, book, status_callback=status_callback)
    except Exception as e:
        logger.warning(f"[BOOK] domain asset pass failed: {e}")

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


def build_from_docs(subject_or_url, storage, course_title=None, llm_json_fn=None,
                    concepts_per_lesson=None, max_pages=None, max_depth=None,
                    fetch=None, status_callback=None, requested_concepts=None,
                    learner_goal=None, level="college"):
    """A documentation website becomes a course, through the book pipeline.

    WHY THIS REUSES build_from_book RATHER THAN DUPLICATING IT
    ----------------------------------------------------------
    A documentation site is a book: its nav tree is a table of contents, its
    sections are parts, its pages are chapters. `doc_reader.to_book` produces a
    real `book_reader.Book`, so everything that already makes a book-sourced
    course good applies unchanged — shape chosen with a recorded *why*, concepts
    named by READING the page rather than recalled from the title, hydration
    that quotes the source, and `book_course_qa` at the end.

    Inventing a third pipeline would have meant re-deriving all of that and
    getting a different, worse answer.

    SMART STRETCH
    -------------
    The crawl reports how much material the doc set HAS (not merely how much was
    fetched), so `scope_fit` can judge the ask before compute is spent. Measured
    on dbt: 492 documentation pages, which `supportable_courses` reads as ~20
    courses of real material — degree scale from documentation alone. A thin doc
    set reports ~0.5 and is refused rather than padded.

    The scope assessment is attached to the course as `doc_scope` and returned
    even when the verdict is poor; the CALLER decides whether to proceed, offer
    a smaller shape, or supplement from the researched path. This function does
    not silently downgrade a request.

    `fetch(url) -> html or None` is injected so callers own rate limiting,
    caching and user-agent, and so this is testable without a network.
    """
    try:
        from services.research import doc_reader
    except ImportError:                      # pragma: no cover - path variance
        import doc_reader

    def _say(msg):
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

    # DOMAIN-PREFERRED SOURCE, BEFORE RESOLVING A CRAWL TARGET.
    #
    # This ran AFTER `resolve()` and its early return, so a subject that is in
    # DevDocs but not in KNOWN_DOCS — which is most of the 824 — aborted with
    # "no documentation known" and never reached the domain at all. Measured on
    # `redux`: DevDocs has 7 pages of it and the build returned None.
    #
    # DevDocs needs no crawlable URL, which is the entire point of it, so the
    # domain gets asked first and `resolve()` only has to succeed when the
    # domain has nothing.
    #
    # A domain may know a better source than the open web. For computer
    # science that is DevDocs: 824 technologies, already cleaned, already
    # grouped into editorial sections, one request instead of a 491-page
    # crawl. Asked for `python` it returns pages; asked for `recursion` it
    # says CONCEPT and declines, because a concept has no authoritative site
    # and crawling for one finds a blog.
    domain_pages, domain_meta = [], {}
    try:
        from services.domains.registry import for_subject as _fs
        # PASS THE MODEL. `domain_for` matches keywords first, and no keyword
        # list covers 824 DevDocs technologies — "redux" is not in it, so the
        # domain never resolved, DevDocs was never asked, and the build aborted
        # with "no documentation known" for a subject DevDocs has 7 pages of.
        # The LLM classifier exists precisely for the tail and was never wired
        # in here.
        _ext = _fs(str(subject_or_url), llm_json_fn=llm_json_fn)
        if _ext and hasattr(_ext, "source_for"):
            from services.research import doc_reader as _dr
            kind, domain_pages, domain_meta = _ext.source_for(
                str(subject_or_url),
                doc_resolver=lambda s: _dr.resolve(s, fetch=fetch))
            _say(f"DOCS:SOURCE_KIND:{kind}")
            if kind == "CONCEPT":
                logger.info(f"[DOCS] {subject_or_url!r} is a CONCEPT — no "
                            f"authoritative documentation; use the researched "
                            f"path")
                _say(f"DOCS:CONCEPT:{subject_or_url}")
                return None
    except Exception as e:
        logger.warning(f"[DOCS] domain source lookup failed: {e}")

    entry = (subject_or_url if str(subject_or_url).startswith("http")
             else doc_reader.resolve(subject_or_url))
    if not entry and not domain_pages:
        logger.warning(f"[DOCS] no documentation known for {subject_or_url!r}")
        _say(f"DOCS:UNKNOWN:{subject_or_url}")
        return None

    if fetch is None:
        fetch = _default_fetch

    _say(f"DOCS:CRAWL:{entry or domain_meta.get('slug') or subject_or_url}")
    kw = {}
    if max_pages is not None:
        kw["max_pages"] = max_pages
    if max_depth is not None:
        kw["max_depth"] = max_depth
    if domain_pages:
        docset = doc_reader.DocSet(domain_meta.get("slug") or entry,
                                   domain_pages,
                                   available_pages=len(domain_pages))
        logger.info(f"[DOCS] using {domain_meta.get('source')} "
                    f"({domain_meta.get('slug')}): {len(domain_pages)} pages")
    else:
        docset = doc_reader.crawl(entry, fetch, **kw)
    if not docset.pages:
        logger.warning(f"[DOCS] nothing readable at {entry}")
        _say(f"DOCS:EMPTY:{entry}")
        return None

    mat = docset.material
    _say(f"DOCS:MATERIAL:{mat['pages_fetched']}:{mat['pages_available']}:"
         f"{mat['code_blocks']}:{mat['sections']}")

    scope = None
    if requested_concepts:
        try:
            from services.core.scope_fit import assess_scope, describe
            scope = assess_scope(docset.as_brief(), requested_concepts)
            _say(f"DOCS:SCOPE:{scope['verdict']}:{describe(scope)[:120]}")
        except Exception as e:
            logger.warning(f"[DOCS] scope assessment failed: {e}")

    # SEQUENCE BEFORE SHAPING. Documentation is organised for REFERENCE: its
    # nav is grouped by product surface, and its sitemap — the only way to
    # enumerate a JS-rendered site — is emitted in ALPHABETICAL path order.
    # Taken literally that produced a real dbt course teaching "Using defer in
    # dbt" as lesson 2, with no installation lesson anywhere.
    #
    # A book needs no such step: its author sequenced it. A doc set has no
    # author's sequence, so one has to be imposed, and the reasoning is recorded
    # on the course so the ordering is auditable rather than mysterious.
    ordered_pages, seq_report = doc_reader.sequence(docset.pages)
    docset.pages = ordered_pages
    _say(f"DOCS:SEQUENCED:{seq_report['junk_dropped']}:"
         f"{len(seq_report.get('tiers', {}))}")

    book = doc_reader.to_book(docset, title=course_title or str(subject_or_url))
    if not book:
        return None
    o = book.outline()
    logger.info(f"[DOCS] {book.title!r}: {o['chapters']} chapter(s), "
                f"{len(o['parts'])} part(s), {o['words']:,} words, "
                f"{mat['code_blocks']} code block(s)")
    _say(f"BOOK:PARSED:docs:{o['chapters']}:{len(o['parts'])}:{o['words']}")

    # SYNTHESISE A CURRICULUM, DON'T MIRROR THE DOC TREE.
    #
    # `build_structure` shapes a course like its source, which is right for a
    # book (the author sequenced it) and wrong for documentation (nobody did).
    # Mirroring produced 1 module and 36 units named after dbt's product lines
    # — Fusion, Dbt Ai, Mesh — instead of the capabilities a course teaches.
    # Synthesis produced 5 balanced modules matching a real dbt curriculum.
    #
    # Falls back to the structural path when the model is unavailable or
    # returns nothing usable: a worse shape is better than no course.
    course = None
    if llm_json_fn:
        try:
            from services.core import doc_curriculum
            cur = doc_curriculum.propose(
                docset.pages, str(subject_or_url), llm_json_fn,
                goal=learner_goal, status_callback=status_callback)
            if cur:
                rep = doc_curriculum.shape_report(cur,
                                                  subject=str(subject_or_url))
                _say(f"DOCS:CURRICULUM_SHAPE:{rep['modules']}:{rep['units']}:"
                     f"{rep['lessons']}:{'ok' if rep['ok'] else 'loose'}")
                course = _course_from_curriculum(cur, book, course_title
                                                 or book.title,
                                                 concepts_per_lesson)
                course["doc_curriculum"] = {"coverage": cur["coverage"],
                                            "shape": rep,
                                            "target": cur["target"]}
        except Exception as e:
            logger.warning(f"[DOCS] curriculum synthesis failed: {e}")

    if course is None:
        course = build_structure(book, course_title=course_title or book.title,
                                 concepts_per_lesson=concepts_per_lesson)
        # FLAGGED ON THE COURSE ITSELF. A doc course that fell back to the
        # source's own shape is not the same product as a synthesised one —
        # measured, the fallback gave 1 module and 36 units named after dbt's
        # product lines. It shipped looking finished. Anything reading this
        # course can now tell, and `summarise` prints it.
        if llm_json_fn:
            course["shape_degraded"] = True
            course["shape_degraded_why"] = (
                "curriculum synthesis did not run; this course follows the "
                "documentation's own structure, not a designed curriculum")
            logger.warning("[DOCS] shipping a course with shape_degraded=True")
        _say(f"BOOK:SHAPE:{course['book_shape']['shape']}:"
             f"{course['book_shape']['why']}")

    if llm_json_fn:
        try:
            try:
                from services.core.book_source import attach_concepts
            except ImportError:
                from book_source import attach_concepts
            course["book_concepts"] = attach_concepts(
                course, book, llm_json_fn, per_lesson=concepts_per_lesson,
                status_callback=status_callback)
        except Exception as e:
            logger.warning(f"[DOCS] concept naming failed: {e}")
    else:
        logger.info("[DOCS] no LLM supplied — concepts left unnamed")

    # DOMAIN ASSETS. For a CS subject this attaches one vetted code example per
    # code-shaped concept, drawn from the concept's OWN source chapter and
    # deduplicated across the course — the code analogue of the whole-course
    # figure pass. Routed through the registry so a history course gets nothing
    # rather than SQL.
    try:
        from services.domains.registry import (for_domain, classify_course)
        dk = classify_course(course, f"{course_title or ''} {subject_or_url}",
                             llm_json_fn=llm_json_fn)
        ext = for_domain(dk)
        # CLASSIFY BEFORE ATTACHING ASSETS. `code_examples` only fires for
        # code-shaped kinds, so an UNKNOWN concept silently gets no aid — and
        # on a real dbt course 25 of 40 were UNKNOWN from titles alone.
        # Reading the source first turns those into real kinds, which is what
        # decides both the aid AND the tutor's teaching instruction.
        if ext and hasattr(ext, "classify_concepts"):
            ctally = ext.classify_concepts(course, book,
                                           llm_json_fn=llm_json_fn,
                                           status_callback=status_callback)
            course["concept_kinds"] = ctally
            _say(f"DOCS:KINDS:{ctally.get('by_pattern',0)}:"
                 f"{ctally.get('by_reading',0)}:{ctally.get('unknown',0)}")
        if ext and hasattr(ext, "attach_to_course"):
            tally = ext.attach_to_course(course, book,
                                         status_callback=status_callback)
            course["code_examples"] = tally
            _say(f"DOCS:CODE_EXAMPLES:{tally.get('examples', 0)}")
    except Exception as e:
        logger.warning(f"[DOCS] domain asset pass failed: {e}")

    course["status"] = "skeleton"
    course["source_path"] = entry
    course["source_kind"] = "documentation"
    course["doc_material"] = mat
    course["doc_sequencing"] = seq_report

    # PROVENANCE — which book this course was actually built from.
    #
    # Recorded because a single book sets a course's whole account of a
    # subject and nothing persisted which one. That matters most exactly where
    # it is most contested: a history course built from one survey text carries
    # that text's historiographical posture, and a learner who can see "built
    # from U.S. History (American YAWP)" can weigh it for themselves. The
    # honest answer to "why does this course read that way" is the source, and
    # finding it should not require reading the logs.
    if domain_meta:
        course["source_provenance"] = {
            "source": domain_meta.get("source"),
            "book": domain_meta.get("name"),
            "url": domain_meta.get("url"),
            "library": domain_meta.get("library"),
            "licence": domain_meta.get("licence"),
            "pages_used": len(domain_pages),
            "pages_available": domain_meta.get("available_pages"),
        }
        _say(f"DOCS:SOURCE:{domain_meta.get('source')}:"
             f"{domain_meta.get('name')}")

    if scope:
        course["doc_scope"] = scope
    course["_book"] = book
    stored = {k: v for k, v in course.items() if not k.startswith("_")}
    try:
        storage.courses.create_course(stored)
    except Exception as e:
        logger.error(f"[DOCS] could not store course: {e}")
        return None
    logger.info(f"[DOCS] built {course['uid']}: {summarise(course)}")
    return course


def _default_fetch(url):
    """Plain HTTP GET. Callers with a session/cache should inject their own."""
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Helga-Research/1.0 (course builder)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status == 200:
                return r.read().decode("utf-8", "replace")
    except Exception as e:
        logger.debug(f"[DOCS] fetch failed {url}: {e}")
    return None


def _course_from_curriculum(cur, book, title, concepts_per_lesson=None):
    """Build the course dict from a synthesised curriculum.

    Each lesson keeps `book_chapter` pointing at its FIRST source page, so
    hydration and concept-naming read real text — the whole pipeline downstream
    is unchanged, it just receives a pedagogical tree instead of a mirrored one.
    """
    n = concepts_per_lesson or 3
    by_title = {c.title: c for c in book.chapters}
    course = {"uid": _uid("course"), "title": title, "modules": []}
    for m in cur["modules"]:
        mod = {"uid": _uid("mod"), "title": m["title"], "units": []}
        for u in m["units"]:
            unit = {"uid": _uid("unit"), "title": u["title"], "lessons": []}
            for i, l in enumerate(u["lessons"]):
                first = (l.get("pages") or [{}])[0]
                ch = by_title.get(first.get("title"))
                lesson = {
                    "uid": _uid("less"), "title": l["title"], "ordinal": i + 1,
                    "book_chapter": ch.order if ch else None,
                    "source_pages": [p.get("url") for p in (l.get("pages") or [])],
                    "concepts": [{"uid": _uid("con"), "title": "",
                                  "ordinal": k + 1,
                                  "book_chapter": ch.order if ch else None,
                                  "from_book": True} for k in range(n)],
                }
                unit["lessons"].append(lesson)
            if unit["lessons"]:
                mod["units"].append(unit)
        if mod["units"]:
            course["modules"].append(mod)
    course["book_shape"] = {
        "shape": "synthesised_curriculum",
        "why": (f"documentation has no author's sequence, so a curriculum of "
                f"{len(course['modules'])} modules was designed from "
                f"{cur['coverage']['pages_used']} pages"),
    }
    return course
