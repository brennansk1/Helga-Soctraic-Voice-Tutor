#!/usr/bin/env python3
"""ingestion_probe.py — how many real-world book shapes can we actually read?

WHY THIS EXISTS
---------------
`document_extract` was written against a tidy EPUB and a tidy PDF. Real books
are not tidy: they come from Calibre, InDesign, Pandoc, Sigil, twenty years of
Project Gutenberg tooling and a dozen scanner drivers, and each leaves its own
dents in the file. The unit tests cover the happy path, and a happy path is
exactly what a "bring your own material" feature does not get.

So this generates the awkward shapes on purpose and reports which ones we can
read. Everything is synthesised locally — no network, no copyrighted text — so
it runs anywhere and is safe to keep in CI.

A case is one of:
  PASS  text came back and contains the marker we hid in the file
  EMPTY tool returned nothing / raised ExtractionFailed on a readable file
  WRONG text came back but the marker is missing -> silent corruption
  RAISE unexpected exception type
  SKIP  correctly refused (encrypted, scanned, genuinely unsupported)

WRONG is the one that matters most: it is the failure the user cannot see.

    python3 tools/ingestion_probe.py            # run everything
    python3 tools/ingestion_probe.py --keep     # leave fixtures on disk
"""
import argparse
import os
import shutil
import sys
import tempfile
import traceback
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.common.document_extract import (  # noqa: E402
    extract, ExtractionFailed, UnsupportedDocument,
)

MARKER = "ZORBLAX"          # nonsense token: if it survives, real body text did

# Fixture bodies must be BOOK-SIZED. An early version of this probe used
# one-line chapters, and every case then tripped the "that is front matter,
# not a book" guard — the probe was measuring its own fixtures, not the
# extractor. Real chapters are paragraphs, so the fixtures are too.
BODY = (
    "The cell is the basic structural unit of every living organism. "
    "Prokaryotic cells lack a membrane-bound nucleus, while eukaryotic cells "
    "enclose their genetic material within a nuclear envelope. This distinction "
    "organises much of modern biology and is the reason the two groups are "
    "treated separately throughout this volume. Mitochondria generate adenosine "
    "triphosphate through oxidative phosphorylation, and chloroplasts capture "
    "light energy in photosynthetic eukaryotes. "
) * 3
CASES = []


def case(name, kind, note="", expect="PASS"):
    def deco(fn):
        CASES.append({"name": name, "kind": kind, "note": note,
                      "expect": expect, "build": fn})
        return fn
    return deco


# --------------------------------------------------------------------------
# EPUB builders
# --------------------------------------------------------------------------

def _xhtml(body):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
        f'<body>{body}</body></html>'
    ).encode("utf-8")


def _write_epub(path, files, mimetype=True):
    with zipfile.ZipFile(path, "w") as z:
        if mimetype:
            z.writestr("mimetype", "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            z.writestr(name, data)


def _container(opf="OEBPS/content.opf"):
    return (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        f'<rootfiles><rootfile full-path="{opf}" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )


def _opf(items, spine, ns=True):
    x = 'xmlns="http://www.idpf.org/2007/opf"' if ns else ""
    man = "".join(
        f'<item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in items)
    ref = "".join(f'<itemref idref="{i}"/>' for i in spine)
    return (f'<?xml version="1.0"?><package {x} version="3.0">'
            f'<metadata/><manifest>{man}</manifest>'
            f'<spine>{ref}</spine></package>')


@case("epub/plain", "epub", "textbook EPUB 3, OEBPS layout")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": _xhtml(f"<h1>Chapter</h1><p>{MARKER} body text.</p><p>{BODY}</p>"),
    })


@case("epub/space-in-filename", "epub",
      "href percent-encoded (%20) — Sigil/InDesign do this constantly")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "chapter%201.xhtml")], ["c1"]),
        "OEBPS/chapter 1.xhtml": _xhtml(f"<p>{MARKER} spaced filename.</p><p>{BODY}</p>"),
    })


@case("epub/spine-fragment", "epub",
      "spine href carries #fragment — legal, and common in split chapters")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml#part2")], ["c1"]),
        "OEBPS/ch1.xhtml": _xhtml(f"<p>{MARKER} fragment href.</p><p>{BODY}</p>"),
    })


@case("epub/no-namespace-opf", "epub",
      "OPF without the IDPF namespace — older Calibre output")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"], ns=False),
        "OEBPS/ch1.xhtml": _xhtml(f"<p>{MARKER} no namespace.</p><p>{BODY}</p>"),
    })


