/* Used Car Deal Analyzer — UI layer: form wiring, charts, live lookups.
 *
 * All arithmetic lives in engine.js; all network calls live in sources.js. This file
 * only reads the form, calls those two, and paints the result.
 *
 * Chart specs follow the project data-viz method: 2px lines, >=8px end dots with a 2px
 * surface ring, bars capped at 24px with a 4px rounded data end, hairline gridlines,
 * selective direct labels, hover tooltips, and a table view on every chart. The two
 * series colors are validated for colorblind separation in both light and dark modes.
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

  var KEYS_STORE = 'ucda_keys_v1';
  var SAVED_STORE = 'ucda_saved_v1';

  var lastCtx = null;
  var live = {};          // whatever the network gave us for the current vehicle
  var sources = null;
  var scrolledOnce = false;

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

  /* ------------------------------------------------------- form reading */

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
      name: val('in-name'),
      brand: val('in-brand'),
      model: val('in-model'),
      year: numOrNull('in-year'),
      segment: val('in-segment'),
      miles: numOrNull('in-miles'),
      asking: numOrNull('in-price'),
      mpg: numOrNull('in-mpg'),
      condition: val('in-condition'),
      title: val('in-title'),
      accidents: val('in-accidents'),
      owners: numOrNull('in-owners'),
      records: val('in-records'),
      ppi: val('in-ppi'),
      comps: [numOrNull('in-comp1'), numOrNull('in-comp2'), numOrNull('in-comp3')]
        .filter(function (c) { return c !== null; }),
      recallsVerified: $('in-recalls-verified').checked,
      payType: val('in-paytype'),
      down: numOrNull('in-down'),
      creditTier: val('in-credit'),
      apr: numOrNull('in-apr') !== null ? numOrNull('in-apr') : DATA.aprByTier[val('in-credit')],
      termMonths: parseInt(val('in-term'), 10),
      taxRate: (numOrNull('in-tax') || 0) / 100,
      fees: numOrNull('in-fees'),
      annualMiles: numOrNull('in-annualmiles'),
      horizon: numOrNull('in-horizon'),
      gasUsdPerGal: numOrNull('in-gas'),
      insurance: numOrNull('in-insurance'),
      income: numOrNull('in-income')
    };
  }

  /* --------------------------------------------------------- rendering */

  function verdictColor(level) {
    return {
      good: css('--status-good'), warning: css('--status-warning'),
      serious: css('--status-serious'), critical: css('--status-critical')
    }[level] || css('--status-warning');
  }

  function renderScore(ctx) {
    $('score-num').textContent = ctx.score.score;
    $('score-fill').style.width = ctx.score.score + '%';
    $('score-fill').style.background = verdictColor(ctx.verdict.level);
    $('score-verdict').innerHTML = '<span class="icon" aria-hidden="true">' + ctx.verdict.icon +
      '</span> ' + esc(ctx.verdict.label);
    $('score-caps').textContent = ctx.score.caps.length
      ? 'Score capped by: ' + ctx.score.caps.join(', ') + '.'
      : '';
    $('score-breakdown').innerHTML = ctx.score.parts.map(function (p) {
      return '<tr><td>' + esc(p.label) +
        (p.source ? ' <span class="prov">' + esc(p.source) + '</span>' : '') +
        '</td><td class="num">' + p.points.toFixed(1) + ' / ' + p.max +
        '</td><td>' + esc(p.note) + '</td></tr>';
    }).join('');
  }

  function renderIdentity(ctx) {
    var card = $('identity-card');
    var bits = [];
    var decoded = live.decoded;
    var hasAnything = decoded || live.recalls || live.safety || live.complaints || live.cpiTrend;
    if (!hasAnything) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    if (decoded) {
      bits.push('<p class="spec-line">' + esc([decoded.year, decoded.make, decoded.model, decoded.trim]
        .filter(Boolean).join(' ')) +
        (decoded.displacementL ? ' · ' + esc(decoded.displacementL) + 'L' : '') +
        (decoded.fuelType ? ' · ' + esc(decoded.fuelType) : '') +
        (decoded.driveType ? ' · ' + esc(decoded.driveType) : '') +
        (decoded.bodyClass ? ' · ' + esc(decoded.bodyClass) : '') +
        ' <span class="prov">NHTSA vPIC</span></p>');
    }
    if (live.safety && live.safety.overallRating) {
      var n = Math.round(live.safety.overallRating);
      bits.push('<p class="spec-line"><span class="stars" aria-hidden="true">' +
        '★'.repeat(n) + '☆'.repeat(5 - n) + '</span> ' + n +
        '-star NHTSA overall crash rating <span class="prov">live</span></p>');
    }
    if (live.recalls) {
      bits.push('<p class="spec-line">' + (live.recalls.length === 0
        ? 'No open NHTSA recalls for this model year.'
        : live.recalls.length + ' open recall' + (live.recalls.length > 1 ? 's' : '') + ': ' +
          esc(live.recalls.map(function (r) { return r.component; }).slice(0, 5).join('; '))) +
        ' <span class="prov">NHTSA</span></p>');
    }
    if (live.complaints) {
      bits.push('<p class="spec-line">' + fmtNum.format(live.complaints.count) +
        ' owner complaints filed with NHTSA' +
        (live.complaints.top && live.complaints.top.length
          ? ', most often ' + esc(live.complaints.top.slice(0, 2).join(' and ').toLowerCase()) : '') +
        ' <span class="prov">live</span></p>');
    }
    if (live.cpiTrend) {
      var t = live.cpiTrend.changeYoY;
      bits.push('<p class="spec-line">Used-car prices nationally are ' +
        (t >= 0 ? 'up ' : 'down ') + Math.abs(t * 100).toFixed(1) +
        '% over the past year <span class="prov">FRED CPI</span></p>');
    }
    $('identity-body').innerHTML = bits.join('');
  }

  function renderTiles(ctx) {
    var tiles = [
      { label: 'Out-the-door price', value: usd(ctx.otd),
        sub: usd(ctx.input.asking) + ' plus tax and fees' },
      ctx.input.financed
        ? { label: 'Monthly payment', value: usd(ctx.loan.payment),
            sub: ctx.input.apr.toFixed(1) + '% APR · ' + ctx.input.termMonths + ' mo · ' +
                 usd(ctx.loan.totalInterest) + ' total interest' }
        : { label: 'Cash purchase', value: usd(ctx.otd), sub: 'no interest cost' },
      { label: ctx.input.horizon + '-year cost of ownership', value: usd(ctx.tco.total),
        sub: fmtUSD2.format(ctx.tco.costPerMile) + ' per mile · ' + usd(ctx.tco.costPerMonth) + '/mo' },
      { label: 'Estimated life remaining', value: fmtNum.format(Math.round(ctx.remainingMiles)) + ' mi',
        sub: 'about ' + ctx.remainingYears.toFixed(1) + ' yrs at ' +
             fmtNum.format(ctx.input.annualMiles) + ' mi/yr' }
    ];
    $('tiles').innerHTML = tiles.map(function (t) {
      return '<div class="tile"><div class="label">' + esc(t.label) + '</div><div class="value">' +
        esc(t.value) + '</div><div class="sub">' + esc(t.sub) + '</div></div>';
    }).join('');
  }

  function renderPrice(ctx) {
    var parts = [];
    var f = ctx.fair;
    parts.push('<p style="margin:0 0 14px;color:var(--text-secondary)">' + (f.hasComps
      ? 'Based on your ' + f.compCount + ' market comp' + (f.compCount > 1 ? 's' : '') +
        ' (average ' + usd(f.base) + '), adjusted ' +
        (f.mileageAdj >= 0 ? 'up ' : 'down ') + usd(Math.abs(f.mileageAdj)) + ' for mileage against the ' +
        fmtNum.format(Math.round(f.expectedMiles)) + '-mile norm for its age, then for condition, title and history.'
      : 'No market comps entered, so fair value is anchored to the asking price and adjusted for mileage, ' +
        'condition, title and history. Add one to three comps from KBB, Edmunds or Cars.com for a much sharper answer.') +
      '</p>');

    var overUnder = ctx.priceDelta > 0.02
      ? '<b>' + pct(ctx.priceDelta) + '</b> above estimated fair value'
      : ctx.priceDelta < -0.02
        ? '<b>' + pct(ctx.priceDelta) + '</b> below fair value — priced in your favor'
        : 'within 2% of estimated fair value';
    var n = ctx.negotiation;
    parts.push('<div class="grid cols-4">' +
      '<div class="tile bare"><div class="label">Estimated fair value</div><div class="value">' +
        usd(n.fair) + '</div><div class="sub">asking is ' + overUnder + '</div></div>' +
      '<div class="tile bare"><div class="label">Open your offer at</div><div class="value">' +
        usd(n.opening) + '</div><div class="sub">leaves room to settle at target</div></div>' +
      '<div class="tile bare"><div class="label">Target price</div><div class="value">' +
        usd(n.target) + '</div><div class="sub">a realistic good outcome</div></div>' +
      '<div class="tile bare"><div class="label">Walk away above</div><div class="value">' +
        usd(n.walkAway) + '</div><div class="sub">out-the-door: ' + usd(n.walkAwayOtd) + '</div></div>' +
      '</div>');

    if (ctx.affordability) {
      var a = ctx.affordability;
      var rows = [
        [a.downOk, 'Down payment at least 20% of out-the-door (currently ' + (a.downPct * 100).toFixed(0) + '%)'],
        [a.termOk, ctx.input.financed ? 'Loan term 48 months or less (currently ' + ctx.input.termMonths + ')' : 'No loan'],
        [a.shareOk, 'Car costs 10% of take-home or less (currently ' + (a.share * 100).toFixed(1) +
          '%, including payment, insurance and fuel)']
      ];
      parts.push('<h3>20 / 4 / 10 affordability check</h3><ul class="findings">' +
        rows.map(function (r) {
          return '<li class="' + (r[0] ? 'f-good' : 'f-warning') + '"><span class="icon" aria-hidden="true">' +
            (r[0] ? '✓' : '⚠') + '</span><span><span class="tag">' + (r[0] ? 'Pass' : 'Over') + '</span> ' +
            esc(r[1]) + '</span></li>';
        }).join('') + '</ul>');
    }
    $('price-analysis').innerHTML = parts.join('');
  }

  /* ------------------------------------------------------- line chart */

  function renderLineChart(ctx) {
    var years = ctx.chartYears;
    var series = [{ name: 'Estimated value', color: css('--series-1'), data: ctx.valueCurve }];
    if (ctx.input.financed) {
      series.push({ name: 'Loan balance', color: css('--series-2'), data: ctx.balanceCurve });
    }

    // Legend only when there are two or more series; a single series is named by the title.
    $('line-legend').innerHTML = series.length >= 2 ? series.map(function (s) {
      return '<span class="key"><span class="swatch" style="background:' + s.color + '"></span>' +
        esc(s.name) + '</span>';
    }).join('') : '';

    $('equity-note').textContent = ctx.input.financed
      ? (ctx.underwaterUntil >= 1
          ? 'You would owe more than the car is worth through year ' + ctx.underwaterUntil +
            '. A larger down payment or a shorter term closes that gap.'
          : 'You stay above water — the car is worth more than the loan balance for the whole term.')
      : 'Cash purchase, so the line is simply the projected resale value.';

    var W = 720, H = 300, padL = 68, padR = 104, padT = 16, padB = 34;
    var maxY = Math.max.apply(null, series.reduce(function (a, s) { return a.concat(s.data); }, [])) * 1.08 || 1;
    var x = function (t) { return padL + (W - padL - padR) * (years ? t / years : 0); };
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / maxY); };

    var step = Math.max(Math.ceil(maxY / 4 / 1000) * 1000, 1000);
    var svg = '';
    for (var g = 0; g <= maxY; g += step) {
      svg += '<line x1="' + padL + '" y1="' + y(g) + '" x2="' + (W - padR) + '" y2="' + y(g) +
        '" stroke="' + css('--grid') + '" stroke-width="1"/>' +
        '<text x="' + (padL - 8) + '" y="' + (y(g) + 4) + '" text-anchor="end" font-size="11" fill="' +
        css('--text-muted') + '" style="font-variant-numeric:tabular-nums">$' + fmtNum.format(g) + '</text>';
    }
    for (var t = 0; t <= years; t++) {
      svg += '<text x="' + x(t) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="11" fill="' +
        css('--text-muted') + '">' + (t === 0 ? 'Now' : '+' + t + 'y') + '</text>';
    }
    svg += '<line x1="' + padL + '" y1="' + y(0) + '" x2="' + (W - padR) + '" y2="' + y(0) +
      '" stroke="' + css('--baseline') + '" stroke-width="1"/>';

    series.forEach(function (s) {
      var d = s.data.map(function (v, i) {
        return (i === 0 ? 'M' : 'L') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
      }).join('');
      var end = s.data.length - 1;
      svg += '<path d="' + d + '" fill="none" stroke="' + s.color +
        '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
        '<circle cx="' + x(end) + '" cy="' + y(s.data[end]) + '" r="4" fill="' + s.color +
        '" stroke="' + css('--surface-1') + '" stroke-width="2"/>' +
        '<text x="' + (x(end) + 8) + '" y="' + (y(s.data[end]) + 4) + '" font-size="12" fill="' +
        css('--text-secondary') + '" style="font-variant-numeric:tabular-nums">' + usd(s.data[end]) + '</text>';
    });
    svg += '<line id="crosshair" x1="0" y1="' + padT + '" x2="0" y2="' + (H - padB) +
      '" stroke="' + css('--baseline') + '" stroke-width="1" style="display:none"/>' +
      '<rect id="line-hit" x="' + padL + '" y="' + padT + '" width="' + (W - padL - padR) +
      '" height="' + (H - padT - padB) + '" fill="transparent"/>';

    $('line-chart').innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" ' +
      'aria-label="Projected vehicle value' + (ctx.input.financed ? ' and loan balance' : '') +
      ' by year" style="min-width:560px">' + svg + '</svg>';

    attachLineHover(ctx, series, x, y, years, W, padL, padR);

    var head = '<table><tr><th>Year</th>' + series.map(function (s) { return '<th>' + esc(s.name) + '</th>'; }).join('') +
      (ctx.input.financed ? '<th>Equity</th>' : '') + '</tr>';
    for (var r = 0; r <= years; r++) {
      head += '<tr><td>' + (r === 0 ? 'Now' : '+' + r) + '</td>' +
        series.map(function (s) { return '<td>' + usd(s.data[r]) + '</td>'; }).join('') +
        (ctx.input.financed ? '<td>' + usd(ctx.valueCurve[r] - ctx.balanceCurve[r]) + '</td>' : '') + '</tr>';
    }
    $('line-table').innerHTML = head + '</table>';
  }

  function attachLineHover(ctx, series, x, y, years, W, padL, padR) {
    var svgEl = $('line-chart').querySelector('svg');
    var hit = svgEl.querySelector('#line-hit');
    var cross = svgEl.querySelector('#crosshair');
    var tip = $('line-tooltip');
    var block = tip.closest('.chart-block');

    function move(clientX, clientY) {
      var pt = svgEl.createSVGPoint();
      pt.x = clientX; pt.y = clientY;
      var sp = pt.matrixTransform(svgEl.getScreenCTM().inverse());
      var t = clamp(Math.round((sp.x - padL) / (W - padL - padR) * years), 0, years);
      cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t));
      cross.style.display = 'block';
      tip.style.display = 'block';
      tip.innerHTML = '<div class="t-title">' + (t === 0 ? 'Today' : 'Year +' + t) + '</div>' +
        series.map(function (s) {
          return '<div class="t-row"><span><span style="display:inline-block;width:10px;height:3px;' +
            'border-radius:2px;background:' + s.color + ';vertical-align:middle;margin-right:5px"></span>' +
            esc(s.name) + '</span><b>' + usd(s.data[t]) + '</b></div>';
        }).join('') +
        (ctx.input.financed
          ? '<div class="t-row"><span>Equity</span><b>' + usd(ctx.valueCurve[t] - ctx.balanceCurve[t]) + '</b></div>'
          : '');
      var br = block.getBoundingClientRect();
      tip.style.left = clamp(clientX - br.left + 14, 0, Math.max(br.width - 200, 0)) + 'px';
      tip.style.top = (clientY - br.top + 14) + 'px';
    }

    hit.addEventListener('mousemove', function (e) { move(e.clientX, e.clientY); });
    hit.addEventListener('touchmove', function (e) {
      if (e.touches[0]) { move(e.touches[0].clientX, e.touches[0].clientY); }
    }, { passive: true });
    function hide() { cross.style.display = 'none'; tip.style.display = 'none'; }
    hit.addEventListener('mouseleave', hide);
    hit.addEventListener('touchend', hide);
  }

  /* -------------------------------------------------------- bar chart */

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
    items = items.filter(function (i) { return i[1] > 0.5; })
                 .sort(function (a, b) { return b[1] - a[1]; });

    $('tco-title').textContent = 'Total cost of ownership — ' + ctx.input.horizon +
      ' year' + (ctx.input.horizon > 1 ? 's' : '');
    $('tco-note').textContent = usd(ctx.tco.total) + ' total over ' +
      fmtNum.format(ctx.tco.totalMiles) + ' miles — ' + fmtUSD2.format(ctx.tco.costPerMile) +
      ' per mile, ' + usd(ctx.tco.costPerMonth) + ' per month all in.';

    if (!items.length) { $('bar-chart').innerHTML = ''; $('bar-table').innerHTML = ''; return; }

    var max = items[0][1];
    var rowH = 40, labelW = 200, valueW = 96, barMaxW = 392, barH = 20;
    var W = labelW + barMaxW + valueW, H = items.length * rowH + 6;
    var color = css('--series-1');
    var svg = '';
    items.forEach(function (it, i) {
      var bw = Math.max(it[1] / max * barMaxW, 4);
      var mid = i * rowH + rowH / 2 + 3;
      var top = mid - barH / 2;
      var r = 4, straight = Math.max(bw - r, 0);
      // Square at the baseline, 4px rounded at the data end.
      svg += '<text x="' + (labelW - 12) + '" y="' + (mid + 4) + '" text-anchor="end" font-size="13" fill="' +
        css('--text-secondary') + '">' + esc(it[0]) + '</text>' +
        '<path d="M' + labelW + ',' + top + ' h' + straight + ' a' + r + ',' + r + ' 0 0 1 ' + r + ',' + r +
        ' v' + (barH - 2 * r) + ' a' + r + ',' + r + ' 0 0 1 -' + r + ',' + r + ' h-' + straight + ' z" ' +
        'fill="' + color + '" class="tco-bar" data-i="' + i + '"/>' +
        '<text x="' + (labelW + bw + 8) + '" y="' + (mid + 4) + '" font-size="13" fill="' +
        css('--text-primary') + '" style="font-variant-numeric:tabular-nums">' + usd(it[1]) + '</text>';
    });
    svg += '<line x1="' + labelW + '" y1="0" x2="' + labelW + '" y2="' + H + '" stroke="' +
      css('--baseline') + '" stroke-width="1"/>';

    $('bar-chart').innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" role="img" ' +
      'aria-label="Cost of ownership by category" style="min-width:' + W + 'px;display:block">' + svg + '</svg>';

    var tip = $('bar-tooltip');
    var block = tip.closest('.chart-block');
    Array.prototype.forEach.call($('bar-chart').querySelectorAll('.tco-bar'), function (bar) {
      bar.addEventListener('mousemove', function (e) {
        var it = items[parseInt(bar.dataset.i, 10)];
        tip.style.display = 'block';
        tip.innerHTML = '<div class="t-title">' + esc(it[0]) + '</div>' +
          '<div class="t-row"><span>' + ctx.input.horizon + '-yr total</span><b>' + usd(it[1]) + '</b></div>' +
          '<div class="t-row"><span>Share</span><b>' + (it[1] / ctx.tco.total * 100).toFixed(0) + '%</b></div>' +
          '<div class="t-row"><span>Per month</span><b>' + usd(it[1] / ctx.input.horizon / 12) + '</b></div>';
        var br = block.getBoundingClientRect();
        tip.style.left = clamp(e.clientX - br.left + 14, 0, Math.max(br.width - 200, 0)) + 'px';
        tip.style.top = (e.clientY - br.top + 14) + 'px';
      });
      bar.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    });

    $('bar-table').innerHTML = '<table><tr><th>Category</th><th>Total</th><th>Share</th><th>Per month</th></tr>' +
      items.map(function (it) {
        return '<tr><td>' + esc(it[0]) + '</td><td>' + usd(it[1]) + '</td><td>' +
          (it[1] / ctx.tco.total * 100).toFixed(0) + '%</td><td>' +
          usd(it[1] / ctx.input.horizon / 12) + '</td></tr>';
      }).join('') +
      '<tr><td><b>Total</b></td><td><b>' + usd(ctx.tco.total) + '</b></td><td>100%</td><td><b>' +
      usd(ctx.tco.costPerMonth) + '</b></td></tr></table>';
  }

  function renderFindings(ctx) {
    var icon = { good: '✓', warning: '⚠', critical: '✕' };
    $('findings').innerHTML = ctx.findings.length
      ? ctx.findings.map(function (f) {
          return '<li class="f-' + f.level + '"><span class="icon" aria-hidden="true">' + icon[f.level] +
            '</span><span><span class="tag">' + esc(f.tag) + ':</span> ' + esc(f.text) + '</span></li>';
        }).join('')
      : '<li class="f-good"><span class="icon" aria-hidden="true">✓</span><span>No notable flags found.</span></li>';
  }

  /* ------------------------------------------------------ comparison */

  function renderCompare() {
    var list = loadJson(SAVED_STORE, []);
    var card = $('compare-card');
    if (!list.length) { card.style.display = 'none'; return; }
    card.style.display = 'block';

    var cols = [
      { head: 'Car', get: function (c) { return esc(c.name); } },
      { head: 'Score', get: function (c) { return c.score; }, best: 'max', raw: function (c) { return c.score; } },
      { head: 'Asking', get: function (c) { return usd(c.asking); } },
      { head: 'Fair value', get: function (c) { return usd(c.fair); } },
      { head: 'Out-the-door', get: function (c) { return usd(c.otd); }, best: 'min', raw: function (c) { return c.otd; } },
      { head: 'Monthly', get: function (c) { return c.monthly > 0 ? usd(c.monthly) : '—'; } },
      { head: 'Cost of ownership', get: function (c) { return usd(c.tco) + ' / ' + c.horizon + 'yr'; },
        best: 'min', raw: function (c) { return c.tco / c.horizon; } },
      { head: '$/mile', get: function (c) { return fmtUSD2.format(c.cpm); }, best: 'min', raw: function (c) { return c.cpm; } },
      { head: 'Life left', get: function (c) { return fmtNum.format(Math.round(c.lifeLeft)) + ' mi'; },
        best: 'max', raw: function (c) { return c.lifeLeft; } },
      { head: 'Verdict', get: function (c) { return esc(c.verdict); } },
      { head: '', get: function (c) { return '<button class="small" data-del="' + c.id + '">✕</button>'; } }
    ];

    var bestRow = {};
    cols.forEach(function (col, ci) {
      if (!col.best || list.length < 2) return;
      var best = 0;
      list.forEach(function (c, ri) {
        var v = col.raw(c), bv = col.raw(list[best]);
        if (col.best === 'max' ? v > bv : v < bv) best = ri;
      });
      bestRow[ci] = best;
    });

    $('compare-table').innerHTML = '<tr>' + cols.map(function (c) { return '<th>' + c.head + '</th>'; }).join('') + '</tr>' +
      list.map(function (c, ri) {
        return '<tr>' + cols.map(function (col, ci) {
          return '<td class="' + (bestRow[ci] === ri ? 'best' : '') + '">' + col.get(c) + '</td>';
        }).join('') + '</tr>';
      }).join('');

    Array.prototype.forEach.call($('compare-table').querySelectorAll('button[data-del]'), function (btn) {
      btn.addEventListener('click', function () {
        saveJson(SAVED_STORE, loadJson(SAVED_STORE, []).filter(function (c) {
          return String(c.id) !== btn.dataset.del;
        }));
        renderCompare();
      });
    });
  }

  /* -------------------------------------------------------- footnote */

  function renderFootnote() {
    var m = DATA.meta;
    var mode = live.provenance && Object.keys(live.provenance).length
      ? 'Live NHTSA/EPA data in use for this vehicle.'
      : 'Running from the built-in dataset (no live lookups yet — enter a VIN or make/model and analyze).';
    $('data-footnote').innerHTML =
      esc(mode) + ' Built-in dataset built ' + esc(m.built) + '. ' +
      'Live sources: NHTSA vPIC (VIN decode), NHTSA recalls, complaints and NCAP crash ratings, ' +
      'and FuelEconomy.gov (EPA) fuel economy — all free U.S. government APIs requiring no key. ' +
      'Optional keyed sources: EIA (fuel and electricity prices), FRED (used-car CPI trend), ' +
      'Marketcheck (live market comps). Built-in benchmarks: ' +
      esc(m.sources.brands) + '; ' + esc(m.sources.depCurves) + '; ' + esc(m.sources.aprByTier) + '. ' +
      'These are estimates for decision support — always verify price against live listings and get a ' +
      'real insurance quote. This is not financial advice.';
  }

  /* ------------------------------------------------------ main flow */

  function run() {
    var ctx = E.analyze(readForm(), DATA, live);
    lastCtx = ctx;
    $('results').style.display = 'block';
    $('btn-save').style.display = 'inline-block';
    renderScore(ctx);
    renderIdentity(ctx);
    renderTiles(ctx);
    renderPrice(ctx);
    renderLineChart(ctx);
    renderBarChart(ctx);
    renderFindings(ctx);
    renderCompare();
    renderFootnote();
    updateHints(ctx);
    if (!scrolledOnce) {
      $('score-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
      scrolledOnce = true;
    }
  }

  function updateHints(ctx) {
    $('mpg-hint').textContent = $('in-mpg').value
      ? 'your figure'
      : (live.mpg ? 'using ' + live.mpg + ' MPG from EPA (live)' : 'using ' + Math.round(ctx.input.mpg || 0) + ' MPG from built-in data');
    $('gas-hint').textContent = $('in-gas').value
      ? 'your figure'
      : (live.gasUsdPerGal ? 'using $' + live.gasUsdPerGal.toFixed(2) + '/gal from EIA (live)'
                           : 'using $' + DATA.energy.gasUsdPerGal.toFixed(2) + '/gal built-in average');
    $('apr-hint').textContent = $('in-apr').value ? 'your figure'
      : 'using ' + DATA.aprByTier[val('in-credit')].toFixed(1) + '% tier average';
  }

  /* --------------------------------------------------- live lookups */

  function setStatus(msg) { $('live-status').textContent = msg || ''; }

  function vehicleQuery() {
    return {
      make: val('in-brand'), model: val('in-model'),
      year: parseInt(val('in-year'), 10),
      stateId: val('in-state') || null,
      gasArea: val('in-state') ? 'S' + val('in-state') : null
    };
  }

  function refreshLive() {
    var q = vehicleQuery();
    if (!q.make || !q.model || !q.year) { return Promise.resolve(); }
    setStatus('Checking NHTSA and EPA…');
    return sources.loadAll(q).then(function (result) {
      var decoded = live.decoded;
      live = result;
      if (decoded) live.decoded = decoded;
      setStatus(Object.keys(live.provenance).length
        ? 'Live data loaded: ' + Object.keys(live.provenance).join(', ') + '.'
        : 'Live lookup unavailable — using built-in data.');
    }).catch(function () {
      setStatus('Live lookup unavailable — using built-in data.');
    });
  }

  function decodeVin() {
    var vin = val('in-vin');
    if (!vin) { $('vin-result').innerHTML = '<div class="banner info">Enter a VIN first.</div>'; return; }
    $('btn-vin').disabled = true;
    setStatus('Decoding VIN…');
    sources.decodeVin(vin).then(function (r) {
      if (!r.ok) {
        $('vin-result').innerHTML = '<div class="banner info"><span aria-hidden="true">⚠</span><span>' +
          (r.reason === 'invalid-format'
            ? 'That does not look like a valid VIN (17 characters, no I, O or Q). Fill the fields in manually.'
            : r.reason === 'not-decoded'
              ? 'NHTSA could not decode that VIN. Fill the fields in manually.'
              : 'Could not reach the NHTSA VIN service. You are offline or it is down — fill the fields in manually; ' +
                'everything else still works.') + '</span></div>';
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
      $('vin-result').innerHTML = '<div class="banner info"><span aria-hidden="true">✓</span><span>Decoded: ' +
        esc([r.year, r.make, r.model, r.trim].filter(Boolean).join(' ')) + '. Checking recalls and ratings…</span></div>';
      return refreshLive().then(function () {
        $('vin-result').innerHTML = '';
        run();
      });
    }).catch(function () {
      setStatus('');
    }).then(function () { $('btn-vin').disabled = false; });
  }

  function autofillComps() {
    var q = vehicleQuery();
    q.miles = numOrNull('in-miles') || 0;
    setStatus('Fetching market comps…');
    sources.marketComps(q).then(function (r) {
      if (!r) { setStatus('No market comps returned.'); return; }
      $('in-comp1').value = Math.round(r.p25);
      $('in-comp2').value = Math.round(r.median);
      $('in-comp3').value = Math.round(r.p75);
      setStatus('Filled from ' + r.n + ' live listings.');
      run();
    });
  }

  /* ------------------------------------------------------- form init */

  function refreshModelList() {
    var make = val('in-brand');
    var models = DATA.vehicles[make] ? Object.keys(DATA.vehicles[make]) : [];
    $('model-list').innerHTML = models.map(function (m) { return '<option value="' + esc(m) + '">'; }).join('');
  }

  function init() {
    $('in-brand').innerHTML = Object.keys(DATA.brands).map(function (b) {
      return '<option' + (b === 'Toyota' ? ' selected' : '') + '>' + esc(b) + '</option>';
    }).join('');
    $('in-segment').innerHTML = Object.keys(DATA.segments).map(function (s) {
      return '<option' + (s === 'Midsize car' ? ' selected' : '') + '>' + esc(s) + '</option>';
    }).join('');
    $('in-state').innerHTML = US_STATES.map(function (s) {
      return '<option value="' + s + '">' + (s || 'Not specified') + '</option>';
    }).join('');
    $('in-model').value = 'Camry';
    refreshModelList();

    var keys = currentKeys();
    ['eia', 'fred', 'marketcheck'].forEach(function (k) {
      if (keys[k]) $('key-' + k).value = keys[k];
    });
    rebuildSources();

    $('btn-analyze').addEventListener('click', function () {
      refreshLive().then(run);
    });
    $('btn-vin').addEventListener('click', decodeVin);
    $('btn-comps').addEventListener('click', autofillComps);
    $('in-brand').addEventListener('change', refreshModelList);

    // Live re-run on any edit, but only once the first analysis has happened.
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
    $('btn-clear-compare').addEventListener('click', function () {
      saveJson(SAVED_STORE, []); renderCompare();
    });

    $('btn-keys').addEventListener('click', function () {
      var card = $('keys-card');
      card.style.display = card.style.display === 'none' ? 'block' : 'none';
    });
    $('btn-save-keys').addEventListener('click', function () {
      saveJson(KEYS_STORE, {
        eia: val('key-eia') || undefined,
        fred: val('key-fred') || undefined,
        marketcheck: val('key-marketcheck') || undefined
      });
      rebuildSources();
      $('keys-status').textContent = 'Saved to this browser.';
    });
    $('btn-clear-keys').addEventListener('click', function () {
      saveJson(KEYS_STORE, {});
      ['eia', 'fred', 'marketcheck'].forEach(function (k) { $('key-' + k).value = ''; });
      rebuildSources();
      $('keys-status').textContent = 'Cleared.';
    });

    $('theme-toggle').addEventListener('click', function () {
      var root = document.documentElement;
      var dark = root.dataset.theme === 'dark' ||
        (!root.dataset.theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
      root.dataset.theme = dark ? 'light' : 'dark';
      if (lastCtx) run();   // charts re-render against the new mode's validated palette
    });

    renderCompare();
    renderFootnote();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
