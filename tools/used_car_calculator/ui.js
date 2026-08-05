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
      income: numOrNull('in-income')
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

    var W = 720, H = 132, padL = 12, padR = 12, axisY = 78;
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

    var W = 720, H = 290, padL = 74, padR = 108, padT = 14, padB = 32;
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
      ' by year" style="min-width:560px">' + svg + '</svg>');

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
    var rowH = 34, labelW = opts.labelW || 196, valueW = 92, barMaxW = 380, barH = 17, r = 4;
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
    var W = 720, H = 200, padL = 74, padR = 96, padT = 12, padB = 30;
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
      'aria-label="Cumulative cost of ownership by year" style="min-width:560px">' + svg + '</svg>');

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
    var ctx = E.analyze(readForm(), DATA, live);
    lastCtx = ctx;
    $('results').style.display = 'block';
    $('btn-save').style.display = 'inline-block';
    renderScore(ctx);
    renderIdentity(ctx);
    renderTiles(ctx);
    renderPrice(ctx);
    renderTermComparison(ctx);
    renderLineChart(ctx);
    renderBarChart(ctx);
    renderCumulativeChart(ctx);
    renderComplaintChart(ctx);
    renderFindings(ctx);
    renderCompare();
    renderColophon(ctx);
    updateHints(ctx);
    if (!scrolledOnce) {
      document.getElementById('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
      scrolledOnce = true;
    }
  }

  function updateHints(ctx) {
    $('mpg-hint').textContent = $('in-mpg').value ? 'your figure'
      : (live.mpg ? live.mpg + ' MPG from EPA, live'
                  : Math.round(ctx.input.mpg || 0) + ' MPG from built-in data');
    $('gas-hint').textContent = $('in-gas').value ? 'your figure'
      : (live.gasUsdPerGal ? fmtUSD2.format(live.gasUsdPerGal) + '/gal from EIA, live'
                           : fmtUSD2.format(DATA.energy.gasUsdPerGal) + '/gal built-in average');
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
      return '<option value="' + s + '">' + (s || '—') + '</option>';
    }).join('');
    $('in-model').value = 'Camry';
    refreshModelList();

    var keys = currentKeys();
    ['eia', 'fred', 'marketcheck'].forEach(function (k) { if (keys[k]) $('key-' + k).value = keys[k]; });
    rebuildSources();

    $('btn-analyze').addEventListener('click', function () { refreshLive().then(run); });
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
      el.addEventListener('change', function () { if (lastCtx) run(); });
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
      $('compare-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
    });

    renderCompare();
    renderColophon(null);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