@case("epub/flat-layout", "epub", "content at archive root, no OEBPS dir")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container("content.opf"),
        "content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "ch1.xhtml": _xhtml(f"<p>{MARKER} flat layout.</p><p>{BODY}</p>"),
    })


@case("epub/latin1", "epub", "declared iso-8859-1 with accented text")
def _(p):
    body = ('<?xml version="1.0" encoding="iso-8859-1"?><html><body>'
            f'<p>{MARKER} caf\xe9 na\xefve r\xe9sum\xe9.</p><p>{BODY}</p></body></html>'
            ).encode("iso-8859-1")
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": body,
    })


@case("epub/cjk", "epub", "CJK body text")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": _xhtml(f"<p>{MARKER} 生物学の細胞。</p><p>{BODY}</p>"),
    })


@case("epub/no-opf", "epub", "container.xml missing — must fall back")
def _(p):
    _write_epub(p, {
        "OEBPS/ch1.xhtml": _xhtml(f"<p>{MARKER} orphan chapter.</p><p>{BODY}</p>"),
    })


@case("epub/uppercase-ext", "epub", ".XHTML uppercase entries in fallback")
def _(p):
    _write_epub(p, {
        "OEBPS/CH1.XHTML": _xhtml(f"<p>{MARKER} uppercase.</p><p>{BODY}</p>"),
    })


@case("epub/many-chapters", "epub", "60 chapters, ordering must hold")
def _(p):
    files = {"META-INF/container.xml": _container()}
    items, spine = [], []
    for i in range(60):
        files[f"OEBPS/c{i:03}.xhtml"] = _xhtml(
            f"<p>{MARKER} chapter {i} body.</p><p>{BODY}</p>")
        items.append((f"c{i}", f"c{i:03}.xhtml"))
        spine.append(f"c{i}")
    files["OEBPS/content.opf"] = _opf(items, spine)
    _write_epub(p, files)


@case("epub/drm", "epub", "META-INF/encryption.xml present (Adobe DRM)",
      expect="SKIP")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "META-INF/encryption.xml":
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"/>',
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": b"\x00\x01\x02 encrypted garbage \xff\xfe",
    })


@case("epub/nav-only", "epub", "spine lists only the nav doc — no body text",
      expect="SKIP")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("nav", "nav.xhtml")], ["nav"]),
        "OEBPS/nav.xhtml": _xhtml('<nav epub:type="toc"><ol><li>x</li></ol></nav>'),
    })


@case("epub/truncated-zip", "epub", "download cut short", expect="SKIP")
def _(p):
    with open(p, "wb") as f:
        f.write(b"PK\x03\x04" + b"\x00" * 200)


@case("epub/script-noise", "epub", "nav/script/style furniture around body")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": _xhtml(
            "<script>var a=1;</script><style>p{color:red}</style>"
            f"<p>{MARKER} real text.</p><p>{BODY}</p>"),
    })


# --------------------------------------------------------------------------
# PDF builders  (PyMuPDF writes them; pypdf reads them)
# --------------------------------------------------------------------------

def _pdf(path, pages=3, text=True, encrypt=None, owner_only=False):
    import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page()
        if text:
            pg.insert_text((72, 100), f"{MARKER} page {i + 1}. " + BODY[:900],
                           fontsize=7)
    kw = {}
    if encrypt:
        kw = dict(encryption=fitz.PDF_ENCRYPT_AES_256,
                  owner_pw=encrypt,
                  user_pw="" if owner_only else encrypt,
                  permissions=fitz.PDF_PERM_ACCESSIBILITY)
    doc.save(path, **kw)
    doc.close()


@case("pdf/plain", "pdf", "ordinary text PDF")
def _(p):
    _pdf(p)


@case("pdf/long", "pdf", "300 pages")
def _(p):
    _pdf(p, pages=300)


@case("pdf/scanned", "pdf", "no text layer at all", expect="SKIP")
def _(p):
    _pdf(p, pages=2, text=False)


@case("pdf/owner-password", "pdf",
      "owner password only — legally and technically readable")
def _(p):
    _pdf(p, encrypt="secret", owner_only=True)


@case("pdf/user-password", "pdf", "real user password", expect="SKIP")
def _(p):
    _pdf(p, encrypt="secret", owner_only=False)


