from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import audit, forms as f
from .models import (SyncPeer, ApiToken, LandedCost, Location, OperationType, Partner, Picking, Product, PurchaseOrder, Scrap, StockMove, StockQuant,
                     ZraConfig, ZraLog)
from .services import demo as demo_service, importer, stock, sync as sync_service, zra
from .permissions import (WM_CONFIG_KEYS, can_edit, can_manage_stock, editor_required, is_warehouse_manager,
                          post_needs_editor)
from .services.stock import StockError

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="login")
stock_manager_required = user_passes_test(can_manage_stock, login_url="login")

OPS = {"receipts": OperationType.RECEIPT, "deliveries": OperationType.DELIVERY, "internal": OperationType.INTERNAL}
OP_TITLES = {"receipts": "Receipts", "deliveries": "Deliveries", "internal": "Internal Transfers"}


def ctx(request, **extra):
    base = {"company": settings.COMPANY_NAME, "unread": 2, "site_code": settings.SITE_CODE,
            "can_edit": can_edit(request.user), "can_manage_stock": can_manage_stock(request.user),
            "is_warehouse_manager": is_warehouse_manager(request.user)}
    base.update(extra)
    return base


class Pill:
    """A coloured badge inside a table cell. kind: ok, plan (amber), bad (red)."""

    def __init__(self, text, kind="ok"):
        self.text, self.kind = text, kind

    def __str__(self):
        return self.text


def _fmt_num(v):
    if isinstance(v, Decimal):
        s = f"{v:,.3f}".rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}"


def _cell(c):
    if isinstance(c, Pill):
        return {"v": c.text, "pill": c.kind}
    if isinstance(c, (Decimal, int, float)) and not isinstance(c, bool):
        return {"v": _fmt_num(c), "num": True}
    return {"v": "" if c is None else c}


def _sort_key(v):
    if isinstance(v, Pill):
        return (1, str(v).lower())
    if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool):
        return (0, float(v))
    return (1, str(v).lower())


def qs_with(request, **changes):
    """Current query string with some params replaced (None removes one). Used for KPI cards that apply a filter."""
    q = request.GET.copy()
    q.pop("page", None)
    for k, v in changes.items():
        if v is None:
            q.pop(k, None)
        else:
            q[k] = v
    s = q.urlencode()
    return "?" + s if s else "?"


def table(request, title, headers, rows, *, new_url=None, new_label="New", back=None, note=None, actions=None,
          po_suppliers=None, kpis=None, filters=None, export=None, empty="Nothing here yet.", search=True):
    """Generic list/report page. rows = list of (cells, url_or_None).
    kpis: [{label, value, tone, url}]; filters: [{name, label, options|type, value}]; export: an exports.BUILDERS key."""
    sort = request.GET.get("sort", "")
    dirn = "desc" if request.GET.get("dir") == "desc" else "asc"
    if sort.isdigit() and int(sort) < len(headers):
        i = int(sort)
        rows = sorted(rows, key=lambda r: _sort_key(r[0][i]), reverse=(dirn == "desc"))
    heads = []
    first = rows[0][0] if rows else []
    for i, h in enumerate(headers):
        q = request.GET.copy()
        q.pop("page", None)
        q["sort"], q["dir"] = str(i), ("desc" if (sort == str(i) and dirn == "asc") else "asc")
        heads.append({"label": h, "url": "?" + q.urlencode(), "active": sort == str(i), "dir": dirn,
                      "num": i < len(first) and isinstance(first[i], (Decimal, int, float)) and not isinstance(first[i], bool)})
    page = Paginator(rows, 80).get_page(request.GET.get("page"))
    body = [{"cells": [_cell(c) for c in cells], "url": url} for cells, url in page]
    keep = request.GET.copy()
    for k in ("page",):
        keep.pop(k, None)
    pq = keep.urlencode()
    flt_active = any(request.GET.get(f_["name"]) for f_ in (filters or [])) or bool(request.GET.get("q"))
    return render(request, "inventory/table.html", ctx(
        request, title=title, heads=heads, body=body, page=page, new_url=new_url, new_label=new_label, note=note,
        actions=actions or [], po_suppliers=po_suppliers, kpis=kpis or [], filters=filters or [], export=export,
        empty=empty, search=search, pq=(pq + "&") if pq else "", sort=sort, dirn=dirn, flt_active=flt_active,
        ncols=len(headers)))


def _category_filter(request):
    from .models import ProductCategory
    return {"name": "category", "label": "All categories", "value": request.GET.get("category", ""),
            "options": [(str(c.pk), c.name) for c in ProductCategory.objects.order_by("name")]}


def _on_hand_map():
    return {r["product"]: r["t"] for r in StockQuant.objects.filter(location__usage=Location.INTERNAL)
            .values("product").annotate(t=Sum("qty"))}


# ---------------------------------------------------------------- overview
@login_required
def overview(request):
    cards = []
    for slug, code in OPS.items():
        qs = Picking.objects.filter(op_type__code=code)
        cards.append({"title": OP_TITLES[slug], "slug": slug, "draft": qs.filter(state=Picking.DRAFT).count(),
                      "done": qs.filter(state=Picking.DONE).count()})
    from pos.models import PosOrder
    pos = PosOrder.objects.filter(kind=PosOrder.SALE)
    cards.append({"title": "PoS Orders", "slug": "deliveries", "draft": pos.filter(zra_status__in=["pending", "failed"]).count(),
                  "done": pos.count(), "pos": True})
    low = len(stock.replenishment_candidates())
    return render(request, "inventory/overview.html", ctx(request, cards=cards, low=low,
                                                          products=Product.objects.filter(active=True).count()))


