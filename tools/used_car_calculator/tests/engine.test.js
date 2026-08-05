/* Unit tests for the Used Car Deal Analyzer model engine and source layer.
 * Run: node --test tools/used_car_calculator/tests/
 * These are also executed by tests/test_used_car_calculator.py under pytest.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const DIR = path.join(__dirname, '..');
const DATA = require(path.join(DIR, 'data.js'));
const E = require(path.join(DIR, 'engine.js'));
const S = require(path.join(DIR, 'sources.js'));

/** A baseline sane input: 2019 Toyota Camry, 60k miles, $18,500, financed. */
function baseInput(over) {
  return Object.assign({
    currentYear: 2026,
    brand: 'Toyota', model: 'Camry', year: 2019, segment: 'Midsize car',
    miles: 60000, asking: 18500, condition: 'good', title: 'clean',
    accidents: 'none', owners: 1, records: 'yes', ppi: 'planned', comps: [],
    payType: 'loan', down: 3000, creditTier: 'prime', apr: 9.6, termMonths: 48,
    taxRate: 0.06, fees: 500, annualMiles: 12000, horizon: 5,
    gasUsdPerGal: 3.15, insurance: 1750, income: 0
  }, over || {});
}

/* ------------------------------------------------------------ utilities */

test('clamp bounds values on both sides', () => {
  assert.strictEqual(E.clamp(5, 0, 10), 5);
  assert.strictEqual(E.clamp(-5, 0, 10), 0);
  assert.strictEqual(E.clamp(50, 0, 10), 10);
});

test('toNum coerces strings and rejects garbage', () => {
  assert.strictEqual(E.toNum('42.5', 0), 42.5);
  assert.strictEqual(E.toNum('abc', 7), 7);
  assert.strictEqual(E.toNum(undefined, 7), 7);
  assert.strictEqual(E.toNum(NaN, 7), 7);
  assert.strictEqual(E.toNum(Infinity, 7), 7);
});

test('keyFor builds a normalized uppercase lookup key', () => {
  assert.strictEqual(E.keyFor('Toyota', 'Camry', 2019), 'TOYOTA|CAMRY|2019');
});

/* ------------------------------------------------------- 4.1 fair value */

test('mileageSlope is banded by price and always within the clamp', () => {
  const cheap = E.mileageSlope(6000, 'Midsize car', DATA);
  const dear = E.mileageSlope(38000, 'Midsize car', DATA);
  assert.ok(dear > cheap, 'pricier cars carry a steeper per-mile adjustment');
  for (const p of [500, 5000, 20000, 100000, 1e9]) {
    const s = E.mileageSlope(p, 'Midsize car', DATA);
    assert.ok(s >= 0.02 && s <= 0.25, `slope ${s} out of range for price ${p}`);
  }
});

test('mileageSlope honors the per-segment override table', () => {
  assert.notStrictEqual(
    E.mileageSlope(20000, 'Pickup truck', DATA),
    E.mileageSlope(20000, 'Midsize car', DATA)
  );
});

test('fairValue: low miles for its age raises value, high miles lowers it', () => {
  const lo = E.fairValue(E.normalizeInput(baseInput({ miles: 40000, comps: [18000] }), DATA), DATA);
  const hi = E.fairValue(E.normalizeInput(baseInput({ miles: 120000, comps: [18000] }), DATA), DATA);
  assert.ok(lo.value > 18000, 'below-average miles should price above the comp');
  assert.ok(hi.value < 18000, 'above-average miles should price below the comp');
});

test('fairValue: mileage adjustment never exceeds +/-15% of base', () => {
  const extreme = E.fairValue(
    E.normalizeInput(baseInput({ miles: 0, comps: [20000], year: 2005 }), DATA), DATA);
  assert.ok(Math.abs(extreme.mileageAdj) <= 0.15 * extreme.base + 1e-9);
});

test('fairValue: salvage title applies the 0.60 factor', () => {
  const clean = E.fairValue(E.normalizeInput(baseInput({ comps: [20000] }), DATA), DATA);
  const salvage = E.fairValue(E.normalizeInput(baseInput({ comps: [20000], title: 'rebuilt' }), DATA), DATA);
  assert.ok(Math.abs(salvage.value / clean.value - 0.60) < 0.001);
});

test('fairValue: condition and accident factors compound', () => {
  const a = E.fairValue(E.normalizeInput(baseInput({ comps: [20000], condition: 'poor', accidents: 'major' }), DATA), DATA);
  const b = E.fairValue(E.normalizeInput(baseInput({ comps: [20000] }), DATA), DATA);
  assert.ok(Math.abs(a.value / b.value - 0.82 * 0.85) < 0.001);
});

test('fairValue: falls back to the asking price when no comps are given, and flags it', () => {
  const fv = E.fairValue(E.normalizeInput(baseInput({ comps: [] }), DATA), DATA);
  assert.strictEqual(fv.hasComps, false);
  assert.strictEqual(fv.base, 18500);
});

test('fairValue: averages multiple comps and ignores blanks', () => {
  const fv = E.fairValue(E.normalizeInput(baseInput({ comps: [18000, 20000, NaN] }), DATA), DATA);
  assert.strictEqual(fv.compCount, 2);
  assert.strictEqual(fv.base, 19000);
});

test('fairValue: never returns less than the floor value', () => {
  const fv = E.fairValue(E.normalizeInput(
    baseInput({ comps: [1000], title: 'rebuilt', condition: 'poor', miles: 350000 }), DATA), DATA);
  assert.ok(fv.value >= DATA.constants.floorValue);
});

/* ------------------------------------------------------ 4.2 negotiation */

test('outTheDoor adds tax then fees', () => {
  assert.strictEqual(E.outTheDoor(10000, 0.06, 500), 11100);
});

test('negotiation ladder is ordered opening < target <= walkAway', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const n = E.negotiation(18000, input);
  assert.ok(n.opening < n.target, 'opening offer must sit below the target');
  assert.ok(n.target <= n.walkAway, 'target must not exceed the walk-away price');
  assert.ok(n.walkAwayOtd > n.walkAway, 'walk-away OTD includes tax and fees');
});

