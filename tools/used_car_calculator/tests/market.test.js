/* Unit tests for the market layer: ingest, dedupe, cohort comps, ranking, search. */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const DIR = path.join(__dirname, '..');
const DATA = require(path.join(DIR, 'data.js'));
const E = require(path.join(DIR, 'engine.js'));
const M = require(path.join(DIR, 'market.js'));

const ASSUMPTIONS = {
  currentYear: 2026, payType: 'loan', down: 3000, creditTier: 'prime', apr: 9.6,
  termMonths: 48, taxRate: 0.06, fees: 500, annualMiles: 12000, horizon: 5,
  gasUsdPerGal: 3.15, insurance: 1750, income: 0, state: null
};

/** A synthetic cohort on a known price curve, so comps have a right answer. */
function cohort(n = 40, over = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const year = 2018 + (i % 4);
    const miles = 40000 + (i % 10) * 8000;
    const price = Math.round(28000 * Math.pow(0.93, 2026 - year) - miles * 0.06);
    out.push(Object.assign({
      year, make: 'Toyota', model: 'Camry', miles, price,
      title: 'clean', accidents: 'none', seller: 'dealer', source: 'a.example',
      url: 'https://a.example/' + i, id: 'x' + i
    }, over));
  }
  return out;
}

/* ------------------------------------------------------------- normalising */

test('normaliseListing coerces strings, currency and mileage text', () => {
  const l = M.normaliseListing({
    year: '2019', make: 'toyota', model: 'Camry', price: '$18,995', mileage: '52,341 mi'
  }, 0);
  assert.strictEqual(l.year, 2019);
  assert.strictEqual(l.make, 'Toyota');
  assert.strictEqual(l.price, 18995);
  assert.strictEqual(l.miles, 52341);
  assert.strictEqual(l.ok, true);
});

test('normaliseListing marks records missing the essentials as unusable', () => {
  assert.strictEqual(M.normaliseListing({ make: 'Toyota', model: 'Camry' }, 0).ok, false);
  assert.strictEqual(M.normaliseListing({ year: 2019, make: 'Toyota', model: 'Camry',
    price: 18000 }, 0).ok, false, 'no odometer reading');
});

test('normaliseTitle maps the words sites actually use', () => {
  assert.strictEqual(M.normaliseTitle('Salvage'), 'rebuilt');
  assert.strictEqual(M.normaliseTitle('Reconstructed title'), 'rebuilt');
  assert.strictEqual(M.normaliseTitle('Manufacturer buyback'), 'lemon');
  assert.strictEqual(M.normaliseTitle('Clean'), 'clean');
  assert.strictEqual(M.normaliseTitle(''), 'clean');
});

test('normaliseAccidents defaults to unknown rather than none', () => {
  assert.strictEqual(M.normaliseAccidents(''), 'unknown');
  assert.strictEqual(M.normaliseAccidents('No accidents reported'), 'none');
  assert.strictEqual(M.normaliseAccidents('1 accident reported'), 'minor');
  assert.strictEqual(M.normaliseAccidents('Structural damage'), 'major');
  assert.strictEqual(M.normaliseAccidents('who knows'), 'unknown');
});

/* ------------------------------------------------------------------ dedupe */

test('dedupe matches on VIN across sources and keeps the cheaper sighting', () => {
  const listings = [
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 50000, price: 19500,
      vin: '4T1B11HK5KU123456', source: 'a.example' },
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 50100, price: 18500,
      vin: '4T1B11HK5KU123456', source: 'b.example' }
  ].map(M.normaliseListing);
  const out = M.dedupe(listings);
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0].price, 18500, 'the cheaper listing wins');
  assert.strictEqual(out[0].duplicates.length, 1);
  assert.strictEqual(out[0].duplicates[0].price, 19500, 'the other price is kept as evidence');
});

test('dedupe falls back to a fingerprint when there is no VIN', () => {
  const listings = [
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 50000, price: 18500 },
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 50100, price: 18550 }
  ].map(M.normaliseListing);
  assert.strictEqual(M.dedupe(listings).length, 1);
});

