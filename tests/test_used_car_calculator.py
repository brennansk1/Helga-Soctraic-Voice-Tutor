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
    assert "do not reduce sales tax" in page.inner_text("#state-note").lower()
    page.select_option("#in-state", "TX")
    page.wait_for_timeout(400)
    assert "reduce the taxable amount" in page.inner_text("#state-note").lower()


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
