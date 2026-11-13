import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

QTY = dict(max_digits=14, decimal_places=3)
MONEY = dict(max_digits=14, decimal_places=2)


class UoMCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name = "UoM category"
        verbose_name_plural = "UoM categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class UoM(models.Model):
    category = models.ForeignKey(UoMCategory, on_delete=models.PROTECT, related_name="uoms")
    name = models.CharField(max_length=40, unique=True)
    ratio = models.DecimalField(max_digits=12, decimal_places=5, default=1,
                                help_text="Size relative to the category reference unit")
    zra_qty_code = models.CharField("ZRA quantity unit code", max_length=5, blank=True, default="U")
    zra_pkg_code = models.CharField("ZRA packaging unit code", max_length=5, blank=True, default="NT")

    class Meta:
        verbose_name = "UoM"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductCategory(models.Model):
    name = models.CharField(max_length=120)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")

    class Meta:
        verbose_name_plural = "product categories"
        ordering = ["name"]

    def __str__(self):
        return f"{self.parent} / {self.name}" if self.parent_id else self.name


class Product(models.Model):
    GOODS, SERVICE = "goods", "service"
    TYPES = [(GOODS, "Goods"), (SERVICE, "Service")]
    TAX_TYPES = [("A", "A - Standard rated (16%)"), ("B", "B - Minimum taxable value"),
                 ("C1", "C1 - Exports (0%)"), ("C2", "C2 - Zero rated local (0%)"),
                 ("C3", "C3 - Zero rated by nature (0%)"), ("D", "D - Exempt")]
    ITEM_TYPES = [("1", "1 - Raw material"), ("2", "2 - Finished product"), ("3", "3 - Service")]

    name = models.CharField(max_length=200, db_index=True)
    internal_ref = models.CharField("Internal reference", max_length=20, unique=True,
                                    help_text="Also sent to ZRA as the item code (max 20 characters)")
    barcode = models.CharField(max_length=40, blank=True, db_index=True)
    product_type = models.CharField(max_length=10, choices=TYPES, default=GOODS)
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL)
    uom = models.ForeignKey(UoM, null=True, blank=True, on_delete=models.SET_NULL)
    price = models.DecimalField("Sales price", default=0, **MONEY)
    secondary_price = models.DecimalField(default=0, **MONEY)
    cost = models.DecimalField(default=0, **MONEY)
    min_qty = models.DecimalField("Reorder minimum", default=0, **QTY)
    max_qty = models.DecimalField("Reorder maximum", default=0, **QTY)
    tax_type = models.CharField(max_length=3, choices=TAX_TYPES, default="A")
    zra_item_class_code = models.CharField("ZRA item class code", max_length=10, blank=True,
                                           help_text="Exactly 10 characters, as listed by the ZRA code service")
    zra_item_type = models.CharField(max_length=1, choices=ITEM_TYPES, default="2")
    zra_registered = models.BooleanField(default=False, editable=False)
    image = models.ImageField(upload_to="products/", blank=True)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"[{self.internal_ref}] {self.name}"

    @property
    def tracks_stock(self):
        return self.product_type == self.GOODS

    @property
    def on_hand(self):
        total = self.quants.filter(location__usage=Location.INTERNAL).aggregate(t=Sum("qty"))["t"]
        return Decimal(total or 0).quantize(Decimal("0.001"))


class Warehouse(models.Model):
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=10, unique=True)
    zra_branch_id = models.CharField("ZRA branch id", max_length=3, default="000")

    def __str__(self):
        return self.name


class Location(models.Model):
    INTERNAL, SUPPLIER, CUSTOMER, LOSS, SCRAP = "internal", "supplier", "customer", "inventory_loss", "scrap"
    USAGES = [(INTERNAL, "Internal"), (SUPPLIER, "Vendor"), (CUSTOMER, "Customer"),
              (LOSS, "Inventory adjustment"), (SCRAP, "Scrap")]

    name = models.CharField(max_length=80)
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="locations")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    usage = models.CharField(max_length=20, choices=USAGES, default=INTERNAL)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["usage", "name"]

    @property
    def full_name(self):
        if self.parent_id:
            return f"{self.parent.full_name}/{self.name}"
        if self.warehouse_id:
            return f"{self.warehouse.code}/{self.name}"
        return self.name

    def __str__(self):
        return self.full_name

    @property
    def is_internal(self):
        return self.usage == self.INTERNAL


