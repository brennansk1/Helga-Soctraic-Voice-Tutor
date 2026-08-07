# Used Car Deal Analyzer

A self-contained tool that scores a used-car deal 0–100, tells you what to offer and when to
walk away, works out how long the loan should be, projects the full cost of ownership, and
pulls live safety recalls and crash ratings from NHTSA. Everything runs in the browser;
nothing you type leaves the machine.

Feed it a few hundred scraped listings and it becomes a **market scanner**: it scores the
whole cohort, ranks it, and — crucially — stops asking you for comparables and derives them
from the data itself.

## Shopping a whole market

The single-car report's weakest input is comparables, because you have to find them by hand.
With a cohort loaded, that goes away.

**Ask an assistant to go and find the cars.** `AGENTS.md` is the brief it follows — what to
search, what to read, and the exact JSON to produce. Then:

```bash
python3 scrape_listings.py --from-json found.json --query "Camry/Accord under 25k" --merge
```

Reload `index.html` and the **Market** section ranks every one of them. For each car it finds
its peers in the cohort (same model, near year, near mileage, matching title class), adjusts
each peer to that car's own mileage and model year, and prices from there — so a 120k-mile car
is never valued off 40k-mile listings. Then it scores all of them through the same engine the
single-car report uses, with *your* state, financing and driving as the assumptions.

You get three shortlists — best overall, biggest bargains, cheapest to own — a searchable and
sortable table, and a price-against-mileage scatter with the market's own depreciation slope
fitted through it. Click any listing to load it into the full report, comparables and all.

Other ways in: `--from-csv` for a spreadsheet export, `--stdin` for a pipe, the **Load
listings file** button, or paste the JSON straight into the page. No shell required.

`--fetch <url>` will read pages directly and extract schema.org `Vehicle` JSON-LD — the
structured data listing sites publish so machines can read their inventory. It honours
`robots.txt`, waits two seconds between requests and identifies itself honestly. It cannot
help with sites that render listings only in JavaScript; there, have the assistant read the
page and emit JSON. **Complying with each site's terms of use is the operator's
responsibility**, and where a site offers an API (Marketcheck, Auto.dev) prefer it.

## Accessibility & quality

- **WCAG 2.1 AA contrast is enforced by test**, not by eye: a Playwright check walks every
  rendered text node, composites its background through any translucent layers, and fails the
  build if the ratio falls short — in both light and dark themes.
- Visible keyboard focus on every control, a skip link, landmark roles, and polite live
  regions so the score and status changes are announced.
- `prefers-reduced-motion` is honoured throughout.
- Inline, specific validation: bad input blocks the report and says exactly what is wrong,
  rather than being silently coerced.
- An error boundary — if the engine or a renderer throws, you get a clear message and keep
  your inputs, instead of a blank page.
- Works from `file://`, offline, with no build step and no dependencies.

## Design

The report is styled as an **auction condition report** rather than a web app: ruled sections
with stamped numbers down the left margin, underline-only form fields, hairline ledger tables,
and every figure set in monospace so columns of money align the way they do on a printed
sheet. Nothing is a rounded card and nothing floats on a shadow. Warm newsprint by day,
inverted at night. Signal colours are teal and rust — validated for colourblind separation
(ΔE 13.4 light / 12.7 dark against an ≥8 target, ≥3:1 contrast on both surfaces) — with a
separate status ramp that never doubles as a data series. There is a print stylesheet, so
`Print` gives you a clean sheet to take to the dealer.

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
| **Loan length** | every term from 24 to 84 months priced out — payment, lifetime interest, months underwater, share of take-home — with a recommendation, and a solver for "what term gets me to $X/month" |
| **Deal score 0–100** | eight weighted components (below), with hard caps for structural risk |
| **Price position** | a number line placing the ask against your comparables, fair value, target and walk-away |
| **Target price solver** | the price at which this car would earn the next score band |
| **Red flags** | ~25 rules, each with a concrete action, sorted critical → warning → good |
| **At signing** | the full cash picture including a trade-in, and how your state's rules move the sales tax |
| **Coming due** | wear items on the odometer clock — tyres, brakes, belts — with a warning when year one is front-loaded |
| **Uncertainty** | each assumption varied on its own and ranked by how much it swings the total, with a realistic range instead of a false-precision point estimate |
| **Comparison** | saved candidates side by side with the best value marked per column, exportable to CSV |
| **Complaint breakdown** | what owners actually report to NHTSA, by component, so the inspection targets the right systems |
| **The bottom line** | a plain-language paragraph up top: what to offer, what to walk at, what it costs a month, and the two or three things to do next |
| **Market ranking** | every scraped listing scored and ranked on your terms, with comparables derived from the cohort, three shortlists, search, filters, a price-vs-mileage scatter and CSV export |
| **State cost profile** | sales tax and trade-in credit, title fee, registration, insurance level, fuel and electricity prices, inspection cadence, EV surcharge and vehicle property tax — all flowing into the totals |

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

