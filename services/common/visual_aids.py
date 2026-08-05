"""Visual teaching aids — the tutor's blackboard.

An *aid* is a diagram shown ABOVE a tutor message in the chat: a number line, a
labelled triangle, a concept map, a timeline. It exists to make a question
askable ("what do you notice about these two squares?"), not to decorate the
answer.

THE CENTRAL DECISION: **aids are specs, not pixels.**

The model emits structured data (`{"kind":"number_line","min":-5,...}`) and the
BROWSER draws the SVG. Nothing here ever produces an image. That falls out of
four hard constraints in this system:

  1. `/state` returns the whole transcript on a 2-second poll. A base64 PNG is
     ~40-200 KB; fifty of them in the transcript is a multi-megabyte payload
     re-sent every two seconds. A spec is ~0.3-2 KB and the bytes leave the
     poll entirely (descriptor in the transcript, spec fetched once by id).
  2. Aids must be legible in dark mode. A matplotlib PNG bakes a white canvas.
     An SVG drawn from CSS custom properties re-themes for free.
  3. ~30 s per LLM call. A spec is emitted inline in the SAME generation as the
     message, so an aid costs zero extra round-trips. Nothing here calls an LLM.
  4. A spec is inspectable, diffable and unit-testable. A PNG is not — you
     cannot assert that a diagram labelled the hypotenuse.

Everything the model sends is untrusted: every field is clamped, every count is
bounded, colours are an enum (never raw CSS), and no free-form markup survives.
`normalize_aid()` never raises — a malformed aid is dropped and the tutor's
message is delivered without it. Aids are strictly additive; a broken aid must
never cost the learner their turn.

Pedagogy note (enforced by prompt + by `stage`, not by this module): an aid
should pose the question, not answer it. That is what progressive `stage`s are
for — show the setup, ask, then reveal the next layer once the learner has
committed to an answer.
"""
import hashlib
import json
import logging
import re
from collections import OrderedDict

logger = logging.getLogger(__name__)


# --- Bounds ------------------------------------------------------------------
# Every one of these is a cap on model-supplied data. They are deliberately
# generous for teaching and deliberately finite for safety.
MAX_SPEC_BYTES = 24_000        # a spec bigger than this is not a teaching aid
MAX_TEXT = 160                 # any single label
MAX_DETAIL = 400               # any single long-form detail string
MAX_ALT = 900                  # alt-text / description
MAX_ITEMS = 60                 # marks, events, steps, rows, nodes…
MAX_SERIES = 6
MAX_POINTS = 240               # samples in one plot series
MAX_COLS = 8
MAX_ROWS = 30
MAX_STAGE = 6
MAX_AIDS_PER_MESSAGE = 2       # more than two diagrams per turn is noise

# Colour is an ENUM, never a CSS value from the model. The client maps these to
# theme-aware custom properties so every aid is legible in light and dark.
COLORS = ("c1", "c2", "c3", "c4", "c5", "c6",
          "accent", "muted", "success", "warning", "danger")
DEFAULT_COLOR = "c1"

KINDS = ("number_line", "geometry", "plot", "bars", "graph", "timeline",
         "table", "venn", "cycle", "steps", "fraction", "image")

# Provenance tiers — surfaced in the UI so a learner can tell a computed figure
# from a model sketch. This is the trust surface, at the level of one diagram.
#   computed  — deterministic from structured data (a plotted function, a table)
#   retrieved — from a grounded source; carries attribution + licence
#   authored  — the model drew it from its own understanding; unverified
TIERS = ("computed", "retrieved", "authored")


class AidError(ValueError):
    """A spec that could not be salvaged. Caught at the boundary; never escapes."""


# --- Coercion helpers --------------------------------------------------------
# Each returns a safe value or raises AidError. Callers work in a try/except so
# one bad field drops one aid, never a turn.
def _num(value, field, lo=-1e9, hi=1e9):
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise AidError(f"'{field}' must be a number")
    if out != out or out in (float("inf"), float("-inf")):
        raise AidError(f"'{field}' must be finite")
    return max(lo, min(hi, out))


def _opt_num(value, field, default=None, lo=-1e9, hi=1e9):
    if value is None or value == "":
        return default
    return _num(value, field, lo, hi)


