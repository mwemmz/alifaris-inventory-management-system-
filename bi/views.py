import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.http import HttpResponse
from django.shortcuts import redirect, render

from inventory.models import Product, ProductCategory
from inventory.services import stock
from pos.models import PosOrder, PosOrderLine

ZERO = Decimal("0")


def _dec(v):
    try:
        return Decimal(v) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _date(v, default):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return default


def _bars(rows, key="total"):
    top = max([abs(r[key] or 0) for r in rows] or [1]) or 1
    for r in rows:
        r["pct"] = round(abs(r[key] or 0) / top * 100)
    return rows


def _category_ids(cid):
    """A category and everything under it, so picking 'Tyres' also covers 'Tyres / Truck'."""
    ids, todo = set(), [cid]
    while todo:
        c = todo.pop()
        if c in ids:
            continue
        ids.add(c)
        todo += list(ProductCategory.objects.filter(parent_id=c).values_list("id", flat=True))
    return ids


@login_required
def sales(request):
    if not request.user.is_staff:
        return redirect("/")
    g = request.GET
    today = date.today()
    d_from, d_to = _date(g.get("from"), today - timedelta(days=29)), _date(g.get("to"), today)
    lines = PosOrderLine.objects.filter(order__created_at__date__gte=d_from, order__created_at__date__lte=d_to)
    if g.get("category"):
        lines = lines.filter(product__category_id__in=_category_ids(int(g["category"])))
    if q := g.get("q", "").strip():
        lines = lines.filter(Q(name__icontains=q) | Q(ref__icontains=q) | Q(product__barcode=q))
    if (lo := _dec(g.get("min_price"))) is not None:
        lines = lines.filter(unit_price__gte=lo)
    if (hi := _dec(g.get("max_price"))) is not None:
        lines = lines.filter(unit_price__lte=hi)
    if g.get("cashier"):
        lines = lines.filter(order__cashier_name=g["cashier"])
    if g.get("site"):
        lines = lines.filter(order__site_code=g["site"])
    if g.get("customer"):
        lines = lines.filter(order__customer_name__icontains=g["customer"])
    if g.get("tier"):
        lines = lines.filter(order__price_tier=g["tier"])
    if g.get("pay"):
        lines = lines.filter(order__payments__method=g["pay"]).distinct()

    cost = ExpressionWrapper(F("qty") * F("product__cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    agg = dict(total=Sum("line_total"), units=Sum("qty"), tax=Sum("tax_amount"), cost=Sum(cost))

    def grouped(*fields, order="-total", limit=None):
        rows = list(lines.values(*fields).annotate(**agg).order_by(order)[:limit])
        for r in rows:
            r["total"], r["cost"] = r["total"] or ZERO, r["cost"] or ZERO
            r["margin"] = r["total"] - r["tax"] - r["cost"] if r["tax"] is not None else r["total"] - r["cost"]
        return _bars(rows)

    grain = {"week": TruncWeek, "month": TruncMonth}.get(g.get("by"), TruncDate)
    series = list(lines.annotate(d=grain("order__created_at")).values("d").annotate(**agg).order_by("d"))
    for r in series:
        r["total"] = r["total"] or ZERO
    _bars(series)
    kpi = lines.aggregate(**agg)
    kpi = {k: (v or ZERO) for k, v in kpi.items()}
    kpi["net"] = kpi["total"] - kpi["tax"]
    kpi["margin"] = kpi["net"] - kpi["cost"]
    kpi["margin_pct"] = round(kpi["margin"] / kpi["net"] * 100, 1) if kpi["net"] else 0
    kpi["orders"] = lines.values("order").distinct().count()
    kpi["avg"] = round(kpi["total"] / kpi["orders"], 2) if kpi["orders"] else 0

    by_product = grouped("ref", "name", limit=None if g.get("export") else 25)
    if g.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="sales_{d_from}_{d_to}.csv"'
        w = csv.writer(resp)
        w.writerow(["Ref", "Product", "Qty", "Sales incl VAT", "VAT", "Est. cost", "Est. margin"])
        for r in by_product:
            w.writerow([r["ref"], r["name"], r["units"], r["total"], r["tax"], r["cost"], r["margin"]])
        return resp

    ctx = {"title": "Sales analysis", "g": g, "d_from": d_from, "d_to": d_to, "kpi": kpi, "series": series,
           "by_category": grouped("product__category__name"), "by_product": by_product,
           "by_cashier": grouped("order__cashier_name"), "by_site": grouped("order__site_code"),
           "categories": ProductCategory.objects.all(),
           "cashiers": PosOrder.objects.order_by().values_list("cashier_name", flat=True).distinct(),
           "sites": PosOrder.objects.order_by().values_list("site_code", flat=True).distinct(), "tab": "sales"}
    return render(request, "bi/sales.html", ctx)


@login_required
def stock_view(request):
    if not request.user.is_staff:
        return redirect("/")
    g = request.GET
    qs = Product.objects.filter(active=True).select_related("category")
    if g.get("category"):
        qs = qs.filter(category_id__in=_category_ids(int(g["category"])))
    if q := g.get("q", "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(internal_ref__icontains=q))
    if (lo := _dec(g.get("min_price"))) is not None:
        qs = qs.filter(price__gte=lo)
    if (hi := _dec(g.get("max_price"))) is not None:
        qs = qs.filter(price__lte=hi)
    rows, cats = [], {}
    for p in qs:
        if not p.tracks_stock:
            continue
        oh = p.on_hand
        if g.get("view") == "low" and oh > p.min_qty:
            continue
        if g.get("view") == "zero" and oh > 0:
            continue
        val = oh * p.cost
        rows.append({"p": p, "on_hand": oh, "value": val, "retail": oh * p.price})
        c = cats.setdefault(p.category.name if p.category else "Uncategorised", {"name": p.category.name if p.category else "Uncategorised", "total": ZERO, "n": 0})
        c["total"] += val
        c["n"] += 1
    rows.sort(key=lambda r: -r["value"])
    if g.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="stock.csv"'
        w = csv.writer(resp)
        w.writerow(["Ref", "Product", "Category", "On hand", "Cost", "Price", "Stock value", "Retail value"])
        for r in rows:
            w.writerow([r["p"].internal_ref, r["p"].name, r["p"].category or "", r["on_hand"], r["p"].cost, r["p"].price, r["value"], r["retail"]])
        return resp
    return render(request, "bi/stock.html", {
        "title": "Stock analysis", "g": g, "tab": "stock", "rows": rows[:200], "count": len(rows),
        "by_category": _bars(sorted(cats.values(), key=lambda c: -c["total"])),
        "value": sum((r["value"] for r in rows), ZERO), "retail": sum((r["retail"] for r in rows), ZERO),
        "categories": ProductCategory.objects.all()})
