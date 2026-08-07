# Used Car Deal Analyzer — Design

**Status: BUILT (Rev 9).** This document specified the tool; it is now implemented and
tested (328 tests, all passing). See `README.md` for usage, `§10` for where the build
diverged from this spec, and `§11`–`§15` for the redesign, the accuracy work, the
production-quality pass, the market layer and the ingestion rework.

**Rev 4** — as-built. Rev 3 specified the vetted candidate sources as concrete,
implementation-ready specifications: exact endpoints and fields consumed (§3, Appendix A),
the baked-data schema (Appendix B), and the corpus-fitting methodology (Appendix C).

---

## 1. Goal

A single tool that answers the four questions that actually matter when buying a used car:

1. **Is this a fair price?** — and if not, exactly what to offer and when to walk away.
2. **What will this car *really* cost me?** — total cost of ownership, not sticker price.
3. **Is this a good *car*?** — reliability, recalls, complaints, safety, remaining life, history.
4. **Which of my candidates is the best overall deal?** — side-by-side comparison.

Output is a **0–100 Deal Score** with a transparent breakdown, a **negotiation playbook**
(opening offer / target / walk-away, as out-the-door numbers), a **cost projection with
charts**, an automated **red-flag report** (including live open-recall lookup), and a
**comparison table** across saved candidates.

### Design principles
- **Every number is explainable** — the score breakdown shows each component, its weight,
  its data source, and why it earned what it earned.
- **Best public data, by tier.** Live free government APIs where they exist (NHTSA, EPA);
  large downloadable datasets baked in at build time where APIs don't (price/depreciation
  curves fitted from millions of real listings); optional user-keyed APIs for live market
  comps. Every value in the UI is traceable to its tier.
- **Degrades gracefully offline.** The baked datasets make the tool fully functional with
  no network; connectivity only *enriches* (VIN autofill, live recalls, live fuel prices).

---

## 2. User inputs

### 2.1 The car
| Field | Type | Default / notes |
|---|---|---|
| **VIN (optional)** | text | → NHTSA vPIC decode auto-fills year/make/model/trim/engine/fuel type, then pulls recalls, complaints & safety rating (§3 T1) |
| Nickname | text | auto: "2019 Toyota Camry" — used in comparison table |
| Make / Model / Year | dropdowns (from baked EPA dataset) or via VIN | drives per-model MPG, complaint rate, price curve |
| Vehicle type | auto from model, editable | drives depreciation/insurance defaults |
| Odometer miles | number | |
| Asking price | number | |
| MPG combined | *auto* | exact per-model figure from EPA dataset; editable |
| Condition | excellent / good / fair / poor | |

### 2.2 History & market comps
| Field | Type | Notes |
|---|---|---|
| Title status | clean / rebuilt-salvage / lemon buyback | hard score caps apply |
| Accident history | none / minor / major / unknown | |
| Previous owners | number | |
| Service records | yes / partial / no | |
| Market comps ×3 | numbers, optional | live listing prices (KBB/Edmunds/Cars.com/Carvana); auto-fillable via Marketcheck key (§3 T4) |
| Pre-purchase inspection | done / planned / refused | "refused" is a red flag |

### 2.3 Financing & ownership plan
| Field | Type | Default |
|---|---|---|
| Payment method | loan / cash | loan |
| Down payment | number | $3,000 |
| Credit tier | 5 tiers | prime — sets default APR (Experian averages, refreshed at build) |
| APR % | number | *auto* from tier |
| Term months | 36–84 | 48 |
| Sales tax % | number | 6.0 |
| Dealer/doc/title fees | number | $500 |
| Annual driving miles | number | 12,000 |
| Years you'll keep it | number | 5 (TCO horizon) |
| State (optional) | dropdown | → regional fuel price & electricity rate (EIA, live or baked) |
| Fuel price $/gal | *auto* | EIA weekly retail price; editable |
| Insurance $/yr | *auto* | segment average; editable |
| Monthly take-home income | optional | enables 20/4/10 affordability check |

---

## 3. Data architecture — public APIs & downloadable datasets

Four tiers. **T1 + T3 are the backbone** (free, no keys). T2/T4 are optional enrichment.

### T1 — Live free government APIs (no key, called from the browser)

| API | Endpoint | What we use it for |
|---|---|---|
| **NHTSA vPIC VIN decoder** | `vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json` | One-paste autofill: year, make, model, trim, body class, engine, fuel type, plant. CORS-enabled, free, no key (v4.06, current 2026). |
| **NHTSA Recalls** | `api.nhtsa.gov/recalls/recallsByVehicle?make=&model=&modelYear=` | **Open recall list for the exact car** — shown verbatim in red flags ("2 open recalls: airbag inflator…"). |
| **NHTSA Complaints** | `api.nhtsa.gov/complaints/complaintsByVehicle?…` | Model-year complaint count + top component categories (engine/transmission/electrical) — a real per-model reliability signal, blended into the score (§4.10). |
| **NHTSA Safety Ratings (NCAP)** | `api.nhtsa.gov/SafetyRatings/…` | 5-star overall crash rating for the model year — displayed + small score component. |
| **FuelEconomy.gov web services** | `fueleconomy.gov/feg/ws/rest/…` | Per-trim MPG / EPA annual fuel cost when online (baked EPA dataset is the offline fallback — same source data). |

### T2 — Live APIs with a free key (optional; key stored in localStorage)

| API | What it adds |
|---|---|
| **EIA Open Data API v2** (`eia.gov/opendata`, free key) | This week's retail gasoline $/gal (national or by region/state) and residential electricity ¢/kWh — replaces the fuel-price default with today's real number. |
| **FRED API** (free key) | CPI Used Cars & Trucks index — shows whether the used-car market is currently rising or falling, displayed as market-timing context next to the fair-value estimate. |

