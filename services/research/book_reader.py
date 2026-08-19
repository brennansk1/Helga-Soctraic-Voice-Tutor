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
    r"|title page|cover|preface|foreword|epigraph|notes)\s*$",
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

    def outline(self):
        return {
            "title": self.title,
            "format": self.format,
            "chapters": len(self.chapters),
            "parts": self.parts,
            "words": sum(c.words for c in self.chapters),
            "chapter_titles": [c.title for c in self.chapters],
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


def _clean(text):
    text = re.sub(r"\r\n?", "\n", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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
            text = _clean(soup.get_text("\n"))
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


def _pdf_chapters(path, max_pages=None):
    """PDF chapters from the table of contents, falling back to page blocks."""
    import fitz
    doc = fitz.open(path)
    toc = doc.get_toc() or []
    chapters, part = [], None

    if toc:
        entries = [(lvl, title, pg) for lvl, title, pg in toc if pg > 0]
        for i, (lvl, title, pg) in enumerate(entries):
            end = entries[i + 1][2] - 1 if i + 1 < len(entries) else len(doc)
            kind, label = _classify_heading(title)
            if kind == "part" or lvl == 1 and _PART_RE.match(title or ""):
                part = label or title
                continue
            if _SKIP_TITLES.match((title or "").strip()):
                continue
            text = []
            for p in range(max(0, pg - 1), min(len(doc), end)):
                try:
                    text.append(doc[p].get_text())
                except Exception:
                    pass
            body = _clean("\n".join(text))
            if len(body) < MIN_CHAPTER_CHARS:
                continue
            chapters.append(Chapter(title, body, len(chapters) + 1, part=part))

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


def open_book(path, max_pages=None):
    """Parse any supported book into a `Book`. Returns None if unreadable."""
    if not path or not os.path.exists(path):
        logger.warning(f"[BOOK] not found: {path}")
        return None
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
    if not chapters:
        logger.warning(f"[BOOK] no chapters found in {path}")
        return None
    logger.info(f"[BOOK] {title!r}: {len(chapters)} chapter(s), "
                f"{len({c.part for c in chapters if c.part})} part(s)")
    return Book(title, chapters, source_path=path, fmt=fmt)
