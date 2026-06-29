"""MCP-style tool + data layer for the tutor.

Lets the local model CALL a curated set of teaching tools and PULL data at key
tutoring moments. This is NOT a separate MCP server — it uses Ollama's native
tool-calling, but is MCP-aligned in shape (tools ≈ MCP tools, data pulls ≈ MCP
resources), so it can be exposed over MCP later without changing the tools.

Failsafes & guardrails scale to the configured model's CAPABILITY TIER: a weaker
model gets fewer tools, stricter validation, fewer calls per turn, and smaller
outputs. The executor never raises — every failure returns a structured error so
a bad tool call can't crash a tutoring turn.

Pedagogy rule (enforced by how callers use results, not here): tool output feeds
the tutor's reasoning / hint / verification — it is NOT handed to the student as
the answer.
"""
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# --- Model capability tiers --------------------------------------------------
# 1 = small (≤4B), 2 = medium (7-15B incl. qwen3.5:9b / qwen3:14b), 3 = large (≥24B).
def model_tier(model_name: str) -> int:
    n = (model_name or "").lower()
    # MoE models with a small active-param count but large total (e.g. 35b-a3b)
    # are strong — treat as large.
    if re.search(r'a\d+b', n) or "-moe" in n:
        return 3
    # Extract every "<number>b" parameter count, boundary-aware so 14b != 4b.
    sizes = [float(x) for x in re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*b\b', n)]
    if not sizes:
        return 2  # unknown -> mid
    biggest = max(sizes)
    if biggest >= 24:
        return 3
    if biggest <= 4:
        return 1
    return 2


# Per-tier policy — latitude vs. failsafe strength. Stronger model → more tools,
# more calls, longer budgets; weaker model → locked down.
TIER_POLICY = {
    1: {"max_tool_calls": 1, "max_output_chars": 1200, "max_safety": 1, "timeout_s": 5, "max_rounds": 1},
    2: {"max_tool_calls": 3, "max_output_chars": 4000, "max_safety": 2, "timeout_s": 8, "max_rounds": 3},
    3: {"max_tool_calls": 5, "max_output_chars": 8000, "max_safety": 3, "timeout_s": 12, "max_rounds": 5},
}


def policy_for(model_name: str) -> dict:
    return TIER_POLICY[model_tier(model_name)]


# --- Tool definition ---------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                      # JSON Schema for the function arguments
    func: Callable[..., Any]
    safety: int = 1                       # 1 pure compute/read · 2 makes artifacts · 3 sensitive
    min_tier: int = 1                     # smallest model tier permitted to use it
    category: str = "general"


# --- Failsafe helpers --------------------------------------------------------
class _Timeout(Exception):
    pass


def _run_with_timeout(func, kwargs, timeout_s):
    """Run func(**kwargs) in a worker thread; raise _Timeout if it overruns.
    (The thread can't be force-killed, but the turn proceeds — a pure-compute
    tool that hangs won't block tutoring.)"""
    box = {}

    def worker():
        try:
            box["result"] = func(**kwargs)
        except Exception as e:  # capture to re-raise on the calling thread
            box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise _Timeout()
    if "error" in box:
        raise box["error"]
    return box.get("result")


_JSON_TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def validate_args(schema: dict, args: dict) -> Optional[str]:
    """Light JSON-Schema validation (required keys, types, string length cap).
    Returns an error string, or None if valid."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = (schema or {}).get("properties", {})
    for req in (schema or {}).get("required", []):
        if req not in args:
            return f"missing required '{req}'"
    for key, val in args.items():
        spec = props.get(key)
        if not spec:
            continue  # unknown keys ignored (forward-compatible)
        expected = _JSON_TYPES.get(spec.get("type"))
        if expected and not isinstance(val, expected):
            # allow ints where numbers are expected
            if not (spec.get("type") == "number" and isinstance(val, (int, float))):
                return f"'{key}' should be {spec.get('type')}"
        if isinstance(val, str) and len(val) > spec.get("maxLength", 2000):
            return f"'{key}' too long"
    return None


# --- Registry + executor -----------------------------------------------------
class ToolRegistry:
    def __init__(self):
        self._tools: dict = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def available_for(self, model_name: str):
        """Tools this model's tier is allowed to use (tier + safety gated)."""
        tier = model_tier(model_name)
        max_safety = TIER_POLICY[tier]["max_safety"]
        return [t for t in self._tools.values() if t.min_tier <= tier and t.safety <= max_safety]

    def to_ollama_tools(self, model_name: str):
        """OpenAI/Ollama function-calling schema for the allowed tools."""
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self.available_for(model_name)
        ]

    def execute(self, model_name: str, name: str, args: dict) -> dict:
        """Run a tool with all failsafes. ALWAYS returns {ok, result|error} —
        never raises, so a bad model tool-call can't crash the tutoring turn."""
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool '{name}'"}
        tier = model_tier(model_name)
        pol = TIER_POLICY[tier]
        if tool.min_tier > tier or tool.safety > pol["max_safety"]:
            return {"ok": False, "error": f"tool '{name}' not permitted for this model tier"}
        args = args or {}
        err = validate_args(tool.parameters, args)
        if err:
            return {"ok": False, "error": f"invalid arguments for '{name}': {err}"}
        try:
            result = _run_with_timeout(tool.func, args, pol["timeout_s"])
        except _Timeout:
            return {"ok": False, "error": f"tool '{name}' timed out"}
        except Exception as e:
            logger.warning(f"tool '{name}' failed: {e}")
            return {"ok": False, "error": f"tool '{name}' error: {e}"}
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        if len(text) > pol["max_output_chars"]:
            text = text[:pol["max_output_chars"]] + "…[truncated]"
        return {"ok": True, "result": text}


