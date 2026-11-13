from django.contrib import admin

from . import models as m

for model in (m.UoMCategory, m.UoM, m.ProductCategory, m.Warehouse, m.Location, m.Partner, m.OperationType,
              m.PickingLine, m.Scrap, m.LandedCost, m.ZraConfig, m.ApiToken):
    admin.site.register(model)


@admin.register(m.Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("internal_ref", "name", "price", "zra_registered", "active")
    search_fields = ("name", "internal_ref", "barcode")
    list_filter = ("active", "product_type", "zra_registered", "category")


class LineInline(admin.TabularInline):
    model = m.PickingLine
    extra = 0


@admin.register(m.Picking)
class PickingAdmin(admin.ModelAdmin):
    list_display = ("ref", "op_type", "state", "origin", "scheduled_date")
    list_filter = ("state", "op_type")
    search_fields = ("ref", "origin")
    inlines = [LineInline]


@admin.register(m.StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    """The ledger is read-only here: change stock with a transfer, scrap or count so quants stay correct."""
    list_display = ("date", "product", "source", "dest", "qty", "site_code")
    search_fields = ("product__name", "product__internal_ref")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(m.StockQuant)
class QuantAdmin(admin.ModelAdmin):
    list_display = ("product", "location", "qty")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(m.ZraLog)
class ZraLogAdmin(admin.ModelAdmin):
    list_display = ("date", "status", "endpoint", "message")
    list_filter = ("status",)
