#!/usr/bin/env python3
"""figures_probe.py — can Phase 3 pull real figures out of real books?

Sibling of `ingestion_probe.py`. That one asks "can we read the TEXT"; this
asks "can we read the PICTURES, and do we correctly refuse the furniture".

The failure that matters here is not a crash. It is a book whose diagrams are
silently dropped — the course builds, looks fine, and teaches without the
figures the author drew for it — or the mirror image, where a publisher logo
repeated on 300 pages is served to a learner as teaching material.

Each case declares what it expects:
    figures=N     exactly N figures survive review
    reject=<sub>  at least one rejection whose reason contains <sub>
    caption=<sub> the top figure's caption/label contains <sub>

    python3 tools/figures_probe.py
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.common.document_figures import figures_from_document  # noqa: E402

CASES = []


def case(name, note="", **expect):
    def deco(fn):
        CASES.append({"name": name, "note": note, "expect": expect, "build": fn})
        return fn
    return deco


def _png(w, h, colour=(30, 90, 160), noise=True):
    """A PNG of a given size. `noise` makes the bytes unique per call."""
    from PIL import Image
    import random
    im = Image.new("RGB", (w, h), colour)
    if noise:
        px = im.load()
        random.seed(w * 7919 + h * 104729 + sum(colour))
        for i in range(0, w, 5):
            for j in range(0, h, 5):
                px[i, j] = (random.randint(0, 255),) * 3
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _xhtml(body):
    return ('<?xml version="1.0" encoding="utf-8"?><html '
            'xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<body>{body}</body></html>').encode("utf-8")


CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf"/>'
             '</rootfiles></container>')

OPF = ('<?xml version="1.0"?><package '
       'xmlns="http://www.idpf.org/2007/opf"><manifest>'
       '<item id="c1" href="ch1.xhtml"/></manifest>'
       '<spine><itemref idref="c1"/></spine></package>')


# --------------------------------------------------------------------------
# EPUB cases
# --------------------------------------------------------------------------

@case("epub/figcaption", "figure + <figcaption> — the ideal case",
      figures=1, caption="right triangle")
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml(
            '<figure><img src="images/fig1.png"/>'
            '<figcaption>Figure 3.2 A right triangle with legs a and b.'
            '</figcaption></figure>'))
        z.writestr("OEBPS/images/fig1.png", _png(520, 400))


@case("epub/percent-encoded-src", "src=\"fig%201.png\" — Sigil/InDesign output",
      figures=1)
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml(
            '<figure><img src="images/fig%201.png" alt="Figure 1 Cell diagram"/>'
            '</figure>'))
        z.writestr("OEBPS/images/fig 1.png", _png(520, 400))


@case("epub/uppercase-src", "manifest says .PNG, archive has .png", figures=1)
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml(
            '<img src="Images/FIG1.PNG" alt="Figure 2 A mitochondrion"/>'))
        z.writestr("OEBPS/images/fig1.png", _png(520, 400))


@case("epub/repeated-logo", "publisher logo on 8 chapters is furniture",
      figures=1, reject="furniture")
def _(p):
    logo = _png(300, 300, colour=(200, 30, 30))
    real = _png(600, 420, colour=(20, 120, 60))
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        for i in range(8):
            z.writestr(f"OEBPS/c{i}.xhtml", _xhtml(
                f'<img src="logo.png" alt="publisher"/>'
                + ('<figure><img src="real.png"/><figcaption>Figure 1 '
                   'Photosynthesis overview.</figcaption></figure>'
                   if i == 0 else '')))
        z.writestr("OEBPS/logo.png", logo)
        z.writestr("OEBPS/real.png", real)


@case("epub/icons-and-rules", "bullet glyphs and a divider rule",
      figures=1, reject="too small")
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml(
            '<img src="bullet.png"/><img src="rule.png"/>'
            '<figure><img src="fig.png"/><figcaption>Figure 4.1 '
            'The nitrogen cycle.</figcaption></figure>'))
        z.writestr("OEBPS/bullet.png", _png(24, 24))
        z.writestr("OEBPS/rule.png", _png(700, 8))
        z.writestr("OEBPS/fig.png", _png(560, 380))


@case("epub/svg-figures", "figures are SVG — excluded on purpose, say so",
      figures=0)
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml(
            '<figure><img src="d.svg"/><figcaption>Figure 1 A cell.'
            '</figcaption></figure>'))
        z.writestr("OEBPS/d.svg",
                   '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')


@case("epub/drm", "DRM'd book — must not emit ciphertext as figures",
      figures=0)
def _(p):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("META-INF/encryption.xml",
                   '<encryption><EncryptedData><CipherReference '
                   'URI="OEBPS/fig.png"/></EncryptedData></encryption>')
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml('<img src="fig.png"/>'))
        z.writestr("OEBPS/fig.png", os.urandom(60_000))


@case("epub/zip-bomb", "one absurdly large embedded image", figures=0)
def _(p):
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", _xhtml('<img src="huge.png"/>'))
        # ~120 MB of zeros, compresses to almost nothing.
        z.writestr("OEBPS/huge.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 120_000_000)


# --------------------------------------------------------------------------
# PDF cases
# --------------------------------------------------------------------------

def _pdf_with_images(path, pages, encrypt=None, owner_only=False):
    import fitz
    doc = fitz.open()
    for spec in pages:
        pg = doc.new_page()
        if spec.get("text"):
            pg.insert_text((60, 70), spec["text"], fontsize=9)
        for rect, png in spec.get("images", []):
            pg.insert_image(fitz.Rect(*rect), stream=png)
    kw = {}
    if encrypt:
        kw = dict(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=encrypt,
                  user_pw="" if owner_only else encrypt,
                  permissions=fitz.PDF_PERM_ACCESSIBILITY)
    doc.save(path, **kw)
    doc.close()


@case("pdf/captioned-figure", "page text carries 'Figure 2.1'",
      figures=1, caption="chloroplast")
def _(p):
    _pdf_with_images(p, [{
        "text": "Figure 2.1 The chloroplast and its thylakoid membranes.",
        "images": [((60, 100, 460, 420), _png(520, 400))]}])


@case("pdf/repeated-header", "same header image on 8 pages", reject="furniture")
def _(p):
    header = _png(500, 90, colour=(180, 180, 180))
    real = _png(560, 420, colour=(20, 110, 70))
    pages = [{"text": f"Chapter text page {i}.",
              "images": [((60, 30, 400, 80), header)]} for i in range(8)]
    pages[0]["images"].append(((60, 120, 460, 430), real))
    pages[0]["text"] = "Figure 1.1 Cellular respiration overview."
    _pdf_with_images(p, pages)


@case("pdf/user-password", "locked PDF — no figures, no crash", figures=0)
def _(p):
    _pdf_with_images(p, [{"text": "Figure 1 Secret.",
                          "images": [((60, 100, 460, 420), _png(520, 400))]}],
                     encrypt="secret", owner_only=False)


@case("pdf/owner-password", "owner password only — readable", figures=1)
def _(p):
    _pdf_with_images(p, [{"text": "Figure 1 A readable diagram of the heart.",
                          "images": [((60, 100, 460, 420), _png(520, 400))]}],
                     encrypt="secret", owner_only=True)


@case("pdf/vector-only", "diagram drawn as vector paths — not recoverable",
      figures=0)
def _(p):
    import fitz
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((60, 70), "Figure 1 A triangle.", fontsize=9)
    pg.draw_line(fitz.Point(60, 100), fitz.Point(300, 300))
    pg.draw_line(fitz.Point(300, 300), fitz.Point(60, 300))
    doc.save(p)
    doc.close()


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

@case("misc/pdf-named-epub", "a PDF the user renamed .epub", figures=1)
def _(p):
    _pdf_with_images(p, [{"text": "Figure 1 A misnamed but readable diagram.",
                          "images": [((60, 100, 460, 420), _png(520, 400))]}])


@case("misc/no-extension", "download with no extension", figures=1)
def _(p):
    _pdf_with_images(p, [{"text": "Figure 1 Diagram with no file extension.",
                          "images": [((60, 100, 460, 420), _png(520, 400))]}])


SUFFIX = {"epub": ".epub", "pdf": ".pdf", "misc": ""}


def run():
    tmp = tempfile.mkdtemp(prefix="helga-figs-")
    rows, bad = [], 0
    for c in CASES:
        family = c["name"].split("/")[0]
        suffix = SUFFIX.get(family, "")
        if c["name"] == "misc/pdf-named-epub":
            suffix = ".epub"
        path = os.path.join(tmp, c["name"].replace("/", "_") + suffix)
        try:
            c["build"](path)
        except Exception as e:
            rows.append((c, "BUILD", f"fixture failed: {e}")); bad += 1
            continue

        try:
            kept, rejected = figures_from_document(path)
        except Exception as e:
            rows.append((c, "RAISE", f"{type(e).__name__}: {e}")); bad += 1
            continue

        problems = []
        exp = c["expect"]
        if "figures" in exp and len(kept) != exp["figures"]:
            problems.append(f"expected {exp['figures']} figure(s), got {len(kept)}")
        if "reject" in exp and not any(
                exp["reject"] in (r.get("reason") or "") for r in rejected):
            problems.append(f"no rejection mentioning {exp['reject']!r}")
        if "caption" in exp:
            blob = " ".join(
                f"{f.get('label','')} {f.get('caption','')}" for f in kept).lower()
            if exp["caption"].lower() not in blob:
                problems.append(f"caption missing {exp['caption']!r}")

        detail = f"{len(kept)} kept / {len(rejected)} rejected"
        if kept:
            top = kept[0]
            detail += f"  top={top.get('label') or '(no label)'} {top['width']}x{top['height']}"
        if problems:
            rows.append((c, "FAIL", "; ".join(problems) + f"  [{detail}]")); bad += 1
        else:
            rows.append((c, "PASS", detail))

    width = max(len(c["name"]) for c in CASES) + 2
    print()
    for c, verdict, detail in rows:
        tint = {"PASS": "\033[32m"}.get(verdict, "\033[31m")
        print(f"  {tint}{verdict:<6}\033[0m {c['name']:<{width}} {detail[:78]}")
        if verdict != "PASS":
            print(f"         {'':<{width}} \033[2m{c['note']}\033[0m")
    print(f"\n  {len(rows) - bad}/{len(rows)} handled correctly\n")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run())