# ---------------------------------------------------------------- warehouse manager screen
@stock_manager_required
def warehouse(request):
    """One screen for the warehouse manager: what is waiting, what is low, where the stock sits, what moved."""
    waiting = []
    for slug, code in OPS.items():
        qs = Picking.objects.filter(op_type__code=code, state=Picking.DRAFT).select_related("source", "dest").order_by("-id")
        waiting.append({"title": OP_TITLES[slug], "slug": slug, "count": qs.count(), "recent": list(qs[:5])})
    low = sorted(stock.replenishment_candidates(), key=lambda r: r["on_hand"] - r["min"])
    by_location = (StockQuant.objects.filter(location__usage=Location.INTERNAL, qty__gt=0)
                   .values("location__name").annotate(items=Count("product"), units=Sum("qty")).order_by("-units"))
    moves = StockMove.objects.select_related("product", "source", "dest", "picking__op_type")[:15]
    return render(request, "inventory/warehouse.html", ctx(
        request, waiting=waiting, low=low[:12], low_total=len(low), by_location=by_location, moves=moves,
        out_of_stock=sum(1 for r in low if r["on_hand"] <= 0), products=Product.objects.filter(active=True).count()))


# ---------------------------------------------------------------- operations
@login_required
def picking_list(request, kind):
    if kind not in OPS:
        raise Http404
    qs = Picking.objects.filter(op_type__code=OPS[kind]).select_related("partner", "source", "dest")
    if s := request.GET.get("state"):
        qs = qs.filter(state=s)
    if q := request.GET.get("q"):
        qs = qs.filter(Q(ref__icontains=q) | Q(origin__icontains=q) | Q(partner__name__icontains=q))
    rows = [([p.ref, p.partner or "", p.source.full_name, p.dest.full_name, p.origin,
              p.scheduled_date.strftime("%Y-%m-%d %H:%M"), p.get_state_display()],
             f"/inventory/transfer/{p.pk}/") for p in qs]
    return table(request, OP_TITLES[kind], ["Reference", "Contact", "From", "To", "Source document", "Scheduled", "Status"],
                 rows, new_url=f"/inventory/operations/{kind}/new/", note="Filter: ?state=draft or ?state=done or ?q=text")


@editor_required
def picking_new(request, kind):
    if kind not in OPS:
        raise Http404
    op = OperationType.objects.filter(code=OPS[kind]).first()
    if not op:
        messages.error(request, "Run: python manage.py seed_inventory")
        return redirect("inventory:overview")
    picking = Picking(op_type=op, source=op.default_source, dest=op.default_dest, created_by=request.user)
    return _picking_form(request, picking, new=True)


@login_required
@post_needs_editor
def picking_detail(request, pk):
    return _picking_form(request, get_object_or_404(Picking.objects.select_related("op_type"), pk=pk))


def _picking_form(request, picking, new=False):
    editable = picking.state == Picking.DRAFT
    if request.method == "POST" and editable:
        form = f.PickingForm(request.POST, instance=picking)
        lines = f.LineFormSet(request.POST, instance=picking)
        if form.is_valid() and lines.is_valid():
            saved = form.save()
            lines.instance = saved
            lines.save()
            messages.success(request, f"Saved {saved.ref}")
            return redirect("inventory:picking_detail", pk=saved.pk)
    else:
        form = f.PickingForm(instance=picking)
        lines = f.LineFormSet(instance=picking)
    kind = next((k for k, v in OPS.items() if v == picking.op_type.code), "receipts")
    return render(request, "inventory/picking_form.html", ctx(request, picking=picking, form=form, lines=lines,
                                                              editable=editable, new=new, kind=kind,
                                                              title=OP_TITLES[kind]))


@editor_required
@require_POST
def picking_action(request, pk, action):
    picking = get_object_or_404(Picking, pk=pk)
    try:
        if action == "validate":
            stock.validate_picking(picking)
            for po in PurchaseOrder.objects.filter(receipt=picking):
                if po.sync() == PurchaseOrder.RECEIVED:
                    audit.log(request, "purchase order received", po.ref, picking.ref)
            audit.log(request, "stock validated", picking.ref, f"{picking.op_type.name}, {picking.lines.count()} line(s)")
            messages.success(request, f"{picking.ref} validated. Stock updated.")
            cfg = ZraConfig.get()
            if cfg.enabled and cfg.push_stock_after_validate:
                for ln in picking.lines.select_related("product"):
                    zra.push_stock_level(ln.product, request.user.get_username())
        elif action == "cancel":
            stock.cancel_picking(picking)
            audit.log(request, "document cancelled", picking.ref)
            messages.success(request, f"{picking.ref} cancelled.")
    except StockError as e:
        messages.error(request, str(e))
    return redirect("inventory:picking_detail", pk=pk)


