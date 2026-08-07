/* Used Car Deal Analyzer — UI layer.
 *
 * All arithmetic lives in engine.js; all network calls live in sources.js. This file
 * reads the form, calls those two, and sets the report.
 *
 * Charts are hand-drawn SVG in the report's own idiom: hairline rules, monospace ticks,
 * 2px lines with an 8px end dot ringed in the paper colour, bars with a 4px rounded data
 * end and a square baseline. Two validated series colours (teal / rust) carry identity;
 * every chart also ships a hover readout, and the two multi-row charts ship a table view.
 */
(function () {
  'use strict';

  var DATA = window.UCDA_DATA;
  var E = window.UCDA_ENGINE;

  var $ = function (id) { return document.getElementById(id); };
  var fmtUSD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  var fmtUSD2 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
  var fmtNum = new Intl.NumberFormat('en-US');
  var usd = function (v) { return fmtUSD.format(Math.round(v)); };
  var pct = function (v) { return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; };
  var css = function (n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); };
  var esc = function (s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var clamp = E.clamp;

  /**
   * Replace a node's markup only when it actually changed.
   *
   * Every edit re-runs the whole report, and a blind innerHTML assignment destroys and
   * rebuilds nodes the user may be in the middle of clicking — the browser then fires no
   * click at all, because mousedown and mouseup landed on different elements. Skipping
   * identical writes keeps untouched sections stable, so a click after an edit still lands.
   */
  function setHTML(el, html) {
    if (!el) return false;
    // Compare against the string we last wrote, not el.innerHTML — the browser
    // re-serializes markup, so an innerHTML comparison almost never matches and every
    // section would rebuild on every run.
    if (el.__ucdaHtml === html) return false;
    el.__ucdaHtml = html;
    el.innerHTML = html;
    return true;
  }

  var KEYS_STORE = 'ucda_keys_v1';
  var SAVED_STORE = 'ucda_saved_v1';
  var INPUT_STORE = 'ucda_inputs_v1';

  /** Charts are drawn at a narrower geometry on phones — this is a tool used at the kerb. */
  function narrowScreen() { return window.innerWidth < 700; }
  function chartW() { return narrowScreen() ? 430 : 720; }

  /* ------------------------------------------------------ input validation */

  /**
   * Field-level checks with specific, actionable messages.
   *
   * The engine coerces anything it is given so it can never produce NaN, but silently
   * repairing a typo is not the same as telling the user about it. Blocking errors stop the
   * report; warnings let it run and say what looks wrong.
   */
  var VALIDATORS = [
    { id: 'in-year', check: function (v) {
        var year = new Date().getFullYear() + 1;
        if (v === null) return 'Enter the model year.';
        if (v < 1981) return 'Enter a model year of 1981 or later — VIN decoding and the ' +
          'benchmark data do not cover earlier cars.';
        if (v > year) return 'That model year is in the future.';
        return null;
      } },
    { id: 'in-miles', check: function (v) {
        if (v === null) return 'Enter the odometer reading.';
        if (v < 0) return 'Mileage cannot be negative.';
        if (v > 500000) return 'That is over 500,000 miles — check the reading.';
        return null;
      } },
    { id: 'in-price', check: function (v) {
        if (v === null || v <= 0) return 'Enter the asking price.';
        if (v > 500000) return 'That is over $500,000 — check the price.';
        return null;
      } },
    { id: 'in-down', check: function (v, form) {
        if (v !== null && v < 0) return 'A down payment cannot be negative.';
        if (v !== null && form.asking && v > form.asking * 1.5) {
          return 'The down payment is larger than the car. Did you mean a smaller figure?';
        }
        return null;
      } },
    { id: 'in-tax', check: function (v) {
        if (v !== null && (v < 0 || v > 20)) return 'Sales tax should be between 0 and 20 percent.';
        return null;
      } },
    { id: 'in-apr', check: function (v) {
        if (v !== null && (v < 0 || v > 40)) return 'Enter an APR between 0 and 40 percent.';
        return null;
      } },
    { id: 'in-annualmiles', check: function (v) {
        if (v !== null && v <= 0) return 'Enter how many miles you drive in a year.';
        if (v !== null && v > 100000) return 'Over 100,000 miles a year is unusual — check the figure.';
        return null;
      } },
    { id: 'in-horizon', check: function (v) {
        if (v !== null && (v < 1 || v > 15)) return 'Choose between 1 and 15 years.';
        return null;
      } },
    { id: 'in-mpg', check: function (v) {
        if (v !== null && (v < 5 || v > 150)) return 'Combined MPG is normally between 5 and 150.';
        return null;
      } },
    { id: 'in-trade-payoff', check: function (v, form) {
        if (v !== null && v > 0 && !form.tradeInValue) {
          return 'You have a loan payoff but no trade-in value — enter what they offered you.';
        }
        return null;
      } }
  ];

  function clearFieldError(id) {
    var el = $(id);
    if (!el) return;
    var label = el.closest('label.f');
    if (!label) return;
    label.classList.remove('invalid');
    el.removeAttribute('aria-invalid');
    el.removeAttribute('aria-describedby');
    var existing = label.querySelector('.field-error');
    if (existing) existing.remove();
  }

  function showFieldError(id, message) {
    var el = $(id);
    if (!el) return;
    var label = el.closest('label.f');
    if (!label) return;
    label.classList.add('invalid');
    el.setAttribute('aria-invalid', 'true');
    var errorId = id + '-error';
    el.setAttribute('aria-describedby', errorId);
    var node = label.querySelector('.field-error');
    if (!node) {
      node = document.createElement('span');
      node.className = 'field-error';
      node.id = errorId;
      label.appendChild(node);
    }
    node.textContent = message;
  }

  /** Returns the list of blocking problems, painting each field as it goes. */
  function validate(form) {
    var problems = [];
    VALIDATORS.forEach(function (v) {
      var message = v.check(numOrNull(v.id), form);
      if (message) {
        showFieldError(v.id, message);
        problems.push({ id: v.id, message: message });
      } else {
        clearFieldError(v.id);
      }
    });
    return problems;
  }

  /* ------------------------------------------------------- announcements */

  function announce(message) {
    var node = $('sr-announce');
    if (node) node.textContent = message;
  }

  function showFatal(error) {
    var panel = $('fatal');
    if (!panel) return;
    panel.style.display = 'block';
    $('fatal-msg').textContent = 'The report could not be generated: ' +
      (error && error.message ? error.message : String(error));
    announce('The report could not be generated.');
    if (window.console && console.error) console.error(error);
  }

  function clearFatal() {
    var panel = $('fatal');
    if (panel) panel.style.display = 'none';
  }

  /** Smooth scrolling, unless the reader has asked for less motion. */
  function scrollTo(el, block) {
    if (!el) return;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: block || 'start' });
  }

  var lastCtx = null;
  var live = {};
  var sources = null;
  var scrolledOnce = false;
  var verifiedRecalls = {};    // campaign number -> true

  /* ------------------------------------------------------------- storage */

  function loadJson(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) || fallback; }
    catch (e) { return fallback; }
  }
  function saveJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  function currentKeys() { return loadJson(KEYS_STORE, {}); }
  function rebuildSources() {
    sources = window.UCDA_SOURCES.createSources({ keys: currentKeys() });
    $('btn-comps').style.display = currentKeys().marketcheck ? 'inline-block' : 'none';
  }

  /* ---------------------------------------------------------- form input */

  var US_STATES = ['', 'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL',
    'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH',
    'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT',
    'VA', 'WA', 'WV', 'WI', 'WY'];

  function val(id) { return $(id).value.trim(); }
  function numOrNull(id) {
    var v = parseFloat($(id).value);
    return isFinite(v) ? v : null;
  }

  function readForm() {
    return {
      currentYear: new Date().getFullYear(),
      name: val('in-name'), brand: val('in-brand'), model: val('in-model'),
      year: numOrNull('in-year'), segment: val('in-segment'),
      miles: numOrNull('in-miles'), asking: numOrNull('in-price'), mpg: numOrNull('in-mpg'),
      condition: val('in-condition'), title: val('in-title'), accidents: val('in-accidents'),
      owners: numOrNull('in-owners'), records: val('in-records'), ppi: val('in-ppi'),
      comps: [numOrNull('in-comp1'), numOrNull('in-comp2'), numOrNull('in-comp3')]
        .filter(function (c) { return c !== null; }),
      recallsVerified: $('in-recalls-verified').checked,
      payType: val('in-paytype'), down: numOrNull('in-down'), creditTier: val('in-credit'),
      apr: numOrNull('in-apr') !== null ? numOrNull('in-apr') : DATA.aprByTier[val('in-credit')],
      termMonths: parseInt(val('in-term'), 10),
      taxRate: (numOrNull('in-tax') || 0) / 100, fees: numOrNull('in-fees'),
      annualMiles: numOrNull('in-annualmiles'), horizon: numOrNull('in-horizon'),
      gasUsdPerGal: numOrNull('in-gas'), insurance: numOrNull('in-insurance'),
      income: numOrNull('in-income'),
      state: val('in-state') || null,
      tradeInValue: numOrNull('in-trade-value'),
      tradePayoff: numOrNull('in-trade-payoff'),
      applyCompBias: $('in-comp-bias').checked
    };
  }

  /* ------------------------------------------------------- score section */

  var BANDS = [
    { min: 0, label: 'Walk away', color: '--bad' },
    { min: 35, label: 'Caution', color: '--warn' },
    { min: 50, label: 'Negotiate', color: '--warn' },
    { min: 65, label: 'Good', color: '--ok' },
    { min: 80, label: 'Excellent', color: '--ok' }
  ];

  function verdictColor(level) {
    return { good: css('--ok'), warning: css('--warn'), serious: css('--warn'), critical: css('--bad') }[level]
      || css('--warn');
  }

  function renderScore(ctx) {
    $('score-num').textContent = ctx.score.score;
    $('score-verdict').textContent = ctx.verdict.icon + '  ' + ctx.verdict.label;
    $('score-verdict').style.color = verdictColor(ctx.verdict.level);
    $('score-caps').textContent = ctx.score.caps.length
      ? 'Capped by ' + ctx.score.caps.join(' and ') + '.' : '';

    // A five-segment band strip with the achieved band inked in.
    var band = BANDS.slice().reverse().find(function (b) { return ctx.score.score >= b.min; });
    $('score-band').innerHTML = BANDS.map(function (b) {
      var on = b.label === band.label;
      return '<span style="background:' + (on ? verdictColor(ctx.verdict.level) : css('--rule')) + '"></span>';
    }).join('');
    $('band-legend').textContent = '0 · walk away — 35 caution — 50 negotiate — 65 good — 80+ excellent';

    var maxPoints = Math.max.apply(null, ctx.score.parts.map(function (p) { return p.max; }));
    setHTML($('score-breakdown'), ctx.score.parts.map(function (p) {
      var w = p.points / maxPoints * 100;
      return '<tr><td>' + esc(p.label) + '<br><span class="src">' + esc(p.source) + '</span></td>' +
        '<td class="n barcell"><span class="fill" style="width:' + w.toFixed(1) + '%"></span>' +
        '<span style="position:relative">' + p.points.toFixed(1) + ' / ' + p.max + '</span></td>' +
        '<td>' + esc(p.note) + '</td></tr>';
    }).join(''));
  }

  /* ---------------------------------------------------- vehicle record */

  function renderIdentity(ctx) {
    var block = $('identity-block');
    var d = live.decoded;
    if (!d && !live.recalls && !live.safety && !live.complaints && !live.cpiTrend) {
      block.style.display = 'none';
      return;
    }
    block.style.display = 'block';
    var out = [];

    if (d) {
      var specs = [
        ['Year', d.year], ['Make', d.make], ['Model', d.model], ['Trim', d.trim],
        ['Body', d.bodyClass], ['Engine', d.displacementL ? d.displacementL + ' L' : null],
        ['Fuel', d.fuelType], ['Drive', d.driveType], ['Transmission', d.transmission],
        ['Electrification', d.electrification], ['Assembled', d.plantCountry]
      ].filter(function (s) { return s[1]; });
      out.push('<h3>Decoded from VIN · NHTSA vPIC</h3><div class="speclist">' +
        specs.map(function (s) {
          return '<div><div class="cap">' + esc(s[0]) + '</div><div class="v">' + esc(s[1]) + '</div></div>';
        }).join('') + '</div>');
    }

    if (live.safety) {
      var s = live.safety;
      var starRow = function (label, v) {
        if (!v) return '';
        var n = Math.round(v);
        return '<div><div class="cap">' + esc(label) + '</div><div class="v"><span class="stars">' +
          '★'.repeat(n) + '☆'.repeat(5 - n) + '</span></div></div>';
      };
      var rows = starRow('Overall', s.overallRating) + starRow('Frontal', s.frontCrash) +
        starRow('Side', s.sideCrash) + starRow('Rollover', s.rollover);
      if (rows) out.push('<h3>Crash test · NHTSA NCAP</h3><div class="speclist">' + rows + '</div>');
    }

    if (live.recalls) {
      out.push('<h3>Open recalls · NHTSA</h3>');
      if (!live.recalls.length) {
        out.push('<p class="block-note" style="margin:0">No open recall campaigns on record for this model year.</p>');
      } else {
        out.push('<p class="block-note">Recall repairs are free at any franchised dealer. ' +
          'Tick each one the seller can document as completed.</p>' +
          live.recalls.map(function (r, i) {
            var critical = E.isSafetyCritical(r.component, DATA);
            return '<details class="recall"><summary>' +
              '<span class="comp">' + esc(r.component || 'Unspecified') + '</span>' +
              (r.campaign ? '<span class="camp">' + esc(r.campaign) + '</span>' : '') +
              (critical ? '<span class="crit">Safety critical</span>' : '') +
              '</summary><div class="detail"><dl>' +
              (r.summary ? '<dt>Defect</dt><dd>' + esc(r.summary) + '</dd>' : '') +
              (r.consequence ? '<dt>Consequence</dt><dd>' + esc(r.consequence) + '</dd>' : '') +
              (r.remedy ? '<dt>Remedy</dt><dd>' + esc(r.remedy) + '</dd>' : '') +
              '</dl><label class="verify"><input type="checkbox" data-recall="' + i + '"' +
              (r.repairVerified ? ' checked' : '') + '> Seller documented this repair</label>' +
              '</div></details>';
          }).join(''));
      }
    } else if (ctx.bakedRecallCount) {
      out.push('<h3>Recall history · built-in</h3><p class="block-note" style="margin:0">' +
        ctx.bakedRecallCount + ' recall campaign' + (ctx.bakedRecallCount > 1 ? 's' : '') +
        ' on record for this model year. The live NHTSA check did not run — look the VIN up at ' +
        'nhtsa.gov/recalls before you buy.</p>');
    }

    if (live.complaints) {
      out.push('<h3>Owner complaints · NHTSA</h3><p class="block-note" style="margin:0">' +
        fmtNum.format(live.complaints.count) + ' complaints filed for this model year' +
        (live.complaints.top && live.complaints.top.length
          ? ', led by ' + esc(live.complaints.top.slice(0, 3).join(', ').toLowerCase()) : '') + '.</p>');
    }

    if (live.cpiTrend) out.push(renderTrendSpark(live.cpiTrend));

    if (!setHTML($('identity-body'), out.join(''))) return;

    Array.prototype.forEach.call($('identity-body').querySelectorAll('input[data-recall]'), function (box) {
      box.addEventListener('change', function () {
        var idx = parseInt(box.dataset.recall, 10);
        live.recalls[idx].repairVerified = box.checked;
        verifiedRecalls[live.recalls[idx].campaign || idx] = box.checked;
        run();
      });
    });
  }

  /** 13-month CPI sparkline — the whole series FRED returns, not just the endpoints. */
  function renderTrendSpark(trend) {
    if (!trend.series || trend.series.length < 2) return '';
    var vals = trend.series.map(function (o) { return o.value; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1;
    var W = 240, H = 40;
    var pts = vals.map(function (v, i) {
      return [(i / (vals.length - 1)) * W, H - ((v - lo) / span) * (H - 8) - 4];
    });
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join('');
    var last = pts[pts.length - 1];
    return '<h3>Used-car price index · FRED CPI</h3>' +
      '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">' +
      '<svg width="' + W + '" height="' + H + '" role="img" aria-label="Used vehicle price index, last 13 months">' +
      '<path d="' + d + '" fill="none" stroke="' + css('--s1') + '" stroke-width="2" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>' +
      '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="4" fill="' + css('--s1') +
      '" stroke="' + css('--paper') + '" stroke-width="2"/></svg>' +
      '<div style="font-size:13px;color:var(--ink-2)">Nationally, used prices are <b>' +
      (trend.changeYoY >= 0 ? 'up ' : 'down ') + Math.abs(trend.changeYoY * 100).toFixed(1) +
      '%</b> over the last 12 months.<br><span class="src">' +
      (trend.changeYoY >= 0 ? 'A rising market weakens your leverage' : 'A falling market is on your side') +
      ' · through ' + esc(trend.date) + '</span></div></div>';
  }

  /* -------------------------------------------------------------- tiles */

  function renderTiles(ctx) {
    var tiles = [
      { cap: 'Out the door', val: usd(ctx.otd), sub: usd(ctx.input.asking) + ' plus tax and fees' },
      ctx.input.financed
        ? { cap: 'Monthly payment', val: usd(ctx.loan.payment),
            sub: ctx.input.apr.toFixed(1) + '% APR · ' + ctx.input.termMonths + ' mo · ' +
                 usd(ctx.loan.totalInterest) + ' interest' }
        : { cap: 'Cash purchase', val: usd(ctx.otd), sub: 'no interest cost' },
      { cap: ctx.input.horizon + '-year cost', val: usd(ctx.tco.total),
        sub: fmtUSD2.format(ctx.tco.costPerMile) + '/mile · ' + usd(ctx.tco.costPerMonth) + '/mo' },
      { cap: 'Life remaining', val: fmtNum.format(Math.round(ctx.remainingMiles)) + ' mi',
        sub: '~' + ctx.remainingYears.toFixed(1) + ' yrs at ' + fmtNum.format(ctx.input.annualMiles) + ' mi/yr' }
    ];
    setHTML($('tiles'), tiles.map(function (t) {
      return '<div class="tile"><div class="cap">' + esc(t.cap) + '</div><div class="val">' +
        esc(t.val) + '</div><div class="sub">' + esc(t.sub) + '</div></div>';
    }).join(''));
  }

  /* ------------------------------------------- price position + ladder */

  function renderPrice(ctx) {
    var f = ctx.fair, n = ctx.negotiation;
    $('price-narrative').innerHTML = '<p class="block-note">' + (f.hasComps
      ? 'Fair value is built from your ' + f.compCount + ' comparable' + (f.compCount > 1 ? 's' : '') +
        ' (average ' + usd(f.base) + '), adjusted ' + (f.mileageAdj >= 0 ? 'up ' : 'down ') +
        usd(Math.abs(f.mileageAdj)) + ' for mileage against the ' +
        fmtNum.format(Math.round(f.expectedMiles)) + '-mile norm for a car this age, then for ' +
        'condition, title and history.'
      : 'No comparables entered, so fair value is anchored to the asking price itself and adjusted ' +
        'for mileage, condition, title and history. Add one to three real listing prices above and ' +
        'this becomes far sharper.') + '</p>';

    renderPriceChart(ctx);

    var rungs = [
      ['Open at', n.opening, 'leaves room to settle'],
      ['Target', n.target, 'a realistic good outcome'],
      ['Fair value', n.fair, 'what the car is worth'],
      ['Walk away above', n.walkAway, usd(n.walkAwayOtd) + ' out the door']
    ];
    setHTML($('price-ladder'), '<h3>Your numbers</h3><table class="ledger">' +
      rungs.map(function (r) {
        return '<tr><td>' + esc(r[0]) + '</td><td class="n" style="font-size:15px;font-weight:700">' +
          usd(r[1]) + '</td><td class="src">' + esc(r[2]) + '</td></tr>';
      }).join('') + '</table>');

    // "What would make this a good deal" — solve for price at the next band up.
    var target = ctx.score.score < 65 ? 65 : 80;
    var solved = ctx.score.score >= 80 ? null : E.priceForScore(readForm(), DATA, live, target);
    if (solved !== null && solved < ctx.input.asking) {
      $('price-solver').innerHTML = '<div class="callout">At <b>' + usd(solved) + '</b> this car would score ' +
        target + ' or better — ' + usd(ctx.input.asking - solved) + ' below the asking price.</div>';
    } else if (ctx.score.score >= 80) {
      $('price-solver').innerHTML = '<div class="callout">Already scoring in the top band at the asking price.</div>';
    } else {
      $('price-solver').innerHTML = '<div class="callout warn">No price reaches a ' + target +
        ' here — the limit is the car itself (title, history or remaining life), not the money.</div>';
    }

    if (ctx.affordability) {
      var a = ctx.affordability;
      var rows = [
        [a.downOk, 'Down payment at least 20% of out-the-door', (a.downPct * 100).toFixed(0) + '%'],
        [a.termOk, 'Term no longer than 48 months', ctx.input.financed ? ctx.input.termMonths + ' mo' : 'cash'],
        [a.shareOk, 'All-in cost within 10% of take-home', (a.share * 100).toFixed(1) + '%']
      ];
      $('afford-check').innerHTML = '<h3>The 20 / 4 / 10 rule</h3><table class="ledger">' +
        rows.map(function (r) {
          return '<tr><td style="color:' + (r[0] ? css('--ok') : css('--warn')) + ';font-family:var(--mono)">' +
            (r[0] ? '✓ PASS' : '✕ OVER') + '</td><td>' + esc(r[1]) + '</td><td class="n">' + esc(r[2]) + '</td></tr>';
        }).join('') + '</table>';
    } else {
      $('afford-check').innerHTML = '<div class="callout warn">Add your monthly take-home pay above to ' +
        'check this against the 20/4/10 affordability rule and to size the loan term.</div>';
    }
  }

  /** A one-dimensional price line: comps, fair value, the target band, and the ask. */
  function renderPriceChart(ctx) {
    var n = ctx.negotiation;
    var comps = ctx.fair.hasComps ? ctx.input.comps.filter(function (c) { return isFinite(c); }) : [];
    var all = comps.concat([n.opening, n.target, n.fair, n.walkAway, ctx.input.asking]);
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
    var pad = Math.max((hi - lo) * 0.14, 400);
    lo -= pad; hi += pad;

    var W = chartW(), H = 132, padL = 12, padR = 12, axisY = 78;
    var x = function (v) { return padL + (W - padL - padR) * ((v - lo) / (hi - lo)); };
    var svg = '';

    // The good/bad zone: everything at or below target reads as the zone you want.
    svg += '<rect x="' + x(lo) + '" y="' + (axisY - 9) + '" width="' + (x(n.target) - x(lo)) +
      '" height="18" fill="' + css('--s1-wash') + '"/>';
    svg += '<rect x="' + x(n.walkAway) + '" y="' + (axisY - 9) + '" width="' + (x(hi) - x(n.walkAway)) +
      '" height="18" fill="' + css('--s2-wash') + '"/>';
    svg += '<line x1="' + padL + '" y1="' + axisY + '" x2="' + (W - padR) + '" y2="' + axisY +
      '" stroke="' + css('--rule-mid') + '" stroke-width="1"/>';

    function marker(v, label, sub, color, above, weight) {
      var px = x(v);
      var y1 = above ? axisY - 9 : axisY + 9;
      var y2 = above ? axisY - 30 : axisY + 30;
      var anchor = px < 70 ? 'start' : px > W - 70 ? 'end' : 'middle';
      return '<line x1="' + px + '" y1="' + y1 + '" x2="' + px + '" y2="' + y2 + '" stroke="' + color +
        '" stroke-width="' + (weight || 1) + '"/>' +
        '<text x="' + px + '" y="' + (above ? y2 - 14 : y2 + 20) + '" text-anchor="' + anchor +
        '" font-size="11" font-weight="700" fill="' + css('--ink') + '">' + usd(v) + '</text>' +
        '<text x="' + px + '" y="' + (above ? y2 - 3 : y2 + 31) + '" text-anchor="' + anchor +
        '" font-size="9" letter-spacing="1.2" fill="' + css('--ink-3') + '">' + label.toUpperCase() + '</text>' +
        (sub ? '<text x="' + px + '" y="' + (above ? y2 - 25 : y2 + 42) + '" text-anchor="' + anchor +
          '" font-size="9" fill="' + css('--ink-3') + '">' + sub + '</text>' : '');
    }

    comps.forEach(function (c) {
      svg += '<circle cx="' + x(c) + '" cy="' + axisY + '" r="4.5" fill="' + css('--s1') +
        '" stroke="' + css('--paper') + '" stroke-width="2"/>';
    });
    svg += marker(n.fair, 'Fair value', '', css('--ink'), true, 2);
    svg += marker(n.target, 'Target', '', css('--s1'), false, 1.5);
    svg += marker(n.walkAway, 'Walk away', '', css('--s2'), false, 1.5);
    svg += '<circle cx="' + x(ctx.input.asking) + '" cy="' + axisY + '" r="6.5" fill="' + css('--s2') +
      '" stroke="' + css('--paper') + '" stroke-width="2"/>' +
      marker(ctx.input.asking, 'Asking', '', css('--s2'), true, 1.5);

    setHTML($('price-chart'), '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" ' +
      'style="min-width:560px" role="img" aria-label="Asking price relative to fair value, target and walk-away">' +
      svg + '</svg>');
    // Rendered into a fixed mount: removing and re-inserting the legend on every run
    // would reflow everything below it mid-interaction.
    var legendBits = [];
    if (comps.length) legendBits.push('<span class="key"><span class="sw" style="background:' + css('--s1') +
      ';height:9px;width:9px;border-radius:50%"></span>Your comparables</span>');
    legendBits.push('<span class="key"><span class="sw" style="background:' + css('--s2') +
      ';height:11px;width:11px;border-radius:50%"></span>Asking price</span>');
    setHTML($('price-legend'), legendBits.join(''));
  }

  /* -------------------------------------------------- loan term section */

  function renderTermComparison(ctx) {
    var block = $('term-block');
    if (!ctx.termComparison) {
      block.style.display = 'none';
      return;
    }
    block.style.display = 'block';
    var tc = ctx.termComparison;
    var out = [];

    var maxInterest = Math.max.apply(null, tc.options.map(function (o) { return o.totalInterest; })) || 1;

    out.push('<table class="ledger" id="term-table"><thead><tr>' +
      '<th>Term</th><th class="n">Payment</th><th class="n">Total interest</th>' +
      '<th class="n">Interest cost</th><th class="n">Underwater</th>' +
      (ctx.input.income > 0 ? '<th class="n">Of take-home</th>' : '') +
      '</tr></thead><tbody>' +
      tc.options.map(function (o) {
        var isRec = tc.recommended && o.months === tc.recommended.months;
        var isCurrent = o.months === ctx.input.termMonths;
        return '<tr class="' + (isRec ? 'rec-row' : '') + '" data-term="' + o.months + '">' +
          '<td><b>' + o.months + ' mo</b>' +
          (isRec ? ' <span class="rec-flag">◆ BEST</span>' : '') +
          (isCurrent ? ' <span class="src">current</span>' : '') + '</td>' +
          '<td class="n">' + usd(o.payment) + '</td>' +
          '<td class="n barcell"><span class="fill" style="width:' +
            (o.totalInterest / maxInterest * 100).toFixed(0) + '%"></span>' +
            '<span style="position:relative">' + usd(o.totalInterest) + '</span></td>' +
          '<td class="n">' + (o.interestShare * 100).toFixed(0) + '% of loan</td>' +
          '<td class="n">' + (o.underwaterMonths ? o.underwaterMonths + ' mo' : '—') + '</td>' +
          (ctx.input.income > 0
            ? '<td class="n" style="color:' + (o.affordable ? css('--ok') : css('--warn')) + '">' +
              (o.incomeShare * 100).toFixed(1) + '%</td>'
            : '') + '</tr>';
      }).join('') + '</tbody></table>');

    if (tc.recommended) {
      var rec = tc.recommended;
      var current = tc.options.filter(function (o) { return o.months === ctx.input.termMonths; })[0];
      var delta = current ? current.totalInterest - rec.totalInterest : 0;
      out.push('<div class="callout"><b>Recommended: ' + rec.months + ' months</b> at ' +
        usd(rec.payment) + '/month. ' + esc(tc.note) +
        (delta > 1 && current && current.months !== rec.months
          ? ' Against the ' + current.months + '-month term you have selected, that saves <b>' +
            usd(delta) + '</b> in interest.'
          : delta < -1 && current
            ? ' It costs ' + usd(-delta) + ' more interest than your selected term but is the shorter safe option.'
            : '') + '</div>');
    } else {
      out.push('<div class="callout warn">' + esc(tc.note) + '</div>');
    }

    // Target-payment solver.
    var targetPayment = numOrNull('in-target-payment');
    if (targetPayment) {
      var months = E.termForPayment(tc.principal, ctx.input.apr, targetPayment);
      out.push(months === null
        ? '<div class="callout warn">A payment of ' + usd(targetPayment) + ' never pays this loan off — ' +
          'it does not even cover the monthly interest of ' +
          usd(tc.principal * ctx.input.apr / 100 / 12) + '. Put more down or spend less.</div>'
        : '<div class="callout">To hit <b>' + usd(targetPayment) + '/month</b> you would need about <b>' +
          months + ' months</b> (' + (months / 12).toFixed(1) + ' years)' +
          (months > 84 ? ' — longer than any lender will normally write, which means this car is out of budget.'
                       : '.') + '</div>');
    }

    var termChanged = setHTML($('term-body'), out.join(''));

    if (termChanged) {
      Array.prototype.forEach.call($('term-body').querySelectorAll('tr[data-term]'), function (row) {
        row.style.cursor = 'pointer';
      });
    }
  }

  /* --------------------------------------------------------- line chart */

  function renderLineChart(ctx) {
    var years = ctx.chartYears;
    var series = [{ name: 'Estimated value', color: css('--s1'), data: ctx.valueCurve }];
    if (ctx.input.financed) series.push({ name: 'Loan balance', color: css('--s2'), data: ctx.balanceCurve });

    $('line-legend').innerHTML = series.length >= 2 ? series.map(function (s) {
      return '<span class="key"><span class="sw" style="background:' + s.color + '"></span>' + esc(s.name) + '</span>';
    }).join('') : '';

    $('equity-note').textContent = ctx.input.financed
      ? (ctx.underwaterUntil >= 1
          ? 'The loan stays bigger than the car through year ' + ctx.underwaterUntil +
            '. Until then, selling or a write-off leaves you paying the difference out of pocket.'
          : 'You never owe more than the car is worth — the value line stays above the balance the whole way.')
      : 'Paid in cash, so this is simply what the car should be worth as the years pass.';

    var narrow = narrowScreen();
    var W = chartW(), H = narrow ? 250 : 290, padL = narrow ? 58 : 74,
        padR = narrow ? 62 : 108, padT = 14, padB = 32;
    var maxY = Math.max.apply(null, series.reduce(function (a, s) { return a.concat(s.data); }, [])) * 1.10 || 1;
    var x = function (t) { return padL + (W - padL - padR) * (years ? t / years : 0); };
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / maxY); };

    var step = Math.max(Math.ceil(maxY / 4 / 1000) * 1000, 1000);
    var svg = '';
    for (var g = 0; g <= maxY; g += step) {
      svg += '<line x1="' + padL + '" y1="' + y(g) + '" x2="' + (W - padR) + '" y2="' + y(g) +
        '" stroke="' + css('--rule') + '" stroke-width="1"/>' +
        '<text x="' + (padL - 9) + '" y="' + (y(g) + 4) + '" text-anchor="end" font-size="10" fill="' +
        css('--ink-3') + '">' + usd(g) + '</text>';
    }
    for (var t = 0; t <= years; t++) {
      svg += '<text x="' + x(t) + '" y="' + (H - 9) + '" text-anchor="middle" font-size="10" fill="' +
        css('--ink-3') + '">' + (t === 0 ? 'NOW' : 'Y' + t) + '</text>';
    }
    svg += '<line x1="' + padL + '" y1="' + y(0) + '" x2="' + (W - padR) + '" y2="' + y(0) +
      '" stroke="' + css('--rule-mid') + '" stroke-width="1"/>';

    series.forEach(function (s) {
      var d = s.data.map(function (v, i) {
        return (i === 0 ? 'M' : 'L') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
      }).join('');
      var end = s.data.length - 1;
      svg += '<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="2" ' +
        'stroke-linejoin="round" stroke-linecap="round"/>' +
        '<circle cx="' + x(end) + '" cy="' + y(s.data[end]) + '" r="4.5" fill="' + s.color +
        '" stroke="' + css('--paper') + '" stroke-width="2"/>' +
        '<text x="' + (x(end) + 10) + '" y="' + (y(s.data[end]) + 4) + '" font-size="11" fill="' +
        css('--ink-2') + '">' + usd(s.data[end]) + '</text>';
    });
    svg += '<line id="crosshair" x1="0" y1="' + padT + '" x2="0" y2="' + (H - padB) + '" stroke="' +
      css('--ink-3') + '" stroke-width="1" style="display:none"/>' +
      '<rect id="line-hit" x="' + padL + '" y="' + padT + '" width="' + (W - padL - padR) +
      '" height="' + (H - padT - padB) + '" fill="transparent"/>';

    setHTML($('line-chart'), '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" ' +
      'aria-label="Projected value' + (ctx.input.financed ? ' against loan balance' : '') +
      ' by year" style="min-width:' + (narrow ? 0 : 560) + 'px">' + svg + '</svg>');

    attachLineHover(ctx, series, x, y, years, W, padL, padR);

    var head = '<table class="ledger"><thead><tr><th>Year</th>' +
      series.map(function (s) { return '<th class="n">' + esc(s.name) + '</th>'; }).join('') +
      (ctx.input.financed ? '<th class="n">Equity</th>' : '') + '</tr></thead><tbody>';
    for (var r = 0; r <= years; r++) {
      head += '<tr><td>' + (r === 0 ? 'Now' : 'Year ' + r) + '</td>' +
        series.map(function (s) { return '<td class="n">' + usd(s.data[r]) + '</td>'; }).join('') +
        (ctx.input.financed ? '<td class="n">' + usd(ctx.valueCurve[r] - ctx.balanceCurve[r]) + '</td>' : '') +
        '</tr>';
    }
    $('line-table').innerHTML = head + '</tbody></table>';
  }

  function attachLineHover(ctx, series, x, y, years, W, padL, padR) {
    var svgEl = $('line-chart').querySelector('svg');
    var hit = svgEl.querySelector('#line-hit');
    var cross = svgEl.querySelector('#crosshair');
    var tip = $('line-tooltip');
    var rig = tip.closest('.chart-rig');

    function move(clientX, clientY) {
      var pt = svgEl.createSVGPoint(); pt.x = clientX; pt.y = clientY;
      var sp = pt.matrixTransform(svgEl.getScreenCTM().inverse());
      var t = clamp(Math.round((sp.x - padL) / (W - padL - padR) * years), 0, years);
      cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t));
      cross.style.display = 'block';
      tip.style.display = 'block';
      tip.innerHTML = '<div class="h">' + (t === 0 ? 'Today' : 'Year ' + t) + '</div>' +
        series.map(function (s) {
          return '<div class="r"><span>' + esc(s.name) + '</span><b>' + usd(s.data[t]) + '</b></div>';
        }).join('') +
        (ctx.input.financed
          ? '<div class="r"><span>Equity</span><b>' + usd(ctx.valueCurve[t] - ctx.balanceCurve[t]) + '</b></div>'
          : '');
      var box = rig.getBoundingClientRect();
      tip.style.left = clamp(clientX - box.left + 14, 0, Math.max(box.width - 200, 0)) + 'px';
      tip.style.top = (clientY - box.top + 14) + 'px';
    }
    hit.addEventListener('mousemove', function (e) { move(e.clientX, e.clientY); });
    hit.addEventListener('touchmove', function (e) {
      if (e.touches[0]) move(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });
    function hide() { cross.style.display = 'none'; tip.style.display = 'none'; }
    hit.addEventListener('mouseleave', hide);
    hit.addEventListener('touchend', hide);
  }

  /* ---------------------------------------------------- horizontal bars */

  function horizontalBars(mountId, tipId, items, opts) {
    opts = opts || {};
    var mount = $(mountId);
    if (!items.length) { mount.innerHTML = ''; return; }
    var max = Math.max.apply(null, items.map(function (i) { return i[1]; })) || 1;
    var narrow = narrowScreen();
    var rowH = 34, labelW = narrow ? 118 : (opts.labelW || 196),
        valueW = narrow ? 76 : 92, barMaxW = narrow ? 210 : 380, barH = 17, r = 4;
    var W = labelW + barMaxW + valueW, H = items.length * rowH + 4;
    var color = opts.color || css('--s1');
    var svg = '';
    items.forEach(function (it, i) {
      var bw = Math.max(it[1] / max * barMaxW, 3);
      var mid = i * rowH + rowH / 2 + 2;
      var top = mid - barH / 2;
      var straight = Math.max(bw - r, 0);
      svg += '<text x="' + (labelW - 12) + '" y="' + (mid + 4) + '" text-anchor="end" font-size="11" fill="' +
        css('--ink-2') + '">' + esc(it[0]) + '</text>' +
        '<path d="M' + labelW + ',' + top + ' h' + straight + ' a' + r + ',' + r + ' 0 0 1 ' + r + ',' + r +
        ' v' + (barH - 2 * r) + ' a' + r + ',' + r + ' 0 0 1 -' + r + ',' + r + ' h-' + straight + ' z" fill="' +
        color + '" class="' + (opts.barClass || 'tco-bar') + '" data-i="' + i + '"/>' +
        '<text x="' + (labelW + bw + 9) + '" y="' + (mid + 4) + '" font-size="11" fill="' + css('--ink') +
        '">' + esc(opts.format ? opts.format(it[1]) : usd(it[1])) + '</text>';
    });
    svg += '<line x1="' + labelW + '" y1="0" x2="' + labelW + '" y2="' + H + '" stroke="' +
      css('--rule-mid') + '" stroke-width="1"/>';
    mount.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" aria-label="' +
      esc(opts.label || 'Bar chart') + '" style="min-width:' + W + 'px;display:block">' + svg + '</svg>';

    if (!tipId) return;
    var tip = $(tipId);
    var rig = tip.closest('.chart-rig');
    Array.prototype.forEach.call(mount.querySelectorAll('path[data-i]'), function (bar) {
      bar.addEventListener('mousemove', function (e) {
        var it = items[parseInt(bar.dataset.i, 10)];
        tip.style.display = 'block';
        tip.innerHTML = '<div class="h">' + esc(it[0]) + '</div>' + opts.tip(it);
        var box = rig.getBoundingClientRect();
        tip.style.left = clamp(e.clientX - box.left + 14, 0, Math.max(box.width - 200, 0)) + 'px';
        tip.style.top = (e.clientY - box.top + 14) + 'px';
      });
      bar.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    });
  }

  function renderBarChart(ctx) {
    var isEV = ctx.input.segment === 'Electric';
    var items = [
      ['Depreciation', ctx.tco.depreciation],
      [isEV ? 'Electricity' : 'Fuel', ctx.tco.fuel],
      ['Insurance', ctx.tco.insurance],
      ['Maintenance & repairs', ctx.tco.maintenance],
      ['Taxes, fees & registration', ctx.tco.taxesFees]
    ];
    if (ctx.tco.interest > 0.5) items.push(['Loan interest', ctx.tco.interest]);
    items = items.filter(function (i) { return i[1] > 0.5; }).sort(function (a, b) { return b[1] - a[1]; });

    $('tco-title').textContent = 'Cost of ownership — ' + ctx.input.horizon +
      ' year' + (ctx.input.horizon > 1 ? 's' : '');
    $('tco-note').textContent = usd(ctx.tco.total) + ' all in over ' +
      fmtNum.format(ctx.tco.totalMiles) + ' miles: ' + fmtUSD2.format(ctx.tco.costPerMile) +
      ' a mile, ' + usd(ctx.tco.costPerMonth) + ' a month. That is the number to compare between cars — ' +
      'not the sticker.';

    horizontalBars('bar-chart', 'bar-tooltip', items, {
      label: 'Cost of ownership by category',
      tip: function (it) {
        return '<div class="r"><span>Total</span><b>' + usd(it[1]) + '</b></div>' +
          '<div class="r"><span>Share</span><b>' + (it[1] / ctx.tco.total * 100).toFixed(0) + '%</b></div>' +
          '<div class="r"><span>Per month</span><b>' + usd(it[1] / ctx.input.horizon / 12) + '</b></div>';
      }
    });

    $('bar-table').innerHTML = '<table class="ledger"><thead><tr><th>Category</th><th class="n">Total</th>' +
      '<th class="n">Share</th><th class="n">Per month</th></tr></thead><tbody>' +
      items.map(function (it) {
        return '<tr><td>' + esc(it[0]) + '</td><td class="n">' + usd(it[1]) + '</td><td class="n">' +
          (it[1] / ctx.tco.total * 100).toFixed(0) + '%</td><td class="n">' +
          usd(it[1] / ctx.input.horizon / 12) + '</td></tr>';
      }).join('') +
      '<tr class="total"><td>Total</td><td class="n">' + usd(ctx.tco.total) + '</td><td class="n">100%</td>' +
      '<td class="n">' + usd(ctx.tco.costPerMonth) + '</td></tr></tbody></table>';
  }

  /** Cumulative spend, with the per-mile figure improving as fixed costs amortize. */
  function renderCumulativeChart(ctx) {
    var cum = ctx.cumulative;
    var narrow = narrowScreen();
    var W = chartW(), H = narrow ? 176 : 200, padL = narrow ? 58 : 74,
        padR = narrow ? 54 : 96, padT = 12, padB = 30;
    var maxY = cum[cum.length - 1].total * 1.10 || 1;
    var years = cum.length - 1;
    var x = function (t) { return padL + (W - padL - padR) * (years ? t / years : 0); };
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / maxY); };
    var svg = '';
    var step = Math.max(Math.ceil(maxY / 3 / 1000) * 1000, 1000);
    for (var g = 0; g <= maxY; g += step) {
      svg += '<line x1="' + padL + '" y1="' + y(g) + '" x2="' + (W - padR) + '" y2="' + y(g) +
        '" stroke="' + css('--rule') + '" stroke-width="1"/>' +
        '<text x="' + (padL - 9) + '" y="' + (y(g) + 4) + '" text-anchor="end" font-size="10" fill="' +
        css('--ink-3') + '">' + usd(g) + '</text>';
    }
    var line = cum.map(function (c, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(c.total).toFixed(1); }).join('');
    svg += '<path d="' + line + ' L' + x(years).toFixed(1) + ',' + y(0) + ' L' + x(0).toFixed(1) + ',' + y(0) +
      ' Z" fill="' + css('--s1-wash') + '"/>';
    svg += '<path d="' + line + '" fill="none" stroke="' + css('--s1') + '" stroke-width="2" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>';
    cum.forEach(function (c, i) {
      svg += '<text x="' + x(i) + '" y="' + (H - 9) + '" text-anchor="middle" font-size="10" fill="' +
        css('--ink-3') + '">' + (i === 0 ? 'BUY' : 'Y' + i) + '</text>';
      if (i === years) {
        svg += '<circle cx="' + x(i) + '" cy="' + y(c.total) + '" r="4.5" fill="' + css('--s1') +
          '" stroke="' + css('--paper') + '" stroke-width="2"/>' +
          '<text x="' + (x(i) + 10) + '" y="' + (y(c.total) + 4) + '" font-size="11" fill="' + css('--ink-2') +
          '">' + usd(c.total) + '</text>';
      }
      svg += '<rect class="cum-hit" data-i="' + i + '" x="' + (x(i) - 16) + '" y="' + padT + '" width="32" height="' +
        (H - padT - padB) + '" fill="transparent"/>';
    });
    setHTML($('cum-chart'), '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" ' +
      'aria-label="Cumulative cost of ownership by year" style="min-width:' +
      (narrow ? 0 : 560) + 'px">' + svg + '</svg>');

    var tip = $('cum-tip');
    var rig = tip.closest('.chart-rig');
    Array.prototype.forEach.call($('cum-chart').querySelectorAll('.cum-hit'), function (hit) {
      hit.addEventListener('mousemove', function (e) {
        var c = cum[parseInt(hit.dataset.i, 10)];
        tip.style.display = 'block';
        tip.innerHTML = '<div class="h">' + (c.year === 0 ? 'At purchase' : 'Through year ' + c.year) + '</div>' +
          '<div class="r"><span>Spent so far</span><b>' + usd(c.total) + '</b></div>' +
          (c.year > 0 ? '<div class="r"><span>Per mile</span><b>' + fmtUSD2.format(c.perMile) + '</b></div>' +
            '<div class="r"><span>Per month</span><b>' + usd(c.total / (c.year * 12)) + '</b></div>' : '');
        var box = rig.getBoundingClientRect();
        tip.style.left = clamp(e.clientX - box.left + 14, 0, Math.max(box.width - 200, 0)) + 'px';
        tip.style.top = (e.clientY - box.top + 14) + 'px';
      });
      hit.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    });
  }

  /** Complaint volume by component — live NHTSA data, otherwise the block stays hidden. */
  function renderComplaintChart(ctx) {
    var block = $('complaint-block');
    var c = live.complaints;
    if (!c || !c.byComponent || !Object.keys(c.byComponent).length) {
      block.style.display = 'none';
      return;
    }
    block.style.display = 'block';
    var entries = Object.keys(c.byComponent).map(function (k) { return [k, c.byComponent[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8)
      .map(function (e) {
        var name = e[0].toLowerCase().replace(/(^|\s)\w/g, function (m) { return m.toUpperCase(); });
        return [name.length > 32 ? name.slice(0, 31) + '…' : name, e[1]];
      });

    $('complaint-note').textContent = fmtNum.format(c.count) +
      ' complaints filed with NHTSA for this model year, grouped by the system owners blamed. ' +
      'Point your pre-purchase inspection at the top of this list.';

    horizontalBars('complaint-chart', 'complaint-tip', entries, {
      color: css('--s2'), barClass: 'complaint-bar', labelW: 210,
      label: 'Owner complaints by component',
      format: function (v) { return fmtNum.format(v); },
      tip: function (it) {
        return '<div class="r"><span>Complaints</span><b>' + fmtNum.format(it[1]) + '</b></div>' +
          '<div class="r"><span>Share</span><b>' + (it[1] / c.count * 100).toFixed(0) + '%</b></div>';
      }
    });
  }

  /* ------------------------------------------------ what it costs at signing */

  function renderSigning(ctx) {
    var p = ctx.purchase;
    var rows = [['Vehicle price', ctx.input.asking, '']];
    if (p.tradeValue > 0) {
      rows.push(['Trade-in allowance', -p.tradeValue, 'what they gave you for the old car']);
      if (p.tradePayoff > 0) rows.push(['Loan still owed on the trade', p.tradePayoff, 'paid off from the allowance']);
      rows.push(['Taxable amount', p.taxableAmount, p.creditAllowed
        ? 'your state taxes the difference only'
        : 'your state taxes the full price despite the trade']);
    }
    rows.push(['Sales tax', p.salesTax, (ctx.input.taxRate * 100).toFixed(2) + '%' +
      (p.taxSavings > 0 ? ' — ' + usd(p.taxSavings) + ' less thanks to the trade' : '')]);
    rows.push(['Dealer and title fees', ctx.input.fees, '']);
    rows.push(['Down payment', -ctx.input.down, 'cash from you']);
    if (p.tradeEquity !== 0) {
      rows.push(['Trade equity applied', -p.tradeEquity, p.tradeEquity < 0
        ? 'negative — this shortfall gets added to the loan' : 'value left after the payoff']);
    }

    var html = '<table class="ledger"><tbody>' + rows.map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td class="n">' +
        (r[1] < 0 ? '\u2212' + usd(Math.abs(r[1])) : usd(r[1])) +
        '</td><td class="src">' + esc(r[2]) + '</td></tr>';
    }).join('') +
      '<tr class="total"><td>' + (ctx.input.financed ? 'Amount financed' : 'Cash due at signing') +
      '</td><td class="n">' + usd(ctx.input.financed ? p.principal : Math.max(p.otd - ctx.input.down - p.tradeEquity, 0)) +
      '</td><td class="src">out the door ' + usd(p.otd) + '</td></tr></tbody></table>';

    if (p.rolledNegativeEquity > 0) {
      html += '<div class="callout warn">You owe <b>' + usd(p.rolledNegativeEquity) +
        '</b> more on the trade than it is worth. That debt does not disappear — it is added to ' +
        'this loan, so you start the new car underwater by that much.</div>';
    } else if (p.taxSavings > 0) {
      html += '<div class="callout">The trade-in cuts your sales tax by <b>' + usd(p.taxSavings) +
        '</b> because your state taxes only the price difference. Selling privately would have to ' +
        'beat the allowance by more than that to be worth it.</div>';
    }
    setHTML($('signing-body'), html);
  }

  /* ------------------------------------------------------- service schedule */

  function renderServices(ctx) {
    var sv = ctx.services;
    var block = $('service-block');
    if (!sv.horizon.length) {
      $('service-note').textContent = 'Nothing on the standard wear schedule falls due within ' +
        'the years you plan to keep this car.';
      setHTML($('service-body'), '');
      return;
    }
    block.style.display = 'block';
    $('service-note').textContent = 'Wear items on the odometer clock, at ' +
      fmtNum.format(ctx.input.annualMiles) + ' miles a year. These are what the maintenance ' +
      'budget above actually gets spent on — shown separately because the first year is often ' +
      'the expensive one.';

    var months = function (m) {
      return m < 1 ? 'now' : m < 12 ? Math.round(m) + ' mo' : (m / 12).toFixed(1) + ' yr';
    };
    var html = '<table class="ledger"><thead><tr><th>Item</th><th class="n">Due at</th>' +
      '<th class="n">From now</th><th class="n">Cost</th></tr></thead><tbody>' +
      sv.horizon.map(function (item) {
        var soon = item.monthsAway <= 12;
        return '<tr><td>' + esc(item.name) +
          (item.conditional ? '<br><span class="src">' + esc(item.conditional) + '</span>' : '') +
          '</td><td class="n">' + fmtNum.format(item.dueAtMiles) + ' mi</td>' +
          '<td class="n"' + (soon ? ' style="color:' + css('--warn') + '"' : '') + '>' +
          months(item.monthsAway) + '</td><td class="n">' + usd(item.cost) + '</td></tr>';
      }).join('') +
      '<tr class="total"><td>Total over ' + ctx.input.horizon + ' years</td><td></td><td></td>' +
      '<td class="n">' + usd(sv.horizonTotal) + '</td></tr></tbody></table>';

    if (sv.firstYearTotal > 0) {
      html += sv.frontLoaded
        ? '<div class="callout warn"><b>' + usd(sv.firstYearTotal) + ' lands in the first year</b> — ' +
          'about ' + usd(sv.overrun) + ' more than the average annual maintenance budget of ' +
          usd(sv.budgetYearOne) + '. Ask the seller to cover it, or take it off the price.</div>'
        : '<div class="callout"><b>' + usd(sv.firstYearTotal) + '</b> of wear items fall due in the ' +
          'first year, within the ' + usd(sv.budgetYearOne) + ' annual maintenance budget.</div>';
    }
    setHTML($('service-body'), html);
  }

  /* ----------------------------------------------------------- sensitivity */

  function renderSensitivity(ctx) {
    var s = E.sensitivity(readForm(), DATA, live);
    $('sens-note').textContent = 'The ' + usd(s.base) + ' total above is a point estimate built on ' +
      'class averages. Varying each assumption on its own shows how much it actually moves the ' +
      'answer — ' + esc(String(s.driver).toLowerCase()) + ' is the one that matters most here.';

    var items = s.items.map(function (i) { return [i.label, i.swing]; });
    horizontalBars('sens-chart', 'sens-tip', items, {
      color: css('--s2'), barClass: 'sens-bar', labelW: 168,
      label: 'How much each assumption moves the total cost',
      format: function (v) { return '±' + usd(v / 2); },
      tip: function (it) {
        var f = s.items.filter(function (x) { return x.label === it[0]; })[0];
        return '<div class="r"><span>' + esc(f.lowLabel) + '</span><b>' + usd(f.low) + '</b></div>' +
          '<div class="r"><span>' + esc(f.highLabel) + '</span><b>' + usd(f.high) + '</b></div>' +
          '<div class="r"><span>Swing</span><b>' + usd(f.swing) + '</b></div>';
      }
    });

    setHTML($('sens-body'), '<div class="callout">Taking the largest single uncertainty either way, ' +
      'the realistic range for ' + ctx.input.horizon + '-year cost of ownership is <b>' +
      usd(s.low) + ' to ' + usd(s.high) + '</b>, not ' + usd(s.base) + ' exactly. ' +
      'The cheapest way to shrink this range is to get a real insurance quote and a ' +
      'pre-purchase inspection before you commit.</div>' +
      '<table class="ledger"><thead><tr><th>Assumption</th><th>Why it is uncertain</th>' +
      '<th class="n">Range of total cost</th></tr></thead><tbody>' +
      s.items.map(function (i) {
        return '<tr><td>' + esc(i.label) + '<br><span class="src">' + esc(i.lowLabel) + ' to ' +
          esc(i.highLabel) + '</span></td><td>' + esc(i.note) + '</td><td class="n">' +
          usd(i.low) + ' – ' + usd(i.high) + '</td></tr>';
      }).join('') + '</tbody></table>');
  }

  /* -------------------------------------------------------- the bottom line */

  /** The paragraph a buyer can act on without reading anything else. */
  function renderBottomLine(ctx) {
    var n = ctx.negotiation;
    var allIn = ctx.tco.costPerMonth;
    var critical = ctx.findings.filter(function (f) { return f.level === 'critical'; });
    var warnings = ctx.findings.filter(function (f) { return f.level === 'warning'; });

    var lead;
    if (ctx.score.score >= 80) {
      lead = 'This is a strong deal. ';
    } else if (ctx.score.score >= 65) {
      lead = 'This is a reasonable deal worth pursuing. ';
    } else if (ctx.score.score >= 50) {
      lead = 'This is workable but only at the right price. ';
    } else if (ctx.score.score >= 35) {
      lead = 'This one is weak. Be sceptical. ';
    } else {
      lead = 'Walk away from this one. ';
    }
    if (critical.length) {
      lead = 'Do not buy this car until the issues below are resolved. ';
    }

    var body = '<p>' + lead +
      'Open at <b>' + usd(n.opening) + '</b>, aim to settle at <b>' + usd(n.target) +
      '</b>, and walk away above <b>' + usd(n.walkAway) + '</b> (' + usd(n.walkAwayOtd) +
      ' out the door). Expect it to cost <b>' + usd(allIn) + ' a month</b> all in — ' +
      'payment, fuel, insurance, upkeep and depreciation together — over ' +
      ctx.input.horizon + ' years.</p>';

    if (ctx.input.financed && ctx.termComparison && ctx.termComparison.recommended) {
      body += '<p>Finance it over <b>' + ctx.termComparison.recommended.months +
        ' months</b> at ' + usd(ctx.termComparison.recommended.payment) + ' a month' +
        (ctx.underwaterUntil >= 1
          ? ', and note you would owe more than it is worth until year ' + ctx.underwaterUntil + '.'
          : '.') + '</p>';
    }

    var actions = [];
    critical.slice(0, 3).forEach(function (f) { actions.push(f.tag + ': ' + f.text.split('. ')[0] + '.'); });
    warnings.slice(0, 3 - Math.min(critical.length, 3)).forEach(function (f) {
      actions.push(f.tag + ': ' + f.text.split('. ')[0] + '.');
    });
    if (ctx.input.ppi !== 'yes') actions.push('Book an independent pre-purchase inspection before you commit.');
    if (!ctx.fair.hasComps) actions.push('Add two or three real listing prices above to sharpen the valuation.');

    if (actions.length) {
      body += '<ul class="actions">' + actions.slice(0, 4).map(function (a) {
        return '<li>' + esc(a) + '</li>';
      }).join('') + '</ul>';
    }
    setHTML($('bottomline'), '<h3>The bottom line</h3>' + body);
  }

  function renderStickyBar(ctx) {
    var bar = $('stickybar');
    bar.classList.add('on');
    bar.setAttribute('aria-hidden', 'false');
    $('sb-num').textContent = ctx.score.score;
    $('sb-verdict').textContent = ctx.verdict.label;
    $('sb-verdict').style.color = verdictColor(ctx.verdict.level);
    $('sb-offer').textContent = usd(ctx.negotiation.opening);
    $('sb-walk').textContent = usd(ctx.negotiation.walkAway);
    $('sb-mo').textContent = usd(ctx.tco.costPerMonth);
  }

  function renderFindings(ctx) {
    var mark = { good: '✓', warning: '!', critical: '×' };
    setHTML($('findings'), ctx.findings.length
      ? ctx.findings.map(function (f) {
          return '<li class="f-' + f.level + '"><span class="mk">[' + mark[f.level] + ']</span>' +
            '<span class="findings-body"><span class="tag">' + esc(f.tag) + '</span>' + esc(f.text) + '</span></li>';
        }).join('')
      : '<li class="f-good"><span class="mk">[✓]</span><span class="findings-body">Nothing of concern found.</span></li>');
  }

  /* ------------------------------------------------------- comparison */

  var COMPARE_COLS = [
    { head: 'Vehicle', get: function (c) { return esc(c.name); }, csv: function (c) { return c.name; } },
    { head: 'Score', get: function (c) { return c.score; }, best: 'max', raw: function (c) { return c.score; },
      csv: function (c) { return c.score; } },
    { head: 'Asking', get: function (c) { return usd(c.asking); }, csv: function (c) { return c.asking; } },
    { head: 'Fair value', get: function (c) { return usd(c.fair); }, csv: function (c) { return Math.round(c.fair); } },
    { head: 'Out the door', get: function (c) { return usd(c.otd); }, best: 'min',
      raw: function (c) { return c.otd; }, csv: function (c) { return Math.round(c.otd); } },
    { head: 'Monthly', get: function (c) { return c.monthly > 0 ? usd(c.monthly) : '—'; },
      csv: function (c) { return Math.round(c.monthly); } },
    { head: 'Cost/yr owned', get: function (c) { return usd(c.tco / c.horizon); }, best: 'min',
      raw: function (c) { return c.tco / c.horizon; }, csv: function (c) { return Math.round(c.tco / c.horizon); } },
    { head: '$/mile', get: function (c) { return fmtUSD2.format(c.cpm); }, best: 'min',
      raw: function (c) { return c.cpm; }, csv: function (c) { return c.cpm.toFixed(3); } },
    { head: 'Life left', get: function (c) { return fmtNum.format(Math.round(c.lifeLeft)) + ' mi'; },
      best: 'max', raw: function (c) { return c.lifeLeft; }, csv: function (c) { return Math.round(c.lifeLeft); } },
    { head: 'Verdict', get: function (c) { return esc(c.verdict); }, csv: function (c) { return c.verdict; } },
    { head: '', get: function (c) { return '<button class="tiny ghost" data-del="' + c.id + '">✕</button>'; },
      csv: null }
  ];

  function renderCompare() {
    var list = loadJson(SAVED_STORE, []);
    var card = $('compare-card');
    if (!list.length) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    var bestRow = {};
    COMPARE_COLS.forEach(function (col, ci) {
      if (!col.best || list.length < 2) return;
      var best = 0;
      list.forEach(function (c, ri) {
        var v = col.raw(c), bv = col.raw(list[best]);
        if (col.best === 'max' ? v > bv : v < bv) best = ri;
      });
      bestRow[ci] = best;
    });

    if (!setHTML($('compare-table'),
      '<thead><tr>' + COMPARE_COLS.map(function (c) { return '<th>' + c.head + '</th>'; }).join('') + '</tr></thead>' +
      '<tbody>' + list.map(function (c, ri) {
        return '<tr>' + COMPARE_COLS.map(function (col, ci) {
          return '<td class="' + (bestRow[ci] === ri ? 'best' : '') + '">' + col.get(c) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody>')) return;

    Array.prototype.forEach.call($('compare-table').querySelectorAll('button[data-del]'), function (btn) {
      btn.addEventListener('click', function () {
        saveJson(SAVED_STORE, loadJson(SAVED_STORE, []).filter(function (c) {
          return String(c.id) !== btn.dataset.del;
        }));
        renderCompare();
      });
    });
  }

  function exportCsv() {
    var list = loadJson(SAVED_STORE, []);
    if (!list.length) return;
    var cols = COMPARE_COLS.filter(function (c) { return c.csv; });
    var quote = function (v) { return '"' + String(v).replace(/"/g, '""') + '"'; };
    var lines = [cols.map(function (c) { return quote(c.head); }).join(',')];
    list.forEach(function (c) {
      lines.push(cols.map(function (col) { return quote(col.csv(c)); }).join(','));
    });
    var blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'car-comparison.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  /* --------------------------------------------------------- colophon */

  function renderColophon(ctx) {
    var m = DATA.meta;
    var liveBits = Object.keys(live.provenance || {});
    $('masthead-mode').textContent = liveBits.length
      ? 'Live · ' + liveBits.join(' · ') : 'Offline · built-in data';
    $('masthead-subject').textContent = ctx
      ? [ctx.input.year, ctx.input.brand, ctx.input.model].filter(Boolean).join(' ')
      : 'No vehicle entered';

    var energyNote = live.gasUsdPerGal
      ? 'Fuel price ' + fmtUSD2.format(live.gasUsdPerGal) + '/gal live from EIA.'
      : 'Fuel price ' + fmtUSD2.format(DATA.energy.gasUsdPerGal) + '/gal from the built-in average.';

    $('data-footnote').innerHTML =
      '<b>Sources.</b> Live and free, no key: NHTSA vPIC (VIN decode), NHTSA recalls, complaints and ' +
      'NCAP crash ratings, and FuelEconomy.gov (EPA) fuel economy. Optional keyed: EIA (fuel and ' +
      'electricity prices), FRED (used-car CPI), Marketcheck (live comparables). ' + esc(energyNote) +
      ' Built-in dataset built ' + esc(m.built) + ' — ' + esc(m.sources.brands) + '; ' +
      esc(m.sources.depCurves) + '; ' + esc(m.sources.aprByTier) + '. ' +
      '<b>Limits.</b> Estimates for decision support, not an appraisal. Depreciation and repair ' +
      'figures are class averages and a specific car can differ widely. Always verify price against ' +
      'live listings, get a real insurance quote, and have the car inspected. Not financial advice.';
  }

  /* ------------------------------------------------------- main flow */

  function run() {
    var form = readForm();
    var problems = validate(form);
    if (problems.length) {
      $('results').style.display = 'none';
      clearFatal();
      setStatus(problems.length + ' field' + (problems.length > 1 ? 's need' : ' needs') +
        ' attention before the report can be generated.');
      announce(problems[0].message);
      var firstBad = $(problems[0].id);
      if (firstBad && document.activeElement !== firstBad) scrollTo(firstBad.closest('label.f'), 'center');
      return;
    }

    var ctx;
    try {
      ctx = E.analyze(form, DATA, live);
    } catch (error) {
      showFatal(error);
      return;
    }
    clearFatal();
    lastCtx = ctx;
    $('results').style.display = 'block';
    $('btn-save').style.display = 'inline-block';
    try {
    renderBottomLine(ctx);
    renderStickyBar(ctx);
    renderScore(ctx);
    renderIdentity(ctx);
    renderTiles(ctx);
    renderPrice(ctx);
    renderSigning(ctx);
    renderTermComparison(ctx);
    renderLineChart(ctx);
    renderBarChart(ctx);
    renderCumulativeChart(ctx);
    renderComplaintChart(ctx);
    renderServices(ctx);
    renderSensitivity(ctx);
    renderFindings(ctx);
    renderCompare();
    renderColophon(ctx);
    updateHints(ctx);
    } catch (error) {
      showFatal(error);
      return;
    }
    announce('Report ready. Deal score ' + ctx.score.score + ' out of 100, ' +
      ctx.verdict.label + '. ' + (ctx.findings.length ? ctx.findings.length + ' findings.' : ''));
    if (!scrolledOnce) {
      scrollTo($('results'), 'start');
      scrolledOnce = true;
    }
  }

  function updateHints(ctx) {
    $('mpg-hint').textContent = $('in-mpg').value ? 'your figure'
      : (live.mpg ? live.mpg + ' MPG from EPA, live'
                  : Math.round(ctx.input.mpg || 0) + ' MPG from built-in data');
    var stateInfoNow = E.stateInfo(val('in-state'), DATA);
    $('gas-hint').textContent = $('in-gas').value ? 'your figure'
      : (live.gasUsdPerGal ? fmtUSD2.format(live.gasUsdPerGal) + '/gal from EIA, live'
        : stateInfoNow ? fmtUSD2.format(stateInfoNow.gas) + '/gal typical for ' + val('in-state')
        : fmtUSD2.format(DATA.energy.gasUsdPerGal) + '/gal built-in average');
    var insHint = $('in-insurance').closest('label.f').querySelector('.hint');
    if (insHint) {
      insHint.textContent = $('in-insurance').value ? 'your figure'
        : stateInfoNow ? usd(ctx.input.insurance) + '/yr — class average scaled for ' + val('in-state')
        : usd(ctx.input.insurance) + '/yr class average — get a real quote';
    }
    $('apr-hint').textContent = $('in-apr').value ? 'your figure'
      : DATA.aprByTier[val('in-credit')].toFixed(1) + '% tier average';
  }

  /* ----------------------------------------------------- live lookups */

  function setStatus(msg) { $('live-status').textContent = msg || ''; }

  function vehicleQuery() {
    return {
      make: val('in-brand'), model: val('in-model'), year: parseInt(val('in-year'), 10),
      stateId: val('in-state') || null, gasArea: val('in-state') ? 'S' + val('in-state') : null
    };
  }

  function refreshLive() {
    var q = vehicleQuery();
    if (!q.make || !q.model || !q.year) return Promise.resolve();
    setStatus('Checking NHTSA and EPA…');
    return sources.loadAll(q).then(function (result) {
      var decoded = live.decoded;
      live = result;
      if (decoded) live.decoded = decoded;
      // Re-apply any recall repairs the user already ticked.
      if (live.recalls) {
        live.recalls.forEach(function (r, i) {
          if (verifiedRecalls[r.campaign || i]) r.repairVerified = true;
        });
      }
      setStatus(Object.keys(live.provenance).length
        ? 'Live: ' + Object.keys(live.provenance).join(', ')
        : 'Live lookup unavailable — using built-in data');
    }).catch(function () { setStatus('Live lookup unavailable — using built-in data'); });
  }

  function decodeVin() {
    var vin = val('in-vin');
    if (!vin) {
      $('vin-result').innerHTML = '<div class="callout warn">Enter a VIN first.</div>';
      return;
    }
    $('btn-vin').disabled = true;
    setStatus('Decoding VIN…');
    sources.decodeVin(vin).then(function (r) {
      if (!r.ok) {
        $('vin-result').innerHTML = '<div class="callout warn">' + (
          r.reason === 'invalid-format'
            ? 'That is not a valid VIN — 17 characters, no I, O or Q. Fill the fields in by hand.'
            : r.reason === 'not-decoded'
              ? 'NHTSA could not decode that VIN. Fill the fields in by hand.'
              : 'Could not reach the NHTSA VIN service — you may be offline. Fill the fields in by ' +
                'hand; every other part of the report still works.'
        ) + '</div>';
        $('btn-vin').disabled = false;
        setStatus('');
        return;
      }
      live.decoded = r;
      if (r.year) $('in-year').value = r.year;
      if (r.make) {
        var match = Object.keys(DATA.brands).filter(function (b) {
          return b.toLowerCase() === String(r.make).toLowerCase();
        })[0];
        $('in-brand').value = match || 'Other';
        refreshModelList();
      }
      if (r.model) $('in-model').value = r.model;
      if (r.segment && DATA.segments[r.segment]) $('in-segment').value = r.segment;
      $('vin-result').innerHTML = '<div class="callout">Decoded ' +
        esc([r.year, r.make, r.model, r.trim].filter(Boolean).join(' ')) +
        '. Pulling recalls and ratings…</div>';
      return refreshLive().then(function () {
        $('vin-result').innerHTML = '';
        run();
      });
    }).catch(function () { setStatus(''); })
      .then(function () { $('btn-vin').disabled = false; });
  }

  function autofillComps() {
    var q = vehicleQuery();
    q.miles = numOrNull('in-miles') || 0;
    setStatus('Fetching comparables…');
    sources.marketComps(q).then(function (r) {
      if (!r) { setStatus('No comparables returned'); return; }
      $('in-comp1').value = Math.round(r.p25);
      $('in-comp2').value = Math.round(r.median);
      $('in-comp3').value = Math.round(r.p75);
      setStatus('Filled from ' + r.n + ' live listings');
      run();
    });
  }

  /* ------------------------------------- persistence, sharing, state defaults */

  var FORM_IDS = ['in-vin', 'in-name', 'in-year', 'in-brand', 'in-model', 'in-segment',
    'in-miles', 'in-price', 'in-mpg', 'in-condition', 'in-title', 'in-accidents', 'in-owners',
    'in-records', 'in-comp1', 'in-comp2', 'in-comp3', 'in-ppi', 'in-paytype', 'in-down',
    'in-credit', 'in-apr', 'in-term', 'in-target-payment', 'in-tax', 'in-fees',
    'in-annualmiles', 'in-horizon', 'in-state', 'in-gas', 'in-insurance', 'in-income',
    'in-trade-value', 'in-trade-payoff'];
  var CHECK_IDS = ['in-recalls-verified', 'in-comp-bias'];
  /** Changing any of these changes how every listing would be owned, so the market re-scores. */
  var MARKET_INPUTS = ['in-state', 'in-paytype', 'in-down', 'in-credit', 'in-apr', 'in-term',
    'in-tax', 'in-fees', 'in-annualmiles', 'in-horizon', 'in-gas', 'in-insurance',
    'in-income', 'in-comp-bias'];

  function collectForm() {
    var out = {};
    FORM_IDS.forEach(function (id) { if ($(id)) out[id] = $(id).value; });
    CHECK_IDS.forEach(function (id) { if ($(id)) out[id] = $(id).checked ? 1 : 0; });
    return out;
  }

  function applyForm(values) {
    if (!values) return false;
    var applied = false;
    Object.keys(values).forEach(function (id) {
      var el = $(id);
      if (!el) return;
      if (CHECK_IDS.indexOf(id) !== -1) el.checked = !!values[id];
      else el.value = values[id];
      applied = true;
    });
    return applied;
  }

  function persistForm() { saveJson(INPUT_STORE, collectForm()); }

  /** Encode the whole form into the URL so a report can be sent to somebody else. */
  function shareLink() {
    var packed = btoa(unescape(encodeURIComponent(JSON.stringify(collectForm()))));
    var url = location.origin + location.pathname + '#c=' + packed;
    var done = function (ok) {
      $('keys-status').textContent = '';
      setStatus(ok ? 'Link copied — it carries every value on this page.'
                   : 'Copy failed; the link is in the address bar.');
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
    history.replaceState(null, '', '#c=' + packed);
  }

  function restoreFromHash() {
    var match = /[#&]c=([^&]+)/.exec(location.hash);
    if (!match) return false;
    try {
      return applyForm(JSON.parse(decodeURIComponent(escape(atob(match[1])))));
    } catch (e) {
      return false;
    }
  }

  /** Selecting a state fills in its tax rate and registration as editable defaults. */
  function applyStateDefaults(force) {
    var code = val('in-state');
    var info = E.stateInfo(code, DATA);
    if (!info) {
      $('state-note').textContent = '';
      return;
    }
    if (force || !$('in-tax').dataset.touched) {
      $('in-tax').value = (info.tax * 100).toFixed(2);
    }
    var insPct = Math.round((info.ins - 1) * 100);
    var rows = [
      ['Sales tax', (info.tax * 100).toFixed(2) + '%',
        info.tradeInCredit ? 'charged on the price after your trade-in'
                           : 'charged on the full price — no trade-in credit here'],
      ['Title at purchase', usd(info.titleFee), 'one-off, added to your out-the-door price'],
      ['Registration', usd(info.reg) + '/yr', 'renewal'],
      ['Insurance', (info.ins).toFixed(2) + '×',
        insPct === 0 ? 'about the national average'
          : (insPct > 0 ? insPct + '% above' : Math.abs(insPct) + '% below') + ' the national average'],
      ['Fuel', fmtUSD2.format(info.gas) + '/gal', 'typical pump price'],
      ['Electricity', fmtUSD2.format(info.elec) + '/kWh', 'residential rate, used for EVs'],
      ['Inspection', info.inspect.cadence === 'none' ? 'none required'
        : usd(info.inspect.cost) + ' ' + info.inspect.cadence,
        info.inspect.cadence === 'none' ? 'no periodic safety or emissions test'
          : 'safety or emissions test'],
      ['EV surcharge', info.evFee > 0 ? usd(info.evFee) + '/yr' : 'none',
        'extra registration charge on electric vehicles'],
      ['Vehicle property tax', info.propertyTaxRate > 0
        ? (info.propertyTaxRate * 100).toFixed(1) + '% of value/yr' : 'none',
        info.propertyTaxRate > 0 ? 'levied annually on what the car is worth' : '']
    ];
    setHTML($('state-note'),
      '<h3 style="margin-top:0">' + esc(code) + ' cost profile</h3>' +
      '<table class="ledger"><tbody>' + rows.map(function (r) {
        return '<tr><td>' + esc(r[0]) + '</td><td class="n">' + esc(r[1]) +
          '</td><td class="src">' + esc(r[2]) + '</td></tr>';
      }).join('') + '</tbody></table>' +
      '<p class="block-note" style="margin:10px 0 0">' +
      'These flow into the totals automatically. Anything you type into the fields above wins. ' +
      '<span class="src">' + esc(DATA.states.verifyNote) + '</span></p>');
  }


  /* ==================================================================== market
   * Ranking a whole cohort of scraped listings through the same engine.
   * Scraping happens outside the browser (scrape_listings.py / AGENTS.md); this only
   * reasons about listings already loaded.
   * ================================================================= */

  var M = window.UCDA_MARKET;
  var LISTINGS_STORE = 'ucda_listings_v1';

  /** Where this copy of the tool is set up to shop. City-level only — no address. */
  var DEFAULT_STATE = 'UT';
  var SEARCH_AREA = {
    town: 'Pleasant Grove', county: 'Utah County', zip: '84062',
    radiusMiles: 40,
    nearby: 'Lehi, American Fork, Orem, Provo, Spanish Fork and the south end of Salt Lake County'
  };
  var market = { listings: [], rows: [], view: [], meta: null, sort: 'score', limit: 40 };

  function marketAssumptions() {
    var form = readForm();
    // The buyer's own circumstances, not the individual car's.
    return {
      currentYear: form.currentYear, state: form.state,
      payType: form.payType, down: form.down, creditTier: form.creditTier, apr: form.apr,
      termMonths: form.termMonths, taxRate: form.taxRate, fees: form.fees,
      annualMiles: form.annualMiles, horizon: form.horizon,
      gasUsdPerGal: form.gasUsdPerGal, insurance: form.insurance, income: form.income,
      ppi: 'planned', records: 'yes', applyCompBias: form.applyCompBias
    };
  }

  function readFilters() {
    return {
      query: val('market-search'),
      maxPrice: numOrNull('mf-maxprice'), maxMiles: numOrNull('mf-maxmiles'),
      minYear: numOrNull('mf-minyear'), minScore: numOrNull('mf-minscore'),
      maxDistance: numOrNull('mf-maxdist'),
      seller: val('mf-seller') || null,
      cleanOnly: $('mf-clean').checked,
      noCriticals: $('mf-nocrit').checked
    };
  }

  function scoreMarket() {
    if (!market.listings.length) return;
    market.rows = M.scoreAll(market.listings, marketAssumptions(), E, DATA, {});
    renderMarket();
  }

  function renderMarket() {
    $('market-empty').style.display = market.listings.length ? 'none' : 'block';
    $('market-live').style.display = market.listings.length ? 'block' : 'none';
    if (!market.listings.length) return;

    market.view = M.sortRows(M.filterRows(market.rows, readFilters()), market.sort);
    renderMarketStats();
    renderMarketPicks();
    renderMarketChart();
    renderMarketTable();
  }

  function renderMarketStats() {
    var all = M.marketStats(market.rows);
    var shown = M.marketStats(market.view);
    var stats = [
      ['Listings', fmtNum.format(market.view.length) +
        (market.view.length !== market.rows.length ? ' of ' + fmtNum.format(market.rows.length) : '')],
      ['Median price', shown.medianPrice === null ? '—' : usd(shown.medianPrice)],
      ['Median miles', shown.medianMiles === null ? '—' : fmtNum.format(Math.round(shown.medianMiles))],
      ['Median score', shown.medianScore === null ? '—' : Math.round(shown.medianScore)],
      ['Depreciation', all.usdPerThousandMiles
        ? usd(all.usdPerThousandMiles) + ' / 1k mi' : '—'],
      ['Private sellers', Math.round(all.privateShare * 100) + '%'],
      ['Branded titles', fmtNum.format(all.branded)]
    ];
    setHTML($('market-stats'), stats.map(function (s) {
      return '<div class="stat"><span class="cap">' + esc(s[0]) + '</span><span class="v">' +
        esc(s[1]) + '</span></div>';
    }).join(''));
  }

  function pickLine(r) {
    var l = r.listing;
    return '<span class="m">' + esc(l.year) + ' ' + esc(l.make) + ' ' + esc(l.model) +
      (l.trim ? ' ' + esc(l.trim) : '') + '</span> — <span class="m">' + usd(l.price) + '</span>, ' +
      fmtNum.format(l.miles) + ' mi, score <b class="m">' + r.score + '</b>' +
      (r.saving > 0 ? ' · <span class="m">' + usd(r.saving) + '</span> under fair value' : '') +
      '<br><button class="tiny ghost" data-open="' + esc(l.id) + '">Open full report</button>';
  }

  function renderMarketPicks() {
    var picks = M.shortlist(market.view, 3);
    var groups = [
      ['Best overall', picks.best, 'highest deal score on your terms'],
      ['Biggest bargains', picks.bargains, 'furthest below their fair value'],
      ['Cheapest to own', picks.cheapestToOwn, 'lowest all-in monthly cost']
    ];
    setHTML($('market-picks'), groups.map(function (g) {
      return '<div class="pick"><h4>' + esc(g[0]) + '</h4>' +
        (g[1].length
          ? '<ol>' + g[1].map(function (r) { return '<li>' + pickLine(r) + '</li>'; }).join('') + '</ol>'
          : '<p class="block-note" style="margin:0">Nothing matches the current filters.</p>') +
        '</div>';
    }).join(''));
  }

  function renderMarketTable() {
    var rows = market.view.slice(0, market.limit);
    var cols = [
      ['#', null], ['Vehicle', null], ['Year', 'year'], ['Miles', 'miles'], ['Price', 'price'],
      ['Fair value', null], ['Vs fair', 'saving'], ['Score', 'score'],
      ['All-in/mo', 'monthly'], ['$/mi', 'cpm'], ['Away', 'distance'], ['', null]
    ];
    $('market-table-head').textContent = 'Ranked listings — ' +
      fmtNum.format(market.view.length) + ' match';

    setHTML($('market-table'),
      '<thead><tr>' + cols.map(function (c) {
        return '<th' + (c[1] ? ' data-sort="' + c[1] + '" tabindex="0" role="button" aria-sort="' +
          (market.sort === c[1] ? 'descending' : 'none') + '"' : '') + '>' + esc(c[0]) + '</th>';
      }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r, i) {
        var l = r.listing;
        var flags = '';
        if (l.title !== 'clean') flags += '<span class="badge bad">' + esc(l.title) + '</span>';
        if (r.criticals) flags += '<span class="badge bad">' + r.criticals + ' critical</span>';
        if (l.seller === 'private') flags += '<span class="badge">private</span>';
        if (l.daysOnMarket >= 60) flags += '<span class="badge warn">' + l.daysOnMarket + 'd listed</span>';
        if (l.priceDrop) flags += '<span class="badge good">−' + usd(l.priceDrop) + '</span>';
        if (l.duplicates && l.duplicates.length) {
          flags += '<span class="badge warn">also listed higher</span>';
        }
        return '<tr><td class="rank">' + (i + 1) + '</td>' +
          '<td class="veh">' + esc([l.make, l.model, l.trim].filter(Boolean).join(' ')) + flags +
          '<span class="sub">' + esc([l.dealer || 'Private seller', l.city, l.state]
            .filter(Boolean).join(' · ')) +
          (l.url ? ' · <a href="' + esc(l.url) + '" target="_blank" rel="noopener noreferrer">listing</a>' : '') +
          '</span></td>' +
          '<td>' + esc(l.year) + '</td>' +
          '<td>' + fmtNum.format(l.miles) + '</td>' +
          '<td>' + usd(l.price) + '</td>' +
          '<td>' + (r.comps.n ? usd(r.fair) : '<span class="src">thin data</span>') + '</td>' +
          '<td style="color:var(' + (r.saving > 0 ? '--ok' : '--ink-2') + ')">' +
            (r.saving > 0 ? '−' + usd(r.saving) : '+' + usd(-r.saving)) + '</td>' +
          '<td><b>' + r.score + '</b></td>' +
          '<td>' + usd(r.monthly) + '</td>' +
          '<td>' + fmtUSD2.format(r.cpm) + '</td>' +
          '<td>' + (l.distance === null ? '—' : Math.round(l.distance) + ' mi') + '</td>' +
          '<td><button class="tiny ghost" data-open="' + esc(l.id) + '">Open</button></td></tr>';
      }).join('') + '</tbody>');

    $('btn-market-more').style.display = market.view.length > market.limit ? 'inline-block' : 'none';
    $('btn-market-more').textContent = 'Show more (' +
      fmtNum.format(market.view.length - market.limit) + ' left)';
  }

  /** Price against mileage for the whole cohort, with the market's own trend line. */
  function renderMarketChart() {
    var rows = market.view;
    if (rows.length < 2) { setHTML($('market-chart'), ''); return; }
    var narrow = narrowScreen();
    var W = chartW(), H = narrow ? 230 : 300, padL = narrow ? 58 : 74, padR = 18,
        padT = 14, padB = 34;
    var miles = rows.map(function (r) { return r.listing.miles; });
    var prices = rows.map(function (r) { return r.listing.price; });
    var maxX = Math.max.apply(null, miles) * 1.05 || 1;
    var maxY = Math.max.apply(null, prices) * 1.08 || 1;
    var x = function (v) { return padL + (W - padL - padR) * (v / maxX); };
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / maxY); };

    var svg = '';
    var stepY = Math.max(Math.ceil(maxY / 4 / 1000) * 1000, 1000);
    for (var g = 0; g <= maxY; g += stepY) {
      svg += '<line x1="' + padL + '" y1="' + y(g) + '" x2="' + (W - padR) + '" y2="' + y(g) +
        '" stroke="' + css('--rule') + '" stroke-width="1"/><text x="' + (padL - 9) + '" y="' +
        (y(g) + 4) + '" text-anchor="end" font-size="10" fill="' + css('--ink-3') + '">' +
        usd(g) + '</text>';
    }
    var stepX = Math.max(Math.ceil(maxX / 5 / 10000) * 10000, 10000);
    for (var gx = 0; gx <= maxX; gx += stepX) {
      svg += '<text x="' + x(gx) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="10" fill="' +
        css('--ink-3') + '">' + Math.round(gx / 1000) + 'k</text>';
    }

    var stats = M.marketStats(rows);
    if (stats.slope) {
      var x1 = 0, x2 = maxX;
      var y1 = clamp(stats.intercept + stats.slope * x1, 0, maxY);
      var y2 = clamp(stats.intercept + stats.slope * x2, 0, maxY);
      svg += '<line x1="' + x(x1) + '" y1="' + y(y1) + '" x2="' + x(x2) + '" y2="' + y(y2) +
        '" stroke="' + css('--s2') + '" stroke-width="2" stroke-linecap="round"/>';
    }

    // Dots below the trend line are cars priced under the market for their mileage.
    rows.forEach(function (r, i) {
      var under = stats.slope
        ? r.listing.price < stats.intercept + stats.slope * r.listing.miles : false;
      svg += '<circle class="mk-dot" data-i="' + i + '" cx="' + x(r.listing.miles).toFixed(1) +
        '" cy="' + y(r.listing.price).toFixed(1) + '" r="' + (r.score >= 75 ? 5.5 : 4) +
        '" fill="' + (under ? css('--s1') : 'none') + '" stroke="' + css('--s1') +
        '" stroke-width="1.5" opacity="' + (under ? 0.9 : 0.55) + '"/>';
    });

    setHTML($('market-chart'), '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" ' +
      'aria-label="Asking price against mileage for every matching listing, with the market trend line" ' +
      'style="min-width:' + (narrow ? 0 : 560) + 'px">' + svg + '</svg>');

    var tip = $('market-tip');
    var rig = tip.closest('.chart-rig');
    Array.prototype.forEach.call($('market-chart').querySelectorAll('.mk-dot'), function (dot) {
      dot.addEventListener('mousemove', function (e) {
        var r = rows[parseInt(dot.dataset.i, 10)];
        var l = r.listing;
        tip.style.display = 'block';
        tip.innerHTML = '<div class="h">' + esc([l.year, l.make, l.model].join(' ')) + '</div>' +
          '<div class="r"><span>Price</span><b>' + usd(l.price) + '</b></div>' +
          '<div class="r"><span>Miles</span><b>' + fmtNum.format(l.miles) + '</b></div>' +
          '<div class="r"><span>Score</span><b>' + r.score + '</b></div>' +
          '<div class="r"><span>Vs fair</span><b>' + (r.saving > 0 ? '−' : '+') +
          usd(Math.abs(r.saving)) + '</b></div>';
        var box = rig.getBoundingClientRect();
        tip.style.left = clamp(e.clientX - box.left + 14, 0, Math.max(box.width - 200, 0)) + 'px';
        tip.style.top = (e.clientY - box.top + 14) + 'px';
      });
      dot.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    });
  }

  /** Push a listing into the single-car form and run the full report on it. */
  function openListing(id) {
    var row = market.rows.filter(function (r) { return r.listing.id === id; })[0];
    if (!row) return;
    var l = row.listing;
    $('in-name').value = [l.year, l.make, l.model, l.trim].filter(Boolean).join(' ') +
      (l.dealer ? ' — ' + l.dealer : '');
    $('in-year').value = l.year;
    var make = Object.keys(DATA.brands).filter(function (b) {
      return b.toLowerCase() === String(l.make).toLowerCase();
    })[0];
    $('in-brand').value = make || 'Other';
    refreshModelList();
    $('in-model').value = l.model;
    if (l.segment && DATA.segments[l.segment]) $('in-segment').value = l.segment;
    $('in-miles').value = l.miles;
    $('in-price').value = l.price;
    $('in-title').value = l.title;
    $('in-accidents').value = l.accidents;
    $('in-owners').value = l.owners || 1;
    if (l.vin) $('in-vin').value = l.vin;
    // The cohort's own comparables become the three comp fields.
    var comps = row.comps.comps.slice().sort(function (a, b) { return a - b; });
    var pick = comps.length
      ? [comps[Math.floor(comps.length * 0.25)], comps[Math.floor(comps.length * 0.5)],
         comps[Math.floor(comps.length * 0.75)]]
      : [];
    ['in-comp1', 'in-comp2', 'in-comp3'].forEach(function (id2, i) {
      $(id2).value = pick[i] ? Math.round(pick[i]) : '';
    });
    persistForm();
    setStatus('Loaded ' + $('in-name').value + ' from the market, with ' + row.comps.n +
      ' cohort comparables.');
    announce('Loaded listing into the report.');
    refreshLive().then(run);
  }

  /* ---------------------------------------------------------- market io */

  var SCHEMA_FIELDS = [
    ['year', true, 'model year, e.g. 2019'],
    ['make', true, 'Toyota, Honda, Ford…'],
    ['model', true, 'Camry, Accord, F-150…'],
    ['miles', true, 'odometer reading'],
    ['price', true, 'asking price'],
    ['url', false, 'link to the listing — so you can open it'],
    ['trim', false, 'SE, EX-L, Limited…'],
    ['vin', false, '17 characters; omit rather than guess'],
    ['title', false, 'clean, rebuilt/salvage, or lemon'],
    ['accidents', false, 'none, minor, major, or unknown when no report was shown'],
    ['owners', false, 'number of previous owners'],
    ['seller', false, 'dealer or private'],
    ['dealer', false, 'name of the seller'],
    ['city', false, 'and state — for distance and local pricing'],
    ['state', false, 'two-letter code'],
    ['distance', false, 'miles from you, if the site shows it'],
    ['daysOnMarket', false, 'a long-listed car is a negotiable car'],
    ['priceDrop', false, 'how much it has already come down'],
    ['mpg', false, 'combined, if stated'],
    ['notes', false, 'anything else worth remembering']
  ];

  function renderSchemaHelp() {
    setHTML($('market-schema'),
      '<table class="ledger"><thead><tr><th>Field</th><th>Needed</th><th>What it is</th></tr></thead>' +
      '<tbody>' + SCHEMA_FIELDS.map(function (f) {
        return '<tr><td><code>' + esc(f[0]) + '</code></td><td>' +
          (f[1] ? '<span class="req">required</span>' : '<span class="src">optional</span>') +
          '</td><td class="src">' + esc(f[2]) + '</td></tr>';
      }).join('') + '</tbody></table>' +
      '<p class="block-note" style="margin:10px 0 0">Field names are matched loosely — ' +
      '<code>mileage</code>, <code>odometer</code>, <code>listPrice</code>, <code>link</code> and ' +
      'similar all work. Anything missing is simply left unknown rather than guessed.</p>');
  }

  /** A ready-made brief to hand an assistant, seeded with what is already on the page. */
  function assistantPrompt() {
    var wants = [];
    if (val('in-brand') && val('in-model')) wants.push(val('in-brand') + ' ' + val('in-model'));
    var maxPrice = numOrNull('in-price');
    var state = val('in-state');
    var where = state === DEFAULT_STATE
      ? 'Near: ' + SEARCH_AREA.town + ', ' + SEARCH_AREA.county + ', ' + DEFAULT_STATE +
        ' (' + SEARCH_AREA.zip + ') — anywhere within about ' + SEARCH_AREA.radiusMiles +
        ' miles is fine, which covers ' + SEARCH_AREA.nearby + '.'
      : state ? 'Near: ' + state + ' — <add your ZIP and how far you will travel>.'
              : 'Near: <your ZIP and how far you will travel>.';
    return [
      'Find me used cars and return them as JSON I can paste into my deal analyzer.',
      '',
      wants.length ? 'Looking for: ' + wants.join(', ') + ' (adjust or widen as you see fit).'
                   : 'Looking for: <say what you want here>.',
      maxPrice ? 'Budget: around $' + fmtNum.format(maxPrice) + '.' : 'Budget: <your budget>.',
      where,
      state === DEFAULT_STATE
        ? 'Worth checking locally: KSL Cars (Utah\'s big classifieds site, lots of private ' +
          'sellers), plus the national aggregators and the dealer groups along the I-15 ' +
          'corridor between Lehi and Provo.'
        : '',
      '',
      'Search widely — aim for 150 or more listings across several sites, dealers and private',
      'sellers. The more you find, the better the valuation, because the tool builds each car\'s',
      'comparables from the others.',
      '',
      'Return ONLY a JSON array. One object per car, with these fields:',
      '',
      '  required: year, make, model, miles, price',
      '  strongly wanted: url (link to the listing), trim, vin, title, accidents, seller,',
      '                   dealer, city, state, distance, daysOnMarket',
      '',
      'Rules:',
      '- title is "clean", "rebuilt" (salvage/reconstructed) or "lemon".',
      '- accidents is "none", "minor", "major", or "unknown" when no history report was shown.',
      '  Use "unknown" rather than "none" if you did not actually see a report.',
      '- Never invent a value. If you did not see it, leave the field out.',
      '- Include the listing url for every car.',
      '',
      'Example of one entry:',
      '{"url":"https://example.com/listing/123","year":2019,"make":"Toyota","model":"Camry",',
      ' "trim":"SE","miles":52341,"price":18995,"title":"clean","accidents":"unknown",',
      ' "seller":"dealer","dealer":"Example Motors","city":"Vienna","state":"VA","distance":12}'
    ].join('\n');
  }

  function copyPrompt() {
    var promptText = assistantPrompt();
    var done = function (ok) {
      $('market-status').textContent = ok
        ? 'Prompt copied — paste it to your assistant, then paste its answer back here.'
        : 'Could not reach the clipboard; the prompt is in the paste box instead.';
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(promptText).then(function () { done(true); }, function () {
        $('market-paste').value = promptText;
        done(false);
      });
    } else {
      $('market-paste').value = promptText;
      done(false);
    }
  }

  /**
   * Take listings from any accepted shape. `append` keeps what is already loaded, so several
   * searches accumulate into one market the way the CLI's --merge does.
   */
  function loadMarketText(input, label, append) {
    var parsed = typeof input === 'string' ? M.parseText(input) : { ok: true, data: input, form: 'file' };
    if (!parsed.ok) {
      $('market-status').textContent = parsed.error;
      announce(parsed.error);
      return false;
    }
    var result = M.ingest(parsed.data);
    if (!result.ok) {
      $('market-status').textContent = result.error;
      announce(result.error);
      return false;
    }
    if (!result.listings.length) {
      $('market-status').textContent = 'Read ' + parsed.form + ', but no listing had all of ' +
        'year, make, model, miles and price.';
      return false;
    }

    var before = append ? market.listings.length : 0;
    var combined = append ? market.listings.concat(result.listings) : result.listings;
    var merged = M.dedupe(combined);
    market.listings = merged;
    market.meta = result.meta || market.meta;
    market.limit = 40;
    saveJson(LISTINGS_STORE, { listings: merged, meta: market.meta });

    var parts = [fmtNum.format(merged.length) + ' listings loaded'];
    if (append) parts.push(fmtNum.format(merged.length - before) + ' new');
    if (parsed.form && parsed.form !== 'file') parts.push('read as ' + parsed.form);
    var dropped = (combined.length - merged.length);
    if (dropped) parts.push(dropped + ' duplicate' + (dropped > 1 ? 's' : '') + ' merged');
    if (result.rejected) parts.push(result.rejected + ' incomplete skipped');
    var withUrl = merged.filter(function (l) { return l.url; }).length;
    if (withUrl < merged.length) parts.push((merged.length - withUrl) + ' without a link');
    $('market-status').textContent = parts.join(' · ') + (label ? ' · from ' + label : '');
    announce(fmtNum.format(merged.length) + ' listings loaded and ranked.');

    scoreMarket();
    return true;
  }

  function exportMarketCsv() {
    if (!market.view.length) return;
    var cols = ['rank', 'score', 'year', 'make', 'model', 'trim', 'miles', 'price',
      'fairValue', 'vsFair', 'monthlyAllIn', 'costPerMile', 'seller', 'dealer', 'city',
      'state', 'distance', 'daysOnMarket', 'title', 'accidents', 'vin', 'url'];
    var quote = function (v) { return '"' + String(v === null || v === undefined ? '' : v).replace(/"/g, '""') + '"'; };
    var lines = [cols.map(quote).join(',')];
    market.view.forEach(function (r, i) {
      var l = r.listing;
      lines.push([i + 1, r.score, l.year, l.make, l.model, l.trim, l.miles, l.price,
        Math.round(r.fair), Math.round(-r.saving), Math.round(r.monthly), r.cpm.toFixed(3),
        l.seller, l.dealer, l.city, l.state, l.distance, l.daysOnMarket, l.title,
        l.accidents, l.vin, l.url].map(quote).join(','));
    });
    var blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'market-shortlist.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function initMarket() {
    if (!M) return;

    // Delegated so the handlers survive every re-render of the table and picks.
    $('market-block').addEventListener('click', function (event) {
      var openBtn = event.target.closest && event.target.closest('[data-open]');
      if (openBtn) { openListing(openBtn.dataset.open); return; }
      var th = event.target.closest && event.target.closest('th[data-sort]');
      if (th) { market.sort = th.dataset.sort; renderMarket(); }
    });
    $('market-block').addEventListener('keydown', function (event) {
      var th = event.target.closest && event.target.closest('th[data-sort]');
      if (th && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        market.sort = th.dataset.sort;
        renderMarket();
      }
    });

    ['market-search', 'mf-maxprice', 'mf-maxmiles', 'mf-minyear', 'mf-minscore',
     'mf-maxdist', 'mf-seller', 'mf-clean', 'mf-nocrit'].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener('input', function () { market.limit = 40; renderMarket(); });
      el.addEventListener('change', function () { market.limit = 40; renderMarket(); });
    });

    $('btn-market-reset-filters').addEventListener('click', function () {
      ['market-search', 'mf-maxprice', 'mf-maxmiles', 'mf-minyear', 'mf-minscore', 'mf-maxdist']
        .forEach(function (id) { $(id).value = ''; });
      $('mf-seller').value = '';
      $('mf-clean').checked = true;
      $('mf-nocrit').checked = false;
      market.limit = 40;
      renderMarket();
    });
    $('btn-market-more').addEventListener('click', function () {
      market.limit += 60;
      renderMarketTable();
    });
    $('btn-market-export').addEventListener('click', exportMarketCsv);

    $('btn-market-prompt').addEventListener('click', copyPrompt);
    $('btn-market-schema').addEventListener('click', function () {
      var box = $('market-schema');
      var open = box.style.display === 'none';
      if (open) renderSchemaHelp();
      box.style.display = open ? 'block' : 'none';
      $('btn-market-schema').textContent = open ? 'Hide the fields' : 'Show the fields';
    });

    $('btn-market-file').addEventListener('click', function () { $('market-file').click(); });
    $('market-file').addEventListener('change', function (event) {
      var file = event.target.files && event.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () { loadMarketText(String(reader.result), file.name, false); };
      reader.readAsText(file);
      event.target.value = '';           // let the same file be picked again
    });

    $('btn-market-load-paste').addEventListener('click', function () {
      if (loadMarketText($('market-paste').value, 'the paste box', false)) {
        $('market-paste').value = '';
      }
    });
    $('btn-market-append').addEventListener('click', function () {
      if (loadMarketText($('market-paste').value, 'the paste box', true)) {
        $('market-paste').value = '';
      }
    });
    $('btn-market-clear').addEventListener('click', function () {
      market.listings = []; market.rows = []; market.view = [];
      saveJson(LISTINGS_STORE, null);
      $('market-status').textContent = 'Cleared.';
      renderMarket();
    });

    // listings.js is optional — generated by scrape_listings.py, never shipped.
    var generated = window.UCDA_LISTINGS;
    var stored = loadJson(LISTINGS_STORE, null);
    if (generated) {
      loadMarketText(generated, 'listings.js' +
        (generated.generated ? ' generated ' + String(generated.generated).slice(0, 10) : ''), false);
    } else if (stored && stored.listings && stored.listings.length) {
      loadMarketText(stored.listings, 'this browser', false);
    } else {
      renderMarket();
    }
  }

  /* ------------------------------------------------------------- init */

  function refreshModelList() {
    var make = val('in-brand');
    var models = DATA.vehicles[make] ? Object.keys(DATA.vehicles[make]) : [];
    $('model-list').innerHTML = models.map(function (m) { return '<option value="' + esc(m) + '">'; }).join('');
  }

  function init() {
    $('report-date').textContent = new Date().toLocaleDateString('en-US',
      { year: 'numeric', month: 'short', day: '2-digit' }).toUpperCase();

    $('in-brand').innerHTML = Object.keys(DATA.brands).map(function (b) {
      return '<option' + (b === 'Toyota' ? ' selected' : '') + '>' + esc(b) + '</option>';
    }).join('');
    $('in-segment').innerHTML = Object.keys(DATA.segments).map(function (s) {
      return '<option' + (s === 'Midsize car' ? ' selected' : '') + '>' + esc(s) + '</option>';
    }).join('');
    $('in-state').innerHTML = US_STATES.map(function (s) {
      return '<option value="' + s + '"' + (s === DEFAULT_STATE ? ' selected' : '') + '>' +
        (s || '—') + '</option>';
    }).join('');
    $('in-model').value = 'Camry';
    refreshModelList();

    var keys = currentKeys();
    ['eia', 'fred', 'marketcheck'].forEach(function (k) { if (keys[k]) $('key-' + k).value = keys[k]; });
    rebuildSources();

    $('btn-analyze').addEventListener('click', function () {
      var button = $('btn-analyze');
      button.disabled = true;
      button.textContent = 'Working…';
      button.setAttribute('aria-busy', 'true');
      refreshLive().then(run).catch(showFatal).then(function () {
        button.disabled = false;
        button.textContent = 'Generate report';
        button.removeAttribute('aria-busy');
      });
    });
    $('btn-vin').addEventListener('click', decodeVin);
    $('btn-comps').addEventListener('click', autofillComps);
    $('in-brand').addEventListener('change', refreshModelList);
    $('btn-print').addEventListener('click', function () { window.print(); });

    // Delegated so it survives re-renders of the term table.
    $('term-body').addEventListener('click', function (event) {
      var row = event.target.closest && event.target.closest('tr[data-term]');
      if (!row) return;
      $('in-term').value = row.dataset.term;
      run();
    });

    Array.prototype.forEach.call(document.querySelectorAll('input, select'), function (el) {
      el.addEventListener('change', function () {
        if (el.id === 'in-tax') el.dataset.touched = '1';
        if (FORM_IDS.indexOf(el.id) !== -1 || CHECK_IDS.indexOf(el.id) !== -1) persistForm();
        if (lastCtx) run();
        // The ranking depends on how *this* buyer would own the car.
        if (MARKET_INPUTS.indexOf(el.id) !== -1) scoreMarket();
      });
    });
    $('in-state').addEventListener('change', function () {
      applyStateDefaults(true);
      persistForm();
      if (lastCtx) run();
    });

    $('sb-edit').addEventListener('click', function () {
      scrollTo($('in-price').closest('section.block'), 'start');
      $('in-price').focus();
    });
    $('btn-share').addEventListener('click', shareLink);
    $('btn-reset').addEventListener('click', function () {
      saveJson(INPUT_STORE, null);
      history.replaceState(null, '', location.pathname);
      location.reload();
    });

    // Charts are drawn at a different geometry on narrow screens; redraw on resize.
    var resizeTimer = null, wasNarrow = narrowScreen();
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (narrowScreen() !== wasNarrow) {
          wasNarrow = narrowScreen();
          if (lastCtx) run();
        }
      }, 200);
    });

    $('btn-save').addEventListener('click', function () {
      if (!lastCtx) return;
      var c = lastCtx;
      var list = loadJson(SAVED_STORE, []);
      list.push({
        id: Date.now(), name: c.input.name, score: c.score.score, asking: c.input.asking,
        fair: c.fair.value, otd: c.otd, monthly: c.loan.payment, tco: c.tco.total,
        cpm: c.tco.costPerMile, lifeLeft: c.remainingMiles, horizon: c.input.horizon,
        verdict: c.verdict.label
      });
      saveJson(SAVED_STORE, list);
      renderCompare();
      scrollTo($('compare-card'), 'nearest');
    });
    $('btn-clear-compare').addEventListener('click', function () { saveJson(SAVED_STORE, []); renderCompare(); });
    $('btn-export').addEventListener('click', exportCsv);

    $('btn-keys').addEventListener('click', function () {
      var b = $('keys-block');
      b.style.display = b.style.display === 'none' ? 'block' : 'none';
    });
    $('btn-save-keys').addEventListener('click', function () {
      saveJson(KEYS_STORE, {
        eia: val('key-eia') || undefined,
        fred: val('key-fred') || undefined,
        marketcheck: val('key-marketcheck') || undefined
      });
      rebuildSources();
      $('keys-status').textContent = 'Saved to this browser';
    });
    $('btn-clear-keys').addEventListener('click', function () {
      saveJson(KEYS_STORE, {});
      ['eia', 'fred', 'marketcheck'].forEach(function (k) { $('key-' + k).value = ''; });
      rebuildSources();
      $('keys-status').textContent = 'Cleared';
    });

    $('theme-toggle').addEventListener('click', function () {
      var root = document.documentElement;
      var dark = root.dataset.theme === 'dark' ||
        (!root.dataset.theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
      root.dataset.theme = dark ? 'light' : 'dark';
      if (lastCtx) run();      // charts re-render against the mode's validated palette
      if (market.rows.length) renderMarket();
    });

    // A shared link wins over whatever this browser had saved.
    var restored = restoreFromHash() || applyForm(loadJson(INPUT_STORE, null));
    if (restored) {
      refreshModelList();
      if ($('in-tax').value) $('in-tax').dataset.touched = '1';
    }
    applyStateDefaults(false);

    initMarket();
    renderCompare();
    renderColophon(null);
    if (restored) refreshLive().then(run);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
