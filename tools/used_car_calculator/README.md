# Used Car Deal Analyzer

A self-contained tool that scores a used-car deal 0–100, tells you what to offer and when to
walk away, projects the full cost of ownership, and pulls live safety recalls and crash
ratings from NHTSA. Everything runs in the browser; nothing you type leaves the machine.

## Running it

Open `index.html` in any browser — double-click it, or:

```bash
xdg-open tools/used_car_calculator/index.html      # Linux
open tools/used_car_calculator/index.html          # macOS
```

There is no build step and no server. The four scripts (`data.js`, `engine.js`,
`sources.js`, `ui.js`) load as plain classic scripts, which work from `file://`.

Offline it is fully functional from the built-in dataset. Online it additionally decodes
VINs, lists open recalls, reads crash ratings and exact EPA fuel economy — all from free
U.S. government APIs that need no key.

## What it computes

| Output | How |
|---|---|
| **Fair value** | your market comps, adjusted for mileage against the age-expected norm, then for condition, title, accidents and owner count |
| **Negotiation ladder** | opening offer, target, and walk-away price, each with its out-the-door equivalent |
| **Cost of ownership** | depreciation + fuel/electricity + insurance + an age-escalating maintenance curve + taxes/fees + loan interest, as a total, per mile and per month |
| **Loan and equity** | amortization schedule, total interest, and a year-by-year value-vs-balance projection that flags negative equity |
| **Deal score 0–100** | eight weighted components (below), with hard caps for structural risk |
| **Red flags** | ~25 rules, each with a concrete action, sorted critical → warning → good |
| **Comparison** | saved candidates side by side with the best value starred per column |

### Score weights

| Component | Max | Source |
|---|---|---|
| Price vs. fair value | 30 | your comps (18 neutral points if none entered) |
| Reliability | 18 | brand baseline blended 60/40 with the model-year's NHTSA complaint rate |
| Remaining useful life | 13 | brand lifespan benchmark minus odometer |
| Mileage for its age | 8 | against the 12,000 mi/yr norm |
| Financing | 10 | APR vs. your credit tier's average; penalty for 72+ month terms |
| Cost per mile | 10 | the full TCO model vs. a $0.45/mi benchmark |
| Safety | 6 | NHTSA NCAP overall stars (neutral 3 when unrated) |
| History & transparency | 5 | service records, history report, inspection |

**Hard caps** override the arithmetic: a branded title caps the score at 40, a major
accident at 55, under 40k miles of remaining life at 45, and an unrepaired safety-critical
recall at 60 until you confirm the repair.

## Data sources

### Live, free, no key required
| Source | Used for |
|---|---|
| [NHTSA vPIC](https://vpic.nhtsa.dot.gov/api/) | VIN decode → year, make, model, trim, engine, body class |
| [NHTSA Recalls](https://api.nhtsa.gov) | open recall campaigns for the exact model year |
| [NHTSA Complaints](https://api.nhtsa.gov) | owner complaint volume and top components |
| [NHTSA Safety Ratings](https://api.nhtsa.gov) | NCAP overall crash rating |
| [FuelEconomy.gov](https://www.fueleconomy.gov/feg/ws/) | per-trim EPA combined MPG |

### Optional, free key (⚙ Data sources in the UI)
| Source | Used for |
|---|---|
| [EIA Open Data](https://www.eia.gov/opendata/) | this week's retail gasoline price and residential electricity rate, by state |
| [FRED](https://fred.stlouisfed.org/) | CPI Used Cars & Trucks — whether the market is rising or falling |
| [Marketcheck](https://www.marketcheck.com/apis/) | auto-fill live market comps from active listings |

Keys are stored only in your browser's local storage and are sent directly to the provider.

### Built in (offline baseline)
Brand maintenance costs, reliability baselines and lifespan miles (RepairPal, Consumer
Reports / J.D. Power, iSeeCars); segment depreciation curves (iSeeCars), insurance averages
(Bankrate/NerdWallet) and EPA class MPG; used-vehicle APR by credit tier (Experian State of
the Automotive Finance Market); EIA energy price averages; and a subset of EPA per-model fuel
economy.

Every table carries a provenance stamp in `data.js` under `meta.sources`, and the UI marks
each displayed value as live, baked, or your own input.

> **On accuracy:** the shipped tables are a curated transcription of published figures, not a
> statistical fit. Run `build_datasets.py --refresh` (below) on a networked machine to replace
> them with values derived directly from EPA and NHTSA data and, if you supply a corpus, from
> fitted depreciation curves. There is no free public API for "what is this exact car worth
> today" — KBB and Edmunds retired theirs — which is why price comps come from you or from a
> keyed Marketcheck account.

## Refreshing the dataset

```bash
python3 tools/used_car_calculator/build_datasets.py --check
# report each table's age and whether its source is reachable

python3 tools/used_car_calculator/build_datasets.py --refresh
# download EPA vehicles.csv, rebuild the per-model fuel-economy table, rewrite data.js

python3 tools/used_car_calculator/build_datasets.py --refresh --corpus listings.csv
# additionally fit depreciation curves and price-per-mile slopes from a listing corpus
```

Suitable corpora: the Kaggle [craigslist-carstrucks-data](https://www.kaggle.com/datasets/austinreese/craigslist-carstrucks-data)
export (~426k listings) or a CarGurus listings sample. Only fitted coefficients are written
into `data.js` — raw listing data is never copied into the repository.

The fitting procedure cleans implausible rows and winsorizes price within each age bucket,
estimates annual retention from consecutive-age median prices (piecewise, with knots at 5, 8
and 12 years), and refuses any cell with fewer than 300 listings. A fitted curve whose
implied 5-year depreciation differs from published segment figures by more than 5 points is
rejected rather than shipped. Every source degrades independently: whatever is unreachable
leaves its table untouched and is reported.

## Tests

```bash
python3 -m pytest tests/test_used_car_calculator.py -v     # everything (37 tests)
node --test 'tools/used_car_calculator/tests/*.test.js'    # the JS engine alone (103 tests)
```

Three layers: 103 Node tests covering every formula, boundary and failure path in the engine
and the API client (with an injected fetch, so no network is touched); Python tests for the
dataset builder, including a round-trip that proves a refreshed `data.js` is still loadable
by the engine; and 15 headless-Chromium tests that drive the real UI — score rendering, chart
geometry, live recomputation, the salvage-title cap, cash-vs-loan series, dark mode,
comparison persistence and HTML-escaping of user text.

The Playwright tests skip cleanly if Playwright or Chromium is absent.

## Files

| File | Purpose |
|---|---|
| `index.html` | markup and styles |
| `ui.js` | form wiring, SVG charts, live lookups, comparison table |
| `engine.js` | every formula — pure functions, no DOM, no network |
| `sources.js` | live API client with injectable fetch, 6s timeouts, silent fallback |
| `data.js` | the baked dataset (machine-managed between the DATA markers) |
| `build_datasets.py` | dataset refresh and fitting |
| `tests/engine.test.js` | Node unit tests |
| `DESIGN.md` | the full design specification |

## Limitations

U.S. market only (USD, MPG, U.S. tax and APR conventions). Estimates are decision support,
not appraisals — verify price against live listings and get a real insurance quote before
committing. Not financial advice.