### Method notes worth knowing

- **Comparables are asking prices.** Listings sit above what cars actually transact for, so
  comp-derived fair value is discounted before it is used. Without that haircut the walk-away
  price is biased high — in the direction that costs you money. The toggle in section 02 turns
  it off if you entered real sold prices.
- **Costs are scaled by vehicle class, not just brand.** A Tundra and a Camry are both Toyotas
  and do not cost the same to keep; maintenance, expected service life and wear-item costs are
  all adjusted for the class of vehicle.
- **The score is a weighted judgement, not a measurement.** The weights were chosen, not
  calibrated against outcomes. It is built to rank cars against each other; the report says so
  where the score appears.
- **State rules matter, and there are nine of them.** Picking a state applies its sales tax and
  trade-in credit rule, title fee, annual registration, insurance cost level (Michigan runs
  roughly three times Maine), typical fuel and electricity prices, safety/emissions inspection
  cadence and cost, EV registration surcharge, and annual vehicle property tax where one
  exists. All of it lands in the totals. The table is approximate and every value stays
  editable — confirm your local figures.

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
python3 -m pytest tests/test_used_car_calculator.py -v     # everything (117 tests)
node --test 'tools/used_car_calculator/tests/*.test.js'    # engine + market (188 tests)
```

Three layers: 188 Node tests covering every formula, boundary and failure path in the engine
and the API client (with an injected fetch, so no network is touched); Python tests for the
dataset builder, including a round-trip that proves a refreshed `data.js` is still loadable
by the engine; and 77 headless-Chromium tests that drive the real UI — score rendering, chart
geometry, the loan-term table's monotonicity, the target-payment solver, trade-in tax rules,
the service schedule, sensitivity ordering, the share-link round-trip, phone layout, WCAG
contrast in both themes, keyboard/ARIA affordances, input validation, error recovery, the
print sheet, CSV export, HTML-escaping of user text, and the whole market flow — ranking
order, search, filters, column sort, paste ingestion and opening a listing into the report.

The Playwright tests skip cleanly if Playwright or Chromium is absent.

## Files

| File | Purpose |
|---|---|
| `index.html` | markup and styles |
| `market.js` | cohort comparables, batch scoring, search/filter/sort, market statistics |
| `scrape_listings.py` | listing ingest: JSON/CSV/stdin, robots-aware JSON-LD fetch, dedupe |
| `AGENTS.md` | the brief an assistant follows when browsing for listings |
| `listings.js` / `.json` | the generated listing database (a sample market ships with the tool) |
| `ui.js` | form wiring, SVG charts, live lookups, comparison table |
| `engine.js` | every formula — pure functions, no DOM, no network |
| `sources.js` | live API client with injectable fetch, 6s timeouts, silent fallback |
| `data.js` | the baked dataset (machine-managed between the DATA markers) |
| `build_datasets.py` | dataset refresh and fitting |
| `tests/engine.test.js` | Node unit tests |
| `DESIGN.md` | the full design specification |

## Limitations

U.S. market only (USD, MPG, U.S. tax and APR conventions).

Known gaps, stated plainly:

- **The shipped benchmark tables have never been regenerated from live sources.** They are a
  curated transcription of published figures. `build_datasets.py --refresh` exists and is
  tested, but has not been run against the real EPA and NHTSA feeds — do that first if the
  numbers matter to you.
- **No model-specific failure intelligence.** The complaint chart gives you component
  categories, not "this engine eats head gaskets around 90k". Mining NHTSA complaint text and
  technical service bulletins would close that gap and is the most valuable thing left undone.
- **Insurance is a class average** and is usually the single largest source of uncertainty in
  the total — the sensitivity section will normally tell you so. Get a real quote.
- **Timing belt vs chain is not known per engine**, so that line is flagged conditional rather
  than assumed.

Estimates are decision support, not appraisals. Verify price against live listings, get a real
insurance quote, and have the car inspected before committing. Not financial advice.