def _text(value, limit=MAX_TEXT):
    """Collapse to a single-line, length-capped plain string.

    Control characters go; `<` and `>` STAY. Stripping them here looked safe and
    was actively wrong — it turns the label "2 < x < 5" into "2 x 5", silently
    destroying exactly the inequality a number line exists to teach.

    The contract instead: **aid text is data, and is escaped at the render
    boundary.** The client builds every label with `document.createTextNode` /
    `textContent` and never string-concatenates a spec value into markup, so a
    literal `<script>` is drawn as the eight characters it is. `_html()` is
    provided for the rare server-side caller that must emit markup.
    """
    if value is None:
        return ""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _html(value):
    """Escape aid text for a server-side HTML context. The browser renderer does
    not use this — it uses textContent — but any future server-rendered view
    (print sheet, exported worksheet) must."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _int(value, field, lo, hi, default=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise AidError(f"'{field}' must be an integer")
    return max(lo, min(hi, out))


def _color(value):
    v = str(value or "").strip().lower()
    return v if v in COLORS else DEFAULT_COLOR


def _stage(value):
    """Which reveal step an element belongs to. 0 = visible immediately."""
    return _int(value, "stage", 0, MAX_STAGE, default=0)


def _bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _seq(value, field, limit=MAX_ITEMS):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AidError(f"'{field}' must be a list")
    if len(value) > limit:
        logger.debug("aid: truncating '%s' from %d to %d", field, len(value), limit)
    return list(value)[:limit]


def _obj(value, field):
    if not isinstance(value, dict):
        raise AidError(f"each entry in '{field}' must be an object")
    return value


# --- Per-kind normalizers ----------------------------------------------------
# Each takes the model's raw spec and returns a clean one. They are the only
# place that decides what a kind may contain; the client renders what it is
# handed and validates nothing beyond presence.

def _norm_number_line(s):
    lo = _num(s.get("min", 0), "min")
    hi = _num(s.get("max", 10), "max")
    if hi <= lo:
        hi = lo + 1
    step = _opt_num(s.get("step"), "step", None, 1e-6, abs(hi - lo))
    if step is None:
        step = _nice_step(lo, hi)

    marks = []
    for raw in _seq(s.get("marks"), "marks"):
        m = _obj(raw, "marks")
        marks.append({
            "at": _num(m.get("at", 0), "marks.at", lo - abs(hi - lo), hi + abs(hi - lo)),
            "label": _text(m.get("label"), 40),
            "style": m.get("style") if m.get("style") in ("dot", "open", "arrow", "tick") else "dot",
            "color": _color(m.get("color")),
            "stage": _stage(m.get("stage")),
        })

    intervals = []
    for raw in _seq(s.get("intervals"), "intervals", 12):
        i = _obj(raw, "intervals")
        a = _num(i.get("from", lo), "intervals.from")
        b = _num(i.get("to", hi), "intervals.to")
        intervals.append({
            "from": min(a, b), "to": max(a, b),
            "open_start": _bool(i.get("open_start")),
            "open_end": _bool(i.get("open_end")),
            "label": _text(i.get("label"), 60),
            "color": _color(i.get("color")),
            "stage": _stage(i.get("stage")),
        })
    return {"min": lo, "max": hi, "step": step, "marks": marks,
            "intervals": intervals, "label": _text(s.get("label"), 80)}


def _nice_step(lo, hi):
    """A tick interval that yields roughly 5-12 ticks — so a model that omits
    `step` still gets a readable axis instead of 400 tick marks."""
    span = abs(hi - lo) or 1.0
    raw = span / 8.0
    mag = 10 ** _floor_log10(raw)
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * mag:
            return mult * mag
    return 10 * mag


def _floor_log10(x):
    import math
    return int(math.floor(math.log10(x))) if x > 0 else 0


def _norm_geometry(s):
    points = {}
    raw_points = s.get("points") or {}
    if not isinstance(raw_points, dict):
        raise AidError("'points' must be an object of name -> [x, y]")
    for name, xy in list(raw_points.items())[:MAX_ITEMS]:
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            raise AidError(f"point '{name}' must be [x, y]")
        points[_text(name, 12) or "P"] = [_num(xy[0], "point.x"), _num(xy[1], "point.y")]
    if not points:
        raise AidError("geometry needs at least one point")

    def _known(name):
        n = _text(name, 12)
        if n not in points:
            raise AidError(f"unknown point '{n}'")
        return n

    segments = []
    for raw in _seq(s.get("segments"), "segments"):
        g = _obj(raw, "segments")
        segments.append({
            "from": _known(g.get("from")), "to": _known(g.get("to")),
            "label": _text(g.get("label"), 40),
            "style": g.get("style") if g.get("style") in ("solid", "dashed") else "solid",
            "color": _color(g.get("color")), "stage": _stage(g.get("stage")),
        })

    polygons = []
    for raw in _seq(s.get("polygons"), "polygons", 12):
        p = _obj(raw, "polygons")
        verts = [_known(v) for v in _seq(p.get("vertices"), "polygons.vertices", 24)]
        if len(verts) < 3:
            raise AidError("a polygon needs 3+ vertices")
        polygons.append({"vertices": verts, "label": _text(p.get("label"), 40),
                         "color": _color(p.get("color")), "stage": _stage(p.get("stage"))})

    circles = []
    for raw in _seq(s.get("circles"), "circles", 12):
        c = _obj(raw, "circles")
        circles.append({"center": _known(c.get("center")),
                        "r": _num(c.get("r", 1), "circles.r", 1e-6, 1e6),
                        "label": _text(c.get("label"), 40),
                        "color": _color(c.get("color")), "stage": _stage(c.get("stage"))})

    angles = []
    for raw in _seq(s.get("angles"), "angles", 12):
        a = _obj(raw, "angles")
        angles.append({"at": _known(a.get("at")), "from": _known(a.get("from")),
                       "to": _known(a.get("to")), "label": _text(a.get("label"), 24),
                       "right": _bool(a.get("right")), "stage": _stage(a.get("stage"))})

    labels = []
    for raw in _seq(s.get("labels"), "labels", 24):
        l = _obj(raw, "labels")
        at = l.get("at")
        if isinstance(at, str):
            pos = points[_known(at)]
        elif isinstance(at, (list, tuple)) and len(at) >= 2:
            pos = [_num(at[0], "labels.x"), _num(at[1], "labels.y")]
        else:
            raise AidError("'labels.at' must be a point name or [x, y]")
        labels.append({"at": pos, "text": _text(l.get("text"), 60),
                       "color": _color(l.get("color")), "stage": _stage(l.get("stage"))})

    return {"points": points, "segments": segments, "polygons": polygons,
            "circles": circles, "angles": angles, "labels": labels,
            "show_grid": _bool(s.get("show_grid"), False),
            "show_point_labels": _bool(s.get("show_point_labels"), True)}


def _norm_plot(s):
    series = []
    for raw in _seq(s.get("series"), "series", MAX_SERIES):
        ser = _obj(raw, "series")
        pts = []
        for p in _seq(ser.get("points"), "series.points", MAX_POINTS):
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            try:
                pts.append([_num(p[0], "x"), _num(p[1], "y")])
            except AidError:
                continue          # a single non-finite sample (a pole) is skipped
        if not pts:
            continue
        series.append({
            "points": pts, "label": _text(ser.get("label"), 60),
            "style": ser.get("style") if ser.get("style") in ("line", "dashed", "scatter", "area") else "line",
            "color": _color(ser.get("color")), "stage": _stage(ser.get("stage")),
        })
    if not series:
        raise AidError("plot needs at least one series with points")

    markers = []
    for raw in _seq(s.get("markers"), "markers", 24):
        m = _obj(raw, "markers")
        at = m.get("at")
        if not isinstance(at, (list, tuple)) or len(at) < 2:
            raise AidError("'markers.at' must be [x, y]")
        markers.append({"at": [_num(at[0], "marker.x"), _num(at[1], "marker.y")],
                        "label": _text(m.get("label"), 40),
                        "color": _color(m.get("color")), "stage": _stage(m.get("stage"))})

    return {"series": series, "markers": markers,
            "x_label": _text(s.get("x_label"), 40), "y_label": _text(s.get("y_label"), 40),
            "x_range": _range(s.get("x_range")), "y_range": _range(s.get("y_range")),
            "show_grid": _bool(s.get("show_grid"), True),
            "show_legend": _bool(s.get("show_legend"), len(series) > 1)}


def _range(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    lo, hi = _num(value[0], "range.min"), _num(value[1], "range.max")
    return [min(lo, hi), max(lo, hi)] if hi != lo else None


def _norm_bars(s):
    cats = [_text(c, 40) for c in _seq(s.get("categories"), "categories", 24)]
    if not cats:
        raise AidError("bars needs 'categories'")
    series = []
    for raw in _seq(s.get("series"), "series", MAX_SERIES):
        ser = _obj(raw, "series")
        vals = [_num(v, "series.values") for v in _seq(ser.get("values"), "series.values", 24)]
        vals = (vals + [0.0] * len(cats))[:len(cats)]     # pad/trim to the categories
        series.append({"label": _text(ser.get("label"), 60), "values": vals,
                       "color": _color(ser.get("color")), "stage": _stage(ser.get("stage"))})
    if not series:
        raise AidError("bars needs at least one series")
    return {"categories": cats, "series": series,
            "orientation": "horizontal" if str(s.get("orientation", "")).startswith("h") else "vertical",
            "y_label": _text(s.get("y_label"), 40),
            "highlight": [_int(i, "highlight", 0, len(cats) - 1, 0)
                          for i in _seq(s.get("highlight"), "highlight", 24)],
            "show_values": _bool(s.get("show_values"), True),
            "show_legend": _bool(s.get("show_legend"), len(series) > 1)}


def _norm_graph(s):
    nodes, seen = [], set()
    for raw in _seq(s.get("nodes"), "nodes", 40):
        n = _obj(raw, "nodes")
        nid = _text(n.get("id") or n.get("label"), 40)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        nodes.append({"id": nid, "label": _text(n.get("label") or nid, 60),
                      "detail": _text(n.get("detail"), MAX_DETAIL),
                      "color": _color(n.get("color")),
                      "shape": n.get("shape") if n.get("shape") in ("box", "round", "diamond") else "round",
                      "stage": _stage(n.get("stage"))})
    if not nodes:
        raise AidError("graph needs nodes")

    edges = []
    for raw in _seq(s.get("edges"), "edges", 80):
        e = _obj(raw, "edges")
        a, b = _text(e.get("from"), 40), _text(e.get("to"), 40)
        if a not in seen or b not in seen or a == b:
            continue                                  # dangling/self edges dropped
        edges.append({"from": a, "to": b, "label": _text(e.get("label"), 40),
                      "directed": _bool(e.get("directed"), True),
                      "color": _color(e.get("color")), "stage": _stage(e.get("stage"))})

    direction = "TB" if str(s.get("direction", "TB")).upper().startswith("T") else "LR"
    return {"nodes": nodes, "edges": edges, "direction": direction,
            "layout": layout_graph(nodes, edges, direction)}


def layout_graph(nodes, edges, direction="TB"):
    """Deterministic layered layout — pure Python, no networkx.

    Layer 0 is every node with no incoming edge; each subsequent layer is one
    hop further from a root. Cycles (a concept map is rarely a DAG) are handled
    by capping the relaxation passes, which leaves back-edges pointing at an
    earlier layer — visually correct for a feedback loop.

    Returns {node_id: [col, row]} in abstract units; the client scales.
    Determinism matters: the same graph must lay out identically on every
    re-render or a diagram twitches while the learner is looking at it.
    """
    ids = [n["id"] for n in nodes]
    incoming = {i: 0 for i in ids}
    for e in edges:
        incoming[e["to"]] = incoming.get(e["to"], 0) + 1

    depth = {i: 0 for i in ids}
    roots = [i for i in ids if incoming.get(i, 0) == 0] or ids[:1]
    for i in ids:
        depth[i] = 0 if i in roots else 1
    # Relax: a node sits one layer below its deepest predecessor. Bounded by
    # node count so a cycle terminates instead of spinning.
    for _ in range(min(len(ids), 24)):
        changed = False
        for e in edges:
            want = depth[e["from"]] + 1
            if want > depth[e["to"]] and want < len(ids):
                depth[e["to"]] = want
                changed = True
        if not changed:
            break

    layers = OrderedDict()
    for i in ids:                                   # insertion order = model order
        layers.setdefault(depth[i], []).append(i)

    pos = {}
    widest = max((len(v) for v in layers.values()), default=1)
    for layer_idx in sorted(layers):
        row = layers[layer_idx]
        # Centre each layer against the widest one so the graph reads centred.
        offset = (widest - len(row)) / 2.0
        for slot, nid in enumerate(row):
            across, along = offset + slot, layer_idx
            pos[nid] = [across, along] if direction == "TB" else [along, across]
    return pos


def _norm_timeline(s):
    events = []
    for raw in _seq(s.get("events"), "events", 30):
        e = _obj(raw, "events")
        events.append({"at": _num(e.get("at", 0), "events.at"),
                       "label": _text(e.get("label"), 80),
                       "detail": _text(e.get("detail"), MAX_DETAIL),
                       "color": _color(e.get("color")), "stage": _stage(e.get("stage"))})
    if not events:
        raise AidError("timeline needs events")
    events.sort(key=lambda e: e["at"])
    ats = [e["at"] for e in events]
    lo = _opt_num(s.get("min"), "min", min(ats))
    hi = _opt_num(s.get("max"), "max", max(ats))
    if hi <= lo:
        hi = lo + max(1.0, abs(lo) * 0.1)
    return {"events": events, "min": lo, "max": hi,
            "unit": _text(s.get("unit"), 24), "label": _text(s.get("label"), 80)}


def _norm_table(s):
    cols = [_text(c, 60) for c in _seq(s.get("columns"), "columns", MAX_COLS)]
    if not cols:
        raise AidError("table needs columns")
    rows = []
    for raw in _seq(s.get("rows"), "rows", MAX_ROWS):
        if not isinstance(raw, (list, tuple)):
            raise AidError("each row must be a list of cells")
        cells = [_text(c, 120) for c in list(raw)[:len(cols)]]
        rows.append(cells + [""] * (len(cols) - len(cells)))
    if not rows:
        raise AidError("table needs rows")
    highlight = []
    for h in _seq(s.get("highlight_cells"), "highlight_cells", 40):
        if isinstance(h, (list, tuple)) and len(h) >= 2:
            highlight.append([_int(h[0], "row", 0, len(rows) - 1, 0),
                              _int(h[1], "col", 0, len(cols) - 1, 0)])
    return {"columns": cols, "rows": rows, "highlight_cells": highlight,
            "row_header": _bool(s.get("row_header"), True)}


def _norm_venn(s):
    sets = []
    for raw in _seq(s.get("sets"), "sets", 3):
        v = _obj(raw, "sets")
        sets.append({"label": _text(v.get("label"), 60),
                     "items": [_text(i, 60) for i in _seq(v.get("items"), "sets.items", 8)],
                     "color": _color(v.get("color"))})
    if len(sets) < 2:
        raise AidError("venn needs 2 or 3 sets")
    overlaps = []
    for raw in _seq(s.get("overlaps"), "overlaps", 4):
        o = _obj(raw, "overlaps")
        idx = sorted({_int(i, "overlaps.sets", 0, len(sets) - 1, 0)
                      for i in _seq(o.get("sets"), "overlaps.sets", 3)})
        if len(idx) < 2:
            continue
        overlaps.append({"sets": idx,
                         "items": [_text(i, 60) for i in _seq(o.get("items"), "overlaps.items", 8)],
                         "stage": _stage(o.get("stage"))})
    return {"sets": sets, "overlaps": overlaps}


def _norm_cycle(s):
    steps = []
    for raw in _seq(s.get("steps"), "steps", 10):
        st = _obj(raw, "steps")
        steps.append({"label": _text(st.get("label"), 60),
                      "detail": _text(st.get("detail"), MAX_DETAIL),
                      "color": _color(st.get("color")), "stage": _stage(st.get("stage"))})
    if len(steps) < 2:
        raise AidError("cycle needs 2+ steps")
    return {"steps": steps, "center_label": _text(s.get("center_label"), 40)}


def _norm_steps(s):
    steps = []
    for raw in _seq(s.get("steps"), "steps", 20):
        st = _obj(raw, "steps")
        steps.append({"label": _text(st.get("label"), 120),
                      "detail": _text(st.get("detail"), MAX_DETAIL),
                      "color": _color(st.get("color")), "stage": _stage(st.get("stage"))})
    if not steps:
        raise AidError("steps needs at least one step")
    return {"steps": steps, "numbered": _bool(s.get("numbered"), True)}


def _norm_fraction(s):
    models = []
    for raw in _seq(s.get("models"), "models", 4):
        m = _obj(raw, "models")
        parts = _int(m.get("parts", 4), "models.parts", 1, 24, 4)
        models.append({"parts": parts,
                       "shaded": _int(m.get("shaded", 0), "models.shaded", 0, parts, 0),
                       "label": _text(m.get("label"), 40),
                       "shape": "circle" if str(m.get("shape", "")).startswith("c") else "bar",
                       "color": _color(m.get("color")), "stage": _stage(m.get("stage"))})
    if not models:
        raise AidError("fraction needs at least one model")
    return {"models": models, "show_labels": _bool(s.get("show_labels"), True)}


# Only these schemes may reach an <img>. `data:` is allowed for an uploaded or
# pre-generated asset; everything else must be same-origin. No remote hosts —
# an offline tutor that silently phones out is a bug, not a feature.
_SAFE_SRC = re.compile(r"^(data:image/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+"
                       r"|/static/[\w./-]+|/api/[\w./-]+)$")


def _norm_image(s):
    src = str(s.get("src") or "").strip()
    if not _SAFE_SRC.match(src):
        raise AidError("image 'src' must be a data: image or a same-origin /static or /api path")
    pins = []
    for raw in _seq(s.get("pins"), "pins", 12):
        p = _obj(raw, "pins")
        pins.append({"x": _num(p.get("x", 0.5), "pins.x", 0, 1),
                     "y": _num(p.get("y", 0.5), "pins.y", 0, 1),
                     "label": _text(p.get("label"), 12),
                     "detail": _text(p.get("detail"), MAX_DETAIL),
                     "stage": _stage(p.get("stage"))})
    return {"src": src, "pins": pins,
            "fit": "cover" if str(s.get("fit", "")) == "cover" else "contain"}


NORMALIZERS = {
    "number_line": _norm_number_line, "geometry": _norm_geometry, "plot": _norm_plot,
    "bars": _norm_bars, "graph": _norm_graph, "timeline": _norm_timeline,
    "table": _norm_table, "venn": _norm_venn, "cycle": _norm_cycle,
    "steps": _norm_steps, "fraction": _norm_fraction, "image": _norm_image,
}


# --- Deterministic description (accessibility + TTS + text-only) -------------
# Generated from the spec, never by an LLM: it costs nothing, cannot hallucinate,
# and cannot drift from what is actually drawn. This is what a screen reader
# announces, what TTS speaks after the message, and what replaces the diagram
# entirely in text-only mode (B13.9).

def describe(kind, spec, title=""):
    try:
        body = _DESCRIBERS.get(kind, lambda _s: "")(spec)
    except Exception as e:                        # a describer must never break an aid
        logger.warning("aid describe(%s) failed: %s", kind, e)
        body = ""
    lead = title or _KIND_NOUN.get(kind, "A diagram")
    out = f"{lead}. {body}".strip() if body else lead
    return out[:MAX_ALT]


_KIND_NOUN = {
    "number_line": "A number line", "geometry": "A geometric figure",
    "plot": "A graph", "bars": "A bar chart", "graph": "A concept map",
    "timeline": "A timeline", "table": "A comparison table", "venn": "A Venn diagram",
    "cycle": "A cycle diagram", "steps": "A sequence of steps",
    "fraction": "A fraction model", "image": "An image",
}


def _fmt(n):
    """Numbers a person would read aloud: 3 not 3.0, 0.25 not 0.25000000001."""
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    return f"{round(n, 4):g}" if isinstance(n, (int, float)) else str(n)


def _join(items, limit=6):
    """Comma list with a spoken 'and' before the last item, truncated politely.
    Written for the ear: this text is read aloud by TTS, not just displayed."""
    items = [str(i) for i in items if i]
    if not items:
        return ""
    overflow = 0
    if len(items) > limit:
        overflow = len(items) - limit
        items = items[:limit]
    if len(items) == 1:
        head = items[0]
    else:
        head = ", ".join(items[:-1]) + " and " + items[-1]
    return f"{head}, and {overflow} more" if overflow else head


def _d_number_line(s):
    bits = [f"It runs from {_fmt(s['min'])} to {_fmt(s['max'])}"]
    if s["marks"]:
        # Only voice a label when it says something the number doesn't — a mark
        # at 5 labelled "5" should be read "5", not "5 (5)".
        bits.append("Marked at " + _join([
            _fmt(m["at"]) + (f" labelled {m['label']}"
                             if m["label"] and m["label"] != _fmt(m["at"]) else "")
            for m in s["marks"]]))
    for i in s["intervals"]:
        left = "open" if i["open_start"] else "closed"
        right = "open" if i["open_end"] else "closed"
        bits.append(f"A shaded interval from {_fmt(i['from'])} to {_fmt(i['to'])}, "
                    f"{left} on the left and {right} on the right"
                    + (f", labelled {i['label']}" if i["label"] else ""))
    return ". ".join(bits) + "."


def _d_geometry(s):
    bits = []
    if s["polygons"]:
        for p in s["polygons"]:
            n = len(p["vertices"])
            name = {3: "triangle", 4: "quadrilateral", 5: "pentagon", 6: "hexagon"}.get(n, f"{n}-sided shape")
            bits.append(f"A {name} with vertices {_join(p['vertices'])}")
    labelled = [f"{g['from']}{g['to']} labelled {g['label']}" for g in s["segments"] if g["label"]]
    if labelled:
        bits.append("Sides " + _join(labelled))
    right = [a for a in s["angles"] if a["right"]]
    if right:
        bits.append(f"A right angle at {_join([a['at'] for a in right])}")
    other = [f"the angle at {a['at']} labelled {a['label']}" for a in s["angles"]
             if a["label"] and not a["right"]]
    if other:
        bits.append("Also " + _join(other))
    for c in s["circles"]:
        bits.append(f"A circle centred at {c['center']} with radius {_fmt(c['r'])}")
    unknown = [l["text"] for l in s["labels"] if l["text"] in ("?", "x", "n")]
    if unknown:
        bits.append("One measurement is left unknown")
    return ". ".join(bits) + "." if bits else ""


def _d_plot(s):
    bits = []
    for ser in s["series"]:
        pts = ser["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        name = ser["label"] or "A curve"
        bits.append(f"{name} plotted from x={_fmt(min(xs))} to x={_fmt(max(xs))}, "
                    f"reaching y values between {_fmt(min(ys))} and {_fmt(max(ys))}")
    if s["markers"]:
        bits.append("Marked points: " + _join([f"{m['label'] or 'point'} at "
                                               f"({_fmt(m['at'][0])}, {_fmt(m['at'][1])})"
                                               for m in s["markers"]]))
    axes = [a for a in (s["x_label"], s["y_label"]) if a]
    if len(axes) == 2:
        bits.append(f"The horizontal axis is {s['x_label']} and the vertical axis is {s['y_label']}")
    return ". ".join(bits) + "."


def _d_bars(s):
    out = []
    for ser in s["series"]:
        pairs = [f"{c} {_fmt(v)}" for c, v in zip(s["categories"], ser["values"])]
        prefix = f"{ser['label']}: " if ser["label"] else ""
        out.append(prefix + _join(pairs, 8))
    tail = f" The vertical axis shows {s['y_label']}." if s["y_label"] else ""
    return ". ".join(out) + "." + tail


def _d_graph(s):
    bits = [f"{len(s['nodes'])} ideas: " + _join([n["label"] for n in s["nodes"]], 8)]
    links = [f"{e['from']} {e['label'] or 'connects to'} {e['to']}" for e in s["edges"][:6]]
    if links:
        bits.append("Links: " + _join(links, 6))
    return ". ".join(bits) + "."


def _d_timeline(s):
    return ("Events: " + _join([f"{_fmt(e['at'])}, {e['label']}" for e in s["events"]], 8) + ".")


def _d_table(s):
    return (f"Columns: {_join(s['columns'], 8)}. "
            f"{len(s['rows'])} rows comparing "
            f"{_join([r[0] for r in s['rows'] if r and r[0]], 8)}.")


def _d_venn(s):
    bits = [f"Circles for {_join([v['label'] for v in s['sets']])}"]
    for v in s["sets"]:
        if v["items"]:
            bits.append(f"Only in {v['label']}: {_join(v['items'], 6)}")
    for o in s["overlaps"]:
        names = _join([s["sets"][i]["label"] for i in o["sets"]])
        if o["items"]:
            bits.append(f"Shared by {names}: {_join(o['items'], 6)}")
    return ". ".join(bits) + "."


def _d_cycle(s):
    return (f"A repeating cycle: {_join([st['label'] for st in s['steps']], 8)}, "
            "then back to the start.")


def _d_steps(s):
    return " ".join(f"Step {i + 1}: {st['label']}."
                    + (f" {st['detail']}" if st["detail"] else "")
                    for i, st in enumerate(s["steps"][:8]))


def _d_fraction(s):
    return _join([f"{m['label'] or str(m['shaded']) + ' of ' + str(m['parts'])}: "
                  f"{m['shaded']} of {m['parts']} parts shaded" for m in s["models"]], 4) + "."


def _d_image(s):
    if s["pins"]:
        return "Labelled points: " + _join([f"{p['label']} {p['detail']}".strip()
                                            for p in s["pins"]], 8) + "."
    return ""


_DESCRIBERS = {
    "number_line": _d_number_line, "geometry": _d_geometry, "plot": _d_plot,
    "bars": _d_bars, "graph": _d_graph, "timeline": _d_timeline, "table": _d_table,
    "venn": _d_venn, "cycle": _d_cycle, "steps": _d_steps, "fraction": _d_fraction,
    "image": _d_image,
}


# --- ASSISTANCE RAILS --------------------------------------------------------
# The model this runs on (qwen3.5:9b, tier 2) is being asked for something it is
# asked for nowhere else: nested JSON with cross-references, emitted inside a
# conversational reply, with no retry and no constrained decoding — a fence in
# mid-prose cannot use Ollama's `format` schema the way the grading path does.
#
# So instead of demanding precision, meet the model where it is. Every rail here
# turns a near-miss into a working diagram rather than a silent drop. They are
# ordered by how often a 9B model actually trips:
#
#   1. malformed JSON      -> repair_json() (trailing commas, single quotes, …)
#   2. a plausible synonym -> KIND_ALIASES  ("flowchart" -> graph)
#   3. a plausible field   -> FIELD_ALIASES ("links" -> edges)
#   4. an incomplete figure-> derived (polygon edges -> segments)
#   5. a FALSE claim       -> verified and removed (a right-angle mark on an
#                             angle that is not 90°)
#
# Rail 5 is different in kind from the others and matters most. The first four
# make a diagram appear; the fifth stops a WRONG one appearing. A right-angle
# square drawn on a 53° angle teaches an error with the full authority of a
# figure, and no amount of prompt wording prevents it — only measurement does.

# Synonyms a model reaches for naturally. Mapping them costs nothing and
# converts an outright rejection into a correct diagram.
KIND_ALIASES = {
    "flowchart": "graph", "flow_chart": "graph", "concept_map": "graph",
    "conceptmap": "graph", "mind_map": "graph", "network": "graph",
    "tree": "graph", "hierarchy": "graph", "diagram": "graph",
    "bar_chart": "bars", "barchart": "bars", "bar": "bars", "column": "bars",
    "histogram": "bars", "chart": "bars",
    "line_chart": "plot", "line_graph": "plot", "linegraph": "plot",
    "scatter": "plot", "function": "plot", "curve": "plot", "graph_xy": "plot",
    "numberline": "number_line", "number-line": "number_line", "numline": "number_line",
    "shape": "geometry", "figure": "geometry", "triangle": "geometry",
    "geometric": "geometry", "geometry_figure": "geometry",
    "venn_diagram": "venn", "sets": "venn",
    "cycle_diagram": "cycle", "circular": "cycle", "loop": "cycle",
    "process": "steps", "procedure": "steps", "worked_example": "steps",
    "list": "steps", "sequence": "steps", "method": "steps",
    "fractions": "fraction", "pie": "fraction", "pie_chart": "fraction",
    "part_whole": "fraction", "area_model": "fraction",
    "comparison": "table", "grid": "table", "matrix": "table",
    "events": "timeline", "chronology": "timeline", "history": "timeline",
}

# Field names a model plausibly substitutes, per kind.
FIELD_ALIASES = {
    "graph": {"links": "edges", "connections": "edges", "relationships": "edges",
              "vertices": "nodes", "concepts": "nodes", "items": "nodes"},
    "bars": {"labels": "categories", "names": "categories", "x": "categories",
             "data": "series", "values": "_bare_values", "y": "_bare_values"},
    "plot": {"data": "series", "lines": "series", "curves": "series",
             "points": "_bare_points"},
    "timeline": {"items": "events", "dates": "events", "entries": "events"},
    "steps": {"items": "steps", "list": "steps", "stages": "steps",
              "instructions": "steps"},
    "cycle": {"stages": "steps", "phases": "steps", "items": "steps"},
    "table": {"headers": "columns", "header": "columns", "data": "rows",
              "body": "rows"},
    "venn": {"groups": "sets", "circles": "sets", "intersections": "overlaps",
             "shared": "overlaps"},
    "number_line": {"points": "marks", "values": "marks", "ranges": "intervals",
                    "start": "min", "end": "max"},
    "fraction": {"fractions": "models", "parts_list": "models", "bars": "models"},
    "geometry": {"lines": "segments", "edges": "segments", "sides": "segments",
                 "shapes": "polygons", "vertices": "points"},
}


def _requote(text):
    """Convert single-quoted JSON string literals to double-quoted ones.

    The shared `repair_json()` handles single-quoted KEYS but leaves values
    alone, so `['Mon','Tue']` still fails to parse. A global regex would be
    worse than useless here — it mangles apostrophes ("Newton's law") — so this
    scans character by character, tracks whether it is inside a double-quoted
    string, and treats a `'` as a delimiter only outside one.

    Deliberately conservative: it is only ever used as a fallback, and if the
    result still does not parse the aid is dropped as normal.
    """
    out, i, n = [], 0, len(text)
    in_double = False
    while i < n:
        ch = text[i]
        if in_double:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            # Read to the closing quote, honouring backslash escapes.
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "'":
                    break
                buf.append(text[j])
                j += 1
            if j >= n:
                return text                       # unterminated — give up cleanly
            body = "".join(buf).replace('"', '\\"')
            out.append('"' + body + '"')
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# _close_unbalanced() WAS HERE AND HAS BEEN REMOVED.
#
# It hand-rolled bracket-closing for truncated model output. That was
# reinventing a dependency this repo already declares: `fast-json-repair`
# (Rust) and `json-repair` (pure Python) both handle truncation, and better —
# verified on python:3.11-slim, which is what every service image runs:
#
#     '{"kind":"steps","steps":[{"label":"a"}'   -> {"kind":"steps","steps":[{"label":"a"}]}
#     '{"a":1,'                                  -> {"a":1}
#     '{"kind":"steps","steps":[{"label":"trunc' -> {"kind":"steps","steps":[{"label":"trunc"}]}
#
# The third case is the one my version could not do: it closed brackets but
# could not close a truncated STRING and recover the partial value. Hand-rolled
# parsing loses to a maintained parser, so repair_json() — which already tries
# both backends — is the single path.

def _repair_json_text(payload):
    """Parse model JSON, repairing what a 9B model commonly gets wrong.

    Delegates to the existing `repair_json()` (trailing commas, single quotes,
    Python True/False/None) rather than reimplementing it, then falls back to
    slicing the outermost braces when the model wrapped its JSON in prose.
    Returns (parsed, repaired_flag) or (None, False).
    """
    try:
        return json.loads(payload), False
    except json.JSONDecodeError:
        pass
    # Escalating repair: each candidate is a strictly more aggressive rewrite,
    # and the first that parses wins. Slicing to the outermost braces handles the
    # model narrating around its JSON ("Here is the diagram: {…}").
    def _candidates(src):
        yield src
        try:
            from services.common.llm_utils import repair_json
            fixed = repair_json(src)
            yield fixed
            yield _requote(fixed)
        except Exception:
            pass
        yield _requote(src)

    slices = [payload]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = payload.find(opener), payload.rfind(closer)
        if 0 <= start < end:
            slices.append(payload[start:end + 1])

    for i, chunk in enumerate(slices):
        for j, candidate in enumerate(_candidates(chunk)):
            try:
                return json.loads(candidate), (i > 0 or j > 0)
            except (json.JSONDecodeError, TypeError):
                continue
    return None, False


def _apply_rails(kind, spec):
    """Rename aliased fields and fill in what the model plausibly left out.
    Returns (kind, spec, notes) — notes name each rail that fired, so the probe
    can report which rails the feature is actually leaning on."""
    notes = []
    for alias, real in FIELD_ALIASES.get(kind, {}).items():
        if alias in spec and real not in spec:
            if real.startswith("_bare_"):
                continue                      # handled below; needs restructuring
            spec[real] = spec.pop(alias)
            notes.append(f"{kind}.{alias}->{real}")

    # A bare list of numbers where a series was expected — extremely common.
    if kind == "bars" and "series" not in spec:
        bare = spec.pop("values", None) or spec.pop("y", None)
        if isinstance(bare, list) and bare and not isinstance(bare[0], dict):
            spec["series"] = [{"values": bare, "label": _text(spec.pop("label", ""), 60)}]
            notes.append("bars.values->series")
    if kind == "plot" and "series" not in spec:
        bare = spec.get("points")
        if isinstance(bare, list) and bare:
            spec["series"] = [{"points": spec.pop("points"),
                               "label": _text(spec.pop("label", ""), 60)}]
            notes.append("plot.points->series")

    # Lists of plain strings where objects were expected ("steps": ["a","b"]).
    for field in ("steps", "events", "marks", "nodes", "models"):
        seq = spec.get(field)
        if isinstance(seq, list) and seq and any(not isinstance(i, dict) for i in seq):
            key = {"marks": "at", "models": "parts"}.get(field, "label")
            spec[field] = [i if isinstance(i, dict) else {key: i} for i in seq]
            notes.append(f"{field}[]->objects")

    if kind == "geometry":
        notes.extend(_geometry_rails(spec))
    return kind, spec, notes


def _geometry_rails(spec):
    """Geometry is the hardest kind for a small model — coordinate reasoning AND
    cross-referenced names. Two rails, one cosmetic and one about truth."""
    notes = []
    points = spec.get("points")
    if not isinstance(points, dict):
        return notes

    # Rail 4: a polygon with no segments still needs its edges drawn, and a model
    # that gave one usually meant the other.
    polys = spec.get("polygons") or []
    if polys and not spec.get("segments"):
        derived = []
        for poly in polys:
            verts = (poly or {}).get("vertices") or []
            for i in range(len(verts)):
                derived.append({"from": verts[i], "to": verts[(i + 1) % len(verts)]})
        if derived:
            spec["segments"] = derived
            notes.append("geometry.segments<-polygon edges")

    # Rail 5: never draw a claim that is false. A right-angle marker asserts 90°
    # with the authority of a figure; if the coordinates disagree, the marker is
    # the thing that is wrong, so it goes. Prompt wording cannot enforce this —
    # only arithmetic can.
    kept = []
    for ang in (spec.get("angles") or []):
        if not isinstance(ang, dict) or not _bool(ang.get("right")):
            kept.append(ang)
            continue
        try:
            at, a, b = points[ang["at"]], points[ang["from"]], points[ang["to"]]
            v1 = (a[0] - at[0], a[1] - at[1])
            v2 = (b[0] - at[0], b[1] - at[1])
            m1 = (v1[0] ** 2 + v1[1] ** 2) ** 0.5
            m2 = (v2[0] ** 2 + v2[1] ** 2) ** 0.5
            if m1 < 1e-9 or m2 < 1e-9:
                kept.append(ang)
                continue
            cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)
            if abs(cos) > 0.02:                     # ~1.1° tolerance
                ang = dict(ang)
                ang["right"] = False
                notes.append("geometry.right-angle claim removed (not 90°)")
                logger.info("Aid rail: dropped a false right-angle marker at "
                            "'%s' (cos=%.3f)", ang.get("at"), cos)
        except (KeyError, TypeError, IndexError):
            pass                                    # unresolvable -> normalizer decides
        kept.append(ang)
    if kept:
        spec["angles"] = kept
    return notes


# --- Aid assembly ------------------------------------------------------------
def max_stage(spec):
    """Highest reveal stage referenced anywhere in a spec. 0 means the aid shows
    all at once and the client renders no reveal control."""
    top = 0

    def walk(node):
        nonlocal top
        if isinstance(node, dict):
            if isinstance(node.get("stage"), int):
                top = max(top, node["stage"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(spec)
    return min(top, MAX_STAGE)


def aid_id(kind, spec):
    """Content-addressed id. Identical aids collapse to one store entry, and an
    id is stable across a re-render — so a pinned aid stays pinned."""
    blob = json.dumps({"k": kind, "s": spec}, sort_keys=True, separators=(",", ":"))
    return "aid_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def normalize_aid(raw, default_tier="authored"):
    """Turn one untrusted aid object into a clean, bounded aid.

    Returns (aid, None) or (None, "reason"). NEVER raises — the caller is in the
    middle of delivering a tutoring turn and a bad diagram must cost nothing.
    """
    try:
        if not isinstance(raw, dict):
            return None, "aid must be a JSON object"
        kind = str(raw.get("kind") or "").strip().lower().replace(" ", "_")
        # Rail 2: a plausible synonym is not a failure.
        if kind not in NORMALIZERS:
            aliased = KIND_ALIASES.get(kind)
            if aliased:
                logger.debug("Aid rail: kind '%s' -> '%s'", kind, aliased)
                kind = aliased
        if kind not in NORMALIZERS:
            return None, f"unknown aid kind '{kind}' (expected one of: {', '.join(KINDS)})"

        # The model may nest the payload under "spec" or inline it alongside
        # "kind" — both are natural to write, so accept both.
        raw_spec = raw.get("spec") if isinstance(raw.get("spec"), dict) else \
            {k: v for k, v in raw.items()
             if k not in ("kind", "title", "alt", "caption", "provenance", "source", "license")}

        if len(json.dumps(raw_spec, default=str)) > MAX_SPEC_BYTES:
            return None, "aid spec too large"

        # Rails 3-5: alias fields, fill in the obvious, and strip false claims —
        # BEFORE validation, so a near-miss becomes a diagram instead of a drop.
        raw_spec = dict(raw_spec)
        kind, raw_spec, rail_notes = _apply_rails(kind, raw_spec)
        if rail_notes:
            logger.debug("Aid rails fired: %s", ", ".join(rail_notes))

        spec = NORMALIZERS[kind](raw_spec)

        title = _text(raw.get("title"), 120)
        prov = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
        tier = prov.get("tier") or raw.get("tier") or default_tier
        aid = {
            "id": aid_id(kind, spec),
            "kind": kind,
            "title": title,
            "caption": _text(raw.get("caption"), 200),
            "alt": _text(raw.get("alt"), MAX_ALT) or describe(kind, spec, title),
            "spec": spec,
            "stage": 0,
            "stages_total": max_stage(spec),
            # WHO uncovers the next layer. Default "tutor": the FSM advances the
            # stage once the learner has actually answered, so the reveal is a
            # consequence of thinking rather than a button that skips it. A
            # "Show me" button on a Socratic diagram is a spoiler with a nice
            # label. "learner" is for reference figures and worked examples,
            # where self-paced uncovering is the point.
            "reveal": "learner" if str(raw.get("reveal", "")).lower() == "learner" else "tutor",
            "rails": rail_notes,          # which assistance rails this aid needed
            "provenance": {
                "tier": tier if tier in TIERS else default_tier,
                "source": _text(prov.get("source") or raw.get("source"), 120),
                "license": _text(prov.get("license") or raw.get("license"), 60),
                "url": _safe_url(prov.get("url") or raw.get("url")),
            },
        }
        return aid, None
    except AidError as e:
        return None, str(e)
    except Exception as e:                       # normalizers are defensive; belt and braces
        logger.warning("aid normalization failed: %s", e)
        return None, f"malformed aid: {e}"


def _safe_url(value):
    u = str(value or "").strip()
    return u[:300] if re.match(r"^https?://[\w.-]+", u) else ""


def descriptor(aid):
    """The slim object that rides in the transcript (~200 bytes).

    The full spec stays in the AidStore and is fetched once by id. This is the
    single most important thing in the file: it keeps the 2-second `/state`
    poll small no matter how many diagrams a session accumulates. It carries
    enough (kind, title, alt) for the client to render the card frame, the
    accessible description and the text-only fallback with no fetch at all.
    """
    return {"id": aid["id"], "kind": aid["kind"], "title": aid["title"],
            "caption": aid["caption"], "alt": aid["alt"], "stage": aid.get("stage", 0),
            "stages_total": aid.get("stages_total", 0),
            "reveal": aid.get("reveal", "tutor"),
            "tier": aid.get("provenance", {}).get("tier", "authored")}


# --- Inline directive extraction --------------------------------------------
# The low-latency production path. The model writes its message and, where a
# picture genuinely helps, drops a fenced ```aid block in it. We lift the block
# out, validate it, and attach it to the message — no second LLM round-trip, and
# it survives token streaming because the fence is closed before the text ends.
# Rail 1 (part a): accept the fences a model actually writes. It is told ```aid,
# but ```json and a bare ``` around an object with a "kind" are near-misses that
# would otherwise be dropped AND leave a code block sitting in the chat.
_FENCE = re.compile(r"```[ \t]*(?:aid|json)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _looks_like_an_aid(payload):
    """Cheap test for whether a code block was MEANT as a diagram. Guards the
    tolerant fence match below: a tutor teaching Python legitimately shows
    ```python blocks, and eating one of those would be far worse than missing a
    diagram."""
    head = payload.lstrip()[:400]
    return ('"kind"' in head or "'kind'" in head) and head[:1] in "{["


def extract_aids(text, default_tier="authored", limit=MAX_AIDS_PER_MESSAGE):
    """Pull diagram fences out of a tutor message.

    Returns (clean_text, aids, errors). `clean_text` is what the learner reads
    and what TTS speaks.

    Two consumption rules, and the distinction matters:
      * An explicit ```aid fence is ALWAYS removed — even when its JSON is
        broken. A wall of braces in the chat is worse than a missing diagram.
      * A ```json or bare fence is removed ONLY once it has parsed into a real
        aid. Otherwise it is left exactly as written, because it is far more
        likely to be a code example the tutor is teaching from.
    """
    if not text or "```" not in text:
        return text, [], []

    aids, errors = [], []

    def _take(match):
        block, payload = match.group(0), match.group(1).strip()
        explicit = block[:20].lower().lstrip("`").strip().startswith("aid")
        if not explicit and not _looks_like_an_aid(payload):
            return block                     # someone else's code block; hands off

        if len(aids) >= limit:
            errors.append(f"more than {limit} aids in one message; extra dropped")
            return "" if explicit else block

        raw, repaired = _repair_json_text(payload)      # Rail 1
        if raw is None:
            errors.append("aid JSON did not parse (even after repair)")
            return "" if explicit else block
        if repaired:
            logger.info("Aid rail: recovered malformed JSON from the model")

        produced = False
        for item in (raw if isinstance(raw, list) else [raw]):
            if len(aids) >= limit:
                break
            aid, err = normalize_aid(item, default_tier=default_tier)
            if aid:
                if repaired:
                    aid.setdefault("rails", []).append("json repaired")
                aids.append(aid)
                produced = True
            else:
                errors.append(err)
        # An implicit fence that produced nothing was probably never an aid.
        return "" if (explicit or produced) else block

    clean = _FENCE.sub(_take, text)
    # Tidy the hole the fence left behind so paragraphs don't gain blank gaps.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    for err in errors:
        logger.info("aid rejected: %s", err)
    return clean, aids, errors