# --- Built-in tools (all guarded: optional deps imported lazily) -------------
def _t_math_check(expression: str, claimed: str = ""):
    """SymPy: simplify an expression; if `claimed` given, test equivalence."""
    from sympy import simplify, sympify, Eq  # lazy
    expr = sympify(str(expression)[:500], evaluate=True)
    out = {"simplified": str(simplify(expr))}
    if claimed:
        try:
            same = bool(simplify(expr - sympify(str(claimed)[:500])) == 0)
        except Exception:
            same = False
        out["equals_claimed"] = same
    return out


def _t_solve_equation(equation: str, variable: str = "x"):
    from sympy import symbols, Eq, solve, sympify
    eq = str(equation)[:500]
    var = symbols(str(variable)[:20])
    if "=" in eq:
        lhs, rhs = eq.split("=", 1)
        expr = Eq(sympify(lhs), sympify(rhs))
    else:
        expr = sympify(eq)
    return {"solutions": [str(s) for s in solve(expr, var)]}


def _t_differentiate(expression: str, variable: str = "x"):
    from sympy import diff, symbols, sympify
    return {"derivative": str(diff(sympify(str(expression)[:500]), symbols(str(variable)[:20])))}


def _t_integrate(expression: str, variable: str = "x"):
    from sympy import integrate, symbols, sympify
    return {"integral": str(integrate(sympify(str(expression)[:500]), symbols(str(variable)[:20])))}


def _t_convert_units(value: float, from_unit: str, to_unit: str):
    from pint import UnitRegistry  # guarded — pint may be absent
    ureg = UnitRegistry()
    q = (float(value) * ureg(str(from_unit)[:30])).to(str(to_unit)[:30])
    return {"value": round(q.magnitude, 6), "unit": str(q.units)}


def _t_plot_function(expression: str, variable: str = "x", start: float = -10, end: float = 10):
    """Matplotlib: plot a function over a range → PNG data URI (rendered in chat)."""
    import base64
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from sympy import lambdify, symbols, sympify
    var = symbols(str(variable)[:20])
    f = lambdify(var, sympify(str(expression)[:500]), "numpy")
    xs = np.linspace(float(start), float(end), 400)
    with np.errstate(all="ignore"):
        ys = f(xs)
    fig, ax = plt.subplots(figsize=(4, 3), dpi=90)
    ax.plot(xs, ys)
    ax.axhline(0, color="#999", lw=0.6)
    ax.axvline(0, color="#999", lw=0.6)
    ax.set_title(f"{expression}")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    # Return the markdown the chat already renders (B13.3) so the tutor can show it.
    return {"image_markdown": f"![plot of {expression}]({uri})"}


