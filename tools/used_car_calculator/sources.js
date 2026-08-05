/* Used Car Deal Analyzer — live data source layer (DESIGN.md Appendix A).
 *
 * Every call: 6s timeout, silent fallback to baked data, provenance marker flipped.
 * A failure here is never fatal — the tool is fully functional offline.
 *
 * `createSources({fetchImpl, timeoutMs, keys})` returns the client; injecting
 * fetchImpl is what makes this testable without a network (see tests/engine.test.js).
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.UCDA_SOURCES = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var VPIC = 'https://vpic.nhtsa.dot.gov/api/vehicles';
  var NHTSA = 'https://api.nhtsa.gov';
  var FEG = 'https://www.fueleconomy.gov/feg/ws/rest';
  var EIA = 'https://api.eia.gov/v2';
  var FRED = 'https://api.stlouisfed.org/fred';

  function createSources(options) {
    options = options || {};
    var doFetch = options.fetchImpl ||
      (typeof fetch !== 'undefined' ? fetch.bind(typeof self !== 'undefined' ? self : null) : null);
    var timeoutMs = options.timeoutMs || 6000;
    var keys = options.keys || {};

    /** JSON GET with a hard timeout. Resolves to null on any failure — never throws. */
    function getJson(url) {
      if (!doFetch) return Promise.resolve(null);
      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timer = setTimeout(function () { if (controller) controller.abort(); }, timeoutMs);
      var opts = controller ? { signal: controller.signal } : {};
      return Promise.resolve()
        .then(function () { return doFetch(url, opts); })
        .then(function (res) {
          if (!res || !res.ok) return null;
          return res.json();
        })
        .catch(function () { return null; })
        .then(function (v) { clearTimeout(timer); return v; });
    }

    /* ------------------------------------------------ A1 · vPIC VIN decode */

    /** Map a vPIC BodyClass / fuel type onto one of our segments. */
    function segmentFromVpic(bodyClass, fuelType, electrification) {
      var b = String(bodyClass || '').toLowerCase();
      var f = String(fuelType || '').toLowerCase();
      var e = String(electrification || '').toLowerCase();
      if (e.indexOf('bev') !== -1 || f.indexOf('electric') === 0) return 'Electric';
      if (e.indexOf('hev') !== -1 || e.indexOf('phev') !== -1) return 'Hybrid';
      if (b.indexOf('pickup') !== -1 || b.indexOf('truck') !== -1) return 'Pickup truck';
      if (b.indexOf('van') !== -1) return 'Minivan';
      if (b.indexOf('sport utility') !== -1 || b.indexOf('suv') !== -1 ||
          b.indexOf('crossover') !== -1) return 'Compact SUV';
      if (b.indexOf('coupe') !== -1 || b.indexOf('convertible') !== -1 ||
          b.indexOf('roadster') !== -1) return 'Sports car';
      if (b.indexOf('hatchback') !== -1 || b.indexOf('compact') !== -1) return 'Compact car';
      return 'Midsize car';
    }

    function decodeVin(vin) {
      if (!vin || !/^[A-HJ-NPR-Z0-9]{11,17}$/i.test(String(vin).trim())) {
        return Promise.resolve({ ok: false, reason: 'invalid-format' });
      }
      var url = VPIC + '/DecodeVinValues/' + encodeURIComponent(String(vin).trim()) + '?format=json';
      return getJson(url).then(function (json) {
        var r = json && json.Results && json.Results[0];
        if (!r) return { ok: false, reason: 'unreachable' };
        if (!r.Make && !r.ModelYear) return { ok: false, reason: 'not-decoded' };
        return {
          ok: true,
          year: parseInt(r.ModelYear, 10) || null,
          make: r.Make || null,
          model: r.Model || null,
          trim: r.Trim || null,
          bodyClass: r.BodyClass || null,
          fuelType: r.FuelTypePrimary || null,
          electrification: r.ElectrificationLevel || null,
          displacementL: r.DisplacementL || null,
          transmission: r.TransmissionStyle || null,
          driveType: r.DriveType || null,
          plantCountry: r.PlantCountry || null,
          segment: segmentFromVpic(r.BodyClass, r.FuelTypePrimary, r.ElectrificationLevel)
        };
      });
    }

    /* --------------------------------------------------- A2 · NHTSA recalls */

    function recalls(make, model, year) {
      if (!make || !model || !year) return Promise.resolve(null);
      var url = NHTSA + '/recalls/recallsByVehicle?make=' + encodeURIComponent(make) +
        '&model=' + encodeURIComponent(model) + '&modelYear=' + encodeURIComponent(year);
      return getJson(url).then(function (json) {
        if (!json || !Array.isArray(json.results)) return null;
        return json.results.map(function (r) {
          return {
            component: r.Component || '',
            summary: r.Summary || '',
            consequence: r.Consequence || '',
            remedy: r.Remedy || '',
            campaign: r.NHTSACampaignNumber || '',
            reported: r.ReportReceivedDate || '',
            repairVerified: false
          };
        });
      });
    }

    /* ------------------------------------------------ A3 · NHTSA complaints */

    function complaints(make, model, year) {
      if (!make || !model || !year) return Promise.resolve(null);
      var url = NHTSA + '/complaints/complaintsByVehicle?make=' + encodeURIComponent(make) +
        '&model=' + encodeURIComponent(model) + '&modelYear=' + encodeURIComponent(year);
      return getJson(url).then(function (json) {
        if (!json || !Array.isArray(json.results)) return null;
        var counts = {};
        json.results.forEach(function (c) {
          String(c.components || '').split(/[,|]/).forEach(function (comp) {
            var name = comp.trim().toUpperCase();
            if (name) counts[name] = (counts[name] || 0) + 1;
          });
        });
        var top = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, 3);
        return { count: json.count || json.results.length, top: top, byComponent: counts };
      });
    }

    /* ---------------------------------------------- A4 · NHTSA NCAP ratings */

    function safetyRating(make, model, year) {
      if (!make || !model || !year) return Promise.resolve(null);
      var url = NHTSA + '/SafetyRatings/modelyear/' + encodeURIComponent(year) +
        '/make/' + encodeURIComponent(make) + '/model/' + encodeURIComponent(model);
      return getJson(url).then(function (json) {
        var first = json && json.Results && json.Results[0];
        if (!first || !first.VehicleId) return null;
        return getJson(NHTSA + '/SafetyRatings/VehicleId/' + first.VehicleId);
      }).then(function (json) {
        var r = json && json.Results && json.Results[0];
        if (!r) return null;
        var overall = parseFloat(r.OverallRating);
        return {
          overallRating: isFinite(overall) ? overall : null,
          frontCrash: parseFloat(r.OverallFrontCrashRating) || null,
          sideCrash: parseFloat(r.OverallSideCrashRating) || null,
          rollover: parseFloat(r.RolloverRating) || null,
          description: r.VehicleDescription || ''
        };
      });
    }

    /* ---------------------------------------- A5 · FuelEconomy.gov per-trim */

    function fuelEconomy(year, make, model) {
      if (!year || !make || !model) return Promise.resolve(null);
      var menu = FEG + '/vehicle/menu/options?year=' + encodeURIComponent(year) +
        '&make=' + encodeURIComponent(make) + '&model=' + encodeURIComponent(model);
      return getJson(menu).then(function (json) {
        var items = json && json.menuItem;
        if (!items) return null;
        var first = Array.isArray(items) ? items[0] : items;
        if (!first || !first.value) return null;
        return getJson(FEG + '/vehicle/' + encodeURIComponent(first.value));
      }).then(function (v) {
        if (!v) return null;
        var comb = parseFloat(v.comb08);
        var combE = parseFloat(v.combE);
        return {
          mpg: isFinite(comb) && comb > 0 ? comb : null,
          kwhPer100mi: isFinite(combE) && combE > 0 ? combE : null,
          fuelType: v.fuelType1 || null,
          vclass: v.VClass || null,
          annualFuelCost: parseFloat(v.fuelCost08) || null
        };
      });
    }

    /* --------------------------------------------------------- A6 · EIA */

    function gasPrice(stateDuoarea) {
      if (!keys.eia) return Promise.resolve(null);
      var area = stateDuoarea || 'NUS';
      var url = EIA + '/petroleum/pri/gnd/data/?api_key=' + encodeURIComponent(keys.eia) +
        '&frequency=weekly&data[0]=value&facets[product][]=EPMR&facets[duoarea][]=' +
        encodeURIComponent(area) + '&sort[0][column]=period&sort[0][direction]=desc&length=1';
      return getJson(url).then(function (json) {
        var row = json && json.response && json.response.data && json.response.data[0];
        var v = row && parseFloat(row.value);
        return isFinite(v) && v > 0 ? { usdPerGal: v, period: row.period, area: area } : null;
      });
    }

    function electricityPrice(stateId) {
      if (!keys.eia) return Promise.resolve(null);
      var url = EIA + '/electricity/retail-sales/data/?api_key=' + encodeURIComponent(keys.eia) +
        '&frequency=monthly&data[0]=price&facets[sectorid][]=RES' +
        (stateId ? '&facets[stateid][]=' + encodeURIComponent(stateId) : '') +
        '&sort[0][column]=period&sort[0][direction]=desc&length=1';
      return getJson(url).then(function (json) {
        var row = json && json.response && json.response.data && json.response.data[0];
        var centsPerKwh = row && parseFloat(row.price);
        return isFinite(centsPerKwh) && centsPerKwh > 0
          ? { usdPerKwh: centsPerKwh / 100, period: row.period } : null;
      });
    }

    /* -------------------------------------------------------- A7 · FRED */

    function usedCarPriceTrend() {
      if (!keys.fred) return Promise.resolve(null);
      var url = FRED + '/series/observations?series_id=CUSR0000SETA02&api_key=' +
        encodeURIComponent(keys.fred) + '&file_type=json&sort_order=desc&limit=13';
      return getJson(url).then(function (json) {
        var obs = json && json.observations;
        if (!obs || obs.length < 13) return null;
        var latest = parseFloat(obs[0].value);
        var yearAgo = parseFloat(obs[12].value);
        if (!isFinite(latest) || !isFinite(yearAgo) || yearAgo === 0) return null;
        return {
          changeYoY: (latest - yearAgo) / yearAgo,
          latest: latest,
          date: obs[0].date
        };
      });
    }

    /* ------------------------------------- A11 · market comps (user key) */

    function marketComps(params) {
      if (!keys.marketcheck || !params || !params.year || !params.make || !params.model) {
        return Promise.resolve(null);
      }
      var lo = Math.max((params.miles || 0) - 15000, 0);
      var hi = (params.miles || 0) + 15000;
      var url = 'https://mc-api.marketcheck.com/v2/search/car/active?api_key=' +
        encodeURIComponent(keys.marketcheck) +
        '&year=' + encodeURIComponent(params.year) +
        '&make=' + encodeURIComponent(params.make) +
        '&model=' + encodeURIComponent(params.model) +
        '&miles_range=' + lo + '-' + hi + '&rows=50' +
        (params.zip ? '&zip=' + encodeURIComponent(params.zip) + '&radius=100' : '');
      return getJson(url).then(function (json) {
        var listings = json && json.listings;
        if (!Array.isArray(listings) || !listings.length) return null;
        var prices = listings.map(function (l) { return parseFloat(l.price); })
          .filter(function (p) { return isFinite(p) && p > 500; })
          .sort(function (a, b) { return a - b; });
        if (!prices.length) return null;
        var q = function (p) { return prices[Math.min(Math.floor(prices.length * p), prices.length - 1)]; };
        return { median: q(0.5), p25: q(0.25), p75: q(0.75), n: prices.length };
      });
    }

    /**
     * Fetch everything relevant for one vehicle, in parallel, tolerating any subset
     * failing. Returns the `live` object the engine consumes.
     */
    function loadAll(vehicle) {
      var make = vehicle.make, model = vehicle.model, year = vehicle.year;
      return Promise.all([
        recalls(make, model, year),
        complaints(make, model, year),
        safetyRating(make, model, year),
        fuelEconomy(year, make, model),
        gasPrice(vehicle.gasArea),
        electricityPrice(vehicle.stateId),
        usedCarPriceTrend()
      ]).then(function (r) {
        var live = { provenance: {} };
        if (r[0]) { live.recalls = r[0]; live.provenance.recalls = 'live'; }
        if (r[1]) { live.complaints = r[1]; live.provenance.complaints = 'live'; }
        if (r[2]) { live.safety = r[2]; live.provenance.safety = 'live'; }
        if (r[3]) {
          if (r[3].mpg) { live.mpg = r[3].mpg; live.provenance.mpg = 'live'; }
          if (r[3].kwhPer100mi) live.kwhPer100mi = r[3].kwhPer100mi;
        }
        if (r[4]) { live.gasUsdPerGal = r[4].usdPerGal; live.provenance.gas = 'live'; }
        if (r[5]) { live.elecUsdPerKwh = r[5].usdPerKwh; live.provenance.elec = 'live'; }
        if (r[6]) { live.cpiTrend = r[6]; live.provenance.trend = 'live'; }
        return live;
      });
    }

    return {
      getJson: getJson,
      segmentFromVpic: segmentFromVpic,
      decodeVin: decodeVin,
      recalls: recalls,
      complaints: complaints,
      safetyRating: safetyRating,
      fuelEconomy: fuelEconomy,
      gasPrice: gasPrice,
      electricityPrice: electricityPrice,
      usedCarPriceTrend: usedCarPriceTrend,
      marketComps: marketComps,
      loadAll: loadAll
    };
  }

  return { createSources: createSources };
}));
