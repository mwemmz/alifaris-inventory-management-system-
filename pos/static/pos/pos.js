(function () {
  var till = document.getElementById('till');
  if (!till) return;
  var $ = function (id) { return document.getElementById(id); };
  var SESSION = till.dataset.session, MANAGER = till.dataset.manager === '1', MAXDISC = +till.dataset.maxdisc;
  var csrf = function () { var m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ''; };
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };
  var uid = function () {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); });
  };
  var money = function (n) { return (Math.round(n * 100) / 100).toFixed(2); };

  var catalog = store.get('pos.catalog', {products: [], inclusive: true, rates: {A: 16, B: 16}});
  var cart = [], tier = 'primary';

  function loadCatalog() {
    return fetch(till.dataset.catalog, {credentials: 'same-origin'}).then(function (r) { return r.json(); })
      .then(function (d) { catalog = d; store.set('pos.catalog', d); render(); }).catch(function () {});
  }
  function unitPrice(p) { return (tier === 'secondary' && p.price2) ? +p.price2 : +p.price; }
  function lineCalc(l) {
    var p = byId(l.id), rate = +(catalog.rates[p.tax] || 0), unit = l.price != null ? +l.price : unitPrice(p);
    var gross = Math.round(l.qty * unit * (100 - l.discount)) / 100, tax, total;
    if (catalog.inclusive) { tax = gross * rate / (100 + rate); total = gross; } else { tax = gross * rate / 100; total = gross + tax; }
    return {unit: unit, total: Math.round(total * 100) / 100, tax: Math.round(tax * 100) / 100};
  }
  function byId(id) { return catalog.products.filter(function (p) { return p.id === id; })[0]; }

  function renderGrid() {
    var q = $('q').value.trim().toLowerCase(), out = '';
    var list = catalog.products.filter(function (p) {
      return !q || p.name.toLowerCase().indexOf(q) > -1 || p.ref.toLowerCase().indexOf(q) > -1 || p.barcode === q; }).slice(0, 60);
    list.forEach(function (p) {
      var low = p.stock !== null && +p.stock <= 0;
      out += '<button class="tile' + (low ? ' out' : '') + '" data-id="' + p.id + '"><small>' + esc(p.ref) + '</small><br>' + esc(p.name) +
        '<b>' + money(unitPrice(p)) + '</b><small>' + (p.stock === null ? 'service' : 'On hand ' + (+p.stock)) + '</small></button>';
    });
    $('grid').innerHTML = out || '<p>No products match.</p>';
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }

  function render() {
    renderGrid();
    var html = '', total = 0, vat = 0;
    cart.forEach(function (l, i) {
      var p = byId(l.id), c = lineCalc(l); total += c.total; vat += c.tax;
      html += '<div class="row"><div>' + esc(p.name) + '<br><small>' + esc(p.ref) + ' @ ' + money(c.unit) + '</small></div><div class="r"><b>' + money(c.total) + '</b></div>' +
        '<div class="ctl"><button class="btn" data-a="dec" data-i="' + i + '">−</button>' +
        '<input type="number" step="any" min="0" value="' + l.qty + '" data-a="qty" data-i="' + i + '">' +
        '<button class="btn" data-a="inc" data-i="' + i + '">+</button>' +
        '<input type="number" min="0" max="' + (MANAGER ? 100 : MAXDISC) + '" value="' + l.discount + '" data-a="disc" data-i="' + i + '" title="Discount %" style="width:64px">%' +
        '<button class="btn" data-a="del" data-i="' + i + '">✕</button></div></div>';
    });
    $('cart').innerHTML = html || '<p style="color:#6b7280;padding:12px">Scan or tap a product to start.</p>';
    $('total').textContent = money(total); $('vat').textContent = money(vat);
    $('pay').disabled = !cart.length;
    $('tier').textContent = tier === 'secondary' ? 'Secondary price' : 'Price';
    till._total = Math.round(total * 100) / 100;
  }
  function add(id) {
    var l = cart.filter(function (x) { return x.id === id; })[0];
    if (l) l.qty += 1; else cart.push({id: id, qty: 1, discount: 0});
    render();
  }

  $('grid').addEventListener('click', function (e) { var t = e.target.closest('.tile'); if (t) { add(+t.dataset.id); $('q').focus(); } });
  $('q').addEventListener('input', renderGrid);
  $('q').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    var v = $('q').value.trim().toLowerCase(); if (!v) return;
    var hit = catalog.products.filter(function (p) { return p.barcode.toLowerCase() === v || p.ref.toLowerCase() === v; });
    if (!hit.length) { var m = catalog.products.filter(function (p) { return p.name.toLowerCase().indexOf(v) > -1; }); if (m.length === 1) hit = m; }
    if (hit.length) { add(hit[0].id); $('q').value = ''; renderGrid(); } else { $('q').select(); }
  });
  $('cart').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-a]'); if (!b) return; var i = +b.dataset.i;
    if (b.dataset.a === 'inc') cart[i].qty += 1;
    if (b.dataset.a === 'dec') cart[i].qty = Math.max(0, cart[i].qty - 1);
    if (b.dataset.a === 'del') cart.splice(i, 1);
    cart = cart.filter(function (l) { return l.qty > 0; }); render();
  });
  $('cart').addEventListener('change', function (e) {
    var el = e.target; if (!el.dataset.a) return; var i = +el.dataset.i, v = parseFloat(el.value) || 0;
    if (el.dataset.a === 'qty') cart[i].qty = v;
    if (el.dataset.a === 'disc') cart[i].discount = Math.min(v, MANAGER ? 100 : MAXDISC);
    cart = cart.filter(function (l) { return l.qty > 0; }); render();
  });
  $('tier').addEventListener('click', function () { tier = tier === 'primary' ? 'secondary' : 'primary'; render(); });
  $('clear').addEventListener('click', function () { cart = []; render(); });

  // ---- payment
  var pays = [];
  function renderPay() {
    var due = till._total, paid = pays.reduce(function (s, p) { return s + (+p.amount || 0); }, 0);
    $('due').textContent = '— due ' + money(due);
    $('paylines').innerHTML = pays.map(function (p, i) {
      return '<div class="payline"><select data-i="' + i + '" data-f="method">' +
        [['cash', 'Cash'], ['card', 'Card'], ['mobile', 'Mobile money'], ['bank', 'Bank transfer'], ['credit', 'On account']].map(function (m) {
          return '<option value="' + m[0] + '"' + (m[0] === p.method ? ' selected' : '') + '>' + m[1] + '</option>'; }).join('') +
        '</select><input type="number" step="0.01" min="0" inputmode="decimal" value="' + p.amount + '" data-i="' + i + '" data-f="amount"><button class="btn" data-del="' + i + '">✕</button></div>';
    }).join('');
    $('paid').textContent = money(paid);
    var diff = paid - due; $('chg').textContent = diff >= 0 ? 'Change ' + money(diff) : 'Short ' + money(-diff);
  }
  function openPay() { pays = [{method: 'cash', amount: money(till._total)}]; $('payerr').hidden = true; $('paydlg').hidden = false; renderPay(); }
  $('pay').addEventListener('click', function () { if (cart.length) openPay(); });
  $('paycancel').addEventListener('click', function () { $('paydlg').hidden = true; });
  $('paylines').addEventListener('input', function (e) { var el = e.target, i = +el.dataset.i; if (el.dataset.f) { pays[i][el.dataset.f] = el.value; renderPay2(); } });
  function renderPay2() { var paid = pays.reduce(function (s, p) { return s + (+p.amount || 0); }, 0), diff = paid - till._total;
    $('paid').textContent = money(paid); $('chg').textContent = diff >= 0 ? 'Change ' + money(diff) : 'Short ' + money(-diff); }
  $('paylines').addEventListener('change', function (e) { if (e.target.dataset.f === 'method') renderPay(); });
  $('paylines').addEventListener('click', function (e) { var d = e.target.dataset.del; if (d !== undefined) { pays.splice(+d, 1); renderPay(); } });
  document.querySelector('.paybtns').innerHTML = '<button class="btn" id="addpay">+ Split payment</button>';
  $('addpay').addEventListener('click', function () {
    var paid = pays.reduce(function (s, p) { return s + (+p.amount || 0); }, 0);
    pays.push({method: 'card', amount: money(Math.max(0, till._total - paid))}); renderPay(); });

  // ---- sending and offline queue
  function queue() { return store.get('pos.queue', []); }
  function showQueue() { var n = queue().length; $('queued').textContent = n ? ' · ' + n + ' sale(s) waiting to sync' : ''; }
  function post(sale) {
    return fetch(till.dataset.sale, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify(sale)})
      .then(function (r) { return r.json().then(function (j) { return {status: r.status, body: j}; }); });
  }
  function flush() {
    var q = queue(); if (!q.length) return showQueue();
    var sale = q[0];
    post(sale).then(function (r) {
      if (r.body.ok || r.status === 422) { var rest = queue().filter(function (s) { return s.uuid !== sale.uuid; }); store.set('pos.queue', rest);
        if (!r.body.ok) { var bad = store.get('pos.rejected', []); bad.push({sale: sale, error: r.body.error}); store.set('pos.rejected', bad); }
        showQueue(); flush(); }
    }).catch(function () {});
  }
  setInterval(flush, 15000); window.addEventListener('online', flush);
  function net() { var on = navigator.onLine; $('net').className = 'dot' + (on ? '' : ' off'); $('netlabel').textContent = on ? 'Online' : 'Offline - sales are saved on this device'; }
  window.addEventListener('online', net); window.addEventListener('offline', net); net(); showQueue();

  $('payok').addEventListener('click', function () {
    var paid = pays.reduce(function (s, p) { return s + (+p.amount || 0); }, 0);
    if (paid + 0.001 < till._total) { $('payerr').textContent = 'Payment is short by ' + money(till._total - paid) + '.'; $('payerr').hidden = false; return; }
    var sale = {uuid: uid(), session: SESSION, tier: tier, created_at: new Date().toISOString(),
      customer: $('cust').value || null, lines: cart.map(function (l) { var o = {product: l.id, qty: l.qty, discount: l.discount}; return o; }),
      payments: pays.map(function (p) { return {method: p.method, amount: +p.amount || 0}; }).filter(function (p) { return p.amount > 0; })};
    $('payok').disabled = true;
    var total = till._total;
    function finish(msg, link) {
      $('payok').disabled = false; $('paydlg').hidden = true; $('donetxt').textContent = msg;
      $('printlink').style.display = link ? '' : 'none'; if (link) $('printlink').href = link + '?print=1';
      $('done').hidden = false; cart = []; $('cust').value = ''; render(); }
    post(sale).then(function (r) {
      if (r.body.ok) {
        finish(r.body.number + ' — total ' + money(total) + (+r.body.change ? ', change ' + r.body.change : ''), r.body.receipt);
        loadCatalog();
      } else if (r.status === 422 || r.status === 400) {
        $('payok').disabled = false; $('payerr').textContent = r.body.error; $('payerr').hidden = false;
      } else { throw new Error(r.body.error || 'server'); }
    }).catch(function () {
      var q = queue(); q.push(sale); store.set('pos.queue', q); showQueue();
      finish('No connection to the server. The sale is saved on this device and will sync automatically. Total ' + money(total) + '.', null);
    });
  });
  $('next').addEventListener('click', function () { $('done').hidden = true; $('q').focus(); });

  loadCatalog().then(flush); render();
})();
