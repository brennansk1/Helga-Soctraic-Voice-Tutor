"""Reading a book — any book — as a structured, chunkable source.

WHY THIS EXISTS
---------------
"Upload a book and get a course from it" is the product's clearest promise, and
until now the pipeline could only *search* for material. This reads the book
itself: its structure, so the course can be shaped like it, and its text, so
hydration can quote it rather than recall it.

NOT ONLY TEXTBOOKS
------------------
A textbook has parts, chapters and numbered sections. A self-help book has
chapters and no parts. A programming book has parts that are really topics. A
memoir has nothing but chapters. All of them are legitimate course material, and
a pipeline that only understands textbook shape will mangle the rest.

So the structure is READ, not assumed, and the course shape adapts to what the
book actually has:

    Part I / Part II present   ->  parts become MODULES, chapters become LESSONS
    no parts, many chapters    ->  chapters become LESSONS directly
    few long chapters          ->  chapters become MODULES, sections become LESSONS

WHY NO NEW DEPENDENCY
---------------------
EPUB is a ZIP of XHTML with a manifest; PDF is already handled by PyMuPDF, which
is installed for figure extraction. `ebooklib` would add a dependency to do what
forty lines of `zipfile` and BeautifulSoup already do — and this project has
twice paid the cost of adopting a library and removing it later.

CHUNKING IS FOR READING, NOT FOR RETRIEVAL
------------------------------------------
Chunks here are chapter-scoped and ordered, because hydration reads a chapter to
write its lessons. That is a different job from semantic retrieval over a corpus,
and conflating them produces chunks that are wrong for both.
"""

import logging
import os
import re
import zipfile

logger = logging.getLogger(__name__)

# A chapter shorter than this is almost certainly front matter, a dedication, or
# a section divider rather than teachable content.
MIN_CHAPTER_CHARS = 1200

# Roman and written numerals show up in part headings far more than digits.
_PART_RE = re.compile(
    r"^\s*(?:part|section|book|unit)\s+"
    r"(?:([0-9]{1,2})|([ivxlc]{1,6})|(one|two|three|four|five|six|seven|eight|nine|ten))"
    r"\b[\s:.–—-]*(.*)$",
    re.IGNORECASE)

_CHAPTER_RE = re.compile(
    r"^\s*(?:chapter|ch\.?|lesson|module)\s+"
    r"(?:([0-9]{1,3})|([ivxlc]{1,6}))\b[\s:.–—-]*(.*)$",
    re.IGNORECASE)

# Front and back matter that is never a lesson.
# Distribution boilerplate that is never content. Gutenberg's markers in
# particular sit inside the text body, not in metadata.
_BOILERPLATE = re.compile(
    r"(\*\*\*\s*(?:START|END) OF (?:THE|THIS) PROJECT GUTENBERG"
    r"|project gutenberg (?:ebook|literary archive)"
    r"|produced by .{0,60}(?:distributed proofread|online distributed))",
    re.IGNORECASE)

# A chapter heading ANYWHERE in a block of text, used to split documents that
# pack several chapters together.
_CHAPTER_SPLIT = re.compile(
    r"(?:^|\n)\s*(CHAPTER|Chapter)\s+([0-9]{1,3}|[IVXLC]{1,7})\.?\s*(?=\n|$)")

_SKIP_TITLES = re.compile(
    r"^\s*(contents?|table of contents|copyright|dedication|acknowledge?ments?"
    r"|about the author|index|glossary|bibliography|references|colophon"
    r"|title page|cover|preface|foreword|epigraph|notes"
    # A BARE "Introduction" is a chapter opener, not a lesson — measured at 230
    # words in a real textbook, against sections of 2,500-5,700. Anchored, so
    # "Introduction to Biology" (a real chapter) is untouched.
    r"|introduction|summary|chapter summary|key terms|review questions"
    r"|critical thinking questions|visual connection questions)\s*$",
    re.IGNORECASE)