# --- Course-built aids -------------------------------------------------------
# Diagrams drawn during the ASSET GATHERING phase of course creation and stored
# in the concept's markdown, alongside `## Misconceptions` and `## Analogies`,
# which the FSM already parses the same way. No schema migration, and the aids
# travel with the content they belong to.
#
# Why build time is the right place for the hard ones: a retry costs nothing
# there, so geometry can be drawn, validated and regenerated against a named
# failure — exactly how the depth contract already works. Runtime then SELECTS
# rather than authors: no JSON-reliability risk, no latency, no variance.
#
#   ## Visual Aids
#   ```aid
#   {"slot": "opening", "kind": "geometry", ...}
#   ```
#   ```aid
#   {"slot": "misconception:0", "kind": "number_line", ...}
#   ```
_AIDS_SECTION = re.compile(r"##+\s*Visual Aids\s*\n(.*?)(?=\n##\s|\Z)",
                           re.DOTALL | re.IGNORECASE)


def parse_concept_aids(markdown):
    """Return {slot: aid} for a concept's pre-built diagrams.

    Never raises and never partially fails a concept: a malformed block is
    skipped and the rest are kept, because a missing diagram must never cost a
    learner their lesson.
    """
    if not markdown or "Visual Aids" not in markdown:
        return {}
    section = _AIDS_SECTION.search(markdown)
    if not section:
        return {}
    out = {}
    for match in _FENCE.finditer(section.group(1)):
        raw, _ = _repair_json_text(match.group(1).strip())
        if raw is None:
            continue
        for item in (raw if isinstance(raw, list) else [raw]):
            if not isinstance(item, dict):
                continue
            slot = _text(item.get("slot"), 40) or "opening"
            aid, err = normalize_aid(item, default_tier=item.get("tier", "authored"))
            if aid:
                aid["slot"] = slot
                out[slot] = aid
            else:
                logger.info("Pre-built aid in slot '%s' rejected: %s", slot, err)
    return out