# ---------------------------------------------------------------- adjustments
@login_required
@post_needs_editor
def physical_inventory(request):
    locations = Location.objects.filter(usage=Location.INTERNAL, active=True)
    loc = locations.filter(pk=request.GET.get("location") or request.POST.get("location")).first() or locations.first()
    quants = StockQuant.objects.filter(location=loc).select_related("product").order_by("product__name") if loc else []
    if request.method == "POST" and loc:
        counts = {}
        for q in quants:
            raw = request.POST.get(f"count_{q.product_id}", "").strip()
            if raw != "":
                try:
                    counts[q.product_id] = Decimal(raw)
                except InvalidOperation:
                    messages.error(request, f"'{raw}' is not a number for {q.product.name}")
                    return redirect(request.path + f"?location={loc.pk}")
        # products not yet in the location can be counted by ref in the extra row
        ref, extra = request.POST.get("new_ref", "").strip(), request.POST.get("new_count", "").strip()
        if ref and extra != "":
            p = Product.objects.filter(internal_ref=ref).first()
            if not p:
                messages.error(request, f"No product with reference {ref}")
                return redirect(request.path + f"?location={loc.pk}")
            counts[p.pk] = Decimal(extra)
        try:
            n = stock.apply_count(loc, counts)
            audit.log(request, "stock count applied", loc, f"{n} product(s) adjusted")
            messages.success(request, f"Applied counts: {n} product(s) adjusted.")
        except StockError as e:
            messages.error(request, str(e))
        return redirect(request.path + f"?location={loc.pk}")
    return render(request, "inventory/physical_inventory.html", ctx(request, locations=locations, loc=loc, quants=quants))


@login_required
def scrap_list(request):
    rows = [([s.date.strftime("%Y-%m-%d %H:%M"), s.product, s.location.full_name, s.qty, s.reason], None)
            for s in Scrap.objects.select_related("product", "location")]
    return table(request, "Scrap", ["Date", "Product", "Location", "Quantity", "Reason"], rows,
                 new_url="/inventory/scrap/new/")


@editor_required
def scrap_new(request):
    form = f.ScrapForm(request.POST or None)
    if form.is_valid():
        try:
            s = form.save()
            stock.do_scrap(s)
            messages.success(request, "Scrapped.")
            return redirect("inventory:scrap")
        except StockError as e:
            s.delete()
            messages.error(request, str(e))
    return render(request, "inventory/form.html", ctx(request, title="New scrap", form=form, back="/inventory/scrap/"))


@login_required
def landed_list(request):
    rows = [([lc.date, lc.picking.ref, lc.description, lc.amount, "Applied" if lc.applied else "Not applied"],
             f"/inventory/landed-costs/{lc.pk}/") for lc in LandedCost.objects.select_related("picking")]
    return table(request, "Landed Costs", ["Date", "Receipt", "Description", "Amount", "Status"], rows,
                 new_url="/inventory/landed-costs/new/")


@editor_required
def landed_new(request):
    form = f.LandedCostForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect("inventory:landed")
    return render(request, "inventory/form.html", ctx(request, title="New landed cost", form=form,
                                                      back="/inventory/landed-costs/"))


@login_required
@post_needs_editor
def landed_detail(request, pk):
    lc = get_object_or_404(LandedCost, pk=pk)
    if request.method == "POST":
        try:
            stock.apply_landed_cost(lc)
            messages.success(request, "Landed cost applied to product costs.")
        except StockError as e:
            messages.error(request, str(e))
        return redirect("inventory:landed")
    return render(request, "inventory/landed_detail.html", ctx(request, lc=lc))


@login_required
@post_needs_editor
def replenishment(request):
    rows_data = stock.replenishment_candidates()
    if request.method == "POST" and rows_data:
        op = OperationType.objects.filter(code=OperationType.RECEIPT).first()
        picking = stock.new_picking(op, [(r["product"], r["to_order"], r["product"].cost) for r in rows_data],
                                    user=request.user, notes="Created from replenishment")
        messages.success(request, f"Draft receipt {picking.ref} created. Edit it, then validate when the goods arrive.")
        return redirect("inventory:picking_detail", pk=picking.pk)
    rows = [([r["product"], r["on_hand"], r["min"], r["max"], r["to_order"]], f"/inventory/products/{r['product'].pk}/")
            for r in rows_data]
    return table(request, "Replenishment", ["Product", "On hand", "Minimum", "Maximum", "To order"], rows,
                 note="Products below their reorder minimum. Set minimum and maximum on each product.",
                 actions=[{"label": "Create draft receipt", "post": True}] if rows else [],
                 po_suppliers=Partner.objects.filter(is_supplier=True).order_by("name") if rows and can_manage_stock(request.user) else None)


# ---------------------------------------------------------------- products
@login_required
def products(request):
    qs = Product.objects.select_related("category", "uom")
    flt = request.GET.get("filter", "goods")
    if flt == "goods":
        qs = qs.filter(product_type=Product.GOODS)
    elif flt == "services":
        qs = qs.filter(product_type=Product.SERVICE)
    elif flt == "archived":
        qs = qs.filter(active=False)
    if flt != "archived":
        qs = qs.filter(active=True)
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(internal_ref__iexact=q) | Q(barcode=q) | Q(name__icontains=q.replace(" ", "")))
    if cat := request.GET.get("category"):
        qs = qs.filter(category_id=cat)
    page = Paginator(qs, 80).get_page(request.GET.get("page"))
    qty = {r["product"]: r["t"] for r in StockQuant.objects.filter(
        product__in=[p.pk for p in page], location__usage=Location.INTERNAL).values("product").annotate(t=Sum("qty"))}
    for p in page:
        p.qty_on_hand = qty.get(p.pk, Decimal("0"))
    view = request.GET.get("view", "kanban")
    return render(request, "inventory/products.html", ctx(request, page=page, view=view, flt=flt, q=q))