class Partner(models.Model):
    name = models.CharField(max_length=120)
    is_supplier = models.BooleanField(default=False)
    is_customer = models.BooleanField(default=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    tpin = models.CharField("TPIN", max_length=10, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class OperationType(models.Model):
    RECEIPT, DELIVERY, INTERNAL = "receipt", "delivery", "internal"
    CODES = [(RECEIPT, "Receipt"), (DELIVERY, "Delivery"), (INTERNAL, "Internal transfer")]

    name = models.CharField(max_length=60)
    code = models.CharField(max_length=10, choices=CODES)
    prefix = models.CharField(max_length=10)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    default_source = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    default_dest = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return f"{self.warehouse.name}: {self.name}"


class Picking(models.Model):
    DRAFT, DONE, CANCEL = "draft", "done", "cancel"
    STATES = [(DRAFT, "Draft"), (DONE, "Done"), (CANCEL, "Cancelled")]

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ref = models.CharField(max_length=40, blank=True, db_index=True)
    op_type = models.ForeignKey(OperationType, on_delete=models.PROTECT, related_name="pickings")
    source = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    dest = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="+")
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.SET_NULL)
    state = models.CharField(max_length=10, choices=STATES, default=DRAFT, db_index=True)
    scheduled_date = models.DateTimeField(default=timezone.now)
    done_date = models.DateTimeField(null=True, blank=True)
    origin = models.CharField("Source document", max_length=60, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    site_code = models.CharField(max_length=10, default=settings.SITE_CODE)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scheduled_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["op_type", "origin"], condition=~Q(origin=""),
                                    name="unique_origin_per_operation"),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.ref:
            self.ref = f"{self.op_type.warehouse.code}/{self.op_type.prefix}/{self.pk:05d}"
            super().save(update_fields=["ref"])

    def __str__(self):
        return self.ref or f"Picking {self.pk}"


class PickingLine(models.Model):
    picking = models.ForeignKey(Picking, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField("Unit cost", default=0, **MONEY,
                                    help_text="Receipts only: used for average cost")

    def __str__(self):
        return f"{self.product} x {self.qty}"


class StockMove(models.Model):
    """Append-only ledger. Quants are the running totals of these rows."""
    uuid = models.UUIDField(default=uuid.uuid4, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="moves")
    source = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="moves_out")
    dest = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="moves_in")
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(default=0, **MONEY)
    date = models.DateTimeField(default=timezone.now, db_index=True)
    picking = models.ForeignKey(Picking, null=True, blank=True, on_delete=models.SET_NULL, related_name="moves")
    reason = models.CharField(max_length=80, blank=True)
    site_code = models.CharField(max_length=10, default=settings.SITE_CODE)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-date", "-id"]

    @property
    def kind(self):
        if self.picking_id:
            return self.picking.op_type.get_code_display()
        return self.reason or "Move"


class StockQuant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="quants")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="quants")
    qty = models.DecimalField(default=0, **QTY)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "location"], name="one_quant_per_location")]


class Scrap(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, limit_choices_to={"usage": Location.INTERNAL})
    qty = models.DecimalField(**QTY)
    reason = models.CharField(max_length=120, blank=True)
    date = models.DateTimeField(default=timezone.now)
    move = models.OneToOneField(StockMove, null=True, blank=True, on_delete=models.SET_NULL, editable=False)

    class Meta:
        ordering = ["-date"]


class LandedCost(models.Model):
    picking = models.ForeignKey(Picking, on_delete=models.PROTECT, limit_choices_to={"op_type__code": "receipt"})
    description = models.CharField(max_length=120)
    amount = models.DecimalField(**MONEY)
    date = models.DateField(default=timezone.localdate)
    applied = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-date", "-id"]


class ZraConfig(models.Model):
    """Single row. Settings for the local ZRA VSDC (Smart Invoice) device."""
    enabled = models.BooleanField(default=False)
    base_url = models.CharField(max_length=200, default="http://127.0.0.1:8080/zraprodvsdc")
    tpin = models.CharField("TPIN", max_length=10, blank=True)
    branch_id = models.CharField(max_length=3, default="000")
    device_serial = models.CharField(max_length=40, blank=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=15)
    register_after_import = models.BooleanField(default=False)
    push_stock_after_validate = models.BooleanField(default=False)

    class Meta:
        verbose_name = "ZRA settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ZraLog(models.Model):
    SUCCESS, ERROR = "success", "error"
    date = models.DateTimeField(default=timezone.now, db_index=True)
    tpin = models.CharField(max_length=10, blank=True)
    branch = models.CharField(max_length=3, blank=True)
    model = models.CharField(max_length=60, blank=True)
    record_id = models.CharField(max_length=40, blank=True)
    endpoint = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=[(SUCCESS, "Success"), (ERROR, "Error")], db_index=True)
    message = models.TextField(blank=True)
    request_body = models.TextField(blank=True)
    response_body = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]


class ApiToken(models.Model):
    name = models.CharField(max_length=80, help_text="For example: Main shop POS")
    key = models.CharField(max_length=48, unique=True, editable=False)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_hex(24)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
