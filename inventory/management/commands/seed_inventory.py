from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from inventory.models import (Location, OperationType, Partner, UoM, UoMCategory, Warehouse, ZraConfig)


class Command(BaseCommand):
    help = "Create the default warehouse, locations, operation types and units. Safe to run more than once."

    def handle(self, *args, **opts):
        unit_cat, _ = UoMCategory.objects.get_or_create(name="Unit")
        weight_cat, _ = UoMCategory.objects.get_or_create(name="Weight")
        vol_cat, _ = UoMCategory.objects.get_or_create(name="Volume")
        UoM.objects.get_or_create(name="Units", defaults=dict(category=unit_cat, zra_qty_code="U", zra_pkg_code="NT"))
        UoM.objects.get_or_create(name="kg", defaults=dict(category=weight_cat, zra_qty_code="KG", zra_pkg_code="KG"))
        UoM.objects.get_or_create(name="Litre", defaults=dict(category=vol_cat, zra_qty_code="L", zra_pkg_code="LTR"))

        wh, _ = Warehouse.objects.get_or_create(code="SHOP", defaults=dict(name="Shop"))
        stock, _ = Location.objects.get_or_create(name="Stock", warehouse=wh, defaults=dict(usage=Location.INTERNAL))
        vend, _ = Location.objects.get_or_create(name="Vendors", warehouse=None, defaults=dict(usage=Location.SUPPLIER))
        cust, _ = Location.objects.get_or_create(name="Customers", warehouse=None, defaults=dict(usage=Location.CUSTOMER))
        Location.objects.get_or_create(name="Inventory adjustment", warehouse=None, defaults=dict(usage=Location.LOSS))
        Location.objects.get_or_create(name="Scrap", warehouse=None, defaults=dict(usage=Location.SCRAP))

        OperationType.objects.get_or_create(warehouse=wh, code="receipt", defaults=dict(
            name="Receipts", prefix="IN", default_source=vend, default_dest=stock))
        OperationType.objects.get_or_create(warehouse=wh, code="delivery", defaults=dict(
            name="Delivery Orders", prefix="OUT", default_source=stock, default_dest=cust))
        OperationType.objects.get_or_create(warehouse=wh, code="internal", defaults=dict(
            name="Internal Transfers", prefix="INT", default_source=stock, default_dest=stock))
        Partner.objects.get_or_create(name="Walk-in customer", defaults=dict(is_customer=True))
        ZraConfig.get()
        Group.objects.get_or_create(name="Storekeeper")
        Group.objects.get_or_create(name="Cashier")
        self.stdout.write(self.style.SUCCESS("Inventory defaults are in place."))
