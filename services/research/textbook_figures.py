"""Figure extraction from a textbook — with the caption that belongs to it.

WHY CAPTION BINDING IS THE WHOLE JOB
------------------------------------
Pulling images out of a PDF is trivial and nearly useless: an extracted image
with no idea what it depicts cannot be attached to a concept, cannot be given
alt text, and cannot be checked for relevance. What makes a figure a teaching
asset is the sentence underneath it — "Figure 3.4 The mitochondrion has an
inner and outer membrane" — plus the section it sits in.

So this binds three things to every image: its **caption**, its **figure
number**, and the **section heading** above it. An image that gets none of them
is refused rather than stored, because an unattributable image is exactly the
decorative asset the seductive-details evidence warns about.

WHY PyMuPDF AND NOT MinerU
--------------------------
The research reversed the earlier "wrong economics" verdict on MinerU, on real
grounds — a 1.2B VLM at 90.67 OmniDocBench and an MLX backend at ~38 s/page.
But it also said to benchmark the zero-dependency option FIRST, because this
project has paid the adopt-then-remove cost twice (KuzuDB, ZIM).

PyMuPDF is already installed, needs no model, no GPU and no MLX, and runs at
milliseconds per page rather than ~38 seconds. For a born-digital textbook —
which is what an openly licensed one almost always is — the images are embedded
objects and the captions are real text, so there is nothing for a VLM to infer.
MinerU earns its place on scanned or formula-dense material, and the measured
comparison belongs in `tools/figure_bench.py`.

WHAT IT DELIBERATELY DROPS
--------------------------
Decorative furniture: page rules, logos, icons, background gradients, and the
repeated banner art that OpenStax puts at the head of every chapter. These are
filtered by size, aspect ratio, and — the effective one — by REPETITION across
pages, since a decoration recurs and a figure does not.
"""

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# Below this a bitmap is an icon, a bullet or a rule, never a figure.
MIN_PIXELS = 40_000        # e.g. 200x200
MIN_EDGE = 120
# A band far wider than tall (or vice versa) is a rule or a sidebar, not a
# diagram. Real figures cluster well inside this.
MAX_ASPECT = 6.0
# An identical image appearing on this many pages is furniture.
REPEAT_LIMIT = 3

# MEASURED AGAINST A REAL BOOK, NOT ASSUMED.
#
# The first version required a "Figure N" prefix and bound 0 captions out of 15
# figures on a real OpenStax/CNX export. Manual inspection of page 102 showed
# why: that export STRIPS figure numbers. Its captions open with a sub-figure
# label -- "(a) The lattice structure of ice makes it less dense than..." --
# and cross-references in the body read "[link]" rather than "Figure 3.4".
#
# Font size does not rescue it either: captions and body text are both 14.0pt
# in this book, so the usual "captions are set smaller" heuristic is useless
# here.
#
# So two openers are accepted: the conventional numbered form, and the
# sub-figure label. Anything else is REFUSED rather than guessed, because the
# nearest paragraph is not a caption and attaching it would give every figure
# confident nonsense.
_CAPTION_RE = re.compile(
    r"^\s*(?:(Figure|Fig\.?|Table|Chart|Diagram)\s*([0-9]+(?:[.\-][0-9]+)*)\s*[.:]?\s*(.+)"
    r"|(\((?:[a-z]|[ivx]+)\))\s*(.+))",
    re.IGNORECASE | re.DOTALL)

# A heading is short, title-ish, and not a sentence. Numbered OR unnumbered:
# this book's headings are "Water Is Cohesive" and "Carbohydrates", with no
# numbering at all, so requiring a leading number found zero of them.
_HEADING_RE = re.compile(r"^\s*(?:([0-9]+(?:\.[0-9]+)*)\s+)?([A-Z][^.!?;:]{3,60})\s*$")

# THE SIGNAL THAT ACTUALLY WORKS, found by inspecting a real book.
#
# A caption's text block sits INSIDE the image's bounding box — the rect PyMuPDF
# reports for the image includes the caption area — while the body text that
# resumes after the figure starts just below it. Measured on 8 figures:
#
#     gap -4   "(a) The lattice structure of ice makes it less dense..."   CAPTION
#     gap -4   "The weight of a needle on top of water pulls..."           CAPTION
#     gap -4   "Glucose, galactose, and fructose are isomeric..."          CAPTION
#     gap +12  "These cohesive forces are also related to..."              BODY
#     gap +12  "Acids are substances that provide hydrogen ions..."        BODY
#     gap +12  "However, structures that are more complex..."              BODY
#
# The separation is clean and it does not depend on the caption being worded
# like one, which is what the pattern approach got wrong. Pattern matching is
# kept as a second route for books that DO number their figures.
CAPTION_BAND = (-16.0, 2.0)


