"""LaTeX to speech — so the TTS path never meets a formula.

THE PROBLEM
-----------
KaTeX renders `\\frac{a}{b}` beautifully and cannot say it. The TTS path and the
text-only path both receive raw LaTeX today, and a speech engine handed
`\\frac{a}{b}` reads backslashes and braces or silently drops them. A tutor that
teaches mathematics out loud has to be able to pronounce it.

WHY NOT THE MathJax SPEECH RULE ENGINE
--------------------------------------
SRE is the mature answer and produces better speech than this does. It is also
Node, and wiring a Node runtime into a Python service for one string conversion
buys a whole dependency — a runtime, a package tree, and a subprocess on the
hydration path — for a job that is a few hundred lines of substitution.

This is a deterministic ClearSpeak-flavoured converter in the language the rest
of the pipeline is written in. It handles what generated course content actually
contains. If it ever meets material it cannot pronounce it says so (see
`unspoken`) rather than guessing, and SRE remains the upgrade path with a
`concept_math` schema already shaped for it.

WHEN IT RUNS
------------
At HYDRATION, once, offline — never in a session. The speech string is stored
next to the LaTeX, so a tutoring turn reads a field instead of parsing markup on
the critical path of a reply that already costs ~30 s of inference.

THE FAILURE THAT LOOKS LIKE SUCCESS
-----------------------------------
A converter that silently passes LaTeX through returns a non-empty string and
passes every "did it produce output" check, while being useless to a listener.
`unspoken()` exists for exactly that: it asserts no control sequences survived,
and it is the thing to test rather than truthiness.
"""

import re

# Order matters: longer commands first, so \leftarrow is not matched by \left.
_SYMBOLS = [
    (r"\\leftrightarrow", " if and only if "), (r"\\Leftrightarrow", " if and only if "),
    (r"\\rightarrow", " approaches "), (r"\\Rightarrow", " implies "),
    (r"\\leftarrow", " from "), (r"\\to", " to "),
    (r"\\leq", " is less than or equal to "), (r"\\le\b", " is less than or equal to "),
    (r"\\geq", " is greater than or equal to "), (r"\\ge\b", " is greater than or equal to "),
    (r"\\neq", " is not equal to "), (r"\\ne\b", " is not equal to "),
    (r"\\approx", " is approximately "), (r"\\equiv", " is equivalent to "),
    (r"\\times", " times "), (r"\\cdot", " times "), (r"\\div", " divided by "),
    (r"\\pm", " plus or minus "), (r"\\mp", " minus or plus "),
    (r"\\infty", " infinity "), (r"\\partial", " partial "),
    (r"\\forall", " for all "), (r"\\exists", " there exists "),
    (r"\\in\b", " in "), (r"\\notin", " not in "),
    (r"\\subseteq", " is a subset of "), (r"\\subset", " is a subset of "),
    (r"\\cup", " union "), (r"\\cap", " intersect "),
    (r"\\emptyset", " the empty set "), (r"\\varnothing", " the empty set "),
    (r"\\ldots", " and so on "), (r"\\dots", " and so on "), (r"\\cdots", " and so on "),
    (r"\\alpha", " alpha "), (r"\\beta", " beta "), (r"\\gamma", " gamma "),
    (r"\\delta", " delta "), (r"\\epsilon", " epsilon "), (r"\\varepsilon", " epsilon "),
    (r"\\theta", " theta "), (r"\\lambda", " lambda "), (r"\\mu", " mu "),
    (r"\\pi", " pi "), (r"\\rho", " rho "), (r"\\sigma", " sigma "),
    (r"\\tau", " tau "), (r"\\phi", " phi "), (r"\\omega", " omega "),
    (r"\\Delta", " delta "), (r"\\Sigma", " sigma "), (r"\\Omega", " omega "),
    (r"\\Gamma", " gamma "), (r"\\Lambda", " lambda "), (r"\\Phi", " phi "),
    (r"\\sin", " sine "), (r"\\cos", " cosine "), (r"\\tan", " tangent "),
    (r"\\log", " log "), (r"\\ln", " natural log "), (r"\\exp", " exp "),
    (r"\\lim", " the limit "), (r"\\max", " the maximum "), (r"\\min", " the minimum "),
    (r"\\det", " the determinant "), (r"\\dim", " the dimension "),
    (r"\\quad", " "), (r"\\qquad", " "), (r"\\,", " "), (r"\\;", " "), (r"\\!", ""),
    (r"\\left", ""), (r"\\right", ""), (r"\\displaystyle", ""), (r"\\text", ""),
    (r"\\mathbb", ""), (r"\\mathbf", ""), (r"\\mathrm", ""), (r"\\mathcal", ""),
]

_BRACED = r"\{([^{}]*)\}"

_ORDINAL = {"2": "square", "3": "cube", "4": "fourth", "5": "fifth",
            "6": "sixth", "n": "nth"}