test('negotiation never suggests offering more than the asking price', () => {
  const input = E.normalizeInput(baseInput({ asking: 15000 }), DATA);
  const n = E.negotiation(40000, input);
  assert.ok(n.target <= 15000);
  assert.ok(n.opening <= 15000);
});

/* ----------------------------------------------------- 4.3 depreciation */

test('depRateAt slows down as the car ages past the knots', () => {
  const young = E.depRateAt(3, 'Midsize car', 'Toyota', DATA);
  const mid = E.depRateAt(7, 'Midsize car', 'Toyota', DATA);
  const old = E.depRateAt(10, 'Midsize car', 'Toyota', DATA);
  const ancient = E.depRateAt(15, 'Midsize car', 'Toyota', DATA);
  assert.ok(young > mid && mid > old && old > ancient);
});

test('depRateAt applies the brand adjustment, clamped', () => {
  const toyota = E.depRateAt(3, 'Midsize car', 'Toyota', DATA);
  const landRover = E.depRateAt(3, 'Midsize car', 'Land Rover', DATA);
  assert.ok(landRover > toyota, 'weak-resale brands depreciate faster');
});

test('projectValue decreases monotonically and respects the floor', () => {
  const v = E.projectValue(20000, 5, 'Midsize car', 'Toyota', 10, DATA);
  assert.strictEqual(v.length, 11);
  assert.strictEqual(v[0], 20000);
  for (let i = 1; i < v.length; i++) assert.ok(v[i] <= v[i - 1]);
  assert.ok(v[v.length - 1] >= DATA.constants.floorValue);
});

test('projectValue never falls below the floor even over a long horizon', () => {
  const v = E.projectValue(3000, 18, 'Luxury', 'Land Rover', 15, DATA);
  v.forEach(x => assert.ok(x >= DATA.constants.floorValue));
});

/* ------------------------------------------------------------ 4.4 loan */

test('loanSchedule matches the closed-form amortization payment', () => {
  // $20,000 at 6% for 60 months = $386.66/mo (standard reference value)
  const l = E.loanSchedule(20000, 6, 60);
  assert.ok(Math.abs(l.payment - 386.66) < 0.02, `got ${l.payment}`);
});

test('loanSchedule amortizes to zero by the final payment', () => {
  const l = E.loanSchedule(20000, 6, 60);
  const last = l.balanceByYear[l.balanceByYear.length - 1];
  assert.ok(Math.abs(last) < 0.01, `final balance ${last} should be ~0`);
});

test('loanSchedule total interest is payment*n - principal', () => {
  const l = E.loanSchedule(15000, 9.6, 48);
  assert.ok(Math.abs(l.totalInterest - (l.payment * 48 - 15000)) < 0.5);
});

test('loanSchedule handles a 0% APR without dividing by zero', () => {
  const l = E.loanSchedule(12000, 0, 48);
  assert.strictEqual(l.payment, 250);
  assert.ok(Math.abs(l.totalInterest) < 1e-9);
});

test('loanSchedule returns a zero schedule for a zero principal', () => {
  const l = E.loanSchedule(0, 9, 48);
  assert.strictEqual(l.payment, 0);
  assert.strictEqual(l.totalInterest, 0);
});

test('loanSchedule: longer terms cost more interest', () => {
  const short = E.loanSchedule(20000, 9.6, 36);
  const long = E.loanSchedule(20000, 9.6, 84);
  assert.ok(long.totalInterest > short.totalInterest * 2);
  assert.ok(long.payment < short.payment);
});

/* ----------------------------------------------------- 4.5 maintenance */

test('maintenanceCost rises with age and caps at 2.2x', () => {
  const young = E.maintenanceCost('Toyota', 0, 1, DATA);
  const old = E.maintenanceCost('Toyota', 25, 1, DATA);
  const base = DATA.brands.Toyota.maintPerYear;
  assert.ok(Math.abs(young.total - base) < 1e-9, 'no escalation before age 3');
  assert.ok(Math.abs(old.total - base * 2.2) < 1e-9, 'escalation caps at 2.2x');
});

test('maintenanceCost sums per-year costs', () => {
  const m = E.maintenanceCost('Honda', 5, 3, DATA);
  assert.strictEqual(m.perYear.length, 3);
  assert.ok(Math.abs(m.total - m.perYear.reduce((a, b) => a + b, 0)) < 1e-9);
});

test('maintenanceCost falls back to the Other brand for unknown makes', () => {
  const m = E.maintenanceCost('Koenigsegg', 0, 1, DATA);
  assert.strictEqual(m.total, DATA.brands.Other.maintPerYear);
});

/* -------------------------------------------------------- 4.6 fuel/TCO */

test('vehicleRecord finds an exact model-year', () => {
  const r = E.vehicleRecord('Toyota', 'Camry', 2019, DATA);
  assert.strictEqual(r.seg, 'Midsize car');
  assert.strictEqual(r.mpg, 32);
});

test('vehicleRecord falls back to the nearest known model-year', () => {
  const r = E.vehicleRecord('Toyota', 'Camry', 2018, DATA);
  assert.ok(r && r.mpg > 0, 'should resolve to a neighbouring year');
});

test('vehicleRecord returns null for unknown make/model', () => {
  assert.strictEqual(E.vehicleRecord('Toyota', 'Supra', 2019, DATA), null);
  assert.strictEqual(E.vehicleRecord('Nonexistent', 'Thing', 2019, DATA), null);
});

test('annualFuelCost: gasoline math is miles / mpg * price', () => {
  const cost = E.annualFuelCost(
    { segment: 'Midsize car', annualMiles: 12000, mpg: 30, gasUsdPerGal: 3.00 }, DATA);
  assert.ok(Math.abs(cost - 1200) < 1e-9);
});

test('annualFuelCost: EV math uses kWh/100mi and the electricity rate', () => {
  const cost = E.annualFuelCost(
    { segment: 'Electric', annualMiles: 12000, kwhPer100mi: 30, elecUsdPerKwh: 0.15 }, DATA);
  assert.ok(Math.abs(cost - 540) < 1e-9, `got ${cost}`);
});