def _blocks(page):
    """Text blocks as (rect, text), top-to-bottom."""
    out = []
    for b in page.get_text("blocks") or []:
        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip():
            out.append(((b[0], b[1], b[2], b[3]), " ".join(b[4].split())))
    return sorted(out, key=lambda r: r[0][1])


def _caption_for(img_rect, blocks, max_gap=90):
    """The caption belonging to an image, or None.

    Looks BELOW the image first — the overwhelming convention in textbooks —
    then above, and requires the block to actually look like a caption. A
    nearby paragraph is not a caption, and treating it as one would attach
    confident nonsense to every figure.
    """
    x0, y0, x1, y1 = img_rect
    best = None
    for (bx0, by0, bx1, by1), text in blocks:
        m = _CAPTION_RE.match(text)
        in_band = CAPTION_BAND[0] <= (by0 - y1) <= CAPTION_BAND[1]
        # Either the layout says caption, or the wording does. Neither alone is
        # reliable across books: this one strips figure numbers, and others put
        # the caption well clear of the image box.
        if not m and not in_band:
            continue
        # Vertically near, and horizontally overlapping the image.
        gap_below = by0 - y1
        gap_above = y0 - by1
        # A small NEGATIVE gap is normal: an image's bounding box and its
        # caption block routinely overlap by a few points. Requiring >= 0
        # rejected a correctly-placed caption 4pt inside the image box, which
        # was half of why the first run bound nothing.
        near = (-12 <= gap_below <= max_gap) or (0 <= gap_above <= max_gap / 2)
        overlaps = not (bx1 < x0 - 40 or bx0 > x1 + 40)
        if (near or in_band) and overlaps:
            gap = gap_below if -16 <= gap_below <= max_gap else gap_above
            # Prefer the in-band block: position is the stronger evidence here.
            rank = (0 if in_band else 1, abs(gap))
            if best is None or rank < best[0]:
                best = (rank, m, text)
    if not best:
        return None
    _, m, text = best
    if m is None:                       # matched by POSITION, not wording
        return {"label": None, "number": None, "text": text.strip()[:600]}
    if m.group(1):                      # "Figure 3.4 ..."
        return {"label": f"{m.group(1).title()} {m.group(2)}",
                "number": m.group(2),
                "text": m.group(3).strip()[:600]}
    # "(a) ..." — a real caption whose number the export removed. Recorded with
    # no number rather than a fabricated one.
    return {"label": None, "number": None,
            "text": f"{m.group(4)} {m.group(5)}".strip()[:600]}


def _headings_on(blocks):
    """(y, heading) for every heading-looking block, top to bottom."""
    out = []
    for (bx0, by0, bx1, by1), text in blocks:
        m = _HEADING_RE.match(text)
        if m and len(text.split()) <= 8:
            out.append((by0, " ".join(x for x in (m.group(1), m.group(2)) if x)))
    return out


def _section_for(img_rect, headings, carried=None):
    """The nearest heading above the image, or the last one seen.

    `carried` matters more than it looks: figures routinely sit at the TOP of a
    page with no heading above them on that page, and their section began
    pages earlier. Resetting per page attributed section to none of 15 figures
    in a real book; carrying it forward is what makes the field usable.
    """
    y0 = img_rect[1]
    found = carried
    for hy, text in headings:
        if hy > y0:
            break
        found = text
    return found


# --- format profiles ---------------------------------------------------------
#
# EVERY BOOK LAYS FIGURES OUT DIFFERENTLY, AND ONE HEURISTIC WILL NOT DO.
#
# A single real textbook broke three assumptions in a row:
#   * captions carried no "Figure N" prefix (the export stripped them)
#   * the caption block sat INSIDE the image's bounding box, at gap -4
#   * headings were unnumbered ("Carbohydrates", not "3.2 Carbohydrates")
#
# Another book will place the caption above the figure, or number it, or set it
# in a smaller font, or run it across two columns. Guessing one convention and
# applying it everywhere yields either nothing (too strict) or confident
# nonsense (too loose), and this project has shipped both.
#
# So the extractor SAMPLES the book, scores each strategy on what it actually
# finds, and uses the one that wins — recording which, so a bad extraction is
# diagnosable rather than mysterious.

