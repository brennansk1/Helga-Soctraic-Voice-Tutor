/* Visual teaching aids — client renderer.
 *
 * Draws the diagram that sits ABOVE a tutor message. The server sends a
 * validated JSON spec (services/common/visual_aids.py); this file turns it into
 * SVG. Nothing here fetches an image, and no aid is ever a bitmap.
 *
 * THREE RULES THIS FILE KEEPS:
 *
 * 1. NEVER innerHTML a spec value. Every label goes in through textContent or
 *    createTextNode. That is the whole reason the server is allowed to preserve
 *    "<" and ">" in labels — "2 < x < 5" must survive as an inequality, so the
 *    escaping obligation moves here, to the render boundary, and it is absolute.
 *
 * 2. An aid must never cost the learner their turn. A failed fetch, an unknown
 *    kind, a malformed spec — all degrade to the written description, which the
 *    transcript descriptor already carries. There is no broken-image state.
 *
 * 3. Colour comes from CSS custom properties, never from the spec. The model
 *    picks a semantic slot ("c1", "warning"); the stylesheet decides what that
 *    looks like in light and dark. A diagram that is unreadable at night is a
 *    diagram that does not work.
 */
(function () {
    'use strict';

    var NS = 'http://www.w3.org/2000/svg';
    var specCache = new Map();     // aid_id -> full aid object
    var inflight = new Map();      // aid_id -> Promise (dedupes concurrent cards)
    var PIN_KEY = 'helga_pinned_aid';

    // ---------------------------------------------------------------- utils
    function svg(tag, attrs, cls) {
        var n = document.createElementNS(NS, tag);
        if (attrs) { for (var k in attrs) { if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]); } }
        if (cls) n.setAttribute('class', cls);
        return n;
    }

    /* The only way text enters an SVG in this file. */
    function svgText(x, y, str, cls, anchor) {
        var t = svg('text', { x: x, y: y, 'text-anchor': anchor || 'middle' }, 'aid-t ' + (cls || ''));
        t.textContent = String(str === null || str === undefined ? '' : str);
        return t;
    }

    function elem(tag, cls, textContent) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (textContent !== undefined && textContent !== null) n.textContent = String(textContent);
        return n;
    }

    /* An SVG whose viewBox was built at its intended DISPLAY size, so its text
     * renders at the pixel size it was designed for. `width:auto` on inline SVG
     * still resolves to 100% and would scale it back up, so the intrinsic size
     * has to be stated as attributes; CSS then only ever shrinks it. */
    function intrinsicSvg(W, H, cls) {
        return svg('svg', {
            viewBox: '0 0 ' + Math.round(W) + ' ' + Math.round(H),
            width: Math.round(W), height: Math.round(H), role: 'presentation',
        }, 'aid-svg aid-svg--intrinsic ' + (cls || ''));
    }

    /* Linear map from data units to pixels. */
    function scale(d0, d1, p0, p1) {
        var span = (d1 - d0) || 1;
        return function (v) { return p0 + ((v - d0) / span) * (p1 - p0); };
    }

    function fmt(n) {
        if (typeof n !== 'number' || !isFinite(n)) return String(n);
        var r = Math.round(n * 1e4) / 1e4;
        return Number.isInteger(r) ? String(r) : String(r);
    }

    /* Colour slot -> class. Never interpolate a spec value into a style. */
    function colorClass(c) {
        var ok = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'accent', 'muted', 'success', 'warning', 'danger'];
        return 'aid-' + (ok.indexOf(c) >= 0 ? c : 'c1');
    }

    /* Elements above the current stage are drawn but hidden, so revealing is a
     * class toggle rather than a redraw — no layout jump, and CSS can animate it. */
    function stageClass(el, stage, current) {
        var s = stage || 0;
        if (s > 0) {
            el.classList.add('aid-staged');
            el.setAttribute('data-stage', s);
            if (s > current) el.classList.add('aid-veiled');
        }
        return el;
    }

    function ticks(lo, hi, step) {
        var out = [], v, guard = 0;
        if (!(step > 0)) step = (hi - lo) / 8 || 1;
        // Start at the first multiple of `step` at or above lo, so ticks land on
        // round numbers (0, 5, 10) instead of wherever the range happens to start.
        v = Math.ceil(lo / step) * step;
        while (v <= hi + step * 1e-6 && guard++ < 400) { out.push(Math.round(v * 1e6) / 1e6); v += step; }
        return out;
    }

    // ------------------------------------------------------- kind renderers
    // Each returns an <svg> (or an HTMLElement for the two kinds that are
    // genuinely better as real markup — tables and step lists, where semantic
    // HTML gives wrapping and screen-reader structure that SVG cannot).
    var RENDER = {};

    RENDER.number_line = function (s, stage) {
        var W = 560, H = s.intervals.length ? 132 : 104, PAD = 34, axisY = 62;
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');
        var x = scale(s.min, s.max, PAD, W - PAD);

        root.appendChild(svg('line', { x1: PAD - 14, y1: axisY, x2: W - PAD + 14, y2: axisY }, 'aid-axis'));
        // Arrowheads: the line continues beyond what is drawn.
        root.appendChild(svg('path', { d: 'M' + (W - PAD + 14) + ',' + axisY + ' l-8,-4 l0,8 z' }, 'aid-axis-cap'));
        root.appendChild(svg('path', { d: 'M' + (PAD - 14) + ',' + axisY + ' l8,-4 l0,8 z' }, 'aid-axis-cap'));

        ticks(s.min, s.max, s.step).forEach(function (t) {
            var px = x(t);
            root.appendChild(svg('line', { x1: px, y1: axisY - 5, x2: px, y2: axisY + 5 }, 'aid-tick'));
            root.appendChild(svgText(px, axisY + 21, fmt(t), 'aid-t-sm'));
        });

        s.intervals.forEach(function (iv) {
            var g = svg('g', null, 'aid-interval ' + colorClass(iv.color));
            g.appendChild(svg('line', { x1: x(iv.from), y1: axisY - 15, x2: x(iv.to), y2: axisY - 15 }, 'aid-interval-bar'));
            [[iv.from, iv.open_start], [iv.to, iv.open_end]].forEach(function (end) {
                g.appendChild(svg('circle', { cx: x(end[0]), cy: axisY - 15, r: 5 },
                    end[1] ? 'aid-dot-open' : 'aid-dot'));
            });
            if (iv.label) g.appendChild(svgText((x(iv.from) + x(iv.to)) / 2, axisY - 26, iv.label, 'aid-t-sm aid-t-strong'));
            root.appendChild(stageClass(g, iv.stage, stage));
        });

        s.marks.forEach(function (m) {
            var g = svg('g', null, 'aid-mark ' + colorClass(m.color));
            var px = x(m.at);
            if (m.style === 'arrow') {
                g.appendChild(svg('path', { d: 'M' + px + ',' + (axisY - 22) + ' l-5,-9 l10,0 z' }, 'aid-fill'));
            } else if (m.style === 'tick') {
                g.appendChild(svg('line', { x1: px, y1: axisY - 11, x2: px, y2: axisY + 11 }, 'aid-mark-tick'));
            } else {
                g.appendChild(svg('circle', { cx: px, cy: axisY, r: 6 }, m.style === 'open' ? 'aid-dot-open' : 'aid-dot'));
            }
            if (m.label) g.appendChild(svgText(px, axisY - 16, m.label, 'aid-t-sm aid-t-strong'));
            root.appendChild(stageClass(g, m.stage, stage));
        });

        if (s.label) root.appendChild(svgText(W / 2, H - 6, s.label, 'aid-t-sm aid-t-muted'));
        return root;
    };

    RENDER.geometry = function (s, stage) {
        // PAD only has to clear the vertex labels and the side labels drawn just
        // outside the shape; 44 was framing, not clearance.
        var W = 560, PAD = 30;
        var xs = [], ys = [];
        Object.keys(s.points).forEach(function (k) { xs.push(s.points[k][0]); ys.push(s.points[k][1]); });
        s.circles.forEach(function (c) {
            var p = s.points[c.center];
            xs.push(p[0] - c.r, p[0] + c.r); ys.push(p[1] - c.r, p[1] + c.r);
        });
        var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
        var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
        var spanX = (maxX - minX) || 1, spanY = (maxY - minY) || 1;

        // The CANVAS follows the figure, and its SIZE follows the display size.
        //
        // Two bugs live here, and the second is the subtle one. A fixed 560x360
        // box meant a 4:3 triangle filled 362x272 and floated in ~100px of dead
        // margin. Fixing that by capping height in CSS then made it worse: the
        // whole SVG scaled to 0.56x, so 13-unit labels rendered at 7px and the
        // side lengths became unreadable. Text in an SVG scales with the
        // viewBox, so a viewBox far larger than its display box silently
        // shrinks every label.
        //
        // So the viewBox is built at roughly the size it will actually be
        // drawn: 1 unit ≈ 1 CSS pixel, and the labels stay the size they were
        // designed to be.
        var TARGET_INNER_H = 185;
        var aspect = spanX / spanY;
        var innerH = TARGET_INNER_H;
        var innerW = innerH * aspect;
        var maxInnerW = W - 2 * PAD;
        if (innerW > maxInnerW) {            // wide figure: fit the width instead
            innerW = maxInnerW;
            innerH = innerW / aspect;
        }
        innerW = Math.max(innerW, 150);
        W = innerW + 2 * PAD;
        var H = innerH + 2 * PAD;

        // Equal aspect: a right angle must look like a right angle and a circle
        // must be round, so both axes share one scale factor.
        var k = Math.min(innerW / spanX, innerH / spanY);
        var cx = (W - k * spanX) / 2, cy = (H - k * spanY) / 2;
        var X = function (v) { return cx + (v - minX) * k; };
        var Y = function (v) { return H - cy - (v - minY) * k; };   // flip: SVG y grows down

        var root = intrinsicSvg(W, H);

        if (s.show_grid) {
            var g = svg('g', null, 'aid-grid');
            for (var gx = Math.ceil(minX); gx <= maxX; gx++) g.appendChild(svg('line', { x1: X(gx), y1: Y(minY), x2: X(gx), y2: Y(maxY) }));
            for (var gy = Math.ceil(minY); gy <= maxY; gy++) g.appendChild(svg('line', { x1: X(minX), y1: Y(gy), x2: X(maxX), y2: Y(gy) }));
            root.appendChild(g);
        }

        s.polygons.forEach(function (p) {
            var pts = p.vertices.map(function (v) { return X(s.points[v][0]) + ',' + Y(s.points[v][1]); }).join(' ');
            var el = svg('polygon', { points: pts }, 'aid-poly ' + colorClass(p.color));
            root.appendChild(stageClass(el, p.stage, stage));
        });

        s.circles.forEach(function (c) {
            var p = s.points[c.center];
            var el = svg('circle', { cx: X(p[0]), cy: Y(p[1]), r: c.r * k }, 'aid-circle ' + colorClass(c.color));
            root.appendChild(stageClass(el, c.stage, stage));
        });

        s.angles.forEach(function (a) {
            var at = s.points[a.at], from = s.points[a.from], to = s.points[a.to];
            var g2 = svg('g', null, 'aid-angle');
            var v1 = norm(from[0] - at[0], from[1] - at[1]), v2 = norm(to[0] - at[0], to[1] - at[1]);
            if (a.right) {
                // The square marker, drawn in data space then projected.
                var sz = Math.min(spanX, spanY) * 0.11;
                var p1 = [at[0] + v1[0] * sz, at[1] + v1[1] * sz];
                var p3 = [at[0] + v2[0] * sz, at[1] + v2[1] * sz];
                var p2 = [p1[0] + v2[0] * sz, p1[1] + v2[1] * sz];
                g2.appendChild(svg('polyline', {
                    points: [p1, p2, p3].map(function (p) { return X(p[0]) + ',' + Y(p[1]); }).join(' '),
                    fill: 'none'
                }, 'aid-right-angle'));
            } else {
                var r = Math.min(spanX, spanY) * 0.13;
                var a1 = Math.atan2(v1[1], v1[0]), a2 = Math.atan2(v2[1], v2[0]);
                var s1 = [at[0] + Math.cos(a1) * r, at[1] + Math.sin(a1) * r];
                var s2 = [at[0] + Math.cos(a2) * r, at[1] + Math.sin(a2) * r];
                var sweep = ((a2 - a1 + Math.PI * 2) % (Math.PI * 2)) > Math.PI ? 1 : 0;
                g2.appendChild(svg('path', {
                    d: 'M' + X(s1[0]) + ',' + Y(s1[1]) + ' A' + (r * k) + ',' + (r * k) + ' 0 ' + sweep + ' 0 ' + X(s2[0]) + ',' + Y(s2[1]),
                    fill: 'none'
                }, 'aid-arc'));
            }
            if (a.label) {
                var bis = norm(v1[0] + v2[0], v1[1] + v2[1]);
                var off = Math.min(spanX, spanY) * 0.22;
                g2.appendChild(svgText(X(at[0] + bis[0] * off), Y(at[1] + bis[1] * off) + 4, a.label, 'aid-t-sm'));
            }
            root.appendChild(stageClass(g2, a.stage, stage));
        });

        s.segments.forEach(function (seg) {
            var a = s.points[seg.from], b = s.points[seg.to];
            var g3 = svg('g', null, 'aid-seg ' + colorClass(seg.color));
            g3.appendChild(svg('line', { x1: X(a[0]), y1: Y(a[1]), x2: X(b[0]), y2: Y(b[1]) },
                seg.style === 'dashed' ? 'aid-line aid-dashed' : 'aid-line'));
            if (seg.label) {
                // Offset the label off the segment along its normal so it never
                // sits on top of the line it names.
                var mx = (X(a[0]) + X(b[0])) / 2, my = (Y(a[1]) + Y(b[1])) / 2;
                var dx = X(b[0]) - X(a[0]), dy = Y(b[1]) - Y(a[1]);
                var len = Math.hypot(dx, dy) || 1;
                g3.appendChild(svgText(mx - (dy / len) * 15, my + (dx / len) * 15 + 4, seg.label, 'aid-t-sm aid-t-strong'));
            }
            root.appendChild(stageClass(g3, seg.stage, stage));
        });

        if (s.show_point_labels) {
            Object.keys(s.points).forEach(function (name) {
                var p = s.points[name];
                root.appendChild(svg('circle', { cx: X(p[0]), cy: Y(p[1]), r: 3.5 }, 'aid-vertex'));
                root.appendChild(svgText(X(p[0]), Y(p[1]) - 11, name, 'aid-t-sm aid-t-vertex'));
            });
        }

        s.labels.forEach(function (l) {
            root.appendChild(stageClass(svgText(X(l.at[0]), Y(l.at[1]), l.text, 'aid-t-lg ' + colorClass(l.color)), l.stage, stage));
        });
        return root;

        function norm(dx, dy) { var m = Math.hypot(dx, dy) || 1; return [dx / m, dy / m]; }
    };

    RENDER.plot = function (s, stage) {
        var W = 560, H = 360, L = 56, R = 18, T = 20, B = 46;
        var all = s.series.reduce(function (acc, se) { return acc.concat(se.points); }, []);
        var xr = s.x_range || extent(all, 0), yr = s.y_range || extent(all, 1);
        var X = scale(xr[0], xr[1], L, W - R), Y = scale(yr[0], yr[1], H - B, T);
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');

        var xt = ticks(xr[0], xr[1], niceStep(xr)), yt = ticks(yr[0], yr[1], niceStep(yr));
        if (s.show_grid) {
            var g = svg('g', null, 'aid-grid');
            xt.forEach(function (t) { g.appendChild(svg('line', { x1: X(t), y1: T, x2: X(t), y2: H - B })); });
            yt.forEach(function (t) { g.appendChild(svg('line', { x1: L, y1: Y(t), x2: W - R, y2: Y(t) })); });
            root.appendChild(g);
        }
        // Axes drawn AT zero when zero is in range — for a maths plot the axes
        // are part of the content, not a frame around it.
        var zx = (yr[0] <= 0 && yr[1] >= 0) ? Y(0) : H - B;
        var zy = (xr[0] <= 0 && xr[1] >= 0) ? X(0) : L;
        root.appendChild(svg('line', { x1: L, y1: zx, x2: W - R, y2: zx }, 'aid-axis'));
        root.appendChild(svg('line', { x1: zy, y1: T, x2: zy, y2: H - B }, 'aid-axis'));
        xt.forEach(function (t) { if (Math.abs(X(t) - zy) > 8) root.appendChild(svgText(X(t), zx + 17, fmt(t), 'aid-t-xs')); });
        yt.forEach(function (t) { if (Math.abs(Y(t) - zx) > 8) root.appendChild(svgText(zy - 8, Y(t) + 4, fmt(t), 'aid-t-xs', 'end')); });

        s.series.forEach(function (se) {
            var g2 = svg('g', null, 'aid-series ' + colorClass(se.color));
            var d = se.points.map(function (p, i) { return (i ? 'L' : 'M') + X(p[0]) + ',' + Y(p[1]); }).join(' ');
            if (se.style === 'scatter') {
                se.points.forEach(function (p) { g2.appendChild(svg('circle', { cx: X(p[0]), cy: Y(p[1]), r: 3 }, 'aid-fill')); });
            } else if (se.style === 'area') {
                g2.appendChild(svg('path', { d: d + ' L' + X(se.points[se.points.length - 1][0]) + ',' + Y(Math.max(yr[0], 0)) + ' L' + X(se.points[0][0]) + ',' + Y(Math.max(yr[0], 0)) + ' Z' }, 'aid-area'));
                g2.appendChild(svg('path', { d: d, fill: 'none' }, 'aid-curve'));
            } else {
                g2.appendChild(svg('path', { d: d, fill: 'none' }, 'aid-curve' + (se.style === 'dashed' ? ' aid-dashed' : '')));
            }
            root.appendChild(stageClass(g2, se.stage, stage));
        });

        s.markers.forEach(function (m) {
            var g3 = svg('g', null, 'aid-marker ' + colorClass(m.color));
            g3.appendChild(svg('circle', { cx: X(m.at[0]), cy: Y(m.at[1]), r: 5 }, 'aid-fill aid-marker-dot'));
            if (m.label) g3.appendChild(svgText(X(m.at[0]), Y(m.at[1]) - 12, m.label, 'aid-t-sm aid-t-strong'));
            root.appendChild(stageClass(g3, m.stage, stage));
        });

        if (s.x_label) root.appendChild(svgText((L + W - R) / 2, H - 8, s.x_label, 'aid-t-sm aid-t-muted'));
        if (s.y_label) {
            var yl = svgText(0, 0, s.y_label, 'aid-t-sm aid-t-muted');
            yl.setAttribute('transform', 'translate(14,' + (T + H - B) / 2 + ') rotate(-90)');
            root.appendChild(yl);
        }
        if (s.show_legend) root.appendChild(legend(s.series, L + 8, T + 4));
        return root;

        function extent(pts, i) {
            if (!pts.length) return [0, 1];
            var lo = Math.min.apply(null, pts.map(function (p) { return p[i]; }));
            var hi = Math.max.apply(null, pts.map(function (p) { return p[i]; }));
            if (hi === lo) { hi = lo + 1; lo -= 1; }
            var pad = (hi - lo) * 0.06;
            return [lo - pad, hi + pad];
        }
        function niceStep(r) {
            var raw = (r[1] - r[0]) / 7, mag = Math.pow(10, Math.floor(Math.log10(Math.abs(raw) || 1)));
            var mults = [1, 2, 2.5, 5, 10];
            for (var i = 0; i < mults.length; i++) if (raw <= mults[i] * mag) return mults[i] * mag;
            return 10 * mag;
        }
    };

    function legend(series, x, y) {
        var g = svg('g', null, 'aid-legend');
        series.forEach(function (se, i) {
            if (!se.label) return;
            var row = svg('g', { transform: 'translate(' + x + ',' + (y + i * 18) + ')' }, colorClass(se.color));
            row.appendChild(svg('rect', { x: 0, y: 0, width: 12, height: 3, rx: 1.5 }, 'aid-fill'));
            row.appendChild(svgText(18, 4, se.label, 'aid-t-xs', 'start'));
            g.appendChild(row);
        });
        return g;
    }

    RENDER.bars = function (s, stage) {
        var horiz = s.orientation === 'horizontal';
        var n = s.categories.length, k = s.series.length;
        var W = 560, H = Math.max(220, horiz ? 60 + n * 34 : 320);
        var L = horiz ? 110 : 56, R = 20, T = 22, B = horiz ? 40 : 62;
        var maxV = Math.max.apply(null, s.series.reduce(function (a, se) { return a.concat(se.values); }, [0]));
        var minV = Math.min.apply(null, s.series.reduce(function (a, se) { return a.concat(se.values); }, [0]));
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');
        var V = horiz ? scale(Math.min(0, minV), maxV, L, W - R) : scale(Math.min(0, minV), maxV, H - B, T);
        var band = ((horiz ? H - T - B : W - L - R) / n);
        var bw = Math.min(46, (band * 0.72) / k);

        root.appendChild(svg('line', horiz
            ? { x1: V(0), y1: T, x2: V(0), y2: H - B }
            : { x1: L, y1: V(0), x2: W - R, y2: V(0) }, 'aid-axis'));

        s.categories.forEach(function (cat, ci) {
            var base = (horiz ? T : L) + band * ci + band / 2;
            s.series.forEach(function (se, si) {
                var v = se.values[ci] || 0;
                var off = base - (k * bw) / 2 + si * bw;
                var hot = s.highlight.indexOf(ci) >= 0;
                var g = svg('g', null, 'aid-bar ' + colorClass(se.color) + (hot ? ' aid-bar-hot' : ''));
                g.appendChild(svg('rect', horiz
                    ? { x: Math.min(V(0), V(v)), y: off, width: Math.abs(V(v) - V(0)), height: bw - 3, rx: 3 }
                    : { x: off, y: Math.min(V(0), V(v)), width: bw - 3, height: Math.abs(V(v) - V(0)), rx: 3 },
                    'aid-fill'));
                if (s.show_values) {
                    g.appendChild(horiz
                        ? svgText(V(v) + 16, off + bw / 2 + 1, fmt(v), 'aid-t-xs')
                        : svgText(off + (bw - 3) / 2, Math.min(V(0), V(v)) - 6, fmt(v), 'aid-t-xs'));
                }
                root.appendChild(stageClass(g, se.stage, stage));
            });
            root.appendChild(horiz
                ? svgText(L - 10, base + 4, cat, 'aid-t-sm', 'end')
                : svgText(base, H - B + 18, cat, 'aid-t-sm'));
        });

        if (s.y_label) root.appendChild(svgText(horiz ? W / 2 : 14, horiz ? H - 8 : 14, s.y_label, 'aid-t-xs aid-t-muted', horiz ? 'middle' : 'start'));
        if (s.show_legend) root.appendChild(legend(s.series, L + 6, T));
        return root;
    };

    RENDER.graph = function (s, stage) {
        var pos = s.layout || {};
        var cols = 0, rows = 0;
        Object.keys(pos).forEach(function (id) { cols = Math.max(cols, pos[id][0]); rows = Math.max(rows, pos[id][1]); });
        var NW = 118, NH = 46, GX = 26, GY = 60, PAD = 18;
        var W = Math.max(320, PAD * 2 + (cols + 1) * NW + cols * GX);
        var H = PAD * 2 + (rows + 1) * NH + rows * GY;
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');

        var defs = svg('defs');
        var mk = svg('marker', { id: 'aid-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
        mk.appendChild(svg('path', { d: 'M0,0 L10,5 L0,10 z' }, 'aid-arrow-head'));
        defs.appendChild(mk); root.appendChild(defs);

        function centre(id) {
            var p = pos[id] || [0, 0];
            return [PAD + p[0] * (NW + GX) + NW / 2, PAD + p[1] * (NH + GY) + NH / 2];
        }

        s.edges.forEach(function (e) {
            var a = centre(e.from), b = centre(e.to);
            // Stop the line at the node's boundary box so the arrowhead touches
            // the edge of the box rather than vanishing under its centre.
            var t = clipToBox(a, b, NW / 2 + 4, NH / 2 + 4);
            var g = svg('g', null, 'aid-edge ' + colorClass(e.color));
            var line = svg('line', { x1: t.a[0], y1: t.a[1], x2: t.b[0], y2: t.b[1] }, 'aid-edge-line');
            if (e.directed) line.setAttribute('marker-end', 'url(#aid-arrow)');
            g.appendChild(line);
            if (e.label) {
                var mx = (t.a[0] + t.b[0]) / 2, my = (t.a[1] + t.b[1]) / 2;
                var bg = svg('rect', { x: mx - e.label.length * 3.3 - 4, y: my - 9, width: e.label.length * 6.6 + 8, height: 16, rx: 4 }, 'aid-edge-label-bg');
                g.appendChild(bg);
                g.appendChild(svgText(mx, my + 3, e.label, 'aid-t-xs'));
            }
            root.appendChild(stageClass(g, e.stage, stage));
        });

        s.nodes.forEach(function (nd) {
            var c = centre(nd.id);
            var g = svg('g', { transform: 'translate(' + (c[0] - NW / 2) + ',' + (c[1] - NH / 2) + ')' },
                'aid-node ' + colorClass(nd.color) + ' aid-node-' + nd.shape);
            g.appendChild(svg('rect', { x: 0, y: 0, width: NW, height: NH, rx: nd.shape === 'round' ? 22 : 6 }, 'aid-node-box'));
            wrapText(g, nd.label, NW / 2, NH / 2, NW - 14, 2);
            if (nd.detail) { var ti = svg('title'); ti.textContent = nd.detail; g.appendChild(ti); }
            root.appendChild(stageClass(g, nd.stage, stage));
        });
        return root;

        function clipToBox(a, b, hw, hh) {
            var dx = b[0] - a[0], dy = b[1] - a[1];
            var m = Math.max(Math.abs(dx) / hw, Math.abs(dy) / hh) || 1;
            return { a: [a[0] + dx / m, a[1] + dy / m], b: [b[0] - dx / m, b[1] - dy / m] };
        }
    };

    /* Naive greedy wrap. SVG has no flow text, and a concept-map node whose
     * label overflows its box is the single ugliest failure in a diagram. */
    function wrapText(parent, str, cx, cy, maxW, maxLines, cls, per) {
        var words = String(str).split(/\s+/), lines = [], cur = '';
        per = per || 6.4;   // approx px per char at the node font size
        words.forEach(function (w) {
            var probe = cur ? cur + ' ' + w : w;
            if (probe.length * per > maxW && cur) { lines.push(cur); cur = w; } else { cur = probe; }
        });
        if (cur) lines.push(cur);
        if (lines.length > maxLines) {
            lines = lines.slice(0, maxLines);
            lines[maxLines - 1] = lines[maxLines - 1].replace(/.{2}$/, '…');
        }
        // A single unbreakable word ("Condensation") cannot wrap, so truncate it
        // to what the box actually holds rather than letting it bleed out.
        var maxChars = Math.max(4, Math.floor(maxW / per));
        lines = lines.map(function (ln) {
            return ln.length > maxChars ? ln.slice(0, maxChars - 1) + '…' : ln;
        });
        var lh = per > 6 ? 14 : 12;
        var y0 = cy - ((lines.length - 1) * (lh / 2));
        lines.forEach(function (ln, i) {
            parent.appendChild(svgText(cx, y0 + i * lh + 4, ln, cls || 'aid-t-node'));
        });
    }

    RENDER.timeline = function (s, stage) {
        var W = 560, H = 210, PAD = 40, axisY = 112;
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');
        // Derive the span from the events when min/max are absent. The server
        // always supplies them, but a renderer that emits NaN coordinates fails
        // silently and invisibly — the worst possible failure for a diagram.
        var ats = s.events.map(function (e) { return e.at; }).filter(isFinite);
        var lo = isFinite(s.min) ? s.min : (ats.length ? Math.min.apply(null, ats) : 0);
        var hi = isFinite(s.max) ? s.max : (ats.length ? Math.max.apply(null, ats) : 1);
        if (!(hi > lo)) hi = lo + 1;
        var X = scale(lo, hi, PAD, W - PAD);
        root.appendChild(svg('line', { x1: PAD - 10, y1: axisY, x2: W - PAD + 10, y2: axisY }, 'aid-axis'));

        s.events.forEach(function (ev, i) {
            var above = i % 2 === 0;            // alternate sides so labels never collide
            var px = X(ev.at), ly = above ? axisY - 30 : axisY + 30;
            var g = svg('g', null, 'aid-event ' + colorClass(ev.color));
            g.appendChild(svg('line', { x1: px, y1: axisY, x2: px, y2: ly + (above ? 12 : -12) }, 'aid-event-stem'));
            g.appendChild(svg('circle', { cx: px, cy: axisY, r: 5 }, 'aid-dot'));
            g.appendChild(svgText(px, ly, ev.label, 'aid-t-sm aid-t-strong'));
            g.appendChild(svgText(px, above ? ly - 14 : ly + 15, fmt(ev.at) + (s.unit ? ' ' + s.unit : ''), 'aid-t-xs aid-t-muted'));
            if (ev.detail) { var ti = svg('title'); ti.textContent = ev.detail; g.appendChild(ti); }
            root.appendChild(stageClass(g, ev.stage, stage));
        });
        if (s.label) root.appendChild(svgText(W / 2, H - 6, s.label, 'aid-t-xs aid-t-muted'));
        return root;
    };

    /* Real <table>: gives wrapping, column semantics and screen-reader
     * navigation that an SVG grid cannot. */
    RENDER.table = function (s, stage) {
        var wrap = elem('div', 'aid-table-wrap');
        var t = elem('table', 'aid-table');
        var thead = elem('thead'), hr = elem('tr');
        s.columns.forEach(function (c) { var th = elem('th', null, c); th.setAttribute('scope', 'col'); hr.appendChild(th); });
        thead.appendChild(hr); t.appendChild(thead);
        var tb = elem('tbody');
        s.rows.forEach(function (row, ri) {
            var tr = elem('tr');
            row.forEach(function (cell, ci) {
                var isHead = s.row_header && ci === 0;
                var td = elem(isHead ? 'th' : 'td', null, cell);
                if (isHead) td.setAttribute('scope', 'row');
                if (s.highlight_cells.some(function (h) { return h[0] === ri && h[1] === ci; })) td.classList.add('aid-cell-hot');
                tr.appendChild(td);
            });
            tb.appendChild(tr);
        });
        t.appendChild(tb); wrap.appendChild(t);
        return wrap;
    };

    RENDER.venn = function (s, stage) {
        var three = s.sets.length >= 3;
        var W = 560, H = three ? 380 : 300;
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' }, 'aid-svg');
        var r = three ? 104 : 112;
        var cs = three
            ? [[W / 2 - 74, H / 2 - 44], [W / 2 + 74, H / 2 - 44], [W / 2, H / 2 + 62]]
            : [[W / 2 - 66, H / 2], [W / 2 + 66, H / 2]];

        s.sets.forEach(function (set, i) {
            root.appendChild(svg('circle', { cx: cs[i][0], cy: cs[i][1], r: r }, 'aid-venn-circle ' + colorClass(set.color || ('c' + (i + 1)))));
        });
        s.sets.forEach(function (set, i) {
            // Label pushed outward from the group centre so it clears the overlaps.
            var gx = cs.reduce(function (a, c) { return a + c[0]; }, 0) / cs.length;
            var gy = cs.reduce(function (a, c) { return a + c[1]; }, 0) / cs.length;
            var dx = cs[i][0] - gx, dy = cs[i][1] - gy, m = Math.hypot(dx, dy) || 1;
            root.appendChild(svgText(cs[i][0] + (dx / m) * (r * 0.72), cs[i][1] + (dy / m) * (r * 0.72) - 6, set.label, 'aid-t-sm aid-t-strong'));
            set.items.slice(0, 4).forEach(function (it, j) {
                root.appendChild(svgText(cs[i][0] + (dx / m) * (r * 0.62), cs[i][1] + (dy / m) * (r * 0.62) + 12 + j * 14, it, 'aid-t-xs'));
            });
        });
        s.overlaps.forEach(function (ov) {
            var pts = ov.sets.map(function (i) { return cs[i]; });
            var mx = pts.reduce(function (a, p) { return a + p[0]; }, 0) / pts.length;
            var my = pts.reduce(function (a, p) { return a + p[1]; }, 0) / pts.length;
            var g = svg('g', null, 'aid-venn-overlap');
            ov.items.slice(0, 4).forEach(function (it, j) { g.appendChild(svgText(mx, my + j * 14 - 6, it, 'aid-t-xs aid-t-strong')); });
            root.appendChild(stageClass(g, ov.stage, stage));
        });
        return root;
    };

    RENDER.cycle = function (s, stage) {
        // Sized near its display size for the same reason as geometry: a large
        // viewBox shrunk to fit would take the step labels with it.
        var n = s.steps.length, W = 340, H = 320, cx = W / 2, cy = H / 2, R = 102;
        var root = intrinsicSvg(W, H);
        var defs = svg('defs');
        var mk = svg('marker', { id: 'aid-cyc-arrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto' });
        mk.appendChild(svg('path', { d: 'M0,0 L10,5 L0,10 z' }, 'aid-arrow-head'));
        defs.appendChild(mk); root.appendChild(defs);

        var ang = function (i) { return (i / n) * Math.PI * 2 - Math.PI / 2; };
        for (var i = 0; i < n; i++) {
            // Arc from step i to step i+1, inset at both ends so it starts and
            // stops clear of the two node circles it connects.
            var a0 = ang(i) + 0.32, a1 = ang(i + 1) - 0.32;
            root.appendChild(svg('path', {
                d: 'M' + (cx + Math.cos(a0) * R) + ',' + (cy + Math.sin(a0) * R) +
                    ' A' + R + ',' + R + ' 0 0 1 ' + (cx + Math.cos(a1) * R) + ',' + (cy + Math.sin(a1) * R),
                fill: 'none', 'marker-end': 'url(#aid-cyc-arrow)'
            }, 'aid-cycle-arc'));
        }
        s.steps.forEach(function (st, i) {
            var a = ang(i), x = cx + Math.cos(a) * R, y = cy + Math.sin(a) * R;
            var g = svg('g', null, 'aid-cycle-step ' + colorClass(st.color));
            g.appendChild(svg('circle', { cx: x, cy: y, r: 43 }, 'aid-node-box'));
            // Smaller type and a wider box: cycle labels are single long words
            // ("Condensation", "Precipitation") far more often than node labels.
            // 'Precipitation' is 13 characters and must fit: at 5.4px/char in a
            // 70px box it truncated to 'Precipitati…'. Narrower metrics, wider box.
            wrapText(g, st.label, x, y, 76, 3, 'aid-t-cycle', 4.9);
            if (st.detail) { var ti = svg('title'); ti.textContent = st.detail; g.appendChild(ti); }
            root.appendChild(stageClass(g, st.stage, stage));
        });
        if (s.center_label) root.appendChild(svgText(cx, cy + 4, s.center_label, 'aid-t-sm aid-t-muted'));
        return root;
    };

    /* Ordered list, not SVG — steps are prose and must wrap and be selectable. */
    RENDER.steps = function (s, stage) {
        var list = elem(s.numbered ? 'ol' : 'ul', 'aid-steps');
        s.steps.forEach(function (st) {
            var li = elem('li', 'aid-step ' + colorClass(st.color));
            li.appendChild(elem('span', 'aid-step-label', st.label));
            if (st.detail) li.appendChild(elem('span', 'aid-step-detail', st.detail));
            stageClass(li, st.stage, stage);
            list.appendChild(li);
        });
        return list;
    };

    RENDER.fraction = function (s, stage) {
        var n = s.models.length, W = 560, unit = Math.min(240, (W - 40) / n), H = 0;
        var root = svg('svg', null, 'aid-svg');
        s.models.forEach(function (m, mi) {
            var g = svg('g', null, 'aid-fraction ' + colorClass(m.color));
            var ox = 20 + mi * unit, cy = 84;
            if (m.shape === 'circle') {
                var R = Math.min(64, unit / 2 - 14), cx = ox + unit / 2;
                for (var i = 0; i < m.parts; i++) {
                    var a0 = (i / m.parts) * Math.PI * 2 - Math.PI / 2, a1 = ((i + 1) / m.parts) * Math.PI * 2 - Math.PI / 2;
                    var big = (a1 - a0) > Math.PI ? 1 : 0;
                    var d = m.parts === 1
                        ? 'M' + (cx - R) + ',' + cy + ' a' + R + ',' + R + ' 0 1 0 ' + (R * 2) + ',0 a' + R + ',' + R + ' 0 1 0 ' + (-R * 2) + ',0'
                        : 'M' + cx + ',' + cy + ' L' + (cx + Math.cos(a0) * R) + ',' + (cy + Math.sin(a0) * R) +
                          ' A' + R + ',' + R + ' 0 ' + big + ' 1 ' + (cx + Math.cos(a1) * R) + ',' + (cy + Math.sin(a1) * R) + ' Z';
                    g.appendChild(svg('path', { d: d }, i < m.shaded ? 'aid-part aid-part-on' : 'aid-part'));
                }
                H = Math.max(H, 190);
            } else {
                var bw = unit - 30, bh = 62, pw = bw / m.parts;
                for (var j = 0; j < m.parts; j++) {
                    g.appendChild(svg('rect', { x: ox + 15 + j * pw, y: cy - bh / 2, width: pw, height: bh },
                        j < m.shaded ? 'aid-part aid-part-on' : 'aid-part'));
                }
                H = Math.max(H, 170);
            }
            if (s.show_labels) {
                g.appendChild(svgText(ox + unit / 2, 158, m.label || (m.shaded + '/' + m.parts), 'aid-t-lg aid-t-strong'));
            }
            root.appendChild(stageClass(g, m.stage, stage));
        });
        root.setAttribute('viewBox', '0 0 ' + W + ' ' + (H || 170));
        return root;
    };

    RENDER.image = function (s, stage) {
        var wrap = elem('div', 'aid-image-wrap');
        var img = document.createElement('img');
        img.className = 'aid-image aid-image--' + s.fit;
        img.loading = 'lazy';
        img.alt = '';                              // described by the card's own text
        img.src = s.src;                           // server-validated: data: or same-origin
        wrap.appendChild(img);
        s.pins.forEach(function (p) {
            var pin = elem('span', 'aid-pin');
            pin.style.left = (p.x * 100) + '%';
            pin.style.top = (p.y * 100) + '%';
            pin.appendChild(elem('span', 'aid-pin-dot', p.label));
            if (p.detail) { pin.title = p.detail; pin.appendChild(elem('span', 'aid-pin-detail', p.detail)); }
            stageClass(pin, p.stage, stage);
            wrap.appendChild(pin);
        });
        return wrap;
    };

    // -------------------------------------------------------------- the card
    var KIND_LABEL = {
        number_line: 'Number line', geometry: 'Figure', plot: 'Graph', bars: 'Chart',
        graph: 'Concept map', timeline: 'Timeline', table: 'Table', venn: 'Venn diagram',
        cycle: 'Cycle', steps: 'Steps', fraction: 'Fractions', image: 'Image'
    };
    var TIER_LABEL = {
        computed: 'Computed', retrieved: 'From source', authored: 'Sketch'
    };
    var TIER_TITLE = {
        computed: 'Drawn from exact values calculated here — the figure is accurate.',
        retrieved: 'Taken from a cited source.',
        authored: 'Drawn by the tutor from its own understanding, and not independently checked.'
    };

    function fetchSpec(id) {
        if (specCache.has(id)) return Promise.resolve(specCache.get(id));
        if (inflight.has(id)) return inflight.get(id);
        var p = fetch('/api/aid/' + encodeURIComponent(id), { credentials: 'same-origin' })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (aid) { specCache.set(id, aid); inflight.delete(id); return aid; })
            .catch(function (e) { inflight.delete(id); throw e; });
        inflight.set(id, p);
        return p;
    }

    /* Build the card frame immediately from the transcript descriptor — title,
     * caption and description need no network. The learner sees a real,
     * readable card while the spec is still in flight, and if it never arrives
     * the card is already complete without it. */
    function buildCard(desc) {
        var fig = elem('figure', 'aid-card');
        fig.dataset.aidId = desc.id;
        fig.dataset.kind = desc.kind;

        var head = elem('div', 'aid-head');
        head.appendChild(elem('span', 'aid-chip', KIND_LABEL[desc.kind] || 'Diagram'));
        if (desc.title) head.appendChild(elem('h4', 'aid-title', desc.title));
        var tools = elem('div', 'aid-tools');
        head.appendChild(tools);
        fig.appendChild(head);

        var canvas = elem('div', 'aid-canvas');
        canvas.appendChild(elem('div', 'aid-skeleton'));
        fig.appendChild(canvas);

        if (desc.caption) fig.appendChild(elem('figcaption', 'aid-caption', desc.caption));

        // The written description is a first-class part of the card, not a
        // hidden attribute: it is what a learner on audio hears, what a screen
        // reader announces, and what replaces the figure when rendering fails.
        var descPanel = elem('div', 'aid-desc');
        descPanel.hidden = true;
        descPanel.appendChild(elem('p', null, desc.alt || ''));
        fig.appendChild(descPanel);

        var foot = elem('div', 'aid-foot');
        var tier = elem('span', 'aid-tier aid-tier--' + (desc.tier || 'authored'),
            TIER_LABEL[desc.tier] || 'Sketch');
        tier.title = TIER_TITLE[desc.tier] || '';
        foot.appendChild(tier);
        foot.appendChild(elem('span', 'aid-stage-label'));
        fig.appendChild(foot);

        tools.appendChild(iconBtn('describe', 'Describe in words', function () {
            var open = descPanel.hidden;
            descPanel.hidden = !open;
            fig.classList.toggle('aid-describing', open);
        }));
        tools.appendChild(iconBtn('pin', 'Keep this on screen', function () { togglePin(desc); }));
        tools.appendChild(iconBtn('zoom', 'Enlarge', function () { openLightbox(fig, desc); }));

        // An unlabelled figure is unusable on a screen reader; the description
        // is the accessible name for the whole card.
        fig.setAttribute('role', 'group');
        fig.setAttribute('aria-label', (desc.title ? desc.title + '. ' : '') + (desc.alt || 'Diagram'));

        renderInto(fig, desc);
        return fig;
    }

    function iconBtn(kind, label, onClick) {
        var b = elem('button', 'aid-btn aid-btn--' + kind);
        b.type = 'button';
        b.title = label;
        b.setAttribute('aria-label', label);
        b.appendChild(elem('span', 'aid-btn-icon'));
        b.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); onClick(); });
        return b;
    }

    function renderInto(fig, desc) {
        var canvas = fig.querySelector('.aid-canvas');
        fetchSpec(desc.id).then(function (aid) {
            var draw = RENDER[aid.kind];
            if (!draw) throw new Error('no renderer for ' + aid.kind);
            var stage = Math.max(desc.stage || 0, aid.stage || 0);
            var node = draw(aid.spec, stage);
            canvas.textContent = '';
            canvas.appendChild(node);
            fig.dataset.stagesTotal = aid.stages_total || 0;
            fig.dataset.reveal = aid.reveal || 'tutor';
            applyStage(fig, stage);
        }).catch(function (e) {
            // The one thing that must not happen is a broken figure. Fall back
            // to the words, and say plainly that that is what happened.
            console.warn('[aids] render failed for ' + desc.id + ':', e);
            canvas.textContent = '';
            var fb = elem('div', 'aid-fallback');
            fb.appendChild(elem('p', 'aid-fallback-note', 'Diagram unavailable — here it is in words:'));
            fb.appendChild(elem('p', 'aid-fallback-text', desc.alt || 'No description available.'));
            canvas.appendChild(fb);
            fig.classList.add('aid-card--fallback');
        });
    }

    /* Reveal is a class toggle on already-drawn elements, so nothing reflows
     * and the learner's eye stays where it was. */
    function applyStage(fig, stage) {
        fig.querySelectorAll('.aid-staged').forEach(function (el) {
            var s = parseInt(el.getAttribute('data-stage'), 10) || 0;
            el.classList.toggle('aid-veiled', s > stage);
        });
        var total = parseInt(fig.dataset.stagesTotal, 10) || 0;
        var label = fig.querySelector('.aid-stage-label');
        var foot = fig.querySelector('.aid-foot');
        var existing = foot.querySelector('.aid-reveal');
        if (existing) existing.remove();

        if (total > 0) {
            label.textContent = 'Layer ' + (stage + 1) + ' of ' + (total + 1);
            if (stage < total) {
                if (fig.dataset.reveal === 'learner') {
                    var b = elem('button', 'aid-reveal', 'Reveal next layer');
                    b.type = 'button';
                    b.addEventListener('click', function () { requestReveal(fig, stage + 1); });
                    foot.appendChild(b);
                } else {
                    // Tutor-controlled: signal that there IS more without
                    // offering a button that would let the learner skip the
                    // thinking the next layer is meant to reward.
                    label.textContent += ' — more once you answer';
                }
            }
        } else {
            label.textContent = '';
        }
        fig.dataset.stage = stage;
    }

    function requestReveal(fig, stage) {
        applyStage(fig, stage);                    // optimistic; poll will confirm
        if (typeof window.sendEvent === 'function') {
            window.sendEvent('REVEAL_AID', { aid_id: fig.dataset.aidId, stage: stage });
        }
    }

    // ------------------------------------------------------------- lightbox
    function openLightbox(sourceFig, desc) {
        var prevFocus = document.activeElement;
        var ov = elem('div', 'aid-lightbox');
        ov.setAttribute('role', 'dialog');
        ov.setAttribute('aria-modal', 'true');
        ov.setAttribute('aria-label', desc.title || 'Diagram');

        var panel = elem('div', 'aid-lightbox-panel');
        var bar = elem('div', 'aid-lightbox-bar');
        bar.appendChild(elem('span', 'aid-lightbox-title', desc.title || KIND_LABEL[desc.kind] || 'Diagram'));
        var close = elem('button', 'aid-lightbox-close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', 'Close');
        bar.appendChild(close);
        panel.appendChild(bar);

        var body = elem('div', 'aid-lightbox-body');
        var drawn = sourceFig.querySelector('.aid-canvas > *');
        if (drawn) body.appendChild(drawn.cloneNode(true));
        panel.appendChild(body);
        if (desc.alt) panel.appendChild(elem('p', 'aid-lightbox-desc', desc.alt));
        ov.appendChild(panel);

        function shut() {
            ov.remove();
            document.removeEventListener('keydown', onKey);
            if (prevFocus && prevFocus.focus) prevFocus.focus();
        }
        function onKey(e) {
            if (e.key === 'Escape') { e.preventDefault(); shut(); }
            // Minimal focus trap: the dialog has exactly one control.
            if (e.key === 'Tab') { e.preventDefault(); close.focus(); }
        }
        close.addEventListener('click', shut);
        ov.addEventListener('click', function (e) { if (e.target === ov) shut(); });
        document.addEventListener('keydown', onKey);
        document.body.appendChild(ov);
        close.focus();
    }

    // ------------------------------------------------------------- pin rail
    /* A long dialogue scrolls the diagram you are reasoning about off the top of
     * the screen, right when you need it to answer. Pinning holds one aid in a
     * sticky rail above the conversation. */
    function railEl() {
        var rail = document.getElementById('aid-pin-rail');
        if (rail) return rail;
        var list = document.getElementById('chat-stream');
        if (!list || !list.parentNode) return null;
        rail = elem('div', 'aid-pin-rail');
        rail.id = 'aid-pin-rail';
        rail.hidden = true;
        list.parentNode.insertBefore(rail, list);
        return rail;
    }

    function togglePin(desc) {
        var cur = null;
        try { cur = sessionStorage.getItem(PIN_KEY); } catch (e) { /* private mode */ }
        if (cur === desc.id) { unpin(); return; }
        try { sessionStorage.setItem(PIN_KEY, desc.id); } catch (e) { /* ignore */ }
        drawPinned(desc);
    }

    function unpin() {
        try { sessionStorage.removeItem(PIN_KEY); } catch (e) { /* ignore */ }
        var rail = document.getElementById('aid-pin-rail');
        if (rail) { rail.textContent = ''; rail.hidden = true; }
        document.querySelectorAll('.aid-card.aid-pinned').forEach(function (c) { c.classList.remove('aid-pinned'); });
    }

    function drawPinned(desc) {
        var rail = railEl();
        if (!rail) return;
        rail.textContent = '';
        rail.hidden = false;
        var mini = elem('div', 'aid-pinned-card');
        var head = elem('div', 'aid-pinned-head');
        head.appendChild(elem('span', 'aid-chip', KIND_LABEL[desc.kind] || 'Diagram'));
        head.appendChild(elem('span', 'aid-pinned-title', desc.title || ''));
        var x = elem('button', 'aid-btn aid-btn--unpin');
        x.type = 'button';
        x.title = 'Unpin';
        x.setAttribute('aria-label', 'Unpin diagram');
        x.appendChild(elem('span', 'aid-btn-icon'));
        x.addEventListener('click', unpin);
        head.appendChild(x);
        mini.appendChild(head);
        var body = elem('div', 'aid-canvas aid-canvas--mini');
        mini.appendChild(body);
        rail.appendChild(mini);

        fetchSpec(desc.id).then(function (aid) {
            var draw = RENDER[aid.kind];
            if (!draw) return;
            body.textContent = '';
            body.appendChild(draw(aid.spec, Math.max(desc.stage || 0, aid.stage || 0)));
        }).catch(function () {
            body.appendChild(elem('p', 'aid-fallback-text', desc.alt || ''));
        });

        document.querySelectorAll('.aid-card').forEach(function (c) {
            c.classList.toggle('aid-pinned', c.dataset.aidId === desc.id);
        });
    }

    // ---------------------------------------------------------------- public
    window.HelgaAids = {
        /* Attach every aid on a message, above its text. Called by session.js. */
        attach: function (messageEl, aids) {
            if (!messageEl || !aids || !aids.length) return;
            var body = messageEl.querySelector('.chat-msg-body');
            var textEl = messageEl.querySelector('.chat-msg-text');
            if (!body) return;
            var holder = elem('div', 'aid-stack');
            aids.forEach(function (desc) {
                if (!desc || !desc.id) return;
                try {
                    holder.appendChild(buildCard(desc));
                } catch (e) {
                    console.warn('[aids] card build failed:', e);
                }
            });
            // ABOVE the text: the learner should meet the figure first and read
            // the question second, which is the order the tutor is teaching in.
            body.insertBefore(holder, textEl || body.firstChild);
            var pinned = null;
            try { pinned = sessionStorage.getItem(PIN_KEY); } catch (e) { /* ignore */ }
            aids.forEach(function (d) { if (d.id === pinned) drawPinned(d); });
        },

        /* Keep already-rendered cards in step with the polled transcript —
         * this is how a tutor-driven reveal reaches the screen. */
        syncStages: function (aids) {
            (aids || []).forEach(function (desc) {
                var fig = document.querySelector('.aid-card[data-aid-id="' + CSS.escape(desc.id) + '"]');
                if (!fig) return;
                var shown = parseInt(fig.dataset.stage, 10) || 0;
                if ((desc.stage || 0) > shown) applyStage(fig, desc.stage);
            });
        },

        /* What TTS should read after the message text. A learner working by ear
         * must not simply lose the diagram (B13.9). */
        speechFor: function (aids) {
            if (!aids || !aids.length) return '';
            return aids.map(function (a) { return a.alt || ''; })
                .filter(Boolean).join(' ');
        },

        unpin: unpin,
        _render: RENDER,          // exposed for tests
        _cache: specCache
    };
})();