@login_required
@post_needs_editor
def product_edit(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk else None
    form = f.ProductForm(request.POST or None, request.FILES or None, instance=product)
    if form.is_valid():
        changed = [f"{k}: {form.initial.get(k)} to {form.cleaned_data.get(k)}" for k in
                   ("name", "price", "secondary_price", "cost", "min_qty", "max_qty", "tax_type", "active")
                   if product and k in form.changed_data]
        saved = form.save()
        audit.log(request, "product changed" if product else "product created", saved,
                  "; ".join(changed))
        messages.success(request, f"Saved {saved.name}")
        return redirect("inventory:product_edit", pk=saved.pk)
    ctx_extra = {"product": product, "on_hand": product.on_hand if product else None}
    if product:
        ctx_extra["quants"] = product.quants.select_related("location").filter(location__usage=Location.INTERNAL)
        ctx_extra["problems"] = zra.check_item(product)
    return render(request, "inventory/product_form.html", ctx(request, form=form, title="Product", **ctx_extra))


@staff_required
@require_POST
def product_zra(request, pk):
    p = get_object_or_404(Product, pk=pk)
    try:
        ok, msg = zra.register_product(p, request.user.get_username())
        (messages.success if ok else messages.error)(request, f"ZRA: {msg}")
    except zra.ZraDisabled as e:
        messages.error(request, str(e))
    return redirect("inventory:product_edit", pk=pk)


# ---------------------------------------------------------------- reporting
@login_required
def report_stock(request):
    qs = Product.objects.filter(product_type=Product.GOODS, active=True).select_related("category", "uom").order_by("name")
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(internal_ref__iexact=q) | Q(barcode=q))
    if cat := request.GET.get("category"):
        qs = qs.filter(category_id=cat)
    have = _on_hand_map()
    items = []
    for p in qs:
        oh = have.get(p.pk, Decimal("0"))
        status = "out" if oh <= 0 else "low" if p.min_qty and oh < p.min_qty else "ok"
        items.append((p, oh, status))
    count = {s: sum(1 for i in items if i[2] == s) for s in ("out", "low", "ok")}
    status_f = request.GET.get("status", "")
    shown = [i for i in items if not status_f or i[2] == status_f]
    pill = {"out": Pill("Out of stock", "bad"), "low": Pill("Low", "plan"), "ok": Pill("In stock", "ok")}
    rows = [([p.internal_ref, p.name, p.category.name if p.category else "", oh, p.min_qty or "", pill[s], p.uom or ""],
             f"/inventory/products/{p.pk}/") for p, oh, s in shown]
    kpis = [{"label": "Products", "value": len(items), "url": qs_with(request, status=None)},
            {"label": "Units on hand", "value": _fmt_num(sum((i[1] for i in items), Decimal("0")))},
            {"label": "Low stock", "value": count["low"], "tone": "plan" if count["low"] else "", "url": qs_with(request, status="low")},
            {"label": "Out of stock", "value": count["out"], "tone": "bad" if count["out"] else "", "url": qs_with(request, status="out")}]
    filters = [_category_filter(request),
               {"name": "status", "label": "Any status", "value": status_f,
                "options": [("ok", "In stock"), ("low", "Low"), ("out", "Out of stock")]}]
    return table(request, "Stock", ["Ref", "Product", "Category", "On hand", "Reorder at", "Status", "Unit"], rows,
                 kpis=kpis, filters=filters, export="products", empty="No products match these filters.",
                 note="Click a column heading to sort, a number above to filter, or a row to open the product.",
                 po_suppliers=(Partner.objects.filter(is_supplier=True).order_by("name")
                               if status_f in ("low", "out") and rows and can_manage_stock(request.user) else None))


@login_required
def report_locations(request):
    qs = StockQuant.objects.exclude(qty=0).select_related("product", "location", "location__warehouse")
    if loc := request.GET.get("location"):
        qs = qs.filter(location_id=loc)
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(product__name__icontains=q) | Q(product__internal_ref__iexact=q))
    qs = qs.filter(location__usage=Location.INTERNAL).order_by("location_id", "product__name")
    data = list(qs)
    rows = [([x.location.full_name, x.product.internal_ref, x.product.name, x.qty], f"/inventory/products/{x.product_id}/") for x in data]
    per_loc = {}
    for x in data:
        per_loc[x.location.full_name] = per_loc.get(x.location.full_name, Decimal("0")) + x.qty
    kpis = [{"label": "Locations holding stock", "value": len(per_loc)},
            {"label": "Products", "value": len({x.product_id for x in data})},
            {"label": "Units", "value": _fmt_num(sum(per_loc.values(), Decimal("0")))}]
    kpis += [{"label": name, "value": _fmt_num(units), "url": qs_with(request, location=str(next(
        x.location_id for x in data if x.location.full_name == name)))} for name, units in list(per_loc.items())[:3] if len(per_loc) > 1]
    filters = [{"name": "location", "label": "All locations", "value": request.GET.get("location", ""),
                "options": [(str(l.pk), l.full_name) for l in Location.objects.filter(usage=Location.INTERNAL)]}]
    return table(request, "Stock by location", ["Location", "Ref", "Product", "Quantity"], rows, kpis=kpis, filters=filters,
                 export="locations", empty="No stock in this location.",
                 note="Where each product sits, for example the shop shelf against the main warehouse.")