STRATEGIES = ("inside_box", "below", "above", "numbered")


def _candidate(strategy, img_rect, blocks):
    """The caption a single strategy would pick, or None."""
    x0, y0, x1, y1 = img_rect
    best = None
    for (bx0, by0, bx1, by1), text in blocks:
        if bx1 < x0 - 40 or bx0 > x1 + 40:      # not horizontally aligned
            continue
        gap_below, gap_above = by0 - y1, y0 - by1
        m = _CAPTION_RE.match(text)
        if strategy == "inside_box":
            ok, dist = (CAPTION_BAND[0] <= gap_below <= CAPTION_BAND[1]), abs(gap_below)
        elif strategy == "below":
            ok, dist = (2 < gap_below <= 90), gap_below
        elif strategy == "above":
            ok, dist = (0 <= gap_above <= 60), gap_above
        elif strategy == "numbered":
            ok, dist = (m is not None and -16 <= gap_below <= 120), abs(gap_below)
        else:
            ok, dist = False, 0
        if ok and (best is None or dist < best[0]):
            best = (dist, m, text)
    if best is None:
        return None
    _, m, text = best
    if m and m.group(1):
        return {"label": f"{m.group(1).title()} {m.group(2)}",
                "number": m.group(2), "text": m.group(3).strip()[:600],
                "strategy": strategy}
    if m and m.group(4):
        return {"label": None, "number": None,
                "text": f"{m.group(4)} {m.group(5)}".strip()[:600],
                "strategy": strategy}
    return {"label": None, "number": None, "text": text.strip()[:600],
            "strategy": strategy}


def _plausible_caption(text):
    """Does this read like a caption rather than body prose?

    Deliberately weak and model-free. A caption describes the figure; body text
    continues an argument. The tells that survive across books: captions are
    short-ish, do not open with a discourse connective, and are not a fragment
    of a sentence begun elsewhere.
    """
    if not text or len(text) < 20 or len(text) > 700:
        return False
    t = text.strip()
    if t[0].islower():                       # continuation of a prior sentence
        return False

    # A FRAGMENT IS BODY TEXT SPLIT BY LAYOUT, NOT A CAPTION.
    #
    # Measured: scoring on "plausible" alone let the loosest strategy win by
    # recall, and it dragged in "A fat molecule, such as a triglyceride,
    # consists of" and "The chemical nature of the R group determines the" —
    # both mid-sentence fragments of a paragraph the column break interrupted.
    # A caption is a complete thought and ends like one.
    if not re.search(r"[.!?]['\")\]]?\s*$", t):
        return False

    # Discourse connectives open a continuing argument, never a caption.
    if re.match(r"^(However|These|This|Therefore|Thus|Also|Because|Since|And|But|"
                r"In addition|For example|As a result|Consequently|Although|"
                r"While|When|If|Each|Both|Most|Many|Some)\b", t, re.I):
        return False

    # A QUESTION IS NOT A CAPTION.
    #
    # OpenStax sets "Visual Connection" prompts directly under figures — "Why
    # does the cis face of the Golgi not face the plasma membrane?" — and they
    # pass every other test here: capitalised, complete, properly punctuated.
    # Worse, they are actively harmful downstream: that one keyword-matched to
    # a concept called "The Plasma Membrane" despite illustrating the Golgi,
    # because the words are in the question.
    if t.rstrip().endswith("?"):
        return False

    # Named sidebars are their own genre — "Careers in Action Registered
    # Dietitian Obesity is a worldwide health concern..." is a boxed feature
    # that happens to sit under a figure.
    if re.match(r"^(Careers?|Concepts?|Evolution|Link|Visual|Art|Everyday)\s+"
                r"(in|to|Connection)\b", t, re.I):
        return False
    return True