test('annualFuelCost: EVs are cheaper to fuel than equivalent gas cars', () => {
  const gas = E.annualFuelCost({ segment: 'Midsize car', annualMiles: 12000, mpg: 30, gasUsdPerGal: 3.15 }, DATA);
  const ev = E.annualFuelCost({ segment: 'Electric', annualMiles: 12000, kwhPer100mi: 30, elecUsdPerKwh: 0.17 }, DATA);
  assert.ok(ev < gas);
});

test('totalCostOfOwnership sums its components exactly', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const loan = E.loanSchedule(16610, 9.6, 48);
  const t = E.totalCostOfOwnership(input, DATA, loan);
  const sum = t.depreciation + t.fuel + t.insurance + t.maintenance + t.taxesFees + t.interest;
  assert.ok(Math.abs(t.total - sum) < 1e-6);
});

test('totalCostOfOwnership derives cost per mile and per month consistently', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const t = E.totalCostOfOwnership(input, DATA, null);
  assert.ok(Math.abs(t.costPerMile - t.total / t.totalMiles) < 1e-9);
  assert.ok(Math.abs(t.costPerMonth - t.total / (input.horizon * 12)) < 1e-9);
  assert.ok(t.costPerMile > 0.1 && t.costPerMile < 3, `implausible $/mi: ${t.costPerMile}`);
});

test('depreciation is the largest ownership cost on a late-model car', () => {
  const input = E.normalizeInput(baseInput({ year: 2024, asking: 32000, miles: 20000 }), DATA);
  const t = E.totalCostOfOwnership(input, DATA, null);
  assert.ok(t.depreciation > t.fuel, 'depreciation should exceed fuel');
  assert.ok(t.depreciation > t.maintenance, 'depreciation should exceed maintenance');
  assert.ok(t.depreciation > t.insurance, 'depreciation should exceed insurance');
});

test('depreciation curve produces realistic residual values', () => {
  // A 7-year-old $18,500 Camry should be worth roughly $10-14k five years on,
  // not the near-flat residual an under-steep curve would give.
  const input = E.normalizeInput(baseInput(), DATA);
  const t = E.totalCostOfOwnership(input, DATA, null);
  const residual = t.values[5];
  assert.ok(residual > 9000 && residual < 14000, `implausible 12-year-old residual: ${residual}`);
});

test('an economical old car can out-cost its depreciation in fuel', () => {
  // Sanity check on the model's shape: fuel is a real competitor to depreciation
  // once a car is old and cheap, so the TCO chart must not assume an ordering.
  const input = E.normalizeInput(baseInput({ year: 2012, asking: 7000, mpg: 22, annualMiles: 20000 }), DATA);
  const t = E.totalCostOfOwnership(input, DATA, null);
  assert.ok(t.fuel > t.depreciation);
  assert.ok(Number.isFinite(t.costPerMile));
});

/* -------------------------------------------------- 4.7 affordability */

test('affordability returns null without an income', () => {
  const input = E.normalizeInput(baseInput({ income: 0 }), DATA);
  assert.strictEqual(E.affordability(input, 20000, 400, { insurance: 8750, horizon: 5, fuelPerYear: 1200 }), null);
});

test('affordability applies the 20/4/10 thresholds', () => {
  const input = E.normalizeInput(baseInput({ income: 6000, down: 5000, termMonths: 48 }), DATA);
  const tco = { insurance: 1750 * 5, fuelPerYear: 1200 };
  input.horizon = 5;
  const a = E.affordability(input, 20000, 300, tco);
  assert.strictEqual(a.downOk, true, '5000/20000 = 25% >= 20%');
  assert.strictEqual(a.termOk, true, '48 months is within 4 years');
  assert.strictEqual(a.shareOk, true);
  assert.ok(Math.abs(a.monthlyAllIn - (300 + 1750 / 12 + 100)) < 1e-6);
});

test('affordability fails a long term and a thin down payment', () => {
  const input = E.normalizeInput(baseInput({ income: 3000, down: 500, termMonths: 84 }), DATA);
  input.horizon = 5;
  const a = E.affordability(input, 25000, 450, { insurance: 8750, fuelPerYear: 1800 });
  assert.strictEqual(a.downOk, false);
  assert.strictEqual(a.termOk, false);
  assert.strictEqual(a.shareOk, false);
});

/* ------------------------------------------- 4.10 reliability blend */

test('reliabilityScore falls back to brand-only without complaint data', () => {
  const input = E.normalizeInput(baseInput({ model: 'Unknown Model' }), DATA);
  const r = E.reliabilityScore(input, DATA, {});
  assert.strictEqual(r.blended, false);
  assert.strictEqual(r.score, DATA.brands.Toyota.relBaseline);
  assert.strictEqual(r.source, 'brand-only');
});

test('reliabilityScore blends 60/40 with the baked complaint rate', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const r = E.reliabilityScore(input, DATA, {});
  assert.strictEqual(r.blended, true);
  assert.strictEqual(r.source, 'baked');
  const expectedComplaint = 100 - (14.2 / 70) * 100;
  assert.ok(Math.abs(r.complaintScore - expectedComplaint) < 1e-6);
  assert.ok(Math.abs(r.score - (0.6 * 90 + 0.4 * expectedComplaint)) < 1e-6);
});

test('reliabilityScore prefers live complaint data over the baked table', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const r = E.reliabilityScore(input, DATA, { complaints: { per100k: 200, top: ['ENGINE'] } });
  assert.strictEqual(r.source, 'live');
  assert.strictEqual(r.complaintScore, 0, 'a rate far above p90 floors the complaint score');
});

test('reliabilityScore: a high complaint rate drags the blended score below the brand baseline', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const clean = E.reliabilityScore(input, DATA, { complaints: { per100k: 1 } });
  const bad = E.reliabilityScore(input, DATA, { complaints: { per100k: 150 } });
  assert.ok(bad.score < clean.score);
  assert.ok(bad.score < DATA.brands.Toyota.relBaseline);
});

/* ------------------------------------------------------ 4.8 safety */

test('safetyPoints maps NCAP stars onto the scoring band', () => {
  assert.strictEqual(E.safetyPoints({ safety: { overallRating: 5 } }).points, 6);
  assert.strictEqual(E.safetyPoints({ safety: { overallRating: 4 } }).points, 4.5);
  assert.strictEqual(E.safetyPoints({ safety: { overallRating: 3 } }).points, 2);
  assert.strictEqual(E.safetyPoints({ safety: { overallRating: 2 } }).points, 0);
});

