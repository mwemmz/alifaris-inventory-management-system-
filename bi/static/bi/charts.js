// Small dependency-free SVG charts, so they work offline on the shop server.
// Dark "Noir" look: smooth gradient areas, crosshair + glass tooltip, rounded columns.
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var COLORS = ['#e0303c', '#ff8a4c', '#f5b942', '#8d939b', '#3ddc97', '#c3c7cc', '#b81a26', '#6e747c'];
  var INK = '#eceef0', MUTED = '#8d939b', GRID = 'rgba(255,255,255,.07)', TRACK = 'rgba(255,255,255,.05)', uid = 0;
  function theme() { var s = getComputedStyle(document.documentElement), v = function (n, d) { return (s.getPropertyValue(n) || '').trim() || d; }; INK = v('--ink', INK); MUTED = v('--muted', MUTED); GRID = v('--grid', GRID); TRACK = v('--wash', TRACK); }
  theme();
  function el(n, a, parent) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]); if (parent) parent.appendChild(e); return e; }
  function fmt(n) { return Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k' : Math.round(n); }
  function full(n) { return Math.round(n).toLocaleString(); }
  function svg(box, w, h) { box.innerHTML = ''; box.style.position = 'relative'; return el('svg', {viewBox: '0 0 ' + w + ' ' + h, width: '100%', role: 'img', style: 'display:block;overflow:visible'}, box); }
  function tip(box) {
    var t = document.createElement('div');
    t.style.cssText = 'position:absolute;pointer-events:none;opacity:0;transform:translate(-50%,-110%);transition:opacity .12s ease;padding:7px 11px;border-radius:12px;font-size:12px;line-height:1.35;color:#14161a;background:#fff;box-shadow:0 10px 30px rgba(0,0,0,.55);white-space:nowrap;z-index:5';
    box.appendChild(t); return t;
  }
  function grad(s, id, c, a0, a1) {
    var d = el('defs', {}, s), g = el('linearGradient', {id: id, x1: 0, y1: 0, x2: 0, y2: 1}, d);
    el('stop', {offset: '0%', 'stop-color': c, 'stop-opacity': a0}, g); el('stop', {offset: '100%', 'stop-color': c, 'stop-opacity': a1}, g);
  }
  // smooth path through points (Catmull-Rom to Bezier), never overshoots below the baseline
  function smooth(p, base) {
    if (p.length < 3) return p.map(function (q, i) { return (i ? 'L' : 'M') + q[0] + ' ' + q[1]; }).join(' ');
    var d = 'M' + p[0][0] + ' ' + p[0][1];
    for (var i = 0; i < p.length - 1; i++) {
      var p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2, k = 0.18;
      var c1y = Math.min(p1[1] + (p2[1] - p0[1]) * k, base), c2y = Math.min(p2[1] - (p3[1] - p1[1]) * k, base);
      d += ' C' + (p1[0] + (p2[0] - p0[0]) * k) + ' ' + c1y + ',' + (p2[0] - (p3[0] - p1[0]) * k) + ' ' + c2y + ',' + p2[0] + ' ' + p2[1];
    }
    return d;
  }

  // points: [[label, value], ...]; future: optional dashed continuation
  window.lineChart = function (box, points, future) {
    var W = 720, H = 270, L = 44, B = 28, T = 14, all = points.concat(future || []), id = 'lg' + (++uid);
    if (!all.length) { box.textContent = 'No data'; return; }
    var s = svg(box, W, H), t = tip(box), max = Math.max.apply(null, all.map(function (p) { return p[1]; })) || 1;
    max *= 1.1;
    var x = function (i) { return L + i * (W - L - 10) / Math.max(all.length - 1, 1); }, y = function (v) { return T + (H - T - B) * (1 - v / max); }, base = y(0);
    grad(s, id, '#e0303c', 0.55, 0);
    for (var g = 0; g <= 4; g++) { var gv = max * g / 4;
      el('line', {x1: L, x2: W - 10, y1: y(gv), y2: y(gv), stroke: GRID, 'stroke-dasharray': g ? '3 5' : ''}, s);
      el('text', {x: L - 10, y: y(gv) + 4, 'text-anchor': 'end', 'font-size': 11, fill: MUTED}, s).textContent = fmt(gv); }
    var pts = points.map(function (p, i) { return [x(i), y(p[1])]; }), d = smooth(pts, base);
    el('path', {d: d + ' L' + x(points.length - 1) + ' ' + base + ' L' + x(0) + ' ' + base + 'Z', fill: 'url(#' + id + ')'}, s);
    el('path', {d: d, fill: 'none', stroke: '#ff5a66', 'stroke-width': 2.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', style: 'filter:drop-shadow(0 4px 10px rgba(224,48,60,.55))'}, s);
    if (future && future.length) {
      var f = [points[points.length - 1]].concat(future).map(function (p, i) { var k = points.length - 1 + i; return (i ? 'L' : 'M') + x(k) + ' ' + y(p[1]); }).join(' ');
      el('path', {d: f, fill: 'none', stroke: '#f5b942', 'stroke-width': 2.4, 'stroke-dasharray': '6 5', 'stroke-linecap': 'round'}, s);
    }
    var step = Math.ceil(all.length / 7);
    all.forEach(function (p, i) { if (i % step === 0) el('text', {x: x(i), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: MUTED}, s).textContent = p[0].slice(5); });
    // crosshair
    var vl = el('line', {y1: T, y2: base, stroke: MUTED, 'stroke-dasharray': '3 4', opacity: 0}, s);
    var halo = el('circle', {r: 9, fill: 'rgba(255,90,102,.25)', opacity: 0}, s), dot = el('circle', {r: 4.5, fill: '#fff', stroke: '#e0303c', 'stroke-width': 2.5, opacity: 0}, s);
    var last = points.length - 1; // resting marker on the latest point
    halo.setAttribute('cx', x(last)); halo.setAttribute('cy', y(points[last][1])); dot.setAttribute('cx', x(last)); dot.setAttribute('cy', y(points[last][1])); halo.setAttribute('opacity', 1); dot.setAttribute('opacity', 1);
    var hit = el('rect', {x: L, y: 0, width: W - L, height: H, fill: 'transparent', style: 'cursor:crosshair;touch-action:pan-y'}, s);
    function at(ev) {
      var r = s.getBoundingClientRect(), px = (ev.clientX - r.left) / r.width * W, i = Math.round((px - L) / ((W - L - 10) / Math.max(all.length - 1, 1)));
      i = Math.max(0, Math.min(all.length - 1, i)); var p = all[i], cx = x(i), cy = y(p[1]);
      vl.setAttribute('x1', cx); vl.setAttribute('x2', cx); vl.setAttribute('opacity', 1);
      halo.setAttribute('cx', cx); halo.setAttribute('cy', cy); dot.setAttribute('cx', cx); dot.setAttribute('cy', cy);
      t.innerHTML = '<div style="color:#6b7078;font-size:11px">' + p[0] + (i >= points.length ? ' · forecast' : '') + '</div><b style="font-size:14px;letter-spacing:-.01em">ZK ' + full(p[1]) + '</b>';
      t.style.left = Math.max(60, Math.min(box.clientWidth - 60, cx / W * box.clientWidth)) + 'px'; t.style.top = (cy / H * box.clientHeight - 6) + 'px'; t.style.opacity = 1;
    }
    hit.addEventListener('pointermove', at); hit.addEventListener('pointerdown', at);
    hit.addEventListener('pointerleave', function () { t.style.opacity = 0; vl.setAttribute('opacity', 0); var c = x(last), v = y(points[last][1]); halo.setAttribute('cx', c); halo.setAttribute('cy', v); dot.setAttribute('cx', c); dot.setAttribute('cy', v); });
  };

  // vertical columns, the tallest one lit: rows: [[label, value], ...]
  window.colChart = function (box, rows) {
    if (!rows.length) { box.textContent = 'No data'; return; }
    var W = 720, H = 250, L = 40, B = 28, T = 26, id = 'cg' + (++uid), s = svg(box, W, H), t = tip(box);
    var max = Math.max.apply(null, rows.map(function (r) { return r[1]; })) || 1, hi = rows.reduce(function (a, r, i) { return r[1] > rows[a][1] ? i : a; }, 0);
    max *= 1.12; var y = function (v) { return T + (H - T - B) * (1 - v / max); }, slot = (W - L - 6) / rows.length, bw = Math.min(46, slot * 0.58);
    grad(s, id, '#e0303c', 1, 0.45); grad(s, id + 'h', '#ff8a4c', 1, 0.55); grad(s, id + 'd', '#ffffff', 0.16, 0.05);
    for (var g = 0; g <= 3; g++) { var gv = max * g / 3; el('line', {x1: L, x2: W - 6, y1: y(gv), y2: y(gv), stroke: GRID, 'stroke-dasharray': g ? '3 5' : ''}, s); el('text', {x: L - 8, y: y(gv) + 4, 'text-anchor': 'end', 'font-size': 11, fill: MUTED}, s).textContent = fmt(gv); }
    rows.forEach(function (r, i) {
      var cx = L + slot * i + slot / 2, top = y(r[1]), h = Math.max(y(0) - top, 3), lit = i === hi;
      var b = el('rect', {x: cx - bw / 2, y: top, width: bw, height: h, rx: Math.min(9, bw / 2), fill: 'url(#' + id + (lit ? 'h' : r[1] ? '' : 'd') + ')'}, s);
      if (lit) { var tag = el('g', {}, s); el('rect', {x: cx - 30, y: top - 24, width: 60, height: 19, rx: 9.5, fill: '#ff8a4c'}, tag); el('text', {x: cx, y: top - 11, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: '#2a1206'}, tag).textContent = fmt(r[1]); }
      if (rows.length <= 14 || i % 2 === 0) el('text', {x: cx, y: H - 8, 'text-anchor': 'middle', 'font-size': 11, fill: MUTED}, s).textContent = r[0].replace(':00', '');
      var hitr = el('rect', {x: cx - slot / 2, y: 0, width: slot, height: H, fill: 'transparent'}, s);
      hitr.addEventListener('pointerenter', function () { b.setAttribute('opacity', .85); t.innerHTML = '<div style="color:#6b7078;font-size:11px">' + r[0] + '</div><b>ZK ' + full(r[1]) + '</b>'; t.style.left = (cx / W * box.clientWidth) + 'px'; t.style.top = (top / H * box.clientHeight - 4) + 'px'; t.style.opacity = 1; });
      hitr.addEventListener('pointerleave', function () { b.setAttribute('opacity', 1); t.style.opacity = 0; });
    });
  };

  // rows: [[label, value], ...], total in the middle, legend with shares on the right
  window.donutChart = function (box, rows) {
    rows = rows.filter(function (r) { return r[1] > 0; });
    var total = rows.reduce(function (a, r) { return a + r[1]; }, 0);
    if (!total) { box.textContent = 'No data'; return; }
    var s = svg(box, 380, 190), R = 78, r = 54, cx = 92, cy = 95, a0 = -Math.PI / 2, gap = rows.length > 1 ? 0.045 : 0;
    var c = function (a, rad) { return (cx + rad * Math.cos(a)) + ' ' + (cy + rad * Math.sin(a)); };
    rows.forEach(function (row, i) {
      var sweep = row[1] / total * Math.PI * 2, A0 = a0 + gap / 2, A1 = a0 + sweep - gap / 2; if (rows.length === 1) A1 = a0 + Math.PI * 2 - 0.001;
      var big = A1 - A0 > Math.PI ? 1 : 0, col = COLORS[i % COLORS.length];
      var p = el('path', {d: 'M' + c(A0, R) + ' A' + R + ' ' + R + ' 0 ' + big + ' 1 ' + c(A1, R) + ' L' + c(A1, r) + ' A' + r + ' ' + r + ' 0 ' + big + ' 0 ' + c(A0, r) + 'Z', fill: col, 'stroke-linejoin': 'round'}, s);
      el('title', {}, p).textContent = row[0] + ': ZK ' + full(row[1]) + ' (' + Math.round(row[1] / total * 100) + '%)';
      var y0 = 40 + i * 30;
      el('circle', {cx: 196, cy: y0 - 4, r: 5, fill: col}, s);
      el('text', {x: 210, y: y0, 'font-size': 13, fill: INK}, s).textContent = (row[0] || '').slice(0, 14);
      el('text', {x: 372, y: y0, 'text-anchor': 'end', 'font-size': 13, 'font-weight': 700, fill: INK}, s).textContent = Math.round(row[1] / total * 100) + '%';
      a0 += sweep;
    });
    el('text', {x: cx, y: cy - 2, 'text-anchor': 'middle', 'font-size': 10.5, fill: MUTED, 'letter-spacing': '.08em'}, s).textContent = 'TOTAL';
    el('text', {x: cx, y: cy + 17, 'text-anchor': 'middle', 'font-size': 18, 'font-weight': 700, fill: INK}, s).textContent = fmt(total);
  };

  window.barChart = function (box, rows) {
    if (!rows.length) { box.textContent = 'No data'; return; }
    var id = 'bg' + (++uid), W = 720, rowH = 30, H = rows.length * rowH + 8, s = svg(box, W, H), max = Math.max.apply(null, rows.map(function (r) { return r[1]; })) || 1;
    var d = el('defs', {}, s), g = el('linearGradient', {id: id}, d); el('stop', {offset: '0%', 'stop-color': '#e0303c'}, g); el('stop', {offset: '100%', 'stop-color': '#ff8a4c'}, g);
    rows.forEach(function (r, i) {
      var w = (W - 270) * r[1] / max;
      el('text', {x: 0, y: i * rowH + 19, 'font-size': 12.5, fill: INK}, s).textContent = (r[0] || '').slice(0, 30);
      el('rect', {x: 210, y: i * rowH + 7, width: W - 270, height: 14, rx: 7, fill: TRACK}, s);
      var b = el('rect', {x: 210, y: i * rowH + 7, width: Math.max(w, 6), height: 14, rx: 7, fill: 'url(#' + id + ')'}, s);
      el('title', {}, b).textContent = r[0] + ': ' + full(r[1]);
      el('text', {x: 216 + Math.max(w, 6) + 4, y: i * rowH + 19, 'font-size': 12, fill: MUTED}, s).textContent = fmt(r[1]);
    });
  };

  // tiny trend line for KPI cards: values: [n, n, ...]
  window.sparkline = function (box, values, color) {
    if (!values || values.length < 2) return;
    var W = 300, H = 46, id = 'sp' + (++uid), s = svg(box, W, H), max = Math.max.apply(null, values) || 1, min = Math.min.apply(null, values);
    s.setAttribute('preserveAspectRatio', 'none'); s.style.height = '100%';
    var y = function (v) { return 6 + (H - 12) * (1 - (v - min) / Math.max(max - min, 1)); }, pts = values.map(function (v, i) { return [i * W / (values.length - 1), y(v)]; });
    grad(s, id, color || '#fff', 0.35, 0);
    var d = smooth(pts, H);
    el('path', {d: d + ' L' + W + ' ' + H + ' L0 ' + H + 'Z', fill: 'url(#' + id + ')'}, s);
    el('path', {d: d, fill: 'none', stroke: color || '#fff', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke', 'stroke-linecap': 'round', opacity: .9}, s);
  };

  // remember every chart so it can be redrawn with the new colours when the theme flips
  var reg = [];
  ['lineChart', 'colChart', 'donutChart', 'barChart', 'sparkline'].forEach(function (n) {
    var f = window[n];
    window[n] = function (box) { var a = arguments; reg = reg.filter(function (r) { return r.box !== box; }); reg.push({f: f, box: box, a: a}); theme(); return f.apply(null, a); };
  });
  window.addEventListener('themechange', function () { theme(); reg.forEach(function (r) { if (document.body.contains(r.box)) r.f.apply(null, r.a); }); });
})();
