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

PDF IS NOW SUPPORTED (was advertised and unimplemented). `/library` accepted
`.pdf` in its file input and its MIME whitelist while `extract()` raised
UnsupportedDocument for it — the same "we appear to read your material and do
not" bug this module was written to fix, reintroduced one layer up. It reads
through `pypdf` (BSD-3-Clause, pure Python): no AGPL entanglement, and no
native toolchain in the image.

Text is returned page-delimited so a caller can map a passage back to a page —
which is what `document_figures` needs to attach a figure to the prose that
describes it.
"""

import logging
import os
import re
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_OPF_NS = "{http://www.idpf.org/2007/opf}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"

TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".rst")
EPUB_SUFFIXES = (".epub",)
PDF_SUFFIXES = (".pdf",)
# Named so the error message can be specific about why.
UNSUPPORTED_SUFFIXES = (".doc", ".docx", ".mobi", ".azw", ".azw3")

# A page marker kept in the extracted text. Cheap, greppable, and it survives
# the chunking the hydrator does, so a figure on page 42 can still be tied to
# the paragraph that discusses it.
PAGE_MARKER = "\n\n[[page:{n}]]\n\n"

# Below this, whatever came back is furniture — a table of contents, a title
# page, a copyright notice — not a book. Extracting 3 characters from a table
# of contents and calling it source material is how a course gets built from
# nothing while every check reports success. Deliberately low: a genuine
# pamphlet clears it easily, an EPUB whose only spine entry is the nav doc
# does not.
MIN_USEFUL_CHARS = 200


class UnsupportedDocument(Exception):
    """Raised for formats we cannot honestly extract."""


class ExtractionFailed(Exception):
    """Raised when a supported format could not be parsed."""


class EncryptedDocument(ExtractionFailed):
    """Raised for DRM/password-protected files.

    Its own type because the remedy is different from every other failure: no
    amount of retrying or reformatting helps, and the user needs to be told
    that specifically rather than "extraction failed".
    """


def sniff_kind(path):
    """Identify a file by its CONTENT, returning 'pdf' | 'zip' | 'text' | None.

    The suffix is a hint the user controls, and they get it wrong constantly:
    books arrive as `book.epub` that are really PDFs, as `download` with no
    extension at all, and as `.txt` that are really EPUBs. Dispatching on the
    suffix alone turns each of those into "unrecognised document type" for a
    file we could have read perfectly well.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):      # zip container: epub, docx, odt…
        return "zip"
    if not head:
        return None
    # No magic number: treat as text only if it decodes and is mostly printable.
    if b"\x00" not in head:
        return "text"
    return None