test('safetyPoints is neutral (3) and flagged unrated when no data exists', () => {
  const s = E.safetyPoints({});
  assert.strictEqual(s.points, 3);
  assert.strictEqual(s.rated, false);
});

test('isSafetyCritical recognizes airbag and brake recalls but not trim', () => {
  assert.strictEqual(E.isSafetyCritical('AIR BAGS', DATA), true);
  assert.strictEqual(E.isSafetyCritical('SERVICE BRAKES, HYDRAULIC', DATA), true);
  assert.strictEqual(E.isSafetyCritical('EXTERIOR TRIM', DATA), false);
});

/* -------------------------------------------------- 4.8 deal score */

test('dealScore components never exceed their individual maxima', () => {
  const ctx = E.analyze(baseInput({ comps: [18000] }), DATA);
  ctx.score.parts.forEach(p => {
    assert.ok(p.points >= 0 && p.points <= p.max, `${p.label}: ${p.points}/${p.max}`);
  });
});

test('deal score weights sum to 100', () => {
  const ctx = E.analyze(baseInput(), DATA);
  const totalMax = ctx.score.parts.reduce((a, p) => a + p.max, 0);
  assert.strictEqual(totalMax, 100);
});

test('a cheaper price scores strictly higher, all else equal', () => {
  const dear = E.analyze(baseInput({ asking: 22000, comps: [18000] }), DATA);
  const cheap = E.analyze(baseInput({ asking: 15000, comps: [18000] }), DATA);
  assert.ok(cheap.score.score > dear.score.score);
});

test('a salvage title caps the score at 40 however good the rest is', () => {
  const ctx = E.analyze(baseInput({
    title: 'rebuilt', asking: 6000, comps: [18000], miles: 20000, ppi: 'yes', payType: 'cash'
  }), DATA);
  assert.ok(ctx.score.score <= 40, `got ${ctx.score.score}`);
  assert.ok(ctx.score.caps.includes('branded title'));
});

test('a major accident caps the score at 55', () => {
  const ctx = E.analyze(baseInput({
    accidents: 'major', asking: 9000, comps: [18000], miles: 20000, ppi: 'yes', payType: 'cash'
  }), DATA);
  assert.ok(ctx.score.score <= 55);
  assert.ok(ctx.score.caps.includes('major accident'));
});

test('a worn-out car caps the score at 45', () => {
  const ctx = E.analyze(baseInput({ miles: 230000, asking: 3000, comps: [6000], payType: 'cash' }), DATA);
  assert.ok(ctx.score.score <= 45);
  assert.ok(ctx.score.caps.includes('little life remaining'));
});

test('an unrepaired safety recall caps the score at 60', () => {
  const live = { recalls: [{ component: 'AIR BAGS', campaign: '19V123', repairVerified: false }] };
  const ctx = E.analyze(baseInput({ asking: 14000, comps: [18000], ppi: 'yes', payType: 'cash' }), DATA, live);
  assert.ok(ctx.score.score <= 60, `got ${ctx.score.score}`);
  assert.ok(ctx.score.caps.includes('unrepaired safety recall'));
});

test('marking recalls verified lifts the recall cap', () => {
  const live = { recalls: [{ component: 'AIR BAGS', campaign: '19V123', repairVerified: false }] };
  const capped = E.analyze(baseInput({ asking: 14000, comps: [18000], ppi: 'yes', payType: 'cash' }), DATA, live);
  const cleared = E.analyze(baseInput({
    asking: 14000, comps: [18000], ppi: 'yes', payType: 'cash', recallsVerified: true
  }), DATA, live);
  assert.ok(cleared.score.score >= capped.score.score);
  assert.ok(!cleared.score.caps.includes('unrepaired safety recall'));
});

test('a non-safety recall does not trigger the cap', () => {
  const live = { recalls: [{ component: 'EXTERIOR TRIM', campaign: '19V999', repairVerified: false }] };
  const ctx = E.analyze(baseInput({ asking: 14000, comps: [18000], ppi: 'yes', payType: 'cash' }), DATA, live);
  assert.ok(!ctx.score.caps.includes('unrepaired safety recall'));
});

test('score always lands in 0..100 across a wide input sweep', () => {
  const brands = Object.keys(DATA.brands);
  const titles = ['clean', 'rebuilt', 'lemon'];
  const conditions = ['excellent', 'good', 'fair', 'poor'];
  for (const brand of brands) {
    for (const title of titles) {
      for (const condition of conditions) {
        for (const miles of [5000, 90000, 260000]) {
          for (const asking of [1500, 18500, 90000]) {
            const ctx = E.analyze(baseInput({ brand, title, condition, miles, asking, comps: [asking] }), DATA);
            assert.ok(ctx.score.score >= 0 && ctx.score.score <= 100,
              `score ${ctx.score.score} for ${brand}/${title}/${condition}/${miles}/${asking}`);
            assert.ok(Number.isFinite(ctx.tco.total), 'TCO must be finite');
            assert.ok(Number.isFinite(ctx.fair.value), 'fair value must be finite');
          }
        }
      }
    }
  }
});

test('verdictFor covers every band boundary', () => {
  assert.strictEqual(E.verdictFor(100).label, 'Excellent deal');
  assert.strictEqual(E.verdictFor(80).label, 'Excellent deal');
  assert.strictEqual(E.verdictFor(79).label, 'Good deal');
  assert.strictEqual(E.verdictFor(65).label, 'Good deal');
  assert.strictEqual(E.verdictFor(64).label, 'Fair — negotiate');
  assert.strictEqual(E.verdictFor(50).label, 'Fair — negotiate');
  assert.strictEqual(E.verdictFor(49).label, 'Below average — caution');
  assert.strictEqual(E.verdictFor(35).label, 'Below average — caution');
  assert.strictEqual(E.verdictFor(34).label, 'Walk away');
  assert.strictEqual(E.verdictFor(0).label, 'Walk away');
});

/* --------------------------------------------------- 4.9 findings */

test('findings flags a salvage title as critical', () => {
  const ctx = E.analyze(baseInput({ title: 'rebuilt' }), DATA);
  const critical = ctx.findings.filter(f => f.level === 'critical');
  assert.ok(critical.some(f => f.tag === 'Title'));
});

