"""Presentation MathML to LaTeX, because flattening it produces FALSE mathematics.

THE MEASUREMENT THAT FORCED THIS
--------------------------------
OpenStax serves its books as MathML. Extracting that with `get_text()` — the
approach every generic HTML reader takes, including this repository's — gives:

    3 2 = 9              for   3² = 9
    f ( x ) = x          for   f(x) = √x
    x = 1 3 (y+1) 2      for   x = ⅓(y+1)²

These are not ugly renderings. They are WRONG STATEMENTS. "3 2 = 9" reads as
thirty-two equals nine; the square root disappears without trace; a one-third
becomes a thirteen. A tutor built on this teaches false mathematics with total
confidence, and no structural check downstream would ever notice, because the
text is well-formed prose-shaped output.

The equivalent bug in the computer-science domain destroyed indentation and
took four attempts to fix. This one is worse: bad indentation is visible, and
`3 2 = 9` is not.

WHY LATEX AND NOT UNICODE
-------------------------
KaTeX is already vendored in the web UI and wired into `learn.html` and
`session.js`, so LaTeX renders as mathematics for the learner rather than as
symbols in a sentence. It also survives the round trip through JSON, the model
prompt and back — LaTeX is the notation the model has seen most of during
training, so it both reads and writes it more reliably than Unicode soup.

WHAT IT REFUSES
---------------
An element it does not understand degrades to its text content rather than
raising or silently dropping — a partially-converted expression is recoverable
by a reader, and a missing one is not. `<annotation-xml>` is stripped first:
OpenStax wraps every expression in `<semantics>` carrying BOTH presentation and
content MathML, and extracting both is what produced the doubled
"f(x)=4−2x+5. f(x)=4−2x+5." in the first place.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: Operators whose Unicode form LaTeX spells differently. Anything absent is
#: passed through, which is right for +, -, =, (, ) and friends.
_OPS = {
    "−": "-", "≤": r"\le", "≥": r"\ge", "≠": r"\ne",
    "×": r"\times", "÷": r"\div", "±": r"\pm",
    "≈": r"\approx", "≡": r"\equiv", "∞": r"\infty",
    "→": r"\to", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int",
    "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
    "∂": r"\partial", "∇": r"\nabla", "√": r"\sqrt",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset",
    "∀": r"\forall", "∃": r"\exists", "¬": r"\neg",
    "∧": r"\land", "∨": r"\lor", "⋅": r"\cdot",
    "…": r"\dots", "⋯": r"\cdots", "⋮": r"\vdots",
    "′": "'", "″": "''",
}

#: Greek and common named symbols appearing as <mi>.
_IDENTS = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma",
    "δ": r"\delta", "ε": r"\epsilon", "θ": r"\theta",
    "λ": r"\lambda", "μ": r"\mu", "π": r"\pi",
    "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau",
    "φ": r"\phi", "ω": r"\omega",
    "Δ": r"\Delta", "Σ": r"\Sigma", "Ω": r"\Omega",
    "Γ": r"\Gamma", "Φ": r"\Phi", "Π": r"\Pi",
}

#: Multi-letter identifiers that are FUNCTION NAMES, not products of variables.
#: Without this, `sin` becomes s·i·n in italics, which is a different
#: expression entirely.
_FUNCS = {
    "sin", "cos", "tan", "sec", "csc", "cot", "sinh", "cosh", "tanh",
    "arcsin", "arccos", "arctan", "log", "ln", "exp", "lim", "max", "min",
    "det", "dim", "ker", "deg", "gcd", "lcm", "sup", "inf", "mod",
}

_WS = re.compile(r"\s+")


def _clean(text):
    return _WS.sub(" ", (text or "")).strip()


def _cmd(latex):
    """A LaTeX token, with the separator a control word needs.

    `\\Delta` followed directly by `y` is the control word `\\Deltay`, which
    is undefined — KaTeX renders nothing at all. Every backslash command that
    can be followed by a letter needs the space; a bare symbol must not have
    one, or `2x-2` becomes `2x- 2`.
    """
    return latex + " " if latex.startswith("\\") else latex


def _wrap(latex):
    """Brace a sub-expression unless it is already a single token.

    Primes are left bare: f^{'} is correct LaTeX but f' is what a reader and a
    model both expect, and this string goes into prompts as well as KaTeX.

    `x^{2}` and `x^2` are the same; `x^{n+1}` and `x^n+1` are not. Bracing
    anything longer than one character is the cheap way to never get that
    wrong.
    """
    latex = latex.strip()
    if set(latex) == {"'"} and latex:
        return latex
    if len(latex) == 1 or (len(latex) == 2 and latex.startswith("\\")):
        return latex
    return "{" + latex + "}"


def _children(node):
    return [c for c in getattr(node, "children", [])
            if getattr(c, "name", None)]


def _convert(node):
    """One MathML node to LaTeX. Never raises."""
    name = (getattr(node, "name", None) or "").lower()

    if name in ("semantics", "mrow", "mstyle", "mpadded", "math", "mphantom"):
        return "".join(_convert(c) for c in _children(node))

    if name == "mi":
        t = _clean(node.get_text())
        if t in _IDENTS:
            return _cmd(_IDENTS[t])
        if t.lower() in _FUNCS:
            return "\\" + t.lower() + " "
        return t

    if name == "mn":
        return _clean(node.get_text())

    if name == "mo":
        t = _clean(node.get_text())
        if t in _OPS:
            out = _OPS[t]
            # Only a BACKSLASH COMMAND needs a trailing space, to stop
            # \times2 being read as a control word named "times2". Adding it
            # unconditionally turned the minus in 2x-2 into "2x- 2" and, worse,
            # made the prime in f' arrive as "' " — two characters, which then
            # got braced into f^{'}.
            return _cmd(out)
        return t

    if name == "mtext":
        t = _clean(node.get_text())
        if not t:
            return ""
        # OpenStax puts operator names and Greek letters in <mtext> as often as
        # in <mi>. Wrapping those in \text{} is not merely ugly: \text{lim}
        # loses the operator spacing and limit placement that make it read as a
        # limit, and \text{Δ} is a word where \Delta is a symbol.
        if t.lower() in _FUNCS:
            return "\\" + t.lower() + " "
        if t in _IDENTS:
            return _cmd(_IDENTS[t])
        return r"\text{" + t + "}"

    if name == "mspace":
        return " "

    if name == "mfrac":
        kids = _children(node)
        if len(kids) == 2:
            return (r"\frac{" + _convert(kids[0]) + "}{"
                    + _convert(kids[1]) + "}")

    if name == "msqrt":
        return r"\sqrt{" + "".join(_convert(c) for c in _children(node)) + "}"

    if name == "mroot":
        kids = _children(node)
        if len(kids) == 2:
            return (r"\sqrt[" + _convert(kids[1]) + "]{"
                    + _convert(kids[0]) + "}")

    if name == "msup":
        kids = _children(node)
        if len(kids) == 2:
            return _convert(kids[0]) + "^" + _wrap(_convert(kids[1]))

    if name == "msub":
        kids = _children(node)
        if len(kids) == 2:
            return _convert(kids[0]) + "_" + _wrap(_convert(kids[1]))

    if name == "msubsup":
        kids = _children(node)
        if len(kids) == 3:
            return (_convert(kids[0]) + "_" + _wrap(_convert(kids[1]))
                    + "^" + _wrap(_convert(kids[2])))

    if name in ("munder", "mover", "munderover"):
        kids = _children(node)
        # \sum_{i=1}^{n} — the limits of a big operator are subscripts in
        # LaTeX even though MathML stacks them.
        if name == "munderover" and len(kids) == 3:
            return (_convert(kids[0]) + "_" + _wrap(_convert(kids[1]))
                    + "^" + _wrap(_convert(kids[2])))
        if len(kids) == 2:
            joiner = "_" if name == "munder" else "^"
            base = _convert(kids[0])
            # An overbar or hat is a decoration, not an exponent.
            deco = _clean(kids[1].get_text())
            if name == "mover" and deco in ("¯", "―", "_"):
                return r"\overline{" + base + "}"
            if name == "mover" and deco == "^":
                return r"\hat{" + base + "}"
            return base + joiner + _wrap(_convert(kids[1]))

    if name == "mfenced":
        inner = ", ".join(_convert(c) for c in _children(node))
        return (node.get("open", "(") or "(") + inner + (node.get("close", ")") or ")")

    if name in ("mtable", "mtr", "mtd"):
        if name == "mtd":
            return "".join(_convert(c) for c in _children(node))
        if name == "mtr":
            return " & ".join(_convert(c) for c in _children(node))
        rows = [_convert(r) for r in _children(node)]
        return (r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}")

    # Unknown element: keep its text rather than lose the expression.
    return _clean(node.get_text())


def to_latex(math_node):
    """A `<math>` element as LaTeX, or "" if nothing usable. Never raises."""
    try:
        out = _clean(_convert(math_node))
        # Collapse the spacing the operator table adds before delimiters.
        out = re.sub(r"\s+([,)\]}])", r"\1", out)
        out = re.sub(r"([(\[{])\s+", r"\1", out)
        return out
    except Exception as e:                # pragma: no cover - defensive
        logger.debug(f"[MATH] MathML conversion failed: {e}")
        try:
            return _clean(math_node.get_text())
        except Exception:
            return ""


def replace_math(soup, delimiter="$"):
    """Replace every `<math>` in `soup` with its LaTeX, in place.

    Strips `<annotation-xml>`/`<annotation>` FIRST. OpenStax wraps every
    expression in `<semantics>` carrying both presentation and content MathML,
    and reading both is what produced the doubled expressions that started
    this. Returns the number of expressions converted.
    """
    n = 0
    try:
        for tag in soup.find_all(["annotation-xml", "annotation"]):
            tag.decompose()
        for m in soup.find_all("math"):
            latex = to_latex(m)
            m.replace_with(f"{delimiter}{latex}{delimiter}" if latex else "")
            n += 1
    except Exception as e:                # pragma: no cover - defensive
        logger.warning(f"[MATH] replace_math failed after {n}: {e}")
    return n