def render_concept_aids(aids):
    """Serialise pre-built aids into the `## Visual Aids` markdown section.
    Inverse of parse_concept_aids; used by the asset-gathering phase."""
    if not aids:
        return ""
    lines = ["## Visual Aids", ""]
    for slot, aid in sorted(aids.items()):
        # The WHOLE provenance record round-trips, not just the tier. Writing
        # only the tier silently dropped `source`, `license` and `url` — so a
        # figure lifted from someone's book came back attributed to nobody.
        # Attribution that does not survive persistence is not attribution.
        prov = aid.get("provenance", {}) or {}
        payload = {"slot": slot, "kind": aid["kind"], "title": aid.get("title", ""),
                   "caption": aid.get("caption", ""), "alt": aid.get("alt", ""),
                   "reveal": aid.get("reveal", "tutor"),
                   "tier": prov.get("tier", "authored"),
                   "spec": aid["spec"]}
        for field in ("source", "license", "url"):
            if prov.get(field):
                payload[field] = prov[field]
        lines.append("```aid")
        lines.append(json.dumps(payload, separators=(",", ":")))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


# --- Store -------------------------------------------------------------------
class AidStore:
    """Bounded, content-addressed store of aid specs for one session.

    Bounded because this lives in a long-running FSM: without a cap a chatty
    session grows without limit. LRU eviction, and because ids are content
    hashes, re-emitting the same diagram refreshes it rather than duplicating.

    A miss is normal, not exceptional — an aid can be evicted while its message
    is still on screen. The client handles that by falling back to the alt-text
    it already holds in the descriptor, which is why the descriptor carries it.
    """

    def __init__(self, capacity=64):
        self.capacity = max(1, int(capacity))
        self._items = OrderedDict()

    def put(self, aid):
        aid_key = aid["id"]
        if aid_key in self._items:
            self._items.move_to_end(aid_key)
            self._items[aid_key].update(aid)
        else:
            self._items[aid_key] = dict(aid)
            while len(self._items) > self.capacity:
                evicted, _ = self._items.popitem(last=False)
                logger.debug("aid store evicted %s", evicted)
        return aid_key

    def get(self, aid_key):
        item = self._items.get(aid_key)
        if item is not None:
            self._items.move_to_end(aid_key)
        return item

    def set_stage(self, aid_key, stage):
        """Advance a progressive aid. Clamped to the stages the spec defines, so
        a stray REVEAL_AID can't push an aid past its last layer."""
        item = self.get(aid_key)
        if item is None:
            return None
        item["stage"] = max(0, min(int(stage), item.get("stages_total", 0)))
        return item

    def __len__(self):
        return len(self._items)

    def __contains__(self, aid_key):
        return aid_key in self._items
