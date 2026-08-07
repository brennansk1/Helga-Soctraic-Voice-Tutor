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

  /** Keep only real http(s) links; a bare domain gets a scheme so it stays clickable. */
  function normaliseUrl(value) {
    var v = text(value);
    if (!v) return null;
    if (/^https?:\/\//i.test(v)) return v;
    if (/^[\w.-]+\.[a-z]{2,}(\/|$)/i.test(v)) return 'https://' + v;
    return null;
  }

  /** The site a listing came from, so duplicates across aggregators can be reconciled. */
  function hostOf(url) {
    if (!url) return null;
    var match = String(url).match(/^https?:\/\/([^/?#]+)/i);
    return match ? match[1].replace(/^www\./i, '') : null;
  }


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
  /** Field names an assistant might reasonably use, mapped onto ours. */
  var ALIASES = {
    year: ['year', 'modelYear', 'model_year', 'yr'],
    make: ['make', 'brand', 'manufacturer'],
    model: ['model', 'modelName', 'model_name'],
    trim: ['trim', 'trimLevel', 'trim_level', 'version'],
    miles: ['miles', 'mileage', 'odometer', 'odometerReading', 'miles_driven', 'km'],
    price: ['price', 'askingPrice', 'asking_price', 'listPrice', 'list_price', 'cost'],
    vin: ['vin', 'VIN', 'vehicleIdentificationNumber'],
    url: ['url', 'link', 'href', 'listingUrl', 'listing_url', 'webpage', 'website', 'page'],
    source: ['source', 'site', 'website_name', 'siteName', 'domain', 'marketplace'],
    dealer: ['dealer', 'dealerName', 'dealer_name', 'sellerName', 'seller_name', 'store'],
    city: ['city', 'town', 'locality'],
    state: ['state', 'region', 'province'],
    zip: ['zip', 'zipcode', 'zip_code', 'postalCode', 'postal_code'],
    distance: ['distance', 'distanceMiles', 'miles_away', 'milesAway', 'radius'],
    title: ['title', 'titleStatus', 'title_status', 'titleType'],
    accidents: ['accidents', 'accidentHistory', 'accident_history', 'accident', 'damage'],
    owners: ['owners', 'numOwners', 'num_owners', 'previousOwners', 'ownerCount'],
    condition: ['condition', 'vehicleCondition'],
    seller: ['seller', 'sellerType', 'seller_type', 'listedBy'],
    daysOnMarket: ['daysOnMarket', 'days_on_market', 'daysListed', 'dom', 'age_days'],
    priceDrop: ['priceDrop', 'price_drop', 'priceReduction', 'reduced'],
    mpg: ['mpg', 'combinedMpg', 'combined_mpg', 'fuelEconomy'],
    segment: ['segment', 'bodyStyle', 'body_style', 'bodyType', 'category'],
    notes: ['notes', 'description', 'summary', 'comments', 'title_text', 'headline'],
    id: ['id', 'listingId', 'listing_id', 'stockNumber', 'stock']
  };

  /** First non-empty value among a field's accepted names. */
  function pick(raw, field) {
    var names = ALIASES[field] || [field];
    for (var i = 0; i < names.length; i++) {
      var v = raw[names[i]];
      if (v !== undefined && v !== null && String(v).trim() !== '') return v;
    }
    // Last resort: a case-insensitive match on any key.
    var lower = {};
    Object.keys(raw).forEach(function (k) { lower[k.toLowerCase().replace(/[^a-z]/g, '')] = raw[k]; });
    for (var j = 0; j < names.length; j++) {
      var key = names[j].toLowerCase().replace(/[^a-z]/g, '');
      if (lower[key] !== undefined && String(lower[key]).trim() !== '') return lower[key];
    }
    return undefined;
  }

  function normaliseListing(rawInput, index) {
    var raw = rawInput || {};
    var get = function (field) { return pick(raw, field); };
    var year = num(get('year'), null);
    var price = num(get('price'), null);
    var miles = num(get('miles'), null);
    var make = titleCase(get('make'));
    var model = text(get('model'));
    var vin = text(get('vin')).toUpperCase() || null;
    if (vin && !/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) vin = null;   // a wrong VIN is worse than none

    var listing = {
      id: text(get('id')) || vin || ('L' + index),
      vin: vin,
      year: year, make: make, model: model, trim: text(get('trim')) || null,
      miles: miles, price: price,
      title: normaliseTitle(get('title')),
      accidents: normaliseAccidents(get('accidents')),
      owners: num(get('owners'), null),
      condition: text(get('condition')).toLowerCase() || null,
      seller: /private|owner|fsbo|individual/i.test(text(get('seller'))) ? 'private' : 'dealer',
      dealer: text(get('dealer')) || null,
      city: titleCase(get('city')) || null,
      state: text(get('state')).toUpperCase().slice(0, 2) || null,
      zip: text(get('zip')) || null,
      distance: num(get('distance'), null),
      daysOnMarket: num(get('daysOnMarket'), null),
      priceDrop: num(get('priceDrop'), null),
      url: normaliseUrl(get('url')),
      source: text(get('source')) || hostOf(normaliseUrl(get('url'))),
      seenAt: text(raw.seenAt) || null,
      mpg: num(get('mpg'), null),
      segment: text(get('segment')) || null,
      notes: text(get('notes')) || null
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
      // A VIN is definitive. Failing that: a named dealer plus model-year and mileage
      // identifies a car well enough that price is left out of the key deliberately — the
      // same car listed at two different prices is precisely the duplicate worth catching.
      // With no dealer to go on, price comes back in as the discriminator.
      var base = [l.year, l.make.toLowerCase(), l.model.toLowerCase(), Math.round(l.miles / 500)];
      var key = l.vin || (l.dealer
        ? base.concat(l.dealer.toLowerCase()).join('|')
        : base.concat([Math.round(l.price / 250), l.seller]).join('|'));
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
   * Parse whatever an assistant actually hands you.
   *
   * Models emit listings in a dozen shapes: a bare array, a wrapped object, JSON inside a
   * markdown fence, one object per line, a CSV block, or any of those with a sentence of
   * prose in front. Rejecting all but one shape would push that cleanup onto the user for
   * no reason, so this accepts the lot and reports which form it recognised.
   */
  function parseText(input) {
    var raw = String(input === null || input === undefined ? '' : input).trim();
    if (!raw) return { ok: false, error: 'Nothing to read — paste some listings first.' };

    // Strip markdown code fences, with or without a language tag.
    var fenced = raw.match(/```(?:[a-zA-Z]+)?\s*([\s\S]*?)```/);
    if (fenced) raw = fenced[1].trim();

    // Whole-document JSON.
    try {
      return { ok: true, data: JSON.parse(raw), form: 'JSON' };
    } catch (e) { /* fall through */ }

    // JSON with prose around it: take the outermost bracketed span.
    var firstBrace = raw.indexOf('{');
    var firstBracket = raw.indexOf('[');
    var start = firstBracket === -1 ? firstBrace
      : firstBrace === -1 ? firstBracket : Math.min(firstBrace, firstBracket);
    if (start > -1) {
      var closer = raw[start] === '[' ? ']' : '}';
      var end = raw.lastIndexOf(closer);
      if (end > start) {
        try {
          return { ok: true, data: JSON.parse(raw.slice(start, end + 1)), form: 'JSON in surrounding text' };
        } catch (e) { /* fall through */ }
      }
    }

    var lines = raw.split(/\r?\n/).map(function (l) { return l.trim(); })
      .filter(function (l) { return l.length; });

    // One JSON object per line, optionally comma-terminated.
    var ndjson = [];
    var allObjects = lines.length > 0 && lines.every(function (line) {
      try {
        var obj = JSON.parse(line.replace(/,$/, ''));
        if (obj && typeof obj === 'object' && !Array.isArray(obj)) { ndjson.push(obj); return true; }
      } catch (e) { /* not this form */ }
      return false;
    });
    if (allObjects) return { ok: true, data: ndjson, form: 'one JSON object per line' };

    // A delimited table with a header row.
    var delimiter = lines[0].indexOf('\t') !== -1 ? '\t' : (lines[0].indexOf(',') !== -1 ? ',' : null);
    if (delimiter && lines.length > 1) {
      var table = parseDelimited(lines, delimiter);
      if (table.length) return { ok: true, data: table, form: delimiter === '\t' ? 'TSV' : 'CSV' };
    }

    return {
      ok: false,
      error: 'Could not read that. Paste JSON (an array or a {"listings": [...]} object), ' +
             'one JSON object per line, or a CSV/TSV block with a header row.'
    };
  }

  /** Minimal delimited-text reader that understands quoted fields. */
  function parseDelimited(lines, delimiter) {
    function splitRow(line) {
      var out = [], field = '', inQuotes = false;
      for (var i = 0; i < line.length; i++) {
        var ch = line[i];
        if (inQuotes) {
          if (ch === '"' && line[i + 1] === '"') { field += '"'; i++; }
          else if (ch === '"') inQuotes = false;
          else field += ch;
        } else if (ch === '"') inQuotes = true;
        else if (ch === delimiter) { out.push(field); field = ''; }
        else field += ch;
      }
      out.push(field);
      return out.map(function (v) { return v.trim(); });
    }
    var headers = splitRow(lines[0]).map(function (h) { return h.replace(/^\ufeff/, ''); });
    if (headers.length < 2) return [];
    var rows = [];
    for (var i = 1; i < lines.length; i++) {
      var cells = splitRow(lines[i]);
      if (cells.length === 1 && !cells[0]) continue;
      var record = {};
      headers.forEach(function (h, j) { if (h) record[h] = cells[j]; });
      rows.push(record);
    }
    return rows;
  }


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
    parseText: parseText, normaliseUrl: normaliseUrl, hostOf: hostOf, ALIASES: ALIASES,
    normaliseAccidents: normaliseAccidents,
    dedupe: dedupe, cohortComps: cohortComps, scoreAll: scoreAll,
    matchesQuery: matchesQuery, filterRows: filterRows, sortRows: sortRows,
    marketStats: marketStats, shortlist: shortlist, ingest: ingest,
    median: median, quantile: quantile, SORTS: SORTS
  };
}));
