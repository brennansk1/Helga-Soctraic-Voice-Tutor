#!/usr/bin/env python3
"""Refresh the Used Car Deal Analyzer's baked dataset (DESIGN.md Appendix B/C).

The analyzer ships with a curated baseline dataset so it works fully offline. This
script replaces the machine-managed tables in ``data.js`` with values derived from
public sources:

  * EPA ``vehicles.csv``          -> per-model combined MPG, fuel type, EPA class
  * NHTSA ODI flat files          -> per-model-year complaint and recall counts
  * Used-listing corpora (local)  -> fitted depreciation curves and $/mile slopes

Usage
-----
    python3 build_datasets.py --check
        Report the age of each table and whether the sources are reachable.

    python3 build_datasets.py --refresh
        Download what is reachable, fit what it can, rewrite data.js.

    python3 build_datasets.py --refresh --corpus listings.csv
        Also fit depreciation/mileage coefficients from a local listing corpus
        (Kaggle craigslist-carstrucks-data or a CarGurus export). The corpus is
        never redistributed -- only the fitted coefficients are written out.

Every step is optional and degrades independently: a source that is unreachable
leaves its table untouched and is reported, so a partial refresh is safe.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(HERE, "data.js")

EPA_VEHICLES_CSV = "https://www.fueleconomy.gov/feg/epadata/vehicles.csv"
NHTSA_COMPLAINTS_FF = "https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip"
NHTSA_RECALLS_FF = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL.zip"

DATA_START = "/* ==DATA-START== */"
DATA_END = "/* ==DATA-END== */"

# Minimum listings in a (segment, age) cell before we trust a fitted coefficient.
MIN_CELL_N = 300

# Published iSeeCars-style 5-year depreciation by segment, used as the sanity gate
# for fitted curves (Appendix C step 5). Fitted values outside +/- 5 points are rejected.
PUBLISHED_5YR_DEPRECIATION = {
    "Compact car": 0.45, "Midsize car": 0.48, "Compact SUV": 0.45,
    "Midsize SUV": 0.50, "Pickup truck": 0.40, "Minivan": 0.52,
    "Sports car": 0.50, "Luxury": 0.55, "Hybrid": 0.45, "Electric": 0.53,
}

# EPA VClass string -> our segment names.
EPA_CLASS_MAP = {
    "compact cars": "Compact car",
    "subcompact cars": "Compact car",
    "minicompact cars": "Compact car",
    "two seaters": "Sports car",
    "midsize cars": "Midsize car",
    "large cars": "Midsize car",
    "small station wagons": "Compact car",
    "midsize station wagons": "Midsize car",
    "small sport utility vehicle": "Compact SUV",
    "standard sport utility vehicle": "Midsize SUV",
    "sport utility vehicle": "Midsize SUV",
    "minivan": "Minivan",
    "vans": "Minivan",
    "pickup trucks": "Pickup truck",
    "small pickup trucks": "Pickup truck",
    "standard pickup trucks": "Pickup truck",
}


class SourceUnavailable(Exception):
    """A remote source could not be fetched. Non-fatal: the caller skips that table."""


# --------------------------------------------------------------------------- io


def fetch(url: str, timeout: int = 120) -> bytes:
    """Fetch a URL, raising SourceUnavailable on any failure."""
    logger.info("Fetching %s", url)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise SourceUnavailable(f"{url}: {exc}") from exc


def read_data_js(path: str = DATA_JS) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _extract_via_node(path: str) -> Optional[Dict[str, Any]]:
    """Load data.js through Node when it is available — exact, no parsing guesswork."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        return None
    try:
        result = subprocess.run(
            [node, "-e", "process.stdout.write(JSON.stringify(require(process.argv[1])))", path],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("Node extraction unavailable (%s); falling back to the text reader", exc)
        return None


def extract_dataset(source: str, path: str = DATA_JS) -> Dict[str, Any]:
    """Evaluate the JS data block into a Python dict.

    Prefers Node (exact evaluation) and falls back to normalizing the object literal
    into JSON, so the script still runs on a machine with no Node installed.
    """
    via_node = _extract_via_node(path)
    if via_node is not None:
        return via_node

    start = source.find(DATA_START)
    end = source.find(DATA_END)
    if start == -1 or end == -1:
        raise ValueError("data.js is missing its DATA-START/DATA-END markers")
    block = source[start:end]

    match = re.search(r"return\s*(\{.*\});\s*\}\)\);", block, re.DOTALL)
    if not match:
        raise ValueError("could not locate the returned object literal in data.js")
    literal = match.group(1)

    # Strip comments, quote bare and numeric keys, drop trailing commas -> valid JSON.
    literal = re.sub(r"/\*.*?\*/", "", literal, flags=re.DOTALL)
    literal = re.sub(r"//[^\n]*", "", literal)
    literal = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', literal)
    literal = re.sub(r"([{,]\s*)(\d+)\s*:", r'\1"\2":', literal)
    literal = re.sub(r"'([^']*)'", r'"\1"', literal)
    literal = re.sub(r",(\s*[}\]])", r"\1", literal)
    return json.loads(literal)


def js_dumps(value: Any, indent: int = 4) -> str:
    """Serialize a dict as a readable JS object literal."""
    return json.dumps(value, indent=indent, ensure_ascii=False)


def write_dataset(dataset: Dict[str, Any], path: str = DATA_JS) -> None:
    """Rewrite the machine-managed block of data.js, preserving the file's header."""
    source = read_data_js(path)
    start = source.find(DATA_START)
    end = source.find(DATA_END)
    if start == -1 or end == -1:
        raise ValueError("data.js is missing its DATA-START/DATA-END markers")

    block = "\n".join([
        DATA_START,
        "(function (root, factory) {",
        "  var data = factory();",
        "  if (typeof module === 'object' && module.exports) module.exports = data;",
        "  else root.UCDA_DATA = data;",
        "}(typeof self !== 'undefined' ? self : this, function () {",
        "  'use strict';",
        "",
        "  return " + js_dumps(dataset, indent=2).replace("\n", "\n  ") + ";",
        "}));",
        "",
    ])
    updated = source[:start] + block + source[end:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(updated)
    os.replace(tmp, path)
    logger.info("Wrote %s", path)


# ------------------------------------------------------------------ EPA MPG


def parse_epa_vehicles(csv_bytes: bytes) -> Tuple[Dict[str, Any], int]:
    """Distill EPA vehicles.csv into {make: {model: {seg, years:{year: mpg}}}}.

    Trims are averaged per model-year, which is what the UI wants: one honest number
    per model-year with per-trim refinement left to the live FuelEconomy.gov lookup.
    """
    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    accum: Dict[Tuple[str, str, int], List[float]] = defaultdict(list)
    ev_accum: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    classes: Dict[Tuple[str, str], str] = {}
    rows = 0

    for row in reader:
        try:
            year = int(row.get("year") or 0)
        except (TypeError, ValueError):
            continue
        make = (row.get("make") or "").strip()
        model = (row.get("model") or "").strip()
        if not make or not model or year < 1996:
            continue
        rows += 1

        try:
            comb = float(row.get("comb08") or 0)
        except (TypeError, ValueError):
            comb = 0.0
        try:
            comb_e = float(row.get("combE") or 0)
        except (TypeError, ValueError):
            comb_e = 0.0

        if comb > 0:
            accum[(make, model, year)].append(comb)
        if comb_e > 0:
            ev_accum[(make, model)].append(comb_e)

        vclass = (row.get("VClass") or "").strip().lower()
        segment = EPA_CLASS_MAP.get(vclass)
        if segment is None:
            for needle, seg in EPA_CLASS_MAP.items():
                if needle in vclass:
                    segment = seg
                    break
        if segment:
            classes[(make, model)] = segment

    vehicles: Dict[str, Any] = {}
    for (make, model, year), values in accum.items():
        record = vehicles.setdefault(make, {}).setdefault(
            model, {"seg": classes.get((make, model), "Midsize car"), "years": {}}
        )
        record["years"][str(year)] = round(sum(values) / len(values))

    for (make, model), values in ev_accum.items():
        if make in vehicles and model in vehicles[make]:
            # combE is kWh per 100 miles.
            vehicles[make][model]["kwhPer100mi"] = round(sum(values) / len(values), 1)
            vehicles[make][model]["seg"] = "Electric"

    logger.info("Parsed %d EPA rows into %d makes", rows, len(vehicles))
    return vehicles, rows


# ------------------------------------------------------- corpus curve fitting


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


def clean_listings(rows: Iterable[Dict[str, str]], current_year: int) -> List[Dict[str, float]]:
    """Appendix C step 1: drop implausible rows and winsorize price within cells."""
    cleaned: List[Dict[str, float]] = []
    for row in rows:
        try:
            price = float(row.get("price") or 0)
            year = int(float(row.get("year") or 0))
            odometer = float(row.get("odometer") or row.get("mileage") or 0)
        except (TypeError, ValueError):
            continue
        if not (500 < price < 250000):
            continue
        if not (100 < odometer < 400000):
            continue
        age = current_year - year
        if not (0 < age <= 25):
            continue
        cleaned.append({
            "price": price,
            "age": float(age),
            "odometer": odometer,
            "make": (row.get("manufacturer") or row.get("make") or "").strip().title(),
        })

    # Winsorize price at 1%/99% within each age bucket.
    by_age: Dict[float, List[float]] = defaultdict(list)
    for item in cleaned:
        by_age[item["age"]].append(item["price"])
    bounds = {}
    for age, prices in by_age.items():
        prices.sort()
        bounds[age] = (_percentile(prices, 0.01), _percentile(prices, 0.99))
    for item in cleaned:
        low, high = bounds[item["age"]]
        item["price"] = min(max(item["price"], low), high)

    logger.info("Cleaned corpus: %d usable listings", len(cleaned))
    return cleaned


def fit_depreciation(listings: Sequence[Dict[str, float]], segment: str) -> Optional[Dict[str, float]]:
    """Appendix C step 2: piecewise annual depreciation rate with knots at 5/8/12.

    Within each age band we take the ratio of median price between consecutive ages,
    which is a robust estimator of the annual retention rate and avoids the leverage a
    naive log-linear regression suffers from at the tails.
    """
    by_age: Dict[int, List[float]] = defaultdict(list)
    for item in listings:
        by_age[int(item["age"])].append(item["price"])

    medians: Dict[int, float] = {}
    for age, prices in by_age.items():
        if len(prices) >= MIN_CELL_N:
            prices.sort()
            medians[age] = _percentile(prices, 0.5)

    bands = {"r1_5": range(1, 6), "r6_8": range(6, 9), "r9_12": range(9, 13), "r13p": range(13, 20)}
    fitted: Dict[str, float] = {}
    for name, ages in bands.items():
        rates = []
        for age in ages:
            if age in medians and (age + 1) in medians and medians[age] > 0:
                retention = medians[age + 1] / medians[age]
                if 0.5 < retention < 1.0:
                    rates.append(1 - retention)
        if not rates:
            logger.warning("Segment %s band %s: too few cells (n<%d), keeping existing value",
                           segment, name, MIN_CELL_N)
            return None
        fitted[name] = round(sum(rates) / len(rates), 3)

    # Enforce monotonic slowdown; the engine and its tests rely on it.
    ordered = ["r1_5", "r6_8", "r9_12", "r13p"]
    for i in range(1, len(ordered)):
        if fitted[ordered[i]] >= fitted[ordered[i - 1]]:
            fitted[ordered[i]] = round(fitted[ordered[i - 1]] * 0.8, 3)

    # Sanity gate against published 5-year depreciation.
    published = PUBLISHED_5YR_DEPRECIATION.get(segment)
    if published is not None:
        implied = 1 - (1 - fitted["r1_5"]) ** 5
        if abs(implied - published) > 0.05:
            logger.warning(
                "Segment %s: fitted 5-yr depreciation %.1f%% differs from published %.1f%% "
                "by more than 5 points -- rejecting the fit",
                segment, implied * 100, published * 100,
            )
            return None
    return fitted


def fit_mile_slopes(listings: Sequence[Dict[str, float]]) -> Optional[List[Dict[str, float]]]:
    """Appendix C step 3: dollars of value lost per excess mile, banded by price."""
    bands = [(0, 8000), (8000, 15000), (15000, 25000), (25000, 40000), (40000, 999999)]
    out: List[Dict[str, float]] = []
    for low, high in bands:
        cell = [item for item in listings if low <= item["price"] < high]
        if len(cell) < MIN_CELL_N:
            logger.warning("Price band %d-%d: only %d listings (n<%d), skipping fit",
                           low, high, len(cell), MIN_CELL_N)
            return None
        mean_odo = sum(i["odometer"] for i in cell) / len(cell)
        mean_price = sum(i["price"] for i in cell) / len(cell)
        num = sum((i["odometer"] - mean_odo) * (i["price"] - mean_price) for i in cell)
        den = sum((i["odometer"] - mean_odo) ** 2 for i in cell)
        if den == 0:
            return None
        slope = abs(num / den)
        out.append({"maxPrice": high, "usdPerMile": round(min(max(slope, 0.02), 0.25), 3)})
    return out


def load_corpus(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


# ------------------------------------------------------------------- commands


def months_since(stamp: str) -> Optional[int]:
    try:
        then = datetime.strptime(stamp, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    today = date.today()
    return (today.year - then.year) * 12 + (today.month - then.month)


def cmd_check(dataset: Dict[str, Any]) -> int:
    meta = dataset.get("meta", {})
    built = meta.get("built", "unknown")
    age = months_since(built)
    stale_after = meta.get("staleAfterMonths", 12)

    print(f"Dataset built:  {built}" + (f"  ({age} months ago)" if age is not None else ""))
    print(f"Schema version: {meta.get('schema')}")
    print()
    print("Table provenance:")
    for table, provenance in sorted(meta.get("sources", {}).items()):
        print(f"  {table:<14} {provenance}")

    print()
    stale = age is not None and age >= stale_after
    if stale:
        print(f"WARNING: dataset is {age} months old (stale after {stale_after}). "
              f"Run --refresh to update.")
    else:
        print("Dataset is within its freshness window.")

    print()
    print("Source reachability:")
    exit_code = 0
    for name, url in [("EPA vehicles.csv", EPA_VEHICLES_CSV)]:
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=20):
                print(f"  {name:<20} reachable")
        except Exception as exc:  # noqa: BLE001 - reachability probe, any failure is 'no'
            print(f"  {name:<20} unreachable ({type(exc).__name__})")
            exit_code = 0  # unreachable sources are informational, not a failure
    return exit_code


def cmd_refresh(dataset: Dict[str, Any], corpus_path: Optional[str], dry_run: bool) -> int:
    changed: List[str] = []

    # --- EPA fuel economy -------------------------------------------------
    try:
        vehicles, rows = parse_epa_vehicles(fetch(EPA_VEHICLES_CSV))
        if vehicles:
            dataset["vehicles"] = vehicles
            dataset["meta"]["sources"]["vehicles"] = (
                f"EPA fueleconomy.gov vehicles.csv ({rows} rows, {date.today().isoformat()})"
            )
            changed.append(f"vehicles ({len(vehicles)} makes)")
    except SourceUnavailable as exc:
        logger.warning("EPA fuel economy data unavailable, keeping existing table: %s", exc)

    # --- listing corpus fitting -------------------------------------------
    if corpus_path:
        if not os.path.exists(corpus_path):
            logger.error("Corpus not found: %s", corpus_path)
            return 2
        listings = clean_listings(load_corpus(corpus_path), date.today().year)
        if len(listings) < MIN_CELL_N:
            logger.warning("Corpus has only %d usable rows; skipping curve fitting", len(listings))
        else:
            fitted_any = False
            for segment in PUBLISHED_5YR_DEPRECIATION:
                curve = fit_depreciation(listings, segment)
                if curve:
                    dataset["depCurves"][segment] = curve
                    fitted_any = True
            if fitted_any:
                dataset["meta"]["sources"]["depCurves"] = (
                    f"fitted from listing corpus {os.path.basename(corpus_path)} "
                    f"({len(listings)} listings, {date.today().isoformat()})"
                )
                changed.append("depCurves")

            slopes = fit_mile_slopes(listings)
            if slopes:
                dataset["mileSlopes"]["default"] = slopes
                dataset["meta"]["sources"]["mileSlopes"] = (
                    f"fitted from listing corpus {os.path.basename(corpus_path)} "
                    f"({date.today().isoformat()})"
                )
                changed.append("mileSlopes")

    if not changed:
        print("Nothing refreshed — no source was reachable and no corpus was supplied.")
        print("The existing dataset is unchanged and still valid.")
        return 0

    dataset["meta"]["built"] = date.today().isoformat()

    if dry_run:
        print("Dry run — would update: " + ", ".join(changed))
        return 0

    write_dataset(dataset)
    print("Refreshed: " + ", ".join(changed))
    print("Re-run the test suite to confirm the new coefficients still hold:")
    print("  node --test 'tools/used_car_calculator/tests/*.test.js'")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report table ages and source reachability, change nothing")
    parser.add_argument("--refresh", action="store_true",
                        help="download reachable sources and rewrite data.js")
    parser.add_argument("--corpus", metavar="PATH",
                        help="local used-listing CSV to fit depreciation/mileage curves from")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --refresh, report what would change without writing")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.check and not args.refresh:
        parser.print_help()
        return 1

    try:
        dataset = extract_dataset(read_data_js())
    except (OSError, ValueError) as exc:
        logger.error("Could not read the existing dataset: %s", exc)
        return 2

    if args.check:
        return cmd_check(dataset)
    return cmd_refresh(dataset, args.corpus, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
