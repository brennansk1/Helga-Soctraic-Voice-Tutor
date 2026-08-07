"""Tests for the Used Car Deal Analyzer (tools/used_car_calculator).

Three layers:
  * the JavaScript model engine, run through Node's test runner and asserted on here
  * the Python dataset builder, tested directly
  * the rendered UI, driven in headless Chromium via Playwright

Node and Playwright layers skip cleanly when their runtime is absent, so this file is
safe on a machine that only has Python.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_DIR = os.path.join(REPO_ROOT, "tools", "used_car_calculator")
INDEX_HTML = os.path.join(TOOL_DIR, "index.html")

sys.path.insert(0, TOOL_DIR)
import build_datasets  # noqa: E402  (path set above)


# --------------------------------------------------------------- JS engine


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def test_engine_js_suite_passes():
    """Run the Node test suite; every assertion in it must pass."""
    node = _node()
    test_glob = os.path.join(TOOL_DIR, "tests", "*.test.js")
    result = subprocess.run(
        [node, "--test", test_glob],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300,
    )
    output = result.stdout + result.stderr

    match = re.search(r"^# pass (\d+)$", output, re.MULTILINE)
    fail_match = re.search(r"^# fail (\d+)$", output, re.MULTILINE)
    assert match, f"could not parse the Node test summary:\n{output[-3000:]}"

    passed = int(match.group(1))
    failed = int(fail_match.group(1)) if fail_match else 0

    assert failed == 0, f"{failed} JS test(s) failed:\n{output[-5000:]}"
    assert passed > 90, f"expected the full JS suite to run, only {passed} tests reported"
    assert result.returncode == 0, f"node --test exited {result.returncode}"


def _run_node_expr(expression: str):
    """Evaluate a JS expression against the engine and return the parsed JSON result."""
    node = _node()
    script = (
        "const DATA=require({data});"
        "const E=require({engine});"
        "process.stdout.write(JSON.stringify(({expr})));"
    ).format(
        data=json.dumps(os.path.join(TOOL_DIR, "data.js")),
        engine=json.dumps(os.path.join(TOOL_DIR, "engine.js")),
        expr=expression,
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


BASE_INPUT = {
    "currentYear": 2026, "brand": "Toyota", "model": "Camry", "year": 2019,
    "segment": "Midsize car", "miles": 60000, "asking": 18500, "condition": "good",
    "title": "clean", "accidents": "none", "owners": 1, "records": "yes",
    "ppi": "planned", "comps": [18000], "payType": "loan", "down": 3000,
    "creditTier": "prime", "apr": 9.6, "termMonths": 48, "taxRate": 0.06,
    "fees": 500, "annualMiles": 12000, "horizon": 5, "gasUsdPerGal": 3.15,
    "insurance": 1750, "income": 0,
}


def test_engine_reachable_from_python():
    """Sanity check that the engine loads and scores a baseline car."""
    score = _run_node_expr(f"E.analyze({json.dumps(BASE_INPUT)}, DATA).score.score")
    assert 0 <= score <= 100
    assert score > 50, "a clean, fairly-priced Toyota should not score poorly"


def test_engine_score_responds_to_price():
    cheap = dict(BASE_INPUT, asking=14000)
    dear = dict(BASE_INPUT, asking=24000)
    cheap_score = _run_node_expr(f"E.analyze({json.dumps(cheap)}, DATA).score.score")
    dear_score = _run_node_expr(f"E.analyze({json.dumps(dear)}, DATA).score.score")
    assert cheap_score > dear_score


# ---------------------------------------------------------- dataset builder


@pytest.fixture(scope="module")
def dataset():
    source = build_datasets.read_data_js()
    return build_datasets.extract_dataset(source)


def test_dataset_extracts_and_has_every_table(dataset):
    for table in ("meta", "brands", "segments", "depCurves", "mileSlopes",
                  "vehicles", "complaints", "aprByTier", "energy", "constants"):
        assert table in dataset, f"missing table: {table}"


def test_dataset_meta_is_stamped(dataset):
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", dataset["meta"]["built"])
    for table in ("vehicles", "brands", "depCurves", "aprByTier"):
        assert dataset["meta"]["sources"].get(table), f"no provenance stamp for {table}"


def test_dataset_depreciation_knots_are_ordered(dataset):
    for segment in dataset["segments"]:
        curve = dataset["depCurves"][segment]
        assert curve["r1_5"] > curve["r6_8"] > curve["r9_12"] > curve["r13p"], segment


def test_dataset_brand_values_are_plausible(dataset):
    for name, brand in dataset["brands"].items():
        assert 100 < brand["maintPerYear"] < 3000, name
        assert 0 <= brand["relBaseline"] <= 100, name
        assert 100000 < brand["lifeMiles"] <= 400000, name


def test_dataset_apr_tiers_increase_with_risk(dataset):
    tiers = dataset["aprByTier"]
    ordered = [tiers["superprime"], tiers["prime"], tiers["nearprime"],
               tiers["subprime"], tiers["deepsub"]]
    assert ordered == sorted(ordered)


def test_months_since_computes_age():
    assert build_datasets.months_since("2020-01-01") > 0
    assert build_datasets.months_since("not-a-date") is None


def test_epa_parser_averages_trims_per_model_year():
    csv_text = (
        "year,make,model,comb08,combE,VClass\n"
        "2019,Toyota,Camry,30,,Midsize Cars\n"
        "2019,Toyota,Camry,34,,Midsize Cars\n"
        "2019,Ford,F150,20,,Standard Pickup Trucks\n"
        "1990,Toyota,Old,25,,Midsize Cars\n"
    )
    vehicles, rows = build_datasets.parse_epa_vehicles(csv_text.encode())
    assert rows == 3, "pre-1996 rows should be dropped"
    assert vehicles["Toyota"]["Camry"]["years"]["2019"] == 32, "trims should be averaged"
    assert vehicles["Toyota"]["Camry"]["seg"] == "Midsize car"
    assert vehicles["Ford"]["F150"]["seg"] == "Pickup truck"
    assert "Old" not in vehicles.get("Toyota", {})


def test_epa_parser_marks_electric_models():
    csv_text = (
        "year,make,model,comb08,combE,VClass\n"
        "2019,Nissan,Leaf,112,30,Midsize Cars\n"
    )
    vehicles, _ = build_datasets.parse_epa_vehicles(csv_text.encode())
    assert vehicles["Nissan"]["Leaf"]["seg"] == "Electric"
    assert vehicles["Nissan"]["Leaf"]["kwhPer100mi"] == 30.0


def _synthetic_corpus(n_per_age=400, current_year=2026):
    """Listings on a known 15%/yr depreciation curve, so the fit has a right answer."""
    rows = []
    for age in range(1, 20):
        base = 30000 * (0.85 ** age)
        for i in range(n_per_age):
            # Spread price around the curve and vary odometer so slopes are estimable.
            odo = 8000 * age + (i % 50) * 400 + 1000
            jitter = 1 + ((i % 11) - 5) * 0.01
            price = base * jitter - (odo - 12000 * age) * 0.05
            rows.append({
                "price": str(max(price, 600)),
                "year": str(current_year - age),
                "odometer": str(odo),
                "manufacturer": "toyota",
            })
    return rows


def test_clean_listings_drops_implausible_rows():
    rows = [
        {"price": "20000", "year": "2019", "odometer": "60000"},   # keep
        {"price": "10", "year": "2019", "odometer": "60000"},      # too cheap
        {"price": "20000", "year": "2019", "odometer": "50"},      # odometer too low
        {"price": "20000", "year": "1900", "odometer": "60000"},   # too old
        {"price": "abc", "year": "2019", "odometer": "60000"},     # unparseable
    ]
    cleaned = build_datasets.clean_listings(rows, 2026)
    assert len(cleaned) == 1
    assert cleaned[0]["age"] == 7


def test_fit_depreciation_recovers_a_known_curve():
    listings = build_datasets.clean_listings(_synthetic_corpus(), 2026)
    curve = build_datasets.fit_depreciation(listings, "Midsize car")
    assert curve is not None, "a clean 400-per-age corpus should fit"
    assert 0.10 < curve["r1_5"] < 0.20, f"expected ~15%/yr, got {curve['r1_5']}"
    assert curve["r1_5"] > curve["r6_8"] > curve["r9_12"] > curve["r13p"], \
        "the fit must preserve the monotonic slowdown the engine relies on"


def test_fit_depreciation_refuses_thin_data():
    listings = build_datasets.clean_listings(_synthetic_corpus(n_per_age=5), 2026)
    assert build_datasets.fit_depreciation(listings, "Midsize car") is None


def test_fit_depreciation_rejects_a_fit_far_from_published_figures():
    """A corpus implying near-zero depreciation must be rejected by the sanity gate."""
    rows = []
    for age in range(1, 20):
        for i in range(400):
            rows.append({"price": "20000", "year": str(2026 - age),
                         "odometer": str(10000 * age + i), "manufacturer": "toyota"})
    listings = build_datasets.clean_listings(rows, 2026)
    assert build_datasets.fit_depreciation(listings, "Midsize car") is None


def test_fit_mile_slopes_returns_bounded_slopes():
    listings = build_datasets.clean_listings(_synthetic_corpus(), 2026)
    slopes = build_datasets.fit_mile_slopes(listings)
    if slopes is not None:          # thin price bands legitimately return None
        for band in slopes:
            assert 0.02 <= band["usdPerMile"] <= 0.25
            assert band["maxPrice"] > 0


def test_fit_mile_slopes_refuses_thin_data():
    listings = build_datasets.clean_listings(_synthetic_corpus(n_per_age=3), 2026)
    assert build_datasets.fit_mile_slopes(listings) is None


def test_dataset_roundtrips_through_a_rewrite(tmp_path, dataset):
    """write_dataset must produce a file that re-extracts to the same data."""
    target = tmp_path / "data.js"
    shutil.copy(os.path.join(TOOL_DIR, "data.js"), target)

    modified = json.loads(json.dumps(dataset))
    modified["meta"]["built"] = "2030-01-01"
    build_datasets.write_dataset(modified, str(target))

    reloaded = build_datasets.extract_dataset(
        build_datasets.read_data_js(str(target)), str(target))
    assert reloaded["meta"]["built"] == "2030-01-01"
    assert reloaded["brands"]["Toyota"] == dataset["brands"]["Toyota"]
    assert reloaded["depCurves"]["Midsize car"] == dataset["depCurves"]["Midsize car"]


def test_rewritten_dataset_is_still_loadable_by_the_engine(tmp_path, dataset):
    """A refresh must not break the JS side — Node has to be able to require() it."""
    node = _node()
    target = tmp_path / "data.js"
    shutil.copy(os.path.join(TOOL_DIR, "data.js"), target)
    build_datasets.write_dataset(json.loads(json.dumps(dataset)), str(target))

    result = subprocess.run(
        [node, "-e", "const d=require(process.argv[1]);"
                     "process.stdout.write(String(Object.keys(d.brands).length))", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"rewritten data.js is not valid JS: {result.stderr}"
    assert int(result.stdout) == len(dataset["brands"])


def test_check_command_runs_without_network(capsys, monkeypatch):
    monkeypatch.setattr(build_datasets.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    exit_code = build_datasets.main(["--check"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Dataset built" in output
    assert "unreachable" in output


def test_refresh_is_a_no_op_when_everything_is_unreachable(capsys, monkeypatch):
    monkeypatch.setattr(build_datasets, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(
                            build_datasets.SourceUnavailable("offline")))
    exit_code = build_datasets.main(["--refresh"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Nothing refreshed" in output


def test_cli_requires_an_action(capsys):
    assert build_datasets.main([]) == 1


# ------------------------------------------------------------------- the UI


CHROMIUM_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
]


def _chromium_path():
    for candidate in CHROMIUM_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    import glob
    found = glob.glob("/opt/pw-browsers/chromium*/chrome-linux/chrome")
    return found[0] if found else None


@pytest.fixture(scope="module")
def page():
    playwright_api = pytest.importorskip("playwright.sync_api",
                                         reason="playwright is not installed")
    chromium = _chromium_path()
    if not chromium:
        pytest.skip("no chromium build available")

    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        pg = browser.new_page()
        pg.page_errors = []
        pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
        pg.goto("file://" + INDEX_HTML)
        pg.click("#btn-analyze")
        pg.wait_for_selector("#results", state="visible", timeout=20000)
        yield pg
        browser.close()


def test_ui_renders_a_score(page):
    score = int(page.inner_text("#score-num"))
    assert 0 <= score <= 100
    assert page.inner_text("#score-verdict").strip()


def test_ui_has_no_javascript_errors(page):
    """Blocked network requests are expected offline; uncaught JS exceptions are not."""
    assert page.page_errors == [], f"uncaught JS errors: {page.page_errors}"


def test_ui_renders_all_four_stat_tiles(page):
    assert page.eval_on_selector_all("#tiles .tile", "els => els.length") == 4


def test_ui_renders_the_score_breakdown_summing_to_100(page):
    maxima = page.eval_on_selector_all(
        "#score-breakdown td.n",
        "els => els.map(e => parseFloat(e.textContent.split('/')[1]))",
    )
    assert maxima, "score breakdown is empty"
    assert abs(sum(maxima) - 100) < 0.01, f"component maxima sum to {sum(maxima)}"


def test_ui_renders_both_chart_series_and_a_legend(page):
    # Two lines (value + loan balance) for the default financed scenario.
    assert page.eval_on_selector_all("#line-chart path", "els => els.length") == 2
    assert page.eval_on_selector_all("#line-legend .key", "els => els.length") == 2


def test_ui_renders_the_cost_breakdown_bars(page):
    bars = page.eval_on_selector_all(".tco-bar", "els => els.length")
    assert bars >= 5, f"expected the full cost breakdown, got {bars} bars"


def test_ui_charts_have_table_fallbacks(page):
    assert page.eval_on_selector_all("#line-table tr", "els => els.length") > 1
    assert page.eval_on_selector_all("#bar-table tr", "els => els.length") > 1


def test_ui_lists_findings(page):
    assert page.eval_on_selector_all("#findings li", "els => els.length") >= 1


def test_ui_recomputes_when_an_input_changes(page):
    before = int(page.inner_text("#score-num"))
    page.fill("#in-price", "26000")
    page.dispatch_event("#in-price", "change")
    page.wait_for_timeout(400)
    after = int(page.inner_text("#score-num"))
    assert after < before, "raising the asking price should lower the score"
    page.fill("#in-price", "18500")
    page.dispatch_event("#in-price", "change")
    page.wait_for_timeout(400)


def test_ui_salvage_title_forces_a_walk_away_verdict(page):
    page.select_option("#in-title", "rebuilt")
    page.wait_for_timeout(400)
    score = int(page.inner_text("#score-num"))
    assert score <= 40, f"a salvage title must cap the score, got {score}"
    assert "capped" in page.inner_text("#score-caps").lower()
    # Tags render uppercased by the report's type treatment, so match case-insensitively.
    findings = page.inner_text("#findings").lower()
    assert "title" in findings
    assert "salvage" in findings
    page.select_option("#in-title", "clean")
    page.wait_for_timeout(400)


def test_ui_cash_purchase_hides_the_loan_series(page):
    page.select_option("#in-paytype", "cash")
    page.wait_for_timeout(400)
    assert page.eval_on_selector_all("#line-chart path", "els => els.length") == 1
    assert page.eval_on_selector_all("#line-legend .key", "els => els.length") == 0, \
        "a single series needs no legend box"
    page.select_option("#in-paytype", "loan")
    page.wait_for_timeout(400)


def test_ui_comparison_table_saves_and_clears(page):
    page.click("#btn-save")
    page.wait_for_selector("#compare-card", state="visible", timeout=5000)
    rows = page.eval_on_selector_all("#compare-table tr", "els => els.length")
    assert rows >= 2, "expected a header row plus the saved car"
    page.click("#btn-clear-compare")
    page.wait_for_timeout(300)
    assert page.eval_on_selector("#compare-card", "e => e.style.display") == "none"


def test_ui_dark_mode_toggle_redraws_the_charts(page):
    page.click("#theme-toggle")
    page.wait_for_timeout(500)
    assert page.eval_on_selector("html", "e => e.dataset.theme") in ("dark", "light")
    assert page.eval_on_selector_all("#line-chart path", "els => els.length") >= 1, \
        "charts must survive a theme switch"
    page.click("#theme-toggle")
    page.wait_for_timeout(400)


def test_ui_vin_field_rejects_a_malformed_vin_offline(page):
    page.fill("#in-vin", "NOTAVIN")
    page.click("#btn-vin")
    page.wait_for_timeout(800)
    message = page.inner_text("#vin-result")
    assert "valid VIN" in message or "manually" in message
    page.fill("#in-vin", "")


def test_ui_escapes_html_in_user_supplied_text(page):
    """A nickname containing markup must never become live DOM."""
    page.fill("#in-name", "<img src=x onerror=alert(1)>Camry")
    page.dispatch_event("#in-name", "change")
    page.wait_for_timeout(300)
    page.click("#btn-save")
    page.wait_for_selector("#compare-card", state="visible", timeout=5000)
    injected = page.eval_on_selector_all("#compare-table img", "els => els.length")
    assert injected == 0, "user text was injected as HTML"
    assert "<img" in page.inner_text("#compare-table"), "the literal text should still show"
    page.click("#btn-clear-compare")
    page.wait_for_timeout(300)
    page.fill("#in-name", "")


# ------------------------------------------------- new report sections (UI)


def test_ui_renders_the_loan_term_comparison(page):
    """Section 08 must price out every term with payment, interest and underwater time."""
    rows = page.eval_on_selector_all("#term-table tbody tr", "els => els.length")
    assert rows >= 5, f"expected a row per loan length, got {rows}"
    payments = page.eval_on_selector_all(
        "#term-table tbody tr td:nth-child(2)",
        "els => els.map(e => parseFloat(e.textContent.replace(/[^0-9.]/g, '')))",
    )
    assert payments == sorted(payments, reverse=True), \
        "payments must fall as the term lengthens"
    interest = page.eval_on_selector_all(
        "#term-table tbody tr td:nth-child(3)",
        "els => els.map(e => parseFloat(e.textContent.replace(/[^0-9.]/g, '')))",
    )
    assert interest == sorted(interest), "total interest must rise as the term lengthens"


def test_ui_marks_a_recommended_term(page):
    assert page.eval_on_selector_all("#term-table tr.rec-row", "els => els.length") == 1


def _settle_scroll(page, tries=25):
    """Wait until the page has stopped scrolling.

    The report uses smooth scrolling after 'Add to comparison', and a long animation
    can still be running when the next interaction starts — which would slide the
    click target out from under the cursor.
    """
    last = None
    for _ in range(tries):
        current = page.evaluate("() => Math.round(window.scrollY)")
        if current == last:
            return
        last = current
        page.wait_for_timeout(100)


def test_ui_clicking_a_term_row_adopts_it(page):
    _settle_scroll(page)
    row = page.locator("#term-table tbody tr[data-term='60']")
    row.scroll_into_view_if_needed()
    _settle_scroll(page)
    row.click()
    page.wait_for_timeout(400)
    assert page.input_value("#in-term") == "60"
    page.select_option("#in-term", "48")
    page.wait_for_timeout(400)


def test_ui_target_payment_solves_for_a_term(page):
    page.fill("#in-target-payment", "300")
    page.dispatch_event("#in-target-payment", "change")
    page.wait_for_timeout(500)
    body = page.inner_text("#term-body").lower()
    assert "months" in body and "300" in body
    page.fill("#in-target-payment", "5")
    page.dispatch_event("#in-target-payment", "change")
    page.wait_for_timeout(500)
    assert "never pays this loan off" in page.inner_text("#term-body").lower()
    page.fill("#in-target-payment", "")
    page.dispatch_event("#in-target-payment", "change")
    page.wait_for_timeout(400)


def test_ui_term_section_hidden_for_cash(page):
    page.select_option("#in-paytype", "cash")
    page.wait_for_timeout(400)
    assert page.eval_on_selector("#term-block", "e => e.style.display") == "none"
    page.select_option("#in-paytype", "loan")
    page.wait_for_timeout(400)
    assert page.eval_on_selector("#term-block", "e => e.style.display") == "block"


def test_ui_renders_the_price_position_chart(page):
    """Section 07 plots the ask against fair value, target and walk-away."""
    assert page.eval_on_selector_all("#price-chart svg", "els => els.length") == 1
    labels = page.inner_text("#price-chart").upper()
    for expected in ("ASKING", "FAIR VALUE", "TARGET", "WALK AWAY"):
        assert expected in labels, f"{expected} missing from the price line"


def test_ui_price_chart_plots_each_comparable(page):
    page.fill("#in-comp1", "18000")
    page.fill("#in-comp2", "19000")
    page.dispatch_event("#in-comp2", "change")
    page.wait_for_timeout(500)
    circles = page.eval_on_selector_all("#price-chart circle", "els => els.length")
    assert circles >= 3, "two comparables plus the asking marker"
    page.fill("#in-comp1", "")
    page.fill("#in-comp2", "")
    page.dispatch_event("#in-comp2", "change")
    page.wait_for_timeout(400)


def test_ui_price_solver_names_a_target_price(page):
    page.fill("#in-price", "26000")
    page.fill("#in-comp1", "18000")
    page.dispatch_event("#in-comp1", "change")
    page.wait_for_timeout(600)
    text = page.inner_text("#price-solver")
    assert "$" in text and "score" in text.lower()
    page.fill("#in-price", "18500")
    page.fill("#in-comp1", "")
    page.dispatch_event("#in-comp1", "change")
    page.wait_for_timeout(400)


def test_ui_renders_the_cumulative_cost_chart(page):
    assert page.eval_on_selector_all("#cum-chart path", "els => els.length") >= 2, \
        "expected an area wash and a line"
    assert page.eval_on_selector_all("#cum-chart .cum-hit", "els => els.length") >= 2


def test_ui_affordability_prompts_for_income_then_grades_it(page):
    assert "take-home" in page.inner_text("#afford-check").lower()
    page.fill("#in-income", "5200")
    page.dispatch_event("#in-income", "change")
    page.wait_for_timeout(500)
    text = page.inner_text("#afford-check")
    assert "20 / 4 / 10" in text
    assert "PASS" in text or "OVER" in text
    page.fill("#in-income", "")
    page.dispatch_event("#in-income", "change")
    page.wait_for_timeout(400)


def test_ui_offline_recall_fallback_is_surfaced(page):
    """With no network, the baked recall count must still warn the user."""
    findings = page.inner_text("#findings").lower()
    assert "recall" in findings, "offline runs should still raise the recall check"


def test_ui_complaint_section_hidden_without_live_data(page):
    assert page.eval_on_selector("#complaint-block", "e => e.style.display") == "none"


def test_ui_masthead_reports_the_data_mode(page):
    assert "offline" in page.inner_text("#masthead-mode").lower()
    assert page.inner_text("#masthead-subject").strip() != ""


def test_ui_colophon_names_its_sources(page):
    text = page.inner_text("#data-footnote")
    for expected in ("NHTSA", "EPA", "EIA", "FRED", "Not financial advice"):
        assert expected in text, f"colophon does not mention {expected}"


def test_ui_csv_export_produces_a_downloadable_file(page):
    page.fill("#in-name", "Test Camry")
    page.dispatch_event("#in-name", "change")
    page.wait_for_timeout(300)
    page.click("#btn-save")
    page.wait_for_selector("#compare-card", state="visible", timeout=5000)
    with page.expect_download() as info:
        page.click("#btn-export")
    download = info.value
    assert download.suggested_filename == "car-comparison.csv"
    page.click("#btn-clear-compare")
    page.wait_for_timeout(300)
    page.fill("#in-name", "")


def test_ui_score_band_strip_has_five_segments(page):
    assert page.eval_on_selector_all("#score-band span", "els => els.length") == 5


def test_ui_click_survives_an_edit_in_another_field(page):
    """Regression: the first click after editing a field must still register.

    Re-running the report used to rewrite every section's innerHTML, so the node under
    the cursor was replaced between mousedown and mouseup and the browser emitted no
    click at all. Sections now only rebuild when their content actually changes.
    """
    _settle_scroll(page)
    page.fill("#in-name", "Regression check")     # dirty a field, leave it focused
    row = page.locator("#term-table tbody tr[data-term='72']")
    row.scroll_into_view_if_needed()
    _settle_scroll(page)
    row.click()                                   # first click after the edit
    page.wait_for_timeout(400)
    assert page.input_value("#in-term") == "72", "the click after an edit was swallowed"
    page.select_option("#in-term", "48")
    page.fill("#in-name", "")
    page.dispatch_event("#in-name", "change")
    page.wait_for_timeout(400)


# ------------------------------------- trade-in, services, sensitivity (UI)


def test_ui_state_selection_fills_tax_and_explains_the_rules(page):
    page.select_option("#in-state", "VA")
    page.wait_for_timeout(500)
    assert float(page.input_value("#in-tax")) > 0, "selecting a state should seed the tax rate"
    note = page.inner_text("#state-note").lower()
    assert "virginia" in note or "va" in note
    assert "confirm" in note, "the note must say the defaults need verifying"


def test_ui_state_without_trade_in_credit_says_so(page):
    page.select_option("#in-state", "CA")
    page.wait_for_timeout(500)
    assert "no trade-in credit" in page.inner_text("#state-note").lower()
    page.select_option("#in-state", "TX")
    page.wait_for_timeout(400)
    assert "after your trade-in" in page.inner_text("#state-note").lower()


def test_ui_state_panel_covers_every_cost_that_varies_by_state(page):
    page.select_option("#in-state", "VA")
    page.wait_for_timeout(500)
    note = page.inner_text("#state-note").lower()
    for expected in ("sales tax", "title", "registration", "insurance", "fuel",
                     "electricity", "inspection", "ev surcharge", "property tax"):
        assert expected in note, f"the state profile omits {expected}"
    assert "confirm your local figures" in note


def test_ui_signing_section_breaks_down_the_deal(page):
    rows = page.eval_on_selector_all("#signing-body tr", "els => els.length")
    assert rows >= 4
    text = page.inner_text("#signing-body").lower()
    assert "sales tax" in text
    assert "out the door" in text


def test_ui_trade_in_shows_the_tax_saving(page):
    page.select_option("#in-state", "TX")
    page.fill("#in-trade-value", "8000")
    page.dispatch_event("#in-trade-value", "change")
    page.wait_for_timeout(600)
    text = page.inner_text("#signing-body").lower()
    assert "trade-in allowance" in text
    assert "taxable amount" in text
    assert "cuts your sales tax" in text


def test_ui_negative_trade_equity_is_called_out(page):
    page.fill("#in-trade-payoff", "12000")
    page.dispatch_event("#in-trade-payoff", "change")
    page.wait_for_timeout(600)
    assert "underwater" in page.inner_text("#signing-body").lower()
    assert "trade-in" in page.inner_text("#findings").lower()
    page.fill("#in-trade-value", "")
    page.fill("#in-trade-payoff", "")
    page.dispatch_event("#in-trade-payoff", "change")
    page.wait_for_timeout(500)


def test_ui_service_schedule_lists_upcoming_work(page):
    page.fill("#in-miles", "44000")
    page.dispatch_event("#in-miles", "change")
    page.wait_for_timeout(700)
    rows = page.eval_on_selector_all("#service-body tbody tr", "els => els.length")
    assert rows >= 2, "wear items should be listed"
    text = page.inner_text("#service-body").lower()
    assert "tyres" in text or "brake" in text
    assert "first year" in text


def test_ui_service_schedule_warns_when_year_one_is_front_loaded(page):
    text = page.inner_text("#service-body").lower()
    assert "lands in the first year" in text or "fall due in the" in text


def test_ui_sensitivity_ranks_the_assumptions(page):
    bars = page.eval_on_selector_all(".sens-bar", "els => els.length")
    assert bars >= 5, f"expected a bar per assumption, got {bars}"
    widths = page.eval_on_selector_all(
        ".sens-bar", "els => els.map(e => e.getBBox().width)")
    assert widths == sorted(widths, reverse=True), "bars must be sorted by swing"


def test_ui_sensitivity_states_a_range_not_a_point(page):
    text = page.inner_text("#sens-body")
    assert "realistic range" in text.lower()
    assert text.count("$") >= 3
    assert "point estimate" in page.inner_text("#sens-note").lower()


def test_ui_score_section_qualifies_the_number(page):
    text = page.inner_text("#score-card").lower() if page.query_selector("#score-card") \
        else page.inner_text("#results").lower()
    assert "not a measurement" in text or "weighted judgement" in text


def test_ui_comp_bias_toggle_moves_the_walk_away_price(page):
    page.fill("#in-comp1", "19000")
    page.dispatch_event("#in-comp1", "change")
    page.wait_for_timeout(600)
    with_bias = page.inner_text("#price-ladder")
    page.uncheck("#in-comp-bias")
    page.wait_for_timeout(600)
    without_bias = page.inner_text("#price-ladder")
    assert with_bias != without_bias, "the haircut must change the numbers"
    page.check("#in-comp-bias")
    page.wait_for_timeout(500)


def test_ui_share_link_round_trips_the_form(page):
    page.fill("#in-price", "17250")
    page.dispatch_event("#in-price", "change")
    page.wait_for_timeout(400)
    page.click("#btn-share")
    page.wait_for_timeout(400)
    url = page.evaluate("() => location.hash")
    assert url.startswith("#c="), "the link must carry the encoded form"

    page.goto("file://" + INDEX_HTML + url)
    page.wait_for_timeout(1200)
    assert page.input_value("#in-price") == "17250", "a shared link must restore the values"


def test_ui_inputs_survive_a_reload(page):
    page.goto("file://" + INDEX_HTML)
    page.wait_for_timeout(800)
    assert page.input_value("#in-price") == "17250", "inputs should persist between visits"


def test_ui_reset_clears_saved_inputs(page):
    page.click("#btn-reset")
    page.wait_for_timeout(1200)
    assert page.input_value("#in-price") == "18500", "reset should restore the defaults"
    # Re-establish a rendered report for any later test.
    page.click("#btn-analyze")
    page.wait_for_selector("#results", state="visible", timeout=20000)
    page.wait_for_timeout(400)


def test_ui_charts_shrink_for_a_phone_viewport(page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_timeout(900)
    page.click("#btn-analyze")
    page.wait_for_timeout(1200)
    width = page.eval_on_selector("#line-chart svg", "e => e.viewBox.baseVal.width")
    assert width < 600, f"charts should redraw narrower on a phone, got {width}"
    body_scrolls = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
    assert not body_scrolls, "the page itself must not scroll sideways on a phone"
    page.set_viewport_size({"width": 1240, "height": 1000})
    page.wait_for_timeout(700)


# ------------------------------------------- professional polish (UI layer)


def test_ui_bottom_line_gives_actionable_numbers(page):
    text = page.inner_text("#bottomline")
    assert "bottom line" in text.lower()
    assert "open at" in text.lower()
    assert "walk away above" in text.lower()
    assert "a month" in text.lower()
    assert text.count("$") >= 4


def test_ui_bottom_line_leads_with_a_verdict(page):
    page.select_option("#in-title", "rebuilt")
    page.wait_for_timeout(600)
    assert "do not buy" in page.inner_text("#bottomline").lower()
    page.select_option("#in-title", "clean")
    page.wait_for_timeout(600)
    assert "do not buy" not in page.inner_text("#bottomline").lower()


def test_ui_sticky_bar_carries_the_key_numbers(page):
    assert "on" in page.eval_on_selector("#stickybar", "e => e.className")
    assert page.inner_text("#sb-num").strip().isdigit()
    for sel in ("#sb-offer", "#sb-walk", "#sb-mo"):
        assert "$" in page.inner_text(sel), f"{sel} should show a figure"


def test_ui_sticky_bar_edit_button_returns_to_the_form(page):
    page.click("#sb-edit")
    page.wait_for_timeout(600)
    focused = page.evaluate("() => document.activeElement.id")
    assert focused == "in-price"


def test_ui_blocks_the_report_on_invalid_input_with_a_specific_message(page):
    page.fill("#in-price", "0")
    page.dispatch_event("#in-price", "change")
    page.wait_for_timeout(500)
    assert page.eval_on_selector("#results", "e => e.style.display") == "none"
    error = page.inner_text(".field-error")
    assert "asking price" in error.lower()
    assert page.eval_on_selector("#in-price", "e => e.getAttribute('aria-invalid')") == "true"
    page.fill("#in-price", "18500")
    page.dispatch_event("#in-price", "change")
    page.wait_for_timeout(500)
    assert page.eval_on_selector("#results", "e => e.style.display") == "block"
    assert page.eval_on_selector_all(".field-error", "els => els.length") == 0


def test_ui_validation_catches_an_impossible_year(page):
    page.fill("#in-year", "1899")
    page.dispatch_event("#in-year", "change")
    page.wait_for_timeout(500)
    assert "1981" in page.inner_text(".field-error")
    page.fill("#in-year", "2019")
    page.dispatch_event("#in-year", "change")
    page.wait_for_timeout(500)


def test_ui_has_a_live_region_for_screen_readers(page):
    assert page.eval_on_selector("#sr-announce", "e => e.getAttribute('aria-live')") == "polite"
    text = page.inner_text("#sr-announce")
    assert "deal score" in text.lower(), "the score should be announced when it changes"


def test_ui_exposes_landmarks_and_a_skip_link(page):
    assert page.eval_on_selector("#skip-link", "e => e.getAttribute('href')") == "#results"
    assert page.eval_on_selector("#results", "e => e.getAttribute('role')") == "region"
    assert page.eval_on_selector("#results", "e => e.getAttribute('aria-label')")


def test_ui_every_input_has_an_accessible_name(page):
    unlabelled = page.evaluate("""() => {
      const bad = [];
      document.querySelectorAll('input, select').forEach(el => {
        const inLabel = el.closest('label');
        const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
        if (!inLabel && !aria) bad.push(el.id || el.outerHTML.slice(0, 40));
      });
      return bad;
    }""")
    assert unlabelled == [], f"inputs without an accessible name: {unlabelled}"


CONTRAST_JS = r"""
() => {
  // Composite a possibly-transparent colour over its ancestors before measuring,
  // otherwise a 14%-alpha wash reads as a fully saturated background.
  const parse = (s) => {
    const m = s.match(/-?\d+(\.\d+)?/g);
    if (!m) return null;
    const v = m.map(Number);
    return { r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1 };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1
  });
  const bgOf = (el) => {
    const stack = [];
    let n = el;
    while (n && n !== document.documentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a === 1) break; }
      n = n.parentElement;
    }
    const root = parse(getComputedStyle(document.documentElement).backgroundColor)
      || { r: 255, g: 255, b: 255, a: 1 };
    let acc = root.a === 1 ? root : { r: 255, g: 255, b: 255, a: 1 };
    for (let i = stack.length - 1; i >= 0; i--) acc = over(stack[i], acc);
    return acc;
  };
  const lum = (c) => {
    const f = [c.r, c.g, c.b].map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  };
  const bad = [];
  document.querySelectorAll('p, td, th, li, span, div, label, h1, h2, h3, button, summary, dd, dt')
    .forEach(el => {
      if (el.offsetParent === null) return;
      if (!Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim().length > 1)) return;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.95) return;
      const size = parseFloat(cs.fontSize);
      const weight = parseInt(cs.fontWeight, 10) || 400;
      const need = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
      const bg = bgOf(el);
      const fgRaw = parse(cs.color);
      if (!fgRaw) return;
      const fg = fgRaw.a < 1 ? over(fgRaw, bg) : fgRaw;
      const l1 = lum(fg), l2 = lum(bg);
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      if (ratio < need - 0.02) {
        bad.push((el.tagName + '.' + (el.className || '')).slice(0, 42) +
          '  ' + ratio.toFixed(2) + ':1 (need ' + need + ')  "' +
          el.textContent.trim().slice(0, 30) + '"');
      }
    });
  return bad.slice(0, 14);
}
"""


def test_ui_text_meets_wcag_contrast(page):
    """Every rendered text node must clear WCAG AA against its composited background."""
    failures = page.evaluate(CONTRAST_JS)
    assert failures == [], "light-mode text failing WCAG AA:\n" + "\n".join(failures)


def test_ui_dark_mode_also_meets_contrast(page):
    page.click("#theme-toggle")
    page.wait_for_timeout(700)
    failures = page.evaluate(CONTRAST_JS)
    page.click("#theme-toggle")
    page.wait_for_timeout(500)
    assert failures == [], "dark-mode text failing WCAG AA:\n" + "\n".join(failures)


def test_ui_recovers_from_an_engine_failure_instead_of_dying(page):
    page.evaluate("""() => {
      window.__origAnalyze = window.UCDA_ENGINE.analyze;
      window.UCDA_ENGINE.analyze = () => { throw new Error('synthetic engine failure'); };
    }""")
    page.fill("#in-miles", "61000")
    page.dispatch_event("#in-miles", "change")
    page.wait_for_timeout(500)
    assert page.eval_on_selector("#fatal", "e => e.style.display") == "block"
    assert "synthetic engine failure" in page.inner_text("#fatal-msg")
    page.evaluate("() => { window.UCDA_ENGINE.analyze = window.__origAnalyze; }")
    page.fill("#in-miles", "60000")
    page.dispatch_event("#in-miles", "change")
    page.wait_for_timeout(600)
    assert page.eval_on_selector("#fatal", "e => e.style.display") == "none"


def test_ui_marks_the_required_fields(page):
    required = page.eval_on_selector_all("label.f .req", "els => els.length")
    assert required >= 3, "the minimum viable inputs should be marked"
    assert "only need four things" in page.inner_text(".lede").lower()


def test_ui_declares_document_metadata(page):
    assert page.eval_on_selector("meta[name=description]", "e => e.content")
    assert page.eval_on_selector("link[rel=icon]", "e => e.href").startswith("data:image/svg")
    assert page.eval_on_selector_all("meta[name=theme-color]", "els => els.length") == 2


def test_print_sheet_drops_the_inputs_and_keeps_the_numbers(page):
    page.emulate_media(media="print")
    page.wait_for_timeout(300)
    hidden = page.eval_on_selector_all(
        ".input-block", "els => els.every(e => getComputedStyle(e).display === 'none')")
    assert hidden, "input sections must not print"
    assert page.eval_on_selector("#stickybar", "e => getComputedStyle(e).display") == "none"
    for keep in ("#bottomline", "#score-breakdown", "#findings"):
        assert page.eval_on_selector(keep, "e => getComputedStyle(e).display") != "none", \
            f"{keep} should still print"
    page.emulate_media(media="screen")
    page.wait_for_timeout(300)


def test_ui_state_costs_reach_the_totals(page):
    """Changing state alone must move the cost of ownership."""
    page.select_option("#in-state", "ME")
    page.wait_for_timeout(700)
    cheap = page.inner_text("#tco-note")
    page.select_option("#in-state", "MI")
    page.wait_for_timeout(700)
    dear = page.inner_text("#tco-note")
    assert cheap != dear, "state should change the cost of ownership"
    money = lambda s: int(re.sub(r"[^0-9]", "", s.split("all in")[0]))
    assert money(dear) > money(cheap), "Michigan insurance should cost more than Maine"


# --------------------------------------------------- listing ingest (Python)

import scrape_listings  # noqa: E402  (TOOL_DIR is on sys.path)


def test_num_parses_the_shapes_listings_actually_use():
    assert scrape_listings._num("$18,995") == 18995
    assert scrape_listings._num("52,341 mi") == 52341
    assert scrape_listings._num(18500) == 18500
    assert scrape_listings._num("") is None
    assert scrape_listings._num(None) is None
    assert scrape_listings._num("n/a") is None


def test_normalise_maps_aliases_and_types():
    row = scrape_listings.normalise(
        {"year": "2019", "make": "toyota", "model": "Camry",
         "mileage": "52,341", "listPrice": "$18,995", "dealerName": "Example"}, 0)
    assert row["year"] == 2019
    assert row["make"] == "Toyota"
    assert row["miles"] == 52341
    assert row["price"] == 18995
    assert row["dealer"] == "Example"
    assert row["seenAt"]


def test_normalise_discards_a_malformed_vin():
    good = scrape_listings.normalise({"vin": "4T1B11HK5KU123456"}, 0)
    bad = scrape_listings.normalise({"vin": "NOT-A-VIN"}, 0)
    assert good["vin"] == "4T1B11HK5KU123456"
    assert bad["vin"] is None, "a wrong VIN is worse than no VIN"


def test_usable_requires_the_scorable_minimum():
    complete = {"year": 2019, "make": "Toyota", "model": "Camry", "price": 18000, "miles": 50000}
    assert scrape_listings.usable(complete)
    for missing in ("year", "make", "model", "price", "miles"):
        partial = dict(complete)
        partial[missing] = None
        assert not scrape_listings.usable(partial), missing


def test_dedupe_prefers_the_cheaper_sighting_and_records_the_other():
    rows = [
        scrape_listings.normalise({"year": 2019, "make": "Toyota", "model": "Camry",
                                   "miles": 50000, "price": 19500,
                                   "vin": "4T1B11HK5KU123456", "source": "a"}, 0),
        scrape_listings.normalise({"year": 2019, "make": "Toyota", "model": "Camry",
                                   "miles": 50000, "price": 18500,
                                   "vin": "4T1B11HK5KU123456", "source": "b"}, 1),
    ]
    out = scrape_listings.dedupe(rows)
    assert len(out) == 1
    assert out[0]["price"] == 18500
    assert out[0]["duplicates"][0]["price"] == 19500


def test_jsonld_extraction_reads_a_schema_org_vehicle():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Car",
     "name":"2019 Toyota Camry SE",
     "vehicleIdentificationNumber":"4T1B11HK5KU123456",
     "vehicleModelDate":"2019","brand":{"@type":"Brand","name":"Toyota"},
     "model":"Camry","mileageFromOdometer":{"@type":"QuantitativeValue","value":52341},
     "offers":{"@type":"Offer","price":"18995","priceCurrency":"USD"}}
    </script></head><body></body></html>
    """
    found = scrape_listings.extract_jsonld_vehicles(html, "https://example.com/l/1")
    assert len(found) == 1
    assert found[0]["vin"] == "4T1B11HK5KU123456"
    assert found[0]["make"] == "Toyota"
    assert found[0]["price"] == "18995"
    assert found[0]["source"] == "example.com"