def _t_evaluate_numeric(expression: str):
    from sympy import N, sympify
    return {"value": str(N(sympify(str(expression)[:500])))}


def _t_factor(expression: str):
    from sympy import factor, sympify
    return {"factored": str(factor(sympify(str(expression)[:500])))}


def _t_expand(expression: str):
    from sympy import expand, sympify
    return {"expanded": str(expand(sympify(str(expression)[:500])))}


def _t_compute_limit(expression: str, variable: str = "x", point: str = "0"):
    from sympy import limit, symbols, sympify
    return {"limit": str(limit(sympify(str(expression)[:500]), symbols(str(variable)[:20]), sympify(str(point)[:50])))}


def _t_linear_algebra(matrix, operation: str = "determinant"):
    from sympy import Matrix
    m = Matrix(matrix)
    op = str(operation).lower()
    if op.startswith("det"):
        return {"determinant": str(m.det())}
    if op.startswith("inv"):
        return {"inverse": str(m.inv().tolist())}
    if op.startswith("eig"):
        return {"eigenvalues": [str(k) for k in m.eigenvals().keys()]}
    if op == "rref":
        return {"rref": str(m.rref()[0].tolist())}
    return {"error": f"unknown operation '{operation}'"}


def _t_descriptive_statistics(numbers):
    import statistics as st
    xs = [float(x) for x in numbers]
    if not xs:
        return {"error": "no numbers"}
    out = {"n": len(xs), "mean": round(st.mean(xs), 6), "median": st.median(xs),
           "min": min(xs), "max": max(xs)}
    if len(xs) > 1:
        out["stdev"] = round(st.stdev(xs), 6)
        out["variance"] = round(st.variance(xs), 6)
    return out


def _t_linear_regression(x, y):
    from scipy import stats
    r = stats.linregress([float(v) for v in x], [float(v) for v in y])
    return {"slope": round(r.slope, 6), "intercept": round(r.intercept, 6),
            "r_squared": round(r.rvalue ** 2, 6)}


def _t_physical_constant(name: str):
    from scipy import constants
    key = str(name).strip()
    if hasattr(constants, key):
        return {"name": key, "value": getattr(constants, key)}
    try:
        val, unit, _ = constants.physical_constants[key]
        return {"name": key, "value": val, "unit": unit}
    except KeyError:
        # fuzzy: list close matches
        matches = [k for k in constants.physical_constants if key.lower() in k.lower()][:5]
        return {"error": f"unknown constant '{name}'", "did_you_mean": matches}


def _t_periodic_element(query: str):
    import periodictable  # guarded — not always installed
    el = getattr(periodictable, str(query).strip(), None) or periodictable.elements.symbol(str(query).strip())
    return {"symbol": el.symbol, "name": el.name, "number": el.number, "mass": el.mass}


def _t_plot_points(x, y, kind: str = "scatter", title: str = ""):
    import base64, io, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    xs = [float(v) for v in x]
    ys = [float(v) for v in y]
    fig, ax = plt.subplots(figsize=(4, 3), dpi=90)
    k = str(kind).lower()
    (ax.bar if k == "bar" else ax.plot if k == "line" else ax.scatter)(xs, ys)
    if title:
        ax.set_title(str(title)[:80])
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"image_markdown": f"![{kind} plot]({uri})"}


def _t_draw_graph(edges, directed: bool = False):
    import base64, io, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    G = nx.DiGraph() if directed else nx.Graph()
    for e in edges:
        G.add_edge(str(e[0]), str(e[1]))
    fig, ax = plt.subplots(figsize=(4, 3), dpi=90)
    nx.draw(G, ax=ax, with_labels=True, node_color="#cfe3ee", edge_color="#888", node_size=600, font_size=9)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"image_markdown": f"![graph]({uri})", "nodes": G.number_of_nodes(), "edges": G.number_of_edges()}


