# Used Car Deal Analyzer — Full Design (for approval)

**Status: DESIGN ONLY — no implementation yet.** This document specifies the complete tool.
Nothing gets built until this design is approved.

---

## 1. Goal

A single tool that answers the four questions that actually matter when buying a used car:

1. **Is this a fair price?** — and if not, exactly what to offer and when to walk away.
2. **What will this car *really* cost me?** — total cost of ownership, not sticker price.
3. **Is this a good *car*?** — reliability, remaining life, mileage-for-age, history red flags.
4. **Which of my candidates is the best overall deal?** — side-by-side comparison.

Output is a **0–100 Deal Score** with a full transparent breakdown, a **negotiation playbook**
(opening offer / target / walk-away, all as out-the-door numbers), a **cost projection with
charts**, an automated **red-flag report**, and a **comparison table** across saved candidates.

### Design principles
- **Every number is explainable.** No black box — the score breakdown shows each component,
  its weight, and why it earned what it earned.
- **Best available data, honestly labeled.** The tool ships with embedded U.S. market
  benchmark data (sources in §3). Live per-listing values (KBB/Edmunds comps) come from the
  user, because real comps for *this exact car* beat any national average. The tool makes
  entering 1–3 comps a 30-second step and clearly degrades (and says so) when they're absent.
- **Fully offline, zero dependencies** — matches this repo's offline-first convention. One
  self-contained HTML file; open it in any browser. No data leaves the machine.

---

## 2. User inputs

Three short input sections. Every field has a sensible default or auto-fills from the
embedded benchmarks (marked *auto*), so a minimal run needs only ~5 fields.

### 2.1 The car
| Field | Type | Default / notes |
|---|---|---|
| Nickname | text | auto: "2019 Toyota" — used in the comparison table |
| Brand | dropdown (31 brands) | drives reliability, repair-cost, and lifespan data |
| Vehicle type | dropdown (10 segments) | drives depreciation, MPG, insurance defaults |
| Model year | number | → age |
| Odometer miles | number | |
| Asking price | number | |
| MPG combined | number | *auto* from segment (EVs use ¢/mile electricity instead) |
| Condition | excellent / good / fair / poor | |

### 2.2 History & market comps
| Field | Type | Notes |
|---|---|---|
| Title status | clean / rebuilt-salvage / lemon buyback | hard score caps apply |
| Accident history | none / minor / major / unknown (no report) | |
| Previous owners | number | |
| Service records | yes / partial / no | |
| Market comps ×3 | numbers, optional | KBB/Edmunds/Carvana price for same year-trim-miles |
| Pre-purchase inspection | done / planned / refused | "refused" is a red flag |

### 2.3 Financing & ownership plan
| Field | Type | Default |
|---|---|---|
| Payment method | loan / cash | loan |
| Down payment | number | $3,000 |
| Credit tier | 5 tiers | prime — sets default APR |
| APR % | number | *auto* from tier (Experian averages) |
| Term months | 36/48/60/72/84 | 48 |
| Sales tax % | number | 6.0 |
| Dealer/doc/title fees | number | $500 |
| Annual driving miles | number | 12,000 |
| Years you'll keep it | number | 5 (the TCO horizon) |
| Fuel price $/gal | number | $3.15 |
| Insurance $/yr | number | *auto* from segment |
| Monthly take-home income | optional | enables the 20/4/10 affordability check |

---

## 3. Embedded benchmark data (the "best available data")

All embedded data is U.S.-market averages, 2024–2025, with the source pattern named. It is
versioned in one clearly-marked block at the top of the file so it can be refreshed easily.

| Dataset | Contents | Source pattern |
|---|---|---|
| **Brand table** (31 brands) | avg annual maintenance+repair $ (e.g. Toyota $441, BMW $968, Land Rover $1,174) · reliability score 0–100 (Toyota 90, Jeep 52…) · typical useful life in miles (Toyota 250k, Land Rover 150k…) | RepairPal cost data; Consumer Reports / JD Power dependability rankings; iSeeCars longevity studies |
| **Segment table** (10 types) | baseline annual depreciation rate (trucks 10% … luxury 17%) · avg combined MPG · avg full-coverage insurance $/yr · EV ¢/mile | iSeeCars depreciation studies; EPA; Bankrate/NerdWallet insurance averages |
| **APR by credit tier** | used-vehicle averages: super-prime 7.4%, prime 9.6%, near-prime 14.1%, subprime 18.9%, deep-subprime 21.6% | Experian State of the Automotive Finance Market |
| **Constants** | 12,000 mi/yr valuation norm · $150/yr registration · $1,500 floor value · $0.45/mi cost-per-mile benchmark | FHWA / industry convention |