def test_jsonld_extraction_falls_back_to_the_name_for_make_and_model():
    html = """<script type="application/ld+json">
    {"@type":"Vehicle","name":"2018 Honda Accord EX-L",
     "offers":{"price":17450},"mileageFromOdometer":61200}</script>"""
    found = scrape_listings.extract_jsonld_vehicles(html, "https://example.com/x")
    assert found[0]["year"] == "2018"
    assert found[0]["make"] == "Honda"
    assert "Accord" in found[0]["model"]


def test_jsonld_extraction_walks_a_graph_and_ignores_other_types():
    html = """<script type="application/ld+json">
    {"@graph":[{"@type":"Organization","name":"Dealer"},
               {"@type":"Car","name":"2020 Mazda Mazda3","offers":{"price":19000},
                "mileageFromOdometer":30000}]}</script>"""
    found = scrape_listings.extract_jsonld_vehicles(html, None)
    assert len(found) == 1
    assert found[0]["make"] == "Mazda"


def test_jsonld_extraction_survives_malformed_blocks():
    html = ('<script type="application/ld+json">{not json}</script>'
            '<script type="application/ld+json">{"@type":"Car","offers":{"price":1}}</script>')
    assert len(scrape_listings.extract_jsonld_vehicles(html, None)) == 1