def _is_epub_zip(path):
    """True if this ZIP is an EPUB rather than some other zip-based format."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            return any(n.startswith("META-INF/") for n in names) or any(
                n.lower().endswith((".xhtml", ".opf")) for n in names)
    except (zipfile.BadZipFile, OSError):
        return False


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


def _resolve_href(href, opf_dir, names, lowered):
    """Map an OPF href onto an actual archive entry, or None.

    Three real-world mismatches are handled here, each of which otherwise
    yields "EPUB contained no extractable text" on a completely readable book:

      * **percent-encoding.** `chapter%201.xhtml` in the OPF is the file
        `chapter 1.xhtml` in the ZIP. Sigil and InDesign emit this constantly.
      * **fragments.** `ch1.xhtml#part2` is a legal spine href — common where
        one source file is split across several spine entries — and never
        matches a ZIP name.
      * **case.** Some producers disagree with themselves between the manifest
        and the archive. ZIP names are case-sensitive; the fallback is not.
    """
    if not href:
        return None
    href = unquote(href.split("#", 1)[0].split("?", 1)[0]).strip()
    if not href:
        return None
    full = os.path.normpath(os.path.join(opf_dir, href)) if opf_dir else href
    full = full.replace(os.sep, "/").lstrip("./")
    if full in names:
        return full
    hit = lowered.get(full.lower())
    if hit:
        return hit
    # Last resort: match on basename alone. Wrong-but-present beats absent.
    base = os.path.basename(full).lower()
    for name in names:
        if os.path.basename(name).lower() == base:
            return name
    return None


def _assert_not_drm(z):
    """Refuse a DRM-protected EPUB loudly.

    Without this the ciphertext is handed to BeautifulSoup, which cheerfully
    returns the bytes as 'text'. A course then gets built from encrypted noise
    and every downstream check passes, because there IS content — it just is
    not language. Silent corruption is the worst outcome available here, so it
    is worth one explicit check.
    """
    names = set(z.namelist())
    if "META-INF/encryption.xml" in names:
        try:
            body = z.read("META-INF/encryption.xml").decode("utf-8", "replace")
        except Exception:
            body = ""
        # Font mangling (obfuscation) also lives in encryption.xml and is NOT
        # DRM — the text is still readable, so only refuse when a content file
        # is the thing encrypted.
        if "EncryptedData" in body and not _only_fonts_encrypted(body):
            raise EncryptedDocument(
                "this EPUB is DRM-protected, so its text cannot be read. "
                "Use a DRM-free copy (Project Gutenberg, Standard Ebooks and "
                "most publisher direct-sales are DRM-free).")
        if "EncryptedData" not in body:
            raise EncryptedDocument(
                "this EPUB declares encryption but names no algorithm; its "
                "text cannot be read reliably.")


def _only_fonts_encrypted(body):
    """True when encryption.xml covers fonts alone (obfuscation, not DRM)."""
    targets = re.findall(r'URI="([^"]+)"', body)
    return bool(targets) and all(
        t.lower().endswith((".otf", ".ttf", ".woff", ".woff2")) for t in targets)


_NAV_HINTS = ("nav.xhtml", "toc.xhtml", "toc.ncx", "contents.x")


def _is_navigation_doc(name, markup):
    """True for a table-of-contents document rather than body text.

    A nav doc is legitimately in the spine, so it cannot simply be skipped by
    position — but its content is a list of links to the book, not the book.
    """
    if os.path.basename(name).lower().startswith(_NAV_HINTS):
        return True
    head = markup[:4000].decode("utf-8", "replace") if isinstance(
        markup, bytes) else markup[:4000]
    return 'epub:type="toc"' in head or "epub:type='toc'" in head


def _epub_spine_documents(z):
    """Return content file names in reading order.

    Falls back to every XHTML entry in archive order if the OPF can't be read —
    a slightly wrong order still beats losing the text entirely.
    """
    names = z.namelist()
    lowered = {n.lower(): n for n in names}
    try:
        container = z.read("META-INF/container.xml")
        rootfile = ET.fromstring(container).find(f".//{_CONTAINER_NS}rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = os.path.dirname(opf_path)

        opf = ET.fromstring(z.read(opf_path))
        # Namespace-agnostic: older Calibre output omits the IDPF namespace, so
        # matching on the tag's local name reads both.
        manifest, ordered = {}, []
        for item in opf.iter():
            tag = item.tag.rsplit("}", 1)[-1]
            if tag == "item":
                manifest[item.get("id")] = item.get("href")
        for ref in opf.iter():
            if ref.tag.rsplit("}", 1)[-1] != "itemref":
                continue
            resolved = _resolve_href(
                manifest.get(ref.get("idref")), opf_dir, names, lowered)
            if resolved and resolved not in ordered:
                ordered.append(resolved)
        if ordered:
            return ordered
        logger.warning("EPUB spine empty; falling back to archive order")
    except Exception as e:
        logger.warning(f"EPUB OPF parse failed ({e}); falling back to archive order")

    return [n for n in names
            if n.lower().endswith((".xhtml", ".html", ".htm"))]


def extract_epub(path, max_chars=None, min_chars=MIN_USEFUL_CHARS):
    """Extract readable text from an EPUB in reading order.

    `min_chars` is the "is this actually a book" floor. It is a parameter so
    that tests exercising the parsing MECHANICS (spine order, script
    stripping) can use small fixtures without tripping a guard aimed at real
    uploads; production callers take the default and get the check.
    """
    if not os.path.exists(path):
        raise ExtractionFailed(f"file not found: {path}")
    try:
        with zipfile.ZipFile(path) as z:
            _assert_not_drm(z)
            names = set(z.namelist())
            chunks, total, skipped_nav = [], 0, 0
            for doc in _epub_spine_documents(z):
                if doc not in names:
                    continue
                try:
                    raw = z.read(doc)
                    if _is_navigation_doc(doc, raw):
                        skipped_nav += 1
                        continue
                    text = _html_to_text(raw)
                except Exception as e:
                    logger.warning(f"EPUB: skipping {doc}: {e}")
                    continue
                if not text:
                    continue
                chunks.append(text)
                total += len(text)
                if max_chars and total >= max_chars:
                    break
    except EncryptedDocument:
        raise
    except zipfile.BadZipFile:
        raise ExtractionFailed("not a valid EPUB (bad zip archive)")

    out = "\n\n".join(chunks).strip()
    if not out:
        raise ExtractionFailed("EPUB contained no extractable text")
    # A caller that asked for only N characters must not then be told its book
    # is too short — it got exactly what it requested.
    floor = min(min_chars, max_chars) if max_chars else min_chars
    if len(out) < floor:
        raise ExtractionFailed(
            f"EPUB yielded only {len(out)} characters"
            + (f" after skipping {skipped_nav} navigation document(s)"
               if skipped_nav else "")
            + " — that is front matter, not a book. The file may be a stub, or "
              "its text may live in a format we cannot read.")
    return out[:max_chars] if max_chars else out


def extract_pdf(path, max_chars=None, keep_page_markers=True):
    """Extract text from a PDF, page by page.

    Raises rather than returning a plausible-looking empty string: a scanned
    PDF with no text layer produces nothing, and silently building a course
    from nothing is precisely the failure this module exists to prevent. The
    error names OCR as the fix, because that is what such a file actually
    needs.
    """
    if not os.path.exists(path):
        raise ExtractionFailed(f"file not found: {path}")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise UnsupportedDocument(
            "PDF support needs the 'pypdf' package, which is not installed")
    try:
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            # Most "encrypted" PDFs carry only an OWNER password restricting
            # printing or copying; the empty user password opens them and doing
            # so is not a circumvention. A real USER password is different, and
            # must be reported as such.
            #
            # decrypt() signals failure by RETURNING 0, not by raising — so
            # catching exceptions alone lets a locked file through to fail
            # later as "File has not been decrypted", which reads like
            # corruption rather than a password.
            try:
                result = reader.decrypt("")
            except Exception as e:
                raise EncryptedDocument(
                    f"this PDF is password-protected and cannot be read: {e}")
            if not result:
                raise EncryptedDocument(
                    "this PDF is locked with a password, so its text cannot be "
                    "read. Supply an unlocked copy.")
        chunks, total, pages = [], 0, 0
        for index, page in enumerate(reader.pages):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as e:
                logger.warning(f"PDF: skipping page {index + 1}: {e}")
                continue
            if not text:
                continue
            pages += 1
            if keep_page_markers:
                chunks.append(PAGE_MARKER.format(n=index + 1))
            chunks.append(text)
            total += len(text)
            if max_chars and total >= max_chars:
                break
    except ExtractionFailed:
        raise
    except Exception as e:
        raise ExtractionFailed(f"could not read PDF: {e}")

    out = "".join(chunks).strip()
    if not out:
        raise ExtractionFailed(
            "PDF contained no extractable text — it is probably a scan, and "
            "would need OCR before it can be taught from")
    logger.info(f"PDF: extracted {len(out):,} chars from {pages} page(s)")
    return out[:max_chars] if max_chars else out


_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
)


def _decode_bytes(raw):
    """Decode text bytes, honouring BOMs and detecting the rest.

    `open(path, errors="replace")` is not good enough: a UTF-16 file read as
    UTF-8 does not fail, it succeeds into mojibake studded with NULs — text
    that looks extracted, passes every "is it non-empty" check, and teaches
    nothing. Windows-authored .txt and .md files are UTF-16 often enough to
    matter.
    """
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc, "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best:
            return str(best)
    except ImportError:
        logger.debug("charset_normalizer unavailable; falling back to cp1252")
    # cp1252 over latin-1: it is a superset in practice for Windows text and
    # never raises, so smart quotes survive instead of becoming control chars.
    return raw.decode("cp1252", "replace")


def extract_text_file(path, max_chars=None):
    if not os.path.exists(path):
        raise ExtractionFailed(f"file not found: {path}")
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.strip():
        raise ExtractionFailed("file is empty")
    data = _decode_bytes(raw)
    if not data.strip():
        raise ExtractionFailed("file is empty")
    return data[:max_chars] if max_chars else data


def extract(path, max_chars=400_000):
    """Extract text from a supported document.

    Raises UnsupportedDocument for formats we cannot read, rather than
    returning empty text and letting the caller silently generate a course
    from nothing.
    """
    lower = (path or "").lower()

    # Formats we have no parser for are answerable from the NAME alone, so
    # answer them first. Requiring the file to exist before saying ".docx is
    # not supported" reports a missing file when the real problem is the
    # format — and the caller cannot act on that.
    if lower.endswith(UNSUPPORTED_SUFFIXES):
        ext = os.path.splitext(lower)[1]
        raise UnsupportedDocument(
            f"{ext} is not supported — no parser is installed for it. "
            f"Convert to EPUB, Markdown or plain text first."
        )
    if not path or not os.path.exists(path):
        # Nothing readable and no suffix verdict: an unknown extension is a
        # more actionable answer than "file not found" for callers probing
        # what we support.
        if not lower.endswith(TEXT_SUFFIXES + EPUB_SUFFIXES + PDF_SUFFIXES):
            raise UnsupportedDocument(f"unrecognised document type: {path}")
        raise ExtractionFailed(f"file not found: {path}")

    # CONTENT FIRST, SUFFIX SECOND. The suffix is a user-supplied hint and it
    # is wrong often enough to matter: PDFs saved as .epub, EPUBs downloaded
    # with no extension, .txt that is really a zip. Believing the suffix turns
    # each of those into a refusal for a file we can read.
    kind = sniff_kind(path)
    if kind == "pdf":
        return extract_pdf(path, max_chars=max_chars)
    if kind == "zip":
        if _is_epub_zip(path):
            return extract_epub(path, max_chars=max_chars)
        if lower.endswith((".docx", ".odt")):
            ext = os.path.splitext(lower)[1]
            raise UnsupportedDocument(
                f"{ext} is not supported — no parser is installed for it. "
                f"Convert to EPUB, Markdown or plain text first.")
        # A ZIP header that will not open is a truncated or corrupt DOWNLOAD,
        # not a wrong file type. Telling someone to "unpack it and supply the
        # document inside" when the real fix is "download it again" sends them
        # somewhere there is nothing to find.
        try:
            with zipfile.ZipFile(path):
                pass
        except (zipfile.BadZipFile, OSError):
            raise ExtractionFailed(
                "this file starts like a ZIP/EPUB but the archive is damaged — "
                "the download was probably cut short. Try downloading it again.")
        raise UnsupportedDocument(
            "this is a ZIP archive but not an EPUB — unpack it and supply the "
            "document inside.")

    if kind == "text" or lower.endswith(TEXT_SUFFIXES):
        return extract_text_file(path, max_chars=max_chars)
    raise UnsupportedDocument(f"unrecognised document type: {path}")


def summarize_source(text, limit=80):
    """Short human description of what was extracted, for status messages."""
    words = len(text.split())
    preview = " ".join(text.split()[:12])
    return f"{words:,} words extracted — starts: “{preview[:limit]}…”"
