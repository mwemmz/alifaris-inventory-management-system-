import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from inventory.models import MONEY, QTY, Partner, Picking, Product, Warehouse


class PosSession(models.Model):
    """One cashier's shift at one till. Cash up happens when it is closed."""
    OPEN, CLOSED = "open", "closed"
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    state = models.CharField(max_length=8, choices=[(OPEN, "Open"), (CLOSED, "Closed")], default=OPEN, db_index=True)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_cash = models.DecimalField(default=0, **MONEY)
    counted_cash = models.DecimalField(null=True, blank=True, **MONEY)
    expected_cash = models.DecimalField(null=True, blank=True, **MONEY)
    notes = models.CharField(max_length=200, blank=True)
    site_code = models.CharField(max_length=10, default=settings.SITE_CODE)

    class Meta:
        ordering = ["-opened_at"]

    @property
    def difference(self):
        if self.counted_cash is None or self.expected_cash is None:
            return None
        return self.counted_cash - self.expected_cash

    def __str__(self):
        return f"{self.site_code}/S{self.pk} {self.cashier}"


class PosOrder(models.Model):
    SALE, REFUND = "sale", "refund"
    ZRA_NA, ZRA_PENDING, ZRA_SENT, ZRA_FAILED = "na", "pending", "sent", "failed"
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.CharField(max_length=30, unique=True, blank=True)
    kind = models.CharField(max_length=8, choices=[(SALE, "Sale"), (REFUND, "Refund")], default=SALE)
    refund_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="refunded_by")
    session = models.ForeignKey(PosSession, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    cashier_name = models.CharField(max_length=80)
    customer = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.SET_NULL)
    customer_name = models.CharField(max_length=120, blank=True)
    customer_tpin = models.CharField(max_length=10, blank=True)
    price_tier = models.CharField(max_length=10, default="primary", choices=[("primary", "Price"), ("secondary", "Secondary price")])
    tax_total = models.DecimalField(default=0, **MONEY)
    discount_total = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    paid_total = models.DecimalField(default=0, **MONEY)
    change_due = models.DecimalField(default=0, **MONEY)
    note = models.CharField(max_length=200, blank=True)
    picking = models.ForeignKey(Picking, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    site_code = models.CharField(max_length=10, default=settings.SITE_CODE)
    remote = models.BooleanField(default=False, help_text="Copied from another site; its stock moves arrive through stock sync")
    zra_status = models.CharField(max_length=8, default=ZRA_NA, db_index=True,
                                  choices=[(ZRA_NA, "Not required"), (ZRA_PENDING, "Waiting"), (ZRA_SENT, "Sent"), (ZRA_FAILED, "Failed")])
    zra_message = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"{self.site_code}/POS/{self.pk:06d}"
            super().save(update_fields=["number"])

    def __str__(self):
        return self.number


class PosOrderLine(models.Model):
    order = models.ForeignKey(PosOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    ref = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    qty = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(default=0, **MONEY)  # the product's average cost at the moment of sale
    discount_pct = models.DecimalField(default=0, max_digits=5, decimal_places=2)
    tax_type = models.CharField(max_length=3, default="A")
    tax_amount = models.DecimalField(default=0, **MONEY)
    line_total = models.DecimalField(**MONEY)


class PosPayment(models.Model):
    CASH, CARD, MOBILE, BANK, CREDIT = "cash", "card", "mobile", "bank", "credit"
    METHODS = [(CASH, "Cash"), (CARD, "Card"), (MOBILE, "Mobile money"), (BANK, "Bank transfer"), (CREDIT, "On account")]
    ZRA_CODES = {CASH: "01", CARD: "03", MOBILE: "04", BANK: "05", CREDIT: "06"}
    order = models.ForeignKey(PosOrder, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=METHODS)
    amount = models.DecimalField(**MONEY)


class CustomerPayment(models.Model):
    """Money received from a customer against what they owe on account."""
    METHODS = [(PosPayment.CASH, "Cash"), (PosPayment.CARD, "Card"), (PosPayment.MOBILE, "Mobile money"),
               (PosPayment.BANK, "Bank transfer")]
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="payments_received")
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=10, choices=METHODS, default=PosPayment.CASH)
    reference = models.CharField(max_length=60, blank=True)
    note = models.CharField(max_length=200, blank=True)
    paid_at = models.DateTimeField(default=timezone.now, db_index=True)
    received_by = models.CharField(max_length=80, blank=True)
    site_code = models.CharField(max_length=10, default=settings.SITE_CODE)

    class Meta:
        ordering = ["-paid_at", "-id"]

    def __str__(self):
        return f"{self.customer} paid {self.amount}"