def test_fetch_refuses_a_path_robots_disallows(monkeypatch):
    class DenyAll:
        def set_url(self, url): pass
        def read(self): pass
        def can_fetch(self, agent, url): return False
    monkeypatch.setattr(scrape_listings.urllib.robotparser, "RobotFileParser", DenyAll)
    scrape_listings._robots_cache.clear()
    with pytest.raises(scrape_listings.FetchError, match="robots.txt"):
        scrape_listings.fetch_page("https://example.com/listing/1")
    scrape_listings._robots_cache.clear()


def test_fetch_listings_skips_disallowed_pages_without_dying(monkeypatch, caplog):
    monkeypatch.setattr(scrape_listings, "fetch_page",
                        lambda url, timeout=30: (_ for _ in ()).throw(
                            scrape_listings.FetchError("nope")))
    assert scrape_listings.fetch_listings(["https://example.com/a"], delay=0) == []


def test_cli_writes_a_database_the_browser_can_load(tmp_path):
    payload = {"listings": [
        {"year": 2019, "make": "Toyota", "model": "Camry", "miles": 52341, "price": 18995},
        {"year": 2018, "make": "Honda", "model": "Accord", "miles": 61200, "price": 17450},
        {"make": "Incomplete"},
    ]}
    src = tmp_path / "found.json"
    src.write_text(json.dumps(payload))
    js, js_json = tmp_path / "listings.js", tmp_path / "listings.json"

    code = scrape_listings.main(["--from-json", str(src), "--query", "test run",
                                 "--out", str(js), "--out-json", str(js_json)])
    assert code == 0

    saved = json.loads(js_json.read_text())
    assert saved["count"] == 2, "the incomplete record should be dropped"
    assert saved["query"] == "test run"
    assert saved["schema"] == scrape_listings.SCHEMA_VERSION

    node = _node()
    result = subprocess.run(
        [node, "-e", "const d=require(process.argv[1]);"
                     "process.stdout.write(String(d.listings.length))", str(js)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"generated listings.js is not valid JS: {result.stderr}"
    assert result.stdout == "2"


def test_cli_merge_accumulates_searches(tmp_path):
    js, js_json = tmp_path / "listings.js", tmp_path / "listings.json"
    first = tmp_path / "a.json"
    first.write_text(json.dumps([{"year": 2019, "make": "Toyota", "model": "Camry",
                                  "miles": 50000, "price": 18000}]))
    second = tmp_path / "b.json"
    second.write_text(json.dumps([{"year": 2020, "make": "Honda", "model": "Accord",
                                   "miles": 40000, "price": 21000}]))
    scrape_listings.main(["--from-json", str(first), "--out", str(js), "--out-json", str(js_json)])
    scrape_listings.main(["--from-json", str(second), "--merge",
                          "--out", str(js), "--out-json", str(js_json)])
    assert json.loads(js_json.read_text())["count"] == 2


def test_cli_reads_csv(tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("year,make,model,miles,price\n2019,Toyota,Camry,52341,18995\n")
    js, js_json = tmp_path / "listings.js", tmp_path / "listings.json"
    assert scrape_listings.main(["--from-csv", str(csv_path),
                                 "--out", str(js), "--out-json", str(js_json)]) == 0
    assert json.loads(js_json.read_text())["count"] == 1


def test_cli_needs_a_source(capsys):
    assert scrape_listings.main([]) == 1
    assert "Nothing to ingest" in capsys.readouterr().out


def test_cli_prints_the_schema(capsys):
    assert scrape_listings.main(["--schema"]) == 0
    out = capsys.readouterr().out
    for field in ("year", "make", "model", "miles", "price", "vin"):
        assert field in out


def test_shipped_sample_database_is_valid():
    """The checked-in listings.js must load and be scorable."""
    node = _node()
    script = (
        "const M=require({market});const E=require({engine});const D=require({data});"
        "const L=require({listings});"
        "const ing=M.ingest(L);"
        "const rows=M.scoreAll(ing.listings,{{currentYear:2026,payType:'cash',horizon:5,"
        "annualMiles:12000,taxRate:0.06,fees:500,down:0,termMonths:48,apr:9.6,"
        "creditTier:'prime',gasUsdPerGal:3.15,insurance:1750,income:0}},E,D,{{}});"
        "process.stdout.write(JSON.stringify({{n:ing.listings.length,scored:rows.length,"
        "top:rows[0].score,allFinite:rows.every(r=>Number.isFinite(r.score))}}));"
    ).format(
        market=json.dumps(os.path.join(TOOL_DIR, "market.js")),
        engine=json.dumps(os.path.join(TOOL_DIR, "engine.js")),
        data=json.dumps(os.path.join(TOOL_DIR, "data.js")),
        listings=json.dumps(os.path.join(TOOL_DIR, "listings.js")),
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["n"] > 100, "the sample market should be a real cohort"
    assert summary["scored"] == summary["n"]
    assert summary["allFinite"]
    assert 0 <= summary["top"] <= 100


# ------------------------------------------------------- market section (UI)


def test_ui_market_loads_the_generated_database(page):
    status = page.inner_text("#market-status").lower()
    assert "listings" in status
    assert page.eval_on_selector("#market-live", "e => e.style.display") == "block"
    assert page.eval_on_selector("#market-empty", "e => e.style.display") == "none"


def test_ui_market_ranks_listings_best_first(page):
    scores = page.eval_on_selector_all(
        "#market-table tbody tr td:nth-child(8)",
        "els => els.map(e => parseInt(e.textContent, 10))")
    assert len(scores) > 10, "expect a real cohort in the table"
    assert scores == sorted(scores, reverse=True), "the table must be ranked"
    assert all(0 <= s <= 100 for s in scores)


def test_ui_market_shows_the_three_shortlists(page):
    assert page.eval_on_selector_all(".pick", "els => els.length") == 3
    text = page.inner_text("#market-picks").lower()
    for heading in ("best overall", "biggest bargains", "cheapest to own"):
        assert heading in text


def test_ui_market_derives_stats_from_the_cohort(page):
    stats = page.inner_text("#market-stats").lower()
    for expected in ("listings", "median price", "median score", "depreciation"):
        assert expected in stats


def test_ui_market_plots_every_listing(page):
    dots = page.eval_on_selector_all(".mk-dot", "els => els.length")
    rows = page.eval_on_selector_all("#market-table tbody tr", "els => els.length")
    assert dots > rows, "the chart shows the whole filtered set, not just the visible page"


def test_ui_market_search_narrows_the_table(page):
    before = page.eval_on_selector_all("#market-table tbody tr", "els => els.length")
    page.fill("#market-search", "camry")
    page.wait_for_timeout(500)
    after = page.inner_text("#market-table").lower()
    assert "camry" in after
    assert "accord" not in after
    page.fill("#market-search", "")
    page.wait_for_timeout(400)
    assert page.eval_on_selector_all("#market-table tbody tr", "els => els.length") == before


def test_ui_market_filters_apply(page):
    page.fill("#mf-maxprice", "14000")
    page.dispatch_event("#mf-maxprice", "change")
    page.wait_for_timeout(500)
    prices = page.eval_on_selector_all(
        "#market-table tbody tr td:nth-child(5)",
        "els => els.map(e => parseInt(e.textContent.replace(/[^0-9]/g, ''), 10))")
    assert prices, "the filter should still leave something"
    assert max(prices) <= 14000
    page.click("#btn-market-reset-filters")
    page.wait_for_timeout(500)


def test_ui_market_clean_title_filter_is_on_by_default(page):
    assert page.eval_on_selector("#mf-clean", "e => e.checked") is True
    assert "rebuilt" not in page.inner_text("#market-table").lower()
    page.uncheck("#mf-clean")
    page.wait_for_timeout(600)
    page.click("th[data-sort='price']")
    page.wait_for_timeout(500)
    page.check("#mf-clean")
    page.wait_for_timeout(500)


def test_ui_market_column_sort_works(page):
    page.click("th[data-sort='price']")
    page.wait_for_timeout(500)
    prices = page.eval_on_selector_all(
        "#market-table tbody tr td:nth-child(5)",
        "els => els.map(e => parseInt(e.textContent.replace(/[^0-9]/g, ''), 10))")
    assert prices == sorted(prices), "sorting by price should ascend"
    page.click("th[data-sort='score']")
    page.wait_for_timeout(500)


def test_ui_market_show_more_pages_the_table(page):
    first = page.eval_on_selector_all("#market-table tbody tr", "els => els.length")
    if page.eval_on_selector("#btn-market-more", "e => e.style.display") != "none":
        page.click("#btn-market-more")
        page.wait_for_timeout(500)
        assert page.eval_on_selector_all("#market-table tbody tr", "els => els.length") > first


def test_ui_market_opens_a_listing_into_the_full_report(page):
    page.click("#btn-market-reset-filters")
    page.wait_for_timeout(500)
    first_row = page.locator("#market-table tbody tr").first
    price = first_row.locator("td").nth(4).inner_text()
    expected = int(re.sub(r"[^0-9]", "", price))
    first_row.locator("button[data-open]").click()
    page.wait_for_timeout(1500)
    assert int(page.input_value("#in-price")) == expected, \
        "the listing's price should land in the report"
    assert page.eval_on_selector("#results", "e => e.style.display") == "block"
    # Cohort comparables should have been pushed into the comp fields.
    assert page.input_value("#in-comp2"), "the cohort should supply comparables"


def test_ui_market_paste_accepts_json(page):
    page.click("#btn-market-paste")
    page.wait_for_timeout(300)
    payload = json.dumps({"listings": [
        {"year": 2021, "make": "Toyota", "model": "Camry", "miles": 30000, "price": 21000},
        {"year": 2020, "make": "Toyota", "model": "Camry", "miles": 45000, "price": 19000},
    ]})
    page.fill("#market-paste", payload)
    page.click("#btn-market-load-paste")
    page.wait_for_timeout(900)
    assert "pasted json" in page.inner_text("#market-status").lower()
    assert page.eval_on_selector_all("#market-table tbody tr", "els => els.length") == 2


def test_ui_market_rejects_bad_json_with_a_message(page):
    page.click("#btn-market-paste")
    page.wait_for_timeout(300)
    page.fill("#market-paste", "{not json")
    page.click("#btn-market-load-paste")
    page.wait_for_timeout(500)
    assert "not valid json" in page.inner_text("#market-status").lower()


def test_ui_market_clear_empties_the_section(page):
    page.click("#btn-market-clear")
    page.wait_for_timeout(600)
    assert page.eval_on_selector("#market-empty", "e => e.style.display") == "block"
    assert page.eval_on_selector("#market-live", "e => e.style.display") == "none"
    assert "agents.md" in page.inner_text("#market-block").lower() or \
        "scrape_listings" in page.inner_text("#market-empty").lower()
