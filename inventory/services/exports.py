"""Excel exports. Each builder returns (sheet title, header row, data rows)."""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ..models import Product, PurchaseOrder, StockMove
from . import stock


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def products():
    rows = [[p.internal_ref, p.barcode, p.name, p.category.name if p.category else "", p.price, p.secondary_price, p.cost,
             p.on_hand, p.min_qty, p.max_qty, p.tax_type, "yes" if p.active else "no"]
            for p in stock.with_on_hand(Product.objects.select_related("category").order_by("name"))]
    return "Products", ["Ref", "Barcode", "Name", "Category", "Price", "Secondary price", "Avg cost", "On hand",
                        "Min", "Max", "Tax", "Active"], rows


def valuation():
    rows = [[r["product"].internal_ref, r["product"].name, r["qty"], r["cost"], r["value"]] for r in stock.stock_valuation()]
    return "Stock valuation", ["Ref", "Product", "On hand", "Avg cost", "Value (ZMW)"], rows


def moves():
    qs = StockMove.objects.select_related("product", "source", "dest", "picking").order_by("-date")[:20000]
    rows = [[m.date.strftime("%Y-%m-%d %H:%M"), m.product.name, m.source.full_name, m.dest.full_name, m.qty, m.unit_cost,
             m.picking.ref if m.picking else "", m.reason] for m in qs]
    return "Stock moves", ["Date", "Product", "From", "To", "Qty", "Unit cost", "Document", "Reason"], rows


def sales(start=None, end=None):
    from pos.models import PosOrderLine
    qs = PosOrderLine.objects.select_related("order").order_by("-order__created_at")
    if start:
        qs = qs.filter(order__created_at__date__gte=start)
    if end:
        qs = qs.filter(order__created_at__date__lte=end)
    rows = []
    for ln in qs[:50000]:
        o = ln.order
        net = ln.line_total - ln.tax_amount
        rows.append([o.created_at.strftime("%Y-%m-%d %H:%M"), o.number, o.kind, o.customer_name or (o.customer.name if o.customer else ""),
                     ln.ref, ln.name, ln.qty, ln.unit_price, ln.unit_cost, ln.line_total, ln.tax_amount,
                     net - ln.unit_cost * ln.qty])
    return "Sales", ["Date", "Receipt", "Type", "Customer", "Ref", "Product", "Qty", "Unit price", "Unit cost",
                     "Line total", "VAT", "Profit (ex VAT)"], rows


def receivables():
    from pos import receivables as r
    rows = [[x["customer"].name, x["balance"], x["overdue"], x["oldest_days"], x["limit"] or "no limit",
             "OVER LIMIT" if x["over_limit"] else ""] for x in r.summary()]
    return "Receivables", ["Customer", "Owes (ZMW)", "Overdue over 30 days", "Oldest item (days)", "Credit limit", "Flag"], rows


def purchases():
    rows = [[o.ref, o.supplier.name, o.order_date, o.expected_date, o.get_state_display(), o.total]
            for o in PurchaseOrder.objects.select_related("supplier").prefetch_related("lines")]
    return "Purchase orders", ["Reference", "Supplier", "Ordered", "Expected", "Status", "Total (ZMW)"], rows


def locations():
    from ..models import Location, StockQuant
    rows = [[q.location.full_name, q.product.internal_ref, q.product.name, q.qty]
            for q in StockQuant.objects.filter(location__usage=Location.INTERNAL).exclude(qty=0)
            .select_related("product", "location").order_by("location_id", "product__name")]
    return "Stock by location", ["Location", "Ref", "Product", "Quantity"], rows


def analysis():
    from django.db.models import Count, Q, Sum
    from ..models import Location
    inn = Location.INTERNAL
    qs = (StockMove.objects.values("product__internal_ref", "product__name")
          .annotate(got=Sum("qty", filter=Q(dest__usage=inn) & ~Q(source__usage=inn)),
                    sent=Sum("qty", filter=Q(source__usage=inn) & ~Q(dest__usage=inn)), n=Count("id")).order_by("product__name"))
    return "Moves analysis", ["Ref", "Product", "Total in", "Total out", "Net", "Moves"], [
        [r["product__internal_ref"], r["product__name"], r["got"] or 0, r["sent"] or 0, (r["got"] or 0) - (r["sent"] or 0), r["n"]] for r in qs]


BUILDERS = {"locations": locations, "analysis": analysis, "products": products, "valuation": valuation, "moves": moves, "sales": sales,
            "receivables": receivables, "purchases": purchases}


def to_xlsx(title, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for row in rows:
        ws.append([_num(v) for v in row])
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:200]]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 45)
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
