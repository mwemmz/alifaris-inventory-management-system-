// Small dependency-free SVG charts, so they work offline on the shop server.
(function () {
  var NS = 'http://www.w3.org/2000/svg', COLORS = ['#017e84', '#714b67', '#e8913a', '#2f8df5', '#c0392b', '#7f8c8d', '#27ae60', '#8e44ad'];
  function el(n, a, parent) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]); if (parent) parent.appendChild(e); return e; }
  function fmt(n) { return Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : Math.round(n); }
  function svg(box, w, h) { box.innerHTML = ''; return el('svg', {viewBox: '0 0 ' + w + ' ' + h, width: '100%', role: 'img'}, box); }
  function tip(box) { var t = document.createElement('div'); t.style.cssText = 'position:absolute;background:#1f2937;color:#fff;padding:3px 8px;border-radius:4px;font-size:12px;pointer-events:none;display:none';
    box.style.position = 'relative'; box.appendChild(t); return t; }

  // points: [[label, value], ...]; future: optional dashed continuation
  window.lineChart = function (box, points, future) {
    var W = 720, H = 240, L = 46, B = 26, T = 10, all = points.concat(future || []);
    if (!all.length) { box.textContent = 'No data'; return; }
    var s = svg(box, W, H), t = tip(box), max = Math.max.apply(null, all.map(function (p) { return p[1]; })) || 1;
    var x = function (i) { return L + i * (W - L - 8) / Math.max(all.length - 1, 1); }, y = function (v) { return T + (H - T - B) * (1 - v / max); };
    for (var g = 0; g <= 4; g++) { var gv = max * g / 4;
      el('line', {x1: L, x2: W - 8, y1: y(gv), y2: y(gv), stroke: '#e5e7eb'}, s);
      el('text', {x: L - 6, y: y(gv) + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#6b7280'}, s).textContent = fmt(gv); }
    var d = points.map(function (p, i) { return (i ? 'L' : 'M') + x(i) + ' ' + y(p[1]); }).join(' ');
    el('path', {d: d + ' L' + x(points.length - 1) + ' ' + y(0) + ' L' + x(0) + ' ' + y(0) + 'Z', fill: '#017e8422'}, s);
    el('path', {d: d, fill: 'none', stroke: '#017e84', 'stroke-width': 2.5}, s);
    if (future && future.length) {
      var f = [points[points.length - 1]].concat(future).map(function (p, i) { var k = points.length - 1 + i; return (i ? 'L' : 'M') + x(k) + ' ' + y(p[1]); }).join(' ');
      el('path', {d: f, fill: 'none', stroke: '#e8913a', 'stroke-width': 2.5, 'stroke-dasharray': '6 4'}, s);
    }
    var step = Math.ceil(all.length / 8);
    all.forEach(function (p, i) {
      if (i % step === 0) el('text', {x: x(i), y: H - 8, 'text-anchor': 'middle', 'font-size': 11, fill: '#6b7280'}, s).textContent = p[0].slice(5);
      if (all.length <= 40) el('circle', {cx: x(i), cy: y(p[1]), r: 3, fill: i >= points.length ? '#e8913a' : '#017e84'}, s);
      var c = el('circle', {cx: x(i), cy: y(p[1]), r: 8, fill: 'transparent'}, s);
      c.addEventListener('mouseenter', function () { t.style.display = 'block'; t.textContent = p[0] + (i >= points.length ? ' (forecast)' : '') + ': ' + Math.round(p[1]).toLocaleString();
        t.style.left = (x(i) / W * box.clientWidth + 8) + 'px'; t.style.top = (y(p[1]) / H * box.clientHeight - 24) + 'px'; });
      c.addEventListener('mouseleave', function () { t.style.display = 'none'; });
    });
  };

  // rows: [[label, value], ...]
  window.donutChart = function (box, rows) {
    rows = rows.filter(function (r) { return r[1] > 0; });
    var total = rows.reduce(function (a, r) { return a + r[1]; }, 0);
    if (!total) { box.textContent = 'No data'; return; }
    var s = svg(box, 360, 200), R = 80, r = 48, cx = 100, cy = 100, a0 = -Math.PI / 2;
    rows.forEach(function (row, i) {
      var a1 = a0 + row[1] / total * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0, c = function (a, rad) { return (cx + rad * Math.cos(a)) + ' ' + (cy + rad * Math.sin(a)); };
      if (rows.length === 1) a1 = a0 + Math.PI * 2 - 0.001;
      var p = el('path', {d: 'M' + c(a0, R) + ' A' + R + ' ' + R + ' 0 ' + big + ' 1 ' + c(a1, R) + ' L' + c(a1, r) + ' A' + r + ' ' + r + ' 0 ' + big + ' 0 ' + c(a0, r) + 'Z', fill: COLORS[i % COLORS.length]}, s);
      el('title', {}, p).textContent = row[0] + ': ' + Math.round(row[1]).toLocaleString() + ' (' + Math.round(row[1] / total * 100) + '%)';
      el('rect', {x: 205, y: 14 + i * 22, width: 11, height: 11, fill: COLORS[i % COLORS.length]}, s);
      el('text', {x: 221, y: 24 + i * 22, 'font-size': 12, fill: '#1f2937'}, s).textContent = (row[0] || '').slice(0, 20) + ' ' + Math.round(row[1] / total * 100) + '%';
      a0 = a1;
    });
  };

  window.barChart = function (box, rows) {
    if (!rows.length) { box.textContent = 'No data'; return; }
    var W = 720, rowH = 26, H = rows.length * rowH + 8, s = svg(box, W, H), max = Math.max.apply(null, rows.map(function (r) { return r[1]; })) || 1;
    rows.forEach(function (r, i) {
      var w = (W - 260) * r[1] / max;
      el('text', {x: 0, y: i * rowH + 18, 'font-size': 12, fill: '#1f2937'}, s).textContent = (r[0] || '').slice(0, 30);
      var b = el('rect', {x: 210, y: i * rowH + 5, width: Math.max(w, 2), height: 16, rx: 3, fill: '#017e84'}, s);
      el('title', {}, b).textContent = r[0] + ': ' + Math.round(r[1]).toLocaleString();
      el('text', {x: 216 + w, y: i * rowH + 18, 'font-size': 12, fill: '#6b7280'}, s).textContent = fmt(r[1]);
    });
  };
})();
