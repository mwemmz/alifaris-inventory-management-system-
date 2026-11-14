"""POS business rules. Sales and refunds move stock through the same ledger as the Inventory screens."""
import uuid as uuidlib
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import Location, OperationType, Partner, Product, ZraConfig, ZraLog
from inventory.services import stock, zra
from inventory.services.stock import StockError

from . import receivables
from .models import PosOrder, PosOrderLine, PosPayment, PosSession

CENT = Decimal("0.01")
GROUP_CASHIER = "Cashier"


class PosError(Exception):
    """Something the cashier can fix. The message is shown on screen."""


def money(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def is_manager(user):
    return user.is_active and user.is_staff


def can_sell(user):
    return user.is_authenticated and user.is_active and (
        user.is_staff or user.groups.filter(name=GROUP_CASHIER).exists())


def price_line(qty, unit_price, discount_pct, tax_type):
    """Return (line_total, tax_amount). Prices include VAT unless POS_PRICES_INCLUDE_TAX is off."""
    rate = Decimal(settings.POS_TAX_RATES.get(tax_type, 0))
    gross = money(qty * unit_price * (Decimal(100) - discount_pct) / 100)
    if settings.POS_PRICES_INCLUDE_TAX:
        tax = money(gross * rate / (100 + rate))
        return gross, tax
    tax = money(gross * rate / 100)
    return gross + tax, tax


def open_session(user, warehouse, opening_cash=0):
    if PosSession.objects.filter(cashier=user, state=PosSession.OPEN).exists():
        raise PosError("You already have an open session.")
    return PosSession.objects.create(cashier=user, warehouse=warehouse, opening_cash=money(opening_cash))


def current_session(user):
    return PosSession.objects.filter(cashier=user, state=PosSession.OPEN).select_related("warehouse").first()


def session_summary(session):
    """Totals for the cash-up screen. Refunds are stored as negative amounts, so plain sums are net."""
    pays = (PosPayment.objects.filter(order__session=session).values("method").annotate(t=Sum("amount")))
    by_method = {r["method"]: r["t"] for r in pays}
    orders = session.orders.all()
    sales = orders.filter(kind=PosOrder.SALE)
    refunds = orders.filter(kind=PosOrder.REFUND)
    change = orders.aggregate(c=Sum("change_due"))["c"] or Decimal("0")
    # cash payments are recorded as handed over, so change given back leaves the drawer again
    cash_in_drawer = (by_method.get(PosPayment.CASH, Decimal("0")) - change)
    return {"by_method": by_method, "sales_count": sales.count(), "refund_count": refunds.count(),
            "total": orders.aggregate(t=Sum("total"))["t"] or Decimal("0"),
            "tax": orders.aggregate(t=Sum("tax_total"))["t"] or Decimal("0"),
            "cash_in_drawer": cash_in_drawer, "expected_cash": session.opening_cash + cash_in_drawer}


@transaction.atomic
def close_session(session, counted_cash, user):
    if session.state != PosSession.OPEN:
        raise PosError("This session is already closed.")
    if session.cashier_id != user.pk and not is_manager(user):
        raise PosError("Only the cashier or a manager can close this session.")
    summary = session_summary(session)
    session.expected_cash = money(summary["expected_cash"])
    session.counted_cash = money(counted_cash)
    session.closed_at = timezone.now()
    session.state = PosSession.CLOSED
    session.save()
    return session


def _delivery_ops(session):
    op = OperationType.objects.filter(code=OperationType.DELIVERY, warehouse=session.warehouse).first()
    rcp = OperationType.objects.filter(code=OperationType.RECEIPT, warehouse=session.warehouse).first()
    if not op or not rcp:
        raise PosError("This warehouse has no delivery or receipt operation. Run seed_inventory.")
    return op, rcp


@transaction.atomic
def create_sale(session, user, lines, payments, *, customer=None, customer_name="", customer_tpin="",
                tier="primary", client_uuid=None, note="", created_at=None):
    """Create a paid sale and take the stock out. Sending the same client_uuid twice returns the first sale.

    lines:    [{"product": id, "qty": n, "price": optional, "discount": optional percent}]
    payments: [{"method": "cash", "amount": n}]
    """
    if client_uuid:
        existing = PosOrder.objects.filter(uuid=client_uuid).first()
        if existing:
            return existing, False
    if session.state != PosSession.OPEN:
        raise PosError("The session is closed. Open a new session to keep selling.")
    if not lines:
        raise PosError("The cart is empty.")
    order = PosOrder(kind=PosOrder.SALE, session=session, cashier_name=user.get_full_name() or user.get_username(),
                     customer=customer, customer_name=customer_name or (customer.name if customer else ""),
                     customer_tpin=customer_tpin or (customer.tpin if customer else ""), price_tier=tier, note=note,
                     site_code=settings.SITE_CODE)
    if client_uuid:
        order.uuid = client_uuid
    if created_at:
        order.created_at = created_at
    order.save()

    manager = is_manager(user)
    total = tax_total = discount_total = Decimal("0")
    stock_lines = []
    for ln in lines:
        try:
            product = Product.objects.get(pk=ln["product"], active=True)
            qty = Decimal(str(ln["qty"]))
        except (Product.DoesNotExist, KeyError, ArithmeticError):
            raise PosError("A product in the cart no longer exists.")
        if qty <= 0:
            raise PosError(f"Quantity for {product.name} must be more than zero.")
        listed = product.secondary_price if tier == "secondary" and product.secondary_price else product.price
        price = money(ln["price"]) if manager and ln.get("price") not in (None, "") else listed
        disc = Decimal(str(ln.get("discount") or 0))
        if disc < 0 or disc > 100:
            raise PosError("Discount must be between 0 and 100 percent.")
        if disc > settings.POS_MAX_DISCOUNT_CASHIER and not manager:
            raise PosError(f"Discounts above {settings.POS_MAX_DISCOUNT_CASHIER}% need a manager.")
        line_total, tax = price_line(qty, price, disc, product.tax_type)
        PosOrderLine.objects.create(order=order, product=product, ref=product.internal_ref, name=product.name[:200],
                                    qty=qty, unit_price=price, unit_cost=product.cost, discount_pct=disc, tax_type=product.tax_type,
                                    tax_amount=tax, line_total=line_total)
        total += line_total
        tax_total += tax
        discount_total += money(qty * price) - money(qty * price * (100 - disc) / 100)
        if product.tracks_stock:
            stock_lines.append((product, qty))

    paid = Decimal("0")
    cash_paid = Decimal("0")
    for p in payments:
        amt = money(p["amount"])
        if amt <= 0:
            continue
        if p["method"] not in dict(PosPayment.METHODS):
            raise PosError(f"Unknown payment method: {p['method']}")
        if p["method"] == PosPayment.CREDIT:
            if not customer:
                raise PosError("Choose a customer from the list for a sale on account, so what they owe can be tracked.")
            receivables.check_limit(customer, amt)
        PosPayment.objects.create(order=order, method=p["method"], amount=amt)
        paid += amt
        if p["method"] == PosPayment.CASH:
            cash_paid += amt
    if paid < total:
        raise PosError(f"Payment is short by {total - paid:.2f}.")
    change = paid - total
    if change > cash_paid:
        raise PosError("Only cash can be overpaid. Check the payment amounts.")
    order.total, order.tax_total, order.discount_total = total, tax_total, discount_total
    order.paid_total, order.change_due = paid, change

    if stock_lines:
        delivery, _ = _delivery_ops(session)
        try:
            picking = stock.new_picking(delivery, stock_lines, origin=order.number, user=user,
                                        notes=f"POS sale {order.number}")
            stock.validate_picking(picking)
        except StockError as e:
            raise PosError(str(e))
        order.picking = picking
    order.zra_status = PosOrder.ZRA_PENDING if ZraConfig.get().enabled else PosOrder.ZRA_NA
    order.save()
    return order, True


@transaction.atomic
def refund_order(original, user, reason=""):
    """Full refund of a sale: money out (negative payments), stock back in. Managers only."""
    if not is_manager(user):
        raise PosError("Only a manager can refund a sale.")
    if original.kind != PosOrder.SALE:
        raise PosError("Only sales can be refunded.")
    if hasattr(original, "refunded_by"):
        raise PosError("This sale has already been refunded.")
    session = current_session(user)
    if not session:
        raise PosError("Open a session first: the refund comes out of your till.")
    order = PosOrder.objects.create(
        kind=PosOrder.REFUND, refund_of=original, session=session, cashier_name=user.get_username(),
        customer=original.customer, customer_name=original.customer_name, customer_tpin=original.customer_tpin,
        price_tier=original.price_tier, note=reason[:200], site_code=settings.SITE_CODE,
        total=-original.total, tax_total=-original.tax_total, discount_total=-original.discount_total,
        paid_total=-original.total, change_due=0)
    returns = []
    for ln in original.lines.select_related("product"):
        PosOrderLine.objects.create(order=order, product=ln.product, ref=ln.ref, name=ln.name, qty=-ln.qty,
                                    unit_price=ln.unit_price, unit_cost=ln.unit_cost, discount_pct=ln.discount_pct, tax_type=ln.tax_type,
                                    tax_amount=-ln.tax_amount, line_total=-ln.line_total)
        if ln.product.tracks_stock:
            returns.append((ln.product, ln.qty))
    # money goes back the way it came in; any change handed over originally was never the shop's money
    net = {}
    for p in original.payments.all():
        net[p.method] = net.get(p.method, Decimal("0")) + p.amount
    net[PosPayment.CASH] = net.get(PosPayment.CASH, Decimal("0")) - original.change_due
    for method, amt in net.items():
        if amt > 0:
            PosPayment.objects.create(order=order, method=method, amount=-amt)
    if returns:
        _, receipt = _delivery_ops(session)
        customers = Location.objects.filter(usage=Location.CUSTOMER).first()
        picking = stock.new_picking(receipt, returns, origin=order.number, source=customers, user=user,
                                    notes=f"POS refund {order.number}")
        stock.validate_picking(picking)
        order.picking = picking
    order.zra_status = PosOrder.ZRA_PENDING if ZraConfig.get().enabled else PosOrder.ZRA_NA
    order.save()
    return order


# ------------------------------------------------------------------ ZRA Smart Invoice (sales)
def build_sale_payload(order, cfg):
    """Field names follow the vsdcsdk saveSales schema. Not yet tested against a live VSDC."""
    now = order.created_at.astimezone()
    sign = -1 if order.kind == PosOrder.REFUND else 1
    totals = {t: [Decimal("0"), Decimal("0")] for t in ("A", "B", "C1", "C2", "C3", "D")}
    items = []
    for i, ln in enumerate(order.lines.select_related("product", "product__uom"), start=1):
        tax = ln.tax_amount * sign
        gross = ln.line_total * sign
        taxable = gross - tax
        slot = totals.setdefault(ln.tax_type, [Decimal("0"), Decimal("0")])
        slot[0] += taxable
        slot[1] += tax
        uom = ln.product.uom
        items.append({"itemSeq": i, "itemCd": ln.ref, "itemClsCd": ln.product.zra_item_class_code, "itemNm": ln.name,
                      "bcd": ln.product.barcode or None, "pkgUnitCd": uom.zra_pkg_code if uom else "NT", "pkg": 1,
                      "qtyUnitCd": uom.zra_qty_code if uom else "U", "qty": float(ln.qty * sign),
                      "prc": float(ln.unit_price), "splyAmt": float(money(ln.qty * sign * ln.unit_price)),
                      "dcRt": float(ln.discount_pct),
                      "dcAmt": float(money(ln.qty * sign * ln.unit_price) - gross),
                      "taxTyCd": ln.tax_type, "taxblAmt": float(taxable), "taxAmt": float(tax), "totAmt": float(gross)})
    main = order.payments.order_by("-amount").first()
    pay_code = PosPayment.ZRA_CODES.get(main.method, "07") if main else "01"
    payload = {"tpin": cfg.tpin, "bhfId": cfg.branch_id, "invcNo": order.pk,
               "orgInvcNo": order.refund_of_id or 0, "custTpin": order.customer_tpin or None,
               "custNm": order.customer_name or None, "salesTyCd": "N", "rcptTyCd": "R" if sign < 0 else "S",
               "pmtTyCd": pay_code, "salesSttsCd": "02", "cfmDt": now.strftime("%Y%m%d%H%M%S"),
               "salesDt": now.strftime("%Y%m%d"), "totItemCnt": len(items),
               "totTaxblAmt": float(sum(v[0] for v in totals.values())), "totTaxAmt": float(order.tax_total * sign),
               "totAmt": float(order.total * sign), "prchrAcptcYn": "N", "remark": order.note or None,
               "regrNm": order.cashier_name[:60], "regrId": order.cashier_name[:20],
               "modrNm": order.cashier_name[:60], "modrId": order.cashier_name[:20], "itemList": items}
    if sign < 0:
        payload.update({"rfdDt": now.strftime("%Y%m%d%H%M%S"), "rfdRsnCd": "01"})
    for code, (taxable, tax) in totals.items():
        payload[f"taxblAmt{code}"] = float(taxable)
        payload[f"taxAmt{code}"] = float(tax)
        payload[f"taxRt{code}"] = settings.POS_TAX_RATES.get(code, 0)
    return payload


def check_order_for_zra(order):
    problems = []
    for ln in order.lines.select_related("product"):
        code = (ln.product.zra_item_class_code or "").strip()
        if len(code) != 10:
            problems.append(f"{ln.ref}: item class code must be exactly 10 characters")
    return problems


def submit_to_zra(order):
    """Send one sale or refund to the VSDC. Never raises for a business or network problem: it records the outcome."""
    cfg = ZraConfig.get()
    if order.remote or not cfg.enabled:
        return False
    problems = check_order_for_zra(order)
    if problems:
        msg = "Not sent. " + "; ".join(problems[:3])
        ZraLog.objects.create(tpin=cfg.tpin, branch=cfg.branch_id, model="pos.posorder", record_id=str(order.pk),
                              endpoint=cfg.base_url.rstrip("/") + "/trnsSales/saveSales", status=ZraLog.ERROR, message=msg)
        order.zra_status, order.zra_message = PosOrder.ZRA_FAILED, msg[:300]
        order.save(update_fields=["zra_status", "zra_message"])
        return False
    try:
        ok, msg = zra.post("/trnsSales/saveSales", build_sale_payload(order, cfg), model="pos.posorder", record_id=order.pk)
    except zra.ZraDisabled:
        return False
    order.zra_status = PosOrder.ZRA_SENT if ok else PosOrder.ZRA_FAILED
    order.zra_message = msg[:300]
    order.save(update_fields=["zra_status", "zra_message"])
    return ok


def send_pending_sales(limit=10):
    """Try the oldest unsent sales. Returns (sent, failed). Safe to call often."""
    sent = failed = 0
    qs = PosOrder.objects.filter(remote=False, zra_status__in=[PosOrder.ZRA_PENDING, PosOrder.ZRA_FAILED]).order_by("id")[:limit]
    for order in qs:
        if submit_to_zra(order):
            sent += 1
        else:
            failed += 1
    return sent, failed


# ------------------------------------------------------------------ sync of sales records between sites
def order_to_dict(o):
    return {"uuid": str(o.uuid), "number": o.number, "kind": o.kind,
            "refund_of": str(o.refund_of.uuid) if o.refund_of_id else None, "cashier": o.cashier_name,
            "customer_name": o.customer_name, "customer_tpin": o.customer_tpin, "tier": o.price_tier,
            "tax_total": str(o.tax_total), "discount_total": str(o.discount_total), "total": str(o.total),
            "paid_total": str(o.paid_total), "change_due": str(o.change_due), "note": o.note,
            "created_at": o.created_at.isoformat(), "recorded_at": o.recorded_at.isoformat(), "site": o.site_code,
            "lines": [{"ref": l.ref, "name": l.name, "qty": str(l.qty), "unit_price": str(l.unit_price),
                       "unit_cost": str(l.unit_cost), "discount_pct": str(l.discount_pct), "tax_type": l.tax_type, "tax_amount": str(l.tax_amount),
                       "line_total": str(l.line_total)} for l in o.lines.all()],
            "payments": [{"method": p.method, "amount": str(p.amount)} for p in o.payments.all()]}


def _match_customer(name, tpin):
    """Link a sale from another site to the customer record here, so what they owe adds up across sites."""
    qs = Partner.objects.filter(is_customer=True)
    if tpin:
        found = qs.filter(tpin=tpin).first()
        if found:
            return found
    return qs.filter(name=name).first() if name else None


@transaction.atomic
def import_remote_orders(rows):
    """Store sales made at other sites for reporting. Their stock effect arrives through stock-move sync, never here."""
    added = 0
    for r in sorted(rows, key=lambda r: r["kind"] != PosOrder.SALE):  # sales before the refunds that point at them
        if PosOrder.objects.filter(uuid=r["uuid"]).exists():
            continue
        refund_of = PosOrder.objects.filter(uuid=r["refund_of"]).first() if r.get("refund_of") else None
        o = PosOrder.objects.create(
            uuid=r["uuid"], number=r["number"], kind=r["kind"], refund_of=refund_of, session=None,
            customer=_match_customer(r.get("customer_name", ""), r.get("customer_tpin", "")),
            cashier_name=r["cashier"], customer_name=r.get("customer_name", ""), customer_tpin=r.get("customer_tpin", ""),
            price_tier=r.get("tier", "primary"), tax_total=r["tax_total"], discount_total=r["discount_total"],
            total=r["total"], paid_total=r["paid_total"], change_due=r["change_due"], note=r.get("note", ""),
            created_at=r["created_at"], site_code=r["site"], remote=True)
        for l in r["lines"]:
            product = Product.objects.get(internal_ref=l["ref"])
            PosOrderLine.objects.create(order=o, product=product, ref=l["ref"], name=l["name"], qty=l["qty"],
                                        unit_price=l["unit_price"], unit_cost=l.get("unit_cost") or product.cost,
                                        discount_pct=l["discount_pct"],
                                        tax_type=l["tax_type"], tax_amount=l["tax_amount"], line_total=l["line_total"])
        for p in r["payments"]:
            PosPayment.objects.create(order=o, method=p["method"], amount=p["amount"])
        added += 1
    return added