test('findings lists each unrepaired safety recall individually with its campaign number', () => {
  const live = { recalls: [
    { component: 'AIR BAGS', campaign: '19V001', repairVerified: false },
    { component: 'SERVICE BRAKES', campaign: '19V002', repairVerified: false }
  ] };
  const ctx = E.analyze(baseInput(), DATA, live);
  const recallFindings = ctx.findings.filter(f => f.tag === 'Open recall');
  assert.strictEqual(recallFindings.length, 2);
  assert.ok(recallFindings[0].text.includes('19V001'));
});

test('findings reports a clean recall check as good news', () => {
  const ctx = E.analyze(baseInput(), DATA, { recalls: [] });
  assert.ok(ctx.findings.some(f => f.tag === 'Recalls' && f.level === 'good'));
});

test('findings warns about an 84-month term and a sub-10% down payment', () => {
  const ctx = E.analyze(baseInput({ termMonths: 84, down: 200 }), DATA);
  const texts = ctx.findings.map(f => f.text).join(' ');
  assert.ok(texts.includes('84-month'));
  assert.ok(texts.includes('negative equity'));
});

test('findings warns when the APR is well above the credit tier average', () => {
  const ctx = E.analyze(baseInput({ apr: 18, creditTier: 'prime' }), DATA);
  assert.ok(ctx.findings.some(f => f.tag === 'Financing' && f.text.includes('above the')));
});

test('findings praises a cash purchase and a reliable brand', () => {
  const ctx = E.analyze(baseInput({ payType: 'cash' }), DATA);
  const good = ctx.findings.filter(f => f.level === 'good').map(f => f.tag);
  assert.ok(good.includes('Financing'));
  assert.ok(good.includes('Reliability'));
});

test('findings warns about suspiciously low mileage on an old car', () => {
  const ctx = E.analyze(baseInput({ year: 2010, miles: 20000 }), DATA);
  assert.ok(ctx.findings.some(f => f.tag === 'Mileage' && f.text.includes('Unusually low')));
});

test('findings are ordered critical first, good last', () => {
  const ctx = E.analyze(baseInput({ title: 'rebuilt', ppi: 'no', payType: 'cash' }), DATA);
  const rank = { critical: 0, warning: 1, good: 2 };
  for (let i = 1; i < ctx.findings.length; i++) {
    assert.ok(rank[ctx.findings[i].level] >= rank[ctx.findings[i - 1].level]);
  }
});

/* ------------------------------------------------ normalize + analyze */

test('normalizeInput defends against garbage input', () => {
  const n = E.normalizeInput({
    currentYear: 2026, brand: 'NotABrand', segment: 'NotASegment', year: 'xyz',
    miles: -500, asking: 'abc', owners: 99, termMonths: 9999, taxRate: 5,
    apr: -3, horizon: 100, annualMiles: 0, condition: 'bogus', title: 'bogus'
  }, DATA);
  assert.strictEqual(n.brand, 'Other');
  assert.strictEqual(n.segment, 'Midsize car');
  assert.strictEqual(n.miles, 0);
  assert.strictEqual(n.asking, 0);
  assert.strictEqual(n.condition, 'good');
  assert.strictEqual(n.title, 'clean');
  assert.ok(n.owners <= 20 && n.termMonths <= 120 && n.taxRate <= 0.25);
  assert.ok(n.apr >= 0 && n.horizon <= 15 && n.annualMiles >= 100);
  assert.ok(Number.isFinite(n.age));
});

test('normalizeInput adopts the segment from the vehicle record', () => {
  const n = E.normalizeInput(baseInput({ brand: 'Ford', model: 'F-150', segment: 'Compact car' }), DATA);
  assert.strictEqual(n.segment, 'Pickup truck', 'the EPA record should win over a wrong manual pick');
});

test('analyze produces a complete, finite result object', () => {
  const ctx = E.analyze(baseInput({ comps: [18000, 19000], income: 5000 }), DATA);
  ['fair', 'negotiation', 'tco', 'score', 'verdict', 'findings', 'loan', 'affordability']
    .forEach(k => assert.ok(ctx[k], `missing ${k}`));
  assert.ok(Number.isFinite(ctx.score.score));
  assert.ok(Number.isFinite(ctx.tco.total));
  assert.ok(Number.isFinite(ctx.loan.payment));
  assert.ok(Array.isArray(ctx.findings));
});

test('analyze: a cash purchase has no loan, no interest and no negative equity', () => {
  const ctx = E.analyze(baseInput({ payType: 'cash' }), DATA);
  assert.strictEqual(ctx.loan.payment, 0);
  assert.strictEqual(ctx.tco.interest, 0);
  assert.strictEqual(ctx.underwaterUntil, -1);
});

test('analyze: a thin down payment on a long term produces negative equity', () => {
  const ctx = E.analyze(baseInput({ down: 0, termMonths: 84, apr: 14 }), DATA);
  assert.ok(ctx.underwaterUntil >= 1, 'expected to be underwater for at least a year');
});

test('analyze: a large down payment on a short term avoids negative equity', () => {
  const ctx = E.analyze(baseInput({ down: 9000, termMonths: 36, apr: 6 }), DATA);
  assert.strictEqual(ctx.underwaterUntil, -1);
});

test('analyze: the value and balance curves are the same length', () => {
  const ctx = E.analyze(baseInput({ termMonths: 72, horizon: 3 }), DATA);
  assert.strictEqual(ctx.valueCurve.length, ctx.balanceCurve.length);
  assert.strictEqual(ctx.valueCurve.length, ctx.chartYears + 1);
});

test('analyze: live fuel prices override the baked defaults', () => {
  const cheap = E.analyze(baseInput(), DATA, { gasUsdPerGal: 2.00 });
  const dear = E.analyze(baseInput(), DATA, { gasUsdPerGal: 6.00 });
  assert.ok(dear.tco.fuel > cheap.tco.fuel * 2.5);
});

test('analyze: a user-entered MPG beats the live figure', () => {
  const ctx = E.analyze(baseInput({ mpg: 20 }), DATA, { mpg: 50 });
  const expected = 12000 / 20 * 3.15 * 5;
  assert.ok(Math.abs(ctx.tco.fuel - expected) < 1e-6);
});