**Why user-entered comps instead of scraped prices:** the tool is offline and a national
average can't price a specific trim/region/condition. The user pastes 1–3 real comp prices;
the model does the adjustment math. The footer and the results explicitly say accuracy is
much higher with comps, and which parts fall back to neutral without them.

---

## 4. The models (all formulas specified)

### 4.1 Fair value
```
base        = mean(comps)                     — or asking price if no comps (flagged)
expectedMi  = age × 12,000
perMileRate = clamp(base × 0.000005, $0.03, $0.20)      ≈ $0.10/mi on a $20k car
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
opening   = target × 0.96          — leaves room to settle at target
walk-away = fairValue × 1.03
OTD(p)    = p × (1 + taxRate) + fees
```

### 4.3 Depreciation projection (year-by-year forward curve)
```
rate(a) = segmentBaseRate × ageSlowdown       age ≤5: 1.00 · ≤8: 0.75 · ≤12: 0.55 · >12: 0.40
value(t+1) = max(value(t) × (1 − rate), $1,500)
```
Older cars depreciate slower in % terms — this matches observed used-market curves.

### 4.4 Loan
Standard amortization: `payment = P·r / (1 − (1+r)^−n)`, principal = OTD − down.
Produces monthly payment, total interest, and year-end balance series (feeds the
equity chart and negative-equity detection).

### 4.5 Maintenance aging curve
```
cost(year) = brandAnnualCost × clamp(1 + 0.06 × max(age − 3, 0), 1.0, 2.2)
```
Repair costs rise ~6%/yr past age 3, capped at 2.2× — reflects out-of-warranty reality.

### 4.6 Total cost of ownership (over the user's horizon)
```
TCO = depreciation (price − projected value)
    + fuel (miles/MPG × $/gal, or miles × ¢/mi for EVs)
    + insurance
    + maintenance & repairs (aging curve)
    + taxes + fees + registration
    + loan interest
costPerMile = TCO / total miles
```

### 4.7 Affordability — the 20/4/10 rule (only if income given)
≥20% down on OTD · term ≤48 months · (payment + insurance + fuel) ≤10% of take-home.

### 4.8 Deal Score (0–100)

| Component | Weight | Scoring |
|---|---|---|
| Price vs. fair value | 30 | at fair = 21; each −1% under fair ≈ +1.2 pts; **no comps → fixed 18 (neutral, labeled)** |
| Brand reliability & repair cost | 20 | linear on reliability 40→92 |
| Remaining useful life | 15 | linear on % of lifespan miles left |
| Mileage for its age | 10 | full marks ≤12k mi/yr, declining above |
| Financing | 10 | cash = 10; loans scored on APR vs. tier benchmark, −3 for ≥72-mo terms |
| Cost per mile | 10 | full marks ≤ $0.45/mi, declining above |
| History & transparency | 5 | deductions: partial/no records, no history report, refused inspection |

**Hard caps (structural risk beats arithmetic):** rebuilt/lemon title → max 40 ·
major accident → max 55 · <40k miles of life left → max 45.

**Verdict bands:** 80+ Excellent deal · 65–79 Good deal · 50–64 Fair — negotiate ·
35–49 Below average — caution · <35 Walk away.

### 4.9 Red-flag / findings engine
Rule-driven list, sorted critical → warning → good, each with a concrete action:
- **Critical:** salvage/rebuilt title (with the ~40% value math) · lemon buyback · structural damage.
- **Warning:** no history report · refused inspection · price >8% over fair (with your opening
  number) · *suspiciously under* market (−12%: "verify VIN/title — too-good deals usually are") ·
  >18k mi/yr · <5k mi/yr on an old car (odometer-fraud check) · <60k miles of life left ·
  72+ month term · APR >2 pts over tier average (credit-union pre-approval tip) · <10% down
  (negative equity + GAP advice) · expensive-to-run brand (≥$850/yr) · 3+ owners · no records.
- **Good:** below-market price · high-reliability brand · long remaining life · ≥20% down ·
  cash purchase · passed inspection.

---

## 5. Outputs & page layout (top to bottom)