@case("pdf/truncated", "pdf", "corrupt tail", expect="SKIP")
def _(p):
    _pdf(p)
    with open(p, "r+b") as f:
        f.truncate(os.path.getsize(p) // 3)


@case("pdf/blank-pages-first", "pdf", "front matter blank, text later")
def _(p):
    import fitz
    doc = fitz.open()
    for _i in range(4):
        doc.new_page()
    pg = doc.new_page()
    pg.insert_text((72, 100), f"{MARKER} after front matter. " + BODY[:900], fontsize=7)
    doc.save(p)
    doc.close()


# --------------------------------------------------------------------------
# Dispatch / misc
# --------------------------------------------------------------------------

@case("misc/pdf-named-epub", "epub",
      "a PDF the user renamed .epub — sniffing beats the suffix")
def _(p):
    _pdf(p)


@case("misc/epub-no-extension", "none",
      "no extension at all (common from downloads)")
def _(p):
    _write_epub(p, {
        "META-INF/container.xml": _container(),
        "OEBPS/content.opf": _opf([("c1", "ch1.xhtml")], ["c1"]),
        "OEBPS/ch1.xhtml": _xhtml(f"<p>{MARKER} no extension.</p><p>{BODY}</p>"),
    })


@case("misc/txt-utf16", "txt", "UTF-16 plain text with BOM")
def _(p):
    open(p, "wb").write(f"{MARKER} utf-16 body text. {BODY}\n".encode("utf-16"))


@case("misc/txt-latin1", "txt", "latin-1 plain text")
def _(p):
    open(p, "wb").write(f"{MARKER} caf\xe9 text. {BODY}\n".encode("iso-8859-1"))


@case("misc/empty", "txt", "zero bytes", expect="SKIP")
def _(p):
    open(p, "wb").close()


SUFFIX = {"epub": ".epub", "pdf": ".pdf", "txt": ".txt", "none": ""}


def run(keep=False):
    tmp = tempfile.mkdtemp(prefix="helga-ingest-")
    rows, counts = [], {}
    for c in CASES:
        path = os.path.join(tmp, c["name"].replace("/", "_") + SUFFIX[c["kind"]])
        try:
            c["build"](path)
        except Exception as e:
            rows.append((c, "BUILD", f"fixture failed: {e}"))
            continue

        try:
            text = extract(path)
            if not text or not text.strip():
                status, detail = "EMPTY", "returned empty string"
            elif MARKER not in text:
                status = "WRONG"
                detail = f"marker lost; got {len(text)} chars: {text[:60]!r}"
            else:
                status, detail = "PASS", f"{len(text):,} chars"
        except (ExtractionFailed, UnsupportedDocument) as e:
            status, detail = "SKIP", f"{type(e).__name__}: {e}"
        except Exception as e:
            status = "RAISE"
            detail = f"{type(e).__name__}: {e}"
            if os.getenv("PROBE_TRACE"):
                traceback.print_exc()

        # A refusal we expected is a pass; a refusal we did not is a miss.
        verdict = status
        if status == "SKIP" and c["expect"] == "SKIP":
            verdict = "PASS"
        elif status == "PASS" and c["expect"] == "SKIP":
            verdict = "WRONG"      # read something we should have refused
        elif status in ("EMPTY", "SKIP") and c["expect"] == "PASS":
            verdict = "MISS"
        counts[verdict] = counts.get(verdict, 0) + 1
        rows.append((c, verdict, detail))

    width = max(len(c["name"]) for c in CASES) + 2
    print()
    colour = {"PASS": "\033[32m", "MISS": "\033[33m", "WRONG": "\033[31m",
              "RAISE": "\033[31m", "EMPTY": "\033[33m", "BUILD": "\033[35m"}
    for c, verdict, detail in rows:
        tint = colour.get(verdict, "")
        print(f"  {tint}{verdict:<6}\033[0m {c['name']:<{width}} {detail[:74]}")
        if verdict != "PASS":
            print(f"         {'':<{width}} \033[2m{c['note']}\033[0m")

    total = len(rows)
    good = counts.get("PASS", 0)
    print(f"\n  {good}/{total} handled correctly", end="")
    bad = {k: v for k, v in counts.items() if k != "PASS"}
    if bad:
        print("  —  " + ", ".join(f"{v} {k}" for k, v in sorted(bad.items())))
    else:
        print()
    print()

    if keep:
        print(f"  fixtures kept in {tmp}\n")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    sys.exit(run(**vars(ap.parse_args())))