@login_required
def report_moves(request):
    from datetime import date
    qs = StockMove.objects.select_related("product", "source", "dest", "picking", "picking__op_type")
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(product__name__icontains=q) | Q(product__internal_ref__iexact=q) | Q(picking__ref__icontains=q))
    if pid := request.GET.get("product", ""):
        qs = qs.filter(product_id=pid) if pid.isdigit() else qs
    kind = request.GET.get("type", "")
    if kind in ("receipt", "delivery", "internal"):
        qs = qs.filter(picking__op_type__code=kind)
    elif kind == "adjustment":
        qs = qs.filter(picking__isnull=True)
    for key, lookup in (("from", "date__date__gte"), ("to", "date__date__lte")):
        try:
            qs = qs.filter(**{lookup: date.fromisoformat(request.GET.get(key, ""))})
        except ValueError:
            pass
    inn = Location.INTERNAL
    agg = qs.aggregate(n=Count("id"), got=Sum("qty", filter=Q(dest__usage=inn) & ~Q(source__usage=inn)),
                       sent=Sum("qty", filter=Q(source__usage=inn) & ~Q(dest__usage=inn)))
    rows = [([m.date.strftime("%Y-%m-%d %H:%M"), m.picking.ref if m.picking_id else (m.reason or "Adjustment"), m.product,
              m.source.full_name, m.dest.full_name, m.qty, m.site_code],
             f"/inventory/transfer/{m.picking_id}/" if m.picking_id else None) for m in qs.order_by("-date")[:2000]]
    kpis = [{"label": "Moves", "value": agg["n"] or 0}, {"label": "Units in", "value": _fmt_num(agg["got"] or Decimal("0")), "tone": "ok"},
            {"label": "Units out", "value": _fmt_num(agg["sent"] or Decimal("0"))}]
    filters = [{"name": "type", "label": "All types", "value": kind,
                "options": [("receipt", "Receipts"), ("delivery", "Deliveries and sales"), ("internal", "Transfers"), ("adjustment", "Adjustments")]},
               {"name": "from", "label": "From", "type": "date", "value": request.GET.get("from", "")},
               {"name": "to", "label": "To", "type": "date", "value": request.GET.get("to", "")}]
    pname = Product.objects.filter(pk=pid).first() if pid.isdigit() else None
    return table(request, "Moves History", ["Date", "Reference", "Product", "From", "To", "Quantity", "Site"], rows, kpis=kpis,
                 filters=filters, export="moves", empty="No moves match these filters.",
                 note=(f"Showing moves for {pname.name}. " if pname else "") + "Newest 2000 moves shown. Click a row to open its document.")


@login_required
def report_analysis(request):
    inn = Location.INTERNAL
    qs = StockMove.objects.values("product", "product__internal_ref", "product__name", "product__category")
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(product__name__icontains=q) | Q(product__internal_ref__iexact=q))
    if cat := request.GET.get("category"):
        qs = qs.filter(product__category_id=cat)
    qs = (qs.annotate(received=Sum("qty", filter=Q(dest__usage=inn) & ~Q(source__usage=inn)),
                      sent=Sum("qty", filter=Q(source__usage=inn) & ~Q(dest__usage=inn)), n=Count("id"))
          .order_by("product__name"))
    data = list(qs)
    rows = [([r["product__internal_ref"], r["product__name"], r["received"] or 0, r["sent"] or 0,
              (r["received"] or 0) - (r["sent"] or 0), r["n"]], f"/inventory/reporting/moves/?product={r['product']}") for r in data]
    got = sum((r["received"] or 0 for r in data), Decimal("0"))
    sent = sum((r["sent"] or 0 for r in data), Decimal("0"))
    kpis = [{"label": "Total in", "value": _fmt_num(got), "tone": "ok"}, {"label": "Total out", "value": _fmt_num(sent)},
            {"label": "Net change", "value": _fmt_num(got - sent)}, {"label": "Products moved", "value": len(data)}]
    return table(request, "Moves Analysis", ["Ref", "Product", "Total in", "Total out", "Net", "Moves"], rows, kpis=kpis,
                 filters=[_category_filter(request)], export="analysis", empty="Nothing moved yet.",
                 note="Quantities entering and leaving your locations (transfers between your own locations are excluded). "
                      "Sort by Total out to see what sells fastest, then click a row to see its moves.")


@login_required
def report_valuation(request):
    data = stock.stock_valuation()
    if cat := request.GET.get("category"):
        data = [r for r in data if str(r["product"].category_id) == cat]
    if q := request.GET.get("q", "").strip():
        data = [r for r in data if q.lower() in r["product"].name.lower() or q.lower() == r["product"].internal_ref.lower()]
    total = sum((r["value"] for r in data), Decimal("0"))
    rows = [([r["product"].internal_ref, r["product"].name, r["product"].category.name if r["product"].category else "",
              r["qty"], r["cost"], r["value"], f"{(r['value'] * 100 / total):.1f}%" if total else "0%"],
             f"/inventory/products/{r['product'].pk}/") for r in data]
    top = max(data, key=lambda r: r["value"], default=None)
    kpis = [{"label": "Total stock value (ZK)", "value": _fmt_num(total.quantize(Decimal("1")))},
            {"label": "Products valued", "value": len(data)},
            {"label": "Most capital tied up", "value": top["product"].name if top else "-", "small": True}]
    return table(request, "Valuation", ["Ref", "Product", "Category", "On hand", "Unit cost", "Value", "Share"], rows, kpis=kpis,
                 filters=[_category_filter(request)], export="valuation", empty="No stock to value.",
                 note="On hand x average cost. Sort by Value to see where your money is sitting.")


# ---------------------------------------------------------------- configuration
def _crud_model(request, key):
    """Managers may open every list; a warehouse manager only the stock-related ones."""
    model, fields, title = f.CRUD.get(key) or (None, None, None)
    if not model or (not request.user.is_staff and key not in WM_CONFIG_KEYS):
        raise Http404
    return model, fields, title


@stock_manager_required
def crud_list(request, key):
    model, fields, title = _crud_model(request, key)
    rows = [([str(o)], f"/inventory/config/{key}/{o.pk}/") for o in model.objects.all()]
    return table(request, title, ["Name"], rows, new_url=f"/inventory/config/{key}/new/")


