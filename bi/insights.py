"""Automatic suggestions computed from the shop's own sales. No outside service is used here."""
import json
import math
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from inventory.models import Product
from pos.models import PosOrder, PosOrderLine, PosSession

LOOKBACK = 30
LEAD_DAYS = 14      # reorder when stock covers less than this many days
TARGET_DAYS = 30    # order enough to cover this many days


def reorder_suggestions(lookback=LOOKBACK, lead=LEAD_DAYS, target=TARGET_DAYS):
    since = timezone.now() - timedelta(days=lookback)
    sold = (PosOrderLine.objects.filter(order__created_at__gte=since).values("product")
            .annotate(q=Sum("qty")).filter(q__gt=0))
    out = []
    for row in sold:
        p = Product.objects.filter(pk=row["product"], active=True).first()
        if not p or not p.tracks_stock:
            continue
        per_day = row["q"] / Decimal(lookback)
        on_hand = p.on_hand
        cover = on_hand / per_day if per_day else None
        if cover is not None and cover < lead:
            qty = max(Decimal(0), per_day * target - on_hand)
            out.append({"p": p, "per_day": per_day, "on_hand": on_hand, "cover": max(cover, Decimal(0)),
                        "qty": Decimal(math.ceil(qty)), "cost": Decimal(math.ceil(qty)) * p.cost})
    out.sort(key=lambda r: r["cover"])
    return out


def dead_stock(days=60, limit=30):
    since = timezone.now() - timedelta(days=days)
    moving = set(PosOrderLine.objects.filter(order__created_at__gte=since).values_list("product", flat=True))
    rows = []
    for p in Product.objects.filter(active=True).exclude(pk__in=moving).select_related("category")[:2000]:
        if p.tracks_stock and p.on_hand > 0 and p.cost > 0:
            rows.append({"p": p, "on_hand": p.on_hand, "value": p.on_hand * p.cost})
    rows.sort(key=lambda r: -r["value"])
    return rows[:limit]


def anomalies():
    """Things a manager would want to look at. These are prompts to check, not accusations."""
    flags = []
    since = timezone.now() - timedelta(days=LOOKBACK)
    by = defaultdict(lambda: {"sales": 0, "refunds": 0, "total": Decimal(0), "disc": Decimal(0)})
    for o in PosOrder.objects.filter(created_at__gte=since, remote=False):
        c = by[o.cashier_name]
        if o.kind == PosOrder.REFUND:
            c["refunds"] += 1
        else:
            c["sales"] += 1
            c["total"] += o.total
            c["disc"] += o.discount_total
    for name, c in by.items():
        if c["sales"] >= 5 and c["refunds"] / c["sales"] > 0.10:
            flags.append(f"{name}: {c['refunds']} refunds on {c['sales']} sales in {LOOKBACK} days ({c['refunds'] / c['sales']:.0%}).")
        gross = c["total"] + c["disc"]
        if c["sales"] >= 5 and gross and c["disc"] / gross > Decimal("0.10"):
            flags.append(f"{name}: discounts are {c['disc'] / gross:.0%} of sales value.")
    for s in PosSession.objects.filter(state=PosSession.CLOSED, closed_at__gte=since):
        d = s.difference
        if d is not None and abs(d) > max(Decimal(50), (s.expected_cash or 0) * Decimal("0.02")):
            flags.append(f"Cash {'over' if d > 0 else 'short'} by {abs(d):.2f} on {s} ({s.closed_at:%d %b}).")
    stuck = PosOrder.objects.filter(remote=False, zra_status="failed").count()
    if stuck:
        flags.append(f"{stuck} sale(s) failed to submit to ZRA. Check ZRA Logs.")
    return flags


def forecast(days_back=28, ahead=7):
    """Next-week sales from the same weekday over the past 4 weeks. Simple and explainable, not magic."""
    start = date.today() - timedelta(days=days_back)
    rows = (PosOrderLine.objects.filter(order__created_at__date__gte=start)
            .annotate(d=TruncDate("order__created_at")).values("d").annotate(t=Sum("line_total")))
    daily = {r["d"]: r["t"] or Decimal(0) for r in rows}
    hist = []
    for i in range(days_back, 0, -1):
        d = date.today() - timedelta(days=i - 1)
        hist.append((d, daily.get(d, Decimal(0))))
    overall = sum((v for _, v in hist), Decimal(0)) / max(len(hist), 1)
    future = []
    for k in range(1, ahead + 1):
        d = date.today() + timedelta(days=k)
        same = [v for dd, v in hist if dd.weekday() == d.weekday()]
        future.append((d, sum(same, Decimal(0)) / len(same) if same else overall))
    return hist, future


def summary_for_ai():
    """A small aggregate-only snapshot. No customer names, TPINs or receipt lines leave the server."""
    since = timezone.now() - timedelta(days=LOOKBACK)
    lines = PosOrderLine.objects.filter(order__created_at__gte=since)
    tot = lines.aggregate(sales=Sum("line_total"), vat=Sum("tax_amount"))
    top = list(lines.values("name").annotate(t=Sum("line_total"), q=Sum("qty")).order_by("-t")[:10])
    cats = list(lines.values("product__category__name").annotate(t=Sum("line_total")).order_by("-t")[:10])
    hist, future = forecast()
    return {"period_days": LOOKBACK, "currency": "ZMW", "sales_incl_vat": str(tot["sales"] or 0), "vat": str(tot["vat"] or 0),
            "orders": PosOrder.objects.filter(created_at__gte=since, kind="sale").count(),
            "top_products": [{"name": r["name"], "sales": str(r["t"]), "units": str(r["q"])} for r in top],
            "by_department": [{"name": r["product__category__name"] or "Uncategorised", "sales": str(r["t"])} for r in cats],
            "low_stock": [{"name": r["p"].name, "on_hand": str(r["on_hand"]), "days_cover": f"{r['cover']:.1f}"} for r in reorder_suggestions()[:10]],
            "dead_stock_value": str(sum((r["value"] for r in dead_stock(limit=2000)), Decimal(0))),
            "flags": anomalies(), "forecast_next_7_days": [{"date": d.isoformat(), "sales": f"{v:.0f}"} for d, v in future]}


class AiError(Exception):
    pass


def ask(question):
    if not settings.AI_API_KEY:
        raise AiError("The AI box is off. Set ANTHROPIC_API_KEY on the server to turn it on.")
    system = ("You are the analyst for Alifaris Auto Spares, a Zambian motor parts business. Answer the manager's question "
              "using only the JSON snapshot provided. Be brief and practical. If the snapshot cannot answer it, say what is missing. "
              "Amounts are in Zambian kwacha.")
    body = json.dumps({"model": settings.AI_MODEL, "max_tokens": 600, "system": system,
                       "messages": [{"role": "user", "content": f"Snapshot:\n{json.dumps(summary_for_ai())}\n\nQuestion: {question[:500]}"}]})
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body.encode(), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": settings.AI_API_KEY,
                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    except urllib.error.HTTPError as e:
        raise AiError(f"The AI service refused the request (HTTP {e.code}). Check the key and model name.")
    except Exception as e:
        raise AiError(f"Could not reach the AI service: {e}")
