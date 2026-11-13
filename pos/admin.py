from django.contrib import admin

from .models import PosOrder, PosOrderLine, PosPayment, PosSession


class LineInline(admin.TabularInline):
    model = PosOrderLine
    extra = 0


class PayInline(admin.TabularInline):
    model = PosPayment
    extra = 0


@admin.register(PosOrder)
class PosOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "created_at", "cashier_name", "total", "zra_status", "site_code", "remote")
    list_filter = ("kind", "zra_status", "site_code", "remote")
    search_fields = ("number", "customer_name")
    inlines = [LineInline, PayInline]


@admin.register(PosSession)
class PosSessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "state", "opened_at", "closed_at", "expected_cash", "counted_cash")
