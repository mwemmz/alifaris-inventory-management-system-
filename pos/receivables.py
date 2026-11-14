"""What customers owe on account, how old it is, and the statement behind each balance.

A sale paid "On account" is a charge. A refund of one is a credit. Money received (CustomerPayment) is a credit.
Everything is worked out from the sale and payment rows, so there is no separate balance to drift out of line.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from inventory.models import Partner

from .models import CustomerPayment, PosPayment

ZERO = Decimal("0")
BUCKETS = [("0-30 days", 0, 30), ("31-60 days", 31, 60), ("61-90 days", 61, 90), ("Over 90 days", 91, 10**6)]


def _charges(customer):
    return (PosPayment.objects.filter(method=PosPayment.CREDIT, order__customer=customer)
            .select_related("order").order_by("order__created_at", "id"))


def balance(customer):
    sold = PosPayment.objects.filter(method=PosPayment.CREDIT, order__customer=customer).aggregate(t=Sum("amount"))["t"] or ZERO
    paid = CustomerPayment.objects.filter(customer=customer).aggregate(t=Sum("amount"))["t"] or ZERO
    return sold - paid


def statement(customer):
    """Dated entries with a running balance, oldest first."""
    rows = []
    for p in _charges(customer):
        o = p.order
        rows.append({"date": o.created_at, "kind": "Refund" if p.amount < 0 else "Sale on account", "ref": o.number,
                     "charge": p.amount if p.amount > 0 else ZERO, "credit": -p.amount if p.amount < 0 else ZERO,
                     "order": o})
    for c in CustomerPayment.objects.filter(customer=customer):
        rows.append({"date": c.paid_at, "kind": f"Payment ({c.get_method_display()})", "ref": c.reference or f"PAY-{c.pk}",
                     "charge": ZERO, "credit": c.amount, "order": None})
    rows.sort(key=lambda r: r["date"])
    running = ZERO
    for r in rows:
        running += r["charge"] - r["credit"]
        r["balance"] = running
    return rows


def open_items(customer, now=None):
    """Unpaid sales, oldest first. Payments and refunds are applied to the oldest sales first."""
    now = now or timezone.now()
    rows = statement(customer)
    credits = sum((r["credit"] for r in rows), ZERO)
    items = []
    for r in rows:
        if r["charge"] <= 0:
            continue
        take = min(credits, r["charge"])
        credits -= take
        left = r["charge"] - take
        if left > 0:
            items.append({"ref": r["ref"], "date": r["date"], "amount": r["charge"], "open": left,
                          "age": (now - r["date"]).days})
    return items


def aging(customer, now=None):
    items = open_items(customer, now)
    out = []
    for label, lo, hi in BUCKETS:
        out.append({"label": label, "amount": sum((i["open"] for i in items if lo <= i["age"] <= hi), ZERO)})
    return out


def summary(now=None):
    """One row per customer who owes money, biggest first."""
    now = now or timezone.now()
    ids = set(PosPayment.objects.filter(method=PosPayment.CREDIT, order__customer__isnull=False)
              .values_list("order__customer", flat=True))
    ids |= set(CustomerPayment.objects.values_list("customer", flat=True))
    rows = []
    for c in Partner.objects.filter(pk__in=ids):
        bal = balance(c)
        if bal == 0:
            continue
        items = open_items(c, now)
        oldest = max((i["age"] for i in items), default=0)
        rows.append({"customer": c, "balance": bal, "oldest_days": oldest, "limit": c.credit_limit,
                     "over_limit": bool(c.credit_limit and bal > c.credit_limit),
                     "overdue": sum((i["open"] for i in items if i["age"] > 30), ZERO)})
    rows.sort(key=lambda r: r["balance"], reverse=True)
    return rows


def total_owed():
    return sum((r["balance"] for r in summary()), ZERO)


def record_payment(customer, amount, method, user, reference="", note="", paid_at=None):
    from .services import PosError, money
    amount = money(amount)
    if amount <= 0:
        raise PosError("The amount must be more than zero.")
    if amount > balance(customer):
        raise PosError(f"{customer.name} only owes {balance(customer):.2f}. Enter that amount or less.")
    return CustomerPayment.objects.create(
        customer=customer, amount=amount, method=method, reference=reference[:60], note=note[:200],
        received_by=user.get_username() if user else "", paid_at=paid_at or timezone.now())


def check_limit(customer, new_credit):
    """Raise if putting new_credit on account would take the customer past their limit."""
    from .services import PosError
    if customer and customer.credit_limit and customer.credit_limit > 0:
        after = balance(customer) + new_credit
        if after > customer.credit_limit:
            raise PosError(f"{customer.name} would owe {after:.2f}, over their limit of {customer.credit_limit:.2f}. "
                           f"A manager can raise the limit.")