1. **Deal Score card** — hero number (52px), colored meter, verdict with icon + label
   (never color alone), and the full component breakdown table.
2. **Stat tiles ×4** — Out-the-door price · Monthly payment (APR, term, total interest) ·
   N-year TCO (with $/mile) · Estimated life remaining (miles and years at your rate).
3. **Price & negotiation card** — fair value with the adjustment story in plain English,
   then four numbers: fair value / opening offer / target / walk-away (with its OTD), plus
   the 20/4/10 affordability checklist when income is provided.
4. **Chart: Value vs. loan balance** (line, 2 series) — projected value and loan balance by
   year; end-labels, hover crosshair with per-year tooltip incl. equity; a plain-English
   negative-equity note ("you'd owe more than it's worth through year N").
5. **Chart: TCO breakdown** (horizontal bars) — depreciation / fuel / insurance /
   maintenance / taxes+fees / interest, sorted, value at each bar tip; hover shows share-%
   and per-month; subtitle gives total, $/mile, and all-in $/month.
6. **Findings & red flags** — the rule-engine output (§4.9).
7. **Comparison table** — saved candidates (localStorage): score, asking, fair value, OTD,
   monthly, TCO, $/mile, life left, verdict; the best value in each numeric column gets a ★;
   per-row delete + clear-all.
8. **Inspection checklist** — 12 static pre-purchase items (VIN/recall lookup, cold start,
   fluids, paint/panel gaps, tires, electronics, highway test, title match, OTD in writing,
   decline add-ons, arrive pre-approved).
9. **Data provenance footer** — names every embedded source and the "verify with live
   listings; not financial advice" disclaimer.

### Interaction model
"Analyze this deal" runs everything; afterwards, **any input change re-runs live**. "Save to
comparison" snapshots the current analysis. Results appear on the same page (no navigation).

---

## 6. Visual & chart design

Follows the repo dataviz method (validated reference palette):
- **Palette:** series-1 blue `#2a78d6` / series-2 orange `#eb6834` (dark: `#3987e5`/`#d95926`) —
  **validator run: ALL CHECKS PASS in both modes** (CVD ΔE 24.7 light / 26.8 dark, well above
  the ≥8 gate). Status colors (good/warning/serious/critical) reserved for verdict + findings,
  always paired with icon + label.
- **Marks:** 2px lines, ≥8px end-dots with 2px surface ring, bars ≤24px with 4px rounded
  data-end (square at baseline), hairline gridlines, selective direct labels (endpoints only).
- **Both charts ship a hover tooltip layer and a "View as table" fallback** (accessibility).
- **Light + dark mode:** OS preference + manual toggle; charts re-render with the mode's
  validated palette steps. Text never wears series colors.
- Single-hue bars for the TCO chart (one measure across categories); legend only on the
  2-series line chart.

---

## 7. Technical approach

- **One self-contained file:** `tools/used_car_calculator/index.html` (~1,000 lines).
  Vanilla JS + inline SVG charts. No CDN, no build step, no network calls — works from
  `file://` and offline.
- **Persistence:** `localStorage` for the comparison list (versioned key `ucda_saved_v1`).
- All currency/number formatting via `Intl.NumberFormat`; all injected strings HTML-escaped.
- Plus `README.md` documenting the methodology, data vintage, and how to refresh benchmarks.

---

## 8. Optional extensions (not in v1 — pick any to include)

| Extension | What it adds | Cost |
|---|---|---|
| **VIN decode (NHTSA vPIC API)** | auto-fills year/make/model + open recalls from a VIN | requires network; free, no key |
| Per-model data (top ~50 models) | model-level reliability/lifespan instead of brand-level | bigger embedded table |
| Buy-vs-buy financing compare | same car under 2–3 financing scenarios side by side | small |
| Lease-vs-buy module | for CPO/newer cars | medium |
| Export comparison to CSV | share/spreadsheet the candidate table | small |
| Helga integration | serve from web-ui as a tab | touches web-ui service |

---

## 9. Open questions for approval

1. **Scope OK?** v1 = everything in §2–§7, standalone offline file in `tools/`.
2. Any **extensions from §8** you want in v1? (VIN/recall lookup is the highest-value one,
   but breaks the fully-offline property.)
3. **U.S.-market assumptions** (USD, mph/MPG, U.S. APR/tax norms) — correct for you?
4. Default TCO horizon of **5 years** and default comparison via localStorage — OK?

Approve as-is or with changes, and the build proceeds on this branch.
