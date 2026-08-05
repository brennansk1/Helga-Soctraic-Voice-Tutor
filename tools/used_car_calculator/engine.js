/* Used Car Deal Analyzer — model engine.
 *
 * Pure functions only: no DOM, no network, no globals. Everything the UI shows is
 * computed here so it can be unit-tested headlessly (see tests/engine.test.js).
 *
 * Implements DESIGN.md section 4:
 *   4.1 fair value          4.6 total cost of ownership
 *   4.2 negotiation         4.7 affordability (20/4/10)
 *   4.3 depreciation        4.8 deal score
 *   4.4 loan amortization   4.9 findings / red flags
 *   4.5 maintenance curve   4.10 complaint-rate reliability blend
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.UCDA_ENGINE = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------------------------------------------------------------- utilities */

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  /** Coerce to a finite number, else return the fallback. */
  function toNum(v, fallback) {
    var n = typeof v === 'string' ? parseFloat(v) : v;
    return isNum(n) ? n : fallback;
  }

  function keyFor(make, model, year) {
    return String(make || '').toUpperCase() + '|' +
           String(model || '').toUpperCase() + '|' + String(year || '');
  }

  /* --------------------------------------------------- 4.1 fair market value */

  /**
   * Dollar-per-mile adjustment rate for a vehicle, from the fitted mileage slopes.
   * Banded by price, with a per-segment override table. Always within [0.02, 0.25].
   */
  function mileageSlope(price, segment, data) {
    var table = (data.mileSlopes && data.mileSlopes[segment]) || data.mileSlopes['default'];
    var slope = table[table.length - 1].usdPerMile;
    for (var i = 0; i < table.length; i++) {
      if (price <= table[i].maxPrice) { slope = table[i].usdPerMile; break; }
    }
    return clamp(slope, 0.02, 0.25);
  }

  var CONDITION_FACTOR = { excellent: 1.05, good: 1.00, fair: 0.92, poor: 0.82 };
  var TITLE_FACTOR     = { clean: 1.00, rebuilt: 0.60, lemon: 0.70 };
  var ACCIDENT_FACTOR  = { none: 1.00, minor: 0.95, major: 0.85, unknown: 0.97 };

  /**
   * Fair value = comp average (or asking as a flagged anchor), adjusted for mileage
   * vs. the age-expected norm, then multiplied by condition / title / accident / owner
   * factors. Floored at the running-car floor value.
   */
  function fairValue(input, data) {
    var comps = (input.comps || []).filter(function (c) { return isNum(c) && c > 0; });
    var hasComps = comps.length > 0;
    var base = hasComps
      ? comps.reduce(function (a, b) { return a + b; }, 0) / comps.length
      : Math.max(input.asking || 0, 0);

    var expectedMiles = input.age * data.constants.stdMilesPerYear;
    var slope = mileageSlope(base, input.segment, data);
    var rawAdj = (expectedMiles - input.miles) * slope;
    // A mileage correction can never dominate the valuation.
    var mileageAdj = clamp(rawAdj, -0.15 * base, 0.15 * base);

    var condition = CONDITION_FACTOR[input.condition] !== undefined ? CONDITION_FACTOR[input.condition] : 1;
    var title     = TITLE_FACTOR[input.title] !== undefined ? TITLE_FACTOR[input.title] : 1;
    var accident  = ACCIDENT_FACTOR[input.accidents] !== undefined ? ACCIDENT_FACTOR[input.accidents] : 1;
    var owners    = (input.owners || 1) >= 3 ? 0.97 : 1.00;

    var value = (base + mileageAdj) * condition * title * accident * owners;
    return {
      value: Math.max(value, data.constants.floorValue),
      base: base,
      hasComps: hasComps,
      compCount: comps.length,
      mileageAdj: mileageAdj,
      expectedMiles: expectedMiles,
      slope: slope,
      factors: { condition: condition, title: title, accident: accident, owners: owners }
    };
  }

  /* ------------------------------------------------------- 4.2 negotiation */

  function outTheDoor(price, taxRate, fees) {
    return price * (1 + taxRate) + fees;
  }

  function negotiation(fair, input) {
    var target = Math.min(input.asking, fair * 0.97);
    return {
      fair: fair,
      opening: Math.min(target * 0.96, input.asking),
      target: target,
      walkAway: fair * 1.03,
      walkAwayOtd: outTheDoor(fair * 1.03, input.taxRate, input.fees)
    };
  }

  /* ------------------------------------------------------ 4.3 depreciation */

  /** Annual depreciation rate at a given age, from the piecewise segment curve. */
  function depRateAt(age, segment, brand, data) {
    var curve = data.depCurves[segment] || data.depCurves['Midsize car'];
    var rate = age <= 5 ? curve.r1_5 : age <= 8 ? curve.r6_8 : age <= 12 ? curve.r9_12 : curve.r13p;
    var adj = (data.depCurves.brandAdj && data.depCurves.brandAdj[brand]) || 0;
    return clamp(rate + clamp(adj, -0.03, 0.03), 0.01, 0.40);
  }

  /** Forward value curve: index 0 is today, index t is t years out. */
  function projectValue(price, age, segment, brand, years, data) {
    var out = [price];
    var v = price;
    for (var t = 1; t <= years; t++) {
      v = Math.max(v * (1 - depRateAt(age + t, segment, brand, data)), data.constants.floorValue);
      out.push(v);
    }
    return out;
  }

  /* --------------------------------------------------------------- 4.4 loan */

  /**
   * Standard amortization. Returns the monthly payment, lifetime interest, and the
   * balance at each year boundary (index 0 = at signing).
   */
  function loanSchedule(principal, aprPercent, months) {
    if (!(principal > 0) || !(months > 0)) {
      return { payment: 0, totalInterest: 0, balanceByYear: [0], months: months || 0 };
    }
    var r = aprPercent / 100 / 12;
    var payment = r === 0
      ? principal / months
      : principal * r / (1 - Math.pow(1 + r, -months));

    var balance = principal, interest = 0;
    var byYear = [principal];
    for (var m = 1; m <= months; m++) {
      var interestPart = balance * r;
      interest += interestPart;
      balance = Math.max(balance - (payment - interestPart), 0);
      if (m % 12 === 0) byYear.push(balance);
    }
    if (months % 12 !== 0) byYear.push(balance);
    return { payment: payment, totalInterest: interest, balanceByYear: byYear, months: months };
  }

  /* -------------------------------------------------------- 4.5 maintenance */

  /** Repair spend rises ~6%/yr past age 3 (out of warranty), capped at 2.2x. */
  function maintenanceCost(brand, startAge, years, data) {
    var brandData = data.brands[brand] || data.brands['Other'];
    var perYear = [], total = 0;
    for (var t = 0; t < years; t++) {
      var factor = clamp(1 + 0.06 * Math.max(startAge + t - 3, 0), 1, 2.2);
      var cost = brandData.perYear !== undefined ? brandData.perYear : brandData.maintPerYear;
      cost = cost * factor;
      perYear.push(cost);
      total += cost;
    }
    return { total: total, perYear: perYear };
  }

  /* ----------------------------------------------------------- 4.6 fuel/TCO */

  /** Look up the per-model EPA record, if we have one. */
  function vehicleRecord(make, model, year, data) {
    var byMake = data.vehicles[make];
    if (!byMake) return null;
    var rec = byMake[model];
    if (!rec) return null;
    var mpg = rec.years && rec.years[year];
    if (mpg === undefined && rec.years) {
      // nearest available model-year
      var yrs = Object.keys(rec.years).map(Number).sort(function (a, b) {
        return Math.abs(a - year) - Math.abs(b - year);
      });
      if (yrs.length) mpg = rec.years[yrs[0]];
    }
    return { seg: rec.seg, mpg: mpg, kwhPer100mi: rec.kwhPer100mi };
  }

  /** Annual energy cost — gasoline or electricity, whichever the vehicle uses. */
  function annualFuelCost(input, data) {
    var seg = data.segments[input.segment] || {};
    var isEV = input.isEV !== undefined ? input.isEV
      : (input.segment === 'Electric' || seg.evKwhPer100mi !== undefined);
    if (isEV) {
      var kwh = toNum(input.kwhPer100mi, seg.evKwhPer100mi || 33);
      var rate = toNum(input.elecUsdPerKwh, data.energy.elecUsdPerKwh);
      return input.annualMiles / 100 * kwh * rate;
    }
    var mpg = toNum(input.mpg, seg.mpg || 25);
    if (!(mpg > 0)) return 0;
    return input.annualMiles / mpg * toNum(input.gasUsdPerGal, data.energy.gasUsdPerGal);
  }

  function totalCostOfOwnership(input, data, loan) {
    var years = input.horizon;
    var values = projectValue(input.asking, input.age, input.segment, input.brand, years, data);
    var depreciation = input.asking - values[years];
    var fuelPerYear = annualFuelCost(input, data);
    var fuel = fuelPerYear * years;
    var insurance = toNum(input.insurance, (data.segments[input.segment] || {}).insPerYear || 1750) * years;
    var maintenance = maintenanceCost(input.brand, input.age, years, data);
    var taxesFees = input.asking * input.taxRate + input.fees + data.constants.regPerYear * years;
    var interest = loan ? loan.totalInterest : 0;
    var total = depreciation + fuel + insurance + maintenance.total + taxesFees + interest;
    var totalMiles = input.annualMiles * years;

    return {
      values: values,
      depreciation: depreciation,
      fuel: fuel,
      fuelPerYear: fuelPerYear,
      insurance: insurance,
      maintenance: maintenance.total,
      maintenancePerYear: maintenance.perYear,
      taxesFees: taxesFees,
      interest: interest,
      total: total,
      totalMiles: totalMiles,
      costPerMile: totalMiles > 0 ? total / totalMiles : 0,
      costPerMonth: total / (years * 12)
    };
  }

  /* --------------------------------------------------- 4.7 affordability */

  /** The 20/4/10 rule: 20% down, <=4 year term, <=10% of take-home all-in. */
  function affordability(input, otd, monthlyPayment, tco) {
    if (!(input.income > 0)) return null;
    var monthlyAllIn = monthlyPayment
      + (tco.insurance / input.horizon / 12)
      + (tco.fuelPerYear / 12);
    var downPct = otd > 0 ? input.down / otd : 1;
    return {
      downPct: downPct,
      downOk: downPct >= 0.20,
      termOk: !input.financed || input.termMonths <= 48,
      share: monthlyAllIn / input.income,
      shareOk: monthlyAllIn <= input.income * 0.10,
      monthlyAllIn: monthlyAllIn
    };
  }

  /* ------------------------------------------- 4.10 reliability blend */

  /**
   * Blend the brand baseline with this model-year's NHTSA complaint rate.
   * Live complaint data (when present) supersedes the baked table. Falls back to
   * brand-only, flagged, when neither is available.
   */
  function reliabilityScore(input, data, live) {
    var brandData = data.brands[input.brand] || data.brands['Other'];
    var baseline = brandData.relBaseline;
    var key = keyFor(input.brand, input.model, input.year);
    var rec = (live && live.complaints) || data.complaints[key] || null;
    if (!rec) {
      return { score: baseline, blended: false, baseline: baseline, source: 'brand-only' };
    }
    var p90 = data.complaintSegP90[input.segment] || data.complaintSegP90['default'];
    var rate = isNum(rec.per100k) ? rec.per100k
      : (isNum(rec.count) ? rec.count / 10 : null);   // crude proxy if only a count is known
    if (rate === null) {
      return { score: baseline, blended: false, baseline: baseline, source: 'brand-only' };
    }
    var complaintScore = 100 - clamp(rate / p90 * 100, 0, 100);
    return {
      score: 0.6 * baseline + 0.4 * complaintScore,
      blended: true,
      baseline: baseline,
      complaintScore: complaintScore,
      rate: rate,
      p90: p90,
      top: rec.top || [],
      source: (live && live.complaints) ? 'live' : 'baked'
    };
  }

  /* ------------------------------------------------------------ 4.8 safety */

  function safetyPoints(live) {
    var stars = live && live.safety && isNum(live.safety.overallRating) ? live.safety.overallRating : null;
    if (stars === null) return { points: 3, stars: null, rated: false };  // neutral when unrated
    if (stars >= 5) return { points: 6,   stars: stars, rated: true };
    if (stars >= 4) return { points: 4.5, stars: stars, rated: true };
    if (stars >= 3) return { points: 2,   stars: stars, rated: true };
    return { points: 0, stars: stars, rated: true };
  }

  /* --------------------------------------------------------- 4.8 deal score */

  function isSafetyCritical(component, data) {
    var c = String(component || '').toUpperCase();
    return data.constants.safetyCriticalComponents.some(function (sc) {
      return c.indexOf(sc) !== -1;
    });
  }

  function dealScore(ctx) {
    var input = ctx.input, data = ctx.data, live = ctx.live || {};
    var parts = [];

    // Price vs. fair value (30). At fair = 21 pts; every 1% under fair adds ~1.2.
    var pricePts, priceNote;
    if (!ctx.fair.hasComps) {
      pricePts = 18;
      priceNote = 'no market comps entered — neutral';
    } else {
      pricePts = clamp(21 - ctx.priceDelta * 120, 0, 30);
      priceNote = (ctx.priceDelta > 0 ? '+' : '') + (ctx.priceDelta * 100).toFixed(1) +
        '% vs fair value' + (ctx.priceDelta > 0 ? ' (over)' : ' (under)');
    }
    parts.push({ label: 'Price vs. fair value', points: pricePts, max: 30, note: priceNote,
                 source: ctx.fair.hasComps ? 'your comps' : 'none' });

    // Reliability (18) — brand baseline blended with model-year complaint rate.
    var rel = ctx.reliability;
    parts.push({
      label: 'Reliability', points: clamp((rel.score - 40) / 52 * 18, 0, 18), max: 18,
      note: rel.blended
        ? rel.score.toFixed(0) + '/100 (brand ' + rel.baseline + ' + complaint rate)'
        : rel.baseline + '/100 (brand only)',
      source: rel.source
    });

    // Remaining useful life (13).
    parts.push({
      label: 'Remaining useful life', points: clamp((1 - ctx.lifeUsed) * 1.25, 0, 1) * 13, max: 13,
      note: Math.round(ctx.remainingMiles).toLocaleString('en-US') + ' mi left', source: 'baked'
    });

    // Mileage for its age (8).
    parts.push({
      label: 'Mileage for its age',
      points: clamp(8 - Math.max(ctx.milesPerYear - data.constants.stdMilesPerYear, 0) / 1000, 0, 8),
      max: 8, note: Math.round(ctx.milesPerYear).toLocaleString('en-US') + ' mi/yr', source: 'your input'
    });

    // Financing (10).
    var finPts, finNote;
    if (!input.financed) {
      finPts = 10; finNote = 'cash purchase';
    } else {
      var benchmark = data.aprByTier[input.creditTier] || 10;
      finPts = clamp(7 - (input.apr - benchmark) * 0.8, 0, 10);
      if (input.termMonths >= 72) finPts = Math.max(finPts - 3, 0);
      finNote = input.apr.toFixed(1) + '% APR vs ' + benchmark.toFixed(1) + '% tier avg · ' +
        input.termMonths + ' mo';
    }
    parts.push({ label: 'Financing', points: finPts, max: 10, note: finNote, source: 'Experian tiers' });

    // Cost per mile (10).
    parts.push({
      label: 'Cost per mile',
      points: clamp(10 - Math.max(ctx.tco.costPerMile - data.constants.cpmBenchmark, 0) * 25, 0, 10),
      max: 10, note: '$' + ctx.tco.costPerMile.toFixed(2) + '/mi', source: 'computed'
    });

    // Safety (6) — NHTSA NCAP.
    var safety = ctx.safety;
    parts.push({
      label: 'Safety (NHTSA)', points: safety.points, max: 6,
      note: safety.rated ? safety.stars + '-star overall' : 'not rated (neutral)',
      source: safety.rated ? 'live' : 'n/a'
    });

    // History & transparency (5).
    var histPts = 5;
    if (input.records === 'partial') histPts -= 1;
    if (input.records === 'no') histPts -= 2;
    if (input.accidents === 'unknown') histPts -= 2;
    if (input.ppi === 'no') histPts -= 2;
    histPts = Math.max(histPts, 0);
    parts.push({ label: 'History & transparency', points: histPts, max: 5, note: '', source: 'your input' });

    var raw = parts.reduce(function (a, p) { return a + p.points; }, 0);
    var score = Math.round(raw);
    var caps = [];

    // Hard caps — structural risk beats arithmetic.
    if (input.title !== 'clean') { score = Math.min(score, 40); caps.push('branded title'); }
    if (input.accidents === 'major') { score = Math.min(score, 55); caps.push('major accident'); }
    if (ctx.remainingMiles < 40000) { score = Math.min(score, 45); caps.push('little life remaining'); }

    var openSafetyRecalls = (live.recalls || []).filter(function (r) {
      return !r.repairVerified && isSafetyCritical(r.component, data);
    });
    if (openSafetyRecalls.length > 0 && !input.recallsVerified) {
      score = Math.min(score, 60);
      caps.push('unrepaired safety recall');
    }

    return { score: clamp(score, 0, 100), raw: raw, parts: parts, caps: caps,
             openSafetyRecalls: openSafetyRecalls };
  }

  function verdictFor(score) {
    if (score >= 80) return { label: 'Excellent deal', level: 'good', icon: '✓' };
    if (score >= 65) return { label: 'Good deal', level: 'good', icon: '✓' };
    if (score >= 50) return { label: 'Fair — negotiate', level: 'warning', icon: '⚠' };
    if (score >= 35) return { label: 'Below average — caution', level: 'serious', icon: '⚠' };
    return { label: 'Walk away', level: 'critical', icon: '✕' };
  }

  /* ------------------------------------------------------------ 4.9 findings */

  function findings(ctx) {
    var input = ctx.input, data = ctx.data, live = ctx.live || {};
    var out = [];
    function add(level, tag, text) { out.push({ level: level, tag: tag, text: text }); }
    var money = function (v) { return '$' + Math.round(v).toLocaleString('en-US'); };
    var pctStr = function (v) { return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; };

    /* --- critical --- */
    if (input.title === 'rebuilt') {
      add('critical', 'Title', 'Rebuilt/salvage title: worth roughly 40% less than a clean-title equivalent, ' +
        'hard to insure and finance, and resale is severely limited. Only proceed at a deep discount with a specialist inspection.');
    }
    if (input.title === 'lemon') {
      add('critical', 'Title', 'Lemon-law buyback: the original defect may or may not be fixed. Demand the buyback ' +
        'documentation and the full repair history before considering it.');
    }
    if (input.accidents === 'major') {
      add('critical', 'History', 'Major/structural damage reported. Frame repairs affect crash safety and resale — ' +
        'have an independent body shop verify the repair quality before making any offer.');
    }
    (ctx.score.openSafetyRecalls || []).forEach(function (r) {
      add('critical', 'Open recall', 'Unrepaired safety recall — ' + (r.component || 'component') +
        (r.campaign ? ' (NHTSA ' + r.campaign + ')' : '') + '. ' +
        'Recall repairs are free at any franchised dealer; get written confirmation it was completed before you buy.');
    });

    /* --- warnings --- */
    var otherRecalls = (live.recalls || []).filter(function (r) {
      return (ctx.score.openSafetyRecalls || []).indexOf(r) === -1;
    });
    if (otherRecalls.length > 0) {
      add('warning', 'Recalls', otherRecalls.length + ' additional open recall' +
        (otherRecalls.length > 1 ? 's' : '') + ' on this model year (' +
        otherRecalls.slice(0, 3).map(function (r) { return r.component || 'unspecified'; }).join(', ') +
        '). Free to fix at a dealer — confirm completion.');
    }
    if (input.accidents === 'unknown') {
      add('warning', 'History', 'No vehicle history report. Pull a Carfax or AutoCheck before negotiating — ' +
        'an undisclosed accident changes the price completely.');
    }
    if (input.ppi === 'no') {
      add('warning', 'Inspection', 'No pre-purchase inspection. This is the highest-value $150 you can spend on a ' +
        'used car. A seller who refuses one is telling you something.');
    }
    if (ctx.fair.hasComps && ctx.priceDelta > 0.08) {
      add('warning', 'Price', 'Asking price is ' + pctStr(ctx.priceDelta) + ' above estimated fair value. ' +
        'Open at ' + money(ctx.negotiation.opening) + ' and bring your comps as evidence.');
    }
    if (ctx.fair.hasComps && ctx.priceDelta < -0.12) {
      add('warning', 'Price', 'Priced well below market. Sometimes that is a motivated seller — but verify the ' +
        'title, VIN and history extra carefully. Deals that look too good usually are.');
    }
    if (ctx.milesPerYear > 18000) {
      add('warning', 'Mileage', Math.round(ctx.milesPerYear).toLocaleString('en-US') +
        ' miles/year is well above the 12,000 norm. Mostly-highway miles are fine, but ask what the use was.');
    }
    if (ctx.milesPerYear < 5000 && input.age >= 5) {
      add('warning', 'Mileage', 'Unusually low mileage for its age. Verify against the history report (it lists past ' +
        'odometer readings) and note that seals, belts and rubber age even when a car sits.');
    }
    if (ctx.remainingMiles < 60000) {
      add('warning', 'Lifespan', 'Only about ' + Math.round(ctx.remainingMiles).toLocaleString('en-US') +
        ' miles of typical service life remain for a ' + input.brand +
        '. Budget for major repairs or plan a short ownership window.');
    }
    if (input.financed) {
      if (input.termMonths >= 72) {
        add('warning', 'Financing', 'A ' + input.termMonths + '-month term keeps you underwater longer and adds ' +
          'interest. 48–60 months is the sweet spot; if the payment only works at 72+, the car is too expensive.');
      }
      var bench = data.aprByTier[input.creditTier] || 10;
      if (input.apr > bench + 2) {
        add('warning', 'Financing', 'Your ' + input.apr.toFixed(1) + '% APR is well above the ' + bench.toFixed(1) +
          '% average for your credit tier. Get a credit-union pre-approval before accepting dealer financing.');
      }
      if (ctx.otd > 0 && input.down / ctx.otd < 0.10) {
        add('warning', 'Financing', 'Less than 10% down means instant negative equity. If the car is totaled early ' +
          'you could owe more than insurance pays — consider GAP coverage or a larger down payment.');
      }
      if (ctx.underwaterUntil >= 1) {
        add('warning', 'Equity', 'You would owe more than the car is worth through year ' + ctx.underwaterUntil +
          '. A larger down payment or a shorter term closes that gap.');
      }
    }
    var brandData = data.brands[input.brand] || data.brands['Other'];
    if (brandData.maintPerYear >= 850) {
      add('warning', 'Running cost', input.brand + ' averages ' + money(brandData.maintPerYear) +
        '/year in maintenance and repairs — roughly double a Toyota or Honda. Budget accordingly out of warranty.');
    }
    if (input.owners >= 3) {
      add('warning', 'History', input.owners + ' previous owners on a ' + input.age +
        '-year-old car is above normal — a string of short ownerships can signal a problem car.');
    }
    if (input.records === 'no') {
      add('warning', 'History', 'No service records. Assume maintenance is overdue: price in a full fluid service ' +
        '(roughly $400–600) plus a timing belt if the engine uses one.');
    }
    var rel = ctx.reliability;
    if (rel.blended && rel.rate > rel.p90) {
      add('warning', 'Complaints', 'NHTSA complaints for this model year run above the 90th percentile for its class' +
        (rel.top && rel.top.length ? ', concentrated in ' + rel.top.slice(0, 2).join(' and ').toLowerCase() : '') +
        '. Have the inspection target exactly those systems.');
    }
    if (ctx.safety.rated && ctx.safety.stars <= 3) {
      add('warning', 'Safety', 'NHTSA overall crash rating is only ' + ctx.safety.stars +
        ' stars. Compare against alternatives in the same class before committing.');
    }

    /* --- good --- */
    if (ctx.fair.hasComps && ctx.priceDelta < -0.02) {
      add('good', 'Price', 'Priced ' + (Math.abs(ctx.priceDelta) * 100).toFixed(1) +
        '% below your comps — a below-market starting point.');
    }
    if (brandData.relBaseline >= 80) {
      add('good', 'Reliability', input.brand + ' ranks among the most reliable brands (' + brandData.relBaseline +
        '/100) with low repair costs (' + money(brandData.maintPerYear) + '/yr).');
    }
    if (ctx.remainingMiles > 120000) {
      add('good', 'Lifespan', 'About ' + Math.round(ctx.remainingMiles).toLocaleString('en-US') +
        ' miles of expected service life remain — roughly ' + ctx.remainingYears.toFixed(0) +
        ' years at your driving rate.');
    }
    if (input.financed && ctx.otd > 0 && input.down / ctx.otd >= 0.20) {
      add('good', 'Financing', 'Solid down payment — you start the loan with real equity.');
    }
    if (!input.financed) {
      add('good', 'Financing', 'Cash purchase — no interest, no negative-equity risk, and a stronger position ' +
        'when negotiating on price.');
    }
    if (input.ppi === 'yes') {
      add('good', 'Inspection', 'Passed an independent pre-purchase inspection.');
    }
    if (ctx.safety.rated && ctx.safety.stars >= 5) {
      add('good', 'Safety', '5-star NHTSA overall crash rating.');
    }
    if (live.recalls && live.recalls.length === 0) {
      add('good', 'Recalls', 'No open NHTSA recalls found for this model year.');
    }

    var order = { critical: 0, warning: 1, good: 2 };
    out.sort(function (a, b) { return order[a.level] - order[b.level]; });
    return out;
  }

  /* -------------------------------------------------------- orchestration */

  /** Fill in defaults and coerce types so downstream math never sees NaN. */
  function normalizeInput(raw, data) {
    var currentYear = raw.currentYear || new Date().getFullYear();
    var year = toNum(raw.year, currentYear - 5);
    var brand = data.brands[raw.brand] ? raw.brand : 'Other';
    var segment = data.segments[raw.segment] ? raw.segment : 'Midsize car';
    var creditTier = data.aprByTier[raw.creditTier] !== undefined ? raw.creditTier : 'prime';
    var financed = raw.payType !== 'cash';
    var rec = vehicleRecord(raw.brand, raw.model, year, data);

    return {
      name: raw.name || (year + ' ' + brand + (raw.model ? ' ' + raw.model : '')),
      brand: brand,
      model: raw.model || '',
      year: year,
      age: clamp(currentYear - year, 0, 40),
      segment: rec && rec.seg && data.segments[rec.seg] ? rec.seg : segment,
      miles: Math.max(toNum(raw.miles, 60000), 0),
      asking: Math.max(toNum(raw.asking, 0), 0),
      mpg: toNum(raw.mpg, rec && rec.mpg ? rec.mpg : null),
      kwhPer100mi: toNum(raw.kwhPer100mi, rec && rec.kwhPer100mi ? rec.kwhPer100mi : null),
      condition: CONDITION_FACTOR[raw.condition] !== undefined ? raw.condition : 'good',
      title: TITLE_FACTOR[raw.title] !== undefined ? raw.title : 'clean',
      accidents: ACCIDENT_FACTOR[raw.accidents] !== undefined ? raw.accidents : 'none',
      owners: clamp(Math.round(toNum(raw.owners, 1)), 1, 20),
      records: raw.records || 'yes',
      ppi: raw.ppi || 'planned',
      comps: (raw.comps || []).map(function (c) { return toNum(c, NaN); }),
      recallsVerified: !!raw.recallsVerified,
      payType: financed ? 'loan' : 'cash',
      financed: financed,
      down: Math.max(toNum(raw.down, 0), 0),
      creditTier: creditTier,
      apr: clamp(toNum(raw.apr, data.aprByTier[creditTier]), 0, 40),
      termMonths: clamp(Math.round(toNum(raw.termMonths, 48)), 1, 120),
      taxRate: clamp(toNum(raw.taxRate, 0.06), 0, 0.25),
      fees: Math.max(toNum(raw.fees, 0), 0),
      annualMiles: Math.max(toNum(raw.annualMiles, 12000), 100),
      horizon: clamp(Math.round(toNum(raw.horizon, 5)), 1, 15),
      gasUsdPerGal: toNum(raw.gasUsdPerGal, data.energy.gasUsdPerGal),
      elecUsdPerKwh: toNum(raw.elecUsdPerKwh, data.energy.elecUsdPerKwh),
      insurance: toNum(raw.insurance, (data.segments[rec && rec.seg ? rec.seg : segment] || {}).insPerYear),
      income: Math.max(toNum(raw.income, 0), 0),
      isEV: raw.isEV
    };
  }

  /**
   * Full analysis. `live` is optional and carries anything fetched from the network
   * layer: { recalls: [{component, campaign, summary, repairVerified}],
   *          complaints: {per100k, top}, safety: {overallRating},
   *          mpg, gasUsdPerGal, elecUsdPerKwh, cpiTrend }
   */
  function analyze(rawInput, data, live) {
    live = live || {};
    var input = normalizeInput(rawInput, data);

    // Live overrides: network data wins over baked defaults when present.
    if (isNum(live.mpg) && !isNum(rawInput.mpg)) input.mpg = live.mpg;
    if (isNum(live.gasUsdPerGal)) input.gasUsdPerGal = live.gasUsdPerGal;
    if (isNum(live.elecUsdPerKwh)) input.elecUsdPerKwh = live.elecUsdPerKwh;

    var fair = fairValue(input, data);
    var priceDelta = fair.value > 0 ? (input.asking - fair.value) / fair.value : 0;
    var otd = outTheDoor(input.asking, input.taxRate, input.fees);
    var principal = Math.max(otd - input.down, 0);
    var loan = input.financed
      ? loanSchedule(principal, input.apr, input.termMonths)
      : { payment: 0, totalInterest: 0, balanceByYear: [0], months: 0 };

    var tco = totalCostOfOwnership(input, data, loan);

    var brandData = data.brands[input.brand] || data.brands['Other'];
    var remainingMiles = Math.max(brandData.lifeMiles - input.miles, 0);
    var lifeUsed = clamp(input.miles / brandData.lifeMiles, 0, 1);
    var milesPerYear = input.age > 0 ? input.miles / input.age : input.miles;

    // Equity: how long the loan balance exceeds the car's value.
    var chartYears = Math.max(input.horizon, input.financed ? Math.ceil(input.termMonths / 12) : 0);
    var valueCurve = projectValue(input.asking, input.age, input.segment, input.brand, chartYears, data);
    var balanceCurve = [];
    for (var t = 0; t <= chartYears; t++) {
      balanceCurve.push(input.financed
        ? (loan.balanceByYear[t] !== undefined ? loan.balanceByYear[t] : 0)
        : 0);
    }
    var underwaterUntil = -1;
    if (input.financed) {
      for (var u = 0; u <= chartYears; u++) {
        if (balanceCurve[u] > valueCurve[u]) underwaterUntil = u;
      }
    }

    var reliability = reliabilityScore(input, data, live);
    var safety = safetyPoints(live);

    var ctx = {
      input: input, data: data, live: live, fair: fair, priceDelta: priceDelta,
      otd: otd, loan: loan, tco: tco, remainingMiles: remainingMiles, lifeUsed: lifeUsed,
      milesPerYear: milesPerYear, remainingYears: remainingMiles / input.annualMiles,
      reliability: reliability, safety: safety,
      negotiation: negotiation(fair.value, input),
      valueCurve: valueCurve, balanceCurve: balanceCurve, underwaterUntil: underwaterUntil,
      chartYears: chartYears
    };

    ctx.score = dealScore(ctx);
    ctx.verdict = verdictFor(ctx.score.score);
    ctx.affordability = affordability(input, otd, loan.payment, tco);
    ctx.findings = findings(ctx);
    ctx.principal = principal;
    return ctx;
  }

  return {
    clamp: clamp, toNum: toNum, keyFor: keyFor,
    mileageSlope: mileageSlope, fairValue: fairValue,
    outTheDoor: outTheDoor, negotiation: negotiation,
    depRateAt: depRateAt, projectValue: projectValue,
    loanSchedule: loanSchedule, maintenanceCost: maintenanceCost,
    vehicleRecord: vehicleRecord, annualFuelCost: annualFuelCost,
    totalCostOfOwnership: totalCostOfOwnership, affordability: affordability,
    reliabilityScore: reliabilityScore, safetyPoints: safetyPoints,
    isSafetyCritical: isSafetyCritical, dealScore: dealScore, verdictFor: verdictFor,
    findings: findings, normalizeInput: normalizeInput, analyze: analyze,
    CONDITION_FACTOR: CONDITION_FACTOR, TITLE_FACTOR: TITLE_FACTOR, ACCIDENT_FACTOR: ACCIDENT_FACTOR
  };
}));
