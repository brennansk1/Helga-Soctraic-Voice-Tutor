# Used Car Deal Analyzer — Full Design (for approval)

**Status: DESIGN ONLY — no implementation yet.** This document specifies the complete tool.
Nothing gets built until this design is approved.

**Rev 3** — the vetted candidate sources are now built into the design as concrete,
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

## 9. Open questions for approval

1. **Data architecture OK?** T1 NHTSA/EPA live + T3 baked datasets by default; EIA/FRED and
   Marketcheck/Auto.dev as optional user-keyed enrichment.
2. **Corpus fitting:** OK to fit depreciation/$-per-mile coefficients from the Craigslist
   (Kaggle) + CarGurus (HuggingFace) corpora at build time? (Only fitted coefficients ship;
   raw datasets are never redistributed.)
3. **U.S.-market assumptions** (USD, MPG, U.S. APR/tax norms) — correct for you?
4. Default 5-year TCO horizon and localStorage persistence — OK?

Approve as-is or with changes, and the build proceeds on this branch.

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