test('dedupe keeps genuinely different cars apart', () => {
  const listings = [
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 50000, price: 18500 },
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 92000, price: 14000 }
  ].map(M.normaliseListing);
  assert.strictEqual(M.dedupe(listings).length, 2);
});

/* -------------------------------------------------------------- ingestion */

test('ingest accepts a bare array or a wrapped envelope', () => {
  const rows = cohort(6);
  assert.strictEqual(M.ingest(rows).listings.length, 6);
  const wrapped = M.ingest({ listings: rows, query: 'test', generated: '2026-08-07' });
  assert.strictEqual(wrapped.listings.length, 6);
  assert.strictEqual(wrapped.meta.query, 'test');
});

test('ingest rejects a payload that is not listings', () => {
  const result = M.ingest({ nope: true });
  assert.strictEqual(result.ok, false);
  assert.ok(result.error.includes('listings'));
});

test('ingest reports what it dropped', () => {
  const result = M.ingest(cohort(4).concat([{ make: 'Toyota' }]));
  assert.strictEqual(result.rejected, 1);
});

/* ------------------------------------------------------------ cohort comps */

test('cohortComps builds comparables from peers and says which net it used', () => {
  const all = M.ingest(cohort(40)).listings;
  const target = all[0];
  const comps = M.cohortComps(target, all, E, DATA);
  assert.ok(comps.n >= 3, 'a 40-car cohort should yield peers');
  assert.ok(comps.method.includes('same model'));
  assert.ok(comps.comps.every(c => c > 1000 && c < 60000), 'adjusted comps stay plausible');
});

test('cohortComps normalises peers towards the target mileage', () => {
  // One high-mileage target among low-mileage peers: raw peer prices are much higher,
  // so the adjustment must pull them down towards the target.
  const all = M.ingest(cohort(30).concat([{
    year: 2019, make: 'Toyota', model: 'Camry', miles: 150000, price: 9000,
    title: 'clean', id: 'high'
  }])).listings;
  const target = all.filter(l => l.id === 'high')[0];
  const comps = M.cohortComps(target, all, E, DATA);
  if (comps.n) {
    const mean = comps.comps.reduce((a, b) => a + b, 0) / comps.n;
    const rawPeers = all.filter(l => l !== target && Math.abs(l.year - target.year) <= 3);
    const rawMean = rawPeers.reduce((a, l) => a + l.price, 0) / rawPeers.length;
    assert.ok(mean < rawMean, 'high-mileage target should not be valued at low-mileage prices');
  }
});

test('cohortComps refuses to price a branded title off clean-title peers', () => {
  const all = M.ingest(cohort(30).concat([{
    year: 2019, make: 'Toyota', model: 'Camry', miles: 60000, price: 9000,
    title: 'salvage', id: 'branded'
  }])).listings;
  const target = all.filter(l => l.id === 'branded')[0];
  const comps = M.cohortComps(target, all, E, DATA);
  assert.strictEqual(comps.n, 0, 'no clean-title peers may be used');
  assert.ok(comps.method.includes('not enough peers'));
});

test('cohortComps gives up cleanly on a thin cohort', () => {
  const all = M.ingest(cohort(2)).listings;
  const comps = M.cohortComps(all[0], all, E, DATA);
  assert.strictEqual(comps.n, 0);
  assert.deepStrictEqual(comps.comps, []);
});

test('cohortComps never uses a different make', () => {
  const mixed = M.ingest(cohort(20).concat(cohort(20).map((l, i) =>
    Object.assign({}, l, { make: 'Honda', model: 'Accord', id: 'h' + i })))).listings;
  const target = mixed.filter(l => l.make === 'Toyota')[0];
  const comps = M.cohortComps(target, mixed, E, DATA);
  assert.ok(comps.peerIds.every(id => !id.startsWith('h')));
});

/* ------------------------------------------------------------- scoring */