@stock_manager_required
def crud_edit(request, key, pk=None):
    model, fields, title = _crud_model(request, key)
    obj = get_object_or_404(model, pk=pk) if pk else None
    form = f.crud_form(model, fields)(request.POST or None, instance=obj)
    if form.is_valid():
        saved = form.save()
        audit.log(request, f"{title.lower()} saved", saved, ", ".join(form.changed_data) if obj else "created")
        messages.success(request, "Saved")
        return redirect("inventory:crud_list", key=key)
    return render(request, "inventory/form.html", ctx(request, title=title, form=form, back=f"/inventory/config/{key}/"))


@staff_required
def zra_settings(request):
    cfg = ZraConfig.get()
    form = f.ZraConfigForm(request.POST or None, instance=cfg)
    if form.is_valid():
        form.save()
        audit.log(request, "ZRA settings changed", "ZRA Smart Invoice", ", ".join(form.changed_data))
        messages.success(request, "ZRA settings saved")
        return redirect("inventory:zra_settings")
    pending = Product.objects.filter(active=True, zra_registered=False).count()
    return render(request, "inventory/form.html", ctx(request, title="ZRA Smart Invoice settings", form=form,
                                                      back="/inventory/zra-logs/",
                                                      note=f"{pending} product(s) not yet registered with ZRA."))


@staff_required
def tokens(request):
    form = f.TokenForm(request.POST or None)
    if request.method == "POST":
        if "revoke" in request.POST:
            ApiToken.objects.filter(pk=request.POST["revoke"]).update(active=False)
            audit.log(request, "API token revoked", request.POST["revoke"])
        elif form.is_valid():
            tok = form.save()
            audit.log(request, "API token created", getattr(tok, "name", ""))
        return redirect("inventory:tokens")
    return render(request, "inventory/tokens.html", ctx(request, tokens=ApiToken.objects.all(), form=f.TokenForm()))


# ---------------------------------------------------------------- people and audit trail
ROLE_LABELS = dict(f.ROLES)


def role_of(user):
    if user.is_staff or user.is_superuser:
        return "manager"
    names = set(user.groups.values_list("name", flat=True))
    for role, group in f.ROLE_GROUPS.items():
        if group in names:
            return role
    return "viewer"


def _managers_left(excluding=None):
    from django.contrib.auth import get_user_model
    qs = get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))
    return qs.exclude(pk=excluding.pk).count() if excluding else qs.count()


@staff_required
def users(request):
    from django.contrib.auth import get_user_model
    rows = []
    for u in get_user_model().objects.order_by("-is_active", "username"):
        rows.append(([u.username, u.get_full_name(), ROLE_LABELS[role_of(u)], "Active" if u.is_active else "Switched off",
                      u.last_login.strftime("%d %b %Y %H:%M") if u.last_login else "never"], f"/inventory/users/{u.pk}/"))
    return table(request, "Users", ["Username", "Name", "Role", "Status", "Last login"], rows,
                 new_url="/inventory/users/new/", new_label="New user",
                 note="Managers see everything. Warehouse managers run stock. Cashiers sell. View only can look but not change.")


@staff_required
def user_edit(request, pk=None):
    from django.contrib.auth import get_user_model
    U = get_user_model()
    user = get_object_or_404(U, pk=pk) if pk else None
    if user and user.is_superuser and user != request.user:
        messages.error(request, "Superuser accounts are changed in the admin site.")
        return redirect("inventory:users")
    initial = {"username": user.username, "first_name": user.first_name, "last_name": user.last_name,
               "role": role_of(user), "active": user.is_active} if user else {}
    form = f.UserForm(request.POST or None, initial=initial, user=user)
    if form.is_valid():
        d = form.cleaned_data
        role = d["role"]
        if user and user == request.user and (role != "manager" or not d["active"]):
            messages.error(request, "You cannot remove your own manager access or switch yourself off.")
            return redirect("inventory:user_edit", pk=user.pk)
        if user and (role != "manager" or not d["active"]) and role_of(user) == "manager" and _managers_left(user) == 0:
            messages.error(request, "That would leave no manager. Make someone else a manager first.")
            return redirect("inventory:user_edit", pk=user.pk)
        creating = user is None
        before = None if creating else f"{ROLE_LABELS[role_of(user)]}, {'active' if user.is_active else 'off'}"
        if creating:
            user = U(username=d["username"])
        user.first_name, user.last_name = d["first_name"], d["last_name"]
        user.is_staff = role == "manager"
        user.is_active = d["active"]
        if d["password"]:
            user.set_password(d["password"])
        user.save()
        from django.contrib.auth.models import Group
        user.groups.remove(*Group.objects.filter(name__in=f.ROLE_GROUPS.values()))
        if role in f.ROLE_GROUPS:
            user.groups.add(Group.objects.get_or_create(name=f.ROLE_GROUPS[role])[0])
        after = f"{ROLE_LABELS[role]}, {'active' if user.is_active else 'off'}"
        audit.log(request, "user created" if creating else "user changed", user.username,
                  after if creating else f"{before} to {after}" + ("; password reset" if d["password"] else ""))
        messages.success(request, f"Saved {user.username}.")
        return redirect("inventory:users")
    return render(request, "inventory/form.html", ctx(request, title=f"User: {user.username}" if user else "New user",
                                                      form=form, back="/inventory/users/"))