def _strip_braces(s):
    # Innermost-first so nested groups collapse without a parser.
    prev = None
    while prev != s:
        prev = s
        s = re.sub(_BRACED, r" \1 ", s)
    return s


def speak(latex):
    """A ClearSpeak-flavoured spoken form of `latex`. '' for empty input.

    "a over b", not "StartFraction a Over b EndFraction" — ClearSpeak rather
    than MathSpeak, because a tutor speaking naturally is easier to follow than
    one speaking unambiguously, and ambiguity is recoverable here by looking at
    the rendered formula on screen.
    """
    if not latex or not latex.strip():
        return ""
    s = latex.strip()

    # Structures that need their arguments reordered, applied repeatedly so
    # nesting resolves from the inside out.
    for _ in range(6):
        before = s
        s = re.sub(r"\\d?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r" \1 over \2 ", s)
        # Ordinal roots read as words: "the cube root of 8", not "the 3 root".
        s = re.sub(r"\\sqrt\s*\[\s*([^\]]+)\s*\]\s*\{([^{}]+)\}",
                   lambda m: f" the {_ORDINAL.get(m.group(1).strip(), m.group(1).strip() + 'th')} root of {m.group(2)} ", s)
        s = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r" the square root of \1 ", s)
        s = re.sub(r"\\(sum|prod|int)\s*_\s*\{([^{}]+)\}\s*\^\s*\{([^{}]+)\}",
                   lambda m: (f" the {'sum' if m.group(1)=='sum' else 'product' if m.group(1)=='prod' else 'integral'}"
                              f" from {m.group(2)} to {m.group(3)} of "), s)
        # \lim_{x \to 0} is "the limit as x approaches 0" — the subscript is a
        # condition, not an index, so the generic "sub" rule reads it wrongly.
        s = re.sub(r"\\lim\s*_\s*\{([^{}]+?)\s*\\to\s*([^{}]+)\}",
                   r" the limit as \1 approaches \2 ", s)
        s = re.sub(r"\\(sum|prod|int)\b",
                   lambda m: (" the sum of " if m.group(1) == "sum"
                              else " the product of " if m.group(1) == "prod"
                              else " the integral of "), s)
        if s == before:
            break

    # Superscripts before subscripts: x^2 is "squared", x_1 is "sub 1".
    s = re.sub(r"\^\s*\{?\s*2\s*\}?(?![\w])", " squared ", s)
    s = re.sub(r"\^\s*\{?\s*3\s*\}?(?![\w])", " cubed ", s)
    s = re.sub(r"\^\s*\{([^{}]+)\}", r" to the power of \1 ", s)
    s = re.sub(r"\^\s*(\w)", r" to the power of \1 ", s)
    s = re.sub(r"_\s*\{([^{}]+)\}", r" sub \1 ", s)
    s = re.sub(r"_\s*(\w)", r" sub \1 ", s)

    for pat, word in _SYMBOLS:
        s = re.sub(pat, word, s)

    s = _strip_braces(s)
    s = (s.replace("=", " equals ").replace("+", " plus ")
          .replace("<", " is less than ").replace(">", " is greater than ")
          .replace("(", " open paren ").replace(")", " close paren ")
          .replace("[", " ").replace("]", " ").replace("$", " "))
    # Minus only between operands; a leading hyphen is "negative".
    s = re.sub(r"(?<=[\w\s])-(?=[\w\s])", " minus ", s)
    s = re.sub(r"(?<![\w])-(?=\d)", " negative ", s)
    # "minus b plus or minus ..." at the very start is a negation, not a
    # subtraction with a missing left operand.
    s = re.sub(r"^\s*minus\b", "negative", s)
    return re.sub(r"\s+", " ", s).strip()


def unspoken(text):
    """LaTeX control sequences that survived. Empty list means fully spoken.

    THE TEST THAT MATTERS. A converter that passes markup through returns a
    non-empty string and satisfies every "did it produce output" check while
    being unusable to a listener.
    """
    return sorted(set(re.findall(r"\\[A-Za-z]+", text or "")))


def extract(markdown):
    """Every math span in a document, in order, deduplicated.

    Handles $$...$$ before $...$ so a display block is not shredded into two
    inline spans, and \\(...\\) / \\[...\\] which the generator also emits.
    """
    if not markdown:
        return []
    found, seen = [], set()
    for pat in (r"\$\$(.+?)\$\$", r"\\\[(.+?)\\\]",
                r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", r"\\\((.+?)\\\)"):
        for m in re.findall(pat, markdown, re.DOTALL):
            t = m.strip()
            if t and t not in seen and len(t) < 600:
                seen.add(t)
                found.append(t)
    return found


def speech_for(markdown):
    """[(latex, speech, unspoken)] for a document. Never raises."""
    out = []
    for latex in extract(markdown):
        try:
            spoken = speak(latex)
        except Exception:
            spoken = ""
        out.append((latex, spoken, unspoken(spoken)))
    return out
