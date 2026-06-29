"""Tests for the MCP-style tutor tool layer (B14 / Task #14)."""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/common'))

from services.common.tutor_tools import (
    model_tier, TIER_POLICY, policy_for, Tool, ToolRegistry,
    validate_args, _run_with_timeout, _Timeout, build_default_registry,
)


# --- tiers ---
def test_model_tier_mapping():
    assert model_tier("qwen3.5:9b") == 2
    assert model_tier("qwen3:14b") == 2
    assert model_tier("qwen3.5:35b-a3b") == 3
    assert model_tier("qwen3:1.5b") == 1
    assert model_tier("phi-3-mini-4b") == 1
    assert model_tier("") == 2  # unknown defaults to mid


def test_policy_scales_with_tier():
    assert policy_for("qwen3:1.5b")["max_tool_calls"] < policy_for("qwen3.5:35b-a3b")["max_tool_calls"]
    assert policy_for("qwen3:1.5b")["max_safety"] == 1


# --- gating ---
def test_available_for_is_tier_and_safety_gated():
    r = build_default_registry()
    small = {t.name for t in r.available_for("qwen3:1.5b")}
    mid = {t.name for t in r.available_for("qwen3.5:9b")}
    # small tier: only min_tier==1, safety==1 -> math_check but NOT solve/plot
    assert "math_check" in small
    assert "solve_equation" not in small
    assert "plot_function" not in small      # safety 2 > tier-1 max_safety
    # mid tier: gets the calculus + plot (safety 2)
    assert {"solve_equation", "differentiate", "plot_function"} <= mid


# --- executor failsafes ---
def test_execute_unknown_tool():
    r = ToolRegistry()
    assert r.execute("qwen3.5:9b", "nope", {})["ok"] is False


def test_execute_tool_above_tier_denied():
    r = build_default_registry()
    out = r.execute("qwen3:1.5b", "plot_function", {"expression": "x"})
    assert out["ok"] is False and "not permitted" in out["error"]


def test_execute_invalid_args():
    r = build_default_registry()
    out = r.execute("qwen3.5:9b", "math_check", {})  # missing required 'expression'
    assert out["ok"] is False and "required" in out["error"]


def test_execute_never_raises_on_tool_error():
    r = ToolRegistry()
    r.register(Tool("boom", "raises", {"type": "object", "properties": {}}, lambda: 1 / 0))
    out = r.execute("qwen3.5:9b", "boom", {})
    assert out["ok"] is False and "error" in out


def test_execute_truncates_oversized_output():
    r = ToolRegistry()
    r.register(Tool("big", "huge", {"type": "object", "properties": {}}, lambda: "x" * 100000))
    out = r.execute("qwen3:1.5b", "big", {})  # tier-1 cap 1200
    assert out["ok"] and len(out["result"]) <= 1200 + 20 and out["result"].endswith("[truncated]")


def test_validate_args_types():
    schema = {"type": "object", "properties": {"v": {"type": "number"}}, "required": ["v"]}
    assert validate_args(schema, {"v": 3}) is None
    assert validate_args(schema, {"v": "x"}) is not None
    assert validate_args(schema, {}) is not None


def test_run_with_timeout_raises():
    try:
        _run_with_timeout(lambda: time.sleep(2), {}, 0.2)
        assert False
    except _Timeout:
        pass


# --- real tool logic (sympy + matplotlib are installed; pint is guarded) ---
def test_math_check_simplify_and_equivalence():
    r = build_default_registry()
    out = r.execute("qwen3.5:9b", "math_check", {"expression": "2*x + 3*x"})
    assert out["ok"] and "5*x" in out["result"]
    out2 = r.execute("qwen3.5:9b", "math_check", {"expression": "x**2 - 1", "claimed": "(x-1)*(x+1)"})
    assert '"equals_claimed": true' in out2["result"].lower()


def test_solve_equation():
    r = build_default_registry()
    out = r.execute("qwen3.5:9b", "solve_equation", {"equation": "x**2 - 4 = 0", "variable": "x"})
    assert out["ok"] and "-2" in out["result"] and "2" in out["result"]


def test_plot_returns_image_markdown():
    r = build_default_registry()
    out = r.execute("qwen3.5:9b", "plot_function", {"expression": "x**2", "start": -3, "end": 3})
    assert out["ok"] and "data:image/png;base64," in out["result"] and out["result"].startswith('{')


def test_convert_units_guarded_when_pint_absent():
    # pint isn't installed on the dev host -> executor returns a clean error, no crash.
    r = build_default_registry()
    out = r.execute("qwen3.5:9b", "convert_units", {"value": 1, "from_unit": "m", "to_unit": "ft"})
    assert "ok" in out  # either ok (if pint present) or a structured error — never raises


# --- data-pull tools via injected callbacks ---
def test_data_tools_registered_only_with_callbacks():
    r_no = build_default_registry()
    assert r_no.get("search_concepts") is None
    r_yes = build_default_registry(search_fn=lambda q, c=None: [{"title": "Photosynthesis"}])
    out = r_yes.execute("qwen3:1.5b", "search_concepts", {"query": "plants"})
    assert out["ok"] and "Photosynthesis" in out["result"]


# --- expanded tool set ---
def test_evaluate_numeric_and_factor():
    r = build_default_registry()
    assert r.execute("qwen3.5:9b", "evaluate_numeric", {"expression": "sqrt(16) + 2"})["result"].count("6")
    assert "x" in r.execute("qwen3.5:9b", "factor_expression", {"expression": "x**2 - 1"})["result"]


def test_descriptive_statistics():
    out = build_default_registry().execute("qwen3:1.5b", "descriptive_statistics", {"numbers": [2, 4, 6]})
    assert out["ok"] and '"mean": 4' in out["result"]


def test_linear_regression_scipy():
    out = build_default_registry().execute("qwen3.5:9b", "linear_regression", {"x": [1, 2, 3], "y": [2, 4, 6]})
    assert out["ok"] and '"slope": 2' in out["result"] and '"r_squared": 1' in out["result"]


def test_physical_constant_scipy():
    out = build_default_registry().execute("qwen3:1.5b", "physical_constant", {"name": "speed of light in vacuum"})
    assert out["ok"] and "299792458" in out["result"]


def test_plot_points_and_graph_images():
    r = build_default_registry()
    p = r.execute("qwen3.5:9b", "plot_points", {"x": [1, 2, 3], "y": [1, 4, 9], "kind": "line"})
    assert p["ok"] and "data:image/png;base64," in p["result"]
    g = r.execute("qwen3.5:9b", "draw_graph", {"edges": [["a", "b"], ["b", "c"]]})
    assert g["ok"] and "data:image/png;base64," in g["result"]


def test_regex_test():
    out = build_default_registry().execute("qwen3.5:9b", "regex_test", {"pattern": r"\d+", "test_string": "a1b22c"})
    assert out["ok"] and '"match_count": 2' in out["result"]


def test_periodic_element_guarded_when_lib_absent():
    # periodictable isn't installed on the dev host -> clean structured error, no crash.
    out = build_default_registry().execute("qwen3.5:9b", "periodic_element", {"query": "Fe"})
    assert "ok" in out  # never raises


def test_code_tool_off_by_default():
    r = build_default_registry()                 # enable_code defaults off
    assert r.get("run_python") is None
    r2 = build_default_registry(enable_code=True)
    assert r2.get("run_python") is not None
    # even enabled, it's tier-3 only — denied for a small model
    assert r2.execute("qwen3:1.5b", "run_python", {"code": "print(1)"})["ok"] is False