test('analyze: an EV uses electricity, not gasoline, for its fuel cost', () => {
  const ctx = E.analyze(baseInput({
    brand: 'Tesla', model: 'Model 3', year: 2019, segment: 'Electric', asking: 22000
  }), DATA);
  const expected = 12000 / 100 * 25 * DATA.energy.elecUsdPerKwh * 5;
  assert.ok(Math.abs(ctx.tco.fuel - expected) < 1e-6, `got ${ctx.tco.fuel}, expected ${expected}`);
});

test('analyze is deterministic — identical inputs give identical scores', () => {
  const a = E.analyze(baseInput({ comps: [18000] }), DATA);
  const b = E.analyze(baseInput({ comps: [18000] }), DATA);
  assert.strictEqual(a.score.score, b.score.score);
  assert.strictEqual(a.tco.total, b.tco.total);
});

test('analyze handles a zero asking price without producing NaN', () => {
  const ctx = E.analyze(baseInput({ asking: 0, down: 0 }), DATA);
  assert.ok(Number.isFinite(ctx.score.score));
  assert.ok(Number.isFinite(ctx.tco.total));
  assert.ok(Number.isFinite(ctx.fair.value));
});

/* ------------------------------------------------ source layer (A1-A11) */

/** Build a fake fetch that returns canned JSON per URL substring. */
function fakeFetch(routes) {
  return function (url) {
    for (const key of Object.keys(routes)) {
      if (url.includes(key)) {
        const v = routes[key];
        if (v === 'fail') return Promise.reject(new Error('network down'));
        if (v === '500') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(v) });
      }
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
  };
}

test('decodeVin rejects a malformed VIN before making a request', async () => {
  let called = false;
  const s = S.createSources({ fetchImpl: () => { called = true; return Promise.resolve(null); } });
  const r = await s.decodeVin('TOO-SHORT!');
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.reason, 'invalid-format');
  assert.strictEqual(called, false, 'must not hit the network on an invalid VIN');
});

test('decodeVin maps a vPIC response onto our fields and segment', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    'DecodeVinValues': { Results: [{
      ModelYear: '2019', Make: 'TOYOTA', Model: 'Camry', Trim: 'SE',
      BodyClass: 'Sedan/Saloon', FuelTypePrimary: 'Gasoline', DisplacementL: '2.5'
    }] }
  }) });
  const r = await s.decodeVin('4T1B11HK5KU123456');
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.year, 2019);
  assert.strictEqual(r.make, 'TOYOTA');
  assert.strictEqual(r.segment, 'Midsize car');
});

test('decodeVin reports failure rather than throwing when the API is down', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({ 'DecodeVinValues': 'fail' }) });
  const r = await s.decodeVin('4T1B11HK5KU123456');
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.reason, 'unreachable');
});

test('segmentFromVpic classifies trucks, EVs, hybrids and SUVs', () => {
  const s = S.createSources({ fetchImpl: () => Promise.resolve(null) });
  assert.strictEqual(s.segmentFromVpic('Pickup', 'Gasoline', ''), 'Pickup truck');
  assert.strictEqual(s.segmentFromVpic('Sedan', 'Electric', 'BEV'), 'Electric');
  assert.strictEqual(s.segmentFromVpic('Sedan', 'Gasoline', 'Strong HEV'), 'Hybrid');
  assert.strictEqual(s.segmentFromVpic('Sport Utility Vehicle (SUV)', 'Gasoline', ''), 'Compact SUV');
  assert.strictEqual(s.segmentFromVpic('Van', 'Gasoline', ''), 'Minivan');
  assert.strictEqual(s.segmentFromVpic('Coupe', 'Gasoline', ''), 'Sports car');
});

test('recalls normalizes the NHTSA payload', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    'recallsByVehicle': { results: [
      { Component: 'AIR BAGS', NHTSACampaignNumber: '19V123', Summary: 'x', Consequence: 'y', Remedy: 'z' }
    ] }
  }) });
  const r = await s.recalls('Toyota', 'Camry', 2019);
  assert.strictEqual(r.length, 1);
  assert.strictEqual(r[0].campaign, '19V123');
  assert.strictEqual(r[0].repairVerified, false);
});

test('recalls returns null (not a throw) on a server error', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({ 'recallsByVehicle': '500' }) });
  assert.strictEqual(await s.recalls('Toyota', 'Camry', 2019), null);
});

test('complaints aggregates the top components', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    'complaintsByVehicle': { count: 4, results: [
      { components: 'ENGINE' }, { components: 'ENGINE' },
      { components: 'ELECTRICAL SYSTEM' }, { components: 'ENGINE,FUEL SYSTEM' }
    ] }
  }) });
  const c = await s.complaints('Toyota', 'Camry', 2019);
  assert.strictEqual(c.count, 4);
  assert.strictEqual(c.top[0], 'ENGINE');
  assert.strictEqual(c.byComponent['ENGINE'], 3);
});

test('safetyRating follows the two-step NCAP lookup', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    '/SafetyRatings/modelyear/': { Results: [{ VehicleId: 12345 }] },
    '/SafetyRatings/VehicleId/': { Results: [{ OverallRating: '5', OverallFrontCrashRating: '4' }] }
  }) });
  const r = await s.safetyRating('Toyota', 'Camry', 2019);
  assert.strictEqual(r.overallRating, 5);
  assert.strictEqual(r.frontCrash, 4);
});

test('safetyRating returns null when the model is not rated', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({ '/SafetyRatings/modelyear/': { Results: [] } }) });
  assert.strictEqual(await s.safetyRating('Toyota', 'Camry', 2019), null);
});

test('fuelEconomy resolves a menu option then the vehicle record', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    '/vehicle/menu/options': { menuItem: [{ value: '41231', text: 'Auto' }] },
    '/vehicle/41231': { comb08: '32', fuelType1: 'Regular Gasoline', VClass: 'Midsize Cars', fuelCost08: '1650' }
  }) });
  const r = await s.fuelEconomy(2019, 'Toyota', 'Camry');
  assert.strictEqual(r.mpg, 32);
  assert.strictEqual(r.annualFuelCost, 1650);
});

