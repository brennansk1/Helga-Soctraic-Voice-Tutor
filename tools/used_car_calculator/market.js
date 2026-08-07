/* Used Car Deal Analyzer — market layer.
 *
 * Turns a pile of scraped listings into a ranked shortlist.
 *
 * The single biggest weakness in the per-car report is that fair value depends on
 * comparables the user types in by hand — usually one or two, often none. With a few
 * hundred listings in hand we can do far better: for every car, build its comparable set
 * from the cohort itself, normalise each peer to the target's mileage, and score the whole
 * market through the same engine the single-car report uses.
 *
 * Pure functions, no DOM, no network — the scraping happens outside (see scrape_listings.py
 * and AGENTS.md); this file only reasons about listings it is handed.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.UCDA_MARKET = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var SCHEMA_VERSION = 1;

  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function num(v, fallback) {
    if (typeof v === 'string') v = parseFloat(v.replace(/[$,\s]/g, ''));
    return isNum(v) ? v : fallback;
  }
  function text(v) { return v === null || v === undefined ? '' : String(v).trim(); }
  function titleCase(s) {
    return text(s).toLowerCase().replace(/(^|[\s\-/])\w/g, function (m) { return m.toUpperCase(); });
  }

  /* ------------------------------------------------------------ normalising */

  var TITLE_WORDS = {
    salvage: 'rebuilt', rebuilt: 'rebuilt', reconstructed: 'rebuilt', branded: 'rebuilt',
    lemon: 'lemon', buyback: 'lemon', clean: 'clean', clear: 'clean'
  };

  /** Map a free-text title/condition string onto the engine's enum. */
  function normaliseTitle(value) {
    var v = text(value).toLowerCase();
    for (var word in TITLE_WORDS) {
      if (v.indexOf(word) !== -1) return TITLE_WORDS[word];
    }
    return 'clean';
  }

  function normaliseAccidents(value) {
    var v = text(value).toLowerCase();
    if (!v) return 'unknown';
    if (/no accident|none|0 accident|clean/.test(v)) return 'none';
    if (/major|structural|frame|severe/.test(v)) return 'major';
    if (/minor|1 accident|2 accident|damage reported/.test(v)) return 'minor';
    return 'unknown';
  }

  /**
   * Coerce one scraped record into the canonical shape. Anything unparseable becomes null
   * rather than a guess, and `ok` says whether the record has the minimum to be scored.
   */
  function normaliseListing(raw, index) {
    var year = num(raw.year, null);
    var price = num(raw.price, null);
    var miles = num(raw.miles !== undefined ? raw.miles : raw.mileage, null);
    var make = titleCase(raw.make);
    var model = text(raw.model);
    var vin = text(raw.vin).toUpperCase() || null;

    var listing = {
      id: text(raw.id) || vin || ('L' + index),
      vin: vin,
      year: year, make: make, model: model, trim: text(raw.trim) || null,
      miles: miles, price: price,
      title: normaliseTitle(raw.title || raw.titleStatus),
      accidents: normaliseAccidents(raw.accidents || raw.accidentHistory),
      owners: num(raw.owners, null),
      condition: text(raw.condition).toLowerCase() || null,
      seller: /private|owner|fsbo/i.test(text(raw.seller)) ? 'private' : 'dealer',
      dealer: text(raw.dealer || raw.dealerName) || null,
      city: titleCase(raw.city) || null,
      state: text(raw.state).toUpperCase().slice(0, 2) || null,
      zip: text(raw.zip) || null,
      distance: num(raw.distance, null),
      daysOnMarket: num(raw.daysOnMarket, null),
      priceDrop: num(raw.priceDrop, null),
      url: text(raw.url) || null,
      source: text(raw.source) || null,
      seenAt: text(raw.seenAt) || null,
      mpg: num(raw.mpg, null),
      segment: text(raw.segment) || null,
      notes: text(raw.notes) || null
    };
    listing.ok = !!(listing.year && listing.make && listing.model &&
      isNum(listing.price) && listing.price > 100 &&
      isNum(listing.miles) && listing.miles >= 0);
    return listing;
  }

  /**
   * Deduplicate. The same car appears on several aggregators, so match on VIN first and
   * fall back to a fingerprint. When duplicates collide, keep the cheapest and remember
   * where else it was seen — a car listed at two prices is itself a negotiating fact.
   */
  function dedupe(listings) {
    var byKey = {};
    var order = [];
    listings.forEach(function (l) {
      var key = l.vin || [l.year, l.make.toLowerCase(), l.model.toLowerCase(),
        Math.round(l.miles / 500), Math.round(l.price / 250)].join('|');
      if (!byKey[key]) {
        l.duplicates = [];
        byKey[key] = l;
        order.push(key);
        return;
      }
      var kept = byKey[key];
      var loser = l;
      if (l.price < kept.price) {
        l.duplicates = kept.duplicates || [];
        byKey[key] = l;
        loser = kept;
      }
      byKey[key].duplicates.push({
        price: loser.price, source: loser.source, url: loser.url
      });
    });
    return order.map(function (k) { return byKey[k]; });
  }

  /* ---------------------------------------------------------- cohort comps */

  /**
   * Build a comparable set for one listing out of the rest of the cohort.
   *
   * Peers are normalised to the target's mileage using the engine's own price-per-mile
   * slope before averaging, so a 30k-mile peer does not drag a 90k-mile car's value up.
   * Branded titles never act as comps for a clean car (or the reverse). Widens the net in
   * two documented steps before giving up, and always reports which one it used.
   */
  function cohortComps(target, cohort, engine, data, options) {
    options = options || {};
    var minPeers = options.minPeers || 3;
    var maxComps = options.maxComps || 12;

    function peersWithin(yearSpan, mileSpan, sameModel) {
      return cohort.filter(function (p) {
        if (p === target || !p.ok) return false;
        if (p.title !== target.title) return false;
        if (p.make.toLowerCase() !== target.make.toLowerCase()) return false;
        if (sameModel && p.model.toLowerCase() !== target.model.toLowerCase()) return false;
        if (Math.abs(p.year - target.year) > yearSpan) return false;
        if (Math.abs(p.miles - target.miles) > mileSpan) return false;
        return true;
      });
    }

    var attempts = [
      { peers: peersWithin(1, 20000, true), method: 'same model, ±1 year, ±20k miles' },
      { peers: peersWithin(2, 40000, true), method: 'same model, ±2 years, ±40k miles' },
      { peers: peersWithin(3, 60000, true), method: 'same model, ±3 years, ±60k miles' }
    ];
    var chosen = null;
    for (var i = 0; i < attempts.length; i++) {
      if (attempts[i].peers.length >= minPeers) { chosen = attempts[i]; break; }
    }
    if (!chosen) return { comps: [], n: 0, method: 'not enough peers in this cohort' };

    var peers = chosen.peers.slice(0, maxComps);
    // Normalise every peer to the target's mileage and model year before averaging.
    var adjusted = peers.map(function (p) {
      var slope = engine.mileageSlope(p.price, target.segment || 'Midsize car', data);
      var mileageDelta = (p.miles - target.miles) * slope;      // peer has more miles -> worth less
      var yearDelta = 0;
      if (p.year !== target.year) {
        var rate = engine.depRateAt(
          new Date().getFullYear() - p.year, target.segment || 'Midsize car', target.make, data);
        // Bring an older peer forward (or a newer one back) one year at a time.
        yearDelta = p.price * (Math.pow(1 - rate, p.year - target.year) - 1) * -1;
      }
      return Math.max(p.price + mileageDelta + yearDelta, 500);
    });

    return {
      comps: adjusted,
      n: adjusted.length,
      method: chosen.method,
      peerIds: peers.map(function (p) { return p.id; }),
      spread: {
        low: Math.min.apply(null, adjusted),
        high: Math.max.apply(null, adjusted)
      }
    };
  }

  /* --------------------------------------------------------- batch scoring */

  /**
   * Score every listing through the single-car engine, using the cohort for comps.
   * `assumptions` are the buyer's own settings (state, financing, horizon, mileage) so the
   * ranking reflects how *this* buyer would own the car, not a generic one.
   */
  function scoreAll(listings, assumptions, engine, data, live) {
    var usable = listings.filter(function (l) { return l.ok; });
    return usable.map(function (l) {
      var comps = cohortComps(l, usable, engine, data);
      var input = Object.assign({}, assumptions, {
        name: [l.year, l.make, l.model, l.trim].filter(Boolean).join(' '),
        brand: l.make, model: l.model, year: l.year,
        miles: l.miles, asking: l.price,
        title: l.title, accidents: l.accidents,
        owners: l.owners || 1,
        condition: l.condition || 'good',
        mpg: l.mpg || null,
        comps: comps.comps,
        // Cohort comps are asking prices too, so the haircut still applies.
        applyCompBias: assumptions.applyCompBias !== false
      });
      var ctx;
      try {
        ctx = engine.analyze(input, data, live || {});
      } catch (e) {
        return { listing: l, error: e.message, score: -1, comps: comps };
      }
      return {
        listing: l,
        ctx: ctx,
        comps: comps,
        score: ctx.score.score,
        verdict: ctx.verdict.label,
        fair: ctx.fair.value,
        delta: ctx.fair.value > 0 ? (l.price - ctx.fair.value) / ctx.fair.value : 0,
        saving: ctx.fair.value - l.price,
        monthly: ctx.tco.costPerMonth,
        cpm: ctx.tco.costPerMile,
        tco: ctx.tco.total,
        otd: ctx.otd,
        lifeLeft: ctx.remainingMiles,
        criticals: ctx.findings.filter(function (f) { return f.level === 'critical'; }).length
      };
    }).sort(function (a, b) { return b.score - a.score; });
  }

  /* ------------------------------------------------ searching and filtering */

  /** Free-text search across the fields a human would type. */
  function matchesQuery(row, query) {
    if (!query) return true;
    var l = row.listing;
    var haystack = [l.year, l.make, l.model, l.trim, l.dealer, l.city, l.state,
      l.vin, l.source, l.notes].filter(Boolean).join(' ').toLowerCase();
    // Every whitespace-separated term must appear somewhere.
    return query.toLowerCase().split(/\s+/).filter(Boolean).every(function (term) {
      return haystack.indexOf(term) !== -1;
    });
  }

  var SORTS = {
    score: function (a, b) { return b.score - a.score; },
    price: function (a, b) { return a.listing.price - b.listing.price; },
    miles: function (a, b) { return a.listing.miles - b.listing.miles; },
    year: function (a, b) { return b.listing.year - a.listing.year; },
    saving: function (a, b) { return b.saving - a.saving; },
    monthly: function (a, b) { return a.monthly - b.monthly; },
    cpm: function (a, b) { return a.cpm - b.cpm; },
    distance: function (a, b) {
      return (a.listing.distance === null ? 1e9 : a.listing.distance) -
             (b.listing.distance === null ? 1e9 : b.listing.distance);
    }
  };

  function filterRows(rows, filters) {
    filters = filters || {};
    return rows.filter(function (r) {
      var l = r.listing;
      if (!matchesQuery(r, filters.query)) return false;
      if (isNum(filters.maxPrice) && l.price > filters.maxPrice) return false;
      if (isNum(filters.minPrice) && l.price < filters.minPrice) return false;
      if (isNum(filters.maxMiles) && l.miles > filters.maxMiles) return false;
      if (isNum(filters.minYear) && l.year < filters.minYear) return false;
      if (isNum(filters.maxYear) && l.year > filters.maxYear) return false;
      if (isNum(filters.minScore) && r.score < filters.minScore) return false;
      if (isNum(filters.maxDistance) && l.distance !== null && l.distance > filters.maxDistance) return false;
      if (filters.state && l.state !== filters.state) return false;
      if (filters.seller && l.seller !== filters.seller) return false;
      if (filters.cleanOnly && l.title !== 'clean') return false;
      if (filters.noCriticals && r.criticals > 0) return false;
      return true;
    });
  }

  function sortRows(rows, key) {
    var fn = SORTS[key] || SORTS.score;
    return rows.slice().sort(fn);
  }

  /* --------------------------------------------------------- market stats */

  function median(values) {
    if (!values.length) return null;
    var s = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function quantile(values, q) {
    if (!values.length) return null;
    var s = values.slice().sort(function (a, b) { return a - b; });
    return s[Math.min(Math.floor(s.length * q), s.length - 1)];
  }

  /** Headline numbers about the cohort, plus the price-vs-mileage trend line. */
  function marketStats(rows) {
    var prices = rows.map(function (r) { return r.listing.price; });
    var miles = rows.map(function (r) { return r.listing.miles; });
    var stats = {
      count: rows.length,
      medianPrice: median(prices),
      p25: quantile(prices, 0.25),
      p75: quantile(prices, 0.75),
      medianMiles: median(miles),
      medianScore: median(rows.map(function (r) { return r.score; })),
      dealers: Object.keys(rows.reduce(function (acc, r) {
        if (r.listing.dealer) acc[r.listing.dealer] = 1;
        return acc;
      }, {})).length,
      privateShare: rows.length
        ? rows.filter(function (r) { return r.listing.seller === 'private'; }).length / rows.length
        : 0,
      branded: rows.filter(function (r) { return r.listing.title !== 'clean'; }).length
    };

    // Least-squares price against mileage — the market's own depreciation slope.
    if (rows.length >= 4) {
      var n = rows.length;
      var mx = miles.reduce(function (a, b) { return a + b; }, 0) / n;
      var my = prices.reduce(function (a, b) { return a + b; }, 0) / n;
      var numer = 0, denom = 0;
      for (var i = 0; i < n; i++) {
        numer += (miles[i] - mx) * (prices[i] - my);
        denom += (miles[i] - mx) * (miles[i] - mx);
      }
      if (denom > 0) {
        stats.slope = numer / denom;               // dollars per mile (negative)
        stats.intercept = my - stats.slope * mx;
        stats.usdPerThousandMiles = -stats.slope * 1000;
      }
    }
    return stats;
  }

  /** The shortlist: best score, cheapest running cost, and the biggest bargain. */
  function shortlist(rows, limit) {
    limit = limit || 5;
    var byScore = sortRows(rows, 'score').slice(0, limit);
    return {
      best: byScore,
      bargains: sortRows(rows, 'saving').filter(function (r) { return r.saving > 0; }).slice(0, limit),
      cheapestToOwn: sortRows(rows, 'monthly').slice(0, limit)
    };
  }

  /* ------------------------------------------------------------- ingestion */

  /**
   * Accept whatever shape the scrape produced: a bare array, or a wrapped
   * `{schema, generated, query, listings: [...]}` envelope.
   */
  function ingest(payload) {
    var raw = Array.isArray(payload) ? payload
      : (payload && Array.isArray(payload.listings) ? payload.listings : null);
    if (!raw) {
      return { ok: false, error: 'Expected an array of listings, or an object with a "listings" array.',
               listings: [], meta: null };
    }
    var normalised = raw.map(normaliseListing);
    var usable = normalised.filter(function (l) { return l.ok; });
    var deduped = dedupe(usable);
    return {
      ok: true,
      listings: deduped,
      rejected: normalised.length - usable.length,
      duplicatesRemoved: usable.length - deduped.length,
      meta: Array.isArray(payload) ? null : {
        generated: payload.generated || null,
        query: payload.query || null,
        sources: payload.sources || null,
        schema: payload.schema || null
      }
    };
  }

  return {
    SCHEMA_VERSION: SCHEMA_VERSION,
    normaliseListing: normaliseListing, normaliseTitle: normaliseTitle,
    normaliseAccidents: normaliseAccidents,
    dedupe: dedupe, cohortComps: cohortComps, scoreAll: scoreAll,
    matchesQuery: matchesQuery, filterRows: filterRows, sortRows: sortRows,
    marketStats: marketStats, shortlist: shortlist, ingest: ingest,
    median: median, quantile: quantile, SORTS: SORTS
  };
}));
