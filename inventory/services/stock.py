"""Stock engine. Every change to stock goes through apply_move()."""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from ..models import (LandedCost, Location, OperationType, Picking, PickingLine, Product, Scrap, StockMove,
                      StockQuant)


class StockError(Exception):
    pass


def _bump(product, location, delta):
    """Add delta to the quant of an internal location (other locations are virtual and not tracked)."""
    if not location.is_internal:
        return
    quant, _ = StockQuant.objects.select_for_update().get_or_create(product=product, location=location)
    if delta < 0 and not settings.ALLOW_NEGATIVE_STOCK and quant.qty + delta < 0:
        raise StockError(f"Not enough stock of {product} in {location}: have {quant.qty}, need {-delta}")
    quant.qty = F("qty") + delta
    quant.save(update_fields=["qty"])


@transaction.atomic
def apply_move(product, source, dest, qty, *, picking=None, reason="", uuid=None, date=None,
               unit_cost=None, site_code=None):
    """Record one ledger row and update quants. Idempotent when a uuid is given."""
    qty = Decimal(qty)
    if qty <= 0:
        raise StockError("Quantity must be greater than zero")
    if not product.tracks_stock:
        raise StockError(f"{product} is a service and has no stock")
    if source_id_same(source, dest):
        raise StockError("Source and destination are the same location")
    if uuid:
        existing = StockMove.objects.filter(uuid=uuid).first()
        if existing:
            return existing, False
    fields = dict(product=product, source=source, dest=dest, qty=qty, picking=picking, reason=reason,
                  unit_cost=product.cost if unit_cost is None else unit_cost)
    if uuid:
        fields["uuid"] = uuid
    if date:
        fields["date"] = date
    if site_code:
        fields["site_code"] = site_code
    move = StockMove.objects.create(**fields)
    _bump(product, source, -qty)
    _bump(product, dest, qty)
    return move, True


def source_id_same(a, b):
    return a.pk == b.pk


def _average_cost(product, qty_in, cost_in):
    old_qty = max(product.on_hand - qty_in, Decimal("0"))  # on_hand already includes the receipt
    if cost_in is None or cost_in <= 0:
        return product.cost
    total = old_qty + qty_in
    if total <= 0:
        return cost_in
    return ((old_qty * product.cost) + (qty_in * cost_in)) / total


@transaction.atomic
def validate_picking(picking):
    picking = Picking.objects.select_for_update().get(pk=picking.pk)
    if picking.state != Picking.DRAFT:
        raise StockError(f"{picking.ref} is already {picking.get_state_display().lower()}")
    lines = list(picking.lines.select_related("product"))
    if not lines:
        raise StockError("Add at least one product line first")
    is_receipt = picking.op_type.code == OperationType.RECEIPT
    for line in lines:
        apply_move(line.product, picking.source, picking.dest, line.qty, picking=picking,
                   reason=picking.op_type.get_code_display(),
                   unit_cost=line.unit_cost if is_receipt and line.unit_cost else None)
        if is_receipt and line.unit_cost:
            line.product.cost = _average_cost(line.product, line.qty, line.unit_cost)
            line.product.save(update_fields=["cost"])
    picking.state = Picking.DONE
    picking.done_date = timezone.now()
    picking.save(update_fields=["state", "done_date"])
    return picking


def cancel_picking(picking):
    if picking.state != Picking.DRAFT:
        raise StockError("Only draft documents can be cancelled")
    picking.state = Picking.CANCEL
    picking.save(update_fields=["state"])


def new_picking(op_type, lines, *, partner=None, origin="", source=None, dest=None, user=None, notes=""):
    """lines: iterable of (product, qty[, unit_cost])."""
    picking = Picking.objects.create(op_type=op_type, source=source or op_type.default_source,
                                     dest=dest or op_type.default_dest, partner=partner, origin=origin,
                                     created_by=user, notes=notes)
    for item in lines:
        product, qty = item[0], item[1]
        cost = item[2] if len(item) > 2 else 0
        PickingLine.objects.create(picking=picking, product=product, qty=qty, unit_cost=cost)
    return picking


@transaction.atomic
def do_scrap(scrap):
    scrap_loc = Location.objects.filter(usage=Location.SCRAP).first()
    if not scrap_loc:
        raise StockError("No scrap location is configured")
    move, _ = apply_move(scrap.product, scrap.location, scrap_loc, scrap.qty, reason="Scrap")
    scrap.move = move
    scrap.save(update_fields=["move"])
    return move


@transaction.atomic
def apply_count(location, counts, reason="Physical inventory"):
    """counts: {product_id: counted_qty}. Creates adjustment moves for every difference. Returns count of changes."""
    loss = Location.objects.filter(usage=Location.LOSS).first()
    if not loss:
        raise StockError("No inventory adjustment location is configured")
    changed = 0
    for pid, counted in counts.items():
        product = Product.objects.get(pk=pid)
        quant = StockQuant.objects.filter(product=product, location=location).first()
        have = quant.qty if quant else Decimal("0")
        diff = Decimal(counted) - have
        if diff > 0:
            apply_move(product, loss, location, diff, reason=reason)
            changed += 1
        elif diff < 0:
            apply_move(product, location, loss, -diff, reason=reason)
            changed += 1
    return changed


@transaction.atomic
def apply_landed_cost(lc):
    """Spread the extra cost over the receipt lines by value and raise each product's cost."""
    if lc.applied:
        raise StockError("Landed cost already applied")
    lines = list(lc.picking.lines.select_related("product"))
    base = sum((ln.qty * (ln.unit_cost or ln.product.cost)) for ln in lines)
    if not lines or base <= 0:
        raise StockError("The receipt has no valued lines to spread the cost over")
    for ln in lines:
        share = lc.amount * (ln.qty * (ln.unit_cost or ln.product.cost)) / base
        ln.product.cost = ln.product.cost + (share / ln.qty if ln.qty else 0)
        ln.product.save(update_fields=["cost"])
    lc.applied = True
    lc.save(update_fields=["applied"])


def stock_valuation():
    rows = []
    for p in Product.objects.filter(product_type=Product.GOODS, active=True):
        qty = p.on_hand
        if qty:
            rows.append({"product": p, "qty": qty, "cost": p.cost, "value": qty * p.cost})
    return rows


def replenishment_candidates():
    out = []
    for p in Product.objects.filter(product_type=Product.GOODS, active=True, min_qty__gt=0):
        oh = p.on_hand
        if oh < p.min_qty:
            target = p.max_qty if p.max_qty > p.min_qty else p.min_qty
            out.append({"product": p, "on_hand": oh, "min": p.min_qty, "max": p.max_qty, "to_order": target - oh})
    return out