def detect_profile(pdf_path, sample_pages=150, first_page=0):
    """Which caption strategy this book uses, chosen by trying them.

    Returns {strategy, score, tried}. Scored on how many sampled figures each
    strategy gives a PLAUSIBLE caption to — not merely a caption, since the
    loosest strategy always finds *something* and would win a naive count.
    """
    try:
        import fitz
    except ImportError:
        return {"strategy": "inside_box", "score": 0.0, "tried": {},
                "reason": "PyMuPDF unavailable"}
    doc = fitz.open(pdf_path)
    last = min(len(doc), first_page + sample_pages)
    samples = []
    for pno in range(first_page, last):
        try:
            page = doc[pno]
            blocks = _blocks(page)
            for xref, *_ in (page.get_images(full=True) or []):
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                r = rects[0]
                if r.width < MIN_EDGE or r.height < MIN_EDGE:
                    continue
                samples.append(((r.x0, r.y0, r.x1, r.y1), blocks))
        except Exception:
            continue
    doc.close()
    if not samples:
        return {"strategy": "inside_box", "score": 0.0, "tried": {},
                "reason": "no figures in the sample"}

    tried = {}
    for st in STRATEGIES:
        hits = 0
        for rect, blocks in samples:
            c = _candidate(st, rect, blocks)
            if c and _plausible_caption(c["text"]):
                hits += 1
        tried[st] = round(hits / len(samples), 3)
    # A NEAR-TIE MUST NOT BE DECIDED BY NOISE.
    #
    # Measured: on a 40-page sample this chose `inside_box` at 0.083 over
    # `below` at 0.083, and on a 120-page sample chose `below` at 0.222 over
    # `inside_box` at 0.083 — the same book, opposite answers. When no strategy
    # wins by a real margin, prefer the PRECISE one: a missing caption costs a
    # figure, a wrong caption costs trust and propagates into alt text and
    # concept matching.
    ranked = sorted(tried.items(), key=lambda kv: -kv[1])
    winner, top = ranked[0]
    runner, second = ranked[1] if len(ranked) > 1 else (None, 0.0)
    decisive = (top - second) >= 0.08
    if not decisive:
        winner = "inside_box" if "inside_box" in tried else winner
    return {"strategy": winner, "score": tried[winner], "tried": tried,
            "sampled_figures": len(samples), "decisive": decisive,
            "note": None if decisive else
                    "no strategy won by a margin — defaulted to the precise one"}