### T3 — Downloadable datasets, baked at build time

A refresh script `tools/used_car_calculator/build_datasets.py` (run occasionally; uses the
repo's existing Python stack) downloads these, distills them, and emits compact JSON that is
inlined into the tool. This is how multi-GB datasets become an offline-capable single file.

| Dataset | Source | What we distill from it |
|---|---|---|
| **EPA vehicles.csv** (all 1984–present models) | `fueleconomy.gov/feg/epadata/vehicles.csv` | Per-model combined MPG, fuel type, EV efficiency → exact fuel math per car, offline. Also powers the make/model/year dropdowns. |
| **NHTSA complaints & recalls flat files** | NHTSA datasets portal (full downloadable extracts) | Complaints-per-100k-sales rate by make & model-year → per-model reliability adjustment; recall counts as offline fallback. |
| **Used-listing price corpora** — Craigslist used-cars dataset (~426k listings, Kaggle) + CarGurus US listings sample (~4.7M rows, HuggingFace) | Kaggle `austinreese/craigslist-carstrucks-data`; HF `rebrowser/carguruscom-dataset` | Fitted coefficients, not raw data: price-vs-age depreciation curves and price-per-mile slopes **per segment and brand**, estimated from millions of real transactions-adjacent listings. Replaces Rev 1's hand-set depreciation rates and $/mile constants with empirically fitted ones (methodology documented in the README; coefficients carry a data-vintage stamp). |
| **Experian State of the Automotive Finance Market** (published quarterly) | experian.com (report) | APR-by-credit-tier defaults, refreshed each run of the build script. |
| **Brand maintenance-cost & longevity benchmarks** | RepairPal / Consumer Reports / iSeeCars published figures | Brand-level $/yr maintenance and lifespan-miles (no bulk public dataset exists for these; values are cited and versioned in the data block). |

### T4 — Optional paid/keyed APIs (documented, off by default)

| API | What it adds |
|---|---|
| **Marketcheck Cars API** (keyed) | Live comparable listings + predicted market price for the exact YMM/trim/miles/region — auto-fills the comps fields. |
| **Auto.dev Listings API** (keyed, free tier) | Alternative live-comps source. |
| **NMVTIS-approved providers** (e.g. VinAudit, ~$1/report) | Real title-brand/salvage/odometer history. The tool links out and lets the user paste the result into the title/accident fields; no key bundled. |

### Degradation matrix (always visible in the UI footer)

| Condition | Behavior |
|---|---|
| Fully offline | Everything works from baked T3 data; VIN box disabled with explanation; recalls shown from baked counts with "check live before buying" note. |
| Online, no keys | + VIN autofill, live open recalls, complaints, safety rating, per-trim MPG. **This is the expected default mode.** |
| + EIA/FRED keys | + this week's fuel/electricity prices, market-trend context. |
| + Marketcheck/Auto.dev key | + auto-filled live market comps. |

Every displayed number gets a small provenance marker (live / baked / your input).

---

## 4. The models

### 4.1 Fair value
```
base        = mean(comps)                     — or asking price if no comps (flagged)
expectedMi  = age × 12,000
perMileRate = fitted $/mile slope for segment+price band (T3 listing corpora)
mileageAdj  = clamp((expectedMi − odometer) × perMileRate, ±15% of base)

fairValue   = (base + mileageAdj)
              × condition (exc 1.05 · good 1.00 · fair 0.92 · poor 0.82)
              × title     (clean 1.00 · rebuilt/salvage 0.60 · lemon 0.70)
              × accidents (none 1.00 · minor 0.95 · major 0.85 · unknown 0.97)
              × owners    (≥3 owners: 0.97)
              floored at $1,500
```

### 4.2 Negotiation playbook (all shown with out-the-door equivalents)
```
target    = min(asking, fairValue × 0.97)
opening   = target × 0.96
walk-away = fairValue × 1.03
OTD(p)    = p × (1 + taxRate) + fees
```

### 4.3 Depreciation projection
Year-by-year forward curve using the **fitted segment/brand depreciation coefficients**
(T3), with the observed old-car slowdown and a $1,500 floor.

### 4.4 Loan
Standard amortization (payment = P·r / (1 − (1+r)^−n)), principal = OTD − down; yields
payment, total interest, and the year-end balance series for the equity chart and
negative-equity detection.

### 4.5 Maintenance aging curve
`brandAnnualCost × clamp(1 + 0.06 × max(age − 3, 0), 1.0, 2.2)` — costs rise out of
warranty; brand base costs from T3 benchmarks.

### 4.6 Total cost of ownership
Depreciation + fuel (exact EPA MPG × EIA fuel price; EVs: kWh/100mi × electricity rate)
+ insurance + maintenance curve + taxes/fees/registration + loan interest → total, $/mile,
all-in $/month.

### 4.7 Affordability — 20/4/10 rule (if income given)
≥20% down on OTD · term ≤48 months · (payment + insurance + fuel) ≤10% of take-home.

### 4.8 Deal Score (0–100)

| Component | Weight | Data behind it |
|---|---|---|
| Price vs. fair value | 30 | your comps + fitted mileage slopes; no comps → fixed 18, labeled neutral |
| Reliability | 18 | brand baseline **blended with the model-year's NHTSA complaint rate** (§4.10) |
| Remaining useful life | 13 | brand lifespan benchmarks − odometer |
| Mileage for its age | 8 | 12k mi/yr norm |
| Financing | 10 | APR vs. Experian tier average; −3 for ≥72-mo terms; cash = 10 |
| Cost per mile | 10 | full TCO model vs. $0.45/mi benchmark |
| Safety | 6 | NHTSA NCAP overall stars (5★=6 … 3★=2, unrated=3 neutral) |
| History & transparency | 5 | deductions for missing records/report/inspection |

**Hard caps:** rebuilt/lemon title → max 40 · major accident → max 55 · <40k mi life left →
max 45 · **unrepaired open recall on a safety-critical component → max 60 until noted fixed**.

**Verdict bands:** 80+ Excellent · 65–79 Good · 50–64 Fair — negotiate · 35–49 Caution ·
<35 Walk away.

### 4.9 Red-flag / findings engine
Rule-driven, sorted critical → warning → good, each with a concrete action. Includes all
Rev 1 rules (title, accident, inspection-refused, over/under-priced, mileage anomalies,
odometer-fraud check, 72-mo terms, APR above tier, <10% down + GAP advice, expensive
brands, 3+ owners, no records) **plus live-data rules**: open recalls listed by name with
"verify completed repair (free at any dealer) before purchase"; complaint-rate outliers
("this model-year's transmission complaints are 4× the average — inspect specifically");
missing/poor NCAP rating.

### 4.10 Complaint-rate blend (per-model reliability)
```
relScore = 0.6 × brandBaseline(0–100) + 0.4 × modelYearComplaintScore
modelYearComplaintScore = 100 − clamp(complaintsPer100k / p90 × 100, 0, 100)
```
Falls back to brand-only (with a "model-level data unavailable" note) when the model-year
isn't in the baked table and the live API is unreachable.

---

## 5. Outputs & page layout (top to bottom)

1. **Deal Score card** — 52px hero number, colored meter, verdict with icon + label, full
   component breakdown table with per-row data-source markers.
2. **Vehicle identity card** (when VIN/model resolved) — decoded spec line, NCAP stars,
   open-recall banner, complaint-rate sparkline vs. segment average.
3. **Stat tiles ×4** — Out-the-door price · Monthly payment · N-year TCO ($/mile) ·
   Estimated life remaining.
4. **Price & negotiation card** — fair value with the adjustment story in plain English;
   opening / target / walk-away; market-trend note (FRED, if keyed); 20/4/10 checklist.
5. **Chart: value vs. loan balance** (2-series line) — end labels, hover crosshair tooltip
   with equity, negative-equity plain-English note.
6. **Chart: TCO breakdown** (horizontal bars) — sorted categories, value at tip, hover
   share-% and per-month, table fallback.
7. **Findings & red flags** — §4.9 output, recalls listed individually.
8. **Comparison table** — saved candidates (localStorage): score, asking, fair value, OTD,
   monthly, TCO, $/mile, life left, verdict; best-per-column ★; delete/clear.
9. **Inspection checklist** — 12 pre-purchase items (VIN/recall lookup, cold start, fluids,
   panel gaps, tires, electronics, highway test, title match, OTD in writing, decline
   add-ons, arrive pre-approved).
10. **Data provenance footer** — every source + vintage stamp + degradation-mode indicator
    + "not financial advice".

Interaction: "Analyze" runs everything; input changes re-run live; "Save to comparison"
snapshots. Results render on the same page.

---

## 6. Visual & chart design

Follows the validated dataviz reference palette:
- Series-1 blue `#2a78d6` / series-2 orange `#eb6834` (dark `#3987e5`/`#d95926`) —
  **palette validator: ALL CHECKS PASS both modes** (CVD ΔE 24.7 light / 26.8 dark vs. ≥8 gate).
- Status colors reserved for verdict/findings, always icon + label, never color alone.
- 2px lines, ≥8px end-dots with 2px surface rings, ≤24px bars with 4px rounded data-ends,
  hairline grids, selective direct labels; hover tooltips + "view as table" on every chart.
- Light + dark mode (OS preference + manual toggle); charts re-render per mode.

---

## 7. Technical approach

- **`tools/used_car_calculator/index.html`** — self-contained UI + models + baked data
  (vanilla JS, inline SVG, no build step for the app itself). Live T1/T2/T4 calls are
  plain `fetch()` with short timeouts and silent fallback to baked data.
- **`tools/used_car_calculator/build_datasets.py`** — the dataset refresh script: downloads
  T3 sources (EPA CSV, NHTSA flat files, listing corpora), fits the depreciation and $/mile
  coefficients, and rewrites the tagged data block inside `index.html` (or emits
  `data.json`). Includes `--check` mode reporting each source's freshness. Kaggle/HF corpus
  downloads are optional inputs — the script works with any subset and stamps what it used.
- **`tools/used_car_calculator/README.md`** — methodology, source list, data vintage, how
  to refresh, how to add API keys.
- Persistence: localStorage (comparison list `ucda_saved_v1`, API keys `ucda_keys_v1`).
- All injected strings HTML-escaped; `Intl.NumberFormat` everywhere; API keys never leave
  the browser.

---

## 8. Out of scope for v1 (available later)

Lease-vs-buy module · CSV export of the comparison table · per-trim price curves ·
Helga web-ui integration · non-US markets.

---

## 9. Decisions taken at build time

The Rev 3 open questions were resolved as follows when the build was authorized:

1. **Data architecture** — built as specified: T1 NHTSA/EPA live by default, T3 baked
   dataset as the offline baseline, T2/T4 keyed sources optional and off by default.
2. **Corpus fitting** — implemented in `build_datasets.py` as an opt-in `--corpus` flag.
   Only fitted coefficients are written to `data.js`; raw corpora are never redistributed.
   The shipped dataset is the curated baseline (see §10).
3. **U.S.-market assumptions** — retained (USD, MPG, U.S. APR/tax conventions).
4. **5-year default horizon and localStorage persistence** — retained.

---

## 10. As-built notes

Two deliberate divergences from the Rev 3 spec, plus the shipped data's honest status.

### 10.1 Multiple files instead of one self-contained HTML

Rev 3 called for a single self-contained `index.html`. The build splits it into
`index.html` + `data.js` + `engine.js` + `sources.js` + `ui.js`, loaded as classic
`<script src>` tags.

**Why:** the model engine had to be importable by a headless test runner. A single inlined
file cannot be `require()`d, which would have left every formula testable only through the
DOM. The split preserves every property that mattered — no build step, no network, works
directly from `file://`, nothing to install — while making the 103-test engine suite
possible. `data.js` still carries the `DATA-START`/`DATA-END` markers so
`build_datasets.py` rewrites exactly the machine-managed block.

### 10.2 Shipped dataset is curated, not fitted

The environment this was built in blocks egress to `nhtsa.gov` and `fueleconomy.gov`, so
the fitted tables could not be generated at build time. The shipped `data.js` is therefore
a **curated transcription of published figures** — every table stamped as `curated (…)` in
`meta.sources` — rather than the fitted output of Appendix C.

This is a data-vintage limitation, not a functional one: `build_datasets.py --refresh`
is implemented, tested, and will replace those tables with EPA-derived and corpus-fitted
values on any networked machine. The `--check` command reports each table's age and
provenance. The Appendix C fitting code is covered by tests that recover a known
depreciation curve from a synthetic corpus and that reject both thin cells and fits that
disagree with published figures.

One calibration bug was caught by the tests during the build and fixed in the data: the
original segment depreciation curves were too flat for older vehicles (they implied a
12-year-old Camry retaining ~$13.3k). The curves were re-derived against published
five-year segment depreciation, and a regression test now pins residual values to a
realistic band.

### 10.3 Test coverage as built

| Layer | Count | What it covers |
|---|---|---|
| Engine + sources (Node) | 103 | every formula in §4, all score caps, boundary conditions, a full input sweep asserting the score stays in 0–100 and nothing goes NaN, plus all eleven Appendix A endpoints against an injected fetch (success, HTTP error, network failure, and missing-key paths) |
| Dataset builder (pytest) | 22 | dataset extraction, provenance stamps, curve ordering, the EPA parser, corpus cleaning and curve fitting, the sanity gates, and a round-trip proving a rewritten `data.js` is still loadable by the engine |
| UI (headless Chromium) | 15 | score and chart rendering, breakdown maxima summing to 100, live recomputation on input change, the salvage-title cap, cash-vs-loan series and legend suppression, dark-mode redraw, comparison persistence, VIN validation, and HTML-escaping of user-supplied text |

Run: `python3 -m pytest tests/test_used_car_calculator.py -v`

---

## Appendix A — Concrete source specifications (the built-in candidates)

Each entry: exact call, the fields we consume, where they flow, and the failure behavior.
All live calls use `fetch()` with a 6s timeout; any failure silently falls back to baked
data and flips that value's provenance marker to "baked".

### A1 · NHTSA vPIC — VIN decode (T1)
```
GET https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{VIN}?format=json
```
Consume from `Results[0]`: `ModelYear`, `Make`, `Model`, `Trim`, `BodyClass`, `FuelTypePrimary`,
`ElectrificationLevel`, `DisplacementL`, `TransmissionStyle`, `DriveType`, `PlantCountry`.
→ Auto-fills §2.1; `BodyClass` maps to our segment via a fixed lookup table; partial decodes
(vPIC returns blanks, not errors) fill what they can and leave the rest editable.
`ErrorCode != 0` → show "VIN not recognized — fill fields manually".

### A2 · NHTSA Recalls (T1)
```
GET https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}
```
Consume per result: `Component`, `Summary`, `Consequence`, `Remedy`, `NHTSACampaignNumber`,
`ReportReceivedDate`. → Vehicle identity card banner + one red-flag entry per recall with
campaign number; safety-critical components (air bags, brakes, steering, fuel system,
seat belts) trigger the score cap of 60 (§4.8) until the user marks "repair verified".
Baked fallback: recall *count* per make/model-year from the T3 flat file, with a
"check live at nhtsa.gov/recalls before buying" note.

### A3 · NHTSA Complaints (T1)
```
GET https://api.nhtsa.gov/complaints/complaintsByVehicle?make={make}&model={model}&modelYear={year}
```
Consume: total `count`, and per-complaint `components` — aggregated client-side into the
top-3 component categories. → Complaint-rate blend (§4.10) and the "complaints are N× the
segment average for {component}" warning rule. Baked fallback: per-model-year complaint
counts table (T3).

### A4 · NHTSA Safety Ratings / NCAP (T1)
```
GET https://api.nhtsa.gov/SafetyRatings/modelyear/{year}/make/{make}/model/{model}
GET https://api.nhtsa.gov/SafetyRatings/VehicleId/{id}          (first result's VehicleId)
```
Consume: `OverallRating` (1–5 or "Not Rated"), `OverallFrontCrashRating`,
`OverallSideCrashRating`, `RolloverRating`. → Stars on the identity card; Safety score
component (§4.8): 5★→6, 4★→4.5, 3★→2, ≤2★→0, Not Rated→3 (neutral, labeled).

### A5 · FuelEconomy.gov web services (T1) + vehicles.csv (T3)
```
GET https://www.fueleconomy.gov/feg/ws/rest/vehicle/menu/options?year=&make=&model=
GET https://www.fueleconomy.gov/feg/ws/rest/vehicle/{id}        (Accept: application/json)
Bulk: https://www.fueleconomy.gov/feg/epadata/vehicles.csv
```
Consume (both paths carry the same schema): `comb08` (combined MPG), `fuelType1`,
`combE` (EV kWh/100mi), `fuelCost08`, `VClass`. → Exact fuel math in §4.6; `VClass` →
segment mapping. The CSV is the offline source of the same records — the build script
distills it to `{year, make, model → comb08, fuelType, combE, VClass}` (~one compact row
per model-year, trim-averaged). Live per-trim lookup refines it when online.

### A6 · EIA Open Data v2 (T2, free key)
```
GET https://api.eia.gov/v2/petroleum/pri/gnd/data/?api_key={k}&frequency=weekly
    &data[0]=value&facets[product][]=EPMR&facets[duoarea][]=NUS&sort[0][column]=period
    &sort[0][direction]=desc&length=1
```
Consume: latest `value` = US regular retail $/gal (state-level via `duoarea` when the user
picks a state; falls back to national). Electricity: `/v2/electricity/retail-sales/data/`
with `facets[sectorid][]=RES` and the user's state → ¢/kWh for EV math. → Replaces the
fuel-price default; marker flips to "live". No key → baked national averages.

### A7 · FRED (T2, free key)
```
GET https://api.stlouisfed.org/fred/series/observations?series_id=CUSR0000SETA02
    &api_key={k}&file_type=json&sort_order=desc&limit=13
```
Consume: last 13 monthly observations of CPI *Used Cars and Trucks* → 12-month trend
("used prices are down 2.1% over the last year — time is on your side") next to the
fair-value estimate. Cosmetic context only; never enters the score.

### A8 · Listing corpora (T3, build-time only — never shipped raw)
- Kaggle `austinreese/craigslist-carstrucks-data` (~426k rows): `price, year, manufacturer,
  model, odometer, condition, title_status, state`.
- HuggingFace `rebrowser/carguruscom-dataset` (~4.7M rows sample): price, mileage, YMM,
  deal-rating fields.
→ Input to the fitting procedure in Appendix C. Downloaded manually or via the script's
`--corpus <path>` flag (Kaggle requires a logged-in download; the script never embeds
credentials). The script works with either corpus alone and stamps which it used.

### A9 · NHTSA flat files (T3)
Full complaints/recalls extracts from NHTSA's datasets portal (`static.nhtsa.gov/odi/ffdd/`)
→ per-make/model-year complaint counts and recall counts, normalized per Appendix C.

### A10 · Experian / RepairPal / CR / iSeeCars published figures (T3, manual refresh)
No bulk public source exists for APR-by-tier, brand maintenance $/yr, or lifespan miles.
These stay as a hand-maintained, cited, date-stamped table in the data block; the build
script's `--check` mode prints their age and nags past 12 months.

### A11 · Marketcheck / Auto.dev (T4, user key)
```
Marketcheck: GET https://mc-api.marketcheck.com/v2/search/car/active?api_key={k}
             &year={y}&make={m}&model={mo}&miles_range={lo}-{hi}&radius=100&zip={zip}
Auto.dev:    GET https://auto.dev/api/listings?apikey={k}&year_min=&year_max=&make=&model=
```
Consume: median + IQR of listed prices for matching year/model/miles/region → auto-fills
the three comp fields (user can override). Keys live only in localStorage; a missing key
simply leaves comps manual.

---

## Appendix B — Baked data block schema

`build_datasets.py` emits one JSON object, inlined into `index.html` between
`/* ==DATA-START== */` and `/* ==DATA-END== */` markers (or `data.json` side-file):

```json
{
  "meta":     { "built": "2026-08-05", "sources": { "epa_csv": "2026-07-…", "nhtsa_ffdd": "…",
                "corpus": "cargurus-sample@2026-02", "manual_tables": "2026-08" } },
  "vehicles": { "Toyota": { "Camry": { "2019": { "mpg": 34, "fuel": "gas", "vclass": "Midsize" } } } },
  "brands":   { "Toyota": { "maintPerYear": 441, "relBaseline": 90, "lifeMiles": 250000 } },
  "segments": { "Midsize": { "insPerYear": 1750 } },
  "depCurves":{ "Midsize": { "r1_5": 0.128, "r6_8": 0.094, "r9_12": 0.071, "r13p": 0.052 },
                "brandAdj": { "Toyota": -0.013, "Land Rover": 0.024 } },
  "mileSlopes": { "Midsize": [ { "band": [0, 15000], "usdPerMile": 0.09 } ] },
  "complaints": { "Toyota|Camry|2019": { "count": 61, "per100k": 14.2, "top": ["ELECTRICAL"] } },
  "recallCounts": { "Toyota|Camry|2019": 3 },
  "aprByTier": { "superprime": 7.4, "prime": 9.6, "nearprime": 14.1, "subprime": 18.9, "deepsub": 21.6 },
  "energy":   { "gasUsdPerGal": 3.15, "elecUsdPerKwh": 0.17 },
  "constants": { "stdMilesPerYear": 12000, "regPerYear": 150, "floorValue": 1500, "cpmBenchmark": 0.45 }
}
```
The `vehicles` tree also powers the make/model/year dropdowns. Target inlined size:
≤ 1.5 MB minified (model list trimmed to 1996+ — OBD-II era — with a manual-entry escape
hatch for older cars).

---

## Appendix C — Corpus fitting methodology (build-time)

Run inside `build_datasets.py` (pandas; deterministic, seeded):

1. **Clean:** drop rows with price < $500 or > $250k, odometer < 100 or > 400k,
   missing year/make; dedupe by VIN/listing-id where present; winsorize price at 1%/99%
   within each (segment, year) cell.
2. **Depreciation curves:** for each segment, regress `log(price)` on vehicle age with
   piecewise-linear knots at 5, 8, and 12 years, controlling for odometer deviation from
   `age × 12k`. Slope per piece → `depCurves.{seg}.r*`. Brand adjustment = brand fixed-effect
   on the residual, clamped to ±3%/yr → `depCurves.brandAdj`.
3. **$/mile slopes:** within (segment × price band), coefficient of odometer on price for
   listings of the same model-year → `mileSlopes`, clamped to [$0.02, $0.25]/mile.
4. **Complaint normalization:** complaints per model-year ÷ approximate sales weight
   (corpus listing frequency as the proxy denominator) → `per100k`-style rate; p90 within
   segment defines the score scale (§4.10).
5. **Validation gate:** the script refuses to emit coefficients from cells with n < 300
   listings (falls back to the parent segment) and prints an R² report; fitted values are
   sanity-checked against published iSeeCars segment depreciation figures (±5 pts) before
   the data block is replaced.

Raw corpora never ship — only the fitted coefficient tables above.

---

## 11. Rev 5 — visual identity and full data integration

Two changes on top of the built spec: a from-scratch visual identity, and closing the gap
between data the tool *fetched* and data it actually *showed*.

### 11.1 The design: an auction condition report

The original build used a conventional app idiom — rounded cards on an off-white surface,
blue accent, sans-serif throughout. Rev 5 replaces it with a document idiom drawn from what
a used car is actually bought off: a window sticker, an auction sheet, an inspection form.

| Element | Treatment |
|---|---|
| Structure | Numbered sections (`01`…`14`) stamped in a left margin gutter, separated by 2px ink rules. No cards, no rounded corners, no shadows. |
| Figures | Every number in monospace with tabular figures, so money columns align like a printed ledger. |
| Type | Sans for prose; uppercase letterspaced monospace for labels and micro-copy. |
| Form fields | Underline-only, no boxes — a form to be filled in, not a UI to be operated. |
| Score | A bordered "grade stamp" block with a 66px monospace figure and a five-segment band strip beneath. |
| Findings | Ruled list with monospace `[✓] [!] [×]` marks rather than coloured pills. |
| Surfaces | Warm newsprint `#f2efe6` / warm ink `#14150f` inverted at night. |
| Series colours | Teal `#008f80` / rust `#cc4a15` (dark: `#0fa892` / `#e06a35`). Validator: **all checks pass in both modes** — CVD ΔE 13.4 light, 12.7 dark against an ≥8 target; ≥3:1 contrast on both surfaces. |
| Print | A print stylesheet drops the input sections and the chrome, leaving a one-page sheet to take to the dealer. |

Status colours (ok / warn / bad) remain a reserved ramp that never doubles as a data series,
and are always paired with a mark or word, never carrying meaning by colour alone.

### 11.2 Loan-length calculation (§08)

Added on request. Every term from 24 to 84 months is priced out side by side: monthly
payment, lifetime interest, interest as a share of the amount financed, **how many months
the loan stays underwater** (computed at monthly resolution against the depreciation curve,
via `valueAtMonth`), and — when income is known — the all-in share of take-home pay.

- `loanTermComparison()` returns the option set plus a recommendation: the shortest term
  that clears the 20/4/10 payment share and stays at or under 60 months. When no term
  clears it, the tool says so plainly rather than recommending a longer one.
- `termForPayment()` inverts the amortization formula for "what term gets me to $X/month",
  and returns null — with an explanation — when the payment would not even cover interest.
- Clicking a row adopts that term and re-runs the report.

### 11.3 Data that was fetched but not shown

Every field the source layer retrieves is now surfaced:

| Data | Where it now appears |
|---|---|
| vPIC body, engine, drive, transmission, electrification, plant | Full spec grid in §05 |
| NCAP frontal / side / rollover sub-ratings | Star rows in §05 (previously only the overall rating) |
| Recall summary, consequence, remedy, campaign number | Expandable per-recall entries with a safety-critical flag |
| Per-recall repair confirmation | A checkbox per campaign, feeding the score cap (previously one global toggle) |
| Complaint counts by component | New §11 bar chart of the top eight systems |
| FRED 13-month CPI series | Sparkline in §05 (previously only the first and last observation were used) |
| EIA period and region | Named in the colophon and field hints |
| Baked `recallCounts` | Now the offline recall warning — the table shipped in `data.js` was previously unused |

New derived views: a **price-position number line** (§07) placing the ask against your
comparables, fair value, target and walk-away; a **target-price solver** naming the price at
which the car would reach the next score band; a **cumulative-cost chart** (§10) showing
running spend and the per-mile figure improving as fixed costs amortize; and **CSV export**
of the comparison table.

### 11.4 Bug found and fixed during the redesign

Re-running the report rewrote every section's `innerHTML`. If the user edited a field and
then clicked, the blur-driven change re-rendered the section *between mousedown and
mouseup* — the node under the cursor was replaced, so the browser emitted no click event at
all and the interaction was silently swallowed. Sections now only rebuild when their content
actually changed (compared against the last string written, since the browser re-serializes
`innerHTML` and a naive comparison never matches), the price-chart legend renders into a
fixed mount instead of being removed and re-inserted, and the term table uses a delegated
listener. Covered by `test_ui_click_survives_an_edit_in_another_field`.

---

## 12. Rev 6 — accuracy, missing costs, and honesty about uncertainty

Rev 6 answers the question "is this as useful as it could be?" with the gaps that mattered.

### 12.1 Accuracy corrections

| Problem | Fix |
|---|---|
| Comps are **asking** prices; cars transact below ask, so fair value — and the walk-away price — was biased high, in the direction that costs the buyer money | `data.compBias.askingToTransaction` haircut applied to comp-derived value only (never to the seller's own ask, which is not evidence), disclosed in the findings and toggleable for real sold prices |
| Brand-level maintenance and lifespan applied to every model — a Tundra costed like a Camry | `segmentFactors` scales maintenance, expected service life and wear-item costs by vehicle class; EVs drop engine-only items entirely |
| Registration was a flat national constant and vehicle property tax did not exist | Both come from the state table when a state is picked |

### 12.2 Costs that were missing

- **Trade-in** (`purchaseCosts`): allowance, payoff, and the sales-tax treatment. Most states
  tax only the price difference — worth real money and invisible on the sticker. Negative
  equity on the trade is rolled into the new loan and raised as a critical finding, because
  that is the trap that quietly turns a good deal into a bad one.
- **State rules**: approximate combined sales tax, registration, annual vehicle property tax,
  and whether a trade-in reduces the taxable amount. Every value is an editable default
  carrying a "confirm locally" note rather than an assertion.
- **Wear items** (`serviceOutlook`): tyres, brakes, fluids, plugs, belt, struts on the
  odometer clock, costed by class. Presented as a *breakdown of* the maintenance budget, not
  added on top of it — double counting would be worse than the omission. When year one
  exceeds the smooth annual budget by 25%, that becomes a negotiating point.
- **Dealer fees**: flagged above $800, noting they are negotiable in practice, without
  asserting specific legal caps that could not be verified offline.

### 12.3 Honesty about what is known

- **`sensitivity()`** varies insurance, repair costs, fuel, mileage, price and APR
  independently, ranks them by how much each swings the total, and reports a range rather
  than a point estimate. Insurance and repair costs — the two class averages — normally top
  the list, which is exactly the message the buyer needs.
- **The score now says what it is** in the report itself: a weighted judgement with chosen,
  uncalibrated weights, built to rank cars against each other rather than to judge one.

### 12.4 Workflow

Inputs persist to local storage and survive a reload; `Copy link` encodes the entire form
into the URL so a report can be sent to someone else; `Reset` clears both. Charts redraw at a
narrower geometry below 700px and the page no longer scrolls sideways on a phone — which is
where this tool is actually used, standing next to the car.

### 12.5 Still open

The shipped dataset has still never been regenerated from live EPA/NHTSA feeds (egress is
blocked in the build environment), and there is no model-specific failure intelligence —
mining NHTSA complaint text and TSBs for "this engine eats head gaskets at 90k" remains the
highest-value work left.

**Test coverage: 216 — 148 Node, 69 pytest (47 headless-browser).**

---

## 13. Rev 7 — production quality

### 13.1 Accessibility, measured rather than claimed

An audit of the palette found a real defect: `--ink-3` — used for every hint, caption,
provenance label and axis tick — measured **3.13:1 on paper and 2.88:1 on panels**, below the
4.5:1 WCAG AA floor for body text. The success green also failed at 4.38:1 on panel
backgrounds. Both are fixed, and the fix is now defended by a test rather than a promise:
`test_ui_text_meets_wcag_contrast` walks every rendered text node in the live page,
composites its background through any translucent layers (an earlier version of the check
read a 14%-alpha wash as opaque and produced false positives), and fails if the ratio falls
short. It runs in both themes.

| Token | Was | Now | On paper / panel |
|---|---|---|---|
| `--ink-3` | `#8b8779` | `#676354` | 5.23 / 4.82 |
| `--warn` (as text) | `#a86a00` | `#845200` | 5.73 / 5.28 |
| `--ok` (as text) | `#0f7a2e` | `#0d6b28` | 5.80 / 5.34 |
| dark `--ink-3` | `#77746a` | `#928f82` | 5.66 / 5.23 |

New `--field-line` (3.13:1) carries form-control boundaries, which need 3:1 rather than 4.5:1,
and `--s1-ink` is a darkened teal for the places the series colour was being used as text.

Also added: visible `:focus-visible` on every control, a skip link, landmark roles, polite
live regions announcing the score and status, `prefers-reduced-motion` support, and an
accessible-name check over every input.

### 13.2 Robustness

- **Inline validation** with specific messages. Bad input blocks the report, marks the field
  `aria-invalid`, scrolls to it and announces the problem — instead of being silently coerced
  into something plausible.
- **An error boundary** around both the analysis and the render phase. A thrown exception now
  produces a clear panel that keeps every input, rather than a half-rendered or blank page.
  Tested by injecting a synthetic engine failure and asserting recovery.
- **A real busy state** on the primary action while the network lookups run.

### 13.3 State-level completeness

The state table went from four fields to eleven. Selecting a state now applies its sales tax
and trade-in credit rule, one-off title fee, annual registration, **insurance cost index**
(Michigan ≈ 1.9× national, Maine ≈ 0.55× — one of the largest lines in the total), typical
fuel and electricity prices, safety/emissions inspection cadence and cost, EV registration
surcharge, and annual vehicle property tax. All of it flows into the cost of ownership and
the cumulative curve, and the reconciliation between them is asserted for all fifty states.
Anything typed by hand still wins over a state default.

### 13.4 Usefulness and orientation

- **The bottom line** — a plain-language paragraph at the top of the report: what to open at,
  what to settle at, what to walk away above, what it costs a month all in, which loan term,
  and the two to four things to do next. It leads with "do not buy this car until…" when a
  critical finding exists.
- **A sticky summary bar** carrying score, verdict, opening offer, walk-away price and all-in
  monthly cost while you scroll a long report, with a control back to the inputs.
- **A stated minimum**: four fields, marked in the form, with everything else defaulted.
- **A genuine print sheet** — inputs, chrome and interactive affordances drop out, leaving the
  bottom line, the numbers, the findings and a checklist with room to write.
- Document metadata: description, theme-color for both schemes, and an inline SVG favicon.

**Test coverage: 245 — 159 Node, 86 pytest (64 headless-browser).**

---

## 14. Rev 8 — the market layer

The single-car report's weakest input was always comparables: the user had to find them by
hand, and most people entered one or none, which left the price component of the score
neutral. Rev 8 fixes that by changing the unit of work from one car to a market.

### 14.1 Division of labour

Scraping does not belong in a `file://` page — no server, no CORS, no credentials. So it
happens outside, and the browser only reasons about listings it is handed:

| Stage | Where | What |
|---|---|---|
| Find and read listings | An assistant with web access | Follows `AGENTS.md`; emits JSON in a documented schema |
| Normalise, dedupe, persist | `scrape_listings.py` | Aliases, type coercion, VIN validation, cross-source dedupe; writes `listings.js` (loadable from `file://`) and `listings.json` |
| Comparables, scoring, ranking | `market.js` in the browser | Uses the same `engine.js` as the single-car report — one source of truth for scoring |

Three independent ways in, so no step is a hard dependency: the generated `listings.js`, a
file picker, or a paste box.

### 14.2 Cohort comparables

For each listing, peers are drawn from the cohort — same make and model, within a year and
mileage band, **matching title class** (a branded title is never priced off clean-title
peers, or the reverse). Each peer is then adjusted to the target's own mileage using the
engine's price-per-mile slope and to its model year using the depreciation curve, before
averaging. Without that adjustment a high-mileage car would be valued off low-mileage
listings, which is the exact error the manual-comp workflow invited.

The net widens in three documented steps (±1 year/±20k, ±2/±40k, ±3/±60k) and reports which
one it used; below three peers it declines to guess and says "thin data" in the table.
Cohort comps are still asking prices, so the asking-to-transaction haircut still applies.

### 14.3 Ranking on the buyer's terms

`scoreAll` runs every listing through `analyze()` with the buyer's own state, financing,
driving and horizon — so the ranking answers "which of these is best *for me*", not "which
is best in the abstract". Changing any of those inputs re-scores the whole market. All the
existing machinery applies unchanged: hard caps, findings, cost of ownership, state costs.

Output: three shortlists (best overall, biggest bargains, cheapest to own), a searchable and
sortable table, cohort statistics including a least-squares price-vs-mileage slope — the
market telling you its own depreciation rate — and a scatter plot with that trend line, where
a filled dot is a car priced under the trend for its mileage.

### 14.4 Ethics and terms

`--fetch` extracts schema.org `Vehicle` JSON-LD, which sites publish precisely so machines
can read their inventory. It checks `robots.txt` per origin and refuses disallowed paths,
waits two seconds between requests, and identifies itself honestly. Sites that render
listings only in JavaScript are out of scope for it by design — the assistant reads those.
Terms-of-use compliance is stated as the operator's responsibility in both the module
docstring and the README, and official APIs are recommended where they exist.

### 14.5 Two bugs the existing gates caught

The accessibility and contrast suites written in Rev 7 immediately failed against the new
code, which is what they were for:

- The market table baked **resolved** colours into inline styles, so a theme switch left
  light-mode green on a dark background at 2.75:1. Inline styles now reference the CSS
  custom property, and the market redraws on theme change.
- The hidden file input had no accessible name.

**Test coverage: 305 — 188 Node, 117 pytest (77 headless-browser).**

---

## 15. Rev 9 — no sample data, and ingestion that meets the assistant halfway

### 15.1 The sample market is gone

Rev 8 shipped a 172-listing synthetic market so the feature worked on first open. That was a
mistake: fabricated listings sitting inside a tool whose whole purpose is to tell you what is
true invite exactly the confusion the rest of the report works to avoid, however clearly they
are labelled. `listings.js` and `listings.json` are now git-ignored and never checked in —
the database is built from the user's own searches, and a test asserts neither file is
present in the repository.

The empty state carries the weight instead: a **Copy the prompt** button that puts a complete
brief on the clipboard — seeded with whatever make, model, budget and state are already on the
page — a paste box, and the field list on request.

### 15.2 Ingestion that accepts what models actually produce

Requiring one exact JSON shape pushed cleanup onto the user for no reason. Both the browser
(`market.parseText`) and the CLI (`scrape_listings.parse_text`) now accept:

- a bare array, or a `{"listings": […]}` envelope
- JSON inside a markdown code fence, with or without a language tag
- JSON with a sentence of prose around it (the outermost bracketed span is taken)
- one JSON object per line, comma-terminated or not
- a CSV or TSV block with a header row, quoted fields included

Field names are resolved through an alias table with a case- and punctuation-insensitive
fallback, so `mileage`, `odometer`, `listPrice`, `link`, `modelYear`, `sellerType`,
`days_on_market` and similar all land in the right place. The assistant writes what is
natural; the tool adapts.

### 15.3 The listing URL is first class

`url` is normalised (a bare domain gains a scheme; anything that is not a link becomes
null), the source site is **derived from it** when not supplied — which is what makes
cross-aggregator dedupe work without the assistant having to name the site — and every row in
the ranked table links back to its listing with `rel="noopener noreferrer"`. A test asserts
one link per row, because a shortlist you cannot click through to is half a result.

### 15.4 Testing without shipped data

The market UI tests seed their own cohort through the paste box, which incidentally exercises
the real ingestion path rather than a fixture shortcut. Two testing bugs surfaced and were
fixed: an autouse fixture was starting Chromium for the pure-Python tests, and the
copy-the-prompt test hung because the browser context had no clipboard permission.

**Test coverage: 328 — 202 Node, 126 pytest (86 headless-browser).**
