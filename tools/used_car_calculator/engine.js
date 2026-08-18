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

    // Comps come off listings, which are ASKING prices; cars transact below ask. Without
    // this haircut the fair value — and with it the walk-away price — is biased high.
    // Only applied to real comps: anchoring to the seller's own ask is not evidence.
    var biasFactor = (hasComps && input.applyCompBias !== false)
      ? (data.compBias ? data.compBias.askingToTransaction : 1) : 1;
    var adjustedBase = base * biasFactor;

    var expectedMiles = input.age * data.constants.stdMilesPerYear;
    var slope = mileageSlope(adjustedBase, input.segment, data);
    var rawAdj = (expectedMiles - input.miles) * slope;
    // A mileage correction can never dominate the valuation.
    var mileageAdj = clamp(rawAdj, -0.15 * adjustedBase, 0.15 * adjustedBase);

    var condition = CONDITION_FACTOR[input.condition] !== undefined ? CONDITION_FACTOR[input.condition] : 1;
    var title     = TITLE_FACTOR[input.title] !== undefined ? TITLE_FACTOR[input.title] : 1;
    var accident  = ACCIDENT_FACTOR[input.accidents] !== undefined ? ACCIDENT_FACTOR[input.accidents] : 1;
    var owners    = (input.owners || 1) >= 3 ? 0.97 : 1.00;

    var value = (adjustedBase + mileageAdj) * condition * title * accident * owners;
    return {
      value: Math.max(value, data.constants.floorValue),
      base: base,                   // raw comp average, as entered
      adjustedBase: adjustedBase,   // after the asking-to-transaction haircut
      biasFactor: biasFactor,
      biasApplied: biasFactor !== 1,
      hasComps: hasComps,
      compCount: comps.length,
      mileageAdj: mileageAdj,
      expectedMiles: expectedMiles,
      slope: slope,
      factors: { condition: condition, title: title, accident: accident, owners: owners }
    };
  }

  /* ------------------------------------------------- purchase costs & trade-in */

  /** State defaults, when a state is selected. */
  function stateInfo(code, data) {
    if (!code || !data.states || !data.states.rates) return null;
    return data.states.rates[String(code).toUpperCase()] || null;
  }

  /**
   * What the deal actually costs at signing, including the trade-in.
   *
   * Most states tax only the price difference after a trade-in, which is worth real money
   * and is invisible if you only look at the sticker. Negative equity on the trade is
   * rolled into the new loan, which is the trap this makes visible.
   */
  function purchaseCosts(input, data) {
    var st = stateInfo(input.state, data);
    var creditAllowed = input.tradeInCredit !== undefined
      ? !!input.tradeInCredit
      : (st ? !!st.tradeInCredit : true);

    var tradeValue = Math.max(toNum(input.tradeInValue, 0), 0);
    var tradePayoff = Math.max(toNum(input.tradePayoff, 0), 0);
    var creditedTrade = creditAllowed ? Math.min(tradeValue, input.asking) : 0;

    var taxableAmount = Math.max(input.asking - creditedTrade, 0);
    var salesTax = taxableAmount * input.taxRate;
    var taxWithoutTrade = input.asking * input.taxRate;
    var taxSavings = Math.max(taxWithoutTrade - salesTax, 0);

    var titleFee = st ? st.titleFee : 0;
    var otd = input.asking + salesTax + input.fees + titleFee;
    var tradeEquity = tradeValue - tradePayoff;
    var principal = Math.max(otd - input.down - tradeEquity, 0);

    return {
      salesTax: salesTax, taxableAmount: taxableAmount, taxSavings: taxSavings,
      tradeValue: tradeValue, tradePayoff: tradePayoff, tradeEquity: tradeEquity,
      rolledNegativeEquity: Math.max(-tradeEquity, 0),
      creditAllowed: creditAllowed, otd: otd, principal: principal,
      titleFee: titleFee,
      registrationPerYear: st ? st.reg : data.constants.regPerYear,
      propertyTaxRate: st ? st.propertyTaxRate : 0,
      // Inspections are amortized to a yearly figure so the annual total is comparable.
      inspectionPerYear: st && st.inspect
        ? (st.inspect.cadence === 'annual' ? st.inspect.cost
          : st.inspect.cadence === 'biennial' ? st.inspect.cost / 2 : 0)
        : 0,
      inspection: st ? st.inspect : null,
      evFeePerYear: (st && input.segment === 'Electric') ? st.evFee : 0,
      insuranceIndex: st ? st.ins : 1,
      stateCode: input.state || null
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

  /**
   * Value at an arbitrary month, decaying the annual rate geometrically. Used by the
   * loan-term comparison, which needs sub-year resolution to find the exact month a
   * loan crosses back above water.
   */
  function valueAtMonth(price, age, segment, brand, month, data) {
    var value = price;
    for (var m = 1; m <= month; m++) {
      var rate = depRateAt(age + Math.floor((m - 1) / 12) + 1, segment, brand, data);
      value = Math.max(value * Math.pow(1 - rate, 1 / 12), data.constants.floorValue);
    }
    return value;
  }

  /**
   * Shortest term that amortizes `principal` at `aprPercent` within `targetPayment`.
   * Returns null when the payment never covers the interest, which is the honest
   * answer to "what term gets me to $X/month" for an impossible X.
   */
  function termForPayment(principal, aprPercent, targetPayment) {
    if (!(principal > 0) || !(targetPayment > 0)) return null;
    var r = aprPercent / 100 / 12;
    if (r === 0) return Math.ceil(principal / targetPayment);
    if (targetPayment <= principal * r) return null;   // interest-only or worse
    var n = -Math.log(1 - principal * r / targetPayment) / Math.log(1 + r);
    return Math.ceil(n);
  }

  /**
   * Compare loan lengths side by side: payment, lifetime interest, and how long each
   * term leaves you underwater. `recommended` is the shortest term that clears the
   * 20/4/10 payment share (when income is known) and stays at or under 60 months.
   */
  function loanTermComparison(input, data, terms) {
    terms = terms || [24, 36, 48, 60, 72, 84];
    var principal = purchaseCosts(input, data).principal;
    var insurancePerMonth = toNum(input.insurance,
      (data.segments[input.segment] || {}).insPerYear || 1750) / 12;
    var fuelPerMonth = annualFuelCost(input, data) / 12;

    var options = terms.map(function (months) {
      var loan = loanSchedule(principal, input.apr, months);
      var balance = principal, r = input.apr / 100 / 12, underwaterMonths = 0;
      for (var m = 1; m <= months; m++) {
        balance = Math.max(balance - (loan.payment - balance * r), 0);
        if (balance > valueAtMonth(input.asking, input.age, input.segment, input.brand, m, data)) {
          underwaterMonths = m;
        }
      }
      var allIn = loan.payment + insurancePerMonth + fuelPerMonth;
      return {
        months: months,
        payment: loan.payment,
        totalInterest: loan.totalInterest,
        totalPaid: loan.payment * months,
        interestShare: principal > 0 ? loan.totalInterest / principal : 0,
        underwaterMonths: underwaterMonths,
        monthlyAllIn: allIn,
        incomeShare: input.income > 0 ? allIn / input.income : null,
        affordable: input.income > 0 ? allIn <= input.income * 0.10 : null
      };
    });

    var recommended = null, note = '';
    if (input.income > 0) {
      var fits = options.filter(function (o) { return o.affordable; });
      if (!fits.length) {
        note = 'No term here keeps the all-in cost within 10% of your take-home pay. ' +
          'Stretching the loan does not fix that — a cheaper car does.';
      } else {
        var shortSafe = fits.filter(function (o) { return o.months <= 60; });
        recommended = (shortSafe.length ? shortSafe : fits)[0];
        note = 'Shortest term that keeps the all-in cost within 10% of take-home.';
      }
    } else {
      var dry = options.filter(function (o) { return o.months <= 60 && o.underwaterMonths === 0; });
      recommended = dry.length ? dry[0]
        : options.filter(function (o) { return o.months <= 60; }).slice(-1)[0] || options[0];
      note = dry.length
        ? 'Shortest term that never leaves you owing more than the car is worth.'
        : 'Shortest sensible term. Add income above for an affordability-based recommendation.';
    }

    return { options: options, recommended: recommended, note: note, principal: principal };
  }

  /* -------------------------------------------------------- 4.5 maintenance */

  /** Class cost/life multipliers — a Tundra is not a Camry, even though both are Toyotas. */
  function segmentFactors(segment, data) {
    return (data.segmentFactors && (data.segmentFactors[segment] || data.segmentFactors['default']))
      || { maint: 1, life: 1, tyres: 900, brakes: 650, belt: true };
  }

  /**
   * Repair spend rises ~6%/yr past age 3 (out of warranty), capped at 2.2x, and is scaled
   * by vehicle class. `segment` is optional so callers that only know the brand still work.
   */
  function maintenanceCost(brand, startAge, years, data, segment) {
    var brandData = data.brands[brand] || data.brands['Other'];
    var classMultiplier = segment ? segmentFactors(segment, data).maint : 1;
    var base = brandData.perYear !== undefined ? brandData.perYear : brandData.maintPerYear;
    var perYear = [], total = 0;
    for (var t = 0; t < years; t++) {
      var ageFactor = clamp(1 + 0.06 * Math.max(startAge + t - 3, 0), 1, 2.2);
      var cost = base * ageFactor * classMultiplier;
      perYear.push(cost);
      total += cost;
    }
    return { total: total, perYear: perYear };
  }

  /** Expected service life in miles for this brand in this class. */
  function lifespanMiles(brand, segment, data) {
    var brandData = data.brands[brand] || data.brands['Other'];
    return brandData.lifeMiles * segmentFactors(segment, data).life;
  }

  /**
   * Wear items coming due, on the odometer clock. Returns everything due within
   * `withinMiles` of today, soonest first, with the cost scaled to the vehicle class.
   */
  function upcomingServices(input, data, withinMiles) {
    var factors = segmentFactors(input.segment, data);
    var isEV = input.segment === 'Electric';
    var out = [];
    (data.serviceSchedule || []).forEach(function (item) {
      if (isEV && item.evSkip) return;
      var cost = item.costKey ? factors[item.costKey] : item.cost * factors.maint;
      // Next multiple of the interval at or after the current odometer.
      var due = Math.ceil(input.miles / item.everyMiles) * item.everyMiles;
      if (due <= input.miles) due += item.everyMiles;
      var milesAway = due - input.miles;
      if (milesAway > withinMiles) return;
      out.push({
        name: item.name,
        dueAtMiles: due,
        milesAway: milesAway,
        monthsAway: input.annualMiles > 0 ? milesAway / input.annualMiles * 12 : Infinity,
        cost: cost,
        conditional: item.conditional || null
      });
    });
    return out.sort(function (a, b) { return a.milesAway - b.milesAway; });
  }

  /**
   * Near-term maintenance outlook. The first-year total is the number a buyer actually
   * needs: it is what lands right after purchase, and it is often front-loaded well above
   * the smooth annual average that the cost-of-ownership model budgets.
   */
  function serviceOutlook(input, data) {
    var firstYear = upcomingServices(input, data, input.annualMiles);
    var horizon = upcomingServices(input, data, input.annualMiles * input.horizon);
    var sum = function (list) {
      return list.reduce(function (a, s) { return a + s.cost; }, 0);
    };
    var budgetYearOne = maintenanceCost(input.brand, input.age, 1, data, input.segment).total;
    var firstYearTotal = sum(firstYear);
    return {
      firstYear: firstYear,
      firstYearTotal: firstYearTotal,
      horizon: horizon,
      horizonTotal: sum(horizon),
      budgetYearOne: budgetYearOne,
      frontLoaded: firstYearTotal > budgetYearOne * 1.25,
      overrun: Math.max(firstYearTotal - budgetYearOne, 0)
    };
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
    var purchase = purchaseCosts(input, data);
    var values = projectValue(input.asking, input.age, input.segment, input.brand, years, data);
    var depreciation = input.asking - values[years];
    var fuelPerYear = annualFuelCost(input, data);
    var fuel = fuelPerYear * years;
    var insurance = toNum(input.insurance, (data.segments[input.segment] || {}).insPerYear || 1750) * years;
    var maintenance = maintenanceCost(input.brand, input.age, years, data, input.segment);
    var maintenanceTotal = maintenance.total * toNum(input.maintMultiplier, 1);

    // Several states levy an annual tax on the vehicle's value; it falls as the car does.
    var propertyTax = 0;
    for (var t = 1; t <= years; t++) propertyTax += values[t] * purchase.propertyTaxRate;

    var stateAnnual = (purchase.registrationPerYear + purchase.inspectionPerYear +
      purchase.evFeePerYear) * years;
    var taxesFees = purchase.salesTax + input.fees + purchase.titleFee +
      stateAnnual + propertyTax;
    var interest = loan ? loan.totalInterest : 0;
    var total = depreciation + fuel + insurance + maintenanceTotal + taxesFees + interest;
    var totalMiles = input.annualMiles * years;

    return {
      values: values,
      depreciation: depreciation,
      fuel: fuel,
      fuelPerYear: fuelPerYear,
      insurance: insurance,
      maintenance: maintenanceTotal,
      maintenancePerYear: maintenance.perYear,
      taxesFees: taxesFees,
      salesTax: purchase.salesTax,
      propertyTax: propertyTax,
      titleFee: purchase.titleFee,
      registrationPerYear: purchase.registrationPerYear,
      inspectionPerYear: purchase.inspectionPerYear,
      evFeePerYear: purchase.evFeePerYear,
      stateAnnual: stateAnnual,
      interest: interest,
      total: total,
      totalMiles: totalMiles,
      costPerMile: totalMiles > 0 ? total / totalMiles : 0,
      costPerMonth: total / (years * 12)
    };
  }

  /** Cumulative ownership cost year by year — index 0 is the day you buy. */
  function cumulativeCost(input, data, loan) {
    var years = input.horizon;
    var purchase = purchaseCosts(input, data);
    var values = projectValue(input.asking, input.age, input.segment, input.brand, years, data);
    var maint = maintenanceCost(input.brand, input.age, years, data, input.segment);
    var maintMultiplier = toNum(input.maintMultiplier, 1);
    var fuelPerYear = annualFuelCost(input, data);
    var insurancePerYear = toNum(input.insurance,
      (data.segments[input.segment] || {}).insPerYear || 1750);
    var upfront = purchase.salesTax + input.fees + purchase.titleFee;
    var interestPerYear = loan && loan.months
      ? loan.totalInterest / (loan.months / 12) : 0;

    var out = [{ year: 0, total: upfront, perMile: 0 }];
    var running = upfront;
    for (var t = 1; t <= years; t++) {
      running += values[t - 1] - values[t];                       // that year's depreciation
      running += fuelPerYear + insurancePerYear +
        maint.perYear[t - 1] * maintMultiplier + purchase.registrationPerYear +
        purchase.inspectionPerYear + purchase.evFeePerYear +
        values[t] * purchase.propertyTaxRate;
      if (loan && loan.months) running += Math.min(interestPerYear, loan.totalInterest);
      var miles = input.annualMiles * t;
      out.push({ year: t, total: running, perMile: miles > 0 ? running / miles : 0 });
    }
    return out;
  }

  /**
   * How much the cost estimate moves when the assumptions do.
   *
   * The report otherwise presents a single number to the dollar, which overstates how much
   * is actually known — insurance and repair costs here are class averages that can be off
   * by half. Each factor is varied independently and ranked by how much it swings the total.
   */
  function sensitivity(rawInput, data, live) {
    var base = analyze(rawInput, data, live);
    var baseTotal = base.tco.total;
    var factors = [
      { label: 'Insurance premium', note: 'a class average; real quotes vary by half',
        low: { insurance: base.input.insurance * 0.6 }, high: { insurance: base.input.insurance * 1.4 },
        lowLabel: '-40%', highLabel: '+40%' },
      { label: 'Repair costs', note: 'brand and class average, not this specific car',
        low: { maintMultiplier: 0.6 }, high: { maintMultiplier: 1.4 },
        lowLabel: '-40%', highLabel: '+40%' },
      { label: 'Fuel price', note: 'pump prices over the years you keep it',
        low: { gasUsdPerGal: base.input.gasUsdPerGal * 0.7 },
        high: { gasUsdPerGal: base.input.gasUsdPerGal * 1.3 },
        lowLabel: '-30%', highLabel: '+30%' },
      { label: 'Miles you drive', note: 'more miles means more fuel and faster wear',
        low: { annualMiles: base.input.annualMiles * 0.7 },
        high: { annualMiles: base.input.annualMiles * 1.3 },
        lowLabel: '-30%', highLabel: '+30%' },
      { label: 'Price you pay', note: 'how the negotiation actually lands',
        low: { asking: base.input.asking * 0.95 }, high: { asking: base.input.asking * 1.05 },
        lowLabel: '-5%', highLabel: '+5%' }
    ];
    if (base.input.financed) {
      factors.push({ label: 'APR', note: 'the rate you are actually approved at',
        low: { apr: Math.max(base.input.apr - 2, 0) }, high: { apr: base.input.apr + 2 },
        lowLabel: '-2 pts', highLabel: '+2 pts' });
    }

    var items = factors.map(function (f) {
      var lo = analyze(Object.assign({}, rawInput, f.low), data, live).tco.total;
      var hi = analyze(Object.assign({}, rawInput, f.high), data, live).tco.total;
      return {
        label: f.label, note: f.note,
        low: Math.min(lo, hi), high: Math.max(lo, hi),
        lowLabel: f.lowLabel, highLabel: f.highLabel,
        swing: Math.abs(hi - lo)
      };
    }).sort(function (a, b) { return b.swing - a.swing; });

    return {
      base: baseTotal,
      items: items,
      // A plausible band, not a worst case: the largest single swing either way.
      low: baseTotal - (items.length ? items[0].swing / 2 : 0),
      high: baseTotal + (items.length ? items[0].swing / 2 : 0),
      driver: items.length ? items[0].label : null
    };
  }

  /**
   * Monthly cash cost of keeping this car: the money that actually leaves your account.
   *
   * Loan payment, insurance, fuel, upkeep, registration and any inspection or EV charge.
   * Depreciation is deliberately excluded — it is a real cost and the cost-of-ownership
   * total counts it, but it is not a monthly outgoing, and a monthly budget is about cash.
   */
  function monthlyCashCost(input, data) {
    var purchase = purchaseCosts(input, data);
    var loan = input.financed
      ? loanSchedule(purchase.principal, input.apr, input.termMonths)
      : { payment: 0 };
    var insurance = toNum(input.insurance,
      (data.segments[input.segment] || {}).insPerYear || 1750);
    var maint = maintenanceCost(input.brand, input.age, input.horizon, data, input.segment);
    var maintPerYear = (maint.total * toNum(input.maintMultiplier, 1)) / input.horizon;
    var values = projectValue(input.asking, input.age, input.segment, input.brand, input.horizon, data);
    var propertyTax = 0;
    for (var t = 1; t <= input.horizon; t++) propertyTax += values[t] * purchase.propertyTaxRate;

    var perYear = insurance + annualFuelCost(input, data) + maintPerYear +
      purchase.registrationPerYear + purchase.inspectionPerYear + purchase.evFeePerYear +
      propertyTax / input.horizon;

    return {
      payment: loan.payment,
      insurance: insurance / 12,
      fuel: annualFuelCost(input, data) / 12,
      maintenance: maintPerYear / 12,
      fees: (purchase.registrationPerYear + purchase.inspectionPerYear +
             purchase.evFeePerYear + propertyTax / input.horizon) / 12,
      total: loan.payment + perYear / 12
    };
  }

  /**
   * The most expensive car whose monthly cash cost stays inside `budget`.
   *
   * Answers the question people actually shop with — "what can I afford at $X a month" —
   * rather than making them guess a price and check. Optionally also caps the loan payment
   * itself, since a budget met by stretching the term is not the same as an affordable car.
   * Returns null when even the cheapest running car breaks the budget.
   */
  function maxPriceForMonthlyBudget(rawInput, data, budget, options) {
    options = options || {};
    var maxPayment = isNum(options.maxPayment) ? options.maxPayment : Infinity;
    var floor = data.constants.floorValue;

    function costAt(price) {
      var input = normalizeInput(Object.assign({}, rawInput, { asking: price }), data);
      applyStateDefaults(input, data);
      var cash = monthlyCashCost(input, data);
      return { cash: cash, ok: cash.total <= budget && cash.payment <= maxPayment };
    }

    if (!costAt(floor).ok) return null;          // nothing runs this cheaply

    var lo = floor, hi = Math.max(toNum(rawInput.asking, 20000) * 3, 60000);
    if (costAt(hi).ok) return { price: hi, cash: costAt(hi).cash, capped: true };
    for (var i = 0; i < 40; i++) {
      var mid = (lo + hi) / 2;
      if (costAt(mid).ok) lo = mid; else hi = mid;
    }
    var atLo = costAt(lo);
    return { price: lo, cash: atLo.cash, capped: false };
  }

  /**
   * Solve for the asking price that would earn `targetScore`. Binary search, because
   * the score is monotonic in price except where a hard cap flattens it — hence the
   * explicit reachability check before searching.
   */
  function priceForScore(rawInput, data, live, targetScore) {
    var lo = 500, hi = Math.max(toNum(rawInput.asking, 20000), 1000) * 1.5;
    var atLo = analyze(Object.assign({}, rawInput, { asking: lo }), data, live).score.score;
    if (atLo < targetScore) return null;     // a cap or the car itself makes this unreachable
    var atHi = analyze(Object.assign({}, rawInput, { asking: hi }), data, live).score.score;
    if (atHi >= targetScore) return hi;
    for (var i = 0; i < 40; i++) {
      var mid = (lo + hi) / 2;
      if (analyze(Object.assign({}, rawInput, { asking: mid }), data, live).score.score >= targetScore) {
        lo = mid;
      } else {
        hi = mid;
      }
    }
    return lo;
  }

  /** Offline recall signal: campaign count for this model-year from the baked table. */
  function bakedRecallCount(input, data) {
    var count = data.recallCounts[keyFor(input.brand, input.model, input.year)];
    return count === undefined ? null : count;
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
    // Trade-in traps.
    if (ctx.purchase && ctx.purchase.rolledNegativeEquity > 0) {
      add('critical', 'Trade-in', 'You owe ' + money(ctx.purchase.rolledNegativeEquity) +
        ' more on your trade than it is worth, and that debt gets rolled into this loan. ' +
        'You would start the new car already underwater by that amount — pay it off separately ' +
        'if you possibly can.');
    }
    if (ctx.purchase && ctx.purchase.tradeValue > 0 && !ctx.purchase.creditAllowed) {
      add('warning', 'Trade-in', 'Your state does not appear to reduce sales tax by the trade-in ' +
        'value, so you are taxed on the full price. Selling the old car privately instead may net ' +
        'you more — check your state\'s rule, it is worth ' +
        money(Math.min(ctx.purchase.tradeValue, input.asking) * input.taxRate) + ' here.');
    }

    // Dealer fees are the least regulated number on the contract.
    if (input.fees >= 800) {
      add('warning', 'Fees', 'Dealer and documentation fees of ' + money(input.fees) +
        ' are on the high side. Some states cap this fee by law and others do not, so check ' +
        'your state\'s rule — and remember it is negotiable in practice even where it is not ' +
        'capped: ask for the price to come down by the same amount.');
    }

    // Near-term wear items.
    if (ctx.services && ctx.services.frontLoaded) {
      add('warning', 'Maintenance', 'About ' + money(ctx.services.firstYearTotal) +
        ' of wear items come due in the first year (' +
        ctx.services.firstYear.slice(0, 3).map(function (s) { return s.name.toLowerCase(); }).join(', ') +
        ') — roughly ' + money(ctx.services.overrun) + ' above the average annual budget. ' +
        'Use it as a negotiating point, and have the money ready.');
    }

    if (ctx.tco && ctx.tco.propertyTax > 0) {
      add('warning', 'Taxes', 'Your state levies an annual tax on the vehicle\'s value — about ' +
        money(ctx.tco.propertyTax) + ' over ' + input.horizon + ' years on this car. ' +
        'It is easy to forget and it is included in the totals here.');
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
    // Offline recall signal: when the live NHTSA lookup did not run, fall back to the
    // baked campaign count so the user still knows to check.
    if (!live.recalls && ctx.bakedRecallCount) {
      add('warning', 'Recalls', 'This model year had ' + ctx.bakedRecallCount +
        ' recall campaign' + (ctx.bakedRecallCount > 1 ? 's' : '') +
        ' on record. The live NHTSA check did not run — look the VIN up at nhtsa.gov/recalls ' +
        'and confirm the repairs were completed.');
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
    if (ctx.purchase && ctx.purchase.taxSavings > 0) {
      add('good', 'Trade-in', 'Your state taxes only the price difference after the trade-in, ' +
        'which saves ' + money(ctx.purchase.taxSavings) + ' in sales tax on this deal.');
    }
    if (ctx.fair && ctx.fair.biasApplied) {
      add('good', 'Method', 'Your comparables are listing prices, so fair value here is set ' +
        (100 - ctx.fair.biasFactor * 100).toFixed(0) + '% below their average — cars normally ' +
        'transact under ask, and pricing off the ask alone would push your walk-away number too high.');
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
      // What the buyer can actually spend each month, if they said.
      monthlyBudget: Math.max(toNum(raw.monthlyBudget, 0), 0),
      maxPayment: Math.max(toNum(raw.maxPayment, 0), 0),
      isEV: raw.isEV,
      state: raw.state || null,
      stateDefaultsApplied: false,
      tradeInValue: Math.max(toNum(raw.tradeInValue, 0), 0),
      tradePayoff: Math.max(toNum(raw.tradePayoff, 0), 0),
      tradeInCredit: raw.tradeInCredit,
      applyCompBias: raw.applyCompBias !== false,
      maintMultiplier: clamp(toNum(raw.maintMultiplier, 1), 0.1, 5)
    };
  }

  /**
   * Full analysis. `live` is optional and carries anything fetched from the network
   * layer: { recalls: [{component, campaign, summary, repairVerified}],
   *          complaints: {per100k, top}, safety: {overallRating},
   *          mpg, gasUsdPerGal, elecUsdPerKwh, cpiTrend }
   */
  /**
   * Apply the selected state's cost profile to anything the user has not set by hand.
   * Insurance varies about threefold across states and is one of the largest lines in the
   * total, so leaving it at a national average is a real accuracy loss.
   */
  function applyStateDefaults(input, data) {
    var st = stateInfo(input.state, data);
    if (!st) return input;
    if (!isNum(input.rawInsurance)) {
      input.insurance = (data.segments[input.segment] || {}).insPerYear * st.ins;
    }
    if (!isNum(input.rawGas)) input.gasUsdPerGal = st.gas;
    if (!isNum(input.rawElec)) input.elecUsdPerKwh = st.elec;
    input.stateDefaultsApplied = true;
    return input;
  }

  function analyze(rawInput, data, live) {
    live = live || {};
    var input = normalizeInput(rawInput, data);
    // Remember which figures the user supplied, so state defaults never overwrite them.
    input.rawInsurance = toNum(rawInput.insurance, NaN);
    input.rawGas = toNum(rawInput.gasUsdPerGal, NaN);
    input.rawElec = toNum(rawInput.elecUsdPerKwh, NaN);
    applyStateDefaults(input, data);

    // Live overrides: network data wins over baked defaults when present.
    if (isNum(live.mpg) && !isNum(rawInput.mpg)) input.mpg = live.mpg;
    if (isNum(live.gasUsdPerGal)) input.gasUsdPerGal = live.gasUsdPerGal;
    if (isNum(live.elecUsdPerKwh)) input.elecUsdPerKwh = live.elecUsdPerKwh;

    var fair = fairValue(input, data);
    var priceDelta = fair.value > 0 ? (input.asking - fair.value) / fair.value : 0;
    var purchase = purchaseCosts(input, data);
    var otd = purchase.otd;
    var principal = purchase.principal;
    var loan = input.financed
      ? loanSchedule(principal, input.apr, input.termMonths)
      : { payment: 0, totalInterest: 0, balanceByYear: [0], months: 0 };

    var tco = totalCostOfOwnership(input, data, loan);

    var lifeMiles = lifespanMiles(input.brand, input.segment, data);
    var remainingMiles = Math.max(lifeMiles - input.miles, 0);
    var lifeUsed = clamp(input.miles / lifeMiles, 0, 1);
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
      purchase: purchase, lifeMiles: lifeMiles,
      reliability: reliability, safety: safety,
      negotiation: negotiation(fair.value, input),
      valueCurve: valueCurve, balanceCurve: balanceCurve, underwaterUntil: underwaterUntil,
      chartYears: chartYears
    };

    ctx.score = dealScore(ctx);
    ctx.verdict = verdictFor(ctx.score.score);
    ctx.affordability = affordability(input, otd, loan.payment, tco);
    ctx.bakedRecallCount = bakedRecallCount(input, data);
    ctx.principal = principal;
    ctx.cumulative = cumulativeCost(input, data, loan);
    ctx.termComparison = input.financed ? loanTermComparison(input, data) : null;
    ctx.services = serviceOutlook(input, data);
    ctx.monthlyCash = monthlyCashCost(input, data);
    // findings() reads the views above, so it runs last.
    ctx.findings = findings(ctx);
    return ctx;
  }

  return {
    clamp: clamp, toNum: toNum, keyFor: keyFor,
    mileageSlope: mileageSlope, fairValue: fairValue,
    outTheDoor: outTheDoor, negotiation: negotiation,
    depRateAt: depRateAt, projectValue: projectValue,
    loanSchedule: loanSchedule, maintenanceCost: maintenanceCost,
    valueAtMonth: valueAtMonth, termForPayment: termForPayment,
    segmentFactors: segmentFactors, lifespanMiles: lifespanMiles,
    upcomingServices: upcomingServices, serviceOutlook: serviceOutlook,
    stateInfo: stateInfo, purchaseCosts: purchaseCosts, sensitivity: sensitivity,
    applyStateDefaults: applyStateDefaults,
    loanTermComparison: loanTermComparison, cumulativeCost: cumulativeCost,
    priceForScore: priceForScore, bakedRecallCount: bakedRecallCount,
    monthlyCashCost: monthlyCashCost, maxPriceForMonthlyBudget: maxPriceForMonthlyBudget,
    vehicleRecord: vehicleRecord, annualFuelCost: annualFuelCost,
    totalCostOfOwnership: totalCostOfOwnership, affordability: affordability,
    reliabilityScore: reliabilityScore, safetyPoints: safetyPoints,
    isSafetyCritical: isSafetyCritical, dealScore: dealScore, verdictFor: verdictFor,
    findings: findings, normalizeInput: normalizeInput, analyze: analyze,
    CONDITION_FACTOR: CONDITION_FACTOR, TITLE_FACTOR: TITLE_FACTOR, ACCIDENT_FACTOR: ACCIDENT_FACTOR
  };
}));