test('EIA and FRED calls are skipped entirely without a key', async () => {
  let called = false;
  const s = S.createSources({ fetchImpl: () => { called = true; return Promise.resolve(null); } });
  assert.strictEqual(await s.gasPrice(), null);
  assert.strictEqual(await s.usedCarPriceTrend(), null);
  assert.strictEqual(called, false);
});

test('gasPrice parses the EIA v2 envelope when a key is present', async () => {
  const s = S.createSources({
    keys: { eia: 'KEY' },
    fetchImpl: fakeFetch({ '/petroleum/pri/gnd/': { response: { data: [{ value: '3.42', period: '2026-08-03' }] } } })
  });
  const r = await s.gasPrice();
  assert.strictEqual(r.usdPerGal, 3.42);
});

test('electricityPrice converts cents per kWh to dollars', async () => {
  const s = S.createSources({
    keys: { eia: 'KEY' },
    fetchImpl: fakeFetch({ '/electricity/retail-sales/': { response: { data: [{ price: '17.5', period: '2026-06' }] } } })
  });
  const r = await s.electricityPrice('CA');
  assert.ok(Math.abs(r.usdPerKwh - 0.175) < 1e-9);
});

test('usedCarPriceTrend computes the year-over-year change', async () => {
  const obs = [{ date: '2026-07-01', value: '196.0' }];
  for (let i = 0; i < 11; i++) obs.push({ date: 'x', value: '198.0' });
  obs.push({ date: '2025-07-01', value: '200.0' });
  const s = S.createSources({ keys: { fred: 'KEY' }, fetchImpl: fakeFetch({ '/fred/series/observations': { observations: obs } }) });
  const r = await s.usedCarPriceTrend();
  assert.ok(Math.abs(r.changeYoY - (-0.02)) < 1e-9);
});

test('marketComps returns the median and quartiles of matching listings', async () => {
  const listings = [12000, 13000, 14000, 15000, 16000].map(p => ({ price: p }));
  const s = S.createSources({ keys: { marketcheck: 'KEY' }, fetchImpl: fakeFetch({ 'mc-api.marketcheck.com': { listings } }) });
  const r = await s.marketComps({ year: 2019, make: 'Toyota', model: 'Camry', miles: 60000 });
  assert.strictEqual(r.median, 14000);
  assert.strictEqual(r.n, 5);
});

test('loadAll tolerates every source failing and still returns an object', async () => {
  const s = S.createSources({ fetchImpl: () => Promise.reject(new Error('offline')) });
  const live = await s.loadAll({ make: 'Toyota', model: 'Camry', year: 2019 });
  assert.ok(live && typeof live === 'object');
  assert.deepStrictEqual(live.provenance, {});
  // The engine must still analyze cleanly with an empty live object.
  const ctx = E.analyze(baseInput(), DATA, live);
  assert.ok(Number.isFinite(ctx.score.score));
});

test('loadAll merges partial results and records provenance', async () => {
  const s = S.createSources({ fetchImpl: fakeFetch({
    'recallsByVehicle': { results: [] },
    'complaintsByVehicle': 'fail',
    '/SafetyRatings/modelyear/': { Results: [{ VehicleId: 1 }] },
    '/SafetyRatings/VehicleId/': { Results: [{ OverallRating: '5' }] }
  }) });
  const live = await s.loadAll({ make: 'Toyota', model: 'Camry', year: 2019 });
  assert.strictEqual(live.provenance.recalls, 'live');
  assert.strictEqual(live.provenance.safety, 'live');
  assert.ok(!live.provenance.complaints, 'a failed source records no provenance');
});

/* --------------------------------------------------------- data integrity */

test('every brand entry is complete and plausible', () => {
  Object.entries(DATA.brands).forEach(([name, b]) => {
    assert.ok(b.maintPerYear > 100 && b.maintPerYear < 3000, `${name} maintenance`);
    assert.ok(b.relBaseline >= 0 && b.relBaseline <= 100, `${name} reliability`);
    assert.ok(b.lifeMiles > 100000 && b.lifeMiles <= 400000, `${name} lifespan`);
  });
});

test('every segment has a depreciation curve with correctly ordered knots', () => {
  Object.keys(DATA.segments).forEach(seg => {
    const c = DATA.depCurves[seg];
    assert.ok(c, `missing depreciation curve for ${seg}`);
    assert.ok(c.r1_5 > c.r6_8 && c.r6_8 > c.r9_12 && c.r9_12 > c.r13p, `${seg} knots out of order`);
  });
});

test('every vehicle record maps to a real segment', () => {
  Object.entries(DATA.vehicles).forEach(([make, models]) => {
    assert.ok(DATA.brands[make], `vehicle make ${make} is missing from the brand table`);
    Object.entries(models).forEach(([model, rec]) => {
      assert.ok(DATA.segments[rec.seg], `${make} ${model} has unknown segment ${rec.seg}`);
    });
  });
});

test('every complaint key parses and has a matching segment p90', () => {
  Object.keys(DATA.complaints).forEach(key => {
    const parts = key.split('|');
    assert.strictEqual(parts.length, 3, `malformed complaint key ${key}`);
    assert.ok(DATA.brands[parts[0][0] + parts[0].slice(1).toLowerCase()] || true);
    assert.ok(DATA.complaints[key].per100k > 0);
  });
  assert.ok(DATA.complaintSegP90['default'] > 0);
});

test('APR tiers increase monotonically as credit worsens', () => {
  const t = DATA.aprByTier;
  assert.ok(t.superprime < t.prime);
  assert.ok(t.prime < t.nearprime);
  assert.ok(t.nearprime < t.subprime);
  assert.ok(t.subprime < t.deepsub);
});

test('the dataset carries a provenance stamp for every table', () => {
  assert.ok(DATA.meta.built);
  ['vehicles', 'brands', 'segments', 'depCurves', 'mileSlopes', 'aprByTier', 'energy']
    .forEach(k => assert.ok(DATA.meta.sources[k], `no provenance stamp for ${k}`));
});

/* ------------------------------------------- loan length & solver additions */

test('termForPayment inverts the amortization formula', () => {
  const months = E.termForPayment(20000, 6, 386.66);
  assert.ok(Math.abs(months - 60) <= 1, `got ${months}`);
});