test('scoreAll ranks every usable listing, best first', () => {
  const all = M.ingest(cohort(40)).listings;
  const rows = M.scoreAll(all, ASSUMPTIONS, E, DATA, {});
  assert.strictEqual(rows.length, all.length);
  for (let i = 1; i < rows.length; i++) {
    assert.ok(rows[i].score <= rows[i - 1].score, 'must be sorted by score');
  }
  rows.forEach(r => {
    assert.ok(Number.isFinite(r.score) && r.score >= 0 && r.score <= 100);
    assert.ok(Number.isFinite(r.monthly) && r.monthly > 0);
    assert.ok(Number.isFinite(r.tco));
  });
});

test('scoreAll finds the underpriced car', () => {
  // The same car at two prices: the cheap one must read as under fair value and score higher.
  const base = { year: 2020, make: 'Toyota', model: 'Camry', miles: 60000, title: 'clean' };
  const all = M.ingest(cohort(40).concat([
    Object.assign({}, base, { price: 9000, id: 'bargain' }),
    Object.assign({}, base, { price: 21000, id: 'overpriced' })
  ])).listings;
  const rows = M.scoreAll(all, ASSUMPTIONS, E, DATA, {});
  const bargain = rows.filter(r => r.listing.id === 'bargain')[0];
  const overpriced = rows.filter(r => r.listing.id === 'overpriced')[0];

  assert.ok(bargain.saving > 0, 'the cheap one should read as under fair value');
  assert.ok(overpriced.saving < 0, 'the dear one should read as over fair value');
  assert.ok(bargain.score > overpriced.score, 'and it must score higher');
  assert.ok(rows.indexOf(bargain) < rows.indexOf(overpriced), 'and rank above it');
  assert.ok(bargain.score >= M.median(rows.map(r => r.score)),
    'a genuine bargain should sit in the better half of the market');
});

test('scoreAll reflects the buyer, not just the car', () => {
  const all = M.ingest(cohort(30)).listings;
  const cheapState = M.scoreAll(all, Object.assign({}, ASSUMPTIONS,
    { state: 'ME', insurance: null }), E, DATA, {});
  const dearState = M.scoreAll(all, Object.assign({}, ASSUMPTIONS,
    { state: 'MI', insurance: null }), E, DATA, {});
  const idOf = r => r.listing.id;
  const a = cheapState.filter(r => idOf(r) === 'x0')[0];
  const b = dearState.filter(r => idOf(r) === 'x0')[0];
  assert.ok(b.monthly > a.monthly, 'Michigan should cost more to run the same car');
});

test('a branded title still ranks below clean cars at the same price', () => {
  const all = M.ingest(cohort(30).concat([{
    year: 2021, make: 'Toyota', model: 'Camry', miles: 45000, price: 12000,
    title: 'salvage', accidents: 'major', id: 'branded'
  }])).listings;
  const rows = M.scoreAll(all, ASSUMPTIONS, E, DATA, {});
  const branded = rows.filter(r => r.listing.id === 'branded')[0];
  assert.ok(branded.score <= 40, 'the hard cap must still apply');
});

/* --------------------------------------------------- search, filter, sort */

test('search requires every term to match somewhere', () => {
  const rows = M.scoreAll(M.ingest(cohort(10)).listings, ASSUMPTIONS, E, DATA, {});
  assert.ok(M.matchesQuery(rows[0], 'toyota camry'));
  assert.ok(M.matchesQuery(rows[0], 'CAMRY'), 'case insensitive');
  assert.ok(!M.matchesQuery(rows[0], 'toyota tundra'));
  assert.ok(M.matchesQuery(rows[0], ''), 'an empty query matches everything');
});

test('filters narrow on price, miles, year, score and title', () => {
  const rows = M.scoreAll(M.ingest(cohort(40)).listings, ASSUMPTIONS, E, DATA, {});
  // Thresholds are taken from the cohort itself, so the test cannot become a no-op
  // if the synthetic price curve changes.
  const medianPrice = M.median(rows.map(r => r.listing.price));
  const cheap = M.filterRows(rows, { maxPrice: medianPrice });
  assert.ok(cheap.every(r => r.listing.price <= medianPrice));
  assert.ok(cheap.length < rows.length, 'the median must exclude somebody');
  assert.ok(cheap.length > 0);

  const medianMiles = M.median(rows.map(r => r.listing.miles));
  const lowMiles = M.filterRows(rows, { maxMiles: medianMiles });
  assert.ok(lowMiles.every(r => r.listing.miles <= medianMiles));
  assert.ok(lowMiles.length < rows.length);

  const newer = M.filterRows(rows, { minYear: 2020 });
  assert.ok(newer.every(r => r.listing.year >= 2020));
  assert.ok(newer.length < rows.length);

  const good = M.filterRows(rows, { minScore: 70 });
  assert.ok(good.every(r => r.score >= 70));

  const combined = M.filterRows(rows, { maxPrice: medianPrice, maxMiles: medianMiles });
  assert.ok(combined.length <= cheap.length, 'filters compose');
});

