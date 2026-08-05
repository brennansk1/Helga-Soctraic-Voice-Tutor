"""Figures from the book itself.

When a course is built from an uploaded EPUB or PDF, the book's OWN figures are
the authoritative illustrations for it. An author drew that diagram for that
explanation; a stock photo fetched from an archive is at best a coincidence and
at worst contradicts the text. So for document-sourced courses this is the only
image source, and the external archives are switched off entirely.

TWO JOBS: EXTRACT, THEN REVIEW.

Extraction is the easy half. The hard half is that **most images embedded in a
book are not figures.** A textbook carries publisher colophons, chapter-heading
rules, bullet glyphs, page furniture, an author photo, and the same logo on
every one of 600 pages. Handing those to a learner as teaching material is
worse than having no images at all, because each one occupies the slot a real
figure would have had.

So everything extracted is scored and filtered:

  * too small            an icon or a bullet, not a figure
  * extreme aspect       a horizontal rule or a page border
  * repeated             the same bytes on many pages is furniture by
                         definition — this is the single strongest signal, and
                         it needs the whole document to detect
  * near-monochrome      a rule, a divider, a blank scan artefact
  * has a caption        "Figure 3.2", <figcaption>, or a non-trivial alt
                         attribute — the strongest POSITIVE signal there is

Captions are worth more than the filtering. A real caption gives an accurate
title and alt-text for free — no LLM call, no hallucination risk, and the
author's own words for what the figure shows.

COPYRIGHT

An uploaded book is very likely still in copyright. Figures extracted here are
therefore scoped to the course built from that book, marked with the book as
their source, and — enforced in the collector — never written into the shared
cross-course asset library. Letting one user's textbook leak its plates into an
unrelated course would be a genuine wrong, not a tidiness issue.
"""

import hashlib
import logging
import os
import re
import zipfile
from collections import Counter
from urllib.parse import unquote
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# --- Review thresholds -------------------------------------------------------
MIN_BYTES = 3_000          # below this it is a glyph
MIN_WIDTH = 120
MIN_HEIGHT = 100
MAX_ASPECT = 6.0           # a rule or a banner, not a figure
MAX_FIGURES = 400          # a whole-book cap
REPEAT_LIMIT = 3           # same bytes this many times => furniture

# No single figure in a textbook is 25 MB. An entry claiming to be is either
# corrupt or hostile: an EPUB is a ZIP, and a ZIP entry's declared size costs
# nothing to inflate, so `z.read()` on an untrusted upload can allocate
# hundreds of megabytes from a file of a few kilobytes. On a 24 GB host shared
# with Ollama and the containers, that is an OOM, and the upload path is
# reachable by anyone who can use /library.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

# The whole-document budget. 400 figures at 25 MB each would still be 10 GB,
# so the per-book total is capped as well as the per-image size.
MAX_TOTAL_IMAGE_BYTES = 400 * 1024 * 1024

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# SVG is excluded deliberately: it is executable markup, and a figure taken
# from an untrusted upload is exactly where that matters.

# "Figure 3.2.", "Fig. 12 —", "Plate IV:", "Table 2.1" ...
CAPTION_RE = re.compile(
    r"\b(figure|fig\.?|plate|diagram|illustration|chart|table|scheme|exhibit)\s*"
    r"([0-9]+(?:[.\-][0-9]+)*|[IVXLC]+)\s*[.:—–-]?\s*(.{0,220})",
    re.IGNORECASE | re.DOTALL)


class Figure(dict):
    """A candidate figure. dict so it serialises straight into a manifest."""


