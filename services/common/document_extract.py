"""Document text extraction (sprint A3) — make "bring your own material" real.

WHY THIS EXISTS
---------------
Helga advertised two ways to supply your own source material, and neither read
a single byte of it:

1. EPUB upload. `/api/upload_epub` saved the file and forwarded
   "create course from epub {path}" to the FSM. `start_creation()` used that
   path ONLY to derive a topic from the filename — so uploading
   `organic_chemistry.epub` produced a generic course about the words "organic
   chemistry" from the model's own knowledge. The book was never opened.
2. Wizard per-module source files. `module_source_map` was populated in
   ContentHydrator.hydrate() and then never read by anything.

Both are worse than an absent feature: the user believes their material is
being taught and it isn't. This module supplies the missing step.

DEPENDENCIES
------------
Stdlib `zipfile`/`xml` plus BeautifulSoup, which is already installed — no new
dependency for an offline appliance. EPUB is a ZIP of XHTML, so this is
tractable without `ebooklib`.

PDF is deliberately NOT claimed. There is no PDF library available, and
returning empty text for a PDF while reporting success would recreate exactly
the bug this module fixes. `extract()` raises UnsupportedDocument instead.
"""

import logging
import os
import zipfile
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_OPF_NS = "{http://www.idpf.org/2007/opf}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst")
EPUB_SUFFIXES = (".epub",)
# Named so the error message can be specific about why.
UNSUPPORTED_SUFFIXES = (".pdf", ".doc", ".docx", ".mobi", ".azw", ".azw3")


class UnsupportedDocument(Exception):
    """Raised for formats we cannot honestly extract."""


class ExtractionFailed(Exception):
    """Raised when a supported format could not be parsed."""


def _html_to_text(markup):
    """Strip markup to readable text, dropping script/style noise."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - bs4 ships with the services
        raise ExtractionFailed("BeautifulSoup unavailable — cannot parse EPUB XHTML")
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _epub_spine_documents(z):
    """Return content file names in reading order.

    Falls back to every XHTML entry in archive order if the OPF can't be read —
    a slightly wrong order still beats losing the text entirely.
    """
    try:
        container = z.read("META-INF/container.xml")
        rootfile = ET.fromstring(container).find(f".//{_CONTAINER_NS}rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = os.path.dirname(opf_path)

        opf = ET.fromstring(z.read(opf_path))
        manifest = {}
        for item in opf.iter(f"{_OPF_NS}item"):
            manifest[item.get("id")] = item.get("href")

        ordered = []
        for ref in opf.iter(f"{_OPF_NS}itemref"):
            href = manifest.get(ref.get("idref"))
            if not href:
                continue
            # OPF hrefs are relative to the OPF's own directory.
            full = os.path.normpath(os.path.join(opf_dir, href)) if opf_dir else href
            ordered.append(full.replace(os.sep, "/"))
        if ordered:
            return ordered
        logger.warning("EPUB spine empty; falling back to archive order")
    except Exception as e:
        logger.warning(f"EPUB OPF parse failed ({e}); falling back to archive order")

    return [n for n in z.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))]


def extract_epub(path, max_chars=None):
    """Extract readable text from an EPUB in reading order."""
    if not os.path.exists(path):
        raise ExtractionFailed(f"file not found: {path}")
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            chunks, total = [], 0
            for doc in _epub_spine_documents(z):
                if doc not in names:
                    continue
                try:
                    text = _html_to_text(z.read(doc))
                except Exception as e:
                    logger.warning(f"EPUB: skipping {doc}: {e}")
                    continue
                if not text:
                    continue
                chunks.append(text)
                total += len(text)
                if max_chars and total >= max_chars:
                    break
    except zipfile.BadZipFile:
        raise ExtractionFailed("not a valid EPUB (bad zip archive)")

    out = "\n\n".join(chunks).strip()
    if not out:
        raise ExtractionFailed("EPUB contained no extractable text")
    return out[:max_chars] if max_chars else out


def extract_text_file(path, max_chars=None):
    if not os.path.exists(path):
        raise ExtractionFailed(f"file not found: {path}")
    with open(path, "r", errors="replace") as f:
        data = f.read(max_chars) if max_chars else f.read()
    if not data.strip():
        raise ExtractionFailed("file is empty")
    return data


def extract(path, max_chars=400_000):
    """Extract text from a supported document.

    Raises UnsupportedDocument for formats we cannot read, rather than
    returning empty text and letting the caller silently generate a course
    from nothing.
    """
    lower = (path or "").lower()
    if lower.endswith(EPUB_SUFFIXES):
        return extract_epub(path, max_chars=max_chars)
    if lower.endswith(TEXT_SUFFIXES):
        return extract_text_file(path, max_chars=max_chars)
    if lower.endswith(UNSUPPORTED_SUFFIXES):
        ext = os.path.splitext(lower)[1]
        raise UnsupportedDocument(
            f"{ext} is not supported — no parser is installed for it. "
            f"Convert to EPUB, Markdown or plain text first."
        )
    raise UnsupportedDocument(f"unrecognised document type: {path}")


def summarize_source(text, limit=80):
    """Short human description of what was extracted, for status messages."""
    words = len(text.split())
    preview = " ".join(text.split()[:12])
    return f"{words:,} words extracted — starts: “{preview[:limit]}…”"