def extract(pdf_path, max_pages=None, first_page=0, strategy=None):
    """Figures with captions from a PDF.

    Returns a list of dicts. Never raises on a bad page — a textbook with one
    unreadable page should still yield the other 1485.
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not installed — figure extraction unavailable")
        return []

    doc = fitz.open(pdf_path)
    last = len(doc) if max_pages is None else min(len(doc), first_page + max_pages)
    seen_hash = {}
    figures = []
    # A one-element list so the per-page loop can update it without `nonlocal`.
    carried_section = [None]

    for pno in range(first_page, last):
        try:
            page = doc[pno]
            blocks = _blocks(page)
            headings = _headings_on(blocks)
            for xref, *_ in (page.get_images(full=True) or []):
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                if not rects:
                    continue
                r = rects[0]
                w, h = r.width, r.height
                if w < MIN_EDGE or h < MIN_EDGE or (w * h) < MIN_PIXELS:
                    continue
                if max(w, h) / max(1.0, min(w, h)) > MAX_ASPECT:
                    continue
                info = doc.extract_image(xref)
                data = info.get("image")
                if not data:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                seen_hash.setdefault(digest, []).append(pno)
                rect4 = (r.x0, r.y0, r.x1, r.y1)
                if strategy:
                    cap = _candidate(strategy, rect4, blocks)
                    if cap and not _plausible_caption(cap["text"]):
                        cap = None      # refuse rather than attach body prose
                else:
                    cap = _caption_for(rect4, blocks)
                figures.append({
                    "sha256": digest,
                    "page": pno + 1,
                    "bytes": data,
                    "mime": f"image/{info.get('ext', 'png')}",
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "caption": (cap or {}).get("text"),
                    "label": (cap or {}).get("label"),
                    "figure_number": (cap or {}).get("number"),
                    "section": _section_for((r.x0, r.y0, r.x1, r.y1),
                                            headings, carried_section[0]),
                })
            if headings:
                carried_section[0] = headings[-1][1]
        except Exception as e:
            logger.debug(f"page {pno} skipped: {e}")

    # REPETITION IS THE EFFECTIVE DECORATION FILTER. Size and aspect catch rules
    # and icons; they do not catch a chapter-header illustration, which is a
    # perfectly figure-shaped image that simply recurs. A real figure appears
    # once.
    kept = [f for f in figures if len(seen_hash.get(f["sha256"], [])) < REPEAT_LIMIT]
    dropped = len(figures) - len(kept)
    if dropped:
        logger.info(f"[FIGURES] dropped {dropped} repeated image(s) as furniture")

    # Deduplicate what remains, keeping the first occurrence.
    out, seen = [], set()
    for f in kept:
        if f["sha256"] in seen:
            continue
        seen.add(f["sha256"])
        out.append(f)
    doc.close()
    return out


def captioned(figures):
    """Only figures that carry a caption — the ones that can become assets.

    An uncaptioned image cannot be attached to a concept with a role, cannot be
    given honest alt text, and cannot be checked for relevance. Storing it would
    be storing exactly the decorative asset the evidence warns against.
    """
    return [f for f in figures if f.get("caption")]


def summarise(figures):
    total = len(figures)
    with_cap = len(captioned(figures))
    with_sec = sum(1 for f in figures if f.get("section"))
    return {
        "figures": total,
        "captioned": with_cap,
        "caption_rate": round(with_cap / total, 3) if total else 0.0,
        "with_section": with_sec,
        "pages_spanned": len({f["page"] for f in figures}),
    }


def ingest(pdf_path, storage, course_uid, license, source, provenance_url=None,
           first_page=0, max_pages=None, strategy=None, min_caption=True):
    """Extract figures and store them as licensed, captioned, roled assets.

    Returns a summary. Three refusals are deliberate and each drops material:

      * NO LICENCE, NO INGEST. Enforced at the storage boundary too, but caught
        here so a whole book is refused once rather than every figure.
      * NO CAPTION, NO ASSET (`min_caption`). An uncaptioned figure cannot be
        given honest alt text or checked for relevance, and storing it would be
        storing exactly the decorative asset the evidence warns against.
      * The caption becomes the alt text VERBATIM and is marked
        `caption_verified`, because it is the publisher's own words. Nothing is
        generated, so there is no caption to hallucinate.

    Assets are stored but NOT attached to concepts here. Attachment needs a
    role, and which figure illustrates which concept is a matching problem this
    does not attempt — see `match_to_concepts`.
    """
    if not license:
        logger.warning(f"[INGEST] refused {pdf_path}: no licence stated")
        return {"ingested": 0, "reason": "no licence"}

    if strategy is None:
        prof = detect_profile(pdf_path, first_page=first_page)
        strategy = prof["strategy"]
        logger.info(f"[INGEST] format profile: {strategy} "
                    f"(score {prof['score']}, tried {prof.get('tried')})")

    figs = extract(pdf_path, max_pages=max_pages, first_page=first_page,
                   strategy=strategy)
    usable = captioned(figs) if min_caption else figs
    stored, refused = 0, 0
    for f in usable:
        aid = storage.courses.save_asset(
            f["sha256"], data=f["bytes"], mime=f["mime"],
            width=f["width"], height=f["height"], source=source,
            license=license, provenance_url=provenance_url,
            alt_text=f["caption"], caption=f["caption"],
            caption_verified=True)
        if aid:
            stored += 1
        else:
            refused += 1
    return {"figures_found": len(figs), "captioned": len(captioned(figs)),
            "stored": stored, "refused": refused, "strategy": strategy,
            "caption_rate": round(len(captioned(figs)) / len(figs), 3) if figs else 0.0}


def match_to_concepts(figures, concepts, min_overlap=0.30):
    """Which figure belongs to which concept, by caption/section word overlap.

    Deliberately model-free and deliberately strict. A figure that matches
    nothing is left unattached rather than assigned to its nearest neighbour:
    an asset with a weak match is the decorative case, and the role field exists
    precisely so those cannot be attached.
    """
    def words(t):
        return {w for w in re.findall(r"[a-z]+", (t or "").lower()) if len(w) > 3}

    out = []
    for f in figures:
        fw = words(f.get("caption")) | words(f.get("section"))
        if not fw:
            continue
        best, best_score = None, 0.0
        for c in concepts:
            cw = words(c.get("title")) | words(" ".join(c.get("objectives") or []))
            if not cw:
                continue
            score = len(fw & cw) / len(cw)
            if score > best_score:
                best, best_score = c, score
        if best and best_score >= min_overlap:
            out.append({"figure": f, "concept_uid": best.get("uid"),
                        "concept_title": best.get("title"),
                        "score": round(best_score, 3),
                        "role": "illustrates"})
    return out