@staff_required
def audit_log(request):
    from .models import AuditLog
    qs = AuditLog.objects.all()
    if u := request.GET.get("user"):
        qs = qs.filter(username__icontains=u)
    if a := request.GET.get("action"):
        qs = qs.filter(action__icontains=a)
    if q := request.GET.get("q"):
        qs = qs.filter(Q(subject__icontains=q) | Q(detail__icontains=q))
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "inventory/audit.html", ctx(request, page=page, g=request.GET))


# ---------------------------------------------------------------- import
@stock_manager_required
def import_products(request):
    form = f.ImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = importer.read_rows(request.FILES["file"])
        except Exception as e:
            messages.error(request, f"Could not read the file: {e}")
            rows = None
        if rows is not None:
            result = importer.import_rows(rows, form.cleaned_data["mode"], form.cleaned_data["skip_errors"])
            audit.log(request, "products imported", request.FILES["file"].name, form.cleaned_data["mode"])
            loc = Location.objects.filter(usage=Location.INTERNAL).first()
            for product, qty in result["opening"]:
                if product.tracks_stock and loc:
                    vend = Location.objects.filter(usage=Location.LOSS).first()
                    stock.apply_move(product, vend, loc, qty, reason="Opening stock", unit_cost=product.cost)
            if form.cleaned_data["register_zra"]:
                try:
                    ok, bad = zra.register_pending()
                    messages.info(request, f"ZRA registration: {ok} sent, {bad} failed. See ZRA Logs.")
                except zra.ZraDisabled as e:
                    messages.error(request, str(e))
    return render(request, "inventory/import.html", ctx(request, form=form, result=result))


@login_required
def import_template(request):
    resp = HttpResponse(importer.template_csv(), content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="product_import_template.csv"'
    return resp


# ---------------------------------------------------------------- ZRA logs
@login_required
def zra_logs(request):
    qs = ZraLog.objects.all()
    if s := request.GET.get("status"):
        qs = qs.filter(status=s)
    page = Paginator(qs, 80).get_page(request.GET.get("page"))
    return render(request, "inventory/zra_logs.html", ctx(request, page=page, status=request.GET.get("status", "")))


@login_required
def zra_log_detail(request, pk):
    return render(request, "inventory/zra_log_detail.html", ctx(request, log=get_object_or_404(ZraLog, pk=pk)))


@staff_required
@require_POST
def zra_register_pending(request):
    try:
        ok, bad = zra.register_pending()
        messages.info(request, f"ZRA registration finished: {ok} accepted, {bad} failed or blocked. See the log below.")
    except zra.ZraDisabled as e:
        messages.error(request, str(e))
    return redirect("inventory:zra_logs")


# ---------------------------------------------------------------- scanner (mobile PWA)
@login_required
def scan(request):
    return render(request, "inventory/scan.html", ctx(request))


@login_required
def lookup(request):
    code = request.GET.get("code", "").strip()
    p = Product.objects.filter(Q(barcode=code) | Q(internal_ref__iexact=code)).first()
    if not p:
        return JsonResponse({"found": False}, status=404)
    return JsonResponse({"found": True, "id": p.pk, "ref": p.internal_ref, "name": p.name, "price": str(p.price),
                         "on_hand": str(p.on_hand)})


# ---------------------------------------------------------------- site sync
@staff_required
def sync_peers(request):
    if request.method == "POST":
        peer = get_object_or_404(SyncPeer, pk=request.POST.get("peer"))
        ok, msg = sync_service.sync_peer(peer)
        (messages.success if ok else messages.error)(request, f"{peer.name}: {msg}")
        return redirect("inventory:sync")
    return render(request, "inventory/sync.html", ctx(request, peers=SyncPeer.objects.all()))


# ---------------------------------------------------------------- purchase orders
@stock_manager_required
def po_list(request):
    qs = PurchaseOrder.objects.select_related("supplier", "receipt").prefetch_related("lines")
    for po in qs.filter(state=PurchaseOrder.CONFIRMED):
        po.sync()
    if s := request.GET.get("state"):
        qs = qs.filter(state=s)
    if q := request.GET.get("q"):
        qs = qs.filter(Q(ref__icontains=q) | Q(supplier__name__icontains=q))
    rows = [([o.ref, o.supplier.name, o.order_date.isoformat(), o.expected_date.isoformat() if o.expected_date else "",
              f"{o.total:,.2f}", o.get_state_display()], f"/inventory/purchase/{o.pk}/") for o in qs]
    return table(request, "Purchase Orders", ["Reference", "Supplier", "Ordered", "Expected", "Total (ZMW)", "Status"], rows,
                 new_url="/inventory/purchase/new/", note="Filter: ?state=draft, ?state=confirmed, ?state=received or ?q=text. "
                 "To start from what is low on stock, use Replenishment, then Create purchase orders.")


@stock_manager_required
def po_edit(request, pk=None):
    order = get_object_or_404(PurchaseOrder, pk=pk) if pk else PurchaseOrder(created_by=request.user)
    if pk:
        order.sync()
    editable = order.state == PurchaseOrder.DRAFT
    if request.method == "POST" and editable:
        form = f.PurchaseOrderForm(request.POST, instance=order)
        lines = f.POLineFormSet(request.POST, instance=order)
        if form.is_valid() and lines.is_valid():
            creating = order.pk is None
            saved = form.save()
            lines.instance = saved
            lines.save()
            audit.log(request, "purchase order created" if creating else "purchase order changed", saved.ref,
                      f"{saved.supplier}, {saved.lines.count()} line(s), ZMW {saved.total:,.2f}")
            messages.success(request, f"Saved {saved.ref}")
            return redirect("inventory:po_edit", pk=saved.pk)
    else:
        form = f.PurchaseOrderForm(instance=order)
        lines = f.POLineFormSet(instance=order)
    return render(request, "inventory/po_form.html", ctx(request, order=order, form=form, lines=lines, editable=editable,
                                                         title="Purchase Orders", new=pk is None))


@stock_manager_required
@require_POST
def po_action(request, pk, action):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if action == "confirm" and order.state == PurchaseOrder.DRAFT:
        if not order.lines.exists():
            messages.error(request, "Add at least one product before ordering.")
        else:
            order.state = PurchaseOrder.CONFIRMED
            order.save(update_fields=["state"])
            audit.log(request, "purchase order placed", order.ref, f"{order.supplier}, ZMW {order.total:,.2f}")
            messages.success(request, f"{order.ref} marked as ordered.")
    elif action == "receive" and order.state == PurchaseOrder.CONFIRMED:
        if order.receipt_id is None:
            op = OperationType.objects.filter(code=OperationType.RECEIPT).first()
            if not op:
                messages.error(request, "Run: python manage.py seed_inventory")
                return redirect("inventory:po_edit", pk=pk)
            order.receipt = stock.new_picking(op, [(ln.product, ln.qty, ln.unit_cost) for ln in order.lines.all()],
                                              partner=order.supplier, origin=order.ref, user=request.user,
                                              notes=f"Goods for {order.ref}")
            order.save(update_fields=["receipt"])
            audit.log(request, "purchase order receiving", order.ref, order.receipt.ref)
        return redirect("inventory:picking_detail", pk=order.receipt_id)
    elif action == "cancel" and order.state in (PurchaseOrder.DRAFT, PurchaseOrder.CONFIRMED):
        if order.receipt_id and order.receipt.state == Picking.DRAFT:
            stock.cancel_picking(order.receipt)
        order.state = PurchaseOrder.CANCEL
        order.save(update_fields=["state"])
        audit.log(request, "purchase order cancelled", order.ref)
        messages.success(request, f"{order.ref} cancelled.")
    return redirect("inventory:po_edit", pk=pk)


@stock_manager_required
@require_POST
def po_from_replenishment(request):
    """One draft order per preferred supplier-less group: we have no supplier on products, so use the chosen supplier."""
    from .models import Partner, PurchaseOrderLine
    supplier = Partner.objects.filter(pk=request.POST.get("supplier"), is_supplier=True).first()
    if not supplier:
        messages.error(request, "Choose a supplier first (add one under Configuration, Contacts, tick Supplier).")
        return redirect("inventory:replenishment")
    items = stock.replenishment_candidates()
    if not items:
        messages.info(request, "Nothing is below its reorder minimum.")
        return redirect("inventory:replenishment")
    order = PurchaseOrder.objects.create(supplier=supplier, created_by=request.user, notes="Created from replenishment")
    for r in items:
        PurchaseOrderLine.objects.create(order=order, product=r["product"], qty=r["to_order"], unit_cost=r["product"].cost)
    audit.log(request, "purchase order created", order.ref, f"{supplier}, from replenishment, {len(items)} line(s)")
    messages.success(request, f"{order.ref} drafted with {len(items)} product(s). Check quantities and prices, then mark it ordered.")
    return redirect("inventory:po_edit", pk=order.pk)


# ---------------------------------------------------------------- backup and exports
@staff_required
def backup_download(request):
    """Manager-only: download a consistent copy of the whole database."""
    from django.http import FileResponse
    from .services.backup import BackupUnavailable, make_backup
    try:
        path = make_backup()
    except BackupUnavailable as e:
        messages.info(request, str(e))
        return redirect("inventory:data")
    audit.log(request, "database backup downloaded", path.name)
    resp = FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)
    path.unlink(missing_ok=True)  # the open handle keeps the data readable on Linux
    return resp