test('cleanOnly excludes branded titles', () => {
  const all = M.ingest(cohort(20).concat([{
    year: 2019, make: 'Toyota', model: 'Camry', miles: 60000, price: 9000,
    title: 'salvage', id: 'branded'
  }])).listings;
  const rows = M.scoreAll(all, ASSUMPTIONS, E, DATA, {});
  assert.ok(M.filterRows(rows, { cleanOnly: true }).every(r => r.listing.title === 'clean'));
  assert.ok(M.filterRows(rows, {}).some(r => r.listing.title === 'rebuilt'));
});

test('every sort key produces a real ordering', () => {
  const rows = M.scoreAll(M.ingest(cohort(30)).listings, ASSUMPTIONS, E, DATA, {});
  Object.keys(M.SORTS).forEach(key => {
    const sorted = M.sortRows(rows, key);
    assert.strictEqual(sorted.length, rows.length, key);
  });
  const byPrice = M.sortRows(rows, 'price');
  for (let i = 1; i < byPrice.length; i++) {
    assert.ok(byPrice[i].listing.price >= byPrice[i - 1].listing.price);
  }
  assert.strictEqual(M.sortRows(rows, 'nonsense').length, rows.length, 'unknown key falls back');
});

test('sorting does not mutate the input order', () => {
  const rows = M.scoreAll(M.ingest(cohort(10)).listings, ASSUMPTIONS, E, DATA, {});
  const firstBefore = rows[0];
  M.sortRows(rows, 'price');
  assert.strictEqual(rows[0], firstBefore);
});

/* --------------------------------------------------------------- stats */

test('marketStats summarises the cohort and fits the mileage trend', () => {
  const rows = M.scoreAll(M.ingest(cohort(40)).listings, ASSUMPTIONS, E, DATA, {});
  const stats = M.marketStats(rows);
  assert.strictEqual(stats.count, rows.length);
  assert.ok(stats.medianPrice > 0);
  assert.ok(stats.p25 <= stats.medianPrice && stats.medianPrice <= stats.p75);
  assert.ok(stats.slope < 0, 'more miles must mean less money');
  assert.ok(stats.usdPerThousandMiles > 0);
});

test('marketStats copes with an empty or tiny cohort', () => {
  assert.strictEqual(M.marketStats([]).count, 0);
  assert.strictEqual(M.marketStats([]).medianPrice, null);
  const one = M.scoreAll(M.ingest(cohort(1)).listings, ASSUMPTIONS, E, DATA, {});
  assert.ok(!('slope' in M.marketStats(one)), 'no trend line from one point');
});

test('median and quantile behave on even and odd lengths', () => {
  assert.strictEqual(M.median([1, 2, 3]), 2);
  assert.strictEqual(M.median([1, 2, 3, 4]), 2.5);
  assert.strictEqual(M.median([]), null);
  assert.strictEqual(M.quantile([1, 2, 3, 4], 0.5), 3);
});

test('shortlist returns the three angles a buyer cares about', () => {
  const rows = M.scoreAll(M.ingest(cohort(40)).listings, ASSUMPTIONS, E, DATA, {});
  const picks = M.shortlist(rows, 3);
  assert.strictEqual(picks.best.length, 3);
  assert.ok(picks.cheapestToOwn.length <= 3);
  assert.ok(picks.bargains.every(r => r.saving > 0), 'a bargain must actually be under value');
  assert.ok(picks.best[0].score >= picks.best[1].score);
});

