import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from inventory import audit
from inventory.models import Partner, Product, Warehouse, ZraConfig

from . import receivables, services
from .models import CustomerPayment, PosOrder, PosPayment, PosSession
from .services import PosError


def seller_required(view):
    @login_required
    def wrapper(request, *a, **kw):
        if not services.can_sell(request.user):
            messages.error(request, "Your account cannot sell. Ask a manager to add you to the Cashier group.")
            return redirect("/")
        return view(request, *a, **kw)
    wrapper.__name__ = view.__name__
    return wrapper


def manager_required(view):
    @login_required
    def wrapper(request, *a, **kw):
        if not services.is_manager(request.user):
            messages.error(request, "Only a manager can open that page.")
            return redirect("pos:terminal")
        return view(request, *a, **kw)
    wrapper.__name__ = view.__name__
    return wrapper


def _ctx(request, title, **extra):
    return {"title": title, "session": services.current_session(request.user),
            "is_manager": services.is_manager(request.user), **extra}


# ------------------------------------------------------------------ till
@seller_required
def terminal(request):
    session = services.current_session(request.user)
    if not session:
        return redirect("pos:session_open")
    ctx = _ctx(request, "Till", partners=Partner.objects.filter(is_customer=True).order_by("name")[:300],
               max_discount=100 if services.is_manager(request.user) else settings.POS_MAX_DISCOUNT_CASHIER,
               zra_on=ZraConfig.get().enabled)
    return render(request, "pos/terminal.html", ctx)


@seller_required
def catalog(request):
    """Whole price list for the till to keep on the device, so it keeps working when the network drops."""
    rows = []
    for p in Product.objects.filter(active=True).select_related("uom"):
        rows.append({"id": p.pk, "ref": p.internal_ref, "name": p.name, "barcode": p.barcode or "",
                     "price": str(p.price), "price2": str(p.secondary_price or ""), "tax": p.tax_type,
                     "stock": str(p.on_hand) if p.tracks_stock else None, "image": p.image.url if p.image else ""})
    return JsonResponse({"generated": timezone.now().isoformat(), "products": rows,
                         "inclusive": settings.POS_PRICES_INCLUDE_TAX, "rates": settings.POS_TAX_RATES})


def _parse_dt(value):
    from django.utils.dateparse import parse_datetime
    dt = parse_datetime(value) if value else None
    return dt


@seller_required
@require_POST
def sale_api(request):
    try:
        data = json.loads(request.body)
        lines = data["lines"]
        payments = data["payments"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "Bad request."}, status=400)
    session = PosSession.objects.filter(uuid=data.get("session")).first() if data.get("session") else None
    if not session or session.state != PosSession.OPEN:
        session = services.current_session(request.user)
    if not session:
        return JsonResponse({"ok": False, "error": "No open session. Open one, then the sale will go through.", "retry": True}, status=409)
    customer = Partner.objects.filter(pk=data["customer"]).first() if data.get("customer") else None
    try:
        order, created = services.create_sale(
            session, request.user, lines, payments, customer=customer, customer_name=(data.get("customer_name") or "")[:120],
            customer_tpin=(data.get("customer_tpin") or "")[:10], tier=data.get("tier") or "primary",
            client_uuid=data.get("uuid"), note=(data.get("note") or "")[:200], created_at=_parse_dt(data.get("created_at")))
    except PosError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=422)
    except (InvalidOperation, ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "A number in the sale is not valid."}, status=400)
    return JsonResponse({"ok": True, "created": created, "number": order.number, "id": order.pk,
                         "total": str(order.total), "change": str(order.change_due),
                         "receipt": f"/pos/orders/{order.pk}/receipt/"})


# ------------------------------------------------------------------ sessions
@seller_required
def session_open(request):
    if services.current_session(request.user):
        return redirect("pos:terminal")
    warehouses = Warehouse.objects.all()
    if request.method == "POST":
        wh = get_object_or_404(Warehouse, pk=request.POST.get("warehouse"))
        try:
            services.open_session(request.user, wh, Decimal(request.POST.get("opening_cash") or 0))
        except (PosError, InvalidOperation) as e:
            messages.error(request, str(e) or "Opening cash is not a number.")
        else:
            return redirect("pos:terminal")
    return render(request, "pos/session_open.html", _ctx(request, "Open session", warehouses=warehouses))


@seller_required
def session_close(request, pk=None):
    session = get_object_or_404(PosSession, pk=pk) if pk else services.current_session(request.user)
    if not session:
        return redirect("pos:session_open")
    summary = services.session_summary(session)
    if request.method == "POST" and session.state == PosSession.OPEN:
        try:
            services.close_session(session, Decimal(request.POST.get("counted") or 0), request.user)
        except (PosError, InvalidOperation) as e:
            messages.error(request, str(e) or "Counted cash is not a number.")
        else:
            diff = session.difference
            audit.log(request, "session closed", session,
                      f"expected {session.expected_cash}, counted {session.counted_cash}"
                      + (f", DIFFERENCE {diff}" if diff else ""))
            messages.success(request, "Session closed.")
            return redirect("pos:session_close", pk=session.pk)
    return render(request, "pos/session_close.html", _ctx(request, "Cash up", s=session, summary=summary,
                                                          methods=dict(PosPayment.METHODS)))