def _t_regex_test(pattern: str, test_string: str):
    import re as _re
    rx = _re.compile(str(pattern)[:200])
    matches = rx.findall(str(test_string)[:1000])
    return {"matches": matches[:50], "match_count": len(matches),
            "fullmatch": bool(rx.fullmatch(str(test_string)[:1000]))}


def _t_analyze_sentence(text: str):
    import spacy  # guarded — also needs the en_core_web_sm model
    try:
        nlp = spacy.load("en_core_web_sm")
    except Exception:
        raise RuntimeError("spaCy English model not installed (python -m spacy download en_core_web_sm)")
    doc = nlp(str(text)[:500])
    return {"tokens": [{"text": t.text, "pos": t.pos_, "dep": t.dep_, "lemma": t.lemma_} for t in doc][:60]}


def _t_readability(text: str):
    import textstat  # guarded — not always installed
    t = str(text)[:5000]
    return {"flesch_reading_ease": textstat.flesch_reading_ease(t),
            "grade_level": textstat.text_standard(t, float_output=False),
            "sentences": textstat.sentence_count(t), "words": textstat.lexicon_count(t)}


def _t_run_python(code: str):
    """SANDBOXED-ish code execution. OFF by default (HELGA_ENABLE_CODE_TOOL=1),
    tier-3 + safety-3 only. Runs in an isolated subprocess with a timeout. NOTE:
    `-I` isolation is NOT a real sandbox — production should use a container/nsjail.
    This is the most safeguarded tool: disabled unless explicitly opted in."""
    import os as _os
    import subprocess
    import sys as _sys
    if str(_os.environ.get("HELGA_ENABLE_CODE_TOOL", "")).lower() not in ("1", "true", "yes"):
        raise RuntimeError("code execution is disabled (set HELGA_ENABLE_CODE_TOOL=1 to allow)")
    proc = subprocess.run([_sys.executable, "-I", "-c", str(code)[:4000]],
                          capture_output=True, text=True, timeout=5)
    return {"stdout": proc.stdout[:2000], "stderr": proc.stderr[:1000], "returncode": proc.returncode}


def _num_param(desc):
    return {"type": "number", "description": desc}


def _str_param(desc, max_len=500):
    return {"type": "string", "description": desc, "maxLength": max_len}