test('the whole pipeline survives a messy, realistic payload', () => {
  const messy = {
    listings: cohort(30).concat([
      { year: '2019', make: 'HONDA', model: 'Accord', mileage: '61,200', price: '$17,450' },
      { year: 2018, make: 'Honda', model: 'Accord', miles: 70000, price: 15000, title: 'Salvage' },
      { make: 'Broken' },
      { year: 2020, make: 'Honda', model: 'Accord', miles: 40000, price: 21000,
        vin: 'not-a-vin', accidents: '' }
    ])
  };
  const ingested = M.ingest(messy);
  assert.ok(ingested.ok);
  const rows = M.scoreAll(ingested.listings, ASSUMPTIONS, E, DATA, {});
  rows.forEach(r => {
    assert.ok(Number.isFinite(r.score), 'no listing may produce a NaN score');
    assert.ok(!r.error, r.error);
  });
});

/* ------------------------- tolerant ingestion: whatever an assistant emits */

test('parseText reads plain JSON, an array or an envelope', () => {
  assert.strictEqual(M.parseText('[{"a":1}]').form, 'JSON');
  assert.deepStrictEqual(M.parseText('{"listings":[{"a":1}]}').data.listings.length, 1);
});

test('parseText unwraps a markdown code fence', () => {
  const out = M.parseText('```json\n[{"year":2019}]\n```');
  assert.ok(out.ok);
  assert.strictEqual(out.data[0].year, 2019);
});

test('parseText finds JSON buried in prose', () => {
  const out = M.parseText('Here is what I found:\n[{"year":2019,"make":"Toyota"}]\nHope that helps!');
  assert.ok(out.ok);
  assert.strictEqual(out.form, 'JSON in surrounding text');
  assert.strictEqual(out.data[0].make, 'Toyota');
});

test('parseText accepts one JSON object per line', () => {
  const out = M.parseText('{"year":2019}\n{"year":2020},\n{"year":2021}');
  assert.ok(out.ok);
  assert.strictEqual(out.form, 'one JSON object per line');
  assert.strictEqual(out.data.length, 3);
});

test('parseText accepts a CSV block with quoted fields', () => {
  const out = M.parseText('year,make,model,notes\n2019,Toyota,Camry,"one owner, new tyres"');
  assert.ok(out.ok);
  assert.strictEqual(out.form, 'CSV');
  assert.strictEqual(out.data[0].notes, 'one owner, new tyres');
});

test('parseText accepts a TSV block', () => {
  const out = M.parseText('year\tmake\tmodel\n2019\tToyota\tCamry');
  assert.strictEqual(out.form, 'TSV');
  assert.strictEqual(out.data[0].make, 'Toyota');
});

test('parseText explains itself when it cannot read the input', () => {
  const out = M.parseText('this is just a sentence');
  assert.strictEqual(out.ok, false);
  assert.ok(out.error.includes('JSON'));
  assert.strictEqual(M.parseText('').ok, false);
  assert.strictEqual(M.parseText(null).ok, false);
});

test('field names are matched loosely, the way an assistant would write them', () => {
  const l = M.normaliseListing({
    modelYear: '2019', brand: 'toyota', model: 'Camry',
    odometer: '52,341', listPrice: '$18,995', link: 'https://cars.example/l/1',
    titleStatus: 'Clean', accidentHistory: 'No accidents reported',
    sellerType: 'Private', numOwners: '2', days_on_market: '41'
  }, 0);
  assert.strictEqual(l.year, 2019);
  assert.strictEqual(l.make, 'Toyota');
  assert.strictEqual(l.miles, 52341);
  assert.strictEqual(l.price, 18995);
  assert.strictEqual(l.url, 'https://cars.example/l/1');
  assert.strictEqual(l.title, 'clean');
  assert.strictEqual(l.accidents, 'none');
  assert.strictEqual(l.seller, 'private');
  assert.strictEqual(l.owners, 2);
  assert.strictEqual(l.daysOnMarket, 41);
  assert.strictEqual(l.ok, true);
});