@manager_required
def session_list(request):
    return render(request, "pos/session_list.html", _ctx(request, "Sessions", sessions=PosSession.objects.select_related("cashier")[:200]))


# ------------------------------------------------------------------ orders
@seller_required
def order_list(request):
    qs = PosOrder.objects.select_related("session")
    if not services.is_manager(request.user):
        qs = qs.filter(session__cashier=request.user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(number__icontains=q) | qs.filter(customer_name__icontains=q)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "pos/order_list.html", _ctx(request, "Orders", page=page, q=q))


@seller_required
def order_detail(request, pk):
    order = get_object_or_404(PosOrder, pk=pk)
    return render(request, "pos/order_detail.html", _ctx(request, order.number, o=order))


@seller_required
def receipt(request, pk):
    order = get_object_or_404(PosOrder, pk=pk)
    return render(request, "pos/receipt.html", {"o": order, "company": settings.COMPANY_NAME, "cfg": ZraConfig.get(),
                                                 "autoprint": request.GET.get("print") == "1"})


@manager_required
@require_POST
def refund(request, pk):
    order = get_object_or_404(PosOrder, pk=pk)
    try:
        r = services.refund_order(order, request.user, request.POST.get("reason", ""))
    except PosError as e:
        messages.error(request, str(e))
        return redirect("pos:order_detail", pk=pk)
    audit.log(request, "sale refunded", order.number, f"as {r.number}, K{order.total}; {request.POST.get('reason', '')}")
    messages.success(request, f"Refunded as {r.number}.")
    return redirect("pos:order_detail", pk=r.pk)


@manager_required
@require_POST
def zra_flush(request):
    sent, failed = services.send_pending_sales(limit=20)
    return JsonResponse({"sent": sent, "failed": failed})


# ------------------------------------------------------------------ report
@manager_required
def report(request):
    days = int(request.GET.get("days") or 14)
    since = timezone.now() - timedelta(days=days)
    orders = PosOrder.objects.filter(created_at__gte=since)
    daily = (orders.annotate(d=TruncDate("created_at")).values("d")
             .annotate(n=Count("id"), total=Sum("total"), tax=Sum("tax_total")).order_by("-d"))
    methods = (PosPayment.objects.filter(order__created_at__gte=since).values("method").annotate(t=Sum("amount")).order_by("-t"))
    labels = dict(PosPayment.METHODS)
    change = orders.aggregate(c=Sum("change_due"))["c"] or 0  # cash is recorded as handed over; change went back out
    top = (orders.filter(kind=PosOrder.SALE).values("lines__ref", "lines__name")
           .annotate(q=Sum("lines__qty"), t=Sum("lines__line_total")).order_by("-t")[:15])
    waiting = PosOrder.objects.filter(remote=False, zra_status__in=["pending", "failed"]).count()
    return render(request, "pos/report.html", _ctx(
        request, "Sales report", daily=daily, days=days, top=top, waiting=waiting,
        methods=[{"label": labels.get(m["method"], m["method"]),
                  "t": m["t"] - (change if m["method"] == PosPayment.CASH else 0)} for m in methods],
        grand=orders.aggregate(t=Sum("total"), tax=Sum("tax_total"), n=Count("id"))))


# ------------------------------------------------------------------ customers on account
@manager_required
def customers(request):
    rows = receivables.summary()
    return render(request, "pos/customers.html", _ctx(
        request, "Customers on account", rows=rows, total=sum((r["balance"] for r in rows), Decimal("0")),
        overdue=sum((r["overdue"] for r in rows), Decimal("0"))))


@manager_required
def customer_statement(request, pk):
    customer = get_object_or_404(Partner, pk=pk, is_customer=True)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "payment":
                pay = receivables.record_payment(
                    customer, Decimal(request.POST.get("amount") or 0), request.POST.get("method") or "cash",
                    request.user, reference=request.POST.get("reference", ""), note=request.POST.get("note", ""))
                audit.log(request, "payment received", f"{customer.name}", f"K{pay.amount} by {pay.get_method_display()}")
                messages.success(request, f"Recorded {pay.amount:.2f} from {customer.name}.")
            elif action == "limit":
                old = customer.credit_limit
                customer.credit_limit = Decimal(request.POST.get("credit_limit") or 0)
                customer.save(update_fields=["credit_limit"])
                audit.log(request, "credit limit changed", customer.name, f"{old} to {customer.credit_limit}")
                messages.success(request, "Credit limit saved.")
        except (PosError, InvalidOperation) as e:
            messages.error(request, str(e) or "That amount is not a number.")
        return redirect("pos:customer_statement", pk=pk)
    rows = receivables.statement(customer)
    return render(request, "pos/customer_statement.html", _ctx(
        request, f"Statement: {customer.name}", customer=customer, rows=rows, balance=receivables.balance(customer),
        aging=receivables.aging(customer), methods=CustomerPayment.METHODS, today=timezone.now()))