def build_default_registry(search_fn: Callable = None,
                           content_fn: Callable = None,
                           mastery_fn: Callable = None,
                           wiki_fn: Callable = None,
                           enable_code: Optional[bool] = None) -> ToolRegistry:
    """Build a registry with the safe compute tools, plus DATA-PULL tools bound to
    the caller's storage/RAG via injected callbacks (omit a callback to disable
    that data tool). `search_fn(query, course_uid)->list`, `content_fn(course_uid,
    concept_uid)->str`, `mastery_fn(concept_uid)->dict`, `wiki_fn(title)->dict`.
    `enable_code` registers the sandboxed run_python tool (tier-3/safety-3); defaults
    to the HELGA_ENABLE_CODE_TOOL env flag (off)."""
    import os
    if enable_code is None:
        enable_code = str(os.environ.get("HELGA_ENABLE_CODE_TOOL", "")).lower() in ("1", "true", "yes")
    r = ToolRegistry()

    # Compute tools (pure, safety=1) — tier 1+ may use math_check; solve/calculus tier 2+.
    r.register(Tool("math_check", "Simplify a math expression and optionally check if it equals a claimed result. Use to VERIFY a student's algebra before hinting — do not reveal the answer.",
                    {"type": "object", "properties": {"expression": _str_param("a math expression"), "claimed": _str_param("optional claimed result to check", 500)}, "required": ["expression"]},
                    _t_math_check, safety=1, min_tier=1, category="math"))
    r.register(Tool("solve_equation", "Solve an equation for a variable (SymPy). Use to check correctness internally.",
                    {"type": "object", "properties": {"equation": _str_param("equation, e.g. x**2-4=0"), "variable": _str_param("variable", 20)}, "required": ["equation"]},
                    _t_solve_equation, safety=1, min_tier=2, category="math"))
    r.register(Tool("differentiate", "Differentiate an expression w.r.t. a variable.",
                    {"type": "object", "properties": {"expression": _str_param("expression"), "variable": _str_param("variable", 20)}, "required": ["expression"]},
                    _t_differentiate, safety=1, min_tier=2, category="math"))
    r.register(Tool("integrate", "Integrate an expression w.r.t. a variable.",
                    {"type": "object", "properties": {"expression": _str_param("expression"), "variable": _str_param("variable", 20)}, "required": ["expression"]},
                    _t_integrate, safety=1, min_tier=2, category="math"))
    r.register(Tool("convert_units", "Convert a value between units (dimensional analysis).",
                    {"type": "object", "properties": {"value": _num_param("numeric value"), "from_unit": _str_param("source unit", 30), "to_unit": _str_param("target unit", 30)}, "required": ["value", "from_unit", "to_unit"]},
                    _t_convert_units, safety=1, min_tier=2, category="physics"))
    # Artifact tool (safety=2) — generates an image; tier 2+.
    r.register(Tool("plot_function", "Plot a function over a range and return an image to show the student a graph to reason about.",
                    {"type": "object", "properties": {"expression": _str_param("function of the variable"), "variable": _str_param("variable", 20), "start": _num_param("range start"), "end": _num_param("range end")}, "required": ["expression"]},
                    _t_plot_function, safety=2, min_tier=2, category="viz"))

    # More math (SymPy)
    r.register(Tool("evaluate_numeric", "Evaluate a math expression to a numeric value.",
                    {"type": "object", "properties": {"expression": _str_param("expression")}, "required": ["expression"]},
                    _t_evaluate_numeric, safety=1, min_tier=1, category="math"))
    r.register(Tool("factor_expression", "Factor a polynomial / algebraic expression.",
                    {"type": "object", "properties": {"expression": _str_param("expression")}, "required": ["expression"]},
                    _t_factor, safety=1, min_tier=2, category="math"))
    r.register(Tool("expand_expression", "Expand an algebraic expression.",
                    {"type": "object", "properties": {"expression": _str_param("expression")}, "required": ["expression"]},
                    _t_expand, safety=1, min_tier=2, category="math"))
    r.register(Tool("compute_limit", "Compute the limit of an expression as a variable approaches a point (e.g. 0, oo).",
                    {"type": "object", "properties": {"expression": _str_param("expression"), "variable": _str_param("variable", 20), "point": _str_param("point", 50)}, "required": ["expression"]},
                    _t_compute_limit, safety=1, min_tier=2, category="math"))
    r.register(Tool("linear_algebra", "Matrix op (determinant/inverse/eigenvalues/rref) on a matrix given as a list of row lists.",
                    {"type": "object", "properties": {"matrix": {"type": "array", "description": "list of rows"}, "operation": _str_param("determinant|inverse|eigenvalues|rref", 20)}, "required": ["matrix"]},
                    _t_linear_algebra, safety=1, min_tier=2, category="math"))
    # Statistics
    r.register(Tool("descriptive_statistics", "Mean/median/stdev/variance/min/max of a list of numbers.",
                    {"type": "object", "properties": {"numbers": {"type": "array", "description": "list of numbers"}}, "required": ["numbers"]},
                    _t_descriptive_statistics, safety=1, min_tier=1, category="stats"))
    r.register(Tool("linear_regression", "Least-squares line fit of (x, y) data: slope, intercept, r^2.",
                    {"type": "object", "properties": {"x": {"type": "array"}, "y": {"type": "array"}}, "required": ["x", "y"]},
                    _t_linear_regression, safety=1, min_tier=2, category="stats"))
    # Science
    r.register(Tool("physical_constant", "Look up a physical constant (e.g. 'c', 'Planck constant', 'g').",
                    {"type": "object", "properties": {"name": _str_param("constant name or symbol", 60)}, "required": ["name"]},
                    _t_physical_constant, safety=1, min_tier=1, category="physics"))
    r.register(Tool("periodic_element", "Look up a chemical element's properties by symbol or name.",
                    {"type": "object", "properties": {"query": _str_param("element symbol or name", 30)}, "required": ["query"]},
                    _t_periodic_element, safety=1, min_tier=1, category="chemistry"))
    # More visualization
    r.register(Tool("plot_points", "Plot (x, y) data as a scatter/line/bar chart image for the student to interpret.",
                    {"type": "object", "properties": {"x": {"type": "array"}, "y": {"type": "array"}, "kind": _str_param("scatter|line|bar", 10), "title": _str_param("title", 80)}, "required": ["x", "y"]},
                    _t_plot_points, safety=2, min_tier=2, category="viz"))
    r.register(Tool("draw_graph", "Draw a graph/network from a list of [from, to] edges as an image.",
                    {"type": "object", "properties": {"edges": {"type": "array", "description": "list of [a, b] pairs"}, "directed": {"type": "boolean"}}, "required": ["edges"]},
                    _t_draw_graph, safety=2, min_tier=2, category="viz"))
    # CS / language
    r.register(Tool("regex_test", "Test a regular expression against a string and return the matches.",
                    {"type": "object", "properties": {"pattern": _str_param("regex", 200), "test_string": _str_param("string to test", 1000)}, "required": ["pattern", "test_string"]},
                    _t_regex_test, safety=1, min_tier=2, category="cs"))
    r.register(Tool("analyze_sentence", "Parse a sentence (POS, dependencies, lemmas) for grammar/linguistics teaching.",
                    {"type": "object", "properties": {"text": _str_param("a sentence", 500)}, "required": ["text"]},
                    _t_analyze_sentence, safety=1, min_tier=2, category="language"))
    r.register(Tool("readability", "Readability / grade-level metrics for a passage of text.",
                    {"type": "object", "properties": {"text": _str_param("text", 5000)}, "required": ["text"]},
                    _t_readability, safety=1, min_tier=1, category="language"))
    # Sandboxed code execution — strongest safeguard: tier-3 + safety-3 + OFF by default.
    if enable_code:
        r.register(Tool("run_python", "Run a short Python snippet in an isolated subprocess (CS exercises). Returns stdout/stderr.",
                        {"type": "object", "properties": {"code": _str_param("python code", 4000)}, "required": ["code"]},
                        _t_run_python, safety=3, min_tier=3, category="cs"))

    # Data-pull tools (MCP-resource-like) — only when a backend callback is supplied.
    if search_fn:
        r.register(Tool("search_concepts", "Search the course knowledge base for relevant concepts/passages to ground a question or hint.",
                        {"type": "object", "properties": {"query": _str_param("search query", 200), "course_uid": _str_param("optional course uid", 40)}, "required": ["query"]},
                        lambda query, course_uid=None: search_fn(query, course_uid), safety=1, min_tier=1, category="data"))
    if content_fn:
        r.register(Tool("get_concept_content", "Fetch the full content of a specific concept to ground the dialogue in the source material.",
                        {"type": "object", "properties": {"course_uid": _str_param("course uid", 40), "concept_uid": _str_param("concept uid", 40)}, "required": ["course_uid", "concept_uid"]},
                        lambda course_uid, concept_uid: content_fn(course_uid, concept_uid), safety=1, min_tier=1, category="data"))
    if mastery_fn:
        r.register(Tool("get_mastery", "Get the student's mastery/progress for a concept, to adapt difficulty.",
                        {"type": "object", "properties": {"concept_uid": _str_param("concept uid", 40)}, "required": ["concept_uid"]},
                        lambda concept_uid: mastery_fn(concept_uid), safety=1, min_tier=1, category="data"))
    if wiki_fn:
        r.register(Tool("wikipedia_lookup", "Look up a concise, grounded definition/summary of a term to ground a hint.",
                        {"type": "object", "properties": {"title": _str_param("term to look up", 120)}, "required": ["title"]},
                        lambda title: wiki_fn(title), safety=1, min_tier=1, category="data"))
    return r
