from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import forms as f
from .models import (SyncPeer, ApiToken, LandedCost, Location, OperationType, Picking, Product, Scrap, StockMove, StockQuant,
                     ZraConfig, ZraLog)
from .services import importer, stock, sync as sync_service, zra
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


def table(request, title, headers, rows, *, new_url=None, new_label="New", back=None, note=None, actions=None):
    """Generic list page. rows = list of (cells, url_or_None)."""
    page = Paginator(rows, 80).get_page(request.GET.get("page"))
    return render(request, "inventory/table.html", ctx(request, title=title, headers=headers, page=page,
                                                        new_url=new_url, new_label=new_label, note=note,
                                                        actions=actions or []))


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
            messages.success(request, f"{picking.ref} validated. Stock updated.")
            cfg = ZraConfig.get()
            if cfg.enabled and cfg.push_stock_after_validate:
                for ln in picking.lines.select_related("product"):
                    zra.push_stock_level(ln.product, request.user.get_username())
        elif action == "cancel":
            stock.cancel_picking(picking)
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
                 actions=[{"label": "Create draft receipt", "post": True}] if rows else [])


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
        saved = form.save()
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
    qs = Product.objects.filter(product_type=Product.GOODS, active=True)
    if q := request.GET.get("q"):
        qs = qs.filter(Q(name__icontains=q) | Q(internal_ref__iexact=q))
    rows = [([p.internal_ref, p.name, p.on_hand, p.uom or ""], f"/inventory/products/{p.pk}/") for p in qs]
    return table(request, "Stock", ["Ref", "Product", "On hand", "Unit"], rows, note="Search with ?q=text")


@login_required
def report_locations(request):
    qs = StockQuant.objects.exclude(qty=0).select_related("product", "location", "location__warehouse")
    if loc := request.GET.get("location"):
        qs = qs.filter(location_id=loc)
    rows = [([q.location.full_name, q.product.internal_ref, q.product.name, q.qty], None) for q in qs.order_by("location_id", "product__name")]
    return table(request, "Stock by location", ["Location", "Ref", "Product", "Quantity"], rows)


@login_required
def report_moves(request):
    qs = StockMove.objects.select_related("product", "source", "dest", "picking", "picking__op_type")
    if q := request.GET.get("q"):
        qs = qs.filter(Q(product__name__icontains=q) | Q(product__internal_ref__iexact=q) | Q(picking__ref__icontains=q))
    rows = [([m.date.strftime("%Y-%m-%d %H:%M"), m.picking.ref if m.picking_id else m.reason, m.product,
              m.source.full_name, m.dest.full_name, m.qty, m.site_code], None) for m in qs[:2000]]
    return table(request, "Moves History", ["Date", "Reference", "Product", "From", "To", "Quantity", "Site"], rows,
                 note="Newest 2000 moves shown. Search with ?q=text")


@login_required
def report_analysis(request):
    inn = Location.INTERNAL
    qs = (StockMove.objects.values("product__internal_ref", "product__name")
          .annotate(received=Sum("qty", filter=Q(dest__usage=inn) & ~Q(source__usage=inn)),
                    sent=Sum("qty", filter=Q(source__usage=inn) & ~Q(dest__usage=inn)), n=Count("id"))
          .order_by("product__name"))
    rows = [([r["product__internal_ref"], r["product__name"], r["received"] or 0, r["sent"] or 0, r["n"]], None) for r in qs]
    return table(request, "Moves Analysis", ["Ref", "Product", "Total in", "Total out", "Moves"], rows,
                 note="Quantities entering and leaving your internal locations (transfers between your own locations are excluded).")


@login_required
def report_valuation(request):
    data = stock.stock_valuation()
    rows = [([r["product"].internal_ref, r["product"].name, r["qty"], r["cost"], f"{r['value']:.2f}"], None) for r in data]
    total = sum((r["value"] for r in data), Decimal("0"))
    return table(request, "Valuation", ["Ref", "Product", "On hand", "Unit cost", "Value"], rows,
                 note=f"Total stock value: {total:,.2f} ZK (on hand x average cost)")


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
        form.save()
        messages.success(request, "Saved")
        return redirect("inventory:crud_list", key=key)
    return render(request, "inventory/form.html", ctx(request, title=title, form=form, back=f"/inventory/config/{key}/"))


@staff_required
def zra_settings(request):
    cfg = ZraConfig.get()
    form = f.ZraConfigForm(request.POST or None, instance=cfg)
    if form.is_valid():
        form.save()
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
        elif form.is_valid():
            form.save()
        return redirect("inventory:tokens")
    return render(request, "inventory/tokens.html", ctx(request, tokens=ApiToken.objects.all(), form=f.TokenForm()))


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