class Chapter:
    def __init__(self, title, text, order, part=None, level=1):
        self.title = (title or "").strip()
        self.text = text or ""
        self.order = order
        self.part = part
        self.level = level

    @property
    def chars(self):
        return len(self.text)

    @property
    def words(self):
        return len(self.text.split())

    def chunks(self, size=6000, overlap=400):
        """Ordered, overlapping windows over this chapter's text.

        Overlap so a paragraph split across a boundary is still whole in one of
        them — a concept explained across a chunk edge is otherwise invisible to
        whichever half the model reads.
        """
        t = self.text
        if len(t) <= size:
            return [t] if t.strip() else []
        out, start = [], 0
        while start < len(t):
            end = min(len(t), start + size)
            # Prefer a paragraph break near the end so chunks split cleanly.
            if end < len(t):
                brk = t.rfind("\n\n", start + size // 2, end)
                if brk > 0:
                    end = brk
            out.append(t[start:end])
            if end >= len(t):
                break
            start = max(start + 1, end - overlap)
        return out

    def __repr__(self):
        return f"<Chapter {self.order}: {self.title[:40]!r} {self.words}w>"


class Book:
    """A parsed book: ordered chapters, optional parts, chunkable text."""

    def __init__(self, title, chapters, source_path=None, fmt=None):
        self.title = title or ""
        self.chapters = chapters or []
        self.source_path = source_path
        self.format = fmt

    @property
    def parts(self):
        """Ordered distinct part names, or [] when the book has none."""
        seen, out = set(), []
        for c in self.chapters:
            if c.part and c.part not in seen:
                seen.add(c.part)
                out.append(c.part)
        return out

    @property
    def hierarchical(self):
        """Did the book's own table of contents have a level above the leaf?

        A textbook groups sections under numbered chapters; a novel does not.
        That difference decides whether the course gets modules, so it is read
        from the source rather than guessed from the title.
        """
        return any((c.level or 1) > 1 for c in self.chapters) and bool(self.parts)

    def outline(self):
        return {
            "title": self.title,
            "format": self.format,
            "chapters": len(self.chapters),
            "parts": self.parts,
            "hierarchical": self.hierarchical,
            "words": sum(c.words for c in self.chapters),
            "chapter_titles": [c.title for c in self.chapters],
        }

    def as_brief(self):
        """A `scope_fit`-shaped brief — smart stretch for book-sourced courses.

        `assess_scope` reads `chapter_count` and `structural_sources`. A book's
        chapters ARE its syllabus: an author decided how much material the
        subject needs and wrote that much, which is exactly the evidence the
        scope check wants.

        `structural_sources` is 1. One book is one account of how a subject is
        organised, however many chapters it runs to — claiming more would
        inflate confidence the same way `domain_sources` warns about. It also
        means a 6-chapter book asked to carry a degree is correctly reported as
        unsupported rather than being padded into one, which is the whole
        purpose of the check.
        """
        return {
            "chapter_count": len(self.chapters),
            "structural_sources": 1 if self.chapters else 0,
            "degraded": not self.chapters,
            "source": "book",
            "entry_url": self.source_path,
        }

    def chapter(self, order):
        for c in self.chapters:
            if c.order == order:
                return c
        return None

    def find(self, needle):
        """Chapters whose title matches — how hydration locates its source."""
        n = (needle or "").lower()
        if not n:
            return []
        words = {w for w in re.findall(r"[a-z]+", n) if len(w) > 3}
        hits = []
        for c in self.chapters:
            t = c.title.lower()
            cw = {w for w in re.findall(r"[a-z]+", t) if len(w) > 3}
            if n in t or (words and cw and len(words & cw) / len(words) >= 0.5):
                hits.append(c)
        return hits


# --- parsing -----------------------------------------------------------------

def _split_on_chapters(text, start_order, part=None):
    """Split one blob into chapters when it packs several.

    MEASURED: Gutenberg's Pride and Prejudice EPUB puts roughly nine chapters
    per XHTML document, so taking one chapter per file found 7 of 61 — and the
    "title" of each was whatever illustration caption happened to carry the
    first heading. A document is not a chapter.
    """
    marks = list(_CHAPTER_SPLIT.finditer(text or ""))
    if len(marks) < 2:
        return []
    out = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[start:end].strip()
        if len(body) < MIN_CHAPTER_CHARS:
            continue
        title = f"Chapter {m.group(2).upper()}"
        out.append(Chapter(title, body, start_order + len(out), part=part))
    return out


_ROMAN = {"i":1,"ii":2,"iii":3,"iv":4,"v":5,"vi":6,"vii":7,"viii":8,"ix":9,
          "x":10,"xi":11,"xii":12,"xiii":13,"xiv":14,"xv":15,"xvi":16,
          "xvii":17,"xviii":18,"xix":19,"xx":20}


def _chapter_number(title):
    """The number a chapter heading claims, or None."""
    m = re.search(r"(?:chapter|ch\.?|section|part)\s+([0-9]{1,3}|[ivxlc]{1,7})\b",
                  title or "", re.IGNORECASE)
    if not m:
        return None
    tok = m.group(1).lower()
    if tok.isdigit():
        return int(tok)
    return _ROMAN.get(tok)


def _drop_front_matter(chapters):
    """Remove a leading block that is front matter wearing a chapter's title.

    MEASURED on Gutenberg's Art of War: the Gutenberg header, the translator's
    introduction and the table of contents come before chapter I as one
    14,000-word block, and the split titled it from the LAST ToC line it
    contained — "Chapter XIII. The Use of Spies". The book then began at its own
    final chapter, which for a course built to follow a book's order is the
    worst possible failure.

    The tell is arithmetic: a leading chapter whose claimed number is higher
    than the one that follows it is not where the book starts. Conservative on
    purpose — it only ever drops from the FRONT, and only when the numbers
    actually contradict the order.
    """
    if len(chapters) < 3:
        return chapters
    nums = [_chapter_number(c.title) for c in chapters]
    dropped = 0
    while (len(chapters) - dropped) >= 3:
        a, b = nums[dropped], nums[dropped + 1]
        if a is not None and b is not None and a > b:
            logger.info(f"[BOOK] dropping front matter titled "
                        f"{chapters[dropped].title[:48]!r} — it claims chapter "
                        f"{a} but is followed by {b}")
            dropped += 1
            continue
        break
    # The arithmetic tell only fires when the front matter WEARS a chapter
    # number. Gutenberg's Art of War (pglaf mirror edition) is the other
    # shape: six unnumbered items — "Preface to the Project Gutenberg Etext",
    # "The Commentators", "Apologies for War" — sit in front of a clean
    # "Chapter I", so every number comparison is against None and nothing is
    # dropped. The course then opened by teaching the Gutenberg boilerplate.
    #
    # When a book numbers its chapters from I, that is where the book starts.
    # Requiring the first numbered chapter to be 1, and requiring at least
    # three numbered chapters, keeps this from firing on a book whose
    # numbering is incidental — and it never drops anything at or after the
    # first numbered chapter.
    if not dropped:
        numbered = [(i, n) for i, n in enumerate(nums) if n is not None]
        if len(numbered) >= 3:
            first_idx, first_num = numbered[0]
            if first_num == 1 and first_idx > 0:
                for c in chapters[:first_idx]:
                    logger.info(f"[BOOK] dropping front matter {c.title[:48]!r} "
                                f"— it precedes chapter 1 and carries no "
                                f"chapter number of its own")
                dropped = first_idx

    if not dropped:
        return chapters
    kept = chapters[dropped:]
    for i, c in enumerate(kept, 1):
        c.order = i
    return kept


def _clean(text):
    """Normalise whitespace — EXCEPT inside fenced code blocks.

    Collapsing runs of spaces is right for prose and destroys code: measured on
    a Python chapter, a four-space-indented call argument came out with one
    space, and in Python indentation IS the syntax. YAML has the same property.
    So fenced blocks are lifted out, the prose is cleaned, and the blocks are
    put back byte-for-byte.
    """
    text = re.sub(r"\r\n?", "\n", text or "")
    blocks = []

    def _lift(m):
        blocks.append(m.group(0))
        return f"\x00FENCE{len(blocks) - 1}\x00"

    text = re.sub(r"```.*?```", _lift, text, flags=re.S)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    for i, b in enumerate(blocks):
        text = text.replace(f"\x00FENCE{i}\x00", b)
    return text.strip()


def _classify_heading(title):
    """(kind, label) for a heading — 'part', 'chapter' or None."""
    t = (title or "").strip()
    if not t:
        return None, None
    m = _PART_RE.match(t)
    if m:
        num = m.group(1) or m.group(2) or m.group(3) or ""
        rest = (m.group(4) or "").strip()
        return "part", f"{t[:60]}" if not rest else f"{rest[:60]}"
    if _CHAPTER_RE.match(t):
        return "chapter", t
    return None, t


def _text_preserving_code(soup):
    """Chapter text with fenced code blocks, rather than flattened prose.

    `soup.get_text("\n")` returns every character including the contents of
    `<pre><code>`, but it returns them as UNDIFFERENTIATED PROSE. For a novel
    that is correct and irrelevant. For a programming book it is the difference
    between material and mush: nothing downstream can tell a SELECT statement
    from a sentence about one, so hydration cannot quote code as code and the
    `code` visual aid — the thing that teaches programming Socratically — has
    nothing to build from.

    So `<pre>` blocks are replaced with fenced markdown BEFORE the flatten.
    Same fix, same reason, as `doc_reader._text_and_code`; a coding book and a
    documentation site fail identically without it.

    Mutates a copy of the tree, never raises: an extraction quirk in one
    chapter must not lose the book.
    """
    try:
        for pre in soup.find_all("pre"):
            # Shared with doc_reader: line-level elements make lines,
            # inline spans do not. Neither separator alone is correct.
            try:  # imported as a package (tests, other services)
                from services.research.doc_reader import _code_text
            except ImportError:  # container: this image mounts the modules flat at /app
                from doc_reader import _code_text
            code = _code_text(pre)
            if not (code or "").strip():
                continue
            pre.replace_with("\n```\n" + code.strip("\n") + "\n```\n")
    except Exception:                        # pragma: no cover - defensive
        pass
    return soup.get_text("\n")

def _epub_chapters(path):
    """EPUB is a ZIP of XHTML. Read the spine order, not the file order."""
    from bs4 import BeautifulSoup
    chapters, part = [], None
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        opf = next((n for n in names if n.lower().endswith(".opf")), None)
        order = []
        if opf:
            soup = BeautifulSoup(z.read(opf), "xml")
            base = os.path.dirname(opf)
            manifest = {i.get("id"): i.get("href")
                        for i in soup.find_all("item") if i.get("id")}
            for ref in soup.find_all("itemref"):
                href = manifest.get(ref.get("idref"))
                if href:
                    order.append(os.path.normpath(os.path.join(base, href)))
        if not order:
            order = [n for n in names
                     if n.lower().endswith((".xhtml", ".html", ".htm"))]

        for i, name in enumerate(order):
            if name not in names:
                continue
            try:
                soup = BeautifulSoup(z.read(name), "html.parser")
            except Exception:
                continue
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()
            head = soup.find(["h1", "h2"])
            title = (head.get_text(" ", strip=True) if head else "").strip()
            text = _clean(_text_preserving_code(soup))
            kind, label = _classify_heading(title)
            if kind == "part":
                # A part divider is usually its own near-empty document; it
                # names the part for everything that follows rather than being
                # a chapter itself.
                part = label or title
                if len(text) < MIN_CHAPTER_CHARS:
                    continue
            if _SKIP_TITLES.match(title or ""):
                continue
            if len(text) < MIN_CHAPTER_CHARS:
                continue
            inner = _split_on_chapters(text, len(chapters) + 1, part)
            if inner:
                chapters.extend(inner)
                continue
            if _BOILERPLATE.search(text[:600]):
                continue
            chapters.append(Chapter(title or f"Section {i+1}", text,
                                    len(chapters) + 1, part=part))
    return chapters


def _toc_spans(entries, n_pages):
    """Inclusive 1-based (first_page, last_page) for each table-of-contents entry.

    WHY NOT `entries[i + 1].page - 1`, WHICH IS WHAT THIS USED TO BE.
    ---------------------------------------------------------------
    That arithmetic assumes every entry starts on a later page than the one
    before it, and a real book breaks the assumption two ways:

      * A REFLOWED PDF puts §4.3 and §4.4 on the same page. `next - 1` then
        makes §4.3 end on the page BEFORE it starts, its body comes back empty,
        the length filter drops it, and the course loses a section without a
        trace — the surviving sections renumber over the gap so nothing even
        looks missing.
      * A BACKWARDS ENTRY — an appendix or an index whose bookmark points at an
        earlier page — makes the entry before it swallow, or lose, the rest of
        the book depending on which way the jump goes.

    So the boundary is taken from the set of distinct start pages rather than
    from the neighbouring entry: a section runs to the page before the next page
    any entry starts on. That is order-independent, so a non-monotonic ToC gets
    a sane span, and it never yields an empty range.

    Entries sharing a start page therefore share their text. That is what the
    book itself says — the two sections really are on that page — and giving
    both the shared page is honest where dropping one was not.
    """
    n_pages = max(1, int(n_pages or 1))
    starts = sorted({pg for _, _, pg in entries})
    next_start = {a: b for a, b in zip(starts, starts[1:])}
    spans = []
    for _, _, pg in entries:
        first = min(max(1, pg), n_pages)
        nxt = next_start.get(pg)
        last = (nxt - 1) if nxt else n_pages
        spans.append((first, min(max(first, last), n_pages)))
    return spans


def _pdf_chapters(path, max_pages=None):
    """PDF chapters from the table of contents, falling back to page blocks."""
    import fitz
    doc = fitz.open(path)
    # try/finally, because open_book's blanket handler turns any exception
    # from in here into a tidy `return None` — which converted a crash into
    # a leaked PyMuPDF handle on every malformed PDF a user ever uploads.
    try:
        toc = doc.get_toc() or []
        chapters, part = [], None

        if toc:
            entries = [(lvl, title, pg) for lvl, title, pg in toc if pg > 0]

            # A TEXTBOOK'S TABLE OF CONTENTS IS A LADDER, NOT A LIST.
            #
            # Measured on a real OpenStax biology export: 4 level-1 entries, 19
            # level-2 CHAPTERS ("Chapter 2: Chemistry of Life") and 86 level-3
            # SECTIONS ("Water", "Passive Transport"). Flattening those into one
            # list produced 83 "chapters" that were really chapters and sections
            # mixed together — so a chapter and one of its own sections became
            # siblings.
            #
            # The leaf level is what a learner actually studies in a sitting, so
            # leaves become the chapters here and their parent is recorded. The
            # skeleton then decides whether that parent is a module.
            by_level = {}
            for lvl, _, _ in entries:
                by_level[lvl] = by_level.get(lvl, 0) + 1
            # The deepest level with enough entries to be the real content level.
            leaf = max((l for l, n in by_level.items() if n >= 5), default=1)

            parent_of, current = {}, {}
            for i, (lvl, title, pg) in enumerate(entries):
                current[lvl] = title
                parent_of[i] = current.get(leaf - 1) if leaf > 1 else None

            spans = _toc_spans(entries, len(doc))
            dropped = []
            for i, (lvl, title, pg) in enumerate(entries):
                first, last = spans[i]
                kind, label = _classify_heading(title)
                if kind == "part" or (lvl == 1 and _PART_RE.match(title or "")):
                    part = label or title
                    continue
                # Only the content level becomes a chapter; the levels above it are
                # groupings and are carried on `part`.
                if leaf > 1 and lvl != leaf:
                    continue
                if _SKIP_TITLES.match((title or "").strip()):
                    continue
                text = []
                for p in range(first - 1, last):
                    try:
                        text.append(doc[p].get_text())
                    except Exception:
                        pass
                body = _clean("\n".join(text))
                if len(body) < MIN_CHAPTER_CHARS:
                    # Say which section went and why. `len(chapters) + 1` renumbers
                    # the survivors over the gap, so a silent drop here is invisible
                    # both in the course and in the logs — the one failure mode that
                    # cannot be noticed after the fact.
                    dropped.append((title, first, last, len(body)))
                    continue
                group = parent_of.get(i) or part
                chapters.append(Chapter(title, body, len(chapters) + 1, part=group,
                                        level=lvl))
            if dropped:
                logger.warning(
                    f"[BOOK] {len(dropped)} table-of-contents section(s) had less "
                    f"than {MIN_CHAPTER_CHARS} chars of text and are NOT in the "
                    f"course: " + "; ".join(f"{t!r} (pp. {a}-{b}, {n} chars)"
                                            for t, a, b, n in dropped[:8])
                    + (" ..." if len(dropped) > 8 else ""))

        if not chapters:
            # No usable ToC: fall back to whole-document text split on headings that
            # look like chapters. Weaker, and honest about being weaker.
            logger.info("[BOOK] no usable table of contents — falling back to "
                        "heading detection")
            full = _clean("\n".join(doc[p].get_text()
                                    for p in range(min(len(doc), max_pages or len(doc)))))
            parts = re.split(r"\n(?=\s*(?:chapter|CHAPTER)\s+[0-9IVXivx]+)", full)
            for i, seg in enumerate(parts):
                if len(seg) < MIN_CHAPTER_CHARS:
                    continue
                first = seg.strip().split("\n", 1)[0][:120]
                chapters.append(Chapter(first, seg, len(chapters) + 1))
    finally:
        doc.close()
    return chapters


def _text_chapters(path):
    """Plain text or Markdown, split on headings."""
    with open(path, "r", errors="replace") as fh:
        raw = fh.read()
    segs = re.split(r"\n(?=#{1,2}\s+|\s*(?:Chapter|CHAPTER|Part|PART)\s+[0-9IVXivx]+)",
                    _clean(raw))
    chapters, part = [], None
    for seg in segs:
        title = seg.strip().split("\n", 1)[0].lstrip("# ").strip()[:120]
        kind, label = _classify_heading(title)
        if kind == "part":
            part = label or title
            if len(seg) < MIN_CHAPTER_CHARS:
                continue
        if _SKIP_TITLES.match(title or "") or len(seg) < MIN_CHAPTER_CHARS:
            continue
        if _BOILERPLATE.search(seg[:800]):
            continue
        chapters.append(Chapter(title, seg, len(chapters) + 1, part=part))
    return chapters


#: Parsed books, keyed by (path, mtime, size). Small on purpose: a parsed book
#: holds every chapter's full text, so a 400-page textbook is tens of MB and an
#: unbounded dict here would repeat the mistake `_DIGEST_CACHE` made.
_BOOK_CACHE = {}
_BOOK_CACHE_MAX = 2


def open_book(path, max_pages=None):
    """Parse any supported book into a `Book`. Returns None if unreadable."""
    if not path or not os.path.exists(path):
        logger.warning(f"[BOOK] not found: {path}")
        return None
    # CACHED ON (path, mtime, size). `build_from_book` parses the upload and
    # `fsm_logic` parses it again when a session opens it, and `_pdf_chapters`
    # runs PyMuPDF over every page of a file that lives on an 11 MB/s external
    # drive. Keying on mtime+size means an edited file re-parses rather than
    # serving stale chapters.
    try:
        st = os.stat(path)
        ckey = (os.path.abspath(path), int(st.st_mtime), st.st_size, max_pages)
        hit = _BOOK_CACHE.get(ckey)
        if hit is not None:
            logger.debug(f"[BOOK] cache hit for {os.path.basename(path)}")
            return hit
    except Exception:                        # pragma: no cover - defensive
        ckey = None

    ext = os.path.splitext(path)[1].lower()
    title = os.path.splitext(os.path.basename(path))[0].replace("-", " ").replace("_", " ")
    try:
        if ext == ".epub":
            chapters, fmt = _epub_chapters(path), "epub"
        elif ext == ".pdf":
            chapters, fmt = _pdf_chapters(path, max_pages=max_pages), "pdf"
        elif ext in (".txt", ".md", ".markdown"):
            chapters, fmt = _text_chapters(path), "text"
        else:
            logger.warning(f"[BOOK] unsupported format {ext!r}")
            return None
    except Exception as e:
        logger.warning(f"[BOOK] parse failed for {path}: {e}")
        return None
    chapters = _drop_front_matter(chapters)
    if not chapters:
        logger.warning(f"[BOOK] no chapters found in {path}")
        return None
    logger.info(f"[BOOK] {title!r}: {len(chapters)} chapter(s), "
                f"{len({c.part for c in chapters if c.part})} part(s)")
    book = Book(title, chapters, source_path=path, fmt=fmt)
    if ckey is not None:
        while len(_BOOK_CACHE) >= _BOOK_CACHE_MAX:
            _BOOK_CACHE.pop(next(iter(_BOOK_CACHE)), None)
        _BOOK_CACHE[ckey] = book
    return book
