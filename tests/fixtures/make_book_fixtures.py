#!/usr/bin/env python3
"""Build small EPUB/PDF fixtures that look like real books.

Generated rather than committed, and generated rather than downloaded: the test
suite must not need the network, and a real textbook is too large to vendor.

Each fixture deliberately contains BOTH real figures and the page furniture a
book carries around them — a publisher logo repeated on every page, a
horizontal rule, a bullet glyph. A figure extractor that cannot tell those
apart is worse than none, because each ornament occupies the slot a real
figure would have had.

The real books to run against by hand are named in docs/VISUAL_AIDS_DESIGN.md.
"""
import io
import os
import zipfile


def _art(w, h, colour, detail=True):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), colour)
    if detail:
        d = ImageDraw.Draw(im)
        for x in range(0, w, 17):
            d.line([(x, 0), (x, h)], fill=(0, 0, 0), width=1)
        d.ellipse([w // 4, h // 4, 3 * w // 4, 3 * h // 4], outline=(20, 20, 20), width=3)
    return im


def _png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make_epub(path):
    fig1, fig2 = _png(_art(640, 480, (210, 225, 235))), _png(_art(520, 400, (225, 235, 215)))
    logo = _png(_art(140, 140, (255, 255, 255), detail=False))
    rule = _png(_art(600, 12, (180, 180, 180), detail=False))
    bullet = _png(_art(16, 16, (0, 0, 0), detail=False))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument'
                   ':xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   '</rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                   '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        for name, blob in (("fig1", fig1), ("fig2", fig2), ("logo", logo),
                           ("rule", rule), ("bullet", bullet)):
            z.writestr(f"OEBPS/images/{name}.png", blob)
        z.writestr("OEBPS/ch1.xhtml",
                   '<html><body><img src="images/logo.png" alt=""/><h1>Right triangles</h1>'
                   '<figure><img src="images/fig1.png" alt="A right triangle"/><figcaption>'
                   'Figure 1.1: A right triangle with legs of 3 and 4 units.</figcaption></figure>'
                   '<img src="images/rule.png" alt=""/><p>A right triangle has a hypotenuse.</p>'
                   '<img src="images/bullet.png" alt=""/></body></html>')
        z.writestr("OEBPS/ch2.xhtml",
                   '<html><body><img src="images/logo.png" alt=""/><h1>Circles</h1>'
                   '<figure><img src="images/fig2.png"/><figcaption>'
                   'Fig. 2.3 &#8212; The relationship between radius and circumference.'
                   '</figcaption></figure><img src="images/logo.png" alt=""/>'
                   '<img src="images/logo.png" alt=""/></body></html>')
    return path


def make_pdf(path):
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    fig1, fig2 = _art(640, 480, (210, 225, 235)), _art(520, 400, (225, 235, 215))
    logo = _art(120, 120, (255, 255, 255), detail=False)
    c = canvas.Canvas(path, pagesize=(612, 792))
    c.drawImage(ImageReader(logo), 40, 730, width=50, height=50)
    c.drawString(60, 700, "Chapter 1: Right Triangles")
    c.drawImage(ImageReader(fig1), 100, 380, width=320, height=240)
    c.drawString(100, 360, "Figure 1.1: A right triangle with legs of 3 and 4 units.")
    c.showPage()
    c.drawImage(ImageReader(logo), 40, 730, width=50, height=50)
    c.drawString(60, 700, "Chapter 2: Circles")
    c.drawImage(ImageReader(fig2), 100, 400, width=300, height=230)
    c.drawString(100, 380, "Fig. 2.3 - The relationship between radius and circumference.")
    c.showPage()
    for n in range(3, 6):
        c.drawImage(ImageReader(logo), 40, 730, width=50, height=50)
        c.drawString(60, 700, f"Page {n} of running text with no figures.")
        c.showPage()
    c.save()
    return path


def build(directory):
    """Returns {'epub': path, 'pdf': path or None}. PDF needs reportlab; when it
    is absent the EPUB half of the suite still runs."""
    os.makedirs(directory, exist_ok=True)
    out = {"epub": make_epub(os.path.join(directory, "fixture_book.epub")), "pdf": None}
    try:
        out["pdf"] = make_pdf(os.path.join(directory, "fixture_book.pdf"))
    except ImportError:
        pass
    return out


if __name__ == "__main__":
    import tempfile
    print(build(tempfile.mkdtemp(prefix="helga_fixtures_")))
