"""Answer plain-English questions straight from the database. No outside service, no API key.

It recognises a fixed set of question types (sales, top sellers, stock, prices, cashiers, refunds, cash-ups ...),
pulls out dates, departments and product names, and runs read-only queries. It never writes SQL from the text,
so a badly worded question can only fail to match, never touch data.
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from inventory.models import Product, ProductCategory
from pos.models import PosOrder, PosOrderLine, PosPayment, PosSession

from . import insights

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
STOP = set("""the a an of for on in at to is are was were do does did we you i me my our how what which who when where much many
much show tell give list me please total all any with by from this that last next week month today yesterday year days day than
sales sale sold selling revenue top best worst most least stock price cost have has had left available units unit quantity items item
product products department category categories cashier cashiers staff refund refunds""".split())


def money(v):
    return f"{Decimal(v or 0):,.2f}"


def parse_range(q):
    t, today = q.lower(), date.today()
    if "yesterday" in t:
        return today - timedelta(days=1), today - timedelta(days=1), "yesterday"
    if "today" in t:
        return today, today, "today"
    if "last week" in t:
        end = today - timedelta(days=today.weekday() + 1)
        return end - timedelta(days=6), end, "last week"
    if "this week" in t:
        return today - timedelta(days=today.weekday()), today, "this week"
    if "last month" in t:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, "last month"
    if "this month" in t:
        return today.replace(day=1), today, "this month"
    if "this year" in t:
        return today.replace(month=1, day=1), today, "this year"
    m = re.search(r"(?:last|past)\s+(\d+)\s+days", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today, f"the last {n} days"
    for i, name in enumerate(MONTHS, 1):
        if re.search(rf"\b{name}\b", t):
            y = today.year if i <= today.month else today.year - 1
            start = date(y, i, 1)
            end = (date(y + (i == 12), i % 12 + 1, 1)) - timedelta(days=1)
            return start, end, f"{name.title()} {y}"
    return today - timedelta(days=29), today, "the last 30 days"


def find_category(q):
    t = q.lower()
    for c in sorted(ProductCategory.objects.all(), key=lambda c: -len(c.name)):
        if c.name.lower() in t:
            return c
    return None


def find_products(q, category=None):
    words = [w for w in re.findall(r"[a-z0-9./-]+", q.lower()) if w not in STOP and len(w) > 1 and w not in MONTHS]
    if category:
        words = [w for w in words if w not in category.name.lower()]
    if not words:
        return [], ""
    qs = Product.objects.filter(active=True)
    for w in words:
        qs = qs.filter(Q(name__icontains=w) | Q(internal_ref__iexact=w) | Q(barcode=w))
    return list(qs[:15]), " ".join(words)


def _lines(d1, d2, category=None, product=None, cashier=None):
    qs = PosOrderLine.objects.filter(order__created_at__date__gte=d1, order__created_at__date__lte=d2)
    if category:
        qs = qs.filter(product__category__name__iexact=category.name)
    if product:
        qs = qs.filter(product=product)
    if cashier:
        qs = qs.filter(order__cashier_name__iexact=cashier)
    return qs


def has(t, *words):
    return any(re.search(rf"\b{re.escape(w)}", t) for w in words)


def answer(question):
    """Returns {"answer": str, "table": {"cols": [...], "rows": [[...]]} or None} or None if not understood."""
    q = question.strip()
    t = q.lower()
    d1, d2, label = parse_range(q)
    cat = find_category(q)
    cost = ExpressionWrapper(F("qty") * F("product__cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
    scope = f" in {cat.name}" if cat else ""

    if has(t, "out of stock", "zero stock", "no stock", "finished"):
        rows = [[p.internal_ref, p.name, money(p.price)] for p in Product.objects.filter(active=True) if p.tracks_stock and p.on_hand <= 0 and (not cat or p.category_id == cat.id)][:50]
        return {"answer": f"{len(rows)} product(s) out of stock{scope}" + (" (first 50)." if len(rows) == 50 else "."), "table": {"cols": ["Ref", "Product", "Price"], "rows": rows}}

    if has(t, "reorder", "re-order", "running low", "low stock", "running out", "restock", "need to order", "should i order", "what to order"):
        r = insights.reorder_suggestions()
        if cat:
            r = [x for x in r if x["p"].category_id == cat.id]
        return {"answer": f"{len(r)} product(s) will run out within {insights.LEAD_DAYS} days at their recent selling speed{scope}.",
                "table": {"cols": ["Product", "On hand", "Days left", "Order"], "rows": [[x["p"].name, f"{x['on_hand']:.0f}", f"{x['cover']:.1f}", f"{x['qty']:.0f}"] for x in r[:30]]}}

    if has(t, "dead stock", "slow moving", "slow stock", "not selling", "not sold", "never sold", "no sales"):
        r = insights.dead_stock()
        return {"answer": f"{len(r)} product(s) with stock but no sales in 60 days, tying up {money(sum((x['value'] for x in r), Decimal(0)))} at cost (top 30 shown).",
                "table": {"cols": ["Product", "On hand", "Tied up (cost)"], "rows": [[x["p"].name, f"{x['on_hand']:.0f}", money(x["value"])] for x in r]}}

    if has(t, "stock value", "inventory value", "value of stock", "value of inventory", "worth", "stock worth"):
        qs = Product.objects.filter(active=True)
        if cat:
            qs = qs.filter(category=cat)
        cost_v = retail_v = Decimal(0)
        for p in qs:
            if p.tracks_stock and p.on_hand > 0:
                cost_v += p.on_hand * p.cost
                retail_v += p.on_hand * p.price
        return {"answer": f"Stock{scope} is worth {money(cost_v)} at cost and {money(retail_v)} at selling price.", "table": None}

    if has(t, "forecast", "predict", "expect", "next week", "next 7"):
        _, fut = insights.forecast()
        return {"answer": f"Expected sales for the next 7 days: about {money(sum((v for _, v in fut), Decimal(0)))}, based on the same weekday over the last 4 weeks.",
                "table": {"cols": ["Day", "Expected sales"], "rows": [[d.strftime("%a %d %b"), money(v)] for d, v in fut]}}

    if has(t, "cash short", "cash over", "cash up", "cash-up", "drawer", "till difference", "short"):
        rows = [[str(s), f"{s.closed_at:%d %b}", money(s.expected_cash), money(s.counted_cash), money(s.difference)]
                for s in PosSession.objects.filter(state="closed", closed_at__date__gte=d1, closed_at__date__lte=d2) if s.difference]
        return {"answer": f"{len(rows)} closed session(s) with a cash difference in {label}.", "table": {"cols": ["Session", "Closed", "Expected", "Counted", "Difference"], "rows": rows}}

    if has(t, "refund", "returned", "returns"):
        qs = PosOrder.objects.filter(kind="refund", created_at__date__gte=d1, created_at__date__lte=d2)
        return {"answer": f"{qs.count()} refund(s) totalling {money(-(qs.aggregate(t=Sum('total'))['t'] or 0))} in {label}.",
                "table": {"cols": ["Number", "Date", "Cashier", "Amount"], "rows": [[o.number, f"{o.created_at:%d %b %H:%M}", o.cashier_name, money(-o.total)] for o in qs[:30]]}}

    if has(t, "zra", "vsdc", "smart invoice", "failed to send"):
        qs = PosOrder.objects.filter(remote=False)
        rows = [[s, qs.filter(zra_status=s).count()] for s in ("sent", "pending", "failed")]
        return {"answer": "ZRA submission status of sales made at this site.", "table": {"cols": ["Status", "Sales"], "rows": rows}}

    if has(t, "payment method", "paid by", "cash vs", "mobile money", "card payments", "how did customers pay", "payments"):
        qs = PosPayment.objects.filter(order__created_at__date__gte=d1, order__created_at__date__lte=d2).values("method").annotate(t=Sum("amount")).order_by("-t")
        labels = dict(PosPayment.METHODS)
        return {"answer": f"Payments received in {label} (cash shown before change given back).", "table": {"cols": ["Method", "Amount"], "rows": [[labels.get(r["method"]), money(r["t"])] for r in qs]}}

    if has(t, "cashier", "who sold", "staff", "employee", "salesperson", "sales person"):
        qs = _lines(d1, d2, cat).values("order__cashier_name").annotate(t=Sum("line_total"), n=Count("order", distinct=True)).order_by("-t")
        return {"answer": f"Sales by cashier{scope} for {label}.", "table": {"cols": ["Cashier", "Orders", "Sales"], "rows": [[r["order__cashier_name"], r["n"], money(r["t"])] for r in qs]}}

    if has(t, "department", "categor") and not cat:
        qs = _lines(d1, d2).values("product__category__name").annotate(t=Sum("line_total"), c=Sum(cost)).order_by("-t")
        return {"answer": f"Sales by department for {label}.", "table": {"cols": ["Department", "Sales", "Est. margin"], "rows": [[r["product__category__name"] or "Uncategorised", money(r["t"]), money((r["t"] or 0) - (r["c"] or 0))] for r in qs]}}

    if has(t, "top", "best", "most sold", "most popular", "fast moving", "worst", "least", "slowest", "bottom"):
        bottom = has(t, "worst", "least", "slowest", "bottom")
        by_units = has(t, "units", "quantity", "pieces", "how many")
        key = "u" if by_units else "t"
        qs = _lines(d1, d2, cat).values("ref", "name").annotate(t=Sum("line_total"), u=Sum("qty")).order_by(key if bottom else "-" + key)[:10]
        return {"answer": f"{'Slowest' if bottom else 'Top'} 10 products{scope} for {label}, by {'units' if by_units else 'sales value'}.",
                "table": {"cols": ["Ref", "Product", "Units", "Sales"], "rows": [[r["ref"], r["name"], f"{r['u']:.0f}", money(r["t"])] for r in qs]}}

    prods, term = find_products(q, cat)
    if prods and has(t, "price", "cost", "how much is", "selling for", "retail"):
        return {"answer": f"{len(prods)} match(es) for '{term}'.", "table": {"cols": ["Ref", "Product", "Price", "Secondary", "Cost"], "rows": [[p.internal_ref, p.name, money(p.price), money(p.secondary_price) if p.secondary_price else "", money(p.cost)] for p in prods]}}

    if has(t, "stock", "on hand", "how many", "do we have", "left", "available", "in stock", "quantity"):
        if prods:
            return {"answer": f"{len(prods)} match(es) for '{term}'.", "table": {"cols": ["Ref", "Product", "On hand", "Min"], "rows": [[p.internal_ref, p.name, f"{p.on_hand:.0f}" if p.tracks_stock else "service", f"{p.min_qty:.0f}"] for p in prods]}}
        if cat:
            ps = Product.objects.filter(category=cat, active=True)
            return {"answer": f"{ps.count()} products in {cat.name}.", "table": {"cols": ["Product", "On hand"], "rows": [[p.name, f"{p.on_hand:.0f}"] for p in ps[:30]]}}

    if has(t, "sales", "sold", "revenue", "turnover", "took", "made", "earn", "profit", "margin", "orders"):
        prod = prods[0] if len(prods) == 1 else None
        qs = _lines(d1, d2, cat, prod)
        if prods and len(prods) > 1 and not cat:
            qs = qs.filter(product__in=prods)
        a = qs.aggregate(t=Sum("line_total"), tax=Sum("tax_amount"), c=Sum(cost), u=Sum("qty"), n=Count("order", distinct=True))
        total, tax, c = a["t"] or 0, a["tax"] or 0, a["c"] or 0
        what = f" of '{term}'" if prods else ""
        return {"answer": f"Sales{what}{scope} for {label}: {money(total)} incl. VAT from {a['n']} order(s), {a['u'] or 0:.0f} unit(s). "
                          f"Excluding VAT {money(total - tax)}; estimated margin {money(total - tax - c)} (uses current costs).", "table": None}

    if prods:
        return {"answer": f"{len(prods)} product(s) match '{term}'.", "table": {"cols": ["Ref", "Product", "Price", "On hand"], "rows": [[p.internal_ref, p.name, money(p.price), f"{p.on_hand:.0f}" if p.tracks_stock else "service"] for p in prods]}}
    return None


EXAMPLES = ["sales today", "sales this month", "top products last month", "worst selling products", "sales by cashier this week",
            "how many oil filters do we have", "price of brake pads", "what should I reorder", "dead stock", "stock value",
            "refunds this week", "cash short last week", "payment methods today", "forecast next week", "out of stock"]