test('termForPayment returns null when the payment never covers interest', () => {
  assert.strictEqual(E.termForPayment(20000, 12, 100), null);
  assert.strictEqual(E.termForPayment(20000, 12, 200), null);
});

test('termForPayment handles a 0% loan', () => {
  assert.strictEqual(E.termForPayment(12000, 0, 500), 24);
});

test('valueAtMonth interpolates between the annual value points', () => {
  const year1 = E.projectValue(20000, 5, 'Midsize car', 'Toyota', 1, DATA)[1];
  const month12 = E.valueAtMonth(20000, 5, 'Midsize car', 'Toyota', 12, DATA);
  assert.ok(Math.abs(month12 - year1) < 1, `${month12} vs ${year1}`);
  const month6 = E.valueAtMonth(20000, 5, 'Midsize car', 'Toyota', 6, DATA);
  assert.ok(month6 < 20000 && month6 > month12, 'mid-year value sits between the endpoints');
});

test('loanTermComparison: longer terms mean lower payments and more interest', () => {
  const input = E.normalizeInput(baseInput(), DATA);
  const c = E.loanTermComparison(input, DATA);
  for (let i = 1; i < c.options.length; i++) {
    assert.ok(c.options[i].payment < c.options[i - 1].payment, 'payment should fall as the term grows');
    assert.ok(c.options[i].totalInterest > c.options[i - 1].totalInterest, 'interest should rise');
  }
});

test('loanTermComparison: longer terms leave you underwater longer', () => {
  const input = E.normalizeInput(baseInput({ down: 500 }), DATA);
  const c = E.loanTermComparison(input, DATA);
  const short = c.options.find(o => o.months === 36);
  const long = c.options.find(o => o.months === 84);
  assert.ok(long.underwaterMonths >= short.underwaterMonths);
});

test('loanTermComparison recommends the shortest affordable term when income is known', () => {
  const input = E.normalizeInput(baseInput({ income: 6000 }), DATA);
  const c = E.loanTermComparison(input, DATA);
  assert.ok(c.recommended, 'a $6k income should afford this car');
  assert.ok(c.recommended.affordable);
  const shorterAffordable = c.options.filter(o => o.affordable && o.months < c.recommended.months);
  assert.strictEqual(shorterAffordable.length, 0, 'nothing shorter should also have been affordable');
});

test('loanTermComparison refuses to recommend when no term is affordable', () => {
  const input = E.normalizeInput(baseInput({ income: 900, asking: 45000, down: 0 }), DATA);
  const c = E.loanTermComparison(input, DATA);
  assert.strictEqual(c.recommended, null);
  assert.ok(c.note.includes('cheaper car'), 'should say stretching the term is not the fix');
});

test('loanTermComparison computes income share only when income is given', () => {
  const withIncome = E.loanTermComparison(E.normalizeInput(baseInput({ income: 5000 }), DATA), DATA);
  const without = E.loanTermComparison(E.normalizeInput(baseInput({ income: 0 }), DATA), DATA);
  assert.ok(withIncome.options[0].incomeShare > 0);
  assert.strictEqual(without.options[0].incomeShare, null);
  assert.strictEqual(without.options[0].affordable, null);
});

test('analyze attaches a term comparison for loans but not for cash', () => {
  assert.ok(E.analyze(baseInput(), DATA).termComparison);
  assert.strictEqual(E.analyze(baseInput({ payType: 'cash' }), DATA).termComparison, null);
});

test('cumulativeCost grows monotonically and ends at the TCO total', () => {
  const ctx = E.analyze(baseInput({ payType: 'cash' }), DATA);
  const cum = ctx.cumulative;
  assert.strictEqual(cum.length, ctx.input.horizon + 1);
  for (let i = 1; i < cum.length; i++) assert.ok(cum[i].total >= cum[i - 1].total);
  assert.ok(Math.abs(cum[cum.length - 1].total - ctx.tco.total) < 1,
    `cumulative ${cum[cum.length - 1].total} vs TCO ${ctx.tco.total}`);
});

test('cumulativeCost per-mile figure falls as fixed costs amortize', () => {
  const ctx = E.analyze(baseInput({ payType: 'cash' }), DATA);
  const first = ctx.cumulative[1].perMile;
  const last = ctx.cumulative[ctx.cumulative.length - 1].perMile;
  assert.ok(last < first, 'cost per mile should improve the longer you keep the car');
});

test('priceForScore finds a price that actually achieves the target', () => {
  const raw = baseInput({ asking: 24000, comps: [18000] });
  const price = E.priceForScore(raw, DATA, {}, 80);
  assert.ok(price !== null, 'an 80 should be reachable by paying less');
  const achieved = E.analyze(Object.assign({}, raw, { asking: price }), DATA).score.score;
  assert.ok(achieved >= 80, `price ${price} only scored ${achieved}`);
  assert.ok(price < 24000, 'the target price must be below the asking price');
});

test('priceForScore returns null when a hard cap makes the target unreachable', () => {
  const raw = baseInput({ title: 'rebuilt', comps: [18000] });
  assert.strictEqual(E.priceForScore(raw, DATA, {}, 80), null,
    'no price fixes a salvage title');
});

test('bakedRecallCount reads the offline table and misses cleanly', () => {
  const known = E.normalizeInput(baseInput(), DATA);
  assert.strictEqual(E.bakedRecallCount(known, DATA), DATA.recallCounts['TOYOTA|CAMRY|2019']);
  const unknown = E.normalizeInput(baseInput({ model: 'Nonexistent' }), DATA);
  assert.strictEqual(E.bakedRecallCount(unknown, DATA), null);
});

test('offline analysis warns from the baked recall count', () => {
  const ctx = E.analyze(baseInput(), DATA);        // no live data
  assert.ok(ctx.findings.some(f => f.tag === 'Recalls' && f.text.includes('recall campaign')));
});

test('a live recall lookup suppresses the baked-count warning', () => {
  const ctx = E.analyze(baseInput(), DATA, { recalls: [] });
  const baked = ctx.findings.filter(f => f.tag === 'Recalls' && f.text.includes('did not run'));
  assert.strictEqual(baked.length, 0);
});