@login_required
def export_xlsx(request, kind):
    from datetime import date
    from .services import exports
    # money and sales detail are for managers; a storekeeper may take the product and stock lists
    allowed = {"products", "valuation", "moves", "purchases", "locations", "analysis"} if can_manage_stock(request.user) else set()
    if request.user.is_staff:
        allowed = set(exports.BUILDERS)
    if kind not in exports.BUILDERS or kind not in allowed:
        raise Http404
    args = []
    if kind == "sales":
        for key in ("from", "to"):
            try:
                args.append(date.fromisoformat(request.GET.get(key, "")))
            except ValueError:
                args.append(None)
    title, header, rows = exports.BUILDERS[kind](*args)
    audit.log(request, "export downloaded", title, f"{len(rows)} row(s)")
    resp = HttpResponse(exports.to_xlsx(title, header, rows),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="alifaris-{kind}-{date.today():%Y%m%d}.xlsx"'
    return resp


@stock_manager_required
def data_page(request):
    from .services import exports
    return render(request, "inventory/data.html", ctx(request, title="Backup and exports", kinds=[
        ("products", "Products and prices"), ("valuation", "Stock valuation"), ("moves", "Stock movements"),
        ("purchases", "Purchase orders"), ("sales", "Sales with profit"), ("receivables", "Customers who owe")],
        is_manager=request.user.is_staff, can_backup=__import__("inventory.services.backup", fromlist=["x"]).is_sqlite(),
        demo_mode=request.user.is_staff and demo_service.enabled(), demo_running=demo_service.running()))


@staff_required
@require_POST
def demo_reset(request):
    try:
        demo_service.reset_in_background()
    except demo_service.DemoResetError as e:
        messages.error(request, str(e))
        return redirect("inventory:data")
    messages.success(request, "Resetting the demo. This takes about a minute: you will be logged out. Log in again as manager.")
    return redirect("inventory:data")