def _sniff(data):
    """(format, width, height) from the header bytes. No Pillow needed for the
    common cases; Pillow is used only as a fallback so the module still works
    when it is absent."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        return "png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        # Walk the JPEG segments to the frame header.
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return ("jpg",
                        int.from_bytes(data[i + 7:i + 9], "big"),
                        int.from_bytes(data[i + 5:i + 7], "big"))
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
        return "jpg", 0, 0
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", 0, 0
    try:                                            # anything exotic
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return (im.format or "").lower(), im.width, im.height
    except Exception:
        return "", 0, 0


def parse_caption(text):
    """Pull a figure label and caption out of nearby prose.
    Returns (label, caption) — ("Figure 3.2", "A right triangle …")."""
    if not text:
        return "", ""
    match = CAPTION_RE.search(text)
    if not match:
        return "", " ".join(text.split())[:220]
    word, number, rest = match.group(1), match.group(2), match.group(3)
    label = f"{word.rstrip('.').title()} {number}"
    return label, " ".join(rest.split())[:220]


# --- EPUB --------------------------------------------------------------------
def _epub_opf_dir(z):
    try:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        ns = "{urn:oasis:names:tc:opendocument:xmlns:container}"
        full = root.find(f".//{ns}rootfile").get("full-path")
        return os.path.dirname(full)
    except Exception:
        return ""


def extract_epub_figures(path, limit=MAX_FIGURES):
    """Figures from an EPUB, with the caption text that surrounds them.

    Works off the XHTML rather than the zip listing, because only the markup
    says which images are FIGURES and what they are called. An image nobody
    references is page furniture.
    """
    out = []
    budget = MAX_TOTAL_IMAGE_BYTES
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup unavailable — cannot read EPUB figures")
        return out
    try:
        with zipfile.ZipFile(path) as z:
            # A DRM'd book's "images" are ciphertext. Sniffing usually rejects
            # them as an unrecognised format, but that is luck rather than a
            # decision — random bytes can begin with a valid magic number.
            if "META-INF/encryption.xml" in set(z.namelist()):
                logger.warning("EPUB figures: DRM-protected, skipping")
                return out
            names = set(z.namelist())
            base = _epub_opf_dir(z)
            docs = [n for n in z.namelist()
                    if n.lower().endswith((".xhtml", ".html", ".htm"))]
            for doc in sorted(docs):
                try:
                    soup = BeautifulSoup(z.read(doc), "html.parser")
                except Exception as e:
                    logger.debug(f"EPUB figures: skipping {doc}: {e}")
                    continue
                doc_dir = os.path.dirname(doc)
                for img in soup.find_all(["img", "image"]):
                    src = img.get("src") or img.get("href") or img.get(
                        "{http://www.w3.org/1999/xlink}href")
                    if not src or not src.lower().endswith(_IMG_EXT):
                        continue
                    target = _resolve_zip_path(src, doc_dir, base, names)
                    if not target:
                        continue
                    data = _safe_zip_read(z, target)
                    if data is None:
                        continue
                    budget -= len(data)
                    if budget < 0:
                        logger.warning(
                            "EPUB figures: total image budget exhausted; "
                            f"stopping after {len(out)} figure(s)")
                        return out

                    # Caption, best source first: an explicit <figcaption>,
                    # then the alt attribute, then the nearest text.
                    caption_text = ""
                    figure = img.find_parent("figure")
                    if figure and figure.find("figcaption"):
                        caption_text = figure.find("figcaption").get_text(" ", strip=True)
                    if not caption_text:
                        caption_text = (img.get("alt") or "").strip()
                    if not caption_text:
                        sib = img.find_next(string=True)
                        caption_text = (str(sib) or "").strip()[:300]

                    label, caption = parse_caption(caption_text)
                    out.append(_candidate(data, target, caption_text,
                                          label, caption, location=doc))
                    if len(out) >= limit:
                        return out
    except zipfile.BadZipFile:
        logger.warning("EPUB figures: bad zip archive")
    except Exception as e:
        logger.warning(f"EPUB figure extraction failed: {e}")
    return out


def _resolve_zip_path(src, doc_dir, base, names):
    """Map an href in the markup to an entry in the zip.

    Percent-decodes and falls back to a case-insensitive match, for the same
    reason `document_extract._resolve_href` does: `src="fig%201.png"` is the
    file `fig 1.png`, and producers routinely disagree with themselves about
    case between the markup and the archive. Missing either one drops the
    book's figures silently — the course still builds, just without the
    diagrams its author drew.
    """
    src = unquote(src.split("#")[0].split("?")[0])
    lowered = {n.lower(): n for n in names}
    for candidate in (os.path.normpath(os.path.join(doc_dir, src)),
                      os.path.normpath(os.path.join(base, src)),
                      src.lstrip("/")):
        candidate = candidate.replace("\\", "/").lstrip("./")
        if candidate in names:
            return candidate
        hit = lowered.get(candidate.lower())
        if hit:
            return hit
    tail = os.path.basename(src).lower()
    for name in names:                       # last resort: match on filename
        if os.path.basename(name).lower() == tail:
            return name
    return None


def _safe_zip_read(z, name):
    """Read a zip entry, refusing implausibly large ones.

    The DECLARED size is checked before reading, so a decompression bomb is
    rejected without ever being inflated.
    """
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_IMAGE_BYTES:
        logger.warning(
            f"figure {name}: declared {info.file_size:,} bytes exceeds the "
            f"{MAX_IMAGE_BYTES:,} cap — skipping (corrupt or hostile archive)")
        return None
    return z.read(name)


# --- PDF ---------------------------------------------------------------------
def extract_pdf_figures(path, limit=MAX_FIGURES):
    """Figures from a PDF, with the page text as caption context.

    Only embedded raster images are taken. A figure drawn as vector paths is
    not recoverable without rendering the page, and rendering every page of a
    600-page book to hunt for diagrams would cost more than the figures are
    worth. That limitation is recorded rather than hidden.
    """
    out = []
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf unavailable — cannot read PDF figures")
        return out
    try:
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            # decrypt() returns 0 on failure rather than raising, so catching
            # exceptions alone lets a locked file through to fail later inside
            # the page loop as "File has not been decrypted".
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                logger.warning("PDF figures: password-protected, skipping")
                return out
        for index, page in enumerate(reader.pages):
            try:
                images = list(page.images)
            except Exception as e:
                logger.debug(f"PDF figures: page {index + 1}: {e}")
                continue
            if not images:
                continue
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            label, caption = parse_caption(page_text)
            for image in images:
                try:
                    data = image.data
                except Exception:
                    continue
                out.append(_candidate(data, f"page{index + 1}/{image.name}",
                                      page_text[:400], label, caption,
                                      location=f"page {index + 1}"))
                if len(out) >= limit:
                    return out
    except Exception as e:
        logger.warning(f"PDF figure extraction failed: {e}")
    return out


def _candidate(data, name, context, label, caption, location=""):
    fmt, width, height = _sniff(data)
    return Figure({
        "name": name, "bytes": len(data), "format": fmt,
        "width": width, "height": height,
        "digest": hashlib.sha1(data).hexdigest()[:16],
        "label": label, "caption": caption,
        "context": " ".join((context or "").split())[:400],
        "location": location, "data": data,
    })


# --- Review ------------------------------------------------------------------
def review(figures):
    """Keep the figures, drop the furniture.

    Returns (kept, rejected). `rejected` carries a reason per item, because a
    silent filter is impossible to tune — if a book yields no figures, the
    reasons are the only way to find out whether that is correct.
    """
    if not figures:
        return [], []

    # Repetition is the strongest furniture signal and needs the whole
    # document: a logo appearing on 300 pages is identical bytes 300 times.
    repeats = Counter(f["digest"] for f in figures)

    kept, rejected = [], []
    for fig in figures:
        reason = _reject_reason(fig, repeats)
        if reason:
            rejected.append({k: v for k, v in fig.items() if k != "data"} | {"reason": reason})
            continue
        fig["score"] = _score(fig)
        kept.append(fig)

    # Best first, and de-duplicate identical images that survived.
    kept.sort(key=lambda f: -f["score"])
    seen, unique = set(), []
    for fig in kept:
        if fig["digest"] in seen:
            continue
        seen.add(fig["digest"])
        unique.append(fig)

    logger.info(f"[FIGURES] kept {len(unique)} of {len(figures)} "
                f"({len(rejected)} rejected, {len(kept) - len(unique)} duplicates)")
    return unique, rejected


def _reject_reason(fig, repeats):
    """DIMENSIONS decide, not file size.

    File size looked like a reasonable proxy for "is this a real image" and is
    actively wrong here. A textbook figure is typically clean line art — few
    colours, large flat areas — which is the best case for PNG compression: a
    520x400 diagram can be under 3 KB, while a decorative 200x200 gradient is
    40 KB. Filtering on bytes therefore rejects the BEST figures and keeps the
    ornaments. (Caught by the fixture: a real 520x400 figure at 2,939 bytes.)

    Bytes are used only when the dimensions could not be read at all.
    """
    if not fig["format"]:
        return "unrecognised image format"
    if repeats[fig["digest"]] > REPEAT_LIMIT:
        return f"appears {repeats[fig['digest']]} times — page furniture, not a figure"

    w, h = fig["width"], fig["height"]
    if w and h:
        # Aspect BEFORE the size floor, so a 600x12 divider is reported as the
        # rule it is rather than as "too small". These reasons are the only
        # tuning surface this filter has — if a book yields no figures, they
        # are how you find out whether that verdict was right.
        aspect = max(w / h, h / w)
        if aspect > MAX_ASPECT:
            return f"extreme aspect ratio ({w}x{h}) — rule or border"
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            return f"too small ({w}x{h}) — icon or glyph"
        return ""

    # Dimensions unreadable — fall back to the crude signal.
    if fig["bytes"] < MIN_BYTES:
        return f"too small ({fig['bytes']} bytes, dimensions unknown)"
    return ""


def _score(fig):
    """Higher is more likely to be a real teaching figure."""
    score = 0
    if fig["label"]:
        score += 10                      # "Figure 3.2" — the author said so
    if len(fig["caption"]) > 25:
        score += 5
    if fig["width"] >= 400 and fig["height"] >= 300:
        score += 3
    if fig["bytes"] > 40_000:
        score += 2
    return score


def figures_from_document(path, limit=MAX_FIGURES):
    """Extract and review in one call. Returns (kept, rejected).
    Never raises — a book with no usable figures is a normal outcome."""
    # CONTENT FIRST. `document_extract` already reads the TEXT of a PDF that
    # was named .epub; if figures still dispatched on the suffix, such a book
    # would be taught from with its diagrams silently missing — the hardest
    # kind of defect to notice, because the course still builds.
    try:
        from services.common.document_extract import sniff_kind, _is_epub_zip
        kind = sniff_kind(path)
        if kind == "zip" and _is_epub_zip(path):
            raw = extract_epub_figures(path, limit=limit)
        elif kind == "pdf":
            raw = extract_pdf_figures(path, limit=limit)
        else:
            return [], []
    except Exception as e:
        logger.warning(f"figure extraction failed for {path}: {e}")
        return [], []
    return review(raw)