test('snake_case and odd casing still resolve', () => {
  const l = M.normaliseListing({
    'Model Year': 2020, 'MAKE': 'Honda', 'model': 'Accord',
    'miles_driven': 30000, 'asking_price': 22000, 'listing_url': 'https://x.example/a'
  }, 0);
  assert.strictEqual(l.year, 2020);
  assert.strictEqual(l.make, 'Honda');
  assert.strictEqual(l.miles, 30000);
  assert.strictEqual(l.price, 22000);
  assert.strictEqual(l.url, 'https://x.example/a');
});

test('normaliseUrl keeps real links and repairs a bare domain', () => {
  assert.strictEqual(M.normaliseUrl('https://a.example/1'), 'https://a.example/1');
  assert.strictEqual(M.normaliseUrl('cars.example/listing/5'), 'https://cars.example/listing/5');
  assert.strictEqual(M.normaliseUrl('not a url'), null);
  assert.strictEqual(M.normaliseUrl(''), null);
  assert.strictEqual(M.normaliseUrl(null), null);
});

test('the source is derived from the link when it is not given', () => {
  const l = M.normaliseListing({
    year: 2019, make: 'Toyota', model: 'Camry', miles: 50000, price: 18000,
    url: 'https://www.cars.example/listing/7'
  }, 0);
  assert.strictEqual(l.source, 'cars.example', 'www. is dropped so duplicates reconcile');
});

test('a malformed VIN is discarded rather than trusted', () => {
  assert.strictEqual(M.normaliseListing({ vin: 'NOT-A-VIN' }, 0).vin, null);
  assert.strictEqual(M.normaliseListing({ vin: '4t1b11hk5ku123456' }, 0).vin, '4T1B11HK5KU123456');
});

test('normaliseListing survives null and empty records', () => {
  assert.strictEqual(M.normaliseListing(null, 0).ok, false);
  assert.strictEqual(M.normaliseListing({}, 0).ok, false);
});

test('a full messy paste goes end to end', () => {
  const pasted = '```json\n' + JSON.stringify([
    { link: 'https://a.example/1', modelYear: 2019, brand: 'Toyota', model: 'Camry',
      odometer: '52,341', listPrice: '$18,995' },
    { link: 'https://a.example/2', modelYear: 2020, brand: 'Toyota', model: 'Camry',
      odometer: '38,900', listPrice: '$21,400' },
    { link: 'https://a.example/3', modelYear: 2018, brand: 'Toyota', model: 'Camry',
      odometer: '71,000', listPrice: '$15,900' },
    { link: 'https://a.example/4', modelYear: 2019, brand: 'Toyota', model: 'Camry',
      odometer: '60,100', listPrice: '$17,750' }
  ]) + '\n```';
  const parsed = M.parseText(pasted);
  assert.ok(parsed.ok);
  const ingested = M.ingest(parsed.data);
  assert.strictEqual(ingested.listings.length, 4);
  assert.ok(ingested.listings.every(l => l.url && l.source === 'a.example'));
  const rows = M.scoreAll(ingested.listings, ASSUMPTIONS, E, DATA, {});
  assert.strictEqual(rows.length, 4);
  rows.forEach(r => assert.ok(Number.isFinite(r.score)));
});

test('dedupe keeps similar cars at different dealers apart', () => {
  const listings = [
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 52000, price: 18995,
      dealer: 'Alpha Motors', url: 'https://a.example/1' },
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 52100, price: 18995,
      dealer: 'Beta Motors', url: 'https://b.example/2' }
  ].map(M.normaliseListing);
  assert.strictEqual(M.dedupe(listings).length, 2,
    'two lots, two cars — only a shared seller or VIN means a duplicate');
});

test('dedupe still merges the same dealer car seen on two aggregators', () => {
  const listings = [
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 52000, price: 19500,
      dealer: 'Alpha Motors', url: 'https://a.example/1' },
    { year: 2019, make: 'Toyota', model: 'Camry', miles: 52100, price: 18995,
      dealer: 'Alpha Motors', url: 'https://b.example/2' }
  ].map(M.normaliseListing);
  const out = M.dedupe(listings);
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0].price, 18995);
  assert.strictEqual(out[0].duplicates.length, 1);
});
